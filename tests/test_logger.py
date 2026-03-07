import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any, Dict, List, cast

from microlog import (
    FileConfig,
    LogConfig,
    OTLPConfig,
    StdoutConfig,
    configure_logging,
    get_runtime_stats,
    get_logger,
    log_context,
    reset_runtime_stats,
    shutdown_logging,
)
import microlog.logger as microlog_logger
import microlog.otel as microlog_otel
from microlog.logger import DevColorFormatter, JsonFormatter
from logging.handlers import RotatingFileHandler

pytest = cast(Any, __import__("pytest"))


def flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        if hasattr(handler, "flush"):
            try:
                handler.flush()
            except Exception:
                pass


def read_json_lines(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def stub_http_otlp_exporter(monkeypatch: Any):
    class DummyExporter:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.exporter.otlp.proto.http._log_exporter",
        SimpleNamespace(OTLPLogExporter=DummyExporter),
    )
    return DummyExporter


def test_configure_logging_requires_handler() -> None:
    cfg = LogConfig(stdout=None, file=None)
    with pytest.raises(ValueError):
        configure_logging(cfg)


def test_stdout_handler_level_override(monkeypatch) -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="DEBUG"), file=None, async_mode=False)
    configure_logging(cfg)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.level == logging.DEBUG
    assert isinstance(handler.formatter, JsonFormatter)


def test_file_handler_created(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=False)
    configure_logging(cfg)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert log_path.exists()
    assert handler.baseFilename == str(log_path)


def test_file_logging_writes_json(tmp_path: Path) -> None:
    log_path = tmp_path / "records" / "app.log"
    cfg = LogConfig(
        service_name="orders",
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=False,
        static={"team": "core"},
    )
    configure_logging(cfg)
    logger = get_logger("orders-service", cfg)
    with log_context(request_id="req-1", user_id="user-7"):
        logger.info("checkout", extra={"password": "secret", "cart_id": "cart-9"})
    flush_handlers()
    entries = read_json_lines(log_path)
    assert entries
    record = entries[-1]
    assert record["body"] == "checkout"
    assert record["request_id"] == "req-1"
    assert record["user_id"] == "user-7"
    assert record["cart_id"] == "cart-9"
    assert record["password"] == "***"
    assert record["service.name"] == "orders"
    assert record["team"] == "core"


def test_reserved_fields_cannot_be_overridden(tmp_path: Path) -> None:
    log_path = tmp_path / "reserved" / "app.log"
    cfg = LogConfig(
        service_name="orders",
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=False,
        static={"service.name": "evil", "body": "static-body", "team": "core"},
    )
    configure_logging(cfg)
    logger = get_logger("orders-service", cfg)
    with log_context(body="ctx-body", severity_text="TRACE", request_id="req-1"):
        logger.info(
            "checkout",
            extra={"body": "extra-body", "service.name": "evil", "severity_number": 999},
        )
    flush_handlers()
    record = read_json_lines(log_path)[-1]
    assert record["body"] == "checkout"
    assert record["service.name"] == "orders"
    assert record["severity_text"] == "INFO"
    assert record["severity_number"] == 9
    assert record["request_id"] == "req-1"
    assert record["team"] == "core"


def test_stdlib_logger_extra_still_works_without_adapter(tmp_path: Path) -> None:
    log_path = tmp_path / "stdlib" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=False)
    configure_logging(cfg)
    logging.getLogger("svc").info(
        "plain",
        extra={"request_id": "req-1", "body": "spoofed", "cart_id": "cart-9"},
    )
    flush_handlers()
    record = read_json_lines(log_path)[-1]
    assert record["body"] == "plain"
    assert record["request_id"] == "req-1"
    assert record["cart_id"] == "cart-9"


def test_redaction_applies_to_nested_data_and_message(tmp_path: Path) -> None:
    class Custom:
        def __str__(self) -> str:
            return "custom-value"

    log_path = tmp_path / "scrub" / "app.log"
    cfg = LogConfig(
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=False,
        redact_value_patterns=[r"user-\d+"],
    )
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    with log_context(session="user-42"):
        logger.info(
            "login user-42",
            extra={
                "credentials": {"token": "abc", "nested": [{"password": "pw"}]},
                "custom": Custom(),
            },
        )
    flush_handlers()
    record = read_json_lines(log_path)[-1]
    assert record["body"] == "login ***"
    assert record["session"] == "***"
    assert record["credentials"]["token"] == "***"
    assert record["credentials"]["nested"][0]["password"] == "***"
    assert record["custom"] == "custom-value"


def test_stdout_logging_includes_context(capfd) -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="INFO"), file=None, async_mode=False)
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    with log_context(request_id="ctx-1"):
        logger.info("ping", extra={"token": "abc123"})
    flush_handlers()
    out, _ = capfd.readouterr()
    lines: List[str] = [line for line in out.splitlines() if line]
    assert lines
    record = json.loads(lines[-1])
    assert record["body"] == "ping"
    assert record["request_id"] == "ctx-1"
    assert record["token"] == "***"


def test_dev_color_formatter_applied(monkeypatch) -> None:
    class DummyStdout:
        def write(self, _: str) -> None:
            pass

        def flush(self) -> None:
            pass

        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(microlog_logger.sys, "stdout", DummyStdout())
    cfg = LogConfig(stdout=StdoutConfig(), file=None, async_mode=False, dev_color=True)
    configure_logging(cfg)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, DevColorFormatter)


def test_invalid_level_raises() -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="NOT-A-LEVEL"), file=None)
    with pytest.raises(ValueError):
        configure_logging(cfg)


def test_async_mode_flushes_to_file(tmp_path: Path) -> None:
    log_path = tmp_path / "async" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)
    logger = get_logger(None, cfg)
    logger.info("queued")
    shutdown_logging()
    assert log_path.exists()
    entries = read_json_lines(log_path)
    assert entries[-1]["body"] == "queued"


def test_logging_shutdown_flushes_async_queue(tmp_path: Path) -> None:
    log_path = tmp_path / "shutdown" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    logger.info("shutdown flush")
    logging.shutdown()
    assert log_path.exists()
    assert read_json_lines(log_path)[-1]["body"] == "shutdown flush"
    stats = get_runtime_stats()
    assert stats.queue_size == 0
    assert stats.queue_maxsize == 0


def test_async_exception_logging_preserves_structured_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "async-exc" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    try:
        1 / 0
    except ZeroDivisionError:
        logger.exception("boom")
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
    record = read_json_lines(log_path)[-1]
    assert record["body"] == "boom"
    assert record["exception.type"] == "ZeroDivisionError"
    assert record["exception.message"] == "division by zero"
    assert "ZeroDivisionError: division by zero" in record["exception.stacktrace"]
    assert "message" not in record


def test_async_stack_info_is_preserved(tmp_path: Path) -> None:
    log_path = tmp_path / "async-stack" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    logger.warning("warn", stack_info=True)
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
    record = read_json_lines(log_path)[-1]
    assert record["body"] == "warn"
    assert "Stack (most recent call last):" in record["stack"]


def test_get_logger_provides_static_metadata() -> None:
    cfg = LogConfig(static={"region": "eu-west-1"})
    adapter = get_logger(None, cfg)
    extra = adapter.extra
    assert isinstance(extra, dict)
    assert extra == {}


def test_get_logger_process_only_emits_dynamic_metadata() -> None:
    cfg = LogConfig(static={"region": "eu-west-1"})
    adapter = get_logger(None, cfg)
    with log_context(request_id="req-1"):
        _, kwargs = adapter.process("hello", {"extra": {"cart_id": "cart-9"}})
    assert kwargs["extra"]["_microlog"] == {"request_id": "req-1", "cart_id": "cart-9"}


def test_normalize_protocol_variants() -> None:
    assert microlog_otel._normalize_protocol("HTTP") == "http/protobuf"  # pyright: ignore[reportPrivateUsage]
    assert microlog_otel._normalize_protocol("grpc") == "grpc"  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError):
        microlog_otel._normalize_protocol("udp")  # pyright: ignore[reportPrivateUsage]


def test_resolve_otlp_endpoint_env_precedence(monkeypatch: Any) -> None:
    otlp_cfg = OTLPConfig(endpoint=None)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert (
        microlog_otel._resolve_otlp_endpoint("http/protobuf", otlp_cfg)  # pyright: ignore[reportPrivateUsage]
        == "http://localhost:4318/v1/logs"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector:4318/v1/logs")
    assert (
        microlog_otel._resolve_otlp_endpoint("http/protobuf", otlp_cfg)  # pyright: ignore[reportPrivateUsage]
        == "https://collector:4318/v1/logs"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "https://logs-endpoint/v1/logs")
    assert (
        microlog_otel._resolve_otlp_endpoint("http/protobuf", otlp_cfg)  # pyright: ignore[reportPrivateUsage]
        == "https://logs-endpoint/v1/logs"
    )


def test_http_otlp_exporter_keeps_tls_verification_by_default(monkeypatch: Any) -> None:
    stub_http_otlp_exporter(monkeypatch)
    exporter, cleanup = microlog_otel._build_otlp_exporter(  # pyright: ignore[reportPrivateUsage]
        "http/protobuf",
        OTLPConfig(endpoint="https://collector.example/v1/logs"),
    )
    assert cleanup is None
    assert "session" not in exporter.kwargs


def test_http_otlp_exporter_disables_tls_verification_only_when_explicit(
    monkeypatch: Any,
) -> None:
    stub_http_otlp_exporter(monkeypatch)
    exporter, cleanup = microlog_otel._build_otlp_exporter(  # pyright: ignore[reportPrivateUsage]
        "http/protobuf",
        OTLPConfig(endpoint="https://collector.example/v1/logs", insecure=True),
    )
    assert cleanup is not None
    assert exporter.kwargs["session"].verify is False
    cleanup()


def test_otlp_handler_resolves_level_without_optional_sdk(monkeypatch: Any) -> None:
    class DummyExporter:
        pass

    class DummyProcessor:
        def __init__(self, exporter: Any) -> None:
            self.exporter = exporter

    class DummyProvider:
        def __init__(self, resource: dict[str, Any]) -> None:
            self.resource = resource
            self.processors: list[Any] = []

        def add_log_record_processor(self, processor: Any) -> None:
            self.processors.append(processor)

        def shutdown(self) -> None:
            pass

    class DummyHandler(logging.Handler):
        def __init__(self, *, level: int = logging.NOTSET, logger_provider: Any) -> None:
            super().__init__(level)
            self.logger_provider = logger_provider

    class DummyResource:
        @staticmethod
        def create(attrs: dict[str, Any]) -> dict[str, Any]:
            return attrs

    monkeypatch.setattr(
        microlog_otel,
        "_build_otlp_exporter",
        lambda *_args: (DummyExporter(), None),
    )
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.sdk._logs",
        SimpleNamespace(LoggerProvider=DummyProvider, LoggingHandler=DummyHandler),
    )
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.sdk._logs.export",
        SimpleNamespace(BatchLogRecordProcessor=DummyProcessor),
    )
    monkeypatch.setitem(
        sys.modules,
        "opentelemetry.sdk.resources",
        SimpleNamespace(Resource=DummyResource),
    )

    handler = microlog_otel._otlp_handler(  # pyright: ignore[reportPrivateUsage]
        LogConfig(stdout=None, file=None, static={"region": "eu-west-1"}),
        OTLPConfig(endpoint="http://collector:4318/v1/logs", level="ERROR"),
    )
    assert handler.level == logging.ERROR
    assert getattr(handler, "logger_provider").resource["service.name"] == "app"
    handler.close()


def test_otlp_record_filter_merges_static_and_dynamic_fields() -> None:
    record = logging.makeLogRecord({"request_id": "top-level", "_microlog": {"cart_id": "cart-9"}})
    filt = microlog_otel._OTLPRecordFilter({"region": "eu-west-1", "request_id": "static"})  # pyright: ignore[reportPrivateUsage]
    assert filt.filter(record) is True
    assert record.region == "eu-west-1"
    assert record.request_id == "top-level"
    assert record.cart_id == "cart-9"


def test_otlp_http_export_reaches_collector() -> None:
    received: Queue[tuple[str, str, bytes, dict[str, str]]] = Queue()

    class CollectorHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.put((self.command, self.path, body, dict(self.headers.items())))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, _format: str, *args: Any) -> None:
            _ = args

    server = HTTPServer(("127.0.0.1", 0), CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
    cfg = LogConfig(
        service_name="orders",
        stdout=None,
        file=None,
        async_mode=False,
        otlp=OTLPConfig(endpoint=endpoint, timeout=5.0),
        otlp_fail_open=False,
    )

    try:
        try:
            configure_logging(cfg)
        except RuntimeError as exc:
            pytest.skip(f"OpenTelemetry HTTP OTLP dependencies not installed: {exc}")
        logger = get_logger("svc", cfg)
        with log_context(request_id="req-otel"):
            logger.info("collector-ready", extra={"order_id": "ord-1"})
        logging.shutdown()
        method, path, body, headers = received.get(timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert method == "POST"
    assert path == "/v1/logs"
    assert body
    assert headers["Content-Type"] == "application/x-protobuf"
    assert len(body) > 32


def test_otlp_handler_configures_when_otel_available(monkeypatch: Any) -> None:
    try:
        from opentelemetry.sdk._logs import LoggingHandler as OTLoggingHandler  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dependency handling
        pytest.skip("OpenTelemetry dependencies not installed")
        return

    class DummyExporter:
        def shutdown(self) -> None:
            pass

    class DummyProcessor:
        def __init__(self, exporter: Any) -> None:
            self.exporter = exporter
            self.shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

        def force_flush(self, timeout_millis: int = 0) -> bool:
            return True

    monkeypatch.setattr(microlog_otel, "_build_otlp_exporter", lambda *_: (DummyExporter(), None))  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        "opentelemetry.sdk._logs.export.BatchLogRecordProcessor", DummyProcessor, raising=False
    )
    cfg = LogConfig(
        stdout=None,
        file=None,
        async_mode=False,
        otlp=OTLPConfig(endpoint="http://localhost:4318/v1/logs", level="ERROR"),
    )
    configure_logging(cfg)
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, OTLoggingHandler) for h in handlers)
    assert any(h.level == logging.ERROR for h in handlers if isinstance(h, OTLoggingHandler))


def test_bounded_queue_handler_drops_oldest() -> None:
    reset_runtime_stats()
    q: Queue[logging.LogRecord] = Queue(maxsize=1)
    handler = microlog_logger._BoundedQueueHandler(q, drop_oldest=True)  # pyright: ignore[reportPrivateUsage]
    first = logging.makeLogRecord({"msg": "first"})
    second = logging.makeLogRecord({"msg": "second"})
    handler.enqueue(first)
    handler.enqueue(second)
    assert q.qsize() == 1
    stored = q.get()
    assert stored.msg == "second"
    stats = get_runtime_stats()
    assert stats.queued_records == 2
    assert stats.dropped_records == 1
    assert stats.dropped_oldest_records == 1


def test_bounded_queue_handler_drops_newest_when_configured() -> None:
    reset_runtime_stats()
    q: Queue[logging.LogRecord] = Queue(maxsize=1)
    handler = microlog_logger._BoundedQueueHandler(q, drop_oldest=False)  # pyright: ignore[reportPrivateUsage]
    first = logging.makeLogRecord({"msg": "first"})
    second = logging.makeLogRecord({"msg": "second"})
    handler.enqueue(first)
    handler.enqueue(second)
    assert q.qsize() == 1
    stored = q.get()
    assert stored.msg == "first"
    stats = get_runtime_stats()
    assert stats.queued_records == 1
    assert stats.dropped_records == 1
    assert stats.dropped_oldest_records == 0


def test_async_queue_configuration_applies_bounded_handler() -> None:
    cfg = LogConfig(stdout=StdoutConfig(), file=None, async_mode=True, async_queue_size=5)
    configure_logging(cfg)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, microlog_logger._BoundedQueueHandler)  # pyright: ignore[reportPrivateUsage]
    assert handler.queue.maxsize == 5
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]


def test_runtime_stats_track_unbounded_async_queue(tmp_path: Path) -> None:
    reset_runtime_stats()
    log_path = tmp_path / "runtime" / "app.log"
    cfg = LogConfig(
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=True,
        async_queue_size=0,
    )
    configure_logging(cfg)
    logger = get_logger(None, cfg)
    logger.info("runtime")
    stats = get_runtime_stats()
    assert stats.queued_records >= 1
    assert stats.dropped_records == 0
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]


def test_adaptive_shedding_drops_low_severity_under_pressure() -> None:
    reset_runtime_stats()
    q: Queue[logging.LogRecord] = Queue(maxsize=10)
    for i in range(9):
        q.put(logging.makeLogRecord({"msg": f"prefill-{i}", "levelno": logging.INFO}))
    handler = microlog_logger._BoundedQueueHandler(  # pyright: ignore[reportPrivateUsage]
        q,
        drop_oldest=False,
        shed_below_level=logging.WARNING,
        shed_when_queue_above=0.8,
        shed_rate=0.0,
    )
    handler.enqueue(logging.makeLogRecord({"msg": "shed-me", "levelno": logging.INFO}))
    assert q.qsize() == 9
    stats = get_runtime_stats()
    assert stats.shed_records == 1
    assert stats.dropped_records == 1


def test_adaptive_shedding_preserves_high_severity() -> None:
    reset_runtime_stats()
    q: Queue[logging.LogRecord] = Queue(maxsize=10)
    for i in range(9):
        q.put(logging.makeLogRecord({"msg": f"prefill-{i}", "levelno": logging.INFO}))
    handler = microlog_logger._BoundedQueueHandler(  # pyright: ignore[reportPrivateUsage]
        q,
        drop_oldest=False,
        shed_below_level=logging.WARNING,
        shed_when_queue_above=0.8,
        shed_rate=0.0,
    )
    handler.enqueue(logging.makeLogRecord({"msg": "keep-me", "levelno": logging.ERROR}))
    assert q.qsize() == 10
    stats = get_runtime_stats()
    assert stats.shed_records == 0
    assert stats.queued_records == 1


def test_enable_disable_otel_runtime_metrics_no_crash() -> None:
    enabled = microlog_otel.enable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]
    assert isinstance(enabled, bool)
    microlog_otel.disable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]


def test_enable_otel_runtime_metrics_registers_each_meter_once(monkeypatch: Any) -> None:
    class Observation:
        def __init__(self, value: int, attributes: dict[str, str]) -> None:
            self.value = value
            self.attributes = attributes

    class DummyMeter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def create_observable_counter(self, name: str, **_kwargs: Any) -> object:
            self.calls.append(name)
            return object()

        def create_observable_gauge(self, name: str, **_kwargs: Any) -> object:
            self.calls.append(name)
            return object()

    microlog_otel.disable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_attrs.clear()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_handles.clear()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_meters.clear()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setitem(
        sys.modules, "opentelemetry.metrics", SimpleNamespace(Observation=Observation)
    )

    first = DummyMeter()
    second = DummyMeter()
    assert microlog_otel.enable_otel_runtime_metrics(meter=first) is True  # pyright: ignore[reportPrivateUsage]
    assert microlog_otel.enable_otel_runtime_metrics(meter=first) is True  # pyright: ignore[reportPrivateUsage]
    assert microlog_otel.enable_otel_runtime_metrics(meter=second) is True  # pyright: ignore[reportPrivateUsage]

    assert len(first.calls) == 5
    assert len(second.calls) == 5
    microlog_otel.disable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]


def test_enable_otel_runtime_metrics_keeps_attributes_per_meter(monkeypatch: Any) -> None:
    class Observation:
        def __init__(self, value: int, attributes: dict[str, str]) -> None:
            self.value = value
            self.attributes = attributes

    class DummyMeter:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def create_observable_counter(self, _name: str, **kwargs: Any) -> object:
            self.callbacks.append(kwargs["callbacks"][0])
            return object()

        def create_observable_gauge(self, _name: str, **kwargs: Any) -> object:
            self.callbacks.append(kwargs["callbacks"][0])
            return object()

    microlog_otel.disable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_attrs.clear()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_handles.clear()  # pyright: ignore[reportPrivateUsage]
    microlog_otel._runtime_metrics_meters.clear()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setitem(
        sys.modules, "opentelemetry.metrics", SimpleNamespace(Observation=Observation)
    )

    first = DummyMeter()
    second = DummyMeter()
    assert microlog_otel.enable_otel_runtime_metrics(  # pyright: ignore[reportPrivateUsage]
        meter=first,
        attributes={"service.name": "one"},
    )
    assert microlog_otel.enable_otel_runtime_metrics(  # pyright: ignore[reportPrivateUsage]
        meter=second,
        attributes={"service.name": "two"},
    )

    first_obs = first.callbacks[0](None)[0]
    second_obs = second.callbacks[0](None)[0]
    assert first_obs.attributes == {"service.name": "one"}
    assert second_obs.attributes == {"service.name": "two"}
    microlog_otel.disable_otel_runtime_metrics()  # pyright: ignore[reportPrivateUsage]


def test_otlp_fail_open_continues_with_other_handlers(monkeypatch: Any) -> None:
    def _raise_otlp(*_args: Any, **_kwargs: Any) -> logging.Handler:
        raise RuntimeError("collector down")

    monkeypatch.setattr(microlog_otel, "_otlp_handler", _raise_otlp)  # pyright: ignore[reportPrivateUsage]
    cfg = LogConfig(
        stdout=StdoutConfig(level="INFO"),
        file=None,
        async_mode=False,
        otlp=OTLPConfig(endpoint="http://collector:4318/v1/logs"),
        otlp_fail_open=True,
    )
    with pytest.warns(RuntimeWarning, match="OTLP handler setup failed"):
        configure_logging(cfg)
    handlers = logging.getLogger().handlers
    assert handlers
    assert all(not isinstance(h, logging.NullHandler) for h in handlers)


def test_otlp_fail_closed_raises(monkeypatch: Any) -> None:
    def _raise_otlp(*_args: Any, **_kwargs: Any) -> logging.Handler:
        raise RuntimeError("collector down")

    monkeypatch.setattr(microlog_otel, "_otlp_handler", _raise_otlp)  # pyright: ignore[reportPrivateUsage]
    cfg = LogConfig(
        stdout=StdoutConfig(level="INFO"),
        file=None,
        async_mode=False,
        otlp=OTLPConfig(endpoint="http://collector:4318/v1/logs"),
        otlp_fail_open=False,
    )
    with pytest.raises(RuntimeError, match="collector down"):
        configure_logging(cfg)


def test_configure_logging_failure_preserves_existing_handlers() -> None:
    cfg = LogConfig(stdout=StdoutConfig(level="INFO"), file=None, async_mode=False)
    configure_logging(cfg)
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)

    with pytest.raises(ValueError, match="Unknown log level"):
        configure_logging(LogConfig(stdout=StdoutConfig(level="NOPE"), file=None, async_mode=False))

    assert root.level == original_level
    assert root.handlers == original_handlers


def test_configure_logging_allows_notset_root_level() -> None:
    cfg = LogConfig(
        level=logging.NOTSET, stdout=StdoutConfig(level="INFO"), file=None, async_mode=False
    )
    configure_logging(cfg)
    assert logging.getLogger().level == logging.NOTSET


def test_async_mode_remains_non_blocking_with_slow_sink(monkeypatch: Any) -> None:
    class SlowHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            _ = record
            time.sleep(0.05)

    monkeypatch.setattr(
        microlog_logger,
        "_build_handlers",
        lambda _cfg: [SlowHandler()],
    )  # pyright: ignore[reportPrivateUsage]
    cfg = LogConfig(
        stdout=StdoutConfig(level="INFO"), file=None, async_mode=True, async_queue_size=100
    )
    configure_logging(cfg)
    logger = get_logger(None, cfg)
    start = time.perf_counter()
    logger.info("fast path")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.02
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]


def test_async_logging_handles_thread_contention_without_context_leak(tmp_path: Path) -> None:
    reset_runtime_stats()
    log_path = tmp_path / "threaded" / "app.log"
    workers = 8
    per_worker = 250
    total = workers * per_worker
    cfg = LogConfig(
        stdout=None,
        file=FileConfig(path=str(log_path)),
        async_mode=True,
        async_queue_size=total + 32,
        async_queue_drop_oldest=False,
    )
    configure_logging(cfg)
    logger = get_logger("svc", cfg)
    barrier = threading.Barrier(workers)
    errors: list[Exception] = []

    def _worker(worker_id: int) -> None:
        try:
            barrier.wait()
            with log_context(worker=f"worker-{worker_id}"):
                for seq in range(per_worker):
                    logger.info("threaded", extra={"seq": seq, "entry_id": f"{worker_id}:{seq}"})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(worker_id,)) for worker_id in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]

    entries = read_json_lines(log_path)
    assert len(entries) == total
    stats = get_runtime_stats()
    assert stats.queued_records == total
    assert stats.dropped_records == 0

    seen = {(entry["worker"], entry["seq"]) for entry in entries}
    assert len(seen) == total
    for worker_id in range(workers):
        worker = f"worker-{worker_id}"
        assert sum(1 for entry in entries if entry["worker"] == worker) == per_worker
        assert {
            entry["seq"]
            for entry in entries
            if entry["worker"] == worker and entry["entry_id"].startswith(f"{worker_id}:")
        } == set(range(per_worker))


def test_stop_listener_is_race_safe(tmp_path: Path) -> None:
    log_path = tmp_path / "race" / "app.log"
    cfg = LogConfig(stdout=None, file=FileConfig(path=str(log_path)), async_mode=True)
    configure_logging(cfg)

    errors: list[Exception] = []

    def _worker() -> None:
        for _ in range(25):
            try:
                microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
