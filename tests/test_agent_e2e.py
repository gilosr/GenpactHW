"""
tests/test_agent_e2e.py
───────────────────────
End-to-end tests for the compiled LangGraph agent pipeline.

Tests the full compiled graph as a black box, verifying all execution paths:
  - Group 1: Happy path (relevant question → SQL → answer)
  - Group 2: Retry path (bad SQL → failure → regenerate → success)
  - Group 3: Decline path (irrelevant question → polite_decline)
  - Group 4: Max retries exhausted (all SQL attempts fail → error_response)
  - Group 5: Trace verification via get_trace_summary()
  - Group 6: Answer format validation (prose, not raw SQL or dict)
  - Group 7: Integration E2E (real LLMs, real DB, @pytest.mark.integration)

All non-integration tests mock all LLM factory functions for determinism
and speed — no API keys required. Only Group 7 calls real LLMs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.graph import create_graph
from tracing.tracer import get_trace_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm(content: str) -> MagicMock:
    """Return a mock LLM whose .invoke() returns a response with .content."""
    mock_resp = MagicMock()
    mock_resp.content = content
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_resp
    return mock_llm


def _configure_mock(mock_factory: MagicMock, content: str) -> None:
    """Make a patched factory return _mock_llm(content) when called."""
    mock_factory.return_value = _mock_llm(content)


# ---------------------------------------------------------------------------
# Group 1: Happy Path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Verify the full relevant-question pipeline produces a well-formed result."""

    def test_happy_path_produces_answer(self, db_for_nodes):
        """Happy path: relevant question → SQL execution → natural-language answer."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students in the database.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        assert "answer" in result
        assert "steps" in result
        assert result["answer"] == "There are 20 students in the database."

    def test_happy_path_steps_contain_expected_nodes(self, db_for_nodes):
        """Steps must include check_relevance at the start and format_answer at the end."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students in the database.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        step_names = [s.split(":")[0].strip() for s in result["steps"]]
        assert "check_relevance" in step_names
        assert "format_answer" in step_names

    def test_happy_path_exposes_sql_query_in_output(self, db_for_nodes):
        """OutputState must include sql_query and query_result."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        assert "sql_query" in result
        assert result["sql_query"] != ""
        assert "query_result" in result
        assert len(result["query_result"]) > 0


# ---------------------------------------------------------------------------
# Group 2: Retry Path
# ---------------------------------------------------------------------------


class TestRetryPath:
    """Verify the retry cycle: bad SQL → execute fails → regenerate → success."""

    def test_retry_path_succeeds_on_second_attempt(self, db_for_nodes):
        """First SQL has bad column → execute fails → retry generates valid SQL → answer."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_retry_llm") as mock_retry, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT bad_column FROM students")
            _configure_mock(mock_retry, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        assert "20" in result["answer"]
        assert any("regenerate_sql" in s for s in result["steps"])

    def test_retry_path_answer_is_non_empty(self, db_for_nodes):
        """After a successful retry, the answer must be a non-empty string."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_retry_llm") as mock_retry, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT bad_column FROM students")
            _configure_mock(mock_retry, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        assert result["answer"]
        assert len(result["answer"]) > 0


# ---------------------------------------------------------------------------
# Group 3: Decline Path
# ---------------------------------------------------------------------------


class TestDeclinePath:
    """Verify off-topic questions get a polite decline without touching SQL generation."""

    def test_decline_path_for_irrelevant_question(self):
        """Off-topic question → polite_decline in steps, generate_sql absent."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "not_relevant")
            _configure_mock(mock_ans, "I can only help with university data.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "What is the weather today?"})

        step_names = [s.split(":")[0].strip() for s in result["steps"]]
        assert "polite_decline" in step_names
        assert "generate_sql" not in step_names


# ---------------------------------------------------------------------------
# Group 4: Max Retries Exhausted
# ---------------------------------------------------------------------------


class TestMaxRetriesExhausted:
    """Verify that repeated SQL failures eventually surface an error to the user."""

    def test_max_retries_exhausted_produces_error(self, db_for_nodes):
        """All SQL attempts fail → error_response in steps → non-empty answer."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_retry_llm") as mock_retry:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT nonexistent_col FROM students")
            _configure_mock(mock_retry, "SELECT still_wrong_col FROM students")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        step_names = [s.split(":")[0].strip() for s in result["steps"]]
        assert "error_response" in step_names
        regen_steps = [s for s in result["steps"] if "regenerate_sql" in s]
        assert len(regen_steps) >= 1
        assert result["answer"]


# ---------------------------------------------------------------------------
# Group 5: Trace Verification
# ---------------------------------------------------------------------------


class TestTraceVerification:
    """Validate get_trace_summary() output against known execution paths."""

    def test_happy_path_trace_structure(self, db_for_nodes):
        """Happy path trace: exact node order, zero retries, no errors, not declined."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students in the database.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        summary = get_trace_summary(result["steps"])
        expected_nodes = [
            "check_relevance",
            "fetch_schema",
            "generate_sql",
            "validate_sql",
            "execute_sql",
            "format_answer",
        ]
        assert summary["nodes_visited"] == expected_nodes
        assert summary["retry_count"] == 0
        assert summary["had_error"] is False
        assert summary["was_declined"] is False

    def test_retry_path_trace_structure(self, db_for_nodes):
        """Retry path trace: retry_count >= 1, had_error is False (succeeded after retry)."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_retry_llm") as mock_retry, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT bad_column FROM students")
            _configure_mock(mock_retry, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        summary = get_trace_summary(result["steps"])
        assert summary["retry_count"] >= 1
        assert summary["had_error"] is False
        assert summary["was_declined"] is False


# ---------------------------------------------------------------------------
# Group 6: Answer Format Validation
# ---------------------------------------------------------------------------


class TestAnswerFormatValidation:
    """Verify the answer is human-readable prose, not raw SQL or a Python dict."""

    def test_answer_is_human_readable_string(self, db_for_nodes):
        """Answer must be a non-empty string that looks like prose, not SQL or a dict."""
        with patch("agent.nodes.get_relevance_llm") as mock_rel, \
             patch("agent.nodes.get_sql_llm") as mock_sql, \
             patch("agent.nodes.get_answer_llm") as mock_ans:
            _configure_mock(mock_rel, "relevant")
            _configure_mock(mock_sql, "SELECT COUNT(*) AS cnt FROM students")
            _configure_mock(mock_ans, "There are 20 students in the database.")
            graph = create_graph().compile()
            result = graph.invoke({"question": "How many students are there?"})

        answer = result["answer"]
        assert isinstance(answer, str)
        assert len(answer) > 0
        assert not answer.startswith("{")
        assert not answer.upper().startswith("SELECT")


# ---------------------------------------------------------------------------
# Group 7: Integration E2E
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIntegrationE2E:
    """Full real invocation with no mocks. Requires API keys and the seeded DB."""

    def test_full_real_invocation_count_students(self, db_for_nodes):
        """Real LLMs + seeded in-memory DB: 'How many students?' → answer contains '20'."""
        graph = create_graph().compile()
        result = graph.invoke({"question": "How many students are there?"})
        assert "20" in result["answer"]
