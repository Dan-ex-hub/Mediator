"""Command-line entrypoint for Mediator.

Commands:
    setup   First-run wizard: choose Privacy (local) or Reasoning (cloud).
    ping    Verify every configured provider is reachable.

Usage:
    python -m mediator setup
    python -m mediator ping [--config config.toml]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .client import LLMError, client_for_role
from .config import ConfigError, load_config
from .debate_log import DebateLog
from .orchestrator import Orchestrator
from .profiles import profile_for
from .setup_wizard import run_setup
from .util import detect_language

console = Console()


def _load(config_path: str):
    """Load config, printing friendly messages on missing file or invalid config.

    Returns the Config, or None if it could not be loaded.
    """
    if not Path(config_path).exists():
        console.print(
            f"[yellow]No {config_path} found. Run [bold]python -m mediator setup[/bold] first.[/yellow]"
        )
        return None
    try:
        return load_config(config_path)
    except ConfigError as exc:
        console.print(f"[bold red]Config error:[/bold red] {exc}")
        return None


def cmd_setup(args: argparse.Namespace) -> int:
    return run_setup(console, config_path=Path(args.config))


def cmd_ping(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if config is None:
        return 1

    if config.has_cloud_agent():
        console.print(
            "[bold yellow]Privacy notice:[/bold yellow] one or more agents use a cloud "
            "provider. Code sent to those agents leaves your machine.\n"
        )

    ok = True
    tested: set[tuple[str, str]] = set()
    for role in config.agents:
        client, model = client_for_role(config, role)
        provider = config.provider_for(role)
        key = (provider.base_url, model)
        if key in tested:
            continue
        tested.add(key)

        scope = "local" if provider.is_local else "CLOUD"
        console.print(f"[bold]{role}[/bold] -> {scope} @ {provider.base_url} "
                      f"(model: {model or 'auto'})")
        try:
            reply = client.chat(
                messages=[{"role": "user",
                           "content": "Reply with one short sentence to confirm you work."}],
                model=model,
            )
            console.print(f"  [green]OK[/green]: {reply.strip()[:120]}")
        except LLMError as exc:
            console.print(f"  [red]FAILED[/red]: {exc}")
            ok = False

    if ok:
        console.print("\n[bold green]All configured providers are reachable.[/bold green]")
        return 0
    console.print("\n[bold red]One or more providers failed. See messages above.[/bold red]")
    return 1


def cmd_review(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if config is None:
        return 1

    target = Path(args.file)
    if not target.exists() or not target.is_file():
        console.print(f"[red]File not found:[/red] {target}")
        return 1
    try:
        code = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        console.print(f"[red]Could not read {target}:[/red] {exc}")
        return 1
    if not code.strip():
        console.print(f"[yellow]{target} is empty — nothing to review.[/yellow]")
        return 1

    if args.rounds is not None:
        config.max_rounds = args.rounds
    if not config.provider_for("author").is_local or not config.provider_for("adversary").is_local:
        console.print(
            "[bold yellow]Privacy notice:[/bold yellow] an agent uses a cloud "
            "provider. This file's contents will be sent off your machine.\n"
        )

    task = args.task
    if args.refine:
        from .agent import refine_prompt
        console.print("[dim]Refining the task into a structured brief...[/dim]")
        try:
            task = refine_prompt(config, args.task)
        except LLMError as exc:
            console.print(f"[bold red]Prompt refinement failed:[/bold red] {exc}")
            return 1
        if not args.quiet:
            console.print(Panel(Markdown(task), title="Refined task",
                                border_style="magenta", title_align="left"))

    if not args.quiet:
        console.print(
            f"[bold]Debate on[/bold] [cyan]{target.name}[/cyan]  "
            f"(max rounds: {config.max_rounds})\n"
        )

    log = DebateLog(console=console, live=not args.quiet)
    orchestrator = Orchestrator(config, log)
    try:
        result = orchestrator.run(task, target.name, code, detect_language(target))
    except LLMError as exc:
        console.print(f"[bold red]Debate failed:[/bold red] {exc}")
        return 1

    med = result.mediator
    sec = (med.security_verdict if med else "") or "?"
    req = (med.requirement_verdict if med else "") or "?"

    # Persist the full transcript.
    providers = ", ".join(
        f"{r}={config.provider_for(r).base_url if not config.provider_for(r).is_local else 'local'}"
        for r in config.agents
    )
    meta = {
        "file": target.name,
        "task": task,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": str(result.rounds_run),
        "verdict": f"Security: {sec}, Requirement: {req}",
        "providers": providers,
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = Path("logs") / f"{stamp}_{target.stem}.md"
    saved = log.save(log_path, meta)

    # In quiet mode the turns weren't printed live; show the final result now.
    if args.quiet and med is not None:
        if med.final_code is not None:
            console.print(Panel(Markdown(f"```{detect_language(target)}\n{med.final_code}\n```"),
                                title="FINAL_CODE", border_style="cyan", title_align="left"))
        else:
            console.print(Panel(Markdown(med.raw), title="Mediator (raw)",
                                border_style="cyan", title_align="left"))

    if not args.quiet:
        if result.stopped_early:
            console.print(
                f"\n[bold green]Adversary conceded[/bold green] after {result.rounds_run} round(s); "
                "Mediator produced the final version above."
            )
        else:
            console.print(
                f"\n[bold]Debate complete[/bold] after {result.rounds_run} round(s); "
                "Mediator produced the final version above."
            )

    if med is not None:
        sec_color = "green" if sec == "PASS" else ("red" if sec == "FAIL" else "yellow")
        req_color = "green" if req == "MET" else ("red" if req == "NOT MET" else "yellow")
        console.print(
            f"\n[bold]Verdict[/bold]  "
            f"Security: [{sec_color}]{sec}[/{sec_color}]   "
            f"Requirement: [{req_color}]{req}[/{req_color}]"
        )
        if med.final_code is None and not args.quiet:
            console.print(
                "[yellow]Note: could not extract a fenced FINAL_CODE block from the "
                "Mediator's reply — see the Mediator panel above for the full text.[/yellow]"
            )

    console.print(f"[dim]Transcript saved to {saved}[/dim]")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if config is None:
        return 1

    console.print(f"[bold]Effective configuration[/bold] ({args.config})\n")
    console.print(f"  max_rounds:      {config.max_rounds}")
    console.print(f"  timeout_seconds: {config.timeout_seconds:g}\n")

    console.print("[bold]Providers[/bold]")
    for name, p in config.providers.items():
        scope = "local" if p.is_local else "CLOUD"
        if p.is_local:
            key_state = "n/a"
        else:
            key_state = "[green]set[/green]" if p.resolved_key() else "[red]MISSING[/red]"
        console.print(f"  {name}: {scope} @ {p.base_url}  (api key: {key_state})")

    console.print("\n[bold]Agents[/bold]")
    for role, a in config.agents.items():
        prof = profile_for(a.model)
        console.print(
            f"  {role}: provider={a.provider}  "
            f"model={a.model or 'auto'}  temperature={a.temperature:g}  "
            f"[dim]dialect={prof.dialect} ({prof.label})[/dim]"
        )
    if config.has_cloud_agent():
        console.print(
            "\n[yellow]Note: at least one agent uses the cloud; its inputs leave your machine.[/yellow]"
        )
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if config is None:
        return 1

    raw = args.text
    if not raw:
        console.print("[cyan]Enter your request (end with an empty line):[/cyan]")
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
        except EOFError:
            pass
        raw = "\n".join(lines).strip()
    if not raw:
        console.print("[yellow]No request provided.[/yellow]")
        return 1

    from .agent import refine_prompt
    console.print("[dim]Refining into a structured brief...[/dim]\n")
    try:
        refined = refine_prompt(config, raw)
    except LLMError as exc:
        console.print(f"[bold red]Prompt refinement failed:[/bold red] {exc}")
        return 1

    console.print(Panel(Markdown(refined), title="Refined prompt",
                        border_style="magenta", title_align="left"))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    if _load(args.config) is None:
        return 1
    from .webui import serve
    console.print(
        f"[bold green]Mediator web UI[/bold green] -> http://{args.host}:{args.port}\n"
        "[dim]Press Ctrl+C to stop.[/dim]"
    )
    serve(config_path=args.config, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mediator",
        description="Local-first multi-agent debate system for code review.",
    )
    parser.add_argument("--config", default="config.toml",
                        help="Path to the config file (default: config.toml).")
    sub = parser.add_subparsers(dest="command", required=True)

    setup_p = sub.add_parser("setup", help="First-run wizard (choose Privacy or Reasoning).")
    setup_p.set_defaults(func=cmd_setup)

    ping_p = sub.add_parser("ping", help="Check that all configured providers are reachable.")
    ping_p.set_defaults(func=cmd_ping)

    review_p = sub.add_parser("review", help="Run an Author vs Adversary debate on a code file.")
    review_p.add_argument("file", help="Path to the code file to review.")
    review_p.add_argument(
        "--task", default="Review this code.",
        help="What you want done (default: 'Review this code.').",
    )
    review_p.add_argument(
        "--rounds", type=int, default=None,
        help="Override max debate rounds from config (e.g. --rounds 2).",
    )
    review_p.add_argument(
        "--quiet", action="store_true",
        help="Suppress the live debate; show only the final result. (Full transcript still saved.)",
    )
    review_p.add_argument(
        "--refine", action="store_true",
        help="First rewrite the task into a structured brief via the Prompt Engineer.",
    )
    review_p.set_defaults(func=cmd_review)

    prompt_p = sub.add_parser("prompt", help="Rewrite a rough request into an AI-friendly brief.")
    prompt_p.add_argument("text", nargs="?", default="",
                          help="The request to refine (omit to type it interactively).")
    prompt_p.set_defaults(func=cmd_prompt)

    config_p = sub.add_parser("config", help="Show the effective configuration.")
    config_p.set_defaults(func=cmd_config)

    web_p = sub.add_parser("web", help="Launch the web UI.")
    web_p.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1).")
    web_p.add_argument("--port", type=int, default=8000, help="Port (default 8000).")
    web_p.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
