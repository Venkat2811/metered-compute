#!/usr/bin/env python3
"""Sustained load test — fire at target RPS for N seconds, report latencies.

Separate from make prove. Tests sustained throughput at a target rate.

Usage:
    python scripts/loadtest.py
    python scripts/loadtest.py --rps 100 --duration 30
    python scripts/loadtest.py --rps 200 --duration 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "http://localhost:8000"
TASK_COST = 10
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}

# Seed data from migrations/0002_seed.sql
USERS = [
    {
        "api_key": "sk-alice-secret-key-001",
        "user_id": "a0000000-0000-0000-0000-000000000001",
    },
    {
        "api_key": "sk-bob-secret-key-002",
        "user_id": "b0000000-0000-0000-0000-000000000002",
    },
]


@dataclass(slots=True)
class Result:
    status_code: int
    latency_ms: float
    task_id: str | None = None


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(round((len(ordered) - 1) * q), len(ordered) - 1)
    return ordered[idx]


async def _topup(
    client: httpx.AsyncClient,
    *,
    auth_key: str,
    user_id: str,
    amount: int,
) -> int:
    """Add credits to a user via admin endpoint."""
    resp = await client.post(
        "/v1/admin/credits",
        headers={"Authorization": f"Bearer {auth_key}"},
        json={"user_id": user_id, "amount": amount},
    )
    assert resp.status_code == 200, f"topup failed: {resp.text}"
    return int(resp.json()["new_balance"])


async def _submit(
    client: httpx.AsyncClient,
    api_key: str,
    x: int,
    y: int,
) -> Result:
    start = time.perf_counter()
    try:
        resp = await client.post(
            "/v1/task",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"x": x, "y": y},
        )
    except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError):
        latency = (time.perf_counter() - start) * 1000.0
        return Result(status_code=0, latency_ms=latency, task_id=None)
    latency = (time.perf_counter() - start) * 1000.0
    payload = resp.json()
    task_id = str(payload.get("task_id", "")) if resp.status_code in (200, 201) else None
    return Result(status_code=resp.status_code, latency_ms=latency, task_id=task_id)


async def _poll_until_terminal(
    client: httpx.AsyncClient,
    task_id: str,
    *,
    max_attempts: int = 60,
    interval: float = 0.5,
) -> str:
    for _ in range(max_attempts):
        resp = await client.get("/v1/poll", params={"task_id": task_id})
        if resp.status_code == 200:
            status = str(resp.json().get("status", "UNKNOWN"))
            if status in TERMINAL_STATES:
                return status
        await asyncio.sleep(interval)
    return "TIMEOUT"


async def run_loadtest(
    *,
    base_url: str,
    rps: int,
    duration: int,
    seed: int,
) -> dict[str, Any]:
    total = rps * duration
    credits_per_user = (total // len(USERS) + 1) * TASK_COST * 2

    limits = httpx.Limits(max_connections=rps * 2, max_keepalive_connections=rps)
    async with httpx.AsyncClient(base_url=base_url, timeout=15.0, limits=limits) as client:
        health = await client.get("/health")
        assert health.status_code == 200, f"service unhealthy: {health.status_code}"

        # Top up credits generously (use alice's key for admin calls)
        admin_key = USERS[0]["api_key"]
        for user in USERS:
            await _topup(
                client,
                auth_key=admin_key,
                user_id=user["user_id"],
                amount=credits_per_user,
            )

        # Sustained load: schedule requests at target RPS
        rnd = random.Random(seed)
        sem = asyncio.Semaphore(rps * 3)
        interval = 1.0 / rps

        async def fire(i: int) -> Result:
            async with sem:
                user = USERS[i % len(USERS)]
                x, y = rnd.randint(1, 100), rnd.randint(1, 100)
                return await _submit(client, user["api_key"], x, y)

        tasks: list[asyncio.Task[Result]] = []
        wall_start = time.monotonic()
        for i in range(total):
            target_time = wall_start + i * interval
            now = time.monotonic()
            if target_time > now:
                await asyncio.sleep(target_time - now)
            tasks.append(asyncio.create_task(fire(i)))

        results = await asyncio.gather(*tasks)
        wall_end = time.monotonic()
        submit_wall = wall_end - wall_start

        # Poll a sample of accepted tasks
        accepted = [r for r in results if r.task_id]
        sample_size = min(50, len(accepted))
        sample = random.Random(seed).sample(accepted, sample_size) if accepted else []
        terminal_counts: dict[str, int] = {}
        for r in sample:
            status = await _poll_until_terminal(client, r.task_id or "")
            terminal_counts[status] = terminal_counts.get(status, 0) + 1

    # Compute stats
    latencies = [r.latency_ms for r in results]
    status_dist: dict[int, int] = {}
    for r in results:
        status_dist[r.status_code] = status_dist.get(r.status_code, 0) + 1

    actual_rps = len(accepted) / submit_wall if submit_wall > 0 else 0

    return {
        "target_rps": rps,
        "duration_seconds": duration,
        "total_requests": total,
        "accepted": len(accepted),
        "actual_rps": round(actual_rps, 1),
        "submit_wall_seconds": round(submit_wall, 2),
        "status_distribution": {str(k): v for k, v in sorted(status_dist.items())},
        "latency_ms": {
            "p50": round(_pct(latencies, 0.50), 1),
            "p95": round(_pct(latencies, 0.95), 1),
            "p99": round(_pct(latencies, 0.99), 1),
            "avg": round(statistics.mean(latencies), 1) if latencies else 0,
            "max": round(max(latencies), 1) if latencies else 0,
        },
        "poll_sample": {
            "sampled": sample_size,
            "terminal_status_counts": terminal_counts,
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 60)
    print("  Sustained Load Test — Solution 5")
    print("=" * 60)
    total = report["total_requests"]
    pct = report["accepted"] * 100 / total if total else 0
    print(f"  Target:      {report['target_rps']} RPS x {report['duration_seconds']}s = {total} requests")
    print(f"  Accepted:    {report['accepted']} ({pct:.1f}%)")
    print(f"  Actual RPS:  {report['actual_rps']}")
    print(f"  Wall time:   {report['submit_wall_seconds']}s")
    print()
    lat = report["latency_ms"]
    print("  Submit latency:")
    print(f"    p50:  {lat['p50']}ms")
    print(f"    p95:  {lat['p95']}ms")
    print(f"    p99:  {lat['p99']}ms")
    print(f"    avg:  {lat['avg']}ms")
    print(f"    max:  {lat['max']}ms")
    print()
    print(f"  Status codes: {report['status_distribution']}")
    print(f"  Poll sample:  {report['poll_sample']}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sustained load test for solution 5")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--rps", type=int, default=100)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default="worklog/evidence/load/loadtest-latest.json",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_path = Path(args.output) if Path(args.output).is_absolute() else (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        run_loadtest(
            base_url=args.base_url,
            rps=args.rps,
            duration=args.duration,
            seed=args.seed,
        )
    )
    report["base_url"] = args.base_url
    report["seed"] = args.seed
    report["generated_at_epoch"] = int(time.time())

    _print_report(report)
    output_path.write_text(json.dumps(report, indent=2))
    print(f"\n  Report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
