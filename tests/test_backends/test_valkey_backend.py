"""
Tests for ValkeyQueueManager — uses fakeredis for in-process Redis emulation.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest

from src.backends.valkey_backend import (
    _LATEST_PREFIX,
    _QUEUE_KEY,
    ValkeyQueueManager,
)
from src.queue_manager import ReviewJob

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    """Synchronous FakeRedis server shared across connections."""
    return fakeredis.FakeServer()


@pytest.fixture
async def vqm(fake_redis):
    """ValkeyQueueManager wired to a FakeAsyncRedis instance."""
    mgr = ValkeyQueueManager(
        url="redis://localhost:6379",
        max_concurrent=2,
        max_size=5,
        cache_ttl=3600,
    )
    # Inject fake Redis so no real network connection is needed
    mgr._redis = fakeredis.FakeAsyncRedis(server=fake_redis, decode_responses=True)
    yield mgr
    await mgr.drain()


# ---------------------------------------------------------------------------
# Enqueue basics
# ---------------------------------------------------------------------------


class TestEnqueue:
    async def test_enqueue_returns_true(self, vqm):
        job = ReviewJob(project_id=1, mr_iid=1)
        assert await vqm.enqueue(job) is True

    async def test_enqueue_assigns_job_id(self, vqm):
        job = ReviewJob(project_id=1, mr_iid=1)
        await vqm.enqueue(job)
        assert job.id >= 1

    async def test_job_ids_are_globally_unique_and_increasing(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=2)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)
        assert j2.id > j1.id

    async def test_enqueue_increments_pending(self, vqm):
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=2))
        assert vqm.status()["pending"] == 2

    async def test_full_queue_returns_false(self, fake_redis):
        mgr = ValkeyQueueManager(max_concurrent=1, max_size=2, cache_ttl=3600)
        mgr._redis = fakeredis.FakeAsyncRedis(server=fake_redis, decode_responses=True)
        await mgr.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await mgr.enqueue(ReviewJob(project_id=1, mr_iid=2))
        result = await mgr.enqueue(ReviewJob(project_id=1, mr_iid=3))
        assert result is False
        await mgr.drain()

    async def test_payload_pushed_to_redis(self, vqm):
        r = vqm._redis
        await vqm.enqueue(ReviewJob(project_id=42, mr_iid=7, diff_hash="abc"))
        assert await r.llen(_QUEUE_KEY) == 1

    async def test_latest_key_stored_in_redis(self, vqm):
        r = vqm._redis
        job = ReviewJob(project_id=10, mr_iid=3)
        await vqm.enqueue(job)
        val = await r.get(f"{_LATEST_PREFIX}10:3")
        assert int(val) == job.id


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    async def test_same_diff_hash_deduped_after_mark_seen(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash1")
        await vqm.enqueue(j1)
        vqm.mark_seen(1, 1, "hash1")
        j2 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash1")
        assert await vqm.enqueue(j2) is False

    async def test_different_hash_not_deduped(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash1")
        await vqm.enqueue(j1)
        vqm.mark_seen(1, 1, "hash1")
        j2 = ReviewJob(project_id=1, mr_iid=1, diff_hash="hash2")
        assert await vqm.enqueue(j2) is True

    async def test_same_hash_different_mr_not_deduped(self, vqm):
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="same"))
        vqm.mark_seen(1, 1, "same")
        result = await vqm.enqueue(ReviewJob(project_id=1, mr_iid=2, diff_hash="same"))
        assert result is True

    async def test_no_diff_hash_always_enqueued(self, vqm):
        r1 = await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash=""))
        r2 = await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash=""))
        assert r1 is True
        assert r2 is True

    async def test_is_already_seen_false_before_mark(self, vqm):
        assert vqm.is_already_seen(1, 1, "newhash") is False

    async def test_is_already_seen_true_after_mark(self, vqm):
        vqm.mark_seen(1, 1, "newhash")
        assert vqm.is_already_seen(1, 1, "newhash") is True

    async def test_is_already_seen_false_empty_hash(self, vqm):
        vqm.mark_seen(1, 1, "")
        assert vqm.is_already_seen(1, 1, "") is False


# ---------------------------------------------------------------------------
# Supersede
# ---------------------------------------------------------------------------


class TestSupersede:
    async def test_older_job_superseded_by_newer(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)
        # j1 is older than j2 → superseded
        assert vqm.is_superseded(j1) is True

    async def test_latest_job_not_superseded(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)
        assert vqm.is_superseded(j2) is False

    async def test_supersede_does_not_affect_other_mr(self, vqm):
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=2)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)
        # j1 and j2 are different MRs — neither supersedes the other
        assert vqm.is_superseded(j1) is False
        assert vqm.is_superseded(j2) is False

    async def test_is_superseded_async_uses_redis(self, vqm):
        j1 = ReviewJob(project_id=5, mr_iid=10)
        j2 = ReviewJob(project_id=5, mr_iid=10)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)
        r = vqm._redis
        assert await vqm._is_superseded_async(r, j1) is True
        assert await vqm._is_superseded_async(r, j2) is False


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


class TestWorkers:
    async def test_worker_processes_job(self, vqm):
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job.mr_iid)

        vqm.start(review_fn=handler)
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=42))
        await asyncio.sleep(0.15)
        assert 42 in processed

    async def test_done_counter_increments(self, vqm):
        vqm.start(review_fn=AsyncMock())
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await asyncio.sleep(0.15)
        assert vqm.status()["done"] >= 1

    async def test_error_counter_increments(self, vqm):
        async def boom(_job):
            raise RuntimeError("test error")

        vqm.start(review_fn=boom)
        await vqm.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await asyncio.sleep(0.15)
        assert vqm.status()["errors"] >= 1

    async def test_superseded_job_not_processed(self, vqm):
        """If a newer job arrives, the older one should be silently dropped."""
        processed = []

        async def handler(job: ReviewJob):
            # Simulate small delay so supersede check has time to fire
            await asyncio.sleep(0.01)
            processed.append(job.id)

        # Enqueue two jobs for the same MR *before* starting workers
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await vqm.enqueue(j1)
        await vqm.enqueue(j2)

        # Only now start the workers
        vqm.start(review_fn=handler)
        await asyncio.sleep(0.3)

        # j1 was superseded by j2 → only j2 (or neither) processed
        assert j1.id not in processed


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    async def test_initial_status(self, vqm):
        s = vqm.status()
        assert s["pending"] == 0
        assert s["active"] == 0
        assert s["done"] == 0
        assert s["errors"] == 0
        assert s["backend"] == "valkey"

    async def test_status_has_required_keys(self, vqm):
        s = vqm.status()
        for key in ("pending", "active", "done", "errors", "max_concurrent", "queue_maxsize"):
            assert key in s


# ---------------------------------------------------------------------------
# load_seen_from_db
# ---------------------------------------------------------------------------


class TestLoadSeenFromDb:
    async def test_seeds_in_memory_cache(self, vqm):
        db = MagicMock()
        db.list_diff_hashes = AsyncMock(return_value=[(1, 10, "abc"), (2, 20, "def")])
        count = await vqm.load_seen_from_db(db)
        assert count == 2
        assert vqm.is_already_seen(1, 10, "abc") is True
        assert vqm.is_already_seen(2, 20, "def") is True

    async def test_handles_db_error_gracefully(self, vqm):
        db = MagicMock()
        db.list_diff_hashes = AsyncMock(side_effect=Exception("db error"))
        count = await vqm.load_seen_from_db(db)
        assert count == 0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_queue_manager_for_memory(self):
        from src.backends import create_queue_manager
        from src.config import AppConfig, QueueConfig
        from src.queue_manager import QueueManager

        cfg = AppConfig(queue=QueueConfig(backend="memory"))
        mgr = create_queue_manager(cfg)
        assert isinstance(mgr, QueueManager)

    def test_factory_returns_valkey_manager_for_valkey(self):
        from src.backends import create_queue_manager
        from src.config import AppConfig, QueueConfig

        cfg = AppConfig(queue=QueueConfig(backend="valkey"))
        mgr = create_queue_manager(cfg)
        assert isinstance(mgr, ValkeyQueueManager)
