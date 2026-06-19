"""Lightweight codebase index for retrieval (Phase 15).

A dependency-free BM25 index over the workspace's code files. It powers:
  - semantic-ish file search ("which files are about auth?"),
  - the agent ``search`` tool (tools.py),
  - smarter candidate selection for multi-file edits (rank by relevance to the request
    instead of just taking the first N files).

It is lexical, not neural: it tokenizes identifiers (splitting camelCase / snake_case) and
ranks files with BM25. That keeps it fully local, instant, and free of an embedding model,
while still surfacing the right files for a query.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SUBWORD_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")

_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    """Tokens = lowercased identifiers plus their camel/snake subwords."""
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        w = m.group(0)
        wl = w.lower()
        out.append(wl)
        for part in _SUBWORD_RE.findall(w):
            pl = part.lower()
            if pl and pl != wl:
                out.append(pl)
        for piece in wl.split("_"):
            if piece and piece != wl:
                out.append(piece)
    return out


@dataclass
class _Doc:
    path: str
    length: int
    tf: dict[str, int]


@dataclass
class CodeIndex:
    root: Path
    docs: list[_Doc] = field(default_factory=list)
    df: dict[str, int] = field(default_factory=dict)
    avgdl: float = 0.0

    def search(self, query: str, top_k: int = 8) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms or not self.docs:
            return []
        n = len(self.docs)
        idf: dict[str, float] = {}
        for t in set(terms):
            df = self.df.get(t, 0)
            if df:
                idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
        scored: list[tuple[str, float]] = []
        for d in self.docs:
            score = 0.0
            for t in terms:
                if t not in idf:
                    continue
                tf = d.tf.get(t, 0)
                if not tf:
                    continue
                denom = tf + _K1 * (1 - _B + _B * d.length / (self.avgdl or 1))
                score += idf[t] * (tf * (_K1 + 1)) / denom
            if score > 0:
                scored.append((d.path, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def build_index(root: Path, skip_dirs: set[str], exts: set[str],
                max_files: int = 4000, max_bytes: int = 2_000_000) -> CodeIndex:
    """Walk ``root`` and build a BM25 index over code files."""
    idx = CodeIndex(root=root)
    total_len = 0
    for p in sorted(root.rglob("*")):
        if len(idx.docs) >= max_files:
            break
        rel_parts = p.relative_to(root).parts
        if any(part in skip_dirs or part.startswith(".") for part in rel_parts):
            continue
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        toks = tokenize(text)
        if not toks:
            continue
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        rel = str(p.relative_to(root)).replace("\\", "/")
        idx.docs.append(_Doc(path=rel, length=len(toks), tf=tf))
        total_len += len(toks)
        for t in tf:
            idx.df[t] = idx.df.get(t, 0) + 1
    idx.avgdl = (total_len / len(idx.docs)) if idx.docs else 0.0
    return idx
