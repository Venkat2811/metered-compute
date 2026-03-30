#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from solution0.api.paths import (
    COMPAT_TASK_POLL_PATH,
    COMPAT_TASK_SUBMIT_PATH,
    HEALTH_PATH,
    READY_PATH,
    V1_ADMIN_CREDITS_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_BOB_API_KEY,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_ADMIN_KEY = os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
DEFAULT_USER1_KEY = os.getenv("ALICE_API_KEY", DEFAULT_ALICE_API_KEY)
DEFAULT_USER2_KEY = os.getenv("BOB_API_KEY", DEFAULT_BOB_API_KEY)
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}


@dataclass(slots=True)
class ScenarioResult:
    name: str
    passed: bool
    duration_seconds: float
    details: dict[str, Any]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _compose(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _admin_adjust_to(
    client: httpx.Client,
    *,
    admin_key: str,
    user_api_key: str,
    target_credits: int,
    reason_prefix: str,
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"api_key": user_api_key, "delta": 0, "reason": f"{reason_prefix}_probe"},
    )
    _assert(probe.status_code == 200, f"admin probe failed: {probe.status_code} {probe.text}")
    current = int(probe.json()["new_balance"])
    delta = target_credits - current
    if delta == 0:
        return current
    change = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_key}"},
        json={"api_key": user_api_key, "delta": delta, "reason": f"{reason_prefix}_set"},
    )
    _assert(change.status_code == 200, f"admin adjust failed: {change.status_code} {change.text}")
    return int(change.json()["new_balance"])


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    api_key: str,
    max_attempts: int = 40,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code == 200:
            payload: dict[str, Any] = response.json()
            status = payload.get("status")
            if isinstance(status, str) and status in TERMINAL_STATUSES:
                return payload
        time.sleep(poll_interval_seconds)
    raise AssertionError(f"task {task_id} did not reach terminal state")


def scenario_health_ready(client: httpx.Client) -> dict[str, Any]:
    health = client.get(HEALTH_PATH)
    ready = client.get(READY_PATH)
    _assert(health.status_code == 200, f"/health unexpected: {health.status_code}")
    _assert(ready.status_code == 200, f"/ready unexpected: {ready.status_code} {ready.text}")
    payload = ready.json()
    _assert(payload.get("ready") is True, f"/ready payload not ready: {payload}")
    return {"ready_dependencies": payload.get("dependencies", {})}


def scenario_unauthorized_submit(client: httpx.Client) -> dict[str, Any]:
    response = client.post(V1_TASK_SUBMIT_PATH, json={"x": 1, "y": 2})
    _assert(response.status_code == 401, f"expected 401, got {response.status_code}")
    payload = response.json()
    _assert(payload.get("error", {}).get("code") == "UNAUTHORIZED", f"unexpected error: {payload}")
    return {"status": response.status_code}


def scenario_admin_topup(client: httpx.Client, *, admin_key: str, user_key: str) -> dict[str, Any]:
    new_balance = _admin_adjust_to(
        client,
        admin_key=admin_key,
        user_api_key=user_key,
        target_credits=200,
        reason_prefix="scenario_topup",
    )
    _assert(new_balance == 200, f"expected user balance 200, got {new_balance}")
    return {"new_balance": new_balance}


def scenario_submit_poll_v1(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    submit = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}"},
        json={"x": 21, "y": 21},
    )
    _assert(submit.status_code == 201, f"submit failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    terminal = _poll_until_terminal(client, task_id=task_id, api_key=user_key)
    _assert(terminal.get("status") == "COMPLETED", f"terminal not completed: {terminal}")
    _assert(terminal.get("result") == {"z": 42}, f"result mismatch: {terminal}")
    return {"task_id": task_id, "status": terminal.get("status")}


def scenario_submit_poll_compat(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    submit = client.post(
        COMPAT_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}"},
        json={"x": 7, "y": 8},
    )
    _assert(submit.status_code == 201, f"/task failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    for _ in range(40):
        poll = client.get(
            COMPAT_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {user_key}"},
        )
        _assert(poll.status_code == 200, f"/poll failed: {poll.status_code} {poll.text}")
        payload = poll.json()
        status = payload.get("status")
        if isinstance(status, str) and status in TERMINAL_STATUSES:
            _assert(status == "COMPLETED", f"/poll terminal not completed: {payload}")
            _assert(payload.get("result") == {"z": 15}, f"/poll result mismatch: {payload}")
            return {"task_id": task_id, "status": status}
        time.sleep(1.0)
    raise AssertionError("compat poll timed out")


def scenario_idempotency_replay(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    idem = f"scenario-{uuid4()}"
    payload = {"x": 2, "y": 3}
    first = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}", "Idempotency-Key": idem},
        json=payload,
    )
    second = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}", "Idempotency-Key": idem},
        json=payload,
    )
    _assert(first.status_code == 201, f"idempotency first failed: {first.status_code} {first.text}")
    _assert(
        second.status_code == 200, f"idempotency replay failed: {second.status_code} {second.text}"
    )
    first_id = str(first.json()["task_id"])
    second_id = str(second.json()["task_id"])
    _assert(first_id == second_id, f"idempotency task mismatch: {first_id} vs {second_id}")
    return {"task_id": first_id}


def scenario_idempotency_conflict(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    idem = f"scenario-conflict-{uuid4()}"
    first = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}", "Idempotency-Key": idem},
        json={"x": 10, "y": 1},
    )
    second = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}", "Idempotency-Key": idem},
        json={"x": 99, "y": 1},
    )
    _assert(first.status_code == 201, f"conflict first failed: {first.status_code}")
    _assert(second.status_code == 409, f"expected 409, got {second.status_code} {second.text}")
    _assert(
        second.json().get("error", {}).get("code") == "CONFLICT",
        f"expected conflict error code: {second.text}",
    )
    return {"task_id": str(first.json()["task_id"])}


def scenario_insufficient_credits(
    client: httpx.Client, *, admin_key: str, user_key: str
) -> dict[str, Any]:
    new_balance = _admin_adjust_to(
        client,
        admin_key=admin_key,
        user_api_key=user_key,
        target_credits=5,
        reason_prefix="scenario_insufficient",
    )
    _assert(new_balance == 5, f"expected 5 credits, got {new_balance}")
    submit = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_key}"},
        json={"x": 1, "y": 2},
    )
    _assert(submit.status_code == 402, f"expected 402, got {submit.status_code} {submit.text}")
    _assert(
        submit.json().get("error", {}).get("code") == "INSUFFICIENT_CREDITS",
        f"unexpected error payload: {submit.text}",
    )
    return {"status": submit.status_code}


def scenario_cancel_pending(
    client: httpx.Client,
    *,
    repo_root: Path,
    admin_key: str,
    user_key: str,
) -> dict[str, Any]:
    _admin_adjust_to(
        client,
        admin_key=admin_key,
        user_api_key=user_key,
        target_credits=200,
        reason_prefix="scenario_cancel",
    )
    stopped = _compose(repo_root, "stop", "worker")
    _assert(stopped.returncode == 0, f"failed stopping worker: {stopped.stderr}")
    task_id = ""
    try:
        submit = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {user_key}"},
            json={"x": 4, "y": 4},
        )
        _assert(
            submit.status_code == 201, f"cancel submit failed: {submit.status_code} {submit.text}"
        )
        task_id = str(submit.json()["task_id"])

        cancel = client.post(
            f"/v1/task/{task_id}/cancel",
            headers={"Authorization": f"Bearer {user_key}"},
        )
        _assert(cancel.status_code == 200, f"cancel failed: {cancel.status_code} {cancel.text}")
        payload = cancel.json()
        _assert(payload.get("status") == "CANCELLED", f"unexpected cancel payload: {payload}")
        _assert(payload.get("credits_refunded") == 10, f"unexpected refund payload: {payload}")
        return {"task_id": task_id, "status": payload.get("status")}
    finally:
        started = _compose(repo_root, "start", "worker")
        if started.returncode != 0:
            raise AssertionError(f"failed starting worker: {started.stderr}") from None


def scenario_multi_user_concurrency(
    client: httpx.Client,
    *,
    repo_root: Path,
    admin_key: str,
    user1_key: str,
    user2_key: str,
    requests_per_user: int = 10,
) -> dict[str, Any]:
    for user_key, target in ((user1_key, 300), (user2_key, 300)):
        _admin_adjust_to(
            client,
            admin_key=admin_key,
            user_api_key=user_key,
            target_credits=target,
            reason_prefix="scenario_concurrency",
        )

    stopped = _compose(repo_root, "stop", "worker")
    _assert(stopped.returncode == 0, f"failed stopping worker: {stopped.stderr}")

    accepted: dict[str, list[str]] = {user1_key: [], user2_key: []}
    status_counts: dict[str, dict[int, int]] = {
        user1_key: {},
        user2_key: {},
    }
    try:

        def _submit_one(user_key: str, idx: int) -> tuple[str, int, str | None]:
            response = client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {user_key}",
                    "Idempotency-Key": f"concurrency-{user_key}-{idx}-{uuid4()}",
                },
                json={"x": 3, "y": 4},
            )
            task_id = None
            if response.status_code in (200, 201):
                task_id = str(response.json()["task_id"])
            return user_key, response.status_code, task_id

        submissions: list[tuple[str, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=2 * requests_per_user) as executor:
            futures = []
            for idx in range(requests_per_user):
                futures.append(executor.submit(_submit_one, user1_key, idx))
                futures.append(executor.submit(_submit_one, user2_key, idx))
            for future in futures:
                submissions.append(future.result())

        for user_key, status, task_id in submissions:
            status_counts[user_key][status] = status_counts[user_key].get(status, 0) + 1
            if task_id is not None:
                accepted[user_key].append(task_id)

        for user_key in (user1_key, user2_key):
            accepted_count = len(accepted[user_key])
            rejected_count = status_counts[user_key].get(429, 0)
            unexpected = {
                code: count
                for code, count in status_counts[user_key].items()
                if code not in {200, 201, 429}
            }
            _assert(not unexpected, f"unexpected statuses for {user_key}: {unexpected}")
            _assert(
                accepted_count <= 3,
                f"accepted_count exceeded max_concurrent for {user_key}: {accepted_count}",
            )
            _assert(
                rejected_count >= 1,
                f"expected at least one 429 for {user_key}, got {status_counts[user_key]}",
            )

        return {
            "status_counts": status_counts,
            "accepted_count_user1": len(accepted[user1_key]),
            "accepted_count_user2": len(accepted[user2_key]),
        }
    finally:
        started = _compose(repo_root, "start", "worker")
        if started.returncode != 0:
            raise AssertionError(f"failed starting worker: {started.stderr}") from None
        for task_id in accepted[user1_key]:
            _poll_until_terminal(client, task_id=task_id, api_key=user1_key)
        for task_id in accepted[user2_key]:
            _poll_until_terminal(client, task_id=task_id, api_key=user2_key)


def scenario_poll_not_found(client: httpx.Client, *, user_key: str) -> dict[str, Any]:
    missing_id = str(uuid4())
    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": missing_id},
        headers={"Authorization": f"Bearer {user_key}"},
    )
    _assert(response.status_code == 404, f"expected 404, got {response.status_code}")
    _assert(response.json().get("error", {}).get("code") == "NOT_FOUND", response.text)
    return {"task_id": missing_id}


def scenario_python_demo(repo_root: Path, *, base_url: str, user_key: str) -> dict[str, Any]:
    demo_script = repo_root / "utils" / "demo.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(demo_script),
            "--base-url",
            base_url,
            "--api-key",
            user_key,
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    _assert(
        completed.returncode == 0,
        f"python demo script failed: {completed.stdout}\n{completed.stderr}",
    )
    return {"stdout_tail": completed.stdout.strip().splitlines()[-1] if completed.stdout else ""}


def _run_scenario(name: str, fn: Callable[[], dict[str, Any]]) -> ScenarioResult:
    started = time.perf_counter()
    try:
        details = fn()
        return ScenarioResult(
            name=name,
            passed=True,
            duration_seconds=round(time.perf_counter() - started, 3),
            details=details,
        )
    except Exception as exc:  # pragma: no cover - runtime harness
        return ScenarioResult(
            name=name,
            passed=False,
            duration_seconds=round(time.perf_counter() - started, 3),
            details={"error": str(exc)},
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run production-style scenario checks for solution 0."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--admin-key", default=DEFAULT_ADMIN_KEY)
    parser.add_argument("--user1-key", default=DEFAULT_USER1_KEY)
    parser.add_argument("--user2-key", default=DEFAULT_USER2_KEY)
    parser.add_argument(
        "--output",
        default="worklog/evidence/latest/scenarios.json",
        help="Path to scenario JSON output file",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_path = (
        (repo_root / args.output).resolve()
        if not Path(args.output).is_absolute()
        else Path(args.output)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.base_url, timeout=20.0) as client:
        scenarios: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("health_and_ready", lambda: scenario_health_ready(client)),
            ("unauthorized_submit", lambda: scenario_unauthorized_submit(client)),
            (
                "admin_topup",
                lambda: scenario_admin_topup(
                    client, admin_key=args.admin_key, user_key=args.user1_key
                ),
            ),
            ("submit_poll_v1", lambda: scenario_submit_poll_v1(client, user_key=args.user1_key)),
            (
                "submit_poll_compat",
                lambda: scenario_submit_poll_compat(client, user_key=args.user1_key),
            ),
            (
                "idempotency_replay",
                lambda: scenario_idempotency_replay(client, user_key=args.user1_key),
            ),
            (
                "idempotency_conflict",
                lambda: scenario_idempotency_conflict(client, user_key=args.user1_key),
            ),
            (
                "insufficient_credits",
                lambda: scenario_insufficient_credits(
                    client, admin_key=args.admin_key, user_key=args.user2_key
                ),
            ),
            (
                "cancel_pending",
                lambda: scenario_cancel_pending(
                    client,
                    repo_root=repo_root,
                    admin_key=args.admin_key,
                    user_key=args.user1_key,
                ),
            ),
            (
                "multi_user_concurrency",
                lambda: scenario_multi_user_concurrency(
                    client,
                    repo_root=repo_root,
                    admin_key=args.admin_key,
                    user1_key=args.user1_key,
                    user2_key=args.user2_key,
                ),
            ),
            ("poll_not_found", lambda: scenario_poll_not_found(client, user_key=args.user1_key)),
            (
                "python_demo_script",
                lambda: scenario_python_demo(
                    repo_root, base_url=args.base_url, user_key=args.user1_key
                ),
            ),
        ]

        results = [_run_scenario(name, fn) for name, fn in scenarios]

    output = {
        "base_url": args.base_url,
        "generated_at_epoch": int(time.time()),
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [asdict(result) for result in results],
    }
    output_path.write_text(json.dumps(output, indent=2))

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name} ({result.duration_seconds:.3f}s)")
        if not result.passed:
            print(f"  details: {result.details}")

    print(f"scenario report: {output_path}")
    return 0 if output["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
