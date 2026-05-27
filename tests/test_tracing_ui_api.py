from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_reports_service_status():
    from api.main import app

    client = TestClient(app)
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "langsmith" in payload
    assert "database" in payload


def test_examples_endpoint_returns_interview_scenarios():
    from api.main import app

    client = TestClient(app)
    response = client.get("/api/traces/examples")

    assert response.status_code == 200
    traces = response.json()["traces"]
    outcomes = {trace["outcome"] for trace in traces}
    assert {"SUCCESS", "SELF_HEALED", "DECLINED", "BLOCKED"}.issubset(outcomes)
    assert all(trace["question"] for trace in traces)
    assert all(trace["timeline"] for trace in traces)


def test_ask_rejects_non_json_content_type():
    from api.main import app

    client = TestClient(app)
    response = client.post(
        "/api/ask",
        content="question=How many students?",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 415


def test_ask_rejects_empty_question():
    from api.main import app

    client = TestClient(app)
    response = client.post("/api/ask", json={"question": "   "})

    assert response.status_code == 422
    assert "Question is required" in response.json()["detail"]


def test_cache_clear_endpoint_flushes_query_cache():
    import api.main as main
    from agent.cache import QueryCache
    from agent.conversation_manager import ConversationManager

    manager = ConversationManager(cache=QueryCache())
    manager._cache.put(
        "How many students?",
        "SELECT COUNT(*) FROM students",
        [{"count": 20}],
        "There are 20 students.",
    )
    assert manager._cache.stats()["size"] == 1

    main._manager = manager
    client = TestClient(main.app)
    response = client.post("/api/cache/clear")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_cache_cleared"] is True
    assert payload["query_cache"]["cleared"] is True
    assert payload["query_cache"]["entries_removed"] == 1
    assert manager._cache.stats()["size"] == 0


def test_ask_endpoint_surfaces_cached_flag_on_cache_hit(monkeypatch):
    import api.main as main

    class FakeManager:
        def create_session(self) -> str:
            return "session-cached"

        def ask(self, question: str, thread_id: str, bypass_cache: bool = False) -> dict:
            return {
                "answer": "There are 20 students.",
                "steps": ["cache_hit: returned cached answer"],
                "sql_query": "SELECT COUNT(*) FROM students",
                "thread_id": thread_id,
                "turn": 1,
                "cached": True,
            }

    monkeypatch.setattr(main, "_manager", FakeManager())
    client = TestClient(main.app)
    response = client.post(
        "/api/ask",
        json={"question": "How many students are there?"},
    )

    assert response.status_code == 200
    assert response.json()["cached"] is True


def test_ask_endpoint_returns_live_trace(monkeypatch):
    import api.main as main

    class FakeManager:
        def create_session(self) -> str:
            return "session-test"

        def ask(self, question: str, thread_id: str, bypass_cache: bool = False) -> dict:
            assert question == "How many students are there?"
            assert thread_id == "session-test"
            assert bypass_cache is False
            return {
                "answer": "There are 20 students.",
                "steps": [
                    "check_relevance: relevant",
                    "fetch_schema: loaded schema (1432 chars)",
                    "generate_sql: generated SQL — SELECT COUNT(*) FROM students",
                    "validate_sql: query passed safety check",
                    "execute_sql: returned 1 rows",
                    "format_answer: answer formatted (22 chars)",
                ],
                "sql_query": "SELECT COUNT(*) FROM students",
                "thread_id": thread_id,
                "turn": 1,
                "cached": False,
            }

    monkeypatch.setattr(main, "_manager", FakeManager())
    client = TestClient(main.app)
    response = client.post(
        "/api/ask",
        json={"question": "How many students are there?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "There are 20 students."
    assert payload["outcome"] == "SUCCESS"
    assert payload["sql_query"] == "SELECT COUNT(*) FROM students"
    assert payload["thread_id"] == "session-test"
    assert [node["node"] for node in payload["timeline"]] == [
        "check_relevance",
        "fetch_schema",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "format_answer",
    ]


def test_static_frontend_is_served():
    from api.main import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Obsidian Trace" in response.text
    assert "Type a university question" in response.text


def test_self_healed_example_keeps_failed_and_repaired_sql_distinct():
    from api.main import app

    client = TestClient(app)
    response = client.get("/api/traces/examples")

    assert response.status_code == 200
    self_healed = next(
        trace for trace in response.json()["traces"] if trace["outcome"] == "SELF_HEALED"
    )
    sql_events = [
        event["sql"]
        for event in self_healed["timeline"]
        if event["node"] == "generate_sql"
    ]
    assert sql_events[0] == "SELECT AVG(grade) FROM students WHERE name = 'Alice Johnson'"
    assert "JOIN enrollments" in sql_events[1]


def test_each_timeline_step_has_clear_output_text(monkeypatch):
    import api.main as main

    class FakeManager:
        def create_session(self) -> str:
            return "session-output"

        def ask(self, question: str, thread_id: str, bypass_cache: bool = False) -> dict:
            return {
                "answer": "There are 20 students.",
                "steps": [
                    "check_relevance: relevant",
                    "fetch_schema: loaded schema (1432 chars)",
                    "generate_sql: generated SQL — SELECT COUNT(*) FROM students",
                    "validate_sql: query passed safety check",
                    "execute_sql: returned 1 rows",
                    "format_answer: answer formatted (22 chars)",
                ],
                "sql_query": "SELECT COUNT(*) FROM students",
                "thread_id": thread_id,
                "turn": 1,
                "cached": False,
            }

    monkeypatch.setattr(main, "_manager", FakeManager())
    client = TestClient(main.app)
    response = client.post("/api/ask", json={"question": "How many students are there?"})

    assert response.status_code == 200
    timeline = response.json()["timeline"]
    assert all(event["output_text"] for event in timeline)
    assert timeline[0]["output_text"] == "Classification: relevant"
    assert timeline[2]["output_kind"] == "sql"
    assert timeline[2]["output_text"] == "SELECT COUNT(*) FROM students"
    assert timeline[-1]["output_text"] == "There are 20 students."


def test_frontend_renders_output_section_instead_of_metadata_dict():
    from pathlib import Path

    app_js = Path("web/app.js").read_text()

    assert "Output" in app_js
    assert "JSON.stringify(node.metadata" not in app_js


def test_frontend_sql_output_uses_timeline_step_sql_not_final_trace_sql():
    from pathlib import Path

    app_js = Path("web/app.js").read_text()

    assert "renderSQL(item.sql || item.output_text || trace.sql_query)" in app_js


def test_frontend_renders_metadata_as_labeled_rows():
    from pathlib import Path

    app_js = Path("web/app.js").read_text()

    assert "Metadata" in app_js
    assert "Object.entries(metadata || {})" in app_js
    assert "kv-list" in app_js
    assert "kv-row" in app_js
    assert "kv-key" in app_js
    assert "kv-val" in app_js
    assert "JSON.stringify(node.metadata" not in app_js
