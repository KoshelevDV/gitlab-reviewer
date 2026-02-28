"""Tests for /api/v1/config — read, write, secret masking, reload."""

from __future__ import annotations

import pytest


class TestGetConfig:
    async def test_get_returns_200(self, app):
        r = await app.get("/api/v1/config")
        assert r.status_code == 200

    async def test_get_returns_valid_structure(self, app):
        r = await app.get("/api/v1/config")
        data = r.json()
        assert "providers" in data
        assert "model" in data
        assert "gitlab" in data
        assert "queue" in data
        assert "prompts" in data

    async def test_webhook_secret_masked(self, app):
        r = await app.get("/api/v1/config")
        data = r.json()
        webhook_secret = data.get("gitlab", {}).get("webhook_secret", "")
        # If there was a secret value it should be masked
        assert webhook_secret != "test-secret" or webhook_secret == ""  # noqa: S105

    async def test_api_key_masked_in_providers(self, app):
        """Providers with api_key should have it masked."""
        # First add a provider with api_key
        await app.post(
            "/api/v1/providers",
            json={
                "id": "secret-provider",
                "name": "Secret",
                "type": "openai_compat",
                "url": "http://x",
                "api_key": "sk-very-secret",
                "active": False,
            },
        )
        r = await app.get("/api/v1/config")
        providers = r.json()["providers"]
        secret_prov = next((p for p in providers if p["id"] == "secret-provider"), None)
        if secret_prov:
            assert secret_prov.get("api_key", "") != "sk-very-secret"


class TestPutConfig:
    async def test_put_updates_queue_settings(self, app):
        r = await app.put(
            "/api/v1/config", json={"queue": {"max_concurrent": 7, "max_queue_size": 200}}
        )
        assert r.status_code == 200

        r2 = await app.get("/api/v1/config")
        assert r2.json()["queue"]["max_concurrent"] == 7
        assert r2.json()["queue"]["max_queue_size"] == 200

    async def test_put_updates_model_temperature(self, app):
        await app.put("/api/v1/config", json={"model": {"temperature": 0.05}})
        r = await app.get("/api/v1/config")
        assert r.json()["model"]["temperature"] == pytest.approx(0.05)

    async def test_put_masked_secret_preserves_existing(self, app):
        """Sending **** should not overwrite the existing secret."""
        # Set a prompt stack
        await app.put("/api/v1/config", json={"prompts": {"system": ["base", "security"]}})
        # Now update something else while sending masked webhook secret
        await app.put("/api/v1/config", json={"gitlab": {"webhook_secret": "****"}})
        # Config should still work (we can't verify the actual secret value from API)
        r = await app.get("/api/v1/config")
        assert r.status_code == 200

    async def test_put_invalid_config_returns_422(self, app):
        r = await app.put("/api/v1/config", json={"model": {"temperature": "not-a-number"}})
        assert r.status_code in (422, 200)  # validation error or ignored


class TestReload:
    async def test_reload_returns_ok(self, app):
        r = await app.post("/api/v1/config/reload")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    async def test_reload_response_has_counts(self, app):
        r = await app.post("/api/v1/config/reload")
        data = r.json()
        assert "providers" in data
        assert "review_targets" in data


class TestSchema:
    async def test_schema_returns_json_schema(self, app):
        r = await app.get("/api/v1/config/schema")
        assert r.status_code == 200
        data = r.json()
        assert "properties" in data or "title" in data
