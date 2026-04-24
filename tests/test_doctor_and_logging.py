"""Tests for `pciv doctor` and CLI verbose/quiet toggles."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pciv import telemetry as telemetry_mod
from pciv.cli import app
from pciv.redaction import REDACTED

from .test_cli_e2e import _write_tiny_plan_yaml


def _mk_config(tmp_path: Path) -> Path:
    state_dir = tmp_path / "state"
    cfg = tmp_path / "plan.yaml"
    _write_tiny_plan_yaml(cfg, state_dir)
    return cfg


def test_doctor_all_green_exits_zero(tmp_path: Path) -> None:
    cfg = _mk_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    labels = {c["check"] for c in payload["checks"]}
    assert {
        "python",
        "git",
        "config",
        "state_dir_writable",
        "env",
        "os",
    }.issubset(labels)


def test_doctor_redacts_env_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-visible-should-not-appear-1234")
    cfg = _mk_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "sk-visible-should-not-appear-1234" not in result.output
    assert REDACTED in result.output


def test_doctor_missing_config_still_probes_default_state_dir(tmp_path: Path) -> None:
    # Missing config path is a soft failure; state_dir_writable check
    # still runs against the ``.pciv`` default under the CWD.
    monkey_cwd = tmp_path
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--config", str(monkey_cwd / "missing.yaml")])
    # Exit code depends on whether CWD is writable, but the payload must
    # parse and include the config check flipped to False.
    payload = json.loads(result.output)
    config_check = next(c for c in payload["checks"] if c["check"] == "config")
    assert config_check["ok"] is False


def test_verbose_and_quiet_are_mutually_exclusive(tmp_path: Path) -> None:
    cfg = _mk_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["--verbose", "--quiet", "doctor", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_verbose_sets_debug_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pciv.telemetry import logging as log_mod

    monkeypatch.setattr(log_mod, "_logging_configured", False)
    cfg = _mk_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["--verbose", "doctor", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert logging.getLogger().level == logging.DEBUG


def test_quiet_sets_warning_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pciv.telemetry import logging as log_mod

    monkeypatch.setattr(log_mod, "_logging_configured", False)
    cfg = _mk_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["--quiet", "doctor", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert logging.getLogger().level == logging.WARNING


def test_log_format_env_selects_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    from pciv.telemetry import logging as log_mod

    monkeypatch.setattr(log_mod, "_logging_configured", False)
    telemetry_mod.configure_logging(level=logging.INFO, force=True)
    root = logging.getLogger()
    assert any(isinstance(h.formatter, telemetry_mod.JsonFormatter) for h in root.handlers)
