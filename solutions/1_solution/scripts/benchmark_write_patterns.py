#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from statistics import mean

import asyncpg

from solution1.core.defaults import DEFAULT_USER1_API_KEY
from solution1.db.repository import (
    admin_update_user_credits,
    admin_update_user_credits_transactional,
)

USER1_KEY = os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = round((len(ordered) - 1) * q)
    return ordered[idx]


async def _reset_user_credits(pool: asyncpg.Pool, *, api_key: str, credits: int) -> None:
    await pool.execute(
        "UPDATE users SET credits=$1, updated_at=now() WHERE api_key=$2",
        credits,
        api_key,
    )


async def _run_benchmark_variant(
    pool: asyncpg.Pool,
    *,
    variant: str,
    iterations: int,
    concurrency: int,
) -> dict[str, float | str | int]:
    latencies_ms: list[float] = []
    lock = asyncio.Lock()
    start = time.perf_counter()

    if variant == "single_statement":
        op = admin_update_user_credits
    else:
        op = admin_update_user_credits_transactional

    async def _one_call() -> None:
        call_started = time.perf_counter()
        await op(
            pool,
            target_api_key=USER1_KEY,
            delta=1,
            reason=f"bench_{variant}",
        )
        latency_ms = (time.perf_counter() - call_started) * 1000.0
        async with lock:
            latencies_ms.append(latency_ms)

    semaphore = asyncio.Semaphore(concurrency)

    async def _worker() -> None:
        async with semaphore:
            await _one_call()

    await asyncio.gather(*[_worker() for _ in range(iterations)])
    duration = time.perf_counter() - start
    throughput = iterations / duration if duration > 0 else 0.0

    return {
        "variant": variant,
        "iterations": iterations,
        "concurrency": concurrency,
        "duration_seconds": round(duration, 4),
        "throughput_ops_per_sec": round(throughput, 4),
        "latency_ms_p50": round(_percentile(latencies_ms, 0.5), 4),
        "latency_ms_p95": round(_percentile(latencies_ms, 0.95), 4),
        "latency_ms_avg": round(mean(latencies_ms), 4) if latencies_ms else 0.0,
    }


async def main_async(args: argparse.Namespace) -> int:
    pool = await asyncpg.create_pool(
        dsn=str(args.postgres_dsn),
        min_size=1,
        max_size=max(2, int(args.pool_max_size)),
        command_timeout=float(args.command_timeout_seconds),
    )
    try:
        await _reset_user_credits(pool, api_key=USER1_KEY, credits=0)
        single_statement = await _run_benchmark_variant(
            pool,
            variant="single_statement",
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
        await _reset_user_credits(pool, api_key=USER1_KEY, credits=0)
        transactional = await _run_benchmark_variant(
            pool,
            variant="transactional_two_statement",
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
        await _reset_user_credits(pool, api_key=USER1_KEY, credits=100)

        report = {
            "generated_at_epoch": int(time.time()),
            "iterations": args.iterations,
            "concurrency": args.concurrency,
            "results": [single_statement, transactional],
            "winner": (
                "single_statement"
                if single_statement["throughput_ops_per_sec"]
                >= transactional["throughput_ops_per_sec"]
                else "transactional_two_statement"
            ),
        }

        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = (Path(__file__).resolve().parents[1] / output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 0
    finally:
        await pool.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark write-pattern variants for admin credit updates."
    )
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument(
        "--postgres-dsn",
        default=os.getenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres"),
    )
    parser.add_argument("--pool-max-size", type=int, default=10)
    parser.add_argument("--command-timeout-seconds", type=float, default=5.0)
    parser.add_argument(
        "--output",
        default="worklog/evidence/load/latest-write-pattern-benchmark.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
