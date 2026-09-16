"""Frame-difference motion detection: pure numpy, no cv2 dependency.

Deliberately separated from frame capture. Capture needs cv2 (decode, colour
conversion, resizing); detection is just array arithmetic on whatever grayscale
frame it is handed. Keeping it cv2-free means these tests run on plain numpy
arrays with no video file, no OpenCV video backend, and no camera - fast and
independent of what codecs happen to be installed on the machine running them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Below this, per-pixel brightness change is sensor noise, not motion. Chosen
# against the committed demo clip: quiet frames diff at exactly 0 after blur,
# motion frames diff at 1.1-2.5% of pixels - ~10x this threshold at the floor.
DEFAULT_PIXEL_THRESHOLD = 18
DEFAULT_AREA_THRESHOLD = 0.006

# Maps changed-pixel fraction to a confidence in the same 0.35-0.99 range the
# rest of the system already treats as "a detector's honest range" - see
# stream.py's own generator. A bare fraction (up to ~1.0) would read as
# suspiciously more certain than any other detector in the system.
CONFIDENCE_FLOOR = 0.4
CONFIDENCE_CEILING = 0.97
# Fraction of pixels changed at which confidence saturates at the ceiling.
CONFIDENCE_SATURATION_AREA = 0.05


@dataclass(frozen=True)
class MotionReading:
    detected: bool
    changed_fraction: float
    confidence: float
    bbox: tuple[int, int, int, int] | None  # (x, y, w, h) in frame pixels, or None


def _score_to_confidence(fraction: float) -> float:
    span = CONFIDENCE_CEILING - CONFIDENCE_FLOOR
    scaled = min(1.0, fraction / CONFIDENCE_SATURATION_AREA)
    return round(CONFIDENCE_FLOOR + span * scaled, 2)


def _largest_bbox(changed_mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of changed pixels. No contour library - just where the
    mask is true. Good enough to tell an operator roughly where to look;
    real object detection is out of scope for a frame-diff detector."""
    rows = np.any(changed_mask, axis=1)
    cols = np.any(changed_mask, axis=0)
    if not rows.any():
        return None
    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)


class MotionDebouncer:
    """Turns a stream of detected/not-detected readings into "alert now" edges.

    A raw timer ("emit at most once every N seconds") does not actually cap one
    alarm per motion episode: if a person takes longer to cross the frame than
    the cooldown, the timer re-arms mid-crossing and fires again. This instead
    fires only on the transition into motion (the rising edge) and will not
    fire again until the episode has genuinely ended - detected has gone false
    for `quiet_run_required` consecutive readings, which absorbs a frame or two
    of flicker right at the boundary of a real event without either merging two
    separate people into one alarm or splitting one crossing into several.

    `cooldown_s` remains a floor between the end of one episode and the start
    of the next being alertable, as a second line of defence against rapid
    flapping beyond what `quiet_run_required` alone catches.
    """

    def __init__(
        self,
        *,
        cooldown_s: float,
        quiet_run_required: int = 2,
        rising_edge_run_required: int = 1,
    ) -> None:
        if cooldown_s < 0:
            raise ValueError("cooldown_s must not be negative")
        if quiet_run_required < 1:
            raise ValueError("quiet_run_required must be at least 1")
        if rising_edge_run_required < 1:
            raise ValueError("rising_edge_run_required must be at least 1")
        self._cooldown_s = cooldown_s
        self._quiet_run_required = quiet_run_required
        self._rising_edge_run_required = rising_edge_run_required
        self._in_episode = False
        self._quiet_run = 0
        self._rising_run = 0
        self._last_episode_end = float("-inf")

    def observe(self, detected: bool, now: float) -> bool:
        """Feed one reading; returns True exactly when a new episode starts.

        `rising_edge_run_required` > 1 requires that many *consecutive*
        detected readings before declaring an episode - the fix for a real
        camera, where auto-exposure or auto-gain can make a single frame
        cross the motion threshold with nobody in view. A one-off brightness
        step doesn't sustain across several samples; a person walking through
        frame does. The default of 1 preserves the original single-frame
        trigger, which is correct for a low-noise source like the committed
        synthetic demo clip.
        """
        if detected:
            self._quiet_run = 0
            if self._in_episode:
                return False
            if (now - self._last_episode_end) < self._cooldown_s:
                return False
            self._rising_run += 1
            if self._rising_run >= self._rising_edge_run_required:
                self._in_episode = True
                self._rising_run = 0
                return True
            return False

        self._rising_run = 0
        if self._in_episode:
            self._quiet_run += 1
            if self._quiet_run >= self._quiet_run_required:
                self._in_episode = False
                self._last_episode_end = now
        return False


class FrameDiffDetector:
    """Stateful: compares each new frame against the previous one.

    Consecutive-frame diffing, not a rolling background model. It cannot
    detect something that stops moving and stays in frame - a real limitation,
    and one background subtraction (cv2.createBackgroundSubtractorMOG2) would
    fix. Named as a known gap rather than silently accepted; see the README.
    """

    def __init__(
        self,
        *,
        pixel_threshold: int = DEFAULT_PIXEL_THRESHOLD,
        area_threshold: float = DEFAULT_AREA_THRESHOLD,
    ) -> None:
        if not 0 < pixel_threshold <= 255:
            raise ValueError("pixel_threshold must be in (0, 255]")
        if not 0 < area_threshold < 1:
            raise ValueError("area_threshold must be in (0, 1)")
        self._pixel_threshold = pixel_threshold
        self._area_threshold = area_threshold
        self._previous: np.ndarray | None = None

    @property
    def pixel_threshold(self) -> int:
        return self._pixel_threshold

    @property
    def area_threshold(self) -> float:
        return self._area_threshold

    def reset(self) -> None:
        """Forget the previous frame - call after a loop/seek discontinuity so
        the detector does not compare frame 0 against the clip's last frame."""
        self._previous = None

    def update(self, gray_frame: np.ndarray) -> MotionReading:
        if gray_frame.ndim != 2:
            raise ValueError("expected a single-channel (grayscale) frame")

        previous = self._previous
        self._previous = gray_frame

        if previous is None or previous.shape != gray_frame.shape:
            # Nothing to compare against yet, or the frame size changed.
            return MotionReading(
                detected=False, changed_fraction=0.0, confidence=0.0, bbox=None
            )

        diff = np.abs(gray_frame.astype(np.int16) - previous.astype(np.int16))
        changed = diff > self._pixel_threshold
        fraction = float(changed.mean())
        detected = fraction >= self._area_threshold

        return MotionReading(
            detected=detected,
            changed_fraction=round(fraction, 4),
            confidence=_score_to_confidence(fraction) if detected else 0.0,
            bbox=_largest_bbox(changed) if detected else None,
        )
