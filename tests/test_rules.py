"""Tests for Automation Rules engine — FT-6."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.rules import (
    ActionType,
    MRContext,
    Rule,
    RuleAction,
    RuleCondition,
    RulesConfig,
    RulesEngine,
    _match_condition,
    load_rules,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _engine(rules: list[Rule]) -> RulesEngine:
    return RulesEngine(RulesConfig(rules=rules))


def _ctx(**kwargs) -> MRContext:
    defaults = dict(project_id="42", mr_iid=1)
    defaults.update(kwargs)
    return MRContext(**defaults)


def _rule(
    name: str = "test",
    actions: list[RuleAction] | None = None,
    stop: bool = False,
    **cond_kwargs,
) -> Rule:
    if actions is None:
        actions = [RuleAction(type=ActionType.ADD_LABEL, value="test-label")]
    return Rule(
        name=name,
        condition=RuleCondition(**cond_kwargs),
        actions=actions,
        stop=stop,
    )


# ──────────────────────────────────────────────────────────────────────────────
# TestRuleConditionMatching
# ──────────────────────────────────────────────────────────────────────────────


class TestRuleConditionMatching:
    def test_files_match_glob_pattern(self):
        cond = RuleCondition(if_files_match=["security/**", "*.env"])
        ctx = _ctx(changed_files=["security/auth.py"])
        assert _match_condition(cond, ctx) is True

    def test_files_match_glob_pattern_dot_env(self):
        cond = RuleCondition(if_files_match=["*.env", ".env*"])
        ctx = _ctx(changed_files=[".env.local"])
        assert _match_condition(cond, ctx) is True

    def test_files_match_no_match(self):
        cond = RuleCondition(if_files_match=["security/**"])
        ctx = _ctx(changed_files=["src/main.py"])
        assert _match_condition(cond, ctx) is False

    def test_files_match_empty_files_no_match(self):
        """If changed_files is empty and if_files_match is set → False (data unavailable)."""
        cond = RuleCondition(if_files_match=["security/**"])
        ctx = _ctx(changed_files=[])
        assert _match_condition(cond, ctx) is False

    def test_author_in_list(self):
        cond = RuleCondition(if_author_in=["dependabot", "renovate-bot"])
        ctx = _ctx(author="dependabot")
        assert _match_condition(cond, ctx) is True

    def test_author_not_in_list(self):
        cond = RuleCondition(if_author_in=["dependabot", "renovate-bot"])
        ctx = _ctx(author="alice")
        assert _match_condition(cond, ctx) is False

    def test_lines_changed_gt(self):
        cond = RuleCondition(if_lines_changed_gt=500)
        ctx = _ctx(lines_changed=600)
        assert _match_condition(cond, ctx) is True

    def test_lines_changed_not_gt(self):
        cond = RuleCondition(if_lines_changed_gt=500)
        ctx = _ctx(lines_changed=500)
        assert _match_condition(cond, ctx) is False

    def test_lines_changed_zero_no_match(self):
        """lines_changed=0 with if_lines_changed_gt set → False (data unavailable)."""
        cond = RuleCondition(if_lines_changed_gt=0)
        ctx = _ctx(lines_changed=0)
        assert _match_condition(cond, ctx) is False

    def test_target_branch_match(self):
        cond = RuleCondition(if_target_branch="main")
        ctx = _ctx(target_branch="main")
        assert _match_condition(cond, ctx) is True

    def test_target_branch_no_match(self):
        cond = RuleCondition(if_target_branch="main")
        ctx = _ctx(target_branch="develop")
        assert _match_condition(cond, ctx) is False

    def test_empty_condition_always_matches(self):
        cond = RuleCondition()  # no fields set
        ctx = _ctx()
        assert _match_condition(cond, ctx) is True

    def test_multiple_conditions_and_logic(self):
        """All conditions must match (AND logic)."""
        cond = RuleCondition(
            if_author_in=["dependabot"],
            if_target_branch="main",
        )
        # author matches but branch does not → False
        ctx_fail = _ctx(author="dependabot", target_branch="feature")
        assert _match_condition(cond, ctx_fail) is False

        # both match → True
        ctx_ok = _ctx(author="dependabot", target_branch="main")
        assert _match_condition(cond, ctx_ok) is True

    def test_stop_prevents_subsequent_rules(self):
        rule1 = _rule(
            name="stopper",
            actions=[RuleAction(type=ActionType.SKIP_REVIEW)],
            stop=True,
            if_author_in=["bot"],
        )
        rule2 = _rule(
            name="second",
            actions=[RuleAction(type=ActionType.ADD_LABEL, value="nope")],
        )
        engine = _engine([rule1, rule2])
        ctx = _ctx(author="bot")
        actions = engine.evaluate(ctx)
        # Only actions from rule1 — rule2 must not run
        assert len(actions) == 1
        assert actions[0].type == ActionType.SKIP_REVIEW


# ──────────────────────────────────────────────────────────────────────────────
# TestRulesEngine
# ──────────────────────────────────────────────────────────────────────────────


class TestRulesEngine:
    def test_skip_review_action(self):
        rule = _rule(
            actions=[RuleAction(type=ActionType.SKIP_REVIEW)],
            if_author_in=["bot"],
        )
        engine = _engine([rule])
        ctx = _ctx(author="bot")
        assert engine.should_skip(ctx) is True

    def test_no_skip_when_author_not_in_list(self):
        rule = _rule(
            actions=[RuleAction(type=ActionType.SKIP_REVIEW)],
            if_author_in=["bot"],
        )
        engine = _engine([rule])
        ctx = _ctx(author="alice")
        assert engine.should_skip(ctx) is False

    def test_multiple_actions_from_single_rule(self):
        rule = Rule(
            name="multi",
            condition=RuleCondition(if_target_branch="main"),
            actions=[
                RuleAction(type=ActionType.ADD_LABEL, value="needs-review"),
                RuleAction(type=ActionType.FORCE_FULL_REVIEW),
            ],
            stop=False,
        )
        engine = _engine([rule])
        ctx = _ctx(target_branch="main")
        actions = engine.evaluate(ctx)
        assert len(actions) == 2
        assert actions[0].type == ActionType.ADD_LABEL
        assert actions[1].type == ActionType.FORCE_FULL_REVIEW

    def test_no_rules_empty_actions(self):
        engine = _engine([])
        ctx = _ctx()
        assert engine.evaluate(ctx) == []
        assert engine.should_skip(ctx) is False

    def test_multiple_rules_combined(self):
        rule1 = _rule(name="r1", actions=[RuleAction(type=ActionType.ADD_LABEL, value="lbl1")])
        rule2 = _rule(name="r2", actions=[RuleAction(type=ActionType.ADD_LABEL, value="lbl2")])
        engine = _engine([rule1, rule2])
        ctx = _ctx()
        actions = engine.evaluate(ctx)
        assert len(actions) == 2


# ──────────────────────────────────────────────────────────────────────────────
# TestLoadRules
# ──────────────────────────────────────────────────────────────────────────────


class TestLoadRules:
    def test_load_valid_yaml(self, tmp_path: Path):
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text(
            textwrap.dedent("""
                rules:
                  - name: Skip bots
                    condition:
                      if_author_in:
                        - dependabot
                        - renovate-bot
                    actions:
                      - type: skip_review
                    stop: true
                  - name: Security review
                    condition:
                      if_files_match:
                        - "security/**"
                    actions:
                      - type: add_label
                        value: security
                      - type: force_full_review
            """),
            encoding="utf-8",
        )
        config = load_rules(str(rules_yml))
        assert len(config.rules) == 2
        assert config.rules[0].name == "Skip bots"
        assert config.rules[0].stop is True
        assert config.rules[0].actions[0].type == ActionType.SKIP_REVIEW
        assert config.rules[1].condition.if_files_match == ["security/**"]

    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        config = load_rules(str(tmp_path / "nonexistent.yml"))
        assert config.rules == []

    def test_load_none_path_returns_empty(self):
        config = load_rules(None)
        assert config.rules == []

    def test_load_empty_yaml_returns_empty(self, tmp_path: Path):
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text("", encoding="utf-8")
        config = load_rules(str(rules_yml))
        assert config.rules == []

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text("rules: [not: valid: yaml: here", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid YAML"):
            load_rules(str(rules_yml))

    def test_load_invalid_schema_raises(self, tmp_path: Path):
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text(
            textwrap.dedent("""
                rules:
                  - condition:
                      if_author_in: [bot]
                    actions:
                      - type: skip_review
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="name"):
            load_rules(str(rules_yml))

    def test_load_invalid_action_type_raises(self, tmp_path: Path):
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text(
            textwrap.dedent("""
                rules:
                  - name: bad
                    condition: {}
                    actions:
                      - type: nonexistent_action
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown type"):
            load_rules(str(rules_yml))


# ──────────────────────────────────────────────────────────────────────────────
# TestWebhookWithRules
# ──────────────────────────────────────────────────────────────────────────────


class TestWebhookWithRules:
    """Integration tests: webhook handler + rule engine."""

    def _make_payload(
        self, action: str = "open", author: str = "alice", target_branch: str = "main"
    ):
        return {
            "object_attributes": {"action": action, "iid": 7, "target_branch": target_branch},
            "project": {"id": 42},
            "user": {"username": author},
        }

    def _make_mock_config(self):
        """Return a mock config with empty webhook_secret (no auth required)."""
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.gitlab.webhook_secret = ""
        return cfg

    @pytest.mark.asyncio
    async def test_skip_rule_returns_skipped_status(self, tmp_path: Path):
        """When a skip_review rule matches, webhook returns skipped_by_rule."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        # Write a rules.yml that skips user 'bot'
        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text(
            textwrap.dedent("""
                rules:
                  - name: Skip bot
                    condition:
                      if_author_in:
                        - bot
                    actions:
                      - type: skip_review
                    stop: true
            """),
            encoding="utf-8",
        )

        with (
            patch("src.webhook._rules_path", str(rules_yml)),
            patch("src.webhook._queue") as mock_queue,
            patch("src.webhook.get_config", return_value=self._make_mock_config()),
        ):
            mock_queue.enqueue = AsyncMock(return_value=True)

            from src.webhook import make_webhook_router

            app = FastAPI()
            app.include_router(make_webhook_router())

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/webhook/gitlab",
                    json=self._make_payload(author="bot"),
                    headers={"X-Gitlab-Event": "Merge Request Hook"},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "skipped_by_rule"
            mock_queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_skip_rule_enqueues_normally(self, tmp_path: Path):
        """When no skip_review rule matches, webhook enqueues normally."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        rules_yml = tmp_path / "rules.yml"
        rules_yml.write_text(
            textwrap.dedent("""
                rules:
                  - name: Skip bot
                    condition:
                      if_author_in:
                        - bot
                    actions:
                      - type: skip_review
                    stop: true
            """),
            encoding="utf-8",
        )

        with (
            patch("src.webhook._rules_path", str(rules_yml)),
            patch("src.webhook._queue") as mock_queue,
            patch("src.webhook.get_config", return_value=self._make_mock_config()),
        ):
            mock_queue.enqueue = AsyncMock(return_value=True)

            from src.webhook import make_webhook_router

            app = FastAPI()
            app.include_router(make_webhook_router())

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/webhook/gitlab",
                    json=self._make_payload(author="alice"),
                    headers={"X-Gitlab-Event": "Merge Request Hook"},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "accepted"
            mock_queue.enqueue.assert_awaited_once()
