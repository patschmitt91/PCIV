"""Reusable scaffolding for structured JSON agents.

Each phase that emits a single pydantic-validated JSON object (plan, critique,
verify) shares the same control flow: a bounded repair loop that calls the
Azure chat endpoint, extracts text and token usage, charges the budget,
records OTel and ledger entries, then parses and validates the response.
This module centralizes that logic so the concrete agents only supply their
prompt and result type.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ..budget import BudgetGovernor
from ..config import ModelRef
from ..state import Ledger
from ..telemetry import agent_span
from ._azure import AzureOpenAILike, build_azure_client, extract_text, extract_usage

T = TypeVar("T", bound=BaseModel)


class JsonAgent(ABC, Generic[T]):
    """Base class for agents that emit a single validated JSON object."""

    phase: ClassVar[str]
    agent_id: ClassVar[str]
    system_prompt: ClassVar[str]
    result_type: ClassVar[type[BaseModel]]

    def __init__(
        self,
        model_ref: ModelRef,
        governor: BudgetGovernor,
        ledger: Ledger,
        run_id: str,
        tracer: Any,
        client: AzureOpenAILike | None = None,
    ) -> None:
        if model_ref.provider != "azure_openai":
            raise ValueError(
                f"{type(self).__name__} requires provider=azure_openai, got {model_ref.provider}"
            )
        if not model_ref.deployment:
            raise ValueError(f"{type(self).__name__} requires a deployment name")
        self._model = model_ref
        self._governor = governor
        self._ledger = ledger
        self._run_id = run_id
        self._tracer = tracer
        self._client = client or build_azure_client(model_ref)

    @abstractmethod
    def _build_user_prompt(self, last_raw: str | None, last_error: str | None) -> str:
        """Return the user-facing prompt, optionally incorporating prior failure context."""

    def _run_loop(self, iteration: int) -> T:
        last_error: str | None = None
        last_raw: str | None = None

        for attempt in range(self._model.retries + 1):
            user_prompt = self._build_user_prompt(last_raw, last_error)
            raw = self._invoke(user_prompt, iteration=iteration, attempt=attempt)
            last_raw = raw
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = f"malformed JSON: {e}"
                continue
            try:
                return self.result_type.model_validate(data)  # type: ignore[return-value]
            except ValidationError as e:
                last_error = f"schema validation failed: {e}"
                continue

        raise RuntimeError(
            f"{self.agent_id} failed after {self._model.retries + 1} attempts: {last_error}"
        )

    def _invoke(self, user_prompt: str, iteration: int, attempt: int) -> str:
        model_id = self._model.model_id()
        invocation_id = self._ledger.start_invocation(
            run_id=self._run_id,
            iteration=iteration,
            phase=self.phase,
            agent_id=self.agent_id,
            model=model_id,
        )
        with agent_span(
            self._tracer,
            f"pciv.{self.agent_id}.invoke",
            agent_id=self.agent_id,
            model=model_id,
            phase=self.phase,
            iteration=iteration,
        ) as span:
            span.set_attribute("attempt", attempt)
            try:
                response = self._client.chat.completions.create(
                    model=model_id,
                    max_tokens=self._model.max_tokens,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
            except Exception as e:
                self._ledger.finish_invocation(
                    invocation_id, 0, 0, 0.0, status="error", error=str(e)
                )
                raise

            text = extract_text(response)
            input_tokens, output_tokens = extract_usage(response)
            try:
                line = self._governor.charge(model_id, input_tokens, output_tokens)
            except Exception as charge_err:
                # Budget ceiling hit: close the invocation before re-raising so
                # the ledger row doesn't stay permanently in status='running'.
                self._ledger.finish_invocation(
                    invocation_id,
                    input_tokens,
                    output_tokens,
                    0.0,
                    status="error",
                    error=str(charge_err),
                )
                raise
            span.set_attribute("tokens_in", input_tokens)
            span.set_attribute("tokens_out", output_tokens)
            span.set_attribute("cost_usd", line.cost_usd)
            self._ledger.record_cost(
                self._run_id,
                invocation_id,
                model_id,
                input_tokens,
                output_tokens,
                line.cost_usd,
            )
            self._ledger.finish_invocation(
                invocation_id, input_tokens, output_tokens, line.cost_usd, status="ok"
            )
            return text
