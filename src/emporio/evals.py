"""Scenario evals: real conversations against a real model, graded by
deterministic checks on tool routing and on facts in the replies.

    python -m emporio.evals --model claude-haiku-5-5 --effort medium
    python -m emporio.evals --cases price-lookup,address --repeat 3
    python -m emporio.evals --report          # Markdown table of saved results

Cases live in evals/cases.yaml; results are saved to evals/results/.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

import yaml

from emporio.agent import Agent
from emporio.app import build_model
from emporio.catalog import Catalog
from emporio.config import PROJECT_ROOT, Clock, Settings, parse_store_datetime
from emporio.data import StoreData, load_store_data
from emporio.llm.base import Usage
from emporio.orders import OrderDesk
from emporio.policies import POLICY_FILE, PolicyManual
from emporio.storage import Storage
from emporio.text import fold
from emporio.tools import Toolbox

CASES_FILE = PROJECT_ROOT / "evals" / "cases.yaml"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
DEFAULT_NOW = "2026-03-24T10:30"  # aligned with the dataset snapshot (README, Assumptions)
EMOJI_RE = re.compile("[\U0001f300-\U0001faff☀-➿]")

# USD per million tokens: input, output, cache read, cache write (5 min).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}


@dataclass
class CaseResult:
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    turns: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    error: str | None = None


def cost_usd(model: str, usage: Usage | dict) -> float | None:
    if model not in PRICES:
        return None
    u = usage if isinstance(usage, dict) else usage.as_dict()
    price_in, price_out, price_read, price_write = PRICES[model]
    return (
        u["input_tokens"] * price_in
        + u["output_tokens"] * price_out
        + u["cache_read_tokens"] * price_read
        + u["cache_write_tokens"] * price_write
    ) / 1_000_000


def grade(case: dict, replies: list[str], tools: list[str]) -> list[str]:
    everything = fold("\n".join(replies))
    # Facts are expected in the final reply unless the case says any reply counts.
    target = everything if case.get("scope") == "all" else fold(replies[-1])
    # House style, checked on every case: WhatsApp formatting, no emojis.
    failures = ["emoji in reply" for r in replies if EMOJI_RE.search(r)][:1]
    failures += ["markdown bold (**) in reply" for r in replies if "**" in r][:1]
    failures += [f"tool not called: {t}" for t in case.get("tools", []) if t not in tools]
    if case.get("tools_any") and not set(case["tools_any"]) & set(tools):
        failures.append(f"none of these tools called: {case['tools_any']}")
    failures += [f"tool should not be called: {t}" for t in case.get("no_tools", []) if t in tools]
    failures += [
        f"{name} called {tools.count(name)}x (max {limit})"
        for name, limit in case.get("max_calls", {}).items()
        if tools.count(name) > limit
    ]
    failures += [f"missing: {s!r}" for s in case.get("include", []) if fold(s) not in target]
    for key, options in case.items():
        if key.startswith("include_any") and not any(fold(o) in target for o in options):
            failures.append(f"none of: {options}")
    failures += [f"must not say: {s!r}" for s in case.get("exclude", []) if fold(s) in everything]
    for trigger, required in case.get("together", []):  # e.g. promo price needs the original price
        if fold(trigger) in everything and fold(required) not in everything:
            failures.append(f"{trigger!r} mentioned without {required!r}")
    return failures


def run_case(case: dict, settings: Settings, data: StoreData, policies: PolicyManual) -> CaseResult:
    clock = Clock(parse_store_datetime(case.get("now", DEFAULT_NOW)))
    storage = Storage(":memory:")
    toolbox = Toolbox(data, Catalog(data), policies, OrderDesk(data), storage, clock)
    agent = Agent(build_model(settings), toolbox, storage, data, clock)
    session = agent.start_session(customer_phone=case.get("phone"))
    turns, usage = [], Usage()
    try:
        for message in case["turns"]:
            reply = agent.reply(session, message)
            usage += reply.usage
            turns.append(
                {
                    "user": message,
                    "reply": reply.text,
                    "tools": [c["name"] for c in reply.tool_calls],
                    "latency_ms": reply.latency_ms,
                }
            )
    except Exception as exc:  # report and keep going with the other cases
        return CaseResult(case["id"], False, [f"error: {exc}"], turns, usage.as_dict(), repr(exc))
    failures = grade(case, [t["reply"] for t in turns], [n for t in turns for n in t["tools"]])
    return CaseResult(case["id"], not failures, failures, turns, usage.as_dict())


def run(settings: Settings, case_ids: list[str] | None, repeat: int, workers: int) -> dict:
    cases = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    data = load_store_data(settings.data_dir)
    policies = PolicyManual.from_pdf(settings.data_dir / POLICY_FILE)
    jobs = [c for c in cases for _ in range(repeat)]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: run_case(c, settings, data, policies), jobs))
    elapsed = time.perf_counter() - started

    latencies = [t["latency_ms"] for r in results for t in r.turns]
    total = sum((Usage(**r.usage) for r in results if r.usage), Usage())
    cost = cost_usd(settings.model, total)
    return {
        "provider": settings.provider,
        "model": settings.model,
        "effort": settings.effort,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "cases": len(results),
            "passed": sum(r.passed for r in results),
            "pass_rate": round(sum(r.passed for r in results) / len(results), 3),
            "turn_latency_ms_avg": round(statistics.mean(latencies)) if latencies else None,
            "turn_latency_ms_p90": round(statistics.quantiles(latencies, n=10)[-1])
            if len(latencies) > 1
            else None,
            "llm_calls": total.calls,
            "tokens": total.as_dict(),
            "cost_usd_total": round(cost, 4) if cost is not None else None,
            "cost_usd_per_conversation": round(cost / len(results), 5) if cost is not None else None,
            "wall_clock_s": round(elapsed, 1),
        },
        "failed": [{"id": r.id, "failures": r.failures} for r in results if not r.passed],
        "cases": [asdict(r) for r in results],
    }


def report() -> str:
    rows = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if path.stem.endswith("_partial"):
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        s = result["summary"]
        failed = sorted({f["id"] for f in result["failed"]})
        runs = result.get("repeat", 1)
        rows.append(
            f"| {result['model']} | {result['effort']} | {runs} | {s['passed']}/{s['cases']} "
            f"({s['pass_rate']:.0%}) | {s['turn_latency_ms_avg'] / 1000:.1f} s | "
            f"{s['turn_latency_ms_p90'] / 1000:.1f} s | "
            + (f"${s['cost_usd_per_conversation'] * 1000:.2f}" if s["cost_usd_per_conversation"] else "-")
            + f" | {', '.join(failed) or '-'} |"
        )
    header = (
        "| Model | Effort | Runs per case | Passed | Avg turn latency | p90 turn latency "
        "| Cost per 1,000 conversations | Failed cases |\n|---|---|---|---|---|---|---|---|"
    )
    return "\n".join([header, *rows])


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m emporio.evals", description=__doc__.splitlines()[0])
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report", action="store_true", help="print the results table and exit")
    args = parser.parse_args(argv)
    if args.report:
        print(report())
        return

    settings = Settings.from_env()
    settings = replace(settings, model=args.model or settings.model, effort=args.effort or settings.effort)
    result = run(settings, args.cases.split(",") if args.cases else None, args.repeat, args.workers)
    result["repeat"] = args.repeat
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{settings.model}_{settings.effort or 'default'}"
    if args.repeat > 1:
        name += f"_x{args.repeat}"
    if args.cases:
        name += "_partial"
    out = RESULTS_DIR / f"{name}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print(
        f"{settings.model} ({settings.effort}): {s['passed']}/{s['cases']} passed, "
        f"avg turn {s['turn_latency_ms_avg']} ms, cost ${s['cost_usd_total']} -> {out.name}"
    )
    for failed in result["failed"]:
        print(f"  FAIL {failed['id']}: {'; '.join(failed['failures'])}")


if __name__ == "__main__":
    main()
