#!/usr/bin/env python3
"""Scenario harness for Solution 5 — adapted from Sol 0's run_scenarios.py.

Runs production-style scenario checks against a live docker compose stack.
Each scenario verifies a specific behaviour: auth, billing, submit/poll,
idempotency, cancellation, multi-user concurrency, scope enforcement,
and edge cases.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

# ── Constants ──────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:8000"
ALICE_KEY = os.getenv("TEST_ALICE_API_KEY", "sk-alice-secret-key-001")
ADMIN_KEY = os.getenv("TEST_ADMIN_API_KEY", ALICE_KEY)
BOB_KEY = os.getenv("TEST_BOB_API_KEY", "sk-bob-secret-key-002")
ALICE_ID = "a0000000-0000-0000-0000-000000000001"
BOB_ID = "b0000000-0000-0000-0000-000000000002"
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}


# ── Result dataclass ──────────────────────────────────────────────


@dataclass(slots=True)
class ScenarioResult:
    name: str
    passed: bool
    duration_seconds: float
    details: dict[str, Any]


# ── Helpers ───────────────────────────────────────────────────────


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _admin_topup(
    client: httpx.Client,
    *,
    user_id: str,
    api_key: str,
    amount: int,
) -> int:
    """Topup credits for a user. Returns new balance."""
    resp = client.post(
        "/v1/admin/credits",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"user_id": user_id, "amount": amount},
    )
    _assert(resp.status_code == 200, f"admin topup failed: {resp.status_code} {resp.text}")
    return int(resp.json()["new_balance"])


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    api_key: str,
    max_attempts: int = 40,
    poll_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        response = client.get(
            "/v1/poll",
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


def _submit_task(
    client: httpx.Client,
    *,
    api_key: str,
    x: int = 3,
    y: int = 4,
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Submit a task. Returns (status_code, response_json)."""
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    resp = client.post(
        "/v1/task",
        headers=headers,
        json={"x": x, "y": y, "idempotency_key": idempotency_key},
    )
    return resp.status_code, resp.json()


# ── Scenarios ─────────────────────────────────────────────────────


def scenario_health_ready(client: httpx.Client) -> dict[str, Any]:
    """1. Health and readiness checks return 200."""
    health = client.get("/health")
    ready = client.get("/ready")
    _assert(health.status_code == 200, f"/health unexpected: {health.status_code}")
    _assert(ready.status_code == 200, f"/ready unexpected: {ready.status_code} {ready.text}")
    payload = ready.json()
    _assert(payload.get("postgres") == "ok", f"postgres not ok: {payload}")
    _assert(payload.get("redis") == "ok", f"redis not ok: {payload}")
    return {"health": health.json(), "ready": payload}


def scenario_unauthorized_submit(client: httpx.Client) -> dict[str, Any]:
    """2. Submit without auth returns 401."""
    response = client.post("/v1/task", json={"x": 1, "y": 2})
    _assert(response.status_code == 401, f"expected 401, got {response.status_code}")
    return {"status": response.status_code}


def scenario_admin_topup(client: httpx.Client) -> dict[str, Any]:
    """3. Admin credit topup works."""
    new_balance = _admin_topup(
        client,
        user_id=ALICE_ID,
        api_key=ADMIN_KEY,
        amount=500,
    )
    _assert(new_balance > 0, f"expected positive balance, got {new_balance}")
    return {"new_balance": new_balance}


def scenario_submit_poll(client: httpx.Client) -> dict[str, Any]:
    """4. Submit task, poll until COMPLETED, verify result."""
    # Ensure credits are available
    _admin_topup(client, user_id=ALICE_ID, api_key=ADMIN_KEY, amount=100)

    status_code, data = _submit_task(client, api_key=ALICE_KEY, x=21, y=21)
    _assert(status_code == 201, f"submit failed: {status_code} {data}")
    task_id = str(data["task_id"])

    terminal = _poll_until_terminal(client, task_id=task_id, api_key=ALICE_KEY)
    _assert(terminal.get("status") == "COMPLETED", f"terminal not completed: {terminal}")
    return {"task_id": task_id, "status": terminal.get("status")}


def scenario_idempotency_replay(client: httpx.Client) -> dict[str, Any]:
    """5. Same idempotency key, same payload → same task_id returned."""
    _admin_topup(client, user_id=ALICE_ID, api_key=ADMIN_KEY, amount=100)

    idem = f"scenario-{uuid4()}"
    code1, data1 = _submit_task(client, api_key=ALICE_KEY, x=2, y=3, idempotency_key=idem)
    code2, data2 = _submit_task(client, api_key=ALICE_KEY, x=2, y=3, idempotency_key=idem)

    _assert(code1 == 201, f"first submit failed: {code1} {data1}")
    # Second may be 200 (replay) or 201 (PG upsert) — both are valid
    _assert(code2 in (200, 201), f"replay failed: {code2} {data2}")
    first_id = str(data1["task_id"])
    second_id = str(data2["task_id"])
    _assert(first_id == second_id, f"idempotency task mismatch: {first_id} vs {second_id}")
    return {"task_id": first_id}


def scenario_insufficient_credits(client: httpx.Client) -> dict[str, Any]:
    """6. Submit with insufficient credits returns 402."""
    # Read Bob's current balance via topup endpoint by using an idempotent zero delta.
    # This keeps the scenario deterministic even when TB balances carry over across runs.
    current_balance = _admin_topup(
        client,
        user_id=BOB_ID,
        api_key=ADMIN_KEY,
        amount=0,
    )
    _assert(current_balance >= 0, f"invalid balance: {current_balance}")

    # Submit enough tasks to exhaust exactly the visible balance if accounting is correct.
    max_attempts = min(max(current_balance // 10 + 3, 20), 400)
    drained = False
    for _ in range(max_attempts):
        code, data = _submit_task(client, api_key=BOB_KEY, x=1, y=1)
        if code == 402:
            drained = True
            break
        _assert(code in (200, 201), f"submit failed: {code} {data}")

    _assert(drained, f"bob never ran out of credits after {max_attempts} submissions")
    return {"status": 402, "attempts": max_attempts}


def scenario_cancel_pending(client: httpx.Client) -> dict[str, Any]:
    """7. Cancel a task and verify credits refunded."""
    _admin_topup(client, user_id=ALICE_ID, api_key=ADMIN_KEY, amount=100)

    code, data = _submit_task(client, api_key=ALICE_KEY, x=4, y=4)
    _assert(code == 201, f"submit failed: {code} {data}")
    task_id = str(data["task_id"])

    cancel = client.post(
        f"/v1/task/{task_id}/cancel",
        headers={"Authorization": f"Bearer {ALICE_KEY}"},
    )
    # May be 200 (cancelled/requested) or 409 (already terminal)
    if cancel.status_code == 200:
        payload = cancel.json()
        status = payload.get("status")
        _assert(status in {"CANCELLED", "CANCEL_REQUESTED"}, f"unexpected cancel payload: {payload}")
        if status == "CANCELLED":
            _assert(payload.get("credits_refunded") == 10, f"unexpected refund: {payload}")
            return {"task_id": task_id, "status": "CANCELLED", "credits_refunded": 10}
        if status == "CANCEL_REQUESTED":
            _assert(payload.get("credits_refunded") == 0, f"unexpected refund: {payload}")
            terminal = _poll_until_terminal(
                client,
                task_id=task_id,
                api_key=ALICE_KEY,
                max_attempts=80,
                poll_interval_seconds=0.5,
            )
            _assert(
                terminal.get("status") == "CANCELLED",
                f"task not cancelled after request: {terminal}",
            )
            return {"task_id": task_id, "status": "CANCEL_REQUESTED", "terminal": terminal}
    elif cancel.status_code == 409:
        # Already terminal or invalid transition.
        return {"task_id": task_id, "status": "ALREADY_TERMINAL", "note": cancel.text}
    else:
        raise AssertionError(f"cancel unexpected status: {cancel.status_code} {cancel.text}")


def scenario_poll_not_found(client: httpx.Client) -> dict[str, Any]:
    """8. Poll for a non-existent task returns 404."""
    missing_id = str(uuid4())
    response = client.get(
        "/v1/poll",
        params={"task_id": missing_id},
        headers={"Authorization": f"Bearer {ALICE_KEY}"},
    )
    _assert(response.status_code == 404, f"expected 404, got {response.status_code}")
    return {"task_id": missing_id}


def scenario_cancel_wrong_user(client: httpx.Client) -> dict[str, Any]:
    """9. Bob cannot cancel Alice's task."""
    _admin_topup(client, user_id=ALICE_ID, api_key=ADMIN_KEY, amount=100)

    code, data = _submit_task(client, api_key=ALICE_KEY, x=7, y=7)
    _assert(code == 201, f"submit failed: {code} {data}")
    task_id = str(data["task_id"])

    cancel = client.post(
        f"/v1/task/{task_id}/cancel",
        headers={"Authorization": f"Bearer {BOB_KEY}"},
    )
    _assert(cancel.status_code == 403, f"expected 403, got {cancel.status_code} {cancel.text}")
    return {"task_id": task_id, "status": cancel.status_code}


def scenario_multi_user_concurrency(client: httpx.Client) -> dict[str, Any]:
    """10. Multiple users submit concurrently — all succeed or get 402."""
    _admin_topup(client, user_id=ALICE_ID, api_key=ADMIN_KEY, amount=500)
    _admin_topup(client, user_id=BOB_ID, api_key=ADMIN_KEY, amount=500)

    status_counts: dict[str, dict[int, int]] = {ALICE_KEY: {}, BOB_KEY: {}}
    accepted: dict[str, list[str]] = {ALICE_KEY: [], BOB_KEY: []}
    requests_per_user = 10

    def _submit_one(user_key: str, idx: int) -> tuple[str, int, str | None]:
        code, data = _submit_task(client, api_key=user_key, x=idx, y=idx)
        task_id = str(data.get("task_id", "")) if code in (200, 201) else None
        return user_key, code, task_id

    with ThreadPoolExecutor(max_workers=2 * requests_per_user) as executor:
        futures = []
        for idx in range(requests_per_user):
            futures.append(executor.submit(_submit_one, ALICE_KEY, idx))
            futures.append(executor.submit(_submit_one, BOB_KEY, idx))
        for future in futures:
            user_key, code, task_id = future.result()
            status_counts[user_key][code] = status_counts[user_key].get(code, 0) + 1
            if task_id:
                accepted[user_key].append(task_id)

    for user_key in (ALICE_KEY, BOB_KEY):
        unexpected = {code: count for code, count in status_counts[user_key].items() if code not in {200, 201, 402}}
        _assert(
            not unexpected,
            f"unexpected statuses for {user_key}: {unexpected}",
        )

    # Wait for accepted tasks to complete
    for user_key in (ALICE_KEY, BOB_KEY):
        for task_id in accepted[user_key]:
            _poll_until_terminal(client, task_id=task_id, api_key=user_key)

    return {
        "status_counts": status_counts,
        "accepted_alice": len(accepted[ALICE_KEY]),
        "accepted_bob": len(accepted[BOB_KEY]),
    }


def scenario_unsupported_surface(client: httpx.Client) -> dict[str, Any]:
    """13. Unsupported surfaces are rejected clearly."""
    response = client.post(
        "/v1/task",
        json={"x": 1, "y": 2, "model_class": "small"},
    )
    _assert(response.status_code == 422, f"expected 422, got {response.status_code}")

    batch = client.post("/v1/task/batch", json={"tasks": [{"x": 1, "y": 2}]})
    _assert(batch.status_code == 404, f"expected 404, got {batch.status_code}")

    compat = client.post("/task", json={"x": 1, "y": 2})
    _assert(compat.status_code in {404, 405}, f"expected compat path to be unsupported: {compat.status_code}")

    return {
        "submit_payload_rejected": response.status_code,
        "batch_status": batch.status_code,
        "compat_status": compat.status_code,
    }


def scenario_metrics_available(client: httpx.Client) -> dict[str, Any]:
    """11. Prometheus /metrics endpoint returns valid metrics."""
    response = client.get("/metrics")
    _assert(response.status_code == 200, f"/metrics unexpected: {response.status_code}")
    text = response.text
    _assert("task_submitted_total" in text, "missing task_submitted_total metric")
    _assert("credit_reserved_total" in text, "missing credit_reserved_total metric")
    return {"has_task_submitted": True, "has_credit_reserved": True}


def scenario_demo_script(*, base_url: str) -> dict[str, Any]:
    """12. Demo script runs without errors."""
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    demo_script = repo_root / "scripts" / "demo.sh"
    completed = subprocess.run(
        ["bash", str(demo_script)],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "API_BASE_URL": base_url},
    )
    _assert(
        completed.returncode == 0,
        f"demo script failed (exit {completed.returncode}):\n{completed.stdout}\n{completed.stderr}",
    )
    return {"stdout_tail": completed.stdout.strip().splitlines()[-1] if completed.stdout else ""}


# ── Runner ────────────────────────────────────────────────────────


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
    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            duration_seconds=round(time.perf_counter() - started, 3),
            details={"error": str(exc)},
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production-style scenario checks for solution 5.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--output",
        default="worklog/evidence/latest/scenarios.json",
        help="Path to scenario JSON output file",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_path = (repo_root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(base_url=args.base_url, timeout=20.0) as client:
        scenarios: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("health_and_ready", lambda: scenario_health_ready(client)),
            ("unauthorized_submit", lambda: scenario_unauthorized_submit(client)),
            ("admin_topup", lambda: scenario_admin_topup(client)),
            ("submit_poll", lambda: scenario_submit_poll(client)),
            ("idempotency_replay", lambda: scenario_idempotency_replay(client)),
            ("insufficient_credits", lambda: scenario_insufficient_credits(client)),
            ("cancel_pending", lambda: scenario_cancel_pending(client)),
            ("poll_not_found", lambda: scenario_poll_not_found(client)),
            ("cancel_wrong_user", lambda: scenario_cancel_wrong_user(client)),
            ("multi_user_concurrency", lambda: scenario_multi_user_concurrency(client)),
            ("unsupported_surface", lambda: scenario_unsupported_surface(client)),
            ("metrics_available", lambda: scenario_metrics_available(client)),
            ("demo_script", lambda: scenario_demo_script(base_url=args.base_url)),
        ]

        results = [_run_scenario(name, fn) for name, fn in scenarios]

    output = {
        "base_url": args.base_url,
        "generated_at_epoch": int(time.time()),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "results": [asdict(r) for r in results],
    }
    output_path.write_text(json.dumps(output, indent=2))

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name} ({result.duration_seconds:.3f}s)")
        if not result.passed:
            print(f"  details: {result.details}")

    print(f"\nscenario report: {output_path}")
    return 0 if output["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
