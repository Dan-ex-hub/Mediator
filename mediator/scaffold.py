"""Project generation (Phase 11): plan a project, then build it file by file.

The Architect produces a JSON build plan; the Builder writes each file. This
module only PLANS and GENERATES content — writing to disk and running setup/run
commands happens in the web layer, where files land inside the workspace and
commands go through the Phase 10 approval gate (never auto-run).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .agent import build_agent
from .config import Config
from .util import extract_last_code_block

MAX_FILES = 14


@dataclass
class FileSpec:
    path: str
    purpose: str = ""


@dataclass
class ProjectPlan:
    name: str
    stack: str
    files: list[FileSpec] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    run: list[str] = field(default_factory=list)
    raw: str = ""


def _safe_name(name: str) -> str:
    name = (name or "project").strip().lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-._")
    return name or "project"


def _safe_rel(path: str) -> str | None:
    """Normalize a planned relative path and reject anything that escapes."""
    p = (path or "").strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p.split("/"):
        return None
    return p


def parse_plan(text: str) -> ProjectPlan:
    """Extract the JSON build plan from the Architect's reply (lenient)."""
    block = extract_last_code_block(text)
    candidate = block or text
    data = {}
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = {}

    files: list[FileSpec] = []
    for f in data.get("files", [])[:MAX_FILES]:
        if isinstance(f, dict):
            rel = _safe_rel(f.get("path", ""))
            if rel:
                files.append(FileSpec(rel, str(f.get("purpose", ""))))
        elif isinstance(f, str):
            rel = _safe_rel(f)
            if rel:
                files.append(FileSpec(rel))

    def _cmds(key):
        v = data.get(key, [])
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v if str(x).strip()]

    return ProjectPlan(
        name=_safe_name(data.get("name", "project")),
        stack=str(data.get("stack", "")),
        files=files,
        setup=_cmds("setup"),
        run=_cmds("run"),
        raw=text,
    )


def plan_project(config: Config, request: str) -> ProjectPlan:
    agent = build_agent(config, "architect")
    reply = agent.respond(f"PROJECT REQUEST:\n{request}\n\nProduce the build plan now.")
    return parse_plan(reply)


def generate_file(config: Config, request: str, plan: ProjectPlan,
                  spec: FileSpec) -> str:
    """Ask the Builder for the full contents of one planned file."""
    agent = build_agent(config, "builder")
    manifest = "\n".join(f"- {f.path}: {f.purpose}" for f in plan.files)
    prompt = (
        f"ORIGINAL REQUEST:\n{request}\n\n"
        f"PROJECT: {plan.name}  (stack: {plan.stack})\n"
        f"PLANNED FILES:\n{manifest}\n\n"
        f"Write the file now: {spec.path}\n"
        f"Purpose: {spec.purpose}\n\n"
        "Output ONLY its complete contents in a single fenced code block."
    )
    reply = agent.respond(prompt)
    code = extract_last_code_block(reply)
    return code if code is not None else reply.strip()
