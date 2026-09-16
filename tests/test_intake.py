"""Intake is the shared door for sensor and camera events alike."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.intake import EventIntake
from backend.metrics import Metrics
from backend.pipeline import EventPipeline
from backend.store import AlarmStore


@pytest.fixture
def intake(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics, correlation
) -> EventIntake:
    return EventIntake(
        pipeline=pipeline, store=store, metrics=metrics, correlation=correlation
    )


async def test_normal_event_is_visible_before_the_llm_runs(
    intake: EventIntake, store: AlarmStore, pipeline: EventPipeline
) -> None:
    record = await intake.accept({"type": "perimeter_breach", "confidence": 0.7})
    assert record is not None
    # On the operator board immediately...
    assert store.get(record.event_id) is not None
    assert store.get(record.event_id).triage_state == "preliminary"
    # ...and queued for enrichment.
    assert pipeline.depth() == 1


async def test_fast_path_events_skip_the_queue_entirely(
    intake: EventIntake, store: AlarmStore, pipeline: EventPipeline, metrics: Metrics
) -> None:
    record = await intake.accept({"type": "panic_button", "confidence": 0.9})
    assert pipeline.depth() == 0, "unambiguous alarms must not cost an LLM call"
    assert store.get(record.event_id).triage_state == "final"
    assert store.get(record.event_id).triage.severity == "critical"
    assert metrics.llm_skipped_fast_path == 1


@pytest.mark.parametrize("message", ["not json", "", "[1,2,3]", '"a string"', b"\xff\xfe"])
async def test_malformed_messages_are_counted_not_fatal(
    intake: EventIntake, metrics: Metrics, message: object
) -> None:
    assert await intake.accept_raw(message) is None
    assert metrics.events_malformed == 1
    assert metrics.events_ingested == 0


async def test_missing_fields_still_produce_an_alarm(
    intake: EventIntake, store: AlarmStore
) -> None:
    record = await intake.accept_raw(json.dumps({"type": "glass_break"}))
    assert record is not None
    assert store.get(record.event_id).event.site_id == "unknown"


async def test_shed_events_remain_visible_with_their_rule_verdict(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    # Fill the queue (capacity 8) with events that will not be drained.
    for index in range(8):
        await intake.accept({"event_id": f"evt_{index}", "type": "motion_detected"})

    record = await intake.accept(
        {"event_id": "evt_shed", "type": "motion_detected", "confidence": 0.4}
    )
    assert metrics.events_shed == 1
    # The alarm is still on the board - only the LLM enrichment was skipped.
    assert store.get("evt_shed") is not None
    assert record.triage.severity == "info"


async def test_critical_alarms_are_protected_from_shedding(
    intake: EventIntake, store: AlarmStore, metrics: Metrics, pipeline: EventPipeline
) -> None:
    for index in range(8):
        await intake.accept({"event_id": f"evt_{index}", "type": "motion_detected"})

    # smoke_detected is critical but not fast-path, so it must queue - and wait.
    task = asyncio.create_task(
        intake.accept({"event_id": "evt_smoke", "type": "smoke_detected", "confidence": 0.9})
    )
    await asyncio.sleep(0.15)
    assert not task.done(), "a critical alarm must apply backpressure, not shed"
    assert metrics.events_shed == 0

    await pipeline.get()  # a worker frees one slot
    await task
    assert metrics.events_shed == 0


async def test_ingest_metrics_track_throughput(intake: EventIntake, metrics: Metrics) -> None:
    for _ in range(5):
        await intake.accept({"type": "motion_detected"})
    assert metrics.events_ingested == 5
    assert metrics.events_per_second > 0
