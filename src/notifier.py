"""
Notification dispatcher.

Sends review result notifications to configured webhooks.
Fail-open: errors are logged but never propagate to callers.

Supported formats:
  - slack    — Slack Incoming Webhook (blocks + text fallback)
  - telegram — Telegram Bot API (sendMessage with MarkdownV2)
  - generic  — plain JSON payload (works with n8n, Zapier, custom handlers)
"""

from __future__ import annotations

import logging

import httpx

from .config import NotificationConfig, NotificationFormat
from .db import ReviewRecord

logger = logging.getLogger(__name__)

# Timeout for notification HTTP calls (non-critical path)
_NOTIFY_TIMEOUT = 10.0


async def notify(record: ReviewRecord, cfg: NotificationConfig) -> None:
    """
    Dispatch a notification for *record* if the config enables it.

    Always returns None; logs errors at WARNING level and continues.
    """
    if not cfg.enabled:
        return

    event = record.status  # "posted" | "error" | "skipped" | "dry_run"
    if not _should_notify(event, cfg):
        return

    try:
        if cfg.format == NotificationFormat.slack:
            await _send_slack(record, cfg)
        elif cfg.format == NotificationFormat.telegram:
            await _send_telegram(record, cfg)
        else:
            await _send_generic(record, cfg)
    except Exception:
        logger.warning(
            "Notification failed for project=%s MR!%d status=%s",
            record.project_id,
            record.mr_iid,
            record.status,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _should_notify(status: str, cfg: NotificationConfig) -> bool:
    if status == "posted" and cfg.on_posted:
        return True
    if status == "error" and cfg.on_error:
        return True
    if status == "skipped" and cfg.on_skipped:
        return True
    return False


def _status_emoji(status: str) -> str:
    return {
        "posted": "✅",
        "dry_run": "🔍",
        "error": "❌",
        "skipped": "⏭️",
    }.get(status, "ℹ️")


def _short_summary(review_text: str, max_chars: int = 300) -> str:
    """Return the first *max_chars* chars of the review, trimmed to a sentence."""
    if not review_text:
        return ""
    text = review_text.strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to cut at last sentence boundary
    for sep in (".\n", "\n", ". ", " "):
        idx = truncated.rfind(sep)
        if idx > max_chars // 2:
            return truncated[: idx + 1].strip() + " …"
    return truncated.strip() + " …"


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


async def _send_slack(record: ReviewRecord, cfg: NotificationConfig) -> None:
    if not cfg.webhook_url:
        logger.warning("Slack notification skipped: webhook_url not configured")
        return

    emoji = _status_emoji(record.status)
    header = f"{emoji} MR Review — {record.status.upper()}"
    fields = [
        {"type": "mrkdwn", "text": f"*Project:*\n{record.project_id}"},
        {"type": "mrkdwn", "text": f"*MR !{record.mr_iid}:*\n{record.mr_title}"},
        {"type": "mrkdwn", "text": f"*Author:*\n{record.author}"},
        {"type": "mrkdwn", "text": f"*Branch:*\n{record.source_branch} → {record.target_branch}"},
    ]
    if record.status == "skipped" and record.skip_reason:
        fields.append({"type": "mrkdwn", "text": f"*Skip reason:*\n{record.skip_reason}"})
    if record.inline_count:
        fields.append({"type": "mrkdwn", "text": f"*Inline comments:*\n{record.inline_count}"})

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "fields": fields},
    ]
    if record.status in ("posted", "dry_run") and record.review_text:
        snippet = _short_summary(record.review_text)
        if snippet:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```{snippet}```"},
                }
            )

    payload = {
        "text": header,  # fallback for notifications
        "blocks": blocks,
    }

    async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT) as client:
        resp = await client.post(cfg.webhook_url, json=payload)
        resp.raise_for_status()
    logger.info(
        "Slack notification sent: project=%s MR!%d status=%s",
        record.project_id,
        record.mr_iid,
        record.status,
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def _escape_md2(text: str) -> str:
    """Escape characters special in Telegram MarkdownV2."""
    specials = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in specials else c for c in text)


async def _send_telegram(record: ReviewRecord, cfg: NotificationConfig) -> None:
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        logger.warning("Telegram notification skipped: bot_token or chat_id not configured")
        return

    emoji = _status_emoji(record.status)
    lines = [
        f"{emoji} *MR Review \\— {_escape_md2(record.status.upper())}*",
        f"📁 Project: `{_escape_md2(str(record.project_id))}`",
        f"🔀 MR \\!{record.mr_iid}: {_escape_md2(record.mr_title)}",
        f"👤 Author: {_escape_md2(record.author)}",
        f"🌿 `{_escape_md2(record.source_branch)}` → `{_escape_md2(record.target_branch)}`",
    ]
    if record.status == "skipped" and record.skip_reason:
        lines.append(f"⏭️ Reason: {_escape_md2(record.skip_reason)}")
    if record.inline_count:
        lines.append(f"💬 Inline comments: {record.inline_count}")
    if record.status in ("posted", "dry_run") and record.review_text:
        snippet = _short_summary(record.review_text, max_chars=200)
        if snippet:
            lines.append(f"\n```\n{snippet}\n```")

    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": "\n".join(lines),
        "parse_mode": "MarkdownV2",
    }

    async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    logger.info(
        "Telegram notification sent: project=%s MR!%d status=%s",
        record.project_id,
        record.mr_iid,
        record.status,
    )


# ---------------------------------------------------------------------------
# Generic JSON webhook
# ---------------------------------------------------------------------------


async def _send_generic(record: ReviewRecord, cfg: NotificationConfig) -> None:
    if not cfg.webhook_url:
        logger.warning("Generic notification skipped: webhook_url not configured")
        return

    payload = {
        "event": "review_complete",
        "status": record.status,
        "project_id": record.project_id,
        "mr_iid": record.mr_iid,
        "mr_title": record.mr_title,
        "author": record.author,
        "source_branch": record.source_branch,
        "target_branch": record.target_branch,
        "skip_reason": record.skip_reason,
        "inline_count": record.inline_count,
        "auto_approved": record.auto_approved,
        "created_at": record.created_at,
        "review_snippet": _short_summary(record.review_text or ""),
    }

    async with httpx.AsyncClient(timeout=_NOTIFY_TIMEOUT) as client:
        resp = await client.post(cfg.webhook_url, json=payload)
        resp.raise_for_status()
    logger.info(
        "Generic webhook notification sent: project=%s MR!%d status=%s",
        record.project_id,
        record.mr_iid,
        record.status,
    )
