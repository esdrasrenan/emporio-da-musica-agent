"""A scripted ChatModel to test the agent loop without network calls."""

from __future__ import annotations

from emporio.llm.base import ModelTurn, ToolCall, ToolResult, ToolSpec, Usage


class FakeChatModel:
    provider = "fake"
    model = "fake-model"

    def __init__(self, script: list[ModelTurn]) -> None:
        self.script = list(script)
        self.requests: list[dict] = []

    @staticmethod
    def says(text: str) -> ModelTurn:
        return ModelTurn({"role": "assistant", "content": text}, text, usage=Usage(calls=1))

    @staticmethod
    def calls(name: str, **arguments) -> ModelTurn:
        call = ToolCall(f"call-{name}", name, arguments)
        return ModelTurn({"role": "assistant", "tool_calls": [name]}, "", [call], "tool_use", Usage(calls=1))

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def system_message(self, text: str) -> dict:
        return {"role": "system", "content": text}

    def assistant_message(self, text: str) -> dict:
        return {"role": "assistant", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[dict]:
        return [{"role": "tool", "id": r.call_id, "content": r.content} for r in results]

    def generate(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], allow_tools: bool = True
    ) -> ModelTurn:
        self.requests.append({"messages": list(messages), "allow_tools": allow_tools})
        return self.script.pop(0)
