#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from solution1.api.paths import (
    V1_ADMIN_CREDITS_PATH,
    V1_OAUTH_TOKEN_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution1.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_USER1_API_KEY,
    DEFAULT_USER2_API_KEY,
)

BASE_URL = "http://localhost:8000"
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
USER1_API_KEY = os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY)
USER2_API_KEY = os.getenv("BOB_API_KEY", DEFAULT_USER2_API_KEY)
REDIS_TASKS_STREAM_KEY = os.getenv("REDIS_TASKS_STREAM_KEY", "tasks:stream")
REDIS_TASKS_STREAM_GROUP = os.getenv("REDIS_TASKS_STREAM_GROUP", "workers")


@dataclass(slots=True)
class RequestResult:
    status_code: int
    latency_ms: float
    payload: dict[str, Any]
    task_id: str | None
    access_token: str


@dataclass(slots=True)
class StreamSample:
    epoch_seconds: float
    stream_length: int
    pel_pending: int
    redis_used_memory_bytes: int


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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _worker_env_int(repo_root: Path, name: str, default: int) -> int:
    result = _compose(repo_root, "exec", "-T", "worker", "printenv", name)
    if result.returncode != 0:
        return default
    raw = result.stdout.strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_xpending_summary_count(output: str) -> int:
    first_line = output.strip().splitlines()
    if not first_line:
        return 0
    try:
        return int(first_line[0])
    except ValueError:
        return 0


def _parse_info_memory_used_bytes(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("used_memory:"):
            value = line.partition(":")[2].strip()
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def _redis_cli_raw(repo_root: Path, *args: str) -> str:
    result = _compose(repo_root, "exec", "-T", "redis", "redis-cli", "--raw", *args)
    _assert(
        result.returncode == 0,
        f"redis-cli command failed ({' '.join(args)}): {result.stderr.strip()}",
    )
    return result.stdout


def _sample_stream_state(repo_root: Path, *, stream_key: str, stream_group: str) -> StreamSample:
    stream_length = 0
    pel_pending = 0
    memory_used_bytes = 0

    try:
        stream_length = int(_redis_cli_raw(repo_root, "XLEN", stream_key).strip() or "0")
    except (AssertionError, ValueError):
        stream_length = 0

    xpending_result = _compose(
        repo_root,
        "exec",
        "-T",
        "redis",
        "redis-cli",
        "--raw",
        "XPENDING",
        stream_key,
        stream_group,
    )
    if xpending_result.returncode == 0:
        pel_pending = _parse_xpending_summary_count(xpending_result.stdout)
    elif "NOGROUP" in xpending_result.stderr:
        pel_pending = 0
    else:
        raise AssertionError(
            "redis-cli XPENDING failed "
            f"({stream_key}/{stream_group}): {xpending_result.stderr.strip()}"
        )

    try:
        memory_info = _redis_cli_raw(repo_root, "INFO", "memory")
        memory_used_bytes = _parse_info_memory_used_bytes(memory_info)
    except AssertionError:
        memory_used_bytes = 0

    return StreamSample(
        epoch_seconds=time.time(),
        stream_length=stream_length,
        pel_pending=pel_pending,
        redis_used_memory_bytes=memory_used_bytes,
    )


def _summarize_series(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {
            "start": 0,
            "end": 0,
            "min": 0,
            "max": 0,
            "avg": 0.0,
            "p95": 0.0,
            "growth": 0,
        }

    return {
        "start": values[0],
        "end": values[-1],
        "min": min(values),
        "max": max(values),
        "avg": round(statistics.mean(values), 3),
        "p95": round(_percentile([float(value) for value in values], 0.95), 3),
        "growth": values[-1] - values[0],
    }


def _summarize_stream_samples(samples: list[StreamSample]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "stream_length": _summarize_series([sample.stream_length for sample in samples]),
        "pel_pending": _summarize_series([sample.pel_pending for sample in samples]),
        "redis_used_memory_bytes": _summarize_series(
            [sample.redis_used_memory_bytes for sample in samples]
        ),
    }


def _run_profile_with_stream_observability(
    client: httpx.Client,
    *,
    repo_root: Path,
    profile_name: str,
    total_requests: int,
    concurrency: int,
    access_tokens: list[str],
    seed: int,
    stream_key: str,
    stream_group: str,
    sample_interval_seconds: float,
    include_stream_samples: bool = False,
    retry_on_429: bool = False,
    max_retry_attempts: int = 0,
    retry_sleep_seconds: float = 0.25,
) -> dict[str, Any]:
    samples: list[StreamSample] = []
    stop_event = threading.Event()

    def _sampler() -> None:
        while not stop_event.is_set():
            with contextlib.suppress(AssertionError):
                samples.append(
                    _sample_stream_state(
                        repo_root,
                        stream_key=stream_key,
                        stream_group=stream_group,
                    )
                )
            stop_event.wait(sample_interval_seconds)

    sampler_thread = threading.Thread(target=_sampler, name=f"stream-sampler-{profile_name}")
    sampler_thread.daemon = True
    sampler_thread.start()

    try:
        profile = _run_profile(
            client,
            profile_name=profile_name,
            total_requests=total_requests,
            concurrency=concurrency,
            access_tokens=access_tokens,
            seed=seed,
            retry_on_429=retry_on_429,
            max_retry_attempts=max_retry_attempts,
            retry_sleep_seconds=retry_sleep_seconds,
        )
    finally:
        stop_event.set()
        sampler_thread.join(timeout=10.0)
        with contextlib.suppress(AssertionError):
            samples.append(
                _sample_stream_state(
                    repo_root,
                    stream_key=stream_key,
                    stream_group=stream_group,
                )
            )

    profile["stream_observability"] = _summarize_stream_samples(samples)
    if include_stream_samples:
        profile["stream_observability"]["samples"] = [
            {
                "epoch_seconds": round(sample.epoch_seconds, 3),
                "stream_length": sample.stream_length,
                "pel_pending": sample.pel_pending,
                "redis_used_memory_bytes": sample.redis_used_memory_bytes,
            }
            for sample in samples
        ]
    return profile


def _oauth_token(client: httpx.Client, *, api_key: str, scope: str | None = None) -> str:
    payload: dict[str, str] = {"api_key": api_key}
    if scope is not None:
        payload["scope"] = scope
    response = client.post(V1_OAUTH_TOKEN_PATH, json=payload)
    _assert(response.status_code == 200, f"oauth token exchange failed: {response.text}")
    access_token = str(response.json().get("access_token", ""))
    _assert(access_token.count(".") == 2, "oauth token payload is not jwt-like")
    return access_token


def _admin_set_balance(
    client: httpx.Client,
    *,
    admin_access_token: str,
    target_api_key: str,
    target_credits: int,
    reason: str,
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_access_token}"},
        json={"api_key": target_api_key, "delta": 0, "reason": f"{reason}_probe"},
    )
    _assert(probe.status_code == 200, f"admin probe failed: {probe.text}")
    current = int(probe.json()["new_balance"])
    delta = target_credits - current
    if delta == 0:
        return current
    update = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_access_token}"},
        json={"api_key": target_api_key, "delta": delta, "reason": f"{reason}_set"},
    )
    _assert(update.status_code == 200, f"admin set failed: {update.text}")
    return int(update.json()["new_balance"])


def _submit_task(
    client: httpx.Client,
    *,
    access_token: str,
    x: int,
    y: int,
    idempotency_key: str | None = None,
) -> RequestResult:
    headers = {"Authorization": f"Bearer {access_token}"}
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
        access_token=access_token,
    )


def _poll_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    access_token: str,
    max_attempts: int = 40,
    sleep_seconds: float = 0.25,
) -> str:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            headers={"Authorization": f"Bearer {access_token}"},
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
    access_tokens: list[str],
    seed: int,
    poll_sample_limit: int = 0,
    retry_on_429: bool = False,
    max_retry_attempts: int = 0,
    retry_sleep_seconds: float = 0.25,
) -> dict[str, Any]:
    rnd = random.Random(seed)

    def _one_submit(index: int) -> RequestResult:
        access_token = access_tokens[index % len(access_tokens)]
        x = rnd.randint(1, 32)
        y = rnd.randint(1, 32)
        attempt = 0
        while True:
            result = _submit_task(client, access_token=access_token, x=x, y=y)
            if not retry_on_429 or result.status_code != 429:
                return result
            if attempt >= max_retry_attempts:
                return result
            attempt += 1
            time.sleep(retry_sleep_seconds)

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
            access_token=accepted_result.access_token,
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


def _scenario_idempotency_race(client: httpx.Client, *, user_access_token: str) -> dict[str, Any]:
    idem = f"load-idem-{uuid4()}"
    total = 20

    def _one(_: int) -> RequestResult:
        return _submit_task(
            client,
            access_token=user_access_token,
            x=9,
            y=9,
            idempotency_key=idem,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(_one, range(total)))

    statuses = [result.status_code for result in results]
    status_counts = {str(code): statuses.count(code) for code in sorted(set(statuses))}
    _assert(201 in statuses, f"idempotency race missing initial accept: {status_counts}")
    _assert(statuses.count(200) >= 1, f"idempotency race missing replay hits: {status_counts}")
    _assert(500 not in statuses, f"idempotency race contains 500: {status_counts}")
    return {"status_counts": status_counts}


def _scenario_insufficient_credits(
    client: httpx.Client,
    *,
    admin_access_token: str,
    user_api_key: str,
    user_access_token: str,
) -> dict[str, Any]:
    _admin_set_balance(
        client,
        admin_access_token=admin_access_token,
        target_api_key=user_api_key,
        target_credits=5,
        reason="load_insufficient",
    )
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_access_token}"},
        json={"x": 1, "y": 1},
    )
    _assert(response.status_code == 402, f"expected 402, got {response.status_code}")
    return {"status_code": response.status_code}


def _scenario_overload_with_paused_worker(
    client: httpx.Client,
    *,
    repo_root: Path,
    admin_access_token: str,
    user_api_key: str,
    user_access_token: str,
) -> dict[str, Any]:
    _admin_set_balance(
        client,
        admin_access_token=admin_access_token,
        target_api_key=user_api_key,
        target_credits=1000,
        reason="load_overload",
    )
    stop = _compose(repo_root, "stop", "worker")
    _assert(stop.returncode == 0, f"worker stop failed: {stop.stderr}")
    try:
        results = _run_profile(
            client,
            profile_name="overload_paused_worker",
            total_requests=120,
            concurrency=32,
            access_tokens=[user_access_token],
            seed=99,
            poll_sample_limit=0,
        )
        rejected_429 = results["status_counts"].get("429", 0)
        _assert(rejected_429 > 0, f"expected 429 under overload: {results}")
        return results
    finally:
        start = _compose(repo_root, "start", "worker")
        _assert(start.returncode == 0, f"worker start failed: {start.stderr}")


def _scenario_redis_transient(
    client: httpx.Client,
    *,
    repo_root: Path,
    user_access_token: str,
) -> dict[str, Any]:
    stop = _compose(repo_root, "stop", "redis")
    _assert(stop.returncode == 0, f"redis stop failed: {stop.stderr}")
    try:
        degraded = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {user_access_token}"},
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
                "Authorization": f"Bearer {user_access_token}",
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
        if line.startswith("stream_queue_depth "):
            try:
                return int(float(line.split(" ", 1)[1].strip()))
            except ValueError:
                return -1
    return -1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic load/stress scenarios for solution 1."
    )
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--output",
        default="worklog/evidence/load/latest-load-report.json",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.5)
    parser.add_argument("--saturation-requests", type=int, default=120)
    parser.add_argument("--saturation-concurrency", type=int, default=18)
    parser.add_argument("--saturation-retry-attempts", type=int, default=40)
    parser.add_argument("--saturation-retry-sleep-seconds", type=float, default=0.25)
    parser.add_argument("--include-stream-samples", action="store_true")
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

        admin_access_token = _oauth_token(
            client,
            api_key=ADMIN_API_KEY,
            scope="task:submit task:poll task:cancel admin:credits",
        )
        user1_access_token = _oauth_token(client, api_key=USER1_API_KEY)
        user2_access_token = _oauth_token(client, api_key=USER2_API_KEY)

        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=3000,
            reason="load_low_u1",
        )
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER2_API_KEY,
            target_credits=3000,
            reason="load_low_u2",
        )
        low = _run_profile_with_stream_observability(
            client,
            repo_root=repo_root,
            profile_name="low",
            total_requests=24,
            concurrency=4,
            access_tokens=[user1_access_token, user2_access_token],
            seed=args.seed,
            stream_key=REDIS_TASKS_STREAM_KEY,
            stream_group=REDIS_TASKS_STREAM_GROUP,
            sample_interval_seconds=args.sample_interval_seconds,
            include_stream_samples=args.include_stream_samples,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=3000,
            reason="load_medium_u1",
        )
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER2_API_KEY,
            target_credits=3000,
            reason="load_medium_u2",
        )
        medium = _run_profile_with_stream_observability(
            client,
            repo_root=repo_root,
            profile_name="medium",
            total_requests=60,
            concurrency=8,
            access_tokens=[user1_access_token, user2_access_token],
            seed=args.seed + 1,
            stream_key=REDIS_TASKS_STREAM_KEY,
            stream_group=REDIS_TASKS_STREAM_GROUP,
            sample_interval_seconds=args.sample_interval_seconds,
            include_stream_samples=args.include_stream_samples,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=3000,
            reason="load_high_u1",
        )
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER2_API_KEY,
            target_credits=3000,
            reason="load_high_u2",
        )
        high = _run_profile_with_stream_observability(
            client,
            repo_root=repo_root,
            profile_name="high",
            total_requests=120,
            concurrency=12,
            access_tokens=[user1_access_token, user2_access_token],
            seed=args.seed + 2,
            stream_key=REDIS_TASKS_STREAM_KEY,
            stream_group=REDIS_TASKS_STREAM_GROUP,
            sample_interval_seconds=args.sample_interval_seconds,
            include_stream_samples=args.include_stream_samples,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=8_000,
            reason="load_saturation_u1",
        )
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER2_API_KEY,
            target_credits=8_000,
            reason="load_saturation_u2",
        )
        saturation = _run_profile_with_stream_observability(
            client,
            repo_root=repo_root,
            profile_name="saturation",
            total_requests=args.saturation_requests,
            concurrency=args.saturation_concurrency,
            access_tokens=[user1_access_token, user2_access_token],
            seed=args.seed + 3,
            stream_key=REDIS_TASKS_STREAM_KEY,
            stream_group=REDIS_TASKS_STREAM_GROUP,
            sample_interval_seconds=args.sample_interval_seconds,
            include_stream_samples=args.include_stream_samples,
            retry_on_429=True,
            max_retry_attempts=args.saturation_retry_attempts,
            retry_sleep_seconds=args.saturation_retry_sleep_seconds,
        )

        overload = _scenario_overload_with_paused_worker(
            client,
            repo_root=repo_root,
            admin_access_token=admin_access_token,
            user_api_key=USER1_API_KEY,
            user_access_token=user1_access_token,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=1000,
            reason="idem_u1",
        )
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER2_API_KEY,
            target_credits=1000,
            reason="idem_u2",
        )
        idempotency = _scenario_idempotency_race(client, user_access_token=user1_access_token)
        _reset_state(repo_root)
        insufficient = _scenario_insufficient_credits(
            client,
            admin_access_token=admin_access_token,
            user_api_key=USER2_API_KEY,
            user_access_token=user2_access_token,
        )
        _reset_state(repo_root)
        _admin_set_balance(
            client,
            admin_access_token=admin_access_token,
            target_api_key=USER1_API_KEY,
            target_credits=5000,
            reason="redis_transient_u1",
        )
        redis_transient = _scenario_redis_transient(
            client,
            repo_root=repo_root,
            user_access_token=user1_access_token,
        )

        queue_depth_after = _sample_queue_depth(client)

    # Explicit stress assertions (BK-009)
    _assert("500" not in low["status_counts"], f"unexpected 500 in low profile: {low}")
    _assert("500" not in medium["status_counts"], f"unexpected 500 in medium profile: {medium}")
    _assert("500" not in high["status_counts"], f"unexpected 500 in high profile: {high}")
    saturation_500_message = f"unexpected 500 in saturation profile: {saturation}"
    _assert("500" not in saturation["status_counts"], saturation_500_message)
    _assert(overload["status_counts"].get("429", 0) > 0, f"missing 429 under overload: {overload}")
    _assert(insufficient["status_code"] == 402, "insufficient scenario did not produce 402")

    report = {
        "generated_at_epoch": int(time.time()),
        "base_url": args.base_url,
        "seed": args.seed,
        "profiles": [low, medium, high, saturation],
        "stream_runtime_settings": {
            "redis_tasks_stream_key": REDIS_TASKS_STREAM_KEY,
            "redis_tasks_stream_group": REDIS_TASKS_STREAM_GROUP,
            "redis_tasks_stream_maxlen": _worker_env_int(
                repo_root,
                "REDIS_TASKS_STREAM_MAXLEN",
                _env_int("REDIS_TASKS_STREAM_MAXLEN", 500_000),
            ),
            "stream_worker_read_count": _worker_env_int(
                repo_root, "STREAM_WORKER_READ_COUNT", _env_int("STREAM_WORKER_READ_COUNT", 1)
            ),
            "stream_worker_claim_count": _worker_env_int(
                repo_root, "STREAM_WORKER_CLAIM_COUNT", _env_int("STREAM_WORKER_CLAIM_COUNT", 20)
            ),
            "stream_worker_claim_idle_ms": _worker_env_int(
                repo_root,
                "STREAM_WORKER_CLAIM_IDLE_MS",
                _env_int("STREAM_WORKER_CLAIM_IDLE_MS", 15_000),
            ),
        },
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
