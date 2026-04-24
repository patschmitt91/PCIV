"""End-to-end Pipeline test with mocked Azure clients and a real tmp git repo."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pciv.agents import CritiqueAgent, ImplementAgent, PlanAgent, VerifyAgent
from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig
from pciv.state import Ledger
from pciv.workflow import Pipeline

from ._gitutil import init_git_repo


def _completion(payload: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload, tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


def _tool_completion(call_id: str, name: str, args: dict[str, Any]) -> Any:
    msg = SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                type="function",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
        ],
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
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


@pytest.mark.asyncio
async def test_pipeline_happy_path_ships_and_merges(
    cfg: PlanConfig,
    governor: BudgetGovernor,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)

    plan_payload = json.dumps(
        {
            "goals": ["add a greeting"],
            "subtasks": [
                {
                    "id": "t1",
                    "description": "create hello.txt",
                    "dependencies": [],
                    "files": ["hello.txt"],
                    "acceptance_criteria": ["file exists"],
                    "risk_flags": [],
                }
            ],
            "global_risks": [],
        }
    )
    critique_payload = json.dumps(
        {
            "valid": True,
            "blocks_proceed": False,
            "issues": [],
            "missing_cases": [],
            "dependency_problems": [],
            "suggested_plan_diff": {},
        }
    )
    verdict_payload = json.dumps(
        {"verdict": "ship", "reasons": ["ok"], "per_subtask": {"t1": "ship"}}
    )
    impl_complete = json.dumps(
        {"status": "complete", "changed_files": ["hello.txt"], "notes": "done"}
    )

    plan_client = _FakeClient([_completion(plan_payload)])
    critique_client = _FakeClient([_completion(critique_payload)])
    verify_client = _FakeClient([_completion(verdict_payload)])
    implement_client = _FakeClient(
        [
            _tool_completion("c1", "write_file", {"path": "hello.txt", "content": "hi\n"}),
            _completion(impl_complete),
        ]
    )

    # Auto-commit implementer output so diff_against_base shows changes.
    # Monkeypatch ImplementAgent.run to add+commit after the real run.
    real_run = ImplementAgent.run

    def run_then_commit(
        self: ImplementAgent, subtask: Any, worktree: Path, iteration: int, prior_feedback: Any = None
    ) -> Any:
        result = real_run(self, subtask, worktree, iteration, prior_feedback)
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"pciv impl {subtask.id}"],
            cwd=str(worktree),
            check=True,
            capture_output=True,
        )
        return result

    monkeypatch.setattr(ImplementAgent, "run", run_then_commit)

    # Patch the build_azure_client used by each agent to return our fakes by
    # instantiating agents directly and swapping their client attribute through
    # a factory monkeypatch.
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

    async def gate(_name: str, _payload: dict[str, Any]) -> str:
        return "approve"

    ledger.record_run("runX", "add greeting", 10.0, 1)
    pipeline = Pipeline(
        cfg=cfg,
        governor=governor,
        ledger=ledger,
        run_id="runX",
        tracer=tracer,
        repo=repo,
        gate_cb=gate,
    )
    outcome = await pipeline.run(task="add greeting", max_iter=1)

    assert outcome.status == "merged", outcome.message
    assert outcome.verdict is not None
    assert outcome.verdict.verdict == "ship"
    assert outcome.merge is not None
    assert outcome.merge.merged_tasks == ["t1"]
    assert outcome.merge.integration_branch == "pciv/runX/integration"

    import subprocess

    out = subprocess.run(
        ["git", "cat-file", "-e", f"{outcome.merge.integration_branch}:hello.txt"],
        cwd=str(repo),
        capture_output=True,
    )
    assert out.returncode == 0
