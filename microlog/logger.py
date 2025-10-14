"""Core logging setup and formatters for microlog.

Implements JsonFormatter (OTel‑friendly) and DevColorFormatter (human‑readable),
plus configure_logging() to wire handlers as per LogConfig.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import socket
import sys
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Optional, cast
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


def _is_tty(stream: Any) -> bool:
    try:
        return hasattr(stream, "isatty") and stream.isatty()
    except Exception:
        return False


def _isoformat(ts: float, use_utc: bool) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc if use_utc else None)
    s = dt.isoformat()
    return s.replace("+00:00", "Z") if use_utc and s.endswith("+00:00") else s


def _format_hex(value: Any, width: int) -> str:
    return f"{value:0{width}x}" if isinstance(value, int) else str(value)


def _extract_otel_context(record: logging.LogRecord, cfg: LogConfig) -> Dict[str, Any]:
    """Pull trace/span fields from OTel‑instrumented records:contentReference[oaicite:8]{index=8}."""
    context: Dict[str, Any] = {}
    for attr, (key, width) in _OTEL_ATTR_MAP.items():
        value = getattr(record, attr, None)
        if value:
            context[key] = _format_hex(value, width) if width else str(value)
    sampled = getattr(record, "otelTraceSampled", None)
    if sampled is not None:
        context["trace_sampled"] = bool(sampled)
    if cfg.try_opentelemetry and not (context.get("trace_id") and context.get("span_id")):
        try:
            from opentelemetry.trace import get_current_span  # type: ignore[import-not-found]

            span = cast(Any, get_current_span())
            get_context = getattr(span, "get_span_context", None)
            sc = get_context() if callable(get_context) else None
            if sc and getattr(sc, "is_valid", False):
                trace_id = getattr(sc, "trace_id", None)
                span_id = getattr(sc, "span_id", None)
                if trace_id and "trace_id" not in context:
                    context["trace_id"] = (
                        f"{trace_id:032x}" if isinstance(trace_id, int) else str(trace_id)
                    )
                if span_id and "span_id" not in context:
                    context["span_id"] = (
                        f"{span_id:016x}" if isinstance(span_id, int) else str(span_id)
                    )
                flags = int(getattr(sc, "trace_flags", 0))
                context["trace_sampled"] = bool(int(flags) & 0x01)
        except Exception:
            pass
    return context


def _redact(value: Any, cfg: LogConfig) -> Any:
    """Shallow redaction for sensitive keys and regex‑matched values:contentReference[oaicite:9]{index=9}."""
    if not isinstance(value, dict):
        return value
    keys = {k.lower() for k in cfg.redact_keys}
    patterns: list[re.Pattern[str]] = []
    for pattern in cfg.redact_value_patterns:
        try:
            patterns.append(re.compile(pattern))
        except re.error:
            continue

    def _scrub(text: str) -> str:
        for pattern in patterns:
            text = pattern.sub("***", text)
        return text

    return {
        key: "***"
        if key.lower() in keys
        else _scrub(val)
        if isinstance(val, str) and patterns
        else val
        for key, val in cast(Dict[str, Any], value).items()
    }


class JsonFormatter(logging.Formatter):
    """Format records as OpenTelemetry‑compatible JSON."""

    def __init__(self, cfg: LogConfig):
        super().__init__()
        self.cfg = cfg
        self.hostname = socket.gethostname() if cfg.include_host else None
        self.pid = os.getpid() if cfg.include_pid else None

    def format(self, record: logging.LogRecord) -> str:
        cfg = self.cfg
        out: Dict[str, Any] = {
            "time": _isoformat(record.created, cfg.utc),
            "severity_text": record.levelname,
            "severity_number": severity_number(record.levelno),
            "body": record.getMessage(),
            "service.name": cfg.service_name,
        }
        out.update(
            {
                key: value
                for key, value in {
                    "service.version": cfg.service_version,
                    "deployment.environment": cfg.environment,
                    "host.name": self.hostname,
                    "process.pid": self.pid,
                }.items()
                if value is not None
            }
        )
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
        # OTel context
        out.update(_extract_otel_context(record, cfg))
        # Merge extras & redact
        extras = {k: v for k, v in record.__dict__.items() if k not in _SKIP_EXTRA_KEYS}
        if extras:
            out.update(_redact(extras, cfg))
        if record.exc_info:
            etype, evalue, etb = record.exc_info
            out["exception.type"] = getattr(etype, "__name__", str(etype))
            out["exception.message"] = str(evalue)
            out["exception.stacktrace"] = "".join(
                traceback.format_exception(etype, evalue, etb)
            ).strip()
        elif record.stack_info:
            out["stack"] = str(record.stack_info)
        return json.dumps(
            {k: v for k, v in out.items() if v is not None},
            ensure_ascii=False,
            separators=(",", ":"),
            indent=cfg.json_indent,
        )


class DevColorFormatter(logging.Formatter):
    """Colourised, single‑line formatter for development:contentReference[oaicite:10]{index=10}."""

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

    def format(self, record: logging.LogRecord) -> str:
        ts = _isoformat(record.created, self.cfg.utc)
        color = self._LEVEL_COLORS.get(record.levelno, 37)
        loc = f"{os.path.basename(record.pathname)}:{record.lineno} {record.funcName}()"
        msg = record.getMessage()
        parts = [f"{ts} \x1b[{color}m{record.levelname}\x1b[0m {record.name} - {msg} [{loc}]"]
        ctx = _extract_otel_context(record, self.cfg)
        if ctx.get("trace_id"):
            parts.append(f"(trace_id={ctx['trace_id']} span_id={ctx.get('span_id')})")
        return " ".join(parts)


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
    use_color = bool(stream) and cfg.dev_color and _is_tty(stream)
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


def _build_otlp_exporter(protocol: str, otlp_cfg: OTLPConfig):
    endpoint = _resolve_otlp_endpoint(protocol, otlp_cfg)
    headers = dict(otlp_cfg.headers) if otlp_cfg.headers else None
    compression = otlp_cfg.compression
    timeout = otlp_cfg.timeout
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
        return OTLPLogExporter(**kwargs)
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
    return OTLPLogExporter(**kwargs)


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
    exporter = _build_otlp_exporter(protocol, otlp_cfg)
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
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _stop_listener()
    handlers = _build_handlers(cfg)
    if cfg.async_mode:
        q: Queue[logging.LogRecord] = Queue(-1)
        root.addHandler(QueueHandler(q))
        _listener = QueueListener(q, *handlers, respect_handler_level=True)
        _listener.start()
    else:
        for h in handlers:
            root.addHandler(h)
    logging.captureWarnings(True)
