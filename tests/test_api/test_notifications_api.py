"""Tests for POST /api/v1/notifications/test endpoint."""

from __future__ import annotations

import pytest_asyncio
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from src.api.notifications_api import router as notifications_router
from src.config import NotificationFormat


@pytest_asyncio.fixture
async def notif_app():
    """Minimal app with just the notifications router."""
    application = FastAPI()
    application.include_router(notifications_router)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


class TestNotificationTestEndpoint:
    async def test_returns_skipped_when_notifications_disabled(self, notif_app):
        import src.config as cfg_mod
        from src.config import AppConfig

        cfg = AppConfig()
        cfg.notifications.enabled = False
        cfg_mod._config = cfg

        r = await notif_app.post("/api/v1/notifications/test")
        assert r.status_code == 200
        assert r.json()["status"] == "skipped"

    @respx.mock
    async def test_sends_generic_webhook_when_enabled(self, notif_app):
        import src.config as cfg_mod
        from src.config import AppConfig

        mock = respx.post("http://hook.test/notify").mock(return_value=Response(200))

        cfg = AppConfig()
        cfg.notifications.enabled = True
        cfg.notifications.format = NotificationFormat.generic
        cfg.notifications.webhook_url = "http://hook.test/notify"
        cfg.notifications.on_posted = True
        cfg_mod._config = cfg

        r = await notif_app.post("/api/v1/notifications/test")
        assert r.status_code == 200
        assert r.json()["status"] == "sent"
        assert mock.called

    @respx.mock
    async def test_sends_slack_webhook_when_format_slack(self, notif_app):
        import src.config as cfg_mod
        from src.config import AppConfig

        mock = respx.post("http://slack.test/slack").mock(return_value=Response(200))

        cfg = AppConfig()
        cfg.notifications.enabled = True
        cfg.notifications.format = NotificationFormat.slack
        cfg.notifications.webhook_url = "http://slack.test/slack"
        cfg.notifications.on_posted = True
        cfg_mod._config = cfg

        r = await notif_app.post("/api/v1/notifications/test")
        assert r.status_code == 200
        assert r.json()["status"] == "sent"
        assert mock.called

    async def test_returns_200_even_without_url(self, notif_app):
        """Notifier is fail-open — missing URL logs warning but doesn't crash endpoint."""
        import src.config as cfg_mod
        from src.config import AppConfig

        cfg = AppConfig()
        cfg.notifications.enabled = True
        cfg.notifications.format = NotificationFormat.generic
        cfg.notifications.webhook_url = ""  # no URL
        cfg.notifications.on_posted = True
        cfg_mod._config = cfg

        r = await notif_app.post("/api/v1/notifications/test")
        # fail-open: notifier logs warning, endpoint still returns 200
        assert r.status_code == 200
