from __future__ import annotations

import socket
from enum import StrEnum
from uuid import UUID

import tigerbeetle as tb
from uuid6 import uuid7

LEDGER_ACCOUNT_CODE_REVENUE = 1
LEDGER_ACCOUNT_CODE_ESCROW = 2
LEDGER_ACCOUNT_CODE_USER = 10
TRANSFER_CODE_TASK = 100
TRANSFER_CODE_TOPUP = 200


def _uuid_to_u128(value: UUID | str) -> int:
    raw_value = value if isinstance(value, str) else str(value)
    return int(raw_value.replace("-", ""), 16)


def resolve_tigerbeetle_addresses(replica_addresses: str) -> str:
    host, separator, port = replica_addresses.rpartition(":")
    if not separator:
        return replica_addresses
    resolved_host = host
    if host and not host[0].isdigit():
        resolved_host = socket.gethostbyname(host)
    return f"{resolved_host}:{port}"


class ReserveCreditsResult(StrEnum):
    ACCEPTED = "accepted"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    ERROR = "error"


class TigerBeetleBilling:
    def __init__(
        self,
        *,
        client: tb.client.ClientSync,
        ledger_id: int,
        revenue_account_id: int,
        escrow_account_id: int,
        pending_timeout_seconds: int,
    ) -> None:
        self._client = client
        self._ledger_id = ledger_id
        self._revenue_account_id = revenue_account_id
        self._escrow_account_id = escrow_account_id
        self._pending_timeout_seconds = pending_timeout_seconds

    def ensure_platform_accounts(self) -> None:
        accounts = [
            tb.Account(
                id=self._revenue_account_id,
                ledger=self._ledger_id,
                code=LEDGER_ACCOUNT_CODE_REVENUE,
                flags=tb.AccountFlags(0),
            ),
            tb.Account(
                id=self._escrow_account_id,
                ledger=self._ledger_id,
                code=LEDGER_ACCOUNT_CODE_ESCROW,
                flags=tb.AccountFlags(0),
            ),
        ]
        errors = self._client.create_accounts(accounts)
        for error in errors:
            if error.result != tb.CreateAccountResult.EXISTS:
                raise RuntimeError(f"platform account bootstrap failed: {error.result}")

    def ensure_user_account(self, user_id: UUID | str, *, initial_credits: int = 0) -> None:
        account = tb.Account(
            id=_uuid_to_u128(user_id),
            ledger=self._ledger_id,
            code=LEDGER_ACCOUNT_CODE_USER,
            flags=tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
        )
        errors = self._client.create_accounts([account])
        for error in errors:
            if error.result != tb.CreateAccountResult.EXISTS:
                raise RuntimeError(f"user account bootstrap failed: {error.result}")
        if initial_credits > 0 and self.get_balance(user_id) == 0:
            self.topup_credits(
                user_id=user_id,
                transfer_id=user_id,
                amount=initial_credits,
            )

    def reserve_credits(
        self,
        *,
        user_id: UUID | str,
        transfer_id: UUID | str,
        amount: int,
    ) -> ReserveCreditsResult:
        transfer = tb.Transfer(
            id=_uuid_to_u128(transfer_id),
            debit_account_id=_uuid_to_u128(user_id),
            credit_account_id=self._escrow_account_id,
            amount=amount,
            ledger=self._ledger_id,
            code=TRANSFER_CODE_TASK,
            flags=tb.TransferFlags.PENDING,
            timeout=self._pending_timeout_seconds,
            user_data_128=_uuid_to_u128(transfer_id),
        )
        errors = self._client.create_transfers([transfer])
        if not errors:
            return ReserveCreditsResult.ACCEPTED
        first_error = errors[0]
        if getattr(first_error, "result", None) == tb.CreateTransferResult.EXCEEDS_CREDITS:
            return ReserveCreditsResult.INSUFFICIENT_CREDITS
        return ReserveCreditsResult.ERROR

    def post_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        transfer = tb.Transfer(
            id=_uuid_to_u128(uuid7()),
            pending_id=_uuid_to_u128(pending_transfer_id),
            debit_account_id=0,
            credit_account_id=0,
            amount=0,
            ledger=self._ledger_id,
            code=TRANSFER_CODE_TASK,
            flags=tb.TransferFlags.POST_PENDING_TRANSFER,
            user_data_128=_uuid_to_u128(pending_transfer_id),
        )
        return not bool(self._client.create_transfers([transfer]))

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        transfer = tb.Transfer(
            id=_uuid_to_u128(uuid7()),
            pending_id=_uuid_to_u128(pending_transfer_id),
            debit_account_id=0,
            credit_account_id=0,
            amount=0,
            ledger=self._ledger_id,
            code=TRANSFER_CODE_TASK,
            flags=tb.TransferFlags.VOID_PENDING_TRANSFER,
            user_data_128=_uuid_to_u128(pending_transfer_id),
        )
        return not bool(self._client.create_transfers([transfer]))

    def topup_credits(self, *, user_id: UUID | str, transfer_id: UUID | str, amount: int) -> bool:
        transfer = tb.Transfer(
            id=_uuid_to_u128(transfer_id),
            debit_account_id=self._revenue_account_id,
            credit_account_id=_uuid_to_u128(user_id),
            amount=amount,
            ledger=self._ledger_id,
            code=TRANSFER_CODE_TOPUP,
            flags=tb.TransferFlags(0),
        )
        return not bool(self._client.create_transfers([transfer]))

    def get_balance(self, user_id: UUID | str) -> int:
        accounts = self._client.lookup_accounts([_uuid_to_u128(user_id)])
        if not accounts:
            return 0
        account = accounts[0]
        return account.credits_posted - account.debits_posted - account.debits_pending
