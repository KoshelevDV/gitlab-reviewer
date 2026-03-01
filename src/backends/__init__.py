"""
Queue backend factory.

Selects the queue manager implementation based on config:
  - memory  → QueueManager (asyncio.Queue + in-memory dedup)
  - valkey  → ValkeyQueueManager (Redis-compatible distributed queue)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import AppConfig
    from ..queue_manager import QueueManager


def create_queue_manager(cfg: AppConfig) -> QueueManager:
    """Return the appropriate QueueManager based on queue.backend config."""
    if cfg.queue.backend == "valkey":
        try:
            from .valkey_backend import ValkeyQueueManager
        except ImportError as exc:
            raise RuntimeError(
                "Valkey backend requires the 'redis' package. "
                "Install with: pip install 'gitlab-reviewer[valkey]'"
            ) from exc
        return ValkeyQueueManager(  # type: ignore[return-value]
            url=cfg.queue.valkey_url,
            max_concurrent=cfg.queue.max_concurrent,
            max_size=cfg.queue.max_queue_size,
            cache_ttl=cfg.cache.ttl,
        )

    if cfg.queue.backend == "kafka":
        try:
            from .kafka_backend import KafkaQueueManager
        except ImportError as exc:
            raise RuntimeError(
                "Kafka backend requires the 'aiokafka' package. "
                "Install with: pip install 'gitlab-reviewer[kafka]'"
            ) from exc
        return KafkaQueueManager(  # type: ignore[return-value]
            brokers=cfg.queue.kafka_brokers,
            topic=cfg.queue.kafka_topic,
            group_id=cfg.queue.kafka_group_id,
            max_concurrent=cfg.queue.max_concurrent,
            max_size=cfg.queue.max_queue_size,
            cache_ttl=cfg.cache.ttl,
        )

    from ..queue_manager import QueueManager  # lazy — avoids circular import

    return QueueManager(
        max_concurrent=cfg.queue.max_concurrent,
        max_size=cfg.queue.max_queue_size,
    )
