"""Tests for queue API endpoints — /api/v1/queue/..."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.queue_api import router, set_queue_manager

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_queue_manager():
    """Ensure _queue_manager is reset between tests."""
    set_queue_manager(None)
    yield
    set_queue_manager(None)


# ---------------------------------------------------------------------------
# GET /api/v1/queue — status
# ---------------------------------------------------------------------------


class TestQueueStatus:
    async def test_status_with_no_manager_returns_zeros(self, client):
        r = await client.get("/api/v1/queue")
        assert r.status_code == 200
        data = r.json()
        assert data["pending"] == 0
        assert data["active"] == 0
        assert data["done"] == 0
        assert data["errors"] == 0

    async def test_status_returns_manager_status(self, client):
        qm = MagicMock()
        qm.status.return_value = {"pending": 5, "active": 2, "done": 10, "errors": 1}
        set_queue_manager(qm)
        r = await client.get("/api/v1/queue")
        assert r.status_code == 200
        assert r.json()["pending"] == 5


# ---------------------------------------------------------------------------
# POST /api/v1/queue/drain
# ---------------------------------------------------------------------------


class TestDrain:
    async def test_drain_with_no_manager(self, client):
        r = await client.post("/api/v1/queue/drain")
        assert r.status_code == 200
        assert r.json()["status"] == "no queue"

    async def test_drain_calls_manager_drain(self, client):
        qm = MagicMock()
        qm.drain = AsyncMock()
        set_queue_manager(qm)
        r = await client.post("/api/v1/queue/drain")
        assert r.status_code == 200
        assert r.json()["status"] == "drained"
        qm.drain.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/queue/start
# ---------------------------------------------------------------------------


class TestStartQueue:
    async def test_start_returns_503_without_manager(self, client):
        r = await client.post("/api/v1/queue/start")
        assert r.status_code == 503
        assert "Queue not available" in r.json()["detail"]

    async def test_start_calls_restart_and_returns_worker_count(self, client):
        qm = MagicMock()
        qm.restart = AsyncMock(return_value=3)
        set_queue_manager(qm)
        r = await client.post("/api/v1/queue/start")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started"
        assert body["workers"] == 3
        qm.restart.assert_awaited_once()

    async def test_start_after_drain_roundtrip(self, client):
        """Drain followed by start should call restart()."""
        qm = MagicMock()
        qm.drain = AsyncMock()
        qm.restart = AsyncMock(return_value=2)
        set_queue_manager(qm)

        drain_r = await client.post("/api/v1/queue/drain")
        assert drain_r.status_code == 200

        start_r = await client.post("/api/v1/queue/start")
        assert start_r.status_code == 200
        assert start_r.json()["workers"] == 2
        qm.restart.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/queue/review
# ---------------------------------------------------------------------------


class TestTriggerReview:
    async def test_review_enqueues_job(self, client):

        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=True)
        set_queue_manager(qm)

        r = await client.post(
            "/api/v1/queue/review", json={"project_id": 1, "mr_iid": 10}
        )
        assert r.status_code == 202
        assert r.json()["status"] == "queued"

    async def test_review_returns_429_when_full(self, client):
        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=False)
        set_queue_manager(qm)

        r = await client.post(
            "/api/v1/queue/review", json={"project_id": 1, "mr_iid": 10}
        )
        assert r.status_code == 429

    async def test_review_returns_503_without_manager(self, client):
        r = await client.post(
            "/api/v1/queue/review", json={"project_id": 1, "mr_iid": 10}
        )
        assert r.status_code == 503


_DRY_RUN_MR_PAYLOAD = {
    "title": "My MR",
    "web_url": "http://gitlab/mr/7",
    "author": {"username": "dev"},
    "source_branch": "feat",
    "target_branch": "main",
    "draft": False,
    "description": "",
}


class TestDryRun:
    """Tests for dry_run=true on POST /api/v1/queue/review."""

    @pytest.fixture(autouse=True)
    def _setup_fake_gitlab(self):
        """Point config at fake-gitlab so dry_run requests hit the respx mock."""
        import src.config as cfg_mod
        from src.config import AppConfig, GitLabConfig

        cfg_mod._config = AppConfig(gitlab=GitLabConfig(url="http://fake-gitlab"))
        yield

    async def test_dry_run_returns_dry_run_status(self, client):
        import httpx
        import respx

        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=True)
        set_queue_manager(qm)

        with respx.mock:
            respx.get("http://fake-gitlab/api/v4/projects/42/merge_requests/7").mock(
                return_value=httpx.Response(200, json=_DRY_RUN_MR_PAYLOAD)
            )
            r = await client.post(
                "/api/v1/queue/review",
                json={"project_id": 42, "mr_iid": 7, "dry_run": True},
            )

        assert r.status_code == 200
        assert r.json()["status"] == "dry_run"

    async def test_dry_run_includes_mr_title(self, client):
        import httpx
        import respx

        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=True)
        set_queue_manager(qm)

        with respx.mock:
            respx.get("http://fake-gitlab/api/v4/projects/42/merge_requests/7").mock(
                return_value=httpx.Response(200, json=_DRY_RUN_MR_PAYLOAD)
            )
            r = await client.post(
                "/api/v1/queue/review",
                json={"project_id": 42, "mr_iid": 7, "dry_run": True},
            )

        assert r.status_code == 200
        assert r.json()["mr_title"] == "My MR"

    async def test_dry_run_does_not_enqueue(self, client):
        import httpx
        import respx

        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=True)
        set_queue_manager(qm)

        with respx.mock:
            respx.get("http://fake-gitlab/api/v4/projects/42/merge_requests/7").mock(
                return_value=httpx.Response(200, json=_DRY_RUN_MR_PAYLOAD)
            )
            r = await client.post(
                "/api/v1/queue/review",
                json={"project_id": 42, "mr_iid": 7, "dry_run": True},
            )

        assert r.status_code == 200
        qm.enqueue.assert_not_awaited()

    async def test_dry_run_mr_not_found_returns_404(self, client):
        import httpx
        import respx

        qm = MagicMock()
        qm.enqueue = AsyncMock(return_value=True)
        set_queue_manager(qm)

        with respx.mock:
            respx.get("http://fake-gitlab/api/v4/projects/42/merge_requests/7").mock(
                return_value=httpx.Response(404, json={"message": "404 Not Found"})
            )
            r = await client.post(
                "/api/v1/queue/review",
                json={"project_id": 42, "mr_iid": 7, "dry_run": True},
            )

        assert r.status_code == 404
