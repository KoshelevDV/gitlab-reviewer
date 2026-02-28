"""Tests for inline comment parsing and GitLab diff annotation flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import respx
from httpx import Response

from src.db import ReviewRecord
from src.queue_manager import QueueManager
from src.reviewer import _severity_count, parse_review_sections

# ---------------------------------------------------------------------------
# parse_review_sections
# ---------------------------------------------------------------------------


class TestParseReviewSections:
    def test_no_inline_returns_full_text(self):
        text = "## Summary\n\nLooks good. No issues."
        comments, summary = parse_review_sections(text)
        assert comments == []
        assert "Looks good" in summary

    def test_single_inline_extracted(self):
        text = (
            '<!-- REVIEW_INLINE file="src/app.py" line="42" -->\n'
            "**[HIGH]** SQL injection here.\n"
            "<!-- REVIEW_ENDINLINE -->\n\n"
            "## Summary\nOverall OK."
        )
        comments, summary = parse_review_sections(text)
        assert len(comments) == 1
        assert comments[0]["path"] == "src/app.py"
        assert comments[0]["line"] == 42
        assert "[HIGH]" in comments[0]["body"]

    def test_inline_removed_from_summary(self):
        text = (
            '<!-- REVIEW_INLINE file="a.py" line="1" -->\n'
            "Issue here.\n"
            "<!-- REVIEW_ENDINLINE -->\n\n"
            "## Summary\nClean code."
        )
        _, summary = parse_review_sections(text)
        assert "REVIEW_INLINE" not in summary
        assert "REVIEW_ENDINLINE" not in summary
        assert "Clean code" in summary

    def test_multiple_inline_comments(self):
        lines = []
        for i in range(5):
            lines.append(f'<!-- REVIEW_INLINE file="file{i}.py" line="{i + 1}" -->')
            lines.append(f"Issue {i}.")
            lines.append("<!-- REVIEW_ENDINLINE -->")
        text = "\n".join(lines) + "\n\n## Summary\nDone."
        comments, summary = parse_review_sections(text)
        assert len(comments) == 5
        assert comments[2]["path"] == "file2.py"
        assert comments[4]["line"] == 5

    def test_caps_at_10_inline_comments(self):
        lines = []
        for i in range(15):
            lines.append(f'<!-- REVIEW_INLINE file="f{i}.py" line="{i}" -->')
            lines.append(f"Issue {i}.")
            lines.append("<!-- REVIEW_ENDINLINE -->")
        text = "\n".join(lines)
        comments, _ = parse_review_sections(text)
        assert len(comments) == 10

    def test_case_insensitive_markers(self):
        text = '<!-- review_inline file="x.py" line="5" -->\nProblem.\n<!-- review_endinline -->'
        comments, _ = parse_review_sections(text)
        assert len(comments) == 1

    def test_multiline_body_preserved(self):
        text = (
            '<!-- REVIEW_INLINE file="utils.py" line="10" -->\n'
            "**[MEDIUM]** Missing error handling.\n\n"
            "Add try/except around the DB call.\n"
            "<!-- REVIEW_ENDINLINE -->"
        )
        comments, _ = parse_review_sections(text)
        assert "Missing error handling" in comments[0]["body"]
        assert "try/except" in comments[0]["body"]

    def test_empty_body_skipped(self):
        text = '<!-- REVIEW_INLINE file="x.py" line="1" -->\n\n<!-- REVIEW_ENDINLINE -->'
        comments, _ = parse_review_sections(text)
        assert len(comments) == 0

    def test_fallback_when_summary_empty_after_strip(self):
        # Only inline blocks, no other text → fallback to original
        text = '<!-- REVIEW_INLINE file="x.py" line="1" -->\nIssue.\n<!-- REVIEW_ENDINLINE -->'
        _, summary = parse_review_sections(text)
        assert summary  # not empty

    def test_whitespace_in_markers_allowed(self):
        text = '<!--  REVIEW_INLINE  file="a.py"  line="3"  -->\nNote.\n<!--  REVIEW_ENDINLINE  -->'
        comments, _ = parse_review_sections(text)
        assert len(comments) == 1

    def test_severity_count_from_inline(self):
        text = (
            '<!-- REVIEW_INLINE file="a.py" line="1" -->\n'
            "**[CRITICAL]** SQL injection.\n"
            "<!-- REVIEW_ENDINLINE -->\n"
            '<!-- REVIEW_INLINE file="b.py" line="2" -->\n'
            "**[HIGH]** Auth missing.\n"
            "<!-- REVIEW_ENDINLINE -->\n"
            "## Summary\nTwo issues."
        )
        counts = _severity_count(text)
        assert counts["critical"] == 1
        assert counts["high"] == 1


# ---------------------------------------------------------------------------
# GitLabClient.get_mr_diff_refs
# ---------------------------------------------------------------------------


class TestGetMrDiffRefs:
    @respx.mock
    async def test_returns_refs_from_latest_version(self):
        from src.gitlab_client import GitLabClient

        respx.get("http://gitlab/api/v4/projects/42/merge_requests/7/versions").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 3,
                        "base_commit_sha": "aabbcc",
                        "start_commit_sha": "112233",
                        "head_commit_sha": "ddeeff",
                    }
                ],
            )
        )
        gl = GitLabClient("http://gitlab", "tok")
        try:
            refs = await gl.get_mr_diff_refs(42, 7)
        finally:
            await gl.aclose()
        assert refs is not None
        assert refs["base_sha"] == "aabbcc"
        assert refs["start_sha"] == "112233"
        assert refs["head_sha"] == "ddeeff"

    @respx.mock
    async def test_returns_none_for_empty_versions(self):
        from src.gitlab_client import GitLabClient

        respx.get("http://gitlab/api/v4/projects/42/merge_requests/7/versions").mock(
            return_value=Response(200, json=[])
        )
        gl = GitLabClient("http://gitlab", "tok")
        try:
            refs = await gl.get_mr_diff_refs(42, 7)
        finally:
            await gl.aclose()
        assert refs is None

    @respx.mock
    async def test_returns_first_version_when_multiple(self):
        from src.gitlab_client import GitLabClient

        respx.get("http://gitlab/api/v4/projects/42/merge_requests/7/versions").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "id": 10,
                        "base_commit_sha": "latest",
                        "start_commit_sha": "s",
                        "head_commit_sha": "h",
                    },
                    {
                        "id": 5,
                        "base_commit_sha": "old",
                        "start_commit_sha": "s2",
                        "head_commit_sha": "h2",
                    },
                ],
            )
        )
        gl = GitLabClient("http://gitlab", "tok")
        try:
            refs = await gl.get_mr_diff_refs(42, 7)
        finally:
            await gl.aclose()
        assert refs["base_sha"] == "latest"


# ---------------------------------------------------------------------------
# QueueManager.load_seen_from_db
# ---------------------------------------------------------------------------


class TestLoadSeenFromDb:
    async def test_loads_hashes_from_db(self, db):
        # Seed some reviews with diff hashes in DB
        await db.save_review(
            ReviewRecord(
                project_id="10",
                mr_iid=1,
                status="posted",
                diff_hash="abc123",
            )
        )
        await db.save_review(
            ReviewRecord(
                project_id="10",
                mr_iid=2,
                status="posted",
                diff_hash="def456",
            )
        )

        q = QueueManager(max_concurrent=1, max_size=10)
        count = await q.load_seen_from_db(db)
        assert count == 2

        # These hashes should now be deduplicated
        from src.queue_manager import ReviewJob

        j1 = ReviewJob(project_id=10, mr_iid=1, diff_hash="abc123")
        j2 = ReviewJob(project_id=10, mr_iid=2, diff_hash="def456")
        assert not await q.enqueue(j1)  # deduped
        assert not await q.enqueue(j2)  # deduped

    async def test_ignores_empty_diff_hashes(self, db):
        await db.save_review(
            ReviewRecord(
                project_id="10",
                mr_iid=1,
                status="posted",
                diff_hash="",
            )
        )
        q = QueueManager(max_concurrent=1, max_size=10)
        count = await q.load_seen_from_db(db)
        assert count == 0

    async def test_new_mr_same_hash_still_allowed_after_restore(self, db):
        """Same hash but different project_id+mr_iid → not deduped."""
        await db.save_review(
            ReviewRecord(
                project_id="10",
                mr_iid=1,
                status="posted",
                diff_hash="samehash",
            )
        )
        q = QueueManager(max_concurrent=1, max_size=10)
        await q.load_seen_from_db(db)

        from src.queue_manager import ReviewJob

        # Different project, same hash → not a duplicate
        j = ReviewJob(project_id=99, mr_iid=5, diff_hash="samehash")
        assert await q.enqueue(j)

    async def test_returns_zero_on_empty_db(self, db):
        q = QueueManager(max_concurrent=1, max_size=10)
        count = await q.load_seen_from_db(db)
        assert count == 0

    async def test_handles_db_error_gracefully(self):
        bad_db = AsyncMock()
        bad_db.list_diff_hashes.side_effect = Exception("DB offline")
        q = QueueManager(max_concurrent=1, max_size=10)
        count = await q.load_seen_from_db(bad_db)
        assert count == 0  # doesn't raise


# ---------------------------------------------------------------------------
# DB migration — inline_count column
# ---------------------------------------------------------------------------


class TestDbMigration:
    async def test_inline_count_saved_and_loaded(self, db):
        rec = ReviewRecord(
            project_id="42",
            mr_iid=1,
            status="posted",
            inline_count=3,
        )
        await db.save_review(rec)
        loaded = await db.get_review(rec.id)
        assert loaded is not None
        assert loaded.inline_count == 3

    async def test_inline_count_defaults_to_zero(self, db):
        rec = ReviewRecord(project_id="42", mr_iid=1, status="posted")
        await db.save_review(rec)
        loaded = await db.get_review(rec.id)
        assert loaded.inline_count == 0

    async def test_list_diff_hashes_excludes_empty(self, db):
        await db.save_review(
            ReviewRecord(project_id="1", mr_iid=1, status="posted", diff_hash="aa")
        )
        await db.save_review(ReviewRecord(project_id="1", mr_iid=2, status="posted", diff_hash=""))
        await db.save_review(ReviewRecord(project_id="1", mr_iid=3, status="error", diff_hash="bb"))
        hashes = await db.list_diff_hashes()
        hash_values = [h[2] for h in hashes]
        assert "aa" in hash_values
        assert "bb" in hash_values
        assert "" not in hash_values
        assert len(hashes) == 2


# ---------------------------------------------------------------------------
# Reviewer flow — inline comments end-to-end (with mocks)
# ---------------------------------------------------------------------------


class TestReviewerInlineFlow:
    async def test_no_inline_when_disabled(self, db, prompt_engine, queue):
        """When inline_comments=False, get_mr_diff_refs is never called."""
        from src.config import AppConfig, GitLabConfig, ModelConfig, Provider
        from src.gitlab_client import FileDiff, MRInfo
        from src.queue_manager import ReviewJob
        from src.reviewer import Reviewer

        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m", inline_comments=False),
            gitlab=GitLabConfig(url="http://gitlab"),
        )
        mock_mr = MRInfo(
            project_id=1,
            iid=1,
            title="T",
            description="",
            author="a",
            source_branch="f",
            target_branch="main",
            is_draft=False,
            web_url="",
        )
        mock_diffs = [
            FileDiff(
                old_path="a.py",
                new_path="a.py",
                diff="+x=1",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
        ]
        gl = AsyncMock()
        gl.get_mr.return_value = mock_mr
        gl.get_diffs.return_value = mock_diffs
        gl.post_mr_note = AsyncMock()
        gl.aclose = AsyncMock()
        llm = AsyncMock()
        llm.chat.return_value = "LGTM"
        llm.aclose = AsyncMock()

        from src.reviewer import set_database

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=1, mr_iid=1))

        gl.get_mr_diff_refs.assert_not_called()

    async def test_inline_comments_posted_when_present(self, db, prompt_engine, queue):
        """When LLM returns REVIEW_INLINE blocks and refs are available,
        post_mr_discussion is called."""
        from src.config import AppConfig, GitLabConfig, ModelConfig, Provider
        from src.gitlab_client import FileDiff, MRInfo
        from src.queue_manager import ReviewJob
        from src.reviewer import Reviewer

        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m", inline_comments=True),
            gitlab=GitLabConfig(url="http://gitlab"),
        )
        inline_response = (
            '<!-- REVIEW_INLINE file="src/app.py" line="10" -->\n'
            "**[HIGH]** Missing auth.\n"
            "<!-- REVIEW_ENDINLINE -->\n\n"
            "## Summary\nNeeds auth fix."
        )
        mock_mr = MRInfo(
            project_id=1,
            iid=1,
            title="T",
            description="",
            author="a",
            source_branch="f",
            target_branch="main",
            is_draft=False,
            web_url="",
        )
        mock_diffs = [
            FileDiff(
                old_path="a.py",
                new_path="a.py",
                diff="+x=1",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
        ]
        gl = AsyncMock()
        gl.get_mr.return_value = mock_mr
        gl.get_diffs.return_value = mock_diffs
        gl.get_mr_diff_refs.return_value = {
            "base_sha": "aaa",
            "start_sha": "bbb",
            "head_sha": "ccc",
        }
        gl.post_mr_discussion = AsyncMock()
        gl.post_mr_note = AsyncMock()
        gl.aclose = AsyncMock()
        llm = AsyncMock()
        llm.chat.return_value = inline_response
        llm.aclose = AsyncMock()

        from src.reviewer import set_database

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=1, mr_iid=1))

        # One inline discussion + one summary note
        gl.get_mr_diff_refs.assert_called_once_with(1, 1)
        gl.post_mr_discussion.assert_called_once()
        gl.post_mr_note.assert_called_once()

        # Check inline_count in DB
        records, _ = await db.list_reviews()
        assert records[0].inline_count == 1

    async def test_inline_fallback_to_summary_when_no_refs(self, db, prompt_engine, queue):
        """If get_mr_diff_refs returns None, inline text is appended to summary."""
        from src.config import AppConfig, GitLabConfig, ModelConfig, Provider
        from src.gitlab_client import FileDiff, MRInfo
        from src.queue_manager import ReviewJob
        from src.reviewer import Reviewer

        cfg = AppConfig(
            providers=[Provider(id="p", name="P", type="ollama", url="http://x", active=True)],
            model=ModelConfig(provider_id="p", name="m", inline_comments=True),
            gitlab=GitLabConfig(url="http://gitlab"),
        )
        inline_response = (
            '<!-- REVIEW_INLINE file="a.py" line="5" -->\n'
            "**[MEDIUM]** Issue.\n"
            "<!-- REVIEW_ENDINLINE -->\n"
            "## Summary\nPartial review."
        )
        mock_mr = MRInfo(
            project_id=1,
            iid=1,
            title="T",
            description="",
            author="a",
            source_branch="f",
            target_branch="main",
            is_draft=False,
            web_url="",
        )
        mock_diffs = [
            FileDiff(
                old_path="a.py",
                new_path="a.py",
                diff="+x",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
        ]
        gl = AsyncMock()
        gl.get_mr.return_value = mock_mr
        gl.get_diffs.return_value = mock_diffs
        gl.get_mr_diff_refs.return_value = None  # no refs
        gl.post_mr_discussion = AsyncMock()
        gl.post_mr_note = AsyncMock()
        gl.aclose = AsyncMock()
        llm = AsyncMock()
        llm.chat.return_value = inline_response
        llm.aclose = AsyncMock()

        from src.reviewer import set_database

        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)
        with (
            patch("src.reviewer._make_gitlab_client", return_value=gl),
            patch("src.reviewer._make_llm_client", return_value=llm),
            patch("src.reviewer.get_config", return_value=cfg),
        ):
            await reviewer.review_job(ReviewJob(project_id=1, mr_iid=1))

        # No inline discussion was posted (no refs)
        gl.post_mr_discussion.assert_not_called()
        # But summary note was posted and includes the inline annotation text
        gl.post_mr_note.assert_called_once()
        call_body = gl.post_mr_note.call_args[0][2]
        assert "a.py" in call_body  # fallback annotation in summary
