"""A controllable WebSocket event feed for ingest tests.

Handlers park on an explicit stop event rather than a bare `asyncio.Future`:
`Server.wait_closed()` waits for open handlers to finish, so a handler that can
never return turns shutdown into a hang.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from websockets.asyncio.server import serve


class FakeFeed:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"ws://127.0.0.1:{port}"
        self.connection_count = 0
        self._server: Any = None
        self._ctx: Any = None
        self._stop = asyncio.Event()

    async def start(self, messages: list[dict[str, Any]]) -> None:
        self._stop = asyncio.Event()

        async def handler(ws: Any) -> None:
            self.connection_count += 1
            try:
                for message in messages:
                    await ws.send(json.dumps(message))
                await self._stop.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                return

        self._ctx = serve(handler, "127.0.0.1", self.port)
        self._server = await self._ctx.__aenter__()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._stop.set()  # release handlers so wait_closed can complete
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2)
        except TimeoutError:
            pass
        self._server = None
        self._ctx = None
