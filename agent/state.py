"""
LangGraph state schemas for the university QA agent.

Defines the data contract that flows through all graph nodes:
  - AgentState: full internal state (extends MessagesState)
  - InputState: public input (question only)
  - OutputState: public output (answer + execution trace)
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import MessagesState


class InputState(TypedDict):
    question: str


class OutputState(TypedDict):
    answer: str
    steps: list[str]
    sql_query: str
    query_result: list[dict[str, Any]]


class AgentState(MessagesState):
    question: str
    relevance: str
    schema_info: str
    sql_query: str
    query_result: list[dict[str, Any]]
    query_rows: int
    answer: str

    sql_error: str
    error_message: str

    attempts: int
    max_retries: int
    previous_attempts: Annotated[list[dict[str, str]], operator.add]

    steps: Annotated[list[str], operator.add]
