"""OpenTelemetry counters.

Counters are created lazily through an accessor so the OTel API can
substitute a no-op instrument when no meter provider is installed, and
tests can swap in a real ``MeterProvider`` via
:func:`set_meter_provider_for_tests`.
"""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.metrics import Counter, MeterProvider

_METER_NAME = "pciv"
_counters: dict[str, Counter] = {}
_meter_provider_override: MeterProvider | None = None


def _meter() -> metrics.Meter:
    if _meter_provider_override is not None:
        return _meter_provider_override.get_meter(_METER_NAME)
    return metrics.get_meter(_METER_NAME)


def _counter(name: str, *, unit: str = "1", description: str = "") -> Counter:
    existing = _counters.get(name)
    if existing is not None:
        return existing
    c = _meter().create_counter(name=name, unit=unit, description=description)
    _counters[name] = c
    return c


def runs_total() -> Counter:
    return _counter("runs_total", description="Total runs started.")


def runs_failed_total() -> Counter:
    return _counter("runs_failed_total", description="Runs that ended in a non-ship status.")


def budget_usd_spent_total() -> Counter:
    return _counter(
        "budget_usd_spent_total",
        unit="USD",
        description="Cumulative USD charged against the budget governor.",
    )


def set_meter_provider_for_tests(provider: MeterProvider | None) -> None:
    global _meter_provider_override
    _meter_provider_override = provider
    _counters.clear()
