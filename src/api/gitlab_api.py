"""GitLab browse API — /api/v1/gitlab"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import get_config
from ..gitlab_client import GitLabClient

router = APIRouter(prefix="/api/v1/gitlab", tags=["gitlab"])


def _make_client() -> GitLabClient:
    cfg = get_config()
    token = cfg.gitlab_token
    if not token and cfg.gitlab.auth_type == "token":
        raise HTTPException(status_code=400, detail="GitLab token not configured")
    return GitLabClient(cfg.gitlab.url, token)


@router.post("/test")
async def test_gitlab_connection() -> JSONResponse:
    cfg = get_config()
    token = cfg.gitlab_token
    if not token:
        return JSONResponse({"ok": False, "error": "No token configured"})
    client = GitLabClient(cfg.gitlab.url, token)
    try:
        info = await client.test_connection()
        return JSONResponse({
            "ok": info.ok,
            "version": info.version,
            "username": info.username,
            "error": info.error,
        })
    finally:
        await client.aclose()


@router.get("/groups")
async def list_groups(search: str = "") -> JSONResponse:
    client = _make_client()
    try:
        groups = await client.list_groups(search=search)
        return JSONResponse([
            {"id": g.id, "name": g.name, "full_path": g.full_path}
            for g in groups
        ])
    finally:
        await client.aclose()


@router.get("/projects")
async def list_projects(search: str = "") -> JSONResponse:
    client = _make_client()
    try:
        projects = await client.list_projects(search=search)
        return JSONResponse([
            {
                "id": p.id,
                "name": p.name,
                "path": p.path_with_namespace,
                "default_branch": p.default_branch,
            }
            for p in projects
        ])
    finally:
        await client.aclose()


@router.get("/projects/{project_id}/branches")
async def list_branches(project_id: int) -> JSONResponse:
    client = _make_client()
    try:
        branches = await client.list_branches(project_id)
        return JSONResponse([
            {"name": b.name, "protected": b.protected, "default": b.default}
            for b in branches
        ])
    finally:
        await client.aclose()
