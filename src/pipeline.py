"""
Pipeline Manager — runs review roles in parallel, then aggregates results.

Flow:
  1. Parallel: DEVELOPER, ARCHITECT, TESTER, SECURITY (each uses role-specific prompt)
  2. Sequential: REVIEWER (final gate, receives all previous findings)
  3. Returns list[RoleResult] with all 5 results
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .config import ModelConfig, Provider, RoleModelConfig
from .context_builder import MRContext
from .llm_client import LLMClient

# Single-pass slot replacement — prevents injected content from expanding other slots
_SLOTS_RE = re.compile(
    r"\[(?:PROJECT_CONTEXT|TASK_CONTEXT|DYNAMIC_CONTEXT|DIFF|"
    r"ARCH_DECISIONS|SECURITY_BASELINE|PREVIOUS_REVIEWS|FOCUS_AREAS)\]"
)

logger = logging.getLogger(__name__)


class ReviewRole(StrEnum):
    DEVELOPER = "developer"
    ARCHITECT = "architect"
    TESTER = "tester"
    SECURITY = "security"
    REVIEWER = "reviewer"  # final, runs after all others


@dataclass
class RoleResult:
    role: ReviewRole
    findings: str  # raw LLM response text
    blocking_count: int  # count of BLOCKING + CRITICAL + HIGH
    decision: str = ""  # APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION (REVIEWER only)


# ---------------------------------------------------------------------------
# Role → prompt path mapping
# ---------------------------------------------------------------------------

# Maps (role, stack) → relative path under prompts_dir
# tester and security use stack-independent prompts
_ROLE_PROMPT_MAP: dict[tuple[ReviewRole, str], str] = {
    (ReviewRole.DEVELOPER, "dotnet"): "developer/dotnet.md",
    (ReviewRole.DEVELOPER, "rust"): "developer/rust.md",
    (ReviewRole.DEVELOPER, "python"): "developer/python.md",
    (ReviewRole.DEVELOPER, "go"): "developer/go.md",
    (ReviewRole.ARCHITECT, "dotnet"): "architect/dotnet.md",
    (ReviewRole.ARCHITECT, "rust"): "architect/rust.md",
    (ReviewRole.ARCHITECT, "python"): "architect/python.md",
    (ReviewRole.ARCHITECT, "go"): "architect/go.md",
    (ReviewRole.TESTER, "*"): "tester/manual.md",
    (ReviewRole.SECURITY, "*"): "security/general.md",
    (ReviewRole.REVIEWER, "*"): "reviewer/general.md",
}

# Slot names used in prompt templates
_SLOT_MAP = {
    "[PROJECT_CONTEXT]": "project_context",
    "[TASK_CONTEXT]": "task_context",
    "[DYNAMIC_CONTEXT]": "dynamic_context",
    "[DIFF]": "diff",
    "[ARCH_DECISIONS]": "arch_decisions",
    "[SECURITY_BASELINE]": "security_baseline",
    "[PREVIOUS_REVIEWS]": None,  # injected separately
}

_BLOCKING_RE = re.compile(r"\b(BLOCKING|CRITICAL|HIGH)\b", re.IGNORECASE)
_DECISION_RE = re.compile(
    r"\b(APPROVE|REQUEST_CHANGES|NEEDS_DISCUSSION)\b",
    re.IGNORECASE,
)


class PipelineManager:
    """
    Orchestrates multi-role parallel review pipeline.

    Usage::

        pm = PipelineManager(llm_client, prompts_dir, stack)
        results = await pm.run(ctx)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompts_dir: str | Path,
        stack: str = "python",
        role_models: RoleModelConfig | None = None,
        providers: list[Provider] | None = None,
        llm_factory: Callable[[Provider, ModelConfig], LLMClient] | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompts_dir = Path(prompts_dir)
        self._stack = stack.lower()
        self._role_models = role_models or RoleModelConfig()
        self._providers = providers or []
        self._llm_factory = llm_factory or self._default_llm_factory
        # Cache of role → LLMClient to avoid creating duplicates
        self._role_llm_cache: dict[ReviewRole, LLMClient] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, ctx: MRContext) -> list[RoleResult]:
        """
        Execute full review pipeline.

        Step 1: Run DEVELOPER, ARCHITECT, TESTER, SECURITY in parallel.
        Step 2: Run REVIEWER with aggregated findings from step 1.

        Returns all 5 RoleResult objects (parallel roles first, reviewer last).
        """
        parallel_roles = [
            ReviewRole.DEVELOPER,
            ReviewRole.ARCHITECT,
            ReviewRole.TESTER,
            ReviewRole.SECURITY,
        ]

        logger.info(
            "Pipeline: starting %d parallel roles (stack=%s)", len(parallel_roles), self._stack
        )

        parallel_results = await asyncio.gather(
            *[self._run_role(role, ctx) for role in parallel_roles],
            return_exceptions=False,
        )
        parallel_results = list(parallel_results)

        # Build PREVIOUS_REVIEWS summary for final reviewer
        previous_reviews = self._format_previous_reviews(parallel_results)

        logger.info("Pipeline: running final REVIEWER role")
        final_result = await self._run_role(
            ReviewRole.REVIEWER, ctx, previous_reviews=previous_reviews
        )

        all_results = parallel_results + [final_result]
        logger.info(
            "Pipeline complete: %d results, final decision=%s",
            len(all_results),
            final_result.decision,
        )
        return all_results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_role(
        self,
        role: ReviewRole,
        ctx: MRContext,
        previous_reviews: str = "",
    ) -> RoleResult:
        """Execute a single role: load prompt, fill slots, call LLM, parse result."""
        try:
            prompt_template = self._load_prompt(role)
            filled_prompt = self._fill_slots(prompt_template, ctx, previous_reviews)

            # Use a minimal user message — the diff is already in the filled system prompt
            user_message = (
                "Please perform your review based on the context and diff provided above."
            )

            llm_client = self._get_llm_for_role(role)
            logger.debug("Role %s: calling LLM (%d chars prompt)", role.value, len(filled_prompt))
            findings = await llm_client.chat(
                system_prompt=filled_prompt,
                user_message=user_message,
                temperature=0.1,
            )

            blocking_count = self._count_blocking(findings)
            decision = self._extract_decision(findings) if role == ReviewRole.REVIEWER else ""

            logger.info(
                "Role %s: done — blocking=%d decision=%s",
                role.value,
                blocking_count,
                decision or "N/A",
            )
            return RoleResult(
                role=role,
                findings=findings,
                blocking_count=blocking_count,
                decision=decision,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error("Role %s failed: %s", role.value, exc, exc_info=True)
            error_text = f"[PIPELINE ERROR] Role {role.value} failed: {exc}"
            return RoleResult(
                role=role,
                findings=error_text,
                blocking_count=0,
                decision="NEEDS_DISCUSSION" if role == ReviewRole.REVIEWER else "",
            )

    @staticmethod
    def _default_llm_factory(provider: Provider, role_model: ModelConfig) -> LLMClient:
        return LLMClient(
            base_url=provider.url,
            model=role_model.name,
            timeout=role_model.timeout,
            api_key=provider.api_key.get_secret_value(),
        )

    def _get_llm_for_role(self, role: ReviewRole) -> LLMClient:
        """
        Return the LLMClient for a given role.

        If a per-role ModelConfig override exists for this role and providers are
        available to resolve it, build (and cache) a dedicated LLMClient.
        Otherwise fall back to the global LLMClient passed to __init__.
        """
        if role in self._role_llm_cache:
            return self._role_llm_cache[role]

        role_model: ModelConfig | None = self._role_models.roles.get(role.value)

        if role_model is None or not self._providers:
            # No override or no providers list — use global client
            return self._llm

        # Find provider by provider_id
        provider: Provider | None = next(
            (p for p in self._providers if p.id == role_model.provider_id and p.active),
            None,
        )
        if provider is None:
            logger.warning(
                "Per-role model for %s: provider_id=%r not found or inactive — using global LLM",
                role.value,
                role_model.provider_id,
            )
            return self._llm

        client = self._llm_factory(provider, role_model)
        self._role_llm_cache[role] = client
        logger.info(
            "Per-role LLM: role=%s provider=%s model=%s",
            role.value,
            provider.id,
            role_model.name,
        )
        return client

    def _load_prompt(self, role: ReviewRole) -> str:
        """
        Read prompt template from prompts_dir for given role + stack.
        Falls back to python if stack-specific prompt not found.
        """
        # Try stack-specific path first
        path = self._prompts_dir / self._resolve_prompt_path(role, self._stack)
        if not path.exists():
            # Fallback: try python stack
            fallback_path = self._prompts_dir / self._resolve_prompt_path(role, "python")
            if fallback_path.exists():
                logger.debug("Prompt fallback: %s → %s", path, fallback_path)
                path = fallback_path
            else:
                logger.warning("Prompt not found for role=%s stack=%s", role.value, self._stack)
                return f"# {role.value.title()} Review\n\nReview the following diff."

        return path.read_text(encoding="utf-8")

    def _resolve_prompt_path(self, role: ReviewRole, stack: str) -> str:
        """Resolve relative prompt path for (role, stack)."""
        # Wildcard roles (tester, security, reviewer)
        wildcard_key = (role, "*")
        if wildcard_key in _ROLE_PROMPT_MAP:
            return _ROLE_PROMPT_MAP[wildcard_key]
        # Stack-specific roles (developer, architect)
        stack_key = (role, stack)
        if stack_key in _ROLE_PROMPT_MAP:
            return _ROLE_PROMPT_MAP[stack_key]
        # Default fallback to python
        fallback_key = (role, "python")
        return _ROLE_PROMPT_MAP.get(fallback_key, f"{role.value}/{stack}.md")

    def _fill_slots(
        self,
        template: str,
        ctx: MRContext,
        previous_reviews: str = "",
    ) -> str:
        """
        Replace slot placeholders in prompt template with actual context values.

        Uses single-pass regex substitution to prevent injected content from
        expanding other slots (prompt injection via [DIFF] containing [PREVIOUS_REVIEWS]).

        Slots:
          [PROJECT_CONTEXT]   → AGENTS.md + docs/
          [TASK_CONTEXT]      → issue or MR description
          [DYNAMIC_CONTEXT]   → full file contents + tests
          [DIFF]              → raw diff
          [ARCH_DECISIONS]    → architecture decision docs
          [SECURITY_BASELINE] → security-relevant docs
          [PREVIOUS_REVIEWS]  → aggregated findings from earlier roles
          [FOCUS_AREAS]       → (reserved, empty by default)
        """
        slot_values: dict[str, str] = {
            "[PROJECT_CONTEXT]": ctx.project_context or "(no project context available)",
            "[TASK_CONTEXT]": ctx.task_context or "(no task context available)",
            "[DYNAMIC_CONTEXT]": ctx.dynamic_context or "(no dynamic context available)",
            "[DIFF]": ctx.diff or "(no diff)",
            "[ARCH_DECISIONS]": ctx.arch_decisions or ctx.project_context or "",
            "[SECURITY_BASELINE]": ctx.security_baseline or "",
            "[PREVIOUS_REVIEWS]": previous_reviews or "",
            "[FOCUS_AREAS]": "",
        }
        return _SLOTS_RE.sub(lambda m: slot_values.get(m.group(0), m.group(0)), template)

    @staticmethod
    def detect_stack(agents_md: str) -> str:
        """
        Detect technology stack from AGENTS.md content.

        Rules (first match wins):
          .NET / C# / Blazor  → dotnet
          Rust                → rust
          Go / golang         → go
          Default             → python
        """
        text = agents_md.lower()
        if any(kw in text for kw in (".net", "c#", "blazor", "csharp", "aspnet")):
            return "dotnet"
        if "rust" in text:
            return "rust"
        if (
            "golang" in text
            or "go module" in text
            or '"go"' in text
            or bool(re.search(r"\bgo\s+\d", text))  # matches "Go 1.22", "go 1.21"
        ):
            return "go"
        return "python"

    def _detect_stack(self, agents_md: str) -> str:
        """Deprecated: use PipelineManager.detect_stack() static method instead."""
        return PipelineManager.detect_stack(agents_md)

    def _count_blocking(self, findings: str) -> int:
        """Count occurrences of BLOCKING, CRITICAL, or HIGH in findings text."""
        return len(_BLOCKING_RE.findall(findings))

    def _extract_decision(self, findings: str) -> str:
        """
        Extract final decision from REVIEWER role output.

        Looks for APPROVE, REQUEST_CHANGES, or NEEDS_DISCUSSION (case-insensitive).
        Returns first match found, or NEEDS_DISCUSSION as safe default.
        """
        match = _DECISION_RE.search(findings)
        if match:
            return match.group(1).upper()
        return "NEEDS_DISCUSSION"

    def _format_previous_reviews(self, results: list[RoleResult]) -> str:
        """Format parallel role results as PREVIOUS_REVIEWS block for the final reviewer."""
        sections: list[str] = []
        for result in results:
            sections.append(
                f"### {result.role.value.title()} Review\n\n{result.findings}\n"
                f"**Blocking issues:** {result.blocking_count}"
            )
        return "\n\n---\n\n".join(sections)
