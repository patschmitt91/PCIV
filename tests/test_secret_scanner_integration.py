"""Tests for the diff-time secret scanner wired into Implement and Verify.

Covers two gates introduced in ADR-0006:

1. ``_tool_write_file`` refuses writes whose content matches the
   shared ``agentcore.scan`` catalogue. The implement agent receives
   a tool error and the file is never written.
2. ``Pipeline.run`` runs the scanner over each per-subtask diff after
   the verifier returns and **forces** the verdict to ``reject`` when
   any subtask's diff introduces a secret-shaped string.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentcore.scan import DiffScanner

from pciv.agents import CritiqueAgent, ImplementAgent, PlanAgent, VerifyAgent
from pciv.agents.implement_agent import _tool_write_file
from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig
from pciv.state import Ledger
from pciv.workflow import Pipeline

from ._gitutil import init_git_repo

# ---------------------------------------------------------------------------
# Gate 1: pre-write scanner in _tool_write_file
# ---------------------------------------------------------------------------


def test_write_file_refuses_sk_key(tmp_path: Path) -> None:
    res = _tool_write_file(
        tmp_path,
        "src/leak.py",
        "API_KEY = 'sk-abcdef0123456789ABCDEF'\n",
    )
    assert res["ok"] is False
    assert "secret pattern" in res["error"].lower()
    assert "openai_sk_key" in res["error"]
    assert (tmp_path / "src" / "leak.py").exists() is False
    assert "secret_findings" in res
    assert res["secret_findings"][0]["pattern"] == "openai_sk_key"
    assert "sk-abcdef" not in res["secret_findings"][0]["excerpt"]


def test_write_file_refuses_jwt(tmp_path: Path) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    res = _tool_write_file(tmp_path, "config.py", f"TOKEN = '{jwt}'\n")
    assert res["ok"] is False
    assert "jwt" in res["error"].lower()


def test_write_file_allows_clean_content(tmp_path: Path) -> None:
    res = _tool_write_file(tmp_path, "src/clean.py", "def add(a, b):\n    return a + b\n")
    assert res["ok"] is True
    assert (tmp_path / "src" / "clean.py").read_text(encoding="utf-8") == (
        "def add(a, b):\n    return a + b\n"
    )


def test_write_file_uses_injected_scanner(tmp_path: Path) -> None:
    """A custom scanner can extend or replace the default behaviour."""

    class _AlwaysFlagScanner(DiffScanner):
        def scan_text(self, text: str) -> list[Any]:  # type: ignore[override]
            from agentcore.scan import Finding

            return [Finding(line_number=1, pattern_name="custom", redacted_excerpt="[REDACTED]")]

    res = _tool_write_file(
        tmp_path,
        "x.py",
        "totally clean content\n",
        scanner=_AlwaysFlagScanner(),
    )
    assert res["ok"] is False
    assert "custom" in res["error"]


# ---------------------------------------------------------------------------
# Gate 2: post-implement scanner override in Pipeline.run
# ---------------------------------------------------------------------------


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
async def test_pipeline_scanner_overrides_ship_to_reject_on_secret_diff(
    cfg: PlanConfig,
    governor: BudgetGovernor,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when verifier votes ship, the scanner must force reject.

    We bypass ``_tool_write_file``'s pre-write gate by writing the
    secret-bearing file directly inside the worktree, simulating a
    write that landed via a different path (e.g. a future tool, or a
    file pytest produced inside the worktree). The diff Verify sees
    will contain the secret, the verifier votes ship, and the scanner
    must override.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)

    plan_payload = json.dumps(
        {
            "goals": ["create config"],
            "subtasks": [
                {
                    "id": "t1",
                    "description": "create config.py",
                    "dependencies": [],
                    "files": ["config.py"],
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
    # The verifier votes "ship" deliberately; the scanner must override.
    verdict_payload = json.dumps(
        {"verdict": "ship", "reasons": ["lgtm"], "per_subtask": {"t1": "ship"}}
    )
    impl_complete = json.dumps(
        {"status": "complete", "changed_files": ["config.py"], "notes": "done"}
    )

    plan_client = _FakeClient([_completion(plan_payload)])
    critique_client = _FakeClient([_completion(critique_payload)])
    verify_client = _FakeClient([_completion(verdict_payload)])
    # The implementer "finishes" without invoking write_file. We drop the
    # secret-bearing file directly into the worktree below.
    implement_client = _FakeClient([_completion(impl_complete)])

    real_run = ImplementAgent.run

    def run_then_drop_secret_and_commit(
        self: ImplementAgent,
        subtask: Any,
        worktree: Path,
        iteration: int,
        prior_feedback: Any = None,
    ) -> Any:
        result = real_run(self, subtask, worktree, iteration, prior_feedback)
        # Bypass the pre-write gate to simulate a path that lands content
        # without going through _tool_write_file. The post-implement scanner
        # must still catch this.
        (worktree / "config.py").write_text(
            "API_KEY = 'sk-abcdef0123456789ABCDEF'\n",
            encoding="utf-8",
        )
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "drop config"],
            cwd=str(worktree),
            check=True,
            capture_output=True,
        )
        return result

    monkeypatch.setattr(ImplementAgent, "run", run_then_drop_secret_and_commit)

    # Inject scripted clients on construction.
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

    ledger.record_run("scan-1", "create config", 10.0, 0)
    pipeline = Pipeline(
        cfg=cfg,
        governor=governor,
        ledger=ledger,
        run_id="scan-1",
        tracer=tracer,
        repo=repo,
        gate_cb=gate,
    )
    outcome = await pipeline.run(task="create config", max_iter=0)

    # Scanner must have downgraded the ship verdict to a rejection.
    assert outcome.status == "rejected", outcome.message
    assert outcome.verdict is not None
    assert outcome.verdict.verdict == "reject"
    assert any("diff scanner refused" in r for r in outcome.verdict.reasons), (
        outcome.verdict.reasons
    )
    assert outcome.verdict.per_subtask.get("t1") == "reject"
    # The merge branch should not have been created.
    assert outcome.merge is None
