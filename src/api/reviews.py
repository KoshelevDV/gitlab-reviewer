"""Reviews API — /api/v1/reviews"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

_db = None


def set_database(db) -> None:  # type: ignore[no-untyped-def]
    global _db
    _db = db


@router.get("")
async def list_reviews(
    project_id: str = "",
    status: str = "",
    author: str = "",
    limit: int = 20,
    offset: int = 0,
) -> JSONResponse:
    if _db is None:
        return JSONResponse({"items": [], "total": 0})
    if limit > 100:
        limit = 100
    records, total = await _db.list_reviews(
        project_id=project_id, status=status, author=author,
        limit=limit, offset=offset,
    )
    return JSONResponse({
        "items": [_serialize(r) for r in records],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.get("/stats")
async def review_stats() -> JSONResponse:
    if _db is None:
        return JSONResponse({})
    return JSONResponse(await _db.stats())


@router.get("/recent")
async def recent_reviews(limit: int = 10) -> JSONResponse:
    if _db is None:
        return JSONResponse([])
    records = await _db.recent(min(limit, 50))
    return JSONResponse([_serialize(r) for r in records])


@router.get("/{review_id}")
async def get_review(review_id: int) -> JSONResponse:
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    rec = await _db.get_review(review_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return JSONResponse(_serialize(rec))


def _serialize(rec) -> dict:  # type: ignore[no-untyped-def]
    from dataclasses import asdict
    d = asdict(rec)
    d["auto_approved"] = bool(d["auto_approved"])
    # Trim review_text for list views (full text available via GET /{id})
    return d
