# Mediator — Local Multi-Agent Debate System for Code Review

A fully local-first code review tool where three AI agents debate over your code instead of one model giving a single opinion. Run it **100% locally** via **LM Studio** so your code never leaves your machine — or opt into **cloud models** per agent when you want more reasoning power and speed. Your choice, made at setup.

> This is **not** a Copilot clone. It models how a real engineering team works: someone writes the code, someone tries to break it, and a senior engineer reconciles the two views into a final answer.

---

## The Idea

Single-model code review tends to be agreeable and shallow. It rarely pushes back hard on its own output. Mediator fixes this by forcing a structured **adversarial debate** between role-specialized agents:

| Agent | Role | Goal |
|-------|------|------|
| **Agent A — Author** | Writes / proposes the code | Produce a working implementation and defend design choices |
| **Agent B — Adversary** | Attacks the code through three focused lenses | A **Security** lens (injection, auth, crypto misuse), a **Spec** lens (does it do what was asked), and a **Logic/edge-case** lens (off-by-one, null handling, concurrency) — each returns structured findings |
| **Agent C — Mediator** | Senior reviewer | Weigh the findings by category, resolve disagreements, produce the final reviewed version |

The agents run in a loop for a configurable number of rounds. Every message in the debate is logged and shown to the developer, so you see *why* the final code looks the way it does — not just the result.

### Why it's novel
- **Structured adversarial loop**, not a single prompt. The Adversary's only job is to break things.
- **A mediator with authority** that produces a final, reconciled version rather than just summarizing.
- **Runs entirely local** via LM Studio, so proprietary code is safe.
- **Transparent debate log** — the reasoning is the product, not a hidden side effect.

---

## How It Works (high level)

```
        ┌──────────────┐
 code → │   Agent A    │  proposes / writes code
        │   (Author)   │
        └──────┬───────┘
               │ code + rationale
               ▼
        ┌──────────────┐
        │   Agent B    │  attacks: security, logic, edge cases
        │ (Adversary)  │
        └──────┬───────┘
               │ critique
               ▼
        ┌──────────────┐
        │   Agent A    │  defends / patches  ◄── loop N rounds
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │   Agent C    │  reconciles → FINAL version + summary
        │  (Mediator)  │
        └──────┬───────┘
               ▼
     debate log + final code shown to developer
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the detailed design and [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) for the build phases.

---

## Features

Everything below works from the web IDE (`python -m mediator web`), and the core review also works from the command line.

**Reviewing code**
- **Debate review** — three agents (Author, Adversary, Mediator) argue over a file and hand you a final, reconciled version with a clear verdict.
- **Three-lens adversary** — instead of one vague "attack," the Adversary runs three focused passes: a **Security** lens, a **Spec-compliance** lens, and a **Logic/edge-case** lens. Each returns structured findings (severity, location, fix) that show up as a color-coded table, and the Mediator weighs them by category.
- **Folder review** — review every code file in a project, then get one overall project report.
- **Live debate** — watch each agent's turn appear in real time instead of waiting for the end.
- **Diff & apply** — the final code shows up as a side-by-side diff you can edit and apply straight to the file (no copy/paste). The terminal version has a `review --apply` flag.

**Changing & building code**
- **Multi-file edits** — describe a change like "add logging to every API route" and the agents plan it, write each file, critique the whole set, and finalize it. You review each change as a diff and apply one file or all at once.
- **Build an app** — describe an app and it plans the project, writes the files, and proposes the commands to install and run it.

**Asking & exploring**
- **Ask (chat)** — a normal back-and-forth chat about your codebase. It reads and searches your actual files to answer, remembers the conversation, and streams its reply word by word.
- **Web access** — flip the **Web** toggle in Ask and the assistant can search the web and fetch pages to ground its answers in current information (SSRF-guarded; fetched content is treated as untrusted).
- **MCP tools** — point Mediator at any [Model Context Protocol](https://modelcontextprotocol.io) server in `config.toml` and its tools become available to the assistant. Example:
  ```toml
  [mcp.servers.fetch]
  command = "uvx"
  args = ["mcp-server-fetch"]
  # env = { API_KEY = "…" }
  # disabled = false
  ```
  Servers are started on demand and every tool call is shown live in the chat. Only add servers you trust — MCP tools run on your machine.
- **Code search** — fast keyword search plus a smarter "find the most relevant files" search across the project.

**Running & verifying**
- **Built-in terminal** — run commands in your workspace, with live output.
- **Verify loop** — the agents suggest safe checks to run (tests, linters). You run them with one click, and the agents then read the real output and give you a grounded verdict. Risky commands are flagged and need an extra confirmation; nothing runs on its own.

**Source control**
- **Git panel** — see changed files, view diffs, stage/unstage, commit, create branches, and push — all from the sidebar.

**Privacy & setup**
- **Local, cloud, or hybrid** — run every agent on your own machine via LM Studio, in the cloud, or mix the two. A privacy notice appears whenever code would leave your machine.
- **Per-agent cloud models** — give each agent (Prompt Engineer, Author, Adversary, Mediator) its own cloud provider, model, and API key. Local too slow? Put the heavy roles on a fast cloud model and keep the rest local. Supports OpenAI, OpenRouter, Groq, DeepSeek, Together, or any custom OpenAI-compatible endpoint. Keys are stored in a gitignored `secrets.toml`, never in `config.toml`.
- **Model profiles** — each agent automatically gets prompts in the style its model prefers (e.g. XML for Claude, markdown for others).
- **Saved transcripts** — every debate is saved to `logs/` as a readable markdown file.

> Safety: the web server stays on `127.0.0.1`, all actions that change files or run commands require a click, and write requests are protected against cross-site requests.

---

## Requirements

- **Python 3.10+**.
- For **Privacy** or **Hybrid** mode: **LM Studio** with a chat-capable model loaded and its local server running (default `http://localhost:1234/v1`).
- For **Reasoning** or **Hybrid** mode: an API key for any OpenAI-compatible cloud provider (OpenAI, OpenRouter, Groq, etc.).

Each agent is configured independently, so you can point all three at the same local model, mix local and cloud, or run fully in the cloud.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. First-run setup — choose how Mediator runs:
python -m mediator setup
#    [1] Privacy    - 100% local via LM Studio (code never leaves your machine)
#    [2] Reasoning  - all agents use a cloud model (faster, stronger)
#    [3] Hybrid     - Author + Adversary local, Mediator in the cloud
#    [4] Per-agent  - give EACH agent its own provider + model + API key
#                     (best when local models are too slow)

# 3. Verify the connection
python -m mediator ping

# 4. Review a file (runs the full Author -> Adversary -> Mediator debate)
python -m mediator review path/to/your_code.py --task "what to check"
#    --rounds N   override debate rounds
#    --quiet      show only the final result (transcript still saved to logs/)

# Inspect your effective settings any time:
python -m mediator config

# Turn a rough idea into a precise, AI-friendly brief:
python -m mediator prompt "make me a login thing for my website"
#   (or add --refine to `review` to auto-refine the task before the debate)

# Launch the web UI (paste code or a path, run the debate in your browser):
python -m mediator web      # then open http://localhost:8000
```

For Privacy/Hybrid mode you must have **LM Studio** running with a model loaded
(Developer tab -> Start Server). For Reasoning/Hybrid mode you provide a cloud API
key during `setup`, which is stored in a gitignored `secrets.toml` — never in `config.toml`.

Every debate is printed live (color-coded by role) and saved to `logs/<timestamp>_<file>.md`.

---

## What This Is / Isn't

**Is:**
- A local, transparent, multi-agent code review tool.
- A way to surface bugs and security issues a single pass would miss.
- A study in structured agent orchestration.

**Isn't:**
- A replacement for human review or real security tooling.
- An autocomplete / inline assistant.
- Dependent on any cloud service.

---

## Documents to Review

1. [`ARCHITECTURE.md`](./ARCHITECTURE.md) — system design, agent contracts, debate protocol.
2. [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) — phased roadmap with milestones.
3. [`docs/PROMPTS.md`](./docs/PROMPTS.md) — draft system prompts for each agent.

After you've reviewed these, the next step is implementing **Phase 1** (LM Studio connection + single agent round-trip).
