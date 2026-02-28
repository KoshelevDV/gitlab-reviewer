"""Serve Web UI static files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter(tags=["ui"])


def mount_ui(app) -> None:  # type: ignore[no-untyped-def]
    """Mount static files and add UI redirect."""
    app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui_static")

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def ui_root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/", include_in_schema=False)
    async def root_redirect():  # type: ignore[return]
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/ui/")
