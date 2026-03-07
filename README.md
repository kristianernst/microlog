## microlog

microlog provides JSON logging, context propagation, and optional OTLP export for Python services.

### Installation

Install core dependencies with [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

Install OTLP support when needed:

```bash
uv sync --extra opentelemetry
```

Install max-throughput JSON serialization when needed:

```bash
uv sync --extra performance
```

### Basic use

Configure logging once at startup and obtain a contextual logger:

```python
from microlog import LogConfig, StdoutConfig, configure_logging, get_logger, log_context

cfg = LogConfig(stdout=StdoutConfig(level="INFO"))
configure_logging(cfg)
log = get_logger(__name__, cfg)

with log_context(request_id="req-123"):
    log.info("ready")
```

For production defaults across microservices, use the preset:

```python
from microlog import production_config, configure_logging

cfg = production_config(
    "orders",
    environment="prod",
    enable_otlp=True,
    otlp_endpoint="http://otel-collector:4318/v1/logs",
)
configure_logging(cfg)
```

`production_config` enables adaptive load shedding defaults for low-severity logs under queue
pressure (`shed_below_level="WARNING"`, `shed_when_queue_above=0.85`, `shed_rate=0.2`).
It is intentionally small; for custom sink levels, rotation, headers, compression, or timeouts,
build `LogConfig(...)` directly.

### Asynchronous logging

`LogConfig(async_mode=True)` wires up a background `QueueListener` so writes never block your
application threads. The queue is bounded by default (`async_queue_size=10_000`) to protect your
process from backpressure. Tune the queue characteristics to match the workload:

```python
cfg = LogConfig(
    async_mode=True,
    async_queue_size=2_000,
    async_queue_drop_oldest=True,
)
configure_logging(cfg)
```

Set `async_queue_size=0` to restore the legacy unbounded queue, though this should generally be
reserved for short-lived scripts where burst loss is unacceptable.

### Production profile

- Use `async_mode=True` with a bounded queue.
- Enable OTLP only when you need collector export.
- Install the `performance` extra to use `orjson` for faster serialization.
- Call `shutdown_logging()` on process exit to drain the async queue and flush handlers.
- Track logger health via `get_runtime_stats()` to detect dropped records.
- Export logger health to OpenTelemetry metrics with `enable_otel_runtime_metrics()`.
- Keep `otlp_fail_open=True` for graceful startup when collector is unavailable.

### Runtime health stats

```python
from microlog import get_runtime_stats

stats = get_runtime_stats()
# RuntimeStats(queued_records=..., dropped_records=..., dropped_oldest_records=...)
```

### OTel runtime metrics

```python
from microlog import enable_otel_runtime_metrics

enabled = enable_otel_runtime_metrics(attributes={"service.name": "orders"})
```

When enabled, microlog publishes:
- `microlog_queued_records_total`
- `microlog_dropped_records_total`
- `microlog_shed_records_total`
- `microlog_queue_size`
- `microlog_queue_maxsize`

See [docs/alerting.md](docs/alerting.md) for a minimal alerting spec.

### OTLP export

Add an `OTLPConfig` to send records to a collector. Environment variables
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and `OTEL_EXPORTER_OTLP_ENDPOINT` act as fallbacks.
HTTPS endpoints keep certificate verification enabled by default; set `insecure=True`
only for local or self-signed collectors.

```python
from microlog import LogConfig, OTLPConfig, configure_logging, shutdown_logging

cfg = LogConfig(
    otlp=OTLPConfig(
        protocol="http/protobuf",
        endpoint="http://otel-collector:4318/v1/logs",
    )
)
configure_logging(cfg)
shutdown_logging()  # drain the async queue and flush OTLP handlers before exit
```

See `examples/otel/app/main.py` for a complete script that enables async logging, tunes the queue,
and shuts down logging to flush the OpenTelemetry `LoggerProvider`.

### Examples

- `examples/simple/simple_example.py` writes to stdout and a file.
  - cmd: `uv run -m examples.simple.simple_example`
- `examples/otel` sends OTLP logs to Loki through the OpenTelemetry Collector and Grafana.
  - cmd:
    ```bash
    cd examples/otel
    docker compose up --build
    ```

### Development

- Run linters:
  ```bash
  uv run --with ruff ruff check
  uv run pyright
  ```
- Run tests:
  ```bash
  uv run pytest
  ```
- Run performance guard:
  ```bash
  uv run python -m benchmarks.benchmark --count 20000 --min-sync-rps 2500 --min-async-rps 2500 --max-drop-rate 0.05
  ```

### Contribution guidelines

- Keep changes focused and incremental. Separate unrelated improvements into individual patches.
- Retain the immutability contract for configuration classes (`LogConfig`, `StdoutConfig`, `FileConfig`, `OTLPConfig`).
- Respect the configure-once model. New behaviour should be driven by configuration options processed inside `configure_logging`.
- Avoid module import side effects. Limit global state changes to `configure_logging`, `shutdown_logging`, and `microlog.logger._stop_listener`.
- Guard optional features. Wrap OpenTelemetry imports so the core package stays usable without extras.
- Preserve the JSON output schema unless a breaking change is intentional and documented.
- Add or update tests when behaviour changes. Prefer unit coverage and skip OTLP integration checks when OTEL extras are unavailable.
