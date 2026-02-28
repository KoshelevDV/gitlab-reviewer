"""Tests for GitLabClient — mock httpx with respx."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.gitlab_client import GitLabClient

BASE = "http://gitlab.test"


@pytest.fixture
def client():
    c = GitLabClient(base_url=BASE, token="test-token", timeout=5)  # noqa: S106
    yield c


class TestTestConnection:
    @respx.mock
    async def test_successful_connection(self, client):
        respx.get(f"{BASE}/api/v4/version").mock(
            return_value=Response(200, json={"version": "16.5.0"})
        )
        respx.get(f"{BASE}/api/v4/user").mock(
            return_value=Response(200, json={"username": "alice"})
        )
        info = await client.test_connection()
        assert info.ok is True
        assert info.version == "16.5.0"
        assert info.username == "alice"
        await client.aclose()

    @respx.mock
    async def test_failed_connection(self, client):
        respx.get(f"{BASE}/api/v4/version").mock(
            return_value=Response(401, json={"message": "Unauthorized"})
        )
        info = await client.test_connection()
        assert info.ok is False
        assert info.error != ""
        await client.aclose()


class TestGetMR:
    @respx.mock
    async def test_get_mr_returns_info(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/merge_requests/7").mock(
            return_value=Response(
                200,
                json={
                    "title": "My feature",
                    "description": "Does stuff",
                    "author": {"username": "bob"},
                    "source_branch": "feature",
                    "target_branch": "main",
                    "draft": False,
                    "web_url": "http://gitlab.test/proj/-/merge_requests/7",
                },
            )
        )
        mr = await client.get_mr(42, 7)
        assert mr.title == "My feature"
        assert mr.author == "bob"
        assert mr.is_draft is False
        await client.aclose()

    @respx.mock
    async def test_draft_mr_detected_by_flag(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/merge_requests/7").mock(
            return_value=Response(
                200,
                json={
                    "title": "My feature",
                    "description": "",
                    "author": {"username": "bob"},
                    "source_branch": "f",
                    "target_branch": "main",
                    "draft": True,
                    "web_url": "http://gitlab.test/mr",
                },
            )
        )
        mr = await client.get_mr(42, 7)
        assert mr.is_draft is True
        await client.aclose()

    @respx.mock
    async def test_draft_mr_detected_by_title_prefix(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/merge_requests/7").mock(
            return_value=Response(
                200,
                json={
                    "title": "Draft: my feature",
                    "description": "",
                    "author": {"username": "bob"},
                    "source_branch": "f",
                    "target_branch": "main",
                    "draft": False,
                    "web_url": "http://gitlab.test/mr",
                },
            )
        )
        mr = await client.get_mr(42, 7)
        assert mr.is_draft is True
        await client.aclose()


class TestGetDiffs:
    @respx.mock
    async def test_get_diffs_parses_files(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/merge_requests/7/diffs").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "old_path": "a.py",
                        "new_path": "a.py",
                        "diff": "@@ -1,3 +1,4 @@\n+new line",
                        "new_file": False,
                        "deleted_file": False,
                        "renamed_file": False,
                    }
                ],
            )
        )
        diffs = await client.get_diffs(42, 7)
        assert len(diffs) == 1
        assert diffs[0].new_path == "a.py"
        assert "+new line" in diffs[0].diff
        await client.aclose()

    @respx.mock
    async def test_get_diffs_empty(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/merge_requests/7/diffs").mock(
            return_value=Response(200, json=[])
        )
        diffs = await client.get_diffs(42, 7)
        assert diffs == []
        await client.aclose()


class TestListGroups:
    @respx.mock
    async def test_list_groups_returns_list(self, client):
        respx.get(f"{BASE}/api/v4/groups").mock(
            return_value=Response(
                200,
                json=[
                    {"id": 1, "name": "alpha", "full_path": "alpha"},
                    {"id": 2, "name": "beta", "full_path": "org/beta"},
                ],
            )
        )
        groups = await client.list_groups()
        assert len(groups) == 2
        assert groups[0].full_path == "alpha"
        await client.aclose()


class TestListBranches:
    @respx.mock
    async def test_list_branches_protected_flag(self, client):
        respx.get(f"{BASE}/api/v4/projects/42/repository/branches").mock(
            return_value=Response(
                200,
                json=[
                    {"name": "main", "protected": True, "default": True},
                    {"name": "feature", "protected": False, "default": False},
                ],
            )
        )
        branches = await client.list_branches(42)
        main = next(b for b in branches if b.name == "main")
        assert main.protected is True
        assert main.default is True
        await client.aclose()


class TestApproveMR:
    @respx.mock
    async def test_approve_returns_true_on_success(self, client):
        respx.post(f"{BASE}/api/v4/projects/42/merge_requests/7/approve").mock(
            return_value=Response(201, json={"id": 1})
        )
        result = await client.approve_mr(42, 7)
        assert result is True
        await client.aclose()

    @respx.mock
    async def test_approve_returns_false_on_error(self, client):
        respx.post(f"{BASE}/api/v4/projects/42/merge_requests/7/approve").mock(
            return_value=Response(403, json={"message": "Forbidden"})
        )
        result = await client.approve_mr(42, 7)
        assert result is False
        await client.aclose()


class TestPostMRNote:
    @respx.mock
    async def test_post_note_calls_correct_endpoint(self, client):
        route = respx.post(f"{BASE}/api/v4/projects/42/merge_requests/7/notes").mock(
            return_value=Response(201, json={"id": 999, "body": "Review"})
        )
        await client.post_mr_note(42, 7, "Review text")
        assert route.called
        await client.aclose()
