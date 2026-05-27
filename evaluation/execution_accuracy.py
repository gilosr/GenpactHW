from __future__ import annotations


def _normalize_value(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _normalize_row(row: dict) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, _normalize_value(v)) for k, v in row.items()))


def compare_result_sets(expected: list[dict], actual: list[dict]) -> bool:
    if len(expected) != len(actual):
        return False
    norm_expected = sorted(_normalize_row(r) for r in expected)
    norm_actual = sorted(_normalize_row(r) for r in actual)
    return norm_expected == norm_actual
