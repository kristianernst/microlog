# OpenTelemetry Collector Example

This example spins up an OpenTelemetry Collector (`0.137.0`) alongside a small
Python application that uses **microlog** and the OpenTelemetry logging
pipeline. Logs are exported to the collector via OTLP gRPC and surfaced in both
a lightweight Loki (`3.4.4`) + Grafana (`12.2.0`) stack. The collector
configuration mirrors Grafana's
[Getting started with the OpenTelemetry Collector and Loki tutorial](https://grafana.com/docs/loki/latest/send-data/otel/otel-collector-getting-started/)
by forwarding logs with the OTLP HTTP exporter to Loki's native OTLP endpoint.

## Prerequisites

- Docker Desktop or Docker Engine with docker compose plugin

## How to run

```bash
cd examples/otel_example
docker compose up --build
```

Browse to Grafana at <http://localhost:3000> (default credentials `admin`/`admin`).
A Loki data source and a dashboard are pre-provisioned. Open **Dashboards → Microlog
OTEL Demo** (or explore with `{service="app"}`) to see the events flowing through the
collector into Loki. The collector ships logs to the Loki tenant `microlog` via the
native OTLP endpoint described in the tutorial above.

When running the app outside Docker, set `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` to point at
your collector (defaults to `http://otel-collector:4318/v1/logs` inside this compose stack).

For local runs without Docker:

```bash
# from the repository root
uv sync --extra opentelemetry
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:4318/v1/logs
uv run python examples/otel_example/app/main.py
```

To stop and clean up the containers:

```bash
docker compose down
```

## Files

- `docker-compose.yaml`: orchestrates the collector, Loki, Grafana, and the Python app
- `collector-config.yaml`: collector pipeline (OTLP receiver → batch → OTLP HTTP → Loki)
- `loki-config.yaml`: filesystem-backed Loki configuration from the Grafana tutorial
- `grafana/provisioning`: pre-configured Loki data source and dashboard definition
- `app/requirements.txt`: Python dependencies for the sample app (mirrors the `opentelemetry` extra)
- `app/main.py`: Python script that configures microlog and ships logs via OTLP
- `app/Dockerfile`: container definition for the sample app
