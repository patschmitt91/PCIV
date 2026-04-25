"""ImplementAgent tests with a mocked Azure OpenAI client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pciv.agents import ImplementAgent
from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig
from pciv.state import Ledger
from pciv.types import Subtask


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _response(content: str | None, tool_calls: list[Any] | None = None) -> Any:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


class _FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeChat:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = _FakeCompletions(responses)


class _FakeAzureOpenAI:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = _FakeChat(responses)


def test_implement_agent_writes_file_and_completes(
    cfg: PlanConfig,
    governor: BudgetGovernor,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()

    final_json = json.dumps({"status": "complete", "changed_files": ["hello.txt"], "notes": "ok"})
    responses = [
        _response(
            content=None,
            tool_calls=[_tool_call("c1", "write_file", {"path": "hello.txt", "content": "hi"})],
        ),
        _response(content=final_json),
    ]
    client = _FakeAzureOpenAI(responses)
    agent = ImplementAgent(cfg.models.implementer, governor, ledger, "run-1", tracer, client=client)
    subtask = Subtask(
        id="t1",
        description="write hello",
        files=["hello.txt"],
        acceptance_criteria=["file exists"],
    )
    result = agent.run(subtask=subtask, worktree=wt, iteration=0)
    assert result.status == "complete"
    assert result.changed_files == ["hello.txt"]
    assert (wt / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert result.turns == 2


def test_implement_agent_rejects_path_escape(
    cfg: PlanConfig,
    governor: BudgetGovernor,
    ledger: Ledger,
    tracer: Any,
    tmp_path: Path,
) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()

    final_json = json.dumps({"status": "complete", "changed_files": [], "notes": ""})
    responses = [
        _response(
            content=None,
            tool_calls=[
                _tool_call("c1", "write_file", {"path": "../escape.txt", "content": "nope"})
            ],
        ),
        _response(content=final_json),
    ]
    client = _FakeAzureOpenAI(responses)
    agent = ImplementAgent(cfg.models.implementer, governor, ledger, "run-2", tracer, client=client)
    subtask = Subtask(id="t1", description="d")
    result = agent.run(subtask=subtask, worktree=wt, iteration=0)
    assert result.status == "complete"
    assert not (tmp_path / "escape.txt").exists()
    # The tool result should have been an error; verify via inspecting that the
    # write did not occur. Tool-level errors do not fail the run.


def test_tool_write_file_rejects_out_of_scope_path(tmp_path) -> None:
    """Regression: write_file enforces subtask.files at the tool boundary."""
    from pciv.agents.implement_agent import _tool_write_file

    res = _tool_write_file(
        tmp_path,
        "src/wrong.py",
        "x = 1\n",
        allowed_files=["src/right.py"],
    )
    assert res["ok"] is False
    assert "outside subtask file scope" in res["error"]
    assert not (tmp_path / "src" / "wrong.py").exists()


def test_tool_write_file_allows_in_scope_path(tmp_path) -> None:
    from pciv.agents.implement_agent import _tool_write_file

    res = _tool_write_file(
        tmp_path,
        "src/right.py",
        "x = 1\n",
        allowed_files=["src/right.py"],
    )
    assert res["ok"] is True
    assert (tmp_path / "src" / "right.py").read_text(encoding="utf-8") == "x = 1\n"


def test_tool_write_file_unrestricted_when_allowed_empty(tmp_path) -> None:
    """Empty allowed_files preserves backwards compatibility."""
    from pciv.agents.implement_agent import _tool_write_file

    res = _tool_write_file(tmp_path, "any/where.py", "y = 2\n", allowed_files=None)
    assert res["ok"] is True
