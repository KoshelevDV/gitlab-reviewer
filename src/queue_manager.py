"""
Review Queue — asyncio.Queue + Semaphore (in-memory backend).

Design:
  - Webhook handler calls enqueue() and returns immediately.
  - N worker coroutines drain the queue, each guarded by a Semaphore
    so no more than max_concurrent reviews run at the same time.
  - Dedup: before enqueue, check (project_id, mr_iid, diff_hash) in cache.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class ReviewJob:
    project_id: int | str
    mr_iid: int
    event_action: str = "open"
    diff_hash: str = ""
    id: int = field(default=0)


class QueueManager:
    def __init__(self, max_concurrent: int = 3, max_size: int = 100) -> None:
        self._queue: asyncio.Queue[ReviewJob] = asyncio.Queue(maxsize=max_size)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._pending = 0
        self._active = 0
        self._done = 0
        self._errors = 0
        self._job_counter = 0
        self._workers: list[asyncio.Task] = []
        # Simple in-memory dedup: (project_id, mr_iid, diff_hash) -> True
        self._seen: dict[tuple, float] = {}
        self._dedup_ttl = 3600.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, job: ReviewJob) -> bool:
        """
        Add a job to the queue.
        Returns True if enqueued, False if deduped or queue full.
        """
        key = (str(job.project_id), job.mr_iid, job.diff_hash)
        if job.diff_hash and self._is_seen(key):
            logger.info(
                "Dedup: skipping project=%s MR!%d (diff hash already seen)",
                job.project_id, job.mr_iid,
            )
            return False

        try:
            self._job_counter += 1
            job.id = self._job_counter
            self._queue.put_nowait(job)
            self._pending += 1
            logger.info(
                "Enqueued job #%d: project=%s MR!%d (queue depth=%d)",
                job.id, job.project_id, job.mr_iid, self._pending,
            )
            return True
        except asyncio.QueueFull:
            logger.warning(
                "Queue full (max_size=%d), dropping job project=%s MR!%d",
                self._queue.maxsize, job.project_id, job.mr_iid,
            )
            return False

    def start(
        self,
        review_fn: Callable[[ReviewJob], Coroutine],
        num_workers: int | None = None,
    ) -> None:
        """Start worker coroutines. Must be called from async context (e.g. app startup)."""
        count = num_workers or self._max_concurrent
        for i in range(count):
            task = asyncio.ensure_future(
                self._worker(review_fn)
            )
            task.set_name(f"reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Started %d review worker(s)", count)

    async def drain(self) -> None:
        """Cancel all workers and wait for in-flight jobs to finish."""
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def status(self) -> dict:
        return {
            "pending": self._pending,
            "active": self._active,
            "done": self._done,
            "errors": self._errors,
            "max_concurrent": self._max_concurrent,
            "queue_maxsize": self._queue.maxsize,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker(self, review_fn: Callable[[ReviewJob], Coroutine]) -> None:
        while True:
            job = await self._queue.get()
            self._pending -= 1
            self._active += 1
            async with self._semaphore:
                try:
                    logger.info(
                        "Worker starting job #%d: project=%s MR!%d",
                        job.id, job.project_id, job.mr_iid,
                    )
                    await review_fn(job)
                    self._done += 1
                except Exception:
                    self._errors += 1
                    logger.exception(
                        "Worker error on job #%d: project=%s MR!%d",
                        job.id, job.project_id, job.mr_iid,
                    )
                finally:
                    self._active -= 1
                    self._queue.task_done()

    def _is_seen(self, key: tuple) -> bool:
        import time
        ts = self._seen.get(key)
        if ts is None:
            return False
        if time.monotonic() - ts > self._dedup_ttl:
            del self._seen[key]
            return False
        return True

    async def load_seen_from_db(self, db: "Database") -> int:  # type: ignore[name-defined]
        """
        Restore dedup cache from the last 7 days of DB records on startup.
        Prevents re-reviewing the same MR diff after a service restart.
        Returns the number of hashes loaded.
        """
        try:
            rows = await db.list_diff_hashes(hours=168)
            now = time.monotonic()
            for project_id, mr_iid, diff_hash in rows:
                key = (str(project_id), mr_iid, diff_hash)
                self._seen.setdefault(key, now)
            logger.info("Dedup cache restored: %d hashes loaded from DB", len(rows))
            return len(rows)
        except Exception:
            logger.exception("Failed to load seen hashes from DB")
            return 0

    def mark_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        import time
        key = (str(project_id), mr_iid, diff_hash)
        self._seen[key] = time.monotonic()
