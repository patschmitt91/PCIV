"""Tests for pipeline-level helpers that do not hit an LLM."""

from __future__ import annotations

from pathlib import Path

from pciv.types import Subtask
from pciv.workflow import _run_pytest_in_worktree, _topological_order, cleanup_worktrees
from pciv.worktree import create_worktree

from ._gitutil import init_git_repo


def test_run_pytest_returns_output_with_no_tests(tmp_path: Path) -> None:
    out = _run_pytest_in_worktree(tmp_path, timeout_s=60)
    # pytest exits with 5 when no tests collected; we just want text back.
    assert "returncode=" in out


def test_topological_order_layers_deps() -> None:
    subtasks = [
        Subtask(id="a", description="a"),
        Subtask(id="b", description="b", dependencies=["a"]),
        Subtask(id="c", description="c", dependencies=["a"]),
        Subtask(id="d", description="d", dependencies=["b", "c"]),
    ]
    layers = _topological_order(subtasks)
    assert [s.id for s in layers[0]] == ["a"]
    assert sorted(s.id for s in layers[1]) == ["b", "c"]
    assert [s.id for s in layers[2]] == ["d"]


def test_cleanup_worktrees_removes_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_git_repo(repo)
    wts = {
        "t1": create_worktree(repo, run_id="rC", task_id="t1", base_ref=base),
        "t2": create_worktree(repo, run_id="rC", task_id="t2", base_ref=base),
    }
    for wt in wts.values():
        assert wt.path.exists()
    cleanup_worktrees(repo, wts)
    for wt in wts.values():
        assert not wt.path.exists()
