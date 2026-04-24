"""Shared pydantic data models for all phase contracts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Subtask(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_safe(cls, v: str) -> str:
        if not _ID_PATTERN.fullmatch(v):
            raise ValueError(
                "subtask id must match [A-Za-z0-9][A-Za-z0-9._-]* and contain no path separators"
            )
        return v

    @field_validator("dependencies")
    @classmethod
    def _deps_safe(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not _ID_PATTERN.fullmatch(dep):
                raise ValueError(f"dependency id {dep!r} has invalid format")
        return v


class Plan(BaseModel):
    goals: list[str] = Field(min_length=1)
    subtasks: list[Subtask] = Field(min_length=1)
    global_risks: list[str] = Field(default_factory=list)

    @field_validator("subtasks")
    @classmethod
    def _dependencies_resolve(cls, v: list[Subtask]) -> list[Subtask]:
        ids = {s.id for s in v}
        for s in v:
            for dep in s.dependencies:
                if dep not in ids:
                    raise ValueError(f"subtask {s.id} depends on unknown id {dep}")
        return v


class Critique(BaseModel):
    valid: bool
    blocks_proceed: bool
    issues: list[str] = Field(default_factory=list)
    missing_cases: list[str] = Field(default_factory=list)
    dependency_problems: list[str] = Field(default_factory=list)
    suggested_plan_diff: dict[str, Any] = Field(default_factory=dict)


Verdict = Literal["ship", "iterate", "reject"]


class VerdictReport(BaseModel):
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    per_subtask: dict[str, Verdict] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class RunConfig(BaseModel):
    task: str
    budget_usd: float
    max_iter: int
    config_path: str
    repo_path: str
    run_id: str
