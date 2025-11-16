from __future__ import annotations
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, MutableMapping, Optional, cast
from .config import LogConfig

_CTX: ContextVar[Dict[str, Any]] = ContextVar("microlog_ctx", default={})


@contextmanager
def log_context(**attrs: Any):
    token = _CTX.set({**_CTX.get(), **attrs})
    try:
        yield
    finally:
        _CTX.reset(token)


class ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(self, msg: str, kwargs: MutableMapping[str, Any]):
        extra: Dict[str, Any] = dict(self.extra) if self.extra else {}
        extra.update(cast(Optional[Dict[str, Any]], kwargs.get("extra")) or {})
        extra.update(_CTX.get())
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(name: Optional[str], cfg: LogConfig) -> ContextAdapter:
    base = {"service.name": cfg.service_name}
    if cfg.service_version:
        base["service.version"] = cfg.service_version
    if cfg.environment:
        base["deployment.environment"] = cfg.environment
    base.update(cfg.static)
    return ContextAdapter(logging.getLogger(name or cfg.service_name), base)
