"""Worktree helper tests against a temporary git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pciv.worktree import create_worktree, current_head, diff_against_base, remove_worktree

from ._gitutil import init_git_repo


def test_worktree_create_and_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_git_repo(repo)
    assert current_head(repo) == base

    wt = create_worktree(repo, run_id="r1", task_id="t1", base_ref=base)
    assert wt.path.is_dir()
    assert wt.branch == "pciv/r1/t1"

    (wt.path / "new.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"], cwd=str(wt.path), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "add new"],
        cwd=str(wt.path),
        check=True,
        capture_output=True,
    )

    diff = diff_against_base(wt)
    assert "new.txt" in diff
    assert "+hello" in diff


def test_worktree_remove(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_git_repo(repo)
    wt = create_worktree(repo, run_id="r2", task_id="t1", base_ref=base)
    remove_worktree(wt, repo)
    assert not wt.path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", wt.branch],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert wt.branch not in branches
