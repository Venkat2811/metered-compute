from __future__ import annotations

import json
from uuid import uuid4

import pytest
from redis.exceptions import NoScriptError

from solution0.services.billing import (
    AdmissionDecision,
    decrement_active_counter,
    hydrate_credits_from_db,
    refund_and_decrement_active,
    run_admission_gate,
)
from tests.constants import TEST_USER_ID, TEST_USER_ID_STR


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.evalsha_calls = 0
        self.script_load_calls = 0
        self.raise_noscript_once = False

    async def set(self, key: str, value: int) -> bool:
        self.values[key] = value
        return True

    async def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def sadd(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.add(value)
        return 1 if len(bucket) > before else 0

    async def evalsha(self, *_: object) -> str:
        self.evalsha_calls += 1
        if self.raise_noscript_once and self.evalsha_calls == 1:
            raise NoScriptError("missing script")
        return json.dumps({"ok": True, "reason": "OK"})

    async def script_load(self, _: str) -> str:
        self.script_load_calls += 1
        return "new-sha"


@pytest.mark.asyncio
async def test_hydrate_credits_from_db_sets_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()

    async def fake_fetch_user_credits_by_api_key(*_: object, **__: object) -> int:
        return 77

    monkeypatch.setattr(
        "solution0.services.billing.fetch_user_credits_by_api_key",
        fake_fetch_user_credits_by_api_key,
    )

    hydrated = await hydrate_credits_from_db(
        redis_client=fake_redis,  # type: ignore[arg-type]
        db_pool=object(),
        api_key="key",
        user_id=TEST_USER_ID,
    )

    assert hydrated is True
    assert fake_redis.values[f"credits:{TEST_USER_ID_STR}"] == 77


@pytest.mark.asyncio
async def test_hydrate_credits_from_db_returns_false_when_user_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    async def fake_fetch_user_credits_by_api_key(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(
        "solution0.services.billing.fetch_user_credits_by_api_key",
        fake_fetch_user_credits_by_api_key,
    )

    hydrated = await hydrate_credits_from_db(
        redis_client=fake_redis,  # type: ignore[arg-type]
        db_pool=object(),
        api_key="missing",
        user_id=TEST_USER_ID,
    )

    assert hydrated is False
    assert fake_redis.values == {}


@pytest.mark.asyncio
async def test_run_admission_gate_parses_success_decision() -> None:
    fake_redis = _FakeRedis()

    decision, sha = await run_admission_gate(
        redis_client=fake_redis,  # type: ignore[arg-type]
        admission_script_sha="sha",
        user_id=uuid4(),
        task_id=uuid4(),
        cost=10,
        idempotency_value="idem",
        idempotency_ttl_seconds=3600,
        max_concurrent=3,
    )

    assert decision == AdmissionDecision(ok=True, reason="OK", existing_task_id=None)
    assert sha == "sha"


@pytest.mark.asyncio
async def test_run_admission_gate_recovers_from_noscript() -> None:
    fake_redis = _FakeRedis()
    fake_redis.raise_noscript_once = True

    decision, sha = await run_admission_gate(
        redis_client=fake_redis,  # type: ignore[arg-type]
        admission_script_sha="old-sha",
        user_id=uuid4(),
        task_id=uuid4(),
        cost=10,
        idempotency_value="idem",
        idempotency_ttl_seconds=3600,
        max_concurrent=3,
    )

    assert decision.reason == "OK"
    assert sha == "new-sha"
    assert fake_redis.script_load_calls == 1


@pytest.mark.asyncio
async def test_refund_and_decrement_active_marks_dirty_and_updates_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()
    user_id = uuid4()

    async def fake_decrement_active_counter(**_: object) -> str:
        return "updated-sha"

    monkeypatch.setattr(
        "solution0.services.billing.decrement_active_counter",
        fake_decrement_active_counter,
    )

    result = await refund_and_decrement_active(
        redis_client=fake_redis,  # type: ignore[arg-type]
        decrement_script_sha="old-sha",
        user_id=user_id,
        amount=25,
    )

    assert result == "updated-sha"
    assert fake_redis.values[f"credits:{user_id}"] == 25
    assert f"credits:{user_id}" in fake_redis.sets["credits:dirty"]


@pytest.mark.asyncio
async def test_decrement_active_counter_recovers_from_noscript() -> None:
    fake_redis = _FakeRedis()
    fake_redis.raise_noscript_once = True

    sha = await decrement_active_counter(
        redis_client=fake_redis,  # type: ignore[arg-type]
        decrement_script_sha="old-sha",
        user_id=uuid4(),
    )

    assert sha == "new-sha"
