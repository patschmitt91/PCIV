"""Typer CLI entry point for pciv."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import typer

from .budget import BudgetExceededError, BudgetGovernor
from .config import load_config
from .state import Ledger
from .telemetry import (
    configure_logging,
    runs_failed_total,
    runs_total,
    setup_tracing,
)
from .workflow import Pipeline, cleanup_worktrees

_VALID_GATE_DECISIONS = {"approve", "revise", "reject", "abort"}
_SUCCESS_STATUSES = {"merged", "ship"}

app = typer.Typer(add_completion=False, help="pciv: Plan-Critique-Implement-Verify CLI")


_VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="DEBUG-level logs.")
_QUIET_OPT = typer.Option(False, "--quiet", "-q", help="Only WARNING+ logs.")


@app.callback()
def _root(
    verbose: bool = _VERBOSE_OPT,
    quiet: bool = _QUIET_OPT,
) -> None:
    """Configure root logger based on verbosity flags and ``LOG_FORMAT`` env."""

    if verbose and quiet:
        typer.echo("--verbose and --quiet are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    configure_logging(level=level)


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
    runs_total().add(1)
    try:
        asyncio.run(_run(task, budget, max_iter, config, repo, yes, cleanup))
    except BudgetExceededError as e:
        runs_failed_total().add(1)
        typer.echo(f"budget: {e}", err=True)
        raise typer.Exit(code=2) from None
    except FileNotFoundError as e:
        runs_failed_total().add(1)
        typer.echo(str(e), err=True)
        raise typer.Exit(code=3) from None
    except typer.Exit as e:
        if e.exit_code not in (0, None):
            runs_failed_total().add(1)
        raise


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


def _check(label: str, ok: bool, detail: str) -> dict[str, object]:
    return {"check": label, "ok": ok, "detail": detail}


def _tool_version(executable: str, *args: str) -> str | None:
    path = shutil.which(executable)
    if path is None:
        return None
    try:
        proc = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0] if out else executable


def _config_path_arg(config: str) -> Path:
    return Path(config)


_DOCTOR_CONFIG_OPT = typer.Option(
    "plan.yaml", "--config", help="Path to the pipeline config to probe."
)


@app.command("doctor")
def doctor_cmd(config: str = _DOCTOR_CONFIG_OPT) -> None:
    """Print environment diagnostics. Exit 0 if all hard checks pass."""

    from .redaction import REDACTED

    results: list[dict[str, object]] = []

    py = sys.version.split()[0]
    results.append(_check("python", sys.version_info >= (3, 11), f"python {py}"))

    uv_ver = _tool_version("uv", "--version")
    results.append(_check("uv", uv_ver is not None, uv_ver or "not found"))

    git_ver = _tool_version("git", "--version")
    results.append(_check("git", git_ver is not None, git_ver or "not found"))

    results.append(_check("os", True, f"{platform.system()} {platform.release()}"))

    cfg_path = Path(config)
    cfg_ok = cfg_path.is_file()
    results.append(_check("config", cfg_ok, f"config at {cfg_path}"))

    # .pciv/ writability. Prefer the config's state_dir if the config loads;
    # otherwise default to literal ``.pciv``.
    state_dir: Path
    try:
        cfg = load_config(config)
        state_dir = Path(cfg.runtime.state_dir)
    except Exception:
        state_dir = Path(".pciv")
    writable = False
    detail = f"state_dir={state_dir}"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError as exc:
        detail = f"state_dir={state_dir} error={exc}"
    results.append(_check("state_dir_writable", writable, detail))

    env_names = (
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "OPENAI_API_KEY",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
    )
    env_report: dict[str, str] = {}
    for name in env_names:
        val = os.environ.get(name)
        if not val:
            env_report[name] = "unset"
        elif name.endswith("_ENDPOINT"):
            env_report[name] = "set"
        else:
            env_report[name] = REDACTED
    results.append(_check("env", True, json.dumps(env_report)))

    hard = {"python", "uv", "git", "state_dir_writable"}
    all_ok = all(r["ok"] for r in results if r["check"] in hard)

    payload = {"ok": all_ok, "checks": results}
    typer.echo(json.dumps(payload, indent=2))
    raise typer.Exit(code=0 if all_ok else 1)


if __name__ == "__main__":
    app()
