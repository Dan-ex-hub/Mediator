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

from dataclasses import dataclass
from typing import Callable

from .agent import build_agent
from .config import Config
from .debate_log import DebateLog
from .parsing import MediatorResult, parse_mediator
from .util import extract_last_code_block

NO_ISSUES_TOKEN = "NO_CRITICAL_ISSUES"

EventCb = Callable[[dict], None] | None


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


class Orchestrator:
    def __init__(self, config: Config, log: DebateLog) -> None:
        self.config = config
        self.log = log
        self.author = build_agent(config, "author")
        self.adversary = build_agent(config, "adversary")
        self.mediator = build_agent(config, "mediator")
        self.max_rounds = config.max_rounds

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

    def _adversary_turn(self, task: str, filename: str, current_code: str,
                        language: str) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"FILE: {filename}\nLatest version of the code under review:\n"
            f"{self._code_block(current_code, language)}\n\n"
            f"Debate so far:\n{self.log.transcript_text()}\n\n"
            "It is your turn, ADVERSARY. Attack this code now per your instructions: "
            "security first, then whether it meets the ORIGINAL REQUEST. "
            f"If nothing critical remains and it meets the request, reply exactly "
            f"{NO_ISSUES_TOKEN}."
        )

    def _author_rebuttal(self, task: str, filename: str, original_code: str,
                         language: str) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"Original code (FILE: {filename}):\n"
            f"{self._code_block(original_code, language)}\n\n"
            f"Debate so far:\n{self.log.transcript_text()}\n\n"
            "The ADVERSARY just raised the issues above. Respond now: for each point, "
            "either FIX the code (show the full updated version in a fenced code block) "
            "or DEFEND it with a concrete technical reason."
        )

    def _mediator_turn(self, task: str, filename: str, original_code: str,
                       language: str) -> str:
        return (
            f"ORIGINAL REQUEST: {task}\n\n"
            f"Original code (FILE: {filename}):\n"
            f"{self._code_block(original_code, language)}\n\n"
            f"Full debate transcript:\n{self.log.transcript_text()}\n\n"
            "You are the MEDIATOR. Reconcile the debate and produce your final answer now "
            "in exactly four sections: FINAL_CODE, VERDICT, SUMMARY, RESIDUAL_RISKS."
        )

    # -- main loop --------------------------------------------------------
    def run(self, task: str, filename: str, code: str, language: str = "",
            on_event: EventCb = None) -> DebateResult:
        def emit(ev: dict) -> None:
            if on_event:
                on_event(ev)

        def thinking(role: str, round_no: int) -> None:
            emit({"type": "thinking", "role": role, "round": round_no})

        def turn(round_no: int, role: str, content: str, model: str) -> None:
            self.log.add(round_no, role, content, model)
            emit({"type": "turn", "round": round_no, "role": role,
                  "content": content, "model": model})

        # Round 0 — Author's initial review.
        thinking("author", 0)
        seed = self._author_seed(task, filename, code, language)
        author_reply = self.author.respond(seed)
        turn(0, "author", author_reply, self.author.model)
        latest_code = extract_last_code_block(author_reply) or code

        stopped_early = False
        rounds_run = 0
        for r in range(1, self.max_rounds + 1):
            rounds_run = r

            # Adversary attacks the latest code.
            thinking("adversary", r)
            adv_ctx = self._adversary_turn(task, filename, latest_code, language)
            adv_reply = self.adversary.respond(adv_ctx)
            turn(r, "adversary", adv_reply, self.adversary.model)

            if NO_ISSUES_TOKEN in adv_reply.upper():
                stopped_early = True
                break

            # Author fixes or defends.
            thinking("author", r)
            reb_ctx = self._author_rebuttal(task, filename, code, language)
            author_reply = self.author.respond(reb_ctx)
            turn(r, "author", author_reply, self.author.model)
            latest_code = extract_last_code_block(author_reply) or latest_code

        # Final pass — the Mediator reconciles the whole debate.
        thinking("mediator", rounds_run)
        med_ctx = self._mediator_turn(task, filename, code, language)
        med_reply = self.mediator.respond(med_ctx)
        turn(rounds_run, "mediator", med_reply, self.mediator.model)
        mediator_result = parse_mediator(med_reply)

        emit({"type": "result", "rounds_run": rounds_run, "stopped_early": stopped_early,
              "security": mediator_result.security_verdict,
              "requirement": mediator_result.requirement_verdict,
              "summary": mediator_result.summary,
              "final_code": mediator_result.final_code})

        return DebateResult(
            log=self.log,
            original_code=code,
            latest_code=mediator_result.final_code or latest_code,
            rounds_run=rounds_run,
            stopped_early=stopped_early,
            mediator=mediator_result,
        )
