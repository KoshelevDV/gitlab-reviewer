"""Review Targets API — /api/v1/targets

Addresses targets by composite key "{type}:{id}" (e.g. "project:42", "all:").
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import CONFIG_PATH, ReviewTarget, get_config, reload_config, save_config

router = APIRouter(prefix="/api/v1/targets", tags=["targets"])


def _key(t: ReviewTarget) -> str:
    return f"{t.type}:{t.id}"


@router.get("")
async def list_targets() -> JSONResponse:
    cfg = get_config()
    return JSONResponse([
        {**t.model_dump(), "_key": _key(t)}
        for t in cfg.review_targets
    ])


@router.post("")
async def add_target(body: ReviewTarget) -> JSONResponse:
    cfg = get_config()
    key = _key(body)
    if any(_key(t) == key for t in cfg.review_targets):
        raise HTTPException(
            status_code=409, detail=f"Target '{key}' already exists"
        )
    cfg.review_targets.append(body)
    save_config(cfg, CONFIG_PATH)
    reload_config(CONFIG_PATH)
    return JSONResponse({"status": "created", "key": key}, status_code=201)


@router.put("/{target_key:path}")
async def update_target(target_key: str, body: ReviewTarget) -> JSONResponse:
    cfg = get_config()
    for i, t in enumerate(cfg.review_targets):
        if _key(t) == target_key:
            cfg.review_targets[i] = body
            save_config(cfg, CONFIG_PATH)
            reload_config(CONFIG_PATH)
            return JSONResponse({"status": "updated", "key": _key(body)})
    raise HTTPException(status_code=404, detail=f"Target '{target_key}' not found")


@router.delete("/{target_key:path}")
async def delete_target(target_key: str) -> JSONResponse:
    cfg = get_config()
    before = len(cfg.review_targets)
    cfg.review_targets = [t for t in cfg.review_targets if _key(t) != target_key]
    if len(cfg.review_targets) == before:
        raise HTTPException(status_code=404, detail=f"Target '{target_key}' not found")
    save_config(cfg, CONFIG_PATH)
    reload_config(CONFIG_PATH)
    return JSONResponse({"status": "deleted"})
