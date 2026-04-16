from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping

MICROLOG_FIELDS = "_microlog"
SCHEMA_VERSION = "1"
DEFAULT_REDACT_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
    }
)
RESERVED_FIELDS = frozenset(
    {
        "schema_version",
        "time",
        "severity_text",
        "severity_number",
        "body",
        "service.name",
        "service.version",
        "deployment.environment",
        "logger.name",
        "thread.name",
        "host.name",
        "process.pid",
        "code.file.path",
        "code.function.name",
        "code.line.number",
        "trace_id",
        "span_id",
        "trace_sampled",
        "exception.type",
        "exception.message",
        "exception.stacktrace",
        "stack",
        MICROLOG_FIELDS,
    }
)


def _redact_keys() -> set[str]:
    return set(DEFAULT_REDACT_KEYS)


def safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    if not fields:
        return {}
    for key in fields:
        if key in RESERVED_FIELDS:
            return {key: value for key, value in fields.items() if key not in RESERVED_FIELDS}
    return dict(fields)


@dataclass(frozen=True, slots=True)
class StdoutConfig:
    level: str | int | None = None


@dataclass(frozen=True, slots=True)
class FileConfig:
    path: str
    rotate_bytes: int | None = None
    rotate_backups: int = 5
    level: str | int | None = None


@dataclass(frozen=True, slots=True)
class OTLPConfig:
    protocol: str = "http/protobuf"
    endpoint: str | None = None
    insecure: bool | None = None
    headers: dict[str, str] = field(default_factory=dict)
    compression: str | None = None
    timeout: float | None = None
    level: str | int | None = None
    resource_attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("headers", "resource_attributes"):
            object.__setattr__(self, name, dict(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class LogConfig:
    service_name: str = "app"
    service_version: str | None = None
    environment: str | None = None
    stdout: StdoutConfig | None = field(default_factory=StdoutConfig)
    file: FileConfig | None = None
    otlp: OTLPConfig | None = None
    otlp_fail_open: bool = True
    level: str | int = "INFO"
    utc: bool = True
    async_mode: bool = True
    async_queue_size: int = 10000
    async_queue_drop_oldest: bool = True
    shed_below_level: str | int | None = None
    shed_when_queue_above: float = 0.9
    shed_rate: float = 1.0
    json_indent: int | None = None
    dev_color: bool = False
    include_logger_name: bool = True
    include_thread: bool = False
    include_pid: bool = True
    include_host: bool = True
    include_code: bool = True
    try_opentelemetry: bool = True
    static: dict[str, Any] = field(default_factory=dict)
    redact_keys: set[str] = field(default_factory=_redact_keys)
    redact_value_patterns: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name, copy in (
            ("static", dict),
            ("redact_keys", set),
            ("redact_value_patterns", list),
        ):
            object.__setattr__(self, name, copy(getattr(self, name)))


def service_fields(cfg: LogConfig, *, include_static: bool = False) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "service.name": cfg.service_name,
    }
    if cfg.service_version:
        fields["service.version"] = cfg.service_version
    if cfg.environment:
        fields["deployment.environment"] = cfg.environment
    if include_static and cfg.static:
        fields.update(safe_fields(cfg.static))
    return fields


def resolve_level(level: str | int | None) -> int | None:
    if level is None:
        return None
    if isinstance(level, int):
        return level
    try:
        return int(level)
    except (TypeError, ValueError):
        pass
    if isinstance(resolved := getattr(logging, str(level).upper(), None), int):
        return resolved
    raise ValueError(f"Unknown log level: {level}")


def severity_number(levelno: int) -> int:
    if levelno <= 0:
        return 1
    if levelno < 20:
        return 5
    if levelno < 30:
        return 9
    if levelno < 40:
        return 13
    if levelno < 50:
        return 17
    return 21
