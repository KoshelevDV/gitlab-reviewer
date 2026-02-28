"""Tests for Reviewer — full mock flow, filters, auto-approve, error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.config import (
    AppConfig,
    BranchRules,
    GitLabConfig,
    ModelConfig,
    PromptsOverride,
    Provider,
    ReviewTarget,
)
from src.gitlab_client import FileDiff, MRInfo
from src.queue_manager import ReviewJob
from src.reviewer import Reviewer, _severity_count, set_database


@pytest.fixture
def mock_mr():
    return MRInfo(
        project_id=42,
        iid=7,
        title="Add feature",
        description="Does cool stuff",
        author="alice",
        source_branch="feature",
        target_branch="main",
        is_draft=False,
        web_url="http://gitlab/mr/7",
    )


@pytest.fixture
def mock_diffs():
    return [
        FileDiff(
            old_path="app.py",
            new_path="app.py",
            diff="@@ -1,3 +1,4 @@\n+def new_func(): pass",
            new_file=False,
            deleted_file=False,
            renamed_file=False,
        )
    ]


@pytest.fixture
def mock_gitlab(mock_mr, mock_diffs):
    gl = AsyncMock()
    gl.get_mr.return_value = mock_mr
    gl.get_diffs.return_value = mock_diffs
    gl.post_mr_note = AsyncMock()
    gl.approve_mr = AsyncMock(return_value=True)
    gl.aclose = AsyncMock()
    return gl


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.chat.return_value = "## Summary\n\nLooks good. No issues found."
    llm.aclose = AsyncMock()
    return llm


@pytest.fixture
def reviewer(prompt_engine, queue):
    return Reviewer(prompts=prompt_engine, queue=queue)


@pytest.fixture
def cfg_with_target():
    return AppConfig(
        providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
        model=ModelConfig(provider_id="p", name="test-model"),
        gitlab=GitLabConfig(url="http://gitlab"),
        review_targets=[
            ReviewTarget(
                type="project",
                id="42",
                branches=BranchRules(pattern="*", protected_only=False),
                auto_approve=False,
                prompts=PromptsOverride(system=[]),
            )
        ],
    )


class TestReviewFilters:
    async def test_draft_mr_skipped(self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target):
        mock_gitlab.get_mr.return_value = MRInfo(
            project_id=42,
            iid=7,
            title="Draft: wip",
            description="",
            author="bob",
            source_branch="f",
            target_branch="main",
            is_draft=True,
            web_url="",
        )
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            job = ReviewJob(project_id=42, mr_iid=7)
            await reviewer.review_job(job)

        records, _ = await db.list_reviews()
        assert any(r.status == "skipped" and r.skip_reason == "draft MR" for r in records)

    async def test_empty_diffs_skipped(self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target):
        mock_gitlab.get_diffs.return_value = []
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            job = ReviewJob(project_id=42, mr_iid=7)
            await reviewer.review_job(job)

        records, _ = await db.list_reviews()
        assert any(r.status == "skipped" and "diffs" in r.skip_reason for r in records)


class TestHappyPath:
    async def test_review_posted_to_gitlab(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.post_mr_note.assert_called_once()
        call_args = mock_gitlab.post_mr_note.call_args
        assert "🤖" in call_args[0][2] or "Automated" in call_args[0][2]

    async def test_review_saved_to_db(self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target):
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        records, total = await db.list_reviews()
        assert total == 1
        rec = records[0]
        assert rec.status == "posted"
        assert rec.project_id == "42"
        assert rec.mr_iid == 7
        assert "Looks good" in rec.review_text

    async def test_mr_metadata_saved(self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target):
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        rec = (await db.list_reviews())[0][0]
        assert rec.author == "alice"
        assert rec.source_branch == "feature"
        assert rec.target_branch == "main"

    async def test_diff_hash_marked_seen(
        self, reviewer, mock_gitlab, mock_llm, db, queue, cfg_with_target
    ):
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7, diff_hash=""))

        # After review, another job with the same diff should be deduped by QueueManager
        rec = (await db.list_reviews())[0][0]
        assert rec.diff_hash != ""


class TestAutoApprove:
    async def test_auto_approve_triggered_when_no_issues(self, reviewer, mock_gitlab, mock_llm, db):
        mock_llm.chat.return_value = "All good! No issues."
        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m"),
            gitlab=GitLabConfig(url="http://gitlab"),
            review_targets=[ReviewTarget(type="project", id="42", auto_approve=True)],
        )
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.approve_mr.assert_called_once_with(42, 7)
        rec = (await db.list_reviews())[0][0]
        assert rec.auto_approved is True

    async def test_auto_approve_blocked_on_critical(self, reviewer, mock_gitlab, mock_llm, db):
        mock_llm.chat.return_value = "## Issues\n- **[CRITICAL]** SQL injection in login handler"
        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m"),
            gitlab=GitLabConfig(url="http://gitlab"),
            review_targets=[ReviewTarget(type="project", id="42", auto_approve=True)],
        )
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.approve_mr.assert_not_called()
        rec = (await db.list_reviews())[0][0]
        assert rec.auto_approved is False

    async def test_auto_approve_blocked_on_high(self, reviewer, mock_gitlab, mock_llm, db):
        mock_llm.chat.return_value = "**[HIGH]** Missing auth check."
        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m"),
            gitlab=GitLabConfig(url="http://gitlab"),
            review_targets=[ReviewTarget(type="project", id="42", auto_approve=True)],
        )
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.approve_mr.assert_not_called()

    async def test_no_auto_approve_when_disabled(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.approve_mr.assert_not_called()


class TestErrorHandling:
    async def test_llm_error_saved_as_error_status(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        mock_llm.chat.side_effect = Exception("LLM timeout")
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        records, _ = await db.list_reviews()
        assert any(r.status == "error" and "LLM timeout" in r.skip_reason for r in records)

    async def test_gitlab_error_saved_as_error_status(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        mock_gitlab.get_mr.side_effect = Exception("GitLab 503")
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        records, _ = await db.list_reviews()
        assert any(r.status == "error" for r in records)

    async def test_no_provider_raises_runtime_error(self, reviewer, mock_gitlab, db):
        cfg = AppConfig(providers=[], model=ModelConfig())  # no providers
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            # RuntimeError from _make_llm_client should be caught → error record
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        records, _ = await db.list_reviews()
        assert any(r.status == "error" for r in records)


class TestSeverityCount:
    def test_critical_counted(self):
        text = "- [CRITICAL] SQL injection\n- [CRITICAL] RCE"
        counts = _severity_count(text)
        assert counts["critical"] == 2

    def test_high_counted(self):
        text = "- [HIGH] Missing auth"
        counts = _severity_count(text)
        assert counts["high"] == 1

    def test_no_issues(self):
        counts = _severity_count("Looks great! No problems found.")
        assert counts["critical"] == 0
        assert counts["high"] == 0

    def test_case_insensitive(self):
        counts = _severity_count("[critical] bad stuff")
        assert counts["critical"] == 1
