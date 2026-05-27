"""
tests/evals/conftest.py
───────────────────────
Shared fixtures and pytest hooks for the eval suite.

Usage:
    pytest -m eval                     # run all evals (requires API key)
    pytest tests/evals/ --collect-only # list cases without running LLM

All eval tests are skipped automatically when no API key is present.
"""

from __future__ import annotations

import os
import pathlib
from collections import defaultdict

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from agent.llm import get_relevance_llm, get_sql_llm
from db.database import DatabaseManager
from db.seed import (
    _insert_courses,
    _insert_enrollments,
    _insert_students,
    _insert_teachers,
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "eval: marks tests as live LLM evals (require API key, run with: pytest -m eval)",
    )


def pytest_collect_file(parent, file_path):
    """Allow pytest to discover eval_*.py files as test modules.

    By default pytest only discovers test_*.py / *_test.py. This hook extends
    discovery to include eval_*.py files inside the evals directory.
    """
    if file_path.suffix == ".py" and file_path.name.startswith("eval_"):
        return pytest.Module.from_parent(parent, path=file_path)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def seeded_db() -> DatabaseManager:
    """In-memory SQLite DatabaseManager seeded with full university data.

    Contains: 6 teachers, 20 students, 12 courses, 52 enrollments
    (45 completed, 4 active, 3 dropped).

    Session-scoped so the DB is created once and shared across all eval tests,
    avoiding repeated DDL+insert overhead.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def set_fk(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    schema_path = pathlib.Path(__file__).parent.parent.parent / "db" / "schema.sql"
    ddl = schema_path.read_text()
    with engine.connect() as conn:
        conn.connection.executescript(ddl)

    with engine.begin() as conn:
        _insert_teachers(conn)
        _insert_students(conn)
        _insert_courses(conn)
        _insert_enrollments(conn)

    return DatabaseManager(engine=engine)


@pytest.fixture(autouse=True)
def llm_available(request: pytest.FixtureRequest) -> None:
    """Skip eval tests when no LLM API key is configured.

    Automatically applied to all tests in the evals directory. Non-eval
    tests (unit tests) are unaffected.
    """
    if "eval" not in request.keywords:
        return
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        pytest.skip("No LLM API key (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")


@pytest.fixture
def sql_llm(llm_available):
    """Configured LLM for SQL generation (temperature=0)."""
    return get_sql_llm()


@pytest.fixture
def relevance_llm(llm_available):
    """Configured LLM for relevance classification (temperature=0)."""
    return get_relevance_llm()


# ── Eval result collection for terminal summary ────────────────────────────────

_EVAL_RESULTS: list[dict] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call) -> None:
    outcome = yield
    report = outcome.get_result()

    if report.when != "call" or "eval" not in item.keywords:
        return

    tier = "unknown"
    if hasattr(item, "callspec"):
        case = item.callspec.params.get("case")
        if case is not None and hasattr(case, "tier"):
            tier = case.tier
        elif "question" in item.callspec.params:
            tier = "relevance"

    _EVAL_RESULTS.append(
        {
            "tier": tier,
            "id": item.nodeid.split("::")[-1],
            "passed": report.passed,
        }
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not _EVAL_RESULTS:
        return

    tier_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in _EVAL_RESULTS:
        tier_stats[r["tier"]]["total"] += 1
        if r["passed"]:
            tier_stats[r["tier"]]["passed"] += 1

    total_all = sum(v["total"] for v in tier_stats.values())
    passed_all = sum(v["passed"] for v in tier_stats.values())

    terminalreporter.write_sep("=", "Eval Suite Summary")
    header = f"{'Tier':<16} | {'Total':>5} | {'Pass':>4} | {'Rate':>5}"
    sep = "-" * len(header)
    terminalreporter.write_line(header)
    terminalreporter.write_line(sep)

    tier_order = ["simple", "medium", "hard", "very_hard", "adversarial", "relevance", "unknown"]
    for tier in tier_order:
        if tier in tier_stats:
            s = tier_stats[tier]
            rate = f"{s['passed'] / s['total'] * 100:.0f}%" if s["total"] else "N/A"
            terminalreporter.write_line(f"{tier:<16} | {s['total']:>5} | {s['passed']:>4} | {rate:>5}")

    terminalreporter.write_line(sep)
    rate = f"{passed_all / total_all * 100:.0f}%" if total_all else "N/A"
    terminalreporter.write_line(f"{'OVERALL':<16} | {total_all:>5} | {passed_all:>4} | {rate:>5}")
