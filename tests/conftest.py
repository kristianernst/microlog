import importlib
import logging
import sys
from pathlib import Path
from typing import Any, Iterator, cast

pytest = cast(Any, importlib.import_module("pytest"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

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
