"""Ingest against a real WebSocket feed, including the feed going away."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.asyncio.server import serve

from backend.ingest import _next_backoff, _with_jitter, ingest_loop
from backend.intake import EventIntake
from backend.metrics import Metrics
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from tests.fake_feed import FakeFeed


@pytest.fixture
def intake(pipeline: EventPipeline, store: AlarmStore, metrics: Metrics) -> EventIntake:
    return EventIntake(pipeline=pipeline, store=store, metrics=metrics)


def _start_ingest(url: str, intake: EventIntake, metrics: Metrics) -> asyncio.Task:
    return asyncio.create_task(
        ingest_loop(
            url=url,
            intake=intake,
            metrics=metrics,
            initial_backoff_s=0.05,
            max_backoff_s=0.2,
        )
    )


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _until(predicate, limit_s: float = 3.0) -> bool:
    """Poll until a condition holds. Keeps tests fast without fixed sleeps."""
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_consumes_a_live_feed(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    feed = FakeFeed(8791)
    await feed.start(
        [
            {"event_id": "evt_a", "type": "fire_alarm", "confidence": 0.9},
            {"event_id": "evt_b", "type": "motion_detected", "confidence": 0.4},
        ]
    )
    task = _start_ingest(feed.url, intake, metrics)
    try:
        assert await _until(lambda: metrics.events_ingested == 2)
        assert metrics.stream_connected is True
        assert store.get("evt_a").triage.severity == "critical"
        assert store.get("evt_b").triage.severity == "info"
    finally:
        await _stop(task)
        await feed.stop()


async def test_malformed_frames_do_not_break_the_connection(
    intake: EventIntake, metrics: Metrics
) -> None:
    stop = asyncio.Event()

    async def handler(ws: Any) -> None:
        await ws.send("this is not json")
        await ws.send("{truncated")
        await ws.send(json.dumps({"event_id": "evt_ok", "type": "glass_break"}))
        await stop.wait()

    ctx = serve(handler, "127.0.0.1", 8792)
    server = await ctx.__aenter__()
    task = _start_ingest("ws://127.0.0.1:8792", intake, metrics)
    try:
        assert await _until(lambda: metrics.events_ingested == 1)
        assert metrics.events_malformed == 2, "bad frames counted, connection kept"
        assert metrics.stream_connected is True
    finally:
        await _stop(task)
        stop.set()
        server.close()
        await asyncio.wait_for(server.wait_closed(), timeout=2)


async def test_reconnects_after_the_feed_drops(
    intake: EventIntake, metrics: Metrics
) -> None:
    feed = FakeFeed(8793)
    await feed.start([{"event_id": "evt_1", "type": "door_forced", "confidence": 0.9}])
    task = _start_ingest(feed.url, intake, metrics)
    try:
        assert await _until(lambda: metrics.stream_connected)
        assert await _until(lambda: metrics.events_ingested == 1)

        await feed.stop()
        assert await _until(lambda: not metrics.stream_connected)
        assert metrics.stream_reconnects >= 1

        # The feed comes back; ingest must find it with no restart.
        await feed.start([{"event_id": "evt_2", "type": "panic_button", "confidence": 0.9}])
        assert await _until(lambda: metrics.stream_connected, limit_s=5)
        assert await _until(lambda: metrics.events_ingested == 2, limit_s=5)
    finally:
        await _stop(task)
        await feed.stop()


async def test_survives_a_feed_that_never_appears(
    intake: EventIntake, metrics: Metrics
) -> None:
    task = _start_ingest("ws://127.0.0.1:9", intake, metrics)  # nothing listens here
    try:
        assert await _until(lambda: metrics.stream_reconnects >= 2, limit_s=3)
        assert not task.done(), "ingest must keep retrying, not crash"
        assert metrics.stream_connected is False
    finally:
        await _stop(task)


async def test_backoff_resets_only_after_a_successful_connection(
    intake: EventIntake, metrics: Metrics
) -> None:
    feed = FakeFeed(8794)
    task = _start_ingest(feed.url, intake, metrics)
    try:
        assert await _until(lambda: metrics.stream_reconnects >= 2, limit_s=3)
        await feed.start([{"event_id": "evt_1", "type": "fire_alarm"}])
        assert await _until(lambda: metrics.events_ingested == 1, limit_s=5)
        assert feed.connection_count == 1
    finally:
        await _stop(task)
        await feed.stop()


def test_backoff_grows_and_is_capped() -> None:
    delay = 0.5
    seen = []
    for _ in range(10):
        delay = _next_backoff(delay, maximum=15.0)
        seen.append(delay)
    assert seen[0] == 1.0
    assert seen[1] == 2.0
    assert delay == 15.0, "backoff must not grow without bound"


def test_jitter_stays_within_band() -> None:
    for _ in range(200):
        assert 0.75 <= _with_jitter(1.0) <= 1.25
