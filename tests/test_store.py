from __future__ import annotations

import asyncio

from backend.store import AlarmStore
from tests.conftest import make_record


def test_capacity_evicts_oldest_first() -> None:
    store = AlarmStore(capacity=3)
    for index in range(5):
        store.upsert(make_record(event_id=f"evt_{index}"))
    retained = {record.event_id for record in store.snapshot()}
    assert retained == {"evt_2", "evt_3", "evt_4"}


def test_board_ranks_open_criticals_first() -> None:
    store = AlarmStore(capacity=10)
    store.upsert(make_record(event_id="evt_info", type="motion_detected", confidence=0.4))
    store.upsert(make_record(event_id="evt_crit", type="fire_alarm"))
    resolved = make_record(event_id="evt_done", type="fire_alarm").resolved("op")
    store.upsert(resolved)

    order = [record.event_id for record in store.snapshot()]
    assert order[0] == "evt_crit"
    assert order[-1] == "evt_done", "resolved alarms sink to the bottom"


def test_acknowledge_is_idempotent() -> None:
    store = AlarmStore(capacity=10)
    store.upsert(make_record(event_id="evt_1"))
    assert store.acknowledge("evt_1", "op").status == "acknowledged"
    assert store.acknowledge("evt_1", "op") is None


def test_resolve_records_an_implicit_acknowledgement() -> None:
    store = AlarmStore(capacity=10)
    store.upsert(make_record(event_id="evt_1"))
    resolved = store.resolve("evt_1", "erven")
    assert resolved.status == "resolved"
    assert resolved.acknowledged_by == "erven"


def test_actions_on_missing_alarms_return_none() -> None:
    store = AlarmStore(capacity=10)
    assert store.acknowledge("nope", "op") is None
    assert store.resolve("nope", "op") is None


def test_updates_do_not_mutate_handed_out_records() -> None:
    store = AlarmStore(capacity=10)
    original = store.upsert(make_record(event_id="evt_1"))
    store.resolve("evt_1", "op")
    assert original.status == "active", "an SSE subscriber's copy must not change"


async def test_subscribers_receive_every_update() -> None:
    store = AlarmStore(capacity=10)
    received: list[str] = []

    async def listen() -> None:
        async for update in store.subscribe():
            received.append(update.record.event_id)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    for index in range(3):
        store.upsert(make_record(event_id=f"evt_{index}"))
    await asyncio.sleep(0.05)
    task.cancel()

    assert received == ["evt_0", "evt_1", "evt_2"]


async def test_a_slow_subscriber_cannot_stall_the_pipeline() -> None:
    store = AlarmStore(capacity=5000)

    async def never_drains() -> None:
        async for _ in store.subscribe():
            await asyncio.sleep(3600)

    task = asyncio.create_task(never_drains())
    await asyncio.sleep(0)

    # Far more than the subscriber buffer; publishing must stay non-blocking.
    for index in range(2000):
        store.upsert(make_record(event_id=f"evt_{index}"))

    assert len(store.snapshot()) == 2000
    task.cancel()


async def test_subscribers_are_released_on_disconnect() -> None:
    store = AlarmStore(capacity=10)

    async def listen() -> None:
        async for _ in store.subscribe():
            pass

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    store.upsert(make_record())
    await asyncio.sleep(0.01)
    assert store.subscriber_count == 1

    task.cancel()
    await asyncio.sleep(0.01)
    assert store.subscriber_count == 0
