# ADR-0001: Port PCIV's orchestration spine to agent-framework graph primitives

- **Status:** Accepted
- **Date:** 2026-04-24
- **Deciders:** @patschmitt91
- **Supersedes:** —
- **Superseded by:** —

## Context

PCIV currently declares `agent-framework` as a runtime dependency but
does not use its graph/workflow primitives for orchestration. The
spine is an application-level `async Pipeline` class in
`src/pciv/workflow.py` that:

1. Calls the four agents (Plan, Critique, Implement, Verify) directly.
2. Caps concurrent Implement subagents with an `asyncio.Semaphore`.
3. Gates human-in-the-loop (HITL) approvals with inline `input()`
   calls at two points (plan approval, ship approval).
4. Writes state transitions to a SQLite ledger.
5. Manages per-subtask git worktrees and branches, and squash-merges
   approved work onto a single integration branch.

This worked for v0 but creates three problems:

- **Composition story is incoherent.** AgentBudgeteer pitches itself
  as a runtime router over agent-framework, but its most complex
  strategy (PCIV) bypasses agent-framework. A reviewer from the
  agent-framework team will notice immediately.
- **HITL is not addressable from non-interactive runners.** Inline
  `input()` calls make it impossible to run PCIV from a CI job, a
  webhook handler, or a daemon that reports to an external approver.
  The `--yes` flag is a workaround, not a solution.
- **Concurrency control lives in application code.** The
  `asyncio.Semaphore` in `Pipeline` duplicates scheduling logic that
  agent-framework's workflow runtime already provides, and it blocks
  future optimizations (budget-aware scheduling, preemption, fair
  sharing across concurrent runs).

## Decision

We port the orchestration spine to agent-framework's graph/workflow
primitives. The four phases become nodes in a workflow graph; the
verify-iterate loop becomes a conditional edge; HITL gates become
workflow events; concurrency is delegated to the workflow scheduler.

### Primitive mapping

| Current `Pipeline` element | Target agent-framework primitive |
| --- | --- |
| `Pipeline.run_plan()` | Workflow node backed by `PlanAgent` (single in-degree from the workflow start, single out-degree to Critique) |
| `Pipeline.run_critique()` | Workflow node with a hard-failure edge that aborts the workflow on a `reject` verdict |
| `Pipeline.run_implement()` fan-out | Workflow fan-out over subtasks; each edge binds one `ImplementAgent` instance to a per-subtask git worktree via agent context |
| `asyncio.Semaphore(N)` | Workflow scheduler concurrency cap, set via the graph's worker-pool configuration (not application code) |
| `Pipeline.run_verify()` | Workflow fan-in node that consumes all Implement outputs and emits `{ship, iterate, reject}` |
| Verify → Implement iterate loop | Conditional edge from Verify back to a re-queued Implement fan-out, carrying only subtasks marked `iterate` plus per-subtask feedback. Loop cap enforced as a workflow invariant (default 2). |
| Inline `input()` HITL gate (plan approval) | Workflow event `hitl.plan_approval_requested` emitted after Critique; workflow pauses until an external approver sends `hitl.plan_approved` or `hitl.plan_rejected`. `--yes` installs an auto-approver that emits the approval event synchronously. |
| Inline `input()` HITL gate (ship approval) | Same pattern: `hitl.ship_approval_requested` event; pause; resume on approver response. |
| Ledger writes scattered through `Pipeline` methods | Workflow middleware that writes a ledger row on every node enter/exit/error transition, plus on every HITL event. No ledger writes in agent code. |
| `preflight_budget_projection()` | A pre-workflow hook (runs before the first node is scheduled) that aborts with `PreflightBudgetExceeded` before any network call. |
| Worktree creation / cleanup | A workflow `setup`/`teardown` phase (pre-first-node / post-last-node) that creates `.pciv/worktrees/<run_id>/<task_id>` and removes them on `--cleanup`. |
| Squash-merge to integration branch | A terminal workflow node that runs only on a `ship` verdict + ship-approval event, merges approved subtask branches onto `pciv/<run_id>/integration`, and records the final ledger row. |

### Invariants that must be preserved

These are behaviors of the current `Pipeline` that a port can
silently regress. The port is not accepted unless tests prove each:

1. **No network call before preflight passes.** A run with a
   projected cost exceeding `--budget` must fail with
   `PreflightBudgetExceeded` and zero HTTP requests recorded.
2. **Ledger row on every transition.** For a run with N subtasks and
   K iterate rounds, the ledger must contain exactly
   `4 + N*(K+1) + gates + terminals` rows (plan, critique, verify
   per round, N implements per round, 2 HITL gate rows, 1 terminal).
   The exact count is enforced by the golden-run test.
3. **HITL gates block in both modes.** Interactive mode pauses and
   waits for approver input. Non-interactive (`--yes`) mode auto-
   approves via an installed approver; no `input()` call is made.
4. **Re-queue carries feedback.** On an `iterate` verdict, only
   subtasks with verdict `iterate` are re-queued, and each carries
   the verifier's per-subtask feedback in its agent context.
5. **Squash-merge only on `ship` + ship-approval.** A `reject` or
   unapproved `ship` must leave the integration branch unmodified
   and the per-subtask branches preserved for inspection.
6. **Public API stability.** `PCIV/src/pciv/workflow.py::Pipeline`
   keeps its class name, constructor signature, and `run()` method
   signature. AgentBudgeteer's `pciv_adapter.py` does not change.

### Public API stability contract

The port modifies internals only. These symbols are frozen for v0.1:

```python
# PCIV/src/pciv/workflow.py
class Pipeline:
    def __init__(
        self,
        config: PipelineConfig,
        ledger: LedgerDB,
        approver: Approver | None = None,
    ) -> None: ...

    async def run(self, task: str) -> RunResult: ...
```

`RunResult` and `PipelineConfig` shapes are also frozen. Any changes
require a follow-up ADR.

## Consequences

### Positive

- **Composition story becomes coherent.** PCIV actually uses
  agent-framework as its orchestration spine. The pitch to the
  agent-framework team now describes real composition, not a
  declared dependency.
- **HITL becomes pluggable.** Non-interactive runners (CI,
  webhooks, Azure Functions, Teams bots) can approve gates by
  emitting workflow events. The `--yes` flag becomes a specific
  approver implementation, not a special code path.
- **Concurrency control moves to the framework.** Future features
  (budget-aware scheduling, preemption, cross-run fair sharing)
  become configuration, not code.
- **Ledger writes become uniform.** Middleware-based writes
  eliminate the scattered per-method writes and their associated
  "did we forget one?" class of bug.
- **Upstream feature requests become concrete.** The port surfaces
  the specific agent-framework primitives we want stronger (HITL
  event types, budget-aware scheduling, ledger hooks). We can file
  these as issues against `microsoft/agent-framework` with real
  code backing each ask.

### Negative

- **One-time porting cost.** Estimated 3-5 engineering days,
  including the golden-run test that enforces invariants.
- **Binds us to agent-framework's public API.** If
  `microsoft/agent-framework` makes a breaking change, we inherit
  it. Mitigated by pinning to a tested minor version and running
  CI against the pin, not `latest`.
- **Diagnostic surface changes.** Stack traces now pass through
  agent-framework's scheduler. We add an OTEL span per node so
  diagnosis is via traces, not tracebacks. This is a net improvement
  for production; a small regression for local `print`-based
  debugging.

### Neutral

- **Dependency remains the same package and version band**
  (`agent-framework>=1.0.0b1`). We do not tighten the pin in this
  ADR; that's a separate decision once we've exercised the port.

## Upstream asks

These surfaced during the port and will be filed as issues against
`microsoft/agent-framework`:

1. First-class HITL event primitives (`hitl.*` event types with a
   documented approver interface).
2. Budget-aware scheduling: a workflow-level cost projection hook
   that the scheduler consults before dispatching the next node.
3. Ledger/audit middleware: a documented middleware interface for
   recording every node transition to external storage, so users
   don't reinvent this per project.
4. Per-node concurrency caps independent of global worker-pool size
   (today we need a cap on Implement only; Plan/Critique/Verify are
   serial).

## Alternatives considered

### Alternative 1: Do nothing; describe the current composition honestly

Update both READMEs to say "PCIV declares agent-framework as a
dependency and uses its `Agent` primitives, but runs its own
`Pipeline` spine; migration to graph workflows is planned."

**Rejected** because the pitch to Microsoft's agent-framework team is
specifically about composition. A dependency that isn't exercised is
not composition; it's decoration. This alternative optimizes for
engineering effort at the cost of the thing we're trying to
demonstrate.

### Alternative 2: Partial port — keep `Pipeline` as the spine, replace only the Implement fan-out with agent-framework

Use agent-framework's parallel agent execution for the Implement
phase, keep the `asyncio` glue for Plan/Critique/Verify.

**Rejected** because it fixes the least important problem (one
semaphore) while leaving the two structural problems (incoherent
composition story, non-addressable HITL) untouched. The half-port
also leaves the codebase in a state where two different scheduling
models coexist, which is worse than either one alone.

### Alternative 3: Full port to LangGraph / CrewAI / AutoGen

Pick a different orchestration framework and port there instead.

**Rejected** because the audience for this work is the
microsoft/agent-framework team and Microsoft Azure AI PMs. Porting
away is the opposite of the pitch.

## Validation

The port is accepted as complete when all of the following hold:

- [ ] Every test in `PCIV/tests/` passes unchanged against the new
      spine.
- [ ] `tests/test_graph_workflow.py` asserts each workflow node is
      an `agent_framework.*` primitive of the expected type.
- [ ] The golden-run test in `tests/test_golden_run.py` exercises
      one full run with N=3 subtasks, K=1 iterate round, and asserts
      the exact ledger-row count, event sequence, and branch-merge
      outcome.
- [ ] The secret-leak test from Phase 6.2 still passes.
- [ ] `mypy --strict` is clean.
- [ ] No import of `asyncio.Semaphore` remains in `src/pciv/`.
- [ ] `docs/agent-framework-composition.md` contains the before/after
      diagrams and is linked from `README.md`.
- [ ] AgentBudgeteer's `pciv_adapter.py` is untouched.
- [ ] AgentBudgeteer's full test suite passes against the ported
      PCIV.

## References

- `microsoft/agent-framework` — https://github.com/microsoft/agent-framework
- Prior spine: `PCIV/src/pciv/workflow.py` at commit `<pre-port-sha>`
  (to be filled in when this ADR is merged)
- Composition diagrams: `docs/agent-framework-composition.md`