from __future__ import annotations

import json
import logging
import traceback
from typing import Any

_COMPACT_JSON_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=str)

from . import otel as _otel
from .config import LogConfig, MICROLOG_FIELDS, safe_fields, service_fields, severity_number


def base_payload_fields(
    cfg: LogConfig,
    *,
    host_name: str | None = None,
    process_id: int | None = None,
) -> dict[str, Any]:
    base = service_fields(cfg, include_static=True)
    if host_name is not None:
        base["host.name"] = host_name
    if process_id is not None:
        base["process.pid"] = process_id
    return base


def json_dumps(
    payload: dict[str, Any],
    indent: int | None,
    *,
    orjson_impl: Any | None = None,
) -> str:
    if orjson_impl is not None:
        try:
            if indent is None:
                return orjson_impl.dumps(payload, default=str).decode("utf-8")
            if indent == 2:
                return orjson_impl.dumps(
                    payload,
                    default=str,
                    option=orjson_impl.OPT_INDENT_2,
                ).decode("utf-8")
        except Exception:
            pass
    if indent is None:
        return _COMPACT_JSON_ENCODER.encode(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        default=str,
    )


def _record_fields(record: logging.LogRecord, cfg: LogConfig, *, timestamp: str) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "time": timestamp,
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


def _record_payload(record: logging.LogRecord, skip_extra_keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if isinstance(payload := getattr(record, MICROLOG_FIELDS, None), dict):
        return safe_fields(payload)
    return safe_fields(
        {key: value for key, value in record.__dict__.items() if key not in skip_extra_keys}
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


def event_payload(
    record: logging.LogRecord,
    cfg: LogConfig,
    *,
    base_fields: dict[str, Any],
    timestamp: str,
    skip_extra_keys: set[str] | frozenset[str],
) -> dict[str, Any]:
    payload = dict(base_fields)
    payload["time"] = timestamp
    payload["severity_text"] = record.levelname
    payload["severity_number"] = severity_number(record.levelno)
    payload["body"] = record.getMessage()
    needs_none_filter = False

    if cfg.include_logger_name:
        payload["logger.name"] = record.name
    if cfg.include_thread:
        payload["thread.name"] = record.threadName
    if cfg.include_code:
        payload["code.file.path"] = record.pathname
        payload["code.function.name"] = record.funcName
        payload["code.line.number"] = record.lineno

    record_attrs = record.__dict__
    if cfg.try_opentelemetry or (
        "otelSpanID" in record_attrs
        or "otelTraceID" in record_attrs
        or "otelServiceName" in record_attrs
        or "otelTraceSampled" in record_attrs
    ):
        otel_fields = _otel._extract_otel_context(record, cfg.try_opentelemetry)
        if otel_fields:
            payload.update(otel_fields)
            needs_none_filter = None in otel_fields.values()

    record_payload = getattr(record, MICROLOG_FIELDS, None)
    extra_fields = (
        safe_fields(record_payload)
        if isinstance(record_payload, dict)
        else safe_fields({key: value for key, value in record.__dict__.items() if key not in skip_extra_keys})
    )
    if extra_fields:
        payload.update(extra_fields)
        needs_none_filter = needs_none_filter or (None in extra_fields.values())

    if record.exc_info:
        etype, evalue, etb = record.exc_info
        payload["exception.type"] = getattr(etype, "__name__", str(etype))
        payload["exception.message"] = str(evalue)
        payload["exception.stacktrace"] = "".join(
            traceback.format_exception(etype, evalue, etb)
        ).strip()
    elif record.stack_info:
        payload["stack"] = str(record.stack_info)

    if not needs_none_filter:
        return payload
    return {key: value for key, value in payload.items() if value is not None}
