"""Unit tests for TigerBeetle billing wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

import tigerbeetle as tb

from solution4.billing import Billing, _user_id_to_u128


class TestUserIdConversion:
    def test_uuid_to_u128(self) -> None:
        uid = "a0000000-0000-0000-0000-000000000001"
        result = _user_id_to_u128(uid)
        assert result == int("a0000000000000000000000000000001", 16)

    def test_different_uuids_give_different_ids(self) -> None:
        a = _user_id_to_u128("a0000000-0000-0000-0000-000000000001")
        b = _user_id_to_u128("b0000000-0000-0000-0000-000000000002")
        assert a != b


class TestBilling:
    def setup_method(self) -> None:
        self.mock_client = MagicMock()
        self.billing = Billing(
            client=self.mock_client,
            revenue_id=1_000_001,
            escrow_id=1_000_002,
            timeout_secs=300,
        )

    def test_reserve_credits_success(self) -> None:
        self.mock_client.create_transfers.return_value = []
        result = self.billing.reserve_credits("a0000000-0000-0000-0000-000000000001", 12345, 10)
        assert result is True
        self.mock_client.create_transfers.assert_called_once()

    def test_reserve_credits_failure(self) -> None:
        error = MagicMock()
        error.result = "EXCEEDS_CREDITS"
        self.mock_client.create_transfers.return_value = [error]
        result = self.billing.reserve_credits("a0000000-0000-0000-0000-000000000001", 12345, 10)
        assert result is False

    def test_capture_credits_success(self) -> None:
        self.mock_client.create_transfers.return_value = []
        result = self.billing.capture_credits(12345)
        assert result is True

    def test_release_credits_success(self) -> None:
        self.mock_client.create_transfers.return_value = []
        result = self.billing.release_credits(12345)
        assert result is True

    def test_topup_credits_success(self) -> None:
        self.mock_client.create_transfers.return_value = []
        result = self.billing.topup_credits("a0000000-0000-0000-0000-000000000001", 99999, 500)
        assert result is True

    def test_topup_credits_treats_exact_duplicate_transfer_as_success(self) -> None:
        error = MagicMock()
        error.result = tb.CreateTransferResult.EXISTS
        self.mock_client.create_transfers.return_value = [error]

        result = self.billing.topup_credits("a0000000-0000-0000-0000-000000000001", 99999, 500)

        assert result is True

    def test_get_balance(self) -> None:
        account = MagicMock()
        account.credits_posted = 1000
        account.debits_posted = 200
        account.debits_pending = 100
        self.mock_client.lookup_accounts.return_value = [account]
        balance = self.billing.get_balance("a0000000-0000-0000-0000-000000000001")
        assert balance == 700  # 1000 - 200 - 100

    def test_get_balance_no_account(self) -> None:
        self.mock_client.lookup_accounts.return_value = []
        balance = self.billing.get_balance("00000000-0000-0000-0000-000000000099")
        assert balance == 0

    def test_ensure_platform_accounts_idempotent(self) -> None:
        self.mock_client.create_accounts.return_value = []
        self.billing.ensure_platform_accounts()
        self.mock_client.create_accounts.assert_called_once()
        args = self.mock_client.create_accounts.call_args[0][0]
        assert len(args) == 2
