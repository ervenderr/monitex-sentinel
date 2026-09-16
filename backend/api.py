"""HTTP surface for the operator dashboard.

The live channel is SSE rather than a WebSocket: the dashboard only ever
receives, SSE reconnects on its own, and it survives proxies that mangle
upgrades. One less thing to hand-roll.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings, load_settings
from backend.runtime import SentinelRuntime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

DEFAULT_OPERATOR = "operator"
# Vite dev server; the built dashboard is served same-origin in production.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _sse(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _runtime(request: Request) -> SentinelRuntime:
    return request.app.state.runtime


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {
        "status": "ok",
        "stream_connected": runtime.metrics.stream_connected,
        "provider": runtime.provider.name,
        "subscribers": runtime.store.subscriber_count,
    }


@router.get("/alarms")
async def list_alarms(request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    return {"alarms": [r.model_dump(mode="json") for r in runtime.store.snapshot()]}


@router.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    return _runtime(request).metrics.snapshot().model_dump(mode="json")


@router.post("/alarms/{event_id}/acknowledge")
async def acknowledge(
    request: Request, event_id: str, by: str = Body(default=DEFAULT_OPERATOR, embed=True)
) -> dict[str, Any]:
    record = _runtime(request).store.acknowledge(event_id, by)
    if record is None:
        raise HTTPException(status_code=404, detail="alarm not found or not active")
    return record.model_dump(mode="json")


@router.post("/alarms/{event_id}/resolve")
async def resolve(
    request: Request, event_id: str, by: str = Body(default=DEFAULT_OPERATOR, embed=True)
) -> dict[str, Any]:
    record = _runtime(request).store.resolve(event_id, by)
    if record is None:
        raise HTTPException(status_code=404, detail="alarm not found or already resolved")
    return record.model_dump(mode="json")


async def _stream_events(runtime: SentinelRuntime, interval_s: float) -> AsyncIterator[str]:
    """Live channel for one dashboard client.

    Subscription is established *before* the snapshot is taken. Doing it the
    other way round leaves a window where an alarm arriving between the
    snapshot and the subscribe is never seen by this client. The cost of this
    ordering is that an alarm may appear in both the snapshot and a following
    update - harmless, because the client keys records by `event_id` and the
    second one simply overwrites the first. Duplicates are cheap; gaps are not.
    """
    merged: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=512)
    subscription = runtime.store.subscribe()  # registered before we snapshot

    async def pump_alarms() -> None:
        async for update in subscription:
            await merged.put(("alarm", update.model_dump(mode="json")))

    async def pump_metrics() -> None:
        while True:
            await asyncio.sleep(interval_s)
            await merged.put(("metrics", runtime.metrics.snapshot().model_dump(mode="json")))

    tasks = [asyncio.create_task(pump_alarms()), asyncio.create_task(pump_metrics())]
    try:
        yield _sse(
            "snapshot",
            {"alarms": [r.model_dump(mode="json") for r in runtime.store.snapshot()]},
        )
        yield _sse("metrics", runtime.metrics.snapshot().model_dump(mode="json"))
        while True:
            kind, payload = await merged.get()
            yield _sse(kind, payload)
    except asyncio.CancelledError:
        raise
    finally:
        subscription.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    runtime = _runtime(request)
    return StreamingResponse(
        _stream_events(runtime, runtime.settings.metrics_interval_s),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let a proxy buffer the live feed
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = SentinelRuntime(resolved)
        app.state.runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="Project Sentinel", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    _mount_dashboard(app)
    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built dashboard from the API process when it exists.

    One command to run the whole thing for a demo. During development the Vite
    dev server proxies /api here instead, so this simply does nothing until
    `npm run build` has been run.
    """
    index = DASHBOARD_DIR / "index.html"
    if not index.is_file():
        logger.info("dashboard build not found at %s; serving API only", DASHBOARD_DIR)
        return

    app.mount("/assets", StaticFiles(directory=DASHBOARD_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(index)

    logger.info("dashboard served at http://%s:%s/", "127.0.0.1", "8000")

