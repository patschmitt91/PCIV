"""End-to-end test of `pciv run` via Typer's CliRunner.

Every Azure OpenAI call is stubbed. A real tmp git repo backs the
worktree / merge plumbing. The assertion asserts the ledger contains
exactly the expected row count for a ship verdict with 1 subtask and
0 iterate rounds.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from pciv.agents import CritiqueAgent, ImplementAgent, PlanAgent, VerifyAgent
from pciv.cli import app
from pciv.state import Ledger

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

    def create(self, **_: Any) -> Any:
        if not self._responses:
            raise AssertionError("no more scripted responses")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = SimpleNamespace(completions=_Scripted(responses))


def _write_tiny_plan_yaml(path: Path, state_dir: Path) -> None:
    cfg = {
        "version": 1,
        "models": {
            "planner": {"provider": "azure_openai", "deployment": "azure-reasoning"},
            "critic": {"provider": "azure_openai", "deployment": "azure-reasoning"},
            "implementer": {
                "provider": "azure_openai",
                "deployment": "azure-codegen",
                "max_concurrency": 1,
            },
            "verifier": {"provider": "azure_openai", "deployment": "azure-reasoning"},
        },
        "pricing": {
            "azure-reasoning": {"input_per_mtok": 0.01, "output_per_mtok": 0.01},
            "azure-codegen": {"input_per_mtok": 0.01, "output_per_mtok": 0.01},
        },
        "budget": {
            "default_ceiling_usd": 0.01,
            "projection": {
                "plan_input_tokens": 10,
                "plan_output_tokens": 10,
                "critique_input_tokens": 10,
                "critique_output_tokens": 10,
                "implement_input_tokens_per_subtask": 10,
                "implement_output_tokens_per_subtask": 10,
                "verify_input_tokens": 10,
                "verify_output_tokens": 10,
                "expected_subtasks": 1,
            },
        },
        "iteration": {"max_rounds": 1, "max_plan_revisions": 1},
        "gates": {
            "approve_plan": {"enabled": True, "default": "approve"},
            "approve_merge": {"enabled": True, "default": "approve"},
        },
        "telemetry": {
            "service_name": "pciv-test",
            "app_insights_connection_string_env": "APPLICATIONINSIGHTS_CONNECTION_STRING_UNSET",
        },
        "runtime": {
            "state_dir": str(state_dir),
            "sqlite_path": str(state_dir / "ledger.db"),
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _install_fake_agents(monkeypatch: pytest.MonkeyPatch) -> None:
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

    real_run = ImplementAgent.run

    def run_then_commit(
        self: ImplementAgent,
        subtask: Any,
        worktree: Path,
        iteration: int,
        prior_feedback: Any = None,
    ) -> Any:
        result = real_run(self, subtask, worktree, iteration, prior_feedback)
        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"pciv impl {subtask.id}"],
            cwd=str(worktree),
            check=True,
            capture_output=True,
        )
        return result

    monkeypatch.setattr(ImplementAgent, "run", run_then_commit)

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


def test_cli_run_ship_one_subtask_zero_iterate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_tiny_plan_yaml(cfg_path, state_dir)

    _install_fake_agents(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "add a greeting",
            "--yes",
            "--budget",
            "0.01",
            "--config",
            str(cfg_path),
            "--repo",
            str(repo),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "status=merged" in result.output

    db_path = state_dir / "ledger.db"
    assert db_path.exists()

    with Ledger(db_path) as ledger:
        runs = ledger.fetch_all("runs")
        tasks = ledger.fetch_all("tasks")
        invocations = ledger.fetch_all("agent_invocations")
        costs = ledger.fetch_all("cost_events")
        verdicts = ledger.fetch_all("verdicts")

    assert len(runs) == 1
    assert runs[0]["status"] == "merged"
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "t1"
    # Plan, critique, implement, verify — one each, no iterate round.
    assert len(invocations) == 4
    phases = sorted(row["phase"] for row in invocations)
    assert phases == ["critique", "implement", "plan", "verify"]
    assert len(costs) == 4
    # Single verdict for iteration 0, with no iterate round.
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "ship"
