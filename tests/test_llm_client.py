"""Tests for LLMClient — chat completions, model listing, provider fallback."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.llm_client import LLMClient, list_models, get_model_info


BASE = "http://ollama.test"
MODEL = "qwen2.5-coder:7b"


@pytest.fixture
def client():
    c = LLMClient(base_url=BASE, model=MODEL, timeout=5)
    yield c


class TestChat:

    @respx.mock
    async def test_chat_returns_content(self, client):
        respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "Looks good to me."}}]
            })
        )
        result = await client.chat("You are a reviewer.", "Here is the diff...")
        assert result == "Looks good to me."
        await client.aclose()

    @respx.mock
    async def test_chat_fallback_to_native_ollama(self, client):
        """When /v1/chat/completions fails, fall back to /api/chat."""
        respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=Response(404)
        )
        respx.post(f"{BASE}/api/chat").mock(
            return_value=Response(200, json={
                "message": {"content": "Fallback response."}
            })
        )
        result = await client.chat("system", "user")
        assert result == "Fallback response."
        await client.aclose()

    @respx.mock
    async def test_chat_sends_system_and_user_turns(self, client):
        route = respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "ok"}}]
            })
        )
        await client.chat("SYSTEM_PROMPT", "USER_MSG")
        request_body = route.calls[0].request.content
        import json
        payload = json.loads(request_body)
        messages = payload["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "SYSTEM_PROMPT"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "USER_MSG"
        await client.aclose()

    @respx.mock
    async def test_temperature_passed(self, client):
        route = respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "ok"}}]
            })
        )
        await client.chat("sys", "usr", temperature=0.05)
        import json
        payload = json.loads(route.calls[0].request.content)
        assert payload["temperature"] == pytest.approx(0.05)
        await client.aclose()

    @respx.mock
    async def test_api_key_sent_in_header(self):
        c = LLMClient(base_url=BASE, model=MODEL, api_key="sk-test")
        route = respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "ok"}}]
            })
        )
        await c.chat("sys", "usr")
        assert "Authorization" in route.calls[0].request.headers
        assert "sk-test" in route.calls[0].request.headers["Authorization"]
        await c.aclose()


class TestListModels:

    @respx.mock
    async def test_list_ollama_models(self):
        respx.get(f"{BASE}/api/tags").mock(
            return_value=Response(200, json={
                "models": [
                    {"name": "qwen2.5-coder:32b"},
                    {"name": "llama3.2:latest"},
                ]
            })
        )
        models = await list_models(BASE, "ollama")
        assert len(models) == 2
        assert models[0].id == "qwen2.5-coder:32b"

    @respx.mock
    async def test_list_llamacpp_models(self):
        respx.get(f"{BASE}/v1/models").mock(
            return_value=Response(200, json={
                "data": [
                    {"id": "codestral-22b-q4"},
                ]
            })
        )
        models = await list_models(BASE, "llamacpp")
        assert len(models) == 1
        assert models[0].id == "codestral-22b-q4"

    @respx.mock
    async def test_list_models_network_error_returns_empty(self):
        respx.get(f"{BASE}/api/tags").mock(side_effect=Exception("timeout"))
        models = await list_models(BASE, "ollama")
        assert models == []

    @respx.mock
    async def test_list_models_sends_api_key(self):
        route = respx.get(f"{BASE}/v1/models").mock(
            return_value=Response(200, json={"data": []})
        )
        await list_models(BASE, "openai_compat", api_key="sk-abc")
        assert "sk-abc" in route.calls[0].request.headers.get("Authorization", "")


class TestGetModelInfo:

    @respx.mock
    async def test_get_context_length_from_ollama(self):
        respx.post(f"{BASE}/api/show").mock(
            return_value=Response(200, json={
                "model_info": {
                    "llama.context_length": 131072,
                    "general.architecture": "qwen2",
                    "general.parameter_count": 32_000_000_000,
                }
            })
        )
        info = await get_model_info(BASE, "qwen2.5-coder:32b", "ollama")
        assert info.context_length == 131072
        assert info.params["architecture"] == "qwen2"

    @respx.mock
    async def test_get_model_info_error_returns_empty(self):
        respx.post(f"{BASE}/api/show").mock(
            return_value=Response(404)
        )
        info = await get_model_info(BASE, "unknown-model", "ollama")
        assert info.id == "unknown-model"
        assert info.context_length is None
