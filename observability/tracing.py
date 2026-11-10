"""OpenTelemetry tracing. LLM spans carry gen_ai.* attributes so Langfuse (OTLP) can read them.

Tracing is a no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set.
"""

import contextlib
import logging
from typing import Any, Iterator, List

from config.settings import get_settings

logger = logging.getLogger(__name__)
_tracer = None


class _NoopSpan:
    def set_attribute(self, *_: Any) -> None:
        pass


def setup_tracing(service_name: str) -> None:
    global _tracer
    s = get_settings()
    if not s.OTEL_EXPORTER_OTLP_ENDPOINT:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers = dict(h.split("=", 1) for h in s.OTEL_EXPORTER_OTLP_HEADERS.split(",") if "=" in h)
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=s.OTEL_EXPORTER_OTLP_ENDPOINT, headers=headers)))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
    except Exception as e:  # tracing must never break calls
        logger.warning(f"Tracing disabled: {e}")


@contextlib.contextmanager
def llm_span(purpose: str, model: str, messages: List[dict]) -> Iterator[Any]:
    if _tracer is None:
        yield _NoopSpan()
        return
    with _tracer.start_as_current_span(f"llm.{purpose}") as span:
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.operation.name", purpose)
        span.set_attribute("gen_ai.prompt.message_count", len(messages))
        yield span
