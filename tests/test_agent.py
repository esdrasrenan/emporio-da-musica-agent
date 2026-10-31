import json

import pytest

from emporio.agent import Agent
from emporio.llm.base import ModelTurn
from emporio.prompts import REFUSAL_REPLY
from tests.fakes import FakeChatModel


def make_agent(script, toolbox, storage, data, clock, max_steps=6):
    return Agent(FakeChatModel(script), toolbox, storage, data, clock, max_steps=max_steps)


def test_tool_call_then_answer(toolbox, storage, data, clock):
    agent = make_agent(
        [
            FakeChatModel.calls("search_products", query="Takamine GD20"),
            FakeChatModel.says("Custa R$ 2.199,00."),
        ],
        toolbox,
        storage,
        data,
        clock,
    )
    session = agent.start_session()
    reply = agent.reply(session, "Quanto custa o Takamine GD20?")

    assert reply.text == "Custa R$ 2.199,00."
    assert [c["name"] for c in reply.tool_calls] == ["search_products"]
    assert json.loads(reply.tool_calls[0]["output"])["products"][0]["product_id"] == 95
    assert reply.usage.calls == 2

    history = storage.load_messages(session)
    assert [m["role"] for m in history] == ["user", "system", "assistant", "tool", "assistant"]
    assert "terça-feira, 24/03/2026, 10:30" in history[1]["content"]


def test_history_is_append_only_across_turns(toolbox, storage, data, clock):
    agent = make_agent(
        [FakeChatModel.says("Oi!"), FakeChatModel.says("Até mais!")], toolbox, storage, data, clock
    )
    session = agent.start_session()
    agent.reply(session, "Oi")
    first = storage.load_messages(session)
    agent.reply(session, "Tchau")
    second = storage.load_messages(session)
    assert second[: len(first)] == first  # earlier turns are never rewritten
    # the second request carried the whole first turn
    assert agent.model.requests[1]["messages"][: len(first)] == first


def test_refusal_is_replaced_by_a_safe_reply(toolbox, storage, data, clock):
    refused = ModelTurn({"role": "assistant", "content": []}, "", stop_reason="refusal")
    agent = make_agent([refused], toolbox, storage, data, clock)
    session = agent.start_session()
    assert agent.reply(session, "...").text == REFUSAL_REPLY
    assert storage.load_messages(session)[-1] == {"role": "assistant", "content": REFUSAL_REPLY}


def test_step_limit_forces_a_text_answer(toolbox, storage, data, clock):
    script = [FakeChatModel.calls("search_products", query="violão")] * 2 + [
        FakeChatModel.says("Aqui estão as opções.")
    ]
    agent = make_agent(script, toolbox, storage, data, clock, max_steps=2)
    reply = agent.reply(agent.start_session(), "violões?")
    assert reply.text == "Aqui estão as opções."
    assert [r["allow_tools"] for r in agent.model.requests] == [True, True, False]


def test_channel_phone_identifies_the_customer(toolbox, storage, data, clock):
    agent = make_agent([FakeChatModel.says("Oi, Ana!")], toolbox, storage, data, clock)
    session = agent.start_session(customer_phone="+55 67 99654-7821")
    agent.reply(session, "Oi")
    assert "Ana Carolina Ferreira" in storage.load_messages(session)[1]["content"]


def test_unknown_phone_stays_anonymous(toolbox, storage, data, clock):
    agent = make_agent([FakeChatModel.says("Oi!")], toolbox, storage, data, clock)
    session = agent.start_session(customer_phone="(67) 90000-0000")
    agent.reply(session, "Oi")
    assert "cliente não identificado" in storage.load_messages(session)[1]["content"].lower()


def test_session_from_another_provider_is_rejected(toolbox, storage, data, clock):
    storage.create_session("abc", "anthropic", None, clock.now())
    agent = make_agent([], toolbox, storage, data, clock)
    with pytest.raises(ValueError, match="another LLM provider"):
        agent.reply("abc", "Oi")
