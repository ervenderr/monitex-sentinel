"""HTTP contract for the dashboard, driven against a real ASGI app.

The stub provider stands in for the LLM and the event feed is pointed at a dead
port, so these tests exercise the API while ingest is in its reconnect loop -
which is itself the assertion that a missing feed does not take the API down.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import create_app
from backend.config import Settings


@pytest.fixture
def app_settings() -> Settings:
    return Settings(
        # Nothing listens here: ingest will loop on reconnect for the whole test.
        event_stream_url="ws://127.0.0.1:9",
        queue_maxsize=16,
        triage_workers=1,
        store_capacity=50,
        llm_provider="stub",
        metrics_interval_s=0.05,
        reconnect_initial_s=0.05,
        reconnect_max_s=0.1,
        video_enabled=False,  # this file tests the API surface, not video capture
    )


@pytest.fixture
async def client(app_settings: Settings):
    app = create_app(app_settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        async with app.router.lifespan_context(app):
            http_client.app = app  # type: ignore[attr-defined]
            yield http_client


def _parse_sse(frame: str) -> tuple[str, dict]:
    name = ""
    payload: dict = {}
    for line in frame.strip().splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
    return name, payload


async def _seed(app, payload: dict) -> str:
    record = await app.state.runtime.intake.accept(payload)
    return record.event_id


async def test_health_reports_a_disconnected_feed_without_failing(client) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["stream_connected"] is False
    assert body["provider"] == "stub"


async def test_alarms_endpoint_returns_a_ranked_board(client) -> None:
    app = client.app
    await _seed(app, {"event_id": "evt_low", "type": "motion_detected", "confidence": 0.4})
    await _seed(app, {"event_id": "evt_high", "type": "fire_alarm", "confidence": 0.9})

    body = (await client.get("/api/alarms")).json()
    assert [a["event"]["event_id"] for a in body["alarms"]][0] == "evt_high"


async def test_metrics_endpoint_shape(client) -> None:
    await _seed(client.app, {"type": "panic_button"})
    body = (await client.get("/api/metrics")).json()
    assert body["events_ingested"] == 1
    assert body["llm_skipped_fast_path"] == 1
    assert body["queue_capacity"] == 16


async def test_acknowledge_then_resolve(client) -> None:
    event_id = await _seed(client.app, {"event_id": "evt_1", "type": "door_forced"})

    acked = await client.post(f"/api/alarms/{event_id}/acknowledge", json={"by": "erven"})
    assert acked.status_code == 200
    assert acked.json()["status"] == "acknowledged"

    resolved = await client.post(f"/api/alarms/{event_id}/resolve", json={"by": "erven"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


async def test_acknowledge_defaults_the_operator_name(client) -> None:
    event_id = await _seed(client.app, {"event_id": "evt_1", "type": "door_forced"})
    response = await client.post(f"/api/alarms/{event_id}/acknowledge", json={})
    assert response.json()["acknowledged_by"] == "operator"


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
async def test_actions_on_unknown_alarms_return_404(client, action: str) -> None:
    response = await client.post(f"/api/alarms/evt_missing/{action}", json={})
    assert response.status_code == 404


async def test_stream_generator_opens_with_snapshot_then_pushes_live_updates(
    app_settings: Settings,
) -> None:
    """Driven against the generator directly.

    Exercising SSE through ASGITransport deadlocks: the transport waits for the
    app coroutine to finish, and a live event stream never does. The generator
    is where the logic lives, so that is what we assert on.
    """
    from backend.api import _stream_events
    from backend.runtime import SentinelRuntime

    runtime = SentinelRuntime(app_settings)
    await runtime.intake.accept({"event_id": "evt_seed", "type": "fire_alarm"})

    stream = _stream_events(runtime, interval_s=0.05)
    try:
        first = _parse_sse(await anext(stream))
        assert first[0] == "snapshot"
        assert [a["event"]["event_id"] for a in first[1]["alarms"]] == ["evt_seed"]

        second = _parse_sse(await anext(stream))
        assert second[0] == "metrics"
        assert second[1]["queue_capacity"] == app_settings.queue_maxsize

        # An alarm arriving after the subscriber attached must be pushed live.
        await runtime.intake.accept(
            {"event_id": "evt_live", "type": "glass_break", "confidence": 0.9}
        )
        # Metrics ticks interleave with alarms by design; find the alarm frame.
        pushed = []
        for _ in range(10):
            name, payload = _parse_sse(await asyncio.wait_for(anext(stream), timeout=2))
            if name == "alarm":
                pushed.append(payload["record"]["event"]["event_id"])
                break
        assert pushed == ["evt_live"]
    finally:
        await stream.aclose()
        await runtime.stop()


async def test_stream_generator_keeps_metrics_ticking(app_settings: Settings) -> None:
    from backend.api import _stream_events
    from backend.runtime import SentinelRuntime

    runtime = SentinelRuntime(app_settings)
    stream = _stream_events(runtime, interval_s=0.02)
    try:
        await anext(stream)  # snapshot
        for _ in range(3):
            name, _payload = _parse_sse(await asyncio.wait_for(anext(stream), timeout=2))
            assert name == "metrics"
    finally:
        await stream.aclose()
        await runtime.stop()


async def test_stream_endpoint_sets_streaming_headers(client) -> None:
    """The route itself: right media type, and no proxy buffering."""
    from backend.api import stream as stream_route

    class _Request:
        def __init__(self, app):
            self.app = app

    response = await stream_route(_Request(client.app))  # type: ignore[arg-type]
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


async def test_no_alarm_is_lost_between_subscribe_and_snapshot(
    app_settings: Settings,
) -> None:
    """Regression: the subscriber must be registered before the snapshot is taken.

    If subscription were lazy, an alarm landing in this window would appear in
    neither the snapshot nor the update feed, and the operator would never see
    it. Here the alarm is injected after `subscribe()` but before the first
    frame is pulled, and must still reach the client by one path or the other.
    """
    from backend.api import _stream_events
    from backend.runtime import SentinelRuntime

    runtime = SentinelRuntime(app_settings)
    stream = _stream_events(runtime, interval_s=5)  # keep metrics out of the way

    try:
        # Prime the generator up to the point where it has subscribed, without
        # letting it emit: the first anext() runs to the snapshot yield.
        primed = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        await runtime.intake.accept({"event_id": "evt_racy", "type": "fire_alarm"})

        name, payload = _parse_sse(await asyncio.wait_for(primed, timeout=2))
        seen = {a["event"]["event_id"] for a in payload["alarms"]} if name == "snapshot" else set()

        if "evt_racy" not in seen:
            await anext(stream)  # metrics frame
            for _ in range(5):
                name, payload = _parse_sse(await asyncio.wait_for(anext(stream), timeout=2))
                if name == "alarm":
                    seen.add(payload["record"]["event"]["event_id"])
                    break

        assert "evt_racy" in seen, "an alarm in the subscribe/snapshot window was lost"
    finally:
        await stream.aclose()
        await runtime.stop()
