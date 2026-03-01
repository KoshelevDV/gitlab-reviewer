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
from src.reviewer import Reviewer, _find_target, _severity_count, set_database


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


class TestFindTarget:
    def _make_cfg(self, *targets: ReviewTarget) -> AppConfig:
        return AppConfig(review_targets=list(targets))

    def test_find_target_type_all(self):
        cfg = self._make_cfg(ReviewTarget(type="all", id=""))
        result = _find_target(cfg, "999")
        assert result is not None
        assert result.type == "all"

    def test_find_target_type_project_exact(self):
        cfg = self._make_cfg(ReviewTarget(type="project", id="42"))
        result = _find_target(cfg, "42")
        assert result is not None
        assert result.id == "42"

    def test_find_target_type_project_miss(self):
        cfg = self._make_cfg(ReviewTarget(type="project", id="42"))
        result = _find_target(cfg, "99")
        assert result is None

    def test_find_target_type_group_with_project_ids(self):
        cfg = self._make_cfg(ReviewTarget(type="group", id="10", project_ids=["42", "43"]))
        result = _find_target(cfg, "42")
        assert result is not None
        assert result.type == "group"

    def test_find_target_type_group_project_ids_miss(self):
        cfg = self._make_cfg(ReviewTarget(type="group", id="10", project_ids=["99"]))
        result = _find_target(cfg, "42")
        assert result is None

    def test_find_target_type_group_empty_project_ids(self):
        cfg = self._make_cfg(ReviewTarget(type="group", id="10", project_ids=[]))
        result = _find_target(cfg, "42")
        assert result is not None
        assert result.type == "group"

    def test_find_target_returns_none_when_no_match(self):
        cfg = self._make_cfg(
            ReviewTarget(type="project", id="1"),
            ReviewTarget(type="group", id="10", project_ids=["5", "6"]),
        )
        result = _find_target(cfg, "999")
        assert result is None


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


class TestRiskScore:
    def _mr(self, is_draft=False):
        return MRInfo(
            project_id=1, iid=1, title="T", description="",
            author="dev", source_branch="feat", target_branch="main",
            is_draft=is_draft, web_url="http://gl/mr/1",
        )

    def _diff(self, path="main.py", lines=10):
        from src.gitlab_client import FileDiff
        return FileDiff(
            old_path=path, new_path=path,
            diff="\n" * lines, new_file=False,
            deleted_file=False, renamed_file=False,
        )

    def test_zero_for_trivial_mr(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr()
        score = _compute_risk_score(mr, [self._diff(lines=5)], "Looks good.")
        assert score == 0

    def test_large_diff_increases_score(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr()
        score = _compute_risk_score(mr, [self._diff(lines=600)], "Looks good.")
        assert score >= 20

    def test_sensitive_path_increases_score(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr()
        score = _compute_risk_score(mr, [self._diff(path="auth/login.py", lines=5)], "")
        assert score >= 20

    def test_critical_finding_increases_score(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr()
        score = _compute_risk_score(mr, [self._diff()], "- [CRITICAL] SQL injection found")
        assert score >= 15

    def test_draft_reduces_score(self):
        from src.reviewer import _compute_risk_score
        mr_draft = self._mr(is_draft=True)
        mr_normal = self._mr(is_draft=False)
        s_draft = _compute_risk_score(mr_draft, [self._diff(lines=300)], "")
        s_normal = _compute_risk_score(mr_normal, [self._diff(lines=300)], "")
        assert s_draft < s_normal

    def test_score_clamped_to_100(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr()
        diffs = [self._diff(path=f"security/auth{i}.py", lines=600) for i in range(5)]
        text = "- [CRITICAL] issue\n" * 10
        score = _compute_risk_score(mr, diffs, text)
        assert score <= 100

    def test_medium_finding_increases_score(self):
        from src.reviewer import _compute_risk_score
        score = _compute_risk_score(self._mr(), [], "- [MEDIUM] minor issue\n" * 3)
        assert score >= 9

    def test_score_clamped_to_0(self):
        from src.reviewer import _compute_risk_score
        mr = self._mr(is_draft=True)
        score = _compute_risk_score(mr, [], "")
        assert score >= 0


class TestIncrementalReview:
    """Test that incremental review uses GitLab MR Versions API."""

    async def test_incremental_diff_used_when_previous_version_exists(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        """When a previous review exists with a lower version_id, compare_commits is called."""
        from src.db import ReviewRecord
        from src.gitlab_client import FileDiff

        # Seed the DB with a previous review at version 1
        rec = ReviewRecord(
            project_id="42", mr_iid=7, mr_title="T", mr_url="",
            author="a", source_branch="f", target_branch="main",
            diff_hash="old", prompt_names=["default"],
            review_text="old review", status="posted",
            mr_version_id=1,
        )
        await db.save_review(rec)

        # get_mr_versions returns both versions so reviewer can find prev sha
        mock_gitlab.get_mr_versions = AsyncMock(
            return_value=[
                {"id": 2, "head_commit_sha": "sha_new", "base_commit_sha": "b",
                 "start_commit_sha": "s"},
                {"id": 1, "head_commit_sha": "sha_old", "base_commit_sha": "b",
                 "start_commit_sha": "s"},
            ]
        )
        incremental_diffs = [
            FileDiff("changed.py", "changed.py", "+new line", False, False, False)
        ]
        mock_gitlab.compare_commits = AsyncMock(return_value=incremental_diffs)

        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            job = ReviewJob(project_id=42, mr_iid=7)
            await reviewer.review_job(job)

        # Verify compare_commits was used with the correct SHAs
        mock_gitlab.compare_commits.assert_called_once()
        call_args = mock_gitlab.compare_commits.call_args
        assert call_args.args[1] == "sha_old"  # from_sha = previous version HEAD
        assert call_args.args[2] == "sha_new"  # to_sha = current version HEAD

        # Verify the new review has version_id=2
        records, _ = await db.list_reviews()
        latest = max(records, key=lambda r: r.id)
        assert latest.mr_version_id == 2

    async def test_full_diff_used_when_no_previous_version(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        """With no previous version in DB, get_diffs (full diff) is used."""
        mock_gitlab.get_mr_versions = AsyncMock(
            return_value=[{"id": 1, "head_commit_sha": "h", "base_commit_sha": "b",
                           "start_commit_sha": "s"}]
        )

        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            job = ReviewJob(project_id=42, mr_iid=7)
            await reviewer.review_job(job)

        # Full diffs were used; get_version_diffs should not be called
        mock_gitlab.get_diffs.assert_called()

    async def test_fallback_to_full_diff_when_versions_api_fails(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target
    ):
        """If get_mr_versions raises, fall back to full diff gracefully."""
        mock_gitlab.get_mr_versions = AsyncMock(side_effect=Exception("API error"))

        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            job = ReviewJob(project_id=42, mr_iid=7)
            await reviewer.review_job(job)

        records, _ = await db.list_reviews()
        assert any(r.status == "posted" for r in records)
        mock_gitlab.get_diffs.assert_called()


class TestDetectLanguage:
    def _diff(self, path: str) -> FileDiff:
        return FileDiff(old_path=path, new_path=path, diff="", new_file=False,
                        deleted_file=False, renamed_file=False)

    def test_detects_python(self):
        from src.reviewer import _detect_language
        diffs = [self._diff(f"mod{i}.py") for i in range(5)]
        assert _detect_language(diffs) == "python"

    def test_detects_rust(self):
        from src.reviewer import _detect_language
        diffs = [self._diff("src/main.rs"), self._diff("src/lib.rs"),
                 self._diff("src/utils.rs")]
        assert _detect_language(diffs) == "rust"

    def test_detects_typescript(self):
        from src.reviewer import _detect_language
        diffs = [self._diff("src/app.tsx"), self._diff("src/index.ts"),
                 self._diff("src/utils.ts")]
        assert _detect_language(diffs) == "typescript"

    def test_detects_go(self):
        from src.reviewer import _detect_language
        diffs = [self._diff("main.go"), self._diff("handler.go")]
        assert _detect_language(diffs) == "go"

    def test_returns_none_below_threshold(self):
        from src.reviewer import _detect_language
        # Only 1 Python file out of 5 total → below 40% threshold
        diffs = [self._diff("a.py")] + [self._diff(f"file{i}.txt") for i in range(4)]
        result = _detect_language(diffs)
        assert result != "python"

    def test_returns_none_for_unknown_extensions(self):
        from src.reviewer import _detect_language
        diffs = [self._diff("Makefile"), self._diff("README.md")]
        assert _detect_language(diffs) is None

    def test_javascript_maps_to_typescript(self):
        from src.reviewer import _detect_language
        diffs = [self._diff("app.js"), self._diff("index.js"), self._diff("utils.js")]
        assert _detect_language(diffs) == "typescript"
