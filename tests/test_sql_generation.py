"""
tests/test_sql_generation.py
─────────────────────────────
Block 6.2: SQL generation pipeline tests.

Strategy: execution match (correct result), not exact SQL string match.

5 groups:
  1. Mocked SQL generation — 4 tiers (simple → CTE/window function)
  2. Destructive query detection via validate_sql
  3. Irrelevant question routing via check_relevance
  4. Status-aware grade filtering correctness
  5. Integration tests — real LLM + real graph  (@pytest.mark.integration)

Unit tests mock the LLM at the factory level (patch("agent.nodes.get_sql_llm"))
so pipeline orchestration is tested without LLM nondeterminism.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agent.nodes import check_relevance, execute_sql, generate_sql, validate_sql

_HAS_LLM_KEY = bool(os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _base_state(**overrides) -> dict:
    """Minimal AgentState dict for passing to node functions in tests."""
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


def _mock_llm_returning(sql: str):
    """Create a mock LLM whose invoke() returns a response with .content = sql."""
    mock_response = MagicMock()
    mock_response.content = sql
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = mock_response
    return mock_llm


def _run_pipeline(state: dict, sql: str, llm_patch_target: str = "agent.nodes.get_sql_llm") -> dict:
    """Run generate_sql → validate_sql → execute_sql with a mocked LLM.

    Returns the final state dict after all three node calls.
    """
    with patch(llm_patch_target, return_value=_mock_llm_returning(sql)):
        state.update(generate_sql(state))
    state.update(validate_sql(state))
    state.update(execute_sql(state))
    return state


# ── Group 1: Mocked SQL Generation — Pipeline Correctness ────────────────────


class TestTier1Simple:
    """Tier 1: Simple COUNT — single table, no JOINs."""

    def test_count_students(self, db_for_nodes):
        """Mocked SQL returns 20 for 'How many students are there?'."""
        sql = "SELECT COUNT(*) AS student_count FROM students"
        state = _base_state(
            question="How many students are there?",
            schema_info=db_for_nodes.get_schema(),
        )
        state = _run_pipeline(state, sql)
        assert state["sql_error"] == ""
        assert state["query_result"][0]["student_count"] == 20


class TestTier2Medium:
    """Tier 2: JOIN + GROUP BY — two tables, aggregation."""

    def test_enrollment_count_per_course(self, db_for_nodes):
        """JOIN + GROUP BY returns multiple courses; CS101 has 9 enrolled students."""
        sql = """
SELECT c.course_code, COUNT(e.student_id) AS student_count
FROM courses c
JOIN enrollments e ON c.course_id = e.course_id
GROUP BY c.course_code
ORDER BY student_count DESC
"""
        state = _base_state(
            question="How many students are enrolled in each course?",
            schema_info=db_for_nodes.get_schema(),
        )
        state = _run_pipeline(state, sql)
        rows = state["query_result"]
        assert state["sql_error"] == ""
        assert len(rows) > 1  # multiple courses have enrollments
        cs101 = next(r for r in rows if r["course_code"] == "CS101")
        assert cs101["student_count"] == 9


class TestTier3Hard:
    """Tier 3: 3-table JOIN + AVG + status filter."""

    def test_avg_grade_per_teacher_excludes_walsh(self, db_for_nodes):
        """3-table JOIN returns 5 teachers; Prof. Walsh excluded (ENG101 has 0 enrollments)."""
        sql = """
SELECT t.first_name || ' ' || t.last_name AS teacher_name,
       ROUND(AVG(e.grade), 2) AS avg_grade
FROM teachers t
JOIN courses c ON t.teacher_id = c.teacher_id
JOIN enrollments e ON c.course_id = e.course_id
WHERE e.status = 'completed'
GROUP BY t.teacher_id
"""
        state = _base_state(
            question="What is the average grade per teacher?",
            schema_info=db_for_nodes.get_schema(),
        )
        state = _run_pipeline(state, sql)
        rows = state["query_result"]
        assert state["sql_error"] == ""
        # 6 teachers, but Prof. Walsh (English/ENG101) has no completed enrollments
        assert len(rows) == 5
        for row in rows:
            assert 60.0 <= row["avg_grade"] <= 100.0


class TestTier4VeryHard:
    """Tier 4: CTE + window function (RANK OVER PARTITION BY)."""

    def test_top_student_per_department(self, db_for_nodes):
        """CTE + RANK() finds the top-grade student in each active department."""
        sql = """
WITH student_dept_avg AS (
    SELECT s.student_id,
           s.first_name || ' ' || s.last_name AS student_name,
           c.department,
           ROUND(AVG(e.grade), 2) AS avg_grade,
           RANK() OVER (PARTITION BY c.department ORDER BY AVG(e.grade) DESC) AS rk
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    JOIN courses c ON e.course_id = c.course_id
    WHERE e.status = 'completed'
    GROUP BY s.student_id, c.department
)
SELECT department, student_name, avg_grade
FROM student_dept_avg
WHERE rk = 1
"""
        state = _base_state(
            question="Which student has the highest average grade in each department?",
            schema_info=db_for_nodes.get_schema(),
        )
        state = _run_pipeline(state, sql)
        rows = state["query_result"]
        assert state["sql_error"] == ""
        depts = {r["department"] for r in rows}
        # English (ENG101) has 0 enrollments — should not appear
        assert "Computer Science" in depts
        assert "Mathematics" in depts
        assert "Physics" in depts
        assert "English" not in depts


# ── Group 2: Destructive Query Detection ─────────────────────────────────────


class TestDestructiveQueryDetection:
    """validate_sql blocks DML/DDL before it reaches the DB."""

    def test_drop_table_blocked(self):
        """validate_sql sets sql_error for DROP TABLE."""
        state = _base_state(sql_query="DROP TABLE students")
        result = validate_sql(state)
        assert result["sql_error"] != ""
        assert "blocked" in result["sql_error"].lower() or "destructive" in result["sql_error"].lower()

    def test_delete_blocked(self):
        """validate_sql sets sql_error for DELETE FROM."""
        state = _base_state(sql_query="DELETE FROM enrollments WHERE student_id = 1")
        result = validate_sql(state)
        assert result["sql_error"] != ""


# ── Group 3: Irrelevant Question Decline ─────────────────────────────────────


class TestIrrelevantQuestionDecline:
    """check_relevance correctly labels off-topic questions."""

    def test_irrelevant_question_returns_not_relevant(self):
        """Mocked relevance LLM returning 'not_relevant' is classified correctly."""
        state = _base_state(question="What's the weather like today?")
        with patch("agent.nodes.get_relevance_llm", return_value=_mock_llm_returning("not_relevant")):
            result = check_relevance(state)
        assert result["relevance"] == "not_relevant"


# ── Group 4: Status-Aware Grade Filtering ────────────────────────────────────


class TestStatusAwareGradeFiltering:
    """Demonstrate that grade queries must filter status='completed'."""

    def test_avg_grade_with_status_filter_in_valid_range(self, db_for_nodes):
        """AVG(grade) WHERE status='completed' returns a non-null value in [60, 100]."""
        rows = db_for_nodes.execute_query(
            "SELECT AVG(grade) AS avg FROM enrollments WHERE status = 'completed'"
        )
        avg = rows[0]["avg"]
        assert avg is not None
        assert 60.0 <= avg <= 100.0

    def test_completed_count_equals_non_null_grade_count(self, db_for_nodes):
        """Exactly 45 enrollments are completed, and all 45 have non-null grades.

        This confirms that status='completed' ↔ grade IS NOT NULL in the seed data,
        validating that the status filter is the correct predicate for grade queries.
        """
        by_status = db_for_nodes.execute_query(
            "SELECT COUNT(*) AS cnt FROM enrollments WHERE status = 'completed'"
        )
        by_grade = db_for_nodes.execute_query(
            "SELECT COUNT(*) AS cnt FROM enrollments WHERE grade IS NOT NULL"
        )
        assert by_status[0]["cnt"] == 45
        assert by_grade[0]["cnt"] == 45


# ── Group 5: Integration Tests ───────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(not _HAS_LLM_KEY, reason="Requires OPENAI_API_KEY or ANTHROPIC_API_KEY")
class TestSQLGenerationIntegration:
    """End-to-end tests using the real LLM + real compiled graph.

    Skipped in default CI — run with: pytest -m integration
    Validates execution match (correct result), not exact SQL text.
    """

    def test_simple_count_returns_20(self, db_for_nodes):
        """Real LLM: 'How many students?' → answer contains '20'."""
        from agent.graph import create_graph
        graph = create_graph().compile()
        result = graph.invoke({"question": "How many students are there?"})
        assert "20" in result["answer"]

    def test_cs101_enrollment_count(self, db_for_nodes):
        """Real LLM: 'How many students in CS101?' → answer contains '9'."""
        from agent.graph import create_graph
        graph = create_graph().compile()
        result = graph.invoke({"question": "How many students are enrolled in CS101?"})
        assert "9" in result["answer"]

    def test_irrelevant_question_declined(self, db_for_nodes):
        """Real LLM: off-topic question triggers polite_decline path."""
        from agent.graph import create_graph
        graph = create_graph().compile()
        result = graph.invoke({"question": "What is the weather like today?"})
        assert any("polite_decline" in s for s in result["steps"])

    def test_avg_grade_query_produces_answer(self, db_for_nodes):
        """Real LLM: average grade query returns a non-empty answer with SQL trace."""
        from agent.graph import create_graph
        graph = create_graph().compile()
        result = graph.invoke({"question": "What is the average grade in CS101?"})
        assert result["answer"] != ""
        assert any("generate_sql" in s for s in result["steps"])
