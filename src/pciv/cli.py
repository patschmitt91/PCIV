"""Typer CLI entry point for pciv."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import typer

from .budget import BudgetExceededError, BudgetGovernor
from .config import load_config
from .state import Ledger
from .telemetry import setup_tracing
from .workflow import Pipeline, cleanup_worktrees

_VALID_GATE_DECISIONS = {"approve", "revise", "reject", "abort"}
_SUCCESS_STATUSES = {"merged", "ship"}

app = typer.Typer(add_completion=False, help="pciv: Plan-Critique-Implement-Verify CLI")


@app.command("run")
def run_cmd(
    task: str = typer.Argument(..., help="Free-form task description."),
    budget: float = typer.Option(2.00, "--budget", help="Hard USD ceiling for the run."),
    max_iter: int = typer.Option(2, "--max-iter", help="Max verify iterations."),
    config: str = typer.Option("plan.yaml", "--config", help="Pipeline config path."),
    repo: str = typer.Option(".", "--repo", help="Repository snapshot path."),
    yes: bool = typer.Option(False, "--yes", help="Auto-approve all HITL gates."),
    cleanup: bool = typer.Option(
        False, "--cleanup", help="Remove per-subtask worktrees and branches at run end."
    ),
) -> None:
    """Execute the pciv workflow end-to-end."""
    try:
        asyncio.run(_run(task, budget, max_iter, config, repo, yes, cleanup))
    except BudgetExceededError as e:
        typer.echo(f"budget: {e}", err=True)
        raise typer.Exit(code=2) from None
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=3) from None


def _make_gate(auto_approve: bool) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
    async def gate(name: str, payload: dict[str, Any]) -> str:
        typer.echo(f"\n=== HITL gate: {name} ===")
        typer.echo(json.dumps(payload, indent=2)[:4000])
        if auto_approve:
            typer.echo("--yes set, auto-approving")
            return "approve"
        raw = str(typer.prompt(f"{name} gate decision", default="approve")).strip().lower()
        if raw not in _VALID_GATE_DECISIONS:
            typer.echo(
                f"unrecognized decision {raw!r}; treating as 'reject'. "
                f"valid: {sorted(_VALID_GATE_DECISIONS)}",
                err=True,
            )
            return "reject"
        return raw

    return gate


async def _run(
    task: str,
    budget: float,
    max_iter: int,
    config: str,
    repo: str,
    auto_approve: bool,
    cleanup: bool,
) -> None:
    cfg = load_config(config)
    Path(cfg.runtime.state_dir).mkdir(parents=True, exist_ok=True)

    tracer = setup_tracing(
        service_name=cfg.telemetry.service_name,
        conn_string_env=cfg.telemetry.app_insights_connection_string_env,
    )

    run_id = str(uuid.uuid4())
    governor = BudgetGovernor(ceiling_usd=budget, cfg=cfg)
    projected = governor.preflight()
    typer.echo(f"run_id={run_id} projected_usd={projected:.4f} ceiling_usd={budget:.4f}")

    with Ledger(cfg.runtime.sqlite_path) as ledger:
        ledger.record_run(run_id, task, budget, max_iter)
        pipeline = Pipeline(
            cfg=cfg,
            governor=governor,
            ledger=ledger,
            run_id=run_id,
            tracer=tracer,
            repo=Path(repo),
            gate_cb=_make_gate(auto_approve),
        )
        outcome = await pipeline.run(task=task, max_iter=max_iter)
        ledger.finalize_run(run_id, status=outcome.status)

        typer.echo(f"\nstatus={outcome.status}")
        typer.echo(f"message={outcome.message}")
        typer.echo(f"iterations_used={outcome.iterations_used}")
        typer.echo(f"spent_usd={governor.spent_usd:.4f}")
        if outcome.verdict is not None:
            typer.echo(f"verdict={outcome.verdict.verdict}")
            typer.echo(f"per_subtask={outcome.verdict.per_subtask}")
        if outcome.merge is not None:
            typer.echo(f"integration_branch={outcome.merge.integration_branch}")
            typer.echo(f"merged_tasks={outcome.merge.merged_tasks}")
            typer.echo(f"skipped_tasks={outcome.merge.skipped_tasks}")
        if cleanup and outcome.worktrees:
            cleanup_worktrees(Path(repo), outcome.worktrees)
            typer.echo(f"cleaned up {len(outcome.worktrees)} worktrees")
        if outcome.status not in _SUCCESS_STATUSES:
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
