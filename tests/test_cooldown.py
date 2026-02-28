"""Tests for review cooldown / rate-limiting logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from src.config import AppConfig, GitLabConfig, ModelConfig, Provider, ReviewTarget
from src.db import ReviewRecord
from src.gitlab_client import FileDiff, MRInfo
from src.queue_manager import ReviewJob
from src.reviewer import Reviewer, set_database

# ---------------------------------------------------------------------------
# DB.get_last_review_time
# ---------------------------------------------------------------------------


class TestGetLastReviewTime:
    async def test_returns_none_when_no_reviews(self, db):
        result = await db.get_last_review_time("42", 1)
        assert result is None

    async def test_returns_most_recent_timestamp(self, db):
        rec1 = ReviewRecord(
            project_id="42",
            mr_iid=7,
            status="posted",
            mr_title="T",
            author="a",
            source_branch="f",
            target_branch="m",
        )
        await db.save_review(rec1)
        result = await db.get_last_review_time("42", 7)
        assert result is not None
        assert isinstance(result, datetime)

    async def test_ignores_other_mr(self, db):
        rec = ReviewRecord(
            project_id="42",
            mr_iid=99,
            status="posted",
            mr_title="T",
            author="a",
            source_branch="f",
            target_branch="m",
        )
        await db.save_review(rec)
        result = await db.get_last_review_time("42", 7)
        assert result is None

    async def test_ignores_other_project(self, db):
        rec = ReviewRecord(
            project_id="999",
            mr_iid=7,
            status="posted",
            mr_title="T",
            author="a",
            source_branch="f",
            target_branch="m",
        )
        await db.save_review(rec)
        result = await db.get_last_review_time("42", 7)
        assert result is None

    async def test_returns_latest_of_multiple(self, db):
        for _ in range(3):
            rec = ReviewRecord(
                project_id="42",
                mr_iid=5,
                status="posted",
                mr_title="T",
                author="a",
                source_branch="f",
                target_branch="m",
            )
            await db.save_review(rec)
        result = await db.get_last_review_time("42", 5)
        assert result is not None


# ---------------------------------------------------------------------------
# Cooldown config resolution
# ---------------------------------------------------------------------------


def make_cfg_with_cooldown(global_min: int = 0, target_min: int | None = None) -> AppConfig:
    targets = []
    if target_min is not None:
        targets.append(
            ReviewTarget(
                type="project",
                id="42",
                review_cooldown_minutes=target_min,
            )
        )
    return AppConfig(
        providers=[Provider(id="p", name="P", type="ollama", url="http://fake", active=True)],
        model=ModelConfig(provider_id="p", name="m"),
        gitlab=GitLabConfig(url="http://fake-gl", webhook_secret="s"),  # noqa: S106
        review_cooldown_minutes=global_min,
        review_targets=targets,
    )


# ---------------------------------------------------------------------------
# Integration — reviewer skips MR during cooldown
# ---------------------------------------------------------------------------


def _make_mr(iid: int = 7) -> MRInfo:
    return MRInfo(
        project_id=42,
        iid=iid,
        title="cool MR",
        description="",
        is_draft=False,
        author="alice",
        source_branch="feat",
        target_branch="main",
        web_url="http://gl/mr/7",
    )


def _make_diff() -> FileDiff:
    return FileDiff(
        old_path="src/main.py",
        new_path="src/main.py",
        diff="@@\n+code\n",
        new_file=False,
        deleted_file=False,
        renamed_file=False,
    )


class TestCooldownIntegration:
    async def _run_review(self, db, prompt_engine, queue, cfg, mr_iid: int = 7) -> None:
        import src.config as cfg_mod

        cfg_mod._config = cfg
        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)

        with (
            patch("src.reviewer.get_config", return_value=cfg),
            patch("src.reviewer._make_gitlab_client") as mock_gl,
            patch("src.reviewer._make_llm_client") as mock_llm,
        ):
            gl = AsyncMock()
            gl.get_mr = AsyncMock(return_value=_make_mr(mr_iid))
            gl.get_diffs = AsyncMock(return_value=[_make_diff()])
            gl.get_mr_diff_refs = AsyncMock(return_value=None)
            gl.post_mr_note = AsyncMock()
            gl.aclose = AsyncMock()
            mock_gl.return_value = gl

            llm = AsyncMock()
            llm.chat = AsyncMock(return_value="LGTM")
            llm.aclose = AsyncMock()
            mock_llm.return_value = llm

            await reviewer.review_job(ReviewJob(project_id="42", mr_iid=mr_iid))

    async def test_no_cooldown_does_not_skip(self, db, prompt_engine, queue):
        cfg = make_cfg_with_cooldown(global_min=0)
        await self._run_review(db, prompt_engine, queue, cfg)

        records, _ = await db.list_reviews()
        assert records
        assert records[-1].status != "skipped"

    async def test_cooldown_skips_second_review_within_window(self, db, prompt_engine, queue):
        """First review should go through; second one within window should be skipped."""
        cfg = make_cfg_with_cooldown(global_min=60)  # 60 minutes cooldown

        # Seed a recent review record directly in DB (simulates a review 5 minutes ago)
        recent_time = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = ReviewRecord(
            project_id="42",
            mr_iid=7,
            status="posted",
            mr_title="prev",
            author="alice",
            source_branch="feat",
            target_branch="main",
            created_at=recent_time,
        )
        await db.save_review(rec)

        # Now trigger review — should be skipped due to cooldown
        await self._run_review(db, prompt_engine, queue, cfg)

        records, _ = await db.list_reviews()
        latest = records[0]  # most recent first
        assert latest.status == "skipped"
        assert "cooldown" in (latest.skip_reason or "")

    async def test_cooldown_allows_review_after_window_expires(self, db, prompt_engine, queue):
        """If last review is older than the cooldown window, allow re-review."""
        cfg = make_cfg_with_cooldown(global_min=30)

        # Seed a review from 45 minutes ago (past the 30-minute window)
        old_time = (datetime.now(UTC) - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = ReviewRecord(
            project_id="42",
            mr_iid=7,
            status="posted",
            mr_title="old",
            author="alice",
            source_branch="feat",
            target_branch="main",
            created_at=old_time,
        )
        await db.save_review(rec)

        await self._run_review(db, prompt_engine, queue, cfg)

        records, _ = await db.list_reviews()
        latest = records[0]
        # Should NOT be skipped — cooldown expired
        assert latest.status != "skipped"

    async def test_per_target_cooldown_overrides_global(self, db, prompt_engine, queue):
        """Target-level cooldown of 0 disables cooldown even if global is non-zero."""
        cfg = make_cfg_with_cooldown(global_min=120, target_min=0)

        # Seed a recent review 2 minutes ago
        recent_time = (datetime.now(UTC) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = ReviewRecord(
            project_id="42",
            mr_iid=7,
            status="posted",
            mr_title="x",
            author="alice",
            source_branch="f",
            target_branch="m",
            created_at=recent_time,
        )
        await db.save_review(rec)

        # With target cooldown=0, should NOT be skipped
        await self._run_review(db, prompt_engine, queue, cfg)

        records, _ = await db.list_reviews()
        latest = records[0]
        assert latest.status != "skipped"
