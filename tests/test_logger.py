import json
import logging
from pathlib import Path
from typing import Any, Dict, List, cast

from microlog import FileConfig, LogConfig, StdoutConfig, configure_logging, get_logger, log_context
import microlog.logger as microlog_logger
from microlog.logger import DevColorFormatter, JsonFormatter
from logging.handlers import RotatingFileHandler

pytest = cast(Any, __import__("pytest"))


def flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            try:
                handler.flush()
            except Exception:
                pass


def read_json_lines(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_configure_logging_requires_handler() -> None:
    cfg = LogConfig(stdout=None, file=None)
    with pytest.raises(ValueError):
        configure_logging(cfg)


def test_stdout_handler_level_override(monkeypatch) -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="DEBUG"), file=None, async_mode=False)
    configure_logging(cfg)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.level == logging.DEBUG
    assert isinstance(handler.formatter, JsonFormatter)


def test_file_handler_created(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=False)
    configure_logging(cfg)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert log_path.exists()
    assert handler.baseFilename == str(log_path)


def test_file_logging_writes_json(tmp_path: Path) -> None:
    log_path = tmp_path / "records" / "app.log"
    cfg = LogConfig(
        service_name="orders",
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=False,
        static={"team": "core"},
    )
    configure_logging(cfg)
    logger = get_logger("orders-service", cfg)
    with log_context(request_id="req-1", user_id="user-7"):
        logger.info("checkout", extra={"password": "secret", "cart_id": "cart-9"})
    flush_handlers()
    entries = read_json_lines(log_path)
    assert entries
    record = entries[-1]
    assert record["body"] == "checkout"
    assert record["request_id"] == "req-1"
    assert record["user_id"] == "user-7"
    assert record["cart_id"] == "cart-9"
    assert record["password"] == "***"
    assert record["service.name"] == "orders"
    assert record["team"] == "core"


def test_stdout_logging_includes_context(capfd) -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="INFO"), file=None, async_mode=False)
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    with log_context(request_id="ctx-1"):
        logger.info("ping", extra={"token": "abc123"})
    flush_handlers()
    out, _ = capfd.readouterr()
    lines: List[str] = [line for line in out.splitlines() if line]
    assert lines
    record = json.loads(lines[-1])
    assert record["body"] == "ping"
    assert record["request_id"] == "ctx-1"
    assert record["token"] == "***"


def test_dev_color_formatter_applied(monkeypatch) -> None:
    def always_tty(_: object) -> bool:
        return True

    monkeypatch.setattr("microlog.logger._is_tty", always_tty)
    cfg = LogConfig(stdout=StdoutConfig(), file=None, async_mode=False, dev_color=True)
    configure_logging(cfg)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, DevColorFormatter)


def test_invalid_level_raises() -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="NOT-A-LEVEL"), file=None)
    with pytest.raises(ValueError):
        configure_logging(cfg)


def test_async_mode_flushes_to_file(tmp_path: Path) -> None:
    log_path = tmp_path / "async" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)
    logger = get_logger(None, cfg)
    logger.info("queued")
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
    flush_handlers()
    assert log_path.exists()
    entries = read_json_lines(log_path)
    assert entries[-1]["body"] == "queued"


def test_get_logger_provides_static_metadata() -> None:
    cfg = LogConfig(static={"region": "eu-west-1"})
    adapter = get_logger(None, cfg)
    extra = adapter.extra
    assert isinstance(extra, dict)
    assert extra["service.name"] == cfg.service_name
    assert extra["region"] == "eu-west-1"
