"""Contextual logging helpers for microlog.

Defines a context manager and a LoggerAdapter to attach request‑specific data.
"""

from __future__ import annotations
import logging
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Any, Dict, MutableMapping, Optional, cast
from .config import LogConfig

# Per‑task/thread context storage
_CTX: ContextVar[Dict[str, Any]] = ContextVar("microlog_ctx", default={})


@contextmanager
def log_context(**attrs: Any):
    """Attach attributes to all logs within the current async task/thread."""
    current = _CTX.get().copy()
    current.update(attrs)
    token = _CTX.set(current)
    try:
        yield
    finally:
        _CTX.reset(token)


class ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    """Merge static metadata, contextvars, and call‑site extras into the log record."""

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        extra: Dict[str, Any] = dict(self.extra) if self.extra else {}
        call_extra = cast(Optional[Dict[str, Any]], kwargs.get("extra"))
        if call_extra:
            extra.update(call_extra)
        ctx = _CTX.get()
        if ctx:
            extra.update(ctx)
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: Optional[str], cfg: LogConfig) -> ContextAdapter:
    """Return a ContextAdapter with static service metadata from cfg."""
    logger = logging.getLogger(name or cfg.service_name)
    static = {"service.name": cfg.service_name}
    if cfg.service_version:
        static["service.version"] = cfg.service_version
    if cfg.environment:
        static["deployment.environment"] = cfg.environment
    static.update(cfg.static)
    return ContextAdapter(logger, static)
