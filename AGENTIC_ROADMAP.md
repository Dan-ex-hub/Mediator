# Agentic Roadmap (Phases 7+)

The first six phases turned Mediator into a working multi-agent **code review** tool.
This roadmap extends it toward an interactive, agentic **dev assistant**: a web UI, a
prompt-engineering layer, folder/repo review, and (carefully, later) autonomous project
generation with sandboxed execution.

This document is for review. Phases are ordered by value and risk. Build one at a time.

---

## Integrated Terminal (implemented)

The web IDE has a VS Code–style integrated terminal (`/api/terminal/run`, `/api/terminal/stop`)
that streams command output live (NDJSON). It runs commands the **user** types, in the
workspace directory. SECURITY: this executes real shell commands, so the server stays
bound to `127.0.0.1`. The AI agents have NO terminal access — autonomous execution is the
sandboxed Phase 10 work.

---

## Model Profiles (cross-cutting, implemented)

Each agent's model gets a **profile** (`mediator/profiles.py`) that adapts how prompts
are presented to that model without changing the shared output protocol:
- **dialect** — Anthropic/Claude models receive XML-structured prompts; OpenAI, Gemini,
  and local models receive markdown. Required labels (FINAL_CODE, VERDICT, …) stay identical.
- **capability flags** — `supports_structured` / `supports_tools` are recorded for future
  use of native JSON mode or tool calling (not yet wired, to keep the uniform client).

Profiles are matched by model name, so a Hybrid setup (local Author + Claude Mediator)
gives each agent prompts in its best dialect while all agents still speak one protocol.
Visible via `mediator config` and the web UI's `/api/config`.

---

## New Agent: the Prompt Engineer

A new role that sits *in front of* the debate. It takes a rough human prompt and rewrites
it into a structured, model-friendly spec before the coding agents see it.

Why: LLMs follow explicit, structured instructions far better than loose human phrasing.
The Prompt Engineer turns "make me a login thing" into a spec with goal, constraints,
inputs/outputs, acceptance criteria, and edge cases.

Output format (what it produces):
- **Objective** — one sentence of intent.
- **Context / stack** — languages, frameworks, runtime if known.
- **Requirements** — numbered, testable.
- **Constraints** — security, performance, style.
- **Acceptance criteria** — how we know it's done.
- **Edge cases to handle.**

This refined spec becomes the `task` passed into the debate, so Author/Adversary/Mediator
all work from the same precise brief.

---

## Phase 7 — Prompt Engineer + Web UI + JSON  ← in progress
**Goal:** make it usable and smarter about intent.

- `prompt_engineer` agent + `prompt` CLI command (raw text -> refined spec).
- `--refine` flag on `review` to auto-refine the task first.
- JSON export of a debate (`DebateLog.to_dicts()`), reused by the UI and tooling.
- A minimal **web UI** (FastAPI + a single page): enter code or a file path + a task,
  run the debate, read the transcript and final verdict in the browser.

**Done when:** a user can run a full review from the browser and see the result.

---

## Phase 8 — Folder / Repo Review  ✅ DONE
**Goal:** "review my project," not just one file.

- `/api/review/folder/stream` walks the workspace (respecting skip dirs), selects code
  files (capped by `max_files`) and runs a debate per file.
- A cross-file Mediator pass (`summarize_project`) produces an overall project report
  (overall health, top risks, cross-cutting issues, next steps).
- The UI's "Folder" scope shows per-file headers, verdicts, and the final report.

**Done when:** pointing Mediator at a folder produces a per-file + overall review. ✅ Verified.

---

## Phase 9 — Live Streaming  ✅ DONE
**Goal:** watch the debate unfold in the UI.

- `Orchestrator.run(on_event=...)` emits `thinking` / `turn` / `result` events.
- `/api/review/stream` and the folder endpoint stream NDJSON; the browser reads the
  response body incrementally (no waiting for the whole run).
- Each agent shows a live "thinking…" placeholder that turns into its message on arrival.

**Done when:** the UI shows turns appearing live. ✅ Verified (events arrived at 49s/71s/115s,
not batched at the end).

---

## Phase 10 — Host Execution with Approval Gate (no sandbox)  ✅ DONE

**Context:** the user runs on Windows without Docker and wants real host execution
(network + possibly admin), so isolation is off the table. The safety model therefore
shifts from *containment* to *human-in-the-loop approval*.

**Hard rules:**
- Agents may **propose** commands but NEVER auto-run them. Every command requires an
  explicit user click to execute.
- A risk classifier flags destructive/dangerous commands (recursive delete, format,
  disk ops, shutdown, piping the network into a shell, registry deletes, etc.); these
  require a second explicit confirmation.
- Every proposed and executed command + its output is logged.
- The web server stays bound to `127.0.0.1`.

**Design:**
- A new **Verifier** agent proposes shell commands to validate the code/debate
  (e.g. run the test suite, lint, execute a snippet) with a one-line rationale each.
- `/api/verify/plan` returns the proposed commands (with risk levels) — it executes
  nothing. The UI renders an approval card; the user runs commands individually in the
  integrated terminal.
- Captured output can be fed back to the agents for a grounded follow-up assessment.

**Done when:** the Verifier proposes commands, the user approves them one by one, they
run in the terminal, and nothing executes without approval.

**Note:** this is explicitly *less safe* than the original Docker plan; the user accepted
that tradeoff. The approval gate is the compensating control.

---

## Phase 11 — Project Generation (Agentic)  ✅ DONE (v1)
**Goal:** "build me an app" — plan, write files, then verify.

Implemented on top of the Phase 10 approval gate:
- **Architect** agent turns a request into a JSON build plan (name, stack, files, setup/run).
- **Builder** agent writes each planned file into a project subfolder inside the workspace
  (paths validated to never escape the workspace root).
- `/api/generate/stream` streams plan → per-file writing/written → proposed commands → done.
- The UI's "Build app" scope shows the plan, each file as it lands (click to open), then an
  approval card with `cd <project> && <setup/run>` commands — nothing runs without a click.

**Done when:** a complex prompt yields a written, runnable project. ✅ Verified — generated a
`prime-cli` project whose `main.py 5` printed the first five primes.

**v1 limitations / next:** no automatic build-test-fix loop yet (commands are proposed, the
user runs them); no Adversary/Mediator review pass over the generated project; one-shot file
generation without cross-file revision.

---

## Cross-cutting concerns
- **Safety:** sandbox isolation, explicit opt-in for execution, confirmation for
  destructive actions, never run untrusted generated code on the host.
- **Context limits:** large projects exceed local model context; need chunking,
  summarization, and file selection.
- **Cost/latency:** agentic loops make many calls; Hybrid (cloud) mode helps a lot.
- **Transparency:** every action (command run, file written) is logged like a debate turn.
