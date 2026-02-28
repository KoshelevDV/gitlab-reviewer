"""Tests for file exclusion helpers and integration with reviewer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.config import AppConfig, GitLabConfig, ModelConfig, Provider
from src.gitlab_client import FileDiff, MRInfo
from src.queue_manager import ReviewJob
from src.reviewer import (
    Reviewer,
    _filter_diffs,
    _is_file_excluded,
    set_database,
)

# ---------------------------------------------------------------------------
# _is_file_excluded
# ---------------------------------------------------------------------------


class TestIsFileExcluded:
    def test_plain_glob_matches_extension(self):
        assert _is_file_excluded("package-lock.json", ["*.lock", "package-lock.json"])

    def test_lock_glob_matches(self):
        assert _is_file_excluded("Cargo.lock", ["*.lock"])

    def test_lock_glob_does_not_match_other(self):
        assert not _is_file_excluded("main.rs", ["*.lock"])

    def test_min_js_glob(self):
        assert _is_file_excluded("bundle.min.js", ["*.min.js"])

    def test_directory_glob_matches_top_level_file(self):
        assert _is_file_excluded("vendor/foo.go", ["vendor/**"])

    def test_directory_glob_matches_nested(self):
        assert _is_file_excluded("vendor/pkg/dep/mod.go", ["vendor/**"])

    def test_directory_glob_does_not_match_sibling(self):
        assert not _is_file_excluded("src/vendor_helper.go", ["vendor/**"])

    def test_directory_glob_matches_exact_prefix(self):
        assert _is_file_excluded("vendor", ["vendor/**"])

    def test_no_patterns_never_excludes(self):
        assert not _is_file_excluded("anything.py", [])

    def test_multiple_patterns_any_match(self):
        assert _is_file_excluded("dist/bundle.js", ["*.lock", "dist/**"])

    def test_generated_pattern(self):
        assert _is_file_excluded("api.generated.go", ["*.generated.*"])

    def test_node_modules_nested(self):
        assert _is_file_excluded("node_modules/react/index.js", ["node_modules/**"])

    def test_non_matching_path(self):
        assert not _is_file_excluded("src/main.py", ["vendor/**", "*.lock"])

    def test_deep_nested_glob(self):
        assert _is_file_excluded("build/assets/img/logo.png", ["build/**"])


# ---------------------------------------------------------------------------
# _filter_diffs
# ---------------------------------------------------------------------------


def make_diff(path: str) -> FileDiff:
    return FileDiff(
        old_path=path,
        new_path=path,
        diff="@@\n+line\n",
        new_file=False,
        deleted_file=False,
        renamed_file=False,
    )


class TestFilterDiffs:
    def test_empty_patterns_returns_all(self):
        diffs = [make_diff("src/main.py"), make_diff("vendor/dep.go")]
        kept, skipped = _filter_diffs(diffs, [], [])
        assert len(kept) == 2
        assert skipped == []

    def test_global_exclude_removes_matching(self):
        diffs = [make_diff("Cargo.lock"), make_diff("src/lib.rs")]
        kept, skipped = _filter_diffs(diffs, ["*.lock"], [])
        assert len(kept) == 1
        assert kept[0].new_path == "src/lib.rs"
        assert "Cargo.lock" in skipped

    def test_target_exclude_combined_with_global(self):
        diffs = [make_diff("vendor/x.go"), make_diff("generated/api.go"), make_diff("src/a.py")]
        kept, skipped = _filter_diffs(diffs, ["vendor/**"], ["generated/**"])
        assert len(kept) == 1
        assert kept[0].new_path == "src/a.py"
        assert len(skipped) == 2

    def test_all_excluded_returns_empty(self):
        diffs = [make_diff("a.lock"), make_diff("b.lock")]
        kept, skipped = _filter_diffs(diffs, ["*.lock"], [])
        assert kept == []
        assert len(skipped) == 2

    def test_uses_new_path(self):
        d = FileDiff(
            old_path="old.lock",
            new_path="new.py",
            diff="@@\n",
            new_file=False,
            deleted_file=False,
            renamed_file=True,
        )
        kept, skipped = _filter_diffs([d], ["*.lock"], [])
        # new_path is "new.py" — should NOT be excluded
        assert len(kept) == 1

    def test_falls_back_to_old_path_when_new_empty(self):
        d = FileDiff(
            old_path="removed.lock",
            new_path="",
            diff="@@\n",
            new_file=False,
            deleted_file=True,
            renamed_file=False,
        )
        kept, skipped = _filter_diffs([d], ["*.lock"], [])
        assert kept == []
        assert "removed.lock" in skipped


# ---------------------------------------------------------------------------
# Integration — reviewer skips MR when all files excluded
# ---------------------------------------------------------------------------


def _make_cfg_with_exclude(exclude: list[str]) -> AppConfig:
    return AppConfig(
        providers=[Provider(id="p", name="P", type="ollama", url="http://fake", active=True)],
        model=ModelConfig(provider_id="p", name="m"),
        gitlab=GitLabConfig(url="http://fake-gl", webhook_secret="s"),  # noqa: S106
        file_exclude=exclude,
    )


class TestReviewerFileFilterIntegration:
    async def test_all_files_excluded_skips_review(self, db, prompt_engine, queue):
        cfg = _make_cfg_with_exclude(["*.lock"])
        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)

        mr = MRInfo(
            project_id=42,
            iid=1,
            title="bump deps",
            description="",
            is_draft=False,
            author="alice",
            source_branch="bump",
            target_branch="main",
            web_url="http://gl/mr/1",
        )
        diffs = [
            FileDiff(
                old_path="Cargo.lock",
                new_path="Cargo.lock",
                diff="@@\n+x\n",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
        ]

        import src.config as cfg_mod

        cfg_mod._config = cfg

        with (
            patch("src.reviewer.get_config", return_value=cfg),
            patch("src.reviewer._make_gitlab_client") as mock_gl,
            patch("src.reviewer._make_llm_client") as mock_llm,
        ):
            gl_mock = AsyncMock()
            gl_mock.get_mr = AsyncMock(return_value=mr)
            gl_mock.get_diffs = AsyncMock(return_value=diffs)
            mock_gl.return_value = gl_mock
            mock_llm.return_value = AsyncMock()

            job = ReviewJob(project_id="42", mr_iid=1)
            await reviewer.review_job(job)

        records, _ = await db.list_reviews()
        assert records
        rec = records[0]
        assert rec.status == "skipped"
        assert "exclusion filter" in (rec.skip_reason or "")

    async def test_partial_exclusion_keeps_remaining_files(self, db, prompt_engine, queue):
        """If some files pass the filter, the review proceeds normally."""
        cfg = _make_cfg_with_exclude(["*.lock"])
        set_database(db)
        reviewer = Reviewer(prompts=prompt_engine, queue=queue)

        mr = MRInfo(
            project_id=42,
            iid=2,
            title="add feature",
            description="",
            is_draft=False,
            author="bob",
            source_branch="feat",
            target_branch="main",
            web_url="http://gl/mr/2",
        )
        diffs = [
            FileDiff(
                old_path="Cargo.lock",
                new_path="Cargo.lock",
                diff="@@\n+x\n",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            ),
            FileDiff(
                old_path="src/lib.rs",
                new_path="src/lib.rs",
                diff="@@\n+fn foo(){}\n",
                new_file=True,
                deleted_file=False,
                renamed_file=False,
            ),
        ]

        import src.config as cfg_mod

        cfg_mod._config = cfg

        with (
            patch("src.reviewer.get_config", return_value=cfg),
            patch("src.reviewer._make_gitlab_client") as mock_gl,
            patch("src.reviewer._make_llm_client") as mock_llm,
        ):
            gl_mock = AsyncMock()
            gl_mock.get_mr = AsyncMock(return_value=mr)
            gl_mock.get_diffs = AsyncMock(return_value=diffs)
            gl_mock.get_mr_diff_refs = AsyncMock(return_value=None)
            gl_mock.post_mr_note = AsyncMock()
            gl_mock.aclose = AsyncMock()
            mock_gl.return_value = gl_mock

            llm_mock = AsyncMock()
            llm_mock.chat = AsyncMock(return_value="LGTM, looks good")
            llm_mock.aclose = AsyncMock()
            mock_llm.return_value = llm_mock

            job = ReviewJob(project_id="42", mr_iid=2)
            await reviewer.review_job(job)

        records, _ = await db.list_reviews()
        # Should not be skipped — review should have proceeded
        rec = next((r for r in records if r.mr_iid == 2), None)
        assert rec is not None
        assert rec.status != "skipped"
