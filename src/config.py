"""
Application configuration.

Two layers:
  1. config.yml  — all settings except secrets; single source of truth
  2. env vars    — secrets only (GLR_GITLAB_TOKEN, GLR_GITLAB_PASSWORD,
                   GLR_WEBHOOK_SECRET); override yaml values when set
"""

from __future__ import annotations

import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

CONFIG_PATH = Path(os.getenv("GLR_CONFIG_FILE", "config.yml"))


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ProviderType(StrEnum):
    ollama = "ollama"
    llamacpp = "llamacpp"
    openai_compat = "openai_compat"


class Provider(BaseModel):
    id: str
    name: str
    type: ProviderType = ProviderType.ollama
    url: str = "http://localhost:11434"
    api_key: str = ""
    active: bool = True


class ModelConfig(BaseModel):
    provider_id: str = ""
    name: str = ""
    temperature: float = 0.2
    context_size: int | None = None  # None = model default
    max_tokens: int = 4096
    inline_comments: bool = True  # post findings as GitLab inline diff comments


class GitLabConfig(BaseModel):
    url: str = "https://gitlab.com"
    auth_type: str = "token"  # token | basic
    # secrets: never stored in yaml; come from env vars at runtime
    tls_verify: bool = True
    webhook_secret: str = ""


class BranchRules(BaseModel):
    pattern: str = "*"  # glob; comma-separated = OR
    protected_only: bool = False


class PromptsOverride(BaseModel):
    system: list[str] = []


class ReviewTarget(BaseModel):
    type: str = "all"  # group | project | all
    id: str = ""
    branches: BranchRules = Field(default_factory=BranchRules)
    auto_approve: bool = False
    prompts: PromptsOverride = Field(default_factory=PromptsOverride)
    # Author filtering (empty list = no restriction)
    author_allowlist: list[str] = []  # only review MRs from these authors
    skip_authors: list[str] = []  # always skip MRs from these authors (bots, CI)
    # File filtering (per-target; merged with global AppConfig.file_exclude)
    file_exclude: list[str] = []  # fnmatch globs — matching files are removed from diff
    # Cooldown: skip reviews within N minutes of the last review of the same MR
    # None = inherit from AppConfig.review_cooldown_minutes; 0 = disabled per-target
    review_cooldown_minutes: int | None = None
    # Max files per review — None = use AppConfig.max_files_per_review
    max_files_per_review: int | None = None
    # For type=group: explicit list of project_ids that belong to this group.
    # Populated via UI (GitLab API browse) or manually in config.yml.
    # Empty list = match all projects (use with caution).
    project_ids: list[str] = Field(default_factory=list)


class NotificationFormat(StrEnum):
    slack = "slack"
    telegram = "telegram"
    generic = "generic"


class NotificationConfig(BaseModel):
    enabled: bool = False
    format: NotificationFormat = NotificationFormat.generic
    webhook_url: str = ""  # Slack / generic HTTP webhook; env: GLR_NOTIFY_WEBHOOK_URL
    # Telegram Bot API (alternative to generic webhook)
    telegram_bot_token: str = ""  # env: GLR_TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = ""  # env: GLR_TELEGRAM_CHAT_ID
    # Events
    on_posted: bool = True  # notify when review posted
    on_error: bool = False  # notify on review error
    on_skipped: bool = False  # notify when review skipped


class QueueConfig(BaseModel):
    backend: str = "memory"  # memory | valkey | kafka
    max_concurrent: int = 3
    max_queue_size: int = 100
    # Valkey backend
    valkey_url: str = "redis://localhost:6379"
    # Kafka backend
    kafka_brokers: str = "localhost:9092"  # comma-separated broker list
    kafka_topic: str = "glr.mr.events"
    kafka_group_id: str = "glr-reviewers"


class CacheConfig(BaseModel):
    backend: str = "memory"
    ttl: int = 3600
    valkey_url: str = "redis://localhost:6379"


class MemoryConfig(BaseModel):
    """Qdrant-backed reviewer memory settings."""

    enabled: bool = False
    qdrant_url: str = "http://qdrant:6333"
    collection: str = "reviewer_memory"
    top_k: int = 5


class RoleModelConfig(BaseModel):
    """Per-role model override for pipeline_v2. Unset fields fall back to global model config."""

    developer: ModelConfig | None = None
    architect: ModelConfig | None = None
    tester: ModelConfig | None = None
    security: ModelConfig | None = None
    reviewer: ModelConfig | None = None


class ReviewConfig(BaseModel):
    """v2 pipeline settings."""

    pipeline_v2: bool = False  # enable v2 multi-role parallel pipeline
    prompts_dir: str = "/opt/projects/llm-review-prompts/prompts"  # path to role prompts
    context_token_budget: int = 3000  # token budget for docs/ and dynamic context
    per_role_models: RoleModelConfig = Field(default_factory=RoleModelConfig)


class PromptsConfig(BaseModel):
    system: list[str] = ["base", "security"]


class UIConfig(BaseModel):
    enabled: bool = True
    log_buffer_lines: int = 1000


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000
    log_level: str = "info"


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    providers: list[Provider] = Field(default_factory=list)
    model: ModelConfig = Field(default_factory=ModelConfig)
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)
    review_targets: list[ReviewTarget] = Field(default_factory=list)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    # Review cooldown — skip re-reviews within this window (0 = disabled)
    review_cooldown_minutes: int = 0
    # Max files to include in a single review (files beyond this count are truncated)
    max_files_per_review: int = 50
    # Global file exclusions applied to every review (per-target file_exclude is appended)
    file_exclude: list[str] = Field(
        default_factory=lambda: [
            "*.lock",
            "package-lock.json",
            "yarn.lock",
            "poetry.lock",
            "Cargo.lock",
            "vendor/**",
            "node_modules/**",
            "*.min.js",
            "*.min.css",
            "*.generated.*",
            "dist/**",
            "build/**",
        ]
    )
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    # Secrets injected from env — not serialised to yaml
    _gitlab_token: str = ""
    _gitlab_password: str = ""

    @model_validator(mode="after")
    def _inject_secrets(self) -> AppConfig:
        token = os.getenv("GLR_GITLAB_TOKEN", "")
        if token:
            object.__setattr__(self, "_gitlab_token", token)
        pwd = os.getenv("GLR_GITLAB_PASSWORD", "")
        if pwd:
            object.__setattr__(self, "_gitlab_password", pwd)
        ws = os.getenv("GLR_WEBHOOK_SECRET", "")
        if ws:
            self.gitlab.webhook_secret = ws
        nwu = os.getenv("GLR_NOTIFY_WEBHOOK_URL", "")
        if nwu:
            self.notifications.webhook_url = nwu
        tbt = os.getenv("GLR_TELEGRAM_BOT_TOKEN", "")
        if tbt:
            self.notifications.telegram_bot_token = tbt
        tci = os.getenv("GLR_TELEGRAM_CHAT_ID", "")
        if tci:
            self.notifications.telegram_chat_id = tci
        # LLM provider API key — injected into the active provider (matches provider_id or first)
        llm_key = os.getenv("GLR_LLM_API_KEY", "")
        if llm_key:
            for p in self.providers:
                if not p.api_key and (p.id == self.model.provider_id or not self.model.provider_id):
                    p.api_key = llm_key
                    break
        return self

    @property
    def gitlab_token(self) -> str:
        return self._gitlab_token  # type: ignore[return-value]

    @property
    def gitlab_password(self) -> str:
        return self._gitlab_password  # type: ignore[return-value]

    def active_provider(self) -> Provider | None:
        for p in self.providers:
            if p.id == self.model.provider_id and p.active:
                return p
        return next((p for p in self.providers if p.active), None)


# ---------------------------------------------------------------------------
# Loader / saver
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        return AppConfig()
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


def save_config(cfg: AppConfig, path: Path = CONFIG_PATH) -> None:
    """Atomic write: temp file → rename. Keeps backup of previous version."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(".yml.bak"))

    tmp = path.with_name(".config.yml.tmp")
    # mode='json' converts Enum → str, datetime → str, etc.
    data: dict = cfg.model_dump(mode="json", exclude_none=False)
    # Never write secrets to yaml — strip all env-only credentials
    data.get("gitlab", {}).pop("webhook_secret", None)
    notif = data.get("notifications", {})
    notif.pop("webhook_url", None)
    notif.pop("telegram_bot_token", None)
    notif.pop("telegram_chat_id", None)
    # Strip private attrs that appear as None (pydantic v2 private fields)
    data.pop("_gitlab_token", None)
    data.pop("_gitlab_password", None)
    # Strip provider api_keys that were injected from env (GLR_LLM_API_KEY)
    # — only keep api_keys that were originally present in the loaded yaml
    llm_key_from_env = os.getenv("GLR_LLM_API_KEY", "")
    if llm_key_from_env:
        for p in data.get("providers", []):
            if p.get("api_key") == llm_key_from_env:
                p["api_key"] = ""

    with tmp.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    tmp.rename(path)


# Global mutable config instance (reloaded without restart)
_config: AppConfig = AppConfig()


def get_config() -> AppConfig:
    return _config


def reload_config(path: Path = CONFIG_PATH) -> AppConfig:
    global _config
    _config = load_config(path)
    return _config
