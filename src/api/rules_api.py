"""Rules API — /api/v1/rules

CRUD for the rules.yml automation rules file.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..rules import load_rules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])

MAX_RULES_BODY = 512 * 1024  # 512 KB — real rules.yml files are well under 10 KB


def _rules_path() -> str | None:
    """Return the current rules path (read from webhook module at call time)."""
    from ..webhook import _rules_path as rp  # noqa: PLC2701

    return rp


def _require_path() -> Path:
    rp = _rules_path()
    if not rp:
        raise HTTPException(status_code=503, detail="rules.yml path not configured")
    return Path(rp)


@router.get("")
async def get_rules() -> JSONResponse:
    """Return current rules.yml content as JSON (list of rules)."""
    rp = _rules_path()
    p = Path(rp) if rp else None

    if p is None or not p.exists():
        return JSONResponse({"rules": [], "raw_yaml": "", "count": 0})

    raw = p.read_text(encoding="utf-8")
    try:
        config = load_rules(str(p))
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "raw_yaml": raw, "count": 0}, status_code=422)

    rules_json = []
    for rule in config.rules:
        rules_json.append(
            {
                "name": rule.name,
                "condition": {
                    "if_files_match": rule.condition.if_files_match,
                    "if_author_in": rule.condition.if_author_in,
                    "if_lines_changed_gt": rule.condition.if_lines_changed_gt,
                    "if_target_branch": rule.condition.if_target_branch,
                },
                "actions": [{"type": a.type.value, "value": a.value} for a in rule.actions],
                "stop": rule.stop,
            }
        )

    return JSONResponse({"rules": rules_json, "raw_yaml": raw, "count": len(rules_json)})


@router.post("")
async def save_rules(request: Request) -> JSONResponse:
    """Accept YAML body, validate, and save as rules.yml."""
    p = _require_path()

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_RULES_BODY:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (max {MAX_RULES_BODY // 1024} KB)",
        )
    body_bytes = await request.body()
    if len(body_bytes) > MAX_RULES_BODY:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (max {MAX_RULES_BODY // 1024} KB)",
        )
    yaml_text = body_bytes.decode("utf-8")

    # Validate by parsing
    try:
        config = load_rules_from_text(yaml_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    p.write_text(yaml_text, encoding="utf-8")
    logger.info("rules.yml saved (%d rules)", len(config.rules))
    return JSONResponse({"status": "saved", "count": len(config.rules)})


@router.delete("")
async def delete_rules() -> JSONResponse:
    """Delete rules.yml (reset to no rules)."""
    rp = _rules_path()
    if not rp:
        raise HTTPException(status_code=503, detail="rules.yml path not configured")

    p = Path(rp)
    if p.exists():
        p.unlink()
        logger.info("rules.yml deleted")
        return JSONResponse({"status": "deleted"})
    return JSONResponse({"status": "not_found"})


@router.get("/validate")
async def validate_rules(yaml_param: str = Query(default="", alias="yaml")) -> JSONResponse:
    """Validate URL-encoded YAML. Returns {valid: bool, error: str|null, count: int}."""
    yaml_text = unquote(yaml_param)
    if not yaml_text.strip():
        return JSONResponse({"valid": True, "error": None, "count": 0})

    try:
        config = load_rules_from_text(yaml_text)
        return JSONResponse({"valid": True, "error": None, "count": len(config.rules)})
    except ValueError as exc:
        return JSONResponse({"valid": False, "error": str(exc), "count": 0})


@router.post("/validate")
async def validate_rules_post(request: Request) -> JSONResponse:
    """Validate YAML rules body (POST version for large configs)."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_RULES_BODY:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (max {MAX_RULES_BODY // 1024} KB)",
        )
    body_bytes = await request.body()
    if len(body_bytes) > MAX_RULES_BODY:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (max {MAX_RULES_BODY // 1024} KB)",
        )
    yaml_text = body_bytes.decode("utf-8")
    if not yaml_text.strip():
        return JSONResponse({"valid": True, "error": None, "count": 0})
    try:
        config = load_rules_from_text(yaml_text)
        return JSONResponse({"valid": True, "error": None, "count": len(config.rules)})
    except (ValueError, Exception) as e:
        return JSONResponse({"valid": False, "error": str(e), "count": 0})


# ──────────────────────────────────────────────────────────────────────────────
# Internal helper: parse YAML from string (not file)
# ──────────────────────────────────────────────────────────────────────────────


def load_rules_from_text(yaml_text: str):  # type: ignore[return]
    """Parse rules from a YAML string. Raises ValueError on invalid input."""
    # Write to a temp file so load_rules can reuse the existing file-based logic
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, encoding="utf-8") as f:
        f.write(yaml_text)
        tmp_path = f.name

    try:
        return load_rules(tmp_path)
    finally:
        os.unlink(tmp_path)
