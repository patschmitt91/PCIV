-- pciv ledger schema. Idempotent.

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    task         TEXT NOT NULL,
    budget_usd   REAL NOT NULL,
    max_iter     INTEGER NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    status       TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS tasks (
    run_id       TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    description  TEXT NOT NULL,
    dependencies TEXT NOT NULL,
    files        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS iterations (
    run_id       TEXT NOT NULL,
    iteration    INTEGER NOT NULL,
    phase        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    outcome      TEXT,
    PRIMARY KEY (run_id, iteration, phase)
);

CREATE TABLE IF NOT EXISTS agent_invocations (
    invocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    iteration     INTEGER NOT NULL,
    phase         TEXT NOT NULL,
    agent_id      TEXT NOT NULL,
    model         TEXT NOT NULL,
    task_id       TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    error         TEXT
);

CREATE TABLE IF NOT EXISTS cost_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    invocation_id INTEGER,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL NOT NULL,
    recorded_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verdicts (
    run_id       TEXT NOT NULL,
    iteration    INTEGER NOT NULL,
    verdict      TEXT NOT NULL,
    reasons      TEXT NOT NULL,
    per_subtask  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, iteration)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    task_id      TEXT,
    kind         TEXT NOT NULL,
    path         TEXT NOT NULL,
    sha256       TEXT,
    recorded_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_invocations_run ON agent_invocations(run_id);
CREATE INDEX IF NOT EXISTS idx_cost_events_run ON cost_events(run_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_run ON verdicts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
