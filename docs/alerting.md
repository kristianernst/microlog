# Microlog Alerting Spec

This document defines minimal production alerts for microlog runtime health metrics.

## Required Metrics

Enable metrics in service bootstrap:

```python
from microlog import enable_otel_runtime_metrics

enable_otel_runtime_metrics(attributes={"service.name": "orders"})
```

The logger emits:

- `microlog_queued_records_total`
- `microlog_dropped_records_total`
- `microlog_shed_records_total`
- `microlog_queue_size`
- `microlog_queue_maxsize`

## Alert Rules (Prometheus style)

`MicrologDropsCritical`

```promql
sum(rate(microlog_dropped_records_total[5m])) by (service_name) > 0
```

Severity: `critical`
Runbook: check collector availability, sink latency, queue saturation, and service log volume spikes.

`MicrologShedWarning`

```promql
sum(rate(microlog_shed_records_total[10m])) by (service_name) > 1
```

Severity: `warning`
Runbook: tune `shed_rate`, `shed_when_queue_above`, and queue size for noisy low-severity logs.

`MicrologQueueSaturation`

```promql
(
  max(microlog_queue_size) by (service_name)
/
  clamp_min(max(microlog_queue_maxsize) by (service_name), 1)
) > 0.9
```

for: `5m`
Severity: `warning`
Runbook: increase queue capacity, reduce sink latency, or increase shedding aggressiveness.

## SLO Notes

- `dropped_records_total` should be zero for critical services.
- `shed_records_total` can be non-zero, but should stay controlled and only affect low-severity logs.
- If queue saturation and shedding both trend up, fix sink throughput before raising queue size.
