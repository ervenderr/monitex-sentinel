"""VideoControl: pause must actually release the capture device, not just
stop rendering - the bug this exists to fix was a UI toggle that hid the
preview <img> but never told the backend anything."""

from __future__ import annotations

import asyncio

from backend.video.control import VideoControl


def test_starts_running_not_paused() -> None:
    assert VideoControl().paused is False


def test_pause_sets_paused_state() -> None:
    control = VideoControl()
    control.pause()
    assert control.paused is True


def test_resume_clears_paused_state() -> None:
    control = VideoControl()
    control.pause()
    control.resume()
    assert control.paused is False


async def test_wait_until_resumed_blocks_while_paused() -> None:
    control = VideoControl()
    control.pause()
    task = asyncio.create_task(control.wait_until_resumed())
    await asyncio.sleep(0.05)
    assert not task.done(), "must actually block while paused"
    control.resume()
    await asyncio.wait_for(task, timeout=1)


async def test_wait_until_resumed_returns_immediately_when_not_paused() -> None:
    control = VideoControl()
    await asyncio.wait_for(control.wait_until_resumed(), timeout=0.1)
