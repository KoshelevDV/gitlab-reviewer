"""Shared retry helpers using tenacity."""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential


def _is_transient(exc: BaseException) -> bool:
    """Return True for errors that warrant a retry (transport errors and 5xx HTTP)."""
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        return True
    return False


async def with_retry(
    fn,
    attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 8.0,
):
    """Execute async callable *fn* with exponential backoff retry on transient errors.

    Retries on:
    - httpx.TransportError  (connection refused, broken pipe, etc.)
    - httpx.TimeoutException
    - httpx.HTTPStatusError with status_code >= 500

    Non-transient errors (4xx, etc.) are re-raised immediately.
    After *attempts* failed retries the last exception is re-raised.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    ):
        with attempt:
            return await fn()
