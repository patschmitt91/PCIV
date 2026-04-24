"""VerifyAgent tests with a mocked Azure OpenAI client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pciv.agents import VerifyAgent
from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig
from pciv.state import Ledger
from pciv.types import Plan, Subtask


class _FakeCompletions:
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
            usage=SimpleNamespace(prompt_tokens=60, completion_tokens=30),
        )


class _FakeChat:
    def __init__(self, payloads: list[str]) -> None:
        self.completions = _FakeCompletions(payloads)


class _FakeAzureOpenAI:
    def __init__(self, payloads: list[str]) -> None:
        self.chat = _FakeChat(payloads)


def _plan() -> Plan:
    return Plan(
        goals=["g"],
        subtasks=[Subtask(id="t1", description="d"), Subtask(id="t2", description="d")],
    )


def test_verify_agent_ship(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    payload = json.dumps(
        {
            "verdict": "ship",
            "reasons": ["all green"],
            "per_subtask": {"t1": "ship", "t2": "ship"},
        }
    )
    client = _FakeAzureOpenAI([payload])
    agent = VerifyAgent(cfg.models.verifier, governor, ledger, "run-1", tracer, client=client)
    report = agent.run(
        plan=_plan(),
        per_subtask_diffs={"t1": "diff1", "t2": "diff2"},
        per_subtask_tests={"t1": "", "t2": ""},
        iteration=0,
    )
    assert report.verdict == "ship"
    assert report.per_subtask == {"t1": "ship", "t2": "ship"}


def test_verify_agent_retries_on_bad_json(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    good = json.dumps(
        {
            "verdict": "iterate",
            "reasons": ["missing tests"],
            "per_subtask": {"t1": "iterate", "t2": "ship"},
        }
    )
    client = _FakeAzureOpenAI(["not json", good])
    agent = VerifyAgent(cfg.models.verifier, governor, ledger, "run-2", tracer, client=client)
    report = agent.run(
        plan=_plan(),
        per_subtask_diffs={"t1": "", "t2": ""},
        per_subtask_tests={"t1": "", "t2": ""},
        iteration=0,
    )
    assert report.verdict == "iterate"
    assert len(client.chat.completions.calls) == 2
