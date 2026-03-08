from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import benchmarks.benchmark as benchmark

pytest = cast(Any, __import__("pytest"))


class _DummyLogger:
    def info(self, _msg: str, *, extra: dict[str, Any]) -> None:
        assert "i" in extra


def test_async_benchmark_uses_bounded_queue(monkeypatch: Any) -> None:
    seen_cfgs: list[Any] = []
    stopped = False
    ticks = iter((10.0, 10.5))

    monkeypatch.setattr(benchmark, "configure_logging", lambda cfg: seen_cfgs.append(cfg))
    monkeypatch.setattr(benchmark, "get_logger", lambda *_args: _DummyLogger())
    monkeypatch.setattr(benchmark, "reset_runtime_stats", lambda: None)
    monkeypatch.setattr(
        benchmark,
        "get_runtime_stats",
        lambda: SimpleNamespace(dropped_records=0, shed_records=0),
    )
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(ticks))

    def _stop_listener() -> None:
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(benchmark.microlog_logger, "_stop_listener", _stop_listener)

    result = benchmark._run("async", 3)

    assert stopped is True
    assert seen_cfgs
    cfg = seen_cfgs[0]
    assert cfg.async_mode is True
    assert cfg.async_queue_size == 10_000
    assert result.mode == "async"
    assert result.count == 3
    assert result.logs_per_sec == pytest.approx(6.0)
