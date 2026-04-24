"""OpenTelemetry configuration with optional Azure Monitor export.

If an App Insights connection string is present in the environment, we
use the azure-monitor-opentelemetry distro. Otherwise we fall back to
a console exporter so tests and offline runs still emit spans.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Tracer

_CONFIGURE_LOCK = threading.Lock()
_CONFIGURED = False


def setup_tracing(service_name: str, conn_string_env: str) -> Tracer:
    """Install a tracer provider exactly once per process."""
    global _CONFIGURED
    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return trace.get_tracer(service_name)
        conn = os.environ.get(conn_string_env)
        if conn:
            try:
                from azure.monitor.opentelemetry import configure_azure_monitor

                configure_azure_monitor(
                    connection_string=conn,
                    resource=Resource.create({"service.name": service_name}),
                )
                _CONFIGURED = True
                return trace.get_tracer(service_name)
            except Exception:
                # Fall through to console if distro fails at import or config.
                pass

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _CONFIGURED = True

    return trace.get_tracer(service_name)


@contextmanager
def agent_span(
    tracer: Tracer,
    name: str,
    *,
    agent_id: str,
    model: str,
    phase: str,
    task_id: str | None = None,
    iteration: int = 0,
) -> Iterator[Span]:
    with tracer.start_as_current_span(name) as span:
        span.set_attribute("agent_id", agent_id)
        span.set_attribute("model", model)
        span.set_attribute("phase", phase)
        span.set_attribute("iteration", iteration)
        if task_id is not None:
            span.set_attribute("task_id", task_id)
        yield span
