"""
SQLite storage via aiosqlite.

Tables:
  reviews  — one row per completed review (posted / skipped / error)

Usage:
  db = Database("data/reviews.db")
  await db.init()
  review_id = await db.save_review(record)
  rows, total = await db.list_reviews(limit=20, offset=0)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_REVIEWS = """
CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT    NOT NULL,
    mr_iid          INTEGER NOT NULL,
    mr_title        TEXT    DEFAULT '',
    mr_url          TEXT    DEFAULT '',
    author          TEXT    DEFAULT '',
    source_branch   TEXT    DEFAULT '',
    target_branch   TEXT    DEFAULT '',
    diff_hash       TEXT    DEFAULT '',
    prompt_names    TEXT    DEFAULT '[]',   -- JSON array
    review_text     TEXT    DEFAULT '',
    status          TEXT    NOT NULL,       -- posted | skipped | error | dry_run
    skip_reason     TEXT    DEFAULT '',
    auto_approved   INTEGER DEFAULT 0,
    inline_count    INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_reviews_project   ON reviews(project_id);
CREATE INDEX IF NOT EXISTS idx_reviews_mr        ON reviews(project_id, mr_iid);
CREATE INDEX IF NOT EXISTS idx_reviews_created   ON reviews(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_status    ON reviews(status);
"""


@dataclass
class ReviewRecord:
    project_id: str
    mr_iid: int
    status: str  # posted | skipped | error | dry_run
    mr_title: str = ""
    mr_url: str = ""
    author: str = ""
    source_branch: str = ""
    target_branch: str = ""
    diff_hash: str = ""
    prompt_names: list[str] = field(default_factory=list)
    review_text: str = ""
    skip_reason: str = ""
    auto_approved: bool = False
    inline_count: int = 0  # number of inline GitLab discussion comments posted
    id: int = 0
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: str | Path = "data/reviews.db") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_CREATE_REVIEWS)
        await self._run_migrations()
        await self._db.commit()
        logger.info("Database initialised at %s", self._path)

    async def _run_migrations(self) -> None:
        """Apply additive schema migrations — safe to run on every startup."""
        assert self._db is not None
        _migrations = [
            # v0.5: inline comment count column
            "ALTER TABLE reviews ADD COLUMN inline_count INTEGER DEFAULT 0",
        ]
        cur = await self._db.execute("PRAGMA table_info(reviews)")
        existing_cols = {row[1] for row in await cur.fetchall()}
        for stmt in _migrations:
            # Extract column name from ALTER TABLE … ADD COLUMN <name> …
            col = stmt.split("ADD COLUMN")[1].strip().split()[0]
            if col not in existing_cols:
                await self._db.execute(stmt)
                logger.info("DB migration applied: %s", stmt)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save_review(self, rec: ReviewRecord) -> int:
        assert self._db is not None
        cursor = await self._db.execute(
            """INSERT INTO reviews
               (project_id, mr_iid, mr_title, mr_url, author,
                source_branch, target_branch, diff_hash, prompt_names,
                review_text, status, skip_reason, auto_approved,
                inline_count, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(rec.project_id),
                rec.mr_iid,
                rec.mr_title,
                rec.mr_url,
                rec.author,
                rec.source_branch,
                rec.target_branch,
                rec.diff_hash,
                json.dumps(rec.prompt_names),
                rec.review_text,
                rec.status,
                rec.skip_reason,
                int(rec.auto_approved),
                rec.inline_count,
                rec.created_at,
            ),
        )
        await self._db.commit()
        rec.id = cursor.lastrowid or 0
        logger.debug(
            "Saved review id=%d project=%s MR!%d status=%s inline=%d",
            rec.id,
            rec.project_id,
            rec.mr_iid,
            rec.status,
            rec.inline_count,
        )
        return rec.id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_review(self, review_id: int) -> ReviewRecord | None:
        assert self._db is not None
        async with self._db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def list_reviews(
        self,
        project_id: str = "",
        status: str = "",
        author: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ReviewRecord], int]:
        """Returns (records, total_count)."""
        assert self._db is not None
        conditions: list[str] = []
        params: list[Any] = []

        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if author:
            conditions.append("author = ?")
            params.append(author)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        # where clause is built from a hardcoded whitelist — not user input
        count_sql = f"SELECT COUNT(*) FROM reviews {where}"  # noqa: S608
        list_sql = (
            f"SELECT * FROM reviews {where}"  # noqa: S608
            " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )

        async with self._db.execute(count_sql, params) as cur:
            total = (await cur.fetchone())[0]

        async with self._db.execute(list_sql, params + [limit, offset]) as cur:
            rows = await cur.fetchall()

        return [_row_to_record(r) for r in rows], total

    async def stats(self) -> dict[str, Any]:
        """Aggregated stats for dashboard."""
        assert self._db is not None
        async with self._db.execute(
            """SELECT
               COUNT(*)                                    AS total,
               SUM(status = 'posted')                     AS posted,
               SUM(status = 'skipped')                    AS skipped,
               SUM(status = 'error')                      AS errors,
               SUM(auto_approved)                         AS auto_approved,
               MAX(created_at)                            AS last_review
               FROM reviews"""
        ) as cur:
            row = await cur.fetchone()

        return dict(row) if row else {}

    async def recent(self, limit: int = 10) -> list[ReviewRecord]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    # ---------------------------------------------------------------------------

    async def list_diff_hashes(self, hours: int = 168) -> list[tuple[str, int, str]]:
        """
        Return (project_id, mr_iid, diff_hash) for reviews created within
        the last `hours` hours that have a non-empty diff_hash.
        Used to restore the in-memory dedup cache on startup.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute(
                """
                SELECT project_id, mr_iid, diff_hash
                FROM reviews
                WHERE diff_hash != '' AND diff_hash IS NOT NULL
                  AND created_at > ?
                """,
                (cutoff.isoformat(),),
            )
            rows = await cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


def _row_to_record(row: aiosqlite.Row) -> ReviewRecord:
    d = dict(row)
    d["prompt_names"] = json.loads(d.get("prompt_names") or "[]")
    d["auto_approved"] = bool(d.get("auto_approved", 0))
    return ReviewRecord(**{k: v for k, v in d.items() if k in ReviewRecord.__dataclass_fields__})
