"""
Queue backend factory.

Selects the queue manager implementation based on config:
  - memory  → QueueManager (asyncio.Queue + in-memory dedup)
  - valkey  → ValkeyQueueManager (Redis-compatible distributed queue)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..queue_manager import QueueManager

if TYPE_CHECKING:
    from ..config import AppConfig


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

    return QueueManager(
        max_concurrent=cfg.queue.max_concurrent,
        max_size=cfg.queue.max_queue_size,
    )
