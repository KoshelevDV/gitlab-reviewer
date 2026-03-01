"""Tests for shared DedupCache (src/backends/dedup.py)."""

from __future__ import annotations

import time

from src.backends.dedup import DedupCache


class TestDedupCacheBasic:
    def test_unseen_key_returns_false(self):
        c = DedupCache()
        assert c.is_seen("42", 7, "abc") is False

    def test_marked_key_is_seen(self):
        c = DedupCache()
        c.mark("42", 7, "abc")
        assert c.is_seen("42", 7, "abc") is True

    def test_different_hash_not_seen(self):
        c = DedupCache()
        c.mark("42", 7, "abc")
        assert c.is_seen("42", 7, "xyz") is False

    def test_different_mr_not_seen(self):
        c = DedupCache()
        c.mark("42", 7, "abc")
        assert c.is_seen("42", 8, "abc") is False

    def test_empty_hash_always_false(self):
        c = DedupCache()
        c.mark("42", 7, "")
        assert c.is_seen("42", 7, "") is False

    def test_seed_sets_key(self):
        c = DedupCache()
        c.seed("42", 7, "abc")
        assert c.is_seen("42", 7, "abc") is True

    def test_seed_does_not_overwrite_existing(self):
        c = DedupCache(ttl=100)
        c.mark("42", 7, "abc")
        old_ts = c._seen[("42", 7, "abc")]
        time.sleep(0.01)
        c.seed("42", 7, "abc")
        assert c._seen[("42", 7, "abc")] == old_ts  # unchanged

    def test_len(self):
        c = DedupCache()
        assert len(c) == 0
        c.mark("42", 7, "a")
        c.mark("42", 7, "b")
        assert len(c) == 2


class TestDedupCacheTTL:
    def test_expired_entry_returns_false(self):
        c = DedupCache(ttl=0.01)  # 10ms TTL
        c.mark("42", 7, "abc")
        time.sleep(0.02)
        assert c.is_seen("42", 7, "abc") is False

    def test_expired_entry_removed_from_dict(self):
        c = DedupCache(ttl=0.01)
        c.mark("42", 7, "abc")
        time.sleep(0.02)
        c.is_seen("42", 7, "abc")  # triggers cleanup
        assert ("42", 7, "abc") not in c._seen


class TestDedupCacheLoadFromDB:
    async def test_load_from_db_seeds_entries(self):
        from unittest.mock import AsyncMock, MagicMock

        db = MagicMock()
        db.list_diff_hashes = AsyncMock(return_value=[("42", 7, "hash1"), ("42", 8, "hash2")])
        c = DedupCache()
        count = await c.load_from_db(db)
        assert count == 2
        assert c.is_seen("42", 7, "hash1") is True
        assert c.is_seen("42", 8, "hash2") is True

    async def test_load_from_db_non_fatal_on_error(self):
        from unittest.mock import AsyncMock, MagicMock

        db = MagicMock()
        db.list_diff_hashes = AsyncMock(side_effect=Exception("DB error"))
        c = DedupCache()
        count = await c.load_from_db(db)
        assert count == 0
