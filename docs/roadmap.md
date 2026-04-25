# Roadmap

Dated, best-effort milestones. Dates slip; scope is load-bearing.

## v0.1.0 — 2026-04-24 (shipped)

- Plan / Critique / Implement / Verify async pipeline.
- SQLite ledger of runs, agent invocations, cost events, verdicts.
- Per-subtask git worktrees + squash-merge to an integration branch.
- Preflight budget projection + hard USD ceiling.
- HITL gates for plan approval and ship approval (`--yes` auto-approves).
- OpenTelemetry span emission with optional Azure Monitor export.
- ADR-0001 + composition doc specifying the agent-framework port.
- Repo hygiene: CI matrix (Ubuntu + Windows, Py3.11 / 3.12), CodeQL,
  Dependabot, pre-commit, Code of Conduct.

## v0.2.0 — 2026-04-25 (shipped)

**Theme: secret-leak ratchet + cross-run budget enforcement (the two
credibility-damaging gaps in v0.1).**

- Diff-time secret-leak detection wired through Implement
  (`_tool_write_file` refuses writes) and Verify (`Pipeline.run`
  forces verdict to `reject` on any post-implement secret finding),
  backed by `agentcore.scan.DiffScanner`. ADR 0006.
- Cross-run rolling-window budget cap via
  `agentcore.budget.PersistentBudgetLedger` mounted on
  `runtime.sqlite_path`. New `[budget].monthly_cap_usd` + `window`
  config; `--ignore-cross-run-cap` emergency flag with `forced=1`
  audit row. ADR 0007.
- Four error-path tests covering plan-malformed-JSON, declined
  ship-gate, mid-run `BudgetExceededError`, and conflicting second
  approved subtask cleanup.
- agentcore pin bumped v0.2.0 → v0.3.0 → v0.4.0.

## v0.3.0 — target Q3 2026

**Theme: ADR-0001 port + non-interactive operation.**

- Port the orchestration spine to `agent-framework` graph primitives
  per [ADR-0001](decisions/0001-agent-framework-port.md). Golden-run
  test enforces the invariants listed in the ADR.
- Replace the inline gate callback with an `Approver` protocol. Ship
  three implementations: `CLIApprover`, `AutoApprover` (backs `--yes`),
  and `WebhookApprover` (POSTs the gate payload to a configured URL
  and awaits a signed response).
- Publish a bench harness under `bench/` that runs the full pipeline
  against a sandboxed Azure deployment and reports cost, latency, and
  verdict per task.

## v0.4.0 — target Q1 2027

**Theme: production observability + pluggability.**

- First-class Application Insights workbook mapping ledger rows to
  span attributes.
- Pluggable verifier tool suite (today the Implement agent is the only
  phase with tools; Verify is read-only).
- Multi-host cross-run cap (today the SQLite-backed ledger only
  shares state across processes on a single filesystem; tracked as a
  follow-up in ADR 0007).

## Out of scope

- Replacing Azure OpenAI with a different provider. Anthropic,
  OpenAI.com, and self-hosted models are intentionally not supported;
  see the project scope note in the README.
- A UI. The CLI is the interface.
- A queue / scheduler across concurrent runs. One run per invocation.
