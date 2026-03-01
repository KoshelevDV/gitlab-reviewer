"""Tests for QueueManager — enqueue, dedup, concurrency, status."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.queue_manager import QueueManager, ReviewJob


@pytest.fixture
async def qm():
    q = QueueManager(max_concurrent=2, max_size=5)
    yield q
    await q.drain()


class TestEnqueue:
    async def test_enqueue_returns_true(self, qm):
        job = ReviewJob(project_id=1, mr_iid=1)
        result = await qm.enqueue(job)
        assert result is True

    async def test_enqueue_assigns_incrementing_id(self, qm):
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=2)
        await qm.enqueue(j1)
        await qm.enqueue(j2)
        assert j2.id > j1.id

    async def test_full_queue_returns_false(self):
        q = QueueManager(max_concurrent=1, max_size=1)
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=2)
        await q.enqueue(j1)
        result = await q.enqueue(j2)
        assert result is False
        await q.drain()

    async def test_enqueue_increments_pending(self, qm):
        await qm.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await qm.enqueue(ReviewJob(project_id=1, mr_iid=2))
        assert qm.status()["pending"] == 2


class TestDedup:
    async def test_same_diff_hash_skipped(self, qm):
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="abc")
        j2 = ReviewJob(project_id=1, mr_iid=1, diff_hash="abc")
        await qm.enqueue(j1)
        qm.mark_seen(1, 1, "abc")
        result = await qm.enqueue(j2)
        assert result is False

    async def test_different_diff_hash_not_deduped(self, qm):
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash1")
        j2 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash2")
        await qm.enqueue(j1)
        qm.mark_seen(1, 1, "hash1")
        result = await qm.enqueue(j2)
        assert result is True

    async def test_same_hash_different_mr_not_deduped(self, qm):
        """Same diff hash on different MRs should both be reviewed."""
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="same")
        j2 = ReviewJob(project_id=1, mr_iid=2, diff_hash="same")
        await qm.enqueue(j1)
        qm.mark_seen(1, 1, "same")
        result = await qm.enqueue(j2)
        assert result is True

    async def test_no_diff_hash_not_deduped(self, qm):
        """Jobs without diff_hash should always be enqueued."""
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="")
        j2 = ReviewJob(project_id=1, mr_iid=1, diff_hash="")
        r1 = await qm.enqueue(j1)
        r2 = await qm.enqueue(j2)
        assert r1 is True
        assert r2 is True


class TestWorkers:
    async def test_worker_processes_job(self):
        q = QueueManager(max_concurrent=1, max_size=10)
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job.mr_iid)

        q.start(review_fn=handler)
        await q.enqueue(ReviewJob(project_id=1, mr_iid=42))
        await asyncio.sleep(0.1)  # let worker run
        await q.drain()
        assert 42 in processed

    async def test_restart_after_drain(self):
        """Workers restarted via restart() should process new jobs normally."""
        q = QueueManager(max_concurrent=1, max_size=10)
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job.mr_iid)

        q.start(review_fn=handler)
        await q.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await asyncio.sleep(0.1)
        await q.drain()

        # Queue is now dead — restart it
        count = await q.restart()
        assert count == 1
        await q.enqueue(ReviewJob(project_id=1, mr_iid=2))
        await asyncio.sleep(0.1)
        await q.drain()

        assert 2 in processed

    async def test_restart_without_start_raises(self):
        q = QueueManager(max_concurrent=1, max_size=10)
        with pytest.raises(RuntimeError, match="start\\(\\) must be called before restart"):
            await q.restart()

    async def test_done_counter_increments(self):
        q = QueueManager(max_concurrent=1, max_size=10)
        q.start(review_fn=AsyncMock())
        await q.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await asyncio.sleep(0.1)
        await q.drain()
        assert q.status()["done"] >= 1

    async def test_error_counter_increments_on_exception(self):
        q = QueueManager(max_concurrent=1, max_size=10)

        async def failing(job):
            raise ValueError("boom")

        q.start(review_fn=failing)
        await q.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await asyncio.sleep(0.1)
        await q.drain()
        assert q.status()["errors"] >= 1

    async def test_max_concurrent_respected(self):
        """With max_concurrent=1, jobs run sequentially."""
        q = QueueManager(max_concurrent=1, max_size=10)
        order = []
        lock = asyncio.Lock()

        async def handler(job):
            assert not lock.locked(), "Two jobs ran simultaneously!"
            async with lock:
                order.append(job.mr_iid)
                await asyncio.sleep(0.02)

        q.start(review_fn=handler)
        for i in range(3):
            await q.enqueue(ReviewJob(project_id=1, mr_iid=i))
        await asyncio.sleep(0.3)
        await q.drain()
        assert len(order) == 3


class TestStatus:
    async def test_initial_status(self, qm):
        s = qm.status()
        assert s["pending"] == 0
        assert s["active"] == 0
        assert s["done"] == 0
        assert s["errors"] == 0

    async def test_status_has_required_keys(self, qm):
        s = qm.status()
        for key in ("pending", "active", "done", "errors", "max_concurrent", "queue_maxsize"):
            assert key in s
