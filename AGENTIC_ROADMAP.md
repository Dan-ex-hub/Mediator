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

## Phase 12 — Diff & Apply  ✅ DONE
**Goal:** turn the Mediator's `FINAL_CODE` from "copy this manually" into a reviewable edit.

- The web UI renders an **apply card** after any review that produced `FINAL_CODE`
  (file *and* folder scope). "View diff" opens a Monaco side-by-side diff (original vs
  proposed, proposed side editable); "Apply" writes the file via the CSRF-protected
  `/api/save`, updates the open editor tab, and refreshes the tree.
- For folder review each per-file result gets its own apply card targeting that file.
- CLI parity: `review --apply` overwrites the reviewed file with `FINAL_CODE` (opt-in only).

**Done when:** a review can be applied to disk as a diff-reviewed change without copy/paste.
✅ Verified (server serves the diff UI; JS passes `node --check`; apply round-trips through
`/api/save`).

**Next:** multi-file *debated edits* (propose coordinated changes across existing files,
not just one file or a greenfield project), and closing the verify loop (run approved
commands and feed output back for a grounded Mediator follow-up).

## Phase 13 — Multi-file Debated Edits  ✅ DONE
**Goal:** "change X across my project" — coordinated, debated edits to EXISTING files,
not just a one-file review or a greenfield build.

Protocol (`mediator/edits.py`, every code-emitting step returns one file in one block):
1. **Planner** (mediator tier) picks the minimal set of files to modify/create as a JSON
   plan; paths are normalized and workspace-escape paths are rejected.
2. **Author** drafts the full new content of each planned file, given the request, the
   plan, the file's current content, and truncated context from the sibling target files.
3. **Adversary** critiques the WHOLE change set at once — security, requirement
   compliance, and cross-file issues (broken calls, inconsistent interfaces, missing wiring).
4. **Mediator** finalizes each file with final authority, applying the valid critique.
5. A short change-set **report** (verdict + summary + residual risks) closes it out.

- `/api/edit/stream` streams plan → per-file draft → critique → per-file final → report.
  Each finalized file is delivered with its original + final content, so the Phase 12
  diff/apply UI renders a per-file diff; an **Apply all** button writes the whole set.
- New "Edit" scope in the web UI. Cost is bounded (2·files + 3 calls) and the file count
  is capped.

**Done when:** a project-wide change request yields debated, per-file diffs the user can
review and apply. ✅ Verified (end-to-end orchestration with mocked agents produces the
full plan→draft→critique→finalize→report→done sequence; diffs carry correct per-file
original/final; paths that escape the workspace are rejected).

**Next:** close the verify loop (run approved commands and feed output back for a grounded
Mediator follow-up).

## Phase 14 — Closing the Verify Loop  ✅ DONE
**Goal:** make verification *grounded* — feed the real output of approved commands back to
the agents instead of stopping at "here are commands to run."

- The approval card now **captures** each command's stdout/stderr and exit code as the
  user runs it (`runTermCommand` returns `{command, output, code}`), still gated by the
  same one-click / destructive-confirm rules.
- An **"Assess results with the agents"** button (enabled once a command has run) posts the
  collected results to `/api/verify/assess`.
- `verify.assess_results` builds a Mediator prompt from the request, the code, and the
  actual command output (truncated), and returns a grounded assessment:
  `GROUNDED_VERDICT` / `EVIDENCE` (tied to quoted output) / `REMAINING_CONCERNS`.
- The loop stays human-in-the-loop: nothing runs without a click, and the assessment only
  uses output the user chose to produce.

**Done when:** the user runs the proposed checks and the Mediator returns a verdict grounded
in their real output. ✅ Verified (the assessment prompt carries each command, its output,
and exit code; endpoint is CSRF-guarded and rejects empty result sets; JS passes `node --check`).

## Phase 15 — Codebase Index (retrieval)  ✅ DONE
**Goal:** find the right files in a large repo instead of walking the first N.

- `mediator/index.py`: a dependency-free **BM25** index over the workspace's code files.
  Tokenizes identifiers and splits camelCase / snake_case subwords; fully local, no
  embedding model required.
- `/api/search/semantic?q=` ranks files by relevance; `/api/index/refresh` rebuilds it.
  The index is cached per workspace root and invalidated on open/save.
- Multi-file **Edit** now selects candidate files by BM25 relevance to the request (falling
  back to a walk), so project-wide edits scale to real repos.

**Done when:** a query returns the most relevant files. ✅ Verified (BM25 ranks the auth
file top for "password login").

## Phase 16 — Agent Tool Use  ✅ DONE
**Goal:** let agents inspect the workspace instead of guessing.

- `mediator/tools.py`: provider-agnostic tool calling. The agent emits a small JSON object
  ({"action":"read_file"|"list_dir"|"search"|"answer"}); a bounded ReAct loop runs the tool
  and feeds the observation back. All tools are READ-ONLY and sandboxed to the workspace
  root (path-escape attempts are rejected), so an agent can gather context but never modify
  or escape the workspace.

**Done when:** an agent can read/search files mid-answer. ✅ Verified (loop executes a
read_file tool call then finalizes; sandbox blocks `../` escapes).

## Phase 17 — Continuous Assistant Chat + Token Streaming  ✅ DONE
**Goal:** a real conversational assistant that streams and remembers.

- `LLMClient.chat_stream` adds OpenAI-compatible `stream: true` token streaming.
- New **Ask** scope + `/api/chat/stream`: a continuous assistant (mediator tier) that keeps
  conversation history (client-maintained), uses the Phase 16 tools to ground answers in
  the codebase, then streams the final answer token-by-token. Tool steps surface as live
  "🔧 read_file …" lines in the chat.

**Done when:** the user can hold a streamed, codebase-aware conversation. ✅ Verified
(end-to-end: a tool call then streamed tokens assemble the final answer; history round-trips).

## Phase 18 — Git Integration  ✅ DONE
**Goal:** source control from inside the IDE.

- `mediator/gitops.py`: a SAFE git CLI wrapper (status, diff, stage/unstage, commit, branch
  create/checkout, log, push with `-u`). No force pushes, hard resets, or cleans by design;
  `current_branch` handles unborn HEAD (fresh repos).
- `/api/git/*` endpoints (mutations CSRF-guarded) and a **Source Control** activity panel:
  branch display, changed-file list with per-file stage/unstage, click-to-view unified diff
  in chat, commit message + commit, new branch, and push (with an upstream prompt).

**Done when:** the user can stage, commit, branch, and push from the UI. ✅ Verified
(read-only ops against the live repo; empty-repo status handled; mutations require a token).

## Phase 19 — Three-Lens Adversary  ✅ DONE
**Goal:** replace one vague "attack" with three sharp, focused review passes.

- The Adversary is now three agents, each with a tight system prompt:
  **Security** (injection, auth, crypto misuse, secrets), **Spec-compliance** (does it do
  what was asked), and **Logic/edge-case** (off-by-one, null handling, concurrency).
- All three share one machine-readable finding contract:
  `SEVERITY | TITLE | WHERE | DETAIL | FIX`. `parsing.parse_findings` turns each lens's
  output into structured `Finding` records (lenient; raw text is always retained).
- The Orchestrator runs the three lenses per round, emits a grouped/severity-sorted
  `findings` event (rendered as a color-coded table in the UI), stops early only when ALL
  lenses are clear, and feeds the Author and Mediator the findings GROUPED BY CATEGORY.
- The Mediator now weighs findings by category (security → spec → logic) instead of a
  free-form debate blob. Each lens inherits the `adversary` provider/model unless given its
  own `[agents.adversary_*]` block, so the split costs nothing to configure.

**Done when:** the adversary produces structured, per-category findings and the Mediator
resolves them by category. ✅ Verified (a mocked 3-lens debate emits SECURITY/SPEC/LOGIC
turns, a grouped findings event with correct counts and severity ordering, and a
category-weighed Mediator verdict; the findings parser handles header rows, NO_ISSUES, and
severity sorting; JS passes `node --check`).

## Phase 20 — Web Access for Agents  ✅ DONE
**Goal:** let the assistant reach the internet for current information.

- `mediator/webtools.py`: `fetch_url` (download a page, strip HTML to text) and
  `web_search` (keyless DuckDuckGo HTML search). Both are **SSRF-guarded** — requests to
  private/loopback/link-local/metadata addresses are refused and redirects re-validated
  each hop; sizes and time are bounded; fetched content is treated as untrusted.
- Exposed through the agent tool-loop as `web_search` / `fetch_url`, gated by a **Web**
  toggle in the Ask chat (`use_web`). Off by default (privacy).

**Done when:** the assistant can search and read the web on demand. ✅ Verified (SSRF guard
blocks localhost/metadata/`file://`, HTML→text strips scripts; tool parsing + gated
instructions tested; live network unavailable in CI).

## Phase 21 — MCP Client  ✅ DONE
**Goal:** Kiro-style extensibility — use any Model Context Protocol server's tools.

- `mediator/mcp_client.py`: a compact, pure-stdlib MCP client over the **stdio**
  transport (newline-delimited JSON-RPC 2.0) with a background reader thread, plus an
  `MCPManager` that starts configured servers, aggregates their tools as `server.tool`,
  routes calls, and shuts them down.
- Configured in `config.toml` under `[mcp.servers.<name>]` (command/args/env/disabled/
  autoApprove). Tools are injected into the same agent tool-loop and used by the Ask
  assistant; each call surfaces as a live tool event.
- Safety: only user-configured servers start; they run as child processes of the
  localhost-only web server; every call is visible.

**Done when:** the assistant can call tools from a configured MCP server. ✅ Verified
(full initialize → tools/list → tools/call round-trip against a mock stdio server; the
tool loop dispatches an MCP call and feeds the observation back; config parses
`[mcp.servers.*]`).

## Cross-cutting concerns
- **Safety:** sandbox isolation, explicit opt-in for execution, confirmation for
  destructive actions, never run untrusted generated code on the host.
- **Context limits:** large projects exceed local model context; need chunking,
  summarization, and file selection.
- **Cost/latency:** agentic loops make many calls; Hybrid (cloud) mode helps a lot.
- **Transparency:** every action (command run, file written) is logged like a debate turn.
