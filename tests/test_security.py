import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any

import microlog.otel as microlog_otel
from microlog import FileConfig, LogConfig, configure_logging, get_logger, shutdown_logging
from microlog.logger import DevColorFormatter

pytest = __import__("pytest")


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_otlp_record_filter_redacts_body_fields_and_exception_details() -> None:
    try:
        raise ValueError("token=abc123")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.makeLogRecord(
        {
            "msg": "Authorization: Bearer top-secret",
            "levelno": logging.ERROR,
            "levelname": "ERROR",
            "pathname": __file__,
            "lineno": 24,
            "funcName": "test_otlp_record_filter_redacts_body_fields_and_exception_details",
            "exc_info": exc_info,
            "stack_info": "token=abc123",
            "request_id": "req-1",
            "_microlog": {
                "credentials": {"password": "pw"},
                "details": "token=abc123",
            },
        }
    )

    filt = microlog_otel._OTLPRecordFilter(  # pyright: ignore[reportPrivateUsage]
        {"region": "eu-west-1"},
        redact_keys={"authorization", "password", "token"},
    )

    assert filt.filter(record) is True
    assert record.getMessage() == "Authorization: Bearer ***"
    assert record.region == "eu-west-1"
    assert record.request_id == "req-1"
    assert record.credentials["password"] == "***"
    assert record.details == "token=***"
    assert record.exc_info is None
    assert record.stack_info is None
    assert record.__dict__["exception.message"] == "token=***"
    assert "token=***" in record.__dict__["exception.stacktrace"]


def test_file_handler_rejects_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "target.log"
    target.write_text("")
    link = tmp_path / "app.log"
    link.symlink_to(target)

    cfg = LogConfig(stdout=None, file=FileConfig(path=str(link)), async_mode=False)
    with pytest.raises(RuntimeError, match="symlink"):
        configure_logging(cfg)


@pytest.mark.skipif(os.name != "posix", reason="permission hardening is POSIX-specific")
def test_file_handler_creates_private_log_file(tmp_path: Path) -> None:
    log_path = tmp_path / "private" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=False)
    configure_logging(cfg)
    get_logger("svc", cfg).info("private")
    shutdown_logging()
    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode & 0o077 == 0


def test_unbounded_async_queue_warns(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "app.log"
    cfg = LogConfig(
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=True,
        async_queue_size=0,
    )
    with pytest.warns(RuntimeWarning, match="unbounded"):
        configure_logging(cfg)


def test_grpc_otlp_insecure_defaults_to_false_without_cleartext_scheme() -> None:
    assert (
        microlog_otel._resolve_otlp_insecure(  # pyright: ignore[reportPrivateUsage]
            "grpc",
            "collector:4317",
            None,
        )
        is False
    )
    assert (
        microlog_otel._resolve_otlp_insecure(  # pyright: ignore[reportPrivateUsage]
            "grpc",
            "http://collector:4317",
            None,
        )
        is True
    )


def test_dev_formatter_escapes_control_characters() -> None:
    formatter = DevColorFormatter(LogConfig(dev_color=True))
    record = logging.makeLogRecord(
        {
            "msg": "line-1\nline-2\tpassword=hunter2",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "pathname": __file__,
            "lineno": 1,
            "funcName": "test_dev_formatter_escapes_control_characters",
            "name": "svc",
        }
    )
    line = formatter.format(record)
    assert "\n" not in line
    assert r"\n" in line
    assert r"\t" in line
    assert "password=***" in line
