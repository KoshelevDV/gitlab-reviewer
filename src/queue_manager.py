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
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import metrics as _metrics

if TYPE_CHECKING:
    from .db import Database

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
        self._review_fn: Callable[[ReviewJob], Coroutine] | None = None
        # Simple in-memory dedup: (project_id, mr_iid, diff_hash) -> monotonic ts
        self._seen: dict[tuple, float] = {}
        self._dedup_ttl = 3600.0
        # Latest-wins debounce: (project_id, mr_iid) -> latest job.id
        # Used to supersede older queued jobs when a newer push arrives for the same MR
        self._latest_job_id: dict[tuple[str, int], int] = {}

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
                job.project_id,
                job.mr_iid,
            )
            _metrics.queue_rejected_total.inc()
            return False

        try:
            self._job_counter += 1
            job.id = self._job_counter
            self._queue.put_nowait(job)
            self._pending += 1
            _metrics.queue_enqueued_total.inc()
            _metrics.queue_pending.set(self._pending)
            # Track latest job per MR for debounce / supersede logic
            mr_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
            self._latest_job_id[mr_key] = job.id
            logger.info(
                "Enqueued job #%d: project=%s MR!%d (queue depth=%d)",
                job.id,
                job.project_id,
                job.mr_iid,
                self._pending,
            )
            return True
        except asyncio.QueueFull:
            logger.warning(
                "Queue full (max_size=%d), dropping job project=%s MR!%d",
                self._queue.maxsize,
                job.project_id,
                job.mr_iid,
            )
            _metrics.queue_rejected_total.inc()
            return False

    def start(
        self,
        review_fn: Callable[[ReviewJob], Coroutine],
        num_workers: int | None = None,
    ) -> None:
        """Start worker coroutines. Must be called from async context (e.g. app startup)."""
        self._review_fn = review_fn
        count = num_workers or self._max_concurrent
        for i in range(count):
            task = asyncio.ensure_future(self._worker(review_fn))
            task.set_name(f"reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Started %d review worker(s)", count)

    async def restart(self, num_workers: int | None = None) -> int:
        """Restart workers after drain. Returns number of workers started."""
        if self._review_fn is None:
            raise RuntimeError("start() must be called before restart()")
        count = num_workers or self._max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        for i in range(count):
            task = asyncio.ensure_future(self._worker(self._review_fn))
            task.set_name(f"reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Restarted %d review workers", count)
        return count

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
            _metrics.queue_pending.set(self._pending)
            _metrics.queue_active.set(self._active)
            async with self._semaphore:
                # Drop superseded jobs silently — a newer job for this MR is pending
                if self.is_superseded(job):
                    logger.info(
                        "Dropping superseded job #%d: project=%s MR!%d (newer job queued)",
                        job.id,
                        job.project_id,
                        job.mr_iid,
                    )
                    _metrics.jobs_superseded_total.inc()
                    self._active -= 1
                    _metrics.queue_active.set(self._active)
                    self._done += 1
                    self._queue.task_done()
                    continue
                try:
                    logger.info(
                        "Worker starting job #%d: project=%s MR!%d",
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
                    self._queue.task_done()

    def _is_seen(self, key: tuple) -> bool:
        ts = self._seen.get(key)
        if ts is None:
            return False
        if time.monotonic() - ts > self._dedup_ttl:
            del self._seen[key]
            return False
        return True

    async def load_seen_from_db(self, db: Database) -> int:  # type: ignore[name-defined]
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

    def is_already_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> bool:
        """Return True if this (project, MR, diff) has been reviewed recently."""
        if not diff_hash:
            return False
        key = (str(project_id), mr_iid, diff_hash)
        return self._is_seen(key)

    def is_superseded(self, job: ReviewJob) -> bool:
        """
        Return True if a *newer* job for the same MR has been enqueued since
        this job was created.  Used by the cooldown debounce logic — if the
        current job is superseded, skip it silently; a fresher job will handle
        the review when the cooldown window expires.
        """
        mr_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
        latest_id = self._latest_job_id.get(mr_key, job.id)
        return job.id < latest_id

    def mark_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        key = (str(project_id), mr_iid, diff_hash)
        self._seen[key] = time.monotonic()
