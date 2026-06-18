"""DebateLog: the append-only record of a debate.

Stores every turn and prints it live, color-coded by role. Markdown/JSON export
is added in Phase 5; for now it keeps the structured turns in memory so the
Orchestrator and (later) the Mediator can consume the full transcript.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

ROLE_STYLE = {
    "author": "green",
    "adversary": "red",
    "mediator": "cyan",
}


@dataclass
class Turn:
    round: int
    role: str
    content: str
    model: str = ""
    timestamp: float = field(default_factory=time.time)


class DebateLog:
    def __init__(self, console: Console | None = None, live: bool = True) -> None:
        self.console = console or Console()
        self.live = live
        self.turns: list[Turn] = []

    def add(self, round_no: int, role: str, content: str, model: str = "") -> Turn:
        turn = Turn(round=round_no, role=role, content=content, model=model)
        self.turns.append(turn)
        if self.live:
            self._print(turn)
        return turn

    def _print(self, turn: Turn) -> None:
        style = ROLE_STYLE.get(turn.role, "white")
        title = f"Round {turn.round} · {turn.role.upper()}"
        if turn.model:
            title += f" ({turn.model})"
        self.console.print(
            Panel(Markdown(turn.content), title=title, border_style=style,
                  title_align="left")
        )

    def to_dicts(self) -> list[dict]:
        """Return the turns as plain dicts (for JSON / the web UI)."""
        return [
            {
                "round": t.round,
                "role": t.role,
                "content": t.content,
                "model": t.model,
                "timestamp": t.timestamp,
            }
            for t in self.turns
        ]

    def transcript_text(self) -> str:
        """Render the whole debate as plain text (for feeding back to agents)."""
        parts = []
        for t in self.turns:
            parts.append(f"--- {t.role.upper()} (round {t.round}) ---\n{t.content}")
        return "\n\n".join(parts)

    def to_markdown(self, meta: dict[str, str] | None = None) -> str:
        """Render the full debate as a standalone markdown document."""
        meta = meta or {}
        lines: list[str] = ["# Mediator debate", ""]
        for key in ("file", "task", "date", "rounds", "verdict", "providers"):
            if key in meta:
                lines.append(f"- **{key.capitalize()}**: {meta[key]}")
        lines.append("")
        for t in self.turns:
            stamp = datetime.fromtimestamp(t.timestamp).strftime("%H:%M:%S")
            heading = f"## Round {t.round} · {t.role.upper()}"
            if t.model:
                heading += f" ({t.model})"
            lines.append(heading)
            lines.append(f"*{stamp}*")
            lines.append("")
            lines.append(t.content.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def save(self, path: str | Path, meta: dict[str, str] | None = None) -> Path:
        """Write the markdown transcript to ``path``, creating parent dirs."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(meta), encoding="utf-8")
        return path
