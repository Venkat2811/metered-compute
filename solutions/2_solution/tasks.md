# Solution 2: TDD Task List

> **Base**: Fork from `1_solution` (which forked from `0_solution`)
> **RFC**: `.0_agentic_engineering/0_rfcs/RFC-0002-2-solution-service-grade-platform/`
> **Pattern**: Copy Sol 1, remove unnecessary (Lua admission, Redis Streams, pending markers), add new (RabbitMQ, outbox/inbox, reservation billing, CQRS, projector, watchdog)

## Key architectural shifts from Sol 1

| Concern               | Sol 1                                    | Sol 2                                         |
| --------------------- | ---------------------------------------- | --------------------------------------------- |
| Credit model          | Redis Lua deduct-then-execute            | PG reservation (reserve/capture/release)      |
| Queue                 | Redis Streams (XREADGROUP)               | RabbitMQ (topic exchange, tiered queues)       |
| Submit atomicity      | Lua mega-script (Redis-side)             | Single PG transaction (outbox pattern)         |
| Dual-write risk       | Yes (mitigated by retry + reaper)        | Solved (outbox guarantees at-least-once)       |
| Poll path             | Redis-first (task hash + result hash)    | Redis cache + query.task_query_view + cmd join |
| Recovery              | Reaper (orphan markers, stuck tasks)     | Watchdog (reservation timeout, result expiry)  |
| Auth                  | JWT/OAuth via Hydra (keep)               | Same (keep)                                    |
| CQRS                  | No                                       | cmd/query schemas, projector                   |
| Idempotency           | Redis Lua-checked (TTL)                  | PG unique constraint (durable, no TTL)         |
| Redis role            | Billing authority + cache + queue        | Query cache only                               |
| Request modes         | async only                               | async, sync, batch                             |

---

## Notation

- **[T]** = Write test first (TDD red phase)
- **[I]** = Implement production code (TDD green phase)
- **[V]** = Verify (run tests, confirm green)
- **[PROVE]** = End-to-end checkpoint (run full stack, confirm milestone works)
- **[R]** = Remove/refactor from Sol 1 fork

Files reference Sol 1 paths. Adjust package name `solution2` → `solution2` throughout.

---

## Milestone 0: Fork & Scaffold

> Goal: Clean fork of Sol 1 with package rename, RabbitMQ in compose, cmd/query schema stubs. No tests yet — pure scaffold.

### 0.1 Fork Sol 1

- [ ] Copy `1_solution/` → `2_solution/`
- [ ] Rename package: `solution2` → `solution2` everywhere (imports, pyproject.toml, Dockerfiles, compose.yaml, .env.dev.defaults)
- [ ] Verify: `cd 2_solution && python -c "import solution2"` succeeds
- [ ] Update pyproject.toml: name, description, add `pika` (RabbitMQ client) or `aio-pika` dependency
- [ ] Remove `pika`-incompatible deps if any; keep `asyncpg`, `redis`, `httpx`, `structlog`, `prometheus_client`, `pydantic`, `pydantic-settings`, `uuid6`, `uvicorn`, `fastapi`, `PyJWT`, `cryptography`

### 0.2 Add RabbitMQ to compose.yaml

- [ ] Add `rabbitmq` service: `rabbitmq:4.1-management-alpine`
  - Ports: 5672 (AMQP), 15672 (management UI)
  - Health check: `rabbitmq-diagnostics -q ping`
  - Volume: `rabbitmq_data`
  - Environment: `RABBITMQ_DEFAULT_USER=guest`, `RABBITMQ_DEFAULT_PASS=guest`
- [ ] Add `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/` to `.env.dev.defaults`
- [ ] Add `rabbitmq_url` to AppSettings

### 0.3 Remove Sol 1-specific modules

- [R] Delete `src/solution2/utils/lua_scripts.py` (ADMISSION_LUA, DECR_ACTIVE_CLAMP_LUA)
- [R] Delete `src/solution2/workers/stream_worker.py` (Redis Streams consumer)
- [R] Delete `src/solution2/workers/reaper.py` (replaced by watchdog)
- [R] Remove `admission_script_sha` and `decrement_script_sha` from `core/runtime.py`
- [R] Remove Lua-related billing functions from `services/billing.py` (keep shell for reservation billing)
- [R] Remove pending marker functions from `services/auth.py` (pending_marker_key, etc.)
- [R] Remove stream-related settings from `core/settings.py` (stream_maxlen, consumer_group, etc.)
- [R] Remove reaper-related settings (orphan_timeout, stuck_timeout, pending_scan_count, etc.)
- [R] Remove stream worker Docker service from compose.yaml
- [R] Remove reaper Docker service from compose.yaml
- [R] Remove `docker/worker/` stream worker Dockerfile (will create new RabbitMQ worker)
- [R] Remove `docker/reaper/` Dockerfile (will create watchdog)

### 0.4 Add new service stubs to compose.yaml

- [ ] `worker` service (new Dockerfile, depends on rabbitmq + postgres + redis)
- [ ] `outbox-relay` service (new Dockerfile, depends on postgres + rabbitmq)
- [ ] `projector` service (new Dockerfile, depends on rabbitmq + postgres)
- [ ] `watchdog` service (new Dockerfile, depends on postgres + redis)
- [ ] `webhook-worker` service (new Dockerfile, depends on rabbitmq)
- [ ] Each service: stub entrypoint that logs "starting <service>" and exits

### 0.5 Scaffold cmd/query schema separation

- [ ] Create `db/migrations/0010_create_cmd_schema.sql`: `CREATE SCHEMA IF NOT EXISTS cmd;`
- [ ] Create `db/migrations/0011_create_query_schema.sql`: `CREATE SCHEMA IF NOT EXISTS query;`
- [ ] Verify: `docker compose up postgres -d && python -m solution2.db.migrate` applies schemas

### 0.6 Query timeout hardening (cross-cutting)

> All PG queries are PK lookups or indexed single-row ops (expected <3ms). Set 10x headroom timeouts at every layer. All values configurable from `.env.dev.defaults`.

New env vars in `.env.dev.defaults`:
```env
DB_STATEMENT_TIMEOUT_MS=50
DB_STATEMENT_TIMEOUT_BATCH_MS=2000
DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=500
DB_POOL_COMMAND_TIMEOUT_SECONDS=0.1
REDIS_SOCKET_TIMEOUT_SECONDS=0.05
REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS=0.05
```

- [ ] Add `db_statement_timeout_ms: int = 50` to AppSettings (PG server-side kill — the real defense)
- [ ] Add `db_statement_timeout_batch_ms: int = 2000` to AppSettings (watchdog batch ops)
- [ ] Add `db_idle_in_transaction_timeout_ms: int = 500` to AppSettings
- [ ] Add `redis_socket_timeout_seconds: float = 0.05` to AppSettings
- [ ] Add `redis_socket_connect_timeout_seconds: float = 0.05` to AppSettings
- [ ] Pass `server_settings={'statement_timeout': str(ms), 'idle_in_transaction_session_timeout': str(ms)}` to every `asyncpg.create_pool()` call — API pool uses `db_statement_timeout_ms`, watchdog pool uses `db_statement_timeout_batch_ms`
- [ ] Set `db_pool_command_timeout_seconds` default to 0.1 (100ms, 2x server timeout)
- [ ] Add `socket_timeout` and `socket_connect_timeout` to all production `Redis.from_url()` calls
- [ ] Add jitter to `retry_async()` — `delay * uniform(0.5, 1.5)` to break thundering herds
- [ ] Add all new env vars to `.env.dev.defaults` and test fixtures
- [ ] Settings validation: `db_statement_timeout_ms > 0`, `redis_socket_timeout_seconds > 0`

Timeout budget chain:

| Layer                  | API/Worker hot-path | Watchdog batch |
| ---------------------- | ------------------- | -------------- |
| `statement_timeout`    | 50ms                | 2000ms         |
| `command_timeout`      | 100ms               | 3000ms         |
| `idle_in_transaction`  | 500ms               | 500ms          |
| Redis `socket_timeout` | 50ms                | 50ms           |

### 0.7 Verify scaffold builds

- [ ] `docker compose build` succeeds (all images build)
- [ ] `docker compose up -d postgres redis rabbitmq` — all 3 healthy
- [ ] `docker compose down -v` clean

---

## Milestone 1: Database Schema (CQRS)

> Goal: All cmd/query tables exist. Migrations run clean. Seed data present.

### 1.1 [T] Migration tests

- [ ] `tests/unit/test_migrations.py`: Test migration file naming, ordering, SQL template rendering
- [ ] Port from Sol 1, adjust for new migration files (0010+)

### 1.2 [I] Command schema migrations

- [ ] `0012_cmd_task_commands.sql`:
  ```sql
  CREATE TABLE cmd.task_commands (
    task_id       UUID PRIMARY KEY,
    user_id       UUID NOT NULL,
    tier          VARCHAR(32) NOT NULL,
    mode          VARCHAR(16) NOT NULL DEFAULT 'async',
    model_class   VARCHAR(16) NOT NULL DEFAULT 'small',
    status        VARCHAR(24) NOT NULL DEFAULT 'PENDING',
    x             INT NOT NULL,
    y             INT NOT NULL,
    cost          INT NOT NULL,
    callback_url  TEXT,
    idempotency_key VARCHAR(128),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE UNIQUE INDEX ux_task_cmd_user_idem
    ON cmd.task_commands (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
  CREATE INDEX idx_task_cmd_status_created
    ON cmd.task_commands (status, created_at);
  CREATE INDEX idx_task_cmd_user_created
    ON cmd.task_commands (user_id, created_at DESC);
  ```

- [ ] `0013_cmd_credit_reservations.sql`:
  ```sql
  CREATE TABLE cmd.credit_reservations (
    reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id        UUID UNIQUE NOT NULL,
    user_id        UUID NOT NULL,
    amount         INT NOT NULL,
    state          VARCHAR(16) NOT NULL DEFAULT 'RESERVED'
                   CHECK (state IN ('RESERVED','CAPTURED','RELEASED')),
    expires_at     TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX idx_reservations_state_expires
    ON cmd.credit_reservations (state, expires_at);
  ```

- [ ] `0014_cmd_outbox_events.sql`:
  ```sql
  CREATE TABLE cmd.outbox_events (
    event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_id UUID NOT NULL,
    event_type   VARCHAR(64) NOT NULL,
    routing_key  VARCHAR(128) NOT NULL,
    payload      JSONB NOT NULL,
    published_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX idx_outbox_unpublished
    ON cmd.outbox_events (created_at)
    WHERE published_at IS NULL;
  ```

- [ ] `0015_cmd_inbox_events.sql`:
  ```sql
  CREATE TABLE cmd.inbox_events (
    event_id      UUID PRIMARY KEY,
    consumer_name VARCHAR(64) NOT NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  ```

- [ ] Keep existing `users`, `api_keys`, `credit_transactions`, `token_revocations` from Sol 1 migrations (0001-0009). Move `tasks` table usage to `cmd.task_commands` (new table, old `tasks` table can be dropped or ignored).

### 1.3 [I] Query schema migration

- [ ] `0016_query_task_view.sql`:
  ```sql
  CREATE TABLE query.task_query_view (
    task_id     UUID PRIMARY KEY,
    user_id     UUID NOT NULL,
    tier        VARCHAR(32) NOT NULL,
    mode        VARCHAR(16) NOT NULL,
    model_class VARCHAR(16) NOT NULL,
    status      VARCHAR(24) NOT NULL,
    result      JSONB,
    error       TEXT,
    queue_name  VARCHAR(32),
    runtime_ms  INT,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
  );
  CREATE INDEX idx_task_query_user_updated
    ON query.task_query_view (user_id, updated_at DESC);
  ```

### 1.4 [I] Seed data migration

- [ ] `0017_seed_users_sol2.sql`: Same seed users as Sol 1 (admin, alice, bob) with api_keys table entries. ON CONFLICT UPDATE for idempotent re-runs.

### 1.5 [V] Verify migrations

- [ ] `docker compose up -d postgres && python -m solution2.db.migrate`
- [ ] Verify all tables exist in cmd and query schemas via `psql`
- [ ] Verify indexes created
- [ ] Verify seed data present

---

## Milestone 2: Domain Models & Constants

> Goal: All domain types, enums, schemas, and routing tables defined and unit-tested.

### 2.1 [T] Reservation state machine tests

- [ ] `tests/unit/test_reservation_state.py`:
  - Test RESERVED → CAPTURED is valid
  - Test RESERVED → RELEASED is valid
  - Test CAPTURED → anything is invalid
  - Test RELEASED → anything is invalid
  - Test RESERVED → RESERVED is invalid

### 2.2 [T] SLA routing table tests

- [ ] `tests/unit/test_sla_routing.py`:
  - Test free + sync → rejected (400)
  - Test free + async → queue.batch
  - Test free + batch → queue.batch
  - Test pro + sync + small → queue.fast
  - Test pro + sync + medium → rejected (400, sync only for small)
  - Test pro + async → queue.fast
  - Test pro + batch → queue.batch
  - Test enterprise + sync → queue.realtime
  - Test enterprise + async → queue.realtime
  - Test enterprise + batch → queue.fast

### 2.3 [T] Cost calculation tests

- [ ] `tests/unit/test_cost_calculation.py`:
  - Test small model: base_cost * 1
  - Test medium model: base_cost * 2
  - Test large model: base_cost * 5
  - Test default model_class is small

### 2.4 [T] Task state machine tests

- [ ] `tests/unit/test_task_state.py`:
  - Test PENDING → RUNNING valid
  - Test RUNNING → COMPLETED valid
  - Test RUNNING → FAILED valid
  - Test PENDING → CANCELLED valid
  - Test RUNNING → CANCELLED valid (cancel while running)
  - Test any terminal → anything invalid
  - Test PENDING → TIMEOUT valid (watchdog)
  - Test terminal → EXPIRED valid (watchdog result expiry)

### 2.5 [I] constants.py

- [ ] `TaskStatus` enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, EXPIRED
- [ ] `ReservationState` enum: RESERVED, CAPTURED, RELEASED
- [ ] `UserRole` enum: admin, user
- [ ] `Tier` enum: free, pro, enterprise
- [ ] `ModelClass` enum: small, medium, large
- [ ] `RequestMode` enum: async, sync, batch
- [ ] `MODEL_COST_MULTIPLIER`: {small: 1, medium: 2, large: 5}
- [ ] `TIER_CONCURRENCY_MULTIPLIER`: {free: 1, pro: 2, enterprise: 4}
- [ ] `MODEL_RUNTIME_SECONDS`: {small: 2, medium: 4, large: 7}
- [ ] `task_cost_for_model(base_cost, model_class)` → int
- [ ] `max_concurrent_for_tier(base_max, tier)` → int
- [ ] `resolve_queue(tier, mode, model_class)` → queue name or raises ValueError
- [ ] `compute_routing_key(mode, tier, model_class)` → str (e.g., `tasks.async.pro.medium`)
- [ ] Reservation state transition validator

### 2.6 [I] models/domain.py

- [ ] `AuthUser` (frozen dataclass): api_key, user_id, name, role, credits, tier, scopes (carry from Sol 1)
- [ ] `TaskCommand` (frozen dataclass): task_id, user_id, tier, mode, model_class, status, x, y, cost, callback_url, idempotency_key, created_at, updated_at
- [ ] `CreditReservation` (frozen dataclass): reservation_id, task_id, user_id, amount, state, expires_at, created_at, updated_at
- [ ] `OutboxEvent` (frozen dataclass): event_id, aggregate_id, event_type, routing_key, payload, published_at, created_at
- [ ] `TaskQueryView` (frozen dataclass): task_id, user_id, tier, mode, model_class, status, result, error, queue_name, runtime_ms, created_at, updated_at
- [ ] `WebhookTerminalEvent` (frozen dataclass): carry from Sol 1

### 2.7 [I] models/schemas.py (Pydantic request/response)

- [ ] `SubmitTaskRequest`: x, y, model_class (default: small), mode (default: async), callback_url (optional)
- [ ] `SubmitTaskResponse`: task_id, status, queue, expires_at
- [ ] `BatchSubmitRequest`: tasks (list of {x, y, model_class}), max 100
- [ ] `BatchSubmitResponse`: batch_id, task_ids, total_cost
- [ ] `PollTaskResponse`: task_id, status, result, error, expires_at (no queue_position — CQRS projections are eventually consistent)
- [ ] `CancelTaskResponse`: task_id, status, credits_refunded
- [ ] `AdminCreditsRequest`: user_id, delta, reason
- [ ] `AdminCreditsResponse`: user_id, new_balance
- [ ] `OAuthTokenRequest`, `OAuthTokenResponse`, `RevokeTokenResponse` (carry from Sol 1)
- [ ] `ErrorPayload`, `ErrorEnvelope` (carry from Sol 1)

### 2.8 [V] Run unit tests

- [ ] `pytest tests/unit/test_reservation_state.py tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_task_state.py -v`
- [ ] All green

---

## Milestone 3: Auth (JWT/OAuth — carry from Sol 1)

> Goal: JWT auth works identically to Sol 1. Hydra issues tokens, API verifies locally.

### 3.1 [T] Auth unit tests

- [ ] `tests/unit/test_auth_service.py`: Port from Sol 1
  - Cache-aside resolution (Redis hit, Redis miss + DB hit, both miss)
  - API key hash validation
  - JTI revocation check (Redis day-bucket, PG fallback)
  - JWKS cache and refresh
- [ ] `tests/unit/test_auth_utils.py`: Port from Sol 1
  - Bearer token parsing
  - Key builder functions (auth:{api_key}, revoked:{uid}:{day})
  - JWT claims extraction

### 3.2 [I] services/auth.py

- [ ] Port from Sol 1 with minimal changes:
  - `resolve_user_from_api_key()` — cache-aside, PG fallback
  - `is_active_api_key_hash()` — hash check against api_keys table
  - `revoke_jti()` — Redis + PG dual-write
  - `revoked_tokens_lookup_keys()` — today + yesterday buckets
  - Key builders: `_auth_cache_key()`, `revoked_tokens_day_key()`
- [ ] Remove: `_credits_key()`, `active_tasks_key()`, `idempotency_key()`, `pending_marker_key()`, `result_cache_key()`, `task_state_key()` — these move to services/cache.py or are removed entirely
- [ ] Add: `task_cache_key(task_id)` → `task:{task_id}` (for Redis query cache)

### 3.3 [I] JWT middleware in app.py

- [ ] Port JWT verification from Sol 1 (local crypto verify via JWKS)
- [ ] Scope checking: `task:submit`, `task:poll`, `task:cancel`, `admin:credits`
- [ ] Revocation check: Redis day-bucket SISMEMBER, PG fallback

### 3.4 [I] Hydra compose setup

- [ ] Port hydra, hydra-migrate, hydra-client-init services from Sol 1 compose.yaml
- [ ] Port `docker/hydra/bootstrap-clients.sh`
- [ ] Port OAuth token endpoint (`POST /v1/oauth/token`)
- [ ] Port revoke endpoint (`POST /v1/auth/revoke`)

### 3.5 [V] Verify auth

- [ ] `pytest tests/unit/test_auth_service.py tests/unit/test_auth_utils.py -v` — all green
- [ ] Manual: `docker compose up -d` → `curl POST /v1/oauth/token` returns JWT

---

## Milestone 4: Repository Layer (Database Operations)

> Goal: All PG query functions for cmd + query schemas, tested with fakes.

### 4.1 [T] Repository unit tests

- [ ] `tests/unit/test_repository_cmd.py`:
  - `create_task_command()`: INSERT into cmd.task_commands
  - `create_reservation()`: INSERT into cmd.credit_reservations (state=RESERVED)
  - `capture_reservation()`: UPDATE state=CAPTURED WHERE state=RESERVED (guarded)
  - `release_reservation()`: UPDATE state=RELEASED WHERE state=RESERVED + refund credits (guarded)
  - `create_outbox_event()`: INSERT into cmd.outbox_events
  - `check_inbox_event()`: SELECT from cmd.inbox_events
  - `record_inbox_event()`: INSERT into cmd.inbox_events (duplicate → PK violation)
  - `get_task_command()`: SELECT from cmd.task_commands
  - `update_task_status()`: Guarded UPDATE with WHERE status=expected
  - `reserve_credits()`: UPDATE users SET credits=credits-cost WHERE credits >= cost
  - `refund_credits()`: UPDATE users SET credits=credits+amount
  - `count_active_reservations()`: SELECT COUNT(*) FROM cmd.credit_reservations WHERE user_id AND state=RESERVED

- [ ] `tests/unit/test_repository_query.py`:
  - `upsert_task_query_view()`: INSERT ON CONFLICT DO UPDATE
  - `get_task_query_view()`: SELECT from query.task_query_view
  - `bulk_expire_results()`: UPDATE status=EXPIRED WHERE terminal AND old

- [ ] `tests/unit/test_repository_outbox.py`:
  - `fetch_unpublished_events()`: SELECT WHERE published_at IS NULL LIMIT N ORDER BY created_at
  - `mark_event_published()`: UPDATE published_at=now()
  - `purge_old_published_events()`: DELETE WHERE published_at < threshold

### 4.2 [I] db/repository.py

- [ ] **User/Auth operations** (port from Sol 1):
  - `fetch_user_by_api_key()`
  - `is_active_api_key_hash()`
  - `admin_update_user_credits()` — CTE: UPDATE users + INSERT credit_transactions
  - `insert_credit_transaction()`

- [ ] **Command operations** (new):
  - `create_task_command(conn, task_id, user_id, tier, mode, model_class, x, y, cost, callback_url, idempotency_key)` → TaskCommand
  - `get_task_command(conn, task_id)` → TaskCommand | None
  - `update_task_command_status(conn, task_id, new_status, expected_status, **kwargs)` → bool (guarded)
  - `reserve_credits(conn, user_id, amount)` → bool (UPDATE ... WHERE credits >= amount)
  - `refund_credits(conn, user_id, amount)` → int (new balance)
  - `count_active_reservations(conn, user_id)` → int
  - `create_reservation(conn, task_id, user_id, amount, expires_at)` → CreditReservation
  - `capture_reservation(conn, task_id)` → bool (guarded: RESERVED → CAPTURED)
  - `release_reservation(conn, task_id)` → tuple[bool, int] (guarded: RESERVED → RELEASED, returns amount)
  - `get_reservation_for_cancel(conn, task_id, user_id)` → CreditReservation | None (SELECT FOR UPDATE)
  - `find_expired_reservations(conn, limit)` → list[CreditReservation] (FOR UPDATE SKIP LOCKED)

- [ ] **Outbox operations** (new):
  - `create_outbox_event(conn, aggregate_id, event_type, routing_key, payload)` → OutboxEvent
  - `fetch_unpublished_events(conn, limit=100)` → list[OutboxEvent]
  - `mark_event_published(conn, event_id)` → None
  - `purge_old_published_events(conn, older_than)` → int

- [ ] **Inbox operations** (new):
  - `check_inbox_event(conn, event_id)` → bool
  - `record_inbox_event(conn, event_id, consumer_name)` → None (raises on duplicate)

- [ ] **Query operations** (new):
  - `upsert_task_query_view(conn, task_id, user_id, tier, mode, model_class, status, result, error, queue_name, runtime_ms, created_at, updated_at)` → None
  - `get_task_query_view(conn, task_id)` → TaskQueryView | None
  - `bulk_expire_results(conn, older_than)` → int (UPDATE cmd + query)

- [ ] **Revocation operations** (port from Sol 1):
  - `insert_revoked_jti()`
  - `is_jti_revoked()`
  - `load_active_revoked_jtis()`

- [ ] **Webhook operations** (port from Sol 1):
  - `upsert_webhook_subscription()`
  - `get_webhook_subscription()`
  - `insert_webhook_dead_letter()`

### 4.3 [V] Run repository tests

- [ ] `pytest tests/unit/test_repository_cmd.py tests/unit/test_repository_query.py tests/unit/test_repository_outbox.py -v`
- [ ] All green

---

## Milestone 5: Submit Path (Reservation Billing + Outbox)

> Goal: `POST /v1/task` creates a reservation, writes outbox event, returns 201. No worker yet.

### 5.1 [T] Submit unit tests

- [ ] `tests/unit/test_submit_reservation.py`:
  - Test successful reservation: credits deducted, task created, outbox event written, all in 1 PG transaction
  - Test insufficient credits: 402, no reservation created, no credits deducted
  - Test concurrency limit: 429 when active reservations >= max_concurrent_for_tier
  - Test idempotency: same user_id + idempotency_key returns existing task_id (PG unique constraint)
  - Test idempotency conflict: same key but different params returns 409
  - Test model_class cost calculation: medium costs 2x, large costs 5x
  - Test mode routing: async/pro → queue.fast routing key in outbox event
  - Test free + sync rejected: 400
  - Test callback_url stored in task_commands
  - Test Redis write-through: task:{task_id} hash set after PG commit

### 5.2 [T] Submit integration tests (compose required)

- [ ] `tests/integration/test_submit_flow.py`:
  - Authenticate via JWT, submit task, verify 201 response
  - Verify cmd.task_commands row created
  - Verify cmd.credit_reservations row created (state=RESERVED)
  - Verify cmd.outbox_events row created (published_at IS NULL)
  - Verify users.credits decremented
  - Verify Redis task:{task_id} hash present

### 5.3 [I] services/billing.py (reservation model)

- [ ] `submit_task(conn, redis, user_id, tier, mode, model_class, x, y, cost, callback_url, idempotency_key, reservation_ttl_seconds)`:
  - BEGIN transaction
  - Idempotency check: SELECT from cmd.task_commands WHERE user_id AND idempotency_key
  - Concurrency check: count_active_reservations(conn, user_id) vs max_concurrent_for_tier
  - Reserve credits: reserve_credits(conn, user_id, cost) — returns False if insufficient
  - Create reservation: create_reservation(conn, task_id, user_id, cost, now + ttl)
  - Create task: create_task_command(conn, ...)
  - Create outbox event: create_outbox_event(conn, task_id, "task.requested", routing_key, payload)
  - COMMIT
  - Post-commit: Redis HSET task:{task_id} {status: PENDING, user_id, ...} EXPIRE 86400
  - Return SubmitTaskResponse

### 5.4 [I] api/task_write_routes.py (submit endpoint)

- [ ] `POST /v1/task`:
  - Parse SubmitTaskRequest
  - Require scope `task:submit`
  - Validate mode/tier/model_class combination via `resolve_queue()`
  - Generate task_id (uuid7)
  - Compute cost via `task_cost_for_model()`
  - Call `submit_task()`
  - Return 201 with SubmitTaskResponse
  - Error mapping: insufficient → 402, concurrency → 429, idempotent → 200 with existing, conflict → 409

### 5.5 [I] api/paths.py

- [ ] Update path constants for Sol 2 endpoints
- [ ] Add batch path: `V1_TASK_BATCH_PATH = "/v1/task/batch"`

### 5.6 [V] Run submit tests

- [ ] `pytest tests/unit/test_submit_reservation.py -v` — all green
- [ ] `docker compose up -d && pytest tests/integration/test_submit_flow.py -v` — all green

---

## **[PROVE 1]: Submit path works end-to-end**

```bash
docker compose up -d
# Get JWT
TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "586f0ef6-e655-4413-ab08-a481db150389"}' | jq -r .access_token)

# Submit task
curl -s -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 5, "y": 3, "model_class": "medium"}' | jq .

# Expect: 201 {task_id, status: PENDING, queue: "fast"}
# Verify in PG: cmd.task_commands row, cmd.credit_reservations row, cmd.outbox_events row
# Verify credits deducted from users table
```

---

## Milestone 6: Outbox Relay

> Goal: Outbox relay polls PG, publishes to RabbitMQ, marks events published.

### 6.1 [T] Outbox relay unit tests

- [ ] `tests/unit/test_outbox_relay.py`:
  - Test fetch unpublished events (returns rows ordered by created_at)
  - Test publish to RabbitMQ (mock channel.basic_publish with delivery_mode=2)
  - Test mark published after successful publish
  - Test idempotent re-publish (if relay crashes between publish and mark, row retried)
  - Test empty batch (no events → no publishes → sleep)
  - Test batch size limit (LIMIT 100)
  - Test publisher confirm wait
  - Test purge old published events (older than 24h)

### 6.2 [T] Outbox relay integration tests

- [ ] `tests/integration/test_outbox_relay.py`:
  - Submit task → outbox event created
  - Start relay → event published to RabbitMQ
  - Verify outbox_events.published_at is set
  - Verify RabbitMQ queue has message with correct routing key and payload

### 6.3 [I] RabbitMQ topology setup

- [ ] `services/rabbitmq.py`:
  - `declare_topology(channel)`:
    - Exchange: `exchange.tasks` (topic, durable)
    - Exchange: `webhooks` (direct, durable)
    - Queue: `queue.realtime` (durable, x-message-ttl: 30000)
    - Queue: `queue.fast` (durable, x-message-ttl: 300000)
    - Queue: `queue.batch` (durable, x-message-ttl: 1800000)
    - Queue: `queue.dlq` (durable, x-message-ttl: 604800000)
    - Queue: `queue.webhooks` (durable, x-message-ttl: 3600000)
    - Queue: `webhooks.dlq` (durable, x-message-ttl: 604800000)
    - Bindings: `queue.realtime` ← `tasks.*.enterprise.*`, `queue.fast` ← `tasks.async.pro.*` + `tasks.sync.pro.small` + `tasks.batch.enterprise.*`, `queue.batch` ← `tasks.*.free.*` + `tasks.batch.pro.*`
    - DLQ bindings: dead-letter-exchange on each queue → `queue.dlq`
  - `get_connection(url)` → blocking or async pika connection
  - `get_channel(connection)` → channel with publisher confirms enabled

### 6.4 [I] workers/outbox_relay.py

- [ ] Main loop (tick every ~1s):
  1. `fetch_unpublished_events(conn, limit=100)`
  2. For each event:
     - `channel.basic_publish(exchange="exchange.tasks", routing_key=event.routing_key, body=json.dumps(event.payload), properties=pika.BasicProperties(delivery_mode=2, message_id=str(event.event_id)))`
     - Wait for publisher confirm
     - `mark_event_published(conn, event.event_id)`
  3. If no events: sleep 1s
  4. Periodic: `purge_old_published_events(conn, older_than=24h)`
- [ ] Metrics: `outbox_unpublished_count` (gauge), `outbox_publish_lag_seconds` (gauge), `outbox_relay_batch_size` (histogram)
- [ ] Graceful shutdown on SIGINT/SIGTERM
- [ ] Settings: `outbox_poll_interval_seconds`, `outbox_batch_size`, `outbox_purge_after_seconds`

### 6.5 [I] Dockerfile for outbox-relay

- [ ] `docker/outbox-relay/Dockerfile`: Same base as Sol 1 worker, CMD runs `python -m solution2.workers.outbox_relay`

### 6.6 [V] Run outbox relay tests

- [ ] `pytest tests/unit/test_outbox_relay.py -v` — all green
- [ ] `docker compose up -d && pytest tests/integration/test_outbox_relay.py -v` — all green

---

## Milestone 7: RabbitMQ Worker

> Goal: Worker consumes from tiered queues, executes model, captures/releases reservation, writes Redis cache.

### 7.1 [T] Worker unit tests

- [ ] `tests/unit/test_worker.py`:
  - Test message parsing (extract task_id, x, y, model_class, user_id, cost from RabbitMQ body)
  - Test inbox dedup: duplicate event_id → ACK and skip
  - Test successful execution: RESERVED → CAPTURED, task status → COMPLETED, result written
  - Test failed execution: RESERVED → RELEASED, credits refunded, task status → FAILED
  - Test model execution: WorkerModel(x, y, model_class) returns x+y with class-appropriate delay
  - Test Redis result cache write on completion
  - Test webhook enqueue on completion (if callback_url present)

### 7.2 [T] Worker integration tests

- [ ] `tests/integration/test_worker_flow.py`:
  - Submit task → outbox relay publishes → worker consumes → task COMPLETED
  - Verify reservation state = CAPTURED
  - Verify credit_transactions has capture entry
  - Verify Redis task:{task_id} has status=COMPLETED and result
  - Verify cmd.task_commands has status=COMPLETED

### 7.3 [I] WorkerModel (carry from Sol 1)

- [ ] `workers/worker_model.py`:
  - `warmup()`: 10-second async sleep (one-time)
  - `__call__(x, y, model_class)`: Sleep for MODEL_RUNTIME_SECONDS[model_class], return x+y

### 7.4 [I] workers/rabbitmq_worker.py

- [ ] **Startup**:
  - Connect to RabbitMQ, PG, Redis
  - Declare topology (ensure queues exist)
  - Warm up model
  - Subscribe to all 3 queues: `basic_consume(queue.realtime)`, `basic_consume(queue.fast)`, `basic_consume(queue.batch)` with `prefetch_count=1`

- [ ] **Message handler** `on_message(channel, method, properties, body)`:
  1. Parse message body → task_id, x, y, model_class, user_id, cost, event_id
  2. Inbox dedup: `check_inbox_event(conn, event_id)` → if exists: `basic_ack`, return
  3. Update task status: `update_task_command_status(conn, task_id, RUNNING, PENDING)` → if False: `basic_ack` (race lost), return
  4. Try:
     - Execute model: `result = model(x, y, model_class)`
     - BEGIN PG transaction:
       - `capture_reservation(conn, task_id)` → assert True
       - `insert_credit_transaction(conn, user_id, task_id, -cost, "capture")`
       - `update_task_command_status(conn, task_id, COMPLETED, RUNNING, result=result, runtime_ms=elapsed)`
       - `record_inbox_event(conn, event_id, "worker")`
       - COMMIT
     - Post-commit: Redis `HSET task:{task_id}` {status: COMPLETED, result: json, ...} EXPIRE 86400
     - If callback_url: publish to `webhooks` exchange
  5. Except:
     - BEGIN PG transaction:
       - `release_reservation(conn, task_id)` → refund amount
       - `refund_credits(conn, user_id, amount)`
       - `insert_credit_transaction(conn, user_id, task_id, +amount, "release")`
       - `update_task_command_status(conn, task_id, FAILED, RUNNING, error=str(exc))`
       - `record_inbox_event(conn, event_id, "worker")`
       - COMMIT
     - Post-commit: Redis `HSET task:{task_id}` {status: FAILED, error: ...} EXPIRE 86400
  6. `basic_ack(delivery_tag)`

- [ ] Metrics: `task_completions_total{status}`, `task_execution_duration_seconds{model_class}`, `rabbitmq_consumer_active` (gauge)
- [ ] Graceful shutdown: stop consuming, finish current task, close connections

### 7.5 [I] Dockerfile for worker

- [ ] `docker/worker/Dockerfile`: CMD runs `python -m solution2.workers.rabbitmq_worker`

### 7.6 [V] Run worker tests

- [ ] `pytest tests/unit/test_worker.py -v` — all green
- [ ] `docker compose up -d && pytest tests/integration/test_worker_flow.py -v` — all green

---

## **[PROVE 2]: Full task lifecycle (submit → relay → worker → completion)**

```bash
docker compose up -d
TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "586f0ef6-e655-4413-ab08-a481db150389"}' | jq -r .access_token)

# Submit
TASK_ID=$(curl -s -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 7, "y": 3}' | jq -r .task_id)

# Wait for worker to process (10s warmup + 2s execution)
sleep 15

# Verify in PG:
# - cmd.task_commands: status=COMPLETED, result={sum: 10}
# - cmd.credit_reservations: state=CAPTURED
# - cmd.outbox_events: published_at IS NOT NULL
# - cmd.credit_transactions: capture entry
# Verify in Redis: task:{task_id} has status=COMPLETED
# Verify RabbitMQ: queue empty (message ACK'd)
```

---

## Milestone 8: Poll Path (CQRS Query Side)

> Goal: `GET /v1/poll?task_id=X` returns task status from Redis → query view → cmd join.

### 8.1 [T] Poll unit tests

- [ ] `tests/unit/test_poll.py`:
  - Test Redis cache hit: task:{task_id} hash exists → return immediately (0 PG calls)
  - Test Redis miss, query view hit: SELECT from query.task_query_view → return (1 PG call)
  - Test both miss, cmd fallback: SELECT from cmd.task_commands → return (1 PG call)
  - Test authorization: user can only poll own tasks (user_id mismatch → 404)
  - Test admin can poll any task
  - Test terminal status includes result/error
  - Test PENDING status (no result yet)
  - Test EXPIRED status (result TTL exceeded)
  - Test cache population on query view hit (write-through for next poll)

### 8.2 [T] Poll integration tests

- [ ] `tests/integration/test_poll_flow.py`:
  - Submit task → poll immediately → PENDING
  - Wait for completion → poll → COMPLETED with result
  - Poll non-existent task → 404
  - Poll another user's task → 404
  - Poll after Redis flush → still works via PG fallback

### 8.3 [I] api/task_read_routes.py

- [ ] `GET /v1/task/{task_id}` (and compat `GET /v1/poll`):
  - Require scope `task:poll`
  - Tier 1: Redis HGETALL `task:{task_id}` — check user_id match
  - Tier 2: `get_task_query_view(conn, task_id)` — check user_id match
  - Tier 3: `get_task_command(conn, task_id)` — check user_id match
  - On hit from tier 2/3: populate Redis cache (write-through)
  - Return PollTaskResponse
  - Admin bypasses user_id check

### 8.4 [V] Run poll tests

- [ ] `pytest tests/unit/test_poll.py tests/integration/test_poll_flow.py -v` — all green

---

## Milestone 9: Cancel Path

> Goal: `POST /v1/task/{id}/cancel` releases reservation, refunds credits, updates status.

### 9.1 [T] Cancel unit tests

- [ ] `tests/unit/test_cancel.py`:
  - Test successful cancel: reservation RESERVED → RELEASED, credits refunded, task → CANCELLED
  - Test cancel non-existent task: 404
  - Test cancel another user's task: 404
  - Test cancel already completed task: 409
  - Test cancel already cancelled task: 409
  - Test cancel RUNNING task: succeeds (reservation still RESERVED)
  - Test cancel FAILED task: 409 (reservation already RELEASED)
  - Test Redis update after cancel: task:{task_id} status=CANCELLED

### 9.2 [T] Cancel integration tests

- [ ] `tests/integration/test_cancel_flow.py`:
  - Submit task → cancel immediately → 200 with credits_refunded
  - Verify cmd.credit_reservations state=RELEASED
  - Verify users.credits restored
  - Verify cmd.credit_transactions has cancel_release entry
  - Verify cmd.task_commands status=CANCELLED

### 9.3 [I] api/task_write_routes.py (cancel endpoint)

- [ ] `POST /v1/task/{task_id}/cancel`:
  - Require scope `task:cancel`
  - BEGIN PG transaction:
    - `get_reservation_for_cancel(conn, task_id, user_id)` → SELECT FOR UPDATE where state=RESERVED
    - If not found: 409
    - `release_reservation(conn, task_id)`
    - `refund_credits(conn, user_id, amount)`
    - `insert_credit_transaction(conn, user_id, task_id, +amount, "cancel_release")`
    - `update_task_command_status(conn, task_id, CANCELLED, expected IN (PENDING, RUNNING))`
    - COMMIT
  - Post-commit: Redis `HSET task:{task_id} status CANCELLED` EXPIRE 86400
  - Return CancelTaskResponse

### 9.4 [V] Run cancel tests

- [ ] `pytest tests/unit/test_cancel.py tests/integration/test_cancel_flow.py -v` — all green

---

## **[PROVE 3]: Submit + Poll + Cancel lifecycle**

```bash
docker compose up -d
TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "586f0ef6-e655-4413-ab08-a481db150389"}' | jq -r .access_token)

# Submit
TASK_ID=$(curl -s -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 5, "y": 3}' | jq -r .task_id)

# Poll → PENDING
curl -s http://localhost:8000/v1/task/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" | jq .status
# Expect: "PENDING"

# Cancel
curl -s -X POST http://localhost:8000/v1/task/$TASK_ID/cancel \
  -H "Authorization: Bearer $TOKEN" | jq .
# Expect: {task_id, status: CANCELLED, credits_refunded: 10}

# Poll → CANCELLED
curl -s http://localhost:8000/v1/task/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" | jq .status
# Expect: "CANCELLED"

# Verify credits restored
```

---

## Milestone 10: Admin Credits

> Goal: `POST /v1/admin/credits` adjusts balance and writes audit trail.

### 10.1 [T] Admin credits tests

- [ ] `tests/unit/test_admin_credits.py`:
  - Test successful top-up: delta > 0, balance increases
  - Test successful deduction: delta < 0, balance decreases
  - Test deduction below zero: rejected (CHECK constraint)
  - Test non-admin user: 403
  - Test credit_transactions audit entry created
  - Test Redis cache updated after PG commit
  - Test outbox event written (credits.adjusted)

- [ ] `tests/integration/test_admin_credits.py`:
  - Admin JWT → POST /v1/admin/credits → verify balance change in users table
  - Verify credit_transactions row

### 10.2 [I] api/admin_routes.py

- [ ] Port from Sol 1 with minor adjustments:
  - Require scope `admin:credits`
  - Single PG transaction (CTE): UPDATE users + INSERT credit_transactions + INSERT outbox_events
  - Post-commit: Redis SET credits:{uid} = new_balance (for cache)

### 10.3 [V] Run admin credits tests

- [ ] `pytest tests/unit/test_admin_credits.py tests/integration/test_admin_credits.py -v` — all green

---

## Milestone 11: Projector

> Goal: Projector consumes RabbitMQ events, updates query.task_query_view.

### 11.1 [T] Projector unit tests

- [ ] `tests/unit/test_projector.py`:
  - Test event consumption: task.requested → INSERT task_query_view with PENDING
  - Test event consumption: task.completed → UPDATE status=COMPLETED with result
  - Test event consumption: task.failed → UPDATE status=FAILED with error
  - Test event consumption: task.cancelled → UPDATE status=CANCELLED
  - Test inbox dedup: duplicate event_id → ACK and skip
  - Test UPSERT idempotency: same task_id event replayed → no error, latest wins
  - Test unknown event_type: log warning, ACK (don't block queue)

### 11.2 [T] Projector integration tests

- [ ] `tests/integration/test_projector.py`:
  - Submit task → relay publishes → projector updates query view
  - Poll via query path → returns projected status
  - Multiple events for same task → query view reflects latest

### 11.3 [I] workers/projector.py

- [ ] **Startup**: Connect to RabbitMQ, PG. Declare topology. Subscribe to `queue.fast`, `queue.realtime`, `queue.batch` (or a dedicated projection queue bound to same exchange — design decision).
  - **Alternative design**: Dedicated `queue.projections` queue bound to `exchange.tasks` with `#` routing key (receives ALL task events). This is cleaner than sharing worker queues.
  - Decision: Use dedicated `queue.projections` queue.

- [ ] Add to topology: `queue.projections` (durable), bound to `exchange.tasks` with `#`

- [ ] **Message handler**:
  1. Parse event: event_id, event_type, task_id, user_id, status, result, error, etc.
  2. Inbox dedup: `check_inbox_event(conn, event_id)` → if exists: ACK, return
  3. BEGIN PG transaction:
     - `upsert_task_query_view(conn, ...)` — ON CONFLICT (task_id) DO UPDATE
     - `record_inbox_event(conn, event_id, "projector")`
     - COMMIT
  4. `basic_ack`

- [ ] Metrics: `projection_lag_seconds` (gauge), `projections_total{event_type}` (counter)
- [ ] Graceful shutdown

### 11.4 [I] Dockerfile for projector

- [ ] `docker/projector/Dockerfile`

### 11.5 [V] Run projector tests

- [ ] `pytest tests/unit/test_projector.py tests/integration/test_projector.py -v` — all green

---

## **[PROVE 4]: Full CQRS path — submit → relay → worker → projector → poll**

```bash
docker compose up -d
TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "586f0ef6-e655-4413-ab08-a481db150389"}' | jq -r .access_token)

# Submit
TASK_ID=$(curl -s -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 10, "y": 20}' | jq -r .task_id)

# Wait for full pipeline
sleep 20

# Poll — should hit Redis cache (set by worker)
curl -s http://localhost:8000/v1/task/$TASK_ID \
  -H "Authorization: Bearer $TOKEN" | jq .
# Expect: {status: COMPLETED, result: {sum: 30}}

# Verify query.task_query_view has the projection
# Verify cmd.credit_reservations state=CAPTURED
# Verify outbox_events all published
# Verify inbox_events has entries for worker + projector
```

---

## Milestone 12: Watchdog

> Goal: Watchdog releases expired reservations and expires old results.

### 12.1 [T] Watchdog unit tests

- [ ] `tests/unit/test_watchdog.py`:
  - Test phase 1: expired reservation (state=RESERVED, expires_at < now) → RELEASED, credits refunded, task → TIMEOUT
  - Test phase 1: non-expired reservation → skipped
  - Test phase 1: already-captured reservation → skipped
  - Test phase 1: FOR UPDATE SKIP LOCKED (concurrent watchdog instances safe)
  - Test phase 1: Redis task:{task_id} updated to TIMEOUT
  - Test phase 2: COMPLETED task older than 24h → EXPIRED in both cmd and query tables
  - Test phase 2: FAILED task older than 24h → EXPIRED
  - Test phase 2: PENDING task → not expired (still active)
  - Test phase 2: recently completed task → not expired

### 12.2 [T] Watchdog integration tests

- [ ] `tests/integration/test_watchdog.py`:
  - Submit task → set reservation expires_at to past (manually UPDATE PG) → run watchdog tick → verify TIMEOUT
  - Verify credits refunded
  - Verify credit_transactions has timeout_release entry

### 12.3 [I] workers/watchdog.py

- [ ] **Phase 1: Expired Reservation Release** (tick every ~30s):
  1. `find_expired_reservations(conn, limit=50)` — SELECT ... WHERE state=RESERVED AND expires_at < now() FOR UPDATE SKIP LOCKED
  2. For each expired reservation (individual transaction):
     - `release_reservation(conn, task_id)`
     - `refund_credits(conn, user_id, amount)`
     - `insert_credit_transaction(conn, user_id, task_id, +amount, "timeout_release")`
     - `update_task_command_status(conn, task_id, TIMEOUT, expected IN (PENDING, RUNNING))`
     - COMMIT
     - Redis: HSET task:{task_id} status TIMEOUT

- [ ] **Phase 2: Result Expiry** (same tick, after phase 1):
  1. `bulk_expire_results(conn, older_than=now-24h)` — UPDATE both cmd.task_commands and query.task_query_view

- [ ] Metrics: `watchdog_reservations_released_total` (counter), `watchdog_results_expired_total` (counter), `watchdog_cycle_duration_seconds` (histogram)
- [ ] Settings: `watchdog_interval_seconds` (default 30), `reservation_ttl_seconds` (default 600), `result_ttl_seconds` (default 86400)
- [ ] Graceful shutdown

### 12.4 [I] Dockerfile for watchdog

- [ ] `docker/watchdog/Dockerfile`

### 12.5 [V] Run watchdog tests

- [ ] `pytest tests/unit/test_watchdog.py tests/integration/test_watchdog.py -v` — all green

---

## Milestone 13: Webhook Worker

> Goal: Webhook worker delivers callbacks on task completion, with retry and DLQ.

### 13.1 [T] Webhook unit tests

- [ ] `tests/unit/test_webhook_worker.py`:
  - Test successful delivery: POST to callback_url → 200 → ACK
  - Test failed delivery (non-2xx): NACK, route to DLQ with TTL backoff
  - Test timeout: treat as failure
  - Test max retries exceeded: route to webhooks.dlq permanently
  - Test callback_url validation: HTTPS required (or HTTP in dev), no private IPs
  - Test event serialization/deserialization

### 13.2 [I] workers/webhook_worker.py

- [ ] Consume from `queue.webhooks`
- [ ] On message:
  1. Parse webhook event: task_id, status, result, target_url
  2. POST to target_url with body: `{task_id, status, result, event_id}`
  3. On 2xx: `basic_ack`
  4. On failure: `basic_nack(requeue=false)` → routes to `webhooks.dlq`
- [ ] Port retry/DLQ logic from Sol 1 webhook dispatcher, adapted for RabbitMQ (Sol 1 used Redis lists)
- [ ] Metrics: `webhook_deliveries_total{result}`, `webhook_delivery_duration_seconds`

### 13.3 [I] Dockerfile for webhook-worker

- [ ] `docker/webhook-worker/Dockerfile`

### 13.4 [V] Run webhook tests

- [ ] `pytest tests/unit/test_webhook_worker.py -v` — all green

---

## Milestone 14: Batch Submit

> Goal: `POST /v1/task/batch` accepts up to 100 tasks, single reservation, fan-out.

### 14.1 [T] Batch submit tests

- [ ] `tests/unit/test_batch_submit.py`:
  - Test successful batch: N tasks, single reservation for total_cost
  - Test batch size limit: > 100 tasks → 400
  - Test empty batch: 0 tasks → 400
  - Test insufficient credits for total: 402
  - Test concurrency check: active + N > max_concurrent → 429
  - Test mixed model classes: cost = sum of individual costs
  - Test each task gets individual outbox event with correct routing key
  - Test batch_id returned (UUIDv7)

- [ ] `tests/integration/test_batch_flow.py`:
  - Submit batch of 3 tasks → all reach COMPLETED
  - Verify single credit_reservations row for total cost
  - Verify 3 outbox_events rows
  - Verify 3 cmd.task_commands rows

### 14.2 [I] Batch submit endpoint

- [ ] `POST /v1/task/batch`:
  - Parse BatchSubmitRequest
  - Require scope `task:submit`
  - Calculate total_cost = sum(task_cost_for_model(base, t.model_class) for t in tasks)
  - Single PG transaction:
    - Concurrency check
    - Reserve credits for total_cost
    - Create single reservation (or one per task — design decision: one per task is simpler for individual cancellation)
    - Create N task_commands rows
    - Create N outbox_events rows
    - COMMIT
  - Post-commit: Redis HSET for each task
  - Return BatchSubmitResponse

### 14.3 [V] Run batch tests

- [ ] `pytest tests/unit/test_batch_submit.py tests/integration/test_batch_flow.py -v` — all green

---

## **[PROVE 5]: Complete API surface — submit, batch, poll, cancel, admin, webhook**

```bash
docker compose up -d

# Auth
TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "586f0ef6-e655-4413-ab08-a481db150389"}' | jq -r .access_token)

# Single submit
T1=$(curl -s -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 1, "y": 2, "model_class": "small"}' | jq -r .task_id)

# Batch submit
curl -s -X POST http://localhost:8000/v1/task/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tasks": [{"x": 3, "y": 4}, {"x": 5, "y": 6, "model_class": "medium"}]}' | jq .

# Admin credits
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "e1138140-6c35-49b6-b723-ba8d609d8eb5"}' | jq -r .access_token)

curl -s -X POST http://localhost:8000/v1/admin/credits \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<user_id>", "delta": 100, "reason": "top-up"}' | jq .

# Wait for pipeline
sleep 25

# Poll each task → all COMPLETED
# Cancel test: submit new task, cancel immediately
# Verify all credit arithmetic correct
```

---

## Milestone 15: Observability

> Goal: Prometheus metrics, Grafana dashboards, alert rules, structured logging.

### 15.1 [I] observability/metrics.py

- [ ] Port base metrics from Sol 1 (HTTP request counters, durations)
- [ ] Add Sol 2-specific metrics:
  - `reservation_created_total{tier,mode}` (counter)
  - `reservation_captured_total{tier,mode}` (counter)
  - `reservation_released_total{reason}` (counter — cancel, timeout, failure)
  - `reservation_age_seconds{state}` (histogram)
  - `outbox_unpublished_count` (gauge)
  - `outbox_publish_lag_seconds` (gauge)
  - `rabbitmq_queue_depth{queue}` (gauge)
  - `rabbitmq_dlq_depth` (gauge)
  - `webhook_delivery_total{status}` (counter)
  - `projection_lag_seconds` (gauge)
  - `query_cache_hit_rate` (gauge)
  - `watchdog_reservations_released_total` (counter)
  - `watchdog_results_expired_total` (counter)

### 15.2 [I] Prometheus & Grafana config

- [ ] `monitoring/prometheus/prometheus.yml`: Scrape targets (api, worker, outbox-relay, projector, watchdog, webhook-worker)
- [ ] `monitoring/prometheus/alerts.yml`:
  - ReservationTimeoutRate: timeout releases > 5/min → critical
  - OutboxBacklog: unpublished > 50 for 2 min → critical
  - DLQNonEmpty: depth > 0 for 10 min → warning
  - ProjectionLag: lag > 30s for 5 min → warning
  - WebhookFailureRate: retry rate > 20% → warning
- [ ] Grafana dashboard JSON: task throughput, reservation lifecycle, outbox lag, queue depth, credit flow

### 15.3 [I] Structured logging

- [ ] Port structlog config from Sol 1
- [ ] Add context bindings: task_id, user_id, trace_id, reservation_id, event_id
- [ ] JSON output for all services

### 15.4 [V] Verify observability

- [ ] `curl http://localhost:8000/metrics` returns Prometheus format
- [ ] `curl http://localhost:9090/api/v1/targets` shows all targets UP
- [ ] Grafana dashboard loads at `http://localhost:3000`

---

## Milestone 16: Health & Readiness

> Goal: /health and /ready endpoints reflect all dependency states.

### 16.1 [T] Health check tests

- [ ] `tests/unit/test_dependency_health.py`:
  - Test Postgres check: success/failure
  - Test Redis check: success/failure
  - Test RabbitMQ check: success/failure (new)
  - Test readiness: all up → ready=true, any down → ready=false with details

### 16.2 [I] core/dependencies.py

- [ ] Add RabbitMQ health check: connection test or management API ping
- [ ] `readiness()` checks: Postgres + Redis + RabbitMQ

### 16.3 [I] api/system_routes.py

- [ ] Port from Sol 1, add RabbitMQ to readiness

### 16.4 [V] Run health tests

- [ ] `pytest tests/unit/test_dependency_health.py -v` — all green

---

## Milestone 17: Error Contracts & Edge Cases

> Goal: All error codes follow shared taxonomy, edge cases handled.

### 17.1 [T] Error contract tests

- [ ] `tests/integration/test_error_contracts.py`:
  - 400: Invalid input (non-integer x/y, missing fields, batch > 100, free+sync)
  - 401: Missing/invalid/expired JWT, revoked token
  - 402: Insufficient credits
  - 404: Unknown task_id, poll another user's task
  - 409: Idempotency conflict, cancel non-cancellable task
  - 429: Concurrency limit exceeded
  - 503: Dependency unavailable

### 17.2 [I] Error handling

- [ ] Port error envelope from Sol 1 (`ErrorPayload`, `ErrorEnvelope`)
- [ ] Ensure all error responses follow shared format
- [ ] Add `Retry-After` header for 429 and 503 responses

### 17.3 [V] Run error contract tests

- [ ] `pytest tests/integration/test_error_contracts.py -v` — all green

---

## Milestone 18: Concurrency & Idempotency Integration

> Goal: Verify multi-user concurrency and idempotency under load.

### 18.1 [T] Concurrency tests

- [ ] `tests/integration/test_concurrency.py`:
  - N concurrent submits for same user → max_concurrent respected
  - Concurrent submits for different users → no interference
  - Cancel releases concurrency slot immediately
  - Watchdog timeout releases concurrency slot eventually

### 18.2 [T] Idempotency tests

- [ ] `tests/integration/test_idempotency.py`:
  - Same user + same idempotency key → same task_id (200, not 201)
  - Same user + same key + different params → 409
  - Different user + same key → independent tasks (both 201)
  - Idempotency survives restart (PG-durable, not TTL-based)

### 18.3 [V] Run concurrency/idempotency tests

- [ ] `pytest tests/integration/test_concurrency.py tests/integration/test_idempotency.py -v` — all green

---

## **[PROVE 6]: Multi-user concurrency stress test**

```bash
docker compose up -d

# Run scenario harness with multiple users, concurrent submits, cancels, polls
# Verify:
# - No credit leaks (sum of credit_transactions per user = users.credits delta)
# - No orphaned reservations (all RESERVED have valid task in PENDING/RUNNING)
# - All terminal tasks have corresponding CAPTURED or RELEASED reservation
# - Query view consistent with cmd table (projector caught up)
```

---

## Milestone 19: Fault Tests

> Goal: Prove degradation matrix claims from RFC-0002.

### 19.1 [T] Redis down

- [ ] `tests/fault/test_redis_down.py`:
  - Stop Redis → submit still works (PG-only path, no Redis cache write)
  - Poll falls back to PG query view / cmd table
  - Rate limits degraded (no Redis counters)
  - Restart Redis → system recovers, cache rebuilds on next access

### 19.2 [T] RabbitMQ down

- [ ] `tests/fault/test_rabbitmq_down.py`:
  - Stop RabbitMQ → submit still succeeds (outbox holds events)
  - Outbox relay logs warnings, retries on reconnect
  - Tasks remain PENDING (no worker processing)
  - Restart RabbitMQ → relay resumes, events flow, tasks complete
  - No duplicate processing (inbox dedup)

### 19.3 [T] Postgres down

- [ ] `tests/fault/test_postgres_down.py`:
  - Stop Postgres → command routes return 503
  - Poll with Redis cache hit still works
  - Poll with cache miss → 503
  - Restart Postgres → full recovery

### 19.4 [T] Worker down

- [ ] `tests/fault/test_worker_down.py`:
  - Stop worker → tasks queue in RabbitMQ
  - Reservations tick toward expiry
  - Watchdog releases expired reservations, refunds credits
  - Restart worker → queued tasks processed
  - No duplicate processing (inbox dedup)

### 19.5 [T] Outbox relay crash-restart

- [ ] `tests/fault/test_outbox_relay_crash.py`:
  - Submit tasks → relay publishes some → kill relay mid-batch
  - Restart relay → unpublished events picked up
  - Consumer inbox dedup prevents double-processing of already-published events

### 19.6 [V] Run fault tests

- [ ] `pytest tests/fault/ -v` — all green

---

## Milestone 20: Demo Script & E2E

> Goal: Demo script that exercises full lifecycle. E2E tests run as pytest.

### 20.1 [I] utils/demo.sh

- [ ] Port from Sol 1, adjusted for Sol 2:
  1. Get JWT via OAuth
  2. Submit task (async, medium model)
  3. Poll until COMPLETED
  4. Submit batch (3 tasks)
  5. Poll batch until all COMPLETED
  6. Cancel a task
  7. Admin top-up credits
  8. Display results

### 20.2 [I] utils/demo.py

- [ ] Python equivalent with structured output and timing

### 20.3 [T] E2E tests

- [ ] `tests/e2e/test_demo_script.py`: Execute demo.sh, verify exit code 0
- [ ] `tests/e2e/test_demo_python.py`: Execute demo.py, verify exit code 0

### 20.4 [V] Run E2E

- [ ] `cd 2_solution && docker compose up -d && ./utils/demo.sh`
- [ ] `pytest tests/e2e/ -v` — all green

---

## **[PROVE 7: FINAL]: Full stack end-to-end validation**

```bash
cd 2_solution

# Clean start
docker compose down -v
docker compose up --build -d

# Wait for healthy
./scripts/wait_ready.sh

# Run all tests
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/fault/ -v
pytest tests/e2e/ -v

# Run demo
./utils/demo.sh

# Verify:
# 1. All tests pass
# 2. No credit leaks (audit trail balances)
# 3. No orphaned reservations
# 4. Query view consistent with cmd table
# 5. Outbox fully drained (no unpublished events)
# 6. RabbitMQ queues empty (all ACK'd)
# 7. Prometheus metrics accessible
# 8. Grafana dashboards render
# 9. Structured logs contain correlation IDs

docker compose down -v
```

---

## Milestone 21: Scripts & Quality Gate

> Goal: CI-ready scripts matching Sol 0/Sol 1 rigor.

### 21.1 [I] scripts/quality_gate.sh

- [ ] Ruff lint + format check
- [ ] Mypy type checking
- [ ] Security scan (bandit or equivalent)
- [ ] Dependency audit

### 21.2 [I] scripts/coverage_gate.sh

- [ ] Pytest with coverage reporting
- [ ] Minimum coverage threshold

### 21.3 [I] scripts/ci_check.sh

- [ ] Full pipeline: quality → unit tests → build compose → integration → fault → e2e

### 21.4 [I] scripts/wait_ready.sh

- [ ] Poll /ready until all services healthy

### 21.5 [I] scripts/run_scenarios.py

- [ ] Port from Sol 1 with Sol 2 scenarios:
  - Auth flow (OAuth + JWT)
  - Single submit + poll
  - Batch submit + poll
  - Idempotency
  - Concurrency limits per tier
  - Cancel flow
  - Admin credits
  - Reservation timeout (watchdog)
  - Model class cost variation
  - SLA routing verification
  - Multi-user isolation
  - Webhook delivery
  - Outbox drain verification

### 21.6 [V] Run quality gate

- [ ] `./scripts/quality_gate.sh` — passes
- [ ] `./scripts/ci_check.sh` — passes

---

## Appendix A: File tree (expected final state)

```
2_solution/
├── compose.yaml
├── pyproject.toml
├── .env.dev.defaults
├── src/solution2/
│   ├── __init__.py
│   ├── main.py
│   ├── app.py
│   ├── constants.py
│   ├── api/
│   │   ├── paths.py
│   │   ├── contracts.py
│   │   ├── error_responses.py
│   │   ├── system_routes.py
│   │   ├── task_write_routes.py     # submit, cancel, batch
│   │   ├── task_read_routes.py      # poll (CQRS query side)
│   │   ├── admin_routes.py
│   │   └── webhook_routes.py
│   ├── core/
│   │   ├── settings.py
│   │   ├── runtime.py
│   │   └── dependencies.py
│   ├── models/
│   │   ├── domain.py
│   │   └── schemas.py
│   ├── services/
│   │   ├── auth.py
│   │   ├── billing.py               # reservation model
│   │   ├── rabbitmq.py              # topology, connection
│   │   ├── cache.py                 # Redis query cache ops
│   │   └── webhooks.py
│   ├── db/
│   │   ├── migrate.py
│   │   ├── repository.py
│   │   └── migrations/
│   │       ├── 0001-0009 (from Sol 1)
│   │       ├── 0010_create_cmd_schema.sql
│   │       ├── 0011_create_query_schema.sql
│   │       ├── 0012_cmd_task_commands.sql
│   │       ├── 0013_cmd_credit_reservations.sql
│   │       ├── 0014_cmd_outbox_events.sql
│   │       ├── 0015_cmd_inbox_events.sql
│   │       ├── 0016_query_task_view.sql
│   │       └── 0017_seed_users_sol2.sql
│   ├── workers/
│   │   ├── rabbitmq_worker.py
│   │   ├── outbox_relay.py
│   │   ├── projector.py
│   │   ├── watchdog.py
│   │   ├── webhook_worker.py
│   │   └── worker_model.py
│   ├── observability/
│   │   ├── metrics.py
│   │   └── tracing.py
│   └── utils/
│       ├── logging.py
│       └── retry.py
├── docker/
│   ├── api/Dockerfile
│   ├── worker/Dockerfile
│   ├── outbox-relay/Dockerfile
│   ├── projector/Dockerfile
│   ├── watchdog/Dockerfile
│   ├── webhook-worker/Dockerfile
│   ├── hydra/bootstrap-clients.sh
│   └── postgres/Dockerfile
├── tests/
│   ├── constants.py
│   ├── fakes.py
│   ├── unit/
│   │   ├── test_reservation_state.py
│   │   ├── test_sla_routing.py
│   │   ├── test_cost_calculation.py
│   │   ├── test_task_state.py
│   │   ├── test_auth_service.py
│   │   ├── test_auth_utils.py
│   │   ├── test_submit_reservation.py
│   │   ├── test_outbox_relay.py
│   │   ├── test_worker.py
│   │   ├── test_projector.py
│   │   ├── test_poll.py
│   │   ├── test_cancel.py
│   │   ├── test_admin_credits.py
│   │   ├── test_watchdog.py
│   │   ├── test_webhook_worker.py
│   │   ├── test_batch_submit.py
│   │   ├── test_dependency_health.py
│   │   ├── test_repository_cmd.py
│   │   ├── test_repository_query.py
│   │   ├── test_repository_outbox.py
│   │   └── test_migrations.py
│   ├── integration/
│   │   ├── test_submit_flow.py
│   │   ├── test_worker_flow.py
│   │   ├── test_poll_flow.py
│   │   ├── test_cancel_flow.py
│   │   ├── test_admin_credits.py
│   │   ├── test_projector.py
│   │   ├── test_watchdog.py
│   │   ├── test_batch_flow.py
│   │   ├── test_concurrency.py
│   │   ├── test_idempotency.py
│   │   ├── test_outbox_relay.py
│   │   ├── test_error_contracts.py
│   │   └── test_oauth_jwt_flow.py
│   ├── fault/
│   │   ├── test_redis_down.py
│   │   ├── test_rabbitmq_down.py
│   │   ├── test_postgres_down.py
│   │   ├── test_worker_down.py
│   │   └── test_outbox_relay_crash.py
│   └── e2e/
│       ├── test_demo_script.py
│       └── test_demo_python.py
├── monitoring/
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alerts.yml
│   └── grafana/
│       ├── provisioning/
│       └── dashboards/
├── scripts/
│   ├── quality_gate.sh
│   ├── coverage_gate.sh
│   ├── ci_check.sh
│   ├── wait_ready.sh
│   └── run_scenarios.py
└── utils/
    ├── demo.sh
    └── demo.py
```

## Appendix B: Compose services (expected final state)

| Service          | Image/Build              | Depends on                    | Ports       |
| ---------------- | ------------------------ | ----------------------------- | ----------- |
| api              | docker/api/Dockerfile    | postgres, redis, rabbitmq, hydra | 8000        |
| worker           | docker/worker/Dockerfile | postgres, redis, rabbitmq     | 9100 (metrics) |
| outbox-relay     | docker/outbox-relay/     | postgres, rabbitmq            | 9200 (metrics) |
| projector        | docker/projector/        | postgres, rabbitmq            | 9300 (metrics) |
| watchdog         | docker/watchdog/         | postgres, redis               | 9400 (metrics) |
| webhook-worker   | docker/webhook-worker/   | rabbitmq                      | 9500 (metrics) |
| hydra            | oryd/hydra:v25.4.0       | postgres                      | 4444, 4445  |
| hydra-migrate    | oryd/hydra:v25.4.0       | postgres                      | -           |
| hydra-client-init| oryd/hydra:v25.4.0       | hydra                         | -           |
| postgres         | postgres:17.6-alpine     | -                             | 5432        |
| redis            | redis:8.2.4-alpine       | -                             | 6379        |
| rabbitmq         | rabbitmq:4.1-mgmt-alpine | -                             | 5672, 15672 |
| prometheus       | prom/prometheus:v3.5.1   | -                             | 9090        |
| grafana          | grafana/grafana:12.3.3   | prometheus                    | 3000        |

## Appendix C: PROVE checkpoint summary

| Checkpoint | After milestone | What it proves                                              |
| ---------- | --------------- | ----------------------------------------------------------- |
| PROVE 1    | 5               | Submit path: reservation + outbox + Redis write-through     |
| PROVE 2    | 7               | Full lifecycle: submit → relay → worker → completion        |
| PROVE 3    | 9               | Submit + poll + cancel with credit arithmetic               |
| PROVE 4    | 11              | Full CQRS: submit → relay → worker → projector → poll      |
| PROVE 5    | 14              | Complete API surface: all endpoints working together        |
| PROVE 6    | 18              | Multi-user concurrency stress, credit invariants            |
| PROVE 7    | 20              | Full stack: all tests + demo + observability + fault proofs |
