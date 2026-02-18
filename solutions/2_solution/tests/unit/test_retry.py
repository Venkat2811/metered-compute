from __future__ import annotations

import pytest

from solution2.utils import retry


@pytest.mark.asyncio
async def test_retry_async_applies_jittered_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("retry me")
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(retry, "uniform", lambda *_: 1.2)
    monkeypatch.setattr("solution2.utils.retry.asyncio.sleep", fake_sleep)

    result = await retry.retry_async(
        operation,
        attempts=3,
        base_delay_seconds=0.05,
        max_delay_seconds=0.5,
    )

    assert result == "ok"
    assert calls == 3
    assert sleeps == [0.06, 0.12]
