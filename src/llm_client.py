"""LLM client — OpenAI-compatible chat completions (ollama, vllm, llama.cpp)."""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Sends chat-format requests to any OpenAI-compatible /v1/chat/completions
    endpoint (ollama, vllm, llama.cpp server, etc.).

    Separation of system / user turns is the primary prompt-injection defence:
    the system prompt is sealed, untrusted content only appears in the user turn.
    """

    def __init__(self, base_url: str, model: str, timeout: int = 300) -> None:
        # ollama exposes /api/chat; also supports /v1/chat/completions in new versions.
        # We use the OpenAI-compat endpoint so this works with vllm / llama.cpp too.
        self._base = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout)

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
        resp = await self._client.post(
            f"{self._base}/api/chat", json=ollama_payload
        )
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
                    import json
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
