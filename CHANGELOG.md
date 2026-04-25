# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed

- `CODE_OF_CONDUCT.md`. The project's contribution surface is
  governed by `CONTRIBUTING.md` (PR checklist) and `SECURITY.md`
  (vulnerability reporting). No replacement.

### Added

- Cross-run rolling-window budget cap (ADR 0007). Two new optional
  fields under `[budget]` in `plan.yaml`:
  - `monthly_cap_usd: <float>` — opt-in. When set, every `pciv run`
    consults a SQLite-backed `agentcore.budget.PersistentBudgetLedger`
    mounted on `runtime.sqlite_path` and refuses to start if the
    rolling window's remaining allowance can't fit the projected run
    cost. Default `null` keeps existing per-run-only behaviour.
  - `window: monthly|daily` — UTC-keyed bucket. Default `monthly`
    (`YYYY-MM`); `daily` uses `YYYY-MM-DD`.
- New CLI flag `--ignore-cross-run-cap` for documented emergencies.
  Skips both preflight checks (logs WARNING) and records the actual
  spend via `force_record(reason=...)`, which writes a `forced=1` row
  to `budget_window` for audit. Per-run `--budget` still applies.
- `tests/test_cross_run_budget.py` (3 tests):
  - Two sequential `pciv run` invocations: the second is rejected at
    preflight with exit code 2 once the window is exhausted, and no
    new row is written to the ledger.
  - `--ignore-cross-run-cap` overrides a pre-seeded exhausted ledger
    and writes a `forced=1` audit row.
  - `monthly_cap_usd` omitted → no ledger opened, no
    `budget_window` table created, existing behaviour preserved.
- Diff-time secret-leak detection wired through both Implement and
  Verify, backed by the new `agentcore.scan.DiffScanner`. ADR 0006.
  - `_tool_write_file` now scans content before writing and refuses
    the write when any pattern in `agentcore.redaction.NAMED_PATTERNS`
    matches; the implement agent receives a tool error and can retry
    with redacted content.
  - `Pipeline.run` runs the scanner over each per-subtask diff after
    the verifier returns. Any finding **forces** the verdict to
    `reject` with a synthesized reason (`"diff scanner refused
    <task>: N secret pattern(s) [...]"`), regardless of whether the
    LLM verifier voted `ship`. Pre-existing rejections are preserved.
- `tests/test_secret_scanner_integration.py` covers both gates:
  pre-write rejection of sk-/JWT/clean content, custom-scanner
  injection, and the post-implement override that downgrades a
  ship-voted verdict to `reject` when a secret-bearing file is
  written outside the `_tool_write_file` path.
- `tests/test_error_paths.py` adds four error-path tests to close the
  gap where v0.1's only workflow test was the happy path:
  - Plan agent surfaces a `RuntimeError` (not a partial `Plan`) when
    every retry returns malformed JSON; ledger captures every
    invocation as `ok` or `error`.
  - Pipeline status is `merge_rejected` and no integration branch is
    created when the operator declines the merge HITL gate after a
    ship verdict.
  - `BudgetExceededError` propagates from a mid-run charge; the
    failed plan invocation is recorded with `status="error"` rather
    than swallowed.
  - `squash_integration` cleans up the `_integration` worktree
    directory and the corresponding `git worktree list` entry even
    when the second of two approved subtasks conflicts.

### Changed

- `agentcore` pin bumped from `git+...@v0.3.0` to `git+...@v0.4.0`.
  The new release ships `agentcore.budget.PersistentBudgetLedger`;
  see agentcore CHANGELOG for the full diff. (Earlier in the same
  pre-release window the pin was bumped from `v0.2.0` to `v0.3.0`
  for the diff-scanner work.)
- `pciv.config.BudgetConfig` gains `monthly_cap_usd: float | None`
  (default `None`) and `window: Literal["daily", "monthly"]`
  (default `"monthly"`). Existing configs validate unchanged.
- `pciv run` banner now prints a `cross_run_window=… cross_run_spent_usd=…
  cross_run_cap_usd=…` line when the cross-run cap is active.
- `[tool.uv.sources]` declares an editable override for sibling
  `agentcore` checkouts so local dev works before tagged releases
  are pushed. `uv sync` on a fresh clone still resolves from the
  git pin.

## [0.2.0] — 2026-04-24

### Added

- Structured logging: `JsonFormatter` in
  `src/pciv/telemetry/logging.py` emits `ts`, `level`, `logger`, `msg`,
  plus `run_id`, `trace_id`, `span_id` when an OTel span is active.
  Root CLI callback accepts `--verbose`/`--quiet` for DEBUG/WARNING,
  honors `LOG_FORMAT=json|text` (default `text` on a TTY, `json`
  otherwise), and attaches a `RedactionFilter` to every handler.
- Central redaction helper `src/pciv/redaction.py` scrubbing `sk-`
  API keys, bearer tokens, JWTs, 40+ char hex blobs, and literal
  values of secret-named env vars (`AZURE_OPENAI_API_KEY`,
  `OPENAI_API_KEY`, `APPLICATIONINSIGHTS_CONNECTION_STRING`, and any
  name containing `KEY`/`SECRET`/`TOKEN`/`PASSWORD`/`CONNECTION_STRING`).
  Available for log records and via `redact_mapping` for span
  attribute dicts and persisted blobs.
- `tests/test_secret_leak.py` extended with
  `test_multiple_secret_shapes_never_leak`: seeds three distinct
  secret shapes (sk- key, bearer token, JWT) into env + the task
  prompt and asserts zero occurrences in stdout, captured JSON logs,
  ledger rows, span attributes, span events, and span names.
- `pciv doctor` subcommand: reports Python version, `uv` version,
  git availability, OS, config file resolution, `.pciv/` state-dir
  writability, and redacted env-var presence as JSON. Exits 0 only
  when python/uv/git/state_dir_writable checks pass.
- OTel counters in `pciv.telemetry.metrics`: `runs_total`,
  `runs_failed_total`, `budget_usd_spent_total`. `tests/test_metrics.py`
  uses an `InMemoryMetricReader` to assert each name appears after
  a full `pciv run`.
- Multi-stage `Dockerfile` at the repo root: builder uses `uv sync
  --no-dev --frozen`; runtime is `python:3.12-slim`, non-root uid 1001,
  healthcheck runs `pciv doctor`. `.dockerignore` excludes `.venv`,
  `.git`, `dist`, `tests`, `docs`, and caches.
- CI `docker` job builds the image on `ubuntu-latest` and runs
  `docker run --rm <image> doctor`; nothing is pushed.

- `SECURITY.md` at the repo root: supported-versions table, private
  reporting channels (GitHub private advisories + maintainer email),
  and a 90-day coordinated-disclosure window. Linked from the README.
- Top-level `justfile` with `install`, `lint`, `fmt`, `typecheck`,
  `test`, `cov`, `build`, and `clean` recipes; all shell out to `uv`.
  README has a new `Development` section documenting the recipes.
- `src/pciv/py.typed` marker, force-included in the wheel via
  `[tool.hatch.build.targets.wheel.force-include]` so downstream type
  checkers pick up the package as typed.
- `[project]` classifiers now include `Development Status :: 4 - Beta`,
  `Operating System :: OS Independent`, and the Python 3.11 / 3.12
  rows (previously no classifiers were declared).
- `[project]` gains a `keywords` array
  (`agent-framework`, `ai`, `orchestration`, `multi-agent`,
  `azure-openai`).
- `[project.urls]` now includes `Homepage`, `Source`, `Issues`, and
  `Changelog` (previously only `Homepage`, `Repository`, `Issues`).
- `twine>=6.1.0` added to the `dev` extra.
- CI `build` job on ubuntu-latest runs `uv build` followed by
  `uv run twine check dist/*` and uploads `dist/` as an artifact.
- `release.yml` split into `build` (runs `uv build` + `twine check` +
  uploads `dist/` artifact) and `release` (downloads the artifact and
  creates the GitHub Release via `softprops/action-gh-release@v2`).
  The release job `needs: build`, so twine metadata failures block
  the GitHub Release.
- `.github/workflows/release.yml` — on tag `v*`, build the wheel with
  `uv build` and upload it as a GitHub Release asset (no PyPI publish).
- CI `type-check` job runs `uv run mypy --strict src/pciv`
  independently on ubuntu-latest and windows-latest.
- CI `pre-commit` job runs `pre-commit run --all-files` on every push
  and PR.
- CI `docs-check` job runs `lycheeverse/lychee-action@v2` against
  `README.md` and `docs/**/*.md`.
- `pytest-cov` and `pre-commit` pinned in the `dev` extra; coverage
  configured through `[tool.pytest.ini_options].addopts` with
  `--cov=src/pciv --cov-report=term-missing --cov-fail-under=85`.
- `tests/test_cli_e2e.py` exercising `pciv run --yes --budget 0.01`
  end-to-end via Typer's `CliRunner` against a real tmp git repo and
  stubbed Azure OpenAI clients; asserts the ledger holds exactly one
  run row, one task row, four `agent_invocations` rows (plan, critique,
  implement, verify), four matching `cost_events`, and one ship
  verdict for a 1-subtask, 0-iterate-round pipeline.
- `tests/test_secret_leak.py` feeds a fake
  `AZURE_OPENAI_API_KEY=sk-secret-do-not-log` into the environment,
  runs the full pipeline with stubbed agents, and captures spans with
  an `InMemorySpanExporter`. Asserts the secret string appears in zero
  ledger rows, zero stdout lines, and zero span attributes or events.
- `tests/test_readme_examples.py` parses README code blocks and
  asserts every recognized shell command parses via `shlex` and its
  executable resolves on PATH (or is the project CLI).
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `.pre-commit-config.yaml` wiring ruff, ruff-format, mypy (via
  `uv run mypy`), and the standard `pre-commit-hooks` whitespace /
  merge-conflict / toml / yaml checks.
- `LICENSE` (MIT). Previously declared in `pyproject.toml` but the
  file was missing from the repository root.
- `docs/configuration.md` — every key in `plan.yaml` documented.
- `docs/roadmap.md` — dated v0.1 / v0.2 / v0.3 milestones.
- `docs/decisions/0002-sqlite-for-ledger.md` and
  `docs/decisions/0003-pydantic-for-structured-agent-io.md`.

### Changed

- Applied `ruff format` across `src/` and `tests/` so the repo
  satisfies `ruff format --check` cleanly.
- Rewrote `README.md` to the 13-section skeleton plus PCIV-specific
  sections: a Mermaid sequence of the run, an "Artifacts and
  inspection" section with 5 example SQL queries against
  `.pciv/ledger.db`, a "Running non-interactively" section
  (`--yes`, exit codes, webhook approver as v0.2), and an "Azure
  OpenAI setup" section with a copy-pasteable
  `az cognitiveservices account deployment create` command.

## [0.1.0] — 2026-04-24

### Added

- Plan / Critique / Implement / Verify async pipeline.
- SQLite ledger of runs, agent invocations, cost events, and verdicts.
- Per-subtask git worktrees and branches; integration branch squash-merge
  on a successful ship.
- Budget governor with preflight cost projection and hard USD ceiling.
- HITL gates for plan approval and ship approval (`--yes` auto-approves).
- OpenTelemetry span emission with optional Azure Monitor export.
- `pciv` CLI (`run`, etc.).
- ADR-0001 and companion composition doc specifying the migration of the
  orchestration spine to `agent-framework` graph workflow primitives.

### Changed

- Default model identifiers in `plan.yaml` are now role-based
  placeholders (`azure-reasoning`, `azure-codegen`) and must be
  overridden with real Azure OpenAI deployment names.

[Unreleased]: https://github.com/patschmitt91/PCIV/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/patschmitt91/PCIV/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/patschmitt91/PCIV/releases/tag/v0.1.0
