#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from solution3.api.paths import V1_OAUTH_TOKEN_PATH, V1_TASK_POLL_PATH, V1_TASK_SUBMIT_PATH
from solution3.core.settings import load_settings

DEFAULT_BASE_URL = "http://localhost:8000"
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}


@dataclass(slots=True)
class RequestResult:
    status_code: int
    latency_ms: float
    terminal_status: str | None
    task_id: str | None


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * q)
    return ordered[index]


def _summarize_latencies(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "avg": 0.0, "p95": 0.0}
    return {
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "avg": round(sum(values) / len(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
    }


def build_summary(
    *,
    profile_name: str,
    results: list[RequestResult],
    total_duration_seconds: float,
) -> dict[str, Any]:
    accepted = [result for result in results if result.status_code in {200, 201}]
    rejected = [result for result in results if result.status_code not in {200, 201}]
    terminal_counts: dict[str, int] = {}
    for result in accepted:
        if result.terminal_status is None:
            continue
        terminal_counts[result.terminal_status] = terminal_counts.get(result.terminal_status, 0) + 1

    duration = max(total_duration_seconds, 0.001)
    return {
        "profile": profile_name,
        "total_requests": len(results),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "throughput_rps": round(len(results) / duration, 4),
        "latency_ms": _summarize_latencies([result.latency_ms for result in results]),
        "terminal_status_counts": terminal_counts,
        "results": [asdict(result) for result in results],
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _oauth_token(client: httpx.Client, *, api_key: str) -> str:
    response = client.post(V1_OAUTH_TOKEN_PATH, json={"api_key": api_key})
    _assert(response.status_code == 200, f"oauth token exchange failed: {response.status_code}")
    return str(response.json()["access_token"])


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    access_token: str,
    max_attempts: int = 60,
    poll_interval_seconds: float = 0.5,
) -> str:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        _assert(response.status_code == 200, f"poll failed: {response.status_code} {response.text}")
        payload: dict[str, Any] = response.json()
        status = payload.get("status")
        if isinstance(status, str) and status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_interval_seconds)
    raise AssertionError(f"task {task_id} did not reach terminal state")


def _run_request(
    *,
    base_url: str,
    access_token: str,
    request_index: int,
    model_class: str,
) -> RequestResult:
    started = time.perf_counter()
    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        response = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Idempotency-Key": f"load-{request_index}-{uuid4()}",
            },
            json={
                "x": request_index,
                "y": request_index + 1,
                "model_class": model_class,
            },
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if response.status_code not in {200, 201}:
            return RequestResult(
                status_code=response.status_code,
                latency_ms=latency_ms,
                terminal_status=None,
                task_id=None,
            )
        task_id = str(response.json()["task_id"])
        terminal_status = _poll_until_terminal(
            client,
            task_id=task_id,
            access_token=access_token,
        )
        return RequestResult(
            status_code=response.status_code,
            latency_ms=latency_ms,
            terminal_status=terminal_status,
            task_id=task_id,
        )


def _run_profile(
    *,
    base_url: str,
    total_requests: int,
    concurrency: int,
    access_tokens: list[str],
    model_class: str,
    profile_name: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                _run_request,
                base_url=base_url,
                access_token=access_tokens[index % len(access_tokens)],
                request_index=index,
                model_class=model_class,
            )
            for index in range(total_requests)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    duration = time.perf_counter() - started
    return build_summary(
        profile_name=profile_name,
        results=results,
        total_duration_seconds=duration,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Solution 3 load profiles.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--output",
        default="worklog/evidence/load/latest-load-report.json",
    )
    parser.add_argument("--steady-requests", type=int, default=12)
    parser.add_argument("--steady-concurrency", type=int, default=3)
    parser.add_argument("--burst-requests", type=int, default=18)
    parser.add_argument("--burst-concurrency", type=int, default=6)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings = load_settings()
    with httpx.Client(base_url=args.base_url, timeout=20.0) as client:
        access_tokens = [
            _oauth_token(client, api_key=settings.admin_api_key),
            _oauth_token(client, api_key=settings.alice_api_key),
        ]

    profiles = [
        _run_profile(
            base_url=args.base_url,
            total_requests=max(args.steady_requests, 1),
            concurrency=max(args.steady_concurrency, 1),
            access_tokens=access_tokens,
            model_class="small",
            profile_name="steady-small",
        ),
        _run_profile(
            base_url=args.base_url,
            total_requests=max(args.burst_requests, 1),
            concurrency=max(args.burst_concurrency, 1),
            access_tokens=access_tokens,
            model_class="medium",
            profile_name="burst-medium",
        ),
    ]

    report = {
        "base_url": args.base_url,
        "profiles": profiles,
    }
    output_path = (_repo_root() / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
