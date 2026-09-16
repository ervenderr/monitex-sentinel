"""In-memory alarm store with live fan-out to dashboard subscribers.

Capacity-bounded on purpose: this is a monitoring surface, not a system of
record. Phase 5 adds SQLite persistence behind the same interface.

Fan-out rule: a slow dashboard must never slow the pipeline. Each subscriber
gets its own bounded queue; if a client stops draining, we evict its oldest
pending message and mark it lagged so it can re-request a snapshot. The
pipeline itself never awaits a subscriber.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.models import AlarmRecord

SUBSCRIBER_BUFFER = 256


class StoreUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["upsert"] = "upsert"
    record: AlarmRecord
    lagged: bool = False


class Subscription:
    """A registered listener. Async-iterable, and released on close."""

    def __init__(self, store: AlarmStore, queue: asyncio.Queue[StoreUpdate]) -> None:
        self._store = store
        self._queue = queue

    def __aiter__(self) -> AsyncIterator[StoreUpdate]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StoreUpdate]:
        try:
            while True:
                yield await self._store._next_update(self._queue)
        finally:
            self.close()

    def close(self) -> None:
        self._store._unsubscribe(self._queue)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.close()


class AlarmStore:
    def __init__(self, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("store capacity must be positive")
        self._capacity = capacity
        self._records: OrderedDict[str, AlarmRecord] = OrderedDict()
        self._subscribers: set[asyncio.Queue[StoreUpdate]] = set()
        self._lagged: set[int] = set()

    # ---- reads -------------------------------------------------------------

    def get(self, event_id: str) -> AlarmRecord | None:
        return self._records.get(event_id)

    def snapshot(self) -> list[AlarmRecord]:
        """Every retained alarm, ranked the way the operator board shows them."""
        return sorted(self._records.values(), key=lambda r: r.sort_key, reverse=True)

    def recent_for_site(self, site_id: str, limit: int = 50) -> list[AlarmRecord]:
        matches = [r for r in self._records.values() if r.event.site_id == site_id]
        return matches[-limit:]

    # ---- writes ------------------------------------------------------------

    def upsert(self, record: AlarmRecord) -> AlarmRecord:
        self._records[record.event_id] = record
        self._records.move_to_end(record.event_id)
        while len(self._records) > self._capacity:
            self._records.popitem(last=False)
        self._publish(StoreUpdate(record=record))
        return record

    def acknowledge(self, event_id: str, by: str) -> AlarmRecord | None:
        existing = self._records.get(event_id)
        if existing is None or existing.status != "active":
            return None
        return self.upsert(existing.acknowledged(by))

    def resolve(self, event_id: str, by: str) -> AlarmRecord | None:
        existing = self._records.get(event_id)
        if existing is None or existing.status == "resolved":
            return None
        return self.upsert(existing.resolved(by))

    # ---- fan-out -----------------------------------------------------------

    def _publish(self, update: StoreUpdate) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(update)
            except asyncio.QueueFull:
                # Slow client: drop its oldest message, never block the pipeline.
                self._lagged.add(id(queue))
                try:
                    queue.get_nowait()
                    queue.put_nowait(update)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self) -> Subscription:
        """Register a live subscriber.

        Deliberately a plain method, not an async generator: registration has to
        happen *synchronously* at the call site. An async generator body does
        not run until its first `__anext__`, which would leave a window where a
        caller believes it is subscribed but is not yet receiving anything.
        """
        queue: asyncio.Queue[StoreUpdate] = asyncio.Queue(maxsize=SUBSCRIBER_BUFFER)
        self._subscribers.add(queue)
        return Subscription(self, queue)

    def _unsubscribe(self, queue: asyncio.Queue[StoreUpdate]) -> None:
        self._subscribers.discard(queue)
        self._lagged.discard(id(queue))

    async def _next_update(self, queue: asyncio.Queue[StoreUpdate]) -> StoreUpdate:
        update = await queue.get()
        if id(queue) in self._lagged:
            self._lagged.discard(id(queue))
            return update.model_copy(update={"lagged": True})
        return update

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
