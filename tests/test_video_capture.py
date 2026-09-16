"""Against the real committed demo clip - the only place the test suite
exercises actual video decode."""

from __future__ import annotations

import pytest

from backend.video.capture import LoopingVideoCapture, VideoSourceError

DEMO_CLIP = "assets/demo_camera.mp4"


def test_reads_grayscale_frames_at_the_requested_size() -> None:
    capture = LoopingVideoCapture(DEMO_CLIP, frame_size=(160, 90))
    try:
        frame = capture.read_gray()
        assert frame.shape == (90, 160)
        assert frame.dtype.name == "uint8"
    finally:
        capture.release()


def test_loops_past_the_end_of_the_clip() -> None:
    """The committed clip is exactly 600 frames; reading past that must not
    raise, and must keep producing valid frames rather than the last one."""
    capture = LoopingVideoCapture(DEMO_CLIP, frame_size=(80, 45))
    try:
        for _ in range(610):
            frame = capture.read_gray()
            assert frame.shape == (45, 80)
    finally:
        capture.release()


def test_missing_source_fails_clearly_rather_than_hanging() -> None:
    with pytest.raises(VideoSourceError, match="could not open"):
        LoopingVideoCapture("assets/does_not_exist.mp4")


def test_the_demo_clip_separates_motion_from_quiet() -> None:
    """Ground truth: the clip has known quiet and motion windows (see
    scripts/generate_demo_video.py). A regression in the clip or the capture
    pipeline that erased the motion signal would silently break every demo,
    so it is pinned here with the same diffing approach the detector uses."""
    import numpy as np

    fps = 15
    # Matches the pass windows in scripts/generate_demo_video.py, pulled in a
    # second past each edge so an off-by-one frame near a transition cannot
    # flip a sample from one bucket to the other.
    motion_windows = [(4.0, 6.0), (16.0, 19.0), (27.0, 33.0)]
    quiet_windows = [(0.0, 2.0), (8.0, 14.0), (21.0, 25.0), (35.0, 40.0)]

    def bucket(t: float) -> str | None:
        if any(a <= t < b for a, b in motion_windows):
            return "motion"
        if any(a <= t < b for a, b in quiet_windows):
            return "quiet"
        return None

    capture = LoopingVideoCapture(DEMO_CLIP, frame_size=(160, 90))
    try:
        previous = None
        quiet_diffs: list[float] = []
        motion_diffs: list[float] = []
        for frame_index in range(600):
            gray = capture.read_gray()
            if previous is not None:
                diff = np.abs(gray.astype(np.int16) - previous.astype(np.int16))
                fraction = float((diff > 18).mean())
                which = bucket(frame_index / fps)
                if which == "motion":
                    motion_diffs.append(fraction)
                elif which == "quiet":
                    quiet_diffs.append(fraction)
            previous = gray
    finally:
        capture.release()

    assert quiet_diffs and motion_diffs
    assert max(quiet_diffs) < 0.002, "quiet windows must stay essentially flat"
    assert min(motion_diffs) > 0.006, "motion windows must clear the detection threshold"
