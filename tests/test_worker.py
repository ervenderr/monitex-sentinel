"""A failing provider must degrade the verdict, never lose the alarm."""

from __future__ import annotations

import asyncio

import pytest

from backend.metrics import Metrics
from backend.models import RawEvent, TriageResult
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.triage.base import StubProvider
from backend.triage.worker import triage_worker
from tests.conftest import make_record


class ExplodingProvider:
    name = "exploding"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def triage(self, event: RawEvent) -> TriageResult:
        self.calls += 1
        raise self._exc


class SlowProvider:
    name = "slow"

    async def triage(self, event: RawEvent) -> TriageResult:
        await asyncio.sleep(5)
        raise AssertionError("should have been cancelled")


async def _run_worker(provider, pipeline, store, metrics, *, settle: float = 0.1):
    task = asyncio.create_task(
        triage_worker(
            name="t0", pipeline=pipeline, provider=provider, store=store, metrics=metrics
        )
    )
    await asyncio.sleep(settle)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_happy_path_marks_the_verdict_final(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics
) -> None:
    record = make_record(event_id="evt_1", type="perimeter_breach", confidence=0.7)
    store.upsert(record)
    await pipeline.submit(record, protected=False)

    await _run_worker(StubProvider(), pipeline, store, metrics)

    assert store.get("evt_1").triage_state == "final"
    assert metrics.events_triaged == 1
    assert metrics.llm_failures == 0


@pytest.mark.parametrize(
    "exc", [TimeoutError(), ValueError("bad json"), RuntimeError("429 rate limited")]
)
async def test_provider_failure_serves_a_degraded_fallback(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics, exc: Exception
) -> None:
    record = make_record(event_id="evt_1", type="door_forced", confidence=0.9)
    store.upsert(record)
    await pipeline.submit(record, protected=False)

    await _run_worker(ExplodingProvider(exc), pipeline, store, metrics)

    result = store.get("evt_1")
    assert result.triage_state == "final", "the alarm must still reach a verdict"
    assert result.triage.degraded is True
    assert result.triage.origin == "fallback"
    assert result.triage.severity == "critical"  # rules still did their job
    assert result.triage.recommended_action
    assert metrics.llm_failures == 1
    assert metrics.llm_degraded == 1


async def test_worker_survives_repeated_failures(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics
) -> None:
    provider = ExplodingProvider(RuntimeError("down"))
    for index in range(5):
        record = make_record(event_id=f"evt_{index}")
        store.upsert(record)
        await pipeline.submit(record, protected=False)

    await _run_worker(provider, pipeline, store, metrics, settle=0.2)

    assert provider.calls == 5, "one bad event must not kill the worker"
    assert all(store.get(f"evt_{i}").triage_state == "final" for i in range(5))


async def test_operator_action_during_triage_is_not_clobbered(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics
) -> None:
    record = make_record(event_id="evt_1", type="perimeter_breach", confidence=0.7)
    store.upsert(record)
    await pipeline.submit(record, protected=False)

    # Operator acknowledges while the (stale) record is in flight.
    store.acknowledge("evt_1", "erven")
    await _run_worker(StubProvider(), pipeline, store, metrics)

    result = store.get("evt_1")
    assert result.status == "acknowledged", "triage must not resurrect an acked alarm"
    assert result.acknowledged_by == "erven"
    assert result.triage_state == "final"


async def test_latency_is_recorded(
    pipeline: EventPipeline, store: AlarmStore, metrics: Metrics
) -> None:
    record = make_record(event_id="evt_1")
    store.upsert(record)
    await pipeline.submit(record, protected=False)
    await _run_worker(StubProvider(), pipeline, store, metrics)

    assert metrics.snapshot().triage_latency_p95_ms >= 0
    assert store.get("evt_1").triage.latency_ms >= 0
