from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass

import microlog.logger as microlog_logger
from microlog import (
    LogConfig,
    StdoutConfig,
    configure_logging,
    get_logger,
    get_runtime_stats,
    reset_runtime_stats,
)


class _NullStream:
    def write(self, _: str) -> int:
        return 0

    def flush(self) -> None:
        return

    def isatty(self) -> bool:
        return False


@dataclass(slots=True, frozen=True)
class BenchResult:
    mode: str
    count: int
    elapsed_s: float
    logs_per_sec: float
    dropped_records: int
    shed_records: int


def _run(mode: str, count: int) -> BenchResult:
    original_stdout = microlog_logger.sys.stdout
    microlog_logger.sys.stdout = _NullStream()
    try:
        cfg = LogConfig(
            service_name=f"bench-{mode}",
            level="INFO",
            stdout=StdoutConfig(level="INFO"),
            file=None,
            async_mode=(mode == "async"),
            async_queue_size=0 if mode == "async" else 10_000,
            async_queue_drop_oldest=True,
            include_code=False,
            include_host=False,
            include_pid=False,
            include_thread=False,
            try_opentelemetry=False,
        )
        reset_runtime_stats()
        configure_logging(cfg)
        logger = get_logger("bench", cfg)
        start = time.perf_counter()
        for i in range(count):
            logger.info("bench message", extra={"i": i})
        if mode == "async":
            microlog_logger._stop_listener()  # pyright: ignore[reportPrivateUsage]
        elapsed = max(time.perf_counter() - start, 1e-9)
        stats = get_runtime_stats()
        return BenchResult(
            mode=mode,
            count=count,
            elapsed_s=elapsed,
            logs_per_sec=count / elapsed,
            dropped_records=stats.dropped_records,
            shed_records=stats.shed_records,
        )
    finally:
        microlog_logger.sys.stdout = original_stdout


def _validate(
    results: list[BenchResult], min_sync_rps: float, min_async_rps: float, max_drop_rate: float
) -> list[str]:
    failures: list[str] = []
    by_mode = {result.mode: result for result in results}
    sync = by_mode["sync"]
    async_result = by_mode["async"]
    if sync.logs_per_sec < min_sync_rps:
        failures.append(f"sync logs/sec {sync.logs_per_sec:.2f} < {min_sync_rps:.2f}")
    if async_result.logs_per_sec < min_async_rps:
        failures.append(f"async logs/sec {async_result.logs_per_sec:.2f} < {min_async_rps:.2f}")
    for result in (sync, async_result):
        drop_rate = result.dropped_records / max(result.count, 1)
        if drop_rate > max_drop_rate:
            failures.append(f"{result.mode} drop rate {drop_rate:.4f} > {max_drop_rate:.4f}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run microlog throughput benchmark and optional guard."
    )
    parser.add_argument("--count", type=int, default=50_000)
    parser.add_argument("--min-sync-rps", type=float, default=0.0)
    parser.add_argument("--min-async-rps", type=float, default=0.0)
    parser.add_argument("--max-drop-rate", type=float, default=1.0)
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    results = [_run("sync", args.count), _run("async", args.count)]
    payload = {
        "results": [asdict(result) for result in results],
        "guard": {
            "min_sync_rps": args.min_sync_rps,
            "min_async_rps": args.min_async_rps,
            "max_drop_rate": args.max_drop_rate,
        },
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    print(json.dumps(payload, indent=2))

    failures = _validate(results, args.min_sync_rps, args.min_async_rps, args.max_drop_rate)
    if failures:
        print("benchmark guard failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
