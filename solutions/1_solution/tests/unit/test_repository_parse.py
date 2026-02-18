from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast

from solution1.db.repository import _parse_task_result, is_active_api_key_hash


def test_parse_task_result_handles_dict_value() -> None:
    result = _parse_task_result({"z": 42})
    assert result == {"z": 42}


def test_parse_task_result_handles_json_string_value() -> None:
    result = _parse_task_result('{"z":42}')
    assert result == {"z": 42}


def test_parse_task_result_returns_none_for_non_mapping_payload() -> None:
    assert _parse_task_result("[1,2,3]") is None
    assert _parse_task_result(123) is None


def test_is_active_api_key_hash_uses_sha256_lookup_key() -> None:
    class _FakePool:
        def __init__(self) -> None:
            self.hash_value = ""

        async def fetchval(self, _: str, key_hash: str) -> bool:
            self.hash_value = key_hash
            return True

    fake_pool = _FakePool()
    exists = asyncio.run(is_active_api_key_hash(cast(Any, fake_pool), "example-api-key"))
    assert exists is True
    assert fake_pool.hash_value == hashlib.sha256(b"example-api-key").hexdigest()
