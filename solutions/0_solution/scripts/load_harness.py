#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from solution0.api.paths import V1_ADMIN_CREDITS_PATH, V1_TASK_POLL_PATH, V1_TASK_SUBMIT_PATH
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_BOB_API_KEY,
)

BASE_URL = "http://localhost:8000"
ADMIN_KEY = os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
USER1_KEY = os.getenv("ALICE_API_KEY", DEFAULT_ALICE_API_KEY)
USER2_KEY = os.getenv("BOB_API_KEY", DEFAULT_BOB_API_KEY)


@dataclass(slots=True)
class RequestResult:
    status_code: int
    latency_ms: float
    payload: dict[str, Any]
    task_id: str | None
    api_key: str


def _compose(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _reset_state(repo_root: Path) -> None:
    reset = subprocess.run(
        ["./scripts/reset_state.sh"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    _assert(reset.returncode == 0, f"state reset failed: {reset.stderr}")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = round((len(ordered) - 1) * q)
    return ordered[idx]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _admin_set_balance(
    client: httpx.Client,
    *,
    target_api_key: str,
    target_credits: int,
    reason: str,
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"api_key": target_api_key, "delta": 0, "reason": f"{reason}_probe"},
    )
    _assert(probe.status_code == 200, f"admin probe failed: {probe.text}")
    current = int(probe.json()["new_balance"])
    delta = target_credits - current
    if delta == 0:
        return current
    update = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"api_key": target_api_key, "delta": delta, "reason": f"{reason}_set"},
    )
    _assert(update.status_code == 200, f"admin set failed: {update.text}")
    return int(update.json()["new_balance"])


def _submit_task(
    client: httpx.Client,
    *,
    api_key: str,
    x: int,
    y: int,
    idempotency_key: str | None = None,
) -> RequestResult:
    headers = {"Authorization": f"Bearer {api_key}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    started = time.perf_counter()
    response = client.post(V1_TASK_SUBMIT_PATH, headers=headers, json={"x": x, "y": y})
    latency_ms = (time.perf_counter() - started) * 1000.0
    payload = response.json()
    task_id_raw = payload.get("task_id")
    task_id = str(task_id_raw) if isinstance(task_id_raw, str) else None
    return RequestResult(
        status_code=response.status_code,
        latency_ms=latency_ms,
        payload=payload,
        task_id=task_id,
        api_key=api_key,
    )


def _poll_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    api_key: str,
    max_attempts: int = 40,
    sleep_seconds: float = 0.25,
) -> str:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            headers={"Authorization": f"Bearer {api_key}"},
            params={"task_id": task_id},
        )
        if response.status_code != 200:
            time.sleep(sleep_seconds)
            continue
        payload = response.json()
        status = str(payload.get("status", "UNKNOWN"))
        if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            return status
        time.sleep(sleep_seconds)
    return "TIMEOUT"


def _run_profile(
    client: httpx.Client,
    *,
    profile_name: str,
    total_requests: int,
    concurrency: int,
    users: list[str],
    seed: int,
    poll_sample_limit: int = 0,
) -> dict[str, Any]:
    rnd = random.Random(seed)

    def _one_submit(index: int) -> RequestResult:
        api_key = users[index % len(users)]
        x = rnd.randint(1, 32)
        y = rnd.randint(1, 32)
        return _submit_task(client, api_key=api_key, x=x, y=y)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(_one_submit, range(total_requests)))
    submit_duration_seconds = time.perf_counter() - started

    accepted = [result for result in results if result.status_code in {200, 201} and result.task_id]
    status_counts: dict[str, int] = {}
    for result in results:
        key = str(result.status_code)
        status_counts[key] = status_counts.get(key, 0) + 1

    terminal_status_counts: dict[str, int] = {}
    for accepted_result in accepted:
        terminal_status = _poll_terminal(
            client,
            task_id=accepted_result.task_id or "",
            api_key=accepted_result.api_key,
        )
        terminal_status_counts[terminal_status] = terminal_status_counts.get(terminal_status, 0) + 1

    end_to_end_duration_seconds = time.perf_counter() - started
    poll_attempts = 0
    if poll_sample_limit > 0:
        for _ in accepted[: min(poll_sample_limit, len(accepted))]:
            poll_attempts += 1

    latencies = [result.latency_ms for result in results]
    throughput_rps = (
        len(accepted) / end_to_end_duration_seconds if end_to_end_duration_seconds > 0 else 0.0
    )
    return {
        "profile": profile_name,
        "total_requests": total_requests,
        "concurrency": concurrency,
        "submit_duration_seconds": round(submit_duration_seconds, 3),
        "end_to_end_duration_seconds": round(end_to_end_duration_seconds, 3),
        "accepted": len(accepted),
        "status_counts": status_counts,
        "terminal_status_counts": terminal_status_counts,
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.5), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "avg": round(statistics.mean(latencies), 3) if latencies else 0.0,
        },
        "throughput_rps": round(throughput_rps, 4),
        "sample_poll_attempts": poll_attempts,
    }


def _scenario_idempotency_race(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    idem = f"load-idem-{uuid4()}"
    total = 20

    def _one(_: int) -> RequestResult:
        return _submit_task(client, api_key=user_key, x=9, y=9, idempotency_key=idem)

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(_one, range(total)))

    statuses = [result.status_code for result in results]
    status_counts = {str(code): statuses.count(code) for code in sorted(set(statuses))}
    _assert(201 in statuses, f"idempotency race missing initial accept: {status_counts}")
    _assert(statuses.count(200) >= 1, f"idempotency race missing replay hits: {status_counts}")
    _assert(500 not in statuses, f"idempotency race contains 500: {status_counts}")
    return {"status_counts": status_counts}


def _scenario_insufficient_credits(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    _admin_set_balance(
        client,
        target_api_key=user_key,
        target_credits=5,
        reason="load_insufficient",
    )
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}"},
        json={"x": 1, "y": 1},
    )
    _assert(response.status_code == 402, f"expected 402, got {response.status_code}")
    return {"status_code": response.status_code}


def _scenario_overload_with_paused_worker(
    client: httpx.Client,
    *,
    repo_root: Path,
    user_key: str,
) -> dict[str, Any]:
    _admin_set_balance(client, target_api_key=user_key, target_credits=1000, reason="load_overload")
    stop = _compose(repo_root, "stop", "worker")
    _assert(stop.returncode == 0, f"worker stop failed: {stop.stderr}")
    try:
        results = _run_profile(
            client,
            profile_name="overload_paused_worker",
            total_requests=120,
            concurrency=32,
            users=[user_key],
            seed=99,
            poll_sample_limit=0,
        )
        rejected_429 = results["status_counts"].get("429", 0)
        _assert(rejected_429 > 0, f"expected 429 under overload: {results}")
        return results
    finally:
        start = _compose(repo_root, "start", "worker")
        _assert(start.returncode == 0, f"worker start failed: {start.stderr}")


def _scenario_redis_transient(client: httpx.Client, *, repo_root: Path) -> dict[str, Any]:
    stop = _compose(repo_root, "stop", "redis")
    _assert(stop.returncode == 0, f"redis stop failed: {stop.stderr}")
    try:
        degraded = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {USER1_KEY}"},
            json={"x": 3, "y": 4},
        )
        _assert(degraded.status_code == 503, f"expected 503 when redis down: {degraded.text}")
    finally:
        start = _compose(repo_root, "start", "redis")
        _assert(start.returncode == 0, f"redis start failed: {start.stderr}")

    started = time.perf_counter()
    recovered = False
    for _ in range(160):
        ready = client.get("/ready")
        if ready.status_code == 200 and ready.json().get("ready") is True:
            recovered = True
            break
        # Nudge Lua reload path after restart.
        client.post(
            V1_TASK_SUBMIT_PATH,
            headers={
                "Authorization": f"Bearer {USER1_KEY}",
                "Idempotency-Key": f"recover-{uuid4()}",
            },
            json={"x": 2, "y": 2},
        )
        time.sleep(0.5)
    _assert(recovered, "service did not recover before timeout")
    recovery_seconds = time.perf_counter() - started
    return {"recovery_seconds": round(recovery_seconds, 3)}


def _sample_queue_depth(client: httpx.Client) -> int:
    metrics = client.get("/metrics")
    if metrics.status_code != 200:
        return -1
    for line in metrics.text.splitlines():
        if line.startswith("celery_queue_depth "):
            try:
                return int(float(line.split(" ", 1)[1].strip()))
            except ValueError:
                return -1
    return -1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic load/stress scenarios for solution 0."
    )
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--output",
        default="worklog/evidence/load/latest-load-report.json",
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_path = (
        Path(args.output)
        if Path(args.output).is_absolute()
        else (repo_root / args.output).resolve()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        health = client.get("/health")
        _assert(health.status_code == 200, f"service unhealthy: {health.status_code}")

        _reset_state(repo_root)
        _admin_set_balance(
            client, target_api_key=USER1_KEY, target_credits=3000, reason="load_low_u1"
        )
        _admin_set_balance(
            client, target_api_key=USER2_KEY, target_credits=3000, reason="load_low_u2"
        )
        low = _run_profile(
            client,
            profile_name="low",
            total_requests=24,
            concurrency=4,
            users=[USER1_KEY, USER2_KEY],
            seed=args.seed,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client, target_api_key=USER1_KEY, target_credits=3000, reason="load_medium_u1"
        )
        _admin_set_balance(
            client, target_api_key=USER2_KEY, target_credits=3000, reason="load_medium_u2"
        )
        medium = _run_profile(
            client,
            profile_name="medium",
            total_requests=60,
            concurrency=8,
            users=[USER1_KEY, USER2_KEY],
            seed=args.seed + 1,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client, target_api_key=USER1_KEY, target_credits=3000, reason="load_high_u1"
        )
        _admin_set_balance(
            client, target_api_key=USER2_KEY, target_credits=3000, reason="load_high_u2"
        )
        high = _run_profile(
            client,
            profile_name="high",
            total_requests=120,
            concurrency=12,
            users=[USER1_KEY, USER2_KEY],
            seed=args.seed + 2,
        )

        overload = _scenario_overload_with_paused_worker(
            client, repo_root=repo_root, user_key=USER1_KEY
        )
        _reset_state(repo_root)
        _admin_set_balance(client, target_api_key=USER1_KEY, target_credits=1000, reason="idem_u1")
        _admin_set_balance(client, target_api_key=USER2_KEY, target_credits=1000, reason="idem_u2")
        idempotency = _scenario_idempotency_race(client, user_key=USER1_KEY)
        _reset_state(repo_root)
        insufficient = _scenario_insufficient_credits(client, user_key=USER2_KEY)
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            target_api_key=USER1_KEY,
            target_credits=5000,
            reason="redis_transient_u1",
        )
        redis_transient = _scenario_redis_transient(client, repo_root=repo_root)

        queue_depth_after = _sample_queue_depth(client)

    # Explicit stress assertions (BK-009)
    _assert("500" not in low["status_counts"], f"unexpected 500 in low profile: {low}")
    _assert("500" not in medium["status_counts"], f"unexpected 500 in medium profile: {medium}")
    _assert("500" not in high["status_counts"], f"unexpected 500 in high profile: {high}")
    _assert(overload["status_counts"].get("429", 0) > 0, f"missing 429 under overload: {overload}")
    _assert(insufficient["status_code"] == 402, "insufficient scenario did not produce 402")

    report = {
        "generated_at_epoch": int(time.time()),
        "base_url": args.base_url,
        "seed": args.seed,
        "profiles": [low, medium, high],
        "stress": {
            "overload": overload,
            "idempotency_race": idempotency,
            "insufficient_credits": insufficient,
            "redis_transient": redis_transient,
        },
        "queue_depth_after": queue_depth_after,
    }
    output_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
