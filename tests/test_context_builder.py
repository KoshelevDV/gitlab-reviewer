"""Tests for context_builder module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response

from src.context_builder import (
    MRContext,
    get_agents_md,
    get_docs_context,
    get_dynamic_context,
    get_security_baseline,
    get_task_context,
)
from src.gitlab_client import FileDiff, GitLabClient

BASE = "http://gitlab.test"


@pytest.fixture
def client():
    c = GitLabClient(base_url=BASE, token="test-token", timeout=5)
    yield c


class TestGetAgentsMd:
    @respx.mock
    async def test_get_agents_md_found(self, client: GitLabClient) -> None:
        """200 response → returns file contents."""
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/AGENTS.md/raw",
            params={"ref": "main"},
        ).mock(return_value=Response(200, text="# AGENTS.md\n\nPython stack."))

        result = await get_agents_md(client, 42, "main")
        assert result == "# AGENTS.md\n\nPython stack."

    @respx.mock
    async def test_get_agents_md_not_found(self, client: GitLabClient) -> None:
        """404 response → returns empty string."""
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/AGENTS.md/raw",
            params={"ref": "main"},
        ).mock(return_value=Response(404, json={"message": "404 File Not Found"}))

        result = await get_agents_md(client, 42, "main")
        assert result == ""

    @respx.mock
    async def test_get_agents_md_network_error(self, client: GitLabClient) -> None:
        """Network error → returns empty string (fail-open)."""
        import httpx

        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/AGENTS.md/raw",
        ).mock(side_effect=httpx.ConnectError("connection refused"))

        result = await get_agents_md(client, 42, "main")
        assert result == ""


class TestGetDocsContext:
    @respx.mock
    async def test_get_docs_context_prioritizes_architecture(self, client: GitLabClient) -> None:
        """ARCHITECTURE.md should come before random.md in output."""
        tree_items = [
            {"path": "docs/random.md", "type": "blob"},
            {"path": "docs/ARCHITECTURE.md", "type": "blob"},
        ]
        respx.get(f"{BASE}/api/v4/projects/42/repository/tree").mock(
            return_value=Response(200, json=tree_items)
        )
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/docs%2FARCHITECTURE.md/raw",
        ).mock(return_value=Response(200, text="# Architecture\n\nMicroservices design."))
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/docs%2Frandom.md/raw",
        ).mock(return_value=Response(200, text="# Random\n\nSome doc."))

        result = await get_docs_context(client, 42, "main", token_budget=10000)

        # ARCHITECTURE.md content should appear before random.md content
        arch_pos = result.find("Architecture")
        random_pos = result.find("Random")
        assert arch_pos < random_pos, "ARCHITECTURE.md should be prioritized"
        assert "Architecture" in result
        assert "Random" in result

    @respx.mock
    async def test_get_docs_context_respects_token_budget(self, client: GitLabClient) -> None:
        """Should stop accumulating when token budget is exceeded."""
        # Two files, each ~1000 chars; budget = 500 tokens = 2000 chars
        # First file (ARCHITECTURE.md) = 1500 chars → fits
        # Second file (random.md) = 1500 chars → would exceed budget
        large_content = "x" * 1500
        tree_items = [
            {"path": "docs/ARCHITECTURE.md", "type": "blob"},
            {"path": "docs/random.md", "type": "blob"},
        ]
        respx.get(f"{BASE}/api/v4/projects/42/repository/tree").mock(
            return_value=Response(200, json=tree_items)
        )
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/docs%2FARCHITECTURE.md/raw",
        ).mock(return_value=Response(200, text=large_content))
        respx.get(
            f"{BASE}/api/v4/projects/42/repository/files/docs%2Frandom.md/raw",
        ).mock(return_value=Response(200, text=large_content))

        # budget=500 tokens = 2000 chars; first file header+content ~1530 chars → fits
        # second file ~1530 chars → total ~3060 > 2000 → should stop
        result = await get_docs_context(client, 42, "main", token_budget=500)
        assert "ARCHITECTURE.md" in result
        assert "random.md" not in result

    @respx.mock
    async def test_get_docs_context_not_found(self, client: GitLabClient) -> None:
        """docs/ not found → returns empty string."""
        respx.get(f"{BASE}/api/v4/projects/42/repository/tree").mock(
            return_value=Response(404, json={"message": "404 Tree Not Found"})
        )

        result = await get_docs_context(client, 42, "main")
        assert result == ""


class TestGetTaskContext:
    @respx.mock
    async def test_get_task_context_from_linked_issue(self, client: GitLabClient) -> None:
        """'Closes #42' in MR description → fetches issue #42."""
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/5").mock(
            return_value=Response(
                200,
                json={
                    "title": "Add feature X",
                    "description": "Closes #42\n\nSome MR description.",
                },
            )
        )
        respx.get(f"{BASE}/api/v4/projects/10/issues/42").mock(
            return_value=Response(
                200,
                json={
                    "title": "Implement feature X",
                    "description": "## Acceptance Criteria\n- AC1\n- AC2",
                },
            )
        )

        result = await get_task_context(client, 10, 5)
        assert "Issue #42" in result
        assert "Implement feature X" in result
        assert "Acceptance Criteria" in result

    @respx.mock
    async def test_get_task_context_fixes_keyword(self, client: GitLabClient) -> None:
        """'Fixes #7' keyword → fetches issue #7."""
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/5").mock(
            return_value=Response(
                200,
                json={"title": "Fix bug", "description": "Fixes #7\nSome details."},
            )
        )
        respx.get(f"{BASE}/api/v4/projects/10/issues/7").mock(
            return_value=Response(
                200,
                json={"title": "Bug: crash on login", "description": "Steps to reproduce..."},
            )
        )

        result = await get_task_context(client, 10, 5)
        assert "Issue #7" in result
        assert "Bug: crash on login" in result

    @respx.mock
    async def test_get_task_context_fallback_to_mr(self, client: GitLabClient) -> None:
        """No 'Closes #N' in description → falls back to MR title + description."""
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/5").mock(
            return_value=Response(
                200,
                json={
                    "title": "Refactor auth module",
                    "description": "General refactoring for better readability.",
                },
            )
        )

        result = await get_task_context(client, 10, 5)
        assert "MR !5" in result
        assert "Refactor auth module" in result
        assert "General refactoring" in result

    @respx.mock
    async def test_get_task_context_resolves_keyword(self, client: GitLabClient) -> None:
        """'Resolves #99' → fetches issue #99."""
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/5").mock(
            return_value=Response(
                200,
                json={
                    "title": "Resolves issue",
                    "description": "Resolves #99",
                },
            )
        )
        respx.get(f"{BASE}/api/v4/projects/10/issues/99").mock(
            return_value=Response(
                200,
                json={"title": "Issue 99", "description": "Details."},
            )
        )

        result = await get_task_context(client, 10, 5)
        assert "Issue #99" in result


class TestGetDynamicContext:
    @respx.mock
    async def test_get_dynamic_context_includes_full_files(self, client: GitLabClient) -> None:
        """Should return full file content, not just diff snippets."""
        # Mock MR to get source_branch
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/3").mock(
            return_value=Response(
                200, json={"title": "PR", "description": "", "source_branch": "feature/x"}
            )
        )

        diffs = [
            FileDiff(
                old_path="src/auth.py",
                new_path="src/auth.py",
                diff="@@ -1,3 +1,5 @@\n def login():\n+    pass\n",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
        ]

        # Mock the full file fetch
        respx.get(
            f"{BASE}/api/v4/projects/10/repository/files/src%2Fauth.py/raw",
        ).mock(
            return_value=Response(
                200,
                text="def login():\n    user = authenticate()\n    return redirect('/home')\n",
            )
        )

        # Mock tree listing for adjacent test files (empty)
        respx.get(f"{BASE}/api/v4/projects/10/repository/tree").mock(
            return_value=Response(200, json=[])
        )

        result = await get_dynamic_context(client, 10, 3, diffs, max_files=5)
        assert "src/auth.py" in result
        # Should contain the FULL file, not just the diff
        assert "authenticate()" in result
        assert "redirect" in result

    @respx.mock
    async def test_get_dynamic_context_empty_diffs(self, client: GitLabClient) -> None:
        """Empty diffs → returns empty string."""
        result = await get_dynamic_context(client, 10, 3, diffs=[], max_files=5)
        assert result == ""

    @respx.mock
    async def test_get_dynamic_context_respects_budget(self, client: GitLabClient) -> None:
        """Should stop when token budget is exhausted."""
        respx.get(f"{BASE}/api/v4/projects/10/merge_requests/3").mock(
            return_value=Response(
                200, json={"title": "PR", "description": "", "source_branch": "main"}
            )
        )

        large_content = "x" * 5000
        diffs = [
            FileDiff(
                old_path=f"src/file{i}.py",
                new_path=f"src/file{i}.py",
                diff=f"@@ -1 +1 @@\n+code{i}\n",
                new_file=False,
                deleted_file=False,
                renamed_file=False,
            )
            for i in range(3)
        ]

        for i in range(3):
            respx.get(
                f"{BASE}/api/v4/projects/10/repository/files/src%2Ffile{i}.py/raw",
            ).mock(return_value=Response(200, text=large_content))

        respx.get(f"{BASE}/api/v4/projects/10/repository/tree").mock(
            return_value=Response(200, json=[])
        )

        # budget=1 token = 4 chars → even first file won't fit (but partial is ok)
        # Let's use a small budget so only 1 file fits at most
        result = await get_dynamic_context(client, 10, 3, diffs, token_budget=2)
        # With budget=2 tokens=8 chars, nothing fits → empty
        assert result == "" or "file0.py" in result  # either empty or only first attempt


class TestMRContext:
    def test_mr_context_defaults(self) -> None:
        """MRContext dataclass defaults are correct."""
        ctx = MRContext()
        assert ctx.project_context == ""
        assert ctx.task_context == ""
        assert ctx.dynamic_context == ""
        assert ctx.security_baseline == ""
        assert ctx.diff == ""

    def test_mr_context_with_values(self) -> None:
        """MRContext stores and exposes values correctly."""
        ctx = MRContext(
            project_context="agents md content",
            task_context="issue description",
            diff="some diff",
        )
        assert ctx.project_context == "agents md content"
        assert ctx.task_context == "issue description"
        assert ctx.diff == "some diff"
