from __future__ import annotations

import atexit, json, logging, os, re, socket, sys, traceback
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable, Dict, Iterable, List, Optional, cast
from types import MethodType
from .config import FileConfig, LogConfig, OTLPConfig, StdoutConfig, severity_number

_SKIP_EXTRA_KEYS = {
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


def _isoformat(ts: float, use_utc: bool) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc if use_utc else None)
    s = dt.isoformat()
    return s.replace("+00:00", "Z") if use_utc and s.endswith("+00:00") else s


def _extract_otel_context(record: logging.LogRecord, cfg: LogConfig) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}
    for attr, (key, width) in _OTEL_ATTR_MAP.items():
        value = getattr(record, attr, None)
        if value:
            ctx[key] = f"{value:0{width}x}" if width and isinstance(value, int) else str(value)
    sampled = getattr(record, "otelTraceSampled", None)
    if sampled is not None:
        ctx["trace_sampled"] = bool(sampled)
    if cfg.try_opentelemetry and not (ctx.get("trace_id") and ctx.get("span_id")):
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
                flags = int(getattr(sc, "trace_flags", 0))
                ctx["trace_sampled"] = bool(flags & 0x01)
        except Exception:
            pass
    return ctx


def _scrub_value(value: Any, keys: set[str], patterns: list[re.Pattern[str]]) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if key.lower() in keys else _scrub_value(val, keys, patterns)
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scrub_value(item, keys, patterns) for item in value]
    if isinstance(value, str) and patterns:
        for pattern in patterns:
            value = pattern.sub("***", value)
        return value
    return value


def _json_default(value: Any) -> str:
    return str(value)


def _compile_patterns(patterns: Iterable[str]) -> List[re.Pattern[str]]:
    compiled: List[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    return compiled


class JsonFormatter(logging.Formatter):
    def __init__(self, cfg: LogConfig):
        super().__init__()
        self.cfg = cfg
        self.hostname = socket.gethostname() if cfg.include_host else None
        self.pid = os.getpid() if cfg.include_pid else None
        self._keys = {k.lower() for k in cfg.redact_keys}
        self._patterns = _compile_patterns(cfg.redact_value_patterns)

    def format(self, record: logging.LogRecord) -> str:
        cfg = self.cfg
        out: Dict[str, Any] = {
            "time": _isoformat(record.created, cfg.utc),
            "severity_text": record.levelname,
            "severity_number": severity_number(record.levelno),
            "body": record.getMessage(),
            "service.name": cfg.service_name,
        }
        if cfg.include_logger_name:
            out["logger.name"] = record.name
        for key, value in (
            ("service.version", cfg.service_version),
            ("deployment.environment", cfg.environment),
            ("host.name", self.hostname),
            ("process.pid", self.pid),
        ):
            if value is not None:
                out[key] = value
        if cfg.include_thread:
            out["thread.name"] = record.threadName
        if cfg.include_code:
            out.update(
                {
                    "code.file.path": record.pathname,
                    "code.function.name": record.funcName,
                    "code.line.number": record.lineno,
                }
            )
        out.update(_extract_otel_context(record, cfg))
        extras = {k: v for k, v in record.__dict__.items() if k not in _SKIP_EXTRA_KEYS}
        if extras:
            out.update(extras)
        if record.exc_info:
            etype, evalue, etb = record.exc_info
            out["exception.type"] = getattr(etype, "__name__", str(etype))
            out["exception.message"] = str(evalue)
            out["exception.stacktrace"] = "".join(
                traceback.format_exception(etype, evalue, etb)
            ).strip()
        elif record.stack_info:
            out["stack"] = str(record.stack_info)
        scrubbed = _scrub_value(out, self._keys, self._patterns)
        return json.dumps(
            {k: v for k, v in scrubbed.items() if v is not None},
            ensure_ascii=False,
            separators=(",", ":"),
            indent=cfg.json_indent,
            default=_json_default,
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
        self.cfg = cfg
        self._patterns = _compile_patterns(cfg.redact_value_patterns)

    def format(self, record: logging.LogRecord) -> str:
        ts = _isoformat(record.created, self.cfg.utc)
        color = self._LEVEL_COLORS.get(record.levelno, 37)
        loc = f"{os.path.basename(record.pathname)}:{record.lineno} {record.funcName}()"
        msg = _scrub_value(record.getMessage(), set(), self._patterns)
        name = record.name if self.cfg.include_logger_name else self.cfg.service_name
        parts = [f"{ts} \x1b[{color}m{record.levelname}\x1b[0m {name} - {msg} [{loc}]"]
        ctx = _extract_otel_context(record, self.cfg)
        if ctx.get("trace_id"):
            parts.append(f"(trace_id={ctx['trace_id']} span_id={ctx.get('span_id')})")
        return " ".join(parts)


class _BoundedQueueHandler(QueueHandler):
    """QueueHandler that applies a bounded queue with drop policies."""

    def __init__(self, queue: Queue[logging.LogRecord], drop_oldest: bool):
        super().__init__(queue)
        self._drop_oldest = drop_oldest

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except Full:
            if self._drop_oldest:
                try:
                    self.queue.get_nowait()
                except Empty:
                    pass
                try:
                    self.queue.put_nowait(record)
                except Full:
                    pass
            # Drop newest if not removing oldest; swallow the exception to avoid spamming stderr


_listener: Optional[QueueListener] = None


def _stop_listener() -> None:
    global _listener
    if _listener:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None


atexit.register(_stop_listener)


def _apply_format(handler: logging.Handler, cfg: LogConfig) -> None:
    stream = getattr(handler, "stream", None)
    use_color = False
    if stream and cfg.dev_color:
        try:
            use_color = bool(getattr(stream, "isatty", lambda: False)())
        except Exception:
            pass
    handler.setFormatter(DevColorFormatter(cfg) if use_color else JsonFormatter(cfg))


def _resolve_level(level: Optional[str]) -> Optional[int]:
    if level is None:
        return None
    try:
        return int(level)
    except (TypeError, ValueError):
        pass
    numeric = getattr(logging, str(level).upper(), None)
    if isinstance(numeric, int):
        return numeric
    raise ValueError(f"Unknown log level: {level}")


def _stdout_handler(cfg: LogConfig, stdout_cfg: StdoutConfig) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    if (resolved := _resolve_level(stdout_cfg.level)) is not None:
        handler.setLevel(resolved)
    _apply_format(handler, cfg)
    return handler


def _file_handler(cfg: LogConfig, file_cfg: FileConfig) -> logging.Handler:
    log_path = Path(file_cfg.path).expanduser()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(f"Unable to create log directory '{log_path.parent}': {exc}") from exc
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=file_cfg.rotate_bytes or 0,
        backupCount=file_cfg.rotate_backups,
        encoding="utf-8",
    )
    if (resolved := _resolve_level(file_cfg.level)) is not None:
        handler.setLevel(resolved)
    _apply_format(handler, cfg)
    return handler


def _normalize_protocol(protocol: str) -> str:
    value = (protocol or "").strip().lower()
    if value in {"http", "http/protobuf", "http_protobuf", "http-protobuf"}:
        return "http/protobuf"
    if value in {"grpc", "grpc/protobuf", "grpc_proto", "grpc-protobuf"}:
        return "grpc"
    raise ValueError(f"Unsupported OTLP protocol '{protocol}'. Expected 'http/protobuf' or 'grpc'.")


def _resolve_otlp_endpoint(protocol: str, otlp_cfg: OTLPConfig) -> str:
    if otlp_cfg.endpoint:
        return otlp_cfg.endpoint
    env = os.getenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    if env:
        return env
    env = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if env:
        return env
    return "http://localhost:4318/v1/logs" if protocol == "http/protobuf" else "localhost:4317"


def _build_otlp_exporter(
    protocol: str, otlp_cfg: OTLPConfig
) -> tuple[Any, Optional[Callable[[], None]]]:
    endpoint = _resolve_otlp_endpoint(protocol, otlp_cfg)
    headers = dict(otlp_cfg.headers) if otlp_cfg.headers else None
    compression = otlp_cfg.compression
    timeout = otlp_cfg.timeout
    cleanup: Optional[Callable[[], None]] = None
    if protocol == "http/protobuf":
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        except Exception as exc:  # pragma: no cover - optional dependency handling
            raise RuntimeError(
                "OTLP HTTP exporter requested, but opentelemetry-exporter-otlp-proto-http is not installed."
            ) from exc
        kwargs: Dict[str, Any] = {"endpoint": endpoint}
        if headers:
            kwargs["headers"] = headers
        if compression:
            kwargs["compression"] = compression
        if timeout is not None:
            kwargs["timeout"] = float(timeout)
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
        exporter = OTLPLogExporter(**kwargs)
        return exporter, cleanup
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    except Exception as exc:  # pragma: no cover - optional dependency handling
        raise RuntimeError(
            "OTLP gRPC exporter requested, but opentelemetry-exporter-otlp-proto-grpc is not installed."
        ) from exc
    kwargs = {"endpoint": endpoint, "insecure": otlp_cfg.insecure}
    if headers:
        kwargs["headers"] = headers
    if compression:
        kwargs["compression"] = compression
    if timeout is not None:
        kwargs["timeout"] = float(timeout)
    exporter = OTLPLogExporter(**kwargs)
    return exporter, None


def _otlp_resource_attributes(cfg: LogConfig, otlp_cfg: OTLPConfig) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {"service.name": cfg.service_name}
    if cfg.service_version:
        attrs["service.version"] = cfg.service_version
    if cfg.environment:
        attrs["deployment.environment"] = cfg.environment
    attrs.update(otlp_cfg.resource_attributes)
    return attrs


def _otlp_handler(cfg: LogConfig, otlp_cfg: OTLPConfig) -> logging.Handler:
    protocol = _normalize_protocol(otlp_cfg.protocol)
    exporter, exporter_cleanup = _build_otlp_exporter(protocol, otlp_cfg)
    try:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
    except Exception as exc:
        raise RuntimeError(
            "OTLP logging requested, but the opentelemetry-sdk package is not installed."
        ) from exc
    resource = Resource.create(_otlp_resource_attributes(cfg, otlp_cfg))
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    level = _resolve_level(otlp_cfg.level) or logging.NOTSET
    handler = LoggingHandler(level=level, logger_provider=provider)
    original_close = handler.close

    def _safe_close(self: logging.Handler) -> None:
        try:
            original_close()
        except Exception:
            pass
        try:
            provider.shutdown()
        except Exception:
            pass
        if exporter_cleanup:
            try:
                exporter_cleanup()
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
            _otlp_handler(cfg, cfg.otlp) if cfg.otlp else None,
        )
        if handler is not None
    ]
    if not handlers:
        raise ValueError("At least one of stdout, file, or otlp logging must be configured.")
    return handlers


def configure_logging(cfg: LogConfig) -> None:
    global _listener
    root = logging.getLogger()
    root_level = _resolve_level(cfg.level)
    root.setLevel(root_level if root_level is not None else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _stop_listener()
    handlers = _build_handlers(cfg)
    if cfg.async_mode:
        maxsize = cfg.async_queue_size if cfg.async_queue_size > 0 else 0
        q: Queue[logging.LogRecord] = Queue(maxsize)
        if maxsize > 0:
            queue_handler: QueueHandler = _BoundedQueueHandler(
                q, drop_oldest=cfg.async_queue_drop_oldest
            )
        else:
            queue_handler = QueueHandler(q)
        root.addHandler(queue_handler)
        _listener = QueueListener(q, *handlers, respect_handler_level=True)
        _listener.start()
    else:
        for h in handlers:
            root.addHandler(h)
    logging.captureWarnings(True)
