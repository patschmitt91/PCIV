"""Budget projector and governor.

Pricing is read from plan.yaml. Preflight projection happens before any
network call. Actual spend is tracked per agent invocation and accumulates
against the user-supplied ceiling.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from agentcore.budget import BudgetExceeded as _CoreBudgetExceeded
from agentcore.pricing import cost_for as _core_cost_for

from .config import PlanConfig, Pricing


class BudgetExceededError(_CoreBudgetExceeded):
    """PCIV-specific alias of :class:`agentcore.budget.BudgetExceeded`.

    Inherits so that cross-project tooling can catch the shared base while
    PCIV-only code keeps using the historical name.
    """


@dataclass(frozen=True)
class CostLine:
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def cost_for(model_id: str, input_tokens: int, output_tokens: int, pricing: Pricing) -> float:
    return _core_cost_for(
        input_tokens, output_tokens, pricing.input_per_mtok, pricing.output_per_mtok
    )


def project_run_cost(cfg: PlanConfig) -> float:
    """Preflight cost projection using config-declared token estimates."""
    proj = cfg.budget.projection
    pricing = cfg.pricing

    def price(model_id: str, inp: int, out: int) -> float:
        if model_id not in pricing:
            raise KeyError(f"no pricing entry for model {model_id}")
        return cost_for(model_id, inp, out, pricing[model_id])

    planner_id = cfg.models.planner.model_id()
    critic_id = cfg.models.critic.model_id()
    impl_id = cfg.models.implementer.model_id()
    verifier_id = cfg.models.verifier.model_id()

    plan_cost = price(planner_id, proj.plan_input_tokens, proj.plan_output_tokens)
    critique_cost = price(critic_id, proj.critique_input_tokens, proj.critique_output_tokens)
    impl_cost = price(
        impl_id,
        proj.implement_input_tokens_per_subtask * proj.expected_subtasks,
        proj.implement_output_tokens_per_subtask * proj.expected_subtasks,
    )
    verify_cost = price(verifier_id, proj.verify_input_tokens, proj.verify_output_tokens)

    return plan_cost + critique_cost + impl_cost + verify_cost


class BudgetGovernor:
    """Thread-safe running ledger that aborts when the ceiling is breached."""

    def __init__(self, ceiling_usd: float, cfg: PlanConfig) -> None:
        self._ceiling = ceiling_usd
        self._cfg = cfg
        self._spent = 0.0
        self._lines: list[CostLine] = []
        self._lock = threading.Lock()

    @property
    def ceiling_usd(self) -> float:
        return self._ceiling

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent

    def preflight(self) -> float:
        projected = project_run_cost(self._cfg)
        if projected > self._ceiling:
            raise BudgetExceededError(
                f"projected cost ${projected:.4f} exceeds ceiling ${self._ceiling:.4f}"
            )
        return projected

    def charge(self, model_id: str, input_tokens: int, output_tokens: int) -> CostLine:
        pricing = self._cfg.pricing.get(model_id)
        if pricing is None:
            raise KeyError(f"no pricing entry for model {model_id}")
        cost = cost_for(model_id, input_tokens, output_tokens, pricing)
        with self._lock:
            if self._spent + cost > self._ceiling:
                raise BudgetExceededError(
                    f"charge ${cost:.4f} would exceed ceiling "
                    f"${self._ceiling:.4f} (already spent ${self._spent:.4f})"
                )
            self._spent += cost
            line = CostLine(model_id, input_tokens, output_tokens, cost)
            self._lines.append(line)
        try:
            from pciv.telemetry import budget_usd_spent_total

            budget_usd_spent_total().add(float(cost))
        except Exception:
            # Telemetry must never break accounting.
            pass
        return line

    def lines(self) -> list[CostLine]:
        with self._lock:
            return list(self._lines)
