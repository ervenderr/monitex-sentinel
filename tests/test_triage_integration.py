"""End to end through the real provider class: intake -> queue -> worker -> store.

The only thing faked is the network. Everything else - schema validation,
retries, the breaker, the fallback, the metrics - is the production path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from backend.config import Settings
from backend.runtime import SentinelRuntime
from backend.triage.circuit import CircuitBreaker
from backend.triage.openai_provider import OpenAIProvider
from tests.test_openai_provider import _completion


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        event_stream_url="ws://127.0.0.1:9",  # nothing listens; ingest just retries
        queue_maxsize=32,
        triage_workers=2,
        store_capacity=100,
        llm_provider="stub",  # replaced below with a mocked real provider
    )


async def _runtime_with(handler, settings: Settings, **kwargs: Any) -> SentinelRuntime:
    provider = OpenAIProvider(
        api_key="sk-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        timeout_s=1.0,
        **kwargs,
    )
    runtime = SentinelRuntime(settings, provider=provider)
    await runtime.start()
    return runtime


async def _until(predicate, limit_s: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_alarm_goes_preliminary_then_final_with_the_llm_verdict(
    settings: Settings,
) -> None:
    runtime = await _runtime_with(
        lambda request: httpx.Response(
            200, json=_completion(severity="critical", summary="Forced entry, act now.")
        ),
        settings,
    )
    try:
        record = await runtime.intake.accept(
            {"event_id": "evt_1", "type": "door_forced", "confidence": 0.7}
        )
        assert record.triage_state == "preliminary"
        assert record.triage.origin == "rules"

        assert await _until(lambda: runtime.store.get("evt_1").triage_state == "final")
        final = runtime.store.get("evt_1")
        assert final.triage.origin == "llm"
        assert final.triage.severity == "critical"
        assert final.triage.summary == "Forced entry, act now."
        assert final.triage.cost_usd > 0

        snapshot = runtime.metrics.snapshot()
        assert snapshot.llm_calls == 1
        assert snapshot.llm_failures == 0
        assert snapshot.llm_tokens_in == 350
        assert snapshot.cost_usd > 0
    finally:
        await runtime.stop()


async def test_a_dead_provider_still_produces_actionable_alarms(
    settings: Settings,
) -> None:
    runtime = await _runtime_with(
        lambda request: httpx.Response(500, json={}),
        settings,
        max_retries=0,
        breaker=CircuitBreaker(failure_threshold=3, cooldown_s=60),
    )
    try:
        for index in range(6):
            await runtime.intake.accept(
                {"event_id": f"evt_{index}", "type": "door_forced", "confidence": 0.9}
            )

        assert await _until(
            lambda: all(
                runtime.store.get(f"evt_{i}").triage_state == "final" for i in range(6)
            )
        )

        for index in range(6):
            record = runtime.store.get(f"evt_{index}")
            assert record.triage.degraded is True
            assert record.triage.origin == "fallback"
            # Still ranked and still actionable - that is the whole point.
            assert record.triage.severity == "critical"
            assert record.triage.recommended_action

        snapshot = runtime.metrics.snapshot()
        assert snapshot.llm_degraded == 6
        assert snapshot.llm_circuit_state == "open", "the breaker should have tripped"
        assert snapshot.cost_usd == 0.0, "failed calls must not be billed as success"
    finally:
        await runtime.stop()


async def test_the_breaker_stops_paying_the_timeout_on_every_alarm(
    settings: Settings,
) -> None:
    """Once open, alarms must not each wait the full provider timeout."""
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return httpx.Response(500, json={})

    runtime = await _runtime_with(
        handler, settings, max_retries=0, breaker=CircuitBreaker(failure_threshold=2, cooldown_s=60)
    )
    try:
        for index in range(2):  # trip it
            await runtime.intake.accept({"event_id": f"evt_trip{index}", "type": "glass_break"})
        assert await _until(lambda: runtime.provider.circuit_state == "open", limit_s=5)

        calls_before = calls["n"]
        started = asyncio.get_running_loop().time()
        for index in range(10):
            await runtime.intake.accept({"event_id": f"evt_{index}", "type": "glass_break"})
        assert await _until(
            lambda: all(
                runtime.store.get(f"evt_{i}").triage_state == "final" for i in range(10)
            )
        )
        elapsed = asyncio.get_running_loop().time() - started

        assert calls["n"] == calls_before, "open circuit must not call the API"
        assert elapsed < 1.0, "ten alarms must not cost ten timeouts"
    finally:
        await runtime.stop()


async def test_malformed_model_output_degrades_that_alarm_only(
    settings: Settings,
) -> None:
    """One junk response must not poison the alarms around it."""
    responses = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        responses["n"] += 1
        if responses["n"] == 2:
            return httpx.Response(200, json={"choices": [{"message": {"content": "{{{"}}]})
        return httpx.Response(200, json=_completion(severity="warning"))

    runtime = await _runtime_with(handler, settings, max_retries=0)
    try:
        for index in range(3):
            await runtime.intake.accept(
                {"event_id": f"evt_{index}", "type": "loitering", "confidence": 0.6}
            )
        assert await _until(
            lambda: all(
                runtime.store.get(f"evt_{i}").triage_state == "final" for i in range(3)
            )
        )

        origins = [runtime.store.get(f"evt_{i}").triage.origin for i in range(3)]
        assert origins.count("llm") == 2
        assert origins.count("fallback") == 1
        assert runtime.metrics.snapshot().llm_failures == 1
    finally:
        await runtime.stop()


async def test_fast_path_alarms_never_reach_the_provider(settings: Settings) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_completion())

    runtime = await _runtime_with(handler, settings)
    try:
        await runtime.intake.accept({"event_id": "evt_fire", "type": "fire_alarm"})
        await runtime.intake.accept({"event_id": "evt_panic", "type": "panic_button"})
        await asyncio.sleep(0.2)

        assert calls["n"] == 0, "unambiguous alarms must not cost a token"
        assert runtime.store.get("evt_fire").triage.severity == "critical"
        assert runtime.store.get("evt_fire").triage_state == "final"
        assert runtime.metrics.snapshot().cost_usd == 0.0
    finally:
        await runtime.stop()


async def test_the_operator_sees_every_alarm_even_when_the_llm_is_slow(
    settings: Settings,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(200, json=_completion())

    runtime = await _runtime_with(handler, settings)
    try:
        for index in range(20):
            await runtime.intake.accept(
                {"event_id": f"evt_{index}", "type": "perimeter_breach", "confidence": 0.8}
            )
        # No waiting: all 20 are already on the board with a rule verdict.
        board = runtime.store.snapshot()
        assert len(board) == 20
        assert all(r.triage.severity in {"info", "warning", "critical"} for r in board)
        assert all(r.triage.recommended_action for r in board)
    finally:
        await runtime.stop()
