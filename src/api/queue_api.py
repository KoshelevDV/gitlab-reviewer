"""Queue status API — /api/v1/queue"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/queue", tags=["queue"])

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


@router.post("/start")
async def start_queue() -> JSONResponse:
    """Restart review workers (after /drain or on initial setup)."""
    if _queue_manager is None:
        raise HTTPException(status_code=503, detail="Queue not available")
    count = await _queue_manager.restart()
    return JSONResponse({"status": "started", "workers": count})


class TriggerBody(BaseModel):
    project_id: int | str
    mr_iid: int
    dry_run: bool = False
    stream: bool = False  # if True, pre-register SSE stream and include stream_url


@router.post("/review")
async def trigger_review(
    body: TriggerBody,
    dry_run: bool = Query(default=False, description="Validate MR without enqueuing"),
) -> JSONResponse:
    """Manually enqueue a review for a specific MR.

    dry_run=true  — validates MR via GitLab API without enqueuing.
                    Can be passed as query param (?dry_run=true) or in JSON body.
    stream=true   — pre-registers SSE queue, returns stream_url.
    """
    if _queue_manager is None:
        raise HTTPException(status_code=503, detail="Queue not available")

    # Accept dry_run from either query param or body
    if dry_run or body.dry_run:
        from ..config import get_config
        from ..gitlab_client import GitLabClient

        cfg = get_config()
        token = cfg.gitlab_token
        if not token:
            raise HTTPException(status_code=503, detail="GitLab token not configured")
        client = GitLabClient(cfg.gitlab.url, token, tls_verify=cfg.gitlab.tls_verify)
        try:
            mr = await client.get_mr(body.project_id, body.mr_iid)
            return JSONResponse(
                {
                    "status": "dry_run",
                    "project_id": str(body.project_id),
                    "mr_iid": body.mr_iid,
                    "mr_title": mr.title,
                    "mr_url": mr.web_url,
                    "is_draft": mr.is_draft,
                }
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"MR not found: {exc}") from exc
        finally:
            await client.aclose()

    from ..queue_manager import ReviewJob
    from ..reviewer import register_stream

    job = ReviewJob(project_id=body.project_id, mr_iid=body.mr_iid)
    enqueued = await _queue_manager.enqueue(job)
    if not enqueued:
        raise HTTPException(
            status_code=429,
            detail="Queue full or MR already queued (same diff)",
        )

    resp: dict = {"status": "queued", "job_id": job.id}
    if body.stream:
        register_stream(job.id)
        resp["stream_url"] = f"/api/v1/queue/review/{job.id}/stream"

    return JSONResponse(resp, status_code=202)


@router.get("/review/{job_id}/stream")
async def stream_review(job_id: int) -> StreamingResponse:
    """SSE endpoint: stream LLM review chunks for a job triggered with stream=true.

    Replays any already-buffered chunks, then waits for new ones.
    Sends 'event: done' when the review is complete or job_id not found.
    """
    from ..reviewer import _live_streams, _stream_buffers, unregister_stream

    async def event_generator():
        # Replay already-received chunks (for clients connecting slightly late)
        for chunk in list(_stream_buffers.get(job_id, [])):
            yield f"data: {json.dumps({'text': chunk})}\n\n"

        q = _live_streams.get(job_id)
        if q is None:
            yield "event: done\ndata: {}\n\n"
            return

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=300.0)
                except TimeoutError:
                    yield 'event: error\ndata: {"detail": "timeout"}\n\n'
                    break
                if chunk is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        finally:
            unregister_stream(job_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
