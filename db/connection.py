"""
db/connection.py
────────────────
SQLAlchemy Core engine factory for the university SQLite database.

Responsibilities:
  - Provide a singleton Engine pointed at university.db
  - Enable SQLite foreign-key enforcement on every new connection
  - Execute schema.sql DDL to create tables and indexes on first run

Usage:
    from db.connection import get_engine, init_db

    init_db()            # create tables (idempotent — uses IF NOT EXISTS)
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM students"))
"""

from __future__ import annotations

import logging
import pathlib
from typing import Optional

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from config import settings

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
_DB_PATH = _PROJECT_ROOT / "university.db"
_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"

# ── Singleton engine ───────────────────────────────────────────────────────────
_engine: Optional[Engine] = None


def get_engine(db_path: Optional[str] = None) -> Engine:
    """Return the singleton SQLAlchemy engine, creating it if necessary.

    Args:
        db_path: Optional override for the database file path.
                 Pass ":memory:" for in-memory DBs in tests.

    Returns:
        A configured SQLAlchemy Engine.
    """
    global _engine
    if _engine is not None:
        if db_path is not None:
            requested = f"sqlite:///{db_path}"
            current = str(_engine.url)
            if requested != current:
                logger.warning(
                    "get_engine() called with db_path=%r but singleton "
                    "points to %r. Call reset_db() first to switch.",
                    db_path,
                    current,
                )
        return _engine
    url = f"sqlite:///{db_path}" if db_path is not None else settings.database.url
    _engine = create_engine(url, echo=settings.database.echo)
    _register_fk_pragma(_engine)
    return _engine


def _register_fk_pragma(engine: Engine) -> None:
    """Enable foreign-key enforcement on every new SQLite connection.

    SQLite ignores FK constraints unless PRAGMA foreign_keys = ON is issued
    per connection. SQLAlchemy's connection pool may create multiple
    connections, so we attach this to the connect event rather than running
    it once.
    """
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables and indexes by executing schema.sql.

    Safe to call multiple times — DDL uses CREATE TABLE IF NOT EXISTS.
    Uses sqlite3's executescript() which handles multi-statement DDL and
    inline comments correctly without a custom SQL splitter.

    Args:
        db_path: Optional override for the database file path (e.g. for tests).
    """
    engine = get_engine(db_path)
    ddl = _SCHEMA_PATH.read_text()

    with engine.connect() as conn:
        # Access the raw DBAPI connection to use executescript(), which
        # correctly handles semicolons in comments and multi-statement scripts.
        conn.connection.executescript(ddl)


def reset_db(db_path: Optional[str] = None) -> None:
    """Drop and recreate the database file, then reinitialise the schema.

    Used by the seed script and tests to ensure a clean state.

    Args:
        db_path: Optional override for the database file path.
    """
    global _engine

    # Dispose of the current engine so SQLite releases the file lock
    if _engine is not None:
        _engine.dispose()
        _engine = None

    target = pathlib.Path(db_path or _DB_PATH)
    if target.exists() and target != pathlib.Path(":memory:"):
        target.unlink()

    init_db(db_path)
