"""Squash-merge each approved subtask branch onto an integration branch.

We create a dedicated ``pciv/<run_id>/integration`` branch off ``base_ref`` via
a throwaway worktree. Each approved subtask branch is squash-merged and
committed on top. The caller's HEAD and index are never touched. An optional
GitHub PR can be opened separately via ``gh``; we do not shell out to ``gh``
here to keep this module offline-testable.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MergeResult:
    integration_branch: str
    merged_tasks: list[str]
    skipped_tasks: list[str]


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def squash_integration(
    repo: Path,
    run_id: str,
    base_ref: str,
    approved_task_ids: list[str],
    all_task_ids: list[str],
    commit_message_for: dict[str, str] | None = None,
) -> MergeResult:
    """Squash-merge approved subtask branches onto an integration branch.

    Args:
        repo: path to the main repository.
        run_id: pciv run identifier.
        base_ref: commit/branch to base the integration branch on.
        approved_task_ids: subtasks operator has approved for merge.
        all_task_ids: full set of task ids (used only to preserve ordering).
        commit_message_for: optional per-task commit messages.

    Returns:
        MergeResult with the integration branch and merged/skipped ids.
    """
    repo = repo.resolve()
    integration = f"pciv/{run_id}/integration"
    wt_path = repo / ".pciv" / "worktrees" / run_id / "_integration"
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    if wt_path.exists():
        shutil.rmtree(wt_path, ignore_errors=True)

    existing = _run_git(["branch", "--list", integration], cwd=repo)
    if existing:
        _run_git(["branch", "-D", integration], cwd=repo)

    _run_git(["worktree", "add", "-b", integration, str(wt_path), base_ref], cwd=repo)

    merged: list[str] = []
    skipped: list[str] = []
    messages = commit_message_for or {}
    try:
        for task_id in all_task_ids:
            if task_id not in approved_task_ids:
                skipped.append(task_id)
                continue
            branch = f"pciv/{run_id}/{task_id}"
            try:
                _run_git(["merge", "--squash", "--no-commit", branch], cwd=wt_path)
            except subprocess.CalledProcessError:
                with contextlib.suppress(subprocess.CalledProcessError):
                    _run_git(["merge", "--abort"], cwd=wt_path)
                skipped.append(task_id)
                continue

            status = _run_git(["status", "--porcelain"], cwd=wt_path)
            if not status:
                skipped.append(task_id)
                continue

            msg = messages.get(task_id, f"pciv({run_id}): squash {task_id}")
            _run_git(["add", "-A"], cwd=wt_path)
            _run_git(["commit", "-m", msg], cwd=wt_path)
            merged.append(task_id)
    finally:
        try:
            _run_git(["worktree", "remove", "--force", str(wt_path)], cwd=repo)
        except subprocess.CalledProcessError:
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)

    return MergeResult(
        integration_branch=integration,
        merged_tasks=merged,
        skipped_tasks=skipped,
    )
