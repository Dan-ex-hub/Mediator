"""The Agent abstraction.

An Agent is a role + system prompt + a client/model to call. Agents are
stateless between calls: the caller (later, the Orchestrator) owns the debate
history and passes it in. This keeps roles composable and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import LLMClient, client_for_role
from .config import Config
from .profiles import GENERIC, ModelProfile, profile_for
from .prompts import ROLE_PROMPTS


@dataclass
class Agent:
    role: str
    system_prompt: str
    client: LLMClient
    model: str = ""
    temperature: float = 0.5
    profile: ModelProfile = GENERIC

    def respond(self, user_content: str,
                history: list[dict[str, str]] | None = None) -> str:
        """Produce a reply to ``user_content``, optionally with prior history.

        ``history`` is a list of ``{"role", "content"}`` messages representing the
        debate so far (excluding this agent's system prompt, which is always prepended).
        Prompts are adapted to the model's preferred dialect via its profile.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.profile.decorate_system(self.system_prompt)}
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": self.profile.decorate_user(user_content)})
        return self.client.chat(messages, model=self.model, temperature=self.temperature)


def build_agent(config: Config, role: str) -> Agent:
    """Construct an Agent for a role using config (provider, model, temperature)."""
    if role not in ROLE_PROMPTS:
        raise ValueError(f"Unknown agent role: {role!r}")
    client, model = client_for_role(config, role)
    agent_cfg = config.agent(role)
    return Agent(
        role=role,
        system_prompt=ROLE_PROMPTS[role],
        client=client,
        model=model,
        temperature=agent_cfg.temperature,
        profile=profile_for(model),
    )


def build_prompt_engineer(config: Config) -> Agent:
    """The Prompt Engineer reuses the Mediator's provider/model (the strongest role).

    It has no dedicated config block by default, so borrowing the Mediator's endpoint
    keeps it working in Privacy, Reasoning, and Hybrid modes alike.
    """
    client, model = client_for_role(config, "mediator")
    return Agent(
        role="prompt_engineer",
        system_prompt=ROLE_PROMPTS["prompt_engineer"],
        client=client,
        model=model,
        temperature=0.3,
        profile=profile_for(model),
    )


def refine_prompt(config: Config, raw_request: str) -> str:
    """Turn a rough human request into a structured, model-friendly brief."""
    agent = build_prompt_engineer(config)
    return agent.respond(
        "Rewrite the following request into the structured brief defined by your "
        f"instructions.\n\nUSER REQUEST:\n{raw_request}"
    )
