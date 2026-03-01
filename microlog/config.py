from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


def _redact_keys() -> set[str]:
    return set(DEFAULT_REDACT_KEYS)


def _str_list() -> list[str]:
    return []


def _str_map() -> dict[str, str]:
    return {}


def _any_map() -> dict[str, Any]:
    return {}


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
    insecure: bool = True
    headers: dict[str, str] = field(default_factory=_str_map)
    compression: str | None = None
    timeout: float | None = None
    level: str | int | None = None
    resource_attributes: dict[str, Any] = field(default_factory=_any_map)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", dict(self.headers))
        object.__setattr__(self, "resource_attributes", dict(self.resource_attributes))


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
    static: dict[str, Any] = field(default_factory=_any_map)
    redact_keys: set[str] = field(default_factory=_redact_keys)
    redact_value_patterns: list[str] = field(default_factory=_str_list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "static", dict(self.static))
        object.__setattr__(self, "redact_keys", set(self.redact_keys))
        object.__setattr__(self, "redact_value_patterns", list(self.redact_value_patterns))


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
