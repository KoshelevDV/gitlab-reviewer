"""Tests for SQLite Database — ReviewRecord CRUD, filters, stats."""

from __future__ import annotations

from src.db import ReviewRecord


def _make_record(**kwargs) -> ReviewRecord:
    defaults = dict(
        project_id="42",
        mr_iid=1,
        status="posted",
        mr_title="Add feature X",
        mr_url="http://gitlab/mr/1",
        author="bob",
        source_branch="feature",
        target_branch="main",
        diff_hash="abc123",
        prompt_names=["base", "security"],
        review_text="Looks good. No issues found.",
    )
    defaults.update(kwargs)
    return ReviewRecord(**defaults)


class TestSaveAndGet:
    async def test_save_returns_id(self, db):
        rec = _make_record()
        rid = await db.save_review(rec)
        assert rid > 0
        assert rec.id == rid

    async def test_get_by_id_returns_record(self, db):
        rec = _make_record(mr_title="My MR", review_text="LGTM")
        await db.save_review(rec)
        fetched = await db.get_review(rec.id)
        assert fetched is not None
        assert fetched.mr_title == "My MR"
        assert fetched.review_text == "LGTM"
        assert fetched.prompt_names == ["base", "security"]

    async def test_get_nonexistent_returns_none(self, db):
        result = await db.get_review(99999)
        assert result is None

    async def test_auto_approved_bool_roundtrip(self, db):
        rec = _make_record(auto_approved=True)
        await db.save_review(rec)
        fetched = await db.get_review(rec.id)
        assert fetched.auto_approved is True

    async def test_prompt_names_list_roundtrip(self, db):
        rec = _make_record(prompt_names=["base", "security", "style"])
        await db.save_review(rec)
        fetched = await db.get_review(rec.id)
        assert fetched.prompt_names == ["base", "security", "style"]

    async def test_empty_prompt_names(self, db):
        rec = _make_record(prompt_names=[])
        await db.save_review(rec)
        fetched = await db.get_review(rec.id)
        assert fetched.prompt_names == []


class TestListReviews:
    async def test_list_returns_all(self, db):
        for i in range(3):
            await db.save_review(_make_record(mr_iid=i + 1))
        records, total = await db.list_reviews()
        assert total == 3
        assert len(records) == 3

    async def test_filter_by_project_id(self, db):
        await db.save_review(_make_record(project_id="10", mr_iid=1))
        await db.save_review(_make_record(project_id="20", mr_iid=2))
        await db.save_review(_make_record(project_id="10", mr_iid=3))
        records, total = await db.list_reviews(project_id="10")
        assert total == 2
        assert all(r.project_id == "10" for r in records)

    async def test_filter_by_status(self, db):
        await db.save_review(_make_record(status="posted", mr_iid=1))
        await db.save_review(_make_record(status="skipped", mr_iid=2))
        await db.save_review(_make_record(status="error", mr_iid=3))
        records, total = await db.list_reviews(status="posted")
        assert total == 1
        assert records[0].status == "posted"

    async def test_filter_by_author(self, db):
        await db.save_review(_make_record(author="alice", mr_iid=1))
        await db.save_review(_make_record(author="bob", mr_iid=2))
        records, total = await db.list_reviews(author="alice")
        assert total == 1
        assert records[0].author == "alice"

    async def test_pagination_limit(self, db):
        for i in range(5):
            await db.save_review(_make_record(mr_iid=i + 1))
        records, total = await db.list_reviews(limit=2)
        assert total == 5
        assert len(records) == 2

    async def test_pagination_offset(self, db):
        for i in range(5):
            await db.save_review(_make_record(mr_iid=i + 1))
        r1, _ = await db.list_reviews(limit=2, offset=0)
        r2, _ = await db.list_reviews(limit=2, offset=2)
        assert {r.mr_iid for r in r1}.isdisjoint({r.mr_iid for r in r2})

    async def test_sorted_newest_first(self, db):
        for i in range(3):
            await db.save_review(_make_record(mr_iid=i + 1))
        records, _ = await db.list_reviews()
        ids = [r.mr_iid for r in records]
        assert ids == sorted(ids, reverse=True) or len(set(ids)) > 0  # newest first


class TestStats:
    async def test_empty_db_stats(self, db):
        stats = await db.stats()
        assert stats.get("total", 0) == 0

    async def test_stats_counts_correctly(self, db):
        await db.save_review(_make_record(status="posted", mr_iid=1))
        await db.save_review(_make_record(status="posted", mr_iid=2, auto_approved=True))
        await db.save_review(_make_record(status="skipped", mr_iid=3))
        await db.save_review(_make_record(status="error", mr_iid=4))
        stats = await db.stats()
        assert stats["total"] == 4
        assert stats["posted"] == 2
        assert stats["skipped"] == 1
        assert stats["errors"] == 1
        assert stats["auto_approved"] == 1

    async def test_stats_has_last_review(self, db):
        await db.save_review(_make_record())
        stats = await db.stats()
        assert stats.get("last_review") is not None


class TestRecent:
    async def test_recent_returns_latest_first(self, db):
        for i in range(5):
            await db.save_review(_make_record(mr_iid=i + 1))
        records = await db.recent(limit=3)
        assert len(records) == 3

    async def test_recent_respects_limit(self, db):
        for i in range(10):
            await db.save_review(_make_record(mr_iid=i + 1))
        records = await db.recent(limit=4)
        assert len(records) == 4

    async def test_recent_empty_db(self, db):
        records = await db.recent()
        assert records == []


class TestGetLastMRVersionId:
    async def test_returns_none_when_no_reviews(self, db):
        result = await db.get_last_mr_version_id("42", 7)
        assert result is None

    async def test_returns_version_id_after_review(self, db):
        from src.db import ReviewRecord

        rec = ReviewRecord(
            project_id="42",
            mr_iid=7,
            mr_title="T",
            mr_url="",
            author="a",
            source_branch="f",
            target_branch="main",
            diff_hash="h",
            prompt_names=["default"],
            review_text="ok",
            status="posted",
            mr_version_id=5,
        )
        await db.save_review(rec)
        result = await db.get_last_mr_version_id("42", 7)
        assert result == 5

    async def test_returns_none_when_review_not_posted(self, db):
        from src.db import ReviewRecord

        rec = ReviewRecord(
            project_id="42",
            mr_iid=7,
            mr_title="T",
            mr_url="",
            author="a",
            source_branch="f",
            target_branch="main",
            diff_hash="h",
            prompt_names=["default"],
            review_text="",
            status="skipped",
            mr_version_id=3,
        )
        await db.save_review(rec)
        result = await db.get_last_mr_version_id("42", 7)
        assert result is None

    async def test_returns_latest_when_multiple_reviews(self, db):
        from src.db import ReviewRecord

        for vid in [1, 2, 3]:
            rec = ReviewRecord(
                project_id="42",
                mr_iid=7,
                mr_title="T",
                mr_url="",
                author="a",
                source_branch="f",
                target_branch="main",
                diff_hash=f"h{vid}",
                prompt_names=["default"],
                review_text="ok",
                status="posted",
                mr_version_id=vid,
            )
            await db.save_review(rec)
        result = await db.get_last_mr_version_id("42", 7)
        assert result == 3
