"""JSON log formatter plus root-logger configuration.

Exposed from :mod:`pciv.telemetry` alongside the tracing helpers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from opentelemetry import trace

from pciv.redaction import RedactionFilter, redact

_STANDARD_LOGRECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter with OTel trace correlation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }

        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = str(run_id)

        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = str(value)
            payload[key] = value

        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))

        return json.dumps(payload, separators=(",", ":"), sort_keys=False)


_logging_configured = False


def _resolve_log_format(explicit: str | None) -> str:
    if explicit:
        return explicit.lower()
    env = os.environ.get("LOG_FORMAT")
    if env:
        return env.strip().lower()
    return "text" if sys.stderr.isatty() else "json"


def configure_logging(
    level: int = logging.INFO,
    fmt: str | None = None,
    *,
    force: bool = False,
) -> None:
    """Install a stderr handler with JSON or text formatter plus redaction."""

    global _logging_configured
    root = logging.getLogger()
    if _logging_configured and not force:
        root.setLevel(level)
        return

    for h in list(root.handlers):
        if getattr(h, "_pciv_handler", False):
            root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stderr)
    chosen = _resolve_log_format(fmt)
    if chosen == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    handler.addFilter(RedactionFilter())
    handler._pciv_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    _logging_configured = True
