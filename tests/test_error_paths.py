"""Error-path tests covering the four scariest failure modes.

PCIV-1 backlog item: prove the pipeline degrades safely under

* mid-run budget exhaustion,
* operator rejection at the HITL merge gate,
* malformed plan JSON after retry exhaustion, and
* partial worktree merge (one subtask conflicts, the other ships).

These tests do not exercise new code paths — they assert existing
behaviour that the v0.1 happy-path tests left implicit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pciv.agents import CritiqueAgent, ImplementAgent, PlanAgent, VerifyAgent
from pciv.budget import BudgetExceededError, BudgetGovernor
from pciv.config import PlanConfig
from pciv.merge import squash_integration
from pciv.state import Ledger
from pciv.workflow import Pipeline
from pciv.worktree import create_worktree

from ._gitutil import init_git_repo

# ---------------------------------------------------------------------------
# Shared client mocking
# ---------------------------------------------------------------------------


def _completion(payload: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload, tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


class _Scripted:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no more scripted responses")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = SimpleNamespace(completions=_Scripted(responses))


def _good_plan_payload(task_id: str = "t1") -> str:
    return json.dumps(
        {
            "goals": ["g"],
            "subtasks": [
                {
                    "id": task_id,
                    "description": "d",
                    "dependencies": [],
                    "files": [f"{task_id}.txt"],
                    "acceptance_criteria": ["ok"],
                    "risk_flags": [],
                }
            ],
            "global_risks": [],
        }
    )


def _good_critique_payload() -> str:
    return json.dumps(
        {
            "valid": True,
            "blocks_proceed": False,
            "issues": [],
            "missing_cases": [],
            "dependency_problems": [],
            "suggested_plan_diff": {},
        }
    )


# ---------------------------------------------------------------------------
# 1. Malformed plan JSON exhausts retries → RuntimeError
# ---------------------------------------------------------------------------


def test_plan_agent_raises_after_retry_exhaustion(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    """All ``retries+1`` attempts return malformed JSON.

    Verifies the PlanAgent surfaces a RuntimeError that names the agent
    and includes the validation error message rather than silently
    returning a partial plan.
    """
    attempts = cfg.models.planner.retries + 1
    bad_payloads = ["not json"] * attempts
    client = _FakeClient([_completion(p) for p in bad_payloads])

    agent = PlanAgent(cfg.models.planner, governor, ledger, "run-1", tracer, client=client)
    with pytest.raises(RuntimeError) as ei:
        agent.run(task="anything", repo_path=".")
    assert "plan_agent" in str(ei.value)
    assert "malformed JSON" in str(ei.value)
    # Each attempt charged the budget; the run as a whole was bounded.
    assert len(client.chat.completions.calls) == attempts
    invocations = ledger.fetch_all("agent_invocations")
    assert len(invocations) == attempts
    # All invocations should have been finished (not left in 'started').
    statuses = {inv["status"] for inv in invocations}
    assert statuses <= {"ok", "error"}


# ---------------------------------------------------------------------------
# 2. Operator rejects merge gate → status == merge_rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_merge_gate_rejection_aborts_cleanly(
    cfg: PlanConfig,
    governor: BudgetGovernor,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifier votes ship, operator declines at the merge gate.

    Outcome must be status='merge_rejected', no integration branch
    created, no exception escapes.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)

    plan_client = _FakeClient([_completion(_good_plan_payload())])
    critique_client = _FakeClient([_completion(_good_critique_payload())])
    verify_client = _FakeClient(
        [_completion(json.dumps({"verdict": "ship", "reasons": [], "per_subtask": {"t1": "ship"}}))]
    )
    impl_complete = json.dumps({"status": "complete", "changed_files": ["t1.txt"], "notes": "done"})
    implement_client = _FakeClient([_completion(impl_complete)])

    real_run = ImplementAgent.run

    def run_then_commit(
        self: ImplementAgent,
        subtask: Any,
        worktree: Path,
        iteration: int,
        prior_feedback: Any = None,
    ) -> Any:
        result = real_run(self, subtask, worktree, iteration, prior_feedback)
        (worktree / "t1.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "impl"],
            cwd=str(worktree),
            check=True,
            capture_output=True,
        )
        return result

    monkeypatch.setattr(ImplementAgent, "run", run_then_commit)
    _patch_agent_clients(monkeypatch, plan_client, critique_client, implement_client, verify_client)

    decisions: list[str] = []

    async def gate(name: str, _payload: dict[str, Any]) -> str:
        decisions.append(name)
        if name == "merge":
            return "decline"
        return "approve"

    ledger.record_run("merge-rej", "x", 10.0, 0)
    pipeline = Pipeline(
        cfg=cfg,
        governor=governor,
        ledger=ledger,
        run_id="merge-rej",
        tracer=tracer,
        repo=repo,
        gate_cb=gate,
    )
    outcome = await pipeline.run(task="x", max_iter=0)

    assert outcome.status == "merge_rejected"
    assert "decline" in outcome.message
    assert outcome.merge is None
    assert outcome.verdict is not None and outcome.verdict.verdict == "ship"
    assert decisions == ["plan", "merge"]
    # Integration branch was never created.
    branches = subprocess.run(
        ["git", "branch", "--list", "pciv/merge-rej/integration"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branches == ""


# ---------------------------------------------------------------------------
# 3. Mid-run budget exhaustion surfaces BudgetExceededError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_budget_exhaustion_mid_run_surfaces_exception(
    cfg: PlanConfig,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tight ceiling that the planner cost exceeds raises mid-run.

    The planner returns one good plan worth ~$3e-4 (10 in / 20 out tokens
    at $2.50 input + $10 output per MTok). We set the ceiling well below
    that to force the second `charge()` call to raise.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)

    # Two scripted responses so the planner has something for attempt 1.
    plan_client = _FakeClient([_completion(_good_plan_payload())])
    critique_client = _FakeClient([_completion(_good_critique_payload())])
    verify_client = _FakeClient(
        [_completion(json.dumps({"verdict": "ship", "reasons": [], "per_subtask": {}}))]
    )
    implement_client = _FakeClient(
        [_completion(json.dumps({"status": "complete", "changed_files": [], "notes": ""}))]
    )

    _patch_agent_clients(monkeypatch, plan_client, critique_client, implement_client, verify_client)

    async def gate(_name: str, _payload: dict[str, Any]) -> str:
        return "approve"

    # A ceiling so small that even one charge breaches it. The planner is
    # the first thing called; that call's cost (~$3e-4) > $1e-9.
    tight = BudgetGovernor(ceiling_usd=1e-9, cfg=cfg)
    ledger.record_run("budget-exh", "x", 1e-9, 0)
    pipeline = Pipeline(
        cfg=cfg,
        governor=tight,
        ledger=ledger,
        run_id="budget-exh",
        tracer=tracer,
        repo=repo,
        gate_cb=gate,
    )
    with pytest.raises(BudgetExceededError):
        await pipeline.run(task="x", max_iter=0)
    # Ledger should record the failed plan invocation rather than swallow it.
    invocations = ledger.fetch_all("agent_invocations")
    assert any(inv["agent_id"] == "plan_agent" for inv in invocations)
    plan_inv = next(inv for inv in invocations if inv["agent_id"] == "plan_agent")
    assert plan_inv["status"] == "error"


# ---------------------------------------------------------------------------
# 4. Partial merge: one subtask conflicts, the other ships, no orphan worktree
# ---------------------------------------------------------------------------


def test_squash_integration_partial_failure_cleans_up_worktree(tmp_path: Path) -> None:
    """When the second subtask conflicts, the integration worktree must
    still be removed from disk so subsequent runs of the same run_id
    don't trip over the leftover.

    `test_merge.test_squash_integration_skips_conflicting_task` already
    proves the verdict shape; this test extends the assertion to
    on-disk cleanup, which is the failure mode that breaks reruns.
    """
    repo = tmp_path / "repo"
    base = init_git_repo(repo)

    wt_a = create_worktree(repo, run_id="r-cleanup", task_id="a", base_ref=base)
    (wt_a.path / "shared.txt").write_text("from a\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(wt_a.path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "a"], cwd=str(wt_a.path), check=True, capture_output=True
    )

    wt_b = create_worktree(repo, run_id="r-cleanup", task_id="b", base_ref=base)
    (wt_b.path / "shared.txt").write_text("from b\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(wt_b.path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "b"], cwd=str(wt_b.path), check=True, capture_output=True
    )

    integration_wt = repo / ".pciv" / "worktrees" / "r-cleanup" / "_integration"

    result = squash_integration(
        repo=repo,
        run_id="r-cleanup",
        base_ref=base,
        approved_task_ids=["a", "b"],
        all_task_ids=["a", "b"],
    )
    assert result.merged_tasks == ["a"]
    assert result.skipped_tasks == ["b"]
    assert result.skip_reasons["b"] == "merge_conflict"
    # The integration worktree directory must be gone so a re-run of the
    # same run_id can recreate it without colliding.
    assert not integration_wt.exists()
    # And `git worktree list` should not list the integration path.
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert str(integration_wt) not in listing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_agent_clients(
    monkeypatch: pytest.MonkeyPatch,
    plan_client: _FakeClient,
    critique_client: _FakeClient,
    implement_client: _FakeClient,
    verify_client: _FakeClient,
) -> None:
    real_init_plan = PlanAgent.__init__
    real_init_critique = CritiqueAgent.__init__
    real_init_impl = ImplementAgent.__init__
    real_init_verify = VerifyAgent.__init__

    def init_plan(self: PlanAgent, *args: Any, **kwargs: Any) -> None:
        kwargs["client"] = plan_client
        real_init_plan(self, *args, **kwargs)

    def init_critique(self: CritiqueAgent, *args: Any, **kwargs: Any) -> None:
        kwargs["client"] = critique_client
        real_init_critique(self, *args, **kwargs)

    def init_impl(self: ImplementAgent, *args: Any, **kwargs: Any) -> None:
        kwargs["client"] = implement_client
        real_init_impl(self, *args, **kwargs)

    def init_verify(self: VerifyAgent, *args: Any, **kwargs: Any) -> None:
        kwargs["client"] = verify_client
        real_init_verify(self, *args, **kwargs)

    monkeypatch.setattr(PlanAgent, "__init__", init_plan)
    monkeypatch.setattr(CritiqueAgent, "__init__", init_critique)
    monkeypatch.setattr(ImplementAgent, "__init__", init_impl)
    monkeypatch.setattr(VerifyAgent, "__init__", init_verify)
