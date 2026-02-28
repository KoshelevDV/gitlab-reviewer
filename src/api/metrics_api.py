"""Metrics API — GET /metrics (Prometheus format)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ..metrics import render_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
