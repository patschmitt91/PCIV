"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from pciv.budget import BudgetGovernor
from pciv.config import PlanConfig, load_config
from pciv.state import Ledger
from pciv.telemetry import setup_tracing

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg() -> PlanConfig:
    return load_config(REPO_ROOT / "plan.yaml")


@pytest.fixture
def governor(cfg: PlanConfig) -> BudgetGovernor:
    return BudgetGovernor(ceiling_usd=10.0, cfg=cfg)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    db = tmp_path / "ledger.db"
    return Ledger(db)


@pytest.fixture
def tracer() -> object:
    return setup_tracing("pciv-test", "APPLICATIONINSIGHTS_CONNECTION_STRING_TEST_UNSET")
