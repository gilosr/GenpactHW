from __future__ import annotations
from typing import Any


def _normalize_value(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _normalize_row(row: dict) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, _normalize_value(v)) for k, v in row.items()))


def compare_result_sets(expected: list[dict], actual: list[dict]) -> dict[str, Any]:
    """Compare two result sets and return a detailed diff."""
    if not expected and not actual:
        return {"is_match": True, "message": "Both empty"}
    
    if not expected and actual:
        return {"is_match": False, "message": "Expected empty, but got results", "actual_count": len(actual)}
    
    if expected and not actual:
        return {"is_match": False, "message": "Expected results, but got empty", "expected_count": len(expected)}

    # Check column names
    exp_cols = set(expected[0].keys())
    act_cols = set(actual[0].keys())
    if exp_cols != act_cols:
        added = act_cols - exp_cols
        removed = exp_cols - act_cols
        msg = "Column mismatch"
        if added: msg += f" (Added: {', '.join(added)})"
        if removed: msg += f" (Missing: {', '.join(removed)})"
        return {
            "is_match": False, 
            "message": msg,
            "expected_cols": list(exp_cols),
            "actual_cols": list(act_cols)
        }

    if len(expected) != len(actual):
        return {
            "is_match": False, 
            "message": f"Row count mismatch: expected {len(expected)}, got {len(actual)}",
            "expected_count": len(expected),
            "actual_count": len(actual)
        }

    norm_expected = [_normalize_row(r) for r in expected]
    norm_actual = [_normalize_row(r) for r in actual]
    
    # Check for rows in expected but not in actual
    missing_in_actual = []
    actual_pool = list(norm_actual)
    for i, exp_norm in enumerate(norm_expected):
        if exp_norm in actual_pool:
            actual_pool.remove(exp_norm)
        else:
            missing_in_actual.append(expected[i])
            if len(missing_in_actual) >= 3: break # Limit diff size

    # Check for rows in actual but not in expected
    extra_in_actual = []
    expected_pool = list(norm_expected)
    for i, act_norm in enumerate(norm_actual):
        if act_norm in expected_pool:
            expected_pool.remove(act_norm)
        else:
            extra_in_actual.append(actual[i])
            if len(extra_in_actual) >= 3: break # Limit diff size

    if missing_in_actual or extra_in_actual:
        return {
            "is_match": False,
            "message": "Data content mismatch",
            "missing_rows": missing_in_actual,
            "extra_rows": extra_in_actual
        }

    return {"is_match": True, "message": "Exact match"}
