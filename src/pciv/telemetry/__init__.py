"""OpenTelemetry setup and span helpers."""

from .tracing import agent_span, setup_tracing

__all__ = ["agent_span", "setup_tracing"]
