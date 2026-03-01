"""Top‑level package for microlog.

Exposes `LogConfig`, `StdoutConfig`, `FileConfig`, `OTLPConfig`, `configure_logging`,
`get_logger`, `log_context`, `production_config`, and runtime stats helpers.
See module docstrings for usage examples.
"""

from .config import FileConfig, LogConfig, OTLPConfig, StdoutConfig
from .logger import (
    RuntimeStats,
    configure_logging,
    disable_otel_runtime_metrics,
    enable_otel_runtime_metrics,
    get_runtime_stats,
    reset_runtime_stats,
)
from .adapter import get_logger, log_context
from .presets import production_config

__all__ = [
    "LogConfig",
    "StdoutConfig",
    "FileConfig",
    "OTLPConfig",
    "RuntimeStats",
    "configure_logging",
    "enable_otel_runtime_metrics",
    "disable_otel_runtime_metrics",
    "get_runtime_stats",
    "reset_runtime_stats",
    "get_logger",
    "log_context",
    "production_config",
]
