from __future__ import annotations

from solution1.services.auth import parse_bearer_token


def test_parse_bearer_token_accepts_valid_header() -> None:
    assert parse_bearer_token("Bearer abc123") == "abc123"


def test_parse_bearer_token_rejects_missing_and_malformed_values() -> None:
    assert parse_bearer_token(None) is None
    assert parse_bearer_token("abc123") is None
    assert parse_bearer_token("Basic abc123") is None
    assert parse_bearer_token("Bearer   ") is None
