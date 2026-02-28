"""Health check — GET /health"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])

_start_time = time.time()
_db = None
_queue = None


def set_database(db) -> None:  # type: ignore[no-untyped-def]
    global _db
    _db = db


def set_queue_manager(q) -> None:  # type: ignore[no-untyped-def]
    global _queue
    _queue = q


@router.get("/health")
async def health_check() -> JSONResponse:
    checks: dict[str, dict] = {}

    # DB check
    if _db is None:
        checks["db"] = {"status": "unavailable"}
    else:
        try:
            await _db.stats()
            checks["db"] = {"status": "ok"}
        except Exception as exc:
            checks["db"] = {"status": "error", "detail": str(exc)}

    # Queue check
    if _queue is None:
        checks["queue"] = {"status": "unavailable"}
    else:
        try:
            status = _queue.status()
            checks["queue"] = {"status": "ok", **status}
        except Exception as exc:
            checks["queue"] = {"status": "error", "detail": str(exc)}

    # Config check
    from ..config import get_config

    try:
        cfg = get_config()
        active = cfg.active_provider()
        checks["config"] = {
            "status": "ok",
            "providers": len(cfg.providers),
            "active_provider": active.name if active else None,
            "targets": len(cfg.review_targets),
        }
    except Exception as exc:
        checks["config"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    uptime_secs = int(time.time() - _start_time)

    return JSONResponse(
        content={
            "status": overall,
            "uptime_seconds": uptime_secs,
            "checks": checks,
        },
        status_code=200 if overall == "ok" else 503,
    )
