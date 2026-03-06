"""
Automation Rules engine — FT-6.

Loads rules.yml, evaluates conditions against MRContext, and returns
a list of RuleActions to execute before the MR is enqueued for review.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ActionType(StrEnum):
    ADD_LABEL = "add_label"
    ASSIGN_REVIEWER = "assign_reviewer"
    SKIP_REVIEW = "skip_review"
    NOTIFY_WEBHOOK = "notify_webhook"
    FORCE_FULL_REVIEW = "force_full_review"


@dataclass
class RuleCondition:
    if_files_match: list[str] = field(default_factory=list)  # glob patterns
    if_author_in: list[str] = field(default_factory=list)    # GitLab usernames
    if_lines_changed_gt: int | None = None                   # total diff lines
    if_target_branch: str | None = None                      # exact match


@dataclass
class RuleAction:
    type: ActionType
    value: str = ""  # label name, username, webhook URL — depends on type


@dataclass
class Rule:
    name: str
    condition: RuleCondition
    actions: list[RuleAction]
    stop: bool = False  # if True — stop processing subsequent rules


@dataclass
class RulesConfig:
    rules: list[Rule] = field(default_factory=list)


@dataclass
class MRContext:
    """Context available at enqueue time (before diff fetch)."""

    project_id: str | int
    mr_iid: int
    author: str = ""
    target_branch: str = ""
    changed_files: list[str] = field(default_factory=list)  # paths only, no content
    lines_changed: int = 0


# ──────────────────────────────────────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────────────────────────────────────


def load_rules(path: str | None) -> RulesConfig:
    """Load rules from a YAML file.

    Returns an empty RulesConfig if path is None or the file does not exist.
    Raises ValueError on invalid YAML or schema violations.
    """
    if path is None:
        return RulesConfig()

    p = Path(path)
    if not p.exists():
        return RulesConfig()

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"rules.yml: invalid YAML — {exc}") from exc

    if raw is None:
        return RulesConfig()

    if not isinstance(raw, dict):
        raise ValueError("rules.yml: top-level must be a mapping")

    raw_rules = raw.get("rules")
    if raw_rules is None:
        return RulesConfig()

    if not isinstance(raw_rules, list):
        raise ValueError("rules.yml: 'rules' must be a list")

    rules: list[Rule] = []
    for i, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            raise ValueError(f"rules.yml: rule #{i} must be a mapping")

        name = r.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(f"rules.yml: rule #{i} missing or invalid 'name'")

        condition = _parse_condition(r.get("condition") or {}, i)
        actions = _parse_actions(r.get("actions") or [], i)
        stop = bool(r.get("stop", False))

        rules.append(Rule(name=name, condition=condition, actions=actions, stop=stop))

    return RulesConfig(rules=rules)


def _parse_condition(raw: Any, rule_idx: int) -> RuleCondition:
    if not isinstance(raw, dict):
        raise ValueError(f"rules.yml: rule #{rule_idx} 'condition' must be a mapping")

    if_files_match = raw.get("if_files_match", [])
    if not isinstance(if_files_match, list):
        raise ValueError(f"rules.yml: rule #{rule_idx} 'if_files_match' must be a list")

    if_author_in = raw.get("if_author_in", [])
    if not isinstance(if_author_in, list):
        raise ValueError(f"rules.yml: rule #{rule_idx} 'if_author_in' must be a list")

    if_lines_changed_gt = raw.get("if_lines_changed_gt")
    if if_lines_changed_gt is not None and not isinstance(if_lines_changed_gt, int):
        raise ValueError(
            f"rules.yml: rule #{rule_idx} 'if_lines_changed_gt' must be an integer"
        )

    if_target_branch = raw.get("if_target_branch")
    if if_target_branch is not None and not isinstance(if_target_branch, str):
        raise ValueError(
            f"rules.yml: rule #{rule_idx} 'if_target_branch' must be a string"
        )

    return RuleCondition(
        if_files_match=[str(p) for p in if_files_match],
        if_author_in=[str(u) for u in if_author_in],
        if_lines_changed_gt=if_lines_changed_gt,
        if_target_branch=if_target_branch,
    )


def _parse_actions(raw: Any, rule_idx: int) -> list[RuleAction]:
    if not isinstance(raw, list):
        raise ValueError(f"rules.yml: rule #{rule_idx} 'actions' must be a list")

    actions: list[RuleAction] = []
    for j, a in enumerate(raw):
        if not isinstance(a, dict):
            raise ValueError(
                f"rules.yml: rule #{rule_idx} action #{j} must be a mapping"
            )
        raw_type = a.get("type")
        if not raw_type:
            raise ValueError(
                f"rules.yml: rule #{rule_idx} action #{j} missing 'type'"
            )
        try:
            action_type = ActionType(raw_type)
        except ValueError:
            valid = ", ".join(t.value for t in ActionType)
            raise ValueError(
                f"rules.yml: rule #{rule_idx} action #{j} unknown type '{raw_type}'"
                f" — valid: {valid}"
            ) from None

        value = str(a.get("value", ""))
        actions.append(RuleAction(type=action_type, value=value))

    return actions


# ──────────────────────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────────────────────


class RulesEngine:
    def __init__(self, config: RulesConfig) -> None:
        self._config = config

    def evaluate(self, ctx: MRContext) -> list[RuleAction]:
        """Return all actions to execute. Respects stop=True."""
        result: list[RuleAction] = []
        for rule in self._config.rules:
            if _match_condition(rule.condition, ctx):
                result.extend(rule.actions)
                logger.debug("Rule matched: %s", rule.name)
                if rule.stop:
                    break
        return result

    def should_skip(self, ctx: MRContext) -> bool:
        """Convenience: True if any evaluated action is skip_review."""
        return any(a.type == ActionType.SKIP_REVIEW for a in self.evaluate(ctx))


def _match_condition(cond: RuleCondition, ctx: MRContext) -> bool:
    """All present conditions must match (AND logic). Empty condition → always True."""
    # if_files_match: any file matches any glob pattern
    if cond.if_files_match:
        if not ctx.changed_files:
            # No file data available yet — treat as no match for this condition
            return False
        matched = any(
            fnmatch.fnmatch(f, pattern)
            for f in ctx.changed_files
            for pattern in cond.if_files_match
        )
        if not matched:
            return False

    # if_author_in: author in list
    if cond.if_author_in:
        if ctx.author not in cond.if_author_in:
            return False

    # if_lines_changed_gt: lines_changed > threshold
    if cond.if_lines_changed_gt is not None:
        if ctx.lines_changed == 0:
            # No line data available — treat as no match
            return False
        if not (ctx.lines_changed > cond.if_lines_changed_gt):
            return False

    # if_target_branch: exact match
    if cond.if_target_branch is not None:
        if ctx.target_branch != cond.if_target_branch:
            return False

    # Empty condition (no fields set) → always True; all present conditions passed
    return True
