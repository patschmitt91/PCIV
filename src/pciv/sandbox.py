"""Sandboxed pytest execution for trusted/untrusted task content.

Threat model
------------
The Implement and Verify phases execute ``python -m pytest`` inside a git
worktree whose contents may have been authored (in part or whole) by an
LLM. A malicious ``conftest.py`` or ``pytest_plugins`` entry would
otherwise execute with full host privileges as soon as pytest collects
the worktree.

This module is the single chokepoint for invoking pytest. Two modes:

* ``trusted``   -- in-process subprocess on the host. Plugin autoload is
  disabled to defang accidental third-party plugin loads, but a
  hand-crafted ``conftest.py`` will still execute. Use only when the
  task content is fully internal.
* ``untrusted`` (default in :class:`pciv.config.PlanConfig`) -- pytest is
  invoked inside a short-lived container (Docker or Podman) with no
  network, a read-only mount of the worktree, dropped capabilities, and
  a non-root uid. If neither runtime is available, sandboxing fails
  closed with an actionable error rather than degrading to host
  execution.

See ``docs/decisions/0004-untrusted-task-sandbox.md`` for the full ADR.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LOG = logging.getLogger(__name__)

TaskTrust = Literal["trusted", "untrusted"]

# Pinned via Phase 0 Dependabot docker ecosystem; see pyproject/Dockerfile.
_SANDBOX_IMAGE = "python:3.12-slim"

# Hardened env for every pytest invocation. PYTEST_DISABLE_PLUGIN_AUTOLOAD
# stops setuptools-discovered plugins from being loaded; PY_COLORS prevents
# ANSI escapes from polluting the captured output we feed to the verifier.
_HARDENED_ENV: dict[str, str] = {
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "PY_COLORS": "0",
}

# Args appended to every pytest run. -p no:cacheprovider keeps the worktree
# free of .pytest_cache scribbles that would otherwise show up in diffs.
_HARDENED_ARGS: tuple[str, ...] = ("-p", "no:cacheprovider")


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    sandboxed: bool  # True if executed inside a container
    runtime: str | None  # "docker" | "podman" | None when host-executed


class SandboxUnavailableError(RuntimeError):
    """Raised when untrusted mode is requested but no container runtime is on PATH."""


def detect_runtime() -> str | None:
    """Return the name of the first container runtime on PATH, else ``None``."""
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    return None


def run_pytest(
    worktree: Path,
    *,
    trust: TaskTrust,
    extra_args: list[str] | None = None,
    timeout_s: int = 300,
    image: str = _SANDBOX_IMAGE,
) -> SandboxResult:
    """Execute pytest against ``worktree`` honoring the ``trust`` boundary.

    For ``untrusted``, raises :class:`SandboxUnavailableError` if no
    container runtime is on PATH (fail-closed).
    """

    args: list[str] = list(_HARDENED_ARGS)
    if extra_args:
        args.extend(extra_args)

    if trust == "trusted":
        return _run_host(worktree, args, timeout_s)

    runtime = detect_runtime()
    if runtime is None:
        raise SandboxUnavailableError(
            "Untrusted task mode requires Docker or Podman on PATH; "
            "install one or set task_trust: trusted in plan.yaml after "
            "reviewing docs/decisions/0004-untrusted-task-sandbox.md"
        )

    return _run_container(worktree, args, timeout_s, runtime=runtime, image=image)


def _run_host(worktree: Path, args: list[str], timeout_s: int) -> SandboxResult:
    env = {**os.environ, **_HARDENED_ENV}
    cmd = [sys.executable, "-m", "pytest", "-q", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(worktree),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            returncode=124,
            stdout="",
            stderr="pytest: timed out",
            sandboxed=False,
            runtime=None,
        )
    except FileNotFoundError:
        return SandboxResult(
            returncode=127,
            stdout="",
            stderr="pytest: not installed in PATH",
            sandboxed=False,
            runtime=None,
        )
    return SandboxResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        sandboxed=False,
        runtime=None,
    )


def _run_container(
    worktree: Path,
    args: list[str],
    timeout_s: int,
    *,
    runtime: str,
    image: str,
) -> SandboxResult:
    # Mount worktree read-only at /work, write tmpfs at /tmp and /work/.cache,
    # drop network and all capabilities, and run as a non-root uid that
    # matches the runtime stage of our Dockerfile.
    mount_path = str(worktree.resolve())
    cmd = [
        runtime,
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--user",
        "1001:1001",
        "--tmpfs",
        "/tmp:rw,size=64m",
        "--tmpfs",
        "/work-cache:rw,size=64m",
        "-v",
        f"{mount_path}:/work:ro",
        "-w",
        "/work",
        "-e",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "-e",
        "PY_COLORS=0",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        image,
        "python",
        "-m",
        "pytest",
        "-q",
        *args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            returncode=124,
            stdout="",
            stderr=f"pytest: timed out (sandbox runtime={runtime})",
            sandboxed=True,
            runtime=runtime,
        )
    except FileNotFoundError:
        # Race between detect_runtime and exec; surface the same fail-closed shape.
        raise SandboxUnavailableError(
            f"Container runtime {runtime!r} disappeared between detection and exec"
        ) from None
    return SandboxResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        sandboxed=True,
        runtime=runtime,
    )
