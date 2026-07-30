"""Optional OpenTelemetry tracing with OTLP export."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)
_provider = None
_instrumented = False


def init_telemetry(app: Any, settings: Any) -> bool:
    global _instrumented, _provider
    if not settings.telemetry_enabled:
        return False
    if _instrumented:
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError as exc:
        raise RuntimeError("OpenTelemetry is enabled but its dependencies are not installed") from exc

    sampler = ParentBased(TraceIdRatioBased(settings.telemetry_sample_ratio))
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.telemetry_service_name}),
        sampler=sampler,
    )
    if settings.telemetry_otlp_endpoint:
        exporter = OTLPSpanExporter(
            endpoint=settings.telemetry_otlp_endpoint,
            headers=settings.telemetry_otlp_headers or None,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    _instrumented = True
    log.info("OpenTelemetry enabled service=%s", settings.telemetry_service_name)
    return True


def annotate_current_span(**attributes: Any) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if not span.is_recording():
            return
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
    except ImportError:
        return


def current_trace_context() -> dict[str, str]:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            return {
                "otel_trace_id": format(context.trace_id, "032x"),
                "otel_span_id": format(context.span_id, "016x"),
            }
    except ImportError:
        pass
    return {}


def shutdown_telemetry() -> None:
    if _provider is not None:
        _provider.shutdown()
