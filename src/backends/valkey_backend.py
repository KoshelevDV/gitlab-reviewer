"""
Valkey (Redis-compatible) distributed review queue.

Design:
  - Queue:   Redis LPUSH for enqueue, BLPOP for workers (distributed, persistent)
  - Dedup:   In-memory dict seeded from DB on startup (same as memory backend)
             → prevents re-review after restart; cross-instance dedup via TTL keys
  - Supersede: Latest-wins via Redis counter (INCR) + per-MR key — works across instances
  - Stats:   Per-instance counters (pending approximation + active/done/errors)

Requirements:
  pip install redis>=4.2
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from .. import metrics as _metrics
from ..queue_manager import ReviewJob
from .dedup import DedupCache

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from ..db import Database

logger = logging.getLogger(__name__)

# Redis key constants
_QUEUE_KEY = "glr:queue"
_COUNTER_KEY = "glr:job_counter"
_LATEST_PREFIX = "glr:latest:"
_DEDUP_PREFIX = "glr:dedup:"

# TTL for latest-job-id keys (24 h) — long enough to cover any MR lifecycle
_LATEST_TTL_SECS = 86_400


class ValkeyQueueManager:
    """
    Valkey-backed review queue.

    Provides the same public interface as QueueManager so callers
    (main.py, reviewer.py, webhook.py) need no changes.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        max_concurrent: int = 3,
        max_size: int = 100,
        cache_ttl: int = 3600,
    ) -> None:
        self._url = url
        self._max_concurrent = max_concurrent
        self._max_size = max_size
        self._cache_ttl = float(cache_ttl)

        self._redis: Redis | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._workers: list[asyncio.Task] = []
        self._review_fn: Callable[[ReviewJob], Coroutine] | None = None

        # Per-instance counters
        self._pending = 0
        self._active = 0
        self._done = 0
        self._errors = 0

        self._dedup = DedupCache()

        # Local fallback for is_superseded (sync callers)
        self._latest_job_id: dict[tuple[str, int], int] = {}

    # ------------------------------------------------------------------
    # Redis connection (lazy, singleton per instance)
    # ------------------------------------------------------------------

    async def _conn(self) -> Redis:
        if self._redis is None:
            import redis.asyncio as aioredis  # noqa: PLC0415

            self._redis = aioredis.from_url(self._url, decode_responses=True)
        return self._redis

    # ------------------------------------------------------------------
    # Public API (mirrors QueueManager)
    # ------------------------------------------------------------------

    async def enqueue(self, job: ReviewJob) -> bool:
        """
        Enqueue a review job.
        Returns True if accepted, False if deduped or queue full.
        """
        if self._dedup.is_seen(job.project_id, job.mr_iid, job.diff_hash):
            logger.info(
                "Dedup (valkey): skipping project=%s MR!%d (diff hash already seen)",
                job.project_id,
                job.mr_iid,
            )
            _metrics.queue_rejected_total.inc()
            return False

        r = await self._conn()

        # 2. Queue depth check
        queue_len = await r.llen(_QUEUE_KEY)
        if queue_len >= self._max_size:
            logger.warning(
                "Valkey queue full (max=%d), dropping project=%s MR!%d",
                self._max_size,
                job.project_id,
                job.mr_iid,
            )
            _metrics.queue_rejected_total.inc()
            return False

        # 3. Assign globally unique job ID (atomic cross-instance counter)
        job_id = int(await r.incr(_COUNTER_KEY))
        job.id = job_id

        # 4. Track latest job per MR in Redis (enables cross-instance supersede)
        mr_redis_key = f"{_LATEST_PREFIX}{job.project_id}:{job.mr_iid}"
        await r.set(mr_redis_key, job_id, ex=_LATEST_TTL_SECS)

        # Also update local dict so sync is_superseded() works immediately
        mr_local_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
        self._latest_job_id[mr_local_key] = job_id

        # 5. Serialize job and push to the list (LPUSH → workers BRPOP from right)
        payload = json.dumps(
            {
                "project_id": str(job.project_id),
                "mr_iid": job.mr_iid,
                "event_action": job.event_action,
                "diff_hash": job.diff_hash,
                "id": job_id,
            }
        )
        await r.lpush(_QUEUE_KEY, payload)

        self._pending += 1
        _metrics.queue_enqueued_total.inc()
        _metrics.queue_pending.set(self._pending)

        logger.info(
            "Enqueued job #%d (valkey): project=%s MR!%d (queue depth≈%d)",
            job_id,
            job.project_id,
            job.mr_iid,
            self._pending,
        )
        return True

    def start(
        self,
        review_fn: Callable[[ReviewJob], Coroutine],
        num_workers: int | None = None,
    ) -> None:
        """Spawn worker coroutines. Call from async context (app startup)."""
        self._review_fn = review_fn
        count = num_workers or self._max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        for i in range(count):
            task = asyncio.ensure_future(self._worker(review_fn))
            task.set_name(f"valkey-reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Started %d Valkey review worker(s)", count)

    async def restart(self, num_workers: int | None = None) -> int:
        """Restart workers after drain. Returns number of workers started."""
        if self._review_fn is None:
            raise RuntimeError("start() must be called before restart()")
        count = num_workers or self._max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        for i in range(count):
            task = asyncio.ensure_future(self._worker(self._review_fn))
            task.set_name(f"valkey-reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Restarted %d Valkey review workers", count)
        return count

    async def drain(self) -> None:
        """Cancel all workers and close Redis connection."""
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def status(self) -> dict[str, Any]:
        return {
            "pending": self._pending,
            "active": self._active,
            "done": self._done,
            "errors": self._errors,
            "max_concurrent": self._max_concurrent,
            "queue_maxsize": self._max_size,
            "backend": "valkey",
        }

    async def load_seen_from_db(self, db: Database) -> int:  # type: ignore[name-defined]
        """Seed in-memory dedup cache from recent DB records."""
        return await self._dedup.load_from_db(db)

    def is_already_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> bool:
        """Sync check — uses in-memory DedupCache (populated at startup + mark_seen)."""
        return self._dedup.is_seen(project_id, mr_iid, diff_hash)

    def mark_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        """Record that this (project, MR, diff) was just reviewed."""
        self._dedup.mark(project_id, mr_iid, diff_hash)

    def is_superseded(self, job: ReviewJob) -> bool:
        """
        Sync supersede check using local job-id dict.

        The async worker uses _is_superseded_async() for cross-instance accuracy.
        This sync variant is called by reviewer.py for the cooldown path.
        """
        mr_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
        latest = self._latest_job_id.get(mr_key, job.id)
        return job.id < latest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _is_superseded_async(self, r: Redis, job: ReviewJob) -> bool:
        """Cross-instance supersede check via Redis."""
        mr_redis_key = f"{_LATEST_PREFIX}{job.project_id}:{job.mr_iid}"
        latest_id = await r.get(mr_redis_key)
        if latest_id is None:
            return False
        return job.id < int(latest_id)

    async def _worker(self, review_fn: Callable[[ReviewJob], Coroutine]) -> None:
        """
        Blocking-pop worker.  Each instance runs N workers concurrently.
        BLPOP with a short timeout allows graceful cancellation.
        """
        r = await self._conn()
        assert self._semaphore is not None

        while True:
            try:
                # Timeout=1 → unblocks every second so cancel() can propagate
                result = await r.brpop(_QUEUE_KEY, timeout=1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Valkey BRPOP error — retrying in 5 s")
                await asyncio.sleep(5)
                continue

            if result is None:
                continue  # timeout, loop back

            _, raw = result
            try:
                data = json.loads(raw)
                job = ReviewJob(
                    project_id=data["project_id"],
                    mr_iid=data["mr_iid"],
                    event_action=data.get("event_action", "open"),
                    diff_hash=data.get("diff_hash", ""),
                    id=data.get("id", 0),
                )
            except Exception:
                logger.exception("Malformed Valkey job payload: %.200s", raw)
                continue

            self._pending = max(0, self._pending - 1)
            self._active += 1
            _metrics.queue_pending.set(self._pending)
            _metrics.queue_active.set(self._active)

            async with self._semaphore:
                # Cross-instance supersede check
                if await self._is_superseded_async(r, job):
                    logger.info(
                        "Dropping superseded job #%d (valkey): project=%s MR!%d",
                        job.id,
                        job.project_id,
                        job.mr_iid,
                    )
                    _metrics.jobs_superseded_total.inc()
                    self._active -= 1
                    _metrics.queue_active.set(self._active)
                    self._done += 1
                    continue

                try:
                    logger.info(
                        "Worker starting job #%d (valkey): project=%s MR!%d",
                        job.id,
                        job.project_id,
                        job.mr_iid,
                    )
                    await review_fn(job)
                    self._done += 1
                except Exception:
                    self._errors += 1
                    logger.exception(
                        "Worker error on job #%d: project=%s MR!%d",
                        job.id,
                        job.project_id,
                        job.mr_iid,
                    )
                finally:
                    self._active -= 1
                    _metrics.queue_active.set(self._active)
