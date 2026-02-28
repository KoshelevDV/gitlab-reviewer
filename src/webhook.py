"""
FastAPI webhook handler.

Security:
  - Constant-time comparison of X-Gitlab-Token header
  - Only MR events trigger reviews (open / update / reopen)
  - Reviews dispatched to QueueManager (not run inline)
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import get_config
from .queue_manager import QueueManager, ReviewJob

logger = logging.getLogger(__name__)

_REVIEWED_ACTIONS = {"open", "update", "reopen"}
_queue: QueueManager | None = None


def set_queue_manager(q: QueueManager) -> None:
    global _queue
    _queue = q


def make_webhook_router() -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/gitlab")
    async def gitlab_webhook(
        request: Request,
        x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
        x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    ) -> JSONResponse:
        cfg = get_config()

        # 1. Auth
        if not _verify_token(x_gitlab_token, cfg.gitlab.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook token")

        # 2. Event type filter
        if x_gitlab_event != "Merge Request Hook":
            return JSONResponse({"status": "ignored", "reason": "not a merge request event"})

        body: dict[str, Any] = await request.json()
        attrs = body.get("object_attributes", {})
        action = attrs.get("action", "")

        if action not in _REVIEWED_ACTIONS:
            return JSONResponse({"status": "ignored", "reason": f"action '{action}' not reviewed"})

        # 3. Extract identifiers
        project_id = body.get("project", {}).get("id")
        mr_iid = attrs.get("iid")

        if not project_id or not mr_iid:
            raise HTTPException(status_code=400, detail="Missing project_id or mr_iid")

        # 4. Enqueue
        if _queue is None:
            raise HTTPException(status_code=503, detail="Review queue not initialised")

        job = ReviewJob(
            project_id=project_id,
            mr_iid=mr_iid,
            event_action=action,
        )
        enqueued = await _queue.enqueue(job)
        status = "accepted" if enqueued else "deduped_or_full"

        return JSONResponse({"status": status, "project_id": project_id, "mr_iid": mr_iid})

    @router.get("/health")
    async def health() -> JSONResponse:
        q = _queue.status() if _queue else {}
        return JSONResponse({"status": "ok", "queue": q})

    return router


def _verify_token(received: str | None, expected: str) -> bool:
    if not received or not expected:
        return not expected  # if no secret configured, allow all
    return hmac.compare_digest(
        received.encode("utf-8"),
        expected.encode("utf-8"),
    )
