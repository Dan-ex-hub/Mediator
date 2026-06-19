"""The Orchestrator drives the structured debate.

Phase 3 implements the Author <-> Adversary loop with an early stop when the
Adversary reports no critical issues. The Mediator (Phase 4) consumes the
resulting DebateLog to produce a final reconciled answer.

Protocol (see ARCHITECTURE.md):
    Round 0 : Author gives an initial review of the code + task.
    Round r : Adversary attacks the latest code (security first, then requirement
              compliance); then the Author fixes or defends.
    Stop    : max_rounds reached, or Adversary emits NO_CRITICAL_ISSUES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .agent import build_agent
from .config import Config
from .debate_log import DebateLog
from .parsing import (Finding, MediatorResult, parse_findings, parse_mediator,
                      sort_findings)
from .util import extract_last_code_block

NO_ISSUES_TOKEN = "NO_CRITICAL_ISSUES"

# The Adversary is split into three focused lenses (Phase 19). Each is its own agent role
# that inherits the `adversary` provider/model unless separately configured, and each
# returns structured findings the Mediator weighs BY CATEGORY.
LENSES: list[tuple[str, str]] = [
    ("SECURITY", "adversary_security"),
    ("SPEC", "adversary_spec"),
    ("LOGIC", "adversary_logic"),
]

# Cap how much debate history is fed back into each agent prompt. Long files plus
# multi-round history can overflow a small local model's context window; we keep the
# most recent turns within this budget (see DebateLog.transcript_text).
TRANSCRIPT_CHAR_BUDGET = 16000

EventCb = Callable[[dict], None] | None


def _count_by_category(findings: list[Finding]) -> dict[str, int]:
    counts = {cat: 0 for cat, _ in LENSES}
    for f in findings:
        counts[f.category] = counts.get(f.category, 0) + 1
    return counts


def adversary_conceded(reply: str) -> bool:
    """True only if the Adversary emitted NO_CRITICAL_ISSUES as its own statement.

    A plain substring check is too loose — the token can appear inside a sentence
    (e.g. "this is not a NO_CRITICAL_ISSUES case"). Require it to lead a line, after
    stripping markdown emphasis/list/quote decoration.
    """
    for line in reply.splitlines():
        cleaned = line.strip().lstrip(">*-#").strip().strip("*`_ ").strip()
        if cleaned.upper().startswith(NO_ISSUES_TOKEN):
            return True
    return False


def summarize_project(config: Config, file_results: list[dict]) -> str:
    """Cross-file Mediator pass: an overall assessment from per-file verdicts."""
    if not file_results:
        return "No files were reviewed."
    lines = []
    for fr in file_results:
        lines.append(
            f"- {fr['file']}: Security={fr.get('security') or '?'}, "
            f"Requirement={fr.get('requirement') or '?'}\n"
            f"  Summary: {(fr.get('summary') or '').strip()[:600]}"
        )
    digest = "\n".join(lines)
    agent = build_agent(config, "mediator")
    prompt = (
        "You are the MEDIATOR doing a CROSS-FILE review of a project. Below are the "
        "per-file verdicts and summaries from individual debates.\n\n"
        f"{digest}\n\n"
        "Produce a concise PROJECT REPORT with these sections:\n"
        "OVERALL_HEALTH (one line: good / needs work / at risk)\n"
        "TOP_RISKS (the most important issues across files, ranked)\n"
        "CROSS_CUTTING (problems that span multiple files, if any)\n"
        "NEXT_STEPS (what to fix first)."
    )
    return agent.respond(prompt)


@dataclass
class DebateResult:
    log: DebateLog
    original_code: str
    latest_code: str  # best-known current version (Author's last code block, if any)
    rounds_run: int
    stopped_early: bool
    mediator: MediatorResult | None = None
    findings: list[Finding] = field(default_factory=list)


class Orchestrator:
    def __init__(self, config: Config, log: DebateLog) -> None:
        self.config = config
        self.log = log
        self.author = build_agent(config, "author")
        # Three focused adversary lenses instead of one vague "attack".
        self.lenses = [(cat, build_agent(config, role)) for cat, role in LENSES]
        self.mediator = build_agent(config, "mediator")
        self.max_rounds = config.max_rounds
        self.transcript_budget = TRANSCRIPT_CHAR_BUDGET

    # -- context builders -------------------------------------------------
    def _code_block(self, code: str, language: str) -> str:
        return f"```{language}\n{code}\n```"

    def _author_seed(self, task: str, filename: str, code: str, language: str) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"Code under review (FILE: {filename}):\n"
            f"{self._code_block(code, language)}\n\n"
            "Give your initial review: intent, key design choices, assumptions, and weak "
            "spots. If you see clear improvements, show an updated version in a fenced "
            "code block."
        )

    def _transcript(self) -> str:
        return self.log.transcript_text(max_chars=self.transcript_budget)

    def _lens_turn(self, category: str, task: str, filename: str, current_code: str,
                   language: str) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"FILE: {filename}\nLatest version of the code under review:\n"
            f"{self._code_block(current_code, language)}\n\n"
            f"Debate so far:\n{self._transcript()}\n\n"
            f"Apply your {category} lens to the latest code now and report findings in the "
            "required format. Stay strictly within your lens."
        )

    def _grouped_findings_text(self, lens_texts: dict[str, str]) -> str:
        parts = []
        for category, _ in LENSES:
            body = (lens_texts.get(category) or "").strip() or "(no findings)"
            parts.append(f"=== {category} FINDINGS ===\n{body}")
        return "\n\n".join(parts)

    def _author_rebuttal(self, task: str, filename: str, current_code: str,
                         language: str, lens_texts: dict[str, str]) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"Latest version of the code (FILE: {filename}):\n"
            f"{self._code_block(current_code, language)}\n\n"
            f"The adversary reviewed it through three lenses:\n"
            f"{self._grouped_findings_text(lens_texts)}\n\n"
            "Respond now: for each finding, either FIX the code (show the full updated "
            "version in a fenced code block) or DEFEND it with a concrete technical reason. "
            "Prioritize SECURITY findings, then SPEC, then LOGIC. Build on the LATEST "
            "version above, not the original."
        )

    def _mediator_turn(self, task: str, filename: str, original_code: str,
                       language: str, lens_texts: dict[str, str]) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"Original code (FILE: {filename}):\n"
            f"{self._code_block(original_code, language)}\n\n"
            f"Adversary findings, grouped by lens:\n"
            f"{self._grouped_findings_text(lens_texts)}\n\n"
            f"Full debate transcript:\n{self._transcript()}\n\n"
            "You are the MEDIATOR. Weigh the findings BY CATEGORY (resolve SECURITY first, "
            "then SPEC compliance, then LOGIC/edge-cases); for each, decide if it is valid "
            "and apply the fix, or reject it with a reason. Then produce your final answer "
            "in exactly four sections: FINAL_CODE, VERDICT, SUMMARY, RESIDUAL_RISKS."
        )

    # -- main loop --------------------------------------------------------
    def run(self, task: str, filename: str, code: str, language: str = "",
            on_event: EventCb = None) -> DebateResult:
        def emit(ev: dict) -> None:
            if on_event:
                on_event(ev)

        def thinking(role: str, round_no: int, lens: str = "") -> None:
            ev = {"type": "thinking", "role": role, "round": round_no}
            if lens:
                ev["lens"] = lens
            emit(ev)

        def turn(round_no: int, role: str, content: str, model: str,
                 lens: str = "") -> None:
            log_content = f"[{lens} LENS]\n{content}" if lens else content
            self.log.add(round_no, role, log_content, model)
            ev = {"type": "turn", "round": round_no, "role": role,
                  "content": content, "model": model}
            if lens:
                ev["lens"] = lens
            emit(ev)

        # Round 0 — Author's initial review.
        thinking("author", 0)
        seed = self._author_seed(task, filename, code, language)
        author_reply = self.author.respond(seed)
        turn(0, "author", author_reply, self.author.model)
        latest_code = extract_last_code_block(author_reply) or code

        stopped_early = False
        rounds_run = 0
        all_findings: list[Finding] = []
        last_lens_texts: dict[str, str] = {}
        for r in range(1, self.max_rounds + 1):
            rounds_run = r

            # Adversary attacks the latest code through three focused lenses.
            lens_texts: dict[str, str] = {}
            round_findings: list[Finding] = []
            for category, agent in self.lenses:
                thinking("adversary", r, lens=category)
                ctx = self._lens_turn(category, task, filename, latest_code, language)
                reply = agent.respond(ctx)
                lens_texts[category] = reply
                turn(r, "adversary", reply, agent.model, lens=category)
                round_findings.extend(parse_findings(reply, category))

            last_lens_texts = lens_texts
            all_findings.extend(round_findings)
            sorted_round = sort_findings(round_findings)
            emit({"type": "findings", "round": r,
                  "findings": [f.to_dict() for f in sorted_round],
                  "counts": _count_by_category(round_findings)})

            # If no lens found anything actionable, the code survived — stop.
            if not round_findings:
                stopped_early = True
                break

            # Author fixes or defends, building on the latest version.
            thinking("author", r)
            reb_ctx = self._author_rebuttal(task, filename, latest_code, language, lens_texts)
            author_reply = self.author.respond(reb_ctx)
            turn(r, "author", author_reply, self.author.model)
            latest_code = extract_last_code_block(author_reply) or latest_code

        # Final pass — the Mediator reconciles, weighing findings by category.
        thinking("mediator", rounds_run)
        med_ctx = self._mediator_turn(task, filename, code, language, last_lens_texts)
        med_reply = self.mediator.respond(med_ctx)
        turn(rounds_run, "mediator", med_reply, self.mediator.model)
        mediator_result = parse_mediator(med_reply)

        emit({"type": "result", "rounds_run": rounds_run, "stopped_early": stopped_early,
              "security": mediator_result.security_verdict,
              "requirement": mediator_result.requirement_verdict,
              "summary": mediator_result.summary,
              "final_code": mediator_result.final_code,
              "counts": _count_by_category(all_findings)})

        return DebateResult(
            log=self.log,
            original_code=code,
            latest_code=mediator_result.final_code or latest_code,
            rounds_run=rounds_run,
            stopped_early=stopped_early,
            mediator=mediator_result,
            findings=all_findings,
        )
