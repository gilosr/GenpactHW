"""
tests/test_evaluator.py
───────────────────────
Unit tests for evaluation/evaluator.py.

Tests call the engine directly — no LLM calls, no HTTP.
Background threads are not started (start_evaluation raises before spawning
when given bad inputs; happy-path threading is covered by test_eval_routes.py).
"""

from __future__ import annotations

import json

import pytest

from evaluation.evaluator import (
    ColumnScore,
    EvaluationEngine,
    EvaluationRun,
    InstanceResult,
    SCORE_LABELS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """Fresh EvaluationEngine backed by a temporary directory."""
    return EvaluationEngine(results_dir=str(tmp_path))


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


def _bom_csv(content: str) -> bytes:
    return b"\xef\xbb\xbf" + content.encode("utf-8")


# ── parse_csv ─────────────────────────────────────────────────────────────────


class TestParseCsv:
    def test_returns_session_id_columns_and_row_count(self, engine):
        data = _csv("question,expected_answer\nHow many students?,20\nWhat is 2+2?,4\n")
        result = engine.parse_csv(data)
        assert "session_id" in result
        assert result["columns"] == ["question", "expected_answer"]
        assert result["row_count"] == 2
        assert len(result["preview"]) == 2

    def test_preview_returns_all_rows(self, engine):
        rows = "\n".join(f"q{i},a{i}" for i in range(10))
        data = _csv(f"question,answer\n{rows}\n")
        result = engine.parse_csv(data)
        assert len(result["preview"]) == 10
        assert result["row_count"] == 10

    def test_bom_prefix_stripped(self, engine):
        data = _bom_csv("question,answer\nHello,World\n")
        result = engine.parse_csv(data)
        assert result["columns"][0] == "question"  # no BOM character in column name

    def test_empty_file_raises_value_error(self, engine):
        with pytest.raises(ValueError, match="empty"):
            engine.parse_csv(b"")

    def test_header_only_raises_value_error(self, engine):
        with pytest.raises(ValueError, match="empty"):
            engine.parse_csv(_csv("question,answer\n"))

    def test_single_column_raises_value_error(self, engine):
        with pytest.raises(ValueError, match="at least 2 columns"):
            engine.parse_csv(_csv("question\nHow many students?\n"))

    def test_session_stored_and_retrievable(self, engine):
        data = _csv("q,a\ntext,val\n")
        result = engine.parse_csv(data)
        stored = engine.get_csv(result["session_id"])
        assert stored is not None
        assert stored["columns"] == ["q", "a"]


# ── _parse_judge_response ─────────────────────────────────────────────────────


class TestParseJudgeResponse:
    def test_valid_json_returns_correct_score(self, engine):
        payload = json.dumps(
            {
                "reasoning": "The answer matches exactly.",
                "score_label": "EXCELLENT",
                "score": 5,
                "confidence": 0.98,
            }
        )
        cs = engine._parse_judge_response(payload, "answer", "20 students", "20 students")
        assert cs.score == 5
        assert cs.score_label == "EXCELLENT"
        assert cs.confidence == pytest.approx(0.98)
        assert "matches" in cs.reasoning

    def test_markdown_fences_stripped(self, engine):
        payload = "```json\n" + json.dumps(
            {"reasoning": "ok", "score_label": "GOOD", "score": 4, "confidence": 0.9}
        ) + "\n```"
        cs = engine._parse_judge_response(payload, "col", "exp", "act")
        assert cs.score == 4
        assert cs.score_label == "GOOD"

    def test_unknown_score_label_defaults_to_fail(self, engine):
        payload = json.dumps(
            {"reasoning": "odd", "score_label": "PERFECT", "score": 5, "confidence": 0.5}
        )
        cs = engine._parse_judge_response(payload, "col", "exp", "act")
        assert cs.score_label == "FAIL"
        assert cs.score == SCORE_LABELS["FAIL"]

    def test_malformed_json_returns_fail(self, engine):
        cs = engine._parse_judge_response("not json at all", "col", "exp", "act")
        assert cs.score_label == "FAIL"
        assert cs.score == 1
        assert cs.confidence == 0.0
        assert cs.expected == "exp"
        assert cs.actual == "act"

    def test_confidence_clamped_to_0_1(self, engine):
        payload = json.dumps(
            {"reasoning": "r", "score_label": "GOOD", "score": 4, "confidence": 99.0}
        )
        cs = engine._parse_judge_response(payload, "col", "exp", "act")
        assert cs.confidence == pytest.approx(1.0)


# ── _compute_statistics ───────────────────────────────────────────────────────


def _make_run(instances: list[InstanceResult]) -> EvaluationRun:
    run = EvaluationRun(
        run_id="test-run",
        dataset_name="Test",
        input_column="question",
        eval_columns=["expected_answer"],
    )
    run.instances = instances
    return run


def _make_instance(score: int, score_label: str, passed: bool) -> InstanceResult:
    cs = ColumnScore(
        column_name="expected_answer",
        score=score,
        score_label=score_label,
        reasoning="ok",
        confidence=0.9,
        expected="exp",
        actual="act",
    )
    return InstanceResult(
        row_index=1,
        input_text="q",
        agent_answer="a",
        agent_sql="",
        scores={"expected_answer": cs},
        avg_score=float(score),
        passed=passed,
    )


class TestComputeStatistics:
    def test_normal_run_computes_correct_overall_avg(self, engine):
        instances = [
            _make_instance(5, "EXCELLENT", True),
            _make_instance(3, "ACCEPTABLE", True),
            _make_instance(1, "FAIL", False),
        ]
        run = _make_run(instances)
        stats = engine._compute_statistics(run)
        assert stats["overall_avg_score"] == pytest.approx(3.0)
        assert stats["overall_pass_rate"] == pytest.approx(2 / 3, abs=0.001)
        assert stats["total_instances"] == 3

    def test_all_excellent_has_full_pass_rate(self, engine):
        instances = [_make_instance(5, "EXCELLENT", True) for _ in range(4)]
        run = _make_run(instances)
        stats = engine._compute_statistics(run)
        assert stats["overall_avg_score"] == pytest.approx(5.0)
        assert stats["overall_pass_rate"] == pytest.approx(1.0)

    def test_zero_instances_returns_zeros_without_crash(self, engine):
        run = _make_run([])
        stats = engine._compute_statistics(run)
        assert stats["overall_avg_score"] == 0
        assert stats["overall_pass_rate"] == 0
        assert stats["total_instances"] == 0

    def test_score_distribution_counts_labels(self, engine):
        instances = [
            _make_instance(5, "EXCELLENT", True),
            _make_instance(5, "EXCELLENT", True),
            _make_instance(1, "FAIL", False),
        ]
        run = _make_run(instances)
        stats = engine._compute_statistics(run)
        dist = stats["score_distribution"]
        assert dist["EXCELLENT"] == 2
        assert dist["FAIL"] == 1
        assert dist["GOOD"] == 0


# ── start_evaluation validation ───────────────────────────────────────────────


class TestStartEvaluationValidation:
    def test_unknown_session_raises_value_error(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.start_evaluation(
                session_id="does-not-exist",
                input_column="question",
                eval_columns=["answer"],
            )

    def test_invalid_input_column_raises_value_error(self, engine):
        data = _csv("question,answer\nHow many?,42\n")
        result = engine.parse_csv(data)
        with pytest.raises(ValueError, match="not found"):
            engine.start_evaluation(
                session_id=result["session_id"],
                input_column="nonexistent",
                eval_columns=["answer"],
            )

    def test_invalid_eval_column_raises_value_error(self, engine):
        data = _csv("question,answer\nHow many?,42\n")
        result = engine.parse_csv(data)
        with pytest.raises(ValueError, match="not found"):
            engine.start_evaluation(
                session_id=result["session_id"],
                input_column="question",
                eval_columns=["no_such_col"],
            )


# ── Judge prompt content ─────────────────────────────────────────────────────


class TestJudgePromptContent:
    def test_system_prompt_mentions_university_domain(self):
        from evaluation.evaluator import _JUDGE_SYSTEM_PROMPT
        assert "university" in _JUDGE_SYSTEM_PROMPT.lower()
        assert "sql" in _JUDGE_SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_generic_phrasing(self):
        from evaluation.evaluator import _JUDGE_SYSTEM_PROMPT
        assert "thorough in your analysis" not in _JUDGE_SYSTEM_PROMPT


class TestJudgeUserPromptContent:
    def test_user_prompt_contains_calibration_examples(self):
        from evaluation.evaluator import _JUDGE_USER_PROMPT
        assert "<examples>" in _JUDGE_USER_PROMPT
        assert "EXCELLENT" in _JUDGE_USER_PROMPT
        assert "FAIL" in _JUDGE_USER_PROMPT

    def test_rubric_mentions_numeric_accuracy(self):
        from evaluation.evaluator import _JUDGE_USER_PROMPT
        assert "number" in _JUDGE_USER_PROMPT.lower() or "numeric" in _JUDGE_USER_PROMPT.lower()




# ── _is_sql_column ──────────────────────────────────────────────────────────


from evaluation.evaluator import _is_sql_column


class TestIsSqlColumn:
    def test_expected_sql_is_sql_column(self):
        assert _is_sql_column("expected_sql") is True

    def test_sql_query_is_sql_column(self):
        assert _is_sql_column("sql_query") is True

    def test_expected_answer_hint_is_not_sql_column(self):
        assert _is_sql_column("expected_answer_hint") is False

    def test_answer_is_not_sql_column(self):
        assert _is_sql_column("answer") is False

    def test_select_query_is_sql_column(self):
        assert _is_sql_column("select_query") is True


# ── Column-type dispatch ────────────────────────────────────────────────────


class TestColumnTypeDispatch:
    def test_sql_column_uses_agent_sql_as_actual(self, engine):
        """When eval column looks like SQL, _judge_column receives agent_sql, not agent_answer."""
        import unittest.mock as mock

        with mock.patch.object(engine, "_judge_column") as mock_judge:
            mock_judge.return_value = ColumnScore(
                column_name="expected_sql",
                score=5,
                score_label="EXCELLENT",
                reasoning="ok",
                confidence=0.9,
                expected="SELECT 1",
                actual="SELECT 1",
            )

            mock_manager = mock.MagicMock()
            mock_manager.create_session.return_value = "t1"
            mock_manager.ask.return_value = {
                "answer": "There is 1 teacher.",
                "sql_query": "SELECT COUNT(*) FROM teachers",
            }

            engine._evaluate_instance(
                manager=mock_manager,
                row={"question": "How many?", "expected_sql": "SELECT COUNT(*) FROM teachers"},
                row_index=1,
                input_column="question",
                eval_columns=["expected_sql"],
            )

            call_kwargs = mock_judge.call_args
            assert call_kwargs.kwargs["actual_output"] == "SELECT COUNT(*) FROM teachers"

    def test_answer_column_uses_agent_answer_as_actual(self, engine):
        """When eval column looks like NL, _judge_column receives agent_answer."""
        import unittest.mock as mock

        with mock.patch.object(engine, "_judge_column") as mock_judge:
            mock_judge.return_value = ColumnScore(
                column_name="expected_answer_hint",
                score=5,
                score_label="EXCELLENT",
                reasoning="ok",
                confidence=0.9,
                expected="6 teachers",
                actual="6 teachers",
            )

            mock_manager = mock.MagicMock()
            mock_manager.create_session.return_value = "t1"
            mock_manager.ask.return_value = {
                "answer": "There are 6 teachers.",
                "sql_query": "SELECT COUNT(*) FROM teachers",
            }

            engine._evaluate_instance(
                manager=mock_manager,
                row={"question": "How many?", "expected_answer_hint": "6 teachers"},
                row_index=1,
                input_column="question",
                eval_columns=["expected_answer_hint"],
            )

            call_kwargs = mock_judge.call_args
            assert call_kwargs.kwargs["actual_output"] == "There are 6 teachers."


class TestExecuteAccuracy:
    def test_matching_sql_returns_true(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = [
            [{"count": 9}],   # expected_sql result
            [{"count": 9}],   # agent_sql result
        ]
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="SELECT COUNT(*) as count FROM students",
            agent_sql="SELECT COUNT(*) as count FROM students WHERE 1=1",
        )
        assert is_match is True

    def test_mismatching_sql_returns_false(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = [
            [{"count": 9}],
            [{"count": 15}],
        ]
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="SELECT COUNT(*) as count FROM students",
            agent_sql="SELECT COUNT(*) as count FROM teachers",
        )
        assert is_match is False

    def test_na_expected_sql_returns_none(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="N/A",
            agent_sql="SELECT 1",
        )
        assert is_match is None
        mock_db.execute_query.assert_not_called()

    def test_blocked_expected_sql_returns_none(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="BLOCKED",
            agent_sql="SELECT 1",
        )
        assert is_match is None

    def test_empty_agent_sql_returns_none(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="SELECT 1",
            agent_sql="",
        )
        assert is_match is None

    def test_agent_sql_execution_error_returns_false(self, engine):
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db.execute_query.side_effect = [
            [{"count": 9}],
            Exception("syntax error"),
        ]
        is_match, *_ = engine._execute_accuracy(
            db_manager=mock_db,
            expected_sql="SELECT COUNT(*) FROM students",
            agent_sql="SELEC BROKEN",
        )
        assert is_match is False


class TestExecuteAccuracyIntegration:
    def test_instance_result_includes_execution_accuracy(self, engine):
        import unittest.mock as mock

        with mock.patch.object(engine, "_judge_column") as mock_judge, \
             mock.patch.object(engine, "_execute_accuracy", return_value=(True, {}, [], [])) as mock_ex:
            mock_judge.return_value = ColumnScore(
                column_name="expected_answer_hint",
                score=5, score_label="EXCELLENT",
                reasoning="ok", confidence=0.9,
                expected="6 teachers", actual="6 teachers",
            )

            mock_manager = mock.MagicMock()
            mock_manager.create_session.return_value = "t1"
            mock_manager.ask.return_value = {
                "answer": "There are 6 teachers.",
                "sql_query": "SELECT COUNT(*) FROM teachers",
            }

            result = engine._evaluate_instance(
                manager=mock_manager,
                row={
                    "question": "How many?",
                    "expected_answer_hint": "6 teachers",
                    "expected_sql": "SELECT COUNT(*) FROM teachers",
                },
                row_index=1,
                input_column="question",
                eval_columns=["expected_answer_hint"],
            )

            assert result.execution_accuracy is True


class TestExecutionAccuracyStatistics:
    def test_statistics_include_execution_accuracy(self, engine):
        from evaluation.evaluator import InstanceResult, ColumnScore
        instances = [
            InstanceResult(
                row_index=1, input_text="Q1",
                agent_answer="A1", agent_sql="S1",
                scores={"col": ColumnScore(column_name="col", score=5, score_label="EXCELLENT",
                    reasoning="ok", confidence=0.9, expected="e", actual="a")},
                latency_ms=1000, execution_accuracy=True,
            ),
            InstanceResult(
                row_index=2, input_text="Q2",
                agent_answer="A2", agent_sql="S2",
                scores={"col": ColumnScore(column_name="col", score=3, score_label="ACCEPTABLE",
                    reasoning="ok", confidence=0.7, expected="e", actual="a")},
                latency_ms=1000, execution_accuracy=False,
            ),
            InstanceResult(
                row_index=3, input_text="Q3",
                agent_answer="A3", agent_sql="S3",
                scores={"col": ColumnScore(column_name="col", score=5, score_label="EXCELLENT",
                    reasoning="ok", confidence=0.9, expected="e", actual="a")},
                latency_ms=1000, execution_accuracy=None,
            ),
        ]
        run = EvaluationRun(
            run_id="test-run",
            dataset_name="Test",
            input_column="question",
            eval_columns=["col"],
        )
        run.instances = instances
        stats = engine._compute_statistics(run)
        assert stats["execution_accuracy"] == 0.5
        assert stats["execution_accuracy_total"] == 2
        assert stats["execution_accuracy_matches"] == 1

    def test_statistics_all_skipped_returns_none(self, engine):
        from evaluation.evaluator import InstanceResult, ColumnScore
        instances = [
            InstanceResult(
                row_index=1, input_text="Q1",
                agent_answer="A1", agent_sql="S1",
                scores={"col": ColumnScore(column_name="col", score=5, score_label="EXCELLENT",
                    reasoning="ok", confidence=0.9, expected="e", actual="a")},
                latency_ms=1000, execution_accuracy=None,
            ),
        ]
        run = EvaluationRun(
            run_id="test-run",
            dataset_name="Test",
            input_column="question",
            eval_columns=["col"],
        )
        run.instances = instances
        stats = engine._compute_statistics(run)
        assert stats["execution_accuracy"] is None
