import logging
from pathlib import Path

from microlog import (
    FileConfig,
    LogConfig,
    StdoutConfig,
    configure_logging,
    get_logger,
    log_context,
)

# Create base config and customise per microservice
cfg = LogConfig(
    service_name="orders",
    service_version="1.0.0",
    environment="prod",
    stdout=StdoutConfig(level="INFO"),
    file=FileConfig(
        path="./examples/logs/orders.log",
        rotate_bytes=10_000_000,  # ~10MB rotation
        rotate_backups=7,
        level="DEBUG",
    ),
    dev_color=True,
    async_mode=False,
)

# Initialise logging at startup
configure_logging(cfg)
assert cfg.file is not None  # file logging enabled above
log_path = Path(cfg.file.path)
# Obtain a ContextAdapter (includes static metadata)
log = get_logger(__name__, cfg)

# Annotate logs with request context
with log_context(request_id="req-1234", user_id="user-5"):
    log.info("checkout started", extra={"order_id": "ord-99"})
    try:
        raise ZeroDivisionError("simulated failure")
    except ZeroDivisionError:
        log.exception("error during checkout")


# use as general:
log.info(f"General logging here {1 + 1}")
logging.shutdown()
