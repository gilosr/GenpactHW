"""
tests/test_database.py
──────────────────────
Block 6.1: Database layer tests.

8 groups, ~22 test items:
  1. Schema structure  — 4 tables exist, column names, indexes
  2. FK enforcement    — invalid advisor_id, invalid student_id
  3. Seed row counts   — 6 teachers, 20 students, 12 courses, 52 enrollments
  4. Seed edge cases   — zero-enrollment students/courses, NULL grades
  5. Status distribution — 45 completed, 4 active, 3 dropped
  6. get_schema()      — non-empty, all 4 tables, type keywords
  7. execute_query()   — COUNT, JOIN, AVG with status filter
  8. Read-only         — INSERT and DROP both raise ValueError
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from db.database import DatabaseManager


# ── Group 1: Schema Structure ────────────────────────────────────────────────


def test_all_four_tables_exist(seeded_engine):
    """All 4 university tables must be present in the schema."""
    insp = inspect(seeded_engine)
    tables = set(insp.get_table_names())
    assert {"teachers", "students", "courses", "enrollments"} <= tables


def test_enrollments_columns(seeded_engine):
    """enrollments table must have the exact column set defined in schema.sql."""
    insp = inspect(seeded_engine)
    cols = {c["name"] for c in insp.get_columns("enrollments")}
    expected = {
        "enrollment_id", "student_id", "course_id",
        "semester", "year", "grade", "status", "enrollment_date",
    }
    assert expected == cols


def test_students_columns(seeded_engine):
    """students table must have the exact column set defined in schema.sql."""
    insp = inspect(seeded_engine)
    cols = {c["name"] for c in insp.get_columns("students")}
    expected = {
        "student_id", "first_name", "last_name", "email",
        "major", "enrollment_year", "advisor_id",
    }
    assert expected == cols


def test_indexes_exist(seeded_engine):
    """All 8 performance indexes from schema.sql must be present."""
    insp = inspect(seeded_engine)
    all_indexes: set[str] = set()
    for table in ["teachers", "students", "courses", "enrollments"]:
        for idx in insp.get_indexes(table):
            all_indexes.add(idx["name"])

    expected = {
        "idx_enrollments_student",
        "idx_enrollments_course",
        "idx_enrollments_semester_year",
        "idx_enrollments_status",
        "idx_courses_teacher",
        "idx_courses_department",
        "idx_students_major",
        "idx_students_advisor",
    }
    assert expected <= all_indexes


# ── Group 2: Foreign Key Enforcement ────────────────────────────────────────


def test_fk_enforcement_rejects_invalid_advisor(in_memory_engine):
    """Inserting a student with a non-existent advisor_id must raise an error.

    SQLite silently ignores FKs without PRAGMA foreign_keys = ON.
    This test verifies our event-listener pragma enforcement actually works.
    """
    with pytest.raises(Exception):
        with in_memory_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO students "
                    "(first_name, last_name, email, major, enrollment_year, advisor_id) "
                    "VALUES ('Test', 'User', 'test@u.edu', 'CS', 2024, 9999)"
                )
            )


def test_fk_enforcement_rejects_invalid_student_id(in_memory_engine):
    """Inserting an enrollment with a non-existent student_id must raise an error."""
    with pytest.raises(Exception):
        with in_memory_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO enrollments "
                    "(student_id, course_id, semester, year, status, enrollment_date) "
                    "VALUES (9999, 9999, 'Fall', 2024, 'active', '2024-09-01')"
                )
            )


# ── Group 3: Seed Data Row Counts ────────────────────────────────────────────


@pytest.mark.parametrize("table,expected_count", [
    ("teachers",    6),
    ("students",   20),
    ("courses",    12),
    ("enrollments", 52),
])
def test_seed_data_row_counts(seeded_engine, table, expected_count):
    """Verify exact row count per table matches the authoritative seed data."""
    with seeded_engine.connect() as conn:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    assert count == expected_count


# ── Group 4: Seed Edge Cases ─────────────────────────────────────────────────


def test_students_with_zero_enrollments(seeded_engine):
    """Exactly 4 students have no enrollments.

    Quinn O'Brien (id=17), Rachel Adams (id=18), Sam Liu (id=19), Tara Evans (id=20)
    are deliberately seeded with no enrollment records to test LEFT JOIN / NULL handling.
    """
    with seeded_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT s.first_name, s.last_name
                FROM students s
                LEFT JOIN enrollments e ON s.student_id = e.student_id
                WHERE e.enrollment_id IS NULL
            """)
        ).fetchall()

    assert len(rows) == 4
    names = {f"{r[0]} {r[1]}" for r in rows}
    assert names == {"Quinn O'Brien", "Rachel Adams", "Sam Liu", "Tara Evans"}


def test_course_with_zero_enrollments(seeded_engine):
    """ENG101 is the only course with no enrolled students."""
    with seeded_engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.course_code
                FROM courses c
                LEFT JOIN enrollments e ON c.course_id = e.course_id
                WHERE e.enrollment_id IS NULL
            """)
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "ENG101"


def test_active_enrollments_have_null_grade(seeded_engine):
    """All 4 active enrollments must have NULL grade (in-progress courses)."""
    with seeded_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM enrollments WHERE status = 'active' AND grade IS NULL")
        ).scalar()
    assert count == 4


def test_dropped_enrollments_have_null_grade(seeded_engine):
    """All 3 dropped enrollments must have NULL grade."""
    with seeded_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM enrollments WHERE status = 'dropped' AND grade IS NULL")
        ).scalar()
    assert count == 3


# ── Group 5: Enrollment Status Distribution ──────────────────────────────────


def test_enrollment_status_distribution(seeded_engine):
    """Exact status breakdown: 45 completed, 4 active, 3 dropped.

    Status values are critical — grade queries MUST filter status='completed'.
    Testing the exact distribution ensures grade-average assertions are correct.
    """
    with seeded_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, COUNT(*) FROM enrollments GROUP BY status ORDER BY status")
        ).fetchall()

    status_map = dict(rows)
    assert status_map == {"active": 4, "completed": 45, "dropped": 3}


# ── Group 6: DatabaseManager.get_schema() ────────────────────────────────────


def test_get_schema_includes_all_tables(db_manager):
    """get_schema() must mention all 4 table names (dynamic introspection)."""
    schema = db_manager.get_schema()
    for table in ["teachers", "students", "courses", "enrollments"]:
        assert table in schema.lower()


def test_get_schema_includes_column_types(db_manager):
    """get_schema() must include column type information (INTEGER and TEXT)."""
    schema = db_manager.get_schema().upper()
    assert "INTEGER" in schema
    assert "TEXT" in schema


# ── Group 7: DatabaseManager.execute_query() ─────────────────────────────────


def test_execute_query_simple_count(db_manager):
    """Simple COUNT(*) query returns exactly 20 students."""
    rows = db_manager.execute_query("SELECT COUNT(*) AS cnt FROM students")
    assert rows == [{"cnt": 20}]


def test_execute_query_join(db_manager):
    """JOIN query returns CS101 with 9 enrolled students."""
    rows = db_manager.execute_query("""
        SELECT c.course_code, COUNT(e.student_id) AS student_count
        FROM courses c
        JOIN enrollments e ON c.course_id = e.course_id
        WHERE c.course_code = 'CS101'
        GROUP BY c.course_code
    """)
    assert len(rows) == 1
    assert rows[0]["student_count"] == 9


def test_execute_query_avg_grade_with_status_filter(db_manager):
    """AVG grade in CS101 filtered by status='completed' equals 80.5.

    CS101 grades: 92, 78.5, 85, 70, 88, 95, 62, 80, 74 → average = 80.5
    """
    rows = db_manager.execute_query("""
        SELECT ROUND(AVG(e.grade), 2) AS avg_grade
        FROM enrollments e
        JOIN courses c ON e.course_id = c.course_id
        WHERE c.course_code = 'CS101' AND e.status = 'completed'
    """)
    assert len(rows) == 1
    assert rows[0]["avg_grade"] == pytest.approx(80.5, abs=0.1)


# ── Group 8: Read-Only Enforcement ───────────────────────────────────────────


def test_execute_query_blocks_insert(db_manager):
    """execute_query raises ValueError for INSERT statements."""
    with pytest.raises(ValueError, match="destructive"):
        db_manager.execute_query(
            "INSERT INTO students VALUES (99, 'X', 'Y', 'z@u.edu', 'CS', 2024, NULL)"
        )


def test_execute_query_blocks_drop(db_manager):
    """execute_query raises ValueError for DROP TABLE statements."""
    with pytest.raises(ValueError, match="destructive"):
        db_manager.execute_query("DROP TABLE students")


def test_execute_query_allows_keyword_in_string_literal(db_manager):
    """DML keywords inside string literals must not trigger ValueError."""
    assert not DatabaseManager._has_blocked_keywords(
        "SELECT * FROM students WHERE first_name = 'CREATE'"
    )


def test_blocked_keywords_still_catches_real_insert():
    """Regression: real INSERT must still be detected."""
    assert DatabaseManager._has_blocked_keywords(
        "INSERT INTO students VALUES (99, 'X', 'Y', 'z@u.edu', 'CS', 2024, NULL)"
    )


# ---------------------------------------------------------------------------
# Singleton engine safety
# ---------------------------------------------------------------------------


def test_get_engine_warns_on_different_path(caplog):
    """Calling get_engine() with a different path after init logs a warning."""
    import logging
    from db.connection import get_engine, reset_db

    reset_db(":memory:")
    with caplog.at_level(logging.WARNING, logger="db.connection"):
        get_engine("/other/path.db")
    assert any("reset_db()" in r.message for r in caplog.records)
    reset_db(":memory:")


def test_get_engine_no_warning_without_explicit_path(caplog):
    """Calling get_engine() without arguments after init should NOT warn."""
    import logging
    from db.connection import get_engine, reset_db

    reset_db(":memory:")
    with caplog.at_level(logging.WARNING, logger="db.connection"):
        get_engine()
    assert not any("reset_db()" in r.message for r in caplog.records)
    reset_db(":memory:")
