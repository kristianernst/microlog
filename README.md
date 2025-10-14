## microlog

microlog provides JSON logging, context propagation, and optional OTLP export for Python services.

### Installation

Install the project and the OpenTelemetry extras with [uv](https://github.com/astral-sh/uv):

```bash
uv sync --extra opentelemetry
```

The command makes the `microlog` package available and installs the OTLP exporter and SDK.

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

### OTLP export

Add an `OTLPConfig` to send records to a collector. Environment variables
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` and `OTEL_EXPORTER_OTLP_ENDPOINT` act as fallbacks.

```python
from microlog import LogConfig, OTLPConfig, configure_logging

cfg = LogConfig(
    otlp=OTLPConfig(
        protocol="http/protobuf",
        endpoint="http://otel-collector:4318/v1/logs",
    )
)
configure_logging(cfg)
```

### Examples

- `examples/simple/simple_example.py` writes to stdout and a rotating file.
  - cmd: `uv run -m examples.simple.simple_example`
  - or to pass parameters via CLI: `uv run -m examples.simple.simple_example settings.stdout_level=DEBUG settings.enable_file=false`
- `examples/otel_example` sends OTLP logs to Loki through the OpenTelemetry Collector and Grafana.
  - cmd:
    ```bash
    cd examples/otel_example
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

### Contribution guidelines

- Keep changes focused and incremental. Separate unrelated improvements into individual patches.
- Retain the immutability contract for configuration classes (`LogConfig`, `StdoutConfig`, `FileConfig`, `OTLPConfig`).
- Respect the configure-once model. New behaviour should be driven by configuration options processed inside `configure_logging`.
- Avoid module import side effects. Limit global state changes to `configure_logging` and `microlog.logger._stop_listener`.
- Guard optional features. Wrap OpenTelemetry imports so the core package stays usable without extras.
- Preserve the JSON output schema unless a breaking change is intentional and documented.
- Add or update tests when behaviour changes. Prefer unit coverage and skip OTLP integration checks when OTEL extras are unavailable.
