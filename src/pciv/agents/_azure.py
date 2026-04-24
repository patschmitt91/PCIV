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
    return AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)


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
