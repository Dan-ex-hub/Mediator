"""Read-only workspace tools + a tool-use loop for agents (Phase 16).

LLM "tool calling" here is provider-agnostic: rather than relying on native function
calling (which varies across local models), the agent emits a small JSON object to call a
tool, the loop runs it and feeds the observation back, repeating until the agent signals it
is ready to answer. All tools are READ-ONLY and confined to the workspace root, so an agent
can gather context but can never modify or escape the workspace.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .client import LLMClient

MAX_TOOL_ITERS = 5
_MAX_OBS_CHARS = 6000

TOOL_INSTRUCTIONS = """\

# Tools
You may inspect the workspace before answering. To call a tool, reply with ONLY a JSON
object on its own (no prose, no code fence):
  {"action": "read_file", "path": "relative/path.py"}
  {"action": "list_dir", "path": "subdir"}      // "" for the root
  {"action": "search", "query": "keywords or identifier"}
When you have enough information, reply with ONLY:
  {"action": "answer"}
Use ONE tool at a time. Paths are relative to the workspace root. Do not invent file
contents — read them. After {"action": "answer"} you will be asked to write the final reply.
"""


@dataclass
class ToolCall:
    action: str
    path: str = ""
    query: str = ""


class WorkspaceTools:
    """Read-only, sandboxed view of the workspace for agents."""

    def __init__(self, root: Path, skip_dirs: set[str], search_fn:
                 Callable[[str, int], list[tuple[str, float]]] | None = None) -> None:
        self.root = root.resolve()
        self.skip_dirs = skip_dirs
        self.search_fn = search_fn

    def _resolve(self, rel: str) -> Path | None:
        candidate = Path(rel) if rel else self.root
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        if candidate == self.root or self.root in candidate.parents:
            return candidate
        return None

    def read_file(self, rel: str) -> str:
        target = self._resolve(rel)
        if target is None or not target.is_file():
            return f"(no such file: {rel})"
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return f"(cannot read {rel}: binary or unreadable)"
        return text

    def list_dir(self, rel: str) -> str:
        target = self._resolve(rel)
        if target is None or not target.is_dir():
            return f"(no such directory: {rel})"
        names: list[str] = []
        try:
            for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name in self.skip_dirs or child.name.startswith("."):
                    continue
                names.append(child.name + ("/" if child.is_dir() else ""))
        except OSError:
            return f"(cannot list {rel})"
        return "\n".join(names) or "(empty)"

    def search(self, query: str) -> str:
        if not self.search_fn:
            return "(search unavailable)"
        hits = self.search_fn(query, 8)
        if not hits:
            return "(no matches)"
        return "\n".join(f"{path}  (score {score:.1f})" for path, score in hits)

    def run(self, call: ToolCall) -> str:
        if call.action == "read_file":
            obs = self.read_file(call.path)
        elif call.action == "list_dir":
            obs = self.list_dir(call.path)
        elif call.action == "search":
            obs = self.search(call.query)
        else:
            obs = f"(unknown tool: {call.action})"
        if len(obs) > _MAX_OBS_CHARS:
            obs = obs[:_MAX_OBS_CHARS] + "\n…[truncated]…"
        return obs


def parse_tool_call(text: str) -> ToolCall | None:
    """Parse a tool-call JSON from the model reply (lenient)."""
    candidate = text.strip()
    m = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    action = str(data.get("action", "")).lower()
    if action not in ("read_file", "list_dir", "search", "answer"):
        return None
    return ToolCall(action=action, path=str(data.get("path", "")),
                    query=str(data.get("query", "")))


def gather_with_tools(client: LLMClient, model: str, temperature: float,
                      system_prompt: str, messages: list[dict[str, str]],
                      tools: WorkspaceTools,
                      on_event: Callable[[dict], None] | None = None,
                      max_iters: int = MAX_TOOL_ITERS) -> list[dict[str, str]]:
    """Run the tool loop, returning the message list ready for a final answer.

    ``messages`` is the conversation so far (user/assistant turns, no system). Returns the
    same list extended with any tool calls + observations the agent made.
    """
    convo = [{"role": "system", "content": system_prompt + TOOL_INSTRUCTIONS}]
    convo.extend(messages)
    for _ in range(max_iters):
        reply = client.chat(convo, model=model, temperature=temperature)
        call = parse_tool_call(reply)
        if call is None or call.action == "answer":
            break
        obs = tools.run(call)
        if on_event:
            on_event({"type": "tool", "action": call.action,
                      "target": call.path or call.query})
        convo.append({"role": "assistant", "content": reply})
        convo.append({"role": "user",
                      "content": f"OBSERVATION ({call.action} {call.path or call.query}):\n{obs}"})
    # Strip the system message; caller re-adds its own for the final streamed answer.
    return convo[1:]
