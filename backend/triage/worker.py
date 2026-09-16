"""Triage workers: pull from the queue, ask the provider, publish the verdict.

Concurrency is bounded by worker count rather than unbounded task spawning, so
a backlog shows up as queue depth on the dashboard instead of as thousands of
in-flight API calls and a rate-limit wall.

A provider failure is never fatal to an alarm. The rule engine's fallback
verdict is published instead, flagged degraded, and the operator still sees a
ranked, actionable alarm.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from backend.metrics import Metrics
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.triage.base import TriageProvider, fallback_triage

logger = logging.getLogger(__name__)


async def _triage_one(
    record, *, provider: TriageProvider, store: AlarmStore, metrics: Metrics
) -> None:
    started = perf_counter()
    metrics.llm_calls += 1
    try:
        result = await provider.triage(record.event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        metrics.llm_failures += 1
        metrics.llm_degraded += 1
        logger.warning(
            "triage provider failed for %s (%s); serving rule fallback",
            record.event_id,
            exc.__class__.__name__,
        )
        result = fallback_triage(record.event)
    latency_ms = (perf_counter() - started) * 1000

    final = result.model_copy(update={"latency_ms": round(latency_ms, 1)})
    metrics.record_triaged(latency_ms=latency_ms, cost_usd=final.cost_usd)

    # Re-read: the operator may have acknowledged it while the LLM was thinking.
    current = store.get(record.event_id) or record
    store.upsert(current.with_triage(final, state="final"))


async def triage_worker(
    *,
    name: str,
    pipeline: EventPipeline,
    provider: TriageProvider,
    store: AlarmStore,
    metrics: Metrics,
) -> None:
    logger.info("triage worker %s started (provider=%s)", name, provider.name)
    while True:
        record = await pipeline.get()
        try:
            await _triage_one(record, provider=provider, store=store, metrics=metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A worker must outlive any single bad event.
            logger.exception("unhandled triage error for %s", record.event_id)
        finally:
            pipeline.task_done()
