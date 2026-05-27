"""
api/eval_routes.py
──────────────────
FastAPI router for the LLM-as-a-Judge evaluation pipeline.

Endpoints:
  POST /api/eval/upload       — upload CSV, return parsed columns + preview
  POST /api/eval/run          — start an evaluation run
  GET  /api/eval/status/{id}  — poll run progress
  GET  /api/eval/results/{id} — full results with instances + statistics
  GET  /api/eval/runs         — list all past runs
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from evaluation.evaluator import EvaluationEngine

router = APIRouter(prefix="/api/eval", tags=["evaluation"])

# Singleton engine — persists across requests
_engine: EvaluationEngine | None = None


def _get_engine() -> EvaluationEngine:
    global _engine
    if _engine is None:
        _engine = EvaluationEngine()
    return _engine


# ── Request / Response schemas ─────────────────────────────────────────────────


class RunRequest(BaseModel):
    session_id: str = Field(..., description="CSV session ID from /upload")
    input_column: str = Field(..., description="Column name containing the input/question")
    eval_columns: list[str] = Field(..., description="Column names to evaluate against")
    dataset_name: str = Field(default="Untitled", description="Human-readable name for the run")
    selected_indices: list[int] | None = Field(default=None, description="Optional list of row indices to evaluate")


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/upload")
async def upload_csv(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a CSV file for evaluation.

    Returns parsed column names, a preview of the first 5 rows,
    and a session_id to reference in /run.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        result = _get_engine().parse_csv(content, filename=file.filename)
        return result
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CSV parsing failed: {exc}")


@router.post("/run")
async def start_run(request: RunRequest) -> dict[str, Any]:
    """Start an evaluation run.

    The evaluation runs in a background thread. Poll /status/{run_id}
    for progress updates.
    """
    engine = _get_engine()
    try:
        run_id = engine.start_evaluation(
            session_id=request.session_id,
            input_column=request.input_column,
            eval_columns=request.eval_columns,
            dataset_name=request.dataset_name,
            selected_indices=request.selected_indices,
        )
        return {"run_id": run_id, "status": "running"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start evaluation: {exc}")


@router.get("/status/{run_id}")
async def run_status(run_id: str) -> dict[str, Any]:
    """Poll the progress of a running evaluation."""
    run = _get_engine().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    percent = (
        round((run.progress_current / run.progress_total) * 100, 1)
        if run.progress_total > 0
        else 0
    )

    return {
        "run_id": run.run_id,
        "status": run.status,
        "progress": {
            "current": run.progress_current,
            "total": run.progress_total,
            "percent": percent,
        },
        "current_instance": run.current_instance,
        "error": run.error,
    }


@router.get("/results/{run_id}")
async def run_results(run_id: str) -> dict[str, Any]:
    """Fetch complete results for a finished evaluation run."""
    engine = _get_engine()
    run = engine.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    if run.status == "running":
        raise HTTPException(status_code=409, detail="Evaluation is still running. Poll /status first.")

    return engine._run_to_dict(run)


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    """List all evaluation runs with summary statistics."""
    return {"runs": _get_engine().list_runs()}
