from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from solution1.utils.lua_scripts import ADMISSION_LUA, parse_lua_result

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _AdmissionKeys:
    credits: str
    idem: str
    active: str
    stream: str
    task: str


def _keys(prefix: str) -> _AdmissionKeys:
    return _AdmissionKeys(
        credits=f"{prefix}:credits",
        idem=f"{prefix}:idem",
        active=f"{prefix}:active",
        stream=f"{prefix}:stream",
        task=f"{prefix}:task",
    )


def _redis_cli(*args: str) -> str:
    command = ["docker", "compose", "exec", "-T", "redis", "redis-cli", "--raw", *args]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise AssertionError(f"redis-cli command failed: {' '.join(command)}\n{completed.stderr}")
    return completed.stdout.strip()


def _eval_admission(
    *,
    keys: _AdmissionKeys,
    task_id: str,
    user_id: str,
    cost: int = 10,
    max_concurrent: int = 3,
    idem_ttl: int = 86_400,
    task_ttl: int = 86_400,
    stream_maxlen: int = 10_000,
) -> str:
    payload_json = json.dumps(
        {
            "task_id": task_id,
            "x": 1,
            "y": 2,
            "model_class": "small",
            "tier": "free",
            "trace_id": f"lua-{task_id}",
            "user_id": user_id,
            "cost": cost,
        }
    )

    return _redis_cli(
        "EVAL",
        ADMISSION_LUA,
        "5",
        keys.credits,
        keys.idem,
        keys.active,
        keys.stream,
        keys.task,
        str(cost),
        task_id,
        str(max_concurrent),
        str(idem_ttl),
        payload_json,
        user_id,
        str(task_ttl),
        str(stream_maxlen),
    )


@pytest.fixture(scope="module", autouse=True)
def _require_redis_compose_service() -> None:
    probe = subprocess.run(
        ["docker", "compose", "ps", "-q", "redis"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        pytest.skip("redis compose service is not running for integration lua contract tests")


@pytest.mark.integration
def test_admission_lua_returns_cache_miss_when_credit_key_missing() -> None:
    prefix = f"it:lua:cache-miss:{uuid4()}"
    keys = _keys(prefix)

    raw = _eval_admission(keys=keys, task_id=str(uuid4()), user_id=str(uuid4()))
    parsed = parse_lua_result(raw)

    assert parsed.ok is False
    assert parsed.reason == "CACHE_MISS"


@pytest.mark.integration
def test_admission_lua_ok_path_sets_atomic_side_effects() -> None:
    prefix = f"it:lua:ok:{uuid4()}"
    keys = _keys(prefix)
    user_id = str(uuid4())
    task_id = str(uuid4())

    _redis_cli("SET", keys.credits, "100")
    _redis_cli("DEL", keys.active)

    raw = _eval_admission(keys=keys, task_id=task_id, user_id=user_id)
    parsed = parse_lua_result(raw)

    assert parsed.ok is True
    assert parsed.reason == "OK"
    assert _redis_cli("GET", keys.credits) == "90"
    assert _redis_cli("GET", keys.idem) == task_id
    assert _redis_cli("GET", keys.active) == "1"

    task_hash = _redis_cli("HGETALL", keys.task).splitlines()
    task_map = dict(zip(task_hash[::2], task_hash[1::2], strict=False))
    assert task_map["status"] == "PENDING"
    assert task_map["user_id"] == user_id
    assert int(_redis_cli("XLEN", keys.stream)) >= 1
    assert _redis_cli("SISMEMBER", "credits:dirty", keys.credits) == "1"


@pytest.mark.integration
def test_admission_lua_idempotent_replay_does_not_double_deduct() -> None:
    prefix = f"it:lua:idem:{uuid4()}"
    keys = _keys(prefix)
    user_id = str(uuid4())
    first_task_id = str(uuid4())

    _redis_cli("SET", keys.credits, "100")

    first = parse_lua_result(_eval_admission(keys=keys, task_id=first_task_id, user_id=user_id))
    second = parse_lua_result(_eval_admission(keys=keys, task_id=str(uuid4()), user_id=user_id))

    assert first.ok is True
    assert second.ok is False
    assert second.reason == "IDEMPOTENT"
    assert second.task_id == first_task_id
    assert _redis_cli("GET", keys.credits) == "90"


@pytest.mark.integration
def test_admission_lua_concurrency_guard_precedes_deduction() -> None:
    prefix = f"it:lua:concurrency:{uuid4()}"
    keys = _keys(prefix)

    _redis_cli("SET", keys.credits, "100")
    _redis_cli("SET", keys.active, "3")

    parsed = parse_lua_result(
        _eval_admission(
            keys=keys,
            task_id=str(uuid4()),
            user_id=str(uuid4()),
            max_concurrent=3,
        )
    )

    assert parsed.ok is False
    assert parsed.reason == "CONCURRENCY"
    assert _redis_cli("GET", keys.credits) == "100"


@pytest.mark.integration
def test_admission_lua_insufficient_credits() -> None:
    prefix = f"it:lua:insufficient:{uuid4()}"
    keys = _keys(prefix)

    _redis_cli("SET", keys.credits, "5")
    _redis_cli("DEL", keys.active)

    parsed = parse_lua_result(
        _eval_admission(
            keys=keys,
            task_id=str(uuid4()),
            user_id=str(uuid4()),
            cost=10,
        )
    )

    assert parsed.ok is False
    assert parsed.reason == "INSUFFICIENT"
    assert _redis_cli("GET", keys.credits) == "5"
