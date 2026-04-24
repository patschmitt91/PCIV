# ADR-0002: Use SQLite as the run ledger

- **Status:** Accepted
- **Date:** 2026-04-24
- **Deciders:** @patschmitt91
- **Supersedes:** —

## Context

Every PCIV run writes a multi-phase transcript: runs, subtasks,
iterations, agent invocations, cost events, verdicts, artifacts. The
ledger is load-bearing:

- It is the source of truth for budget enforcement (preflight sums
  projected rows; live guard sums actual rows).
- It is the forensic record when a run fails (which subtask, which
  iteration, which agent invocation produced the error).
- It is the basis of post-hoc cost attribution per phase.

Candidate backing stores:

1. SQLite file in `.pciv/`.
2. A JSONL append log.
3. A remote service (Application Insights, Cosmos DB, Postgres).

## Decision

Use one SQLite file per workspace, default `.pciv/ledger.db`. Schema
is versioned inline in `src/pciv/state/schema.sql` and applied
idempotently on every ledger open. Writes are serialized through a
single `Ledger` context-manager. No ORM; plain `sqlite3` with
parameter binding.

## Consequences

### Positive

- **Queryable.** SQL is the natural API for "which iteration did this
  subtask fail on?" and "how much did verify cost across all runs
  last week?". Five example queries are shipped in the README.
- **Zero deployment surface.** No service to stand up. A fresh clone
  + `uv sync` + `pciv run` produces a working ledger.
- **Inspectable after the fact.** A failed run leaves the DB on disk;
  `sqlite3 .pciv/ledger.db` works as a forensic tool without any
  extra tooling.
- **Atomic row writes.** SQLite's default transactions give us
  correct-by-construction ledger rows even when an agent crashes
  mid-invocation — the last committed state is coherent.

### Negative

- **Single-host only.** No shared-ledger mode across hosts. Fine for
  the one-run-per-invocation model; wrong if we ever want a
  background daemon coordinating across machines.
- **No historical dashboards out of the box.** A dashboard layer
  (Application Insights workbook, Grafana) would have to read the
  SQLite file or tee writes elsewhere. Tracked in the v0.3 roadmap.
- **Schema evolution is manual.** We version the schema string, apply
  it idempotently, and accept that breaking changes require a
  documented migration.

### Neutral

- The ledger duplicates some fields that also appear in OTel spans.
  This is deliberate: spans are best-effort and sampled; the ledger
  is authoritative. Both exist so that telemetry can be dropped or
  disabled without losing the audit trail.

## Alternatives considered

### JSONL append log

**Rejected.** Trivial to write, painful to query. "Cost by phase
for a specific run" requires parsing the whole file. We already need
SQL-style aggregation for the README queries; doing it in Python
against JSONL reinvents a worse SQLite.

### Application Insights as the primary store

**Rejected for v0.** Makes Azure Monitor a hard runtime dependency
and puts the audit trail on the other side of a network call that
can fail. Application Insights stays as an optional telemetry sink;
the ledger is always local.

### PostgreSQL via SQLAlchemy

**Rejected for v0.** Adds an operational dependency the user has to
stand up before the tool works. A local file beats a remote
database for a CLI that runs on a developer's machine.

## Validation

- Schema is applied on every open; `tests/test_workflow.py` runs
  against fresh temp-directory ledgers.
- The five queries in the README are exercised in the tests
  (`test_workflow_helpers.py` covers the row-count invariants).
