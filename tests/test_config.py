import importlib
from typing import Any, cast

from microlog import FileConfig, LogConfig, StdoutConfig
from microlog.config import severity_number

pytest = cast(Any, importlib.import_module("pytest"))


def test_default_stdout_enabled():
    cfg = LogConfig()
    assert isinstance(cfg.stdout, StdoutConfig)
    assert cfg.file is None
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
    from microlog import StdoutConfig as exported_stdout_config

    assert exported_file_config is FileConfig
    assert exported_stdout_config is StdoutConfig
