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
from typing import Any, Iterable, cast

from . import otel as _otel
from .config import (
    FileConfig,
    LogConfig,
    StdoutConfig,
    MICROLOG_FIELDS,
    resolve_level,
    safe_fields,
    service_fields,
    severity_number,
)

try:
    import orjson as _ORJSON
except Exception:  # pragma: no cover - optional dependency
    _ORJSON = None

_SKIP_EXTRA_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "otelSpanID",
    "otelTraceID",
    "otelServiceName",
    "otelTraceSampled",
    MICROLOG_FIELDS,
}

_listener: QueueListener | None = None
_active_queue: Queue[logging.LogRecord] | None = None
_listener_lock = Lock()
_stats_lock = Lock()
_stats: dict[str, int] = {
    "queued_records": 0,
    "dropped_records": 0,
    "dropped_oldest_records": 0,
    "shed_records": 0,
}


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
        for name in _stats:
            _stats[name] = 0


def _isoformat(ts: float, use_utc: bool) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc if use_utc else None)
    text = dt.isoformat()
    return text.replace("+00:00", "Z") if use_utc and text.endswith("+00:00") else text


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


def _record_fields(record: logging.LogRecord, cfg: LogConfig) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "time": _isoformat(record.created, cfg.utc),
        "severity_text": record.levelname,
        "severity_number": severity_number(record.levelno),
        "body": record.getMessage(),
    }
    if cfg.include_logger_name:
        fields["logger.name"] = record.name
    if cfg.include_thread:
        fields["thread.name"] = record.threadName
    if cfg.include_code:
        fields.update(
            {
                "code.file.path": record.pathname,
                "code.function.name": record.funcName,
                "code.line.number": record.lineno,
            }
        )
    return fields


def _record_payload(record: logging.LogRecord) -> dict[str, Any]:
    if isinstance(payload := getattr(record, MICROLOG_FIELDS, None), dict):
        return safe_fields(payload)
    return safe_fields(
        {key: value for key, value in record.__dict__.items() if key not in _SKIP_EXTRA_KEYS}
    )


def _error_fields(record: logging.LogRecord) -> dict[str, Any]:
    if record.exc_info:
        etype, evalue, etb = record.exc_info
        return {
            "exception.type": getattr(etype, "__name__", str(etype)),
            "exception.message": str(evalue),
            "exception.stacktrace": "".join(traceback.format_exception(etype, evalue, etb)).strip(),
        }
    return {"stack": str(record.stack_info)} if record.stack_info else {}


def _close_handlers(handlers: Iterable[logging.Handler]) -> None:
    for handler in handlers:
        try:
            handler.close()
        except Exception:
            pass


def _stop_queue_listener(listener: QueueListener | None) -> None:
    if listener is None:
        return
    try:
        listener.stop()
    except Exception:
        pass
    _close_handlers(cast(Iterable[logging.Handler], getattr(listener, "handlers", ())))


def _clear_listener_state(listener: QueueListener | None) -> None:
    global _listener, _active_queue
    if listener is None:
        return
    with _listener_lock:
        if _listener is listener:
            _listener = None
            _active_queue = None


def _event_payload(
    record: logging.LogRecord, cfg: LogConfig, base: dict[str, Any]
) -> dict[str, Any]:
    payload = dict(base)
    for fields in (
        _record_fields(record, cfg),
        _otel._extract_otel_context(record, cfg.try_opentelemetry),
        _record_payload(record),
        _error_fields(record),
    ):
        payload.update(fields)
    return {key: value for key, value in payload.items() if value is not None}


class JsonFormatter(logging.Formatter):
    def __init__(self, cfg: LogConfig):
        super().__init__()
        self._cfg = cfg
        self._base = service_fields(cfg, include_static=True)
        if cfg.include_host:
            self._base["host.name"] = socket.gethostname()
        if cfg.include_pid:
            self._base["process.pid"] = os.getpid()
        self._keys = {key.lower() for key in cfg.redact_keys}
        self._patterns = _compile_patterns(cfg.redact_value_patterns)
        self._should_scrub = bool(self._keys or self._patterns)

    def format(self, record: logging.LogRecord) -> str:
        payload = _event_payload(record, self._cfg, self._base)
        if self._should_scrub:
            payload = cast(dict[str, Any], _scrub(payload, self._keys, self._patterns))
        return _json_dumps(payload, self._cfg.json_indent)


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
        ctx = _otel._extract_otel_context(record, cfg.try_opentelemetry)
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
        self._listener: QueueListener | None = None
        self._listener_ref_lock = Lock()

    def attach_listener(self, listener: QueueListener) -> None:
        with self._listener_ref_lock:
            self._listener = listener

    def _take_listener(self) -> QueueListener | None:
        with self._listener_ref_lock:
            listener = self._listener
            self._listener = None
        return listener

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # The queue is process-local, so preserve exception and stack metadata for
        # the listener thread instead of letting QueueHandler flatten it into msg.
        return logging.makeLogRecord(record.__dict__.copy())

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

    def close(self) -> None:
        listener = self._take_listener()
        _clear_listener_state(listener)
        _stop_queue_listener(listener)
        super().close()


def _stop_listener() -> None:
    global _listener, _active_queue
    with _listener_lock:
        listener = _listener
        _listener = None
        _active_queue = None
    _stop_queue_listener(listener)


def shutdown_logging() -> None:
    _stop_listener()
    logging.shutdown()


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
    if (level := resolve_level(stdout_cfg.level)) is not None:
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
    if (level := resolve_level(file_cfg.level)) is not None:
        handler.setLevel(level)
    _apply_format(handler, cfg)
    return handler


def _replace_root_handlers(
    root: logging.Logger, level: int, handlers: Iterable[logging.Handler]
) -> None:
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)


def _build_handlers(cfg: LogConfig) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []
    try:
        if cfg.stdout:
            handlers.append(_stdout_handler(cfg, cfg.stdout))
        if cfg.file:
            handlers.append(_file_handler(cfg, cfg.file))
        if cfg.otlp:
            try:
                handlers.append(_otel._otlp_handler(cfg, cfg.otlp))
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
    except Exception:
        _close_handlers(handlers)
        raise


def configure_logging(cfg: LogConfig) -> None:
    global _listener, _active_queue

    root = logging.getLogger()
    resolved_root_level = resolve_level(cfg.level)
    root_level = logging.INFO if resolved_root_level is None else resolved_root_level
    shed_level = resolve_level(cfg.shed_below_level) if cfg.async_mode else None
    handlers = _build_handlers(cfg)
    root_handlers: list[logging.Handler] = handlers
    listener: QueueListener | None = None
    queue: Queue[logging.LogRecord] | None = None

    if cfg.async_mode:
        queue = Queue(max(0, cfg.async_queue_size))
        queue_handler = _BoundedQueueHandler(
            queue,
            cfg.async_queue_drop_oldest,
            shed_below_level=shed_level,
            shed_when_queue_above=cfg.shed_when_queue_above,
            shed_rate=cfg.shed_rate,
        )
        root_handlers = [queue_handler]
        listener = QueueListener(queue, *handlers, respect_handler_level=True)
        queue_handler.attach_listener(listener)
        try:
            listener.start()
        except Exception:
            _close_handlers(root_handlers)
            _stop_queue_listener(listener)
            _close_handlers(handlers)
            raise

    with _listener_lock:
        old_level = root.level
        old_handlers = list(root.handlers)
        old_listener = _listener
        old_queue = _active_queue
        try:
            _replace_root_handlers(root, root_level, root_handlers)
            _listener = listener
            _active_queue = queue
        except Exception:
            _replace_root_handlers(root, old_level, old_handlers)
            _listener = old_listener
            _active_queue = old_queue
            _close_handlers(root_handlers)
            _stop_queue_listener(listener)
            _close_handlers(handlers)
            raise

    _stop_queue_listener(old_listener)
    _close_handlers(old_handlers)

    logging.captureWarnings(True)
