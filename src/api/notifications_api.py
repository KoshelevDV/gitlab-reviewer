"""Notifications API — test endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_config
from ..db import ReviewRecord
from ..notifier import notify

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.post("/test")
async def test_notification() -> JSONResponse:
    """
    Send a test notification using the current config.
    Returns 200 if the notification was dispatched (may still fail silently
    if the backend is unreachable — check logs for details).
    """
    cfg = get_config()
    if not cfg.notifications.enabled:
        return JSONResponse(
            {"status": "skipped", "detail": "Notifications are disabled"},
            status_code=200,
        )

    # Synthetic record so the notifier has something to send
    record = ReviewRecord(
        project_id="test",
        mr_iid=0,
        status="posted",
        mr_title="[TEST] gitlab-reviewer notification check",
        author="gitlab-reviewer",
        source_branch="feature/test",
        target_branch="main",
        review_text=(
            "This is a test notification from gitlab-reviewer.\n"
            "If you see this, your webhook is configured correctly. ✅"
        ),
        inline_count=0,
        auto_approved=False,
    )

    try:
        await notify(record, cfg.notifications)
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "detail": str(exc)},
            status_code=500,
        )

    return JSONResponse({"status": "sent"})
