"""CritiqueAgent test with a mocked Azure OpenAI client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pciv.agents import CritiqueAgent
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
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=75),
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
        subtasks=[Subtask(id="t1", description="d")],
        global_risks=[],
    )


def _critique_json(valid: bool = True, blocks: bool = False) -> str:
    return json.dumps(
        {
            "valid": valid,
            "blocks_proceed": blocks,
            "issues": [],
            "missing_cases": [],
            "dependency_problems": [],
            "suggested_plan_diff": {},
        }
    )


def test_critique_agent_happy_path(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    client = _FakeAzureOpenAI([_critique_json()])
    agent = CritiqueAgent(cfg.models.critic, governor, ledger, "run-1", tracer, client=client)
    critique = agent.run(plan=_plan())
    assert critique.valid is True
    assert critique.blocks_proceed is False

    kwargs = client.chat.completions.calls[0]
    assert kwargs["model"] == cfg.models.critic.model_id()
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][0]["role"] == "system"

    invocations = ledger.fetch_all("agent_invocations")
    assert invocations[0]["status"] == "ok"


def test_critique_agent_retries_on_schema_error(
    cfg: PlanConfig, governor: BudgetGovernor, ledger: Ledger, tracer: Any
) -> None:
    bad = json.dumps({"blocks_proceed": False, "issues": []})
    client = _FakeAzureOpenAI([bad, _critique_json()])
    agent = CritiqueAgent(cfg.models.critic, governor, ledger, "run-2", tracer, client=client)
    critique = agent.run(plan=_plan())
    assert critique.valid is True
    assert len(client.chat.completions.calls) == 2
