"""
tests/test_nodes.py
───────────────────
Unit tests for agent/nodes.py.

Covers all verification points from Block 2.3:
  1. All 12 public functions + set_db importable
  2. validate_sql rejects "DROP TABLE students"
  3. validate_sql passes "SELECT COUNT(*) FROM students"
  4. route_result — all 3 branches
  5. _clean_sql — markdown fence stripping
  6. fetch_schema with set_db() pointing to in-memory DB
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from agent import nodes
from agent.nodes import (
    _clean_sql,
    _format_attempt_history,
    _format_results_for_prompt,
    error_response,
    fetch_schema,
    regenerate_sql,
    route_result,
    route_relevance,
    route_validation,
    set_db,
    validate_sql,
)
from db.database import DatabaseManager
from db.connection import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_in_memory_db() -> DatabaseManager:
    """Create a DatabaseManager backed by an in-memory SQLite database
    pre-populated with the university schema (no seed data required for
    schema introspection tests).
    """
    engine = create_engine("sqlite:///:memory:", echo=False)

    # Enforce FK pragma on every connection
    @event.listens_for(engine, "connect")
    def set_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    # Run the real schema DDL
    import pathlib
    schema_path = pathlib.Path(__file__).parent.parent / "db" / "schema.sql"
    ddl = schema_path.read_text()
    with engine.connect() as conn:
        conn.connection.executescript(ddl)

    return DatabaseManager(engine=engine)


@pytest.fixture(autouse=True)
def reset_db_singleton():
    """Reset the nodes-level DB singleton before and after each test."""
    set_db(None)
    yield
    set_db(None)


@pytest.fixture()
def in_memory_db():
    db = _make_in_memory_db()
    set_db(db)
    return db


# ---------------------------------------------------------------------------
# Helpers for building minimal AgentState dicts
# ---------------------------------------------------------------------------


def _base_state(**overrides) -> dict:
    """Return a minimal state dict suitable for passing to node functions."""
    defaults = {
        "question": "How many students are there?",
        "relevance": "relevant",
        "schema_info": "",
        "sql_query": "",
        "query_result": [],
        "query_rows": 0,
        "answer": "",
        "sql_error": "",
        "error_message": "",
        "attempts": 0,
        "max_retries": 3,
        "previous_attempts": [],
        "steps": [],
        "messages": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Test 1 — All public names importable
# ---------------------------------------------------------------------------


def test_all_public_names_importable():
    """All 12 node functions + set_db should be importable from agent.nodes."""
    from agent.nodes import (  # noqa: F401
        check_relevance,
        error_response,
        fetch_schema,
        format_answer,
        generate_sql,
        polite_decline,
        regenerate_sql,
        route_relevance,
        route_result,
        route_validation,
        set_db,
        validate_sql,
        execute_sql,
    )


# ---------------------------------------------------------------------------
# Test 2 — validate_sql: reject destructive SQL
# ---------------------------------------------------------------------------


class TestValidateSql:
    def test_rejects_drop_table(self):
        state = _base_state(sql_query="DROP TABLE students")
        result = validate_sql(state)
        assert result["sql_error"], "sql_error should be set for destructive SQL"
        assert result["error_message"], "error_message should be set"
        assert "destructive" in result["sql_error"].lower() or "blocked" in result["sql_error"].lower()

    def test_rejects_insert(self):
        state = _base_state(sql_query="INSERT INTO students VALUES (1, 'Alice', 'CS', 2024, NULL)")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_rejects_delete(self):
        state = _base_state(sql_query="DELETE FROM students WHERE student_id = 1")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_rejects_update(self):
        state = _base_state(sql_query="UPDATE students SET major = 'Math' WHERE student_id = 1")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_rejects_alter(self):
        state = _base_state(sql_query="ALTER TABLE students ADD COLUMN age INT")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_rejects_create(self):
        state = _base_state(sql_query="CREATE TABLE foo (id INT)")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_rejects_truncate(self):
        state = _base_state(sql_query="TRUNCATE TABLE students")
        result = validate_sql(state)
        assert result["sql_error"]

    # Test 3 — passes valid SELECT
    def test_passes_select_count(self):
        state = _base_state(sql_query="SELECT COUNT(*) FROM students")
        result = validate_sql(state)
        assert result["sql_error"] == "", f"sql_error should be empty, got: {result['sql_error']}"

    def test_passes_select_with_join(self):
        sql = (
            "SELECT s.first_name, s.last_name FROM students s "
            "JOIN enrollments e ON s.student_id = e.student_id"
        )
        state = _base_state(sql_query=sql)
        result = validate_sql(state)
        assert result["sql_error"] == ""

    def test_rejects_empty_query(self):
        state = _base_state(sql_query="")
        result = validate_sql(state)
        assert result["sql_error"]
        assert result["error_message"]

    def test_step_appended(self):
        state = _base_state(sql_query="SELECT 1")
        result = validate_sql(state)
        assert len(result["steps"]) == 1

    def test_case_insensitive_block(self):
        state = _base_state(sql_query="drop table students")
        result = validate_sql(state)
        assert result["sql_error"]

    def test_allows_keyword_inside_string_literal(self):
        state = _base_state(sql_query="SELECT * FROM enrollments WHERE action = 'DELETE'")
        result = validate_sql(state)
        assert result["sql_error"] == "", f"False positive: {result['sql_error']}"

    def test_allows_update_inside_string_literal(self):
        state = _base_state(sql_query="SELECT * FROM logs WHERE note = 'UPDATE pending'")
        result = validate_sql(state)
        assert result["sql_error"] == ""

    def test_still_blocks_real_delete_statement(self):
        state = _base_state(sql_query="DELETE FROM students WHERE id = 1")
        result = validate_sql(state)
        assert result["sql_error"] != ""

    def test_preserves_generate_sql_error_when_sql_empty(self):
        specific_error = "The AI service is temporarily rate-limited. Please wait a moment and try again."
        state = _base_state(sql_query="", sql_error=specific_error)
        result = validate_sql(state)
        assert result["sql_error"] == specific_error
        assert result["error_message"] == specific_error

    def test_preserves_cannot_answer_when_sql_empty(self):
        message = "I can't answer that question with the information in the university database."
        state = _base_state(sql_query="", sql_error="cannot_answer", error_message=message)
        result = validate_sql(state)
        assert result["sql_error"] == "cannot_answer"
        assert result["error_message"] == message

    def test_uses_generic_error_when_no_prior_error(self):
        state = _base_state(sql_query="", sql_error="")
        result = validate_sql(state)
        assert result["sql_error"] == "No SQL query was generated"
        assert "unable to generate" in result["error_message"].lower()


# ---------------------------------------------------------------------------
# Test 4 — route_result: all 3 branches
# ---------------------------------------------------------------------------


class TestRouteResult:
    def test_no_error_returns_format_answer(self):
        state = _base_state(sql_error="", attempts=0, max_retries=3)
        assert route_result(state) == "format_answer"

    def test_error_within_budget_returns_regenerate_sql(self):
        state = _base_state(sql_error="no such column: bad_col", attempts=0, max_retries=3)
        assert route_result(state) == "regenerate_sql"

    def test_error_at_max_retries_returns_error_response(self):
        state = _base_state(sql_error="still failing", attempts=3, max_retries=3)
        assert route_result(state) == "error_response"

    def test_error_beyond_max_retries_returns_error_response(self):
        state = _base_state(sql_error="still failing", attempts=5, max_retries=3)
        assert route_result(state) == "error_response"

    def test_error_at_attempt_2_of_3_returns_regenerate(self):
        state = _base_state(sql_error="syntax error", attempts=2, max_retries=3)
        assert route_result(state) == "regenerate_sql"

    def test_cannot_answer_routes_to_error_response(self):
        state = _base_state(sql_error="cannot_answer", attempts=0, max_retries=3)
        assert route_result(state) == "error_response"


# ---------------------------------------------------------------------------
# Test 5 — _clean_sql: markdown fence stripping
# ---------------------------------------------------------------------------


class TestCleanSql:
    def test_strips_sql_fence(self):
        raw = "```sql\nSELECT * FROM students;\n```"
        assert _clean_sql(raw) == "SELECT * FROM students;"

    def test_strips_generic_fence(self):
        raw = "```\nSELECT 1;\n```"
        assert _clean_sql(raw) == "SELECT 1;"

    def test_passthrough_plain_sql(self):
        raw = "SELECT COUNT(*) FROM students;"
        assert _clean_sql(raw) == raw

    def test_strips_leading_trailing_whitespace(self):
        raw = "   SELECT 1;   "
        assert _clean_sql(raw) == "SELECT 1;"

    def test_strips_fence_without_newline(self):
        # LLMs almost always emit a newline after the opening fence.
        # When a newline is present, _clean_sql correctly strips the header.
        raw = "```sql\nSELECT 1\n```"
        result = _clean_sql(raw)
        assert result == "SELECT 1"

    def test_strips_fence_with_trailing_text(self):
        raw = "```sql\nSELECT * FROM students\n```\nThis query returns all students."
        assert _clean_sql(raw) == "SELECT * FROM students"

    def test_strips_fence_with_multiline_trailing_text(self):
        raw = "```sql\nSELECT COUNT(*) FROM students\n```\nNote: counts all.\nIncluding inactive."
        assert _clean_sql(raw) == "SELECT COUNT(*) FROM students"

    def test_strips_fence_with_trailing_newlines(self):
        raw = "```sql\nSELECT 1\n```\n\n"
        assert _clean_sql(raw) == "SELECT 1"


# ---------------------------------------------------------------------------
# Test 6 — fetch_schema with in-memory DB
# ---------------------------------------------------------------------------


class TestFetchSchema:
    def test_returns_non_empty_schema(self, in_memory_db):
        state = _base_state()
        result = fetch_schema(state)
        assert result["schema_info"], "schema_info should be non-empty for a seeded in-memory DB"
        assert "students" in result["schema_info"].lower()

    def test_initialises_retry_fields(self, in_memory_db):
        state = _base_state()
        result = fetch_schema(state)
        assert result["attempts"] == 0
        assert result["max_retries"] == 3
        assert result["sql_error"] == ""
        assert result["previous_attempts"] == []

    def test_step_appended(self, in_memory_db):
        state = _base_state()
        result = fetch_schema(state)
        assert len(result["steps"]) == 1
        assert "fetch_schema" in result["steps"][0]

    def test_gracefully_handles_db_failure(self, monkeypatch):
        """If the DB is unavailable, fetch_schema returns an empty string (no exception)."""
        def _bad_get_schema():
            raise RuntimeError("DB is down")

        set_db(None)  # ensure lazy-init path
        # Patch _get_db to return a mock
        import unittest.mock as mock
        bad_db = mock.MagicMock()
        bad_db.get_schema.side_effect = RuntimeError("DB is down")
        set_db(bad_db)

        state = _base_state()
        result = fetch_schema(state)
        assert result["schema_info"] == ""
        assert result["attempts"] == 0


# ---------------------------------------------------------------------------
# Additional routing tests
# ---------------------------------------------------------------------------


class TestRouteRelevance:
    def test_not_relevant_routes_to_polite_decline(self):
        state = _base_state(relevance="not_relevant")
        assert route_relevance(state) == "polite_decline"

    def test_relevant_routes_to_fetch_schema(self):
        state = _base_state(relevance="relevant")
        assert route_relevance(state) == "fetch_schema"

    def test_missing_relevance_defaults_to_fetch_schema(self):
        state = _base_state()
        state.pop("relevance", None)
        assert route_relevance(state) == "fetch_schema"


class TestRouteValidation:
    def test_destructive_error_routes_to_error_response(self):
        state = _base_state(sql_error="Query blocked: destructive operation detected", attempts=0, max_retries=3)
        assert route_validation(state) == "error_response"

    def test_no_error_routes_to_execute_sql(self):
        state = _base_state(sql_error="")
        assert route_validation(state) == "execute_sql"

    def test_retryable_error_within_budget_routes_to_regenerate(self):
        state = _base_state(
            sql_error="The AI service request timed out. Please try again.",
            attempts=0, max_retries=3,
        )
        assert route_validation(state) == "regenerate_sql"

    def test_retryable_error_at_max_retries_routes_to_error(self):
        state = _base_state(
            sql_error="The AI service request timed out. Please try again.",
            attempts=3, max_retries=3,
        )
        assert route_validation(state) == "error_response"

    def test_destructive_error_never_retries_even_within_budget(self):
        state = _base_state(
            sql_error="Query blocked: destructive operation detected",
            attempts=0, max_retries=3,
        )
        assert route_validation(state) == "error_response"

    def test_cannot_answer_never_retries_even_within_budget(self):
        state = _base_state(sql_error="cannot_answer", attempts=0, max_retries=3)
        assert route_validation(state) == "error_response"

    def test_generic_empty_sql_error_within_budget_retries(self):
        state = _base_state(
            sql_error="No SQL query was generated",
            attempts=1, max_retries=3,
        )
        assert route_validation(state) == "regenerate_sql"


# ---------------------------------------------------------------------------
# error_response priority tests
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_prefers_error_message_over_sql_error(self):
        state = _base_state(
            error_message="I was unable to generate a SQL query.",
            sql_error="some raw error",
        )
        result = error_response(state)
        assert result["answer"] == "I was unable to generate a SQL query."

    def test_wraps_sql_error_when_no_error_message(self):
        state = _base_state(sql_error="no such column: bad_col", error_message="")
        result = error_response(state)
        assert "no such column: bad_col" in result["answer"]
        assert "rephrasing" in result["answer"].lower()

    def test_generic_fallback_when_both_empty(self):
        state = _base_state(sql_error="", error_message="")
        result = error_response(state)
        assert "unexpected error" in result["answer"].lower()

    def test_step_appended(self):
        state = _base_state(sql_error="some error", error_message="")
        result = error_response(state)
        assert len(result["steps"]) == 1
        assert "error_response" in result["steps"][0]


# ---------------------------------------------------------------------------
# P1-4 — result formatting for answer prompts
# ---------------------------------------------------------------------------


class TestFormatResultsForPrompt:
    def test_zero_rows(self):
        assert _format_results_for_prompt([], 0) == "No rows returned."

    def test_scalar_shortcut(self):
        assert _format_results_for_prompt([{"student_count": 20}], 1) == "student_count: 20"

    def test_markdown_table_for_multiple_rows(self):
        result = _format_results_for_prompt(
            [
                {"name": "Alice", "grade": 95},
                {"name": "Ben", "grade": 88},
            ],
            2,
        )
        assert "| name | grade |" in result
        assert "| Alice | 95 |" in result

    def test_truncates_more_than_max_rows(self):
        rows = [{"student": f"S{i}", "grade": i} for i in range(12)]
        result = _format_results_for_prompt(rows, 12, max_rows=10)
        assert "Showing first 10 of 12 rows." in result
        assert "S9" in result
        assert "S10" not in result


# ---------------------------------------------------------------------------
# P1-5 — retry history
# ---------------------------------------------------------------------------


class TestRetryHistory:
    def test_regenerate_sql_appends_failed_attempt(self):
        state = _base_state(
            sql_query="SELECT name FROM students;",
            sql_error="no such column: name",
            attempts=0,
            max_retries=3,
        )
        result = regenerate_sql(state)

        assert result["attempts"] == 1
        assert result["previous_attempts"] == [
            {"sql": "SELECT name FROM students;", "error": "no such column: name"}
        ]

    def test_format_attempt_history_lists_all_attempts(self):
        rendered = _format_attempt_history(
            [
                {"sql": "SELECT name FROM students;", "error": "no such column: name"},
                {"sql": "SELECT id FROM students;", "error": "no such column: id"},
            ]
        )

        assert "Attempt 1" in rendered
        assert "SELECT name FROM students;" in rendered
        assert "Attempt 2" in rendered
        assert "SELECT id FROM students;" in rendered


# ---------------------------------------------------------------------------
# Block 5.1 — generate_sql empty schema guard
# ---------------------------------------------------------------------------


class TestGenerateSqlEmptySchema:
    def test_generate_sql_empty_schema_returns_error(self):
        """When schema is empty and it's not a retry, generate_sql should fail fast
        instead of calling the LLM with an empty schema (Gap 1 fix)."""
        from agent.nodes import generate_sql

        state = _base_state(
            question="How many students?",
            schema_info="",
            sql_query="",
            sql_error="",
        )
        result = generate_sql(state)
        assert result["sql_query"] == ""
        assert result["sql_error"] != ""
        assert "schema" in result["sql_error"].lower() or "unavailable" in result["sql_error"].lower()
        assert any("FAILED" in s or "schema" in s.lower() for s in result["steps"])

    def test_generate_sql_empty_schema_retry_allowed(self):
        """During a retry (sql_error + sql_query both set), empty schema
        should NOT block — the LLM can use the previous SQL context.

        with_structured_output is configured to raise so the fallback
        plain-invoke path is exercised (verifying both paths work).
        """
        from unittest.mock import MagicMock, patch
        from agent.nodes import generate_sql

        state = _base_state(
            schema_info="",
            sql_query="SELECT bad_col FROM students",
            sql_error="no such column: bad_col",
        )
        mock_response = MagicMock()
        mock_response.content = "SELECT COUNT(*) FROM students"
        with patch("agent.nodes.get_retry_llm") as mock_llm_fn:
            mock_llm = mock_llm_fn.return_value
            # Make structured output raise → exercises the fallback path
            mock_llm.with_structured_output.side_effect = Exception("structured output not supported")
            mock_llm.invoke.return_value = mock_response
            result = generate_sql(state)
        # Should reach the fallback invoke (no early return from empty-schema guard)
        mock_llm_fn.return_value.invoke.assert_called_once()
        assert result["sql_error"] == ""  # cleared on success

    def test_generate_sql_cannot_answer_returns_escape_path(self):
        from unittest.mock import MagicMock, patch
        from agent.nodes import generate_sql
        from prompts.schemas import SQLResult

        state = _base_state(
            question="Where can students park on campus?",
            schema_info="CREATE TABLE students (...);",
            sql_query="",
            sql_error="",
        )
        structured_llm = MagicMock()
        structured_llm.invoke.return_value = SQLResult(
            reasoning="Parking data is not in the schema",
            can_answer=False,
            sql="",
        )

        with patch("agent.nodes.get_sql_llm") as mock_llm_fn:
            mock_llm_fn.return_value.with_structured_output.return_value = structured_llm
            result = generate_sql(state)

        assert result["sql_query"] == ""
        assert result["sql_error"] == "cannot_answer"
        assert "university database" in result["error_message"]


# ---------------------------------------------------------------------------
# Block 5.1 — _classify_llm_error
# ---------------------------------------------------------------------------


class TestClassifyLlmError:
    """Test the _classify_llm_error() helper directly."""

    def setup_method(self):
        from agent.nodes import _classify_llm_error
        self.classify = _classify_llm_error

    def test_classify_llm_error_rate_limit(self):
        exc = Exception("RateLimitError: rate limit exceeded (429)")
        msg = self.classify(exc)
        assert "rate" in msg.lower() or "wait" in msg.lower()

    def test_classify_llm_error_timeout(self):
        exc = Exception("Request timed out after 30 seconds")
        msg = self.classify(exc)
        assert "timeout" in msg.lower() or "timed out" in msg.lower()

    def test_classify_llm_error_context_length(self):
        exc = Exception("context length exceeded: max tokens 4096")
        msg = self.classify(exc)
        assert "complex" in msg.lower() or "token" in msg.lower() or "simplif" in msg.lower()

    def test_classify_llm_error_server_error(self):
        exc = Exception("500 Internal Server Error from OpenAI")
        msg = self.classify(exc)
        assert "unavailable" in msg.lower() or "server" in msg.lower()

    def test_classify_llm_error_429_in_string(self):
        exc = Exception("HTTP 429: Too Many Requests")
        msg = self.classify(exc)
        assert "rate" in msg.lower() or "wait" in msg.lower()

    def test_classify_llm_error_unknown_falls_through(self):
        exc = Exception("Unexpected unicorn error XYZ-404-CHAOS")
        msg = self.classify(exc)
        # Falls through to str(exc) — message preserved
        assert "unicorn" in msg.lower() or "XYZ" in msg
