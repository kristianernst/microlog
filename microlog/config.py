from __future__ import annotations
from typing import Any, Dict, List, Optional, Set

try:
    from chz import chz, field
except Exception as exc:  # pragma: no cover
    raise ImportError("chz is required: https://github.com/openai/chz") from exc


@chz
class StdoutConfig:
    level: Optional[str] = field(default=None)


@chz
class FileConfig:
    path: str = field()
    rotate_bytes: Optional[int] = field(default=None)
    rotate_backups: int = field(default=5)
    level: Optional[str] = field(default=None)


@chz
class OTLPConfig:
    protocol: str = field(default="http/protobuf")
    endpoint: Optional[str] = field(default=None)
    insecure: bool = field(default=True)
    headers: Dict[str, str] = field(default_factory=dict)
    compression: Optional[str] = field(default=None)
    timeout: Optional[float] = field(default=None)
    level: Optional[str] = field(default=None)
    resource_attributes: Dict[str, Any] = field(default_factory=dict)


@chz
class LogConfig:
    service_name: str = field(default="app")
    service_version: Optional[str] = field(default=None)
    environment: Optional[str] = field(default=None)
    stdout: Optional[StdoutConfig] = field(default_factory=StdoutConfig)
    file: Optional[FileConfig] = field(default=None)
    otlp: Optional[OTLPConfig] = field(default=None)
    level: str = field(default="INFO")
    utc: bool = field(default=True)
    async_mode: bool = field(default=True)
    async_queue_size: int = field(default=10000)
    async_queue_drop_oldest: bool = field(default=True)
    json_indent: Optional[int] = field(default=None)
    dev_color: bool = field(default=False)
    include_logger_name: bool = field(default=True)
    include_thread: bool = field(default=False)
    include_pid: bool = field(default=True)
    include_host: bool = field(default=True)
    include_code: bool = field(default=True)
    try_opentelemetry: bool = field(default=True)
    static: Dict[str, Any] = field(default_factory=dict)
    redact_keys: Set[str] = field(
        default_factory=lambda: {
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
    redact_value_patterns: List[str] = field(default_factory=list)


def severity_number(levelno: int) -> int:
    """Map stdlib logging levels to OpenTelemetry severity_number values (1-24)."""

    if levelno <= 0:
        return 1  # TRACE
    if levelno < 20:
        return 5  # DEBUG
    if levelno < 30:
        return 9  # INFO
    if levelno < 40:
        return 13  # WARN
    if levelno < 50:
        return 17  # ERROR
    return 21  # FATAL
