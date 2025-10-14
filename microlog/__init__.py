"""Top‑level package for microlog.

Exposes `LogConfig`, `StdoutConfig`, `FileConfig`, `OTLPConfig`, `configure_logging`,
`get_logger` and `log_context`.
See module docstrings for usage examples.
"""

from .config import FileConfig, LogConfig, OTLPConfig, StdoutConfig
from .logger import configure_logging
from .adapter import get_logger, log_context

__all__ = [
    "LogConfig",
    "StdoutConfig",
    "FileConfig",
    "OTLPConfig",
    "configure_logging",
    "get_logger",
    "log_context",
]
