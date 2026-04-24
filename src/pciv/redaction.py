"""Central redaction for logs, span attributes, and persisted state.

Scrubs common secret shapes so they do not surface in logging records,
OpenTelemetry span attributes, or ledger rows. Patterns cover ``sk-``
prefixed API keys, bearer tokens, JWTs, long hex blobs, and the literal
values of env vars whose name contains ``KEY`` / ``SECRET`` / ``TOKEN``
/ ``PASSWORD`` / ``CONNECTION_STRING``.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

SECRET_ENV_NAMES: frozenset[str] = frozenset(
    {
        "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "GITHUB_TOKEN",
    }
)

_SECRET_NAME_HINTS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CONNECTION_STRING",
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.=]{8,}\b"),
    re.compile(r"\b[a-fA-F0-9]{40,}\b"),
)


def _iter_env_secret_values() -> tuple[str, ...]:
    out: list[str] = []
    for name, value in os.environ.items():
        if not value:
            continue
        upper = name.upper()
        if upper in SECRET_ENV_NAMES or any(hint in upper for hint in _SECRET_NAME_HINTS):
            out.append(value)
    out.sort(key=len, reverse=True)
    return tuple(out)


def redact(text: str) -> str:
    """Return ``text`` with known secret shapes replaced by :data:`REDACTED`."""

    if not text:
        return text
    out = text
    for literal in _iter_env_secret_values():
        if literal and literal in out:
            out = out.replace(literal, REDACTED)
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Redact both keys flagged as secret-bearing and any secret-shaped values."""

    clean: dict[str, Any] = {}
    for key, value in data.items():
        upper = str(key).upper()
        if upper in SECRET_ENV_NAMES or any(hint in upper for hint in _SECRET_NAME_HINTS):
            clean[key] = REDACTED
            continue
        if isinstance(value, str):
            clean[key] = redact(value)
        elif isinstance(value, Mapping):
            clean[key] = redact_mapping(value)
        else:
            clean[key] = value
    return clean


class RedactionFilter(logging.Filter):
    """Logging filter that rewrites message and args through :func:`redact`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {
                    k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()
                }
        return True
