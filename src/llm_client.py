"""LLM client — OpenAI-compatible chat completions + model discovery."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    id: str
    context_length: int | None = None
    params: dict = field(default_factory=dict)


async def list_models(base_url: str, provider_type: str, api_key: str = "") -> list[ModelInfo]:
    """
    Fetch available models from a provider.

    ollama:        GET /api/tags       → .models[].name
    llamacpp:      GET /v1/models      → .data[].id
    openai_compat: GET /v1/models      → .data[].id
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        base = base_url.rstrip("/")
        try:
            if provider_type == "ollama":
                resp = await client.get(f"{base}/api/tags")
                resp.raise_for_status()
                models = resp.json().get("models", [])
                return [ModelInfo(id=m["name"]) for m in models]
            else:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                data = resp.json().get("data", [])
                return [ModelInfo(id=m["id"]) for m in data]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list models from %s: %s", base_url, exc)
            return []


async def get_model_info(
    base_url: str, model_name: str, provider_type: str, api_key: str = ""
) -> ModelInfo:
    """Get context length and params for a specific model (best-effort)."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    info = ModelInfo(id=model_name)
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        base = base_url.rstrip("/")
        try:
            if provider_type == "ollama":
                resp = await client.post(f"{base}/api/show", json={"name": model_name})
                if resp.status_code == 200:
                    d = resp.json()
                    model_meta = d.get("model_info", {})
                    info.context_length = model_meta.get("llama.context_length") or model_meta.get(
                        "context_length"
                    )
                    info.params = {
                        "architecture": model_meta.get("general.architecture", ""),
                        "param_count": model_meta.get("general.parameter_count", ""),
                    }
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not fetch model info for %s: %s", model_name, exc)
    return info


class LLMClient:
    """
    Sends chat-format requests to any OpenAI-compatible /v1/chat/completions
    endpoint (ollama, vllm, llama.cpp server, etc.).

    Separation of system / user turns is the primary prompt-injection defence:
    the system prompt is sealed, untrusted content only appears in the user turn.
    """

    def __init__(self, base_url: str, model: str, timeout: int = 300, api_key: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
    ) -> str:
        """
        Send a two-turn chat (system + user) and return the full assistant reply.

        The system prompt is NEVER modified by user data.
        The user message contains the sanitised diff and metadata.
        """
        payload = {
            "model": self._model,
            "temperature": temperature,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        # Try OpenAI-compat endpoint first (ollama ≥0.1.24, vllm, llama.cpp)
        url = f"{self._base}/v1/chat/completions"
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPStatusError, KeyError):
            # Fall back to native ollama /api/chat
            logger.debug("OpenAI-compat endpoint failed, trying ollama native /api/chat")

        ollama_payload = {
            "model": self._model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": payload["messages"],
        }
        resp = await self._client.post(f"{self._base}/api/chat", json=ollama_payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"]

    async def chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Streaming variant — yields text chunks as they arrive."""
        payload = {
            "model": self._model,
            "temperature": temperature,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        async with self._client.stream(
            "POST", f"{self._base}/v1/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
