"""
Shared in-memory dedup cache for all queue backends.

Extracted from QueueManager / ValkeyQueueManager / KafkaQueueManager
to eliminate tripled logic (STYLE-1).

Key: (project_id_str, mr_iid, diff_hash)
Value: monotonic timestamp of when the key was first seen
TTL: entries expire after `ttl` seconds (default 1 h)
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600.0  # 1 hour


class DedupCache:
    """Thread-safe* in-memory dedup cache with TTL expiry.

    (*) asyncio single-threaded — no locks needed for dict ops.
    """

    def __init__(self, ttl: float = _DEFAULT_TTL) -> None:
        self._seen: dict[tuple, float] = {}
        self._ttl = ttl

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def is_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> bool:
        """Return True if this (project, MR, diff) was seen within the TTL window."""
        if not diff_hash:
            return False
        key = (str(project_id), mr_iid, diff_hash)
        return self._check(key)

    def mark(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        """Record that this (project, MR, diff) has been reviewed."""
        key = (str(project_id), mr_iid, diff_hash)
        self._seen[key] = time.monotonic()

    def seed(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        """Seed a key without overwriting if already present (used on DB restore)."""
        key = (str(project_id), mr_iid, diff_hash)
        self._seen.setdefault(key, time.monotonic())

    async def load_from_db(self, db) -> int:  # type: ignore[no-untyped-def]
        """Restore cache from recent DB records (last 7 days).

        Returns the number of hashes loaded. Non-fatal on DB error.
        """
        try:
            rows = await db.list_diff_hashes(hours=168)
            for project_id, mr_iid, diff_hash in rows:
                self.seed(project_id, mr_iid, diff_hash)
            logger.info("DedupCache: seeded %d hashes from DB", len(rows))
            return len(rows)
        except Exception:
            logger.exception("DedupCache: failed to seed from DB")
            return 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self, key: tuple) -> bool:
        ts = self._seen.get(key)
        if ts is None:
            return False
        if time.monotonic() - ts > self._ttl:
            del self._seen[key]
            return False
        return True

    def __len__(self) -> int:
        return len(self._seen)
