"""The camera worker: samples a video feed and emits detection events into the
same pipeline as sensor alarms.

The brief's core requirement for this piece is "keep decoding off the hot
path so it never stalls ingestion." Two things deliver that, both required:

1. Every blocking call (opening the source, reading a frame) runs via
   `loop.run_in_executor`, never inline on the event loop. A slow decode
   blocks a worker thread, not the loop that the WebSocket ingest and the SSE
   fan-out depend on. `test_video_integration.py` proves this concretely: a
   simulated 300ms-per-frame decode runs concurrently with a fast asyncio
   timer, and the timer's jitter is measured to stay in single-digit
   milliseconds throughout.
2. Sampling is rate-capped independently of decode speed (`sample_interval_s`
   sleep between reads). This clip could be decoded at hundreds of FPS; there
   is no reason to, since detection only needs a few samples a second, and
   burning CPU on frames nobody looks at is its own way to degrade the system.

Emission is debounced with `MotionDebouncer`, edge-triggered rather than a
raw timer: it fires once when motion starts and stays quiet for the rest of
that episode, however long it runs, rather than re-arming every `cooldown_s`
regardless of whether the same person is still in frame. That distinction
matters in practice - it was the actual behaviour of an earlier, timer-based
version of this worker, caught by running it against the real demo clip: a
single 8-second pass produced three separate alarms instead of one.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from time import monotonic

from backend.intake import EventIntake
from backend.metrics import Metrics
from backend.video.capture import LoopingVideoCapture, VideoSourceError
from backend.video.motion import FrameDiffDetector, MotionDebouncer
from backend.video.preview import FramePublisher, encode_preview_jpeg

logger = logging.getLogger(__name__)

EVENT_TYPE = "motion_detected"


def _build_event(
    *,
    site_id: str,
    zone: str,
    confidence: float,
    bbox: tuple[int, int, int, int] | None,
    frame_size: tuple[int, int],
) -> dict:
    metadata: dict = {"detector": "frame_diff"}
    if bbox is not None:
        x, y, w, h = bbox
        fw, fh = frame_size
        # Normalised 0-1 so the box means something independent of the
        # detector's internal (deliberately tiny) working resolution.
        metadata["bbox"] = {
            "x": round(x / fw, 3),
            "y": round(y / fh, 3),
            "w": round(w / fw, 3),
            "h": round(h / fh, 3),
        }
    return {
        "site_id": site_id,
        "zone": zone,
        "type": EVENT_TYPE,
        "source": "camera",
        "confidence": confidence,
        "metadata": metadata,
    }


async def video_worker(
    *,
    source: str | int,
    site_id: str,
    zone: str,
    intake: EventIntake,
    metrics: Metrics,
    detector: FrameDiffDetector | None = None,
    debouncer: MotionDebouncer | None = None,
    frame_size: tuple[int, int] = (160, 90),
    sample_interval_s: float = 0.25,
    cooldown_s: float = 4.0,
    rising_edge_frames: int = 1,
    warmup_frames: int = 8,
    reconnect_backoff_s: float = 2.0,
    preview: FramePublisher | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    detector = detector or FrameDiffDetector()
    debouncer = debouncer or MotionDebouncer(
        cooldown_s=cooldown_s, rising_edge_run_required=rising_edge_frames
    )
    capture: LoopingVideoCapture | None = None

    logger.info("video worker started (source=%r, site=%s/%s)", source, site_id, zone)

    while True:
        try:
            if capture is None:
                capture = await loop.run_in_executor(
                    None, partial(LoopingVideoCapture, source, frame_size=frame_size)
                )
                # A live camera's auto-exposure/white-balance has not
                # converged in its first frames; discarding a burst before
                # detection starts avoids treating that settling-in as
                # motion. A looping file has no such transient, so this
                # simply advances a few frames into an already-quiet clip -
                # harmless there.
                for _ in range(warmup_frames):
                    await loop.run_in_executor(None, capture.read_gray)
                detector.reset()
                metrics.video_connected = True
                logger.info(
                    "video source opened: %r (warmed up %d frames)", source, warmup_frames
                )

            gray = await loop.run_in_executor(None, capture.read_gray)
            metrics.video_frames_sampled += 1

            if preview is not None:
                bgr = capture.last_bgr_frame
                if bgr is not None:
                    jpeg_bytes = await loop.run_in_executor(None, encode_preview_jpeg, bgr)
                    preview.publish(jpeg_bytes)

            reading = detector.update(gray)
            if debouncer.observe(reading.detected, monotonic()):
                await intake.accept(
                    _build_event(
                        site_id=site_id,
                        zone=zone,
                        confidence=reading.confidence,
                        bbox=reading.bbox,
                        frame_size=frame_size,
                    )
                )
                metrics.video_events_emitted += 1

        except asyncio.CancelledError:
            if capture is not None:
                await loop.run_in_executor(None, capture.release)
            raise
        except VideoSourceError as exc:
            metrics.video_connected = False
            if capture is not None:
                await loop.run_in_executor(None, capture.release)
            capture = None
            logger.warning(
                "video source unavailable (%s); retrying in %.1fs", exc, reconnect_backoff_s
            )
            await asyncio.sleep(reconnect_backoff_s)
            continue
        except Exception:
            # A frame this worker cannot make sense of must not end triage for
            # the whole site - same principle as the triage worker's try/except.
            metrics.video_connected = False
            logger.exception(
                "unexpected video worker failure; retrying in %.1fs", reconnect_backoff_s
            )
            await asyncio.sleep(reconnect_backoff_s)
            continue

        await asyncio.sleep(sample_interval_s)
