"""Tests for src/utils/retry.py — with_retry + _is_transient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.utils.retry import _is_transient, with_retry

# ---------------------------------------------------------------------------
# _is_transient
# ---------------------------------------------------------------------------


class TestIsTransient:
    def test_transport_error_is_transient(self):
        exc = httpx.TransportError("connection refused")
        assert _is_transient(exc) is True

    def test_timeout_is_transient(self):
        exc = httpx.TimeoutException("timed out")
        assert _is_transient(exc) is True

    def test_http_500_is_transient(self):
        resp = MagicMock()
        resp.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
        assert _is_transient(exc) is True

    def test_http_503_is_transient(self):
        resp = MagicMock()
        resp.status_code = 503
        exc = httpx.HTTPStatusError("503", request=MagicMock(), response=resp)
        assert _is_transient(exc) is True

    def test_http_400_is_not_transient(self):
        resp = MagicMock()
        resp.status_code = 400
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=resp)
        assert _is_transient(exc) is False

    def test_http_404_is_not_transient(self):
        resp = MagicMock()
        resp.status_code = 404
        exc = httpx.HTTPStatusError("404", request=MagicMock(), response=resp)
        assert _is_transient(exc) is False

    def test_value_error_is_not_transient(self):
        assert _is_transient(ValueError("oops")) is False


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


class TestWithRetry:
    async def test_succeeds_on_first_attempt(self):
        fn = AsyncMock(return_value="ok")
        result = await with_retry(fn, attempts=3, min_wait=0, max_wait=0)
        assert result == "ok"
        fn.assert_awaited_once()

    async def test_retries_on_transport_error_then_succeeds(self):
        """TransportError on first 2 calls, success on 3rd."""
        fn = AsyncMock(
            side_effect=[
                httpx.TransportError("err"),
                httpx.TransportError("err"),
                "success",
            ]
        )
        result = await with_retry(fn, attempts=3, min_wait=0, max_wait=0)
        assert result == "success"
        assert fn.await_count == 3

    async def test_retries_on_5xx_then_succeeds(self):
        resp = MagicMock()
        resp.status_code = 503
        fn = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError("503", request=MagicMock(), response=resp),
                "ok",
            ]
        )
        result = await with_retry(fn, attempts=3, min_wait=0, max_wait=0)
        assert result == "ok"
        assert fn.await_count == 2

    async def test_no_retry_on_4xx(self):
        """4xx errors should propagate immediately without retry."""
        resp = MagicMock()
        resp.status_code = 422
        fn = AsyncMock(side_effect=httpx.HTTPStatusError("422", request=MagicMock(), response=resp))
        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(fn, attempts=3, min_wait=0, max_wait=0)
        fn.assert_awaited_once()  # no retry

    async def test_reraise_after_max_attempts(self):
        """If all attempts fail with transient error, reraise."""
        fn = AsyncMock(side_effect=httpx.TransportError("always fails"))
        with pytest.raises(httpx.TransportError):
            await with_retry(fn, attempts=3, min_wait=0, max_wait=0)
        assert fn.await_count == 3
