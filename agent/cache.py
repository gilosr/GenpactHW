"""
agent/cache.py
──────────────
LRU query cache with TTL expiration for the university QA agent.

Caches question -> {sql_query, query_result, answer} for exact matches.
Key normalized to lowercase + stripped whitespace.

Design decisions:
- Exact-match only (not semantic similarity) — simple, deterministic,
  zero chance of serving semantically-similar-but-wrong cached results
- In-memory OrderedDict for O(1) LRU operations
- TTL expiration (default 1h) — prevents stale answers if DB changes
- Max 128 entries with LRU eviction — bounded memory
- Never caches error responses or partial results (empty answer)
- Only caches standalone questions; the interaction with multi-turn context
  is handled by ConversationManager, not by the cache itself

Production upgrade path:
- Swap in Redis for shared state across processes
- Add semantic similarity matching with embeddings for fuzzy lookup
"""

from __future__ import annotations

import time
from collections import OrderedDict

from config import settings


class QueryCache:
    """LRU cache for question -> SQL/result/answer triples.

    Thread-safety note: not thread-safe. For concurrent production use,
    add a threading.Lock around get() and put() operations.
    """

    def __init__(self, max_size: int | None = None, ttl_seconds: int | None = None) -> None:
        """Initialise the cache.

        Args:
            max_size: Maximum number of entries before LRU eviction. Defaults to config value.
            ttl_seconds: Time-to-live in seconds. Defaults to config value.
        """
        self._max_size = max_size if max_size is not None else settings.cache.max_size
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.cache.ttl_seconds
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _normalize(self, question: str) -> str:
        """Produce a deterministic cache key from a question string.

        Case-insensitive, whitespace-stripped to handle minor variations
        like leading/trailing spaces or mixed capitalisation.
        """
        return question.strip().lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, question: str) -> dict | None:
        """Look up a cached result for a question.

        Returns a copy of the cached result dict, or None on miss/expiry.
        Expired entries are evicted on read (lazy expiration).

        Moves the entry to the end of the OrderedDict on hit so it is
        the last to be evicted (most-recently-used).

        Args:
            question: The raw user question string.

        Returns:
            dict with keys "sql_query", "query_result", "answer" on hit;
            None on miss or expired entry.
        """
        key = self._normalize(question)
        if key not in self._cache:
            self._misses += 1
            return None

        entry = self._cache[key]

        # TTL check — lazy eviction on read
        if time.time() - entry["timestamp"] > self._ttl_seconds:
            del self._cache[key]
            self._misses += 1
            return None

        # LRU refresh — move to end so it is evicted last
        self._cache.move_to_end(key)
        self._hits += 1
        return {
            "sql_query": entry["sql_query"],
            "query_result": entry["query_result"],
            "answer": entry["answer"],
        }

    def put(
        self,
        question: str,
        sql_query: str,
        query_result: list,
        answer: str,
    ) -> None:
        """Store a result in the cache.

        No-ops if answer is empty (never cache error/partial responses).
        Evicts the least-recently-used entry when at capacity.

        Args:
            question: The raw user question string (will be normalised).
            sql_query: The SQL query that was executed.
            query_result: The raw query results (list of dicts).
            answer: The formatted natural-language answer.
        """
        if not answer:  # never cache empty/error responses
            return

        key = self._normalize(question)
        self._cache[key] = {
            "sql_query": sql_query,
            "query_result": query_result,
            "answer": answer,
            "timestamp": time.time(),
        }
        # Move to end (most-recently-used position)
        self._cache.move_to_end(key)

        # LRU eviction: remove oldest (from the beginning)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
            self._evictions += 1

    def invalidate(self, question: str) -> bool:
        """Remove a specific entry from the cache.

        Args:
            question: The question whose cached result should be removed.

        Returns:
            True if the entry existed and was removed; False if not found.
        """
        key = self._normalize(question)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        """Flush all entries and reset hit/miss/eviction counters."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict:
        """Return cache performance statistics.

        Returns:
            dict with keys:
              - "size" (int): Current number of cached entries
              - "hits" (int): Total cache hits since last clear
              - "misses" (int): Total cache misses since last clear
              - "hit_rate" (float): hits / (hits + misses), or 0.0
              - "evictions" (int): Total LRU evictions since last clear
        """
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "evictions": self._evictions,
        }
