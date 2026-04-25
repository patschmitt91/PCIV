"""Regression tests for audit fixes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pciv.types import Plan, Subtask


def test_subtask_id_rejects_path_traversal() -> None:
    with pytest.raises(ValidationError):
        Subtask(id="../evil", description="x")


def test_subtask_id_rejects_slash() -> None:
    with pytest.raises(ValidationError):
        Subtask(id="a/b", description="x")


def test_subtask_id_rejects_whitespace() -> None:
    with pytest.raises(ValidationError):
        Subtask(id="has space", description="x")


def test_subtask_id_accepts_safe_chars() -> None:
    s = Subtask(id="task-1.v2_final", description="x")
    assert s.id == "task-1.v2_final"


def test_plan_rejects_invalid_dep_id() -> None:
    with pytest.raises(ValidationError):
        Plan(
            goals=["g"],
            subtasks=[
                Subtask(id="a", description="x"),
                Subtask(id="b", description="y", dependencies=["a/x"]),
            ],
        )


def test_verdict_report_rejects_invalid_per_subtask_verdict() -> None:
    from pciv.types import VerdictReport

    with pytest.raises(ValidationError):
        VerdictReport(verdict="ship", per_subtask={"t1": "maybe"})


def test_plan_rejects_dependency_cycle() -> None:
    """Regression: cycle detection at validation time, not at topo-sort time."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="cycle"):
        Plan(
            goals=["g"],
            subtasks=[
                Subtask(id="a", description="x", dependencies=["b"]),
                Subtask(id="b", description="y", dependencies=["a"]),
            ],
        )


def test_plan_rejects_too_many_subtasks() -> None:
    """Regression: MAX_SUBTASKS bound on planner output."""
    import pytest
    from pydantic import ValidationError

    from pciv.types import MAX_SUBTASKS

    too_many = [Subtask(id=f"t{i:03d}", description="x") for i in range(MAX_SUBTASKS + 1)]
    with pytest.raises(ValidationError, match=f"MAX_SUBTASKS={MAX_SUBTASKS}"):
        Plan(goals=["g"], subtasks=too_many)


def test_plan_rejects_duplicate_subtask_ids() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="duplicate"):
        Plan(
            goals=["g"],
            subtasks=[
                Subtask(id="a", description="x"),
                Subtask(id="a", description="y"),
            ],
        )
