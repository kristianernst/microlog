# OpenTelemetry Collector Example

This example starts an OpenTelemetry Collector (`0.137.0`), Loki (`3.4.4`), Grafana (`12.2.0`), and a small Python application that uses **microlog** with the OTLP exporter. The collector configuration follows Grafana’s [Getting started with the OpenTelemetry Collector and Loki tutorial](https://grafana.com/docs/loki/latest/send-data/otel/otel-collector-getting-started/) and forwards logs to Loki’s native OTLP HTTP endpoint.

## Prerequisites

- Docker Desktop or Docker Engine with the docker compose plugin
- Optional: local Python environment managed by [uv](https://github.com/astral-sh/uv) for running the sample app outside Docker

## Run with Docker

From the repository root:

```bash
cd examples/otel
docker compose up --build
```

This boots:

- `otel-collector` with the pipeline defined in `collector-config.yaml`
- `loki` persisting data on the `loki-data` volume
- `grafana` with a pre-provisioned Loki data source and dashboard
- `app`, a Python container that emits OTLP logs through microlog

Open Grafana at <http://localhost:3000> (credentials `admin`/`admin`). The dashboard **Microlog OTEL Demo** shows the sample events; you can also run a Loki query such as `{service_name="app"}` to explore the stream.

To stop and clean up:

```bash
docker compose down
```

Volumes `grafana-data` and `loki-data` are preserved between runs. Remove them with `docker compose down --volumes` if you want a clean slate.

## Run the sample app locally

```bash
# from the repository root
uv sync --extra opentelemetry
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:4318/v1/logs
uv run -m examples.otel.app.main
```

Point `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` at your collector. The sample reads the variable at startup and falls back to `http://otel-collector:4318/v1/logs` when running inside the compose network.

## File map

- `docker-compose.yaml` – Orchestrates collector, Loki, Grafana, and the sample app
- `collector-config.yaml` – Collector pipeline: OTLP receiver → batch → OTLP HTTP (Loki)
- `loki-config.yaml` – Filesystem-backed Loki configuration from the Grafana tutorial
- `grafana/provisioning` – Pre-configured Loki data source and dashboard
- `app/Dockerfile` – Container definition for the sample app
- `app/requirements.txt` – Python dependencies (matches the `opentelemetry` extra)
- `app/main.py` – Microlog example that exports logs via OTLP
