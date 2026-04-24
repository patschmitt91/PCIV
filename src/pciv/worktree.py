"""Git worktree orchestration for Phase 3 subagents."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Worktree:
    task_id: str
    path: Path
    branch: str
    base_ref: str


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def current_head(repo: Path) -> str:
    return _run_git(["rev-parse", "HEAD"], cwd=repo)


def create_worktree(repo: Path, run_id: str, task_id: str, base_ref: str) -> Worktree:
    root = repo / ".pciv" / "worktrees" / run_id / task_id
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        shutil.rmtree(root)
    branch = f"pciv/{run_id}/{task_id}"
    _run_git(["worktree", "add", "-b", branch, str(root), base_ref], cwd=repo)
    return Worktree(task_id=task_id, path=root, branch=branch, base_ref=base_ref)


def diff_against_base(wt: Worktree) -> str:
    # Use two exclude pathspecs: one for the .pciv dir entry itself and one
    # glob to exclude all files beneath it (e.g. ledger.db, worktree dirs).
    return _run_git(
        ["diff", wt.base_ref, "--", ".", ":(exclude).pciv", ":(exclude,glob).pciv/**"],
        cwd=wt.path,
    )


def remove_worktree(wt: Worktree, repo: Path) -> None:
    try:
        _run_git(["worktree", "remove", "--force", str(wt.path)], cwd=repo)
    except subprocess.CalledProcessError:
        if wt.path.exists():
            shutil.rmtree(wt.path, ignore_errors=True)
    with contextlib.suppress(subprocess.CalledProcessError):
        _run_git(["branch", "-D", wt.branch], cwd=repo)
