"""
Pydantic models for structured LLM output in the university QA agent.

Each model corresponds to one LLM call that requires deterministic parsing.
Using with_structured_output(Model) eliminates free-text parsing hacks
and ensures the LLM returns data in the exact shape the node expects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RelevanceResult(BaseModel):
    """Output of the relevance classifier node."""

    classification: Literal["relevant", "not_relevant"]


class SQLResult(BaseModel):
    """Output of the SQL generation node (first attempt)."""

    reasoning: str
    can_answer: bool = True
    sql: str = ""


class SQLRetryResult(BaseModel):
    """Output of the SQL generation node (retry attempt)."""

    diagnosis: str
    can_answer: bool = True
    sql: str = ""


class AnswerResult(BaseModel):
    """Output of the answer formatting node."""

    answer: str
