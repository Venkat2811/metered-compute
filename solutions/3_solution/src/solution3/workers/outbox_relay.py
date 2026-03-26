from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import asyncpg

from solution3.db.repository import fetch_unpublished_outbox_events, mark_outbox_events_published
from solution3.workers._bootstrap_worker import run_worker


class RelayProducer(Protocol):
    def produce(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: Mapping[str, str],
    ) -> None: ...

    def flush(self) -> None: ...


async def relay_once(
    *,
    db_pool: asyncpg.Pool,
    producer: RelayProducer,
    batch_size: int = 100,
) -> int:
    events = await fetch_unpublished_outbox_events(db_pool, limit=batch_size)
    if not events:
        return 0

    for event in events:
        producer.produce(
            topic=event.topic,
            key=str(event.event_id).encode("utf-8"),
            value=event.payload.encode("utf-8"),
            headers={"event_id": str(event.event_id)},
        )

    producer.flush()
    await mark_outbox_events_published(db_pool, event_ids=[event.event_id for event in events])
    return len(events)


def main() -> None:
    run_worker(name="solution3_outbox_relay")


if __name__ == "__main__":
    main()
