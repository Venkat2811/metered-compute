"""TigerBeetle billing client — pending/post/void double-entry transfers.

Replaces: credit_reservations table, credit_transactions table, watchdog/reaper,
all credit arithmetic SQL. TB is the source of truth for balances.
"""

from __future__ import annotations

import structlog
import tigerbeetle as tb
import uuid6

from solution5 import metrics

log = structlog.get_logger()

LEDGER = 1  # single ledger for all accounts
CODE_TASK = 100  # transfer code for task billing
CODE_TOPUP = 200  # transfer code for admin topup


def _user_id_to_u128(user_uuid_hex: str) -> int:
    """Convert UUID hex string (no dashes) to u128 for TB account ID."""
    return int(user_uuid_hex.replace("-", ""), 16)


class Billing:
    def __init__(self, client: tb.client.ClientSync, revenue_id: int, escrow_id: int, timeout_secs: int = 300) -> None:
        self._client = client
        self._revenue_id = revenue_id
        self._escrow_id = escrow_id
        self._timeout_secs = timeout_secs

    def ensure_platform_accounts(self) -> None:
        """Create revenue + escrow accounts (idempotent)."""
        accounts = [
            tb.Account(
                id=self._revenue_id,
                ledger=LEDGER,
                code=1,
                flags=tb.AccountFlags(0),
            ),
            tb.Account(
                id=self._escrow_id,
                ledger=LEDGER,
                code=2,
                flags=tb.AccountFlags(0),
            ),
        ]
        errors = self._client.create_accounts(accounts)
        for e in errors:
            if e.result != tb.CreateAccountResult.EXISTS:
                log.warning("platform_account_create_error", id=accounts[e.index].id, error=e.result)

    def ensure_user_account(self, user_id: str) -> None:
        """Create a user account in TB (idempotent). Debits must not exceed credits."""
        account = tb.Account(
            id=_user_id_to_u128(user_id),
            ledger=LEDGER,
            code=10,
            flags=tb.AccountFlags.DEBITS_MUST_NOT_EXCEED_CREDITS,
        )
        errors = self._client.create_accounts([account])
        for e in errors:
            if e.result != tb.CreateAccountResult.EXISTS:
                log.warning("user_account_create_error", user_id=user_id, error=e.result)

    def reserve_credits(self, user_id: str, transfer_id: int, amount: int) -> bool:
        """Create a pending transfer (user → escrow). Returns True if successful."""
        transfer = tb.Transfer(
            id=transfer_id,
            debit_account_id=_user_id_to_u128(user_id),
            credit_account_id=self._escrow_id,
            amount=amount,
            ledger=LEDGER,
            code=CODE_TASK,
            flags=tb.TransferFlags.PENDING,
            timeout=self._timeout_secs,
        )
        errors = self._client.create_transfers([transfer])
        if errors:
            err = errors[0].result
            log.warning("reserve_failed", user_id=user_id, amount=amount, error=err)
            return False
        metrics.CREDIT_RESERVED.inc()
        log.info("credits_reserved", user_id=user_id, amount=amount, transfer_id=transfer_id)
        return True

    def capture_credits(self, transfer_id: int) -> bool:
        """Post a pending transfer. Credits are captured (user → escrow finalized)."""
        transfer = tb.Transfer(
            id=int(uuid6.uuid7().hex, 16),
            pending_id=transfer_id,
            debit_account_id=0,  # reuse original
            credit_account_id=0,  # reuse original
            amount=0,  # use original amount
            ledger=LEDGER,
            code=CODE_TASK,
            flags=tb.TransferFlags.POST_PENDING_TRANSFER,
        )
        errors = self._client.create_transfers([transfer])
        if errors:
            log.warning("capture_failed", transfer_id=transfer_id, error=errors[0].result)
            return False
        metrics.CREDIT_CAPTURED.inc()
        log.info("credits_captured", transfer_id=transfer_id)
        return True

    def release_credits(self, transfer_id: int) -> bool:
        """Void a pending transfer. Credits return to user."""
        transfer = tb.Transfer(
            id=int(uuid6.uuid7().hex, 16),
            pending_id=transfer_id,
            debit_account_id=0,
            credit_account_id=0,
            amount=0,
            ledger=LEDGER,
            code=CODE_TASK,
            flags=tb.TransferFlags.VOID_PENDING_TRANSFER,
        )
        errors = self._client.create_transfers([transfer])
        if errors:
            log.warning("release_failed", transfer_id=transfer_id, error=errors[0].result)
            return False
        metrics.CREDIT_RELEASED.inc()
        log.info("credits_released", transfer_id=transfer_id)
        return True

    def topup_credits(self, user_id: str, transfer_id: int, amount: int) -> bool:
        """Direct transfer: revenue → user (admin topup)."""
        transfer = tb.Transfer(
            id=transfer_id,
            debit_account_id=self._revenue_id,
            credit_account_id=_user_id_to_u128(user_id),
            amount=amount,
            ledger=LEDGER,
            code=CODE_TOPUP,
            flags=tb.TransferFlags(0),
        )
        errors = self._client.create_transfers([transfer])
        if errors:
            error = errors[0].result
            if error == tb.CreateTransferResult.EXISTS:
                log.info("credits_topup_replayed", user_id=user_id, amount=amount, transfer_id=transfer_id)
                return True
            log.warning("topup_failed", user_id=user_id, amount=amount, error=error)
            return False
        metrics.CREDIT_TOPUP.inc()
        log.info("credits_topped_up", user_id=user_id, amount=amount)
        return True

    def get_balance(self, user_id: str) -> int:
        """Get available credits for a user (credits_posted - debits_posted - debits_pending)."""
        accounts = self._client.lookup_accounts([_user_id_to_u128(user_id)])
        if not accounts:
            return 0
        a = accounts[0]
        return a.credits_posted - a.debits_posted - a.debits_pending

    def is_ready(self) -> bool:
        """Best-effort health probe: can query known platform accounts."""
        try:
            self._client.lookup_accounts([self._revenue_id, self._escrow_id])
            return True
        except Exception:
            return False
