from __future__ import annotations

from solution1.utils.lua_scripts import parse_lua_result


def test_parse_lua_result_ok() -> None:
    parsed = parse_lua_result('{"ok":true,"reason":"OK"}')
    assert parsed.ok is True
    assert parsed.reason == "OK"
    assert parsed.task_id is None


def test_parse_lua_result_idempotent() -> None:
    parsed = parse_lua_result('{"ok":false,"reason":"IDEMPOTENT","task_id":"abc"}')
    assert parsed.ok is False
    assert parsed.reason == "IDEMPOTENT"
    assert parsed.task_id == "abc"


def test_parse_lua_result_insufficient() -> None:
    parsed = parse_lua_result('{"ok":false,"reason":"INSUFFICIENT"}')
    assert parsed.ok is False
    assert parsed.reason == "INSUFFICIENT"
    assert parsed.task_id is None


def test_parse_lua_result_concurrency() -> None:
    parsed = parse_lua_result('{"ok":false,"reason":"CONCURRENCY"}')
    assert parsed.ok is False
    assert parsed.reason == "CONCURRENCY"
    assert parsed.task_id is None


def test_parse_lua_result_cache_miss() -> None:
    parsed = parse_lua_result('{"ok":false,"reason":"CACHE_MISS"}')
    assert parsed.ok is False
    assert parsed.reason == "CACHE_MISS"
    assert parsed.task_id is None
