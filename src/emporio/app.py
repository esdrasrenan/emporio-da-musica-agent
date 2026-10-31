"""Wiring: build a ready-to-use Agent from Settings."""

from __future__ import annotations

from dataclasses import replace

from emporio.agent import Agent
from emporio.catalog import Catalog
from emporio.config import Settings
from emporio.data import load_store_data
from emporio.llm.base import ChatModel
from emporio.orders import OrderDesk
from emporio.policies import POLICY_FILE, PolicyManual
from emporio.storage import Storage
from emporio.tools import Toolbox


def build_model(settings: Settings) -> ChatModel:
    if settings.provider == "anthropic":
        from emporio.llm.anthropic_model import AnthropicChatModel

        if not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY is not set (see .env.example).")
        return AnthropicChatModel(
            settings.model,
            api_key=settings.anthropic_api_key,
            workspace_id=settings.anthropic_workspace_id,
            effort=settings.effort,
        )
    if settings.provider == "openai":
        from emporio.llm.openai_model import OpenAIChatModel

        return OpenAIChatModel(
            settings.model, api_key=settings.openai_api_key, base_url=settings.openai_base_url
        )
    raise SystemExit(f"Unknown LLM_PROVIDER: {settings.provider}")


def build_agent(
    settings: Settings | None = None, model: ChatModel | None = None, **overrides: object
) -> Agent:
    settings = replace(settings or Settings.from_env(), **overrides)  # type: ignore[arg-type]
    data = load_store_data(settings.data_dir)
    storage = Storage(settings.db_path)
    toolbox = Toolbox(
        data,
        Catalog(data),
        PolicyManual.from_pdf(settings.data_dir / POLICY_FILE),
        OrderDesk(data),
        storage,
        settings.clock,
    )
    return Agent(model or build_model(settings), toolbox, storage, data, settings.clock)
