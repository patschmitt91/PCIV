"""Critique agent. Azure OpenAI (reasoning-class deployment; see plan.yaml).

Emits a structured Critique JSON with a bounded repair loop.
"""

from __future__ import annotations

from ..types import Critique, Plan
from ._json_agent import JsonAgent

_SYSTEM_PROMPT = """You are the critique agent in a Plan-Critique-Implement-Verify loop.
Review the given plan for correctness, completeness, dependency integrity, and risks.
Output ONLY a single JSON object matching this schema exactly:
{
  "valid": boolean,
  "blocks_proceed": boolean,
  "issues": [string, ...],
  "missing_cases": [string, ...],
  "dependency_problems": [string, ...],
  "suggested_plan_diff": object
}
Set blocks_proceed=true ONLY if the plan cannot be safely implemented as written.
Return JSON only. No prose. No code fences.
"""


class CritiqueAgent(JsonAgent[Critique]):
    phase = "critique"
    agent_id = "critique_agent"
    system_prompt = _SYSTEM_PROMPT
    result_type = Critique

    def run(self, plan: Plan, iteration: int = 0) -> Critique:
        self._plan = plan
        return self._run_loop(iteration=iteration)

    def _build_user_prompt(self, last_raw: str | None, last_error: str | None) -> str:
        parts = [
            "Plan JSON to critique:",
            self._plan.model_dump_json(indent=2),
        ]
        if last_error and last_raw is not None:
            parts.append(
                "Your previous output failed validation with: "
                + last_error
                + "\nHere is what you returned:\n"
                + last_raw
                + "\nReturn corrected JSON only."
            )
        return "\n\n".join(parts)
