"""
tests/test_conversation_manager.py
───────────────────────────────────
Unit tests for agent/conversation_manager.py (ConversationManager).

Covers all 8 verification points from the Block 3.1 spec:
  1. create_session returns UUID
  2. ask returns required fields (mock app)
  3. Session turn count increments
  4. reset_session clears history
  5. get_session_info exists vs. not
  6. Contextual question includes history
  7. ask with cache hit returns cached=True
  8. ask with history bypasses cache (follow-ups never cached)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from agent.cache import QueryCache
from agent.conversation_manager import ConversationManager


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_mock_app(answer: str = "There are 20 students.") -> MagicMock:
    """Build a mock compiled graph that returns a fixed answer."""
    mock_app = MagicMock()
    mock_app.invoke.return_value = {
        "answer": answer,
        "steps": ["check_relevance: relevant", "fetch_schema: loaded schema (1432 chars)"],
        "sql_query": "SELECT COUNT(*) FROM students",
        "query_result": [{"count": 20}],
    }
    return mock_app


def _make_cm(answer: str = "There are 20 students.", cache: QueryCache | None = None):
    """Build a ConversationManager with a mock app (no real graph needed)."""
    return ConversationManager(app=_make_mock_app(answer), cache=cache)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_create_session_returns_uuid(self):
        cm = _make_cm()
        session_id = cm.create_session()
        # Should be parseable as a UUID4
        parsed = uuid.UUID(session_id, version=4)
        assert str(parsed) == session_id

    def test_each_session_is_unique(self):
        cm = _make_cm()
        s1 = cm.create_session()
        s2 = cm.create_session()
        assert s1 != s2

    def test_new_session_starts_with_zero_turns(self):
        cm = _make_cm()
        s = cm.create_session()
        info = cm.get_session_info(s)
        assert info["turn_count"] == 0
        assert info["exists"] is True


class TestAsk:
    def test_ask_returns_required_fields(self):
        cm = _make_cm()
        s = cm.create_session()
        result = cm.ask("How many students are there?", s)
        assert "answer" in result
        assert "steps" in result
        assert "sql_query" in result
        assert "thread_id" in result
        assert "turn" in result
        assert "cached" in result

    def test_ask_returns_correct_thread_id(self):
        cm = _make_cm()
        s = cm.create_session()
        result = cm.ask("How many students?", s)
        assert result["thread_id"] == s

    def test_ask_returns_cached_false_on_first_call(self):
        cm = _make_cm()
        s = cm.create_session()
        result = cm.ask("How many students?", s)
        assert result["cached"] is False


class TestTurnCount:
    def test_session_turn_count_increments(self):
        cm = _make_cm()
        s = cm.create_session()
        cm.ask("First question", s)
        cm.ask("Second question", s)
        info = cm.get_session_info(s)
        assert info["turn_count"] == 2

    def test_turn_field_reflects_turn_number(self):
        cm = _make_cm()
        s = cm.create_session()
        r1 = cm.ask("Q1", s)
        r2 = cm.ask("Q2", s)
        assert r1["turn"] == 1
        assert r2["turn"] == 2


class TestResetSession:
    def test_reset_session_clears_history(self):
        cm = _make_cm()
        s = cm.create_session()
        cm.ask("Q1", s)
        cm.ask("Q2", s)
        cm.reset_session(s)
        info = cm.get_session_info(s)
        assert info["exists"] is False
        assert info["turn_count"] == 0

    def test_reset_session_unknown_id_no_error(self):
        cm = _make_cm()
        # Should not raise
        cm.reset_session("nonexistent-thread-id")


class TestGetSessionInfo:
    def test_get_session_info_exists(self):
        cm = _make_cm()
        s = cm.create_session()
        info = cm.get_session_info(s)
        assert info["exists"] is True
        assert info["thread_id"] == s

    def test_get_session_info_not_exists(self):
        cm = _make_cm()
        info = cm.get_session_info("does-not-exist")
        assert info["exists"] is False
        assert info["turn_count"] == 0


class TestContextualQuestion:
    def test_contextual_question_includes_history(self):
        cm = _make_cm()
        history = [
            ("How many students are there?", "There are 20 students."),
            ("What about teachers?", "There are 6 teachers."),
        ]
        augmented = cm._build_contextual_question("How many of them study CS?", history)
        assert "How many students are there?" in augmented
        assert "There are 20 students." in augmented
        assert "What about teachers?" in augmented
        assert "How many of them study CS?" in augmented

    def test_contextual_question_structure(self):
        cm = _make_cm()
        history = [("Q1", "A1")]
        augmented = cm._build_contextual_question("Current question", history)
        assert "Previous conversation" in augmented
        assert "Current question:" in augmented

    def test_graph_receives_augmented_question_on_followup(self):
        mock_app = _make_mock_app()
        cm = ConversationManager(app=mock_app)
        s = cm.create_session()
        cm.ask("How many students?", s)
        cm.ask("What about teachers?", s)

        # Second call should pass augmented question to graph
        second_call_args = mock_app.invoke.call_args_list[1]
        question_sent = second_call_args[0][0]["question"]
        assert "Previous conversation" in question_sent
        assert "What about teachers?" in question_sent

    def test_history_stores_original_not_augmented(self):
        """History must store original question to prevent recursive nesting."""
        mock_app = _make_mock_app()
        cm = ConversationManager(app=mock_app)
        s = cm.create_session()
        cm.ask("How many students?", s)
        cm.ask("What about CS students?", s)

        # Check history entries are original questions
        history = cm._history[s]
        assert history[0][0] == "How many students?"
        assert history[1][0] == "What about CS students?"
        # Not augmented
        assert "Previous conversation" not in history[0][0]
        assert "Previous conversation" not in history[1][0]


class TestCacheIntegration:
    def test_ask_with_cache_hit_returns_cached_true(self):
        cache = QueryCache()
        # Pre-populate the cache
        cache.put(
            "How many students?",
            "SELECT COUNT(*) FROM students",
            [{"count": 20}],
            "There are 20 students.",
        )
        cm = _make_cm(cache=cache)
        s = cm.create_session()
        result = cm.ask("How many students?", s)
        assert result["cached"] is True
        assert result["answer"] == "There are 20 students."

    def test_cache_hit_does_not_call_graph(self):
        cache = QueryCache()
        cache.put("How many students?", "SELECT 1", [], "Cached answer")
        mock_app = _make_mock_app()
        cm = ConversationManager(app=mock_app, cache=cache)
        s = cm.create_session()
        cm.ask("How many students?", s)
        mock_app.invoke.assert_not_called()

    def test_ask_with_history_bypasses_cache(self):
        """Follow-up questions must NOT be served from cache."""
        cache = QueryCache()
        # Pre-populate cache with the same text as the follow-up
        cache.put(
            "What about teachers?",
            "SELECT COUNT(*) FROM teachers",
            [{"count": 6}],
            "Cached teacher answer (wrong context)",
        )
        mock_app = _make_mock_app("Live graph answer for teachers")
        cm = ConversationManager(app=mock_app, cache=cache)
        s = cm.create_session()
        # First question establishes history
        cm.ask("How many students?", s)
        # Second question has history — must bypass cache
        result = cm.ask("What about teachers?", s)
        assert result["cached"] is False
        assert result["answer"] == "Live graph answer for teachers"
        # Graph was called for both turns
        assert mock_app.invoke.call_count == 2

    def test_bypass_cache_flag_forces_graph_call(self):
        cache = QueryCache()
        cache.put("How many students?", "SELECT 1", [], "Cached answer")
        mock_app = _make_mock_app()
        cm = ConversationManager(app=mock_app, cache=cache)
        s = cm.create_session()
        result = cm.ask("How many students?", s, bypass_cache=True)
        assert result["cached"] is False
        mock_app.invoke.assert_called_once()

    def test_cache_hit_includes_steps_key(self):
        cache = QueryCache()
        cache.put(
            "How many students?",
            "SELECT COUNT(*) FROM students",
            [{"count": 20}],
            "There are 20 students.",
        )
        cm = _make_cm(cache=cache)
        s = cm.create_session()
        result = cm.ask("How many students?", s)
        assert result["cached"] is True
        assert "steps" in result
        assert isinstance(result["steps"], list)
        assert len(result["steps"]) >= 1
        assert "cache_hit" in result["steps"][0]


class TestErrorCachingPrevention:
    def test_error_response_not_cached(self):
        cache = QueryCache()
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "answer": "I wasn't able to answer your question. The database query encountered an error.",
            "steps": ["check_relevance: relevant", "error_response: I wasn't able to answer..."],
            "sql_query": "",
            "query_result": [],
        }
        cm = ConversationManager(app=mock_app, cache=cache)
        s = cm.create_session()
        cm.ask("How many students?", s)
        assert cache.get("how many students?") is None

    def test_successful_response_still_cached(self):
        cache = QueryCache()
        mock_app = MagicMock()
        mock_app.invoke.return_value = {
            "answer": "There are 20 students.",
            "steps": ["check_relevance: relevant", "format_answer: answer formatted (22 chars)"],
            "sql_query": "SELECT COUNT(*) FROM students",
            "query_result": [{"count": 20}],
        }
        cm = ConversationManager(app=mock_app, cache=cache)
        s = cm.create_session()
        cm.ask("How many students?", s)
        assert cache.get("how many students?") is not None
