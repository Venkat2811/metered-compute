from __future__ import annotations

import pytest

from solution0.utils.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_transient_failures() -> None:
    attempts = {"count": 0}

    async def flaky_operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await retry_async(
        flaky_operation,
        attempts=3,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
    )

    assert result == "ok"
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_raises_after_max_attempts() -> None:
    attempts = {"count": 0}

    async def always_fail() -> None:
        attempts["count"] += 1
        raise RuntimeError("still failing")

    with pytest.raises(RuntimeError):
        await retry_async(
            always_fail,
            attempts=2,
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
        )

    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_retry_async_applies_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    async def flaky_operation() -> None:
        attempts["count"] += 1
        raise RuntimeError("still failing")

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("solution0.utils.retry.uniform", lambda _a, _b: 1.25)
    monkeypatch.setattr("solution0.utils.retry.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError):
        await retry_async(
            flaky_operation,
            attempts=2,
            base_delay_seconds=1.0,
            max_delay_seconds=5.0,
        )

    assert attempts["count"] == 2
    assert sleep_calls == [1.25]
