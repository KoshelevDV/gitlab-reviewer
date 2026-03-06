"""Memory API — /api/v1/memory

Exposes Qdrant-backed memory store for inspection and management via Web UI.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
    if _memory_store is None or not await _memory_store.is_available():
        return _unavailable()

    try:
        _, qm = _try_import_qdrant()
        if qm is None:
            return _unavailable()

        client = await _memory_store._get_client()
        if client is None:
            return _unavailable()

        await _memory_store._ensure_collection()

        # Scroll through all points collecting project_ids
        project_ids: set[str] = set()
        offset = None
        while True:
            results, next_offset = await client.scroll(
                collection_name=_memory_store._collection,
                limit=100,
                offset=offset,
                with_payload=["project_id"],
                with_vectors=False,
            )
            for point in results:
                pid = (point.payload or {}).get("project_id")
                if pid:
                    project_ids.add(str(pid))
            if next_offset is None:
                break
            offset = next_offset

        return JSONResponse(sorted(project_ids))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("list_projects error: %s", exc)
        return _unavailable()


@router.get("")
async def list_patterns(
    project_id: str = "",
    category: str = "",
    limit: int = 50,
) -> JSONResponse:
    """List memory patterns from Qdrant, with optional filters."""
    if _memory_store is None or not await _memory_store.is_available():
        return _unavailable()

    try:
        _, qm = _try_import_qdrant()
        if qm is None:
            return _unavailable()

        client = await _memory_store._get_client()
        if client is None:
            return _unavailable()

        await _memory_store._ensure_collection()

        # Build filter conditions
        conditions = []
        if project_id:
            conditions.append(
                qm.FieldCondition(
                    key="project_id",
                    match=qm.MatchValue(value=project_id),
                )
            )
        if category:
            conditions.append(
                qm.FieldCondition(
                    key="category",
                    match=qm.MatchValue(value=category),
                )
            )

        scroll_filter = qm.Filter(must=conditions) if conditions else None

        results, _ = await client.scroll(
            collection_name=_memory_store._collection,
            limit=min(limit, 500),
            with_payload=True,
            with_vectors=False,
            scroll_filter=scroll_filter,
        )

        items = []
        for point in results:
            payload = dict(point.payload or {})
            items.append(
                {
                    "id": str(point.id),
                    "project_id": payload.pop("project_id", ""),
                    "category": payload.pop("category", ""),
                    "content": payload.pop("content", ""),
                    "metadata": payload,
                    "score": None,
                }
            )

        return JSONResponse({"items": items, "total": len(items)})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("list_patterns error: %s", exc)
        return _unavailable()


@router.delete("/{point_id}")
async def delete_pattern(point_id: str) -> JSONResponse:
    """Delete a memory pattern by its Qdrant point id."""
    if _memory_store is None or not await _memory_store.is_available():
        return _unavailable()

    try:
        _, qm = _try_import_qdrant()
        if qm is None:
            return _unavailable()

        client = await _memory_store._get_client()
        if client is None:
            return _unavailable()

        await _memory_store._ensure_collection()

        # Check that the point exists first
        points = await client.retrieve(
            collection_name=_memory_store._collection,
            ids=[point_id],
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            return JSONResponse({"error": "not found"}, status_code=404)

        await client.delete(
            collection_name=_memory_store._collection,
            points_selector=qm.PointIdsList(points=[point_id]),
        )
        return JSONResponse({"status": "deleted"})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("delete_pattern error: %s", exc)
        return _unavailable()


def _try_import_qdrant():  # type: ignore[return]
    try:
        from qdrant_client import AsyncQdrantClient  # type: ignore[import-not-found]
        from qdrant_client.http import models as qm  # type: ignore[import-not-found]
        return AsyncQdrantClient, qm
    except ImportError:
        return None, None
