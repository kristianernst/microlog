from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, MutableMapping

from .config import LogConfig

_CTX: ContextVar[dict[str, Any]] = ContextVar("microlog_ctx", default={})


@contextmanager
def log_context(**attrs: Any):
    token = _CTX.set({**_CTX.get(), **attrs})
    try:
        yield
    finally:
        _CTX.reset(token)


class ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(self, msg: str, kwargs: MutableMapping[str, Any]):
        kwargs["extra"] = {**(self.extra or {}), **(kwargs.get("extra") or {}), **_CTX.get()}
        return msg, kwargs


def get_logger(name: str | None, cfg: LogConfig) -> ContextAdapter:
    base = {"service.name": cfg.service_name, **cfg.static}
    if cfg.service_version:
        base["service.version"] = cfg.service_version
    if cfg.environment:
        base["deployment.environment"] = cfg.environment
    return ContextAdapter(logging.getLogger(name or cfg.service_name), base)
