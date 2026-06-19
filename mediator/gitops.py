"""Git integration (Phase 18).

A thin, SAFE wrapper over the git CLI, scoped to a repository root. Only
non-destructive, user-initiated operations are exposed: status, diff, stage/unstage,
commit, branch create/checkout, and push (to a new upstream). There are deliberately NO
force pushes, hard resets, or cleans — destructive history/worktree operations are out of
scope by design.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails or git is unavailable."""


def _run(root: Path, args: list[str], timeout: float = 30.0) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git command timed out.") from exc
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git command failed").strip())
    return proc.stdout


def is_repo(root: Path) -> bool:
    try:
        return _run(root, ["rev-parse", "--is-inside-work-tree"]).strip() == "true"
    except GitError:
        return False


def current_branch(root: Path) -> str:
    """Current branch name. Works even on a fresh repo with no commits (unborn HEAD)."""
    name = _run(root, ["branch", "--show-current"]).strip()
    return name or "(no branch)"


def status(root: Path) -> list[dict]:
    """Parse ``git status --porcelain`` into per-file change records."""
    out = _run(root, ["status", "--porcelain=v1"])
    files: list[dict] = []
    for line in out.splitlines():
        if len(line) < 3:
            continue
        x, y, rest = line[0], line[1], line[3:]
        path = rest
        if " -> " in path:  # rename
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        files.append({
            "path": path,
            "index": x,
            "work": y,
            "staged": x not in (" ", "?"),
            "untracked": x == "?" and y == "?",
        })
    return files


def diff(root: Path, path: str | None = None, staged: bool = False) -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    return _run(root, args)


def stage(root: Path, paths: list[str]) -> None:
    if paths:
        _run(root, ["add", "--", *paths])


def stage_all(root: Path) -> None:
    _run(root, ["add", "-A"])


def unstage(root: Path, paths: list[str]) -> None:
    if paths:
        _run(root, ["restore", "--staged", "--", *paths])


def commit(root: Path, message: str) -> str:
    return _run(root, ["commit", "-m", message])


def branches(root: Path) -> list[str]:
    out = _run(root, ["branch", "--format=%(refname:short)"])
    return [b.strip() for b in out.splitlines() if b.strip()]


def create_branch(root: Path, name: str) -> None:
    _run(root, ["checkout", "-b", name])


def checkout(root: Path, name: str) -> None:
    _run(root, ["checkout", name])


def log(root: Path, n: int = 20) -> list[dict]:
    out = _run(root, ["log", f"-{n}", "--pretty=format:%h%x09%an%x09%ar%x09%s"])
    entries: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            entries.append({"hash": parts[0], "author": parts[1],
                            "when": parts[2], "subject": parts[3]})
    return entries


def push(root: Path, set_upstream: bool = False, branch: str | None = None) -> str:
    """Push. With set_upstream, pushes the branch to origin and sets tracking.

    Never uses --force; safe by construction.
    """
    args = ["push"]
    if set_upstream and branch:
        args += ["-u", "origin", branch]
    return _run(root, args)
