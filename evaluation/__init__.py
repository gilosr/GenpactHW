"""
evaluation/
────────────
LLM-as-a-Judge evaluation pipeline for the university QA agent.

Modules:
  evaluator  — core engine: CSV parsing, agent execution, LLM judge, aggregation
  store      — serialisation helpers for evaluation results
"""

from evaluation.evaluator import EvaluationEngine, EvaluationRun, InstanceResult, ColumnScore

__all__ = ["EvaluationEngine", "EvaluationRun", "InstanceResult", "ColumnScore"]
