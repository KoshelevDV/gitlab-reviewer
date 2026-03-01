"""Tests for slash command parsing and execution."""

from __future__ import annotations

import pytest

from src.slash_commands import SlashCommand, parse_slash_command


class TestParseSlashCommand:
    def test_ask_with_question(self):
        result = parse_slash_command("/ask what does this function do?")
        assert result is not None
        assert result.name == "ask"
        assert result.args == "what does this function do?"

    def test_summary_no_args(self):
        result = parse_slash_command("/summary")
        assert result is not None
        assert result.name == "summary"
        assert result.args == ""

    def test_improve_with_path(self):
        result = parse_slash_command("/improve src/main.py")
        assert result is not None
        assert result.name == "improve"
        assert result.args == "src/main.py"

    def test_help_command(self):
        result = parse_slash_command("/help")
        assert result is not None
        assert result.name == "help"

    def test_case_insensitive(self):
        result = parse_slash_command("/ASK What is happening?")
        assert result is not None
        assert result.name == "ask"

    def test_leading_whitespace_ignored(self):
        result = parse_slash_command("  /summary  ")
        assert result is not None
        assert result.name == "summary"

    def test_not_a_command(self):
        assert parse_slash_command("Great job, LGTM!") is None

    def test_unknown_command(self):
        assert parse_slash_command("/deploy production") is None

    def test_empty_string(self):
        assert parse_slash_command("") is None

    def test_multiline_ask(self):
        body = "/ask What is the purpose of this change?\nContext: I'm new to this codebase."
        result = parse_slash_command(body)
        assert result is not None
        assert result.name == "ask"
        assert "What is the purpose" in result.args


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_returns_without_llm(self):
        from src.slash_commands import execute_slash_command

        cmd = SlashCommand(name="help", args="")
        result = await execute_slash_command(
            cmd=cmd,
            project_id=1,
            mr_iid=1,
            gitlab_url="http://fake",
            gitlab_token="tok",  # noqa: S106
            llm_base_url="http://fake-llm",
            llm_api_key="key",
            llm_model="gpt-4",
        )
        assert "Slash Commands" in result or "ask" in result.lower()


class TestNoteWebhookHandler:
    """Test the Note Hook path in webhook.py using the shared conftest app fixture."""

    async def test_note_hook_slash_command_accepted(self, app):
        from unittest.mock import AsyncMock, patch

        payload = {
            "object_kind": "note",
            "object_attributes": {
                "note": "/summary",
                "noteable_type": "MergeRequest",
            },
            "merge_request": {"iid": 7},
            "project": {"id": 42},
        }

        with patch("src.webhook._run_slash_command", new_callable=AsyncMock):
            resp = await app.post(
                "/webhook/gitlab",
                json=payload,
                headers={"X-Gitlab-Event": "Note Hook", "X-Gitlab-Token": "test-secret"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["command"] == "summary"

    async def test_note_hook_non_mr_ignored(self, app):
        payload = {
            "object_attributes": {
                "note": "/summary",
                "noteable_type": "Issue",
            },
            "merge_request": {"iid": 7},
            "project": {"id": 42},
        }
        resp = await app.post(
            "/webhook/gitlab",
            json=payload,
            headers={"X-Gitlab-Event": "Note Hook", "X-Gitlab-Token": "test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    async def test_note_hook_non_slash_ignored(self, app):
        payload = {
            "object_attributes": {
                "note": "Looks good to me!",
                "noteable_type": "MergeRequest",
            },
            "merge_request": {"iid": 7},
            "project": {"id": 42},
        }
        resp = await app.post(
            "/webhook/gitlab",
            json=payload,
            headers={"X-Gitlab-Event": "Note Hook", "X-Gitlab-Token": "test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
