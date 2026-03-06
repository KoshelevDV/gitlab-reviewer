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
            project_id=1,
            iid=1,
            title="T",
            description="",
            author="dev",
            source_branch="feat",
            target_branch="main",
            is_draft=is_draft,
            web_url="http://gl/mr/1",
        )

    def _diff(self, path="main.py", lines=10):
        from src.gitlab_client import FileDiff

        return FileDiff(
            old_path=path,
            new_path=path,
            diff="\n" * lines,
            new_file=False,
            deleted_file=False,
            renamed_file=False,
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
            project_id="42",
            mr_iid=7,
            mr_title="T",
            mr_url="",
            author="a",
            source_branch="f",
            target_branch="main",
            diff_hash="old",
            prompt_names=["default"],
            review_text="old review",
            status="posted",
            mr_version_id=1,
        )
        await db.save_review(rec)

        # get_mr_versions returns both versions so reviewer can find prev sha
        mock_gitlab.get_mr_versions = AsyncMock(
            return_value=[
                {
                    "id": 2,
                    "head_commit_sha": "sha_new",
                    "base_commit_sha": "b",
                    "start_commit_sha": "s",
                },
                {
                    "id": 1,
                    "head_commit_sha": "sha_old",
                    "base_commit_sha": "b",
                    "start_commit_sha": "s",
                },
            ]
        )
        incremental_diffs = [FileDiff("changed.py", "changed.py", "+new line", False, False, False)]
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
            return_value=[
                {"id": 1, "head_commit_sha": "h", "base_commit_sha": "b", "start_commit_sha": "s"}
            ]
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
        return FileDiff(
            old_path=path,
            new_path=path,
            diff="",
            new_file=False,
            deleted_file=False,
            renamed_file=False,
        )

    def test_detects_python(self):
        from src.reviewer import _detect_language

        diffs = [self._diff(f"mod{i}.py") for i in range(5)]
        assert _detect_language(diffs) == "python"

    def test_detects_rust(self):
        from src.reviewer import _detect_language

        diffs = [self._diff("src/main.rs"), self._diff("src/lib.rs"), self._diff("src/utils.rs")]
        assert _detect_language(diffs) == "rust"

    def test_detects_typescript(self):
        from src.reviewer import _detect_language

        diffs = [self._diff("src/app.tsx"), self._diff("src/index.ts"), self._diff("src/utils.ts")]
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


# ---------------------------------------------------------------------------
# Tests for _parse_diff_line_map and _annotate_diff_with_line_numbers
# ---------------------------------------------------------------------------


class TestParseDiffLineMap:
    """Unit tests for _parse_diff_line_map."""

    def test_all_added_lines_new_file(self):
        from src.reviewer import _parse_diff_line_map

        diff = "@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three\n"
        m = _parse_diff_line_map(diff)
        # All lines are added → old_line is None for every entry
        assert m == {1: None, 2: None, 3: None}

    def test_pure_context_lines(self):
        from src.reviewer import _parse_diff_line_map

        diff = "@@ -5,3 +5,3 @@\n line a\n line b\n line c\n"
        m = _parse_diff_line_map(diff)
        # Context lines map new_line → old_line
        assert m == {5: 5, 6: 6, 7: 7}

    def test_mixed_added_context_deleted(self):
        from src.reviewer import _parse_diff_line_map

        diff = (
            "@@ -10,4 +10,4 @@\n"
            " context1\n"  # new=10, old=10
            "-old line\n"  # deleted → not in map
            "+new line\n"  # new=11, old=None
            " context2\n"  # new=12, old=11
        )
        m = _parse_diff_line_map(diff)
        assert m[10] == 10  # context: new=10, old=10
        assert m[11] is None  # added:   new=11, no old
        assert m[12] == 12  # context after deletion: old_cursor skipped 11 (deleted), so old=12
        assert 13 not in m  # nothing beyond

    def test_multi_hunk(self):
        from src.reviewer import _parse_diff_line_map

        diff = (
            "@@ -1,2 +1,2 @@\n"
            " ctx\n"  # new=1, old=1
            "+added1\n"  # new=2, old=None
            "@@ -10,1 +10,2 @@\n"
            " ctx2\n"  # new=10, old=10
            "+added2\n"  # new=11, old=None
        )
        m = _parse_diff_line_map(diff)
        assert m[1] == 1
        assert m[2] is None
        assert m[10] == 10
        assert m[11] is None

    def test_empty_diff(self):
        from src.reviewer import _parse_diff_line_map

        assert _parse_diff_line_map("") == {}


class TestAnnotateDiffWithLineNumbers:
    """Unit tests for _annotate_diff_with_line_numbers."""

    def test_added_lines_get_correct_numbers(self):
        from src.reviewer import _annotate_diff_with_line_numbers

        diff = "@@ -0,0 +1,2 @@\n+hello\n+world"
        out = _annotate_diff_with_line_numbers(diff)
        assert "+    1 | hello" in out
        assert "+    2 | world" in out

    def test_context_lines_get_numbers(self):
        from src.reviewer import _annotate_diff_with_line_numbers

        diff = "@@ -5,2 +5,2 @@\n ctx\n+new"
        out = _annotate_diff_with_line_numbers(diff)
        assert "5 | ctx" in out
        assert "+    6 | new" in out

    def test_deleted_lines_get_old_line_numbers(self):
        from src.reviewer import _annotate_diff_with_line_numbers

        diff = "@@ -3,1 +3,0 @@\n-removed"
        out = _annotate_diff_with_line_numbers(diff)
        assert "-    3 | removed" in out

    def test_hunk_header_preserved(self):
        from src.reviewer import _annotate_diff_with_line_numbers

        diff = "@@ -1,1 +1,1 @@\n+x"
        out = _annotate_diff_with_line_numbers(diff)
        assert "@@ -1,1 +1,1 @@" in out

    def test_no_crash_on_empty_diff(self):
        from src.reviewer import _annotate_diff_with_line_numbers

        assert _annotate_diff_with_line_numbers("") == ""


class TestIsCommentContent:
    """Unit tests for _is_comment_content."""

    def test_python_comment(self):
        from src.reviewer import _is_comment_content

        assert _is_comment_content("# this is a comment") is True
        assert _is_comment_content("    # indented comment") is True

    def test_cpp_line_comment(self):
        from src.reviewer import _is_comment_content

        assert _is_comment_content("// single line") is True
        assert _is_comment_content("    // indented") is True

    def test_block_comment_line(self):
        from src.reviewer import _is_comment_content

        assert _is_comment_content("/* start block */") is True
        assert _is_comment_content("* middle of block") is True

    def test_code_lines_not_comment(self):
        from src.reviewer import _is_comment_content

        assert _is_comment_content('db_password = "supersecret123"') is False
        assert _is_comment_content("def store_config():") is False
        assert _is_comment_content("cursor.execute(query)") is False

    def test_empty_line(self):
        from src.reviewer import _is_comment_content

        assert _is_comment_content("") is False
        assert _is_comment_content("   ") is False


class TestBuildDiffContentMap:
    """Unit tests for _build_diff_content_map."""

    def test_added_lines_content(self):
        from src.reviewer import _build_diff_content_map

        diff = "@@ -0,0 +5,2 @@\n+hello = 1\n+world = 2\n"
        m = _build_diff_content_map(diff)
        assert m[5] == "hello = 1"
        assert m[6] == "world = 2"

    def test_context_line_content(self):
        from src.reviewer import _build_diff_content_map

        diff = "@@ -3,1 +3,1 @@\n ctx_line\n"
        m = _build_diff_content_map(diff)
        assert m[3] == "ctx_line"

    def test_deleted_lines_excluded(self):
        from src.reviewer import _build_diff_content_map

        diff = "@@ -3,1 +3,0 @@\n-removed\n"
        m = _build_diff_content_map(diff)
        # deleted line has no new_line entry
        assert 3 not in m


# ---------------------------------------------------------------------------
# Tests for review_job_v2 memory integration
# ---------------------------------------------------------------------------


def _make_v2_cfg(memory_enabled: bool = False):
    """Build a minimal AppConfig for v2 pipeline tests."""
    from src.config import AppConfig, GitLabConfig, MemoryConfig, ModelConfig, Provider, ReviewConfig

    return AppConfig(
        providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
        model=ModelConfig(provider_id="p", name="test-model"),
        gitlab=GitLabConfig(url="http://gitlab"),
        review=ReviewConfig(pipeline_v2=True, prompts_dir="prompts"),
        memory=MemoryConfig(
            enabled=memory_enabled,
            qdrant_url="http://fake-qdrant:6333",
            collection="test",
            top_k=3,
        ),
    )


def _make_v2_gitlab_mock(mock_mr, mock_diffs):
    """Build a fully mocked GitLabClient for v2 reviews."""
    gl = AsyncMock()
    gl.get_mr.return_value = mock_mr
    gl.get_diffs.return_value = mock_diffs
    gl.post_mr_note = AsyncMock()
    gl.aclose = AsyncMock()
    return gl


class TestMemoryV2:
    """Tests for review_job_v2 memory recall/remember integration."""

    @pytest.mark.asyncio
    async def test_review_job_v2_memory_disabled_skips_store(
        self, reviewer, db, mock_mr, mock_diffs
    ):
        """When memory.enabled=False, MemoryStore.recall and remember must not be called."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.pipeline import ReviewRole, RoleResult
        from src.reviewer import set_database, set_memory_store

        cfg = _make_v2_cfg(memory_enabled=False)
        mock_gitlab = _make_v2_gitlab_mock(mock_mr, mock_diffs)
        mock_llm = AsyncMock()
        mock_llm.aclose = AsyncMock()

        # Mock memory store — should NOT be called
        mock_memory = AsyncMock()
        mock_memory.recall = AsyncMock(return_value=[])
        mock_memory.remember = AsyncMock()

        # Mock PipelineManager
        fake_pm_instance = AsyncMock()
        fake_pm_instance.run = AsyncMock(
            return_value=[
                RoleResult(role=ReviewRole.REVIEWER, findings="looks good", blocking_count=0),
            ]
        )
        FakePipelineManager = MagicMock(return_value=fake_pm_instance)
        FakePipelineManager.detect_stack = MagicMock(return_value="python")

        set_database(db)
        set_memory_store(mock_memory)

        try:
            with (
                patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
                patch("src.reviewer._make_llm_client", return_value=mock_llm),
                patch("src.reviewer.get_config", return_value=cfg),
                patch("src.reviewer.get_agents_md", AsyncMock(return_value="")),
                patch("src.reviewer.get_docs_context", AsyncMock(return_value="")),
                patch("src.reviewer.get_security_baseline", AsyncMock(return_value="")),
                patch("src.reviewer.get_task_context", AsyncMock(return_value="")),
                patch("src.reviewer.get_dynamic_context", AsyncMock(return_value="")),
                patch("src.reviewer.PipelineManager", FakePipelineManager),
                patch("src.reviewer._notify", AsyncMock()),
                patch("src.reviewer._metrics") as mock_metrics,
            ):
                mock_metrics.record_review = MagicMock()
                await reviewer.review_job_v2(ReviewJob(project_id=42, mr_iid=7))
        finally:
            set_memory_store(None)

        mock_memory.recall.assert_not_called()
        mock_memory.remember.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_job_v2_memory_enabled_correct_order(
        self, reviewer, db, mock_mr, mock_diffs
    ):
        """recall() called before pipeline, remember() called after with blocking findings."""
        from unittest.mock import AsyncMock, MagicMock, call, patch

        from src.pipeline import ReviewRole, RoleResult
        from src.reviewer import set_database, set_memory_store

        cfg = _make_v2_cfg(memory_enabled=True)
        mock_gitlab = _make_v2_gitlab_mock(mock_mr, mock_diffs)
        mock_llm = AsyncMock()
        mock_llm.aclose = AsyncMock()

        # Track call order across recall / pm.run / remember
        call_order: list[str] = []

        async def recall_side_effect(*args, **kwargs):
            call_order.append("recall")
            return []

        async def remember_side_effect(*args, **kwargs):
            call_order.append("remember")

        mock_memory = AsyncMock()
        mock_memory.recall = AsyncMock(side_effect=recall_side_effect)
        mock_memory.remember = AsyncMock(side_effect=remember_side_effect)

        async def pm_run_side_effect(ctx):
            call_order.append("pm.run")
            return [
                RoleResult(
                    role=ReviewRole.SECURITY,
                    findings="[BLOCKING] hardcoded secret found",
                    blocking_count=1,
                ),
                RoleResult(role=ReviewRole.REVIEWER, findings="final review", blocking_count=0),
            ]

        fake_pm_instance = AsyncMock()
        fake_pm_instance.run = AsyncMock(side_effect=pm_run_side_effect)
        FakePipelineManager = MagicMock(return_value=fake_pm_instance)
        FakePipelineManager.detect_stack = MagicMock(return_value="python")

        set_database(db)
        set_memory_store(mock_memory)

        try:
            with (
                patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
                patch("src.reviewer._make_llm_client", return_value=mock_llm),
                patch("src.reviewer.get_config", return_value=cfg),
                patch("src.reviewer.get_agents_md", AsyncMock(return_value="")),
                patch("src.reviewer.get_docs_context", AsyncMock(return_value="")),
                patch("src.reviewer.get_security_baseline", AsyncMock(return_value="")),
                patch("src.reviewer.get_task_context", AsyncMock(return_value="")),
                patch("src.reviewer.get_dynamic_context", AsyncMock(return_value="")),
                patch("src.reviewer.PipelineManager", FakePipelineManager),
                patch("src.reviewer._notify", AsyncMock()),
                patch("src.reviewer._metrics") as mock_metrics,
            ):
                mock_metrics.record_review = MagicMock()
                await reviewer.review_job_v2(ReviewJob(project_id=42, mr_iid=7))
        finally:
            set_memory_store(None)

        # recall must have been called
        mock_memory.recall.assert_called_once()
        recall_kwargs = mock_memory.recall.call_args.kwargs
        assert recall_kwargs.get("project_id") == "42"

        # remember must have been called (blocking_count > 0)
        mock_memory.remember.assert_called_once()

        # Order: recall → pm.run → remember
        assert call_order.index("recall") < call_order.index("pm.run"), (
            f"recall must happen BEFORE pm.run, got order: {call_order}"
        )
        assert call_order.index("pm.run") < call_order.index("remember"), (
            f"pm.run must happen BEFORE remember, got order: {call_order}"
        )


# ── _make_llm_client timeout test ─────────────────────────────────────────────

def test_make_llm_client_uses_config_timeout():
    """_make_llm_client must pass ModelConfig.timeout to LLMClient constructor."""
    from unittest.mock import MagicMock, patch

    from pydantic import SecretStr

    from src.config import AppConfig, ModelConfig, Provider
    from src.reviewer import _make_llm_client

    cfg = AppConfig(
        providers=[
            Provider(
                id="p1",
                name="Test Provider",
                type="ollama",
                url="http://localhost:11434",
                active=True,
                api_key=SecretStr(""),
            )
        ],
        model=ModelConfig(provider_id="p1", name="test-model", timeout=42),
    )

    with patch("src.reviewer.LLMClient") as mock_llm_cls:
        mock_llm_cls.return_value = MagicMock()
        _make_llm_client(cfg)

    mock_llm_cls.assert_called_once()
    _, kwargs = mock_llm_cls.call_args
    assert kwargs.get("timeout") == 42, (
        f"Expected LLMClient(timeout=42), got timeout={kwargs.get('timeout')!r}"
    )


class TestSummaryCommentMrUrl:
    """Q-9: MR URL link prepended to summary comment when mr_url is set."""

    async def test_summary_contains_mr_link_when_url_set(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target, mock_mr
    ):
        """When mr_url is non-empty the summary comment starts with a clickable MR link."""
        mock_mr.web_url = "http://gitlab.example.com/mr/7"
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.post_mr_note.assert_called_once()
        comment = mock_gitlab.post_mr_note.call_args[0][2]
        assert "[MR #7" in comment, f"Expected MR link in comment, got:\n{comment[:300]}"
        assert "http://gitlab.example.com/mr/7" in comment

    async def test_summary_no_mr_link_when_url_empty(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target, mock_mr
    ):
        """When mr_url is empty the summary comment must NOT contain a MR markdown link."""
        mock_mr.web_url = ""
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))

        mock_gitlab.post_mr_note.assert_called_once()
        comment = mock_gitlab.post_mr_note.call_args[0][2]
        assert "[MR #7" not in comment, f"Did not expect MR link when url is empty:\n{comment[:300]}"

    async def test_summary_no_mr_link_when_url_has_javascript_scheme(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target, mock_mr
    ):
        """javascript: URL must NOT produce a link in summary comment."""
        mock_mr.web_url = "javascript:alert(1)"
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))
        comment = mock_gitlab.post_mr_note.call_args[0][2]
        assert "javascript:" not in comment
        assert "[MR #7" not in comment

    async def test_summary_mr_title_with_brackets_escaped(
        self, reviewer, mock_gitlab, mock_llm, db, cfg_with_target, mock_mr
    ):
        """MR title with ] chars must be escaped in link text."""
        mock_mr.web_url = "http://gitlab.example.com/mr/7"
        mock_mr.title = "Fix [bug] in auth"
        set_database(db)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=mock_gitlab),
            patch("src.reviewer._make_llm_client", return_value=mock_llm),
            patch("src.reviewer.get_config", return_value=cfg_with_target),
        ):
            await reviewer.review_job(ReviewJob(project_id=42, mr_iid=7))
        comment = mock_gitlab.post_mr_note.call_args[0][2]
        assert "\\]" in comment, f"Expected escaped bracket '\\]' in comment, got:\n{comment[:300]}"
        assert "Fix" in comment, f"Expected title text 'Fix' in comment, got:\n{comment[:300]}"
        assert "http://gitlab.example.com/mr/7" in comment
