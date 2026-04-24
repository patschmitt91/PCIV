"""PlanAgent test with a mocked Azure OpenAI client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pciv.agents import PlanAgent
from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig
from pciv.state import Ledger


class _FakeCompletions:
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
        )


class _FakeChat:
    def __init__(self, payloads: list[str]) -> None:
        self.completions = _FakeCompletions(payloads)


class _FakeAzureOpenAI:
    def __init__(self, payloads: list[str]) -> None:
        self.chat = _FakeChat(payloads)


def _valid_plan_json() -> str:
    return json.dumps(
        {
            "goals": ["Refactor auth"],
            "subtasks": [
                {
                    "id": "t1",
                    "description": "Extract token helpers",
                    "dependencies": [],
                    "files": ["src/auth.py"],
                    "acceptance_criteria": ["tests pass"],
                    "risk_flags": [],
                }
            ],
            "global_risks": ["breaking callers"],
        }
    )


def test_plan_agent_happy_path(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    client = _FakeAzureOpenAI([_valid_plan_json()])
    agent = PlanAgent(cfg.models.planner, governor, ledger, "run-1", tracer, client=client)
    plan = agent.run(task="refactor auth", repo_path=".")
    assert plan.goals == ["Refactor auth"]
    assert plan.subtasks[0].id == "t1"

    kwargs = client.chat.completions.calls[0]
    assert kwargs["model"] == cfg.models.planner.model_id()
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][0]["role"] == "system"

    invocations = ledger.fetch_all("agent_invocations")
    assert len(invocations) == 1
    assert invocations[0]["status"] == "ok"
    assert invocations[0]["input_tokens"] == 100


def test_plan_agent_retries_on_bad_json(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    client = _FakeAzureOpenAI(["not json at all", _valid_plan_json()])
    agent = PlanAgent(cfg.models.planner, governor, ledger, "run-2", tracer, client=client)
    plan = agent.run(task="refactor auth", repo_path=".")
    assert plan.subtasks[0].id == "t1"
    assert len(client.chat.completions.calls) == 2
