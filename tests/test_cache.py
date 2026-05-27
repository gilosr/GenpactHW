"""
tests/test_cache.py
───────────────────
Unit tests for agent/cache.py (QueryCache).

Covers all 10 verification points from the Block 3.2 spec:
  1. Cache miss returns None
  2. Cache hit after put
  3. TTL expiration (mocked time)
  4. LRU eviction at max_size
  5. Never stores empty answer
  6. Case-insensitive normalisation
  7. Stats tracking (hits, misses)
  8. Invalidate removes entry
  9. Clear resets everything
  10. Hit rate calculation
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agent.cache import QueryCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _put(cache: QueryCache, question: str, answer: str = "42 students") -> None:
    cache.put(question, "SELECT COUNT(*) FROM students", [{"count": 42}], answer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCacheMiss:
    def test_cache_miss_returns_none(self):
        cache = QueryCache()
        assert cache.get("How many students are there?") is None

    def test_cache_miss_increments_misses(self):
        cache = QueryCache()
        cache.get("unknown question")
        assert cache.stats()["misses"] == 1
        assert cache.stats()["hits"] == 0


class TestCacheHit:
    def test_cache_hit_after_put(self):
        cache = QueryCache()
        _put(cache, "How many students are there?")
        result = cache.get("How many students are there?")
        assert result is not None
        assert result["answer"] == "42 students"

    def test_cache_hit_returns_all_fields(self):
        cache = QueryCache()
        cache.put(
            "How many students?",
            "SELECT COUNT(*) FROM students",
            [{"count": 42}],
            "There are 42 students.",
        )
        result = cache.get("How many students?")
        assert result["sql_query"] == "SELECT COUNT(*) FROM students"
        assert result["query_result"] == [{"count": 42}]
        assert result["answer"] == "There are 42 students."

    def test_cache_hit_increments_hits(self):
        cache = QueryCache()
        _put(cache, "test question")
        cache.get("test question")
        assert cache.stats()["hits"] == 1


class TestTTLExpiration:
    def test_cache_ttl_expiration(self):
        cache = QueryCache(ttl_seconds=60)
        _put(cache, "How many students?")

        # Fast-forward time beyond TTL
        with patch("agent.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 3601
            result = cache.get("How many students?")

        assert result is None

    def test_expired_entry_counted_as_miss(self):
        cache = QueryCache(ttl_seconds=60)
        _put(cache, "How many students?")

        with patch("agent.cache.time") as mock_time:
            mock_time.time.return_value = time.time() + 3601
            cache.get("How many students?")

        assert cache.stats()["misses"] == 1


class TestLRUEviction:
    def test_cache_lru_eviction_at_max_size(self):
        cache = QueryCache(max_size=3)
        _put(cache, "question 1")
        _put(cache, "question 2")
        _put(cache, "question 3")
        # This should evict "question 1" (oldest)
        _put(cache, "question 4")

        assert cache.get("question 1") is None  # evicted
        assert cache.get("question 2") is not None
        assert cache.get("question 3") is not None
        assert cache.get("question 4") is not None

    def test_lru_access_prevents_eviction(self):
        cache = QueryCache(max_size=3)
        _put(cache, "question 1")
        _put(cache, "question 2")
        _put(cache, "question 3")
        # Access question 1 — refreshes it to most-recently-used
        cache.get("question 1")
        # This should evict "question 2" (now the oldest unused)
        _put(cache, "question 4")

        assert cache.get("question 1") is not None  # refreshed, not evicted
        assert cache.get("question 2") is None       # evicted

    def test_evictions_tracked_in_stats(self):
        cache = QueryCache(max_size=2)
        _put(cache, "q1")
        _put(cache, "q2")
        _put(cache, "q3")  # evicts q1
        assert cache.stats()["evictions"] == 1


class TestNeverStoresEmpty:
    def test_cache_never_stores_empty_answer(self):
        cache = QueryCache()
        cache.put("How many students?", "SELECT 1", [], "")
        assert cache.get("How many students?") is None

    def test_cache_never_stores_none_answer(self):
        cache = QueryCache()
        cache.put("How many?", "SELECT 1", [], None)  # type: ignore[arg-type]
        assert cache.get("How many?") is None


class TestNormalisation:
    def test_cache_normalize_case_insensitive(self):
        cache = QueryCache()
        _put(cache, "How many STUDENTS are there?")
        result = cache.get("how many students are there?")
        assert result is not None

    def test_cache_normalize_strips_whitespace(self):
        cache = QueryCache()
        _put(cache, "  How many students?  ")
        result = cache.get("How many students?")
        assert result is not None


class TestStats:
    def test_cache_stats_tracks_hits_and_misses(self):
        cache = QueryCache()
        _put(cache, "q1")
        cache.get("q1")   # hit
        cache.get("q2")   # miss
        cache.get("q3")   # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 2

    def test_cache_hit_rate_calculation(self):
        cache = QueryCache()
        _put(cache, "q1")
        cache.get("q1")   # hit
        cache.get("q2")   # miss
        stats = cache.stats()
        assert abs(stats["hit_rate"] - 0.5) < 0.001

    def test_cache_hit_rate_zero_when_no_accesses(self):
        cache = QueryCache()
        assert cache.stats()["hit_rate"] == 0.0

    def test_cache_size_reflects_entries(self):
        cache = QueryCache()
        _put(cache, "q1")
        _put(cache, "q2")
        assert cache.stats()["size"] == 2


class TestInvalidate:
    def test_cache_invalidate_removes_entry(self):
        cache = QueryCache()
        _put(cache, "How many students?")
        removed = cache.invalidate("How many students?")
        assert removed is True
        assert cache.get("How many students?") is None

    def test_cache_invalidate_returns_false_if_not_found(self):
        cache = QueryCache()
        assert cache.invalidate("not in cache") is False

    def test_cache_invalidate_normalizes_key(self):
        cache = QueryCache()
        _put(cache, "How many STUDENTS?")
        removed = cache.invalidate("how many students?")
        assert removed is True


class TestClear:
    def test_cache_clear_resets_everything(self):
        cache = QueryCache()
        _put(cache, "q1")
        cache.get("q1")   # hit
        cache.get("q2")   # miss
        cache.clear()
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["evictions"] == 0

    def test_after_clear_entries_are_gone(self):
        cache = QueryCache()
        _put(cache, "How many students?")
        cache.clear()
        assert cache.get("How many students?") is None
