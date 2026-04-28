# Next Iteration Brief — Budget-Constrained Autonomous Repair

## What we know going in

### What's working
- PCIV pipeline runs end-to-end: plan → critique → implement → test → verify → patch
- `agentcore` budget enforcement enforces per-task and monthly caps correctly
- `_install_deps()` resolves ImportErrors for most repos before PCIV runs
- gpt-4.1-mini plans are coherent and critiques are substantive

### What's blocking results
1. **Environment mismatch** — `_install_deps --only-binary=:all:` silently fails for C-extension repos (astropy, matplotlib, scikit-learn). Those tasks will never produce patches on the current setup.
2. **`max_rounds: 2` is too tight** — two iterations gives the model almost no room to recover from a first-attempt failure. Competitive agents use 5–10.
3. **Model ceiling** — gpt-4.1-mini on hard scientific repos (astropy) is a low-ceiling combination. The first ~40 SWE-bench Verified tasks are all astropy, so the opening of any run looks worse than the dataset actually is.

### Budget state (2026-04)
- Spent: ~$15 (smoke runs + aborted full run)
- Remaining: ~$45
- Ledger: `.pciv/ledger.db`, `monthly_cap_usd: 60.00`

---

## Strategic frame

**The differentiated research angle is not "PCIV solves SWE-bench."**
That space is saturated (SWE-agent, OpenHands, Aider, Devin).

**The angle is: budget-constrained autonomous repair.**
`agentcore` + PCIV together answer: *"How do I deploy autonomous code repair in production without cost blowouts?"*
Nobody publishes that with rigorous numbers. The metric that matters is:

> **Solve rate × (1 / cost-per-solved-bug)** — solve efficiency under a hard budget cap

This makes the benchmark *about* the budget system, not just the model.

---

## Prerequisites before the next benchmark run

### 1. Fix the test environment properly
**Current**: `pip install -e ".[test,dev]" --only-binary=:all:` — best-effort, silent failures.  
**Target**: Use SWE-bench's official per-task Docker images.

SWE-bench provides pre-built images for every repo/version combo used in scoring:
```
ghcr.io/swe-bench/sweb.eval.x86_64.{repo}-v{version}
```
These have all deps pre-installed. Using them for the internal test loop makes PCIV's pass/fail signals match the official scorer — no more phantom rejections.

**Changes needed:**
- `sandbox.py`: accept optional `image` override in `run_tests()`
- `swe_bench_run.py`: derive image name from `instance_id` and pass it through
- `plan.yaml`: add `sandbox_image_template` field

### 2. Raise `max_rounds`
`plan.yaml` line 81: `max_rounds: 2` → `max_rounds: 4`  
Two rounds is not enough for the model to recover from a non-trivial first attempt.

### 3. Add repo-type filtering to `swe_bench_run.py`
Add a `--skip-repos` flag (comma-separated) so a mini-model run can skip C-extension repos it will never solve, preserving budget for solvable tasks.

---

## Benchmark strategy for remaining ~$45

### Run A — Frontier ceiling (do this first)
**Goal**: establish the best-case solve rate with a strong model.  
**Model**: `gpt-4o` (restore `plan.yaml` deployment to `gpt-4o`, pricing `$2.50/$10.00`)  
**Scope**: 40 tasks, skipping astropy — start at task 41 (`--start 40 --tasks 40`)  
**Expected cost**: ~$0.93/task × 40 = ~$37  
**Expected result**: 5–15 solves → a real high-water mark for the paper  

```bash
uv run python scripts/swe_bench_run.py \
  --tasks 40 --start 40 --output predictions_gpt4o.jsonl --budget 3.00
```

### Run B — Mini baseline (do this second, ~$8 remaining)
**Goal**: cost-efficiency comparison against Run A.  
**Model**: `gpt-4.1-mini` (current config)  
**Scope**: same 40 tasks (`--start 40 --tasks 40 --skip-repos astropy`)  
**Expected cost**: ~$0.30/task × 40 = ~$12 (if $8 left, cut to 25 tasks)  
**Expected result**: fewer solves, much lower cost-per-solved-bug on the ones it does solve  

### What this buys you
| run | model | tasks | est. cost | expected solves |
|---|---|---|---|---|
| A | gpt-4o | 40 | ~$37 | 4–15 |
| B | gpt-4.1-mini | 25–40 | ~$8–12 | 1–5 |

Two data points on the **solve rate vs cost** curve. That's the paper's core figure.

---

## Optional: ablation study (~$10, can defer)

Same 10 tasks, three configurations:
1. Full pipeline: plan + critique + implement + verify  
2. No planning: just implement + verify (skip planner/critic)  
3. No verification: implement only, always ship first attempt  

Answers whether the 4-agent structure earns its cost. If plan/critique adds nothing,
the architecture simplifies and costs drop.

---

## Code change checklist

- [ ] `sandbox.py`: thread `image` param from `plan.yaml` → `run_tests()`
- [ ] `swe_bench_run.py`: derive SWE-bench image name per instance, add `--skip-repos`
- [ ] `plan.yaml`: `max_rounds: 2` → `4`, add `sandbox_image_template`
- [ ] `agentcore`: add `cost_per_solved` metric to `PersistentBudgetLedger` reporting
- [ ] Understand `AgentBudgeteer` role — confirm how it connects to the other two repos
- [ ] Reset or document the monthly ledger state before each benchmark run

---

## What to do with AgentBudgeteer

Confirmed: it's the reporting and visibility layer on top of agentcore. The three repos form
a complete production stack:

| repo | role |
|---|---|
| **agentcore** | enforcement — per-task + rolling-window caps, persistent ledger |
| **AgentBudgeteer** | visibility — spend reporting, dashboards, budget tracking UI |
| **PCIV** | the agent — budget-governed autonomous code repair |

This is the differentiating story. No one else publishes a full budget-governed repair system
end-to-end. The benchmark becomes a demonstration of the stack, not just a model evaluation.

The next-session priority is to verify AgentBudgeteer reads from the same `ledger.db` that
agentcore writes, and that PCIV's runs surface correctly in its reporting. If that wiring is
already in place, the stack story is ready to tell.
