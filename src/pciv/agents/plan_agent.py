"""Plan agent. GPT-5.4 via Azure OpenAI.

Emits a structured Plan JSON. Retries on malformed output with a bounded
repair loop that includes the prior raw response and validation error.
"""

from __future__ import annotations

from ..types import Plan
from ._json_agent import JsonAgent

_SYSTEM_PROMPT = """You are the planning agent in a Plan-Critique-Implement-Verify loop.
Given a coding task and a repository snapshot path, emit a structured JSON plan.
Output ONLY a single JSON object matching this schema exactly:
{
  "goals": [string, ...],
  "subtasks": [
    {
      "id": string,
      "description": string,
      "dependencies": [string, ...],
      "files": [string, ...],
      "acceptance_criteria": [string, ...],
      "risk_flags": [string, ...]
    },
    ...
  ],
  "global_risks": [string, ...]
}
Subtask ids must be unique, short, and dependency-resolvable within the plan.
Subtask ids must match the pattern [A-Za-z0-9][A-Za-z0-9._-]* and MUST NOT
contain slashes, spaces, or parent-directory references.
Return JSON only. No prose. No code fences.
"""


class PlanAgent(JsonAgent[Plan]):
    phase = "plan"
    agent_id = "plan_agent"
    system_prompt = _SYSTEM_PROMPT
    result_type = Plan

    def run(
        self,
        task: str,
        repo_path: str,
        iteration: int = 0,
        critique_feedback: str | None = None,
    ) -> Plan:
        self._task = task
        self._repo_path = repo_path
        self._critique_feedback = critique_feedback
        return self._run_loop(iteration=iteration)

    def _build_user_prompt(self, last_raw: str | None, last_error: str | None) -> str:
        parts = [f"Task:\n{self._task}\n\nRepository snapshot path: {self._repo_path}"]
        if self._critique_feedback:
            parts.append(
                "Critic feedback on the previous plan. Address it in this revision:\n"
                + self._critique_feedback
            )
        if last_error and last_raw is not None:
            parts.append(
                "Your previous output failed validation with: "
                + last_error
                + "\nHere is what you returned:\n"
                + last_raw
                + "\nReturn corrected JSON only."
            )
        return "\n\n".join(parts)
