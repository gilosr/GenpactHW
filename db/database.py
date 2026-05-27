"""
db/database.py
──────────────
Database-agnostic access layer for the university QA agent.

All agent nodes interact with the database exclusively through this module.
The underlying engine and dialect are abstracted via LangChain's SQLDatabase
utility (for schema introspection) and SQLAlchemy Core (for query execution).

Swapping from SQLite to PostgreSQL requires changing only the DATABASE_URL
environment variable.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from langchain_community.utilities import SQLDatabase
from sqlalchemy import Engine, text

from db.connection import get_engine, init_db


class DatabaseError(Exception):
    """Raised when a database operation fails.

    Wraps underlying DB exceptions so agent nodes can catch a single
    error type without importing database-specific libraries.
    """
    pass


class DatabaseManager:
    """Single entry point for all database operations in the agent.

    Provides two capabilities:
      1. Schema introspection -- for LLM prompt construction
      2. Safe query execution -- SELECT-only, with error wrapping
    """

    _BLOCKED_PATTERN = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _has_blocked_keywords(sql: str) -> bool:
        """Check for blocked SQL keywords outside of string literals."""
        stripped = re.sub(r"'[^']*'", "''", sql)
        return bool(DatabaseManager._BLOCKED_PATTERN.search(stripped))

    def __init__(
        self,
        connection_string: Optional[str] = None,
        engine: Optional[Engine] = None,
    ) -> None:
        """Initialize the database manager.

        Args:
            connection_string: SQLAlchemy-compatible connection string.
                Defaults to DATABASE_URL env var, then falls back to
                the project's SQLite file.
            engine: Optional pre-existing SQLAlchemy engine (used in tests
                to share the in-memory DB created by connection.py).
        """
        if engine is not None:
            self._engine = engine
        else:
            conn_str = connection_string or os.getenv("DATABASE_URL")
            if conn_str:
                from sqlalchemy import create_engine
                self._engine = create_engine(conn_str)
            else:
                init_db()
                self._engine = get_engine()

        self._db = SQLDatabase(engine=self._engine)

    def get_schema(self) -> str:
        """Return a human-readable description of all tables.

        Output includes CREATE TABLE statements, column types,
        constraints, and 3 sample rows per table. Generated
        dynamically via SQLAlchemy introspection.
        """
        return self._db.get_table_info()

    def get_table_names(self) -> list[str]:
        """Return all user-facing table names in the database."""
        return self._db.get_usable_table_names()

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SELECT query and return structured results.

        Args:
            sql: SQL SELECT statement to execute.

        Returns:
            List of row dicts, e.g. [{"name": "Alice", "grade": 92.0}, ...]

        Raises:
            ValueError: If the query contains destructive keywords
                (INSERT, UPDATE, DELETE, DROP, etc.).
            DatabaseError: If execution fails at the database level.
        """
        stripped = sql.strip().rstrip(";")

        if self._has_blocked_keywords(stripped):
            raise ValueError(
                "Query blocked: destructive operation detected. "
                "Only SELECT queries are allowed."
            )

        try:
            with self._engine.connect() as conn:
                result = conn.execute(text(stripped))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows
        except ValueError:
            raise
        except Exception as e:
            raise DatabaseError(f"Query execution failed: {e}") from e
