from __future__ import annotations

from typing import Any

import pytest

from backend.config import Settings
from backend.metrics import Metrics
from backend.models import AlarmRecord, RawEvent
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.triage import rules


def make_event(**overrides: Any) -> RawEvent:
    payload: dict[str, Any] = {
        "event_id": "evt_test0001",
        "site_id": "site-101",
        "zone": "north-perimeter",
        "type": "motion_detected",
        "source": "sensor",
        "confidence": 0.8,
        "timestamp": "2026-09-16T13:00:00Z",
        "metadata": {},
    }
    payload.update(overrides)
    return RawEvent.model_validate(payload)


def make_record(**overrides: Any) -> AlarmRecord:
    event = make_event(**overrides)
    return AlarmRecord(event=event, triage=rules.triage(event))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        queue_maxsize=8,
        queue_backpressure_timeout_s=0.05,
        triage_workers=2,
        store_capacity=50,
        llm_provider="stub",
    )


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def store() -> AlarmStore:
    return AlarmStore(capacity=50)


@pytest.fixture
def pipeline() -> EventPipeline:
    return EventPipeline(maxsize=8, backpressure_timeout_s=0.05)
