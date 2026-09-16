"""WebSocket ingest: stay connected to the event feed, hand everything to intake.

This layer owns exactly one concern - keeping a socket open. Parsing, triage,
and storage belong to `EventIntake`, which the camera worker shares.

Reconnection uses exponential backoff with jitter. The feed going away is a
normal Tuesday, not an error worth crashing the service over; the dashboard
shows the disconnected state and the backlog keeps draining meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
import random

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from backend.intake import EventIntake
from backend.metrics import Metrics

logger = logging.getLogger(__name__)

JITTER_RATIO = 0.25


def _next_backoff(current: float, *, maximum: float) -> float:
    return min(maximum, current * 2)


def _with_jitter(delay: float) -> float:
    return delay * (1 + random.uniform(-JITTER_RATIO, JITTER_RATIO))


async def ingest_loop(
    *,
    url: str,
    intake: EventIntake,
    metrics: Metrics,
    initial_backoff_s: float,
    max_backoff_s: float,
) -> None:
    backoff = initial_backoff_s
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                logger.info("connected to event stream at %s", url)
                metrics.stream_connected = True
                backoff = initial_backoff_s  # reset only after a real connection
                async for message in ws:
                    await intake.accept_raw(message)
        except asyncio.CancelledError:
            metrics.stream_connected = False
            raise
        except (ConnectionClosed, WebSocketException, OSError) as exc:
            metrics.stream_connected = False
            metrics.stream_reconnects += 1
            delay = _with_jitter(backoff)
            logger.warning(
                "event stream unavailable (%s); retrying in %.1fs",
                exc.__class__.__name__,
                delay,
            )
            await asyncio.sleep(delay)
            backoff = _next_backoff(backoff, maximum=max_backoff_s)
        except Exception:
            metrics.stream_connected = False
            metrics.stream_reconnects += 1
            logger.exception("unexpected ingest failure; retrying in %.1fs", backoff)
            await asyncio.sleep(_with_jitter(backoff))
            backoff = _next_backoff(backoff, maximum=max_backoff_s)
