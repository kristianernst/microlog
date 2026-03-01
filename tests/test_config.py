import importlib
from typing import Any, cast

from microlog import FileConfig, LogConfig, OTLPConfig, StdoutConfig, production_config
from microlog.config import severity_number

pytest = cast(Any, importlib.import_module("pytest"))


def test_default_stdout_enabled():
    cfg = LogConfig()
    assert isinstance(cfg.stdout, StdoutConfig)
    assert cfg.file is None
    assert cfg.otlp_fail_open is True
    assert cfg.level == "INFO"
    assert cfg.utc is True


def test_disable_stdout():
    cfg = LogConfig(stdout=None)
    assert cfg.stdout is None
    assert cfg.file is None


def test_file_config_requires_path():
    cfg = LogConfig(stdout=None, file=FileConfig(path="/tmp/log.jsonl"))
    assert cfg.file is not None
    assert cfg.file.path.endswith("log.jsonl")


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (-1, 1),
        (0, 1),
        (5, 5),
        (20, 9),
        (30, 13),
        (40, 17),
        (50, 21),
        (90, 21),
    ],
)
def test_severity_number(level: int, expected: int):
    assert severity_number(level) == expected


def test_public_reexports_available():
    from microlog import FileConfig as exported_file_config
    from microlog import RuntimeStats as exported_runtime_stats
    from microlog import StdoutConfig as exported_stdout_config
    from microlog import enable_otel_runtime_metrics as exported_enable_metrics
    from microlog import production_config as exported_production_config

    assert exported_file_config is FileConfig
    assert exported_stdout_config is StdoutConfig
    assert exported_runtime_stats.__name__ == "RuntimeStats"
    assert callable(exported_enable_metrics)
    assert callable(exported_production_config)


def test_production_config_defaults():
    cfg = production_config("orders")
    assert cfg.service_name == "orders"
    assert cfg.async_mode is True
    assert cfg.stdout is not None
    assert cfg.file is None
    assert cfg.otlp is None
    assert cfg.otlp_fail_open is True
    assert cfg.shed_below_level == "WARNING"
    assert cfg.shed_when_queue_above == 0.85
    assert cfg.shed_rate == 0.2


def test_production_config_otlp_and_file():
    cfg = production_config(
        "orders",
        file_path="/tmp/orders.log",
        file_level="DEBUG",
        enable_otlp=True,
        otlp_endpoint="http://collector:4318/v1/logs",
        otlp_headers={"x-api-key": "abc"},
        otlp_timeout=3.5,
    )
    assert cfg.file is not None
    assert cfg.file.path == "/tmp/orders.log"
    assert cfg.file.level == "DEBUG"
    assert isinstance(cfg.otlp, OTLPConfig)
    assert cfg.otlp.endpoint == "http://collector:4318/v1/logs"
    assert cfg.otlp.headers["x-api-key"] == "abc"
    assert cfg.otlp.timeout == 3.5


def test_production_config_requires_sink():
    with pytest.raises(ValueError):
        production_config("orders", enable_stdout=False)
