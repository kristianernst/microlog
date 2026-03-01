from __future__ import annotations

import atexit
import json
import logging
import os
import re
import socket
import sys
import traceback
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock
from types import MethodType
from typing import Any, Callable, Iterable, cast

from .config import FileConfig, LogConfig, OTLPConfig, StdoutConfig, severity_number

try:
    import orjson as _ORJSON
except Exception:  # pragma: no cover - optional dependency
    _ORJSON = None

_SKIP_EXTRA_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "otelSpanID",
    "otelTraceID",
    "otelServiceName",
    "otelTraceSampled",
}

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

_listener: QueueListener | None = None
_active_queue: Queue[logging.LogRecord] | None = None
_stats_lock = Lock()
_stats: dict[str, int] = {
    "queued_records": 0,
    "dropped_records": 0,
    "dropped_oldest_records": 0,
    "shed_records": 0,
}
_metrics_lock = Lock()
_runtime_metrics_enabled = False
_runtime_metrics_attrs: dict[str, str] = {}
_runtime_metrics_handles: list[Any] = []


@dataclass(frozen=True, slots=True)
class RuntimeStats:
    queued_records: int
    dropped_records: int
    dropped_oldest_records: int
    shed_records: int
    queue_size: int
    queue_maxsize: int


def _inc_stat(name: str, value: int = 1) -> None:
    with _stats_lock:
        _stats[name] += value


def get_runtime_stats() -> RuntimeStats:
    with _stats_lock:
        queue = _active_queue
        queue_size = 0
        queue_maxsize = 0
        if queue is not None:
            try:
                queue_size = queue.qsize()
            except Exception:
                queue_size = 0
            queue_maxsize = max(0, int(getattr(queue, "maxsize", 0)))
        return RuntimeStats(
            queued_records=_stats["queued_records"],
            dropped_records=_stats["dropped_records"],
            dropped_oldest_records=_stats["dropped_oldest_records"],
            shed_records=_stats["shed_records"],
            queue_size=queue_size,
            queue_maxsize=queue_maxsize,
        )


def reset_runtime_stats() -> None:
    with _stats_lock:
        _stats["queued_records"] = 0
        _stats["dropped_records"] = 0
        _stats["dropped_oldest_records"] = 0
        _stats["shed_records"] = 0


def enable_otel_runtime_metrics(
    *,
    meter: Any | None = None,
    meter_name: str = "microlog",
    attributes: dict[str, str] | None = None,
) -> bool:
    global _runtime_metrics_enabled, _runtime_metrics_attrs
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

    def _counter_callback(field: str):
        def _callback(_options: Any):
            if not _runtime_metrics_enabled:
                return []
            stats = get_runtime_stats()
            return [Observation(getattr(stats, field), _runtime_metrics_attrs)]

        return _callback

    def _gauge_callback(field: str):
        def _callback(_options: Any):
            if not _runtime_metrics_enabled:
                return []
            stats = get_runtime_stats()
            return [Observation(getattr(stats, field), _runtime_metrics_attrs)]

        return _callback

    with _metrics_lock:
        if not _runtime_metrics_handles:
            _runtime_metrics_handles.extend(
                [
                    meter_obj.create_observable_counter(
                        "microlog_queued_records_total",
                        callbacks=[_counter_callback("queued_records")],
                        description="Total records accepted into microlog async queue.",
                    ),
                    meter_obj.create_observable_counter(
                        "microlog_dropped_records_total",
                        callbacks=[_counter_callback("dropped_records")],
                        description="Total records dropped by microlog queue policies.",
                    ),
                    meter_obj.create_observable_counter(
                        "microlog_shed_records_total",
                        callbacks=[_counter_callback("shed_records")],
                        description="Total records dropped by adaptive shedding policy.",
                    ),
                    meter_obj.create_observable_gauge(
                        "microlog_queue_size",
                        callbacks=[_gauge_callback("queue_size")],
                        description="Current microlog async queue depth.",
                    ),
                    meter_obj.create_observable_gauge(
                        "microlog_queue_maxsize",
                        callbacks=[_gauge_callback("queue_maxsize")],
                        description="Configured microlog async queue capacity.",
                    ),
                ]
            )
        _runtime_metrics_attrs = attrs
        _runtime_metrics_enabled = True
    return True


def disable_otel_runtime_metrics() -> None:
    global _runtime_metrics_enabled
    with _metrics_lock:
        _runtime_metrics_enabled = False


def _isoformat(ts: float, use_utc: bool) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc if use_utc else None)
    text = dt.isoformat()
    return text.replace("+00:00", "Z") if use_utc and text.endswith("+00:00") else text


def _resolve_level(level: str | int | None) -> int | None:
    if level is None:
        return None
    if isinstance(level, int):
        return level
    try:
        return int(level)
    except (TypeError, ValueError):
        pass
    resolved = getattr(logging, str(level).upper(), None)
    if isinstance(resolved, int):
        return resolved
    raise ValueError(f"Unknown log level: {level}")


def _compile_patterns(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    return tuple(compiled)


def _scrub(value: Any, keys: set[str], patterns: tuple[re.Pattern[str], ...]) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        return {
            key: "***" if str(key).lower() in keys else _scrub(item, keys, patterns)
            for key, item in mapping.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scrub(item, keys, patterns) for item in cast(Iterable[Any], value)]
    if isinstance(value, str):
        for pattern in patterns:
            value = pattern.sub("***", value)
    return value


def _json_dumps(payload: dict[str, Any], indent: int | None) -> str:
    if _ORJSON is not None:
        try:
            if indent is None:
                return _ORJSON.dumps(payload, default=str).decode("utf-8")
            if indent == 2:
                return _ORJSON.dumps(payload, default=str, option=_ORJSON.OPT_INDENT_2).decode(
                    "utf-8"
                )
        except Exception:
            pass
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":") if indent is None else None,
        indent=indent,
        default=str,
    )


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


class JsonFormatter(logging.Formatter):
    def __init__(self, cfg: LogConfig):
        super().__init__()
        self._cfg = cfg
        self._host = socket.gethostname() if cfg.include_host else None
        self._pid = os.getpid() if cfg.include_pid else None
        self._keys = {key.lower() for key in cfg.redact_keys}
        self._patterns = _compile_patterns(cfg.redact_value_patterns)

    def format(self, record: logging.LogRecord) -> str:
        cfg = self._cfg
        payload: dict[str, Any] = {
            "time": _isoformat(record.created, cfg.utc),
            "severity_text": record.levelname,
            "severity_number": severity_number(record.levelno),
            "body": record.getMessage(),
            "service.name": cfg.service_name,
        }

        if cfg.include_logger_name:
            payload["logger.name"] = record.name
        if cfg.service_version:
            payload["service.version"] = cfg.service_version
        if cfg.environment:
            payload["deployment.environment"] = cfg.environment
        if self._host is not None:
            payload["host.name"] = self._host
        if self._pid is not None:
            payload["process.pid"] = self._pid
        if cfg.include_thread:
            payload["thread.name"] = record.threadName
        if cfg.include_code:
            payload.update(
                {
                    "code.file.path": record.pathname,
                    "code.function.name": record.funcName,
                    "code.line.number": record.lineno,
                }
            )

        payload.update(_extract_otel_context(record, cfg.try_opentelemetry))
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _SKIP_EXTRA_KEYS}
        )

        if record.exc_info:
            etype, evalue, etb = record.exc_info
            payload["exception.type"] = getattr(etype, "__name__", str(etype))
            payload["exception.message"] = str(evalue)
            payload["exception.stacktrace"] = "".join(
                traceback.format_exception(etype, evalue, etb)
            ).strip()
        elif record.stack_info:
            payload["stack"] = str(record.stack_info)

        if self._keys or self._patterns:
            payload = cast(dict[str, Any], _scrub(payload, self._keys, self._patterns))

        return _json_dumps(
            {key: value for key, value in payload.items() if value is not None},
            cfg.json_indent,
        )


class DevColorFormatter(logging.Formatter):
    _LEVEL_COLORS = {
        logging.DEBUG: 36,
        logging.INFO: 32,
        logging.WARNING: 33,
        logging.ERROR: 31,
        logging.CRITICAL: 35,
    }

    def __init__(self, cfg: LogConfig):
        super().__init__()
        self._cfg = cfg
        self._patterns = _compile_patterns(cfg.redact_value_patterns)

    def format(self, record: logging.LogRecord) -> str:
        cfg = self._cfg
        color = self._LEVEL_COLORS.get(record.levelno, 37)
        name = record.name if cfg.include_logger_name else cfg.service_name
        msg = cast(str, _scrub(record.getMessage(), set(), self._patterns))
        location = (
            f" [{os.path.basename(record.pathname)}:{record.lineno} {record.funcName}()]"
            if cfg.include_code
            else ""
        )
        line = (
            f"{_isoformat(record.created, cfg.utc)} "
            f"\x1b[{color}m{record.levelname}\x1b[0m {name} - {msg}{location}"
        )
        ctx = _extract_otel_context(record, cfg.try_opentelemetry)
        if trace_id := ctx.get("trace_id"):
            line += f" (trace_id={trace_id} span_id={ctx.get('span_id')})"
        return line


class _BoundedQueueHandler(QueueHandler):
    def __init__(
        self,
        queue: Queue[logging.LogRecord],
        drop_oldest: bool,
        *,
        shed_below_level: int | None = None,
        shed_when_queue_above: float = 0.9,
        shed_rate: float = 1.0,
    ):
        super().__init__(queue)
        self._drop_oldest = drop_oldest
        self._shed_below_level = shed_below_level
        self._shed_when_queue_above = min(max(float(shed_when_queue_above), 0.0), 1.0)
        self._shed_rate = min(max(float(shed_rate), 0.0), 1.0)
        self._shed_every = (
            1
            if self._shed_rate <= 0.0 or self._shed_rate >= 1.0
            else max(1, int(round(1.0 / self._shed_rate)))
        )
        self._shed_counter = 0

    def _should_shed(self, queue: Queue[logging.LogRecord], record: logging.LogRecord) -> bool:
        if self._shed_below_level is None or self._shed_rate >= 1.0:
            return False
        if queue.maxsize <= 0:
            return False
        if record.levelno >= self._shed_below_level:
            return False
        try:
            fill_ratio = queue.qsize() / queue.maxsize
        except Exception:
            return False
        if fill_ratio < self._shed_when_queue_above:
            return False
        if self._shed_rate <= 0.0:
            return True
        self._shed_counter += 1
        return (self._shed_counter % self._shed_every) != 0

    def enqueue(self, record: logging.LogRecord) -> None:
        queue = cast(Queue[logging.LogRecord], self.queue)
        if self._should_shed(queue, record):
            _inc_stat("dropped_records")
            _inc_stat("shed_records")
            return
        try:
            queue.put_nowait(record)
            _inc_stat("queued_records")
            return
        except Full:
            if not self._drop_oldest:
                _inc_stat("dropped_records")
                return
        try:
            queue.get_nowait()
            _inc_stat("dropped_records")
            _inc_stat("dropped_oldest_records")
            queue.put_nowait(record)
            _inc_stat("queued_records")
        except (Empty, Full):
            _inc_stat("dropped_records")


def _stop_listener() -> None:
    global _listener, _active_queue
    if _listener is None:
        _active_queue = None
        return
    try:
        _listener.stop()
    except Exception:
        pass
    _listener = None
    _active_queue = None


atexit.register(_stop_listener)


def _apply_format(handler: logging.Handler, cfg: LogConfig) -> None:
    use_color = False
    if cfg.dev_color and (stream := getattr(handler, "stream", None)):
        try:
            use_color = bool(getattr(stream, "isatty", lambda: False)())
        except Exception:
            use_color = False
    handler.setFormatter(DevColorFormatter(cfg) if use_color else JsonFormatter(cfg))


def _stdout_handler(cfg: LogConfig, stdout_cfg: StdoutConfig) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    if (level := _resolve_level(stdout_cfg.level)) is not None:
        handler.setLevel(level)
    _apply_format(handler, cfg)
    return handler


def _file_handler(cfg: LogConfig, file_cfg: FileConfig) -> logging.Handler:
    path = Path(file_cfg.path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(f"Unable to create log directory '{path.parent}': {exc}") from exc

    handler = RotatingFileHandler(
        str(path),
        maxBytes=file_cfg.rotate_bytes or 0,
        backupCount=file_cfg.rotate_backups,
        encoding="utf-8",
    )
    if (level := _resolve_level(file_cfg.level)) is not None:
        handler.setLevel(level)
    _apply_format(handler, cfg)
    return handler


def _normalize_protocol(protocol: str) -> str:
    if value := _PROTOCOL_ALIASES.get((protocol or "").strip().lower()):
        return value
    raise ValueError(f"Unsupported OTLP protocol '{protocol}'. Expected 'http/protobuf' or 'grpc'.")


def _resolve_otlp_endpoint(protocol: str, otlp_cfg: OTLPConfig) -> str:
    return (
        otlp_cfg.endpoint
        or os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ("http://localhost:4318/v1/logs" if protocol == "http/protobuf" else "localhost:4317")
    )


def _build_otlp_exporter(
    protocol: str, otlp_cfg: OTLPConfig
) -> tuple[Any, Callable[[], None] | None]:
    endpoint = _resolve_otlp_endpoint(protocol, otlp_cfg)
    headers = dict(otlp_cfg.headers) if otlp_cfg.headers else None
    timeout = float(otlp_cfg.timeout) if otlp_cfg.timeout is not None else None

    if protocol == "http/protobuf":
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        except Exception as exc:  # pragma: no cover - optional dependency handling
            raise RuntimeError(
                "OTLP HTTP exporter requested, but opentelemetry-exporter-otlp-proto-http is not installed."
            ) from exc

        kwargs: dict[str, Any] = {"endpoint": endpoint}
        if headers:
            kwargs["headers"] = headers
        if otlp_cfg.compression:
            kwargs["compression"] = otlp_cfg.compression
        if timeout is not None:
            kwargs["timeout"] = timeout

        cleanup: Callable[[], None] | None = None
        if otlp_cfg.insecure:
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

    kwargs: dict[str, Any] = {"endpoint": endpoint, "insecure": otlp_cfg.insecure}
    if headers:
        kwargs["headers"] = headers
    if otlp_cfg.compression:
        kwargs["compression"] = otlp_cfg.compression
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OTLPLogExporter(**kwargs), None


def _otlp_resource_attributes(cfg: LogConfig, otlp_cfg: OTLPConfig) -> dict[str, Any]:
    attrs: dict[str, Any] = {"service.name": cfg.service_name}
    if cfg.service_version:
        attrs["service.version"] = cfg.service_version
    if cfg.environment:
        attrs["deployment.environment"] = cfg.environment
    attrs.update(otlp_cfg.resource_attributes)
    return attrs


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

    provider = LoggerProvider(resource=Resource.create(_otlp_resource_attributes(cfg, otlp_cfg)))
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    handler = LoggingHandler(
        level=_resolve_level(otlp_cfg.level) or logging.NOTSET, logger_provider=provider
    )
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


def _build_handlers(cfg: LogConfig) -> list[logging.Handler]:
    handlers = [
        handler
        for handler in (
            _stdout_handler(cfg, cfg.stdout) if cfg.stdout else None,
            _file_handler(cfg, cfg.file) if cfg.file else None,
        )
        if handler is not None
    ]
    if cfg.otlp:
        try:
            handlers.append(_otlp_handler(cfg, cfg.otlp))
        except Exception as exc:
            if not cfg.otlp_fail_open:
                raise
            warnings.warn(
                f"OTLP handler setup failed, continuing without OTLP sink: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    if not handlers:
        raise ValueError("At least one of stdout, file, or otlp logging must be configured.")
    return handlers


def configure_logging(cfg: LogConfig) -> None:
    global _listener, _active_queue

    root = logging.getLogger()
    root.setLevel(_resolve_level(cfg.level) or logging.INFO)
    _active_queue = None

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    _stop_listener()
    handlers = _build_handlers(cfg)

    if cfg.async_mode:
        queue: Queue[logging.LogRecord] = Queue(max(0, cfg.async_queue_size))
        root.addHandler(
            _BoundedQueueHandler(
                queue,
                cfg.async_queue_drop_oldest,
                shed_below_level=_resolve_level(cfg.shed_below_level),
                shed_when_queue_above=cfg.shed_when_queue_above,
                shed_rate=cfg.shed_rate,
            )
        )
        _active_queue = queue
        _listener = QueueListener(queue, *handlers, respect_handler_level=True)
        _listener.start()
    else:
        _active_queue = None
        for handler in handlers:
            root.addHandler(handler)

    logging.captureWarnings(True)
