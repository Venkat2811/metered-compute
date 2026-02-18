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

from solution2.api.paths import (
    COMPAT_TASK_POLL_PATH,
    COMPAT_TASK_SUBMIT_PATH,
    HEALTH_PATH,
    READY_PATH,
    V1_ADMIN_CREDITS_PATH,
    V1_OAUTH_TOKEN_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution2.constants import SubscriptionTier, max_concurrent_for_tier
from solution2.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_USER1_API_KEY,
    DEFAULT_USER2_API_KEY,
)

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_ADMIN_KEY = os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
DEFAULT_USER1_KEY = os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY)
DEFAULT_USER2_KEY = os.getenv("BOB_API_KEY", DEFAULT_USER2_API_KEY)
DEFAULT_BASE_MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "3"))
DEFAULT_USER1_TIER = SubscriptionTier(os.getenv("OAUTH_USER1_TIER", SubscriptionTier.PRO.value))
DEFAULT_USER2_TIER = SubscriptionTier(os.getenv("OAUTH_USER2_TIER", SubscriptionTier.FREE.value))
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
    admin_token: str,
    user_api_key: str,
    target_credits: int,
    reason_prefix: str,
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": user_api_key, "delta": 0, "reason": f"{reason_prefix}_probe"},
    )
    _assert(probe.status_code == 200, f"admin probe failed: {probe.status_code} {probe.text}")
    current = int(probe.json()["new_balance"])
    delta = target_credits - current
    if delta == 0:
        return current
    change = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": user_api_key, "delta": delta, "reason": f"{reason_prefix}_set"},
    )
    _assert(change.status_code == 200, f"admin adjust failed: {change.status_code} {change.text}")
    return int(change.json()["new_balance"])


def _admin_balance(
    client: httpx.Client, *, admin_token: str, user_api_key: str, reason: str
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": user_api_key, "delta": 0, "reason": reason},
    )
    _assert(
        probe.status_code == 200, f"admin balance probe failed: {probe.status_code} {probe.text}"
    )
    return int(probe.json()["new_balance"])


def _oauth_token(client: httpx.Client, *, api_key: str, scope: str | None = None) -> str:
    payload: dict[str, str] = {"api_key": api_key}
    if scope is not None:
        payload["scope"] = scope
    response = client.post(V1_OAUTH_TOKEN_PATH, json=payload)
    _assert(response.status_code == 200, f"oauth token exchange failed: {response.status_code}")
    token = str(response.json().get("access_token", ""))
    _assert(token.count(".") == 2, "oauth token payload is not jwt-like")
    return token


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    access_token: str,
    max_attempts: int = 40,
    poll_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
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


def scenario_admin_topup(
    client: httpx.Client,
    *,
    admin_token: str,
    user_api_key: str,
) -> dict[str, Any]:
    new_balance = _admin_adjust_to(
        client,
        admin_token=admin_token,
        user_api_key=user_api_key,
        target_credits=200,
        reason_prefix="scenario_topup",
    )
    _assert(new_balance == 200, f"expected user balance 200, got {new_balance}")
    return {"new_balance": new_balance}


def scenario_submit_poll_v1(client: httpx.Client, *, user_token: str) -> dict[str, Any]:
    submit = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}"},
        json={"x": 21, "y": 21},
    )
    _assert(submit.status_code == 201, f"submit failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    terminal = _poll_until_terminal(client, task_id=task_id, access_token=user_token)
    _assert(terminal.get("status") == "COMPLETED", f"terminal not completed: {terminal}")
    _assert(terminal.get("result") == {"z": 42}, f"result mismatch: {terminal}")
    return {"task_id": task_id, "status": terminal.get("status")}


def scenario_submit_poll_compat(client: httpx.Client, *, user_token: str) -> dict[str, Any]:
    submit = client.post(
        COMPAT_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}"},
        json={"x": 7, "y": 8},
    )
    _assert(submit.status_code == 201, f"/task failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    for _ in range(40):
        poll = client.get(
            COMPAT_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {user_token}"},
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


def scenario_idempotency_replay(client: httpx.Client, *, user_token: str) -> dict[str, Any]:
    idem = f"scenario-{uuid4()}"
    payload = {"x": 2, "y": 3}
    first = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}", "Idempotency-Key": idem},
        json=payload,
    )
    second = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}", "Idempotency-Key": idem},
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


def scenario_idempotency_conflict(client: httpx.Client, *, user_token: str) -> dict[str, Any]:
    idem = f"scenario-conflict-{uuid4()}"
    first = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}", "Idempotency-Key": idem},
        json={"x": 10, "y": 1},
    )
    second = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}", "Idempotency-Key": idem},
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
    client: httpx.Client,
    *,
    admin_token: str,
    user_api_key: str,
    user_token: str,
) -> dict[str, Any]:
    new_balance = _admin_adjust_to(
        client,
        admin_token=admin_token,
        user_api_key=user_api_key,
        target_credits=5,
        reason_prefix="scenario_insufficient",
    )
    _assert(new_balance == 5, f"expected 5 credits, got {new_balance}")
    submit = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}"},
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
    admin_token: str,
    user_api_key: str,
    user_token: str,
) -> dict[str, Any]:
    _admin_adjust_to(
        client,
        admin_token=admin_token,
        user_api_key=user_api_key,
        target_credits=200,
        reason_prefix="scenario_cancel",
    )
    stopped = _compose(repo_root, "stop", "worker")
    _assert(stopped.returncode == 0, f"failed stopping worker: {stopped.stderr}")
    task_id = ""
    try:
        submit = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {user_token}"},
            json={"x": 4, "y": 4},
        )
        _assert(
            submit.status_code == 201, f"cancel submit failed: {submit.status_code} {submit.text}"
        )
        task_id = str(submit.json()["task_id"])

        cancel = client.post(
            f"/v1/task/{task_id}/cancel",
            headers={"Authorization": f"Bearer {user_token}"},
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
    admin_token: str,
    user1_api_key: str,
    user2_api_key: str,
    user1_token: str,
    user2_token: str,
    requests_per_user: int = 10,
) -> dict[str, Any]:
    expected_limits = {
        "user1": max_concurrent_for_tier(
            base_max_concurrent=DEFAULT_BASE_MAX_CONCURRENT,
            tier=DEFAULT_USER1_TIER,
        ),
        "user2": max_concurrent_for_tier(
            base_max_concurrent=DEFAULT_BASE_MAX_CONCURRENT,
            tier=DEFAULT_USER2_TIER,
        ),
    }

    for user_key, target in ((user1_api_key, 300), (user2_api_key, 300)):
        _admin_adjust_to(
            client,
            admin_token=admin_token,
            user_api_key=user_key,
            target_credits=target,
            reason_prefix="scenario_concurrency",
        )

    stopped = _compose(repo_root, "stop", "worker")
    _assert(stopped.returncode == 0, f"failed stopping worker: {stopped.stderr}")

    accepted: dict[str, list[str]] = {"user1": [], "user2": []}
    status_counts: dict[str, dict[int, int]] = {"user1": {}, "user2": {}}
    try:

        def _submit_one(token: str, lane: str, idx: int) -> tuple[str, int, str | None]:
            response = client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": f"concurrency-{lane}-{idx}-{uuid4()}",
                },
                json={"x": 3, "y": 4},
            )
            task_id = None
            if response.status_code in (200, 201):
                task_id = str(response.json()["task_id"])
            return lane, response.status_code, task_id

        submissions: list[tuple[str, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=2 * requests_per_user) as executor:
            futures = []
            for idx in range(requests_per_user):
                futures.append(executor.submit(_submit_one, user1_token, "user1", idx))
                futures.append(executor.submit(_submit_one, user2_token, "user2", idx))
            for future in futures:
                submissions.append(future.result())

        for lane, status, task_id in submissions:
            status_counts[lane][status] = status_counts[lane].get(status, 0) + 1
            if task_id is not None:
                accepted[lane].append(task_id)

        for lane in ("user1", "user2"):
            accepted_count = len(accepted[lane])
            rejected_count = status_counts[lane].get(429, 0)
            unexpected = {
                code: count
                for code, count in status_counts[lane].items()
                if code not in {200, 201, 429}
            }
            _assert(not unexpected, f"unexpected statuses for {lane}: {unexpected}")
            _assert(
                accepted_count <= expected_limits[lane],
                "accepted_count exceeded max_concurrent "
                f"for {lane}: {accepted_count} > {expected_limits[lane]}",
            )
            _assert(
                rejected_count >= 1,
                f"expected at least one 429 for {lane}, got {status_counts[lane]}",
            )

        return {
            "status_counts": status_counts,
            "accepted_count_user1": len(accepted["user1"]),
            "accepted_count_user2": len(accepted["user2"]),
        }
    finally:
        started = _compose(repo_root, "start", "worker")
        if started.returncode != 0:
            raise AssertionError(f"failed starting worker: {started.stderr}") from None
        for task_id in accepted["user1"]:
            _poll_until_terminal(client, task_id=task_id, access_token=user1_token)
        for task_id in accepted["user2"]:
            _poll_until_terminal(client, task_id=task_id, access_token=user2_token)


def scenario_poll_not_found(client: httpx.Client, *, user_token: str) -> dict[str, Any]:
    missing_id = str(uuid4())
    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": missing_id},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    _assert(response.status_code == 404, f"expected 404, got {response.status_code}")
    _assert(response.json().get("error", {}).get("code") == "NOT_FOUND", response.text)
    return {"task_id": missing_id}


def scenario_jwt_tier_model_stress(
    client: httpx.Client,
    *,
    repo_root: Path,
    admin_token: str,
    user1_token: str,
    user2_token: str,
    user1_api_key: str,
    user2_api_key: str,
) -> dict[str, Any]:
    pro_token = user1_token
    free_token = user2_token

    for user_key in (user1_api_key, user2_api_key):
        _admin_adjust_to(
            client,
            admin_token=admin_token,
            user_api_key=user_key,
            target_credits=900,
            reason_prefix="scenario_jwt_tier",
        )

    stopped = _compose(repo_root, "stop", "worker")
    _assert(stopped.returncode == 0, f"failed stopping worker: {stopped.stderr}")

    accepted: dict[str, list[str]] = {"pro": [], "free": []}
    status_counts: dict[str, dict[int, int]] = {"pro": {}, "free": {}}
    try:

        def _submit_one(token: str, lane: str, idx: int) -> tuple[str, int, str | None]:
            response = client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": f"jwt-tier-{lane}-{idx}-{uuid4()}",
                },
                json={"x": 6, "y": 7, "model_class": "small"},
            )
            task_id = None
            if response.status_code in (200, 201):
                task_id = str(response.json()["task_id"])
            return lane, response.status_code, task_id

        submissions: list[tuple[str, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = []
            for idx in range(12):
                futures.append(executor.submit(_submit_one, pro_token, "pro", idx))
                futures.append(executor.submit(_submit_one, free_token, "free", idx))
            for future in futures:
                submissions.append(future.result())

        for lane, status, task_id in submissions:
            status_counts[lane][status] = status_counts[lane].get(status, 0) + 1
            if task_id is not None:
                accepted[lane].append(task_id)

        _assert(
            all(code in {200, 201, 429} for code in status_counts["pro"]),
            f"unexpected pro statuses: {status_counts['pro']}",
        )
        _assert(
            all(code in {200, 201, 429} for code in status_counts["free"]),
            f"unexpected free statuses: {status_counts['free']}",
        )
        _assert(len(accepted["pro"]) <= 6, f"pro accepted exceeds tier envelope: {status_counts}")
        _assert(
            len(accepted["free"]) <= 3,
            f"free accepted exceeds tier envelope: {status_counts}",
        )
        _assert(
            len(accepted["pro"]) > len(accepted["free"]),
            f"tier differentiation missing: {status_counts}",
        )
    finally:
        started = _compose(repo_root, "start", "worker")
        _assert(started.returncode == 0, f"failed starting worker: {started.stderr}")
        for task_id in accepted["pro"]:
            _poll_until_terminal(client, task_id=task_id, access_token=pro_token)
        for task_id in accepted["free"]:
            _poll_until_terminal(client, task_id=task_id, access_token=free_token)

    _admin_adjust_to(
        client,
        admin_token=admin_token,
        user_api_key=user1_api_key,
        target_credits=500,
        reason_prefix="scenario_jwt_model_cost",
    )
    before = _admin_balance(
        client,
        admin_token=admin_token,
        user_api_key=user1_api_key,
        reason="scenario_jwt_model_cost_probe_before",
    )

    small = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {pro_token}", "Idempotency-Key": f"jwt-small-{uuid4()}"},
        json={"x": 1, "y": 1, "model_class": "small"},
    )
    _assert(
        small.status_code == 201,
        f"small model submit failed: {small.status_code} {small.text}",
    )
    small_id = str(small.json()["task_id"])
    _poll_until_terminal(client, task_id=small_id, access_token=pro_token)
    after_small = _admin_balance(
        client,
        admin_token=admin_token,
        user_api_key=user1_api_key,
        reason="scenario_jwt_model_cost_probe_small",
    )

    large = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {pro_token}", "Idempotency-Key": f"jwt-large-{uuid4()}"},
        json={"x": 2, "y": 2, "model_class": "large"},
    )
    _assert(
        large.status_code == 201,
        f"large model submit failed: {large.status_code} {large.text}",
    )
    large_id = str(large.json()["task_id"])
    _poll_until_terminal(client, task_id=large_id, access_token=pro_token)
    after_large = _admin_balance(
        client,
        admin_token=admin_token,
        user_api_key=user1_api_key,
        reason="scenario_jwt_model_cost_probe_large",
    )

    _assert(
        before - after_small == 10,
        f"unexpected small model deduction: {before}->{after_small}",
    )
    _assert(
        after_small - after_large == 50,
        f"unexpected large model deduction: {after_small}->{after_large}",
    )

    return {
        "accepted_pro": len(accepted["pro"]),
        "accepted_free": len(accepted["free"]),
        "status_counts": status_counts,
        "deduction_small": before - after_small,
        "deduction_large": after_small - after_large,
    }


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
        description="Run production-style scenario checks for solution 1."
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
        admin_token = _oauth_token(
            client,
            api_key=args.admin_key,
            scope="task:submit task:poll task:cancel admin:credits",
        )
        user1_token = _oauth_token(client, api_key=args.user1_key)
        user2_token = _oauth_token(client, api_key=args.user2_key)

        scenarios: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("health_and_ready", lambda: scenario_health_ready(client)),
            ("unauthorized_submit", lambda: scenario_unauthorized_submit(client)),
            (
                "admin_topup",
                lambda: scenario_admin_topup(
                    client,
                    admin_token=admin_token,
                    user_api_key=args.user1_key,
                ),
            ),
            ("submit_poll_v1", lambda: scenario_submit_poll_v1(client, user_token=user1_token)),
            (
                "submit_poll_compat",
                lambda: scenario_submit_poll_compat(client, user_token=user1_token),
            ),
            (
                "idempotency_replay",
                lambda: scenario_idempotency_replay(client, user_token=user1_token),
            ),
            (
                "idempotency_conflict",
                lambda: scenario_idempotency_conflict(client, user_token=user1_token),
            ),
            (
                "insufficient_credits",
                lambda: scenario_insufficient_credits(
                    client,
                    admin_token=admin_token,
                    user_api_key=args.user2_key,
                    user_token=user2_token,
                ),
            ),
            (
                "cancel_pending",
                lambda: scenario_cancel_pending(
                    client,
                    repo_root=repo_root,
                    admin_token=admin_token,
                    user_api_key=args.user1_key,
                    user_token=user1_token,
                ),
            ),
            (
                "multi_user_concurrency",
                lambda: scenario_multi_user_concurrency(
                    client,
                    repo_root=repo_root,
                    admin_token=admin_token,
                    user1_api_key=args.user1_key,
                    user2_api_key=args.user2_key,
                    user1_token=user1_token,
                    user2_token=user2_token,
                ),
            ),
            ("poll_not_found", lambda: scenario_poll_not_found(client, user_token=user1_token)),
            (
                "python_demo_script",
                lambda: scenario_python_demo(
                    repo_root, base_url=args.base_url, user_key=args.user1_key
                ),
            ),
            (
                "jwt_tier_model_stress",
                lambda: scenario_jwt_tier_model_stress(
                    client,
                    repo_root=repo_root,
                    admin_token=admin_token,
                    user1_token=user1_token,
                    user2_token=user2_token,
                    user1_api_key=args.user1_key,
                    user2_api_key=args.user2_key,
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
