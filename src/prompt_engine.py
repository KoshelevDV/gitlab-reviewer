"""
Prompt Engine — loads, composes and sanitises prompts.

Prompt injection prevention
────────────────────────────
1. System prompt is assembled from trusted files at startup (immutable per request).
2. Diff / MR metadata are NEVER string-interpolated into the system prompt.
   They are delivered as a separate *user* message turn.
3. The base system prompt contains an explicit anti-injection instruction that
   is always prepended.
4. Model-specific control tokens that could hijack instruction parsing are
   stripped from untrusted input before it reaches the LLM.
5. Diff size is hard-capped so a malicious large diff cannot exceed context.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tokens used by common open-weights models to delimit roles.
# Stripping them from untrusted content prevents role-hijacking.
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"<\|(?:system|user|assistant|im_start|im_end)[^|]*\|>", re.I),  # Qwen/ChatML
    re.compile(r"\[INST\]|\[/INST\]", re.I),           # Llama 2
    re.compile(r"<<SYS>>|<</SYS>>", re.I),              # Llama 2 system
    re.compile(r"<s>|</s>", re.I),                       # BOS/EOS tokens
    re.compile(r"###\s*(?:System|Human|Assistant)\s*:", re.I),  # Alpaca-style
    re.compile(r"\bIGNORE\s+(?:ALL\s+)?(?:PREVIOUS|ABOVE)\b", re.I),  # classic injection
    re.compile(r"\bDISREGARD\s+(?:ALL\s+)?(?:PREVIOUS|ABOVE)\b", re.I),
    re.compile(r"\bNEW\s+INSTRUCTION\b", re.I),
    re.compile(r"\bSYSTEM\s*PROMPT\b", re.I),
]

_INCLUDE_RE = re.compile(r"^\{\{include:\s*(?P<name>[a-zA-Z0-9_\-/]+)\s*\}\}\s*$", re.M)

# ---------------------------------------------------------------------------


class PromptEngine:
    """
    Loads prompt files from disk, resolves {{include: name}} directives,
    and assembles the final system prompt.

    Prompt files live in:
      prompts/system/    — built-in (version-controlled)
      prompts/custom/    — user overrides (gitignored)

    Config example (config.yml):
      prompts:
        system:
          - base          # always first — contains anti-injection rules
          - code_review
          - security
          - "{{include: performance}}"   # also supported inline
    """

    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_system_prompt(self, names: list[str]) -> str:
        """
        Assemble a system prompt from a list of prompt names.
        Each name maps to prompts/system/<name>.md or prompts/custom/<name>.md
        (custom takes precedence over system).
        """
        parts: list[str] = []
        seen: set[str] = set()
        for name in names:
            text = self._load_resolved(name, seen=seen)
            if text:
                parts.append(text.strip())

        assembled = "\n\n---\n\n".join(parts)
        logger.debug("System prompt assembled from: %s (%d chars)", names, len(assembled))
        return assembled

    def sanitize_untrusted(self, text: str, max_chars: int = 32_000) -> str:
        """
        Sanitise untrusted text (diff, MR title, description, commit messages)
        before including it in the *user* message turn.

        - Strips model control tokens
        - Truncates to max_chars
        - Returns sanitised text and a warning note if tokens were stripped
        """
        original_len = len(text)
        stripped_count = 0

        result = text
        for pat in _INJECTION_PATTERNS:
            cleaned = pat.sub("[REDACTED]", result)
            if cleaned != result:
                stripped_count += result.count(pat.pattern)  # approx
                result = cleaned

        if len(result) > max_chars:
            result = result[:max_chars]
            result += f"\n\n[... diff truncated at {max_chars} chars; original was {original_len} chars ...]"

        if stripped_count:
            logger.warning(
                "Sanitised %d potential injection pattern(s) from untrusted input",
                stripped_count,
            )

        return result

    def fingerprint(self, text: str) -> str:
        """SHA-256 of text — used for dedup caching."""
        return hashlib.sha256(text.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_resolved(self, name: str, *, seen: set[str], depth: int = 0) -> str:
        """Load a prompt file and recursively resolve {{include:}} directives."""
        if depth > 8:
            logger.error("{{include}} depth limit reached for '%s' — circular include?", name)
            return ""

        if name in seen:
            logger.warning("Circular {{include}} detected for '%s', skipping", name)
            return ""
        seen.add(name)

        raw = self._read_file(name)
        if raw is None:
            logger.error("Prompt file not found: '%s'", name)
            return ""

        # Resolve {{include: other}} directives
        def _replace(m: re.Match) -> str:
            inc_name = m.group("name")
            return self._load_resolved(inc_name, seen=set(seen), depth=depth + 1)

        return _INCLUDE_RE.sub(_replace, raw)

    def _read_file(self, name: str) -> str | None:
        """
        Try to find <name>.md or <name>.txt in:
          1. prompts/custom/   (user override wins)
          2. prompts/system/
        """
        if name in self._cache:
            return self._cache[name]

        for sub in ("custom", "system"):
            for ext in (".md", ".txt"):
                p = self._dir / sub / f"{name}{ext}"
                if p.exists():
                    content = p.read_text(encoding="utf-8")
                    self._cache[name] = content
                    return content
        return None
