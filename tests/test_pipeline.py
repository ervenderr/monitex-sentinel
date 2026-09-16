"""The load-shedding contract: critical alarms are never the ones we drop."""

from __future__ import annotations

import asyncio

from backend.pipeline import EventPipeline
from tests.conftest import make_record


async def _fill(pipeline: EventPipeline, count: int) -> None:
    for index in range(count):
        outcome = await pipeline.submit(
            make_record(event_id=f"evt_fill{index}"), protected=False
        )
        assert outcome.accepted


async def test_accepts_without_waiting_under_normal_load(pipeline: EventPipeline) -> None:
    outcome = await pipeline.submit(make_record(), protected=False)
    assert outcome.accepted
    assert outcome.waited_ms == 0.0
    assert pipeline.depth() == 1


async def test_sheds_low_priority_only_after_the_backpressure_window(
    pipeline: EventPipeline,
) -> None:
    await _fill(pipeline, 8)
    outcome = await pipeline.submit(make_record(event_id="evt_extra"), protected=False)
    assert outcome.shed is True
    # It waited for room before giving up, rather than dropping instantly.
    assert outcome.waited_ms >= 45


async def test_protected_events_are_never_shed(pipeline: EventPipeline) -> None:
    await _fill(pipeline, 8)
    task = asyncio.create_task(
        pipeline.submit(make_record(event_id="evt_critical", type="fire_alarm"), protected=True)
    )
    await asyncio.sleep(0.15)  # well past the shed timeout
    assert not task.done(), "a protected alarm must keep waiting, not shed"

    await pipeline.get()  # a worker frees one slot
    outcome = await task
    assert outcome.accepted and not outcome.shed


async def test_depth_tracks_the_backlog(pipeline: EventPipeline) -> None:
    await _fill(pipeline, 5)
    assert pipeline.depth() == 5
    await pipeline.get()
    assert pipeline.depth() == 4


async def test_rejects_nonsense_capacity() -> None:
    try:
        EventPipeline(maxsize=0, backpressure_timeout_s=1.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-positive maxsize")
