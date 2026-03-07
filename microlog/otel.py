from __future__ import annotations

import logging
from threading import Lock
from types import MethodType
from typing import Any, Callable, Mapping, cast

from .config import (
    MICROLOG_FIELDS,
    RESERVED_FIELDS,
    LogConfig,
    OTLPConfig,
    resolve_level,
    safe_fields,
    service_fields,
)

_OTEL_ATTR_MAP = {
    "otelSpanID": ("span_id", 16),
    "otelTraceID": ("trace_id", 32),
    "otelServiceName": ("service.name", None),
}

_PROTOCOL_ALIASES = {
    "http": "http/protobuf",
    "http/protobuf": "http/protobuf",
    "http_protobuf": "http/protobuf",
    "http-protobuf": "http/protobuf",
    "grpc": "grpc",
    "grpc/protobuf": "grpc",
    "grpc_proto": "grpc",
    "grpc-protobuf": "grpc",
}

_runtime_metrics_enabled = False
_runtime_metrics_attrs: dict[Any, dict[str, str]] = {}
_runtime_metrics_handles: list[Any] = []
_runtime_metrics_meters: set[Any] = set()
_metrics_lock = Lock()
_METRIC_SPECS = (
    (
        "create_observable_counter",
        "microlog_queued_records_total",
        "queued_records",
        "Total records accepted into microlog async queue.",
    ),
    (
        "create_observable_counter",
        "microlog_dropped_records_total",
        "dropped_records",
        "Total records dropped by microlog queue policies.",
    ),
    (
        "create_observable_counter",
        "microlog_shed_records_total",
        "shed_records",
        "Total records dropped by adaptive shedding policy.",
    ),
    (
        "create_observable_gauge",
        "microlog_queue_size",
        "queue_size",
        "Current microlog async queue depth.",
    ),
    (
        "create_observable_gauge",
        "microlog_queue_maxsize",
        "queue_maxsize",
        "Configured microlog async queue capacity.",
    ),
)


class _OTLPRecordFilter(logging.Filter):
    def __init__(self, static_fields: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self._static_fields = safe_fields(static_fields or {})

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in self._static_fields.items():
            record.__dict__.setdefault(key, value)
        if isinstance(payload := getattr(record, MICROLOG_FIELDS, None), dict):
            record.__dict__.pop(MICROLOG_FIELDS, None)
            record.__dict__.update(safe_fields(payload))
        for key in RESERVED_FIELDS:
            record.__dict__.pop(key, None)
        return True


def _metric_callback(
    field: str, observation: Any, attrs: dict[str, str]
) -> Callable[[Any], list[Any]]:
    from .logger import get_runtime_stats

    def _callback(_options: Any) -> list[Any]:
        if not _runtime_metrics_enabled:
            return []
        return [observation(getattr(get_runtime_stats(), field), attrs)]

    return _callback


def enable_otel_runtime_metrics(
    *,
    meter: Any | None = None,
    meter_name: str = "microlog",
    attributes: dict[str, str] | None = None,
) -> bool:
    global _runtime_metrics_enabled
    try:
        if meter is None:
            from opentelemetry import metrics as otel_metrics  # type: ignore[import-not-found]

            meter = otel_metrics.get_meter(meter_name)
        from opentelemetry.metrics import Observation  # type: ignore[import-not-found]
    except Exception:
        return False
    if meter is None:
        return False

    attrs = dict(attributes or {})
    meter_obj = cast(Any, meter)

    with _metrics_lock:
        attrs_ref = _runtime_metrics_attrs.setdefault(meter_obj, {})
        attrs_ref.clear()
        attrs_ref.update(attrs)
        if meter_obj not in _runtime_metrics_meters:
            for factory, name, field, description in _METRIC_SPECS:
                _runtime_metrics_handles.append(
                    getattr(meter_obj, factory)(
                        name,
                        callbacks=[_metric_callback(field, Observation, attrs_ref)],
                        description=description,
                    )
                )
            _runtime_metrics_meters.add(meter_obj)
        _runtime_metrics_enabled = True
    return True


def disable_otel_runtime_metrics() -> None:
    global _runtime_metrics_enabled
    with _metrics_lock:
        _runtime_metrics_enabled = False


def _extract_otel_context(record: logging.LogRecord, try_opentelemetry: bool) -> dict[str, Any]:
    ctx: dict[str, Any] = {}

    for attr, (target, width) in _OTEL_ATTR_MAP.items():
        value = getattr(record, attr, None)
        if value is not None:
            ctx[target] = f"{value:0{width}x}" if width and isinstance(value, int) else str(value)

    sampled = getattr(record, "otelTraceSampled", None)
    if sampled is not None:
        ctx["trace_sampled"] = bool(sampled)

    if try_opentelemetry and not (ctx.get("trace_id") and ctx.get("span_id")):
        try:
            from opentelemetry.trace import get_current_span  # type: ignore[import-not-found]

            span = cast(Any, get_current_span())
            sc = span.get_span_context() if hasattr(span, "get_span_context") else None
            if sc and getattr(sc, "is_valid", False):
                if (trace_id := getattr(sc, "trace_id", None)) and "trace_id" not in ctx:
                    ctx["trace_id"] = (
                        f"{trace_id:032x}" if isinstance(trace_id, int) else str(trace_id)
                    )
                if (span_id := getattr(sc, "span_id", None)) and "span_id" not in ctx:
                    ctx["span_id"] = f"{span_id:016x}" if isinstance(span_id, int) else str(span_id)
                try:
                    ctx.setdefault("trace_sampled", bool(int(getattr(sc, "trace_flags", 0)) & 1))
                except Exception:
                    pass
        except Exception:
            pass

    return ctx


def _normalize_protocol(protocol: str) -> str:
    if value := _PROTOCOL_ALIASES.get((protocol or "").strip().lower()):
        return value
    raise ValueError(f"Unsupported OTLP protocol '{protocol}'. Expected 'http/protobuf' or 'grpc'.")


def _resolve_otlp_endpoint(protocol: str, otlp_cfg: OTLPConfig) -> str:
    from os import getenv

    return (
        otlp_cfg.endpoint
        or getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        or getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ("http://localhost:4318/v1/logs" if protocol == "http/protobuf" else "localhost:4317")
    )


def _resolve_otlp_insecure(protocol: str, endpoint: str, insecure: bool | None) -> bool:
    if insecure is not None:
        return insecure
    if protocol == "http/protobuf":
        return False
    return not endpoint.strip().lower().startswith("https://")


def _otlp_exporter_kwargs(
    endpoint: str, otlp_cfg: OTLPConfig, *, insecure: bool | None = None
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"endpoint": endpoint}
    if insecure is not None:
        kwargs["insecure"] = insecure
    for name, value in (
        ("headers", dict(otlp_cfg.headers) if otlp_cfg.headers else None),
        ("compression", otlp_cfg.compression),
        ("timeout", float(otlp_cfg.timeout) if otlp_cfg.timeout is not None else None),
    ):
        if value is not None:
            kwargs[name] = value
    return kwargs


def _build_otlp_exporter(
    protocol: str, otlp_cfg: OTLPConfig
) -> tuple[Any, Callable[[], None] | None]:
    endpoint = _resolve_otlp_endpoint(protocol, otlp_cfg)
    insecure = _resolve_otlp_insecure(protocol, endpoint, otlp_cfg.insecure)

    if protocol == "http/protobuf":
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        except Exception as exc:  # pragma: no cover - optional dependency handling
            raise RuntimeError(
                "OTLP HTTP exporter requested, but opentelemetry-exporter-otlp-proto-http is not installed."
            ) from exc

        kwargs = _otlp_exporter_kwargs(endpoint, otlp_cfg)
        cleanup: Callable[[], None] | None = None
        if insecure:
            try:
                import requests
            except Exception as exc:  # pragma: no cover - optional dependency handling
                raise RuntimeError(
                    "HTTP OTLP exporting requested with insecure=True, but the requests package is unavailable."
                ) from exc
            session = requests.Session()
            session.verify = False
            kwargs["session"] = session
            cleanup = session.close

        return OTLPLogExporter(**kwargs), cleanup

    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    except Exception as exc:  # pragma: no cover - optional dependency handling
        raise RuntimeError(
            "OTLP gRPC exporter requested, but opentelemetry-exporter-otlp-proto-grpc is not installed."
        ) from exc

    return OTLPLogExporter(**_otlp_exporter_kwargs(endpoint, otlp_cfg, insecure=insecure)), None


def _otlp_handler(cfg: LogConfig, otlp_cfg: OTLPConfig) -> logging.Handler:
    exporter, cleanup = _build_otlp_exporter(_normalize_protocol(otlp_cfg.protocol), otlp_cfg)
    try:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except Exception as exc:
        raise RuntimeError(
            "OTLP logging requested, but the opentelemetry-sdk package is not installed."
        ) from exc

    provider = LoggerProvider(
        resource=Resource.create({**otlp_cfg.resource_attributes, **service_fields(cfg)})
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    handler = LoggingHandler(
        level=resolve_level(otlp_cfg.level) or logging.NOTSET, logger_provider=provider
    )
    handler.addFilter(_OTLPRecordFilter(cfg.static))
    original_close = handler.close

    def _safe_close(self: logging.Handler) -> None:
        for fn in (original_close, provider.shutdown, cleanup):
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass

    handler.close = MethodType(_safe_close, handler)
    return handler
