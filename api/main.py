from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from agent.cache import QueryCache
from agent.conversation_manager import ConversationManager
from agent.nodes import clear_schema_cache
from config import settings
from db.database import DatabaseManager
from tracing.tracer import get_trace_summary, verify_langsmith_config
from api.eval_routes import router as eval_router

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"

app = FastAPI(title=settings.api.title, version=settings.api.version)
app.include_router(eval_router)
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

_manager: ConversationManager | None = None


class AskRequest(BaseModel):
    question: str = Field(...)
    thread_id: str | None = None
    bypass_cache: bool = False

    @field_validator("question")
    @classmethod
    def validate_question_length(cls, v: str) -> str:
        max_len = settings.api.question_max_length
        if len(v) > max_len:
            raise ValueError(f"Question exceeds maximum length of {max_len}")
        return v


def _get_manager() -> ConversationManager:
    global _manager
    if _manager is None:
        _manager = ConversationManager(cache=QueryCache())
    return _manager


def _node_description(node: str, detail: str) -> str:
    descriptions = {
        "check_relevance": "Classified whether the question belongs to the university database scope.",
        "fetch_schema": "Loaded live table metadata through the database abstraction layer.",
        "generate_sql": "Generated or repaired a SQLite SELECT query from the question and schema.",
        "validate_sql": "Checked the generated SQL for empty or destructive operations.",
        "execute_sql": "Ran the validated read-only SQL query against the university database.",
        "regenerate_sql": "Prepared a retry cycle with the previous database error as repair context.",
        "format_answer": "Converted verified rows into a human-readable answer.",
        "polite_decline": "Stopped early because the question is outside the database scope.",
        "error_response": "Returned a controlled error response without another LLM call.",
        "cache_hit": "Served a prior answer from the in-memory query cache without running the graph.",
    }
    return descriptions.get(node, detail or "Recorded graph execution step.")


def _extract_sql_from_step(step: str) -> str:
    if "SQL" not in step or "—" not in step:
        return ""
    candidate = step.rsplit("—", 1)[-1].strip()
    if candidate.upper().startswith("SELECT") or candidate.upper().startswith("WITH"):
        return candidate
    return ""


def _output_for_step(
    node: str,
    detail: str,
    sql: str,
    row_count: int | None,
    final_answer: str,
) -> tuple[str, str, str]:
    if node == "check_relevance":
        return "classification", "Scope classification", f"Classification: {detail}"
    if node == "fetch_schema":
        return "text", "Schema output", f"Loaded schema context: {detail}"
    if node == "generate_sql":
        return "sql", "Generated SQL", sql or detail
    if node == "validate_sql":
        return "text", "Validation result", detail
    if node == "execute_sql":
        if "FAILED" in detail:
            return "error", "Database error", detail
        if row_count is not None:
            return "rows", "Database result", f"Returned {row_count} row{'s' if row_count != 1 else ''}."
        return "text", "Database result", detail
    if node == "regenerate_sql":
        return "retry", "Retry output", detail
    if node == "format_answer":
        return "answer", "Final answer", final_answer or detail
    if node == "polite_decline":
        return "answer", "Decline output", final_answer or detail
    if node == "error_response":
        return "error", "Error output", final_answer or detail
    if node == "cache_hit":
        return "answer", "Cached answer", final_answer or detail
    return "text", "Step output", detail


def _parse_timeline(
    steps: list[str],
    final_sql: str = "",
    final_answer: str = "",
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for index, step in enumerate(steps, 1):
        node, _, detail = step.partition(":")
        node = node.strip() or "unknown"
        detail = detail.strip()
        status = "ok"
        if "FAILED" in step or node == "error_response":
            status = "error"
        elif "BLOCKED" in step:
            status = "blocked"
        elif node == "polite_decline":
            status = "declined"
        elif node == "regenerate_sql":
            status = "retry"

        sql = _extract_sql_from_step(step)
        if node == "generate_sql" and not sql:
            sql = final_sql

        row_count = None
        match = re.search(r"returned\s+(\d+)\s+rows?", step)
        if match:
            row_count = int(match.group(1))
        output_kind, output_title, output_text = _output_for_step(
            node,
            detail,
            sql,
            row_count,
            final_answer,
        )

        timeline.append(
            {
                "index": index,
                "node": node,
                "status": status,
                "detail": detail,
                "description": _node_description(node, detail),
                "sql": sql,
                "row_count": row_count,
                "output_kind": output_kind,
                "output_title": output_title,
                "output_text": output_text,
                "metadata": _metadata_for_step(node, step),
            }
        )
    return timeline


def _metadata_for_step(node: str, step: str) -> dict[str, str]:
    metadata: dict[str, str] = {"Raw step": step}
    if node == "generate_sql":
        metadata["Model role"] = "SQL generation"
        metadata["Safety note"] = "Validated before execution"
    elif node == "validate_sql":
        metadata["Policy"] = "SELECT-only guardrail"
    elif node == "execute_sql":
        metadata["Database"] = "university.db via DatabaseManager"
    elif node == "regenerate_sql":
        metadata["Retry limit"] = "3 attempts"
    elif node == "check_relevance":
        metadata["Classifier"] = "university database scope"
    return metadata


def _outcome_from_steps(steps: list[str]) -> str:
    summary = get_trace_summary(steps)
    if summary["was_declined"]:
        return "DECLINED"
    if any("BLOCKED" in step for step in steps):
        return "BLOCKED"
    if summary["had_error"]:
        return "ERROR"
    if summary["retry_count"] > 0:
        return "SELF_HEALED"
    return "SUCCESS"


def _response_from_result(
    question: str,
    result: dict[str, Any],
    elapsed_ms: int,
    thread_id: str,
) -> dict[str, Any]:
    steps = result.get("steps", [])
    sql_query = result.get("sql_query", "")
    summary = get_trace_summary(steps)
    outcome = _outcome_from_steps(steps)
    return {
        "question": question,
        "answer": result.get("answer", ""),
        "sql_query": sql_query,
        "steps": steps,
        "timeline": _parse_timeline(steps, sql_query, result.get("answer", "")),
        "outcome": outcome,
        "thread_id": result.get("thread_id", thread_id),
        "turn": result.get("turn", 1),
        "cached": result.get("cached", False),
        "metrics": {
            "latency_ms": elapsed_ms,
            "node_count": len(steps),
            "retry_count": summary["retry_count"],
            "graph_iterations": max(1, summary["retry_count"] + 1),
            "validation_health": "BLOCKED" if outcome == "BLOCKED" else "SECURE",
        },
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    db_status = "ok"
    table_count = 0
    try:
        schema = DatabaseManager().get_schema()
        table_count = sum(1 for name in ("teachers", "students", "courses", "enrollments") if name in schema)
    except Exception as exc:  # pragma: no cover - defensive status reporting
        db_status = f"error: {type(exc).__name__}"

    langsmith = verify_langsmith_config()
    if langsmith.get("warnings"):
        langsmith["warnings"] = [
            warning.replace(os.getenv("LANGSMITH_API_KEY", ""), "[redacted]")
            for warning in langsmith["warnings"]
        ]
    return {
        "status": "ok",
        "database": {"status": db_status, "tables": table_count},
        "langsmith": langsmith,
    }


@app.get("/api/schema/summary")
def schema_summary() -> dict[str, Any]:
    rows = []
    db = DatabaseManager()
    for table in ("teachers", "students", "courses", "enrollments"):
        try:
            result = db.execute_query(f"SELECT COUNT(*) AS count FROM {table}")
            count = result[0]["count"] if result else 0
        except Exception:
            count = None
        rows.append({"table": table, "count": count})
    return {"tables": rows}


@app.get("/api/traces/examples")
def examples() -> dict[str, Any]:
    return {"traces": _example_traces()}


@app.post("/api/cache/clear")
def clear_cache() -> dict[str, Any]:
    """Flush in-memory query and schema caches for the running API process."""
    manager = _get_manager()
    query_cache = manager.clear_query_cache()
    clear_schema_cache()
    return {
        "query_cache": query_cache,
        "schema_cache_cleared": True,
    }


@app.post("/api/ask")
async def ask(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")

    payload = AskRequest.model_validate(await request.json())
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is required")

    manager = _get_manager()
    thread_id = payload.thread_id or manager.create_session()
    start = time.perf_counter()
    try:
        result = manager.ask(
            question,
            thread_id=thread_id,
            bypass_cache=payload.bypass_cache,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent invocation failed: {type(exc).__name__}",
        ) from exc
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response_dict = _response_from_result(question, result, elapsed_ms, thread_id)
    try:
        from db.history import save_trace
        save_trace(
            question=question,
            answer=response_dict["answer"],
            sql_query=response_dict["sql_query"],
            outcome=response_dict["outcome"],
            latency_ms=response_dict["metrics"]["latency_ms"],
            trace_data=response_dict,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to save trace to history: {e}")
    return response_dict


@app.get("/api/history")
def get_trace_history(
    page: int = 1,
    limit: int = 10,
    search: str = "",
    outcome: str = "",
) -> dict[str, Any]:
    from db.history import get_history, get_history_stats
    try:
        traces, total = get_history(page=page, limit=limit, search=search, outcome=outcome)
        stats = get_history_stats()
        total_pages = max(1, (total + limit - 1) // limit)
        return {
            "traces": traces,
            "total_count": total,
            "page": page,
            "total_pages": total_pages,
            "stats": stats,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch trace history: {type(exc).__name__}",
        ) from exc


@app.get("/api/history/{trace_id}")
def get_trace_history_details(trace_id: int) -> dict[str, Any]:
    from db.history import get_trace_details
    try:
        details = get_trace_details(trace_id)
        if not details:
            raise HTTPException(status_code=404, detail=f"Trace with ID {trace_id} not found")
        return details
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch trace details: {type(exc).__name__}",
        ) from exc



def _example_result(question: str, answer: str, sql_query: str, steps: list[str]) -> dict[str, Any]:
    return _response_from_result(
        question,
        {"answer": answer, "sql_query": sql_query, "steps": steps, "thread_id": "example", "turn": 1, "cached": False},
        elapsed_ms=0,
        thread_id="example",
    )


def _example_traces() -> list[dict[str, Any]]:
    return [
        _example_result(
            "What courses does Prof. Chen teach?",
            "Prof. Chen teaches CS101, CS201, and CS301.",
            "SELECT c.course_code, c.title FROM courses c JOIN teachers t ON c.teacher_id = t.id WHERE t.last_name = 'Chen'",
            [
                "check_relevance: relevant",
                "fetch_schema: loaded schema (1432 chars)",
                "generate_sql: generated SQL — SELECT c.course_code, c.title FROM courses c JOIN teachers t ON c.teacher_id = t.id WHERE t.last_name = 'Chen'",
                "validate_sql: query passed safety check",
                "execute_sql: returned 3 rows",
                "format_answer: answer formatted (47 chars)",
            ],
        ),
        _example_result(
            "What is the average grade of student Alice Johnson?",
            "Alice Johnson's completed-course average is 88.5.",
            "SELECT AVG(e.grade) AS average_grade FROM students s JOIN enrollments e ON s.id = e.student_id WHERE s.first_name = 'Alice' AND s.last_name = 'Johnson' AND e.status = 'completed'",
            [
                "check_relevance: relevant",
                "fetch_schema: loaded schema (1432 chars)",
                "generate_sql: generated SQL — SELECT AVG(grade) FROM students WHERE name = 'Alice Johnson'",
                "validate_sql: query passed safety check",
                "execute_sql: FAILED — no such column: name",
                "regenerate_sql: preparing retry (attempt 1 of 3)",
                "generate_sql: regenerated SQL — SELECT AVG(e.grade) AS average_grade FROM students s JOIN enrollments e ON s.id = e.student_id WHERE s.first_name = 'Alice' AND s.last_name = 'Johnson' AND e.status = 'completed'",
                "validate_sql: query passed safety check",
                "execute_sql: returned 1 rows",
                "format_answer: answer formatted (46 chars)",
            ],
        ),
        _example_result(
            "Who won the world cup in 2022?",
            "I can only answer questions about the university database.",
            "",
            [
                "check_relevance: not_relevant",
                "polite_decline: declined off-topic question",
            ],
        ),
        _example_result(
            "DROP TABLE students",
            "I cannot execute queries that modify the database. Only SELECT queries are allowed.",
            "DROP TABLE students",
            [
                "check_relevance: relevant",
                "fetch_schema: loaded schema (1432 chars)",
                "generate_sql: generated SQL — DROP TABLE students",
                "validate_sql: BLOCKED — destructive SQL detected in: DROP TABLE students",
                "error_response: I cannot execute queries that modify the database.",
            ],
        ),
    ]
