"""Tests for reviewer filter logic: branch rules, author rules."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.config import AppConfig, BranchRules, GitLabConfig, ModelConfig, Provider, ReviewTarget
from src.gitlab_client import FileDiff, GitLabBranch, MRInfo
from src.queue_manager import ReviewJob
from src.reviewer import Reviewer, _check_author_rules, _check_branch_rules, set_database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mr(
    target_branch: str = "main", source_branch: str = "feature", author: str = "alice"
) -> MRInfo:
    return MRInfo(
        project_id=10,
        iid=1,
        title="T",
        description="",
        author=author,
        source_branch=source_branch,
        target_branch=target_branch,
        is_draft=False,
        web_url="",
    )


def _target(**kwargs) -> ReviewTarget:
    return ReviewTarget(type="project", id="10", **kwargs)


def _gitlab_mock(protected: bool = True) -> AsyncMock:
    gl = AsyncMock()
    gl.get_mr.return_value = _mr()
    gl.get_diffs.return_value = [
        FileDiff(
            old_path="a.py",
            new_path="a.py",
            diff="+x=1",
            new_file=False,
            deleted_file=False,
            renamed_file=False,
        )
    ]
    gl.list_branches.return_value = [GitLabBranch(name="main", protected=protected)]
    gl.post_mr_note = AsyncMock()
    gl.get_mr_diff_refs.return_value = None
    gl.aclose = AsyncMock()
    return gl


def _cfg(target: ReviewTarget) -> AppConfig:
    return AppConfig(
        providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
        model=ModelConfig(provider_id="p", name="m", inline_comments=False),
        gitlab=GitLabConfig(url="http://gitlab"),
        review_targets=[target],
    )


# ---------------------------------------------------------------------------
# _check_branch_rules (pure logic, no async call needed for pattern tests)
# ---------------------------------------------------------------------------


class TestCheckBranchRules:
    """Unit tests for _check_branch_rules (branch pattern + protected_only)."""

    async def test_star_pattern_allows_any_branch(self):
        target = _target(branches=BranchRules(pattern="*"))
        gl = _gitlab_mock()
        result = await _check_branch_rules(_mr("anything"), target, gl)
        assert result is None

    async def test_exact_pattern_allows_match(self):
        target = _target(branches=BranchRules(pattern="main"))
        gl = _gitlab_mock()
        result = await _check_branch_rules(_mr("main"), target, gl)
        assert result is None

    async def test_exact_pattern_rejects_non_match(self):
        target = _target(branches=BranchRules(pattern="main"))
        gl = _gitlab_mock()
        result = await _check_branch_rules(_mr("dev"), target, gl)
        assert result is not None
        assert "dev" in result
        assert "main" in result

    async def test_glob_pattern_release_star(self):
        target = _target(branches=BranchRules(pattern="release/*"))
        gl = _gitlab_mock()
        assert await _check_branch_rules(_mr("release/1.2"), target, gl) is None
        assert await _check_branch_rules(_mr("release/2.0.0"), target, gl) is None
        assert await _check_branch_rules(_mr("main"), target, gl) is not None

    async def test_comma_separated_or_logic(self):
        target = _target(branches=BranchRules(pattern="main,release/*,hotfix/*"))
        gl = _gitlab_mock()
        assert await _check_branch_rules(_mr("main"), target, gl) is None
        assert await _check_branch_rules(_mr("release/1.0"), target, gl) is None
        assert await _check_branch_rules(_mr("hotfix/urgent"), target, gl) is None
        assert await _check_branch_rules(_mr("dev"), target, gl) is not None
        assert await _check_branch_rules(_mr("feature/x"), target, gl) is not None

    async def test_comma_separated_with_spaces(self):
        target = _target(branches=BranchRules(pattern=" main , release/* "))
        gl = _gitlab_mock()
        assert await _check_branch_rules(_mr("main"), target, gl) is None
        assert await _check_branch_rules(_mr("release/1.0"), target, gl) is None

    async def test_empty_pattern_defaults_to_star(self):
        target = _target(branches=BranchRules(pattern=""))
        gl = _gitlab_mock()
        result = await _check_branch_rules(_mr("any-branch"), target, gl)
        assert result is None

    async def test_protected_only_allows_protected(self):
        target = _target(branches=BranchRules(pattern="*", protected_only=True))
        gl = _gitlab_mock(protected=True)
        result = await _check_branch_rules(_mr("main"), target, gl)
        assert result is None

    async def test_protected_only_rejects_unprotected(self):
        target = _target(branches=BranchRules(pattern="*", protected_only=True))
        gl = _gitlab_mock(protected=False)
        result = await _check_branch_rules(_mr("main"), target, gl)
        assert result is not None
        assert "not protected" in result

    async def test_protected_only_api_failure_proceeds(self):
        """If branch API fails, we proceed (non-blocking)."""
        target = _target(branches=BranchRules(pattern="*", protected_only=True))
        gl = AsyncMock()
        gl.list_branches.side_effect = Exception("API error")
        result = await _check_branch_rules(_mr("main"), target, gl)
        assert result is None  # graceful fallback

    async def test_protected_only_skips_pattern_check_first(self):
        """Pattern must match before protected_only is even checked."""
        target = _target(branches=BranchRules(pattern="main", protected_only=True))
        gl = _gitlab_mock(protected=True)
        result = await _check_branch_rules(_mr("dev"), target, gl)
        assert result is not None  # rejected by pattern, not protection
        gl.list_branches.assert_not_called()


# ---------------------------------------------------------------------------
# _check_author_rules (pure sync, no mocks needed)
# ---------------------------------------------------------------------------


class TestCheckAuthorRules:
    def test_empty_allowlist_allows_everyone(self):
        target = _target()  # no allowlist/skip
        assert _check_author_rules(_mr(author="alice"), target) is None
        assert _check_author_rules(_mr(author="bot"), target) is None

    def test_allowlist_allows_listed_author(self):
        target = _target(author_allowlist=["alice", "bob"])
        assert _check_author_rules(_mr(author="alice"), target) is None
        assert _check_author_rules(_mr(author="bob"), target) is None

    def test_allowlist_rejects_unlisted_author(self):
        target = _target(author_allowlist=["alice"])
        result = _check_author_rules(_mr(author="charlie"), target)
        assert result is not None
        assert "charlie" in result
        assert "allowlist" in result.lower()

    def test_skip_authors_blocks_listed(self):
        target = _target(skip_authors=["ci-bot", "dependabot"])
        result = _check_author_rules(_mr(author="ci-bot"), target)
        assert result is not None
        assert "ci-bot" in result

    def test_skip_authors_allows_others(self):
        target = _target(skip_authors=["ci-bot"])
        assert _check_author_rules(_mr(author="alice"), target) is None

    def test_skip_authors_priority_over_allowlist(self):
        """skip_authors wins even if author is in allowlist."""
        target = _target(
            author_allowlist=["alice", "ci-bot"],
            skip_authors=["ci-bot"],
        )
        result = _check_author_rules(_mr(author="ci-bot"), target)
        assert result is not None  # skip_authors takes priority


# ---------------------------------------------------------------------------
# Integration — review_job skips correctly
# ---------------------------------------------------------------------------


class TestReviewJobFilters:
    async def test_branch_mismatch_skips_job(self, db, prompt_engine, queue):
        target = _target(branches=BranchRules(pattern="main"))
        cfg = _cfg(target)
        gl = _gitlab_mock()
        gl.get_mr.return_value = _mr(target_branch="dev")

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="review")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "skipped"
        assert "dev" in records[0].skip_reason
        llm.chat.assert_not_called()
        gl.post_mr_note.assert_not_called()

    async def test_branch_match_proceeds(self, db, prompt_engine, queue):
        target = _target(branches=BranchRules(pattern="main"))
        cfg = _cfg(target)
        gl = _gitlab_mock()
        # target_branch = "main" — should pass

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="LGTM")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "posted"

    async def test_skip_author_skips_job(self, db, prompt_engine, queue):
        target = _target(skip_authors=["ci-bot"])
        cfg = _cfg(target)
        gl = _gitlab_mock()
        gl.get_mr.return_value = _mr(author="ci-bot")

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="review")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "skipped"
        assert "ci-bot" in records[0].skip_reason
        llm.chat.assert_not_called()

    async def test_author_not_in_allowlist_skips(self, db, prompt_engine, queue):
        target = _target(author_allowlist=["alice"])
        cfg = _cfg(target)
        gl = _gitlab_mock()
        gl.get_mr.return_value = _mr(author="mallory")

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="review")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "skipped"
        assert "allowlist" in records[0].skip_reason.lower()

    async def test_author_in_allowlist_proceeds(self, db, prompt_engine, queue):
        target = _target(author_allowlist=["alice", "bob"])
        cfg = _cfg(target)
        gl = _gitlab_mock()
        gl.get_mr.return_value = _mr(author="alice")

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="LGTM")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "posted"

    async def test_no_target_skips_no_branch_check(self, db, prompt_engine, queue):
        """Without a matching target, branch rules aren't applied."""
        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m", inline_comments=False),
            gitlab=GitLabConfig(url="http://gitlab"),
            review_targets=[],  # no targets at all
        )
        gl = _gitlab_mock()
        gl.get_mr.return_value = _mr(target_branch="dev")  # would fail if filter applied

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value="LGTM")
        llm.aclose = AsyncMock()

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=10, mr_iid=1))

        records, _ = await db.list_reviews()
        assert records[0].status == "posted"  # no target = no filter
