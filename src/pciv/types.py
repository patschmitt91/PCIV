"""Shared pydantic data models for all phase contracts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Cap plan size so a runaway planner cannot fan out work that the budget
# governor would later have to cancel mid-stream. Matches the bench task
# upper bound. See harden/phase-2 PCIV item #4.
MAX_SUBTASKS = 32


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
        if len(v) > MAX_SUBTASKS:
            raise ValueError(f"plan has {len(v)} subtasks; MAX_SUBTASKS={MAX_SUBTASKS}")
        ids = {s.id for s in v}
        if len(ids) != len(v):
            raise ValueError("duplicate subtask ids in plan")
        for s in v:
            for dep in s.dependencies:
                if dep not in ids:
                    raise ValueError(f"subtask {s.id} depends on unknown id {dep}")
        # Cycle detection via Kahn's algorithm. Reject up front so the
        # workflow's _topological_order is never asked to resolve a cycle.
        graph: dict[str, set[str]] = {s.id: set(s.dependencies) for s in v}
        in_degree: dict[str, int] = {sid: 0 for sid in graph}
        for deps in graph.values():
            for dep in deps:
                in_degree[dep] = in_degree.get(dep, 0)
        for sid, deps in graph.items():
            in_degree[sid] = len(deps)
        ready = [sid for sid, d in in_degree.items() if d == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for sid, deps in graph.items():
                if current in deps:
                    in_degree[sid] -= 1
                    if in_degree[sid] == 0:
                        ready.append(sid)
        if visited != len(graph):
            raise ValueError("plan has a dependency cycle")
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
