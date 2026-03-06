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
from .slash_commands import execute_slash_command, parse_slash_command

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

        # 0. Body size guard (prevent memory exhaustion from oversized payloads)
        _MAX_BODY = 512 * 1024  # 512 KB — GitLab webhooks are well under 100 KB
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BODY:
            raise HTTPException(status_code=413, detail="Request body too large")

        # 1. Auth
        if not _verify_token(x_gitlab_token, cfg.gitlab.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook token")

        # 2. Event type filter
        if x_gitlab_event == "Note Hook":
            body = await request.json()
            return await _handle_note_hook(body, cfg)

        if x_gitlab_event != "Merge Request Hook":
            return JSONResponse({"status": "ignored", "reason": "not a merge request event"})

        body: dict[str, Any] = await request.json()
        attrs = body.get("object_attributes", {})
        action = attrs.get("action", "")

        if action not in _REVIEWED_ACTIONS:
            return JSONResponse({"status": "ignored", "reason": f"action '{action}' not reviewed"})

        # 3. Extract and validate identifiers
        raw_project_id = body.get("project", {}).get("id")
        raw_mr_iid = attrs.get("iid")

        # project_id must be a positive integer or non-empty string
        if raw_project_id is None:
            raise HTTPException(status_code=400, detail="Missing project.id")
        if isinstance(raw_project_id, int) and raw_project_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid project_id: must be positive")
        project_id = raw_project_id  # keep as int or str — both are valid

        # mr_iid must be a positive integer
        if not isinstance(raw_mr_iid, int) or raw_mr_iid <= 0:
            raise HTTPException(status_code=400, detail="Invalid or missing mr_iid")
        mr_iid: int = raw_mr_iid

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

    return router


async def _handle_note_hook(body: dict[str, Any], cfg) -> JSONResponse:  # type: ignore[no-untyped-def]
    """Process a GitLab Note Hook (MR comment) for slash commands."""
    attrs = body.get("object_attributes", {})
    note_body: str = attrs.get("note", "")
    noteable_type: str = attrs.get("noteable_type", "")

    # Only handle MR comments
    if noteable_type != "MergeRequest":
        return JSONResponse({"status": "ignored", "reason": "not an MR note"})

    cmd = parse_slash_command(note_body)
    if cmd is None:
        return JSONResponse({"status": "ignored", "reason": "not a slash command"})

    mr_info = body.get("merge_request", {})
    raw_project_id = body.get("project", {}).get("id")
    raw_mr_iid = mr_info.get("iid")

    if not raw_project_id or not isinstance(raw_mr_iid, int) or raw_mr_iid <= 0:
        return JSONResponse(
            {"status": "error", "reason": "invalid project/MR IDs"}, status_code=400
        )

    # Execute slash command asynchronously (background task)
    import asyncio

    asyncio.get_event_loop().create_task(_run_slash_command(cmd, raw_project_id, raw_mr_iid, cfg))
    return JSONResponse(
        {
            "status": "accepted",
            "command": cmd.name,
            "project_id": raw_project_id,
            "mr_iid": raw_mr_iid,
        }
    )


async def _run_slash_command(cmd, project_id, mr_iid, cfg) -> None:  # type: ignore[no-untyped-def]
    """Execute slash command and post reply as MR note (background task)."""
    from .gitlab_client import GitLabClient

    try:
        provider = cfg.active_provider()
        reply = await execute_slash_command(
            cmd=cmd,
            project_id=project_id,
            mr_iid=mr_iid,
            gitlab_url=cfg.gitlab.url,
            gitlab_token=cfg.gitlab_token or "",
            llm_base_url=provider.url if provider else "",
            llm_api_key=provider.api_key.get_secret_value() if provider else "",
            llm_model=cfg.model.name,
            llm_temperature=cfg.model.temperature,
            tls_verify=cfg.gitlab.tls_verify,
        )
        # Post reply as new MR note
        note_body = f"<!-- slash-command:{cmd.name} -->\n{reply}"
        gitlab = GitLabClient(
            cfg.gitlab.url, cfg.gitlab_token or "", tls_verify=cfg.gitlab.tls_verify
        )
        try:
            await gitlab.post_mr_note(project_id, mr_iid, note_body)
        finally:
            await gitlab.aclose()
        logger.info(
            "Slash command /%s reply posted: project=%s MR!%d",
            cmd.name,
            project_id,
            mr_iid,
        )
    except Exception:
        logger.exception("Slash command /%s failed: project=%s MR!%d", cmd.name, project_id, mr_iid)


def _verify_token(received: str | None, expected: str) -> bool:
    if not received or not expected:
        return not expected  # if no secret configured, allow all
    return hmac.compare_digest(
        received.encode("utf-8"),
        expected.encode("utf-8"),
    )
