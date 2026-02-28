"""Queue status API — /api/v1/queue"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])

# QueueManager instance injected at startup via set_queue_manager()
_queue_manager = None


def set_queue_manager(qm) -> None:  # type: ignore[no-untyped-def]
    global _queue_manager
    _queue_manager = qm


@router.get("")
async def queue_status() -> JSONResponse:
    if _queue_manager is None:
        return JSONResponse({"pending": 0, "active": 0, "done": 0, "errors": 0})
    return JSONResponse(_queue_manager.status())


@router.post("/drain")
async def drain_queue() -> JSONResponse:
    """Cancel all pending reviews (in-flight jobs finish naturally)."""
    if _queue_manager is None:
        return JSONResponse({"status": "no queue"})
    await _queue_manager.drain()
    return JSONResponse({"status": "drained"})
