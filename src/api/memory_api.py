"""Memory API — /api/v1/memory

Exposes Qdrant-backed memory store for inspection and management via Web UI.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])

_memory_store = None


def set_memory_store(store) -> None:  # type: ignore[no-untyped-def]
    global _memory_store
    _memory_store = store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unavailable() -> JSONResponse:
    return JSONResponse({"error": "memory not available"}, status_code=503)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects() -> JSONResponse:
    """Return distinct project_ids that have patterns stored in Qdrant."""
    store = _memory_store  # snapshot — защита от гонки
    if store is None or not await store.is_available():
        return _unavailable()
    try:
        projects = await store.list_projects()
        return JSONResponse(projects)
    except Exception as exc:
        logger.debug("list_projects error: %s", exc)
        return _unavailable()


@router.get("")
async def list_patterns(
    project_id: str = "",
    category: str = "",
    limit: int = 50,
) -> JSONResponse:
    """List memory patterns from Qdrant, with optional filters."""
    store = _memory_store  # snapshot — защита от гонки
    if store is None or not await store.is_available():
        return _unavailable()
    try:
        items = await store.list_patterns(project_id, category, limit)
        return JSONResponse({"items": items, "total": len(items)})
    except Exception as exc:
        logger.debug("list_patterns error: %s", exc)
        return _unavailable()


@router.delete("/{point_id}")
async def delete_pattern(point_id: str) -> JSONResponse:
    """Delete a memory pattern by its Qdrant point id."""
    store = _memory_store  # snapshot — защита от гонки
    if store is None or not await store.is_available():
        return _unavailable()
    try:
        found = await store.delete_pattern(point_id)
        if not found:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"status": "deleted"})
    except Exception as exc:
        logger.debug("delete_pattern error: %s", exc)
        return _unavailable()
