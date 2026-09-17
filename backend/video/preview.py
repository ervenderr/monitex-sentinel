"""Fan-out of the camera worker's latest frame to dashboard preview clients.

Deliberately simpler than AlarmStore's subscriber model: every alarm matters
and a slow client must not lose one, so that fan-out queues and preserves
order. A video preview has the opposite correctness requirement - a client
that falls behind should skip straight to the newest frame, not queue up
stale ones and fall further behind. So each subscriber queue holds at most
one frame, and a new publish overwrites whatever was waiting rather than
building a backlog.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import cv2
import numpy as np

# Detection works on a tiny grayscale frame for speed; the preview needs to
# actually look like a picture, so it is encoded separately, at a size chosen
# for a browser panel rather than a motion-diff array.
PREVIEW_MAX_WIDTH = 480
JPEG_QUALITY = 70


def encode_preview_jpeg(bgr_frame: np.ndarray) -> bytes:
    """Resize (if needed) and JPEG-encode one frame for the browser.

    A free function, not a method: it is pure (frame in, bytes out) and is
    run via `loop.run_in_executor` from the video worker, same as every other
    blocking OpenCV call in this codebase - encoding a frame is real CPU work
    and has no business running on the event loop either.
    """
    height, width = bgr_frame.shape[:2]
    if width > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / width
        bgr_frame = cv2.resize(bgr_frame, (PREVIEW_MAX_WIDTH, int(height * scale)))
    ok, buffer = cv2.imencode(".jpg", bgr_frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("failed to JPEG-encode the preview frame")
    return buffer.tobytes()


class FramePublisher:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[bytes]] = set()

    def publish(self, jpeg_bytes: bytes) -> None:
        """Called from the video worker after each frame is encoded. Never
        blocks: a full subscriber queue just has its one stale slot replaced."""
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(jpeg_bytes)

    async def subscribe(self) -> AsyncIterator[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
