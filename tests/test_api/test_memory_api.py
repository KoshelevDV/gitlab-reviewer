"""Tests for /api/v1/memory — list patterns, delete, list projects."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
        store.list_patterns.return_value = [
            {
                "id": "uuid-1",
                "project_id": "p1",
                "category": "error_pattern",
                "content": "SQL injection in query.py",
                "metadata": {"file_path": "query.py"},
            }
        ]
        set_store(store)

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
        store.list_patterns.assert_awaited_once_with("p1", "", 50)

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

    async def test_list_patterns_filters_by_category(self, memory_app):
        """Verify category parameter is forwarded to store.list_patterns."""
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store.list_patterns.return_value = []
        set_store(store)

        r = await client.get("/api/v1/memory?project_id=p1&category=error_pattern")

        assert r.status_code == 200
        # Verify both project_id and category were passed through correctly
        store.list_patterns.assert_awaited_once_with("p1", "error_pattern", 50)

    async def test_list_patterns_limit_cap(self, memory_app):
        """Verify limit is passed to store; capping happens inside MemoryStore."""
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store.list_patterns.return_value = []
        set_store(store)

        r = await client.get("/api/v1/memory?limit=1000")

        assert r.status_code == 200
        # API passes limit as-is; MemoryStore enforces the 500 cap internally
        store.list_patterns.assert_awaited_once_with("", "", 1000)


class TestDeletePattern:
    async def test_delete_pattern_success(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store.delete_pattern.return_value = True
        set_store(store)

        r = await client.delete("/api/v1/memory/uuid-del")

        assert r.status_code == 200
        assert r.json()["status"] == "deleted"
        store.delete_pattern.assert_awaited_once_with("uuid-del")

    async def test_delete_pattern_not_found(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = True
        store.delete_pattern.return_value = False
        set_store(store)

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
        store.list_projects.return_value = ["proj-alpha", "proj-beta"]
        set_store(store)

        r = await client.get("/api/v1/memory/projects")

        assert r.status_code == 200
        projects = r.json()
        assert sorted(projects) == ["proj-alpha", "proj-beta"]
        store.list_projects.assert_awaited_once()

    async def test_list_projects_unavailable(self, memory_app):
        client, set_store = memory_app

        store = AsyncMock()
        store.is_available.return_value = False
        set_store(store)

        r = await client.get("/api/v1/memory/projects")
        assert r.status_code == 503
