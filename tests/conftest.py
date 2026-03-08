import importlib
import logging
from typing import Any, Iterator, cast

pytest = cast(Any, importlib.import_module("pytest"))

import microlog.logger as microlog_logger  # noqa: E402


@pytest.fixture(autouse=True)
def reset_logging_state() -> Iterator[None]:
    """Ensure each test starts with a clean logging configuration."""
    logging.shutdown()
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)
    logging.captureWarnings(False)
    yield
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
    logging.shutdown()
    root = logging.getLogger()
    root.setLevel(logging.NOTSET)
    logging.captureWarnings(False)
