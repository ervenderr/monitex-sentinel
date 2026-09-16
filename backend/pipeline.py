"""The bounded work queue between ingest and triage.

The brief asks for ingestion that neither blocks, drops, nor crashes under
burst. Those goals conflict at the extreme, so the policy is explicit and
tiered:

1. **Normal load** - the queue has room, `put_nowait` returns immediately and
   the ingest loop never stalls.
2. **Burst** - the queue is full, so we wait up to `backpressure_timeout`. The
   WebSocket read loop stops draining, the TCP window closes, and the producer
   feels real backpressure. Nothing is lost; the system just breathes.
3. **Sustained overload** - if even that wait times out, we shed the event and
   count it, because an unbounded queue would only trade a visible drop for an
   invisible OOM. Critical alarms are exempt: they wait as long as it takes.

The shed counter is surfaced on the dashboard. A system that quietly loses
events is worse than one that says so.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

from backend.models import AlarmRecord


@dataclass(frozen=True)
class SubmitOutcome:
    accepted: bool
    waited_ms: float
    shed: bool


class EventPipeline:
    def __init__(self, *, maxsize: int, backpressure_timeout_s: float) -> None:
        if maxsize <= 0:
            raise ValueError("pipeline maxsize must be positive")
        self._queue: asyncio.Queue[AlarmRecord] = asyncio.Queue(maxsize=maxsize)
        self._backpressure_timeout_s = backpressure_timeout_s
        self._capacity = maxsize

    @property
    def capacity(self) -> int:
        return self._capacity

    def depth(self) -> int:
        return self._queue.qsize()

    async def submit(self, record: AlarmRecord, *, protected: bool) -> SubmitOutcome:
        """Enqueue a record for LLM triage under the tiered policy above."""
        try:
            self._queue.put_nowait(record)
            return SubmitOutcome(accepted=True, waited_ms=0.0, shed=False)
        except asyncio.QueueFull:
            pass

        started = perf_counter()

        if protected:
            # A critical alarm is never shed. Waiting here is the backpressure.
            await self._queue.put(record)
            return SubmitOutcome(
                accepted=True, waited_ms=(perf_counter() - started) * 1000, shed=False
            )

        try:
            await asyncio.wait_for(
                self._queue.put(record), timeout=self._backpressure_timeout_s
            )
        except TimeoutError:
            return SubmitOutcome(
                accepted=False, waited_ms=(perf_counter() - started) * 1000, shed=True
            )
        return SubmitOutcome(
            accepted=True, waited_ms=(perf_counter() - started) * 1000, shed=False
        )

    async def get(self) -> AlarmRecord:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def drain(self) -> None:
        await self._queue.join()
