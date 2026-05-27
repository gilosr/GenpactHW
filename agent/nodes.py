"""
agent/nodes.py
──────────────
LangGraph node functions for the university QA agent.

Each node:
  - Accepts (state: AgentState) -> dict
  - Returns a *partial* state update (only keys it touches)
  - Always appends to `steps` for execution tracing
  - Never raises exceptions — errors are captured into state fields

Node graph:
  check_relevance → [polite_decline | fetch_schema]
  fetch_schema → generate_sql → validate_sql → [execute_sql | error_response]
  execute_sql → [format_answer | regenerate_sql | error_response]
  regenerate_sql → generate_sql  (retry cycle, max 3 attempts)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from agent.llm import (
    get_answer_llm,
    get_relevance_llm,
    get_retry_llm,
    get_sql_llm,
    invoke_prompt,
)
from config import settings
from agent.state import AgentState
from db.database import DatabaseError, DatabaseManager
from prompts.manager import get_prompt_manager, set_prompt_manager
from prompts.schemas import AnswerResult, RelevanceResult, SQLResult, SQLRetryResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Defense-in-depth: mirrors DatabaseManager._BLOCKED_PATTERN.
# validate_sql catches destructive SQL before it reaches the DB layer.
_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE)\b",
    re.IGNORECASE,
)


def _has_destructive_keywords(sql: str) -> bool:
    """Check for destructive SQL keywords outside of string literals."""
    stripped = re.sub(r"'[^']*'", "''", sql)
    return bool(_DESTRUCTIVE_PATTERN.search(stripped))


_SCHEMA_TTL_SECONDS = 300
_cached_schema: str | None = None
_cached_schema_at = 0.0

# ---------------------------------------------------------------------------
# DB singleton + test hook
# ---------------------------------------------------------------------------

_db: DatabaseManager | None = None


def _get_db() -> DatabaseManager:
    """Lazy-initialize the shared DatabaseManager instance.

    Avoids import-time DB connection so the module can be imported
    in unit tests without a live database.
    """
    global _db
    if _db is None:
        _db = DatabaseManager()
    return _db


def set_db(db: DatabaseManager | None) -> None:
    """Replace the module-level DB singleton (for testing).

    Pass a DatabaseManager wrapping an in-memory engine to avoid
    touching the on-disk university.db during unit tests.

    Args:
        db: A DatabaseManager instance, or None to reset to lazy-init.
    """
    global _db, _cached_schema, _cached_schema_at
    _db = db
    clear_schema_cache()


def clear_schema_cache() -> None:
    """Invalidate the in-process schema introspection cache."""
    global _cached_schema, _cached_schema_at
    _cached_schema = None
    _cached_schema_at = 0.0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clean_sql(raw: str) -> str:
    """Strip markdown code fences from LLM output.

    LLMs sometimes wrap SQL in ```sql ... ``` blocks even when prompted
    to return raw SQL. This helper normalises the output.

    Args:
        raw: Raw string from the LLM response.

    Returns:
        SQL string without surrounding fences or whitespace.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```[\s\S]*$", "", text)
    return text.strip()


def _extract_content(response) -> str:
    """Normalise AIMessage content across providers.

    OpenAI returns a plain string; Anthropic returns a list of typed
    content blocks. This helper flattens both into a single string.

    Args:
        response: An AIMessage (or any object with a .content attribute).

    Returns:
        Concatenated text content.
    """
    content = response.content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _classify_llm_error(exc: Exception) -> str:
    """Convert LLM API exceptions into user-friendly error messages.

    Uses string matching (not exception types) to stay provider-agnostic:
    works for both OpenAI and Anthropic without importing their error classes,
    preserving the lazy-import pattern in agent/llm.py.

    Covers the 4 most common API failure modes:
    - Rate limit (429): burst of queries, temporary
    - Timeout: complex prompt or slow network
    - Context length: prompt + schema + history too long
    - Server error (5xx): provider outage
    """
    error_str = str(exc).lower()
    if ("rate" in error_str and "limit" in error_str) or "429" in error_str:
        return "The AI service is temporarily rate-limited. Please wait a moment and try again."
    if "timeout" in error_str or "timed out" in error_str:
        return "The AI service request timed out. Please try again."
    if "context length" in error_str or ("token" in error_str and "limit" in error_str):
        return "The question is too complex for a single request. Try simplifying or shortening it."
    if "500" in error_str or "502" in error_str or "503" in error_str or "server error" in error_str:
        return "The AI service is temporarily unavailable. Please try again later."
    return str(exc)


def _format_results_for_prompt(
    results: list[dict[str, Any]],
    row_count: int,
    max_rows: int = 10,
) -> str:
    """Format query results compactly for the answer LLM."""
    if row_count == 0 or not results:
        return "No rows returned."

    if row_count == 1 and len(results[0]) == 1:
        key, value = next(iter(results[0].items()))
        return f"{key}: {value}"

    rows = results[:max_rows]
    headers = list(rows[0].keys())
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")) for column in headers) + " |"
        for row in rows
    ]
    table = "\n".join([header, separator, *body])
    if row_count > max_rows:
        table += f"\n\nShowing first {max_rows} of {row_count} rows."
    return table


def _format_attempt_history(attempts: list[dict[str, str]]) -> str:
    """Render accumulated retry failures for the regeneration prompt."""
    if not attempts:
        return "No previous attempts recorded."
    chunks = []
    for index, attempt in enumerate(attempts, start=1):
        chunks.append(
            f"Attempt {index}:\n"
            f"SQL: {attempt.get('sql', '')}\n"
            f"Error: {attempt.get('error', '')}"
        )
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


def check_relevance(state: AgentState) -> dict:
    """Classify whether the question is relevant to the university database.

    Uses a zero-temperature LLM to get a deterministic binary label.
    On failure, defaults to "relevant" (fail-open) so we don't silently
    drop valid questions due to transient LLM errors.

    State keys read:  question
    State keys written: relevance, steps
    """
    question = state["question"]
    step_tag = "check_relevance"
    try:
        llm = get_relevance_llm()
        bundle = get_prompt_manager().build_relevance_check_messages(question)
        messages = bundle.messages
        trace_metadata = bundle.trace_metadata or None
        try:
            result = invoke_prompt(
                llm.with_structured_output(RelevanceResult),
                messages,
                trace_metadata=trace_metadata,
            )
            if not isinstance(result, RelevanceResult):
                raise TypeError("structured output returned unexpected type")
            relevance = result.classification
        except Exception:
            response = invoke_prompt(llm, messages, trace_metadata=trace_metadata)
            raw = _extract_content(response).lower().strip()
            # Check "not_relevant" first — it is a superset of "relevant" as a substring.
            relevance = "not_relevant" if "not_relevant" in raw else "relevant"
        logger.info("%s: classified as '%s'", step_tag, relevance)
    except Exception as exc:
        logger.warning("%s: LLM error (%s) — defaulting to 'relevant'", step_tag, exc)
        _ = _classify_llm_error(exc)  # classify for logging; fail-open, don't expose to user
        relevance = "relevant"

    return {
        "relevance": relevance,
        "steps": [f"{step_tag}: {relevance}"],
    }


def polite_decline(state: AgentState) -> dict:
    """Generate a polite refusal for off-topic questions.

    Invokes the answer LLM with a specialised prompt explaining the
    system's scope. Falls back to a hard-coded message on LLM failure.

    State keys read:  question
    State keys written: answer, steps
    """
    question = state["question"]
    step_tag = "polite_decline"
    try:
        llm = get_answer_llm()
        bundle = get_prompt_manager().build_polite_decline_messages(question)
        response = invoke_prompt(
            llm,
            bundle.messages,
            trace_metadata=bundle.trace_metadata or None,
        )
        answer = _extract_content(response).strip()
        logger.info("%s: generated decline response", step_tag)
    except Exception as exc:
        logger.warning("%s: LLM error (%s) — using fallback", step_tag, exc)
        _ = _classify_llm_error(exc)
        answer = get_prompt_manager().fallback_decline

    return {
        "answer": answer,
        "steps": [f"{step_tag}: declined off-topic question"],
    }


def fetch_schema(state: AgentState) -> dict:
    """Load the database schema for use in SQL generation prompts.

    Also initialises the retry counter fields so downstream nodes can
    always read them without KeyError.

    State keys read:  (none)
    State keys written: schema_info, attempts, max_retries, sql_error, steps
    """
    step_tag = "fetch_schema"
    try:
        global _cached_schema, _cached_schema_at
        now = time.monotonic()
        if _cached_schema is not None and now - _cached_schema_at < _SCHEMA_TTL_SECONDS:
            schema = _cached_schema
        else:
            schema = _get_db().get_schema()
            _cached_schema = schema
            _cached_schema_at = now
        logger.info("%s: loaded schema (%d chars)", step_tag, len(schema))
    except Exception as exc:
        logger.warning("%s: failed to load schema (%s) — using empty string", step_tag, exc)
        schema = ""

    return {
        "schema_info": schema,
        "attempts": 0,
        "max_retries": settings.agent.max_retries,
        "previous_attempts": [],
        "sql_error": "",
        "steps": [f"{step_tag}: loaded schema ({len(schema)} chars)"],
    }


def generate_sql(state: AgentState) -> dict:
    """Generate (or regenerate) a SQL SELECT query for the user's question.

    First attempt: uses SQL_GENERATION_PROMPT + get_sql_llm().
    Retry attempt (when both sql_error and sql_query are set): uses
    SQL_REGENERATION_PROMPT + get_retry_llm() so the model can learn
    from its previous mistake and explore alternative query structures.

    IMPORTANT: always clears sql_error on success so route_result
    correctly identifies a clean state.

    State keys read:  question, schema_info, sql_error, sql_query
    State keys written: sql_query, sql_error, steps
    """
    step_tag = "generate_sql"
    question = state["question"]
    schema = state.get("schema_info", "")
    sql_error = state.get("sql_error", "")
    failed_sql = state.get("sql_query", "")
    previous_attempts = state.get("previous_attempts", [])

    is_retry = bool(sql_error and failed_sql)

    # Gap 1 fix: empty schema cascades into hallucinated SQL and burns retry cycles.
    # Fail fast with a clear message instead.
    if not schema and not is_retry:
        logger.warning("%s: no schema available — failing fast", step_tag)
        return {
            "sql_query": "",
            "sql_error": "Database schema unavailable — cannot generate SQL",
            "steps": [f"{step_tag}: FAILED — no schema available"],
        }

    try:
        if is_retry:
            logger.info("%s: retry path — regenerating SQL", step_tag)
            llm = get_retry_llm()
            bundle = get_prompt_manager().build_sql_regeneration_messages(
                schema=schema,
                previous_attempts=_format_attempt_history(
                    previous_attempts
                    or [{"sql": failed_sql, "error": sql_error}]
                ),
                question=question,
            )
            messages = bundle.messages
            trace_metadata = bundle.trace_metadata or None
            try:
                structured = invoke_prompt(
                    llm.with_structured_output(SQLRetryResult),
                    messages,
                    trace_metadata=trace_metadata,
                )
                if not isinstance(structured, SQLRetryResult):
                    raise TypeError("structured output returned unexpected type")
                if not structured.can_answer:
                    return {
                        "sql_query": "",
                        "sql_error": "cannot_answer",
                        "error_message": get_prompt_manager().cannot_answer_message,
                        "steps": [f"{step_tag}: question cannot be answered from schema"],
                    }
                sql = structured.sql
                logger.info("%s: diagnosis: %s", step_tag, structured.diagnosis[:120])
            except Exception:
                raw = _extract_content(
                    invoke_prompt(llm, messages, trace_metadata=trace_metadata)
                )
                sql = _clean_sql(raw)
        else:
            logger.info("%s: first attempt — generating SQL", step_tag)
            llm = get_sql_llm()
            bundle = get_prompt_manager().build_sql_generation_messages(
                schema=schema, question=question
            )
            messages = bundle.messages
            trace_metadata = bundle.trace_metadata or None
            try:
                structured = invoke_prompt(
                    llm.with_structured_output(SQLResult),
                    messages,
                    trace_metadata=trace_metadata,
                )
                if not isinstance(structured, SQLResult):
                    raise TypeError("structured output returned unexpected type")
                if not structured.can_answer:
                    return {
                        "sql_query": "",
                        "sql_error": "cannot_answer",
                        "error_message": get_prompt_manager().cannot_answer_message,
                        "steps": [f"{step_tag}: question cannot be answered from schema"],
                    }
                sql = structured.sql
                logger.info("%s: reasoning: %s", step_tag, structured.reasoning[:120])
            except Exception:
                raw = _extract_content(
                    invoke_prompt(llm, messages, trace_metadata=trace_metadata)
                )
                sql = _clean_sql(raw)

        logger.info("%s: generated SQL: %s", step_tag, sql[:120])

        return {
            "sql_query": sql,
            "sql_error": "",  # CRITICAL: clear stale error so route_result works correctly
            "steps": [
                f"{step_tag}: {'regenerated' if is_retry else 'generated'} SQL — {sql[:80]}"
            ],
        }

    except Exception as exc:
        logger.warning("%s: LLM error (%s)", step_tag, exc)
        error_msg = _classify_llm_error(exc)
        return {
            "sql_query": "",
            "sql_error": error_msg,
            "steps": [f"{step_tag}: LLM error — {error_msg[:80]}"],
        }


def validate_sql(state: AgentState) -> dict:
    """Check the generated SQL for safety and completeness.

    Two failure modes:
      1. Empty query — LLM produced nothing useful.
      2. Destructive pattern — query contains INSERT/UPDATE/DROP/etc.

    On failure sets both sql_error (machine-readable) and error_message
    (user-facing) so error_response can use the friendlier copy.

    State keys read:  sql_query
    State keys written: sql_error, error_message, steps
    """
    step_tag = "validate_sql"
    sql = state.get("sql_query", "")

    if not sql:
        existing_error = state.get("sql_error", "")
        existing_message = state.get("error_message", "")
        logger.warning("%s: empty SQL query (existing error: %s)", step_tag, existing_error or "none")
        return {
            "sql_error": existing_error or "No SQL query was generated",
            "error_message": existing_message or existing_error or (
                "I was unable to generate a SQL query for your question. "
                "Please try rephrasing."
            ),
            "steps": [f"{step_tag}: FAILED — empty SQL query"],
        }

    if _has_destructive_keywords(sql):
        logger.warning("%s: destructive SQL detected: %s", step_tag, sql[:80])
        return {
            "sql_error": "Query blocked: destructive operation detected",
            "error_message": (
                "I cannot execute queries that modify the database. "
                "Only SELECT queries are allowed."
            ),
            "steps": [f"{step_tag}: BLOCKED — destructive SQL detected in: {sql[:80]}"],
        }

    logger.info("%s: query passed safety check", step_tag)
    return {
        "sql_error": "",
        "steps": [f"{step_tag}: query passed safety check"],
    }


def execute_sql(state: AgentState) -> dict:
    """Run the validated SQL query against the university database.

    Empty result sets (0 rows) are NOT errors — the query may be
    legitimate but return no matching data.

    State keys read:  sql_query
    State keys written: query_result, query_rows, sql_error, steps
    """
    step_tag = "execute_sql"
    sql = state["sql_query"]

    try:
        results = _get_db().execute_query(sql)
        row_count = len(results)
        logger.info("%s: returned %d rows", step_tag, row_count)
        return {
            "query_result": results,
            "query_rows": row_count,
            "sql_error": "",
            "steps": [f"{step_tag}: returned {row_count} rows"],
        }
    except (ValueError, DatabaseError) as exc:
        logger.warning("%s: query failed — %s", step_tag, exc)
        error_msg = str(exc)
        return {
            "query_result": [],
            "query_rows": 0,
            "sql_error": error_msg,
            "steps": [f"{step_tag}: FAILED — {error_msg[:80]}"],
        }


def regenerate_sql(state: AgentState) -> dict:
    """Prepare state for an SQL retry cycle.

    Does NOT call the LLM — that is generate_sql's job. Only increments
    the attempt counter so route_result can detect when we hit max_retries.

    Deliberately does NOT clear sql_error or sql_query so generate_sql
    can read them to build the regeneration prompt.

    State keys read:  attempts, max_retries
    State keys written: attempts, steps
    """
    step_tag = "regenerate_sql"
    attempts = state.get("attempts", 0) + 1
    max_retries = state.get("max_retries", 3)
    failed_attempt = {
        "sql": state.get("sql_query", ""),
        "error": state.get("sql_error", ""),
    }
    logger.info("%s: preparing retry (attempt %d of %d)", step_tag, attempts, max_retries)

    return {
        "attempts": attempts,
        "previous_attempts": [failed_attempt],
        "steps": [f"{step_tag}: preparing retry (attempt {attempts} of {max_retries})"],
    }


def format_answer(state: AgentState) -> dict:
    """Format the SQL query results as a natural-language answer.

    Uses a low-temperature LLM for stable numeric phrasing.
    Falls back to a minimal raw-data response on LLM failure so the
    user still gets their data.

    State keys read:  question, sql_query, query_result, query_rows
    State keys written: answer, steps
    """
    step_tag = "format_answer"
    question = state["question"]
    sql_query = state.get("sql_query", "")
    results = state.get("query_result", [])
    row_count = state.get("query_rows", 0)

    try:
        llm = get_answer_llm()
        formatted_results = _format_results_for_prompt(results, row_count)
        bundle = get_prompt_manager().build_answer_formatting_messages(
            question=question,
            sql_query=sql_query,
            results=formatted_results,
            row_count=row_count,
        )
        messages = bundle.messages
        trace_metadata = bundle.trace_metadata or None
        try:
            structured = invoke_prompt(
                llm.with_structured_output(AnswerResult),
                messages,
                trace_metadata=trace_metadata,
            )
            if not isinstance(structured, AnswerResult):
                raise TypeError("structured output returned unexpected type")
            answer = structured.answer
        except Exception:
            response = invoke_prompt(llm, messages, trace_metadata=trace_metadata)
            answer = _extract_content(response).strip()
        logger.info("%s: formatted answer (%d chars)", step_tag, len(answer))
    except Exception as exc:
        logger.warning("%s: LLM error (%s) — using raw fallback", step_tag, exc)
        _ = _classify_llm_error(exc)  # classify for logging; always use raw fallback
        answer = f"Query returned {row_count} rows. Raw results: {results}"

    return {
        "answer": answer,
        "steps": [f"{step_tag}: answer formatted ({len(answer)} chars)"],
    }


def error_response(state: AgentState) -> dict:
    """Compose a user-friendly error message without calling the LLM.

    Priority order for the answer:
      1. error_message (pre-written user-friendly copy set by validate_sql)
      2. sql_error wrapped in a helpful suggestion to rephrase
      3. Generic fallback

    State keys read:  error_message, sql_error
    State keys written: answer, steps
    """
    step_tag = "error_response"
    error_message = state.get("error_message", "")
    sql_error = state.get("sql_error", "")

    if error_message:
        answer = error_message
    elif sql_error:
        answer = (
            f"I wasn't able to answer your question. The database query encountered "
            f"an error: {sql_error}. Please try rephrasing your question."
        )
    else:
        answer = "An unexpected error occurred. Please try again."

    logger.info("%s: %s", step_tag, answer[:80])
    return {
        "answer": answer,
        "steps": [f"{step_tag}: {answer[:80]}"],
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_relevance(state: AgentState) -> str:
    """Route after check_relevance.

    Returns:
        "polite_decline" if the question is off-topic.
        "fetch_schema" if the question is relevant.
    """
    if "not_relevant" in state.get("relevance", ""):
        return "polite_decline"
    return "fetch_schema"


def route_validation(state: AgentState) -> str:
    """Route after validate_sql.

    Returns:
        "execute_sql" if the query passed validation.
        "regenerate_sql" if sql_error is retryable and within retry budget.
        "error_response" if sql_error is non-retryable or budget exhausted.
    """
    sql_error = state.get("sql_error", "")
    if not sql_error:
        return "execute_sql"
    if (
        sql_error == "cannot_answer"
        or "destructive" in sql_error.lower()
        or "blocked" in sql_error.lower()
    ):
        return "error_response"
    attempts = state.get("attempts", 0)
    max_retries = state.get("max_retries", 3)
    if attempts < max_retries:
        return "regenerate_sql"
    return "error_response"


def route_result(state: AgentState) -> str:
    """Route after execute_sql.

    Three possible outcomes:
      - No error → format the answer.
      - Error within retry budget → regenerate the SQL.
      - Error at or beyond retry limit → surface the error to the user.

    Returns:
        "format_answer", "regenerate_sql", or "error_response".
    """
    sql_error = state.get("sql_error", "")
    attempts = state.get("attempts", 0)
    max_retries = state.get("max_retries", 3)

    if not sql_error:
        return "format_answer"
    if sql_error == "cannot_answer":
        return "error_response"
    if attempts < max_retries:
        return "regenerate_sql"
    return "error_response"
