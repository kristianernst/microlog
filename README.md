## microlog

microlog is a small lightweight logging library for Python particularly designed for microservices and serverless environments.  It provides structured JSON output, contextual logging helpers, and optional OTLP exporters for shipping events to an OpenTelemetry collector.

### Installation

microlog is published as a regular Python package.  To work on the repository with [uv](https://github.com/astral-sh/uv), install the core dependencies plus the optional OTLP extras:

```bash
uv sync --extra opentelemetry
```

This will make the library (`microlog/`) importable in your virtual environment and pull in `opentelemetry-sdk`/`opentelemetry-exporter-otlp` when you want to stream logs to a collector.

### Examples

- `examples/simple_example.py` demonstrates stdout + rotating-file logging. Run it with:
  ```bash
  uv run python examples/simple_example.py
  ```
- `examples/otel_example` showcases end-to-end OTLP delivery into Loki via the OpenTelemetry Collector and Grafana. See the example README for Docker instructions, or run the app locally once you have a collector endpoint:
  ```bash
  export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:4318/v1/logs
  uv run python examples/otel_example/app/main.py
  ```

### Optional OTLP configuration

You can enable OTLP exporting by adding an `OTLPConfig` to your `LogConfig`:

```python
from microlog import LogConfig, OTLPConfig, configure_logging

cfg = LogConfig(
    otlp=OTLPConfig(
        protocol="http/protobuf",
        endpoint="http://otel-collector:4318/v1/logs",
    ),
)
configure_logging(cfg)
```

By default microlog reads standard OTEL environment variables, attaches basic resource attributes (service name/version/environment), and handles exporter shutdown during process exit.
