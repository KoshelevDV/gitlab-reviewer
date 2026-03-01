"""
Tests for KafkaQueueManager — aiokafka producer/consumer are mocked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.backends.kafka_backend import KafkaQueueManager
from src.queue_manager import ReviewJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kafka_msg(payload: dict) -> SimpleNamespace:
    """Fake Kafka ConsumerRecord with a JSON value field."""
    return SimpleNamespace(value=payload)


class FakeConsumer:
    """
    Async-iterable fake Kafka consumer.
    Yields messages from `items`, then blocks indefinitely until cancelled.
    """

    def __init__(self, items: list) -> None:
        self._items = list(items)
        self.start = AsyncMock()
        self.stop = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        await asyncio.sleep(60)  # block until CancelledError
        raise StopAsyncIteration  # unreachable, but satisfies type checkers


def _make_kqm(**kwargs) -> KafkaQueueManager:
    defaults = dict(
        brokers="localhost:9092",
        topic="glr.mr.events",
        group_id="glr-test",
        max_concurrent=2,
        max_size=100,
        cache_ttl=3600,
    )
    defaults.update(kwargs)
    return KafkaQueueManager(**defaults)


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------


class TestEnqueue:
    async def test_enqueue_returns_true_on_success(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        job = ReviewJob(project_id=1, mr_iid=1)
        result = await kqm.enqueue(job)
        assert result is True

    async def test_enqueue_assigns_job_id(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        job = ReviewJob(project_id=1, mr_iid=5)
        await kqm.enqueue(job)
        assert job.id > 0

    async def test_enqueue_calls_send_and_wait(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        await kqm.enqueue(ReviewJob(project_id=2, mr_iid=3, event_action="update"))
        mock_producer.send_and_wait.assert_awaited_once()

    async def test_enqueue_uses_partition_key(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        await kqm.enqueue(ReviewJob(project_id=7, mr_iid=42))
        call_kwargs = mock_producer.send_and_wait.call_args
        assert call_kwargs.kwargs["key"] == "7:42"

    async def test_enqueue_increments_pending(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1))
        await kqm.enqueue(ReviewJob(project_id=1, mr_iid=2))
        assert kqm.status()["pending"] == 2

    async def test_enqueue_updates_latest_job_id(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await kqm.enqueue(j1)
        await kqm.enqueue(j2)
        assert j2.id >= j1.id


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    async def test_same_hash_deduped_after_mark_seen(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="abc"))
        kqm.mark_seen(1, 1, "abc")
        result = await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="abc"))
        assert result is False
        # Producer called only once (first enqueue)
        assert mock_producer.send_and_wait.await_count == 1

    async def test_different_hash_not_deduped(self):
        kqm = _make_kqm()
        kqm._producer = AsyncMock()
        await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="hash1"))
        kqm.mark_seen(1, 1, "hash1")
        result = await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="hash2"))
        assert result is True

    async def test_same_hash_different_mr_not_deduped(self):
        kqm = _make_kqm()
        kqm._producer = AsyncMock()
        await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash="same"))
        kqm.mark_seen(1, 1, "same")
        result = await kqm.enqueue(ReviewJob(project_id=1, mr_iid=2, diff_hash="same"))
        assert result is True

    async def test_no_diff_hash_always_enqueued(self):
        kqm = _make_kqm()
        kqm._producer = AsyncMock()
        r1 = await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash=""))
        r2 = await kqm.enqueue(ReviewJob(project_id=1, mr_iid=1, diff_hash=""))
        assert r1 is True
        assert r2 is True

    def test_is_already_seen_false_before_mark(self):
        kqm = _make_kqm()
        assert kqm.is_already_seen(1, 1, "x") is False

    def test_is_already_seen_true_after_mark(self):
        kqm = _make_kqm()
        kqm.mark_seen(1, 1, "x")
        assert kqm.is_already_seen(1, 1, "x") is True

    def test_is_already_seen_false_empty_hash(self):
        kqm = _make_kqm()
        kqm.mark_seen(1, 1, "")
        assert kqm.is_already_seen(1, 1, "") is False


# ---------------------------------------------------------------------------
# Supersede
# ---------------------------------------------------------------------------


class TestSupersede:
    async def test_older_job_is_superseded(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await kqm.enqueue(j1)
        await asyncio.sleep(0.002)  # ensure ts difference
        await kqm.enqueue(j2)
        assert kqm.is_superseded(j1) is True

    async def test_latest_job_not_superseded(self):
        kqm = _make_kqm()
        kqm._producer = AsyncMock()
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=1)
        await kqm.enqueue(j1)
        await asyncio.sleep(0.002)
        await kqm.enqueue(j2)
        assert kqm.is_superseded(j2) is False

    async def test_supersede_independent_across_mrs(self):
        kqm = _make_kqm()
        kqm._producer = AsyncMock()
        j1 = ReviewJob(project_id=1, mr_iid=1)
        j2 = ReviewJob(project_id=1, mr_iid=2)
        await kqm.enqueue(j1)
        await kqm.enqueue(j2)
        assert kqm.is_superseded(j1) is False
        assert kqm.is_superseded(j2) is False


# ---------------------------------------------------------------------------
# Worker (consumer) — mocked consumer loop
# ---------------------------------------------------------------------------


class TestWorkers:
    async def test_restart_after_drain(self):
        """Workers restarted via restart() should be able to process new jobs."""
        kqm = _make_kqm(max_concurrent=1)
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job.mr_iid)

        job_payload = {
            "project_id": "1",
            "mr_iid": 99,
            "event_action": "open",
            "diff_hash": "",
            "id": 1,
        }
        fake1 = FakeConsumer([])
        fake2 = FakeConsumer([_make_kafka_msg(job_payload)])

        with patch("aiokafka.AIOKafkaConsumer", side_effect=[fake1, fake2]):
            kqm.start(review_fn=handler, num_workers=1)
            await asyncio.sleep(0.05)
            await kqm.drain()

            count = await kqm.restart(num_workers=1)
            assert count == 1
            await asyncio.sleep(0.15)
            await kqm.drain()

        assert 99 in processed

    async def test_restart_without_start_raises(self):
        import pytest

        kqm = _make_kqm()
        with pytest.raises(RuntimeError, match="start\\(\\) must be called before restart"):
            await kqm.restart()

    async def test_worker_processes_message(self):
        """Worker deserialises a Kafka message and calls review_fn."""
        kqm = _make_kqm()
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job.mr_iid)

        job_payload = {
            "project_id": "1",
            "mr_iid": 99,
            "event_action": "open",
            "diff_hash": "",
            "id": 12345,
        }
        fake = FakeConsumer([_make_kafka_msg(job_payload)])

        with patch("aiokafka.AIOKafkaConsumer", return_value=fake):
            kqm.start(review_fn=handler, num_workers=1)
            await asyncio.sleep(0.15)
            await kqm.drain()

        assert 99 in processed

    async def test_worker_increments_done(self):
        kqm = _make_kqm()

        job_payload = {
            "project_id": "1",
            "mr_iid": 1,
            "event_action": "open",
            "diff_hash": "",
            "id": 1,
        }
        fake = FakeConsumer([_make_kafka_msg(job_payload)])

        with patch("aiokafka.AIOKafkaConsumer", return_value=fake):
            kqm.start(review_fn=AsyncMock(), num_workers=1)
            await asyncio.sleep(0.15)
            await kqm.drain()

        assert kqm.status()["done"] >= 1

    async def test_worker_increments_errors_on_exception(self):
        kqm = _make_kqm()

        async def boom(_job):
            raise RuntimeError("test error")

        job_payload = {
            "project_id": "1",
            "mr_iid": 1,
            "event_action": "open",
            "diff_hash": "",
            "id": 1,
        }
        fake = FakeConsumer([_make_kafka_msg(job_payload)])

        with patch("aiokafka.AIOKafkaConsumer", return_value=fake):
            kqm.start(review_fn=boom, num_workers=1)
            await asyncio.sleep(0.15)
            await kqm.drain()

        assert kqm.status()["errors"] >= 1

    async def test_malformed_message_skipped(self):
        """Worker should skip messages that can't be deserialised."""
        kqm = _make_kqm()
        processed = []

        async def handler(job: ReviewJob):
            processed.append(job)

        # Missing required keys → job construction will raise KeyError → skip
        fake = FakeConsumer([_make_kafka_msg({"bad": "schema"})])

        with patch("aiokafka.AIOKafkaConsumer", return_value=fake):
            kqm.start(review_fn=handler, num_workers=1)
            await asyncio.sleep(0.15)
            await kqm.drain()

        assert processed == []


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_initial_status(self):
        kqm = _make_kqm()
        s = kqm.status()
        assert s["pending"] == 0
        assert s["active"] == 0
        assert s["done"] == 0
        assert s["errors"] == 0
        assert s["backend"] == "kafka"

    def test_status_has_required_keys(self):
        kqm = _make_kqm()
        s = kqm.status()
        for key in ("pending", "active", "done", "errors", "max_concurrent", "queue_maxsize"):
            assert key in s


# ---------------------------------------------------------------------------
# load_seen_from_db
# ---------------------------------------------------------------------------


class TestLoadSeenFromDb:
    async def test_seeds_in_memory_cache(self):
        kqm = _make_kqm()
        db = MagicMock()
        db.list_diff_hashes = AsyncMock(return_value=[(1, 10, "abc"), (2, 20, "def")])
        count = await kqm.load_seen_from_db(db)
        assert count == 2
        assert kqm.is_already_seen(1, 10, "abc") is True
        assert kqm.is_already_seen(2, 20, "def") is True

    async def test_handles_db_error_gracefully(self):
        kqm = _make_kqm()
        db = MagicMock()
        db.list_diff_hashes = AsyncMock(side_effect=Exception("db error"))
        count = await kqm.load_seen_from_db(db)
        assert count == 0


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


class TestDrain:
    async def test_drain_stops_producer(self):
        kqm = _make_kqm()
        mock_producer = AsyncMock()
        kqm._producer = mock_producer
        await kqm.drain()
        mock_producer.stop.assert_awaited_once()
        assert kqm._producer is None

    async def test_drain_with_no_producer_is_safe(self):
        kqm = _make_kqm()
        # No producer started — drain should not raise
        await kqm.drain()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_kafka_manager(self):
        from src.backends import create_queue_manager
        from src.config import AppConfig, QueueConfig

        cfg = AppConfig(queue=QueueConfig(backend="kafka"))
        mgr = create_queue_manager(cfg)
        assert isinstance(mgr, KafkaQueueManager)

    def test_factory_uses_kafka_config_fields(self):
        from src.backends import create_queue_manager
        from src.config import AppConfig, QueueConfig

        cfg = AppConfig(
            queue=QueueConfig(
                backend="kafka",
                kafka_brokers="broker1:9092,broker2:9092",
                kafka_topic="my.topic",
                kafka_group_id="my-group",
                max_concurrent=5,
            )
        )
        mgr = create_queue_manager(cfg)
        assert isinstance(mgr, KafkaQueueManager)
        assert mgr._brokers == "broker1:9092,broker2:9092"
        assert mgr._topic == "my.topic"
        assert mgr._group_id == "my-group"
        assert mgr._max_concurrent == 5
