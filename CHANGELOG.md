# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/patschmitt91/PCIV/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/patschmitt91/PCIV/releases/tag/v0.1.0
