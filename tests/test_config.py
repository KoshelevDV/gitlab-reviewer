"""Tests for config.py — AppConfig, load_config, Provider, RoleModelConfig."""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml


def test_per_role_models_yaml_parsing():
    """AC1: per_role_models loads correctly from config.yml file."""
    from src.config import load_config

    cfg_data = {
        "providers": [{"id": "openrouter", "name": "OR", "type": "openai_compat",
                       "url": "https://openrouter.ai/api", "active": True,
                       "api_key": "test-key"}],
        "model": {"provider_id": "openrouter", "name": "default-model"},
        "review": {
            "pipeline_v2": True,
            "per_role_models": {
                "roles": {
                    "architect": {"provider_id": "openrouter", "name": "claude-sonnet"},
                    "developer": {"provider_id": "openrouter", "name": "qwen2.5-coder"},
                }
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(cfg_data, f)
        tmp = f.name
    try:
        cfg = load_config(tmp)
        assert cfg.review.per_role_models.roles.get("architect") is not None
        assert cfg.review.per_role_models.roles["architect"].name == "claude-sonnet"
        assert cfg.review.per_role_models.roles.get("developer") is not None
        assert cfg.review.per_role_models.roles.get("tester") is None
    finally:
        os.unlink(tmp)


def test_provider_url_scheme_validator_rejects_invalid():
    """Fix 5: Provider must reject non-http/https URLs."""
    from pydantic import ValidationError
    from src.config import Provider

    with pytest.raises(ValidationError, match="http or https"):
        Provider(id="bad", name="Bad", url="ftp://evil.com/api", api_key="")


def test_provider_url_scheme_validator_accepts_http():
    """Fix 5: Provider accepts http URLs."""
    from src.config import Provider

    p = Provider(id="local", name="Local", url="http://localhost:11434", api_key="")
    assert p.url == "http://localhost:11434"


def test_provider_url_scheme_validator_accepts_https():
    """Fix 5: Provider accepts https URLs."""
    from src.config import Provider

    p = Provider(id="cloud", name="Cloud", url="https://openrouter.ai/api", api_key="sk-test")
    assert p.url == "https://openrouter.ai/api"


def test_provider_api_key_is_secret_str():
    """Fix 6: api_key is SecretStr — not revealed in repr/str."""
    from pydantic import SecretStr
    from src.config import Provider

    p = Provider(id="x", name="X", url="http://localhost:11434", api_key="super-secret")
    assert isinstance(p.api_key, SecretStr)
    assert "super-secret" not in repr(p)
    assert "super-secret" not in str(p.api_key)
    assert p.api_key.get_secret_value() == "super-secret"


def test_model_config_has_timeout_field():
    """Fix 2: ModelConfig has timeout field with default 300."""
    from src.config import ModelConfig

    mc = ModelConfig()
    assert mc.timeout == 300

    mc2 = ModelConfig(timeout=60)
    assert mc2.timeout == 60
