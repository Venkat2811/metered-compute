"""Unit tests for API request models and request-surface contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from solution5.app import AdminCreditsRequest, SubmitRequest, _derive_admin_topup_transfer_id


def test_submit_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({"x": 1, "y": 2, "tier": "pro"})


def test_submit_request_accepts_optional_idempotency_key() -> None:
    model = SubmitRequest.model_validate({"x": 1, "y": 2, "idempotency_key": "scope-check"})
    assert model.idempotency_key == "scope-check"


def test_admin_credits_request_accepts_optional_retry_controls() -> None:
    model = AdminCreditsRequest.model_validate(
        {
            "user_id": "a0000000-0000-0000-0000-000000000001",
            "amount": 50,
            "idempotency_key": "retry-1",
            "transfer_id": "00000000-0000-0000-0000-000000000123",
        }
    )
    assert model.idempotency_key == "retry-1"
    assert str(model.transfer_id) == "00000000-0000-0000-0000-000000000123"


def test_admin_topup_transfer_id_is_deterministic_for_same_idempotency_key() -> None:
    first = _derive_admin_topup_transfer_id(
        admin_user_id="admin-1",
        target_user_id="user-1",
        amount=50,
        transfer_id=None,
        idempotency_key="retry-1",
    )
    second = _derive_admin_topup_transfer_id(
        admin_user_id="admin-1",
        target_user_id="user-1",
        amount=999,
        transfer_id=None,
        idempotency_key="retry-1",
    )
    assert first == second
