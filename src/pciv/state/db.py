"""SQLite wrapper with idempotent schema initialization."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Ledger:
    def __init__(self, sqlite_path: str | Path) -> None:
        self._path = Path(sqlite_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock, self._conn:
            self._conn.executescript(sql)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_run(self, run_id: str, task: str, budget_usd: float, max_iter: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, task, budget_usd, max_iter, started_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'running')",
                (run_id, task, budget_usd, max_iter, _utcnow()),
            )

    def finalize_run(self, run_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET ended_at = ?, status = ? WHERE run_id = ?",
                (_utcnow(), status, run_id),
            )

    def start_invocation(
        self,
        run_id: str,
        iteration: int,
        phase: str,
        agent_id: str,
        model: str,
        task_id: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO agent_invocations "
                "(run_id, iteration, phase, agent_id, model, task_id, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, iteration, phase, agent_id, model, task_id, _utcnow()),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError("failed to insert agent_invocation")
            return int(row_id)

    def finish_invocation(
        self,
        invocation_id: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE agent_invocations SET "
                "input_tokens = ?, output_tokens = ?, cost_usd = ?, "
                "ended_at = ?, status = ?, error = ? "
                "WHERE invocation_id = ?",
                (
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    _utcnow(),
                    status,
                    error,
                    invocation_id,
                ),
            )

    def record_cost(
        self,
        run_id: str,
        invocation_id: int | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cost_events "
                "(run_id, invocation_id, model, input_tokens, output_tokens, cost_usd, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, invocation_id, model, input_tokens, output_tokens, cost_usd, _utcnow()),
            )

    def record_tasks(
        self,
        run_id: str,
        subtasks: list[dict[str, Any]],
    ) -> None:
        """Persist plan subtasks. Caller supplies dicts with id, description, dependencies, files."""
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO tasks "
                "(run_id, task_id, description, dependencies, files, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                [
                    (
                        run_id,
                        s["id"],
                        s["description"],
                        json.dumps(s.get("dependencies", [])),
                        json.dumps(s.get("files", [])),
                    )
                    for s in subtasks
                ],
            )

    def record_verdict(
        self,
        run_id: str,
        iteration: int,
        verdict: str,
        reasons: list[str],
        per_subtask: Mapping[str, str],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO verdicts "
                "(run_id, iteration, verdict, reasons, per_subtask, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    iteration,
                    verdict,
                    json.dumps(reasons),
                    json.dumps(dict(per_subtask)),
                    _utcnow(),
                ),
            )

    def fetch_all(self, table: str) -> list[dict[str, Any]]:
        allowed = {
            "runs",
            "tasks",
            "iterations",
            "agent_invocations",
            "cost_events",
            "verdicts",
            "artifacts",
        }
        if table not in allowed:
            raise ValueError(f"unknown table: {table}")
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(r) for r in rows]
