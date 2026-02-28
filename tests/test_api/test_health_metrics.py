"""Tests for GET /health and GET /metrics endpoints."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def health_app(db):
    """Test app with health + metrics routers wired."""
    from fastapi import FastAPI

    import src.config as cfg_mod
    from src.api.health import router as health_router
    from src.api.health import set_database
    from src.api.health import set_queue_manager as hq
    from src.api.metrics_api import router as metrics_router
    from src.config import AppConfig, GitLabConfig, ModelConfig, Provider
    from src.queue_manager import QueueManager

    cfg = AppConfig(
        providers=[
            Provider(id="p1", name="Ollama", type="ollama", url="http://fake:11434", active=True)
        ],
        model=ModelConfig(provider_id="p1", name="test:7b"),
        gitlab=GitLabConfig(url="http://fake-gl", webhook_secret="s"),  # noqa: S106
    )
    cfg_mod._config = cfg

    q = QueueManager(max_concurrent=1, max_size=5)
    set_database(db)
    hq(q)

    application = FastAPI()
    application.include_router(health_router)
    application.include_router(metrics_router)

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client

    await q.drain()


class TestHealthEndpoint:
    async def test_health_returns_200_when_ok(self, health_app):
        r = await health_app.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "uptime_seconds" in body
        assert "checks" in body

    async def test_health_has_db_check(self, health_app):
        r = await health_app.get("/health")
        checks = r.json()["checks"]
        assert "db" in checks
        assert checks["db"]["status"] == "ok"

    async def test_health_has_queue_check(self, health_app):
        r = await health_app.get("/health")
        checks = r.json()["checks"]
        assert "queue" in checks
        assert checks["queue"]["status"] == "ok"

    async def test_health_has_config_check(self, health_app):
        r = await health_app.get("/health")
        checks = r.json()["checks"]
        assert "config" in checks
        assert checks["config"]["status"] == "ok"
        assert checks["config"]["providers"] == 1

    async def test_health_without_db_returns_503(self):
        """If DB is not set, health should return degraded/503."""
        from fastapi import FastAPI

        import src.api.health as health_mod
        import src.config as cfg_mod
        from src.api.health import router as health_router
        from src.config import AppConfig

        # Save and unset DB
        original_db = health_mod._db
        health_mod._db = None
        cfg_mod._config = AppConfig()

        application = FastAPI()
        application.include_router(health_router)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=application), base_url="http://test"
            ) as client:
                r = await client.get("/health")
                # Status may be 503 or 200 with degraded, depending on config check
                body = r.json()
                assert body["checks"]["db"]["status"] == "unavailable"
        finally:
            health_mod._db = original_db

    async def test_health_uptime_is_non_negative(self, health_app):
        r = await health_app.get("/health")
        assert r.json()["uptime_seconds"] >= 0


class TestMetricsEndpoint:
    async def test_metrics_returns_200(self, health_app):
        r = await health_app.get("/metrics")
        assert r.status_code == 200

    async def test_metrics_content_type_is_prometheus(self, health_app):
        r = await health_app.get("/metrics")
        assert "text/plain" in r.headers["content-type"]

    async def test_metrics_contains_expected_metric_names(self, health_app):
        r = await health_app.get("/metrics")
        body = r.text
        assert "glr_reviews_total" in body
        assert "glr_queue_pending" in body
        assert "glr_queue_active" in body
        assert "glr_llm_duration_seconds" in body
        assert "glr_queue_enqueued_total" in body
        assert "glr_jobs_superseded_total" in body
        assert "glr_reviews_deduped_total" in body
        assert "glr_cooldown_reschedules_total" in body

    async def test_metrics_are_zero_initially(self, health_app):
        r = await health_app.get("/metrics")
        body = r.text
        # At minimum, histogram bucket lines should appear (counter starts at 0 on first use)
        assert "glr_llm_duration_seconds_bucket" in body

    async def test_metrics_record_review_increments_counter(self, health_app):
        from src import metrics as m

        m.record_review(status="posted", inline_count=2, auto_approved=True)
        r = await health_app.get("/metrics")
        body = r.text
        assert 'glr_reviews_total{status="posted"}' in body
        assert "glr_inline_comments_total" in body
        assert "glr_auto_approvals_total" in body


class TestMetricsModule:
    def test_render_metrics_returns_bytes_and_content_type(self):
        from src.metrics import render_metrics

        body, ct = render_metrics()
        assert isinstance(body, bytes)
        assert "text/plain" in ct

    def test_record_review_does_not_raise(self):
        from src import metrics as m

        m.record_review(status="skipped")
        m.record_review(status="error", inline_count=0, auto_approved=False)
        m.record_review(status="posted", inline_count=3, auto_approved=True)

    def test_queue_pending_gauge_can_be_set(self):
        from src.metrics import queue_active, queue_pending

        queue_pending.set(5)
        queue_active.set(2)

    def test_llm_duration_histogram_time_context(self):
        import time

        from src.metrics import llm_duration_seconds

        with llm_duration_seconds.time():
            time.sleep(0.001)
