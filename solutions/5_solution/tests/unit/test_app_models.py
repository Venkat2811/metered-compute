"""Unit tests for API request models and request-surface contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from solution5.app import SubmitRequest


def test_submit_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({"x": 1, "y": 2, "tier": "pro"})


def test_submit_request_accepts_optional_idempotency_key() -> None:
    model = SubmitRequest.model_validate({"x": 1, "y": 2, "idempotency_key": "scope-check"})
    assert model.idempotency_key == "scope-check"
