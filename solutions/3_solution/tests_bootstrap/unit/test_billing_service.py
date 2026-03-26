from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import tigerbeetle as tb

from solution3.services.billing import (
    LEDGER_ACCOUNT_CODE_ESCROW,
    LEDGER_ACCOUNT_CODE_REVENUE,
    LEDGER_ACCOUNT_CODE_USER,
    TRANSFER_CODE_TASK,
    TigerBeetleBilling,
    _uuid_to_u128,
)


def test_uuid_to_u128_converts_uuid_text_to_integer() -> None:
    value = "a0000000-0000-0000-0000-000000000001"
    assert _uuid_to_u128(value) == int("a0000000000000000000000000000001", 16)


class TestTigerBeetleBilling:
    def setup_method(self) -> None:
        self.client = MagicMock()
        self.billing = TigerBeetleBilling(
            client=self.client,
            ledger_id=1,
            revenue_account_id=1_000_001,
            escrow_account_id=1_000_002,
            pending_timeout_seconds=600,
        )

    def test_ensure_platform_accounts_creates_revenue_and_escrow(self) -> None:
        self.billing.ensure_platform_accounts()

        self.client.create_accounts.assert_called_once()
        accounts = self.client.create_accounts.call_args.args[0]
        assert len(accounts) == 2
        assert accounts[0].id == 1_000_001
        assert accounts[0].code == LEDGER_ACCOUNT_CODE_REVENUE
        assert accounts[1].id == 1_000_002
        assert accounts[1].code == LEDGER_ACCOUNT_CODE_ESCROW

    def test_ensure_user_account_enables_no_overdraft_flag(self) -> None:
        user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")

        self.billing.ensure_user_account(user_id)

        self.client.create_accounts.assert_called_once()
        account = self.client.create_accounts.call_args.args[0][0]
        assert account.id == _uuid_to_u128(user_id)
        assert account.code == LEDGER_ACCOUNT_CODE_USER
        assert account.flags == tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS

    def test_reserve_credits_builds_pending_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
        transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.reserve_credits(user_id=user_id, transfer_id=transfer_id, amount=25)

        assert result is True
        self.client.create_transfers.assert_called_once()
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.id == _uuid_to_u128(transfer_id)
        assert transfer.debit_account_id == _uuid_to_u128(user_id)
        assert transfer.credit_account_id == 1_000_002
        assert transfer.amount == 25
        assert transfer.code == TRANSFER_CODE_TASK
        assert transfer.flags == tb.TransferFlags.PENDING
        assert transfer.timeout == 600

    def test_reserve_credits_returns_false_when_tb_rejects_transfer(self) -> None:
        error = MagicMock()
        self.client.create_transfers.return_value = [error]

        result = self.billing.reserve_credits(
            user_id="47b47338-5355-4edc-860b-846d71a2a75a",
            transfer_id="019c6db7-0857-7858-af93-f724ae4fe2c2",
            amount=25,
        )

        assert result is False

    def test_post_pending_transfer_builds_post_flag_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        pending_transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.post_pending_transfer(pending_transfer_id=pending_transfer_id)

        assert result is True
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.pending_id == _uuid_to_u128(pending_transfer_id)
        assert transfer.flags == tb.TransferFlags.POST_PENDING_TRANSFER
        assert transfer.code == TRANSFER_CODE_TASK

    def test_void_pending_transfer_builds_void_flag_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        pending_transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.void_pending_transfer(pending_transfer_id=pending_transfer_id)

        assert result is True
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.pending_id == _uuid_to_u128(pending_transfer_id)
        assert transfer.flags == tb.TransferFlags.VOID_PENDING_TRANSFER
        assert transfer.code == TRANSFER_CODE_TASK
