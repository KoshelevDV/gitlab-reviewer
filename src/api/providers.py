"""Providers API — /api/v1/providers"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import CONFIG_PATH, Provider, get_config, reload_config, save_config
from ..llm_client import ModelInfo, get_model_info, list_models

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


@router.get("")
async def list_providers() -> JSONResponse:
    cfg = get_config()
    return JSONResponse([p.model_dump() for p in cfg.providers])


@router.post("")
async def add_provider(body: Provider) -> JSONResponse:
    cfg = get_config()
    if any(p.id == body.id for p in cfg.providers):
        raise HTTPException(status_code=409, detail=f"Provider '{body.id}' already exists")
    cfg.providers.append(body)
    save_config(cfg, CONFIG_PATH)
    reload_config(CONFIG_PATH)
    return JSONResponse({"status": "created", "id": body.id}, status_code=201)


@router.put("/{provider_id}")
async def update_provider(provider_id: str, body: Provider) -> JSONResponse:
    cfg = get_config()
    for i, p in enumerate(cfg.providers):
        if p.id == provider_id:
            cfg.providers[i] = body
            save_config(cfg, CONFIG_PATH)
            reload_config(CONFIG_PATH)
            return JSONResponse({"status": "updated"})
    raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str) -> JSONResponse:
    cfg = get_config()
    before = len(cfg.providers)
    cfg.providers = [p for p in cfg.providers if p.id != provider_id]
    if len(cfg.providers) == before:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    save_config(cfg, CONFIG_PATH)
    reload_config(CONFIG_PATH)
    return JSONResponse({"status": "deleted"})


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str) -> JSONResponse:
    """Test connectivity to a provider and return basic info."""
    cfg = get_config()
    provider = next((p for p in cfg.providers if p.id == provider_id), None)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    headers = {}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=8) as client:
            base = provider.url.rstrip("/")
            if provider.type == "ollama":
                resp = await client.get(f"{base}/api/version")
                resp.raise_for_status()
                version = resp.json().get("version", "unknown")
            else:
                resp = await client.get(f"{base}/v1/models")
                resp.raise_for_status()
                version = f"{len(resp.json().get('data', []))} model(s) available"
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=200)

    return JSONResponse({"ok": True, "version": version})


@router.get("/{provider_id}/models")
async def get_models(provider_id: str) -> JSONResponse:
    """List available models from a provider."""
    cfg = get_config()
    provider = next((p for p in cfg.providers if p.id == provider_id), None)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    models: list[ModelInfo] = await list_models(provider.url, provider.type.value, provider.api_key)
    return JSONResponse([{"id": m.id, "context_length": m.context_length} for m in models])


@router.get("/{provider_id}/models/{model_name:path}/info")
async def get_model_info_endpoint(provider_id: str, model_name: str) -> JSONResponse:
    """Get detailed info (context length, params) for a specific model."""
    cfg = get_config()
    provider = next((p for p in cfg.providers if p.id == provider_id), None)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    info = await get_model_info(provider.url, model_name, provider.type.value, provider.api_key)
    return JSONResponse(
        {
            "id": info.id,
            "context_length": info.context_length,
            "params": info.params,
        }
    )
