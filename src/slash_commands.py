"""
Slash command handler for MR note (comment) webhooks.

Supported commands (posted as MR comments):
  /ask <question>      — ask a question about the MR diff
  /improve [<path>]    — suggest improvements (optionally scoped to a file)
  /summary             — generate a concise MR walkthrough summary
  /help                — list available commands

Commands are only processed when the note body starts with one of the above.
Replies are posted as a new MR note so they appear in the thread.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import ClassVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

_COMMAND_RE = re.compile(
    r"^\s*/(?P<cmd>ask|improve|summary|help)\s*(?P<args>.*)$",
    re.I | re.S,
)


@dataclass
class SlashCommand:
    name: str  # lowercase: ask | improve | summary | help
    args: str  # everything after the command name, stripped

    KNOWN: ClassVar[frozenset[str]] = frozenset({"ask", "improve", "summary", "help"})


def parse_slash_command(note_body: str) -> SlashCommand | None:
    """Return a SlashCommand if the note starts with a valid slash command, else None."""
    m = _COMMAND_RE.match(note_body.strip())
    if m is None:
        return None
    return SlashCommand(name=m.group("cmd").lower(), args=m.group("args").strip())


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
## 🤖 gitlab-reviewer Slash Commands

| Command | Description |
|---------|-------------|
| `/ask <question>` | Ask a question about this MR |
| `/improve [path]` | Get improvement suggestions (optionally for a specific file) |
| `/summary` | Generate a concise MR walkthrough |
| `/help` | Show this help |
"""


async def execute_slash_command(
    cmd: SlashCommand,
    project_id: int | str,
    mr_iid: int,
    gitlab_url: str,
    gitlab_token: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    llm_temperature: float = 0.1,
    tls_verify: bool = True,
) -> str:
    """Execute a slash command and return the reply text to be posted as a comment."""
    from .gitlab_client import GitLabClient
    from .llm_client import LLMClient

    if cmd.name == "help":
        return _HELP_TEXT

    gitlab = GitLabClient(gitlab_url, gitlab_token, tls_verify=tls_verify)
    llm = LLMClient(base_url=llm_base_url, api_key=llm_api_key, model=llm_model)

    try:
        mr = await gitlab.get_mr(project_id, mr_iid)
        diffs = await gitlab.get_diffs(project_id, mr_iid, max_files=30)
    except Exception as exc:
        logger.error("Slash command: failed to fetch MR data — %s", exc)
        return f"⚠️ Failed to fetch MR data: {exc}"
    finally:
        await gitlab.aclose()

    if not diffs:
        return "⚠️ No diff available for this MR."

    diff_text = _build_diff_context(mr, diffs, cmd)

    try:
        if cmd.name == "ask":
            reply = await _handle_ask(llm, diff_text, cmd.args)
        elif cmd.name == "improve":
            reply = await _handle_improve(llm, diff_text, cmd.args)
        elif cmd.name == "summary":
            reply = await _handle_summary(llm, diff_text)
        else:
            reply = _HELP_TEXT
    except Exception as exc:
        logger.error("Slash command '%s' failed: %s", cmd.name, exc)
        reply = f"⚠️ Command failed: {exc}"
    finally:
        await llm.aclose()

    return reply


def _build_diff_context(mr, diffs, cmd: SlashCommand) -> str:
    """Build the user message for the LLM based on command type."""
    from .reviewer import _combine_diffs

    if cmd.name == "improve" and cmd.args:
        # Scope to the requested file path
        path_filter = cmd.args.lower()
        filtered = [d for d in diffs if path_filter in (d.new_path or "").lower()]
        if filtered:
            diff_text = _combine_diffs(filtered)
        else:
            diff_text = _combine_diffs(diffs)
    else:
        diff_text = _combine_diffs(diffs)

    return (
        f"MR: {mr.title}\n"
        f"Author: {mr.author}\n"
        f"{mr.source_branch} → {mr.target_branch}\n\n"
        f"Diff:\n{diff_text[:20_000]}"
    )


async def _handle_ask(llm, diff_context: str, question: str) -> str:
    if not question:
        return "⚠️ Usage: `/ask <your question>`"
    system = (
        "You are a senior engineer reviewing a merge request. "
        "Answer the following question about the MR diff concisely and accurately. "
        "If the answer is not in the diff, say so honestly."
    )
    answer = await llm.chat(system, f"{diff_context}\n\nQuestion: {question}")
    return f"**Q: {question}**\n\n{answer}"


async def _handle_improve(llm, diff_context: str, path: str) -> str:
    scope = f" for `{path}`" if path else ""
    system = (
        "You are a senior engineer reviewing a merge request. "
        f"Suggest concrete, actionable improvements{scope}. "
        "Focus on correctness, readability, and maintainability. "
        "Be specific — quote code lines when possible."
    )
    return await llm.chat(system, diff_context)


async def _handle_summary(llm, diff_context: str) -> str:
    system = (
        "You are a senior engineer. Write a concise MR walkthrough in 3-5 sentences:\n"
        "1. What this MR changes\n"
        "2. The approach used\n"
        "3. Any risks or concerns\n"
        "Be direct. Output plain text only."
    )
    return await llm.chat(system, diff_context)
