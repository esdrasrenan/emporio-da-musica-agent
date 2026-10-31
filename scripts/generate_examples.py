"""Run the example conversations in examples/scenarios.yaml against the real
agent and write one Markdown transcript per scenario (tool calls included).

    python scripts/generate_examples.py
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml

from emporio.app import build_agent
from emporio.config import PROJECT_ROOT, Clock, Settings, parse_store_datetime
from emporio.evals import cost_usd
from emporio.llm.base import Usage

EXAMPLES_DIR = PROJECT_ROOT / "examples"
NOW = "2026-03-24T10:30"  # aligned with the dataset snapshot (README, Assumptions)
MAX_TOOL_OUTPUT = 1500


def _tool_block(call: dict) -> str:
    output = call["output"]
    with contextlib.suppress(json.JSONDecodeError):  # error results are plain text
        output = json.dumps(json.loads(output), ensure_ascii=False, indent=1)
    if len(output) > MAX_TOOL_OUTPUT:
        output = output[:MAX_TOOL_OUTPUT] + "\n... (truncated)"
    arguments = json.dumps(call["input"], ensure_ascii=False)
    status = " - error" if call["is_error"] else ""
    return (
        f"<details><summary><code>{call['name']}</code> {arguments}{status}</summary>\n\n"
        f"```json\n{output}\n```\n\n</details>\n"
    )


def render(scenario: dict, settings: Settings) -> str:
    agent = build_agent(settings)
    session = agent.start_session(customer_phone=scenario.get("phone"))
    lines = [
        f"# {scenario['title']}\n",
        "**What this conversation shows**\n",
        *[f"- {item}" for item in scenario["shows"]],
        "",
        f"> Real run of the agent. Model `{settings.model}` (effort `{settings.effort}`), clock frozen "
        f"at {NOW} in Campo Grande (see README, Assumptions). Tool calls are expanded under each "
        "reply; outputs longer than 1,500 characters are truncated here only.\n",
        "---\n",
    ]
    total = Usage()
    for message in scenario["turns"]:
        reply = agent.reply(session, message)
        total += reply.usage
        lines.append(f"**Cliente:** {message}\n")
        if reply.tool_calls:
            lines.append("".join(_tool_block(call) for call in reply.tool_calls))
        quoted = "\n".join(f"> {line}" if line else ">" for line in reply.text.splitlines())
        lines.append(f"**Tom (assistente):**\n\n{quoted}\n")
        lines.append(f"<sub>{reply.latency_ms / 1000:.1f} s, {len(reply.tool_calls)} tool call(s)</sub>\n")
    cost = cost_usd(settings.model, total)
    prompt_tokens = total.input_tokens + total.cache_read_tokens + total.cache_write_tokens
    lines.append("---\n")
    lines.append(
        f"<sub>{total.calls} model calls, {prompt_tokens} input tokens "
        f"({total.cache_read_tokens} from cache), {total.output_tokens} output tokens"
        + (f", about US$ {cost:.4f}" if cost is not None else "")
        + f". Generated {datetime.now():%Y-%m-%d}.</sub>\n"
    )
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = replace(Settings.from_env(), clock=Clock(parse_store_datetime(NOW)), db_path=Path(":memory:"))
    scenarios = yaml.safe_load((EXAMPLES_DIR / "scenarios.yaml").read_text(encoding="utf-8"))
    for scenario in scenarios:
        (EXAMPLES_DIR / scenario["file"]).write_text(render(scenario, settings), encoding="utf-8")
        print(f"wrote examples/{scenario['file']}")


if __name__ == "__main__":
    main()
