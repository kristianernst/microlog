"""Simple microlog example with CLI-configurable settings via chz."""

from __future__ import annotations

import logging
from pathlib import Path

import chz

from microlog import (
    FileConfig,
    LogConfig,
    StdoutConfig,
    configure_logging,
    get_logger,
    log_context,
)


@chz.chz
class SimpleAppConfig:
    service_name: str = "orders"
    service_version: str | None = "1.0.0"
    environment: str | None = "prod"
    log_level: str = "INFO"
    stdout_level: str = "INFO"
    dev_color: bool = True
    async_mode: bool = False
    enable_file: bool = True
    file_path: str = "./examples/logs/orders.log"
    rotate_bytes: int | None = 10_000_000
    rotate_backups: int = 7
    file_level: str = "DEBUG"


def run_example(settings: SimpleAppConfig) -> None:
    file_cfg = (
        FileConfig(
            path=settings.file_path,
            rotate_bytes=settings.rotate_bytes,
            rotate_backups=settings.rotate_backups,
            level=settings.file_level,
        )
        if settings.enable_file
        else None
    )
    if file_cfg is not None:
        Path(file_cfg.path).parent.mkdir(parents=True, exist_ok=True)
    cfg = LogConfig(
        service_name=settings.service_name,
        service_version=settings.service_version,
        environment=settings.environment,
        level=settings.log_level,
        stdout=StdoutConfig(level=settings.stdout_level),
        file=file_cfg,
        dev_color=settings.dev_color,
        async_mode=settings.async_mode,
    )
    configure_logging(cfg)
    log = get_logger(__name__, cfg)

    with log_context(request_id="req-1234", user_id="user-5"):
        log.info("checkout started", extra={"order_id": "ord-99"})
        try:
            raise ZeroDivisionError("simulated failure")
        except ZeroDivisionError:
            log.exception("error during checkout")

    log.info("General logging here %s", 1 + 1)
    logging.shutdown()


def main(settings: SimpleAppConfig) -> None:
    run_example(settings)


if __name__ == "__main__":
    chz.entrypoint(main)
