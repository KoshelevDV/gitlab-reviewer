"""Tests for /api/v1/reviews — list, stats, recent, get by id."""

from __future__ import annotations

from src.db import ReviewRecord


async def _seed(db, **kwargs) -> ReviewRecord:
    defaults = dict(
        project_id="42",
        mr_iid=1,
        status="posted",
        mr_title="MR title",
        author="alice",
        source_branch="feature",
        target_branch="main",
        review_text="LGTM",
        prompt_names=["base"],
    )
    defaults.update(kwargs)
    rec = ReviewRecord(**defaults)
    await db.save_review(rec)
    return rec


class TestListReviews:
    async def test_empty_returns_zero(self, app):
        r = await app.get("/api/v1/reviews")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_returns_saved_reviews(self, app, db):
        await _seed(db, mr_iid=1)
        await _seed(db, mr_iid=2)
        r = await app.get("/api/v1/reviews")
        data = r.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    async def test_filter_by_project_id(self, app, db):
        await _seed(db, project_id="10", mr_iid=1)
        await _seed(db, project_id="20", mr_iid=2)
        r = await app.get("/api/v1/reviews?project_id=10")
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["project_id"] == "10"

    async def test_filter_by_status(self, app, db):
        await _seed(db, status="posted", mr_iid=1)
        await _seed(db, status="skipped", mr_iid=2)
        r = await app.get("/api/v1/reviews?status=skipped")
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "skipped"

    async def test_pagination_limit(self, app, db):
        for i in range(5):
            await _seed(db, mr_iid=i + 1)
        r = await app.get("/api/v1/reviews?limit=2")
        data = r.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

    async def test_pagination_offset(self, app, db):
        for i in range(4):
            await _seed(db, mr_iid=i + 1)
        r1 = await app.get("/api/v1/reviews?limit=2&offset=0")
        r2 = await app.get("/api/v1/reviews?limit=2&offset=2")
        ids1 = {x["mr_iid"] for x in r1.json()["items"]}
        ids2 = {x["mr_iid"] for x in r2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    async def test_limit_capped_at_100(self, app, db):
        for i in range(5):
            await _seed(db, mr_iid=i + 1)
        r = await app.get("/api/v1/reviews?limit=999")
        # Should not blow up — limit capped at 100
        assert r.status_code == 200

    async def test_review_item_has_required_fields(self, app, db):
        await _seed(db)
        r = await app.get("/api/v1/reviews")
        item = r.json()["items"][0]
        for field in ("id", "project_id", "mr_iid", "status", "author", "created_at"):
            assert field in item, f"Missing field: {field}"


class TestGetReview:
    async def test_get_by_id_returns_review(self, app, db):
        rec = await _seed(db, review_text="Full review text here")
        r = await app.get(f"/api/v1/reviews/{rec.id}")
        assert r.status_code == 200
        assert r.json()["review_text"] == "Full review text here"

    async def test_get_nonexistent_returns_404(self, app):
        r = await app.get("/api/v1/reviews/99999")
        assert r.status_code == 404

    async def test_get_includes_prompt_names(self, app, db):
        rec = await _seed(db, prompt_names=["base", "security", "style"])
        r = await app.get(f"/api/v1/reviews/{rec.id}")
        assert r.json()["prompt_names"] == ["base", "security", "style"]


class TestStats:
    async def test_stats_empty(self, app):
        r = await app.get("/api/v1/reviews/stats")
        assert r.status_code == 200
        data = r.json()
        assert data.get("total", 0) == 0

    async def test_stats_counts(self, app, db):
        await _seed(db, status="posted", mr_iid=1)
        await _seed(db, status="posted", mr_iid=2, auto_approved=True)
        await _seed(db, status="skipped", mr_iid=3)
        await _seed(db, status="error", mr_iid=4)
        r = await app.get("/api/v1/reviews/stats")
        data = r.json()
        assert data["total"] == 4
        assert data["posted"] == 2
        assert data["skipped"] == 1
        assert data["errors"] == 1
        assert data["auto_approved"] == 1


class TestRecent:
    async def test_recent_returns_list(self, app, db):
        for i in range(5):
            await _seed(db, mr_iid=i + 1)
        r = await app.get("/api/v1/reviews/recent?limit=3")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 3

    async def test_recent_empty(self, app):
        r = await app.get("/api/v1/reviews/recent")
        assert r.status_code == 200
        assert r.json() == []
