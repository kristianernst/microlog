"""Simple microlog example that logs to stdout and a file."""

from __future__ import annotations

from pathlib import Path

from microlog import (
    configure_logging,
    get_logger,
    get_runtime_stats,
    log_context,
    production_config,
    shutdown_logging,
)


def main() -> None:
    cfg = production_config(
        "orders",
        service_version="1.0.0",
        environment="prod",
        level="INFO",
        file_path="./examples/logs/orders.log",
    )

    if cfg.file:
        Path(cfg.file.path).parent.mkdir(parents=True, exist_ok=True)

    configure_logging(cfg)
    log = get_logger(__name__, cfg)

    with log_context(request_id="req-1234", user_id="user-5"):
        log.info("checkout started", extra={"order_id": "ord-99"})
        try:
            raise ZeroDivisionError("simulated failure")
        except ZeroDivisionError:
            log.exception("error during checkout")

    log.info("general logging here", extra={"result": 1 + 1})
    stats = get_runtime_stats()
    log.info(
        "logger stats",
        extra={
            "queued_records": stats.queued_records,
            "dropped_records": stats.dropped_records,
            "dropped_oldest_records": stats.dropped_oldest_records,
        },
    )
    shutdown_logging()


if __name__ == "__main__":
    main()
