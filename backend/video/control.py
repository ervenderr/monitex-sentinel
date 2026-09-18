"""Pause/resume control for the video worker, shared between the API and the
running worker loop.

The dashboard's earlier collapse toggle only removed the <img> tag from the
page - it never told the backend anything, so the camera stayed open and
detection kept running whether or not anyone was looking at the preview.
This closes that gap: pausing here actually releases the capture device (the
OS "camera in use" indicator goes off), not just hides a widget.
"""

from __future__ import annotations

import asyncio


class VideoControl:
    def __init__(self) -> None:
        self._paused = False
        self._resumed = asyncio.Event()
        self._resumed.set()  # running by default

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._resumed.clear()

    def resume(self) -> None:
        self._paused = False
        self._resumed.set()

    async def wait_until_resumed(self) -> None:
        await self._resumed.wait()
