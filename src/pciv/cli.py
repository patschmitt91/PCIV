"""Typer CLI entry point for pciv."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import typer
from agentcore.budget import BudgetExceeded as _CoreBudgetExceeded
from agentcore.budget import PersistentBudgetLedger

from .budget import BudgetExceededError, BudgetGovernor
from .config import load_config
from .state import Ledger
from .telemetry import (
    configure_logging,
    cost_usd_per_run,
    latency_seconds_per_run,
    runs_failed_total,
    runs_total,
    setup_tracing,
    tokens_per_run,
)
from .workflow import Pipeline, cleanup_worktrees

_VALID_GATE_DECISIONS = {"approve", "revise", "reject", "abort"}
_SUCCESS_STATUSES = {"merged", "ship"}

app = typer.Typer(add_completion=False, help="pciv: Plan-Critique-Implement-Verify CLI")


_VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="DEBUG-level logs.")
_QUIET_OPT = typer.Option(False, "--quiet", "-q", help="Only WARNING+ logs.")


def _version_callback(value: bool) -> None:
    if value:
        from pciv import __version__

        typer.echo(__version__)
        raise typer.Exit()


_VERSION_OPT = typer.Option(
    False,
    "--version",
    help="Print the pciv version and exit.",
    callback=_version_callback,
    is_eager=True,
)


@app.callback()
def _root(
    verbose: bool = _VERBOSE_OPT,
    quiet: bool = _QUIET_OPT,
    version: bool = _VERSION_OPT,
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
    ignore_cross_run_cap: bool = typer.Option(
        False,
        "--ignore-cross-run-cap",
        help=(
            "Bypass the cross-run rolling-window cap from `[budget].monthly_cap_usd`. "
            "Logs WARNING and records the spend with `forced=1` in the persistent "
            "ledger for audit. Per-run `--budget` still applies. Use only for "
            "documented emergencies."
        ),
    ),
) -> None:
    """Execute the pciv workflow end-to-end."""
    runs_total().add(1)
    try:
        asyncio.run(_run(task, budget, max_iter, config, repo, yes, cleanup, ignore_cross_run_cap))
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


def _make_gate(
    auto_approve: bool, *, run_id: str = "", state_dir: str = ""
) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
    async def gate(name: str, payload: dict[str, Any]) -> str:
        typer.echo(f"\n=== HITL gate: {name} ===")
        full = json.dumps(payload, indent=2)
        if len(full) > 4000:
            # Spool the full payload so the operator can review what was
            # truncated. Without this marker the prompt silently hides the
            # tail of large plans/critiques. See harden/phase-2 PCIV item #8.
            spooled_to = ""
            if state_dir and run_id:
                spool_dir = Path(state_dir) / "hitl"
                with contextlib.suppress(OSError):
                    spool_dir.mkdir(parents=True, exist_ok=True)
                    spool_path = spool_dir / f"{run_id}-{name}.json"
                    spool_path.write_text(full, encoding="utf-8")
                    spooled_to = str(spool_path)
            typer.echo(full[:4000])
            tail_msg = (
                f"\n... [truncated {len(full) - 4000} chars; full payload at {spooled_to}]"
                if spooled_to
                else f"\n... [truncated {len(full) - 4000} chars; spool dir unavailable]"
            )
            typer.echo(tail_msg)
        else:
            typer.echo(full)
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
    ignore_cross_run_cap: bool = False,
) -> None:
    cfg = load_config(config)
    Path(cfg.runtime.state_dir).mkdir(parents=True, exist_ok=True)

    tracer = setup_tracing(
        service_name=cfg.telemetry.service_name,
        conn_string_env=cfg.telemetry.app_insights_connection_string_env,
    )

    # Cross-run rolling-window cap (ADR 0007). Mounts the `budget_window`
    # table on the same SQLite file the run ledger uses so a single
    # `pciv state` directory captures both per-run and cross-run spend.
    # ``monthly_cap_usd is None`` disables the cross-run check entirely
    # while leaving the per-run governor in place.
    cross_run_ledger: PersistentBudgetLedger | None = None
    if cfg.budget.monthly_cap_usd is not None:
        cross_run_ledger = PersistentBudgetLedger(
            cfg.runtime.sqlite_path,
            cap_usd=cfg.budget.monthly_cap_usd,
            window=cfg.budget.window,
        )
        remaining = cross_run_ledger.remaining_in_current_window()
        spent = cross_run_ledger.spent_in_current_window()
        if remaining <= 0:
            msg = (
                f"cross-run {cfg.budget.window} cap exhausted: spent "
                f"${spent:.4f} / cap ${cfg.budget.monthly_cap_usd:.4f} "
                f"(window={cross_run_ledger.window_key})"
            )
            if not ignore_cross_run_cap:
                cross_run_ledger.close()
                raise BudgetExceededError(msg)
            logging.getLogger(__name__).warning(
                "ignoring cross-run cap (--ignore-cross-run-cap): %s", msg
            )

    run_id = str(uuid.uuid4())
    governor = BudgetGovernor(ceiling_usd=budget, cfg=cfg)
    projected = governor.preflight()
    # Cross-run preflight: compare *projected* cost (not the per-run
    # --budget upper bound) against the remaining window allowance. The
    # per-run --budget is the operator's authorization for one run, not a
    # guarantee they'll spend that much; rejecting just because --budget
    # exceeds remaining would block normal usage near the end of a window.
    if cross_run_ledger is not None and not ignore_cross_run_cap:
        remaining = cross_run_ledger.remaining_in_current_window()
        if projected > remaining:
            cross_run_ledger.close()
            raise BudgetExceededError(
                f"projected cost ${projected:.6f} exceeds cross-run remaining "
                f"${remaining:.6f} (window={cross_run_ledger.window_key}, "
                f"cap=${cfg.budget.monthly_cap_usd:.4f}). "
                f"Lower the run's footprint or pass --ignore-cross-run-cap."
            )

    typer.echo(f"run_id={run_id} projected_usd={projected:.4f} ceiling_usd={budget:.4f}")
    if cross_run_ledger is not None:
        typer.echo(
            f"cross_run_window={cross_run_ledger.window_key} "
            f"cross_run_spent_usd={cross_run_ledger.spent_in_current_window():.6f} "
            f"cross_run_cap_usd={cfg.budget.monthly_cap_usd:.4f}"
        )

    started_at = time.perf_counter()
    with Ledger(cfg.runtime.sqlite_path) as ledger:
        ledger.record_run(run_id, task, budget, max_iter)
        pipeline = Pipeline(
            cfg=cfg,
            governor=governor,
            ledger=ledger,
            run_id=run_id,
            tracer=tracer,
            repo=Path(repo),
            gate_cb=_make_gate(auto_approve, run_id=run_id, state_dir=cfg.runtime.state_dir),
        )
        outcome = None
        try:
            outcome = await pipeline.run(task=task, max_iter=max_iter)
            ledger.finalize_run(run_id, status=outcome.status)
        except Exception:
            # Mark the run as crashed in the ledger so an operator can tell
            # the failure mode from a clean abort. See harden/phase-2 PCIV
            # item #3.
            with contextlib.suppress(Exception):
                ledger.finalize_run(run_id, status="crashed")
            raise
        finally:
            if cleanup and outcome is not None and outcome.worktrees:
                with contextlib.suppress(Exception):
                    cleanup_worktrees(Path(repo), outcome.worktrees)
            # Per-run histograms are emitted regardless of crash/success so
            # operators can spot pathological tail latencies and overspend
            # in the same dashboards. Telemetry must never break accounting.
            with contextlib.suppress(Exception):
                elapsed = max(0.0, time.perf_counter() - started_at)
                latency_seconds_per_run().record(elapsed)
                cost_usd_per_run().record(float(governor.spent_usd))
                total_tokens = sum(
                    line.input_tokens + line.output_tokens for line in governor.lines()
                )
                tokens_per_run().record(int(total_tokens))
            # Persist actual spend to the cross-run ledger. Always run on
            # success or crash so a partial run still counts against the
            # window cap. ``record_spend`` may raise if the run pushed us
            # over the cap; that is logged but does not mask the run's own
            # exit status. The emergency-override path uses ``force_record``
            # so the row is marked ``forced=1`` for audit.
            if cross_run_ledger is not None:
                actual_spend = float(governor.spent_usd)
                try:
                    if ignore_cross_run_cap:
                        cross_run_ledger.force_record(
                            actual_spend,
                            reason=f"--ignore-cross-run-cap run_id={run_id}",
                        )
                    else:
                        with contextlib.suppress(_CoreBudgetExceeded):
                            # Surfacing a post-hoc over-cap as a hard error
                            # would mask the actual run outcome. Operators
                            # see the breach via the next run's preflight.
                            cross_run_ledger.record_spend(actual_spend, note=f"run_id={run_id}")
                finally:
                    cross_run_ledger.close()

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

    # Sandbox runtime probe. ``task_trust=untrusted`` is the secure default and
    # requires Docker or Podman; surface availability without forcing a hard
    # failure when running locally on a workstation without containers.
    from .sandbox import detect_runtime

    runtime = detect_runtime()
    trust_default = "untrusted"
    with contextlib.suppress(Exception):
        trust_default = load_config(config).runtime.task_trust
    sandbox_ok = runtime is not None or trust_default == "trusted"
    sandbox_detail = f"runtime={runtime or 'none'} task_trust={trust_default}" + (
        "" if sandbox_ok else " WARNING: install docker/podman or set task_trust=trusted"
    )
    results.append(_check("sandbox", sandbox_ok, sandbox_detail))

    hard = {"python", "uv", "git", "state_dir_writable"}
    all_ok = all(r["ok"] for r in results if r["check"] in hard)

    payload = {"ok": all_ok, "checks": results}
    typer.echo(json.dumps(payload, indent=2))
    raise typer.Exit(code=0 if all_ok else 1)


if __name__ == "__main__":
    app()
