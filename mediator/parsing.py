"""Lenient parsing of the Mediator's structured output.

The Mediator is asked to emit four sections: FINAL_CODE, VERDICT, SUMMARY,
RESIDUAL_RISKS. Smaller local models won't always follow this perfectly, so the
parser is forgiving: if a section is missing, its field is empty and the raw
text is always retained, so nothing is ever lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .util import extract_last_code_block

_SECTION_NAMES = ("FINAL_CODE", "VERDICT", "SUMMARY", "RESIDUAL_RISKS")
_HEADER_RE = re.compile(
    r"^[#>*\-\s]*(FINAL_CODE|VERDICT|SUMMARY|RESIDUAL_RISKS)[:*\s]*$",
    re.MULTILINE | re.IGNORECASE,
)

# Adversary-lens findings (Phase 19).
NO_ISSUES_LENS_TOKEN = "NO_ISSUES"
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
_SEVERITY_ALIASES = {
    "CRITICAL": "CRITICAL", "CRIT": "CRITICAL", "BLOCKER": "CRITICAL",
    "HIGH": "HIGH", "SEVERE": "HIGH", "MAJOR": "HIGH",
    "MEDIUM": "MEDIUM", "MED": "MEDIUM", "MODERATE": "MEDIUM",
    "LOW": "LOW", "MINOR": "LOW", "INFO": "LOW",
}


@dataclass
class Finding:
    category: str   # SECURITY | SPEC | LOGIC
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW
    title: str
    where: str = ""
    detail: str = ""
    fix: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "severity": self.severity, "title": self.title,
                "where": self.where, "detail": self.detail, "fix": self.fix}


def _norm_severity(text: str) -> str | None:
    key = re.sub(r"[^A-Za-z]", "", text or "").upper()
    return _SEVERITY_ALIASES.get(key)


def parse_findings(text: str, category: str) -> list[Finding]:
    """Parse an adversary lens's pipe-delimited findings (lenient).

    Each finding line is ``SEVERITY | TITLE | WHERE | DETAIL | FIX``. Lines that don't start
    with a recognizable severity (e.g. a header row or prose) are skipped. The raw text is
    retained by the caller, so nothing is lost even if parsing yields fewer fields.
    """
    if not text:
        return []
    findings: list[Finding] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("->*•·").strip()
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        severity = _norm_severity(cells[0])
        if severity is None:  # header row or non-finding line
            continue
        title = cells[1] if len(cells) > 1 else ""
        where = cells[2] if len(cells) > 2 else ""
        detail = cells[3] if len(cells) > 3 else ""
        fix = cells[4] if len(cells) > 4 else ""
        if not title and not detail:
            continue
        findings.append(Finding(category, severity, title, where, detail, fix))
    return findings


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))


@dataclass
class MediatorResult:
    final_code: str | None
    security_verdict: str  # "PASS" / "FAIL" / "" if unknown
    requirement_verdict: str  # "MET" / "NOT MET" / "" if unknown
    verdict_text: str
    summary: str
    residual_risks: str
    raw: str

    @property
    def shipped(self) -> bool:
        """True only if both verdicts are explicitly positive."""
        return self.security_verdict == "PASS" and self.requirement_verdict == "MET"


def _split_sections(text: str) -> dict[str, str]:
    matches = list(_HEADER_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).upper()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _read_verdict(verdict_text: str) -> tuple[str, str]:
    security, requirement = "", ""
    sec = re.search(r"security\s*[:\-]\s*(PASS|FAIL)", verdict_text, re.IGNORECASE)
    if sec:
        security = sec.group(1).upper()
    req = re.search(r"requirement\s*[:\-]\s*(NOT\s*MET|MET)", verdict_text, re.IGNORECASE)
    if req:
        requirement = "NOT MET" if "NOT" in req.group(1).upper() else "MET"
    return security, requirement


def parse_mediator(text: str) -> MediatorResult:
    sections = _split_sections(text)

    final_block = sections.get("FINAL_CODE", "")
    # Prefer a fenced block inside FINAL_CODE; fall back to any code block in the reply.
    final_code = extract_last_code_block(final_block) or extract_last_code_block(text)

    verdict_text = sections.get("VERDICT", "")
    security, requirement = _read_verdict(verdict_text or text)

    return MediatorResult(
        final_code=final_code,
        security_verdict=security,
        requirement_verdict=requirement,
        verdict_text=verdict_text,
        summary=sections.get("SUMMARY", ""),
        residual_risks=sections.get("RESIDUAL_RISKS", ""),
        raw=text,
    )
