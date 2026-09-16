"""The seam between the pipeline and whatever is doing the thinking.

Phase 1 ships the stub. Phase 2 adds the OpenAI adapter behind this same
protocol, so swapping providers touches exactly one factory function and no
pipeline code.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from backend.models import RawEvent, TriageResult
from backend.triage import rules


@runtime_checkable
class TriageProvider(Protocol):
    name: str

    async def triage(self, event: RawEvent) -> TriageResult:
        """Return a verdict, or raise. Callers handle failure via the fallback."""
        ...


class StubProvider:
    """Deterministic provider so the system runs end-to-end with no API key.

    It is the rule engine wearing an LLM's interface. The integration seam is
    real; only the model is absent.

    `latency_ms` emulates provider think-time. It defaults to zero (fast tests)
    but lets a load test drive the queue against realistic LLM latency without
    spending anything on tokens - which is the only way to see backpressure and
    shedding actually engage.
    """

    name = "stub"

    def __init__(self, latency_ms: float = 0.0) -> None:
        self._latency_s = max(0.0, latency_ms) / 1000

    async def triage(self, event: RawEvent) -> TriageResult:
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        result = rules.triage(event)
        return result.model_copy(update={"model": "stub", "origin": "rules"})


def fallback_triage(event: RawEvent) -> TriageResult:
    """What the operator sees when the provider fails. Degraded, never blank."""
    return rules.triage(event, degraded=True)
