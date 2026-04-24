"""Secret-leak regression.

Seeds three distinct secret shapes (sk- API key, bearer token, JWT)
into env + the task prompt, runs the full pipeline, and asserts none
of them surface in captured logs, span attributes/events, span names,
or persisted ledger rows.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from typer.testing import CliRunner

from pciv.cli import app
from pciv.redaction import RedactionFilter
from pciv.state import Ledger
from pciv.telemetry import JsonFormatter

from ._gitutil import init_git_repo
from .test_cli_e2e import _install_fake_agents, _write_tiny_plan_yaml

# Legacy single-string secret, kept so the older assertions still make sense.
SECRET = "sk-secret-do-not-log-abcdefghij"

SECRETS = {
    "AZURE_OPENAI_API_KEY": "sk-secret-abcdefghijklmnopqrst",
    "OPENAI_API_KEY": "bearer abcdef0123456789deadbeef",
    "APPLICATIONINSIGHTS_TOKEN": (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    ),
}


def _install_memory_tracer(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "pciv-secret-leak"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    from pciv import telemetry as telemetry_mod
    from pciv.telemetry import tracing as tracing_mod

    def fake_setup(service_name: str, *_args: Any, **_kwargs: Any) -> Any:
        # Return a tracer bound to our local provider so we do not collide
        # with the process-global tracer set by earlier tests.
        return provider.get_tracer(service_name)

    monkeypatch.setattr(tracing_mod, "setup_tracing", fake_setup)
    monkeypatch.setattr(telemetry_mod, "setup_tracing", fake_setup)
    from pciv import cli as cli_mod

    monkeypatch.setattr(cli_mod, "setup_tracing", fake_setup)
    return exporter


def _span_contains(span: Any, needle: str) -> bool:
    for value in (span.attributes or {}).values():
        if needle in str(value):
            return True
    for event in span.events or []:
        if needle in event.name:
            return True
        for value in (event.attributes or {}).values():
            if needle in str(value):
                return True
    return needle in span.name


def test_secret_key_never_appears_in_ledger_stdout_or_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fake secret is placed in the environment. No code path should log
    # or persist it.
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/")

    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_tiny_plan_yaml(cfg_path, state_dir)

    exporter = _install_memory_tracer(monkeypatch)
    _install_fake_agents(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "add a greeting",
            "--yes",
            "--budget",
            "0.01",
            "--config",
            str(cfg_path),
            "--repo",
            str(repo),
        ],
    )
    assert result.exit_code == 0, result.output

    assert SECRET not in result.output, "secret leaked to stdout"

    db_path = state_dir / "ledger.db"
    with Ledger(db_path) as ledger:
        for table in ("runs", "tasks", "agent_invocations", "cost_events", "verdicts"):
            for row in ledger.fetch_all(table):
                blob = json.dumps(row, default=str)
                assert SECRET not in blob, f"secret leaked into ledger table {table}"

    spans = exporter.get_finished_spans()
    assert spans, "expected at least one span"
    for span in spans:
        assert not _span_contains(span, SECRET), f"secret leaked into span {span.name}"


def test_multiple_secret_shapes_never_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/")

    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_tiny_plan_yaml(cfg_path, state_dir)

    exporter = _install_memory_tracer(monkeypatch)
    _install_fake_agents(monkeypatch)

    # Capture root-logger output through the JSON formatter + redaction
    # filter that the CLI would install in production.
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    prior_level = root.level
    root.setLevel(logging.DEBUG)

    # Emit records directly referencing the secrets to prove the logging
    # pipeline itself scrubs them.
    try:
        for value in SECRETS.values():
            logging.getLogger("pciv.test").info("seeded secret: %s", value)

        task_with_secrets = " ; ".join(SECRETS.values())
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                f"echo {task_with_secrets}",
                "--yes",
                "--budget",
                "0.01",
                "--config",
                str(cfg_path),
                "--repo",
                str(repo),
            ],
        )
        assert result.exit_code == 0, result.output
    finally:
        root.removeHandler(handler)
        root.setLevel(prior_level)

    captured_logs = log_buf.getvalue()
    for value in SECRETS.values():
        assert value not in result.output, f"secret leaked to stdout: {value!r}"
        assert value not in captured_logs, f"secret leaked into logs: {value!r}"

    db_path = state_dir / "ledger.db"
    with Ledger(db_path) as ledger:
        for table in ("runs", "tasks", "agent_invocations", "cost_events", "verdicts"):
            for row in ledger.fetch_all(table):
                blob = json.dumps(row, default=str)
                for value in SECRETS.values():
                    assert value not in blob, f"secret leaked into ledger table {table}: {value!r}"

    spans = exporter.get_finished_spans()
    assert spans, "expected at least one span"
    for span in spans:
        for value in SECRETS.values():
            assert not _span_contains(span, value), (
                f"secret leaked into span {span.name}: {value!r}"
            )
