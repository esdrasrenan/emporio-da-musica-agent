"""Claude adapter (Anthropic Messages API)."""

from __future__ import annotations

import anthropic

from emporio.llm.base import ModelTurn, ToolCall, ToolResult, ToolSpec, Usage

# Server-side refusal fallback exists for Opus/Sonnet; Haiku has none.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicChatModel:
    provider = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        workspace_id: str | None = None,
        effort: str | None = "medium",
        max_tokens: int = 16000,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens  # thinking counts toward it, so leave room
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self.client = client or anthropic.Anthropic(
            api_key=api_key, default_headers=headers, max_retries=3, timeout=60.0
        )
        self.refusal_fallback = "haiku" not in model

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def system_message(self, text: str) -> dict:
        # Mid-conversation system message: operator authority, appended (never
        # edited), so the cached prefix and earlier thinking blocks stay valid.
        return {"role": "system", "content": text}

    def assistant_message(self, text: str) -> dict:
        return {"role": "assistant", "content": [{"type": "text", "text": text}]}

    def tool_results_messages(self, results: list[ToolResult]) -> list[dict]:
        # All results of one assistant turn go back in a single user message.
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.content,
                        **({"is_error": True} if r.is_error else {}),
                    }
                    for r in results
                ],
            }
        ]

    def generate(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], allow_tools: bool = True
    ) -> ModelTurn:
        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
                for t in tools
            ],
            "cache_control": {"type": "ephemeral"},  # caches tools + system + history prefix
        }
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if not allow_tools:
            params["tool_choice"] = {"type": "none"}
        if self.refusal_fallback:
            response = self.client.beta.messages.create(**params, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            response = self.client.messages.create(**params)

        content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        usage = response.usage
        return ModelTurn(
            message={"role": "assistant", "content": content},
            text="".join(b.text for b in response.content if b.type == "text").strip(),
            tool_calls=[
                ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"
            ],
            stop_reason=response.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
                calls=1,
            ),
        )
