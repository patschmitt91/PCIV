# pciv

Plan-Critique-Implement-Verify orchestration harness for complex coding tasks.

All phases run through Azure OpenAI. There is no Anthropic dependency.

## Phases

1. **Plan** — `gpt-5.4` emits a structured JSON plan.
2. **Critique** — `gpt-5.4` validates the plan. Must pass before phase 3.
3. **Implement** — Parallel `gpt-5.3-codex` workers, one per independent
   subtask, each in its own git worktree. Tool loop: `read_file`,
   `write_file`, `list_dir`, `run_pytest`. Shared SQLite ledger.
4. **Verify** — `gpt-5.4` reviews diffs plus per-worktree pytest output
   and returns a verdict in `{ship, iterate, reject}`. On `iterate`, loops
   back to phase 3 with feedback, re-queueing only the subtasks marked
   `iterate`. Default iteration cap is 2.

On a `ship` verdict the operator is prompted; on approval the run
squash-merges each approved subtask branch onto a fresh
`pciv/<run_id>/integration` branch.

`microsoft/agent-framework` is a declared dependency; the current
orchestration spine is a plain async `Pipeline` class that calls the
four agents directly and uses an `asyncio.Semaphore` to cap concurrent
implementer subagents. This keeps HITL gates as straightforward CLI
prompts.

## Install

```
uv sync --extra dev
```

## Usage

```
pciv run "refactor the auth module to use JWT" \
    --budget 2.00 --max-iter 2 --config plan.yaml
```

Flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--budget` | `2.00` | Hard USD ceiling. Preflight projection aborts before any network call if it exceeds this. |
| `--max-iter` | `2` | Maximum verify-iterate rounds after the first implementation pass. |
| `--config` | `plan.yaml` | Pipeline config path. |
| `--repo` | `.` | Path to the git repository to operate on. |
| `--yes` | `false` | Auto-approve both HITL gates (plan, merge). |
| `--cleanup` | `false` | Remove per-subtask worktrees and branches at run end. |

### Environment variables

| Variable | Purpose |
|----------|---------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_API_VERSION` | Optional, default `2024-10-21` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry export, optional. Falls back to console exporter. |

## Artifacts

- `.pciv/ledger.db` — SQLite ledger of runs, invocations, cost events, verdicts.
- `.pciv/worktrees/<run_id>/<task_id>` — per-subtask git worktrees.
- `pciv/<run_id>/<task_id>` — per-subtask git branches.
- `pciv/<run_id>/integration` — squash-merge target branch on a successful run.

## Development

```
uv sync --extra dev
uv run ruff check src/ tests/
uv run mypy --strict src/pciv
uv run pytest tests/ -v
```
