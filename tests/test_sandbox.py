"""Regression tests for the pytest sandbox.

The most important guarantee this module makes is that a malicious
``conftest.py`` dropped into a worktree does *not* execute on the host
under the default ``untrusted`` mode. We assert that two ways:

1. Hardened env: ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` is always passed,
   regardless of trust mode, so even installed plugins don't load.
2. Fail-closed: when ``trust='untrusted'`` is requested but neither
   docker nor podman is on PATH, ``run_pytest`` raises
   ``SandboxUnavailableError`` rather than silently falling back to a
   host invocation that would execute the conftest.

A full container-execution test is covered in CI on the Ubuntu runner
(which has Docker available); locally we skip that path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pciv import sandbox
from pciv.sandbox import (
    SandboxUnavailableError,
    detect_runtime,
    run_pytest,
)


def _write_malicious_conftest(worktree: Path, sentinel: Path) -> None:
    """Drop a ``conftest.py`` that would create ``sentinel`` if executed."""
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "conftest.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('pwned')\n",
        encoding="utf-8",
    )
    # An empty test_x.py so pytest has something to collect.
    (worktree / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")


def test_hardened_env_passed_to_host_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusted mode still must pass PYTEST_DISABLE_PLUGIN_AUTOLOAD=1."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = run_pytest(tmp_path, trust="trusted")
    assert result.returncode == 0
    assert result.sandboxed is False
    assert captured["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert captured["env"]["PY_COLORS"] == "0"
    # -p no:cacheprovider must always be present.
    assert "no:cacheprovider" in captured["cmd"]


def test_untrusted_mode_fails_closed_without_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If neither docker nor podman is on PATH, untrusted mode must raise."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    sentinel = tmp_path / "pwned.txt"
    _write_malicious_conftest(tmp_path / "wt", sentinel)
    with pytest.raises(SandboxUnavailableError) as exc_info:
        run_pytest(tmp_path / "wt", trust="untrusted")
    assert "docker" in str(exc_info.value).lower() or "podman" in str(exc_info.value).lower()
    # The malicious conftest must not have run.
    assert not sentinel.exists()


def test_untrusted_mode_uses_container_runtime_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a runtime exists, untrusted mode must invoke it (not the host)."""
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        sandbox.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="passed", stderr="")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    result = run_pytest(tmp_path, trust="untrusted", timeout_s=42)
    assert result.sandboxed is True
    assert result.runtime == "docker"
    cmd = captured["cmd"]
    assert cmd[0] == "docker"
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "1001:1001" in cmd
    # Read-only bind mount of the worktree.
    assert any(arg.endswith(":/work:ro") for arg in cmd)
    assert captured["timeout"] == 42


def test_detect_runtime_returns_first_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sandbox.shutil, "which", lambda name: f"/x/{name}" if name == "podman" else None
    )
    assert detect_runtime() == "podman"
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    assert detect_runtime() is None


@pytest.mark.skipif(
    shutil.which("docker") is None and shutil.which("podman") is None,
    reason="container runtime required for end-to-end sandbox test",
)
def test_malicious_conftest_does_not_execute_on_host(tmp_path: Path) -> None:
    """End-to-end: a malicious conftest must not write to the host filesystem."""
    sentinel = tmp_path / "host-pwned.txt"
    worktree = tmp_path / "wt"
    _write_malicious_conftest(worktree, sentinel)

    # We don't care about pytest's exit code here; what matters is that
    # the host filesystem outside the worktree was not written to.
    try:
        run_pytest(worktree, trust="untrusted", timeout_s=60)
    except SandboxUnavailableError:
        pytest.skip("sandbox runtime unavailable at runtime")
    assert not sentinel.exists(), "malicious conftest.py executed and wrote to host"
