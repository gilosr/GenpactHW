"""
run_questions.py
────────────────
Executes 20 test questions through the university QA agent:
  - 18 relevant to the university database
  - 2 not relevant (should be politely declined)

Run:
    python run_questions.py
"""

from __future__ import annotations

import time

from agent.cache import QueryCache
from agent.conversation_manager import ConversationManager

QUESTIONS: list[tuple[str, str]] = [
    # ── Complex JOIN + GROUP BY queries ────────────────────────────────────
    (
        "relevant",
        "For each department, show the total number of completed enrollments, "
        "the number of distinct students, and the average grade — ordered by average grade descending.",
    ),
    (
        "relevant",
        "For every teacher, list the number of courses they teach, the total number of "
        "students who completed those courses, and their overall average grade. "
        "Include teachers who have no completed enrollments.",
    ),
    (
        "relevant",
        "Show each student's name, their major, the number of courses they completed, "
        "and their GPA (average completed grade). Only include students who completed "
        "at least 3 courses, ordered by GPA descending.",
    ),
    (
        "relevant",
        "For each semester and year combination, show the department that had the highest "
        "average grade among completed enrollments, along with that average grade.",
    ),
]


def run_all() -> None:
    total = len(QUESTIONS)
    relevant_count = sum(1 for kind, _ in QUESTIONS if kind == "relevant")
    not_relevant_count = total - relevant_count

    print("=" * 70)
    print(f"University QA Agent — {total} Question Test Suite")
    print(f"  {relevant_count} university-related | {not_relevant_count} not relevant")
    print("=" * 70)

    for i, (kind, question) in enumerate(QUESTIONS, start=1):
        label = "[RELEVANT]" if kind == "relevant" else "[NOT RELEVANT]"
        print(f"\n{'─' * 70}")
        print(f"Q{i:02d} {label}  {question}")
        print("─" * 70)

        # Fresh session per question — no conversation bleed between unrelated questions
        cm = ConversationManager(cache=None)
        session_id = cm.create_session()

        t0 = time.perf_counter()
        result = cm.ask(question, session_id, bypass_cache=True)
        elapsed = time.perf_counter() - t0

        answer = result.get("answer", "(no answer)")
        sql = result.get("sql_query", "")
        steps = result.get("steps", [])

        print(f"Answer : {answer}")
        if sql:
            print(f"SQL    : {sql}")
        print(f"Steps  : {' → '.join(steps)}")
        print(f"Time   : {elapsed:.2f}s")

    print(f"\n{'=' * 70}")
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    run_all()
