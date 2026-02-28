"""Configuration — loaded from environment variables + config.yml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLR_", env_file=".env", extra="ignore")

    # GitLab
    gitlab_url: str = Field("https://gitlab.com", description="GitLab base URL")
    gitlab_token: str = Field(..., description="GitLab personal/project access token")
    webhook_secret: str = Field(..., description="GitLab webhook secret token")

    # LLM
    ollama_url: str = Field("http://localhost:11434", description="Ollama base URL")
    ollama_model: str = Field("qwen2.5-coder:32b", description="Model name in ollama")
    llm_timeout: int = Field(300, description="LLM request timeout in seconds")
    llm_max_diff_chars: int = Field(
        32_000, description="Max diff characters to send to LLM (rest truncated)"
    )
    llm_temperature: float = Field(0.2, description="LLM temperature (low = focused)")

    # Prompt engine
    prompts_dir: Path = Field(Path("prompts"), description="Path to prompts directory")
    config_file: Path = Field(Path("config.yml"), description="Path to config.yml")

    # Server
    host: str = Field("0.0.0.0")
    port: int = Field(8000)
    log_level: str = Field("info")

    # Behaviour
    dry_run: bool = Field(False, description="Log review but do not post to GitLab")
    diff_cache_ttl: int = Field(3600, description="Seconds to cache diff hashes (dedup)")
    max_files_per_review: int = Field(
        50, description="Skip review if MR touches more files than this"
    )

    @field_validator("prompts_dir", "config_file", mode="before")
    @classmethod
    def expand_path(cls, v: Any) -> Path:
        return Path(v).expanduser().resolve()


def load_review_config(path: Path) -> dict:
    """Load config.yml with prompt composition settings."""
    if not path.exists():
        return _default_review_config()
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {**_default_review_config(), **data}


def _default_review_config() -> dict:
    return {
        "prompts": {
            "system": ["base", "code_review", "security"],
        },
        "reviewers": {
            "whitelist_authors": [],   # empty = allow all
            "whitelist_projects": [],  # empty = allow all
            "skip_draft": True,
        },
    }
