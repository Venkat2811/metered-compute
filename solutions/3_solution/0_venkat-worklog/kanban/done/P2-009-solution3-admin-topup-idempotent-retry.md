# P2-009 Solution 3 - Admin Top-up Retry Idempotency

Objective:

Make `/v1/admin/credits` retries safe when the top-up write to TigerBeetle succeeds but the DB outbox write fails, and add a regression assertion for the cancel false-path warning branch.

Acceptance criteria:

 - [x] Add optional idempotency controls to admin top-up requests (`transfer_id` and/or `idempotency_key`).
 - [x] Derive a deterministic `transfer_id` when only `idempotency_key` is supplied so repeated retries do not produce extra credits.
 - [x] Ensure a 503 from outbox write still prevents silent 200 and keeps retry behavior idempotent.
 - [x] Add unit coverage for outbox-retry idempotency (no extra credit applied on retry).
 - [x] Add unit coverage for `_release_pending_transfer` returning `False` after successful DB cancel.
 - [x] Keep existing cancellation and top-up contract paths unchanged.

TDD order:

1. Add `AdminCreditsRequest` schema fields for retry controls.
2. Add regression unit test for retrying admin top-up after outbox failure with same idempotency key.
3. Add regression unit test for cancel route `billing_void=False` branch.
4. Implement deterministic transfer-id resolution in admin route.
5. Re-run unit + targeted integration + proof.
