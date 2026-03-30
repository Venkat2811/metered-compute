#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from solution3.api.paths import (
    V1_ADMIN_CREDITS_PATH,
    V1_OAUTH_TOKEN_PATH,
    V1_TASK_CANCEL_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution3.core.settings import load_settings

DEFAULT_BASE_URL = "http://localhost:8000"
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}


@dataclass(slots=True)
class ScenarioResult:
    name: str
    passed: bool
    duration_seconds: float
    details: dict[str, Any]


ScenarioFn = Callable[[httpx.Client], dict[str, Any]]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=_repo_root(),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _oauth_token(client: httpx.Client, *, api_key: str) -> str:
    response = client.post(V1_OAUTH_TOKEN_PATH, json={"api_key": api_key})
    _assert(response.status_code == 200, f"oauth token exchange failed: {response.status_code}")
    token = str(response.json().get("access_token", ""))
    _assert(token.count(".") == 2, "oauth token payload is not jwt-like")
    return token


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    access_token: str,
    max_attempts: int = 60,
    poll_interval_seconds: float = 0.5,
) -> dict[str, Any]:
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
            return payload
        time.sleep(poll_interval_seconds)
    raise AssertionError(f"task {task_id} did not reach terminal state")


def scenario_health_ready(client: httpx.Client) -> dict[str, Any]:
    health = client.get("/health")
    ready = client.get("/ready")
    _assert(health.status_code == 200, f"/health unexpected: {health.status_code}")
    _assert(ready.status_code == 200, f"/ready unexpected: {ready.status_code} {ready.text}")
    payload = ready.json()
    _assert(payload.get("ready") is True, f"/ready payload not ready: {payload}")
    return {"dependencies": payload.get("dependencies")}


def scenario_unauthorized_submit(client: httpx.Client) -> dict[str, Any]:
    response = client.post(V1_TASK_SUBMIT_PATH, json={"x": 1, "y": 2})
    _assert(response.status_code == 401, f"expected 401, got {response.status_code}")
    return {"status": response.status_code}


def scenario_oauth_exchange(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    token = _oauth_token(client, api_key=settings.alice_api_key)
    return {"token_prefix": token.split(".", 1)[0]}


def _submit_task(
    client: httpx.Client,
    *,
    access_token: str,
    x: int,
    y: int,
    model_class: str = "small",
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {access_token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return client.post(
        V1_TASK_SUBMIT_PATH,
        headers=headers,
        json={"x": x, "y": y, "model_class": model_class},
    )


def scenario_submit_poll_small(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    token = _oauth_token(client, api_key=settings.alice_api_key)
    submit = _submit_task(client, access_token=token, x=11, y=13)
    _assert(submit.status_code == 201, f"submit failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    terminal = _poll_until_terminal(client, task_id=task_id, access_token=token)
    _assert(terminal.get("status") == "COMPLETED", f"unexpected terminal payload: {terminal}")
    _assert(terminal.get("result") == {"sum": 24}, f"unexpected result payload: {terminal}")
    return {"task_id": task_id, "status": terminal["status"]}


def scenario_submit_poll_medium(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    token = _oauth_token(client, api_key=settings.alice_api_key)
    submit = _submit_task(client, access_token=token, x=5, y=8, model_class="medium")
    _assert(submit.status_code == 201, f"submit failed: {submit.status_code} {submit.text}")
    task_id = str(submit.json()["task_id"])
    terminal = _poll_until_terminal(client, task_id=task_id, access_token=token)
    _assert(terminal.get("status") == "COMPLETED", f"unexpected terminal payload: {terminal}")
    _assert(terminal.get("result") == {"sum": 13}, f"unexpected result payload: {terminal}")
    return {"task_id": task_id, "status": terminal["status"]}


def scenario_idempotency_replay(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    token = _oauth_token(client, api_key=settings.alice_api_key)
    idem = f"scenario-{uuid4()}"
    first = _submit_task(client, access_token=token, x=2, y=3, idempotency_key=idem)
    second = _submit_task(client, access_token=token, x=2, y=3, idempotency_key=idem)
    _assert(first.status_code == 201, f"first submit failed: {first.status_code} {first.text}")
    _assert(second.status_code == 200, f"second submit failed: {second.status_code} {second.text}")
    first_id = str(first.json()["task_id"])
    second_id = str(second.json()["task_id"])
    _assert(first_id == second_id, f"idempotency mismatch: {first_id} vs {second_id}")
    return {"task_id": first_id}


def scenario_admin_credits_success(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    admin_token = _oauth_token(client, api_key=settings.admin_api_key)
    first = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": settings.bob_api_key, "amount": 3, "reason": "scenario_topup_1"},
    )
    _assert(first.status_code == 200, f"expected 200, got {first.status_code} {first.text}")
    first_payload = first.json()
    _assert(first_payload.get("api_key") == settings.bob_api_key, first_payload)
    _assert(isinstance(first_payload.get("new_balance"), int), first_payload)

    second = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": settings.bob_api_key, "amount": 4, "reason": "scenario_topup_2"},
    )
    _assert(second.status_code == 200, f"expected 200, got {second.status_code} {second.text}")
    second_payload = second.json()
    _assert(second_payload.get("api_key") == settings.bob_api_key, second_payload)
    _assert(second_payload.get("new_balance") == first_payload["new_balance"] + 4, second_payload)
    return {"new_balance": second_payload["new_balance"]}


def scenario_cancel_while_worker_paused(client: httpx.Client) -> dict[str, Any]:
    settings = load_settings()
    token = _oauth_token(client, api_key=settings.alice_api_key)
    stopped = _compose("stop", "worker")
    _assert(stopped.returncode == 0, f"failed to stop worker: {stopped.stderr.strip()}")
    try:
        submit = _submit_task(client, access_token=token, x=21, y=34)
        _assert(submit.status_code == 201, f"submit failed: {submit.status_code} {submit.text}")
        task_id = str(submit.json()["task_id"])
        cancel = client.post(
            V1_TASK_CANCEL_PATH.format(task_id=task_id),
            headers={"Authorization": f"Bearer {token}"},
        )
        _assert(cancel.status_code == 200, f"cancel failed: {cancel.status_code} {cancel.text}")
        payload = cancel.json()
        _assert(payload.get("status") == "CANCELLED", f"unexpected cancel payload: {payload}")
        return {"task_id": task_id, "status": payload["status"]}
    finally:
        restarted = _compose("up", "-d", "worker")
        _assert(restarted.returncode == 0, f"failed to restart worker: {restarted.stderr.strip()}")


def _scenario_registry() -> dict[str, ScenarioFn]:
    return {
        "health_ready": scenario_health_ready,
        "unauthorized_submit": scenario_unauthorized_submit,
        "oauth_exchange": scenario_oauth_exchange,
        "submit_poll_small": scenario_submit_poll_small,
        "submit_poll_medium": scenario_submit_poll_medium,
        "idempotency_replay": scenario_idempotency_replay,
        "admin_credits_success": scenario_admin_credits_success,
        "cancel_while_worker_paused": scenario_cancel_while_worker_paused,
    }


def _resolve_selected_scenarios(
    selected: list[str] | None,
    *,
    registry: Mapping[str, object],
) -> list[str]:
    if not selected:
        return list(registry)
    unknown = [name for name in selected if name not in registry]
    if unknown:
        raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
    return selected


def build_report(results: list[ScenarioResult], *, base_url: str) -> dict[str, Any]:
    passed = sum(1 for result in results if result.passed)
    return {
        "base_url": base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "scenarios": [asdict(result) for result in results],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic Solution 3 scenarios.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--output",
        default="worklog/evidence/scenarios/latest-scenarios.json",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Scenario name to run. May be provided multiple times. Default: run all.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    registry = _scenario_registry()
    selected = _resolve_selected_scenarios(args.scenario, registry=registry)
    results: list[ScenarioResult] = []

    with httpx.Client(base_url=args.base_url, timeout=15.0) as client:
        for name in selected:
            started = time.perf_counter()
            try:
                details = registry[name](client)
            except Exception as exc:
                results.append(
                    ScenarioResult(
                        name=name,
                        passed=False,
                        duration_seconds=round(time.perf_counter() - started, 3),
                        details={"error": str(exc)},
                    )
                )
                continue
            results.append(
                ScenarioResult(
                    name=name,
                    passed=True,
                    duration_seconds=round(time.perf_counter() - started, 3),
                    details=details,
                )
            )

    report = build_report(results, base_url=args.base_url)
    output_path = (_repo_root() / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
