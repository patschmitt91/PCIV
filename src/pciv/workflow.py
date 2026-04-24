"""Pipeline orchestrator for all four phases.

We intentionally keep orchestration linear and async-native rather than
routing it through an agent-framework graph. Phases 1, 2, and 4 are
strictly sequential, and Phase 3's fan-out uses an asyncio Semaphore
gated worker pool. HITL gates are CLI prompts via a callback.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import CritiqueAgent, ImplementAgent, PlanAgent, VerifyAgent
from .agents.implement_agent import ImplementResult
from .budget import BudgetGovernor
from .config import PlanConfig
from .merge import MergeResult, squash_integration
from .state import Ledger
from .types import Critique, Plan, Subtask, VerdictReport
from .worktree import Worktree, create_worktree, current_head, diff_against_base, remove_worktree

GateCallback = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass
class RunOutcome:
    verdict: VerdictReport | None
    plan: Plan | None
    critique: Critique | None
    iterations_used: int
    status: str
    message: str
    worktrees: dict[str, Worktree] = field(default_factory=dict)
    diffs: dict[str, str] = field(default_factory=dict)
    tests: dict[str, str] = field(default_factory=dict)
    merge: MergeResult | None = None
    base_ref: str | None = None


def _topological_order(subtasks: list[Subtask]) -> list[list[Subtask]]:
    by_id = {s.id: s for s in subtasks}
    remaining = {s.id: set(s.dependencies) & set(by_id) for s in subtasks}
    layers: list[list[Subtask]] = []
    while remaining:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            raise ValueError("dependency cycle in plan")
        layers.append([by_id[tid] for tid in ready])
        for tid in ready:
            remaining.pop(tid)
        for deps in remaining.values():
            for tid in ready:
                deps.discard(tid)
    return layers


class Pipeline:
    def __init__(
        self,
        cfg: PlanConfig,
        governor: BudgetGovernor,
        ledger: Ledger,
        run_id: str,
        tracer: Any,
        repo: Path,
        gate_cb: GateCallback,
    ) -> None:
        self._cfg = cfg
        self._gov = governor
        self._ledger = ledger
        self._run_id = run_id
        self._tracer = tracer
        self._repo = repo.resolve()
        self._gate_cb = gate_cb

        self._planner = PlanAgent(cfg.models.planner, governor, ledger, run_id, tracer)
        self._critic = CritiqueAgent(cfg.models.critic, governor, ledger, run_id, tracer)
        self._implementer = ImplementAgent(cfg.models.implementer, governor, ledger, run_id, tracer)
        self._verifier = VerifyAgent(cfg.models.verifier, governor, ledger, run_id, tracer)

    async def run(self, task: str, max_iter: int) -> RunOutcome:
        plan, critique = await self._phase_plan_critique(task)
        if plan is None or critique is None:
            return RunOutcome(
                verdict=None,
                plan=plan,
                critique=critique,
                iterations_used=0,
                status="aborted_plan",
                message="plan or critique aborted before implementation",
            )

        self._ledger.record_tasks(self._run_id, [s.model_dump() for s in plan.subtasks])

        base_ref = current_head(self._repo)
        worktrees: dict[str, Worktree] = {}
        for subtask in plan.subtasks:
            worktrees[subtask.id] = create_worktree(self._repo, self._run_id, subtask.id, base_ref)

        diffs: dict[str, str] = {tid: "" for tid in worktrees}
        tests: dict[str, str] = {tid: "" for tid in worktrees}
        prior_feedback: dict[str, str] = {}
        pending_tasks = list(plan.subtasks)

        verdict: VerdictReport | None = None
        iteration = 0
        for iteration in range(max_iter + 1):
            await self._phase_implement(plan, pending_tasks, worktrees, iteration, prior_feedback)
            for st in plan.subtasks:
                wt = worktrees[st.id]
                diffs[st.id] = diff_against_base(wt)
                tests[st.id] = _run_pytest_in_worktree(wt.path)

            verdict = self._verifier.run(
                plan=plan,
                per_subtask_diffs=diffs,
                per_subtask_tests=tests,
                iteration=iteration,
            )
            self._ledger.record_verdict(
                self._run_id,
                iteration,
                verdict.verdict,
                verdict.reasons,
                verdict.per_subtask,
            )

            if verdict.verdict == "ship":
                break
            if verdict.verdict == "reject":
                return RunOutcome(
                    verdict=verdict,
                    plan=plan,
                    critique=critique,
                    iterations_used=iteration + 1,
                    status="rejected",
                    message="verifier rejected the implementation",
                    worktrees=worktrees,
                    diffs=diffs,
                    tests=tests,
                )

            pending_tasks = [
                s
                for s in plan.subtasks
                if verdict.per_subtask.get(s.id, verdict.verdict) == "iterate"
            ]
            if not pending_tasks:
                # Verifier said iterate but flagged no subtasks. Treat as
                # inconclusive rather than silently proceeding to merge.
                if verdict.verdict == "iterate":
                    return RunOutcome(
                        verdict=verdict,
                        plan=plan,
                        critique=critique,
                        iterations_used=iteration + 1,
                        status="inconclusive",
                        message="verdict=iterate with no per-subtask targets",
                        worktrees=worktrees,
                        diffs=diffs,
                        tests=tests,
                    )
                break
            prior_feedback = {s.id: "\n".join(verdict.reasons) for s in pending_tasks}
            if iteration >= max_iter:
                return RunOutcome(
                    verdict=verdict,
                    plan=plan,
                    critique=critique,
                    iterations_used=iteration + 1,
                    status="iteration_cap",
                    message="reached iteration cap without ship verdict",
                    worktrees=worktrees,
                    diffs=diffs,
                    tests=tests,
                )

        decision = await self._gate_cb(
            "merge",
            {
                "verdict": verdict.model_dump() if verdict else None,
                "per_subtask": verdict.per_subtask if verdict else {},
            },
        )
        if decision != "approve":
            return RunOutcome(
                verdict=verdict,
                plan=plan,
                critique=critique,
                iterations_used=iteration + 1,
                status="merge_rejected",
                message=f"operator declined merge: {decision}",
                worktrees=worktrees,
                diffs=diffs,
                tests=tests,
                base_ref=base_ref,
            )

        approved_ids = [
            s.id
            for s in plan.subtasks
            if (verdict.per_subtask.get(s.id, verdict.verdict) if verdict else "reject") == "ship"
        ]
        merge_result = squash_integration(
            repo=self._repo,
            run_id=self._run_id,
            base_ref=base_ref,
            approved_task_ids=approved_ids,
            all_task_ids=[s.id for s in plan.subtasks],
        )

        return RunOutcome(
            verdict=verdict,
            plan=plan,
            critique=critique,
            iterations_used=iteration + 1,
            status="merged",
            message=(
                f"merged {len(merge_result.merged_tasks)} of {len(plan.subtasks)} "
                f"subtasks onto {merge_result.integration_branch}"
            ),
            worktrees=worktrees,
            diffs=diffs,
            tests=tests,
            merge=merge_result,
            base_ref=base_ref,
        )

    async def _phase_plan_critique(self, task: str) -> tuple[Plan | None, Critique | None]:
        feedback: str | None = None
        plan: Plan | None = None
        critique: Critique | None = None
        for _revision in range(self._cfg.iteration.max_plan_revisions + 1):
            plan = self._planner.run(
                task=task, repo_path=str(self._repo), iteration=0, critique_feedback=feedback
            )
            critique = self._critic.run(plan=plan, iteration=0)
            if not critique.blocks_proceed:
                break
            feedback = "\n".join(
                critique.issues + critique.missing_cases + critique.dependency_problems
            )
        else:
            # Loop exhausted without a passing critique. Do NOT proceed.
            return None, critique

        decision = await self._gate_cb(
            "plan",
            {"plan": plan.model_dump(), "critique": critique.model_dump()},
        )
        if decision != "approve":
            return None, critique
        return plan, critique

    async def _phase_implement(
        self,
        plan: Plan,
        pending: list[Subtask],
        worktrees: dict[str, Worktree],
        iteration: int,
        prior_feedback: dict[str, str],
    ) -> list[ImplementResult]:
        sem = asyncio.Semaphore(self._cfg.models.implementer.max_concurrency)
        layers = _topological_order(pending)
        results: list[ImplementResult] = []

        async def _run_one(subtask: Subtask) -> ImplementResult:
            async with sem:
                wt = worktrees[subtask.id]
                return await asyncio.to_thread(
                    self._implementer.run,
                    subtask,
                    wt.path,
                    iteration,
                    prior_feedback.get(subtask.id),
                )

        for layer in layers:
            layer_results = await asyncio.gather(*[_run_one(s) for s in layer])
            results.extend(layer_results)
        return results


def cleanup_worktrees(repo: Path, worktrees: dict[str, Worktree]) -> None:
    for wt in worktrees.values():
        with contextlib.suppress(Exception):
            remove_worktree(wt, repo)


def _run_pytest_in_worktree(path: Path, timeout_s: int = 300) -> str:
    """Run pytest inside a worktree and return a truncated stdout+stderr string.

    A missing pytest config or no tests still produces useful output for the
    verifier. Exceptions are captured and returned as text so a failed
    invocation never short-circuits the pipeline.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(path),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "pytest: timed out"
    except FileNotFoundError:
        return "pytest: not installed in PATH"
    except Exception as e:
        return f"pytest: failed to invoke: {type(e).__name__}: {e}"
    header = f"returncode={result.returncode}\n"
    stdout = result.stdout[-6000:]
    stderr = result.stderr[-2000:]
    return header + stdout + ("\n--- stderr ---\n" + stderr if stderr else "")
