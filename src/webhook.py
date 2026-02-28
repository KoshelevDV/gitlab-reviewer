"""
FastAPI webhook handler.

Security:
  - HMAC-SHA256 / constant-time comparison of X-Gitlab-Token header
  - Only MR events (opened, updated, approved) trigger a review
  - Runs review in background so webhook returns 200 immediately
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .reviewer import Reviewer

logger = logging.getLogger(__name__)

# GitLab sends plain token (not HMAC) in X-Gitlab-Token.
# We do constant-time comparison to prevent timing attacks.
_REVIEWED_ACTIONS = {"open", "update", "reopen"}


def make_webhook_router(reviewer: Reviewer, webhook_secret: str):
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/webhook/gitlab")
    async def gitlab_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
        x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    ) -> JSONResponse:
        # ----------------------------------------------------------------
        # 1. Auth — constant-time token comparison
        # ----------------------------------------------------------------
        if not _verify_token(x_gitlab_token, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook token")

        # ----------------------------------------------------------------
        # 2. Only process Merge Request Hook events
        # ----------------------------------------------------------------
        if x_gitlab_event != "Merge Request Hook":
            return JSONResponse({"status": "ignored", "reason": "not a merge request event"})

        body: dict[str, Any] = await request.json()
        action = body.get("object_attributes", {}).get("action", "")

        if action not in _REVIEWED_ACTIONS:
            return JSONResponse({"status": "ignored", "reason": f"action '{action}' not reviewed"})

        # ----------------------------------------------------------------
        # 3. Extract identifiers
        # ----------------------------------------------------------------
        project_id = body.get("project", {}).get("id")
        mr_iid = body.get("object_attributes", {}).get("iid")

        if not project_id or not mr_iid:
            raise HTTPException(status_code=400, detail="Missing project_id or mr_iid")

        # ----------------------------------------------------------------
        # 4. Run review in background (webhook must return fast)
        # ----------------------------------------------------------------
        background_tasks.add_task(_run_review, reviewer, project_id, mr_iid)
        return JSONResponse({"status": "accepted", "project_id": project_id, "mr_iid": mr_iid})

    @router.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return router


async def _run_review(reviewer: Reviewer, project_id: int, mr_iid: int) -> None:
    try:
        result = await reviewer.review_mr(project_id, mr_iid)
        if result.skipped:
            logger.info(
                "Review skipped for project=%s MR!%d: %s",
                project_id, mr_iid, result.skip_reason,
            )
        else:
            logger.info(
                "Review posted for project=%s MR!%d (fingerprint=%s)",
                project_id, mr_iid, result.fingerprint[:12],
            )
    except Exception:
        logger.exception(
            "Review failed for project=%s MR!%d", project_id, mr_iid
        )


def _verify_token(received: str | None, expected: str) -> bool:
    """Constant-time comparison of webhook token."""
    if not received:
        return False
    return hmac.compare_digest(
        received.encode("utf-8"),
        expected.encode("utf-8"),
    )
