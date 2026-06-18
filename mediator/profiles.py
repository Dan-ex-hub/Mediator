"""Model profiles: adapt prompts to each model's preferred dialect.

Different model families follow instructions best in different styles — Anthropic
models track XML-tagged prompts very precisely, while OpenAI/Gemini/local models
do well with markdown sections. A profile tweaks how we *present* instructions and
context to a model.

Crucially, profiles never change the REQUIRED OUTPUT LABELS (FINAL_CODE, VERDICT,
SUMMARY, RESIDUAL_RISKS, NO_CRITICAL_ISSUES). The multi-agent debate relies on a
single shared protocol so agents on different models can understand each other and
the parser can read every reply. Profiles only adapt the *input* dialect and expose
capability flags; each model adapts TO our protocol, not the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    dialect: str  # "markdown" | "xml"
    supports_structured: bool = False  # native JSON/structured output available
    supports_tools: bool = False       # native tool/function calling available

    def decorate_system(self, base_prompt: str) -> str:
        if self.dialect == "xml":
            return (
                base_prompt
                + "\n\nThis model follows XML-structured prompts best. Organize your "
                "reasoning with tags such as <analysis></analysis> when helpful. "
                "IMPORTANT: keep every required output label (FINAL_CODE, VERDICT, "
                "SUMMARY, RESIDUAL_RISKS, NO_CRITICAL_ISSUES) EXACTLY as written and "
                "un-wrapped, so it can be parsed."
            )
        return base_prompt

    def decorate_user(self, content: str) -> str:
        if self.dialect == "xml":
            return f"<context>\n{content}\n</context>\n\nRespond following your instructions."
        return content


# Default for anything unrecognized (covers most local models: qwen, llama, mistral…).
GENERIC = ModelProfile("generic", "Generic / local", "markdown", False, False)

# Matched by substring against the (lowercased) model name. Order matters.
_REGISTRY: list[tuple[tuple[str, ...], ModelProfile]] = [
    (("claude", "anthropic", "opus", "sonnet", "haiku"),
     ModelProfile("anthropic", "Anthropic (Claude)", "xml", True, True)),
    (("gpt", "openai", "o1", "o3", "o4", "chatgpt"),
     ModelProfile("openai", "OpenAI", "markdown", True, True)),
    (("gemini", "palm", "bison", "gemma"),
     ModelProfile("google", "Google (Gemini)", "markdown", True, True)),
    (("qwen", "llama", "mistral", "mixtral", "phi", "deepseek", "codestral"),
     ModelProfile("local", "Local / open model", "markdown", False, False)),
]


def profile_for(model: str) -> ModelProfile:
    """Pick the best-matching profile for a model name (empty => generic/local)."""
    m = (model or "").lower()
    for keys, profile in _REGISTRY:
        if any(k in m for k in keys):
            return profile
    return GENERIC
