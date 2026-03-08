Threat model
1. Overview
microlog is a lightweight Python logging library intended for microservices and serverless services. It wraps the standard logging module to emit JSON logs to stdout or files and can optionally export logs to OpenTelemetry collectors (OTLP). It runs entirely in-process and does not expose network listeners itself.

Key components and data flow:

microlog/config.py defines configuration objects (LogConfig, FileConfig, OTLPConfig), reserved field protection, and default redaction keys.
microlog/adapter.py implements context propagation (log_context) and structured field merging (ContextAdapter).
microlog/logger.py formats records as JSON, applies redaction, manages async queueing, and builds stdout/file/OTLP handlers.
microlog/otel.py manages OTLP exporter setup, endpoint resolution, TLS/insecure handling, and runtime metrics hooks.
microlog/presets.py provides production defaults (async queue sizes, shedding thresholds, and OTLP enablement).
The primary security objective is to prevent log spoofing and limit sensitive data leakage while keeping logging non-blocking and resilient under load.

2. Threat model, Trust boundaries and assumptions
Trust boundaries
Attacker-controlled inputs: log message strings, extra fields, log_context attributes, and exception messages/stack traces that originate from untrusted user requests. These values flow into JSON log payloads (logger.py).
Operator-controlled inputs: LogConfig and OTLPConfig values (file paths, OTLP endpoints, headers, insecure flag), queue sizing and shedding parameters, redaction patterns, and environment variables such as OTEL_EXPORTER_OTLP_LOGS_ENDPOINT and OTEL_EXPORTER_OTLP_ENDPOINT.
Developer-controlled inputs: application code deciding what to log and at what severity, use of log_context, and test/benchmark/example code (not production runtime paths).
External boundary: OTLP collectors are external services. Logs sent over the network cross a trust boundary and should be treated as sensitive data in transit.
Assumptions
Logging configuration is set at startup by trusted operators/developers and is not attacker-controlled.
Log storage backends and OTLP collectors are trusted and access-controlled.
The host environment (filesystem permissions, environment variables, container configuration) is secured by operators.
Optional OpenTelemetry dependencies are installed only when needed.
These assumptions mean many configuration-based attacks (e.g., changing OTLP endpoints) are operator-risk issues rather than remote attacker issues. If your environment allows untrusted users to influence configuration or environment variables, elevate those risks.

3. Attack surface, mitigations and attacker stories
Logging API and structured fields
Surface: log_context and extra fields are merged into log records. Untrusted user input can become structured log fields (adapter.py, logger.py).

Mitigations:

safe_fields and RESERVED_FIELDS (config.py) strip protected keys so attackers cannot override body, severity_text, service.name, etc.
JSON encoding via _json_dumps ensures newlines/quotes are escaped, reducing log forging in JSON sinks.
Default redact_keys and optional regex-based redact_value_patterns scrub common secrets (logger.py).
Attacker story: an attacker submits a request containing {"severity_text": "CRITICAL", "body": "..."} that the application logs. Reserved-field filtering prevents spoofing of core metadata. However, if the app logs raw request bodies, sensitive values can still be exposed unless redaction is configured.

Data leakage and privacy
Surface: log messages, exception stack traces, static fields, and OTLP headers/resource attributes may contain secrets or PII.

Mitigations: redaction of common secret keys and regex patterns; safe_fields prevents overriding reserved metadata; scrubbing is applied to nested structures.

Residual risk: secrets embedded in message strings or in fields not covered by redact_keys are not scrubbed by default. Exception messages and custom objects are stringified and may leak sensitive data.

Attacker story: a user triggers an error where the exception message contains a token. The stack trace and message are logged and exported to OTLP, exposing credentials in log storage unless redaction or application-level filtering is used.

Availability and log-volume abuse
Surface: high-volume logging from untrusted traffic can saturate the async queue (_BoundedQueueHandler in logger.py).

Mitigations: bounded queues, drop-oldest/drop-new policies, and adaptive shedding (shed_below_level, shed_rate) protect the process from backpressure.

Residual risk: if operators set async_queue_size=0 (unbounded), attackers can trigger memory growth. If shedding is misconfigured, security-relevant logs may be dropped, weakening forensic visibility.

Attacker story: a bot floods a service with requests that generate many INFO logs. The queue saturates and low-severity logs are dropped. If security events are logged at low severity, attackers may hide activity during the flood.

File logging and filesystem boundaries
Surface: FileConfig.path controls where logs are written; directories are created automatically (logger.py).

Mitigations: the path is operator-controlled; errors are raised if directory creation fails.

Residual risk: no explicit permission hardening or symlink checks. If an attacker can influence log paths or the log directory, they could clobber or redirect log files.

Attacker story: in a misconfigured multi-tenant host, a local attacker influences the log path to a symlinked sensitive file. This is typically out of scope because configuration is trusted, but it is high impact if that assumption breaks.

OTLP export and network boundary
Surface: OTLPConfig resolves endpoints from config or environment and sends logs to external collectors (otel.py).

Mitigations:

_normalize_protocol restricts protocols.
_resolve_otlp_insecure defaults to secure TLS behavior unless explicitly set.
_OTLPRecordFilter strips reserved fields before export.
Residual risk: if OTEL_EXPORTER_OTLP_LOGS_ENDPOINT or OTLPConfig.endpoint can be influenced, logs may be exfiltrated. Setting insecure=True disables TLS verification for HTTP exporters, enabling MITM.

Attacker story: a compromised deployment pipeline injects an attacker-controlled OTLP endpoint, resulting in exfiltration of all logs and potentially sensitive data. This is an operator compromise rather than a remote input bug.

Runtime metrics exposure
Surface: enabling OpenTelemetry runtime metrics exposes queue size/drops to the metrics backend (otel.py).

Mitigation: metrics are opt-in; no metrics are emitted by default.

Residual risk: if metrics endpoints are public, attackers could infer load or log drop patterns.

Out-of-scope / not applicable
No authentication/authorization, sessions, CSRF, or XSS handling in this library.
No deserialization of untrusted network payloads; the library only processes what the application chooses to log.
4. Criticality calibration (critical, high, medium, low)
Critical

Remote code execution or arbitrary file write from untrusted input (not present today). Example: if log payload handling executed attacker-controlled code.
Attacker-controlled configuration that silently redirects all logs (including secrets) to a hostile endpoint without operator awareness.
Ability to override reserved fields (severity_text, service.name, body) to forge audit logs and hide malicious actions.
High

Systematic leakage of secrets/PII due to a redaction bypass (e.g., OTLP export skipping scrubbing, or nested fields not scrubbed).
MITM or cleartext transmission of logs caused by insecure defaults or ignoring TLS verification in OTLP export.
Unbounded logging queue leading to process crash or memory exhaustion when attackers can trigger log floods.
Medium

Loss of security-relevant logs due to aggressive shedding/queue limits, reducing forensic visibility.
Log injection that confuses downstream parsing (e.g., control characters in dev formatter or very high-cardinality fields).
Exposure of internal metadata such as hostname/pid when logs are shared beyond intended audiences.
Low

Minor mislabeling of severity numbers or missing runtime metrics.
Log rotation/flush inconsistencies due to configuration errors.
Incomplete redaction patterns for niche formats where the operator can mitigate.