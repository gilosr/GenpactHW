"""
db/history.py
──────────────
Persistent trace storage for the university QA agent.
Manages an isolated SQLite database (`history.db`) for run histories and stats.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "history.db"


def get_connection() -> sqlite3.Connection:
    """Return a sqlite3 Connection to the history database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_history_db() -> None:
    """Initialize the trace history database schema and seed examples if empty."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trace_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sql_query TEXT,
                outcome TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                trace_data TEXT NOT NULL
            );
        """)
        conn.commit()

        # Seed examples if the table is empty
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS count FROM trace_history")
        row = cursor.fetchone()
        if row and row["count"] == 0:
            seed_example_traces(conn)


def save_trace(
    question: str,
    answer: str,
    sql_query: str,
    outcome: str,
    latency_ms: int,
    trace_data: dict[str, Any]
) -> int:
    """Save a trace record to the history database.

    Returns:
        The inserted row ID.
    """
    init_history_db()  # Defensively ensure DB and schema exist
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trace_history (question, answer, sql_query, outcome, latency_ms, trace_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                question,
                answer,
                sql_query,
                outcome,
                latency_ms,
                json.dumps(trace_data),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_history(
    page: int = 1,
    limit: int = 10,
    search: str = "",
    outcome: str = ""
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve filtered, paginated traces from the history database.

    Returns:
        A tuple of (list of traces, total count matching filters).
    """
    init_history_db()
    offset = (page - 1) * limit
    where_clauses = []
    params = []

    if search:
        where_clauses.append("(question LIKE ? OR answer LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    if outcome:
        where_clauses.append("outcome = ?")
        params.append(outcome)

    where_str = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    with get_connection() as conn:
        # Get total count
        count_query = f"SELECT COUNT(*) AS total FROM trace_history {where_str}"
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        total = cursor.fetchone()["total"]

        # Get records ordered by timestamp descending
        records_query = f"""
            SELECT id, question, answer, sql_query, outcome, latency_ms, timestamp
            FROM trace_history
            {where_str}
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
        """
        cursor.execute(records_query, params + [limit, offset])
        rows = cursor.fetchall()

        traces = []
        for r in rows:
            traces.append({
                "id": r["id"],
                "question": r["question"],
                "answer": r["answer"],
                "sql_query": r["sql_query"],
                "outcome": r["outcome"],
                "latency_ms": r["latency_ms"],
                "timestamp": r["timestamp"],
            })

        return traces, total


def get_trace_details(trace_id: int) -> Optional[dict[str, Any]]:
    """Retrieve the full JSON trace data for a given ID."""
    init_history_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT trace_data FROM trace_history WHERE id = ?", (trace_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row["trace_data"])
    return None


def get_history_stats() -> dict[str, Any]:
    """Calculate and return overall trace metrics/statistics."""
    init_history_db()
    with get_connection() as conn:
        cursor = conn.cursor()

        # Total count
        cursor.execute("SELECT COUNT(*) AS total FROM trace_history")
        total = cursor.fetchone()["total"]

        if total == 0:
            return {
                "total_queries": 0,
                "avg_latency_s": 0.0,
                "self_heal_rate_pct": 0.0,
                "error_rate_pct": 0.0,
                "health_rate_pct": 100.0,
            }

        # Average latency (ms)
        cursor.execute("SELECT AVG(latency_ms) AS avg_lat FROM trace_history")
        avg_lat_ms = cursor.fetchone()["avg_lat"] or 0.0
        avg_latency_s = round(avg_lat_ms / 1000.0, 1)

        # Self heal count
        cursor.execute("SELECT COUNT(*) AS count FROM trace_history WHERE outcome = 'SELF_HEALED'")
        self_heal_count = cursor.fetchone()["count"]

        # Error count
        cursor.execute("SELECT COUNT(*) AS count FROM trace_history WHERE outcome = 'ERROR'")
        error_count = cursor.fetchone()["count"]

        self_heal_rate = round((self_heal_count / total) * 100, 1)
        error_rate = round((error_count / total) * 100, 1)
        health_rate = round(((total - error_count) / total) * 100, 1)

        return {
            "total_queries": total,
            "avg_latency_s": avg_latency_s,
            "self_heal_rate_pct": self_heal_rate,
            "error_rate_pct": error_rate,
            "health_rate_pct": health_rate,
        }


def seed_example_traces(conn: sqlite3.Connection) -> None:
    """Seed the database with pre-built example traces for an immersive dynamic setup."""
    # We lazy-import here to avoid potential circular imports
    from api.main import _example_traces
    traces = _example_traces()
    cursor = conn.cursor()

    # Predefined latency overrides to make stats realistic
    latencies = [1800, 4200, 300, 900]
    timestamps = [
        "2026-05-27 15:41:00",  # SUCCESS
        "2026-05-27 15:28:00",  # SELF_HEALED
        "2026-05-27 14:46:00",  # DECLINED
        "2026-05-27 13:46:00",  # BLOCKED
    ]

    for idx, trace in enumerate(traces):
        latency = latencies[idx] if idx < len(latencies) else 1200
        timestamp = timestamps[idx] if idx < len(timestamps) else "2026-05-27 12:00:00"

        # Tweak example trace objects to have dynamic metric latencies
        trace["metrics"]["latency_ms"] = latency

        cursor.execute(
            """
            INSERT INTO trace_history (question, answer, sql_query, outcome, latency_ms, timestamp, trace_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace["question"],
                trace["answer"],
                trace["sql_query"],
                trace["outcome"],
                latency,
                timestamp,
                json.dumps(trace),
            ),
        )
    conn.commit()
