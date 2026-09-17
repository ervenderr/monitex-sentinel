"""Thin, blocking wrapper around cv2.VideoCapture.

Every method here does real I/O and decode work and is meant to be called via
`loop.run_in_executor`, never awaited directly - that is what keeps frame
decoding off the asyncio event loop (see video/worker.py). The class itself
has no async in it; that boundary is deliberate.
"""

from __future__ import annotations

import cv2
import numpy as np


class VideoSourceError(RuntimeError):
    """The source would not open, or stopped producing frames entirely."""


class LoopingVideoCapture:
    """Reads grayscale frames from a file (or camera index), looping at EOF.

    A live camera (`source=0`) does not hit EOF and loops implicitly; a file
    does, and is rewound so a short demo clip runs indefinitely without the
    caller needing to know the difference.
    """

    def __init__(
        self, source: str | int, *, frame_size: tuple[int, int] = (160, 90)
    ) -> None:
        self._source = source
        self._frame_size = frame_size
        self._capture = self._open()
        # The full-colour frame behind the most recent read_gray() call, kept
        # for the dashboard preview stream. Caching here rather than issuing a
        # second capture.read() avoids opening a second handle to the same
        # device - a live webcam backend often only tolerates one - and avoids
        # desyncing the detector, which compares consecutive frames and would
        # skip one every time something else also called read().
        self._last_bgr: np.ndarray | None = None

    def _open(self) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(self._source)
        if not capture.isOpened():
            raise VideoSourceError(f"could not open video source: {self._source!r}")
        return capture

    def read_gray(self) -> np.ndarray:
        """One frame, grayscale, resized, and blurred to damp sensor noise.

        Blurring here rather than in the detector: it is a property of how the
        frame was acquired, and doing it once at the source means every future
        consumer of this frame gets the same denoised view for free.
        """
        ok, frame = self._capture.read()
        if not ok:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._capture.read()
            if not ok:
                raise VideoSourceError(
                    f"video source {self._source!r} produced no frames, even after rewind"
                )

        self._last_bgr = frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, self._frame_size, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    @property
    def last_bgr_frame(self) -> np.ndarray | None:
        """The full-colour frame behind the most recent read_gray() call, or
        None before the first read. For preview/display only - detection
        always works from the grayscale frame read_gray() returns."""
        return self._last_bgr

    def release(self) -> None:
        self._capture.release()
