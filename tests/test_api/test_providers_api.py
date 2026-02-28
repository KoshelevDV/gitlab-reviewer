"""Tests for /api/v1/providers — CRUD, test connection, model listing."""

from __future__ import annotations

import respx
from httpx import Response

NEW_PROVIDER = {
    "id": "new-ollama",
    "name": "New Ollama",
    "type": "ollama",
    "url": "http://new-ollama:11434",
    "api_key": "",
    "active": True,
}


class TestListProviders:
    async def test_list_returns_configured_providers(self, app):
        r = await app.get("/api/v1/providers")
        assert r.status_code == 200
        # The app fixture has one provider pre-configured
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["id"] == "test-provider"


class TestAddProvider:
    async def test_add_provider_returns_201(self, app):
        r = await app.post("/api/v1/providers", json=NEW_PROVIDER)
        assert r.status_code == 201
        assert r.json()["status"] == "created"
        assert r.json()["id"] == "new-ollama"

    async def test_add_provider_appears_in_list(self, app):
        await app.post("/api/v1/providers", json=NEW_PROVIDER)
        r = await app.get("/api/v1/providers")
        ids = [p["id"] for p in r.json()]
        assert "new-ollama" in ids

    async def test_add_duplicate_returns_409(self, app):
        await app.post("/api/v1/providers", json=NEW_PROVIDER)
        r = await app.post("/api/v1/providers", json=NEW_PROVIDER)
        assert r.status_code == 409

    async def test_add_llamacpp_provider(self, app):
        prov = {
            **NEW_PROVIDER,
            "id": "llamacpp-1",
            "type": "llamacpp",
            "url": "http://llamacpp:8080",
        }
        r = await app.post("/api/v1/providers", json=prov)
        assert r.status_code == 201


class TestUpdateProvider:
    async def test_update_provider_name(self, app):
        r = await app.put(
            "/api/v1/providers/test-provider",
            json={
                **{
                    "id": "test-provider",
                    "name": "Updated Name",
                    "type": "ollama",
                    "url": "http://fake-ollama:11434",
                    "api_key": "",
                    "active": True,
                }
            },
        )
        assert r.status_code == 200
        r2 = await app.get("/api/v1/providers")
        prov = next(p for p in r2.json() if p["id"] == "test-provider")
        assert prov["name"] == "Updated Name"

    async def test_update_nonexistent_returns_404(self, app):
        r = await app.put("/api/v1/providers/ghost", json={**NEW_PROVIDER, "id": "ghost"})
        assert r.status_code == 404


class TestDeleteProvider:
    async def test_delete_provider(self, app):
        await app.post("/api/v1/providers", json=NEW_PROVIDER)
        r = await app.delete("/api/v1/providers/new-ollama")
        assert r.status_code == 200
        r2 = await app.get("/api/v1/providers")
        ids = [p["id"] for p in r2.json()]
        assert "new-ollama" not in ids

    async def test_delete_nonexistent_returns_404(self, app):
        r = await app.delete("/api/v1/providers/ghost")
        assert r.status_code == 404


class TestTestProvider:
    @respx.mock
    async def test_test_ollama_provider_success(self, app):
        respx.get("http://fake-ollama:11434/api/version").mock(
            return_value=Response(200, json={"version": "0.1.27"})
        )
        r = await app.post("/api/v1/providers/test-provider/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "0.1.27" in data["version"]

    @respx.mock
    async def test_test_provider_failure(self, app):
        respx.get("http://fake-ollama:11434/api/version").mock(return_value=Response(503))
        r = await app.post("/api/v1/providers/test-provider/test")
        data = r.json()
        assert data["ok"] is False

    async def test_test_nonexistent_provider_returns_404(self, app):
        r = await app.post("/api/v1/providers/ghost/test")
        assert r.status_code == 404


class TestGetModels:
    @respx.mock
    async def test_list_models_from_ollama(self, app):
        respx.get("http://fake-ollama:11434/api/tags").mock(
            return_value=Response(
                200,
                json={
                    "models": [
                        {"name": "qwen2.5-coder:32b"},
                        {"name": "llama3.2:latest"},
                    ]
                },
            )
        )
        r = await app.get("/api/v1/providers/test-provider/models")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        names = [m["id"] for m in data]
        assert "qwen2.5-coder:32b" in names

    @respx.mock
    async def test_get_model_info(self, app):
        respx.post("http://fake-ollama:11434/api/show").mock(
            return_value=Response(
                200,
                json={
                    "model_info": {
                        "llama.context_length": 32768,
                        "general.architecture": "qwen2",
                        "general.parameter_count": 7_000_000_000,
                    }
                },
            )
        )
        r = await app.get("/api/v1/providers/test-provider/models/qwen2.5-coder:7b/info")
        assert r.status_code == 200
        data = r.json()
        assert data["context_length"] == 32768

    async def test_get_models_nonexistent_provider(self, app):
        r = await app.get("/api/v1/providers/ghost/models")
        assert r.status_code == 404
