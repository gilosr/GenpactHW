"""
tests/test_eval_routes.py
──────────────────────────
API integration tests for api/eval_routes.py using FastAPI TestClient.

Strategy:
  - Upload tests use a real EvaluationEngine backed by tmp_path.
  - All other tests patch api.eval_routes._get_engine with a mock so no
    background threads or LLM calls are made.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from evaluation.evaluator import EvaluationEngine, EvaluationRun


# ── Client fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(app)


# ── CSV helpers ───────────────────────────────────────────────────────────────


def _csv_bytes(content: str) -> bytes:
    return content.encode("utf-8")


_VALID_CSV = _csv_bytes("question,expected_answer\nHow many students?,20\nName a teacher.,Alice\n")


# ── Stub EvaluationRun builders ───────────────────────────────────────────────


def _running_run(run_id: str = "run-1") -> EvaluationRun:
    return EvaluationRun(
        run_id=run_id,
        dataset_name="Test",
        input_column="question",
        eval_columns=["expected_answer"],
        status="running",
        created_at=datetime.now(timezone.utc).isoformat(),
        progress_current=1,
        progress_total=2,
    )


def _completed_run(run_id: str = "run-1") -> EvaluationRun:
    run = EvaluationRun(
        run_id=run_id,
        dataset_name="Test",
        input_column="question",
        eval_columns=["expected_answer"],
        status="completed",
        created_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        progress_current=2,
        progress_total=2,
        statistics={
            "overall_avg_score": 4.0,
            "overall_pass_rate": 1.0,
            "total_instances": 2,
            "total_latency_s": 5.0,
            "score_distribution": {"EXCELLENT": 2, "GOOD": 0, "ACCEPTABLE": 0, "POOR": 0, "FAIL": 0},
            "per_column_avg": {"expected_answer": 4.0},
            "per_column_pass_rate": {"expected_answer": 1.0},
        },
    )
    return run


def _mock_engine(run: EvaluationRun | None = None, session_id: str = "sess-1") -> MagicMock:
    """Build a MagicMock that satisfies the EvaluationEngine interface."""
    eng = MagicMock(spec=EvaluationEngine)
    eng.parse_csv.return_value = {
        "session_id": session_id,
        "columns": ["question", "expected_answer"],
        "preview": [{"question": "Q1", "expected_answer": "A1"}],
        "row_count": 1,
        "filename": "test.csv",
    }
    eng.get_run.return_value = run
    eng.start_evaluation.return_value = run.run_id if run else "run-1"
    eng.list_runs.return_value = []
    if run:
        eng._run_to_dict.return_value = {
            "run_id": run.run_id,
            "dataset_name": run.dataset_name,
            "status": run.status,
            "instances": [],
            "statistics": run.statistics,
            "eval_columns": run.eval_columns,
        }
    return eng


# ── POST /api/eval/upload ─────────────────────────────────────────────────────


class TestUploadEndpoint:
    def test_valid_csv_returns_200_with_metadata(self, client, tmp_path):
        with patch("api.eval_routes._engine", None):
            with patch("evaluation.evaluator.EvaluationEngine.__init__", lambda self, results_dir=None: None):
                # Use real endpoint with patched engine via fixture approach
                pass

        # Simpler: patch _get_engine to return a real engine with tmp_path
        real_engine = EvaluationEngine(results_dir=str(tmp_path))
        with patch("api.eval_routes._get_engine", return_value=real_engine):
            resp = client.post(
                "/api/eval/upload",
                files={"file": ("test.csv", _VALID_CSV, "text/csv")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["columns"] == ["question", "expected_answer"]
        assert data["row_count"] == 2

    def test_non_csv_file_returns_400(self, client):
        resp = client.post(
            "/api/eval/upload",
            files={"file": ("data.json", b'{"a":1}', "application/json")},
        )
        assert resp.status_code == 400
        assert "CSV" in resp.json()["detail"]

    def test_empty_csv_returns_400(self, client, tmp_path):
        real_engine = EvaluationEngine(results_dir=str(tmp_path))
        with patch("api.eval_routes._get_engine", return_value=real_engine):
            resp = client.post(
                "/api/eval/upload",
                files={"file": ("empty.csv", b"", "text/csv")},
            )
        assert resp.status_code == 400

    def test_single_column_csv_returns_400(self, client, tmp_path):
        real_engine = EvaluationEngine(results_dir=str(tmp_path))
        with patch("api.eval_routes._get_engine", return_value=real_engine):
            resp = client.post(
                "/api/eval/upload",
                files={"file": ("one_col.csv", _csv_bytes("question\nOnly one column\n"), "text/csv")},
            )
        assert resp.status_code == 400
        assert "2 columns" in resp.json()["detail"]


# ── POST /api/eval/run ────────────────────────────────────────────────────────


class TestRunEndpoint:
    def test_valid_request_returns_200_with_run_id(self, client):
        run = _running_run()
        eng = _mock_engine(run)
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.post(
                "/api/eval/run",
                json={
                    "session_id": "sess-1",
                    "input_column": "question",
                    "eval_columns": ["expected_answer"],
                    "dataset_name": "Test Run",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-1"
        assert data["status"] == "running"

    def test_unknown_session_id_returns_400(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.start_evaluation.side_effect = ValueError("CSV session 'x' not found.")
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.post(
                "/api/eval/run",
                json={
                    "session_id": "x",
                    "input_column": "question",
                    "eval_columns": ["answer"],
                },
            )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"]

    def test_invalid_input_column_returns_400(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.start_evaluation.side_effect = ValueError("Input column 'missing' not found.")
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.post(
                "/api/eval/run",
                json={
                    "session_id": "s",
                    "input_column": "missing",
                    "eval_columns": ["answer"],
                },
            )
        assert resp.status_code == 400


# ── GET /api/eval/status/{run_id} ─────────────────────────────────────────────


class TestStatusEndpoint:
    def test_unknown_run_id_returns_404(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.get_run.return_value = None
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/status/does-not-exist")
        assert resp.status_code == 404

    def test_running_run_returns_progress(self, client):
        run = _running_run()
        eng = _mock_engine(run)
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/status/run-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"]["current"] == 1
        assert data["progress"]["total"] == 2
        assert data["progress"]["percent"] == pytest.approx(50.0)

    def test_completed_run_returns_100_percent(self, client):
        run = _completed_run()
        eng = _mock_engine(run)
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/status/run-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert resp.json()["progress"]["percent"] == pytest.approx(100.0)


# ── GET /api/eval/results/{run_id} ────────────────────────────────────────────


class TestResultsEndpoint:
    def test_unknown_run_returns_404(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.get_run.return_value = None
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/results/x")
        assert resp.status_code == 404

    def test_still_running_returns_409(self, client):
        run = _running_run()
        eng = _mock_engine(run)
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/results/run-1")
        assert resp.status_code == 409
        assert "still running" in resp.json()["detail"]

    def test_completed_run_returns_results(self, client):
        run = _completed_run()
        eng = _mock_engine(run)
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/results/run-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-1"
        assert data["status"] == "completed"
        assert "statistics" in data


# ── GET /api/eval/runs ────────────────────────────────────────────────────────


class TestListRunsEndpoint:
    def test_empty_returns_empty_list(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.list_runs.return_value = []
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/runs")
        assert resp.status_code == 200
        assert resp.json() == {"runs": []}

    def test_returns_run_summaries(self, client):
        eng = MagicMock(spec=EvaluationEngine)
        eng.list_runs.return_value = [
            {
                "run_id": "a",
                "dataset_name": "Run A",
                "status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_instances": 5,
                "overall_avg_score": 4.2,
                "overall_pass_rate": 0.8,
            },
            {
                "run_id": "b",
                "dataset_name": "Run B",
                "status": "completed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_instances": 3,
                "overall_avg_score": 3.5,
                "overall_pass_rate": 0.67,
            },
        ]
        with patch("api.eval_routes._get_engine", return_value=eng):
            resp = client.get("/api/eval/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 2
        assert runs[0]["run_id"] == "a"
        assert runs[1]["dataset_name"] == "Run B"
