"""Queue status API — /api/v1/queue"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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


class TriggerBody(BaseModel):
    project_id: int | str
    mr_iid: int


@router.post("/review")
async def trigger_review(body: TriggerBody) -> JSONResponse:
    """Manually enqueue a review for a specific MR."""
    if _queue_manager is None:
        raise HTTPException(status_code=503, detail="Queue not available")

    from ..queue_manager import ReviewJob
    job = ReviewJob(project_id=body.project_id, mr_iid=body.mr_iid)
    enqueued = await _queue_manager.enqueue(job)
    if not enqueued:
        raise HTTPException(
            status_code=429,
            detail="Queue full or MR already queued (same diff)",
        )
    return JSONResponse({"status": "queued", "job_id": job.id}, status_code=202)
