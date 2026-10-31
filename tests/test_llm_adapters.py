"""Adapters translate between native API shapes and the agent's neutral types.
Clients are stubbed: no network."""

from types import SimpleNamespace as NS

from emporio.llm.anthropic_model import AnthropicChatModel
from emporio.llm.base import ToolResult, ToolSpec
from emporio.llm.openai_model import OpenAIChatModel

TOOLS = [
    ToolSpec("search_products", "Search", {"type": "object", "properties": {}, "additionalProperties": False})
]


class Recorder:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_openai_adapter_maps_tool_calls_and_messages():
    call = NS(id="c1", function=NS(name="search_products", arguments='{"query": "ukulele"}'))
    bad = NS(id="c2", function=NS(name="search_products", arguments="{not json"))
    response = NS(
        choices=[NS(finish_reason="tool_calls", message=NS(content=None, tool_calls=[call, bad]))],
        usage=NS(prompt_tokens=120, completion_tokens=8, prompt_tokens_details=NS(cached_tokens=100)),
    )
    completions = Recorder(response)
    model = OpenAIChatModel("gpt-test", client=NS(chat=NS(completions=completions)))

    turn = model.generate(system="sys", messages=[model.user_message("oi")], tools=TOOLS, allow_tools=False)

    assert completions.kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert completions.kwargs["tool_choice"] == "none"
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].arguments == {"query": "ukulele"}
    assert turn.tool_calls[1].arguments == {"_invalid_json": "{not json"}  # rejected later
    assert turn.message["tool_calls"][0]["function"]["arguments"] == '{"query": "ukulele"}'
    assert (turn.usage.input_tokens, turn.usage.cache_read_tokens) == (120, 100)
    assert model.tool_results_messages([ToolResult("c1", "boom", is_error=True)]) == [
        {"role": "tool", "tool_call_id": "c1", "content": "ERRO: boom"}
    ]


def test_anthropic_adapter_keeps_native_blocks_and_groups_tool_results():
    blocks = [
        NS(
            type="thinking",
            text=None,
            model_dump=lambda **_: {"type": "thinking", "thinking": "", "signature": "sig"},
        ),
        NS(
            type="tool_use",
            id="t1",
            name="search_products",
            input={"query": "ukulele"},
            model_dump=lambda **_: {
                "type": "tool_use",
                "id": "t1",
                "name": "search_products",
                "input": {"query": "ukulele"},
            },
        ),
    ]
    response = NS(
        content=blocks,
        stop_reason="tool_use",
        usage=NS(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=4000, cache_creation_input_tokens=0
        ),
    )
    messages = Recorder(response)
    model = AnthropicChatModel("claude-haiku-5-5", client=NS(messages=messages), effort="low")

    turn = model.generate(system="sys", messages=[model.user_message("oi")], tools=TOOLS)

    sent = messages.kwargs
    assert sent["output_config"] == {"effort": "low"}
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"][0]["strict"] is True and "tool_choice" not in sent
    assert turn.message["content"][0]["signature"] == "sig"  # thinking replayed verbatim
    assert turn.tool_calls[0].name == "search_products"
    assert turn.usage.cache_read_tokens == 4000
    assert model.tool_results_messages([ToolResult("t1", "{}"), ToolResult("t2", "x", True)]) == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "{}"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "x", "is_error": True},
            ],
        }
    ]
