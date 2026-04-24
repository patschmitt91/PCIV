"""OTel counter names appear in an in-memory MetricReader."""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from typer.testing import CliRunner

from pciv import telemetry as telemetry_mod
from pciv.cli import app

from ._gitutil import init_git_repo
from .test_cli_e2e import _install_fake_agents, _write_tiny_plan_yaml


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    telemetry_mod.set_meter_provider_for_tests(provider)
    yield reader
    telemetry_mod.set_meter_provider_for_tests(None)


def _counter_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    names: set[str] = set()
    if data is None:
        return names
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                names.add(m.name)
    return names


def test_counters_emitted_on_successful_run(
    metric_reader: InMemoryMetricReader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_tiny_plan_yaml(cfg_path, state_dir)

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
    names = _counter_names(metric_reader)
    assert "runs_total" in names
    assert "budget_usd_spent_total" in names
