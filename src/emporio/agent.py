"""The agent loop: model -> tools -> model, until a text answer.

A plain loop (no framework): it is short, provider-neutral, persists each turn
atomically and records a trace of every tool call for debugging and evals.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from emporio.config import Clock
from emporio.data.models import StoreData
from emporio.llm.base import ChatModel, Usage
from emporio.orders import contact_matches
from emporio.prompts import FALLBACK_REPLY, REFUSAL_REPLY, SYSTEM_PROMPT
from emporio.storage import Storage
from emporio.store import is_english, turn_context
from emporio.tools import SessionContext, Toolbox

log = logging.getLogger(__name__)


@dataclass
class AgentReply:
    session_id: str
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


class Agent:
    def __init__(
        self,
        model: ChatModel,
        toolbox: Toolbox,
        storage: Storage,
        data: StoreData,
        clock: Clock,
        max_steps: int = 6,
    ) -> None:
        self.model, self.toolbox, self.storage = model, toolbox, storage
        self.data, self.clock, self.max_steps = data, clock, max_steps
        self._contexts: dict[str, SessionContext] = {}

    def start_session(self, customer_phone: str | None = None, session_id: str | None = None) -> str:
        """Open a conversation. ``customer_phone`` is the channel's sender id (e.g. the
        WhatsApp number): trusted input from the integration, never from chat text."""
        session_id = session_id or uuid.uuid4().hex[:12]
        if self.storage.get_session(session_id) is None:
            customer_id = self._identify(customer_phone) if customer_phone else None
            self.storage.create_session(session_id, self.model.provider, customer_id, self.clock.now())
        return session_id

    def reply(self, session_id: str, user_text: str) -> AgentReply:
        started = time.perf_counter()
        ctx = self._context(session_id)
        now = self.clock.now()
        history = self.storage.load_messages(session_id)
        new = [
            self.model.user_message(user_text),
            self.model.system_message(turn_context(now, ctx.customer_name, is_english(user_text))),
        ]
        trace: list[dict] = []
        usage = Usage()
        text = FALLBACK_REPLY

        for step in range(self.max_steps + 1):
            turn = self.model.generate(
                system=SYSTEM_PROMPT,
                messages=history + new,
                tools=self.toolbox.specs,
                allow_tools=step < self.max_steps,
            )
            usage += turn.usage
            if turn.stop_reason in ("refusal", "max_tokens") or (not turn.text and not turn.tool_calls):
                # Keep history valid and honest: store a plain reply instead of a cut-off turn.
                log.warning("turn ended with %s", turn.stop_reason)
                text = REFUSAL_REPLY if turn.stop_reason == "refusal" else FALLBACK_REPLY
                new.append(self.model.assistant_message(text))
                break
            new.append(turn.message)
            if not turn.tool_calls:
                text = turn.text
                break
            results = [self.toolbox.execute(call, ctx) for call in turn.tool_calls]
            trace += [
                {
                    "name": call.name,
                    "input": call.arguments,
                    "output": result.content,
                    "is_error": result.is_error,
                }
                for call, result in zip(turn.tool_calls, results, strict=True)
            ]
            new.extend(self.model.tool_results_messages(results))

        latency_ms = int((time.perf_counter() - started) * 1000)
        self.storage.append_turn(
            session_id,
            new,
            {
                "user_text": user_text,
                "reply_text": text,
                "tool_calls": trace,
                "usage": usage.as_dict(),
                "model": self.model.model,
                "latency_ms": latency_ms,
            },
            now,
        )
        return AgentReply(session_id, text, trace, usage, latency_ms)

    # ------------------------------------------------------------------ helpers
    def _identify(self, phone: str) -> int | None:
        matches = [c.id for c in self.data.customers.values() if contact_matches(c, phone)]
        return matches[0] if len(matches) == 1 else None

    def _context(self, session_id: str) -> SessionContext:
        if session_id not in self._contexts:
            session = self.storage.get_session(session_id)
            if session is None:
                raise KeyError(f"Unknown session {session_id}; call start_session first.")
            if session["provider"] != self.model.provider:
                raise ValueError(
                    f"Session {session_id} was created with another LLM provider "
                    f"({session['provider']}); start a new session."
                )
            customer = self.data.customers.get(session["customer_id"]) if session["customer_id"] else None
            self._contexts[session_id] = SessionContext(
                session_id=session_id,
                customer_id=customer.id if customer else None,
                customer_name=customer.name if customer else None,
            )
        return self._contexts[session_id]
