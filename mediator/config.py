"""Configuration loading for Mediator.

Supports both local (LM Studio) and cloud (OpenAI-compatible) providers. Each
agent points at a named provider, so you can run everything locally for privacy
or send some/all agents to a stronger cloud model for reasoning power.

Secrets: API keys are NEVER stored in ``config.toml``. They are resolved from,
in order of precedence:
  1. an ``env:VARNAME`` reference in the provider's ``api_key`` field,
  2. a matching entry in the gitignored ``secrets.toml`` (written by ``setup``),
  3. a literal value (discouraged; only if you put one there yourself).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore
except ModuleNotFoundError:  # Python 3.10 and earlier
    import tomli as tomllib  # type: ignore


DEFAULT_CONFIG_PATH = Path("config.toml")
SECRETS_PATH = Path("secrets.toml")


class ConfigError(ValueError):
    """Raised when configuration is present but invalid."""


# Roles that don't need their own config block: they inherit a sensible primary agent's
# provider/model so configuring the core agents (incl. on the cloud) covers everything.
ROLE_FALLBACKS: dict[str, str] = {
    "prompt_engineer": "mediator",
    "assistant": "mediator",
    "architect": "mediator",
    "verifier": "adversary",
    "builder": "author",
    "adversary_security": "adversary",
    "adversary_spec": "adversary",
    "adversary_logic": "adversary",
}


@dataclass
class ProviderConfig:
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"  # placeholder is fine for local LM Studio
    is_local: bool = True

    def resolved_key(self) -> str:
        """Resolve ``env:VAR`` references to the actual environment value."""
        key = self.api_key or ""
        if key.startswith("env:"):
            return os.environ.get(key[4:], "")
        return key


@dataclass
class AgentConfig:
    provider: str = "local"
    model: str = ""  # empty => first model the provider reports (good for LM Studio)
    temperature: float = 0.5


@dataclass
class Config:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    max_rounds: int = 3
    timeout_seconds: float = 120.0

    def agent(self, role: str) -> AgentConfig:
        if role in self.agents:
            return self.agents[role]
        fallback = ROLE_FALLBACKS.get(role)
        if fallback and fallback in self.agents:
            return self.agents[fallback]
        return AgentConfig()

    def provider_for(self, role: str) -> ProviderConfig:
        name = self.agent(role).provider
        return self.providers.get(name, ProviderConfig())

    def has_cloud_agent(self) -> bool:
        return any(not self.provider_for(r).is_local for r in self.agents)


def _merge_secret_keys(providers: dict[str, ProviderConfig], path: Path) -> None:
    """Overlay api_key values from secrets.toml onto matching providers."""
    if not path.exists():
        return
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    for name, cfg in data.get("providers", {}).items():
        if name in providers and cfg.get("api_key"):
            providers[name].api_key = cfg["api_key"]


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)

    # Providers ------------------------------------------------------------
    providers: dict[str, ProviderConfig] = {}
    for name, cfg in data.get("providers", {}).items():
        providers[name] = ProviderConfig(
            base_url=cfg.get("base_url", ProviderConfig.base_url),
            api_key=cfg.get("api_key", ""),
            is_local=bool(cfg.get("is_local", False)),
        )
    # Always guarantee a usable "local" provider exists.
    if "local" not in providers:
        providers["local"] = ProviderConfig()
    _merge_secret_keys(providers, SECRETS_PATH)

    # Agents ---------------------------------------------------------------
    agents: dict[str, AgentConfig] = {}
    for role, cfg in data.get("agents", {}).items():
        agents[role] = AgentConfig(
            provider=cfg.get("provider", "local"),
            model=cfg.get("model", ""),
            temperature=float(cfg.get("temperature", 0.5)),
        )
    if not agents:
        agents = {
            "author": AgentConfig(provider="local", temperature=0.4),
            "adversary": AgentConfig(provider="local", temperature=0.7),
            "mediator": AgentConfig(provider="local", temperature=0.2),
        }

    debate_raw = data.get("debate", {})
    lm_raw = data.get("lmstudio", {})  # legacy/back-compat for timeout
    timeout = float(lm_raw.get("timeout_seconds", 120.0))

    config = Config(
        providers=providers,
        agents=agents,
        max_rounds=int(debate_raw.get("max_rounds", 3)),
        timeout_seconds=timeout,
    )
    _validate(config)
    return config


def _validate(config: Config) -> None:
    """Raise ConfigError with all problems found, or return silently."""
    errors: list[str] = []

    if config.max_rounds < 1:
        errors.append("debate.max_rounds must be >= 1")
    if config.timeout_seconds <= 0:
        errors.append("lmstudio.timeout_seconds must be > 0")

    for name, p in config.providers.items():
        if not p.base_url.startswith(("http://", "https://")):
            errors.append(
                f"providers.{name}.base_url must start with http:// or https:// "
                f"(got {p.base_url!r})"
            )

    for role, a in config.agents.items():
        if a.provider not in config.providers:
            errors.append(
                f"agents.{role}.provider '{a.provider}' is not defined under [providers]"
            )
        if not (0.0 <= a.temperature <= 2.0):
            errors.append(
                f"agents.{role}.temperature must be between 0 and 2 (got {a.temperature})"
            )

    for role, a in config.agents.items():
        p = config.providers.get(a.provider)
        if p and not p.is_local and not p.resolved_key():
            errors.append(
                f"agents.{role} uses cloud provider '{a.provider}' but no API key was found. "
                "Run 'python -m mediator setup' again, or set the provider's env var."
            )

    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))
