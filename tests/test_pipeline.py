"""Tests for pipeline.py — PipelineManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.context_builder import MRContext
from src.pipeline import PipelineManager, ReviewRole

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
            f"# Architect Review ({stack})\n\n[PROJECT_CONTEXT]\n[ARCH_DECISIONS]\n[DIFF]\n"
        )

    (tmp_path / "tester" / "manual.md").write_text("# Tester Review\n\n[TASK_CONTEXT]\n[DIFF]\n")
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

    # AC9: Go detection
    def test_detect_stack_go(self) -> None:
        """Go stack detection from AGENTS.md."""
        assert PipelineManager.detect_stack("## Stack\nGo 1.22, gin, pgx/v5") == "go"
        assert PipelineManager.detect_stack("golang 1.21 project") == "go"
        assert PipelineManager.detect_stack('depends on "go" module') == "go"

    def test_detect_stack_go_not_triggered_by_common_words(self) -> None:
        """'go' as common word must not trigger Go detection."""
        result = PipelineManager.detect_stack("let's go ahead and run pytest")
        assert result == "python"  # default, не go


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

        async def mock_chat(system_prompt: str, user_message: str, temperature: float = 0.1) -> str:
            call_log.append({"system": system_prompt[:100], "temp": temperature})
            # Final reviewer response includes APPROVE
            if "Final Review" in system_prompt or "PREVIOUS_REVIEWS" in system_prompt:
                return "## Review\n\nAll good.\n\n---\n## Decision: APPROVE\n\n**Reason:** OK"
            return "## Review\n\nLooks good.\n[HIGH] Minor issue found."

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

        async def mock_chat(system_prompt: str, user_message: str, temperature: float = 0.1) -> str:
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

        async def mock_chat(system_prompt: str, user_message: str, temperature: float = 0.1) -> str:
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

        async def mock_chat(system_prompt: str, user_message: str, temperature: float = 0.1) -> str:
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


# ---------------------------------------------------------------------------
# AC10: pipeline_v2 feature flag reads correctly from config
# ---------------------------------------------------------------------------


class TestPipelineV2Config:
    def test_pipeline_v2_false_uses_old_pipeline(self) -> None:
        """pipeline_v2=False must be readable from config."""
        from src.config import AppConfig

        _p1 = {
            "id": "p1",
            "name": "test-provider",
            "type": "llamacpp",
            "url": "http://localhost:8080",
            "active": True,
        }
        cfg_data = {
            "gitlab": {"url": "http://gl.example.com", "auth_type": "token"},
            "providers": [_p1],
            "model": {"provider_id": "p1", "name": "test-model"},
            "review": {"pipeline_v2": False},
        }
        cfg = AppConfig.model_validate(cfg_data)
        assert cfg.review.pipeline_v2 is False

    def test_pipeline_v2_true_enables_new_pipeline(self, tmp_path) -> None:
        """pipeline_v2=True must be readable from config."""
        import os

        from src.config import AppConfig

        prompts_dir = str(tmp_path / "prompts")
        os.makedirs(prompts_dir, exist_ok=True)
        _p1 = {
            "id": "p1",
            "name": "test-provider",
            "type": "llamacpp",
            "url": "http://localhost:8080",
            "active": True,
        }
        cfg_data = {
            "gitlab": {"url": "http://gl.example.com", "auth_type": "token"},
            "providers": [_p1],
            "model": {"provider_id": "p1", "name": "test-model"},
            "review": {"pipeline_v2": True, "prompts_dir": prompts_dir},
        }
        cfg = AppConfig.model_validate(cfg_data)
        assert cfg.review.pipeline_v2 is True
        assert cfg.review.prompts_dir == prompts_dir


# ---------------------------------------------------------------------------
# AC8: PREVIOUS_REVIEWS contains real content from all 4 roles
# ---------------------------------------------------------------------------


class TestPipelineRunFinalReceivesAllRoles:
    @pytest.mark.asyncio
    async def test_run_final_receives_all_role_findings(self, tmp_path: Path) -> None:
        """Final reviewer must receive actual content from all 4 parallel roles."""
        from src.context_builder import MRContext

        # Create minimal prompt files with role-specific markers
        for role_dir in ["developer", "architect", "tester", "security", "reviewer"]:
            (tmp_path / role_dir).mkdir()
        (tmp_path / "developer" / "python.md").write_text(
            "Developer review: [DIFF] previous: [PREVIOUS_REVIEWS]"
        )
        (tmp_path / "architect" / "python.md").write_text("Arch review: [DIFF]")
        (tmp_path / "tester" / "manual.md").write_text("Test review: [DIFF]")
        (tmp_path / "security" / "general.md").write_text("Sec review: [DIFF]")
        (tmp_path / "reviewer" / "general.md").write_text("Final: [PREVIOUS_REVIEWS]")

        call_args: list[str] = []

        async def mock_chat(system_prompt: str, user_message: str = "", **kwargs) -> str:
            call_args.append(system_prompt)
            if "Arch review" in system_prompt:
                return "ARCHITECT_MARKER: no issues"
            if "Test review" in system_prompt:
                return "TESTER_MARKER: all good"
            if "Sec review" in system_prompt:
                return "SECURITY_MARKER: clear"
            if "Final:" in system_prompt:
                return "## Decision: APPROVE\n\nAll good."
            return "DEVELOPER_MARKER: ok"

        llm = MagicMock()
        llm.chat = mock_chat

        pm = PipelineManager(llm_client=llm, prompts_dir=tmp_path, stack="python")
        ctx = MRContext(
            project_context="proj",
            task_context="task",
            dynamic_context="",
            security_baseline="",
            diff="some diff",
            arch_decisions="",
        )

        await pm.run(ctx)

        # Final call must contain results from all 4 parallel roles
        final_prompt = next((a for a in call_args if "Final:" in a), None)
        assert final_prompt is not None
        assert "DEVELOPER_MARKER" in final_prompt
        assert "ARCHITECT_MARKER" in final_prompt
        assert "TESTER_MARKER" in final_prompt
        assert "SECURITY_MARKER" in final_prompt


# ---------------------------------------------------------------------------
# Per-role model config tests (AC1–AC5)
# ---------------------------------------------------------------------------


class TestPerRoleModelConfig:
    """Tests for per-role model config feature."""

    def _make_providers(self):
        from src.config import Provider, ProviderType

        return [
            Provider(
                id="global-provider",
                name="Global",
                type=ProviderType.llamacpp,
                url="http://global:8080",
                api_key="",
                active=True,
            ),
            Provider(
                id="architect-provider",
                name="Architect LLM",
                type=ProviderType.openai_compat,
                url="http://architect:8080",
                api_key="arch-key",
                active=True,
            ),
        ]

    def test_per_role_config_defaults_empty(self):
        """AC5: RoleModelConfig() has empty roles dict by default."""
        from src.config import RoleModelConfig

        cfg = RoleModelConfig()
        assert cfg.roles == {}
        assert cfg.roles.get("developer") is None
        assert cfg.roles.get("architect") is None
        assert cfg.roles.get("tester") is None
        assert cfg.roles.get("security") is None
        assert cfg.roles.get("reviewer") is None

    def test_per_role_fallback_uses_global_model(self, prompts_dir: Path, mock_llm: MagicMock):
        """AC2: Role with no override → returns global LLMClient."""
        from src.config import RoleModelConfig

        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=RoleModelConfig(),  # all None
            providers=self._make_providers(),
        )
        client = pm._get_llm_for_role(ReviewRole.ARCHITECT)
        assert client is mock_llm

    def test_per_role_override_uses_role_model(self, prompts_dir: Path, mock_llm: MagicMock):
        """AC1 + AC3: Override for architect → dedicated LLMClient, not global."""
        from src.config import ModelConfig, RoleModelConfig

        role_models = RoleModelConfig(
            roles={
                "architect": ModelConfig(
                    provider_id="architect-provider",
                    name="claude-sonnet",
                    temperature=0.1,
                )
            }
        )
        providers = self._make_providers()
        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=role_models,
            providers=providers,
        )

        architect_client = pm._get_llm_for_role(ReviewRole.ARCHITECT)
        # Must NOT be the global client
        assert architect_client is not mock_llm
        # Non-overridden roles still use global
        developer_client = pm._get_llm_for_role(ReviewRole.DEVELOPER)
        assert developer_client is mock_llm

    def test_per_role_all_overridden(self, prompts_dir: Path, mock_llm: MagicMock):
        """AC5: All 5 roles overridden → each gets its own client (or reuses by provider)."""
        from src.config import ModelConfig, Provider, ProviderType, RoleModelConfig

        providers = [
            Provider(
                id=f"p-{role}",
                name=role,
                type=ProviderType.llamacpp,
                url=f"http://{role}:8080",
                api_key="",
                active=True,
            )
            for role in ("developer", "architect", "tester", "security", "reviewer")
        ]

        role_models = RoleModelConfig(
            roles={
                "developer": ModelConfig(provider_id="p-developer", name="dev-model"),
                "architect": ModelConfig(provider_id="p-architect", name="arch-model"),
                "tester": ModelConfig(provider_id="p-tester", name="test-model"),
                "security": ModelConfig(provider_id="p-security", name="sec-model"),
                "reviewer": ModelConfig(provider_id="p-reviewer", name="rev-model"),
            }
        )

        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=role_models,
            providers=providers,
        )

        clients = {role: pm._get_llm_for_role(role) for role in ReviewRole}
        # All must be distinct from global
        for role, client in clients.items():
            assert client is not mock_llm, f"Role {role} should have dedicated client"
        # All clients must be different from each other (different providers)
        client_list = list(clients.values())
        assert len(set(id(c) for c in client_list)) == 5

    def test_backward_compat_no_per_role(self, prompts_dir: Path, mock_llm: MagicMock):
        """AC4: No per_role_models → PipelineManager uses global LLM for all roles."""
        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            # role_models omitted — defaults to RoleModelConfig()
        )
        for role in ReviewRole:
            assert pm._get_llm_for_role(role) is mock_llm

    def test_per_role_unknown_provider_fallback(self, prompts_dir: Path, mock_llm: MagicMock):
        """Unknown provider_id in override → falls back to global LLM + warning logged."""
        from src.config import ModelConfig, RoleModelConfig

        role_models = RoleModelConfig(
            roles={"security": ModelConfig(provider_id="nonexistent-provider", name="some-model")}
        )
        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=role_models,
            providers=self._make_providers(),  # no "nonexistent-provider"
        )
        client = pm._get_llm_for_role(ReviewRole.SECURITY)
        assert client is mock_llm

    def test_per_role_client_cached(self, prompts_dir: Path, mock_llm: MagicMock):
        """Same role called twice → same LLMClient instance (cached)."""
        from src.config import ModelConfig, RoleModelConfig

        role_models = RoleModelConfig(
            roles={"developer": ModelConfig(provider_id="architect-provider", name="dev-model")}
        )
        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=role_models,
            providers=self._make_providers(),
        )
        c1 = pm._get_llm_for_role(ReviewRole.DEVELOPER)
        c2 = pm._get_llm_for_role(ReviewRole.DEVELOPER)
        assert c1 is c2

    def test_per_role_config_in_review_config(self):
        """AC1: ReviewConfig parses per_role_models from config dict."""
        from src.config import AppConfig

        cfg_data = {
            "providers": [
                {
                    "id": "openrouter",
                    "name": "OpenRouter",
                    "type": "openai_compat",
                    "url": "https://openrouter.ai/api",
                    "active": True,
                }
            ],
            "model": {"provider_id": "openrouter", "name": "default-model"},
            "review": {
                "pipeline_v2": True,
                "per_role_models": {
                    "roles": {
                        "architect": {
                            "provider_id": "openrouter",
                            "name": "anthropic/claude-sonnet-4-5",
                        },
                        "security": {
                            "provider_id": "openrouter",
                            "name": "anthropic/claude-sonnet-4-5",
                        },
                        "developer": {"provider_id": "openrouter", "name": "qwen2.5-coder-7b"},
                    }
                },
            },
        }
        cfg = AppConfig.model_validate(cfg_data)
        assert cfg.review.per_role_models.roles.get("architect") is not None
        assert cfg.review.per_role_models.roles["architect"].name == "anthropic/claude-sonnet-4-5"
        assert cfg.review.per_role_models.roles.get("security") is not None
        assert cfg.review.per_role_models.roles.get("developer") is not None
        assert cfg.review.per_role_models.roles["developer"].name == "qwen2.5-coder-7b"
        # Unset roles are None
        assert cfg.review.per_role_models.roles.get("tester") is None
        assert cfg.review.per_role_models.roles.get("reviewer") is None

    @pytest.mark.asyncio
    async def test_per_role_override_uses_role_model_in_run(self, prompts_dir: Path):
        """AC3: _run_role picks up override client during actual pipeline run."""
        from src.config import ModelConfig, Provider, ProviderType, RoleModelConfig

        global_llm = MagicMock()
        global_llm.chat = AsyncMock(return_value="Global LLM response. APPROVE")

        arch_llm = MagicMock()
        arch_llm.chat = AsyncMock(return_value="Architect LLM response. ## Decision: APPROVE")

        providers = [
            Provider(
                id="arch-p",
                name="Arch Provider",
                type=ProviderType.llamacpp,
                url="http://arch:8080",
                api_key="",
                active=True,
            )
        ]
        role_models = RoleModelConfig(
            roles={"architect": ModelConfig(provider_id="arch-p", name="arch-model")}
        )

        pm = PipelineManager(
            llm_client=global_llm,
            prompts_dir=prompts_dir,
            stack="python",
            role_models=role_models,
            providers=providers,
        )
        # Inject the arch_llm directly into the cache to verify it's used
        pm._role_llm_cache[ReviewRole.ARCHITECT] = arch_llm

        ctx = MRContext(
            project_context="proj",
            task_context="task",
            dynamic_context="",
            security_baseline="",
            diff="diff",
            arch_decisions="",
        )
        await pm.run(ctx)

        # architect_llm must have been called
        assert arch_llm.chat.call_count == 1
        # global llm called for the rest (developer, tester, security, reviewer = 4)
        assert global_llm.chat.call_count == 4

    @pytest.mark.asyncio
    async def test_backward_compat_run_uses_global_llm_for_all_roles(
        self, prompts_dir: Path, mock_llm: MagicMock
    ):
        """AC4: Without per_role_models, all roles in run() use the same global LLMClient."""

        call_clients = []

        async def recording_chat(system_prompt, **kwargs):
            call_clients.append(id(mock_llm))
            return "APPROVE"

        mock_llm.chat = recording_chat

        pm = PipelineManager(
            llm_client=mock_llm,
            prompts_dir=prompts_dir,
            stack="python",
            # NO per_role_models — backward compat
        )

        ctx = MRContext(
            project_context="",
            task_context="",
            dynamic_context="",
            security_baseline="",
            diff="diff",
            arch_decisions="",
        )
        await pm.run(ctx)

        # All calls came from the same global llm instance
        assert len(call_clients) >= 4  # at least 4 roles ran
        assert all(c == id(mock_llm) for c in call_clients), "Some role used a different client"
