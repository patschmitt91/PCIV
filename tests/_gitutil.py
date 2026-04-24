"""Shared helpers for git-backed tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


def init_git_repo(path: Path) -> str:
    """Initialize a git repo with a single initial commit. Returns base ref."""
    path.mkdir(parents=True, exist_ok=True)

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=str(path), check=True, capture_output=True, text=True
        )

    run(["init", "-b", "main"])
    run(["config", "user.email", "pciv-test@invalid"])
    run(["config", "user.name", "pciv-test"])
    run(["config", "commit.gpgsign", "false"])
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    run(["add", "."])
    run(["commit", "-m", "seed"])
    return run(["rev-parse", "HEAD"]).stdout.strip()
