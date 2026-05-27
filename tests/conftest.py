"""
tests/conftest.py
─────────────────
Shared pytest fixtures for database and SQL-generation tests.

Provides:
  - in_memory_engine  : Fresh SQLite engine with schema + FK enforcement
  - seeded_engine     : in_memory_engine with full seed data
  - db_manager        : DatabaseManager wrapping seeded_engine
  - db_for_nodes      : Injects seeded DB into the agent.nodes singleton

Also registers the 'integration' marker and auto-skips integration tests
in default runs. Use `pytest -m integration` to opt in.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine, event

from agent.nodes import set_db
from db.database import DatabaseManager


def pytest_configure(config):
    """Register custom markers so pytest doesn't warn about unknown marks."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests that call real LLMs / external APIs (select with -m integration)",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.integration tests unless -m integration is passed."""
    mark_expression = str(getattr(config.option, "markexpr", "") or "")
    if "integration" not in mark_expression:
        skip = pytest.mark.skip(
            reason="Integration tests require real LLM API keys; run with -m integration"
        )
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)

_SCHEMA = (pathlib.Path(__file__).parent.parent / "db" / "schema.sql").read_text()


@pytest.fixture()
def in_memory_engine():
    """Fresh in-memory SQLite engine with FK enforcement and schema applied."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.cursor().execute("PRAGMA foreign_keys = ON")

    with engine.connect() as conn:
        conn.connection.executescript(_SCHEMA)

    yield engine
    engine.dispose()


@pytest.fixture()
def seeded_engine(in_memory_engine):
    """In-memory engine with full seed data inserted."""
    from db.seed import (
        _insert_courses,
        _insert_enrollments,
        _insert_students,
        _insert_teachers,
    )

    with in_memory_engine.begin() as conn:
        _insert_teachers(conn)
        _insert_students(conn)
        _insert_courses(conn)
        _insert_enrollments(conn)
    return in_memory_engine


@pytest.fixture()
def db_manager(seeded_engine):
    """DatabaseManager wrapping a seeded in-memory DB."""
    return DatabaseManager(engine=seeded_engine)


@pytest.fixture()
def db_for_nodes(db_manager):
    """Inject seeded DB into agent.nodes module singleton, reset after test."""
    set_db(db_manager)
    yield db_manager
    set_db(None)
