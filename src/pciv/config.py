"""Loader and pydantic schema for plan.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ModelRef(BaseModel):
    provider: str
    model: str | None = None
    deployment: str | None = None
    api_version: str | None = None
    max_tokens: int = 4096
    thinking: Literal["adaptive", "off"] | None = None
    effort: Literal["low", "medium", "high"] | None = None
    timeout_s: int = 120
    retries: int = 2
    max_turns: int = 40
    max_concurrency: int = 1

    def model_id(self) -> str:
        """Return the canonical model id used for pricing lookups."""
        if self.model:
            return self.model
        if self.deployment:
            return self.deployment
        raise ValueError("ModelRef missing both model and deployment")


class Pricing(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class Projection(BaseModel):
    plan_input_tokens: int
    plan_output_tokens: int
    critique_input_tokens: int
    critique_output_tokens: int
    implement_input_tokens_per_subtask: int
    implement_output_tokens_per_subtask: int
    verify_input_tokens: int
    verify_output_tokens: int
    expected_subtasks: int


class BudgetConfig(BaseModel):
    default_ceiling_usd: float
    projection: Projection
    # Cross-run rolling-window cap (ADR 0007). When set, every `pciv run`
    # consults a persistent SQLite ledger and refuses to start if the
    # window is exhausted. ``None`` disables the cross-run check entirely
    # (per-run ``--budget`` still applies). ``window`` selects the rolling
    # bucket (``daily`` or ``monthly``).
    monthly_cap_usd: float | None = None
    window: Literal["daily", "monthly"] = "monthly"


class Iteration(BaseModel):
    max_rounds: int = 2
    max_plan_revisions: int = 2


class GateConfig(BaseModel):
    enabled: bool = True
    default: Literal["approve", "revise", "reject"] = "approve"


class Gates(BaseModel):
    approve_plan: GateConfig
    approve_merge: GateConfig


class Telemetry(BaseModel):
    service_name: str = "pciv"
    app_insights_connection_string_env: str = "APPLICATIONINSIGHTS_CONNECTION_STRING"


class Runtime(BaseModel):
    state_dir: str = ".pciv"
    sqlite_path: str = ".pciv/ledger.db"
    # Sandbox boundary for model-authored code executed via pytest. Default
    # is ``untrusted`` (secure by default); flip to ``trusted`` only when the
    # task content is fully internal and you accept host-level execution of
    # any conftest.py / pytest_plugins the implement agent may produce.
    # See docs/decisions/0004-untrusted-task-sandbox.md.
    task_trust: Literal["trusted", "untrusted"] = "untrusted"


class Models(BaseModel):
    planner: ModelRef
    critic: ModelRef
    implementer: ModelRef
    verifier: ModelRef


class PlanConfig(BaseModel):
    version: int
    models: Models
    pricing: dict[str, Pricing]
    budget: BudgetConfig
    iteration: Iteration
    gates: Gates
    telemetry: Telemetry
    runtime: Runtime

    pricing_default: dict[str, Pricing] = Field(default_factory=dict, exclude=True)


def load_config(path: str | Path) -> PlanConfig:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    cfg = PlanConfig.model_validate(raw)

    # Allow operators to override Azure deployment names per role from the
    # environment without forking plan.yaml. Mirrors the model-id override
    # pattern Budgeteer's policy.yaml uses. See harden/phase-2 PCIV item #1.
    _role_env = {
        "planner": "AZURE_OPENAI_PLANNER_DEPLOYMENT",
        "critic": "AZURE_OPENAI_CRITIC_DEPLOYMENT",
        "implementer": "AZURE_OPENAI_IMPLEMENTER_DEPLOYMENT",
        "verifier": "AZURE_OPENAI_VERIFIER_DEPLOYMENT",
    }
    for role, env_name in _role_env.items():
        override = os.environ.get(env_name)
        if override:
            ref = getattr(cfg.models, role)
            setattr(cfg.models, role, ref.model_copy(update={"deployment": override}))
    return cfg
