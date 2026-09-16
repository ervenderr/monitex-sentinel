"""The single door every event walks through, whatever produced it.

The WebSocket feed and the camera worker both call `accept`. That is the whole
point: a camera detection is triaged, ranked, and escalated by exactly the same
code as a sensor alarm, so there is no second path to keep in sync.

Two-stage triage happens here:

* The rule engine classifies the event in microseconds and the alarm hits the
  operator board *immediately*, marked preliminary. No operator waits on an API.
* The record is then queued for the LLM, which replaces the verdict in place
  when it lands.

Fast-path events (`fire_alarm`, `panic_button`) skip the queue entirely - the
rules are already authoritative and an LLM call would only add latency.
"""

from __future__ import annotations

import json
from typing import Any

from backend.correlation import CorrelationEngine
from backend.metrics import Metrics
from backend.models import AlarmRecord, RawEvent
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.triage import rules


class EventIntake:
    def __init__(
        self,
        *,
        pipeline: EventPipeline,
        store: AlarmStore,
        metrics: Metrics,
        correlation: CorrelationEngine,
    ) -> None:
        self._pipeline = pipeline
        self._store = store
        self._metrics = metrics
        self._correlation = correlation

    async def accept_raw(self, message: str | bytes) -> AlarmRecord | None:
        """Parse one wire message. Malformed input is counted, never fatal."""
        try:
            payload = json.loads(message)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            self._metrics.events_malformed += 1
            return None
        if not isinstance(payload, dict):
            self._metrics.events_malformed += 1
            return None
        return await self.accept(payload)

    async def accept(self, payload: dict[str, Any]) -> AlarmRecord | None:
        try:
            event = RawEvent.model_validate(payload)
        except Exception:
            # normalise_payload absorbs almost everything; this is the last net.
            self._metrics.events_malformed += 1
            return None

        preliminary = rules.triage(event)
        self._metrics.record_ingested()

        if rules.is_fast_path(event):
            # Unambiguous. Publish as final and spend nothing on an LLM call.
            self._metrics.llm_skipped_fast_path += 1
            self._metrics.record_triaged(latency_ms=0.0, cost_usd=0.0)
            final = AlarmRecord(event=event, triage=preliminary, triage_state="final")
            escalated = self._correlation.evaluate(final)
            if escalated.escalation is not None:
                self._metrics.escalations += 1
            return self._store.upsert(escalated)

        record = AlarmRecord(event=event, triage=preliminary, triage_state="preliminary")
        # Visible to the operator before the LLM has said anything.
        self._store.upsert(record)

        outcome = await self._pipeline.submit(
            record, protected=preliminary.severity == "critical"
        )
        if outcome.waited_ms > 0:
            self._metrics.backpressure_waits += 1
        if outcome.shed:
            # The alarm still shows with its rule verdict; only the LLM
            # enrichment was skipped. That is the honest degradation.
            self._metrics.events_shed += 1
        return record
