# Architecture

This document describes the design of Mediator before implementation. It is meant to be read and critiqued.

## 1. Overview

Mediator orchestrates three role-specialized LLM agents through a structured debate, then returns a reconciled final result plus a full transcript. All model calls go to a local **LM Studio** server through its OpenAI-compatible `/v1/chat/completions` endpoint.

```
CLI / entrypoint
      │
      ▼
 Orchestrator ──► DebateLog
      │
      ├──► Agent A (Author)
      ├──► Agent B (Adversary)
      └──► Agent C (Mediator)
              │
              ▼
        LMStudioClient ──► http://localhost:1234/v1
```

## 2. Components

### 2.1 LLMClient
Thin wrapper over any **OpenAI-compatible** `/v1` HTTP API. The same client talks to LM Studio (local) and cloud providers (OpenAI, OpenRouter, Groq, ...) because they share the `/chat/completions` and `/models` schema. Only `base_url` and `api_key` differ.

Responsibilities:
- Send chat completion requests (messages, model name, temperature).
- Handle timeouts, retries, connection and auth (401/403) errors with clear messages.
- Optionally list available models (`GET /v1/models`) for validation at startup.

A factory (`client_for_role`) builds the right client + model for each agent from config, so an agent never knows or cares whether it is local or cloud. Local LM Studio ignores the key (a placeholder `"lm-studio"` is sent); cloud keys are resolved from `secrets.toml` or `env:VAR` references.

### 2.2 Agent
A configured persona. Each agent holds:
- `role` (author / adversary / mediator)
- `system_prompt`
- `model` name (can differ per agent)
- `temperature`
- a reference to the shared `LMStudioClient`

An agent's `respond(context)` builds the message list (system prompt + relevant debate history) and returns the model's reply. Agents are **stateless** between calls; the Orchestrator owns the conversation state.

### 2.3 Orchestrator
Drives the debate loop and enforces the protocol (section 3). It:
- Seeds the debate with the user's code + task.
- Calls agents in the correct order for each round.
- Appends every turn to the `DebateLog`.
- Decides when to stop (max rounds, or Adversary reports "no significant issues").
- Triggers the Mediator's final pass.

### 2.4 DebateLog
An append-only record of turns. Each entry: `{round, role, content, timestamp, model}`.
Renders to:
- Console (readable, color-coded by role).
- Markdown file in `./logs/<timestamp>.md`.
- (Later) JSON for programmatic use.

## 3. Debate Protocol

Input: a code snippet/file and a task description ("review", "find security bugs", "implement X"). The Orchestrator retains this **original request** and passes it to every agent on every turn, so the Adversary and Mediator can always judge the code against what was actually asked.

```
Round 0 (seed):
    Author receives the code + task.
    - If task is "review": Author explains intent and known assumptions.
    - If task is "implement": Author writes the first version.

For round r in 1..N:
    Adversary  ← sees the user's ORIGINAL REQUEST + latest code + full history
               → produces a critique. PRIMARY focus security, SECONDARY focus whether
                 the code meets the user's request. Each issue tagged
                 [SECURITY] / [REQUIREMENT] / [LOGIC] / [EDGE-CASE] / [STYLE] with severity.
                 Always includes a REQUIREMENT verdict (does it do what was asked?).
    Author     ← sees the critique
               → either patches the code or defends each point with reasoning.
    Early stop: if Adversary returns "NO_CRITICAL_ISSUES", break.

Final:
    Mediator   ← sees the original request + the entire transcript
               → evaluates which critiques were valid, which defenses held,
                 resolves contradictions, applies fixes (security first), and
                 confirms FINAL_CODE both is secure and satisfies the request. Emits:
                   1. FINAL_CODE  (the reconciled version)
                   2. VERDICT     (Security: PASS/FAIL, Requirement: MET/NOT MET)
                   3. SUMMARY     (key issues found + decisions made)
                   4. RESIDUAL_RISKS (anything still uncertain)
```

### Why a fixed structure
Free-form multi-agent chat tends to collapse into agreement or loop forever. A fixed turn order with tagged outputs keeps the debate adversarial, bounded, and parseable.

## 4. Stop Conditions
- `max_rounds` reached (default 3).
- Adversary signals `NO_CRITICAL_ISSUES`.
- A hard wall-clock / token budget (safety against runaway loops on local hardware).

## 5. Configuration

Configuration is created by the `setup` wizard (`python -m mediator setup`), which asks the user to pick one of three modes and writes `config.toml`:

- **Privacy** — all agents use the `local` provider (LM Studio).
- **Reasoning** — all agents use a cloud provider.
- **Hybrid** — Author + Adversary stay local; Mediator uses the cloud.

Providers are named and reusable; each agent references one by name. API keys live in a gitignored `secrets.toml` (or via `env:VAR`), never in `config.toml`.

```toml
[debate]
max_rounds = 3

[lmstudio]
timeout_seconds = 120

[providers.local]
base_url = "http://localhost:1234/v1"
api_key = "lm-studio"
is_local = true

[providers.openai]                 # only present in Reasoning/Hybrid mode
base_url = "https://api.openai.com/v1"
is_local = false                   # api_key comes from secrets.toml

[agents.author]
provider = "local"
model = ""                         # empty => first model the provider reports
temperature = 0.4

[agents.adversary]
provider = "local"
temperature = 0.7                  # higher => more creative attacks

[agents.mediator]
provider = "openai"                # e.g. a stronger model for the final call
model = "gpt-4o"
temperature = 0.2                  # lower => consistent, decisive
```

When any agent is set to a cloud provider, the CLI prints a privacy notice so the user always knows code is leaving the machine.

## 6. Output Parsing
Models are asked to wrap structured parts in clear delimiters (e.g. fenced code blocks for code, and `NO_CRITICAL_ISSUES` as a literal token). The parser is **lenient**: if structure is missing, the raw text is still logged so nothing is lost. This avoids brittle failures with smaller local models.

## 7. Error Handling
- LM Studio unreachable → fail fast with a setup hint.
- Model returns empty / malformed → retry once, then log and continue.
- Per-call timeout so one slow turn doesn't hang the whole run.

## 8. Out of Scope (v1)
- Multi-file / whole-repo context.
- Editor/IDE integration.
- Running or executing the code under review (static debate only).
- Fine-tuned or tool-using agents.

These are candidates for later phases.

## 9. Key Risks & Open Questions
- **Small local models may be weak adversaries.** Mitigation: a sharp Adversary system prompt and higher temperature; allow a stronger model for that role.
- **Context length.** Long files + multi-round history can exceed the model's context. Mitigation: cap rounds, optionally summarize history for later rounds.
- **Latency on consumer hardware.** Three agents × N rounds = many calls. Mitigation: small models, streaming output so the user sees progress.
- **Reliability of structured tokens.** Covered by lenient parsing (section 6).
