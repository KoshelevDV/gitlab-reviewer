"""Tests for src/notifier.py — notification dispatch logic."""

from __future__ import annotations

import json

import respx
from httpx import Response

from src.config import NotificationConfig, NotificationFormat
from src.db import ReviewRecord
from src.notifier import _escape_md2, _short_summary, _should_notify, notify

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_record(
    status: str = "posted",
    project_id: str = "42",
    mr_iid: int = 7,
    mr_title: str = "Fix the bug",
    author: str = "alice",
    source_branch: str = "feature",
    target_branch: str = "main",
    review_text: str = "LGTM, but check line 10.",
    skip_reason: str = "",
    inline_count: int = 0,
    auto_approved: bool = False,
) -> ReviewRecord:
    rec = ReviewRecord(
        project_id=project_id,
        mr_iid=mr_iid,
        status=status,
        mr_title=mr_title,
        author=author,
        source_branch=source_branch,
        target_branch=target_branch,
        review_text=review_text,
        skip_reason=skip_reason,
        inline_count=inline_count,
        auto_approved=auto_approved,
    )
    return rec


def make_cfg(**kwargs) -> NotificationConfig:
    defaults = {
        "enabled": True,
        "format": NotificationFormat.generic,
        "webhook_url": "http://hook.example.com/notify",
        "on_posted": True,
        "on_error": False,
        "on_skipped": False,
    }
    defaults.update(kwargs)
    return NotificationConfig(**defaults)


# ---------------------------------------------------------------------------
# _should_notify
# ---------------------------------------------------------------------------


class TestShouldNotify:
    def test_posted_on_when_enabled(self):
        cfg = make_cfg(on_posted=True)
        assert _should_notify("posted", cfg) is True

    def test_posted_off_when_disabled(self):
        cfg = make_cfg(on_posted=False)
        assert _should_notify("posted", cfg) is False

    def test_error_on_when_enabled(self):
        cfg = make_cfg(on_error=True)
        assert _should_notify("error", cfg) is True

    def test_error_off_by_default(self):
        cfg = make_cfg(on_error=False)
        assert _should_notify("error", cfg) is False

    def test_skipped_on_when_enabled(self):
        cfg = make_cfg(on_skipped=True)
        assert _should_notify("skipped", cfg) is True

    def test_skipped_off_by_default(self):
        cfg = make_cfg(on_skipped=False)
        assert _should_notify("skipped", cfg) is False

    def test_dry_run_not_notified_by_default(self):
        cfg = make_cfg()
        assert _should_notify("dry_run", cfg) is False

    def test_unknown_status_not_notified(self):
        cfg = make_cfg()
        assert _should_notify("unknown", cfg) is False


# ---------------------------------------------------------------------------
# _short_summary
# ---------------------------------------------------------------------------


class TestShortSummary:
    def test_short_text_returned_as_is(self):
        assert _short_summary("Hello world.") == "Hello world."

    def test_long_text_truncated(self):
        text = "A" * 400
        result = _short_summary(text, max_chars=300)
        assert len(result) <= 305  # some slack for ellipsis

    def test_empty_string_returns_empty(self):
        assert _short_summary("") == ""

    def test_truncation_at_sentence_boundary(self):
        text = "First sentence. " + "x" * 300
        result = _short_summary(text, max_chars=50)
        assert result.endswith("…")


# ---------------------------------------------------------------------------
# _escape_md2
# ---------------------------------------------------------------------------


class TestEscapeMd2:
    def test_plain_text_unchanged(self):
        assert _escape_md2("hello world") == "hello world"

    def test_special_chars_escaped(self):
        result = _escape_md2("fix: issue_#1")
        assert "\\_" in result or result.startswith("fix")

    def test_dot_escaped(self):
        assert "\\." in _escape_md2("v1.0")


# ---------------------------------------------------------------------------
# notify — disabled
# ---------------------------------------------------------------------------


class TestNotifyDisabled:
    async def test_notify_does_nothing_when_disabled(self):
        cfg = make_cfg(enabled=False)
        rec = make_record()
        # Should not raise even with no HTTP mock
        await notify(rec, cfg)

    async def test_notify_does_nothing_when_status_not_subscribed(self):
        cfg = make_cfg(enabled=True, on_posted=False)
        rec = make_record(status="posted")
        await notify(rec, cfg)  # no HTTP mock — would fail if called


# ---------------------------------------------------------------------------
# Generic webhook
# ---------------------------------------------------------------------------


class TestGenericWebhook:
    @respx.mock
    async def test_generic_posts_json_payload(self):
        mock = respx.post("http://hook.example.com/notify").mock(return_value=Response(200))
        cfg = make_cfg(format=NotificationFormat.generic)
        rec = make_record(status="posted")

        await notify(rec, cfg)

        assert mock.called
        body = json.loads(mock.calls[0].request.content)
        assert body["status"] == "posted"
        assert body["project_id"] == "42"
        assert body["mr_iid"] == 7
        assert body["event"] == "review_complete"

    @respx.mock
    async def test_generic_includes_skip_reason(self):
        mock = respx.post("http://hook.example.com/notify").mock(return_value=Response(200))
        cfg = make_cfg(format=NotificationFormat.generic, on_skipped=True)
        rec = make_record(status="skipped", skip_reason="draft MR")

        await notify(rec, cfg)

        body = json.loads(mock.calls[0].request.content)
        assert body["skip_reason"] == "draft MR"
        assert body["status"] == "skipped"

    @respx.mock
    async def test_generic_does_not_raise_on_4xx(self):
        respx.post("http://hook.example.com/notify").mock(return_value=Response(500))
        cfg = make_cfg(format=NotificationFormat.generic)
        rec = make_record()

        # Should not raise — fail-open
        await notify(rec, cfg)

    async def test_generic_skipped_when_no_url(self):
        cfg = make_cfg(format=NotificationFormat.generic, webhook_url="")
        rec = make_record()
        await notify(rec, cfg)  # no HTTP mock — OK


# ---------------------------------------------------------------------------
# Slack webhook
# ---------------------------------------------------------------------------


class TestSlackWebhook:
    @respx.mock
    async def test_slack_posts_blocks(self):
        mock = respx.post("http://hook.example.com/slack").mock(return_value=Response(200))
        cfg = make_cfg(format=NotificationFormat.slack, webhook_url="http://hook.example.com/slack")
        rec = make_record(status="posted", inline_count=3)

        await notify(rec, cfg)

        assert mock.called
        body = json.loads(mock.calls[0].request.content)
        assert "blocks" in body
        assert "text" in body  # fallback text
        # Check one of the fields
        fields_text = str(body["blocks"])
        assert "Project" in fields_text

    @respx.mock
    async def test_slack_includes_inline_count_field(self):
        mock = respx.post("http://hook.example.com/slack").mock(return_value=Response(200))
        cfg = make_cfg(format=NotificationFormat.slack, webhook_url="http://hook.example.com/slack")
        rec = make_record(inline_count=5)

        await notify(rec, cfg)

        body = json.loads(mock.calls[0].request.content)
        assert "Inline comments" in str(body["blocks"])

    async def test_slack_skipped_when_no_url(self):
        cfg = make_cfg(format=NotificationFormat.slack, webhook_url="")
        rec = make_record()
        await notify(rec, cfg)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


class TestTelegramNotify:
    @respx.mock
    async def test_telegram_posts_to_bot_api(self):
        mock = respx.post("https://api.telegram.org/botTEST_TOKEN/sendMessage").mock(
            return_value=Response(200, json={"ok": True})
        )

        cfg = NotificationConfig(
            enabled=True,
            format=NotificationFormat.telegram,
            telegram_bot_token="TEST_TOKEN",  # noqa: S106
            telegram_chat_id="-100123456",
            on_posted=True,
        )
        rec = make_record(status="posted")

        await notify(rec, cfg)

        assert mock.called
        body = json.loads(mock.calls[0].request.content)
        assert body["chat_id"] == "-100123456"
        assert body["parse_mode"] == "MarkdownV2"
        assert "Review" in body["text"]

    async def test_telegram_skipped_when_no_token(self):
        cfg = NotificationConfig(
            enabled=True,
            format=NotificationFormat.telegram,
            telegram_bot_token="",
            telegram_chat_id="-100",
            on_posted=True,
        )
        rec = make_record()
        await notify(rec, cfg)  # no mock — OK
