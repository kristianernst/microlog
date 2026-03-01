from __future__ import annotations

from typing import Any

from .config import FileConfig, LogConfig, OTLPConfig, StdoutConfig


def production_config(
    service_name: str,
    *,
    service_version: str | None = None,
    environment: str | None = None,
    level: str | int = "INFO",
    enable_stdout: bool = True,
    stdout_level: str | int | None = None,
    file_path: str | None = None,
    file_level: str | int | None = None,
    file_rotate_bytes: int | None = None,
    file_rotate_backups: int = 5,
    enable_otlp: bool = False,
    otlp_protocol: str = "http/protobuf",
    otlp_endpoint: str | None = None,
    otlp_insecure: bool = True,
    otlp_fail_open: bool = True,
    otlp_headers: dict[str, str] | None = None,
    otlp_compression: str | None = None,
    otlp_timeout: float | None = None,
    otlp_level: str | int | None = None,
    otlp_resource_attributes: dict[str, Any] | None = None,
    async_queue_size: int = 10_000,
    async_queue_drop_oldest: bool = True,
    shed_below_level: str | int | None = "WARNING",
    shed_when_queue_above: float = 0.85,
    shed_rate: float = 0.2,
    static: dict[str, Any] | None = None,
    redact_keys: set[str] | None = None,
    redact_value_patterns: list[str] | None = None,
    try_opentelemetry: bool = True,
) -> LogConfig:
    otlp_requested = enable_otlp or any(
        [
            otlp_endpoint is not None,
            otlp_headers is not None,
            otlp_compression is not None,
            otlp_timeout is not None,
            otlp_level is not None,
            otlp_resource_attributes is not None,
        ]
    )

    stdout_cfg = StdoutConfig(level=stdout_level) if enable_stdout else None
    file_cfg = (
        FileConfig(
            path=file_path,
            level=file_level,
            rotate_bytes=file_rotate_bytes,
            rotate_backups=file_rotate_backups,
        )
        if file_path
        else None
    )
    otlp_cfg = (
        OTLPConfig(
            protocol=otlp_protocol,
            endpoint=otlp_endpoint,
            insecure=otlp_insecure,
            headers=dict(otlp_headers or {}),
            compression=otlp_compression,
            timeout=otlp_timeout,
            level=otlp_level,
            resource_attributes=dict(otlp_resource_attributes or {}),
        )
        if otlp_requested
        else None
    )

    if stdout_cfg is None and file_cfg is None and otlp_cfg is None:
        raise ValueError("At least one sink must be enabled (stdout, file, or OTLP).")

    kwargs: dict[str, Any] = dict(
        service_name=service_name,
        service_version=service_version,
        environment=environment,
        level=level,
        stdout=stdout_cfg,
        file=file_cfg,
        otlp=otlp_cfg,
        otlp_fail_open=otlp_fail_open,
        async_mode=True,
        async_queue_size=async_queue_size,
        async_queue_drop_oldest=async_queue_drop_oldest,
        shed_below_level=shed_below_level,
        shed_when_queue_above=shed_when_queue_above,
        shed_rate=shed_rate,
        include_logger_name=True,
        include_thread=False,
        include_pid=True,
        include_host=True,
        include_code=True,
        try_opentelemetry=try_opentelemetry,
    )
    if static is not None:
        kwargs["static"] = dict(static)
    if redact_keys is not None:
        kwargs["redact_keys"] = set(redact_keys)
    if redact_value_patterns is not None:
        kwargs["redact_value_patterns"] = list(redact_value_patterns)
    return LogConfig(**kwargs)
