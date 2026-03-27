from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest
import tigerbeetle as tb

from solution3.services.billing import (
    LEDGER_ACCOUNT_CODE_ESCROW,
    LEDGER_ACCOUNT_CODE_REVENUE,
    LEDGER_ACCOUNT_CODE_USER,
    TRANSFER_CODE_TASK,
    TRANSFER_CODE_TOPUP,
    ReserveCreditsResult,
    TigerBeetleBilling,
    _uuid_to_u128,
    resolve_tigerbeetle_addresses,
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
        self.client.create_accounts.return_value = []
        self.client.lookup_accounts.return_value = [
            tb.Account(
                id=_uuid_to_u128(user_id),
                ledger=1,
                code=LEDGER_ACCOUNT_CODE_USER,
                flags=tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
            )
        ]
        self.client.create_transfers.return_value = []

        self.billing.ensure_user_account(user_id, initial_credits=500)

        self.client.create_accounts.assert_called_once()
        account = self.client.create_accounts.call_args.args[0][0]
        assert account.id == _uuid_to_u128(user_id)
        assert account.code == LEDGER_ACCOUNT_CODE_USER
        assert account.flags == tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.debit_account_id == 1_000_001
        assert transfer.credit_account_id == _uuid_to_u128(user_id)
        assert transfer.amount == 500
        assert transfer.code == TRANSFER_CODE_TOPUP

    def test_reserve_credits_builds_pending_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
        transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.reserve_credits(user_id=user_id, transfer_id=transfer_id, amount=25)

        assert result == ReserveCreditsResult.ACCEPTED
        self.client.create_transfers.assert_called_once()
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.id == _uuid_to_u128(transfer_id)
        assert transfer.debit_account_id == _uuid_to_u128(user_id)
        assert transfer.credit_account_id == 1_000_002
        assert transfer.amount == 25
        assert transfer.code == TRANSFER_CODE_TASK
        assert transfer.flags == tb.TransferFlags.PENDING
        assert transfer.timeout == 600
        assert transfer.user_data_128 == _uuid_to_u128(transfer_id)

    def test_reserve_credits_returns_false_when_tb_rejects_transfer(self) -> None:
        error = MagicMock()
        error.result = tb.CreateTransferResult.EXCEEDS_CREDITS
        self.client.create_transfers.return_value = [error]

        result = self.billing.reserve_credits(
            user_id="47b47338-5355-4edc-860b-846d71a2a75a",
            transfer_id="019c6db7-0857-7858-af93-f724ae4fe2c2",
            amount=25,
        )

        assert result == ReserveCreditsResult.INSUFFICIENT_CREDITS

    def test_reserve_credits_returns_error_for_non_credit_failures(self) -> None:
        error = MagicMock()
        error.result = tb.CreateTransferResult.EXISTS
        self.client.create_transfers.return_value = [error]

        result = self.billing.reserve_credits(
            user_id="47b47338-5355-4edc-860b-846d71a2a75a",
            transfer_id="019c6db7-0857-7858-af93-f724ae4fe2c2",
            amount=25,
        )

        assert result == ReserveCreditsResult.ERROR

    def test_post_pending_transfer_builds_post_flag_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        pending_transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.post_pending_transfer(pending_transfer_id=pending_transfer_id)

        assert result is True
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.pending_id == _uuid_to_u128(pending_transfer_id)
        assert transfer.flags == tb.TransferFlags.POST_PENDING_TRANSFER
        assert transfer.code == TRANSFER_CODE_TASK
        assert transfer.user_data_128 == _uuid_to_u128(pending_transfer_id)

    def test_void_pending_transfer_builds_void_flag_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        pending_transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.void_pending_transfer(pending_transfer_id=pending_transfer_id)

        assert result is True
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.pending_id == _uuid_to_u128(pending_transfer_id)
        assert transfer.flags == tb.TransferFlags.VOID_PENDING_TRANSFER
        assert transfer.code == TRANSFER_CODE_TASK
        assert transfer.user_data_128 == _uuid_to_u128(pending_transfer_id)

    def test_topup_credits_builds_posted_transfer(self) -> None:
        self.client.create_transfers.return_value = []
        user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
        transfer_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")

        result = self.billing.topup_credits(user_id=user_id, transfer_id=transfer_id, amount=25)

        assert result is True
        transfer = self.client.create_transfers.call_args.args[0][0]
        assert transfer.id == _uuid_to_u128(transfer_id)
        assert transfer.debit_account_id == 1_000_001
        assert transfer.credit_account_id == _uuid_to_u128(user_id)
        assert transfer.amount == 25
        assert transfer.code == TRANSFER_CODE_TOPUP

    def test_get_balance_uses_posted_minus_debits(self) -> None:
        user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
        self.client.lookup_accounts.return_value = [
            tb.Account(
                id=_uuid_to_u128(user_id),
                ledger=1,
                code=LEDGER_ACCOUNT_CODE_USER,
                flags=tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
                credits_posted=500,
                debits_posted=30,
                debits_pending=20,
            )
        ]

        assert self.billing.get_balance(user_id) == 450


def test_resolve_tigerbeetle_addresses_leaves_numeric_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("socket.gethostbyname", lambda host: f"unexpected-{host}")
    assert resolve_tigerbeetle_addresses("127.0.0.1:3000") == "127.0.0.1:3000"


def test_resolve_tigerbeetle_addresses_resolves_hostnames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "socket.gethostbyname",
        lambda host: "10.1.2.3" if host == "tigerbeetle" else host,
    )
    assert resolve_tigerbeetle_addresses("tigerbeetle:3000") == "10.1.2.3:3000"
