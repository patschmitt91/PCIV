"""OpenTelemetry setup and span helpers."""

from .logging import JsonFormatter, configure_logging
from .metrics import (
    budget_usd_spent_total,
    cost_usd_per_run,
    latency_seconds_per_run,
    runs_failed_total,
    runs_total,
    set_meter_provider_for_tests,
    tokens_per_run,
)
from .tracing import agent_span, setup_tracing

__all__ = [
    "JsonFormatter",
    "agent_span",
    "budget_usd_spent_total",
    "configure_logging",
    "cost_usd_per_run",
    "latency_seconds_per_run",
    "runs_failed_total",
    "runs_total",
    "set_meter_provider_for_tests",
    "setup_tracing",
    "tokens_per_run",
]
