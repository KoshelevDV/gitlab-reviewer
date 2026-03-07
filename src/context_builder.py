"""
Context Builder — collects full MR review context from GitLab API.

Assembles:
  - AGENTS.md (project AI context)
  - docs/ contents (architecture decisions, ADRs, etc.)
  - Security baseline (threat models, CVE docs)
  - Task context (linked issue or MR description)
  - Dynamic context (full content of changed files + neighbouring tests)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote

from .gitlab_client import FileDiff, GitLabClient

logger = logging.getLogger(__name__)

# Priority prefixes for docs/ files (first match wins, lower index = higher priority)
_PRIORITY_PREFIXES = ("ARCHITECTURE", "ADR", "DECISION", "DESIGN", "RFC")

# Keywords that mark security-relevant docs
_SECURITY_KEYWORDS = ("threat", "cve", "security", "vulnerability", "risk")

# Regex for linked issue references
_CLOSES_RE = re.compile(
    r"(?:closes|fixes|resolves)\s+#(\d+)",
    re.IGNORECASE,
)

# Test file patterns
_TEST_PATTERNS = re.compile(
    r"(^test_|_test\.|Test[A-Z]|Spec[A-Z]|\.spec\.|\.test\.)",
    re.IGNORECASE,
)


@dataclass
class MRContext:
    """Aggregated context for a single MR review pipeline run."""

    project_context: str = ""  # AGENTS.md + docs/
    task_context: str = ""  # issue or MR description
    dynamic_context: str = ""  # full content of changed files + tests
    security_baseline: str = ""  # security-relevant docs
    diff: str = ""  # raw combined diff (filled by caller from FileDiff list)
    arch_decisions: str = field(default="", init=True)  # alias: docs/ ADR content

    def __post_init__(self) -> None:
        # arch_decisions is a sub-slice of project_context for convenience
        if not self.arch_decisions and self.project_context:
            self.arch_decisions = self.project_context


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_file_raw(
    client: GitLabClient,
    project_id: int | str,
    file_path: str,
    ref: str,
) -> str | None:
    """
    Fetch raw file content via public GitLabClient API.
    Kept as a thin wrapper for backwards compatibility.
    """
    try:
        return await client.get_file_raw(project_id, file_path, ref)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to fetch file %s@%s: %s", file_path, ref, exc)
        return None


async def _list_tree(
    client: GitLabClient,
    project_id: int | str,
    path: str,
    ref: str,
) -> list[dict]:
    """
    List files in a repository directory via public GitLabClient API.
    Kept as a thin wrapper for backwards compatibility.
    """
    try:
        items = await client.list_tree(project_id, path, ref)
        return [item for item in items if item.get("type") == "blob"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to list tree %s@%s: %s", path, ref, exc)
        return []


def _priority_key(filename: str) -> int:
    """Lower return value = higher priority in sorted output."""
    upper = filename.upper()
    for i, prefix in enumerate(_PRIORITY_PREFIXES):
        if upper.startswith(prefix):
            return i
    return len(_PRIORITY_PREFIXES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_agents_md(
    client: GitLabClient,
    project_id: int | str,
    ref: str,
) -> str:
    """
    Read AGENTS.md from the root of the repository.
    Returns empty string if the file doesn't exist.
    """
    content = await _fetch_file_raw(client, project_id, "AGENTS.md", ref)
    if content is None:
        logger.debug("AGENTS.md not found in project=%s ref=%s", project_id, ref)
        return ""
    logger.debug("Loaded AGENTS.md (%d chars) from project=%s", len(content), project_id)
    return content


async def get_docs_context(
    client: GitLabClient,
    project_id: int | str,
    ref: str,
    token_budget: int = 3000,
) -> str:
    """
    Read docs/ directory from the repository.

    Files are prioritised by name prefix:
      ARCHITECTURE*, ADR*, DECISION*, DESIGN*, RFC* come first.

    Stops accumulating when token_budget (chars = budget * 4) is reached.
    Returns empty string if docs/ directory doesn't exist.
    """
    char_budget = token_budget * 4
    items = await _list_tree(client, project_id, "docs", ref)
    if not items:
        logger.debug("docs/ not found in project=%s ref=%s", project_id, ref)
        return ""

    # Sort by priority (high-priority docs first, then alphabetically within tier)
    items.sort(key=lambda item: (_priority_key(item["path"].rsplit("/", 1)[-1]), item["path"]))

    parts: list[str] = []
    accumulated = 0

    for item in items:
        file_path = item["path"]
        content = await _fetch_file_raw(client, project_id, file_path, ref)
        if content is None:
            continue

        chunk = f"### {file_path}\n\n{content}\n"
        if accumulated + len(chunk) > char_budget:
            logger.debug(
                "docs/ context: stopping at %s (budget %d reached)", file_path, token_budget
            )
            break
        parts.append(chunk)
        accumulated += len(chunk)

    if not parts:
        return ""

    result = "## Project Documentation\n\n" + "\n".join(parts)
    logger.debug(
        "docs/ context: %d files, %d chars for project=%s", len(parts), accumulated, project_id
    )
    return result


async def get_security_baseline(
    client: GitLabClient,
    project_id: int | str,
    ref: str,
) -> str:
    """
    Select only security-relevant files from docs/.

    A file is considered security-relevant if its name or content contains
    any of: threat, CVE, security, vulnerability, risk (case-insensitive).

    Returns concatenated content or empty string.
    """
    items = await _list_tree(client, project_id, "docs", ref)
    if not items:
        return ""

    parts: list[str] = []
    for item in items:
        file_path = item["path"]
        filename_lower = file_path.lower()

        # Fast-path: filename contains a security keyword
        name_match = any(kw in filename_lower for kw in _SECURITY_KEYWORDS)

        content = await _fetch_file_raw(client, project_id, file_path, ref)
        if content is None:
            continue

        # Slow-path: content contains a security keyword
        content_match = any(kw in content.lower() for kw in _SECURITY_KEYWORDS)

        if name_match or content_match:
            parts.append(f"### {file_path}\n\n{content}\n")

    if not parts:
        return ""

    return "## Security Baseline\n\n" + "\n".join(parts)


async def get_task_context(
    client: GitLabClient,
    project_id: int | str,
    mr_iid: int,
    sanitize: Callable[[str, int], str] | None = None,
) -> str:
    """
    Build task context for the MR.

    1. Looks for a linked issue in the MR description (Closes #N / Fixes #N / Resolves #N).
    2. If found — returns issue title + description.
    3. If not found — returns MR title + description as fallback.

    Args:
        sanitize: Optional callable(text, max_chars) → str that strips prompt injection
                  markers from untrusted content. When None, content is truncated only.
    """
    pid = quote(str(project_id), safe="")

    def _s(text: str, max_chars: int = 4000) -> str:
        return sanitize(text, max_chars) if sanitize else text[:max_chars]

    # Fetch MR info
    try:
        mr_resp = await client._client.get(
            f"{client._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}"
        )
        mr_resp.raise_for_status()
        mr_data = mr_resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch MR info project=%s MR!%d: %s", project_id, mr_iid, exc)
        return ""

    mr_title = _s(mr_data.get("title", ""), 500)
    mr_description = _s(mr_data.get("description") or "", 2000)

    # Try to find linked issue reference (search raw description before sanitize)
    raw_description = mr_data.get("description") or ""
    match = _CLOSES_RE.search(raw_description)
    if match:
        issue_iid = int(match.group(1))
        try:
            issue_resp = await client._client.get(
                f"{client._base}/api/v4/projects/{pid}/issues/{issue_iid}"
            )
            issue_resp.raise_for_status()
            issue_data = issue_resp.json()
            issue_title = _s(issue_data.get("title", ""), 500)
            issue_description = _s(issue_data.get("description") or "", 2000)
            logger.debug(
                "Linked issue #%d found for project=%s MR!%d", issue_iid, project_id, mr_iid
            )
            return f"## Task: Issue #{issue_iid} — {issue_title}\n\n{issue_description}"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fetch linked issue #%d for project=%s MR!%d: %s",
                issue_iid,
                project_id,
                mr_iid,
                exc,
            )
            # Fall through to MR description fallback

    # Fallback: use MR title + description
    logger.debug(
        "No linked issue found for project=%s MR!%d — using MR description",
        project_id,
        mr_iid,
    )
    return f"## Task: MR !{mr_iid} — {mr_title}\n\n{mr_description}"


async def get_dynamic_context(
    client: GitLabClient,
    project_id: int | str,
    mr_iid: int,
    diffs: list[FileDiff],
    max_files: int = 5,
    token_budget: int = 4000,
) -> str:
    """
    Fetch full content of changed files and their associated test files.

    For each file in diffs (up to max_files):
      1. Reads the full file via GitLab API.
      2. Looks for test files in the same directory (test_*, *_test.*, *Test*, *Spec*).
      3. Appends content with headers, stopping when token_budget (chars = budget * 4) is reached.

    Returns empty string if nothing could be fetched.
    """
    if not diffs:
        return ""

    char_budget = token_budget * 4

    # We need a ref — use source_branch from the MR
    pid = quote(str(project_id), safe="")
    try:
        mr_resp = await client._client.get(
            f"{client._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}"
        )
        mr_resp.raise_for_status()
        mr_data = mr_resp.json()
        ref = mr_data.get("source_branch", "main")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get MR source_branch for dynamic context: %s", exc)
        ref = "main"

    parts: list[str] = []
    accumulated = 0
    files_processed = 0

    for diff_item in diffs[:max_files]:
        file_path = diff_item.new_path or diff_item.old_path
        if not file_path or diff_item.deleted_file:
            continue

        # Fetch full file content
        content = await _fetch_file_raw(client, project_id, file_path, ref)
        if content is not None:
            chunk = f"### Full file: `{file_path}`\n\n```\n{content}\n```\n"
            if accumulated + len(chunk) <= char_budget:
                parts.append(chunk)
                accumulated += len(chunk)
                files_processed += 1
            else:
                logger.debug("dynamic_context: budget reached at file %s", file_path)
                break

        # Look for adjacent test files
        directory = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        base_name = file_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        tree = await _list_tree(client, project_id, directory or ".", ref)
        for tree_item in tree:
            candidate_name = tree_item["path"].rsplit("/", 1)[-1]
            if not _TEST_PATTERNS.search(candidate_name):
                continue
            # Match test files related to the changed file
            name_lower = candidate_name.lower()
            if base_name.lower() not in name_lower and not name_lower.startswith("test_"):
                continue
            test_path = tree_item["path"]
            if test_path == file_path:
                continue

            test_content = await _fetch_file_raw(client, project_id, test_path, ref)
            if test_content is None:
                continue

            test_chunk = f"### Test file: `{test_path}`\n\n```\n{test_content}\n```\n"
            if accumulated + len(test_chunk) <= char_budget:
                parts.append(test_chunk)
                accumulated += len(test_chunk)
            else:
                break

    if not parts:
        return ""

    logger.debug(
        "dynamic_context: %d sections, %d chars for project=%s MR!%d",
        len(parts),
        accumulated,
        project_id,
        mr_iid,
    )
    return "## Dynamic Context (Full Files)\n\n" + "\n".join(parts)
