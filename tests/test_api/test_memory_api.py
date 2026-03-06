"""Tests for /api/v1/memory — list patterns, delete, list projects."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers — mock qdrant objects
# ---------------------------------------------------------------------------


def _make_point(point_id: str, payload: dict):
    """Create a mock Qdrant ScoredPoint / Record object."""
    p = MagicMock()
    p.id = point_id
    p.payload = payload
    return p


# ---------------------------------------------------------------------------
# Fixture: minimal app wired with memory router
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def memory_app():
    """FastAPI app with only the memory router included."""
    from fastapi import FastAPI

    from src.api.memory_api import router as memory_router
    from src.api.memory_api import set_memory_store

    application = FastAPI()
    application.include_router(memory_router)

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client, set_memory_store

    # Reset singleton after test
    set_memory_store(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListPatterns:
    async def test_list_patterns_returns_results(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store._collection = "reviewer_memory"

        mock_client = AsyncMock()
        store._get_client.return_value = mock_client
        store._ensure_collection = AsyncMock()

        point = _make_point(
            "uuid-1",
            {
                "project_id": "p1",
                "category": "error_pattern",
                "content": "SQL injection in query.py",
                "file_path": "query.py",
            },
        )
        mock_client.scroll.return_value = ([point], None)

        set_store(store)

        with patch("src.api.memory_api._try_import_qdrant") as mock_qdrant:
            qm = MagicMock()
            mock_qdrant.return_value = (MagicMock(), qm)
            r = await client.get("/api/v1/memory?project_id=p1")

        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["id"] == "uuid-1"
        assert item["project_id"] == "p1"
        assert item["category"] == "error_pattern"
        assert "SQL injection" in item["content"]
        assert item["metadata"]["file_path"] == "query.py"

    async def test_list_patterns_unavailable(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = False
        set_store(store)

        r = await client.get("/api/v1/memory?project_id=p1")
        assert r.status_code == 503
        assert r.json()["error"] == "memory not available"

    async def test_list_patterns_no_store(self, memory_app):
        client, set_store = memory_app
        set_store(None)

        r = await client.get("/api/v1/memory?project_id=p1")
        assert r.status_code == 503


class TestDeletePattern:
    async def test_delete_pattern_success(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store._collection = "reviewer_memory"

        mock_client = AsyncMock()
        store._get_client.return_value = mock_client
        store._ensure_collection = AsyncMock()

        point = _make_point("uuid-del", {"project_id": "p1", "category": "error_pattern", "content": "x"})
        mock_client.retrieve.return_value = [point]
        mock_client.delete = AsyncMock()

        set_store(store)

        with patch("src.api.memory_api._try_import_qdrant") as mock_qdrant:
            qm = MagicMock()
            mock_qdrant.return_value = (MagicMock(), qm)
            r = await client.delete("/api/v1/memory/uuid-del")

        assert r.status_code == 200
        assert r.json()["status"] == "deleted"
        mock_client.delete.assert_called_once()

    async def test_delete_pattern_not_found(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store._collection = "reviewer_memory"

        mock_client = AsyncMock()
        store._get_client.return_value = mock_client
        store._ensure_collection = AsyncMock()
        mock_client.retrieve.return_value = []  # not found

        set_store(store)

        with patch("src.api.memory_api._try_import_qdrant") as mock_qdrant:
            qm = MagicMock()
            mock_qdrant.return_value = (MagicMock(), qm)
            r = await client.delete("/api/v1/memory/no-such-id")

        assert r.status_code == 404

    async def test_delete_unavailable(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = False
        set_store(store)

        r = await client.delete("/api/v1/memory/some-id")
        assert r.status_code == 503


class TestListProjects:
    async def test_list_projects(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store._collection = "reviewer_memory"

        mock_client = AsyncMock()
        store._get_client.return_value = mock_client
        store._ensure_collection = AsyncMock()

        p1 = _make_point("id-1", {"project_id": "proj-alpha"})
        p2 = _make_point("id-2", {"project_id": "proj-beta"})
        p3 = _make_point("id-3", {"project_id": "proj-alpha"})  # duplicate
        mock_client.scroll.return_value = ([p1, p2, p3], None)

        set_store(store)

        with patch("src.api.memory_api._try_import_qdrant") as mock_qdrant:
            qm = MagicMock()
            mock_qdrant.return_value = (MagicMock(), qm)
            r = await client.get("/api/v1/memory/projects")

        assert r.status_code == 200
        projects = r.json()
        assert sorted(projects) == ["proj-alpha", "proj-beta"]

    async def test_list_projects_unavailable(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = False
        set_store(store)

        r = await client.get("/api/v1/memory/projects")
        assert r.status_code == 503
