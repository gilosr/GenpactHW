"""
tests/evals/eval_relevance.py
──────────────────────────────
Label-match eval suite for the relevance classifier.

20 cases: 10 relevant, 10 not_relevant.

For each case:
  1. Formats RELEVANCE_CHECK_PROMPT with the question.
  2. Invokes the LLM and parses the response to "relevant" / "not_relevant".
  3. Asserts the predicted label matches the expected label.

Run:
    pytest tests/evals/eval_relevance.py -m eval -v
"""

from __future__ import annotations

import pytest

from agent.llm import invoke_prompt
from prompts.manager import get_prompt_manager

# ── Label parsing ──────────────────────────────────────────────────────────────

_RELEVANT = "relevant"
_NOT_RELEVANT = "not_relevant"


def _parse_label(content: str) -> str:
    """Extract 'relevant' or 'not_relevant' from the LLM response.

    Handles common output variations:
      - "relevant" / "not_relevant"
      - "Classification: relevant"
      - "The answer is: not_relevant"
      - "Relevant" (capitalised)
    """
    text = content.strip().lower()

    # Check not_relevant before relevant to avoid substring false-positive.
    if "not_relevant" in text or "not relevant" in text:
        return _NOT_RELEVANT
    if "relevant" in text:
        return _RELEVANT

    # Fallback: if the response is ambiguous, default to not_relevant.
    return _NOT_RELEVANT


# ── Test cases ─────────────────────────────────────────────────────────────────

# (question, expected_label, description)
RELEVANCE_CASES: list[tuple[str, str, str]] = [
    # ── Relevant (10 cases) ────────────────────────────────────────────────────
    (
        "How many students are there?",
        _RELEVANT,
        "Direct count query on students table",
    ),
    (
        "Who teaches CS101?",
        _RELEVANT,
        "Course-teacher lookup — core DB relationship",
    ),
    (
        "What is the average grade per teacher?",
        _RELEVANT,
        "Aggregation query spanning teachers, courses, enrollments",
    ),
    (
        "List all students in the Mathematics major",
        _RELEVANT,
        "Filter on student major — straightforward",
    ),
    (
        "Which courses are offered in Fall 2024?",
        _RELEVANT,
        "Semester-based enrollment query",
    ),
    (
        "How many students dropped a course?",
        _RELEVANT,
        "Status-based enrollment count (status='dropped')",
    ),
    (
        "What is the GPA of Alice Johnson?",
        _RELEVANT,
        "Named student query — even if absent from DB, the topic is relevant",
    ),
    (
        "Which department has the most courses?",
        _RELEVANT,
        "Department-level aggregation on courses table",
    ),
    (
        "How many students does Professor Chen advise?",
        _RELEVANT,
        "Advisor relationship query — advisor_id FK on students",
    ),
    (
        "What is the enrollment count per semester?",
        _RELEVANT,
        "GROUP BY semester on enrollments — core analytics query",
    ),

    # ── Not relevant (10 cases) ────────────────────────────────────────────────
    (
        "What is the weather today?",
        _NOT_RELEVANT,
        "Classic irrelevant question — weather data not in schema",
    ),
    (
        "Who won the World Cup?",
        _NOT_RELEVANT,
        "Sports question — nothing to do with university data",
    ),
    (
        "What are Professor Chen's office hours?",
        _NOT_RELEVANT,
        "On-topic entity (Prof. Chen exists) but office hours are not in the schema",
    ),
    (
        "What is 2 + 2?",
        _NOT_RELEVANT,
        "Arithmetic — not a DB question",
    ),
    (
        "Tell me a joke",
        _NOT_RELEVANT,
        "Entertainment request — unrelated to university DB",
    ),
    (
        "What is the stock price of Apple?",
        _NOT_RELEVANT,
        "Financial query — not in schema",
    ),
    (
        "How do I bake a cake?",
        _NOT_RELEVANT,
        "Cooking instruction — entirely off-domain",
    ),
    (
        "Who is the president of the United States?",
        _NOT_RELEVANT,
        "Political question — not in schema",
    ),
    (
        "What programming language should I learn?",
        _NOT_RELEVANT,
        "Career advice — not answerable from university DB",
    ),
    (
        "Translate 'hello' to French",
        _NOT_RELEVANT,
        "Translation task — not a DB question",
    ),
]


# ── Test function ──────────────────────────────────────────────────────────────


def _make_test_id(params: tuple) -> str:
    question, expected, _ = params
    slug = question[:40].lower().replace(" ", "_").replace("'", "").replace("?", "")
    return f"{expected}__{slug}"


@pytest.mark.eval
@pytest.mark.parametrize(
    "question,expected,description",
    RELEVANCE_CASES,
    ids=[_make_test_id(c) for c in RELEVANCE_CASES],
)
def test_relevance(
    question: str,
    expected: str,
    description: str,
    relevance_llm,
) -> None:
    messages = get_prompt_manager().build_relevance_check_messages(question)
    response = invoke_prompt(relevance_llm, messages)
    predicted = _parse_label(response.content)

    assert predicted == expected, (
        f"Relevance mismatch for: {question!r}\n"
        f"Expected: {expected!r}, Got: {predicted!r}\n"
        f"Description: {description}\n"
        f"Raw LLM response: {response.content!r}"
    )
