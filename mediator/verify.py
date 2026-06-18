"""Verification planning + command risk classification (Phase 10).

The Verifier agent PROPOSES shell commands; this module parses them and tags each
with a risk level so the UI can require extra confirmation for dangerous ones.

NOTHING here executes commands. Execution happens only via the user-approved
terminal endpoint, after an explicit click in the UI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .agent import build_agent
from .config import Config

NO_COMMANDS_TOKEN = "NO_COMMANDS"

# Patterns that indicate a destructive or high-impact command. Conservative on purpose.
_DANGEROUS = [
    r"\brm\s+-rf\b", r"\brm\s+-fr\b", r"\brmdir\s+/s\b", r"\bdel\s+/[sq]",
    r"\bformat\b", r"\bmkfs\b", r"\bdd\s+if=", r"\bdiskpart\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r":\(\)\s*\{", r"\bfork\b",
    r"\breg\s+delete\b", r"\brd\s+/s\b",
    r"\bchmod\s+-R\b", r"\bchown\s+-R\b",
    r"curl[^\n|]*\|\s*(sh|bash)", r"wget[^\n|]*\|\s*(sh|bash)",
    r"Invoke-Expression", r"\biex\b", r"powershell[^\n]*-enc",
    r">\s*/dev/sd", r"\bsudo\b", r"\bnpm\s+publish\b", r"\bgit\s+push\b.*--force",
]
_DANGEROUS_RE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS]


@dataclass
class ProposedCommand:
    command: str
    why: str
    risk: str  # "high" | "normal"


def classify_risk(command: str) -> str:
    """Return 'high' for destructive/dangerous commands, else 'normal'."""
    for rx in _DANGEROUS_RE:
        if rx.search(command):
            return "high"
    return "normal"


def parse_commands(text: str) -> list[ProposedCommand]:
    """Parse the Verifier's ``CMD: <command> ;; <why>`` lines (lenient)."""
    if NO_COMMANDS_TOKEN in text.upper():
        return []
    out: list[ProposedCommand] = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"(?:CMD\s*[:\-]\s*)(.+)", line, re.IGNORECASE)
        if not m:
            continue
        body = m.group(1).strip().strip("`")
        if ";;" in body:
            cmd, why = body.split(";;", 1)
        elif "#" in body:
            cmd, why = body.split("#", 1)
        else:
            cmd, why = body, ""
        cmd = cmd.strip()
        if cmd:
            out.append(ProposedCommand(cmd, why.strip(), classify_risk(cmd)))
    return out


def plan_verification(config: Config, task: str, filename: str, code: str,
                      language: str = "") -> list[ProposedCommand]:
    """Ask the Verifier agent for commands that would validate this code."""
    agent = build_agent(config, "verifier")
    prompt = (
        f"ORIGINAL REQUEST: {task}\n\n"
        f"FILE: {filename}\n```{language}\n{code}\n```\n\n"
        "Propose the shell commands worth running to verify this code, per your format."
    )
    reply = agent.respond(prompt)
    return parse_commands(reply)
