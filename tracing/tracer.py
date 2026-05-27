"""
tracing/tracer.py
─────────────────
Tracing utilities for the university QA agent.

Two complementary tracing systems:
1. LangSmith (automatic) — visual traces in the web UI
   Set LANGSMITH_TRACING=true + LANGSMITH_API_KEY in .env
   LangGraph auto-traces all node executions and LLM calls.

2. State audit trail (steps field) — programmatic trace
   Every node appends to state["steps"]. This module provides
   utilities to format, inspect, and display those traces.

Interview usage:
   from tracing.tracer import print_trace
   print_trace(result)  # shows clean numbered trace for demo
"""

from __future__ import annotations

import datetime
import os
from typing import Optional

from config import settings


def verify_langsmith_config() -> dict:
    """Check whether LangSmith tracing is properly configured.

    Reads the standard LangSmith environment variables and reports
    configuration status with actionable warnings.

    Returns:
        dict with keys:
          - "configured" (bool): True if all required vars are set
          - "project" (str or None): LANGSMITH_PROJECT value
          - "warnings" (list[str]): Missing or misconfigured vars
    """
    warnings: list[str] = []

    tracing_enabled = os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
    api_key = os.getenv("LANGSMITH_API_KEY", "")
    project = os.getenv("LANGSMITH_PROJECT") or None

    if not tracing_enabled:
        warnings.append(
            "LANGSMITH_TRACING is not set to 'true' — LangSmith tracing disabled."
        )
    if not api_key:
        warnings.append(
            "LANGSMITH_API_KEY is not set — LangSmith cannot authenticate."
        )
    if not project:
        warnings.append(
            "LANGSMITH_PROJECT is not set — traces will appear in the default project."
        )

    configured = tracing_enabled and bool(api_key)

    hub_enabled = settings.prompt.hub_enabled
    if hub_enabled and api_key:
        hub_prompts = "configured"
    else:
        hub_prompts = "fallback-only"

    return {
        "configured": configured,
        "project": project,
        "warnings": warnings,
        "hub_enabled": hub_enabled,
        "hub_prompts": hub_prompts,
    }


def format_trace(steps: list[str]) -> str:
    """Pretty-print execution steps as a numbered, indented list.

    Args:
        steps: List of step strings from state["steps"].

    Returns:
        Human-readable multiline string.

    Example output:
        1. check_relevance: relevant
        2. fetch_schema: loaded schema (1432 chars)
        3. generate_sql: generated SQL — SELECT COUNT(*) FROM students
    """
    if not steps:
        return "(no steps recorded)"
    lines = [f"  {i}. {step}" for i, step in enumerate(steps, 1)]
    return "\n".join(lines)


def format_trace_json(
    steps: list[str],
    question: str,
    answer: str,
    sql_query: str = "",
) -> dict:
    """Build a JSON-serializable trace dict with metadata.

    Suitable for logging, API responses, and structured trace inspection
    in tests.

    Args:
        steps: Execution steps from state["steps"].
        question: The original user question.
        answer: The final answer produced by the agent.
        sql_query: The SQL query that was executed (empty if none).

    Returns:
        dict with keys: question, answer, sql_query, steps,
                        node_count, has_retry, timestamp
    """
    node_names = [step.split(":")[0].strip() for step in steps if ":" in step]
    has_retry = "regenerate_sql" in node_names

    return {
        "question": question,
        "answer": answer,
        "sql_query": sql_query,
        "steps": steps,
        "node_count": len(steps),
        "has_retry": has_retry,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


def print_trace(result: dict) -> None:
    """Print a human-readable execution trace for demos and interviews.

    Designed to clearly show the full question → nodes → SQL → answer
    flow. The node name is extracted from each step string and shown
    in brackets for quick orientation.

    Args:
        result: Dict returned by ConversationManager.ask() or app.invoke().
                Expected keys: question, steps, sql_query, answer.
    """
    print(f"\n{'=' * 60}")
    print(f"Question: {result.get('question', 'N/A')}")
    print(f"{'=' * 60}")

    steps = result.get("steps", [])
    if steps:
        for i, step in enumerate(steps, 1):
            node = step.split(":")[0].strip() if ":" in step else "unknown"
            print(f"  {i}. [{node}] {step}")
    else:
        print("  (no steps recorded)")

    sql = result.get("sql_query", "")
    if sql:
        print(f"\nSQL: {sql}")

    answer = result.get("answer", "N/A")
    print(f"\nAnswer: {answer}")
    print(f"{'=' * 60}\n")


def get_trace_summary(steps: list[str]) -> dict:
    """Extract key signal from the steps audit trail.

    Useful for tests and programmatic inspection — answers:
    - Which nodes were visited?
    - How many retries occurred?
    - Did it error or decline?

    Args:
        steps: Execution steps from state["steps"].

    Returns:
        dict with keys:
          - "nodes_visited" (list[str]): Node names in order
          - "retry_count" (int): Number of regenerate_sql steps
          - "had_error" (bool): True if error_response node visited
          - "was_declined" (bool): True if polite_decline node visited
    """
    nodes_visited: list[str] = []
    retry_count = 0
    had_error = False
    was_declined = False

    for step in steps:
        node = step.split(":")[0].strip() if ":" in step else step.strip()
        nodes_visited.append(node)
        if node == "regenerate_sql":
            retry_count += 1
        if node == "error_response":
            had_error = True
        if node == "polite_decline":
            was_declined = True

    return {
        "nodes_visited": nodes_visited,
        "retry_count": retry_count,
        "had_error": had_error,
        "was_declined": was_declined,
    }
