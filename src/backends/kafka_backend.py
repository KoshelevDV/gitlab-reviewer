"""
Kafka distributed review queue.

Design:
  - Producer:  enqueue() → AIOKafkaProducer.send_and_wait() to topic glr.mr.events
  - Consumers: one AIOKafkaConsumer per worker, all in the same consumer group
               so each message is processed by exactly one instance
  - Ordering:  partition key = "project_id:mr_iid" → events for the same MR
               land in the same partition and are processed in order
  - Supersede: in-memory latest-job-id dict (per instance) — sufficient because
               events for the same MR go to the same partition / consumer
  - Dedup:     in-memory dict seeded from DB on startup (same as other backends)
  - max_queue_size is not enforced — Kafka handles back-pressure via lag

Requirements:
  pip install aiokafka
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from .. import metrics as _metrics
from ..queue_manager import ReviewJob
from .dedup import DedupCache

if TYPE_CHECKING:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    from ..db import Database

logger = logging.getLogger(__name__)


class KafkaQueueManager:
    """
    Kafka-backed review queue.

    Provides the same public interface as QueueManager so callers
    (main.py, reviewer.py, webhook.py) need no changes.
    """

    def __init__(
        self,
        brokers: str = "localhost:9092",
        topic: str = "glr.mr.events",
        group_id: str = "glr-reviewers",
        max_concurrent: int = 3,
        max_size: int = 100,  # informational only for Kafka
        cache_ttl: int = 3600,
    ) -> None:
        self._brokers = brokers
        self._topic = topic
        self._group_id = group_id
        self._max_concurrent = max_concurrent
        self._max_size = max_size
        self._cache_ttl = float(cache_ttl)

        self._producer: AIOKafkaProducer | None = None
        self._workers: list[asyncio.Task] = []
        self._semaphore: asyncio.Semaphore | None = None
        self._review_fn: Callable[[ReviewJob], Coroutine] | None = None

        # Per-instance counters
        self._pending = 0
        self._active = 0
        self._done = 0
        self._errors = 0

        self._dedup = DedupCache()

        # Latest job timestamp per MR for supersede (in-memory, per instance)
        # Works because same-MR events go to the same partition → same consumer
        self._latest_job_id: dict[tuple[str, int], int] = {}

    # ------------------------------------------------------------------
    # Producer (lazy init)
    # ------------------------------------------------------------------

    async def _get_producer(self) -> AIOKafkaProducer:
        if self._producer is None:
            from aiokafka import AIOKafkaProducer  # noqa: PLC0415

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._brokers,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode() if k else None,
                # Durability: wait for leader ack (acks=1 default)
            )
            await self._producer.start()
            logger.info("Kafka producer started (brokers=%s, topic=%s)", self._brokers, self._topic)
        return self._producer

    # ------------------------------------------------------------------
    # Public API (mirrors QueueManager)
    # ------------------------------------------------------------------

    async def enqueue(self, job: ReviewJob) -> bool:
        """
        Publish a review job to Kafka.
        Returns True if accepted, False if deduped.
        """
        if self._dedup.is_seen(job.project_id, job.mr_iid, job.diff_hash):
            logger.info(
                "Dedup (kafka): skipping project=%s MR!%d (diff hash already seen)",
                job.project_id,
                job.mr_iid,
            )
            _metrics.queue_rejected_total.inc()
            return False

        # 2. Assign job ID (monotonic timestamp ms — unique enough per instance)
        job_id = int(time.time() * 1000)
        job.id = job_id

        # 3. Track latest job per MR (for supersede — same partition, in order)
        mr_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
        self._latest_job_id[mr_key] = job_id

        # 4. Publish to Kafka
        #    Partition key = "project_id:mr_iid" → deterministic partition assignment
        #    guarantees events for the same MR are ordered and handled by one consumer
        partition_key = f"{job.project_id}:{job.mr_iid}"
        payload = {
            "project_id": str(job.project_id),
            "mr_iid": job.mr_iid,
            "event_action": job.event_action,
            "diff_hash": job.diff_hash,
            "id": job_id,
        }
        producer = await self._get_producer()
        await producer.send_and_wait(self._topic, value=payload, key=partition_key)

        self._pending += 1
        _metrics.queue_enqueued_total.inc()
        _metrics.queue_pending.set(self._pending)

        logger.info(
            "Enqueued job #%d (kafka): project=%s MR!%d → topic=%s key=%s",
            job_id,
            job.project_id,
            job.mr_iid,
            self._topic,
            partition_key,
        )
        return True

    def start(
        self,
        review_fn: Callable[[ReviewJob], Coroutine],
        num_workers: int | None = None,
    ) -> None:
        """Spawn consumer worker coroutines. Call from async context (app startup)."""
        self._review_fn = review_fn
        count = num_workers or self._max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        for i in range(count):
            task = asyncio.ensure_future(self._worker(i, review_fn))
            task.set_name(f"kafka-reviewer-worker-{i}")
            self._workers.append(task)
        logger.info(
            "Started %d Kafka consumer worker(s) (brokers=%s, topic=%s, group=%s)",
            count,
            self._brokers,
            self._topic,
            self._group_id,
        )

    async def restart(self, num_workers: int | None = None) -> int:
        """Restart workers after drain. Returns number of workers started."""
        if self._review_fn is None:
            raise RuntimeError("start() must be called before restart()")
        count = num_workers or self._max_concurrent
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        for i in range(count):
            task = asyncio.ensure_future(self._worker(i, self._review_fn))
            task.set_name(f"kafka-reviewer-worker-{i}")
            self._workers.append(task)
        logger.info("Restarted %d Kafka review workers", count)
        return count

    async def drain(self) -> None:
        """Cancel all workers and stop the producer."""
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    def status(self) -> dict[str, Any]:
        return {
            "pending": self._pending,
            "active": self._active,
            "done": self._done,
            "errors": self._errors,
            "max_concurrent": self._max_concurrent,
            "queue_maxsize": self._max_size,
            "backend": "kafka",
        }

    async def load_seen_from_db(self, db: Database) -> int:  # type: ignore[name-defined]
        """Seed in-memory dedup cache from recent DB records."""
        return await self._dedup.load_from_db(db)

    def is_already_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> bool:
        return self._dedup.is_seen(project_id, mr_iid, diff_hash)

    def mark_seen(self, project_id: int | str, mr_iid: int, diff_hash: str) -> None:
        self._dedup.mark(project_id, mr_iid, diff_hash)

    def is_superseded(self, job: ReviewJob) -> bool:
        """
        Check if a newer job for this MR has arrived since job was enqueued.
        Because same-MR events go to the same partition and are processed in order,
        this in-memory check is reliable within a single consumer.
        """
        mr_key: tuple[str, int] = (str(job.project_id), job.mr_iid)
        latest = self._latest_job_id.get(mr_key, job.id)
        return job.id < latest

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker(
        self,
        worker_id: int,
        review_fn: Callable[[ReviewJob], Coroutine],
    ) -> None:
        """
        Each worker is an independent AIOKafkaConsumer in the same consumer group.
        Kafka's group coordinator assigns partitions across all consumers in the group
        so no two workers process the same message.
        """
        from aiokafka import AIOKafkaConsumer  # noqa: PLC0415

        consumer: AIOKafkaConsumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._brokers,
            group_id=self._group_id,
            value_deserializer=lambda raw: json.loads(raw.decode()),
            enable_auto_commit=True,
            auto_offset_reset="latest",  # skip old events on first start
            # Unique client ID per worker so the coordinator can distinguish them
            client_id=f"glr-worker-{worker_id}",
        )
        assert self._semaphore is not None

        try:
            await consumer.start()
            logger.debug("Kafka worker-%d consumer started", worker_id)

            async for msg in consumer:
                try:
                    data: dict = msg.value
                    job = ReviewJob(
                        project_id=data["project_id"],
                        mr_iid=data["mr_iid"],
                        event_action=data.get("event_action", "open"),
                        diff_hash=data.get("diff_hash", ""),
                        id=data.get("id", 0),
                    )
                except Exception:
                    logger.exception("Malformed Kafka message: %.200s", msg.value)
                    continue

                self._pending = max(0, self._pending - 1)
                self._active += 1
                _metrics.queue_pending.set(self._pending)
                _metrics.queue_active.set(self._active)

                async with self._semaphore:
                    if self.is_superseded(job):
                        logger.info(
                            "Dropping superseded job #%d (kafka): project=%s MR!%d",
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
                            "Worker-%d starting job #%d (kafka): project=%s MR!%d",
                            worker_id,
                            job.id,
                            job.project_id,
                            job.mr_iid,
                        )
                        await review_fn(job)
                        self._done += 1
                    except Exception:
                        self._errors += 1
                        logger.exception(
                            "Worker-%d error on job #%d: project=%s MR!%d",
                            worker_id,
                            job.id,
                            job.project_id,
                            job.mr_iid,
                        )
                    finally:
                        self._active -= 1
                        _metrics.queue_active.set(self._active)

        except asyncio.CancelledError:
            logger.debug("Kafka worker-%d cancelled", worker_id)
        except Exception:
            logger.exception("Kafka worker-%d fatal error", worker_id)
        finally:
            await consumer.stop()
            logger.debug("Kafka worker-%d consumer stopped", worker_id)
