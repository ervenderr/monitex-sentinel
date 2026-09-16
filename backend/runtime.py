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
from backend.video.motion import FrameDiffDetector
from backend.video.worker import video_worker

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

    def build_video_detector(self) -> FrameDiffDetector:
        """Split out from start() so the settings -> detector wiring is
        directly testable - this is exactly the kind of connection that looks
        done because the Settings field and the .env.example line both exist,
        while nothing actually reads the field. It didn't, once."""
        return FrameDiffDetector(
            pixel_threshold=self.settings.video_pixel_threshold,
            area_threshold=self.settings.video_area_threshold,
        )

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
        if self.settings.video_enabled:
            self._tasks.append(
                asyncio.create_task(
                    video_worker(
                        source=self.settings.video_source_resolved,
                        site_id=self.settings.video_site_id,
                        zone=self.settings.video_zone,
                        intake=self.intake,
                        metrics=self.metrics,
                        detector=self.build_video_detector(),
                        sample_interval_s=1.0 / self.settings.video_sample_fps,
                        cooldown_s=self.settings.video_cooldown_s,
                        rising_edge_frames=self.settings.video_rising_edge_frames,
                    ),
                    name="video",
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
