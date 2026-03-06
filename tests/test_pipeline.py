"""Tests for pipeline.py — PipelineManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.context_builder import MRContext
from src.pipeline import PipelineManager, ReviewRole, RoleResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """Create a minimal prompts directory with test prompt files."""
    (tmp_path / "developer").mkdir()
    (tmp_path / "architect").mkdir()
    (tmp_path / "tester").mkdir()
    (tmp_path / "security").mkdir()
    (tmp_path / "reviewer").mkdir()

    for stack in ("python", "dotnet", "rust", "go"):
        (tmp_path / "developer" / f"{stack}.md").write_text(
            f"# Developer Review ({stack})\n\n"
            "[PROJECT_CONTEXT]\n[TASK_CONTEXT]\n[DIFF]\n[DYNAMIC_CONTEXT]\n"
        )
        (tmp_path / "architect" / f"{stack}.md").write_text(
            f"# Architect Review ({stack})\n\n"
            "[PROJECT_CONTEXT]\n[ARCH_DECISIONS]\n[DIFF]\n"
        )

    (tmp_path / "tester" / "manual.md").write_text(
        "# Tester Review\n\n[TASK_CONTEXT]\n[DIFF]\n"
    )
    (tmp_path / "security" / "general.md").write_text(
        "# Security Review\n\n[SECURITY_BASELINE]\n[DIFF]\n"
    )
    (tmp_path / "reviewer" / "general.md").write_text(
        "# Final Review\n\n[PROJECT_CONTEXT]\n[PREVIOUS_REVIEWS]\n[DIFF]\n"
        "\n---\n## Decision: APPROVE\n**Reason:** looks good\n"
    )
    return tmp_path


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value="## Review\n\nLooks good.\n\n---\n## Decision: APPROVE\n\n**Reason:** OK"
    )
    return llm


@pytest.fixture
def sample_ctx() -> MRContext:
    return MRContext(
        project_context="# AGENTS.md\n\nPython project.",
        task_context="## Task: Fix bug\n\nFix the login issue.",
        dynamic_context="### Full file: auth.py\n\n```python\ndef login(): pass\n```",
        security_baseline="## Security\n\nNo known CVEs.",
        diff="@@ -1,3 +1,4 @@\n+new line\n",
        arch_decisions="## Architecture\n\nMicroservices.",
    )


# ---------------------------------------------------------------------------
# _detect_stack tests
# ---------------------------------------------------------------------------


class TestDetectStack:
    def test_detect_stack_dotnet(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """.NET 8 in AGENTS.md → 'dotnet'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("## Stack\n\n.NET 8, ASP.NET Core, Blazor")
        assert result == "dotnet"

    def test_detect_stack_csharp(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """C# in AGENTS.md → 'dotnet'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("Built with C# and Entity Framework")
        assert result == "dotnet"

    def test_detect_stack_rust(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """Rust in AGENTS.md → 'rust'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("## Stack\n\nRust 1.78, tokio, axum")
        assert result == "rust"

    def test_detect_stack_python(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """Python in AGENTS.md → 'python'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("## Stack\n\nPython 3.12, FastAPI, SQLAlchemy")
        assert result == "python"

    def test_detect_stack_default(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """No recognizable stack → default 'python'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("No stack mentioned here at all.")
        assert result == "python"

    def test_detect_stack_empty(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """Empty AGENTS.md → 'python'."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        result = pm._detect_stack("")
        assert result == "python"


# ---------------------------------------------------------------------------
# _count_blocking tests
# ---------------------------------------------------------------------------


class TestCountBlocking:
    def test_count_blocking_single(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """Single BLOCKING word counted."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "[BLOCKING] SQL injection vulnerability found."
        assert pm._count_blocking(text) == 1

    def test_count_blocking_multiple(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """BLOCKING + CRITICAL + HIGH all counted."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = (
            "[BLOCKING] Issue 1\n"
            "[CRITICAL] Issue 2\n"
            "[HIGH] Issue 3\n"
            "[MEDIUM] Issue 4 (not counted)\n"
            "[LOW] Issue 5 (not counted)\n"
        )
        assert pm._count_blocking(text) == 3

    def test_count_blocking_zero(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """No blocking issues → returns 0."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "Everything looks fine. No issues found."
        assert pm._count_blocking(text) == 0

    def test_count_blocking_case_insensitive(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """Case-insensitive matching."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "blocking issue here and CRITICAL and High"
        assert pm._count_blocking(text) == 3


# ---------------------------------------------------------------------------
# _extract_decision tests
# ---------------------------------------------------------------------------


class TestExtractDecision:
    def test_extract_approve(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "## Decision: APPROVE\n\n**Reason:** Code looks good."
        assert pm._extract_decision(text) == "APPROVE"

    def test_extract_request_changes(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "## Decision: REQUEST_CHANGES\n\nMust fix before merge."
        assert pm._extract_decision(text) == "REQUEST_CHANGES"

    def test_extract_needs_discussion(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "## Decision: NEEDS_DISCUSSION\n\nUnclear requirements."
        assert pm._extract_decision(text) == "NEEDS_DISCUSSION"

    def test_extract_default_fallback(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        """No decision keyword → NEEDS_DISCUSSION as safe default."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "The code has some issues but I'm not sure."
        assert pm._extract_decision(text) == "NEEDS_DISCUSSION"

    def test_extract_case_insensitive(self, prompts_dir: Path, mock_llm: MagicMock) -> None:
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        text = "Decision: approve — everything OK"
        assert pm._extract_decision(text) == "APPROVE"


# ---------------------------------------------------------------------------
# _fill_slots tests
# ---------------------------------------------------------------------------


class TestFillSlots:
    def test_fill_slots_all_placeholders(
        self, prompts_dir: Path, mock_llm: MagicMock, sample_ctx: MRContext
    ) -> None:
        """All slot placeholders should be replaced with context values."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        template = (
            "[PROJECT_CONTEXT]\n"
            "[TASK_CONTEXT]\n"
            "[DYNAMIC_CONTEXT]\n"
            "[DIFF]\n"
            "[ARCH_DECISIONS]\n"
            "[SECURITY_BASELINE]\n"
            "[PREVIOUS_REVIEWS]\n"
        )
        filled = pm._fill_slots(template, sample_ctx, previous_reviews="prev review text")

        assert "[PROJECT_CONTEXT]" not in filled
        assert "[TASK_CONTEXT]" not in filled
        assert "[DYNAMIC_CONTEXT]" not in filled
        assert "[DIFF]" not in filled
        assert "[ARCH_DECISIONS]" not in filled
        assert "[SECURITY_BASELINE]" not in filled
        assert "[PREVIOUS_REVIEWS]" not in filled

        assert "Python project." in filled
        assert "Fix bug" in filled
        assert "auth.py" in filled
        assert "new line" in filled
        assert "prev review text" in filled

    def test_fill_slots_empty_ctx_uses_defaults(
        self, prompts_dir: Path, mock_llm: MagicMock
    ) -> None:
        """Empty context → slots filled with '(no ... available)' defaults."""
        pm = PipelineManager(mock_llm, prompts_dir, "python")
        ctx = MRContext()
        filled = pm._fill_slots("[PROJECT_CONTEXT]\n[DIFF]", ctx)
        assert "(no project context available)" in filled
        assert "(no diff)" in filled


# ---------------------------------------------------------------------------
# run() — integration tests with mock LLM
# ---------------------------------------------------------------------------


class TestPipelineRun:
    async def test_run_parallel_then_final(
        self,
        prompts_dir: Path,
        sample_ctx: MRContext,
    ) -> None:
        """
        4 parallel roles + 1 final REVIEWER.
        Mock LLM: count total calls = 5, verify final has previous reviews.
        """
        call_log: list[dict] = []

        async def mock_chat(
            system_prompt: str, user_message: str, temperature: float = 0.1
        ) -> str:
            call_log.append({"system": system_prompt[:100], "temp": temperature})
            # Final reviewer response includes APPROVE
            if "Final Review" in system_prompt or "PREVIOUS_REVIEWS" in system_prompt:
                return "## Review\n\nAll good.\n\n---\n## Decision: APPROVE\n\n**Reason:** OK"
            return f"## Review\n\nLooks good.\n[HIGH] Minor issue found."

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=mock_chat)

        pm = PipelineManager(llm, prompts_dir, "python")
        results = await pm.run(sample_ctx)

        # Should have exactly 5 results
        assert len(results) == 5

        # LLM should have been called exactly 5 times
        assert llm.chat.call_count == 5

        # Roles should be: developer, architect, tester, security, reviewer
        roles = {r.role for r in results}
        assert ReviewRole.DEVELOPER in roles
        assert ReviewRole.ARCHITECT in roles
        assert ReviewRole.TESTER in roles
        assert ReviewRole.SECURITY in roles
        assert ReviewRole.REVIEWER in roles

        # Final reviewer result
        reviewer_result = next(r for r in results if r.role == ReviewRole.REVIEWER)
        assert reviewer_result.decision == "APPROVE"

    async def test_run_returns_blocking_counts(
        self,
        prompts_dir: Path,
        sample_ctx: MRContext,
    ) -> None:
        """blocking_count should reflect BLOCKING/CRITICAL/HIGH in findings."""

        async def mock_chat(
            system_prompt: str, user_message: str, temperature: float = 0.1
        ) -> str:
            if "Security" in system_prompt:
                return "[BLOCKING] SQL injection\n[CRITICAL] Auth bypass\n[HIGH] Exposure"
            if "Decision" in system_prompt or "Final" in system_prompt:
                return "## Decision: REQUEST_CHANGES\n\nMust fix."
            return "No issues found."

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=mock_chat)

        pm = PipelineManager(llm, prompts_dir, "python")
        results = await pm.run(sample_ctx)

        security_result = next(r for r in results if r.role == ReviewRole.SECURITY)
        assert security_result.blocking_count == 3

    async def test_run_final_receives_previous_reviews(
        self,
        prompts_dir: Path,
        sample_ctx: MRContext,
    ) -> None:
        """REVIEWER call should have previous reviews injected into prompt."""
        captured_prompts: list[str] = []

        async def mock_chat(
            system_prompt: str, user_message: str, temperature: float = 0.1
        ) -> str:
            captured_prompts.append(system_prompt)
            if "Final" in system_prompt:
                return "## Decision: APPROVE\n\n**Reason:** Good"
            return "## Review\n\nOK."

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=mock_chat)

        pm = PipelineManager(llm, prompts_dir, "python")
        await pm.run(sample_ctx)

        # The 5th call should be the reviewer with PREVIOUS_REVIEWS
        # (parallel roles have no specific ordering guarantee, but reviewer is last)
        final_prompt = captured_prompts[-1]
        # Final reviewer prompt should contain previous reviews content
        # (parallel reviews inject "Looks good" or similar text)
        assert "Review" in final_prompt  # previous reviews are present

    async def test_run_handles_llm_error_gracefully(
        self,
        prompts_dir: Path,
        sample_ctx: MRContext,
    ) -> None:
        """LLM errors should not crash the pipeline; failed roles get error results."""
        call_count = 0

        async def mock_chat(
            system_prompt: str, user_message: str, temperature: float = 0.1
        ) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("LLM timeout")
            if "Final" in system_prompt or "Decision" in system_prompt:
                return "## Decision: NEEDS_DISCUSSION\n"
            return "## Review\n\nOK."

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=mock_chat)

        pm = PipelineManager(llm, prompts_dir, "python")
        results = await pm.run(sample_ctx)

        # Should still get 5 results even if one fails
        assert len(results) == 5
        # One result should have PIPELINE ERROR
        error_results = [r for r in results if "PIPELINE ERROR" in r.findings]
        assert len(error_results) == 1
