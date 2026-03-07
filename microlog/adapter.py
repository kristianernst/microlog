from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, MutableMapping

from .config import MICROLOG_FIELDS, LogConfig, safe_fields

_CTX: ContextVar[dict[str, Any]] = ContextVar("microlog_ctx", default={})


@contextmanager
def log_context(**attrs: Any):
    token = _CTX.set({**_CTX.get(), **safe_fields(attrs)})
    try:
        yield
    finally:
        _CTX.reset(token)


class ContextAdapter(logging.LoggerAdapter[logging.Logger]):
    def __init__(
        self,
        logger: logging.Logger,
        extra: dict[str, Any] | None,
        fields: dict[str, Any] | None = None,
    ):
        base_fields = dict(extra or {})
        if fields is not None:
            base_fields.update(safe_fields(fields))
        super().__init__(logger, base_fields)
        self._fields = dict(base_fields)

    def process(self, msg: str, kwargs: MutableMapping[str, Any]):
        fields = dict(self._fields)
        if extra := kwargs.get("extra"):
            fields.update(safe_fields(extra))
        if ctx := _CTX.get():
            fields.update(ctx)
        if fields:
            kwargs["extra"] = {MICROLOG_FIELDS: fields}
        else:
            kwargs.pop("extra", None)
        return msg, kwargs


def get_logger(name: str | None, cfg: LogConfig) -> ContextAdapter:
    return ContextAdapter(logging.getLogger(name or cfg.service_name), None)
