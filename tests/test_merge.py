"""squash_integration tests against a temporary git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pciv.merge import squash_integration
from pciv.worktree import create_worktree

from ._gitutil import init_git_repo


def _commit_file(wt_path: Path, relpath: str, content: str, msg: str) -> None:
    (wt_path / relpath).parent.mkdir(parents=True, exist_ok=True)
    (wt_path / relpath).write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"], cwd=str(wt_path), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=str(wt_path), check=True, capture_output=True
    )


def test_squash_integration_merges_approved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_git_repo(repo)

    wt_a = create_worktree(repo, run_id="r1", task_id="a", base_ref=base)
    _commit_file(wt_a.path, "a.txt", "A\n", "task a")
    wt_b = create_worktree(repo, run_id="r1", task_id="b", base_ref=base)
    _commit_file(wt_b.path, "b.txt", "B\n", "task b")

    result = squash_integration(
        repo=repo,
        run_id="r1",
        base_ref=base,
        approved_task_ids=["a", "b"],
        all_task_ids=["a", "b"],
    )
    assert result.merged_tasks == ["a", "b"]
    assert result.skipped_tasks == []
    assert result.integration_branch == "pciv/r1/integration"

    log = subprocess.run(
        ["git", "log", "--oneline", result.integration_branch],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "squash a" in log
    assert "squash b" in log

    # Integration branch should contain both files.
    ls_a = subprocess.run(
        ["git", "cat-file", "-e", f"{result.integration_branch}:a.txt"],
        cwd=str(repo),
        capture_output=True,
    )
    assert ls_a.returncode == 0
    ls_b = subprocess.run(
        ["git", "cat-file", "-e", f"{result.integration_branch}:b.txt"],
        cwd=str(repo),
        capture_output=True,
    )
    assert ls_b.returncode == 0


def test_squash_integration_skips_unapproved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_git_repo(repo)

    wt_a = create_worktree(repo, run_id="r2", task_id="a", base_ref=base)
    _commit_file(wt_a.path, "a.txt", "A\n", "task a")
    wt_b = create_worktree(repo, run_id="r2", task_id="b", base_ref=base)
    _commit_file(wt_b.path, "b.txt", "B\n", "task b")

    result = squash_integration(
        repo=repo,
        run_id="r2",
        base_ref=base,
        approved_task_ids=["a"],
        all_task_ids=["a", "b"],
    )
    assert result.merged_tasks == ["a"]
    assert result.skipped_tasks == ["b"]

    # Only a.txt should exist.
    ls_a = subprocess.run(
        ["git", "cat-file", "-e", f"{result.integration_branch}:a.txt"],
        cwd=str(repo),
        capture_output=True,
    )
    assert ls_a.returncode == 0
    ls_b = subprocess.run(
        ["git", "cat-file", "-e", f"{result.integration_branch}:b.txt"],
        cwd=str(repo),
        capture_output=True,
    )
    assert ls_b.returncode != 0


def test_squash_integration_skips_conflicting_task(tmp_path: Path) -> None:
    """Two worktrees edit the same file at the same line. Second merge conflicts."""
    repo = tmp_path / "repo"
    base = init_git_repo(repo)

    wt_a = create_worktree(repo, run_id="r3", task_id="a", base_ref=base)
    _commit_file(wt_a.path, "shared.txt", "from task a\n", "task a")
    wt_b = create_worktree(repo, run_id="r3", task_id="b", base_ref=base)
    _commit_file(wt_b.path, "shared.txt", "from task b\n", "task b")

    result = squash_integration(
        repo=repo,
        run_id="r3",
        base_ref=base,
        approved_task_ids=["a", "b"],
        all_task_ids=["a", "b"],
    )
    assert "a" in result.merged_tasks
    assert "b" in result.skipped_tasks

    content = subprocess.run(
        ["git", "show", f"{result.integration_branch}:shared.txt"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "from task a" in content
    assert "from task b" not in content
