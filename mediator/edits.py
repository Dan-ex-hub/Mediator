"""Multi-file debated edits (Phase 13).

Take a change request against an EXISTING codebase and produce a coordinated, debated
change set:

  1. Planner   decides which files to modify/create (a JSON plan).
  2. Author    drafts the full new content of each planned file.
  3. Adversary critiques the whole proposed change set (security, requirement
     compliance, and CROSS-FILE issues: broken calls, inconsistent interfaces).
  4. Mediator  finalizes each file, applying the valid critique with final authority.

Every code-emitting step returns ONE file in ONE fenced block, so output parsing stays
as reliable as the rest of the system. Nothing is written to disk here — the web layer
streams each final file to the Phase 12 diff/apply UI, which writes via the
CSRF-protected /api/save.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .agent import Agent
from .client import client_for_role
from .config import Config
from .profiles import profile_for
from .prompts import (
    ADVERSARY_PROMPT,
    EDIT_AUTHOR_PROMPT,
    EDIT_FINALIZER_PROMPT,
    EDIT_PLANNER_PROMPT,
    EDIT_REPORT_PROMPT,
)
from .util import extract_last_code_block

MAX_CHANGES = 12
PLAN_SNIPPET_CHARS = 1200
CONTEXT_SNIPPET_CHARS = 1400
CRITIQUE_SNIPPET_CHARS = 2600


@dataclass
class FileChange:
    path: str
    action: str = "modify"  # "modify" | "create"
    reason: str = ""


@dataclass
class ChangePlan:
    summary: str = ""
    changes: list[FileChange] = field(default_factory=list)
    raw: str = ""


@dataclass
class ProposedFile:
    path: str
    action: str
    original: str
    proposed: str


def _agent(config: Config, tier: str, system_prompt: str,
           temperature: float | None = None) -> Agent:
    """Build an Agent on a configured provider tier with a custom edit prompt."""
    client, model = client_for_role(config, tier)
    temp = temperature if temperature is not None else config.agent(tier).temperature
    return Agent(role=tier, system_prompt=system_prompt, client=client, model=model,
                 temperature=temp, profile=profile_for(model))


def _norm_rel(path: str) -> str | None:
    p = (path or "").strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p.split("/"):
        return None
    return p


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]…"


def parse_plan(text: str) -> ChangePlan:
    """Extract the JSON change plan from the Planner's reply (lenient)."""
    block = extract_last_code_block(text)
    candidate = block or text
    data: dict = {}
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = {}

    changes: list[FileChange] = []
    seen: set[str] = set()
    for c in (data.get("changes") or [])[:MAX_CHANGES]:
        if not isinstance(c, dict):
            continue
        rel = _norm_rel(c.get("path", ""))
        if not rel or rel in seen:
            continue
        seen.add(rel)
        action = str(c.get("action", "modify")).lower()
        if action not in ("modify", "create"):
            action = "modify"
        changes.append(FileChange(rel, action, str(c.get("reason", ""))))
    return ChangePlan(summary=str(data.get("summary", "")), changes=changes, raw=text)


def plan_changes(config: Config, request: str, manifest: str) -> ChangePlan:
    """Ask the Planner which files to modify/create for this request."""
    agent = _agent(config, "mediator", EDIT_PLANNER_PROMPT, temperature=0.2)
    prompt = (
        f"CHANGE REQUEST:\n{request}\n\n"
        f"EXISTING FILES (path, then a content snippet):\n{manifest}\n\n"
        "Produce the change plan now as JSON."
    )
    return parse_plan(agent.respond(prompt))


def draft_file(config: Config, request: str, plan: ChangePlan, change: FileChange,
               original: str, context: str) -> str:
    """Author drafts the complete new content of one planned file."""
    agent = _agent(config, "author", EDIT_AUTHOR_PROMPT)
    manifest = "\n".join(f"- {c.path} ({c.action}): {c.reason}" for c in plan.changes)
    if change.action == "modify" and original:
        base = f"CURRENT CONTENT of {change.path}:\n```\n{original}\n```\n\n"
    else:
        base = f"This is a NEW file: {change.path}\n\n"
    prompt = (
        f"CHANGE REQUEST:\n{request}\n\n"
        f"OVERALL PLAN: {plan.summary}\nFiles in this change set:\n{manifest}\n\n"
        f"{base}"
        f"CONTEXT from related files (truncated):\n{context or '(none)'}\n\n"
        f"Write the COMPLETE new content of {change.path} now. Output ONLY that file's "
        "full contents in a single fenced code block."
    )
    reply = agent.respond(prompt)
    return extract_last_code_block(reply) or reply.strip()


def critique_changeset(config: Config, request: str,
                       proposed: list[ProposedFile]) -> str:
    """Adversary attacks the whole proposed change set, including cross-file issues."""
    agent = _agent(config, "adversary", ADVERSARY_PROMPT)
    parts = [
        f"=== {pf.path} ({pf.action}) ===\n{_truncate(pf.proposed, CRITIQUE_SNIPPET_CHARS)}"
        for pf in proposed
    ]
    body = "\n\n".join(parts)
    prompt = (
        f"ORIGINAL REQUEST: {request}\n\n"
        "The AUTHOR proposes the following MULTI-FILE change set. Attack it as a whole: "
        "security first, then whether it satisfies the request, then CROSS-FILE issues "
        "(inconsistent interfaces, broken imports/calls between these files, missing "
        "wiring, partial changes).\n\n"
        f"{body}\n\n"
        "List issues per your format. If the change set is sound and complete, reply "
        "NO_CRITICAL_ISSUES with a one-line reason."
    )
    return agent.respond(prompt)


def finalize_file(config: Config, request: str, change: FileChange, original: str,
                  proposed: str, critique: str) -> str:
    """Mediator produces the final content of one file, applying the critique."""
    agent = _agent(config, "mediator", EDIT_FINALIZER_PROMPT, temperature=0.2)
    if change.action == "modify" and original:
        base = f"ORIGINAL CONTENT of {change.path}:\n```\n{original}\n```\n\n"
    else:
        base = f"This is a NEW file: {change.path}\n\n"
    prompt = (
        f"CHANGE REQUEST:\n{request}\n\n"
        f"{base}"
        f"AUTHOR'S PROPOSED content for {change.path}:\n```\n{proposed}\n```\n\n"
        f"ADVERSARY'S CRITIQUE of the whole change set:\n"
        f"{_truncate(critique, CRITIQUE_SNIPPET_CHARS * 2)}\n\n"
        f"As MEDIATOR with final authority, produce the FINAL, complete content of "
        f"{change.path}. Apply the valid critique (security first) and keep it consistent "
        "with the other files. Output ONLY this file's full contents in a single fenced "
        "code block."
    )
    reply = agent.respond(prompt)
    return extract_last_code_block(reply) or reply.strip()


def summarize_changeset(config: Config, request: str, proposed: list[ProposedFile],
                        critique: str) -> str:
    """Mediator's concise report over the finished change set."""
    agent = _agent(config, "mediator", EDIT_REPORT_PROMPT, temperature=0.2)
    files = "\n".join(f"- {pf.path} ({pf.action})" for pf in proposed)
    prompt = (
        f"CHANGE REQUEST:\n{request}\n\nFILES CHANGED:\n{files}\n\n"
        f"ADVERSARY CRITIQUE (for reference):\n{_truncate(critique, CRITIQUE_SNIPPET_CHARS)}\n\n"
        "Give the change-set report now."
    )
    return agent.respond(prompt)
