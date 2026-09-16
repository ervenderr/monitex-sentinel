"""Pure numpy: no video file, no cv2 video backend, no camera required."""

from __future__ import annotations

import numpy as np
import pytest

from backend.video.motion import FrameDiffDetector, MotionReading


def frame(fill: int, shape: tuple[int, int] = (90, 160)) -> np.ndarray:
    return np.full(shape, fill, dtype=np.uint8)


def frame_with_box(
    fill: int, box_value: int, box: tuple[int, int, int, int], shape: tuple[int, int] = (90, 160)
) -> np.ndarray:
    f = frame(fill, shape)
    x, y, w, h = box
    f[y : y + h, x : x + w] = box_value
    return f


def test_first_frame_has_nothing_to_compare_against() -> None:
    detector = FrameDiffDetector()
    reading = detector.update(frame(50))
    assert reading == MotionReading(
        detected=False, changed_fraction=0.0, confidence=0.0, bbox=None
    )


def test_identical_frames_show_no_motion() -> None:
    detector = FrameDiffDetector()
    detector.update(frame(50))
    reading = detector.update(frame(50))
    assert not reading.detected
    assert reading.changed_fraction == 0.0


def test_below_pixel_threshold_is_treated_as_noise() -> None:
    detector = FrameDiffDetector(pixel_threshold=18, area_threshold=0.006)
    detector.update(frame(50))
    reading = detector.update(frame(55))  # +5, under the pixel threshold
    assert not reading.detected


def test_a_moving_box_crosses_the_area_threshold() -> None:
    detector = FrameDiffDetector(pixel_threshold=18, area_threshold=0.006)
    detector.update(frame_with_box(30, 200, box=(10, 10, 20, 40)))
    reading = detector.update(frame_with_box(30, 200, box=(30, 10, 20, 40)))
    assert reading.detected
    assert reading.confidence > 0


def test_confidence_is_bounded_to_the_detector_convention() -> None:
    """The rest of the system treats 0.35-0.99 as a detector's honest range
    (see stream.py's own generator); this detector's output must fit inside it."""
    detector = FrameDiffDetector(pixel_threshold=5, area_threshold=0.01)
    detector.update(frame(0))
    reading = detector.update(frame(255))  # maximum possible change, full frame
    assert reading.detected
    assert 0.35 <= reading.confidence <= 0.99


def test_confidence_increases_with_more_changed_area() -> None:
    detector = FrameDiffDetector(pixel_threshold=18, area_threshold=0.006)
    detector.update(frame(30))
    small = detector.update(frame_with_box(30, 200, box=(10, 10, 10, 10)))
    detector.update(frame(30))
    large = detector.update(frame_with_box(30, 200, box=(10, 10, 80, 80)))
    assert large.confidence > small.confidence


def test_bbox_locates_the_changed_region() -> None:
    detector = FrameDiffDetector(pixel_threshold=18, area_threshold=0.006)
    detector.update(frame(30))
    reading = detector.update(frame_with_box(30, 200, box=(40, 20, 20, 30)))
    assert reading.bbox is not None
    x, y, w, h = reading.bbox
    assert (x, y) == (40, 20)
    assert w >= 20 and h >= 30  # blur/rounding may widen it slightly, never shrink


def test_no_bbox_when_nothing_moved() -> None:
    detector = FrameDiffDetector()
    detector.update(frame(50))
    reading = detector.update(frame(50))
    assert reading.bbox is None


def test_reset_forgets_the_previous_frame() -> None:
    """Required after a loop/seek discontinuity, or frame 0 of a fresh loop
    gets compared against the clip's last frame and may falsely fire."""
    detector = FrameDiffDetector(pixel_threshold=18, area_threshold=0.006)
    detector.update(frame(30))
    detector.reset()
    reading = detector.update(frame(220))  # would clearly register without reset
    assert not reading.detected


def test_a_frame_size_change_does_not_crash_or_false_fire() -> None:
    detector = FrameDiffDetector()
    detector.update(frame(50, shape=(90, 160)))
    reading = detector.update(frame(50, shape=(45, 80)))
    assert not reading.detected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pixel_threshold": 0},
        {"pixel_threshold": 300},
        {"area_threshold": 0},
        {"area_threshold": 1},
    ],
)
def test_rejects_nonsense_configuration(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        FrameDiffDetector(**kwargs)


def test_rejects_a_non_grayscale_frame() -> None:
    detector = FrameDiffDetector()
    with pytest.raises(ValueError, match="grayscale"):
        detector.update(np.zeros((90, 160, 3), dtype=np.uint8))


# ---- MotionDebouncer -------------------------------------------------------

from backend.video.motion import MotionDebouncer  # noqa: E402


def test_first_detection_fires_immediately() -> None:
    debouncer = MotionDebouncer(cooldown_s=4.0)
    assert debouncer.observe(True, now=0.0) is True


def test_sustained_detection_does_not_refire_no_matter_how_long() -> None:
    """The bug this class exists to fix: a raw timer re-arms mid-episode if the
    episode outlasts the cooldown. This must not, however long motion runs."""
    debouncer = MotionDebouncer(cooldown_s=4.0)
    assert debouncer.observe(True, now=0.0) is True
    for t in [1.0, 5.0, 9.0, 20.0, 100.0]:
        assert debouncer.observe(True, now=t) is False


def test_a_new_episode_after_genuine_quiet_fires_again() -> None:
    debouncer = MotionDebouncer(cooldown_s=0.0, quiet_run_required=1)
    assert debouncer.observe(True, now=0.0) is True
    assert debouncer.observe(False, now=1.0) is False  # episode ends
    assert debouncer.observe(True, now=2.0) is True  # new episode


def test_brief_flicker_does_not_end_the_episode() -> None:
    """One quiet reading right at a motion boundary must not be read as the
    episode ending and immediately restarting - that would double-count one
    crossing as two alarms."""
    debouncer = MotionDebouncer(cooldown_s=0.0, quiet_run_required=2)
    assert debouncer.observe(True, now=0.0) is True
    assert debouncer.observe(False, now=1.0) is False  # one flicker frame
    assert debouncer.observe(True, now=2.0) is False  # still the same episode
    assert debouncer.observe(False, now=3.0) is False
    assert debouncer.observe(False, now=4.0) is False  # now genuinely ended
    assert debouncer.observe(True, now=5.0) is True  # new episode


def test_cooldown_delays_the_next_episode_after_one_ends() -> None:
    debouncer = MotionDebouncer(cooldown_s=5.0, quiet_run_required=1)
    debouncer.observe(True, now=0.0)
    debouncer.observe(False, now=1.0)  # episode ends at t=1
    assert debouncer.observe(True, now=2.0) is False, "within the cooldown floor"
    assert debouncer.observe(True, now=6.5) is True, "past the cooldown floor"


@pytest.mark.parametrize(
    "kwargs", [{"cooldown_s": -1}, {"cooldown_s": 1.0, "quiet_run_required": 0}]
)
def test_debouncer_rejects_nonsense_configuration(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        MotionDebouncer(**kwargs)


def test_rising_edge_run_required_rejects_a_single_frame_blip() -> None:
    """The fix for a real camera's auto-exposure/gain jump: one frame crossing
    the area threshold with nobody in view must not fire an alarm on its own."""
    debouncer = MotionDebouncer(cooldown_s=0.0, rising_edge_run_required=3)
    assert debouncer.observe(True, now=0.0) is False
    assert debouncer.observe(False, now=1.0) is False  # blip ends, run resets
    assert debouncer.observe(True, now=2.0) is False  # starting over


def test_rising_edge_run_required_fires_once_the_run_is_sustained() -> None:
    debouncer = MotionDebouncer(cooldown_s=0.0, rising_edge_run_required=3)
    assert debouncer.observe(True, now=0.0) is False
    assert debouncer.observe(True, now=1.0) is False
    assert debouncer.observe(True, now=2.0) is True  # third consecutive frame
    assert debouncer.observe(True, now=3.0) is False  # already in the episode


def test_rising_edge_run_required_default_preserves_single_frame_trigger() -> None:
    """The default (1) is the original behaviour, correct for a low-noise
    source like the committed synthetic clip - no regression for that path."""
    debouncer = MotionDebouncer(cooldown_s=0.0)
    assert debouncer.observe(True, now=0.0) is True


def test_rising_edge_run_required_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError, match="rising_edge_run_required"):
        MotionDebouncer(cooldown_s=1.0, rising_edge_run_required=0)
