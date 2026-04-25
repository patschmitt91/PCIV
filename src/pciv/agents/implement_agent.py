"""Implementer subagent. Azure OpenAI (codegen-class deployment; see plan.yaml) with a tool loop.

One subagent session per subtask. Sandboxed to a git worktree: file
reads and writes are path-confined; the only shell command allowed is
``pytest``. Emits a structured completion JSON on success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..budget import BudgetGovernor
from ..config import ModelRef
from ..sandbox import SandboxUnavailableError
from ..sandbox import run_pytest as sandbox_run_pytest
from ..state import Ledger
from ..telemetry import agent_span
from ..types import Subtask
from ._azure import AzureOpenAILike, build_azure_client, extract_usage

_SYSTEM_PROMPT = """You are an implementation subagent in a Plan-Critique-Implement-Verify loop.
You work in a sandboxed git worktree. Modify only the files listed in the subtask.
Use tools to read files, write files, list directories, and run pytest.
When done, send a final assistant message whose content is ONLY this JSON object:
{
  "status": "complete" | "failed",
  "changed_files": [string, ...],
  "notes": string
}
No prose outside that final JSON. No code fences. Do not call tools in the same
message as the final JSON.
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file under the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a UTF-8 text file under the worktree. Overwrites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries of a directory under the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pytest",
            "description": "Run pytest with optional extra arguments. Captures stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": [],
            },
        },
    },
]


class _Completion(BaseModel):
    status: Literal["complete", "failed"]
    changed_files: list[str] = Field(default_factory=list)
    notes: str = ""


@dataclass
class ImplementResult:
    task_id: str
    status: str
    changed_files: list[str]
    notes: str
    turns: int


class PathEscapeError(RuntimeError):
    pass


_MAX_WRITE_BYTES = 2 * 1024 * 1024  # 2 MB per file

# Pytest args the model is allowed to pass. Anything not matching this set or
# these prefixes is silently dropped before subprocess invocation.
_PYTEST_ALLOWED_FLAGS = frozenset(
    {
        "-v",
        "--verbose",
        "-q",
        "--quiet",
        "-x",
        "--exitfirst",
        "-s",
        "--capture=no",
        "--tb=short",
        "--tb=long",
        "--tb=no",
        "--tb=line",
        "--tb=auto",
        "-r",
        "rN",
        "rE",
        "rF",
        "--no-header",
        "--co",
        "--collect-only",
    }
)
_PYTEST_ALLOWED_PREFIXES = ("-k", "-m", "--tb=", "-r")


def _resolve_safe(worktree: Path, rel: str) -> Path:
    candidate = (worktree / rel).resolve()
    root = worktree.resolve()
    if root != candidate and root not in candidate.parents:
        raise PathEscapeError(f"path {rel} escapes worktree")
    return candidate


def _tool_read_file(worktree: Path, path: str) -> dict[str, Any]:
    p = _resolve_safe(worktree, path)
    if not p.is_file():
        return {"ok": False, "error": "not a file"}
    return {"ok": True, "content": p.read_text(encoding="utf-8")}


def _tool_write_file(worktree: Path, path: str, content: str) -> dict[str, Any]:
    p = _resolve_safe(worktree, path)
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_WRITE_BYTES:
        return {
            "ok": False,
            "error": f"content size {len(encoded)} bytes exceeds {_MAX_WRITE_BYTES} byte limit",
        }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "bytes": len(encoded)}


def _tool_list_dir(worktree: Path, path: str) -> dict[str, Any]:
    p = _resolve_safe(worktree, path)
    if not p.is_dir():
        return {"ok": False, "error": "not a directory"}
    entries = sorted(child.name + ("/" if child.is_dir() else "") for child in p.iterdir())
    return {"ok": True, "entries": entries}


def _tool_run_pytest(
    worktree: Path,
    args: list[str] | None,
    *,
    trust: str = "untrusted",
) -> dict[str, Any]:
    safe_args: list[str] = []
    for arg in args or []:
        if arg in _PYTEST_ALLOWED_FLAGS or arg.startswith(_PYTEST_ALLOWED_PREFIXES):
            safe_args.append(arg)
        # Silently drop anything that could modify paths, load plugins, or
        # exfiltrate data (e.g. --rootdir, -p, --import-mode, --pyargs).
    try:
        result = sandbox_run_pytest(
            worktree,
            trust=trust,
            extra_args=safe_args,
            timeout_s=300,  # type: ignore[arg-type]
        )
    except SandboxUnavailableError as e:
        return {"ok": False, "error": f"sandbox unavailable: {e}"}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-4000:],
        "sandboxed": result.sandboxed,
        "runtime": result.runtime,
    }


def _dispatch(
    worktree: Path,
    name: str,
    args: dict[str, Any],
    *,
    trust: str = "untrusted",
) -> dict[str, Any]:
    try:
        if name == "read_file":
            return _tool_read_file(worktree, args["path"])
        if name == "write_file":
            return _tool_write_file(worktree, args["path"], args["content"])
        if name == "list_dir":
            return _tool_list_dir(worktree, args["path"])
        if name == "run_pytest":
            return _tool_run_pytest(worktree, args.get("args"), trust=trust)
        return {"ok": False, "error": f"unknown tool {name}"}
    except PathEscapeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class ImplementAgent:
    def __init__(
        self,
        model_ref: ModelRef,
        governor: BudgetGovernor,
        ledger: Ledger,
        run_id: str,
        tracer: Any,
        client: AzureOpenAILike | None = None,
        task_trust: str = "untrusted",
    ) -> None:
        if model_ref.provider != "azure_openai":
            raise ValueError(
                f"ImplementAgent requires provider=azure_openai, got {model_ref.provider}"
            )
        if not model_ref.deployment:
            raise ValueError("ImplementAgent requires a deployment name")
        self._model = model_ref
        self._governor = governor
        self._ledger = ledger
        self._run_id = run_id
        self._tracer = tracer
        self._client = client or build_azure_client(model_ref)
        self._task_trust = task_trust

    def run(
        self,
        subtask: Subtask,
        worktree: Path,
        iteration: int,
        prior_feedback: str | None = None,
    ) -> ImplementResult:
        model_id = self._model.model_id()
        invocation_id = self._ledger.start_invocation(
            run_id=self._run_id,
            iteration=iteration,
            phase="implement",
            agent_id=f"implement_worker:{subtask.id}",
            model=model_id,
            task_id=subtask.id,
        )

        user_prompt = self._build_user_prompt(subtask, worktree, prior_feedback)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        total_in = 0
        total_out = 0
        total_cost = 0.0

        with agent_span(
            self._tracer,
            "pciv.implement_agent.invoke",
            agent_id=f"implement_worker:{subtask.id}",
            model=model_id,
            phase="implement",
            task_id=subtask.id,
            iteration=iteration,
        ) as span:
            try:
                for turn in range(self._model.max_turns):
                    response = self._client.chat.completions.create(
                        model=model_id,
                        max_tokens=self._model.max_tokens,
                        messages=messages,
                        tools=_TOOLS,
                        tool_choice="auto",
                    )
                    in_tok, out_tok = extract_usage(response)
                    line = self._governor.charge(model_id, in_tok, out_tok)
                    total_in += in_tok
                    total_out += out_tok
                    total_cost += line.cost_usd

                    choice = response.choices[0]
                    msg = choice.message
                    tool_calls = getattr(msg, "tool_calls", None) or []

                    # Append assistant message. OpenAI SDK objects support model_dump.
                    asst_dict: dict[str, Any] = {
                        "role": "assistant",
                        "content": msg.content,
                    }
                    if tool_calls:
                        asst_dict["tool_calls"] = [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ]
                    messages.append(asst_dict)

                    if tool_calls:
                        for tc in tool_calls:
                            try:
                                args = json.loads(tc.function.arguments or "{}")
                            except json.JSONDecodeError as e:
                                result: dict[str, Any] = {
                                    "ok": False,
                                    "error": f"bad JSON args: {e}",
                                }
                            else:
                                result = _dispatch(
                                    worktree,
                                    tc.function.name,
                                    args,
                                    trust=self._task_trust,
                                )
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": json.dumps(result),
                                }
                            )
                        continue

                    content = (msg.content or "").strip()
                    try:
                        data = json.loads(content)
                        completion = _Completion.model_validate(data)
                    except (json.JSONDecodeError, ValidationError) as e:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your final message must be the completion JSON object only. "
                                    f"Validation failed: {e}"
                                ),
                            }
                        )
                        continue

                    span.set_attribute("tokens_in", total_in)
                    span.set_attribute("tokens_out", total_out)
                    span.set_attribute("cost_usd", total_cost)
                    span.set_attribute("turns", turn + 1)
                    self._ledger.record_cost(
                        self._run_id, invocation_id, model_id, total_in, total_out, total_cost
                    )
                    self._ledger.finish_invocation(
                        invocation_id, total_in, total_out, total_cost, status="ok"
                    )
                    return ImplementResult(
                        task_id=subtask.id,
                        status=completion.status,
                        changed_files=completion.changed_files,
                        notes=completion.notes,
                        turns=turn + 1,
                    )

                raise RuntimeError(f"subtask {subtask.id} exceeded max_turns")
            except Exception as e:
                self._ledger.finish_invocation(
                    invocation_id, total_in, total_out, total_cost, status="error", error=str(e)
                )
                raise

    def _build_user_prompt(
        self, subtask: Subtask, worktree: Path, prior_feedback: str | None
    ) -> str:
        parts = [
            f"Subtask id: {subtask.id}",
            f"Description: {subtask.description}",
            f"Allowed files (only modify these): {subtask.files}",
            f"Acceptance criteria: {subtask.acceptance_criteria}",
            f"Risk flags: {subtask.risk_flags}",
            f"Worktree root: {worktree}",
            "Use relative paths in tool calls, relative to the worktree root.",
        ]
        if prior_feedback:
            parts.append("Feedback from the previous iteration:\n" + prior_feedback)
        return "\n".join(parts)
