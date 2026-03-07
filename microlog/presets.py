from __future__ import annotations

from typing import Any

from .config import FileConfig, LogConfig, OTLPConfig


def production_config(
    service_name: str,
    *,
    service_version: str | None = None,
    environment: str | None = None,
    level: str | int = "INFO",
    file_path: str | None = None,
    enable_otlp: bool = False,
    otlp_endpoint: str | None = None,
    otlp_fail_open: bool = True,
    static: dict[str, Any] | None = None,
    redact_keys: set[str] | None = None,
    redact_value_patterns: list[str] | None = None,
) -> LogConfig:
    kwargs: dict[str, Any] = dict(
        service_name=service_name,
        service_version=service_version,
        environment=environment,
        level=level,
        file=FileConfig(path=file_path) if file_path else None,
        otlp=OTLPConfig(endpoint=otlp_endpoint) if (enable_otlp or otlp_endpoint) else None,
        otlp_fail_open=otlp_fail_open,
        async_mode=True,
        async_queue_size=10_000,
        async_queue_drop_oldest=True,
        shed_below_level="WARNING",
        shed_when_queue_above=0.85,
        shed_rate=0.2,
    )
    if static is not None:
        kwargs["static"] = dict(static)
    if redact_keys is not None:
        kwargs["redact_keys"] = set(redact_keys)
    if redact_value_patterns is not None:
        kwargs["redact_value_patterns"] = list(redact_value_patterns)
    return LogConfig(**kwargs)
