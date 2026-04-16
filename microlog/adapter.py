from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, MutableMapping

from .config import MICROLOG_FIELDS, LogConfig, safe_fields

_CTX: ContextVar[dict[str, Any]] = ContextVar("microlog_ctx", default={})


def _find_caller_without_code(
    stack_info: bool = False, stacklevel: int = 1
) -> tuple[str, int, str, str | None]:
    _ = stack_info, stacklevel
    return "", 0, "", None


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
        extra = kwargs.get("extra")
        ctx = _CTX.get()

        if not self._fields:
            if extra:
                fields = safe_fields(extra)
                if ctx:
                    fields = dict(fields)
                    fields.update(ctx)
            elif ctx:
                fields = dict(ctx)
            else:
                kwargs.pop("extra", None)
                return msg, kwargs
            kwargs["extra"] = {MICROLOG_FIELDS: fields}
            return msg, kwargs

        fields = dict(self._fields)
        if extra:
            fields.update(safe_fields(extra))
        if ctx:
            fields.update(ctx)
        kwargs["extra"] = {MICROLOG_FIELDS: fields}
        return msg, kwargs


def get_logger(name: str | None, cfg: LogConfig) -> ContextAdapter:
    logger = logging.getLogger(name or cfg.service_name)
    original = getattr(logger, "_microlog_original_findCaller", None)
    if cfg.include_code:
        if original is not None:
            logger.findCaller = original
    else:
        if original is None:
            original = logger.findCaller
            setattr(logger, "_microlog_original_findCaller", original)
        logger.findCaller = _find_caller_without_code
    return ContextAdapter(logger, None)
