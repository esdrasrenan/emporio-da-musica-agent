"""Provider-neutral types for the agent loop.

Conversation history is kept in each provider's *native* message format and
treated as opaque by the agent: Claude's thinking blocks must be replayed
byte-for-byte, so translating history into a common format would break it.
Only tool calls and tool results cross the boundary in neutral form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(getattr(self, f) + getattr(other, f) for f in self.__dataclass_fields__))

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


@dataclass
class ModelTurn:
    message: dict  # native assistant message, appended to history as is
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens | refusal
    usage: Usage = field(default_factory=Usage)


class ChatModel(Protocol):
    provider: str
    model: str

    def user_message(self, text: str) -> dict: ...

    def system_message(self, text: str) -> dict: ...

    def assistant_message(self, text: str) -> dict: ...

    def tool_results_messages(self, results: list[ToolResult]) -> list[dict]: ...

    def generate(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec], allow_tools: bool = True
    ) -> ModelTurn: ...
