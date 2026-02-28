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
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

CONFIG_PATH = Path(os.getenv("GLR_CONFIG_FILE", "config.yml"))


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
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
    context_size: int | None = None   # None = model default
    max_tokens: int = 4096


class GitLabConfig(BaseModel):
    url: str = "https://gitlab.com"
    auth_type: str = "token"          # token | basic
    # secrets: never stored in yaml; come from env vars at runtime
    tls_verify: bool = True
    webhook_secret: str = ""


class BranchRules(BaseModel):
    pattern: str = "*"                # glob; comma-separated = OR
    protected_only: bool = False


class PromptsOverride(BaseModel):
    system: list[str] = []


class ReviewTarget(BaseModel):
    type: str = "all"                 # group | project | all
    id: str = ""
    branches: BranchRules = Field(default_factory=BranchRules)
    auto_approve: bool = False
    prompts: PromptsOverride = Field(default_factory=PromptsOverride)


class QueueConfig(BaseModel):
    backend: str = "memory"           # memory | valkey
    max_concurrent: int = 3
    max_queue_size: int = 100
    valkey_url: str = "redis://localhost:6379"


class CacheConfig(BaseModel):
    backend: str = "memory"
    ttl: int = 3600
    valkey_url: str = "redis://localhost:6379"


class PromptsConfig(BaseModel):
    system: list[str] = ["base", "security"]


class UIConfig(BaseModel):
    enabled: bool = True
    log_buffer_lines: int = 1000


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
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
    ui: UIConfig = Field(default_factory=UIConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    # Secrets injected from env — not serialised to yaml
    _gitlab_token: str = ""
    _gitlab_password: str = ""

    @model_validator(mode="after")
    def _inject_secrets(self) -> "AppConfig":
        token = os.getenv("GLR_GITLAB_TOKEN", "")
        if token:
            object.__setattr__(self, "_gitlab_token", token)
        pwd = os.getenv("GLR_GITLAB_PASSWORD", "")
        if pwd:
            object.__setattr__(self, "_gitlab_password", pwd)
        ws = os.getenv("GLR_WEBHOOK_SECRET", "")
        if ws:
            self.gitlab.webhook_secret = ws
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
    # Backup
    if path.exists():
        shutil.copy2(path, path.with_suffix(".yml.bak"))

    tmp = path.with_name(".config.yml.tmp")
    data = cfg.model_dump(
        exclude={"_gitlab_token", "_gitlab_password"},
        exclude_none=False,
    )
    # Never write secrets to yaml
    data.get("gitlab", {}).pop("webhook_secret", None)

    with tmp.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    tmp.rename(path)


# Global mutable config instance (reloaded without restart)
_config: AppConfig = AppConfig()


def get_config() -> AppConfig:
    return _config


def reload_config(path: Path = CONFIG_PATH) -> AppConfig:
    global _config
    _config = load_config(path)
    return _config
