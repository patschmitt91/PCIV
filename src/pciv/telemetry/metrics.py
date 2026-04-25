"""OpenTelemetry counters and histograms (delegates to agentcore.telemetry).

The cache, provider override, and instrument-creation logic live in
:mod:`agentcore.telemetry`. This module wraps the shared scaffolding with
PCIV-specific instrument names so callers keep the same API.
"""

from __future__ import annotations

from agentcore import telemetry as _core
from opentelemetry.metrics import Counter, Histogram, MeterProvider

_METER_NAME = "pciv"


def runs_total() -> Counter:
    return _core.get_counter(_METER_NAME, "runs_total", description="Total runs started.")


def runs_failed_total() -> Counter:
    return _core.get_counter(
        _METER_NAME,
        "runs_failed_total",
        description="Runs that ended in a non-ship status.",
    )


def budget_usd_spent_total() -> Counter:
    return _core.get_counter(
        _METER_NAME,
        "budget_usd_spent_total",
        unit="USD",
        description="Cumulative USD charged against the budget governor.",
    )


def cost_usd_per_run() -> Histogram:
    return _core.get_histogram(
        _METER_NAME,
        "cost_usd_per_run",
        unit="USD",
        description="USD spent on a single run, recorded once per terminal status.",
    )


def latency_seconds_per_run() -> Histogram:
    return _core.get_histogram(
        _METER_NAME,
        "latency_seconds_per_run",
        unit="s",
        description="Wall-clock seconds from CLI run start to terminal status.",
    )


def tokens_per_run() -> Histogram:
    return _core.get_histogram(
        _METER_NAME,
        "tokens_per_run",
        description="Total tokens (input+output) charged across a single run.",
    )


def set_meter_provider_for_tests(provider: MeterProvider | None) -> None:
    """Override the meter provider used by all PCIV instruments.

    Delegates to :func:`agentcore.telemetry.set_meter_provider_for_tests`,
    which clears the cache so subsequent accessors rebind to ``provider``.
    """

    _core.set_meter_provider_for_tests(provider)
