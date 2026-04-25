"""Shared Azure OpenAI client factory and usage/text extraction helpers."""

from __future__ import annotations

import os
from typing import Any, Protocol

from ..config import ModelRef


class AzureOpenAILike(Protocol):
    """Minimal Azure OpenAI surface used across agents."""

    @property
    def chat(self) -> Any: ...


def build_azure_client(model_ref: ModelRef) -> AzureOpenAILike:
    from openai import AzureOpenAI

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not set")
    api_version = (
        model_ref.api_version or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-10-21"
    )
    # Honour the per-role timeout/retries from plan.yaml. Without these
    # values the SDK defaults to 600s and a single retry, which silently
    # masks slow Azure deployments and inflates wall time. See harden/
    # phase-2 PCIV item #2.
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        timeout=float(model_ref.timeout_s),
        max_retries=int(model_ref.retries),
    )


def extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    return str(getattr(message, "content", "") or "").strip()


def extract_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0)), int(getattr(usage, "completion_tokens", 0))
