# RFC-0003: Request Flow Diagrams

Parent: [RFC-0003 README](./README.md)

Every diagram below shows the exact sequence of store calls for one API request. The "DB calls on happy path" count directly answers the assignment question: _"how can we reduce the number of calls?"_

Column convention: Client, API, TigerBeetle (TB), PG (cmd+query schemas), Redpanda, RabbitMQ, Redis. Arrows terminate at the column that owns the operation.

Key difference from solution 2: billing invariants are enforced by TigerBeetle (pending/post/void transfers), not application SQL. Redpanda replaces RabbitMQ as the event backbone; RabbitMQ is retained only for worker dispatch with hot/cold model-affinity routing.

---

## 1. Submit path (`POST /v1/task`) -- TigerBeetle + outbox

```text
Client              API                 TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |                   |
  |-- POST /v1/task ->|                   |                   |                   |
  |  {x:5, y:3,       |                   |                   |                   |
  |   model_class:     |                   |                   |                   |
  |   "medium"}        |                   |                   |                   |
  |                   |                   |                   |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT for revocation]                  |
  |                   |                   |                   |                   |
  |                   |  1. Reserve credits (TB pending transfer)                  |
  |                   |-- create_transfer->|                   |                   |
  |                   |   (user->escrow,   |                   |                   |
  |                   |    PENDING,        |                   |                   |
  |                   |    timeout=600s)   |                   |                   |
  |                   |<-- OK / EXCEEDS ---|                   |                   |
  |                   |                   |                   |                   |
  |                   |  [EXCEEDS_CREDITS? -> 402]             |                   |
  |                   |                   |                   |                   |
  |                   |  2. Concurrency check (Redis counter)  |                   |
  |                   |-- GET active:{uid}>|------------------>|----------------->|
  |                   |<-- count ----------|-------------------|<-- count ---------|
  |                   |                   |                   |                   |
  |                   |  [over limit? -> void TB transfer, 429]|                   |
  |                   |-- INCR active:{uid}|------------------>|----------------->|
  |                   |                   |                   |                   |
  |                   |  3. PG transaction: command + outbox   |                   |
  |                   |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  3a. Idempotency  |                   |                   |
  |                   |-- SELECT task_id --|------------------>|                   |
  |                   |   WHERE user_id=$1 |                   |                   |
  |                   |   AND idem_key=$2  |                   |                   |
  |                   |<-- NULL (no dup) --|<-----------------|                   |
  |                   |                   |                   |                   |
  |                   |  [dup? -> void TB transfer, return existing]               |
  |                   |                   |                   |                   |
  |                   |  3b. Command row  |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   task_commands -->|------------------>|                   |
  |                   |   (task_id, user_id,                   |                   |
  |                   |    tier, mode,     |                   |                   |
  |                   |    model_class,    |                   |                   |
  |                   |    x, y, cost,     |                   |                   |
  |                   |    tb_pending_     |                   |                   |
  |                   |    transfer_id,    |                   |                   |
  |                   |    billing_state=  |                   |                   |
  |                   |    'RESERVED')     |                   |                   |
  |                   |                   |                   |                   |
  |                   |  3c. Outbox event  |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   outbox_events -->|------------------>|                   |
  |                   |   (task.requested, |                   |                   |
  |                   |    topic=          |                   |                   |
  |                   |    tasks.requested,|                   |                   |
  |                   |    payload)        |                   |                   |
  |                   |                   |                   |                   |
  |                   |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  4. Write-through cache                |                   |
  |                   |-- HSET task:{tid} -|------------------>|----------------->|
  |                   |   status=PENDING   |                   |                   |
  |                   |   billing_state=   |                   |                   |
  |                   |   RESERVED         |                   |                   |
  |                   |-- EXPIRE 86400 ----|------------------>|----------------->|
  |                   |                   |                   |                   |
  |<-- 201 {task_id,  |                   |                   |                   |
  |     billing_state}|                   |                   |                   |
```

**DB calls: 1 TB + 1 PG** (TB: create pending transfer. PG: single transaction with idempotency check + INSERT task_commands + INSERT outbox_events). Redis: 1 GET + 1 INCR + 1 HSET (non-blocking). The outbox relay publishes to Redpanda asynchronously.

Key difference vs solution 2: credit reservation is a TigerBeetle pending transfer (Jepsen-verified, `debits_must_not_exceed_credits` enforced atomically) instead of an app-coordinated `UPDATE users SET credits=credits-$1 WHERE credits>=$1`. The application cannot overdraft even if there is a bug in the admission logic.

### Full pseudo-code

```python
async def submit_task(jwt_claims, payload, idem_key):
    tier = jwt_claims.tier
    mode = payload.get("mode", "async")
    model = payload.get("model_class", "small")
    cost = compute_cost(model, tier)
    task_id = uuid7()
    transfer_id = uuid7()

    # 1. TigerBeetle: create pending transfer (user -> escrow)
    result = tb_client.create_transfers([
        Transfer(
            id=uuid_to_u128(transfer_id),
            debit_account_id=uuid_to_u128(jwt_claims.sub),
            credit_account_id=ESCROW_ID,
            amount=cost,
            ledger=1,
            code=200,  # task_reserve
            flags=TransferFlags.PENDING,
            timeout=600,  # seconds - auto-void after 10 min
        )
    ])

    if result[0].result == CreateTransferResult.EXCEEDS_CREDITS:
        raise HTTPException(402, "INSUFFICIENT_CREDITS")
    if result[0].result != CreateTransferResult.OK:
        raise HTTPException(500, f"TB transfer failed: {result[0].result}")

    # 2. Check concurrency via Redis counter (accurate across variable costs)
    active_count = int(await redis.get(f"active:{jwt_claims.sub}") or "0")
    if active_count >= get_max_concurrent(tier):
        # Void the transfer we just created
        tb_client.create_transfers([
            Transfer(
                id=new_transfer_id(),
                pending_id=uuid_to_u128(transfer_id),
                flags=TransferFlags.VOID_PENDING_TRANSFER,
            )
        ])
        raise HTTPException(429, "CONCURRENCY_LIMIT")
    await redis.incr(f"active:{jwt_claims.sub}")

    # 3. Postgres: command row + outbox (one transaction)
    async with cmd_db.transaction():
        # Idempotency
        existing = await cmd_db.fetchval(
            "SELECT task_id FROM task_commands WHERE user_id=$1 AND idempotency_key=$2",
            jwt_claims.sub, idem_key)
        if existing:
            # Void the TB transfer (idempotent return)
            tb_client.create_transfers([
                Transfer(id=new_transfer_id(), pending_id=uuid_to_u128(transfer_id),
                         flags=TransferFlags.VOID_PENDING_TRANSFER)
            ])
            return {"task_id": str(existing)}

        await cmd_db.execute("""
            INSERT INTO task_commands(task_id, user_id, tier, mode, model_class,
                                     x, y, cost, tb_pending_transfer_id,
                                     billing_state, callback_url, idempotency_key)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'RESERVED',$10,$11)
        """, task_id, jwt_claims.sub, tier, mode, model, payload["x"], payload["y"],
             cost, transfer_id, payload.get("callback_url"), idem_key)

        await cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, 'task.requested', 'tasks.requested', $2)
        """, task_id, json.dumps({
            "task_id": str(task_id), "user_id": str(jwt_claims.sub),
            "tier": tier, "mode": mode, "model_class": model,
            "x": payload["x"], "y": payload["y"], "cost": cost,
            "tb_transfer_id": str(transfer_id),
        }))

    # 4. Write-through to Redis query cache
    await redis.hset(f"task:{task_id}", mapping={
        "status": "PENDING", "billing_state": "RESERVED",
        "user_id": str(jwt_claims.sub),
    })
    await redis.expire(f"task:{task_id}", 86400)

    return {"task_id": str(task_id), "billing_state": "RESERVED"}
```

---

## 2. Outbox relay (Postgres -> Redpanda)

```text
PG (cmd.outbox_events)     Relay                  Redpanda
  |                          |                      |
  |  [tick every ~1s]        |                      |
  |                          |                      |
  |<-- SELECT event_id, ----|                      |
  |    topic, payload        |                      |
  |    FROM outbox_events    |                      |
  |    WHERE published_at    |                      |
  |    IS NULL               |                      |
  |    ORDER BY created_at   |                      |
  |    LIMIT 100             |                      |
  |--- rows[] ------------->|                      |
  |                          |                      |
  |  [for each row:]        |                      |
  |                          |-- producer.produce ->|
  |                          |   topic=row.topic    |
  |                          |   key=event_id       |
  |                          |   value=payload      |
  |                          |   headers:           |
  |                          |     event_id         |
  |                          |                      |
  |  [batch flush]           |                      |
  |                          |-- producer.flush() ->|
  |                          |<-- acks -------------|
  |                          |                      |
  |<-- UPDATE outbox_events -|                      |
  |    SET published_at=now()|                      |
  |    WHERE event_id =      |                      |
  |    ANY($1)               |                      |
  |--- OK ----------------->|                      |
  |                          |                      |
  |  [sleep, repeat]        |                      |
```

**DB calls: 2 PG** (SELECT unpublished + UPDATE published_at). Redpanda: 1 produce batch + flush.

The relay marks events published only AFTER receiving Redpanda acknowledgement. If the relay crashes mid-batch, unpublished rows remain with `published_at IS NULL` and are retried on restart. Consumers deduplicate via inbox_events table, so re-publishing the same event is safe.

### Full pseudo-code

```python
async def relay_outbox():
    """Polls outbox, publishes to Redpanda. Runs on a loop."""
    rows = await cmd_db.fetch("""
        SELECT event_id, topic, payload FROM outbox_events
        WHERE published_at IS NULL ORDER BY created_at LIMIT 100
    """)
    for row in rows:
        producer.produce(
            topic=row["topic"],
            key=str(row["event_id"]).encode(),
            value=row["payload"].encode(),
            headers={"event_id": str(row["event_id"])},
        )
    producer.flush()
    # Mark published
    event_ids = [r["event_id"] for r in rows]
    await cmd_db.execute(
        "UPDATE outbox_events SET published_at=now() WHERE event_id = ANY($1)", event_ids)
```

---

## 3. Dispatcher (Redpanda -> RabbitMQ)

```text
Redpanda            Dispatcher             RabbitMQ
  |                   |                      |
  |  [consumer group: |                      |
  |   "dispatcher"]   |                      |
  |                   |                      |
  |-- deliver msg --->|                      |
  |   topic=          |                      |
  |   tasks.requested |                      |
  |   {task_id,       |                      |
  |    model_class,   |                      |
  |    tier, ...}     |                      |
  |                   |                      |
  |                   |-- basic_publish ---->|
  |                   |   exchange=          |
  |                   |   "preloaded"        |
  |                   |   headers:           |
  |                   |     model_class=     |
  |                   |     event.model_class|
  |                   |     tier=event.tier  |
  |                   |     task_id=         |
  |                   |     event.task_id    |
  |                   |   delivery_mode=2    |
  |                   |   (persistent)       |
  |                   |                      |
  |                   |                [warm binding exists?]
  |                   |                      |
  |                   |              YES:    |
  |                   |              route ->|-> hot-{model_class} queue
  |                   |                      |
  |                   |              NO:     |
  |                   |              alternate-exchange ->
  |                   |              "coldstart" ->
  |                   |              cold queue
  |                   |                      |
  |<-- consumer.commit|                      |
```

**DB calls: 0**. Redpanda: 1 consume. RabbitMQ: 1 publish.

The dispatcher bridges the event log (durable, replayable) to the work queue (transient, routed). Redpanda retains the event for replay; RabbitMQ provides broker-side model-affinity routing that Redpanda cannot do natively.

### Full pseudo-code

```python
async def run_dispatcher():
    """Consumes tasks.requested from Redpanda, publishes to RabbitMQ for worker dispatch.
    Bridges the event log (durable, replayable) to the work queue (transient, routed)."""
    consumer = Consumer({"group.id": "dispatcher", "auto.offset.reset": "earliest"})
    consumer.subscribe(["tasks.requested"])

    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    channel = connection.channel()

    # Declare exchanges with alternate-exchange fallback
    channel.exchange_declare(exchange="coldstart", exchange_type="headers", durable=True)
    channel.exchange_declare(
        exchange="preloaded", exchange_type="headers", durable=True,
        arguments={"alternate-exchange": "coldstart"})

    # Cold pool queue (catches everything not routed to a warm worker)
    channel.queue_declare(queue="cold", durable=True)
    channel.queue_bind(queue="cold", exchange="coldstart", arguments={"x-match": "all"})

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        event = json.loads(msg.value())

        channel.basic_publish(
            exchange="preloaded",
            routing_key="",
            body=msg.value(),
            properties=pika.BasicProperties(
                headers={
                    "model_class": event["model_class"],
                    "tier": event["tier"],
                    "task_id": event["task_id"],
                },
                delivery_mode=2,  # persistent
            ))
        consumer.commit(msg)
```

---

## 4. Worker with hot/cold model-affinity

```text
RabbitMQ            Worker                 TigerBeetle         PG (cmd)            Redis
  |                   |                      |                   |                   |
  |-- deliver msg --->|                      |                   |                   |
  |   (from hot-{mc}  |                      |                   |                   |
  |    or cold queue)  |                      |                   |                   |
  |                   |                      |                   |                   |
  |                   |  [warm_model ==      |                   |                   |
  |                   |   model_class?]      |                   |                   |
  |                   |                      |                   |                   |
  |                   |  NO: cold start      |                   |                   |
  |                   |  - load model (3s)   |                   |                   |
  |                   |  - go_warm(mc)       |                   |                   |
  |                   |  - bind hot-{mc}     |                   |                   |
  |                   |    to preloaded exch |                   |                   |
  |                   |  - SADD warm:{mc} -->|------------------>|----------------->|
  |                   |                      |                   |                   |
  |                   |  YES: hot path       |                   |                   |
  |                   |  (skip load)         |                   |                   |
  |                   |                      |                   |                   |
  |                   |  [inference]         |                   |                   |
  |                   |  model(x,y) -> result|                   |                   |
  |                   |  (~2-6s)             |                   |                   |
  |                   |                      |                   |                   |
  |                   |  [completion: see flow 5 below]          |                   |
  |                   |                      |                   |                   |
  |<-- basic_ack -----|                      |                   |                   |
```

**DB calls: 0 PG on hot path** (model already loaded, inference only). **0 PG on cold path** (model load is local, warm registration is Redis only). Completion calls are covered in flow 5.

### Full pseudo-code

```python
class InferenceWorker:
    """Worker maintains a warm model cache. When a task matches the loaded model,
    it skips the load phase (hot path). Otherwise it loads the model (cold start).
    On startup, the worker registers its warm model binding in RabbitMQ."""

    def __init__(self, channel, worker_id):
        self.channel = channel
        self.worker_id = worker_id
        self.warm_model = None
        self.hot_queue = None

    def start_cold(self):
        """Start as a cold worker - consumes from cold queue, handles any model."""
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(queue="cold", on_message_callback=self.on_task)
        self.channel.start_consuming()

    def go_warm(self, model_class):
        """Register as warm for a model_class - bind a hot queue to preloaded exchange."""
        self.warm_model = model_class
        self.hot_queue = f"hot-{model_class}"
        self.channel.queue_declare(queue=self.hot_queue, durable=True)
        self.channel.queue_bind(
            queue=self.hot_queue, exchange="preloaded",
            arguments={"x-match": "all", "model_class": model_class})
        # Also consume from hot queue (in addition to cold)
        self.channel.basic_consume(queue=self.hot_queue, on_message_callback=self.on_task)
        # Register in Redis for dispatcher visibility
        redis.sadd(f"warm:{model_class}", self.worker_id)

    def on_task(self, ch, method, properties, body):
        event = json.loads(body)
        model_class = event["model_class"]

        # Cold start penalty if model not loaded
        if self.warm_model != model_class:
            time.sleep(3)  # simulate model loading
            self.go_warm(model_class)

        # Inference (simulated)
        model_factor = {"small": 1, "medium": 2, "large": 3}[model_class]
        time.sleep(model_factor * 2)  # simulate inference
        result = {"result": event["x"] + event["y"]}

        # Post/void in TigerBeetle + outbox (same as on_task_complete below)
        on_task_complete(
            task_id=event["task_id"],
            tb_transfer_id=event["tb_transfer_id"],
            user_id=event["user_id"],
            cost=event["cost"],
            success=True,
            result=result,
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
```

---

## 5. Worker completion: post/void pending transfer

### Happy path (success -- post pending transfer)

```text
Worker              TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |
  |  [inference done, |                   |                   |
  |   success=True]   |                   |                   |
  |                   |                   |                   |
  |  1. Post pending transfer (escrow -> platform revenue)    |
  |-- create_transfer->|                   |                   |
  |   POST_PENDING_    |                   |                   |
  |   TRANSFER         |                   |                   |
  |   pending_id=      |                   |                   |
  |   tb_transfer_id   |                   |                   |
  |<-- OK -------------|                   |                   |
  |                   |                   |                   |
  |  [billing_state = CAPTURED]            |                   |
  |                   |                   |                   |
  |  2. Update command DB + outbox        |                   |
  |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |
  |-- UPDATE           |                   |                   |
  |   task_commands -->|------------------>|                   |
  |   status=COMPLETED |                   |                   |
  |   billing_state=   |                   |                   |
  |   CAPTURED         |                   |                   |
  |                   |                   |                   |
  |-- INSERT           |                   |                   |
  |   outbox_events -->|------------------>|                   |
  |   tasks.completed  |                   |                   |
  |   {task_id, status,|                   |                   |
  |    billing_state,  |                   |                   |
  |    result, cost}   |                   |                   |
  |                   |                   |                   |
  |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |
  |  3. Decrement active counter          |                   |
  |-- DECR active:{uid}|------------------>|----------------->|
  |                   |                   |                   |
  |  4. Write-through to query cache      |                   |
  |-- HSET task:{tid} -|------------------>|----------------->|
  |   status=COMPLETED |                   |                   |
  |   billing_state=   |                   |                   |
  |   CAPTURED         |                   |                   |
  |   result=...       |                   |                   |
```

### Failure path (void pending transfer)

```text
Worker              TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |
  |  [inference failed |                   |                   |
  |   or exception]    |                   |                   |
  |                   |                   |                   |
  |  1. Void pending transfer (credits auto-return to user)   |
  |-- create_transfer->|                   |                   |
  |   VOID_PENDING_    |                   |                   |
  |   TRANSFER         |                   |                   |
  |   pending_id=      |                   |                   |
  |   tb_transfer_id   |                   |                   |
  |<-- OK -------------|                   |                   |
  |                   |                   |                   |
  |  [billing_state = RELEASED]            |                   |
  |                   |                   |                   |
  |  2. Update command DB + outbox        |                   |
  |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |
  |-- UPDATE           |                   |                   |
  |   task_commands -->|------------------>|                   |
  |   status=FAILED    |                   |                   |
  |   billing_state=   |                   |                   |
  |   RELEASED         |                   |                   |
  |                   |                   |                   |
  |-- INSERT           |                   |                   |
  |   outbox_events -->|------------------>|                   |
  |   tasks.failed     |                   |                   |
  |   {task_id, status,|                   |                   |
  |    billing_state,  |                   |                   |
  |    error, cost}    |                   |                   |
  |                   |                   |                   |
  |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |
  |  3. Decrement + update cache          |                   |
  |-- DECR active:{uid}|------------------>|----------------->|
  |-- HSET task:{tid} -|------------------>|----------------->|
  |   status=FAILED    |                   |                   |
  |   billing_state=   |                   |                   |
  |   RELEASED         |                   |                   |
```

**DB calls (both paths): 1 TB + 1 PG** (TB: post or void pending transfer. PG: single transaction with UPDATE task_commands + INSERT outbox_events). Redis: 1 DECR + 1 HSET (non-blocking write-through).

Key difference vs solution 2: no `UPDATE credit_reservations` + `UPDATE users SET credits=credits+$1` -- TigerBeetle void atomically returns credits. No application code can accidentally skip the refund.

### Full pseudo-code

```python
def on_task_complete(task_id, tb_transfer_id, user_id, cost, success, result=None, error=None):
    if success:
        # Post pending transfer: escrow -> platform revenue (credits captured)
        tb_client.create_transfers([
            Transfer(
                id=new_transfer_id(),
                pending_id=uuid_to_u128(tb_transfer_id),
                flags=TransferFlags.POST_PENDING_TRANSFER,
            )
        ])
        billing_state = "CAPTURED"
        status = "COMPLETED"
        event_type = "tasks.completed"
        topic = "tasks.completed"
    else:
        # Void pending transfer: credits auto-return to user
        tb_client.create_transfers([
            Transfer(
                id=new_transfer_id(),
                pending_id=uuid_to_u128(tb_transfer_id),
                flags=TransferFlags.VOID_PENDING_TRANSFER,
            )
        ])
        billing_state = "RELEASED"
        status = "FAILED"
        event_type = "tasks.failed"
        topic = "tasks.failed"

    # Update command DB + outbox
    with cmd_db.transaction():
        cmd_db.execute("""
            UPDATE task_commands SET status=$1, billing_state=$2, updated_at=now()
            WHERE task_id=$3
        """, status, billing_state, task_id)
        cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4)
        """, task_id, event_type, topic, json.dumps({
            "task_id": str(task_id), "user_id": str(user_id),
            "status": status, "billing_state": billing_state,
            "result": result, "error": error, "cost": cost,
        }))

    # Decrement active counter
    redis.decr(f"active:{user_id}")

    # Write-through to query cache
    redis.hset(f"task:{task_id}", mapping={
        "status": status, "billing_state": billing_state,
        "result": json.dumps(result) if result else "",
    })
```

---

## 6. Cancel: void pending transfer (`POST /v1/task/{id}/cancel`)

```text
Client              API                 TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |                   |
  |-- POST cancel --->|                   |                   |                   |
  |                   |                   |                   |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT for revocation]                  |
  |                   |                   |                   |                   |
  |                   |  1. Fetch task + validate             |                   |
  |                   |-- SELECT ----------|------------------>|                   |
  |                   |   tb_pending_      |                   |                   |
  |                   |   transfer_id,     |                   |                   |
  |                   |   cost,            |                   |                   |
  |                   |   billing_state    |                   |                   |
  |                   |   FROM task_commands                   |                   |
  |                   |   WHERE task_id=$1 |                   |                   |
  |                   |   AND user_id=$2   |                   |                   |
  |                   |<-- row ------------|<-----------------|                   |
  |                   |                   |                   |                   |
  |                   |  [billing_state != RESERVED? -> 409]   |                   |
  |                   |                   |                   |                   |
  |                   |  2. Void in TigerBeetle (credits auto-return)              |
  |                   |-- create_transfer->|                   |                   |
  |                   |   VOID_PENDING_    |                   |                   |
  |                   |   TRANSFER         |                   |                   |
  |                   |   pending_id=      |                   |                   |
  |                   |   tb_transfer_id   |                   |                   |
  |                   |<-- OK -------------|                   |                   |
  |                   |                   |                   |                   |
  |                   |  3. Update command DB + outbox        |                   |
  |                   |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |-- UPDATE           |                   |                   |
  |                   |   task_commands -->|------------------>|                   |
  |                   |   status=CANCELLED |                   |                   |
  |                   |   billing_state=   |                   |                   |
  |                   |   RELEASED         |                   |                   |
  |                   |                   |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   outbox_events -->|------------------>|                   |
  |                   |   tasks.cancelled  |                   |                   |
  |                   |   {task_id,        |                   |                   |
  |                   |    billing_state=  |                   |                   |
  |                   |    RELEASED,       |                   |                   |
  |                   |    credits_refunded|                   |                   |
  |                   |    =cost}          |                   |                   |
  |                   |                   |                   |                   |
  |                   |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  4. Update Redis  |                   |                   |
  |                   |-- DECR active:{uid}|------------------>|----------------->|
  |                   |-- HSET task:{tid} -|------------------>|----------------->|
  |                   |   status=CANCELLED |                   |                   |
  |                   |   billing_state=   |                   |                   |
  |                   |   RELEASED         |                   |                   |
  |                   |                   |                   |                   |
  |<-- 200            |                   |                   |                   |
  |  {credits_refunded|                   |                   |                   |
  |   : cost}         |                   |                   |                   |
```

**DB calls: 1 TB + 1 PG read + 1 PG txn** (TB: void pending transfer. PG: SELECT task for validation + single transaction with UPDATE task_commands + INSERT outbox_events). Redis: 1 DECR + 1 HSET (non-blocking).

vs solution 2: no `UPDATE credit_reservations SET state=RELEASED` + `UPDATE users SET credits=credits+$1` -- TigerBeetle void handles the credit return atomically.

### Full pseudo-code

```python
async def cancel_task(jwt_claims, task_id):
    task = await cmd_db.fetchrow("""
        SELECT tb_pending_transfer_id, cost, billing_state
        FROM task_commands WHERE task_id=$1 AND user_id=$2
    """, task_id, jwt_claims.sub)

    if not task or task["billing_state"] != "RESERVED":
        raise HTTPException(409, "Not cancellable")

    # Void in TigerBeetle (credits auto-return)
    tb_client.create_transfers([
        Transfer(
            id=new_transfer_id(),
            pending_id=uuid_to_u128(task["tb_pending_transfer_id"]),
            flags=TransferFlags.VOID_PENDING_TRANSFER,
        )
    ])

    # Update command DB + outbox
    async with cmd_db.transaction():
        await cmd_db.execute("""
            UPDATE task_commands SET status='CANCELLED', billing_state='RELEASED', updated_at=now()
            WHERE task_id=$1
        """, task_id)
        await cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, 'tasks.cancelled', 'tasks.cancelled', $2)
        """, task_id, json.dumps({
            "task_id": str(task_id), "user_id": str(jwt_claims.sub),
            "billing_state": "RELEASED", "credits_refunded": task["cost"],
        }))

    await redis.decr(f"active:{jwt_claims.sub}")
    await redis.hset(f"task:{task_id}", mapping={"status": "CANCELLED", "billing_state": "RELEASED"})
    return {"credits_refunded": task["cost"]}
```

---

## 7. Admin credit topup (`POST /v1/admin/credits`)

```text
Admin               API                 TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |                   |
  |-- POST credits -->|                   |                   |                   |
  |  {api_key, amount,|                   |                   |                   |
  |   reason}         |                   |                   |                   |
  |                   |                   |                   |                   |
  |           [JWT auth: verify admin role]                    |                   |
  |                   |                   |                   |                   |
  |                   |  1. Lookup user by API key hash       |                   |
  |                   |-- SELECT user_id ->|------------------>|                   |
  |                   |   FROM api_keys    |                   |                   |
  |                   |   WHERE key_hash=$1|                   |                   |
  |                   |<-- user_id --------|<-----------------|                   |
  |                   |                   |                   |                   |
  |                   |  2. Direct transfer: platform revenue -> user             |
  |                   |-- create_transfer->|                   |                   |
  |                   |   (non-pending,    |                   |                   |
  |                   |    revenue -> user,|                   |                   |
  |                   |    code=101        |                   |                   |
  |                   |    admin_topup)    |                   |                   |
  |                   |<-- OK -------------|                   |                   |
  |                   |                   |                   |                   |
  |                   |  3. Audit + outbox |                   |                   |
  |                   |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   credit_          |                   |                   |
  |                   |   transactions --->|------------------>|                   |
  |                   |   (delta, reason)  |                   |                   |
  |                   |                   |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   outbox_events -->|------------------>|                   |
  |                   |   billing.topup    |                   |                   |
  |                   |                   |                   |                   |
  |                   |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  4. Read back balance from TB         |                   |
  |                   |-- lookup_accounts->|                   |                   |
  |                   |<-- {credits_posted,|                   |                   |
  |                   |     debits_posted, |                   |                   |
  |                   |     debits_pending}|                   |                   |
  |                   |                   |                   |                   |
  |<-- 200            |                   |                   |                   |
  |  {new_balance}    |                   |                   |                   |
```

**DB calls: 2 TB + 1 PG read + 1 PG txn** (TB: create transfer + lookup account. PG: SELECT api_keys + single transaction with INSERT credit_transactions + INSERT outbox_events).

### Full pseudo-code

```python
async def admin_topup(admin_jwt, target_api_key, amount, reason):
    verify_admin(admin_jwt)
    # Lookup user via hashed key (solutions 1+ use api_keys table with key_hash)
    key_hash = hashlib.sha256(target_api_key.encode()).hexdigest()
    user = await cmd_db.fetchrow(
        "SELECT user_id FROM api_keys WHERE key_hash=$1 AND is_active=true", key_hash)
    if not user:
        raise HTTPException(404, "API_KEY_NOT_FOUND")

    # Direct (non-pending) transfer: platform revenue -> user
    tb_client.create_transfers([
        Transfer(
            id=new_transfer_id(),
            debit_account_id=PLATFORM_REVENUE_ID,
            credit_account_id=uuid_to_u128(user["user_id"]),
            amount=amount,
            ledger=1,
            code=101,  # admin_topup
        )
    ])

    # Audit row (outbox for consistency)
    async with cmd_db.transaction():
        await cmd_db.execute(
            "INSERT INTO credit_transactions(user_id, delta, reason) VALUES($1,$2,$3)",
            user["user_id"], amount, reason)
        await cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, 'billing.topup', 'billing.topup', $2)
        """, user["user_id"], json.dumps({
            "user_id": str(user["user_id"]), "amount": amount, "reason": reason}))

    # Read back balance from TB
    account = tb_client.lookup_accounts([uuid_to_u128(user["user_id"])])[0]
    new_balance = account.credits_posted - account.debits_posted - account.debits_pending
    return {"new_balance": new_balance}
```

---

## 8. Projector: Redpanda -> query model

```text
Redpanda            Projector              PG (query+cmd)      Redis
  |                   |                      |                   |
  |  [consumer group: |                      |                   |
  |   "projector"]    |                      |                   |
  |                   |                      |                   |
  |-- deliver msg --->|                      |                   |
  |   topic=          |                      |                   |
  |   tasks.requested |                      |                   |
  |   (or .completed, |                      |                   |
  |    .failed,       |                      |                   |
  |    .cancelled)    |                      |                   |
  |                   |                      |                   |
  |                   |  1. Inbox dedup check|                   |
  |                   |-- SELECT event_id -->|                   |
  |                   |   FROM inbox_events  |                   |
  |                   |   WHERE event_id=$1  |                   |
  |                   |   AND consumer_name= |                   |
  |                   |   'projector'        |                   |
  |                   |<-- NULL (new) -------|                   |
  |                   |                      |                   |
  |                   |  [duplicate? -> skip, commit]            |
  |                   |                      |                   |
  |                   |  2. Projection transaction              |
  |                   |== BEGIN txn ========>|                   |
  |                   |                      |                   |
  |                   |  [tasks.requested:]  |                   |
  |                   |-- INSERT/UPSERT      |                   |
  |                   |   task_query_view -->|                   |
  |                   |   ON CONFLICT        |                   |
  |                   |   (task_id) DO UPDATE|                   |
  |                   |   status, billing_   |                   |
  |                   |   state,             |                   |
  |                   |   projection_version |                   |
  |                   |                      |                   |
  |                   |  [.completed/.failed/|                   |
  |                   |   .cancelled:]       |                   |
  |                   |-- UPDATE             |                   |
  |                   |   task_query_view -->|                   |
  |                   |   SET status,        |                   |
  |                   |   billing_state,     |                   |
  |                   |   result, error,     |                   |
  |                   |   projection_version |                   |
  |                   |                      |                   |
  |                   |  3. Inbox record     |                   |
  |                   |-- INSERT             |                   |
  |                   |   inbox_events ----->|                   |
  |                   |   (event_id,         |                   |
  |                   |    'projector')      |                   |
  |                   |                      |                   |
  |                   |  4. Checkpoint       |                   |
  |                   |-- UPSERT             |                   |
  |                   |   projection_        |                   |
  |                   |   checkpoints ------>|                   |
  |                   |   (topic, partition, |                   |
  |                   |    offset)           |                   |
  |                   |                      |                   |
  |                   |== COMMIT ===========>|                   |
  |                   |                      |                   |
  |                   |  5. Write-through    |                   |
  |                   |-- HSET task:{tid} -->|----------------->|
  |                   |   status, billing_   |                   |
  |                   |   state              |                   |
  |                   |-- EXPIRE 86400 ----->|----------------->|
  |                   |                      |                   |
  |<-- consumer.commit|                      |                   |
```

**DB calls: 1 PG read + 1 PG txn** (inbox dedup SELECT + single transaction with UPSERT task_query_view + INSERT inbox_events + UPSERT projection_checkpoints). Redis: 1 HSET + 1 EXPIRE (write-through).

The projector is a background process. It does not affect client-facing latency -- the worker already writes to Redis directly (flow 5), so clients see results immediately via poll. The projector updates the PG query view for cache-miss fallback and reporting queries.

### Full pseudo-code

```python
async def run_projector():
    """Consumes all task.* topics, updates query view + Redis cache.
    Rebuildable: reset offset to 0, truncate query view, replay."""
    consumer = Consumer({"group.id": "projector", "auto.offset.reset": "earliest"})
    consumer.subscribe(["tasks.requested", "tasks.completed", "tasks.failed", "tasks.cancelled"])

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue

        event = json.loads(msg.value())
        event_id = msg.headers().get("event_id")

        # Inbox dedup
        exists = await query_db.fetchval(
            "SELECT 1 FROM inbox_events WHERE event_id=$1 AND consumer_name='projector'", event_id)
        if exists:
            consumer.commit(msg)
            continue

        async with query_db.transaction():
            if msg.topic() == "tasks.requested":
                await query_db.execute("""
                    INSERT INTO task_query_view(task_id, user_id, tier, mode, model_class,
                                               status, billing_state, projection_version, created_at, updated_at)
                    VALUES($1,$2,$3,$4,$5,'PENDING','RESERVED',$6,now(),now())
                    ON CONFLICT (task_id) DO UPDATE SET status='PENDING', projection_version=$6, updated_at=now()
                """, event["task_id"], event["user_id"], event["tier"], event["mode"],
                     event["model_class"], msg.offset())
            else:
                await query_db.execute("""
                    UPDATE task_query_view SET status=$1, billing_state=$2, result=$3,
                                              error=$4, projection_version=$5, updated_at=now()
                    WHERE task_id=$6
                """, event["status"], event["billing_state"], json.dumps(event.get("result")),
                     event.get("error"), msg.offset(), event["task_id"])

            await query_db.execute(
                "INSERT INTO inbox_events(event_id, consumer_name) VALUES($1,'projector')", event_id)

            # Update checkpoint
            await query_db.execute("""
                INSERT INTO projection_checkpoints(projector_name, topic, partition_id, committed_offset, updated_at)
                VALUES('projector', $1, $2, $3, now())
                ON CONFLICT (projector_name) DO UPDATE SET committed_offset=$3, updated_at=now()
            """, msg.topic(), msg.partition(), msg.offset())

        # Write-through to Redis
        await redis.hset(f"task:{event['task_id']}", mapping={
            "status": event.get("status", "PENDING"),
            "billing_state": event.get("billing_state", "RESERVED"),
        })
        await redis.expire(f"task:{event['task_id']}", 86400)

        consumer.commit(msg)
```

---

## 9. Projection rebuild

Two paths to rebuild the query view -- Redpanda replay or direct SQL.

### Path A: Redpanda replay (preserves event ordering, exercises projector logic)

```text
Admin CLI            Projector (rebuild)    PG (query)          Redpanda            Redis
  |                   |                      |                   |                   |
  |-- trigger rebuild>|                      |                   |                   |
  |                   |                      |                   |                   |
  |                   |  1. Truncate stale state               |                   |
  |                   |-- TRUNCATE --------->|                   |                   |
  |                   |   task_query_view    |                   |                   |
  |                   |   inbox_events       |                   |                   |
  |                   |   projection_        |                   |                   |
  |                   |   checkpoints        |                   |                   |
  |                   |                      |                   |                   |
  |                   |-- FLUSHDB ---------->|------------------>|----------------->|
  |                   |                      |                   |                   |
  |                   |  2. Consume from offset 0              |                   |
  |                   |-- subscribe -------->|------------------>|<-- earliest -----|
  |                   |   [tasks.requested,  |                   |                   |
  |                   |    tasks.completed,  |                   |                   |
  |                   |    tasks.failed,     |                   |                   |
  |                   |    tasks.cancelled]  |                   |                   |
  |                   |                      |                   |                   |
  |                   |  [for each message:] |                   |                   |
  |                   |-- process_projection_|                   |                   |
  |                   |   event(msg) ------->|                   |                   |
  |                   |   (same logic as     |                   |                   |
  |                   |    flow 8 above)     |                   |                   |
  |                   |                      |                   |                   |
  |                   |  [poll returns None = end of log]       |                   |
  |                   |                      |                   |                   |
  |<-- {events_       |                      |                   |                   |
  |     replayed: N}  |                      |                   |                   |
```

**DB calls: 1 TRUNCATE + N projection transactions** (one per event replayed). Redpanda: N consumes.

### Path B: SQL rebuild from command table (faster, no Redpanda dependency)

```text
Admin CLI            PG (query + cmd)
  |                   |
  |-- TRUNCATE ------>|
  |   query.          |
  |   task_query_view |
  |                   |
  |-- INSERT INTO --->|
  |   query.          |
  |   task_query_view |
  |   SELECT ... FROM |
  |   cmd.            |
  |   task_commands   |
  |                   |
  |<-- rows inserted -|
```

**DB calls: 2 PG** (TRUNCATE + INSERT...SELECT). No Redpanda, no Redis.

Path A is useful when the projector logic includes transformations beyond simple column mapping. Path B is useful when you need the query view back fast and the command table is the source of truth -- which it always is.

### Full pseudo-code (Path A)

```python
async def rebuild_from_events():
    """Rebuild query view by replaying Redpanda log from offset 0."""
    await query_db.execute("TRUNCATE task_query_view, inbox_events, projection_checkpoints")
    await redis.flushdb()

    consumer = Consumer({
        "group.id": "projector-rebuild",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe(["tasks.requested", "tasks.completed", "tasks.failed", "tasks.cancelled"])

    count = 0
    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            break
        await process_projection_event(msg)
        count += 1
    return {"events_replayed": count}
```

### Full pseudo-code (Path B)

```sql
TRUNCATE query.task_query_view;
INSERT INTO query.task_query_view
    (task_id, user_id, tier, mode, model_class, status, billing_state,
     result, error, created_at, updated_at)
SELECT task_id, user_id, tier, mode, model_class, status, billing_state,
       NULL, NULL, created_at, updated_at
FROM cmd.task_commands;
```

---

## 10. Webhook delivery via Redpanda

```text
Redpanda            Webhook Worker         PG (cmd)            External Service
  |                   |                      |                   |
  |  [consumer group: |                      |                   |
  |   "webhook-worker"|                      |                   |
  |   offset: latest] |                      |                   |
  |                   |                      |                   |
  |-- deliver msg --->|                      |                   |
  |   topic=          |                      |                   |
  |   tasks.completed |                      |                   |
  |   (or .failed)    |                      |                   |
  |                   |                      |                   |
  |                   |  1. Lookup callback  |                   |
  |                   |-- SELECT callback_url|                   |
  |                   |   FROM task_commands>|                   |
  |                   |<-- url or NULL ------|                   |
  |                   |                      |                   |
  |                   |  [no callback_url? -> skip, commit]      |
  |                   |                      |                   |
  |                   |  2. Deliver webhook (retry up to 3x)    |
  |                   |-- POST callback_url -|------------------>|
  |                   |   {task_id, status,  |                   |
  |                   |    result,           |                   |
  |                   |    billing_state}    |                   |
  |                   |                      |                   |
  |                   |  [attempt 1]         |                   |
  |                   |<-- 200 OK / 5xx -----|<-----------------|
  |                   |                      |                   |
  |                   |  [5xx or timeout?    |                   |
  |                   |   sleep 2^attempt,   |                   |
  |                   |   retry]             |                   |
  |                   |                      |                   |
  |                   |  [attempt 2]         |                   |
  |                   |-- POST callback_url -|------------------>|
  |                   |<-- 200 OK -----------|<-----------------|
  |                   |                      |                   |
  |<-- consumer.commit|                      |                   |
```

**DB calls: 1 PG read** (SELECT callback_url from task_commands). Redpanda: 1 consume + 1 commit. HTTP: 1-3 POST attempts.

Key difference from solution 2: webhook delivery consumes from Redpanda (independent consumer group) instead of RabbitMQ. If the webhook worker is down, Redpanda retains events -- the worker catches up from last committed offset when it restarts. No message loss.

### Full pseudo-code

```python
async def run_webhook_worker():
    """Consumes tasks.completed and tasks.failed, delivers webhooks for tasks with callback_url.
    Replaces RabbitMQ webhook exchange from solution 2."""
    consumer = Consumer({"group.id": "webhook-worker", "auto.offset.reset": "latest"})
    consumer.subscribe(["tasks.completed", "tasks.failed"])

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        event = json.loads(msg.value())
        task = await cmd_db.fetchrow(
            "SELECT callback_url FROM task_commands WHERE task_id=$1", event["task_id"])
        if task and task["callback_url"]:
            payload = {
                "task_id": event["task_id"], "status": event["status"],
                "result": event.get("result"), "billing_state": event["billing_state"],
            }
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.post(task["callback_url"], json=payload)
                        if resp.status_code < 500:
                            break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(2 ** attempt)
        consumer.commit(msg)
```

---

## 11. Batch submission with TigerBeetle (`POST /v1/task/batch`)

```text
Client              API                 TigerBeetle         PG (cmd)            Redis
  |                   |                   |                   |                   |
  |-- POST batch ---->|                   |                   |                   |
  |  {tasks: [        |                   |                   |                   |
  |   {x:1,y:2},      |                   |                   |                   |
  |   {x:3,y:4},      |                   |                   |                   |
  |   ...]}            |                   |                   |                   |
  |  (max 100)         |                   |                   |                   |
  |                   |                   |                   |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT]             |                   |
  |                   |                   |                   |                   |
  |                   |  1. Batch create pending transfers     |                   |
  |                   |-- create_transfers>|                   |                   |
  |                   |   [N pending       |                   |                   |
  |                   |    transfers,      |                   |                   |
  |                   |    user->escrow,   |                   |                   |
  |                   |    one per task]   |                   |                   |
  |                   |<-- results[] ------|                   |                   |
  |                   |                   |                   |                   |
  |                   |  [any failures?]   |                   |                   |
  |                   |  -> void successes |                   |                   |
  |                   |  -> 402 or 500     |                   |                   |
  |                   |                   |                   |                   |
  |                   |  2. PG transaction: all commands + outbox events           |
  |                   |== BEGIN txn =====>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  [for each task:]  |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   task_commands -->|------------------>|                   |
  |                   |   (task_id,        |                   |                   |
  |                   |    mode='batch',   |                   |                   |
  |                   |    billing_state=  |                   |                   |
  |                   |    'RESERVED')     |                   |                   |
  |                   |                   |                   |                   |
  |                   |-- INSERT           |                   |                   |
  |                   |   outbox_events -->|------------------>|                   |
  |                   |   tasks.requested  |                   |                   |
  |                   |   (per task)       |                   |                   |
  |                   |                   |                   |                   |
  |                   |== COMMIT ========>|==================>|                   |
  |                   |                   |                   |                   |
  |                   |  3. Update Redis  |                   |                   |
  |                   |-- INCR active:{uid}|-- (by N) -------->|----------------->|
  |                   |                   |                   |                   |
  |<-- 201            |                   |                   |                   |
  |  {batch_id,        |                   |                   |                   |
  |   task_ids: [...], |                   |                   |                   |
  |   total_cost}      |                   |                   |                   |
```

**DB calls: 1-2 TB + 1 PG txn** (TB: batch create_transfers, possibly + batch void on partial failure. PG: single transaction with N INSERT task_commands + N INSERT outbox_events). Redis: 1 INCR.

TigerBeetle handles batch transfer creation atomically. If any transfer fails (e.g., insufficient credits partway through), successful transfers are voided and the entire batch is rejected.

### Full pseudo-code

```python
async def submit_batch(jwt_claims, batch_payload, idem_key):
    """Each task gets its own pending transfer. TigerBeetle supports batch transfer creation."""
    if len(batch_payload.tasks) > 100:
        raise HTTPException(400, "Max 100 tasks per batch")

    tier = jwt_claims.tier
    tasks = []
    transfers = []
    total_cost = 0

    for item in batch_payload.tasks:
        model = item.get("model_class", "small")
        cost = compute_cost(model, tier)
        task_id = uuid7()
        transfer_id = uuid7()
        total_cost += cost
        tasks.append({"task_id": task_id, "transfer_id": transfer_id, "cost": cost,
                       "model": model, "x": item["x"], "y": item["y"]})
        transfers.append(Transfer(
            id=uuid_to_u128(transfer_id),
            debit_account_id=uuid_to_u128(jwt_claims.sub),
            credit_account_id=ESCROW_ID,
            amount=cost, ledger=1, code=200,
            flags=TransferFlags.PENDING, timeout=600,
        ))

    # Batch create all pending transfers (TB handles this atomically)
    results = tb_client.create_transfers(transfers)
    # Check for any failures (all-or-nothing: void successes if any fail)
    failed = [r for r in results if r.result != CreateTransferResult.OK]
    if failed:
        # Void any successful transfers
        void_transfers = [
            Transfer(id=new_transfer_id(), pending_id=t.id,
                     flags=TransferFlags.VOID_PENDING_TRANSFER)
            for t, r in zip(transfers, results) if r.result == CreateTransferResult.OK
        ]
        if void_transfers:
            tb_client.create_transfers(void_transfers)
        if any(r.result == CreateTransferResult.EXCEEDS_CREDITS for r in failed):
            raise HTTPException(402, "INSUFFICIENT_CREDITS")
        raise HTTPException(500, "TB batch transfer failed")

    # Persist all commands + outbox events in one PG transaction
    batch_id = uuid7()
    async with cmd_db.transaction():
        for t in tasks:
            await cmd_db.execute("""
                INSERT INTO task_commands(task_id, user_id, tier, mode, model_class,
                    x, y, cost, tb_pending_transfer_id, billing_state, idempotency_key)
                VALUES($1,$2,$3,'batch',$4,$5,$6,$7,$8,'RESERVED',$9)
            """, t["task_id"], jwt_claims.sub, tier, t["model"], t["x"], t["y"],
                 t["cost"], t["transfer_id"], f"{idem_key}:{t['task_id']}" if idem_key else None)
            await cmd_db.execute("""
                INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
                VALUES($1, 'task.requested', 'tasks.requested', $2)
            """, t["task_id"], json.dumps({
                "task_id": str(t["task_id"]), "user_id": str(jwt_claims.sub),
                "tier": tier, "mode": "batch", "model_class": t["model"],
                "x": t["x"], "y": t["y"], "cost": t["cost"],
                "tb_transfer_id": str(t["transfer_id"]),
            }))

    await redis.incr(f"active:{jwt_claims.sub}", len(tasks))
    return {"batch_id": str(batch_id), "task_ids": [str(t["task_id"]) for t in tasks],
            "total_cost": total_cost}
```

---

## 12. Reconciler: stale pending detection (replaces watchdog)

```text
PG (cmd)            Reconciler             TigerBeetle         Redis
  |                   |                      |                   |
  |  [tick every ~30s]|                      |                   |
  |                   |                      |                   |
  |  1. Find stale RESERVED tasks (older than 12 min)          |
  |<-- SELECT --------|                      |                   |
  |   task_id,         |                      |                   |
  |   tb_pending_      |                      |                   |
  |   transfer_id,     |                      |                   |
  |   user_id, cost    |                      |                   |
  |   FROM task_commands                      |                   |
  |   WHERE billing_   |                      |                   |
  |   state='RESERVED' |                      |                   |
  |   AND created_at < |                      |                   |
  |   now() - 12min    |                      |                   |
  |--- stale rows[] ->|                      |                   |
  |                   |                      |                   |
  |  [for each stale row:]                   |                   |
  |                   |                      |                   |
  |                   |  2. Look up transfer in TB              |
  |                   |-- lookup_transfers ->|                   |
  |                   |<-- transfer or NULL -|                   |
  |                   |                      |                   |
  |                   |  [NULL = transfer expired (auto-voided by TB timeout)]     |
  |                   |                      |                   |
  |<-- UPDATE ---------|                      |                   |
  |   task_commands    |                      |                   |
  |   status=EXPIRED   |                      |                   |
  |   billing_state=   |                      |                   |
  |   EXPIRED          |                      |                   |
  |                   |                      |                   |
  |                   |-- HSET task:{tid} -->|------------------>|
  |                   |   status=EXPIRED     |                   |
  |                   |   billing_state=     |                   |
  |                   |   EXPIRED            |                   |
  |                   |                      |                   |
  |<-- INSERT ---------|                      |                   |
  |   billing_         |                      |                   |
  |   reconcile_jobs   |                      |                   |
  |   (RESOLVED,       |                      |                   |
  |    TB_AUTO_EXPIRED)|                      |                   |
  |                   |                      |                   |
  |                   |  [transfer found + VOIDED flag?]        |
  |                   |                      |                   |
  |<-- UPDATE ---------|                      |                   |
  |   task_commands    |                      |                   |
  |   billing_state=   |                      |                   |
  |   RELEASED         |                      |                   |
  |                   |                      |                   |
  |<-- INSERT ---------|                      |                   |
  |   billing_         |                      |                   |
  |   reconcile_jobs   |                      |                   |
  |   (RESOLVED,       |                      |                   |
  |    TB_VOIDED)      |                      |                   |
```

**DB calls per stale row: 1 TB + 2-3 PG** (TB: lookup_transfers. PG: UPDATE task_commands + INSERT billing_reconcile_jobs + optional HSET). Redis: 0-1 HSET.

Key difference from solution 2 watchdog: the reconciler does not refund credits -- TigerBeetle's pending transfer timeout (600s) auto-voids the debit. The reconciler only aligns the command DB with the TB-known transfer state.

### Full pseudo-code

```python
async def reconcile_stale_pendings():
    """Finds task_commands with billing_state=RESERVED older than 12 min.
    Checks TigerBeetle transfer status. Updates command DB if voided/expired."""
    stale = await cmd_db.fetch("""
        SELECT task_id, tb_pending_transfer_id, user_id, cost
        FROM task_commands
        WHERE billing_state = 'RESERVED'
          AND created_at < now() - interval '12 minutes'
    """)

    for row in stale:
        # Look up transfer in TigerBeetle
        transfers = tb_client.lookup_transfers([uuid_to_u128(row["tb_pending_transfer_id"])])
        if not transfers:
            # Transfer expired (auto-voided by TB timeout)
            await cmd_db.execute("""
                UPDATE task_commands SET status='EXPIRED', billing_state='EXPIRED', updated_at=now()
                WHERE task_id=$1 AND billing_state='RESERVED'
            """, row["task_id"])
            await redis.hset(f"task:{row['task_id']}", mapping={
                "status": "EXPIRED", "billing_state": "EXPIRED"})
            # Log reconciliation
            await cmd_db.execute("""
                INSERT INTO billing_reconcile_jobs(task_id, tb_pending_transfer_id, state, resolution)
                VALUES($1, $2, 'RESOLVED', 'TB_AUTO_EXPIRED')
            """, row["task_id"], row["tb_pending_transfer_id"])
        else:
            transfer = transfers[0]
            if transfer.flags & TransferFlags.VOIDED:
                await cmd_db.execute("""
                    UPDATE task_commands SET billing_state='RELEASED', updated_at=now()
                    WHERE task_id=$1 AND billing_state='RESERVED'
                """, row["task_id"])
                await cmd_db.execute("""
                    INSERT INTO billing_reconcile_jobs(task_id, tb_pending_transfer_id, state, resolution)
                    VALUES($1, $2, 'RESOLVED', 'TB_VOIDED')
                """, row["task_id"], row["tb_pending_transfer_id"])
```

---

## 13. Result expiry (24h TTL)

The reconciler also expires completed task results.

```text
PG (cmd + query)    Reconciler
  |                   |
  |  [tick with reconciler or separate cron]
  |                   |
  |<-- UPDATE ---------|
  |   cmd.task_commands|
  |   SET status=      |
  |   'EXPIRED'        |
  |   WHERE status IN  |
  |   ('COMPLETED',    |
  |    'FAILED')       |
  |   AND updated_at < |
  |   now() - 24h      |
  |                   |
  |<-- UPDATE ---------|
  |   query.           |
  |   task_query_view  |
  |   SET status=      |
  |   'EXPIRED'        |
  |   WHERE status IN  |
  |   ('COMPLETED',    |
  |    'FAILED')       |
  |   AND updated_at < |
  |   now() - 24h      |
```

**DB calls: 2 PG** (bulk UPDATE on cmd.task_commands + bulk UPDATE on query.task_query_view). No TB interaction (transfers are already posted/voided). Redis entries expire naturally via 24h TTL set at write time.

### Full pseudo-code

```python
async def expire_results():
    """Transition completed/failed tasks older than 24h to EXPIRED."""
    await db.execute("""
        UPDATE cmd.task_commands SET status='EXPIRED', updated_at=now()
        WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < now() - interval '24 hours'
    """)
    await db.execute("""
        UPDATE query.task_query_view SET status='EXPIRED', updated_at=now()
        WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < now() - interval '24 hours'
    """)
```

---

## 14. Demo script scenario

An example script (`demo.sh` / `demo.py`) demonstrating the hot/cold model-affinity routing benefit.

```text
Step  Action                          Expected behavior                           Timing
----  ------------------------------  ------------------------------------------  ---------
 1    Submit 5 small tasks rapidly    Worker A cold-starts small                  first: 5s
                                      (3s load + 2s inference)                    (3+2)
                                      Remaining 4 handled warm                    each: 2s

 2    Submit 3 large tasks            Worker B cold-starts large                  first: 9s
                                      (3s load + 6s inference)                    (3+6)
                                      Remaining 2 handled warm                    each: 6s

 3    Submit 1 medium task            No warm worker for medium ->                7s
                                      coldstart exchange -> Worker C              (3+4)
                                      loads medium

 4    Submit 3 more small tasks       Routed to Worker A via preloaded            each: 2s
                                      exchange -> still warm -> no cold start

 5    Kill projector, rebuild         Replay from Redpanda offset 0              ~seconds
      from offset 0

 6    Show ClickHouse                 Latency by model_class,                    dashboard
                                      cold-start rate over time
```

Without hot/cold routing (round-robin baseline), the same workload would have ~5 more cold starts, adding ~15s of wasted model loading.

---

## 15. Event exporter: Redpanda -> ClickHouse (optional profile)

```text
Redpanda            Event Exporter         ClickHouse
  |                   |                      |
  |  [consumer group: |                      |
  |   "clickhouse-    |                      |
  |    exporter"]     |                      |
  |                   |                      |
  |-- deliver msg --->|                      |
  |   (any topic:     |                      |
  |    tasks.*,        |                      |
  |    billing.*)      |                      |
  |                   |                      |
  |  [accumulate      |                      |
  |   batch of up to  |                      |
  |   1000 messages   |                      |
  |   or 5s timeout]  |                      |
  |                   |                      |
  |                   |  [batch ready]       |
  |                   |-- INSERT INTO ------>|
  |                   |   events             |
  |                   |   (event_id,         |
  |                   |    event_type,       |
  |                   |    task_id, user_id, |
  |                   |    tier, cost, ts)   |
  |                   |   [batch of N rows]  |
  |                   |<-- OK ---------------|
  |                   |                      |
  |<-- consumer.commit|                      |
  |   (last msg in    |                      |
  |    batch)         |                      |
```

**DB calls: 0 PG, 0 TB**. Redpanda: N consumes. ClickHouse: 1 batch INSERT per flush.

Independent consumer group -- does not affect projections, dispatcher, or webhook delivery. Runs only with: `docker compose --profile analytics up`.

### Full pseudo-code

```python
async def export_to_clickhouse():
    """Consumes all topics, inserts into ClickHouse for OLAP queries.
    Independent consumer group - does not affect projections.
    Runs only with: docker compose --profile analytics up"""
    consumer = Consumer({"group.id": "clickhouse-exporter", "auto.offset.reset": "earliest"})
    consumer.subscribe(["tasks.requested", "tasks.completed", "tasks.failed",
                        "tasks.cancelled", "billing.captured", "billing.released"])
    batch = []
    while True:
        msg = consumer.poll(0.1)
        if msg:
            batch.append(msg)
        if len(batch) >= 1000 or (batch and time.time() - batch_start > 5):
            clickhouse.execute(
                "INSERT INTO events (event_id, event_type, task_id, user_id, tier, cost, ts) VALUES",
                [(e.headers()["event_id"], e.topic(), ...) for e in batch])
            consumer.commit(batch[-1])
            batch = []
```

---

## DB call summary table

| Path                          | TB calls | PG calls (happy) | Redis calls | Redpanda / RabbitMQ | Notes                                                                     |
| ----------------------------- | :------: | :--------------: | :---------: | :-----------------: | ------------------------------------------------------------------------- |
| Submit (`POST /v1/task`)      |    1     |        1         |      3      |         0           | TB pending transfer + PG txn (idem + command + outbox). Redis: GET+INCR+HSET. |
| Poll (`GET /v1/poll`)         |    0     |      0 / 1       |      1      |         0           | 0 PG on Redis hit, 1 on miss (query view fallback).                        |
| Cancel (`POST /v1/task/{id}`) |    1     |      1 + 1       |      2      |         0           | TB void + PG read + PG txn. Redis: DECR + HSET.                          |
| Worker completion (success)   |    1     |        1         |      2      |       1 RMQ         | TB post + PG txn (update + outbox). Redis: DECR + HSET.                   |
| Worker completion (failure)   |    1     |        1         |      2      |       1 RMQ         | TB void + PG txn (update + outbox). Redis: DECR + HSET.                   |
| Admin topup                   |    2     |      1 + 1       |      0      |         0           | TB transfer + lookup. PG read + PG txn (audit + outbox).                  |
| Batch submit (N tasks)        |   1-2    |        1         |      1      |         0           | TB batch transfers (+ void on failure). PG: 1 txn (N*2 inserts).          |
| Outbox relay (per batch)      |    0     |        2         |      0      |      1 Redpanda     | SELECT unpublished + UPDATE published_at. Redpanda produce batch.         |
| Dispatcher (per event)        |    0     |        0         |      0      |    1 RP + 1 RMQ     | Redpanda consume -> RabbitMQ publish. Zero DB.                            |
| Projector (per event)         |    0     |      1 + 1       |      2      |      1 Redpanda     | Inbox dedup + PG txn (upsert + inbox + checkpoint). Redis HSET + EXPIRE.  |
| Webhook (per event)           |    0     |        1         |      0      |      1 Redpanda     | PG read (callback_url). HTTP POST to external.                            |
| Reconciler (per stale row)    |    1     |       2-3        |     0-1     |         0           | TB lookup + PG updates + PG insert reconcile job.                         |
| Result expiry (bulk)          |    0     |        2         |      0      |         0           | Bulk UPDATE cmd + query tables.                                           |
| Event exporter (per batch)    |    0     |        0         |      0      |      1 Redpanda     | Redpanda consume -> ClickHouse batch INSERT.                              |

### Comparison vs solutions 1-2

| Path   | Sol 0 (Celery) | Sol 1 (Redis-native) | Sol 2 (CQRS+outbox) | Sol 3 (TB+Redpanda) |
| ------ | :------------: | :------------------: | :-----------------: | :-----------------: |
| Submit |       1        |          1           |          1          |     1 PG + 1 TB     |
| Poll   |     0 / 1      |        0 / 1         |        0 / 1        |       0 / 1         |
| Cancel |       1        |          2           |          1          |     2 PG + 1 TB     |
| Worker |       2        |          3           |          1          |     1 PG + 1 TB     |

Solution 3 adds TigerBeetle calls but removes all app-coordinated billing SQL (`UPDATE users SET credits=credits-$1`, `UPDATE credit_reservations SET state=CAPTURED`). The billing invariant is enforced by TigerBeetle's state machine, not application code. PG call counts remain the same or improve (worker path: 1 PG txn vs solution 2's 1 PG txn, but no credit reservation table ops inside it).
