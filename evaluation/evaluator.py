"""
evaluation/evaluator.py
───────────────────────
Core evaluation engine: CSV parsing, agent execution, LLM-as-a-Judge,
result aggregation, and persistence.

Design decisions:
  - 5-level discrete rubric (EXCELLENT → FAIL) per 2025 best practices
  - Chain-of-thought: judge outputs reasoning BEFORE score
  - Structured JSON output enforced via prompt
  - Sequential processing (one row at a time) for rate-limit safety
  - Background thread for non-blocking API endpoint
  - JSON file persistence in evaluation_runs/ directory
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.cache import QueryCache
from agent.conversation_manager import ConversationManager
from agent.llm import get_llm, invoke_prompt
from config import settings
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

from evaluation.execution_accuracy import compare_result_sets

# ── Score labels and their numeric values ──────────────────────────────────────

SCORE_LABELS = {
    "EXCELLENT": 5,
    "GOOD": 4,
    "ACCEPTABLE": 3,
    "POOR": 2,
    "FAIL": 1,
}

_SQL_COLUMN_KEYWORDS = frozenset({"sql", "query", "select"})
_SKIP_SQL_VALUES = frozenset({"n/a", "blocked", ""})



def _is_sql_column(column_name: str) -> bool:
    """Heuristic: does this column name suggest it contains SQL?"""
    name_lower = column_name.lower().replace("_", " ").replace("-", " ")
    return any(kw in name_lower for kw in _SQL_COLUMN_KEYWORDS)


# ── Data models ────────────────────────────────────────────────────────────────


@dataclass
class ColumnScore:
    """Result of judging one column for one instance."""

    column_name: str
    score: int  # 1–5
    score_label: str  # EXCELLENT | GOOD | ACCEPTABLE | POOR | FAIL
    reasoning: str
    confidence: float
    expected: str
    actual: str


@dataclass
class InstanceResult:
    """Evaluation result for a single CSV row."""

    row_index: int
    input_text: str
    agent_answer: str
    agent_sql: str
    scores: dict[str, ColumnScore] = field(default_factory=dict)  # col_name → ColumnScore
    avg_score: float = 0.0
    passed: bool = False
    latency_ms: int = 0
    error: str | None = None
    execution_accuracy: bool | None = None


@dataclass
class EvaluationRun:
    """A complete evaluation run with all metadata and results."""

    run_id: str
    dataset_name: str
    input_column: str
    eval_columns: list[str]
    status: str = "pending"  # pending | running | completed | failed
    created_at: str = ""
    completed_at: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    current_instance: str | None = None
    instances: list[InstanceResult] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ── Judge prompt template ──────────────────────────────────────────────────────

_JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator for a university database question-answering system.

The system accepts natural language questions about students, teachers, courses,
and enrollments, generates SQL against a SQLite database, executes the query,
and returns a natural language answer.

Your task: score whether the agent's actual output correctly answers the user's
question relative to the expected reference output. Semantic equivalence is what
matters — not phrasing. For factual and numeric answers, exact values are required
for top scores. Answer length does not affect the score."""

_JUDGE_USER_PROMPT = """\
## Task
- **Question asked**: {input_text}
- **Reference answer**: {expected_output}
- **Agent's actual answer**: {actual_output}
- **Evaluation dimension**: {column_name}

## Scoring Rubric
- EXCELLENT (5): All facts and numbers are correct. Phrasing differences are irrelevant.
- GOOD (4): Core numeric/factual result is correct; only minor rounding or formatting variation (e.g., 92 vs 92.67).
- ACCEPTABLE (3): Correct subject/entity but imprecise or incomplete value (e.g., a range instead of an exact count, missing rows).
- POOR (2): Addresses the question but states a factually wrong value or omits significant facts.
- FAIL (1): Factually wrong, off-topic, contradicts the reference, or refuses to answer a valid question.

## Calibration Examples
<examples>
<example>
  <question>How many students are enrolled in CS101?</question>
  <reference>9 students</reference>
  <actual>CS101 has 9 students enrolled.</actual>
  <verdict>EXCELLENT — exact number correct; phrasing irrelevant.</verdict>
</example>
<example>
  <question>What is Alice Johnson's average completed grade?</question>
  <reference>92.67</reference>
  <actual>Alice Johnson's average is approximately 93.</actual>
  <verdict>GOOD — rounded from 92.67 to 93; core fact accurate, minor precision loss.</verdict>
</example>
<example>
  <question>How many students are enrolled in CS101?</question>
  <reference>9 students</reference>
  <actual>CS101 has between 8 and 10 students.</actual>
  <verdict>ACCEPTABLE — correct range but no exact value; imprecise for a database COUNT query.</verdict>
</example>
<example>
  <question>How many students are enrolled in CS101?</question>
  <reference>9 students</reference>
  <actual>CS101 has about 15 students.</actual>
  <verdict>FAIL — wrong number (15 vs 9).</verdict>
</example>
</examples>

## Instructions
Step 1. Identify the key fact(s) in the reference answer (numbers, names, entities).
Step 2. Check whether each key fact appears — exactly or equivalently — in the actual answer.
Step 3. Assign the score level that best fits.

Respond ONLY with valid JSON:
{{
  "reasoning": "Step-by-step comparison of key facts...",
  "score_label": "EXCELLENT or GOOD or ACCEPTABLE or POOR or FAIL",
  "score": 5,
  "confidence": 0.0
}}

confidence: probability you would assign this exact same score if you re-evaluated blind (0.5 = uncertain, 1.0 = certain)."""


# ── Evaluation Engine ──────────────────────────────────────────────────────────


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class EvaluationEngine:
    """Orchestrates CSV evaluation with LLM-as-a-Judge.

    Lifecycle:
      1. store_csv()   — upload and parse CSV, keep in memory
      2. start_evaluation() — kick off background evaluation thread
      3. get_run()     — poll progress
      4. get_results() — fetch completed results
    """

    def __init__(self, results_dir: str | None = None) -> None:
        self._csv_store: dict[str, dict[str, Any]] = {}  # session_id → parsed CSV
        self._runs: dict[str, EvaluationRun] = {}
        self._lock = threading.Lock()
        self._results_dir = _PROJECT_ROOT / (results_dir or settings.eval.results_dir)
        self._results_dir.mkdir(exist_ok=True)
        self._load_persisted_runs()

    def _execute_accuracy(
        self,
        db_manager,
        expected_sql: str,
        agent_sql: str,
    ) -> bool | None:
        """Execute both SQLs and compare result sets. Returns None if not applicable."""
        if expected_sql.strip().lower() in _SKIP_SQL_VALUES or not agent_sql.strip():
            return None
        try:
            expected_results = db_manager.execute_query(expected_sql)
        except Exception:
            return None  # bad reference SQL = skip, not penalize
        try:
            actual_results = db_manager.execute_query(agent_sql)
        except Exception:
            return False  # agent SQL crashed = fail
        return compare_result_sets(expected_results, actual_results)

    # ── CSV handling ───────────────────────────────────────────────────────

    def parse_csv(self, file_bytes: bytes, filename: str = "upload.csv") -> dict[str, Any]:
        """Parse uploaded CSV and return column info + preview rows.

        Returns:
            dict with keys: session_id, columns, preview (first 5 rows),
                            row_count, filename
        """
        text = file_bytes.decode("utf-8-sig")  # handle BOM
        reader = csv.DictReader(io.StringIO(text))
        columns = reader.fieldnames or []
        rows = list(reader)

        if not columns or not rows:
            raise ValueError("CSV file is empty or has no data rows.")
        if len(columns) < 2:
            raise ValueError(
                "CSV must have at least 2 columns (one input + one evaluation column)."
            )

        session_id = str(uuid.uuid4())
        self._csv_store[session_id] = {
            "columns": columns,
            "rows": rows,
            "filename": filename,
        }

        return {
            "session_id": session_id,
            "columns": list(columns),
            "preview": rows[:5],
            "row_count": len(rows),
            "filename": filename,
        }

    def get_csv(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve stored CSV data by session ID."""
        return self._csv_store.get(session_id)

    # ── Evaluation lifecycle ───────────────────────────────────────────────

    def start_evaluation(
        self,
        session_id: str,
        input_column: str,
        eval_columns: list[str],
        dataset_name: str = "Untitled",
    ) -> str:
        """Start an evaluation run in a background thread.

        Args:
            session_id: CSV session from parse_csv()
            input_column: Column name containing the input/question
            eval_columns: List of column names to evaluate against
            dataset_name: Human-readable name for the run

        Returns:
            run_id string for polling status

        Raises:
            ValueError: if session_id not found or columns invalid
        """
        csv_data = self._csv_store.get(session_id)
        if not csv_data:
            raise ValueError(f"CSV session '{session_id}' not found. Upload a CSV first.")

        available = set(csv_data["columns"])
        if input_column not in available:
            raise ValueError(f"Input column '{input_column}' not found in CSV.")
        for col in eval_columns:
            if col not in available:
                raise ValueError(f"Eval column '{col}' not found in CSV.")

        run_id = str(uuid.uuid4())
        run = EvaluationRun(
            run_id=run_id,
            dataset_name=dataset_name,
            input_column=input_column,
            eval_columns=eval_columns,
            status="running",
            created_at=datetime.now(timezone.utc).isoformat(),
            progress_total=len(csv_data["rows"]),
        )

        with self._lock:
            self._runs[run_id] = run

        thread = threading.Thread(
            target=self._run_evaluation_sync,
            args=(run, csv_data["rows"]),
            daemon=True,
        )
        thread.start()

        return run_id

    def get_run(self, run_id: str) -> EvaluationRun | None:
        """Get an evaluation run by ID."""
        return self._runs.get(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        """List all runs with summary info (no instance details)."""
        summaries = []
        for run in sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True):
            summaries.append({
                "run_id": run.run_id,
                "dataset_name": run.dataset_name,
                "status": run.status,
                "created_at": run.created_at,
                "total_instances": run.progress_total,
                "progress_current": run.progress_current,
                "overall_avg_score": run.statistics.get("overall_avg_score", 0),
                "overall_pass_rate": run.statistics.get("overall_pass_rate", 0),
            })
        return summaries

    # ── Background evaluation loop ─────────────────────────────────────────

    def _run_evaluation_sync(self, run: EvaluationRun, rows: list[dict[str, str]]) -> None:
        """Run the evaluation pipeline synchronously (called in a thread)."""
        manager = ConversationManager(cache=QueryCache())
        start_time = time.time()

        try:
            for i, row in enumerate(rows):
                input_text = row.get(run.input_column, "").strip()
                if not input_text:
                    # Skip empty input rows
                    run.progress_current = i + 1
                    continue

                run.current_instance = f"Row {i + 1}: {input_text[:60]}..."
                run.progress_current = i + 1

                instance = self._evaluate_instance(
                    manager=manager,
                    row=row,
                    row_index=i + 1,
                    input_column=run.input_column,
                    eval_columns=run.eval_columns,
                )
                run.instances.append(instance)
                
                # Persist every 2 instances to balance safety and I/O
                if (i + 1) % 2 == 0:
                    self._persist_run(run)

            run.statistics = self._compute_statistics(run)
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc).isoformat()
            run.current_instance = None

        except Exception as exc:
            logger.exception("Evaluation run %s failed", run.run_id)
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            run.completed_at = datetime.now(timezone.utc).isoformat()

        finally:
            total_elapsed = time.time() - start_time
            run.statistics["total_latency_s"] = round(total_elapsed, 2)
            self._persist_run(run)

    def _evaluate_instance(
        self,
        manager: ConversationManager,
        row: dict[str, str],
        row_index: int,
        input_column: str,
        eval_columns: list[str],
    ) -> InstanceResult:
        """Evaluate a single CSV row: run agent, then judge each column."""
        input_text = row.get(input_column, "").strip()
        instance_start = time.time()

        # 1. Run the agent
        agent_answer = ""
        agent_sql = ""
        try:
            thread_id = manager.create_session()
            result = manager.ask(input_text, thread_id=thread_id, bypass_cache=True)
            agent_answer = result.get("answer", "")
            agent_sql = result.get("sql_query", "")
        except Exception as exc:
            logger.warning("Agent failed for row %d: %s", row_index, exc)
            agent_answer = f"[Agent Error: {type(exc).__name__}]"

        # 2. Judge each evaluation column
        scores: dict[str, ColumnScore] = {}
        for col in eval_columns:
            expected = row.get(col, "").strip()
            actual = agent_sql if _is_sql_column(col) else agent_answer
            col_score = self._judge_column(
                input_text=input_text,
                actual_output=actual,
                expected_output=expected,
                column_name=col,
            )
            scores[col] = col_score

        # 3. Compute averages
        score_values = [s.score for s in scores.values()]
        avg_score = sum(score_values) / len(score_values) if score_values else 0.0
        passed = avg_score >= 3.0  # ACCEPTABLE threshold

        elapsed_ms = int((time.time() - instance_start) * 1000)

        # 4. Execution accuracy (deterministic, no LLM)
        expected_sql = row.get("expected_sql", "").strip()
        if expected_sql:
            from agent.nodes import _get_db
            db_manager = getattr(manager, "_db_manager", None) or _get_db()
            ex_result = self._execute_accuracy(
                db_manager=db_manager,
                expected_sql=expected_sql,
                agent_sql=agent_sql,
            )
        else:
            ex_result = None

        return InstanceResult(
            row_index=row_index,
            input_text=input_text,
            agent_answer=agent_answer,
            agent_sql=agent_sql,
            scores=scores,
            avg_score=round(avg_score, 2),
            passed=passed,
            latency_ms=elapsed_ms,
            execution_accuracy=ex_result,
        )

    def _judge_column(
        self,
        input_text: str,
        actual_output: str,
        expected_output: str,
        column_name: str,
    ) -> ColumnScore:
        """Call the LLM judge for a single column evaluation."""
        try:
            llm = get_llm(
                temperature=settings.eval.judge_temperature,
                model=settings.eval.judge_model,
            )

            user_prompt = _JUDGE_USER_PROMPT.format(
                input_text=input_text,
                expected_output=expected_output,
                actual_output=actual_output,
                column_name=column_name,
            )

            messages = [
                SystemMessage(content=_JUDGE_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]

            response = invoke_prompt(
                llm,
                messages,
                trace_metadata={"task": "evaluation_judge", "column": column_name},
            )

            return self._parse_judge_response(
                response.content, column_name, expected_output, actual_output
            )

        except Exception as exc:
            logger.warning("Judge failed for column '%s': %s", column_name, exc)
            return ColumnScore(
                column_name=column_name,
                score=1,
                score_label="FAIL",
                reasoning=f"Judge error: {type(exc).__name__}: {exc}",
                confidence=0.0,
                expected=expected_output,
                actual=actual_output,
            )

    def _parse_judge_response(
        self,
        content: str,
        column_name: str,
        expected: str,
        actual: str,
    ) -> ColumnScore:
        """Parse the judge's JSON response, with fallback for malformed output."""
        try:
            # Strip markdown code fences if present
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                text = text.strip()

            data = json.loads(text)

            score_label = str(data.get("score_label", "FAIL")).upper().strip()
            if score_label not in SCORE_LABELS:
                score_label = "FAIL"

            score = SCORE_LABELS[score_label]
            reasoning = str(data.get("reasoning", "No reasoning provided."))
            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            return ColumnScore(
                column_name=column_name,
                score=score,
                score_label=score_label,
                reasoning=reasoning,
                confidence=confidence,
                expected=expected,
                actual=actual,
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse judge response: %s", exc)
            return ColumnScore(
                column_name=column_name,
                score=1,
                score_label="FAIL",
                reasoning=f"Failed to parse judge response: {exc}. Raw: {content[:200]}",
                confidence=0.0,
                expected=expected,
                actual=actual,
            )

    # ── Statistics ─────────────────────────────────────────────────────────

    def _compute_statistics(self, run: EvaluationRun) -> dict[str, Any]:
        """Compute aggregate statistics for a completed run."""
        instances = run.instances
        if not instances:
            return {"overall_avg_score": 0, "overall_pass_rate": 0, "total_instances": 0}

        # Overall
        all_avg_scores = [inst.avg_score for inst in instances]
        overall_avg = sum(all_avg_scores) / len(all_avg_scores) if all_avg_scores else 0.0
        overall_pass_rate = sum(1 for inst in instances if inst.passed) / len(instances)

        # Per-column breakdown
        per_column_avg: dict[str, float] = {}
        per_column_pass_rate: dict[str, float] = {}
        per_column_distribution: dict[str, dict[str, int]] = {}

        for col in run.eval_columns:
            col_scores = [inst.scores[col].score for inst in instances if col in inst.scores]
            col_labels = [inst.scores[col].score_label for inst in instances if col in inst.scores]

            if col_scores:
                per_column_avg[col] = round(sum(col_scores) / len(col_scores), 2)
                per_column_pass_rate[col] = round(
                    sum(1 for s in col_scores if s >= 3) / len(col_scores), 4
                )
            else:
                per_column_avg[col] = 0
                per_column_pass_rate[col] = 0

            dist = {label: 0 for label in SCORE_LABELS}
            for label in col_labels:
                dist[label] = dist.get(label, 0) + 1
            per_column_distribution[col] = dist

        # Overall distribution
        all_labels: list[str] = []
        for inst in instances:
            for col_score in inst.scores.values():
                all_labels.append(col_score.score_label)

        score_distribution = {label: 0 for label in SCORE_LABELS}
        for label in all_labels:
            score_distribution[label] = score_distribution.get(label, 0) + 1

        ex_applicable = [i for i in instances if getattr(i, "execution_accuracy", None) is not None]
        ex_matches = sum(1 for i in ex_applicable if i.execution_accuracy is True)
        ex_total = len(ex_applicable)

        return {
            "overall_avg_score": round(overall_avg, 2),
            "overall_pass_rate": round(overall_pass_rate, 4),
            "per_column_avg": per_column_avg,
            "per_column_pass_rate": per_column_pass_rate,
            "score_distribution": score_distribution,
            "per_column_distribution": per_column_distribution,
            "total_instances": len(instances),
            "total_judge_calls": sum(len(inst.scores) for inst in instances),
            "total_latency_s": 0,  # filled by caller
            "min_score": min(all_avg_scores) if all_avg_scores else 0,
            "max_score": max(all_avg_scores) if all_avg_scores else 0,
            "execution_accuracy": round(ex_matches / ex_total, 4) if ex_total else None,
            "execution_accuracy_total": ex_total,
            "execution_accuracy_matches": ex_matches,
        }

    # ── Persistence ────────────────────────────────────────────────────────

    def _persist_run(self, run: EvaluationRun) -> None:
        """Save a completed run to disk as JSON."""
        try:
            filepath = self._results_dir / f"{run.run_id}.json"
            data = self._run_to_dict(run)
            filepath.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("Failed to persist run %s: %s", run.run_id, exc)

    def _load_persisted_runs(self) -> None:
        """Load previously saved runs from disk."""
        if not self._results_dir.exists():
            return
        for filepath in self._results_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text())
                run = self._dict_to_run(data)
                self._runs[run.run_id] = run
            except Exception as exc:
                logger.warning("Failed to load run %s: %s", filepath.name, exc)

    # ── Serialisation helpers ──────────────────────────────────────────────

    @staticmethod
    def _run_to_dict(run: EvaluationRun) -> dict[str, Any]:
        """Convert an EvaluationRun to a JSON-serialisable dict."""
        instances = []
        for inst in run.instances:
            scores_dict = {}
            for col_name, cs in inst.scores.items():
                scores_dict[col_name] = {
                    "column_name": cs.column_name,
                    "score": cs.score,
                    "score_label": cs.score_label,
                    "reasoning": cs.reasoning,
                    "confidence": cs.confidence,
                    "expected": cs.expected,
                    "actual": cs.actual,
                }
            instances.append({
                "row_index": inst.row_index,
                "input_text": inst.input_text,
                "agent_answer": inst.agent_answer,
                "agent_sql": inst.agent_sql,
                "scores": scores_dict,
                "avg_score": inst.avg_score,
                "passed": inst.passed,
                "latency_ms": inst.latency_ms,
                "error": inst.error,
                "execution_accuracy": inst.execution_accuracy,
            })

        return {
            "run_id": run.run_id,
            "dataset_name": run.dataset_name,
            "input_column": run.input_column,
            "eval_columns": run.eval_columns,
            "status": run.status,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "progress_current": run.progress_current,
            "progress_total": run.progress_total,
            "instances": instances,
            "statistics": run.statistics,
            "error": run.error,
        }

    @staticmethod
    def _dict_to_run(data: dict[str, Any]) -> EvaluationRun:
        """Reconstruct an EvaluationRun from a persisted dict."""
        instances = []
        for inst_data in data.get("instances", []):
            scores = {}
            for col_name, cs_data in inst_data.get("scores", {}).items():
                scores[col_name] = ColumnScore(**cs_data)
            instances.append(InstanceResult(
                row_index=inst_data["row_index"],
                input_text=inst_data["input_text"],
                agent_answer=inst_data["agent_answer"],
                agent_sql=inst_data.get("agent_sql", ""),
                scores=scores,
                avg_score=inst_data.get("avg_score", 0),
                passed=inst_data.get("passed", False),
                latency_ms=inst_data.get("latency_ms", 0),
                error=inst_data.get("error"),
                execution_accuracy=inst_data.get("execution_accuracy"),
            ))

        return EvaluationRun(
            run_id=data["run_id"],
            dataset_name=data["dataset_name"],
            input_column=data["input_column"],
            eval_columns=data["eval_columns"],
            status=data.get("status", "completed"),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at"),
            progress_current=data.get("progress_current", 0),
            progress_total=data.get("progress_total", 0),
            instances=instances,
            statistics=data.get("statistics", {}),
            error=data.get("error"),
        )

