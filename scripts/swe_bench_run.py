"""
SWE-bench Verified adapter for PCIV.

Iterates SWE-bench Verified tasks, runs PCIV on each cloned repo, and
writes a predictions JSONL file for the SWE-bench harness evaluator.

Usage:
    # 10-task smoke test (run from the PCIV repo root)
    uv run python scripts/swe_bench_run.py --tasks 10

    # Full run — stops automatically when the monthly_cap_usd in plan.yaml is hit
    uv run python scripts/swe_bench_run.py --tasks 0 --output predictions.jsonl

Prerequisites:
    uv add datasets  (already in PCIV .venv if datasets is a dep)
    Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in your shell,
    or copy .env.example -> .env and source it.

Evaluating predictions:
    pip install swebench
    python -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --predictions_path predictions.jsonl \
        --max_workers 4 \
        --run_id pciv_gpt4o
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Run from PCIV repo root so .pciv/ledger.db accumulates cross-task spend.
PCIV_ROOT = Path(__file__).parent.parent.resolve()
PLAN_YAML = PCIV_ROOT / "plan.yaml"
MODEL_NAME = "pciv-gpt4o-v0.2"

# Exit code PCIV uses when budget is exhausted.
_BUDGET_EXIT = 2


def _check_env() -> None:
    missing = [v for v in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY") if not os.environ.get(v)]
    if missing:
        sys.exit(
            f"Missing env vars: {', '.join(missing)}\n"
            "Copy .env.example -> .env, fill in your values, then:\n"
            "  set -a && source .env && set +a  (bash)\n"
            "  Get-Content .env | ForEach-Object { $k,$v = $_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k,$v) }  (PowerShell)"
        )


def _load_tasks(n: int | None) -> list[dict]:
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("datasets not installed — run: uv add datasets")
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    tasks = list(ds)
    return tasks if n is None else tasks[:n]


def _clone(repo: str, base_commit: str, dest: Path) -> bool:
    try:
        subprocess.run(
            ["git", "clone", "--filter=blob:none", f"https://github.com/{repo}.git", str(dest)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(dest),
            check=True,
            capture_output=True,
            timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _install_deps(repo_dir: Path) -> None:
    """Best-effort install of the repo's test dependencies before PCIV runs.

    Uses ``uv pip install`` so that packages land in PCIV's uv-managed venv
    (which has no pip module) rather than failing silently with rc=1.
    setuptools is ensured first for Python 3.12 compatibility with older packages
    that still import distutils.
    """
    # Python 3.12 removed distutils; setuptools shims it back in.
    subprocess.run(
        ["uv", "pip", "install", "setuptools", "-q"],
        capture_output=True,
        timeout=60,
        cwd=str(PCIV_ROOT),
    )
    for spec in [".[test,dev]", ".[test]", ".[dev]", "."]:
        r = subprocess.run(
            ["uv", "pip", "install", "-e", spec, "-q"],
            cwd=str(repo_dir),
            capture_output=True,
            timeout=300,
        )
        if r.returncode == 0:
            return


def _swebench_image(repo: str, version: str) -> str:
    """Return the SWE-bench evaluation Docker image for a repo+version."""
    normalized = repo.replace("/", "__")
    return f"swebench/sweb.eval.x86_64.{normalized}-v{version}:latest"


def _run_pciv(repo_dir: Path, problem: str, per_task_budget: float, image: str = "") -> tuple[int, str]:
    """Run PCIV on a cloned repo. Returns (returncode, integration_branch, run_id)."""
    env = {**os.environ}
    if image:
        env["PCIV_SANDBOX_IMAGE"] = image
    result = subprocess.run(
        [
            "uv", "run", "pciv", "run", problem,
            "--repo", str(repo_dir),
            "--budget", str(per_task_budget),
            "--yes",
            "--cleanup",
            "--config", str(PLAN_YAML),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PCIV_ROOT),
        timeout=1800,  # 30 min hard ceiling per task
    )
    branch = ""
    run_id = ""
    for line in result.stdout.splitlines():
        if line.startswith("integration_branch="):
            branch = line.split("=", 1)[1].strip()
        elif line.startswith("run_id="):
            run_id = line.split("=", 1)[1].split()[0].strip()
    return result.returncode, branch, run_id


def _extract_diff(repo_dir: Path, base_commit: str, integration_branch: str) -> str:
    if not integration_branch:
        return ""
    try:
        r = subprocess.run(
            ["git", "diff", f"{base_commit}..{integration_branch}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return r.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def _monthly_cap_exhausted() -> bool:
    """Return True only if the cross-run monthly cap is genuinely exhausted.

    rc=2 is used for both per-task ceiling hits and the monthly cap; this
    disambiguates by querying the persistent ledger directly.
    """
    try:
        import yaml  # bundled with datasets / huggingface-hub
        from agentcore.budget import PersistentBudgetLedger

        with open(PLAN_YAML) as f:
            plan = yaml.safe_load(f)
        cap = plan["budget"].get("monthly_cap_usd")
        if cap is None:
            return False
        db_path = PCIV_ROOT / plan["runtime"]["sqlite_path"]
        window = plan["budget"].get("window", "monthly")
        ledger = PersistentBudgetLedger(str(db_path), cap_usd=cap, window=window)
        remaining = ledger.remaining_in_current_window()
        ledger.close()
        return remaining <= 0
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PCIV against SWE-bench Verified")
    parser.add_argument("--tasks", type=int, default=10, help="Tasks to run (0 = all 500)")
    parser.add_argument("--output", default="predictions.jsonl", help="Output JSONL path")
    parser.add_argument("--budget", type=float, default=2.00, help="Per-task USD budget")
    parser.add_argument("--start", type=int, default=0, help="Skip first N tasks (resume)")
    args = parser.parse_args()

    _check_env()

    n = args.tasks or None
    all_tasks = _load_tasks(n)
    tasks = all_tasks[args.start:]
    print(f"Loaded {len(all_tasks)} tasks, running {len(tasks)} (start={args.start})")
    print(f"Output: {args.output}  Per-task budget: ${args.budget:.2f}  Model: {MODEL_NAME}")

    # Open in append mode so --start N can resume a partial run.
    out = open(args.output, "a", encoding="utf-8")
    patches = 0
    budget_exhausted = False

    try:
        for i, task in enumerate(tasks):
            idx = args.start + i + 1
            instance_id = task["instance_id"]
            print(f"\n[{idx}/{args.start + len(tasks)}] {instance_id}", flush=True)

            patch = ""
            run_id = ""
            with tempfile.TemporaryDirectory() as tmpdir:
                repo_dir = Path(tmpdir) / "repo"

                if not _clone(task["repo"], task["base_commit"], repo_dir):
                    print("  clone FAILED — skipping")
                else:
                    _install_deps(repo_dir)
                    image = _swebench_image(task["repo"], task["version"])
                    try:
                        rc, branch, run_id = _run_pciv(repo_dir, task["problem_statement"], args.budget, image=image)
                    except subprocess.TimeoutExpired:
                        print("  pciv TIMEOUT — skipping")
                        rc, branch, run_id = 1, "", ""

                    if rc == _BUDGET_EXIT:
                        if _monthly_cap_exhausted():
                            print("  monthly cap exhausted — stopping")
                            budget_exhausted = True
                        else:
                            print("  per-task budget hit — skipping")
                    elif branch:
                        patch = _extract_diff(repo_dir, task["base_commit"], branch)
                        patches += 1

                    status = "patch" if patch else ("cap" if budget_exhausted else "no_patch")
                    print(f"  rc={rc} branch={branch or '-'} status={status}")

            out.write(
                json.dumps({
                    "instance_id": instance_id,
                    "model_name_or_path": MODEL_NAME,
                    "model_patch": patch,
                    "run_id": run_id,
                }) + "\n"
            )
            out.flush()

            if budget_exhausted:
                break

    finally:
        out.close()

    total = i + 1 if "i" in dir() else 0
    print(f"\nDone. {patches}/{total} patches written to {args.output}")
    if budget_exhausted:
        print("Monthly cap hit. Re-run next month or raise monthly_cap_usd in plan.yaml.")
    print(f"\nEvaluate with:")
    print(f"  python -m swebench.harness.run_evaluation \\")
    print(f"    --dataset_name princeton-nlp/SWE-bench_Verified \\")
    print(f"    --predictions_path {args.output} \\")
    print(f"    --max_workers 4 \\")
    print(f"    --run_id pciv_gpt4o")


if __name__ == "__main__":
    main()
