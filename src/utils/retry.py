"""Shared retry helpers using tenacity."""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential


def _is_transient(exc: BaseException) -> bool:
    """Return True for errors that warrant a retry (transport errors, 429, and 5xx HTTP)."""
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # Retry rate-limit (429) and all server errors (5xx)
        return exc.response.status_code in (429, 502, 503, 504) or exc.response.status_code >= 500
    return False


async def with_retry(
    fn,
    attempts: int = 4,
    min_wait: float = 5.0,
    max_wait: float = 30.0,
):
    """Execute async callable *fn* with exponential backoff retry on transient errors.

    Retries on:
    - httpx.TransportError  (connection refused, broken pipe, etc.)
    - httpx.TimeoutException
    - httpx.HTTPStatusError with status_code 429 (rate limit) or >= 500

    Non-transient errors (401, 403, 404, etc.) are re-raised immediately.
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
