"""Budget projection and governor arithmetic."""

from __future__ import annotations

import pytest

from pciv.budget import BudgetExceededError, BudgetGovernor, cost_for, project_run_cost
from pciv.config import PlanConfig, Pricing


def test_cost_for_basic() -> None:
    pricing = Pricing(input_per_mtok=5.0, output_per_mtok=25.0)
    assert cost_for("x", 1_000_000, 0, pricing) == pytest.approx(5.0)
    assert cost_for("x", 0, 1_000_000, pricing) == pytest.approx(25.0)
    assert cost_for("x", 500_000, 200_000, pricing) == pytest.approx(2.5 + 5.0)


def test_projection_matches_manual_calc(cfg: PlanConfig) -> None:
    projected = project_run_cost(cfg)
    p = cfg.budget.projection

    planner_id = cfg.models.planner.model_id()
    critic_id = cfg.models.critic.model_id()
    impl_id = cfg.models.implementer.model_id()
    verifier_id = cfg.models.verifier.model_id()

    manual = (
        cost_for(planner_id, p.plan_input_tokens, p.plan_output_tokens, cfg.pricing[planner_id])
        + cost_for(
            critic_id,
            p.critique_input_tokens,
            p.critique_output_tokens,
            cfg.pricing[critic_id],
        )
        + cost_for(
            impl_id,
            p.implement_input_tokens_per_subtask * p.expected_subtasks,
            p.implement_output_tokens_per_subtask * p.expected_subtasks,
            cfg.pricing[impl_id],
        )
        + cost_for(
            verifier_id,
            p.verify_input_tokens,
            p.verify_output_tokens,
            cfg.pricing[verifier_id],
        )
    )
    assert projected == pytest.approx(manual)


def test_governor_preflight_abort(cfg: PlanConfig) -> None:
    g = BudgetGovernor(ceiling_usd=0.01, cfg=cfg)
    with pytest.raises(BudgetExceededError):
        g.preflight()


def test_governor_charge_accumulates(cfg: PlanConfig) -> None:
    g = BudgetGovernor(ceiling_usd=10.0, cfg=cfg)
    planner_id = cfg.models.planner.model_id()
    line1 = g.charge(planner_id, 1000, 500)
    line2 = g.charge(planner_id, 2000, 1000)
    assert g.spent_usd == pytest.approx(line1.cost_usd + line2.cost_usd)
    assert len(g.lines()) == 2


def test_governor_charge_aborts_on_ceiling(cfg: PlanConfig) -> None:
    g = BudgetGovernor(ceiling_usd=0.001, cfg=cfg)
    planner_id = cfg.models.planner.model_id()
    with pytest.raises(BudgetExceededError):
        g.charge(planner_id, 1_000_000, 1_000_000)
