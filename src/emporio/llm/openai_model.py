"""Adapter for any OpenAI-compatible Chat Completions endpoint (OpenAI, Gemini,
Groq, OpenRouter, a local Ollama...). Keeps the agent vendor-neutral.

Validated with unit tests against recorded response shapes; the end-to-end runs
in this repository used the Anthropic adapter (README, Limitations).
"""

from __future__ import annotations

import json

import openai

from emporio.llm.base import ModelTurn, ToolCall, ToolResult, ToolSpec, Usage

STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIChatModel:
    provider = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: openai.OpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=3, timeout=60.0)

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def system_message(self, text: str) -> dict:
        return {"role": "system", "content": text}

    def assistant_message(self, text: str) -> dict:
        return {"role": "assistant", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[dict]:
        return [
            {
                "role": "tool",
                "tool_call_id": r.call_id,
                "content": f"ERRO: {r.content}" if r.is_error else r.content,
            }
            for r in results
        ]

    def generate(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], allow_tools: bool = True
    ) -> ModelTurn:
        params: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages]}
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ]
            params["tool_choice"] = "auto" if allow_tools else "none"
        response = self.client.chat.completions.create(**params)
        choice = response.choices[0]
        message = choice.message

        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid_json": call.function.arguments}  # rejected by validation
            calls.append(ToolCall(call.id, call.function.name, arguments))

        native: dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            native["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in message.tool_calls
            ]
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        return ModelTurn(
            message=native,
            text=(message.content or "").strip(),
            tool_calls=calls,
            stop_reason=STOP_REASONS.get(choice.finish_reason, choice.finish_reason or "end_turn"),
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                cache_read_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
                calls=1,
            ),
        )
