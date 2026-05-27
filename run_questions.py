"""
run_questions.py
────────────────
Executes test questions from the golden dataset through the university QA agent.

Run:
    python run_questions.py
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from agent.conversation_manager import ConversationManager

def load_questions(csv_path: str | Path) -> list[tuple[str, str]]:
    questions = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tier = row['tier']
            kind = "relevant"
            if tier == "Non-relevant" or tier == "Harm":
                kind = "not relevant"
            questions.append((kind, row['question']))
    return questions

def run_all() -> None:
    csv_path = Path(__file__).parent / "docs" / "golden_dataset.csv"
    questions = load_questions(csv_path)
    
    total = len(questions)
    relevant_count = sum(1 for kind, _ in questions if kind == "relevant")
    not_relevant_count = total - relevant_count

    print("=" * 70)
    print(f"University QA Agent — {total} Question Test Suite (Golden Dataset)")
    print(f"  {relevant_count} university-related | {not_relevant_count} not relevant")
    print("=" * 70)

    for i, (kind, question) in enumerate(questions, start=1):
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
