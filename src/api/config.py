"""Config API — /api/v1/config"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import CONFIG_PATH, AppConfig, get_config, reload_config, save_config

if TYPE_CHECKING:
    from ..prompt_engine import PromptEngine

_prompt_engine: PromptEngine | None = None


def set_prompt_engine(pe: PromptEngine) -> None:
    """Register the PromptEngine instance so the reload endpoint can invalidate its cache."""
    global _prompt_engine  # noqa: PLW0603
    _prompt_engine = pe


router = APIRouter(prefix="/api/v1/config", tags=["config"])

_MASKED = "****"
_SECRET_FIELDS = {"webhook_secret", "api_key", "token", "password", "telegram_bot_token"}


def _mask_secrets(data: Any) -> Any:
    """Recursively mask secret-looking fields in dicts."""
    if isinstance(data, dict):
        return {
            k: _MASKED if k in _SECRET_FIELDS and v else _mask_secrets(v) for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_secrets(item) for item in data]
    return data


@router.get("")
async def get_config_endpoint() -> JSONResponse:
    """Return current config with secrets masked."""
    cfg = get_config()
    raw = cfg.model_dump()
    return JSONResponse(_mask_secrets(raw))


@router.put("")
async def update_config(body: dict) -> JSONResponse:
    """
    Replace config with the provided dict and write to config.yml.
    Secrets (webhook_secret, api_key) in the body are only applied if non-empty
    and non-masked — otherwise existing values are preserved.
    """
    current = get_config()
    current_raw = current.model_dump()

    # Merge: deep-update current with body, preserving secrets if body sends ****
    merged = _deep_merge(current_raw, body)

    try:
        new_cfg = AppConfig.model_validate(merged)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    save_config(new_cfg, CONFIG_PATH)
    reload_config(CONFIG_PATH)
    if _prompt_engine is not None:
        _prompt_engine.invalidate_cache()
    return JSONResponse(_mask_secrets(new_cfg.model_dump()))


@router.post("/reload")
async def reload_config_endpoint() -> JSONResponse:
    """Hot-reload config.yml without restarting the process."""
    cfg = reload_config(CONFIG_PATH)
    if _prompt_engine is not None:
        _prompt_engine.invalidate_cache()
    return JSONResponse(
        {
            "status": "ok",
            "providers": len(cfg.providers),
            "review_targets": len(cfg.review_targets),
        }
    )


@router.get("/schema")
async def get_schema() -> JSONResponse:
    """Return JSON Schema for AppConfig (useful for UI validation)."""
    return JSONResponse(AppConfig.model_json_schema())


# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        elif val == _MASKED:
            pass  # keep existing secret
        else:
            result[key] = val
    return result
