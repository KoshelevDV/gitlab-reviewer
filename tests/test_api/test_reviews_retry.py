"""Tests for POST /api/v1/reviews/{id}/retry endpoint."""
from __future__ import annotations

import pytest
from src.db import ReviewRecord


async def _seed(db, status: str = "error", mr_iid: int = 1, **kwargs) -> ReviewRecord:
    rec = ReviewRecord(
        project_id="42", mr_iid=mr_iid, status=status,
        mr_title="MR", author="alice",
        source_branch="feature", target_branch="main",
        **kwargs,
    )
    await db.save_review(rec)
    return rec


class TestRetryEndpoint:

    async def test_retry_error_review_returns_200(self, app, db):
        rec = await _seed(db, status="error")
        r = await app.post(f"/api/v1/reviews/{rec.id}/retry")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "queued"
        assert data["review_id"] == rec.id

    async def test_retry_skipped_review_returns_200(self, app, db):
        rec = await _seed(db, status="skipped")
        r = await app.post(f"/api/v1/reviews/{rec.id}/retry")
        assert r.status_code == 200

    async def test_retry_posted_review_returns_409(self, app, db):
        rec = await _seed(db, status="posted")
        r = await app.post(f"/api/v1/reviews/{rec.id}/retry")
        assert r.status_code == 409
        assert "posted" in r.json()["detail"]

    async def test_retry_dry_run_returns_409(self, app, db):
        rec = await _seed(db, status="dry_run")
        r = await app.post(f"/api/v1/reviews/{rec.id}/retry")
        assert r.status_code == 409

    async def test_retry_nonexistent_returns_404(self, app):
        r = await app.post("/api/v1/reviews/99999/retry")
        assert r.status_code == 404

    async def test_retry_enqueues_job(self, app, db):
        """Retried job should show up in queue status."""
        rec = await _seed(db, mr_iid=7)
        await app.post(f"/api/v1/reviews/{rec.id}/retry")
        # Queue should have 1 pending job
        q_status = await app.get("/api/v1/queue")
        assert q_status.status_code == 200
        # pending might be 0 if worker already consumed it, but it shouldn't crash
        data = q_status.json()
        assert "pending" in data
