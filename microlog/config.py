"""Configuration definitions for the microlog logging library.

Defines an immutable LogConfig using the chz decorator.  The fields map
closely to OpenTelemetry’s log data model and include sensible defaults.
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Set, List

try:
    from chz import chz, field
except Exception as exc:
    raise ImportError(
        "The 'chz' package is required. Install it from https://github.com/openai/chz."
    ) from exc


@chz
class StdoutConfig:
    """Configuration for the stdout handler."""

    level: Optional[str] = field(
        default=None,
        doc="Optional log level override for the stdout stream (e.g. DEBUG).",
    )


@chz
class FileConfig:
    """Configuration for the rotating file handler."""

    path: str = field(doc="Filesystem path for the JSONL log file.")
    rotate_bytes: Optional[int] = field(
        default=None, doc="Max file size before rotation. Set to None to disable rotation."
    )
    rotate_backups: int = field(
        default=5, doc="Number of rotated files to keep alongside the active log."
    )
    level: Optional[str] = field(
        default=None,
        doc="Optional log level override for the file handler (e.g. WARNING).",
    )


@chz
class OTLPConfig:
    """Configuration for exporting logs over OTLP."""

    protocol: str = field(
        default="http/protobuf",
        doc="Transport for OTLP exporter. Supported values: 'http/protobuf' or 'grpc'.",
    )
    endpoint: Optional[str] = field(
        default=None,
        doc="Collector endpoint. Falls back to OTEL_EXPORTER_OTLP_LOGS_ENDPOINT / "
        "OTEL_EXPORTER_OTLP_ENDPOINT when omitted.",
    )
    insecure: bool = field(
        default=True,
        doc="Disable TLS verification for the exporter transport.",
    )
    headers: Dict[str, str] = field(
        default_factory=dict,
        doc="Static headers attached to every OTLP request.",
    )
    compression: Optional[str] = field(
        default=None,
        doc="Compression algorithm name supported by the exporter (e.g. 'gzip').",
    )
    timeout: Optional[float] = field(
        default=None, doc="Request timeout for OTLP exports in seconds."
    )
    level: Optional[str] = field(
        default=None,
        doc="Optional log level override for the OTLP handler.",
    )
    resource_attributes: Dict[str, Any] = field(
        default_factory=dict,
        doc="Additional OpenTelemetry resource attributes applied to exported logs.",
    )


@chz
class LogConfig:
    """Immutable configuration for microlog."""

    # Service/resource attributes
    service_name: str = field(default="app", doc="Logical service name for log attribution.")
    service_version: Optional[str] = field(
        default=None, doc="Optional service version string reported in each log entry."
    )
    environment: Optional[str] = field(
        default=None, doc="Deployment environment label (e.g. prod, staging)."
    )

    # Output settings
    stdout: Optional[StdoutConfig] = field(
        default_factory=StdoutConfig,
        doc="Configure stdout logging. Set to null to disable the stdout handler.",
    )
    file: Optional[FileConfig] = field(
        default=None,
        doc="Configure file logging. Leave as null to skip writing logs to disk.",
    )
    otlp: Optional[OTLPConfig] = field(
        default=None,
        doc="Configure OTLP exporting. Leave as null to skip shipping logs to a collector.",
    )

    # Behaviour
    level: str = field(default="INFO", doc="Default log level applied to the root logger.")
    utc: bool = field(default=True, doc="Emit timestamps in UTC when True, local time otherwise.")
    async_mode: bool = field(default=True, doc="Enable QueueListener-based async logging.")
    json_indent: Optional[int] = field(
        default=None, doc="Pretty-print JSON logs. Defaults to compact output when None."
    )
    dev_color: bool = field(
        default=False,
        doc="Colourise console logs when stdout is attached to a TTY and this flag is True.",
    )

    # Field toggles
    include_logger_name: bool = field(default=True, doc="Include the logger name in each record.")
    include_thread: bool = field(default=False, doc="Include thread information in each record.")
    include_pid: bool = field(default=True, doc="Include process ID in each record.")
    include_host: bool = field(default=True, doc="Include hostname in each record.")
    include_code: bool = field(default=True, doc="Include source code location in each record.")
    try_opentelemetry: bool = field(
        default=True, doc="Try to auto-extract OpenTelemetry context when available."
    )

    # Static metadata attached to every log
    static: Dict[str, Any] = field(
        default_factory=dict, doc="Static key/value metadata added to every log entry."
    )

    # Redaction controls
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
        },
        doc="Case-insensitive field names whose values are replaced with ***.",
    )
    redact_value_patterns: List[str] = field(
        default_factory=list, doc="Regex patterns to scrub from string values."
    )


def severity_number(levelno: int) -> int:
    """Map Python logging levels to OTel severity_number ranges:contentReference[oaicite:7]{index=7}."""
    if levelno <= 0:
        return 0
    if levelno < 20:
        return 5  # DEBUG
    if levelno < 30:
        return 9  # INFO
    if levelno < 40:
        return 13  # WARN
    if levelno < 50:
        return 17  # ERROR
    return 21  # FATAL
