# Implementation Plan

A phased plan so each step is testable on its own. Do not start Phase N+1 until Phase N runs.

## Proposed Project Structure

```
mediator/
├── README.md
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── requirements.txt
├── config.toml
├── docs/
│   └── PROMPTS.md
├── logs/                      # generated debate transcripts
└── mediator/                  # the Python package
    ├── __init__.py
    ├── __main__.py            # CLI: setup, ping, (later) review
    ├── setup_wizard.py        # first-run Privacy/Reasoning/Hybrid wizard
    ├── client.py              # LLMClient (any OpenAI-compatible provider) + factory
    ├── agent.py               # Agent class
    ├── orchestrator.py        # debate loop + stop conditions
    ├── debate_log.py          # DebateLog (console + markdown)
    ├── prompts.py             # system prompts per role
    └── config.py              # load/validate config.toml + secrets.toml
```

---

## Phase 1 — Connect to providers (local + cloud)  ✅ DONE
**Goal:** prove we can talk to a model, local or cloud.

- `LLMClient.chat(messages, model, temperature)` for any OpenAI-compatible endpoint.
- `setup` wizard: choose Privacy (local) / Reasoning (cloud) / Hybrid; writes `config.toml`
  and gitignored `secrets.toml`.
- `ping` command: tests every configured provider and prints a privacy notice for cloud.
- Clear errors for connection, timeout, and auth failures.

**Done when:** `ping` returns a real model reply. ✅ Verified against LM Studio.

---

## Phase 2 — Single Agent  ✅ DONE
**Goal:** one role-driven agent answering about code.

- `prompts.py` holds the system prompt for each role; `Agent` + `build_agent` wire a
  role to its provider/model/temperature.
- `review <file> [--task ...]` reads a file and prints the Author's analysis (no debate yet),
  rendered as markdown. Privacy notice shown if the Author uses a cloud provider.

**Done when:** `python -m mediator review <file>` prints an Author analysis. ✅ Verified.

---

## Phase 3 — The Debate Loop  ✅ DONE
**Goal:** the core novel feature.

- `Orchestrator` runs Author seed -> Adversary attack -> Author rebuttal for N rounds.
  Tracks the latest code version so the Adversary always attacks the newest version.
- `DebateLog` stores every turn and prints it live, color-coded (Author green, Adversary red).
- `max_rounds` (config or `--rounds`) plus `NO_CRITICAL_ISSUES` early stop.
- `review <file>` now runs the full debate instead of a single agent.

**Done when:** Author and Adversary visibly argue for N rounds in the terminal. ✅ Verified
(debate may end early when the Adversary finds no critical issues — by design).

---

## Phase 4 — The Mediator  ✅ DONE
**Goal:** a reconciled final result.

- Agent C consumes the original request + full transcript and emits `FINAL_CODE`,
  `VERDICT` (Security PASS/FAIL, Requirement MET/NOT MET), `SUMMARY`, `RESIDUAL_RISKS`.
- `parsing.py` parses these sections leniently and always keeps the raw text.
- CLI surfaces a color-coded verdict line after the debate.

**Done when:** a full run produces a clear final version and summary distinct from both
agents. ✅ Verified (Security: PASS, Requirement: MET on the sample).

---

## Phase 5 — Logging & Output  ✅ DONE
**Goal:** the debate is the product.

- `DebateLog.to_markdown()`/`save()` write each run to `logs/<timestamp>_<file>.md`
  with a metadata header (file, task, date, rounds, verdict, providers) and all turns.
- `--quiet` shows only the final result; the transcript is saved regardless.
- CLI prints the saved transcript path.

**Done when:** every run leaves a readable transcript file. ✅ Verified.

---

## Phase 6 — Configuration & Polish  ✅ DONE
**Goal:** experiment without editing code.

- `config.toml` validated on load with clear, aggregated error messages (bad rounds,
  malformed base_url, unknown provider, out-of-range temperature, missing cloud key).
- Per-agent provider/model/temperature honored; `--config`, `--task`, `--rounds`, `--quiet` flags.
- New `config` command prints the effective configuration (with key presence, never the key).

**Done when:** behavior is controllable from config + flags only. ✅ Verified.

---

## Later / Stretch
- Multi-file and repo-level review.
- JSON output mode for tooling integration.
- Optional code execution / test running to ground the Adversary's claims.
- Simple web UI to browse debates.
- VS Code / Kiro extension.

---

## Dependencies (initial)

```
# requirements.txt (proposed)
httpx          # or `requests` / `openai` client pointed at localhost
rich           # nice console output for the debate
tomli ; python_version < "3.11"   # config parsing (tomllib is stdlib in 3.11+)
```

Kept intentionally small. The only hard external dependency is a running LM Studio server.

---

## Suggested First Command After Approval
Implement **Phase 1** and verify a round-trip to LM Studio before anything else.
