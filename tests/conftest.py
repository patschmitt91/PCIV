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
    inst = Ledger(db)
    # Pre-seed parent run rows so child-table inserts (agent_invocations,
    # cost_events, verdicts) satisfy the Phase-3 ON DELETE CASCADE FKs.
    # Tests instantiate agents with arbitrary literal run_ids; this saves
    # them from having to call record_run themselves.
    for rid in ("run-1", "run-2", "runX"):
        inst.record_run(rid, task="test", budget_usd=1.0, max_iter=1)
    return inst


@pytest.fixture
def tracer() -> object:
    return setup_tracing("pciv-test", "APPLICATIONINSIGHTS_CONNECTION_STRING_TEST_UNSET")
