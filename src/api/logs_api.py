"""Logs API — WebSocket /ws/logs + REST /api/v1/logs"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["logs"])

# LogBuffer instance injected at startup
_log_buffer = None


def set_log_buffer(buf) -> None:  # type: ignore[no-untyped-def]
    global _log_buffer
    _log_buffer = buf


@router.get("/api/v1/logs")
async def get_recent_logs(limit: int = 200) -> JSONResponse:
    """Return last N log lines as JSON array."""
    if _log_buffer is None:
        return JSONResponse([])
    lines = _log_buffer.backlog()
    return JSONResponse(lines[-limit:])


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    """
    WebSocket endpoint — streams log lines as JSON objects.
    On connect: sends backlog, then streams new lines in real-time.
    """
    await websocket.accept()
    logger.debug("WebSocket log client connected: %s", websocket.client)

    if _log_buffer is None:
        await websocket.close()
        return

    queue: asyncio.Queue[str] = _log_buffer.subscribe()

    # Send backlog
    for line in _log_buffer.backlog():
        try:
            await websocket.send_text(line)
        except Exception:  # noqa: BLE001
            break

    # Stream new lines
    try:
        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(line)
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await websocket.send_text('{"type":"ping"}')
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _log_buffer.unsubscribe(queue)
        logger.debug("WebSocket log client disconnected")
