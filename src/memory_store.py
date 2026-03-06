"""
Qdrant-backed memory for gitlab-reviewer.

Stores and retrieves:
- Error patterns per project (what reviewers frequently find)
- Review history per file/function (context for re-reviews)

Falls back to no-op if Qdrant is unavailable — reviewer continues without memory.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VECTOR_DIM = 384  # all-MiniLM-L6-v2 output dimension
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class MemoryCategory(str, Enum):
    ERROR_PATTERN = "error_pattern"    # recurring error found in the project
    REVIEW_HISTORY = "review_history"  # past review of a file/function


@dataclass
class MemoryRecord:
    project_id: str
    category: MemoryCategory
    content: str           # text to store/search
    metadata: dict = field(default_factory=dict)  # file_path, severity, mr_iid, etc.


# ---------------------------------------------------------------------------
# Lazy imports — avoid hard startup failure when optional deps are absent
# ---------------------------------------------------------------------------


def _try_import_qdrant():  # type: ignore[return]
    try:
        from qdrant_client import AsyncQdrantClient  # type: ignore[import-not-found]
        from qdrant_client.http import models as qm  # type: ignore[import-not-found]
        return AsyncQdrantClient, qm
    except ImportError:
        return None, None


def _try_import_sentence_transformers():  # type: ignore[return]
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        return SentenceTransformer
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class MemoryStore:
    """
    Qdrant-backed memory for gitlab-reviewer.

    Uses sentence-transformers for local embeddings (all-MiniLM-L6-v2, ~22 MB).
    Falls back to no-op if:
    - qdrant-client is not installed
    - sentence-transformers is not installed
    - Qdrant server is unreachable
    """

    def __init__(
        self,
        url: str = "http://localhost:6333",
        collection: str = "reviewer_memory",
    ) -> None:
        self._url = url
        self._collection = collection
        self._client: Any = None          # AsyncQdrantClient | None
        self._encoder: Any = None         # SentenceTransformer | None
        self._available: bool | None = None  # None = not yet checked
        self._collection_ready: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def is_available(self) -> bool:
        """Health check.  Returns False if Qdrant is down."""
        if self._available is False:
            return False
        try:
            client = await self._get_client()
            if client is None:
                return False
            await client.get_collections()
            self._available = True
            return True
        except Exception as exc:
            logger.debug("Qdrant unavailable at %s: %s", self._url, exc)
            self._available = False
            return False

    async def remember(self, record: MemoryRecord) -> None:
        """Store a finding. No-op if Qdrant unavailable."""
        try:
            if not await self.is_available():
                return
            await self._ensure_collection()
            encoder = await self._get_encoder()
            if encoder is None:
                return

            vector = await asyncio.to_thread(encoder.encode, record.content)
            vector = vector.tolist()

            AsyncQdrantClient, qm = _try_import_qdrant()
            if qm is None:
                return

            point_id = str(uuid.uuid4())
            payload = {
                "project_id": record.project_id,
                "category": record.category.value,
                "content": record.content,
                **record.metadata,
            }

            await self._client.upsert(
                collection_name=self._collection,
                points=[
                    qm.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )
            logger.debug(
                "memory.remember: project=%s category=%s stored",
                record.project_id,
                record.category.value,
            )
        except Exception as exc:
            logger.debug("memory.remember failed (non-fatal): %s", exc)

    async def recall(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """
        Find relevant past findings for this project. Returns [] if unavailable.

        WARNING: content field may contain LLM-generated text.
        Caller MUST apply sanitize_untrusted(record.content) before inserting into prompts.
        """
        try:
            if not await self.is_available():
                return []
            await self._ensure_collection()
            encoder = await self._get_encoder()
            if encoder is None:
                return []

            _, qm = _try_import_qdrant()
            if qm is None:
                return []

            vector = await asyncio.to_thread(encoder.encode, query)
            vector = vector.tolist()

            results = await self._client.search(
                collection_name=self._collection,
                query_vector=vector,
                query_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="project_id",
                            match=qm.MatchValue(value=project_id),
                        )
                    ]
                ),
                limit=top_k,
                with_payload=True,
            )

            records: list[MemoryRecord] = []
            for hit in results:
                payload = hit.payload or {}
                content = payload.pop("content", "")
                # Basic prompt injection defence: bracket substitution so that
                # LLM-generated content cannot abuse markdown link syntax in prompts.
                # Caller MUST additionally apply sanitize_untrusted() before inserting into prompts.
                content = content.replace("[", "【").replace("]", "】")
                pid = payload.pop("project_id", project_id)
                raw_cat = payload.pop("category", MemoryCategory.ERROR_PATTERN.value)
                try:
                    cat = MemoryCategory(raw_cat)
                except ValueError:
                    cat = MemoryCategory.ERROR_PATTERN
                records.append(
                    MemoryRecord(
                        project_id=pid,
                        category=cat,
                        content=content,
                        metadata=payload,
                    )
                )

            logger.debug(
                "memory.recall: project=%s query=%r → %d results",
                project_id,
                query[:60],
                len(records),
            )
            return records

        except Exception as exc:
            logger.debug("memory.recall failed (non-fatal): %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> Any:
        """Return or lazily create the AsyncQdrantClient."""
        if self._client is not None:
            return self._client
        AsyncQdrantClient, _ = _try_import_qdrant()
        if AsyncQdrantClient is None:
            logger.debug("qdrant-client not installed — memory disabled")
            return None
        self._client = AsyncQdrantClient(url=self._url, timeout=5.0)
        return self._client

    async def _get_encoder(self) -> Any:
        """Return or lazily create the SentenceTransformer encoder (async, CPU-bound load via thread)."""
        if self._encoder is not None:
            return self._encoder
        SentenceTransformer = _try_import_sentence_transformers()
        if SentenceTransformer is None:
            logger.debug("sentence-transformers not installed — memory disabled")
            return None
        try:
            self._encoder = await asyncio.to_thread(SentenceTransformer, _EMBEDDING_MODEL)
        except Exception as exc:
            logger.debug("Failed to load embedding model: %s", exc)
            return None
        return self._encoder

    async def _ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not exist yet."""
        if self._collection_ready:
            return
        _, qm = _try_import_qdrant()
        if qm is None or self._client is None:
            return
        try:
            existing = await self._client.get_collections()
            names = [c.name for c in existing.collections]
            if self._collection not in names:
                await self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=qm.VectorParams(
                        size=VECTOR_DIM,
                        distance=qm.Distance.COSINE,
                    ),
                )
                # Index project_id for fast filtering
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name="project_id",
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
                logger.info("memory: created Qdrant collection %r", self._collection)
            self._collection_ready = True
        except Exception as exc:
            logger.debug("_ensure_collection failed (non-fatal): %s", exc)
