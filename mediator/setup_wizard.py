"""First-run setup wizard.

Presents the user with a clear choice up front:

    [1] Privacy    - everything runs locally via LM Studio.
    [2] Reasoning  - all agents use a cloud model (stronger, faster).
    [3] Hybrid     - Author + Adversary stay local, Mediator uses the cloud.

It then writes ``config.toml`` and, when a cloud key is provided, a gitignored
``secrets.toml``. API keys are never written into ``config.toml``.
"""

from __future__ import annotations

import getpass
from pathlib import Path

from rich.console import Console

from .config import SECRETS_PATH

# Known OpenAI-compatible cloud endpoints + a sensible default model each.
CLOUD_PRESETS: dict[str, dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
}

ROLES = ("author", "adversary", "mediator")
TEMPS = {"author": 0.4, "adversary": 0.7, "mediator": 0.2}


def _prompt(console: Console, text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    console.print(f"[cyan]{text}{suffix}:[/cyan] ", end="")
    try:
        value = input().strip()
    except EOFError:
        value = ""
    return value or default


def _choose_cloud(console: Console) -> tuple[str, str, str, str]:
    """Return (provider_name, base_url, model, api_key)."""
    console.print("\nWhich cloud provider?")
    console.print("  [1] OpenAI")
    console.print("  [2] OpenRouter")
    console.print("  [3] Groq")
    console.print("  [4] Custom (any OpenAI-compatible URL)")
    choice = _prompt(console, "Choose 1-4", "1")

    mapping = {"1": "openai", "2": "openrouter", "3": "groq"}
    if choice in mapping:
        name = mapping[choice]
        preset = CLOUD_PRESETS[name]
        base_url = preset["base_url"]
        default_model = preset["model"]
    else:
        name = "cloud"
        base_url = _prompt(console, "Base URL (must end in /v1)", "https://api.openai.com/v1")
        default_model = ""

    model = _prompt(console, "Model name", default_model)
    console.print("[cyan]Paste your API key (input hidden):[/cyan] ", end="")
    try:
        api_key = getpass.getpass("")
    except Exception:
        api_key = ""
    return name, base_url, model, api_key.strip()


def _write_config(path: Path, providers: dict[str, dict], agents: dict[str, dict],
                  max_rounds: int = 3, timeout: int = 600) -> None:
    lines: list[str] = []
    lines.append("[debate]")
    lines.append(f"max_rounds = {max_rounds}")
    lines.append("")
    lines.append("[lmstudio]")
    lines.append(f"timeout_seconds = {timeout}")
    lines.append("")
    for name, p in providers.items():
        lines.append(f"[providers.{name}]")
        lines.append(f'base_url = "{p["base_url"]}"')
        # api_key intentionally omitted for cloud (lives in secrets.toml).
        if p.get("api_key_inline"):
            lines.append(f'api_key = "{p["api_key_inline"]}"')
        lines.append(f"is_local = {str(p['is_local']).lower()}")
        lines.append("")
    for role, a in agents.items():
        lines.append(f"[agents.{role}]")
        lines.append(f'provider = "{a["provider"]}"')
        lines.append(f'model = "{a["model"]}"')
        lines.append(f"temperature = {a['temperature']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_secret(name: str, api_key: str) -> None:
    if not api_key:
        return
    block = f'[providers.{name}]\napi_key = "{api_key}"\n'
    existing = ""
    if SECRETS_PATH.exists():
        existing = SECRETS_PATH.read_text(encoding="utf-8")
    if f"[providers.{name}]" in existing:
        # Simple replace-whole-file to keep it predictable.
        SECRETS_PATH.write_text(block, encoding="utf-8")
    else:
        SECRETS_PATH.write_text((existing + "\n" + block).strip() + "\n", encoding="utf-8")


def _ensure_gitignore() -> None:
    gi = Path(".gitignore")
    needed = ["secrets.toml", "logs/", "__pycache__/", "*.pyc"]
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    missing = [n for n in needed if n not in existing]
    if missing:
        with gi.open("a", encoding="utf-8") as fh:
            if existing and existing[-1] != "":
                fh.write("\n")
            fh.write("\n".join(missing) + "\n")


def run_setup(console: Console, config_path: Path = Path("config.toml")) -> int:
    console.print("[bold]Mediator setup[/bold]\n")
    console.print("How do you want Mediator to run?\n")
    console.print("  [bold]1[/bold] Privacy    - 100% local via LM Studio. "
                  "Your code never leaves your machine. (Slower, uses your hardware.)")
    console.print("  [bold]2[/bold] Reasoning  - All agents use a cloud model. "
                  "Faster and smarter. Your code is sent to the provider.")
    console.print("  [bold]3[/bold] Hybrid     - Author + Adversary local, Mediator in the cloud.\n")
    choice = _prompt(console, "Choose 1-3", "1")

    local_provider = {"base_url": "http://localhost:1234/v1", "is_local": True,
                      "api_key_inline": "lm-studio"}

    if choice == "2":  # Reasoning — all cloud
        name, base_url, model, api_key = _choose_cloud(console)
        providers = {name: {"base_url": base_url, "is_local": False}}
        agents = {r: {"provider": name, "model": model, "temperature": TEMPS[r]} for r in ROLES}
        _write_config(config_path, providers, agents)
        _write_secret(name, api_key)
        _ensure_gitignore()
        _print_done(console, mode="Reasoning (cloud)", cloud=True)

    elif choice == "3":  # Hybrid
        console.print("\nMediator (the final reviewer) will use the cloud.")
        name, base_url, model, api_key = _choose_cloud(console)
        providers = {
            "local": local_provider,
            name: {"base_url": base_url, "is_local": False},
        }
        agents = {
            "author": {"provider": "local", "model": "", "temperature": TEMPS["author"]},
            "adversary": {"provider": "local", "model": "", "temperature": TEMPS["adversary"]},
            "mediator": {"provider": name, "model": model, "temperature": TEMPS["mediator"]},
        }
        _write_config(config_path, providers, agents)
        _write_secret(name, api_key)
        _ensure_gitignore()
        _print_done(console, mode="Hybrid (local + cloud Mediator)", cloud=True)

    else:  # Privacy — all local
        providers = {"local": local_provider}
        agents = {r: {"provider": "local", "model": "", "temperature": TEMPS[r]} for r in ROLES}
        _write_config(config_path, providers, agents)
        _ensure_gitignore()
        _print_done(console, mode="Privacy (100% local)", cloud=False)

    return 0


def _print_done(console: Console, mode: str, cloud: bool) -> None:
    console.print(f"\n[bold green]Setup complete:[/bold green] {mode}")
    console.print(f"Wrote [bold]config.toml[/bold].")
    if cloud:
        console.print("Wrote [bold]secrets.toml[/bold] (gitignored) with your API key.")
    if "local" in mode.lower() or "hybrid" in mode.lower():
        console.print(
            "\n[bold]Local model setup (LM Studio):[/bold]\n"
            "  1. Open LM Studio and download a model (e.g. Qwen2.5 Coder 7B, Q4_K_M).\n"
            "  2. Go to the Developer / Local Server tab and select that model.\n"
            "  3. Click 'Start Server' (serves on http://localhost:1234).")
    console.print("\nNow run: [bold]python -m mediator ping[/bold] to verify the connection.")
