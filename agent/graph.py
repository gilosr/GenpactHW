"""
agent/graph.py
--------------
Assemble the LangGraph StateGraph for the university QA agent.

Wires node functions from agent.nodes into a directed graph with
conditional routing and a retry cycle for failed SQL queries.

Graph flow:
  START → check_relevance ──not_relevant──→ polite_decline → END
                           ↓ relevant
                        fetch_schema
                           ↓
                        generate_sql
                           ↓
                        validate_sql ──destructive/empty──→ error_response → END
                           ↓ safe
                        execute_sql
                           ↓
                       route_result ──success──────────→ format_answer → END
                                    ──retry budget──→ regenerate_sql → generate_sql (CYCLE)
                                    ──no retries left──→ error_response → END

Exports:
  create_graph() — uncompiled StateGraph (for testing / visualization)
  app            — compiled graph with MemorySaver checkpointer
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    check_relevance,
    error_response,
    execute_sql,
    fetch_schema,
    format_answer,
    generate_sql,
    polite_decline,
    regenerate_sql,
    route_relevance,
    route_result,
    route_validation,
    validate_sql,
)
from agent.state import AgentState, InputState, OutputState


def create_graph() -> StateGraph:
    """Build the university QA agent graph (uncompiled).

    Returns the raw StateGraph so callers can compile with
    custom checkpointers or inspect topology before compilation.

    Design decisions:
    - input=InputState: callers only need to pass {"question": "..."}
    - output=OutputState: returns {"answer": "...", "steps": [...]}
      hiding internal fields (sql_query, schema_info, etc.) from consumers
    - Retry cycle: regenerate_sql → generate_sql loops up to max_retries times
    """
    graph = StateGraph(AgentState, input=InputState, output=OutputState)

    # --- Add all 9 nodes ---
    graph.add_node("check_relevance", check_relevance)
    graph.add_node("polite_decline", polite_decline)
    graph.add_node("fetch_schema", fetch_schema)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_sql", validate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("regenerate_sql", regenerate_sql)
    graph.add_node("format_answer", format_answer)
    graph.add_node("error_response", error_response)

    # --- Entry point ---
    graph.add_edge(START, "check_relevance")

    # --- Conditional: relevance → decline or proceed ---
    # route_relevance returns "polite_decline" or "fetch_schema"
    graph.add_conditional_edges("check_relevance", route_relevance)

    # --- Linear: relevant path ---
    graph.add_edge("fetch_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")

    # --- Conditional: SQL validation → execute, retry, or error ---
    # route_validation returns "execute_sql", "regenerate_sql", or "error_response"
    graph.add_conditional_edges("validate_sql", route_validation)

    # --- Conditional: execution result → answer, retry, or error ---
    # route_result returns "format_answer", "regenerate_sql", or "error_response"
    graph.add_conditional_edges("execute_sql", route_result)

    # --- Retry cycle ---
    # regenerate_sql increments attempt counter (no LLM call),
    # then generate_sql detects sql_error is set and uses regeneration prompt
    graph.add_edge("regenerate_sql", "generate_sql")

    # --- Terminal edges ---
    graph.add_edge("polite_decline", END)
    graph.add_edge("format_answer", END)
    graph.add_edge("error_response", END)

    return graph


# Module-level compiled application — ready for production use.
#
# MemorySaver enables multi-turn conversation via thread_id:
#   app.invoke({"question": "..."}, config={"configurable": {"thread_id": "session-1"}})
#
# Why module-level (not lazy)? Graph compilation is cheap (no DB/LLM calls).
# Block 3.1 (conversation_manager.py) imports `app` directly.
app = create_graph().compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Visualization helpers (optional — useful for documentation/debugging)
# ---------------------------------------------------------------------------


def get_graph_mermaid() -> str:
    """Return the Mermaid diagram text for the agent graph.

    The output can be pasted into any Mermaid-capable renderer
    (GitHub markdown, mermaid.live, VS Code extension, etc.).
    """
    return create_graph().compile().get_graph().draw_mermaid()


def save_graph_image(path: str = "docs/graph.png") -> None:
    """Render the agent graph as a PNG image and save to disk.

    Uses LangGraph's built-in Mermaid → PNG renderer (calls mermaid.ink API).
    Requires an internet connection.

    Args:
        path: Destination file path for the PNG. Parent dirs must exist.
    """
    compiled = create_graph().compile()
    png_bytes = compiled.get_graph().draw_mermaid_png()
    with open(path, "wb") as f:
        f.write(png_bytes)
