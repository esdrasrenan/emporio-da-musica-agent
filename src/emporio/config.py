"""Runtime settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_TZ = ZoneInfo("America/Campo_Grande")

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "medium"


def parse_store_datetime(value: str) -> datetime:
    """ISO 8601; naive values are interpreted in the store's timezone (Campo Grande)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=STORE_TZ)


@dataclass(frozen=True)
class Clock:
    """Source of "now" for the agent. Frozen in demos/tests, real time otherwise."""

    frozen: datetime | None = None

    def now(self) -> datetime:
        return self.frozen.astimezone(STORE_TZ) if self.frozen else datetime.now(STORE_TZ)


@dataclass(frozen=True)
class Settings:
    provider: str = "anthropic"
    model: str = DEFAULT_ANTHROPIC_MODEL
    effort: str | None = DEFAULT_EFFORT
    anthropic_api_key: str | None = None
    anthropic_workspace_id: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    data_dir: Path = PROJECT_ROOT / "data"
    db_path: Path = PROJECT_ROOT / ".data" / "emporio.sqlite3"
    clock: Clock = field(default_factory=Clock)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or PROJECT_ROOT / ".env")
        provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        model = os.getenv("LLM_MODEL", "").strip()
        if not model:
            if provider != "anthropic":
                raise ValueError("Set LLM_MODEL when LLM_PROVIDER is not 'anthropic'.")
            model = DEFAULT_ANTHROPIC_MODEL
        frozen_now = os.getenv("EMPORIO_NOW", "").strip()
        return cls(
            provider=provider,
            model=model,
            effort=os.getenv("LLM_EFFORT", DEFAULT_EFFORT).strip() or None,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            anthropic_workspace_id=os.getenv("ANTHROPIC_WORKSPACE_ID") or None,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
            data_dir=Path(os.getenv("EMPORIO_DATA_DIR", PROJECT_ROOT / "data")),
            db_path=Path(os.getenv("EMPORIO_DB_PATH", PROJECT_ROOT / ".data" / "emporio.sqlite3")),
            clock=Clock(parse_store_datetime(frozen_now) if frozen_now else None),
        )
