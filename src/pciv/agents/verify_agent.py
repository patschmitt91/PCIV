"""Verifier. Azure OpenAI (reasoning-class deployment; see plan.yaml). Emits a VerdictReport."""

from __future__ import annotations

from ..types import Plan, VerdictReport
from ._json_agent import JsonAgent

_SYSTEM_PROMPT = """You are the verifier in a Plan-Critique-Implement-Verify loop.
Given the original plan, per-subtask diffs, and per-subtask test outputs, decide
whether to ship, iterate, or reject. Output ONLY this JSON:
{
  "verdict": "ship" | "iterate" | "reject",
  "reasons": [string, ...],
  "per_subtask": {"<task_id>": "ship" | "iterate" | "reject", ...}
}
Rules:
- Use "iterate" for fixable gaps that another implementation pass can address.
- Use "reject" for unsafe or fundamentally broken outputs that should not be retried.
- Use "ship" only when all subtasks meet their acceptance criteria and tests pass.
Return JSON only. No prose. No code fences.
"""


class VerifyAgent(JsonAgent[VerdictReport]):
    phase = "verify"
    agent_id = "verify_agent"
    system_prompt = _SYSTEM_PROMPT
    result_type = VerdictReport

    def run(
        self,
        plan: Plan,
        per_subtask_diffs: dict[str, str],
        per_subtask_tests: dict[str, str],
        iteration: int,
    ) -> VerdictReport:
        self._plan = plan
        self._diffs = per_subtask_diffs
        self._tests = per_subtask_tests
        return self._run_loop(iteration=iteration)

    def _build_user_prompt(self, last_raw: str | None, last_error: str | None) -> str:
        parts: list[str] = ["Plan JSON:", self._plan.model_dump_json(indent=2), ""]
        parts.append("Per-subtask diffs:")
        for tid, diff in self._diffs.items():
            parts.append(f"=== {tid} ===\n{diff[:12000]}")
        parts.append("")
        parts.append("Per-subtask test output:")
        for tid, out in self._tests.items():
            parts.append(f"=== {tid} ===\n{out[:6000]}")
        if last_error and last_raw is not None:
            parts.append(
                "Your previous output failed validation with: "
                + last_error
                + "\nReturn corrected JSON only."
            )
        return "\n".join(parts)
