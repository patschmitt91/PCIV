# ADR 0007 — Cross-run rolling-window budget cap (`PersistentBudgetLedger`)

* Status: Accepted
* Date: 2026-04-25
* Deciders: PCIV maintainers
* Depends on: agentcore ADR 0003 (`PersistentBudgetLedger`)

## Context

`pciv run` enforces a per-run hard cap (`--budget`) through an
in-memory `BudgetGovernor`. The governor resets every process. An
operator running 100 sequential `pciv run` invocations against a
$2.00 per-run cap can spend $200 in a day while every individual run
respects its $2.00 ceiling.

`RESEARCH.md` flagged this as the most credibility-damaging missing
control: PCIV sells itself as a budget-aware orchestrator and
couldn't bound spend across runs.

## Decision

Wire `agentcore.budget.PersistentBudgetLedger` (ADR 0003 in
agentcore) into PCIV at CLI entry.

### Configuration

Two new optional fields under `[budget]` in `plan.yaml`:

```yaml
budget:
  monthly_cap_usd: 50.00     # null/omitted → cross-run cap disabled
  window: monthly            # "monthly" (YYYY-MM, UTC) or "daily" (YYYY-MM-DD, UTC)
```

Defaults: `monthly_cap_usd=None`, `window="monthly"`. The cross-run
check is opt-in; existing configs without the new fields keep their
current behaviour (per-run cap only).

### Storage

The `budget_window` table is mounted on the existing
`runtime.sqlite_path` (`.pciv/ledger.db` by default) using
`CREATE TABLE IF NOT EXISTS`. No new SQLite file, no schema-version
coordination with PCIV's existing `runs` / `tasks` / `iterations`
tables — the ledger uses no foreign keys and no `PRAGMA user_version`
of its own.

### Preflight

`pciv run` opens the ledger and checks two conditions before invoking
the per-run governor:

1. `remaining_in_current_window() <= 0` → exit 2 with
   `BudgetExceededError("cross-run … cap exhausted: …")`.
2. `governor.preflight() > remaining` → exit 2 with
   `BudgetExceededError("projected cost … exceeds cross-run remaining …")`.

We compare the **projected** cost (from `[budget].projection`) against
the remaining window allowance, not the per-run `--budget` upper
bound. The per-run `--budget` is operator authorization for one run;
rejecting purely because `--budget > remaining` would block normal
usage near the end of a window where typical runs comfortably fit but
the worst-case authorization doesn't.

### Post-hoc accounting

After the pipeline completes (success **or** crash) the actual
`governor.spent_usd` is written to the ledger via
`PersistentBudgetLedger.record_spend(amount, note=f"run_id={run_id}")`.
This always runs in the `finally` block so a partial / crashed run
still counts against the window cap.

`record_spend` may raise `BudgetExceeded` if the actual spend
overshot what preflight projected and pushed the window over the cap.
That is suppressed at the CLI boundary because surfacing it would
mask the run's own exit status; operators see the breach via the
next run's preflight rejection.

### Emergency override

A new CLI flag `--ignore-cross-run-cap`:

- Skips both preflight checks (logs WARNING with the exhausted-cap
  message so the override is auditable in logs).
- Records the actual spend via
  `PersistentBudgetLedger.force_record(amount, reason=f"--ignore-cross-run-cap run_id={run_id}")`,
  which inserts a row with `forced=1` and never raises.
- The per-run `--budget` cap still applies; the override is scoped to
  cross-run enforcement only.

### Default behaviour preserved

`monthly_cap_usd is None` (the default) skips opening the ledger
entirely. Output diagnostics and the `budget_window` table do not
appear; existing pipelines see no change.

## Consequences

- PCIV's `agentcore` pin bumps from `v0.3.0` to `v0.4.0`. CHANGELOG
  documents the bump.
- `BudgetConfig` (in `pciv.config`) gains two optional fields. The
  Pydantic schema rejects unknown windows; `[budget].monthly_cap_usd`
  must be non-negative (validated by `PersistentBudgetLedger`).
- Two new exit-code-2 failure modes for `pciv run`. The existing
  per-run `BudgetExceededError` mapping in `cli.run_cmd` already
  catches the shared `_CoreBudgetExceeded` base, so no new exit-code
  branch is needed.
- `tests/test_cross_run_budget.py` covers the realistic two-sequential
  -invocations path, the `--ignore-cross-run-cap` override (with a
  pre-seeded ledger to simulate an exhausted prior run), and the
  default-disabled path (no ledger file written, no banner line).
- The shared SQLite file means one PCIV state directory, one window
  cap. Operators running two PCIV projects against two state
  directories effectively have two independent caps — that is
  intentional and matches the "one cap per project" mental model.
- Multi-host enforcement is out of scope. Two hosts running
  `pciv run` against the same shared filesystem will share the cap
  via SQLite's WAL + `BEGIN IMMEDIATE`; two hosts with independent
  storage will not. Distributed enforcement requires a remote ledger
  fronting `PersistentBudgetLedger`; tracked in the roadmap.
