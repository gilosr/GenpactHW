"""
tests/test_tracing.py
─────────────────────
Unit tests for tracing/tracer.py.

Covers all 7 verification points from the Block 4.1 spec:
  1. verify_langsmith_config — all env vars set
  2. verify_langsmith_config — missing API key
  3. format_trace readable output
  4. format_trace_json structure
  5. get_trace_summary detects retry
  6. get_trace_summary detects decline
  7. print_trace no crash (smoke test)
"""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest

from tracing.tracer import (
    format_trace,
    format_trace_json,
    get_trace_summary,
    print_trace,
    verify_langsmith_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NORMAL_STEPS = [
    "check_relevance: relevant",
    "fetch_schema: loaded schema (1432 chars)",
    "generate_sql: generated SQL — SELECT COUNT(*) FROM students",
    "validate_sql: query passed safety check",
    "execute_sql: returned 1 rows",
    "format_answer: answer formatted (42 chars)",
]

_RETRY_STEPS = [
    "check_relevance: relevant",
    "fetch_schema: loaded schema (1432 chars)",
    "generate_sql: generated SQL — SELECT bad_col FROM students",
    "validate_sql: query passed safety check",
    "execute_sql: FAILED — no such column: bad_col",
    "regenerate_sql: preparing retry (attempt 1 of 3)",
    "generate_sql: regenerated SQL — SELECT COUNT(*) FROM students",
    "validate_sql: query passed safety check",
    "execute_sql: returned 1 rows",
    "format_answer: answer formatted (42 chars)",
]

_DECLINE_STEPS = [
    "check_relevance: not_relevant",
    "polite_decline: declined off-topic question",
]

_ERROR_STEPS = [
    "check_relevance: relevant",
    "fetch_schema: loaded schema (1432 chars)",
    "generate_sql: generated SQL — SELECT bad_col FROM students",
    "validate_sql: query passed safety check",
    "execute_sql: FAILED — no such column: bad_col",
    "regenerate_sql: preparing retry (attempt 3 of 3)",
    "error_response: I wasn't able to answer your question.",
]


# ---------------------------------------------------------------------------
# Tests: verify_langsmith_config
# ---------------------------------------------------------------------------


class TestVerifyLangsmithConfig:
    def test_verify_langsmith_config_all_set(self):
        env = {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "ls__test_key",
            "LANGSMITH_PROJECT": "university-qa",
        }
        with patch.dict("os.environ", env, clear=False):
            result = verify_langsmith_config()
        assert result["configured"] is True
        assert result["project"] == "university-qa"
        assert len(result["warnings"]) == 0

    def test_verify_langsmith_config_missing_key(self):
        env = {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_PROJECT": "university-qa",
        }
        # Remove LANGSMITH_API_KEY if present
        with patch.dict("os.environ", env, clear=False):
            # Ensure key is absent
            import os
            os.environ.pop("LANGSMITH_API_KEY", None)
            result = verify_langsmith_config()
        assert result["configured"] is False
        assert any("API_KEY" in w for w in result["warnings"])

    def test_verify_langsmith_config_tracing_disabled(self):
        env = {
            "LANGSMITH_API_KEY": "ls__test_key",
        }
        import os
        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("LANGSMITH_TRACING", None)
            result = verify_langsmith_config()
        assert result["configured"] is False
        assert any("TRACING" in w for w in result["warnings"])

    def test_verify_langsmith_config_returns_dict_keys(self):
        with patch.dict("os.environ", {}, clear=True):
            result = verify_langsmith_config()
        assert "configured" in result
        assert "project" in result
        assert "warnings" in result
        assert isinstance(result["warnings"], list)


# ---------------------------------------------------------------------------
# Tests: format_trace
# ---------------------------------------------------------------------------


class TestFormatTrace:
    def test_format_trace_readable_output(self):
        output = format_trace(_NORMAL_STEPS)
        assert "1." in output
        assert "check_relevance" in output
        assert "6." in output
        assert "format_answer" in output

    def test_format_trace_empty_returns_placeholder(self):
        output = format_trace([])
        assert "no steps" in output.lower()

    def test_format_trace_numbered(self):
        steps = ["step_a: info", "step_b: info", "step_c: info"]
        output = format_trace(steps)
        assert "1." in output
        assert "2." in output
        assert "3." in output


# ---------------------------------------------------------------------------
# Tests: format_trace_json
# ---------------------------------------------------------------------------


class TestFormatTraceJson:
    def test_format_trace_json_structure(self):
        result = format_trace_json(
            steps=_NORMAL_STEPS,
            question="How many students?",
            answer="There are 20 students.",
            sql_query="SELECT COUNT(*) FROM students",
        )
        assert result["question"] == "How many students?"
        assert result["answer"] == "There are 20 students."
        assert result["sql_query"] == "SELECT COUNT(*) FROM students"
        assert result["steps"] == _NORMAL_STEPS
        assert result["node_count"] == len(_NORMAL_STEPS)
        assert result["has_retry"] is False
        assert "timestamp" in result

    def test_format_trace_json_detects_retry(self):
        result = format_trace_json(_RETRY_STEPS, "Q", "A")
        assert result["has_retry"] is True

    def test_format_trace_json_timestamp_format(self):
        result = format_trace_json(_NORMAL_STEPS, "Q", "A")
        # Should be ISO format ending in Z
        assert result["timestamp"].endswith("Z")

    def test_format_trace_json_serializable(self):
        import json
        result = format_trace_json(_NORMAL_STEPS, "Q", "A", "SELECT 1")
        # Should not raise
        json.dumps(result)


# ---------------------------------------------------------------------------
# Tests: get_trace_summary
# ---------------------------------------------------------------------------


class TestGetTraceSummary:
    def test_get_trace_summary_detects_retry(self):
        summary = get_trace_summary(_RETRY_STEPS)
        assert summary["retry_count"] == 1
        assert summary["had_error"] is False
        assert summary["was_declined"] is False

    def test_get_trace_summary_detects_decline(self):
        summary = get_trace_summary(_DECLINE_STEPS)
        assert summary["was_declined"] is True
        assert summary["had_error"] is False
        assert summary["retry_count"] == 0

    def test_get_trace_summary_detects_error(self):
        summary = get_trace_summary(_ERROR_STEPS)
        assert summary["had_error"] is True

    def test_get_trace_summary_nodes_visited_order(self):
        summary = get_trace_summary(_NORMAL_STEPS)
        nodes = summary["nodes_visited"]
        assert nodes[0] == "check_relevance"
        assert nodes[-1] == "format_answer"
        assert len(nodes) == len(_NORMAL_STEPS)

    def test_get_trace_summary_empty_steps(self):
        summary = get_trace_summary([])
        assert summary["nodes_visited"] == []
        assert summary["retry_count"] == 0
        assert summary["had_error"] is False
        assert summary["was_declined"] is False


# ---------------------------------------------------------------------------
# Tests: print_trace
# ---------------------------------------------------------------------------


class TestPrintTrace:
    def test_print_trace_no_crash(self, capsys):
        """Smoke test: print_trace should complete without raising."""
        result = {
            "question": "How many students are there?",
            "steps": _NORMAL_STEPS,
            "sql_query": "SELECT COUNT(*) FROM students",
            "answer": "There are 20 students.",
        }
        print_trace(result)  # must not raise
        captured = capsys.readouterr()
        assert "How many students" in captured.out
        assert "SELECT COUNT" in captured.out

    def test_print_trace_shows_answer(self, capsys):
        result = {
            "question": "Q",
            "steps": ["check_relevance: relevant"],
            "sql_query": "",
            "answer": "Final answer here.",
        }
        print_trace(result)
        captured = capsys.readouterr()
        assert "Final answer here." in captured.out

    def test_print_trace_handles_missing_keys(self, capsys):
        """print_trace should handle a partial result dict gracefully."""
        print_trace({})  # must not raise
        captured = capsys.readouterr()
        assert "N/A" in captured.out
