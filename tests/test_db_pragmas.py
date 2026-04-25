"""Phase 3A regressions: schema PRAGMAs + ON DELETE CASCADE."""

from __future__ import annotations

from pathlib import Path

import pytest

from pciv.state import Ledger


def _pragma(ledger: Ledger, name: str) -> object:
    # Reach into the connection deliberately; PRAGMAs aren't part of the
    # public API but they are the contract under test.
    return ledger._conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_pragmas_set_on_connection(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "p.db")
    assert str(_pragma(led, "journal_mode")).lower() == "wal"
    assert int(_pragma(led, "foreign_keys")) == 1
    assert int(_pragma(led, "busy_timeout")) == 5000
    assert int(_pragma(led, "user_version")) == 2


def test_cascade_delete_removes_child_rows(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "c.db")
    led.record_run("rC", task="t", budget_usd=1.0, max_iter=1)
    inv = led.start_invocation("rC", 1, "plan", "planner", "gpt-4o")
    led.finish_invocation(inv, 10, 20, 0.01)
    led.record_verdict("rC", 1, "ship", ["ok"], {})

    assert len(led.fetch_all("agent_invocations")) == 1
    assert len(led.fetch_all("verdicts")) == 1

    with led._conn:
        led._conn.execute("DELETE FROM runs WHERE run_id = ?", ("rC",))

    assert led.fetch_all("agent_invocations") == []
    assert led.fetch_all("verdicts") == []


def test_fetch_all_rejects_unknown_table(tmp_path: Path) -> None:
    led = Ledger(tmp_path / "f.db")
    with pytest.raises(ValueError, match="unknown table"):
        led.fetch_all("evil; DROP TABLE runs;--")
