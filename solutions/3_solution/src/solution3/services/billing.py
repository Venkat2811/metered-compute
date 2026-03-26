from __future__ import annotations

from uuid import UUID

import tigerbeetle as tb
from uuid6 import uuid7

LEDGER_ACCOUNT_CODE_REVENUE = 1
LEDGER_ACCOUNT_CODE_ESCROW = 2
LEDGER_ACCOUNT_CODE_USER = 10
TRANSFER_CODE_TASK = 100


def _uuid_to_u128(value: UUID | str) -> int:
    raw_value = value if isinstance(value, str) else str(value)
    return int(raw_value.replace("-", ""), 16)


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
        self._client.create_accounts(accounts)

    def ensure_user_account(self, user_id: UUID | str) -> None:
        account = tb.Account(
            id=_uuid_to_u128(user_id),
            ledger=self._ledger_id,
            code=LEDGER_ACCOUNT_CODE_USER,
            flags=tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
        )
        self._client.create_accounts([account])

    def reserve_credits(self, *, user_id: UUID | str, transfer_id: UUID | str, amount: int) -> bool:
        transfer = tb.Transfer(
            id=_uuid_to_u128(transfer_id),
            debit_account_id=_uuid_to_u128(user_id),
            credit_account_id=self._escrow_account_id,
            amount=amount,
            ledger=self._ledger_id,
            code=TRANSFER_CODE_TASK,
            flags=tb.TransferFlags.PENDING,
            timeout=self._pending_timeout_seconds,
        )
        return not bool(self._client.create_transfers([transfer]))

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
        )
        return not bool(self._client.create_transfers([transfer]))
