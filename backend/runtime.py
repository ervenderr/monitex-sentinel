"""Composition root: builds the pipeline and owns its background tasks.

Keeping assembly out of the web layer means a test can spin up the whole
pipeline without an HTTP server, and the API module stays about HTTP.
"""

from __future__ import annotations

import asyncio
import logging

from backend.config import Settings
from backend.ingest import ingest_loop
from backend.intake import EventIntake
from backend.metrics import Metrics
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.triage.base import TriageProvider
from backend.triage.factory import build_provider
from backend.triage.worker import triage_worker

logger = logging.getLogger(__name__)


class SentinelRuntime:
    def __init__(self, settings: Settings, *, provider: TriageProvider | None = None) -> None:
        self.settings = settings
        self.metrics = Metrics()
        self.pipeline = EventPipeline(
            maxsize=settings.queue_maxsize,
            backpressure_timeout_s=settings.queue_backpressure_timeout_s,
        )
        self.store = AlarmStore(capacity=settings.store_capacity)
        self.intake = EventIntake(
            pipeline=self.pipeline, store=self.store, metrics=self.metrics
        )
        self.provider = provider or build_provider(settings)
        self.metrics.bind_queue(self.pipeline.depth, self.pipeline.capacity)
        circuit_state = getattr(self.provider, "circuit_state", None)
        if circuit_state is not None:
            self.metrics.bind_circuit(lambda: getattr(self.provider, "circuit_state", "closed"))
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        for index in range(self.settings.triage_workers):
            self._tasks.append(
                asyncio.create_task(
                    triage_worker(
                        name=f"triage-{index}",
                        pipeline=self.pipeline,
                        provider=self.provider,
                        store=self.store,
                        metrics=self.metrics,
                    ),
                    name=f"triage-{index}",
                )
            )
        self._tasks.append(
            asyncio.create_task(
                ingest_loop(
                    url=self.settings.event_stream_url,
                    intake=self.intake,
                    metrics=self.metrics,
                    initial_backoff_s=self.settings.reconnect_initial_s,
                    max_backoff_s=self.settings.reconnect_max_s,
                ),
                name="ingest",
            )
        )
        logger.info(
            "sentinel runtime started: %d triage workers, queue capacity %d",
            self.settings.triage_workers,
            self.pipeline.capacity,
        )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        aclose = getattr(self.provider, "aclose", None)
        if aclose is not None:
            await aclose()
        logger.info("sentinel runtime stopped")
