import os
import json
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from api.main import app
from db.history import (
    DB_PATH,
    init_history_db,
    save_trace,
    get_history,
    get_trace_details,
    get_history_stats,
)


@pytest.fixture(autouse=True)
def clean_history_db():
    """Fixture to ensure the history database is initialized and cleaned up before/after each test."""
    # Delete the DB file if it exists to start clean
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except OSError:
            pass
    
    init_history_db()
    yield
    
    # Cleanup after test
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except OSError:
            pass


def test_history_seeding():
    """Verify that init_history_db seeds the database with 4 example rows when empty."""
    # The clean_history_db fixture already called init_history_db, so it should be seeded with 4 rows
    traces, total = get_history(page=1, limit=10)
    assert total == 4
    assert len(traces) == 4
    
    # Outcomes of the 4 seeded examples
    outcomes = [t["outcome"] for t in traces]
    assert "SUCCESS" in outcomes
    assert "SELF_HEALED" in outcomes
    assert "DECLINED" in outcomes
    assert "BLOCKED" in outcomes


def test_save_and_retrieve_trace():
    """Verify saving a new trace and retrieving its details."""
    question = "Who teaches CS999?"
    answer = "Prof. Chen teaches CS999."
    sql = "SELECT * FROM courses WHERE course_code = 'CS999';"
    outcome = "SUCCESS"
    latency = 1200
    trace_data = {"test_key": "test_value"}
    
    trace_id = save_trace(
        question=question,
        answer=answer,
        sql_query=sql,
        outcome=outcome,
        latency_ms=latency,
        trace_data=trace_data,
    )
    
    assert trace_id > 0
    
    # Verify retrieval in list
    traces, total = get_history(page=1, limit=10, search="CS999")
    assert total == 1
    assert traces[0]["question"] == question
    assert traces[0]["answer"] == answer
    assert traces[0]["sql_query"] == sql
    assert traces[0]["outcome"] == outcome
    
    # Verify full details retrieval
    details = get_trace_details(trace_id)
    assert details == trace_data


def test_history_stats():
    """Verify that statistics calculations behave correctly."""
    # Seeding gives 4 traces: SUCCESS, SELF_HEALED, DECLINED, BLOCKED (no ERRORs)
    stats = get_history_stats()
    assert stats["total_queries"] == 4
    assert stats["error_rate_pct"] == 0.0
    assert stats["health_rate_pct"] == 100.0
    assert stats["self_heal_rate_pct"] == 25.0  # 1 out of 4 is SELF_HEALED
    
    # Add an error trace to verify stats shift
    save_trace(
        question="Query failing",
        answer="Error response",
        sql_query=None,
        outcome="ERROR",
        latency_ms=500,
        trace_data={},
    )
    
    new_stats = get_history_stats()
    assert new_stats["total_queries"] == 5
    assert new_stats["error_rate_pct"] == 20.0  # 1 out of 5
    assert new_stats["health_rate_pct"] == 80.0  # 4 out of 5
    assert new_stats["self_heal_rate_pct"] == 20.0  # 1 out of 5


def test_api_history_endpoints():
    """Verify that the FastAPI history endpoints return correct structures and status codes."""
    client = TestClient(app)
    
    # 1. Get history list
    response = client.get("/api/history?page=1&limit=2")
    assert response.status_code == 200
    res_data = response.json()
    assert "traces" in res_data
    assert "stats" in res_data
    assert len(res_data["traces"]) == 2
    assert res_data["total_count"] == 4
    assert res_data["total_pages"] == 2
    
    # 2. Search filtering
    response_search = client.get("/api/history?search=Prof.%20Chen")
    assert response_search.status_code == 200
    assert response_search.json()["total_count"] == 1
    
    # 3. Outcome filtering
    response_filter = client.get("/api/history?outcome=SELF_HEALED")
    assert response_filter.status_code == 200
    assert response_filter.json()["total_count"] == 1
    
    # 4. Get trace details
    response_detail = client.get("/api/history/1")
    assert response_detail.status_code == 200
    assert "question" in response_detail.json()
    
    # 5. Invalid ID detail
    response_invalid = client.get("/api/history/9999")
    assert response_invalid.status_code == 404
