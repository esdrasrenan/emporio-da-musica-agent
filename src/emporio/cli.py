"""Command-line interface: chat with the agent in the terminal.

emporio chat                      # new conversation
emporio chat --show-tools         # also print every tool call
emporio chat --phone "(67) 99654-7821" --now 2026-03-24T10:30
emporio data-report               # data-quality findings as Markdown
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from rich.console import Console
from rich.markup import escape

from emporio.config import Clock, Settings, parse_store_datetime

console = Console(highlight=False)


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides: dict = {}
    if getattr(args, "now", None):
        overrides["clock"] = Clock(parse_store_datetime(args.now))
    if getattr(args, "model", None):
        overrides["model"] = args.model
    if getattr(args, "effort", None):
        overrides["effort"] = args.effort
    return replace(settings, **overrides)


def chat(args: argparse.Namespace) -> None:
    import anthropic
    import openai

    from emporio.app import build_agent

    settings = _settings(args)
    agent = build_agent(settings)
    session = agent.start_session(customer_phone=args.phone, session_id=args.session)
    show_tools = args.show_tools
    console.print(
        f"[bold]Empório da Música[/bold] - assistente virtual ({settings.provider}:{settings.model})\n"
        f"[dim]Sessão {session}. Comandos: /tools liga/desliga as ferramentas, /novo, /sair.[/dim]\n"
    )
    while True:
        try:
            text = console.input("[bold cyan]Você:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/sair", "/exit", "/quit"):
            break
        if text == "/tools":
            show_tools = not show_tools
            console.print(f"[dim]Ferramentas {'visíveis' if show_tools else 'ocultas'}.[/dim]")
            continue
        if text == "/novo":
            session = agent.start_session(customer_phone=args.phone)
            console.print(f"[dim]Nova sessão {session}.[/dim]")
            continue
        try:
            with console.status("[dim]Tom está digitando...[/dim]"):
                reply = agent.reply(session, text)
        except (anthropic.APIError, openai.APIError) as exc:
            console.print(f"[red]Falha ao falar com o modelo: {escape(str(exc))}[/red]")
            continue
        if show_tools:
            for call in reply.tool_calls:
                status = " [red](erro)[/red]" if call["is_error"] else ""
                arguments = escape(json.dumps(call["input"], ensure_ascii=False))
                console.print(f"[dim]  -> {call['name']} {arguments}[/dim]{status}")
        console.print(f"[bold green]Tom:[/bold green] {escape(reply.text)}\n")
    console.print(f"[dim]Conversa salva. Para retomar: emporio chat --session {session}[/dim]")


def data_report(args: argparse.Namespace) -> None:
    from emporio.data import load_store_data

    data = load_store_data(Settings.from_env().data_dir)
    print("| Table | Record | Issue | How the agent handles it |")
    print("|---|---|---|---|")
    for f in data.findings:
        print(f"| {f.table} | {f.record} | {f.issue} | {f.handling} |")


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="emporio", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    chat_parser = sub.add_parser("chat", help="interactive conversation")
    chat_parser.add_argument("--session", help="resume a saved conversation")
    chat_parser.add_argument("--phone", help="simulate a customer identified by the WhatsApp number")
    chat_parser.add_argument("--now", help="freeze the clock, e.g. 2026-03-24T10:30 (Campo Grande time)")
    chat_parser.add_argument("--model", help="override LLM_MODEL")
    chat_parser.add_argument("--effort", help="override LLM_EFFORT (Anthropic): low, medium, high")
    chat_parser.add_argument("--show-tools", action="store_true", help="print tool calls")
    chat_parser.set_defaults(func=chat)
    report_parser = sub.add_parser("data-report", help="print data-quality findings")
    report_parser.set_defaults(func=data_report)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["chat", *(argv or [])])
    args.func(args)


if __name__ == "__main__":
    main()
