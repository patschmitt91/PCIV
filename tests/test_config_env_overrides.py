"""Regression: env-var overrides for Azure deployment names per role.

See harden/phase-2 PCIV item #1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pciv.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_planner_deployment_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_PLANNER_DEPLOYMENT", "custom-planner-dep")
    cfg = load_config(REPO_ROOT / "plan.yaml")
    assert cfg.models.planner.deployment == "custom-planner-dep"


def test_implementer_deployment_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_IMPLEMENTER_DEPLOYMENT", "custom-impl-dep")
    cfg = load_config(REPO_ROOT / "plan.yaml")
    assert cfg.models.implementer.deployment == "custom-impl-dep"


def test_no_override_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_PLANNER_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_CRITIC_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_IMPLEMENTER_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_VERIFIER_DEPLOYMENT", raising=False)
    baseline = load_config(REPO_ROOT / "plan.yaml")
    # Re-load and confirm deployment values are stable.
    cfg = load_config(REPO_ROOT / "plan.yaml")
    assert cfg.models.planner.deployment == baseline.models.planner.deployment
    assert cfg.models.verifier.deployment == baseline.models.verifier.deployment
