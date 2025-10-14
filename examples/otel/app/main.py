"""Example application that ships microlog output to an OpenTelemetry Collector."""

from __future__ import annotations

import logging
import os
import time

from microlog import LogConfig, OTLPConfig, StdoutConfig, configure_logging, get_logger, log_context


def main() -> None:
    collector_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://otel-collector:4318/v1/logs"
    )
    cfg = LogConfig(
        stdout=StdoutConfig(level="INFO"),
        file=None,
        async_mode=False,
        otlp=OTLPConfig(endpoint=collector_endpoint),
    )
    configure_logging(cfg)

    log = get_logger(__name__, cfg)
    with log_context(request_id="otel-demo", user="microlog"):
        log.info("booting collector example", extra={"phase": "start"})
        try:
            raise RuntimeError("example error to demonstrate exception logging")
        except RuntimeError:
            log.exception("handled runtime error")
        log.info("completed work cycle", extra={"phase": "finish"})

    log.info("Miley Cyrus!")

    time.sleep(2)
    logging.shutdown()


if __name__ == "__main__":
    main()
