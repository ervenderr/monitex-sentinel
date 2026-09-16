"""The video worker against fake capture backends - no real video file here,
except in the one test that proves decode latency does not reach the event
loop (test_video_capture.py already covers the real clip's content)."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from backend.intake import EventIntake
from backend.metrics import Metrics
from backend.pipeline import EventPipeline
from backend.store import AlarmStore
from backend.video.capture import VideoSourceError
from backend.video.motion import MotionReading
from backend.video.worker import video_worker


class FakeCapture:
    """Cycles through a fixed sequence of frames. `read_delay_s` simulates
    decode latency, so tests can prove it does not stall the caller."""

    def __init__(self, frames: list[np.ndarray], *, read_delay_s: float = 0.0) -> None:
        self._frames = frames
        self._index = 0
        self._delay = read_delay_s
        self.reads = 0

    def read_gray(self) -> np.ndarray:
        if self._delay:
            time.sleep(self._delay)  # a real blocking call, as cv2's would be
        self.reads += 1
        frame = self._frames[self._index % len(self._frames)]
        self._index += 1
        return frame

    def release(self) -> None:
        pass


class ScriptedDetector:
    """Ignores frame content; replays a pre-scripted sequence of readings.

    Debounce tests need precise control over the detected/not-detected
    sequence a real frame-diff detector structurally cannot produce - it
    cannot sustain "detected" across identical consecutive frames, since two
    identical frames diff to zero by definition. That is a property of
    frame-diffing (documented on FrameDiffDetector), not something to work
    around with elaborate pixel engineering; this isolates the debounce logic
    from it instead. The last entry repeats once the script is exhausted, so
    a worker that ticks a few extra times before being cancelled does not
    fabricate spurious episodes.
    """

    def __init__(self, sequence: list[bool], *, capture: FakeCapture | None = None) -> None:
        self._sequence = sequence
        self._index = 0
        self._capture = capture
        self.calls = 0
        self.reads_before_first_call: int | None = None

    def reset(self) -> None:
        pass

    def update(self, _gray: np.ndarray) -> MotionReading:
        self.calls += 1
        if self.reads_before_first_call is None and self._capture is not None:
            self.reads_before_first_call = self._capture.reads
        detected = self._sequence[min(self._index, len(self._sequence) - 1)]
        self._index += 1
        return MotionReading(
            detected=detected,
            changed_fraction=0.05 if detected else 0.0,
            confidence=0.8 if detected else 0.0,
            bbox=None,
        )


def quiet_frame(shape=(90, 160)) -> np.ndarray:
    return np.full(shape, 40, dtype=np.uint8)


def motion_frame(shape=(90, 160)) -> np.ndarray:
    frame = quiet_frame(shape)
    frame[20:70, 40:100] = 220
    return frame


@pytest.fixture
def pipeline() -> EventPipeline:
    return EventPipeline(maxsize=32, backpressure_timeout_s=0.5)


@pytest.fixture
def store() -> AlarmStore:
    return AlarmStore(capacity=100)


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def intake(pipeline: EventPipeline, store: AlarmStore, metrics: Metrics) -> EventIntake:
    return EventIntake(pipeline=pipeline, store=store, metrics=metrics)


async def _run(coro_factory, *, run_for_s: float) -> asyncio.Task:
    task = asyncio.create_task(coro_factory())
    await asyncio.sleep(run_for_s)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return task


async def _until(predicate, limit_s: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def make_worker(*, capture: FakeCapture, intake: EventIntake, metrics: Metrics, **kwargs):
    def constructor(source, *, frame_size):
        # Same call shape as the real LoopingVideoCapture(source, frame_size=...):
        # positional source, keyword-only frame_size. A regression that calls it
        # any other way (e.g. both positional, which run_in_executor cannot
        # express for a kwarg) fails here with a TypeError, the same way it did
        # in production before this signature was pinned.
        assert isinstance(source, str)
        assert isinstance(frame_size, tuple)
        return capture

    async def run():
        # Patch just the open call so the rest of the worker's real logic -
        # executor usage, debounce, metrics, error handling - runs unmodified.
        import backend.video.worker as worker_module

        original = worker_module.LoopingVideoCapture
        worker_module.LoopingVideoCapture = constructor  # type: ignore[assignment]
        try:
            await video_worker(
                source="fake", site_id="site-100", zone="lobby",
                intake=intake, metrics=metrics, **kwargs,
            )
        finally:
            worker_module.LoopingVideoCapture = original

    return run


async def test_motion_frames_emit_a_camera_event(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    capture = FakeCapture([quiet_frame(), motion_frame(), motion_frame()])
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        sample_interval_s=0.01, cooldown_s=100,
    )
    await _run(run, run_for_s=0.2)

    board = store.snapshot()
    assert len(board) == 1
    record = board[0]
    assert record.event.source == "camera"
    assert record.event.type == "motion_detected"
    assert record.event.confidence is not None and record.event.confidence > 0
    assert record.event.site_id == "site-100"
    assert record.event.zone == "lobby"
    assert metrics.video_events_emitted == 1


async def test_quiet_frames_emit_nothing(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    capture = FakeCapture([quiet_frame()])
    run = make_worker(capture=capture, intake=intake, metrics=metrics, sample_interval_s=0.01)
    await _run(run, run_for_s=0.15)

    assert store.snapshot() == []
    assert metrics.video_frames_sampled > 0
    assert metrics.video_events_emitted == 0


async def test_sustained_motion_fires_once_even_past_the_cooldown_window(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    """The bug this pins: a timer-based debounce re-arms mid-episode once the
    episode outlasts the cooldown, so a single long crossing becomes several
    alarms. Edge-triggered debounce must not do that, however long motion runs
    relative to the configured cooldown - here, 40 consecutive detected
    readings against a cooldown of essentially zero."""
    capture = FakeCapture([quiet_frame()])
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        detector=ScriptedDetector([True] * 40),
        sample_interval_s=0.005, cooldown_s=0.001,
    )
    await _run(run, run_for_s=0.3)

    assert metrics.video_events_emitted == 1, "one motion episode must not become many alarms"


async def test_a_genuinely_new_episode_after_quiet_fires_again(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    # Two motion episodes, cleanly separated by two consecutive quiet readings
    # (the default quiet_run_required) - a real gap, not just a single frame
    # of flicker at a boundary.
    script = [True, True, False, False, True, True, True, False, False]
    capture = FakeCapture([quiet_frame()])
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        detector=ScriptedDetector(script), sample_interval_s=0.005, cooldown_s=0.0,
    )
    await _run(run, run_for_s=0.15)

    assert metrics.video_events_emitted == 2, "each separated episode should alarm exactly once"


async def test_bbox_metadata_is_normalised_into_the_event(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    capture = FakeCapture([quiet_frame(), motion_frame()])
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        sample_interval_s=0.01, cooldown_s=100, frame_size=(160, 90),
    )
    await _run(run, run_for_s=0.15)

    record = store.snapshot()[0]
    bbox = record.event.metadata.get("bbox")
    assert bbox is not None
    assert 0 <= bbox["x"] <= 1 and 0 <= bbox["y"] <= 1
    assert 0 < bbox["w"] <= 1 and 0 < bbox["h"] <= 1


async def test_survives_a_source_that_will_not_open(
    intake: EventIntake, metrics: Metrics
) -> None:
    import backend.video.worker as worker_module

    def failing_opener(*_args, **_kwargs):
        raise VideoSourceError("no such device")

    original = worker_module.LoopingVideoCapture
    worker_module.LoopingVideoCapture = failing_opener  # type: ignore[assignment]
    try:
        task = asyncio.create_task(
            video_worker(
                source="nope", site_id="s", zone="z", intake=intake, metrics=metrics,
                sample_interval_s=0.01, reconnect_backoff_s=0.02,
            )
        )
        assert await _until(lambda: metrics.video_connected is False)
        await asyncio.sleep(0.1)
        assert not task.done(), "an unopenable source must be retried, not crash the worker"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        worker_module.LoopingVideoCapture = original


async def test_decode_latency_never_reaches_the_event_loop(
    intake: EventIntake, metrics: Metrics
) -> None:
    """The brief's requirement, made concrete: keep decoding off the hot path
    so it never stalls ingestion. Simulates a slow (300ms) decode and, while
    the video worker is running against it, measures the jitter of an
    unrelated fast asyncio timer. If frame reads happened on the loop instead
    of a thread, every tick would stall for up to 300ms; run_in_executor
    keeps the loop free the whole time.
    """
    capture = FakeCapture([quiet_frame()], read_delay_s=0.3)
    run = make_worker(capture=capture, intake=intake, metrics=metrics, sample_interval_s=0.01)
    worker_task = asyncio.create_task(run())

    tick_gaps: list[float] = []
    last = time.perf_counter()
    for _ in range(20):
        await asyncio.sleep(0.02)
        now = time.perf_counter()
        tick_gaps.append(now - last)
        last = now

    worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)

    assert capture.reads >= 1, "the slow capture must actually have been exercised"
    worst_gap_ms = max(tick_gaps) * 1000
    assert worst_gap_ms < 50, (
        f"a 20ms timer stalled to {worst_gap_ms:.0f}ms while a 300ms decode was in "
        "flight - decoding is blocking the event loop instead of running in a thread"
    )


# ---- settings -> detector wiring -------------------------------------------


def test_runtime_actually_applies_the_configured_thresholds() -> None:
    """Regression: video_pixel_threshold and video_area_threshold existed as
    Settings fields and in .env.example, but nothing constructed the detector
    from them - the video worker always used FrameDiffDetector()'s hardcoded
    defaults. Discovered live, against a real webcam whose auto-exposure noise
    made the (apparently set) threshold override do nothing. This pins the
    connection all the way from Settings to the detector actually used."""
    from backend.config import Settings
    from backend.runtime import SentinelRuntime
    from backend.triage.base import StubProvider

    settings = Settings(
        _env_file=None,
        llm_provider="stub",
        video_enabled=False,  # do not actually open a capture for this test
        video_pixel_threshold=42,
        video_area_threshold=0.31,
    )
    runtime = SentinelRuntime(settings, provider=StubProvider())
    detector = runtime.build_video_detector()

    assert detector.pixel_threshold == 42
    assert detector.area_threshold == 0.31


async def test_warmup_frames_are_discarded_before_detection_starts(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    """A fresh camera's auto-exposure/white-balance has not converged yet; the
    warmup burst must be read (and discarded) before the detector sees
    anything. Proven by recording how many capture reads had already happened
    the first time the detector was ever called - it must be exactly
    warmup_frames + 1 (the warmup burst, then the first real sampled frame),
    never fewer, however the event loop happens to schedule things."""
    capture = FakeCapture([quiet_frame()])
    detector = ScriptedDetector([True], capture=capture)
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        detector=detector, sample_interval_s=0.02, warmup_frames=5,
    )
    await _run(run, run_for_s=0.05)

    assert detector.calls >= 1, "the worker must have gotten past warmup at all"
    assert detector.reads_before_first_call == 6  # 5 warmup + the 1 real frame


async def test_warmup_count_is_configurable_and_zero_skips_it(
    intake: EventIntake, store: AlarmStore, metrics: Metrics
) -> None:
    capture = FakeCapture([quiet_frame()])
    run = make_worker(
        capture=capture, intake=intake, metrics=metrics,
        detector=ScriptedDetector([True]),
        sample_interval_s=0.02, warmup_frames=0,
    )
    await _run(run, run_for_s=0.03)

    assert len(store.snapshot()) == 1, "with no warmup, the first real frame fires immediately"
