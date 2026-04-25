"""Integration test for the cross-run rolling-window budget cap (PCIV-2 / ADR 0007).

Two sequential `pciv run` invocations share the SQLite-backed
``PersistentBudgetLedger`` mounted on ``runtime.sqlite_path``. The first
run completes within the cap; the second is fail-fast at preflight
because the recorded spend from run 1 left no headroom.

A third invocation with ``--ignore-cross-run-cap`` proves the emergency
override succeeds and writes a ``forced=1`` audit row.

The fake-agent harness (4 invocations per run, 10 prompt + 20 completion
tokens each, $0.01/MTok pricing) yields ~$1.2e-6 actual spend per run.
``monthly_cap_usd=1.5e-6`` guarantees run 1 fits and run 2 does not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pciv.cli import app

from ._gitutil import init_git_repo

# Reuse the fake-agent harness from the existing e2e test. Importing the
# private helpers is intentional: they encode the canonical ship-path
# fixture (1 subtask, 0 iterate rounds) and copying them would invite drift.
from .test_cli_e2e import _install_fake_agents, _write_tiny_plan_yaml

# Per-run actual spend with the fake-agent token counts (10 in + 20 out
# per call x 5 calls: plan, critique, implement-tool-call,
# implement-completion, verify) and the pricing override below
# ($1.0/MTok). Each call costs $3e-5; per-run total is $1.5e-4. Picking
# pricing larger than the e2e fixture gives us headroom between the
# per-run cap and the cross-run cap so the per-run governor doesn't trip
# first.
_PER_RUN_SPEND_USD = 1.5e-4
# Cross-run cap chosen so run 1 fits (projected $8e-5 < cap $1.6e-4) and
# run 2's projection ($8e-5) does not fit in the $1e-5 remaining after
# run 1's actual spend.
_MONTHLY_CAP_USD = 1.6e-4
# Per-run budget: comfortably above the per-run actual spend so the
# per-run governor never trips. Cross-run preflight uses the projected
# cost (driven by the plan.yaml projection block) instead of --budget.
_PER_RUN_BUDGET_USD = 0.01


def _write_plan_with_cross_run_cap(
    path: Path, state_dir: Path, monthly_cap_usd: float
) -> None:
    """Write the e2e fixture plan.yaml with a cross-run cap and bumped
    pricing so cross-run / per-run cap interactions are measurable."""

    _write_tiny_plan_yaml(path, state_dir)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["budget"]["monthly_cap_usd"] = monthly_cap_usd
    raw["budget"]["window"] = "monthly"
    # Bump pricing from $0.01/MTok (e2e fixture) to $1.0/MTok so 30
    # tokens x 4 calls = $1.2e-4 per run lives in a numeric range we can
    # reason about without floating-point noise.
    raw["pricing"]["azure-reasoning"] = {
        "input_per_mtok": 1.0,
        "output_per_mtok": 1.0,
    }
    raw["pricing"]["azure-codegen"] = {
        "input_per_mtok": 1.0,
        "output_per_mtok": 1.0,
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _read_budget_window_rows(db_path: Path) -> list[tuple[float, int, str | None]]:
    """Return ``(amount_usd, forced, note)`` for every row in budget_window."""
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            (float(r[0]), int(r[1]), r[2])
            for r in conn.execute(
                "SELECT amount_usd, forced, note FROM budget_window "
                "ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_cross_run_cap_rejects_second_invocation_when_window_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_plan_with_cross_run_cap(cfg_path, state_dir, _MONTHLY_CAP_USD)

    runner = CliRunner()

    # --- Invocation 1: should succeed and record actual spend. ---
    _install_fake_agents(monkeypatch)
    result1 = runner.invoke(
        app,
        [
            "run",
            "add a greeting",
            "--yes",
            "--budget",
            f"{_PER_RUN_BUDGET_USD:.10f}",
            "--config",
            str(cfg_path),
            "--repo",
            str(repo),
        ],
    )
    assert result1.exit_code == 0, result1.output
    assert "status=merged" in result1.output
    assert "cross_run_window=" in result1.output

    db_path = state_dir / "ledger.db"
    rows_after_run_1 = _read_budget_window_rows(db_path)
    assert len(rows_after_run_1) == 1
    amount_1, forced_1, note_1 = rows_after_run_1[0]
    assert amount_1 == pytest.approx(_PER_RUN_SPEND_USD, rel=0.05)
    assert forced_1 == 0
    assert note_1 is not None and note_1.startswith("run_id=")

    # --- Invocation 2: should fail-fast at preflight. ---
    # Re-init the FakeClient queues for the second run's agents (the
    # patched clients consumed all responses on run 1).
    _install_fake_agents(monkeypatch)
    result2 = runner.invoke(
        app,
        [
            "run",
            "add another greeting",
            "--yes",
            "--budget",
            f"{_PER_RUN_BUDGET_USD:.10f}",
            "--config",
            str(cfg_path),
            "--repo",
            str(repo),
        ],
    )
    # Exit code 2 = budget rejection (matches existing BudgetExceededError
    # mapping in cli.run_cmd).
    assert result2.exit_code == 2, result2.output
    # The error must mention the cross-run cap so an operator can tell
    # this from a per-run preflight failure.
    combined = (result2.output or "") + (result2.stderr or "")
    assert "cross-run" in combined.lower() or "window" in combined.lower(), combined
    # And run 2 must not have written a new row to the ledger.
    rows_after_run_2 = _read_budget_window_rows(db_path)
    assert rows_after_run_2 == rows_after_run_1


def test_ignore_cross_run_cap_overrides_rejection_and_marks_row_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    _write_plan_with_cross_run_cap(cfg_path, state_dir, _MONTHLY_CAP_USD)

    # Pre-seed the ledger to simulate a prior run that exhausted the cap.
    # Using PersistentBudgetLedger directly here is faster and more
    # deterministic than running the full pipeline twice; the
    # cross-run-rejection test above already exercises the realistic
    # CLI-twice path.
    from agentcore.budget import PersistentBudgetLedger

    db_path = state_dir / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with PersistentBudgetLedger(
        db_path, cap_usd=_MONTHLY_CAP_USD, window="monthly"
    ) as seed:
        seed.charge(_MONTHLY_CAP_USD, note="prior-run-seed")

    _install_fake_agents(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "emergency hotfix",
            "--yes",
            "--budget",
            f"{_PER_RUN_BUDGET_USD:.10f}",
            "--config",
            str(cfg_path),
            "--repo",
            str(repo),
            "--ignore-cross-run-cap",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "status=merged" in result.output

    rows = _read_budget_window_rows(db_path)
    # Exactly two rows: the seed charge and the forced override row.
    assert len(rows) == 2
    seed_row, forced_row = rows
    assert seed_row[1] == 0  # not forced
    assert forced_row[1] == 1  # forced=1
    assert forced_row[2] is not None and "ignore-cross-run-cap" in forced_row[2]


def test_cross_run_cap_disabled_when_monthly_cap_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``monthly_cap_usd`` omitted → no PersistentBudgetLedger is opened
    and no ``cross_run_window=`` line appears in the run banner."""

    repo = tmp_path / "repo"
    init_git_repo(repo)
    state_dir = tmp_path / "state"
    cfg_path = tmp_path / "plan.yaml"
    # Default fixture omits monthly_cap_usd entirely.
    _write_tiny_plan_yaml(cfg_path, state_dir)

    _install_fake_agents(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "no cross-run cap",
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
    assert "cross_run_window=" not in result.output
    # And no budget_window table entries should exist (the table itself
    # is not created if the ledger never opens).
    db_path = state_dir / "ledger.db"
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='budget_window'"
        )
        assert cur.fetchone() is None
    finally:
        conn.close()
