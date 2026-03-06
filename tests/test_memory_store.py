"""
Tests for MemoryStore — Qdrant-backed memory.

All Qdrant interactions are mocked so tests run without a live server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory_store import MemoryCategory, MemoryRecord, MemoryStore
from src.config import MemoryConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hit(project_id: str, content: str, category: str = "error_pattern") -> MagicMock:
    """Create a fake Qdrant search hit."""
    hit = MagicMock()
    hit.payload = {
        "project_id": project_id,
        "category": category,
        "content": content,
        "role": "reviewer",
    }
    hit.score = 0.9
    return hit


def _make_encoder(content: str = "test"):
    """Return a mock SentenceTransformer encoder."""
    enc = MagicMock()
    vec = MagicMock()
    vec.tolist.return_value = [0.1] * 384
    enc.encode.return_value = vec
    return enc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_and_recall():
    """remember() stores a record; recall() retrieves it via vector search."""
    store = MemoryStore(url="http://fake:6333", collection="test")

    mock_client = AsyncMock()
    # is_available → get_collections
    mock_collections = MagicMock()
    mock_collections.collections = [MagicMock(name="test")]
    # Need name attr to work
    mock_collections.collections[0].name = "test"
    mock_client.get_collections = AsyncMock(return_value=mock_collections)
    mock_client.upsert = AsyncMock()
    mock_client.create_collection = AsyncMock()
    mock_client.create_payload_index = AsyncMock()

    hit = _make_hit("proj-1", "SQL injection found in login endpoint")
    mock_client.search = AsyncMock(return_value=[hit])

    encoder = _make_encoder()

    with (
        patch("src.memory_store._try_import_qdrant") as mock_import_qdrant,
        patch("src.memory_store._try_import_sentence_transformers") as mock_import_st,
    ):
        # Patch AsyncQdrantClient constructor to return our mock client
        FakeAsyncQdrantClient = MagicMock(return_value=mock_client)
        fake_qm = MagicMock()
        fake_qm.PointStruct = MagicMock(side_effect=lambda **kw: kw)
        fake_qm.VectorParams = MagicMock()
        fake_qm.Distance = MagicMock()
        fake_qm.Distance.COSINE = "Cosine"
        fake_qm.PayloadSchemaType = MagicMock()
        fake_qm.PayloadSchemaType.KEYWORD = "keyword"
        fake_qm.Filter = MagicMock(side_effect=lambda **kw: kw)
        fake_qm.FieldCondition = MagicMock(side_effect=lambda **kw: kw)
        fake_qm.MatchValue = MagicMock(side_effect=lambda **kw: kw)

        mock_import_qdrant.return_value = (FakeAsyncQdrantClient, fake_qm)
        mock_import_st.return_value = MagicMock(return_value=encoder)

        record = MemoryRecord(
            project_id="proj-1",
            category=MemoryCategory.ERROR_PATTERN,
            content="SQL injection found in login endpoint",
            metadata={"file_path": "src/auth.py", "mr_iid": 42},
        )
        await store.remember(record)

        results = await store.recall(
            project_id="proj-1",
            query="security issues in authentication",
            top_k=5,
        )

    assert len(results) == 1
    assert results[0].project_id == "proj-1"
    assert "SQL injection" in results[0].content


@pytest.mark.asyncio
async def test_recall_empty_when_unavailable():
    """Qdrant is down → recall() returns []."""
    store = MemoryStore(url="http://down:6333", collection="test")

    with patch("src.memory_store._try_import_qdrant") as mock_import_qdrant:
        mock_client = AsyncMock()
        mock_client.get_collections = AsyncMock(
            side_effect=ConnectionRefusedError("Connection refused")
        )
        FakeAsyncQdrantClient = MagicMock(return_value=mock_client)
        mock_import_qdrant.return_value = (FakeAsyncQdrantClient, MagicMock())

        results = await store.recall(project_id="proj-1", query="any query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_remember_noop_when_unavailable():
    """Qdrant is down → remember() silently does nothing (no exception raised)."""
    store = MemoryStore(url="http://down:6333", collection="test")

    with patch("src.memory_store._try_import_qdrant") as mock_import_qdrant:
        mock_client = AsyncMock()
        mock_client.get_collections = AsyncMock(
            side_effect=OSError("Connection refused")
        )
        FakeAsyncQdrantClient = MagicMock(return_value=mock_client)
        mock_import_qdrant.return_value = (FakeAsyncQdrantClient, MagicMock())

        record = MemoryRecord(
            project_id="proj-1",
            category=MemoryCategory.ERROR_PATTERN,
            content="some finding",
            metadata={},
        )
        # Must not raise
        await store.remember(record)


@pytest.mark.asyncio
async def test_project_id_filter():
    """recall() for project A does not return records belonging to project B."""
    store = MemoryStore(url="http://fake:6333", collection="test")

    mock_client = AsyncMock()
    mock_collections = MagicMock()
    mock_collections.collections = [MagicMock()]
    mock_collections.collections[0].name = "test"
    mock_client.get_collections = AsyncMock(return_value=mock_collections)
    mock_client.create_collection = AsyncMock()
    mock_client.create_payload_index = AsyncMock()

    # Simulate Qdrant returning only hits for project-A (filter applied server-side)
    hit_a = _make_hit("project-A", "XSS vulnerability in user profile page")
    mock_client.search = AsyncMock(return_value=[hit_a])

    encoder = _make_encoder()

    with (
        patch("src.memory_store._try_import_qdrant") as mock_import_qdrant,
        patch("src.memory_store._try_import_sentence_transformers") as mock_import_st,
    ):
        FakeAsyncQdrantClient = MagicMock(return_value=mock_client)
        fake_qm = MagicMock()
        fake_qm.Filter = MagicMock(side_effect=lambda **kw: kw)
        fake_qm.FieldCondition = MagicMock(side_effect=lambda **kw: kw)
        fake_qm.MatchValue = MagicMock(side_effect=lambda **kw: kw)

        mock_import_qdrant.return_value = (FakeAsyncQdrantClient, fake_qm)
        mock_import_st.return_value = MagicMock(return_value=encoder)

        results = await store.recall(project_id="project-A", query="security issues", top_k=5)

    # All results belong to project-A, none to project-B
    assert all(r.project_id == "project-A" for r in results)

    # Verify the filter was built with the correct project_id
    call_kwargs = mock_client.search.call_args.kwargs
    assert call_kwargs["collection_name"] == "test"
    # The query_filter contains project_id constraint (mocked as dict)
    assert "must" in call_kwargs["query_filter"]


def test_memory_config_defaults():
    """MemoryConfig has enabled=False and sensible defaults."""
    cfg = MemoryConfig()
    assert cfg.enabled is False
    assert cfg.qdrant_url == "http://qdrant:6333"
    assert cfg.collection == "reviewer_memory"
    assert cfg.top_k == 5


def test_memory_config_in_app_config():
    """AppConfig includes memory section with correct defaults."""
    from src.config import AppConfig
    cfg = AppConfig()
    assert hasattr(cfg, "memory")
    assert isinstance(cfg.memory, MemoryConfig)
    assert cfg.memory.enabled is False
