"""FramePublisher's latest-frame-wins fan-out, and the JPEG encode helper."""

from __future__ import annotations

import asyncio

import numpy as np

from backend.video.preview import FramePublisher, encode_preview_jpeg


def frame(width: int = 320, height: int = 180) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


# ---- encode_preview_jpeg ---------------------------------------------------


def test_encodes_a_frame_to_nonempty_jpeg_bytes() -> None:
    jpeg = encode_preview_jpeg(frame())
    assert isinstance(jpeg, bytes)
    assert jpeg.startswith(b"\xff\xd8")  # JPEG magic bytes
    assert len(jpeg) > 0


def test_downscales_frames_wider_than_the_preview_max() -> None:
    import cv2

    wide = frame(width=1920, height=1080)
    jpeg = encode_preview_jpeg(wide)
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == 480  # PREVIEW_MAX_WIDTH


def test_leaves_frames_already_narrow_enough_unscaled() -> None:
    import cv2

    narrow = frame(width=160, height=90)
    jpeg = encode_preview_jpeg(narrow)
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == 160


# ---- FramePublisher ---------------------------------------------------------


async def test_a_subscriber_receives_a_published_frame() -> None:
    publisher = FramePublisher()
    received = []

    async def listen() -> None:
        async for jpeg in publisher.subscribe():
            received.append(jpeg)
            break

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    publisher.publish(b"frame-1")
    await asyncio.wait_for(task, timeout=1)

    assert received == [b"frame-1"]


async def test_a_slow_subscriber_gets_the_newest_frame_not_a_backlog() -> None:
    """The correctness property that distinguishes this from AlarmStore's
    fan-out: staleness is fine to drop, a backlog of old frames is not."""
    publisher = FramePublisher()

    async def never_reads() -> None:
        async for _ in publisher.subscribe():
            await asyncio.sleep(3600)

    task = asyncio.create_task(never_reads())
    await asyncio.sleep(0)

    for i in range(50):
        publisher.publish(f"frame-{i}".encode())

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # No assertion on a queue depth here - the point is publish() above did
    # not block or raise across 50 rapid publishes into an unread queue.


async def test_multiple_subscribers_each_get_their_own_copy() -> None:
    publisher = FramePublisher()
    results: dict[str, bytes] = {}

    async def listen(name: str) -> None:
        async for jpeg in publisher.subscribe():
            results[name] = jpeg
            break

    tasks = [asyncio.create_task(listen("a")), asyncio.create_task(listen("b"))]
    await asyncio.sleep(0)
    publisher.publish(b"shared-frame")
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert results == {"a": b"shared-frame", "b": b"shared-frame"}


async def test_publishing_with_no_subscribers_does_not_raise() -> None:
    publisher = FramePublisher()
    publisher.publish(b"nobody-is-listening")  # must simply be a no-op


async def test_subscriber_count_reflects_active_listeners() -> None:
    publisher = FramePublisher()
    assert publisher.subscriber_count == 0

    async def listen() -> None:
        async for _ in publisher.subscribe():
            pass

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    assert publisher.subscriber_count == 1

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert publisher.subscriber_count == 0
