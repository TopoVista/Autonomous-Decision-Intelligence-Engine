"""Deep profiling stage — pure Python, no pandas/numpy.

Fast profile (app.data.profiler) always runs at ingestion.
Deep profile (correlations, outliers, duplicates, candidate keys) on demand.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from app.data.descriptor import DatasetDescriptor


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _numeric_series(col: str, rows: list[dict]) -> list[float]:
    return [f for r in rows if (f := _coerce_float(r.get(col))) is not None]


def _paired_numeric_series(a: str, b: str, rows: list[dict]) -> tuple[list[float], list[float]]:
    pairs = [(_coerce_float(row.get(a)), _coerce_float(row.get(b))) for row in rows]
    valid = [(left, right) for left, right in pairs if left is not None and right is not None]
    return [left for left, _ in valid], [right for _, right in valid]


def deep_profile(columns: list[str], rows: list[dict], descriptor: DatasetDescriptor) -> DatasetDescriptor:
    # ── Correlations (Pearson, O(n*c^2)) ────────────────────────────────────
    numeric_cols = [c for c in columns if _numeric_series(c, rows)]
    if len(numeric_cols) >= 2:
        pairs: list[dict] = []
        for i, a in enumerate(numeric_cols):
            for b in numeric_cols[i + 1:]:
                left, right = _paired_numeric_series(a, b, rows)
                r = _pearson(left, right)
                if r is not None and abs(r) > 0.5:
                    pairs.append({"a": a, "b": b, "correlation": round(r, 3)})
        descriptor.statistics["correlations"] = sorted(pairs, key=lambda p: -abs(p["correlation"]))[:20]

    # ── Outliers (IQR) ───────────────────────────────────────────────────────
    outliers: dict[str, int] = {}
    for col in numeric_cols:
        series = _numeric_series(col, rows)
        if len(series) < 8:
            continue
        q1, q3 = _quantile(series, 0.25), _quantile(series, 0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        count = sum(1 for v in series if v < lo or v > hi)
        if count:
            outliers[col] = count
    if outliers:
        descriptor.statistics["outliers"] = outliers

    # ── Duplicate rows ───────────────────────────────────────────────────────
    row_keys = [tuple(str(r.get(c, "")) for c in columns) for r in rows]
    duplicates = len(row_keys) - len(set(row_keys))
    if duplicates:
        descriptor.statistics["duplicate_rows"] = duplicates

    # ── Candidate primary key ────────────────────────────────────────────────
    for col in descriptor.columns:
        if col.unique_count == descriptor.row_count and col.missing_count == 0:
            descriptor.relationships.append({"kind": "candidate_primary_key", "column": col.name})
            break

    descriptor.deep_profiled = True
    _quality_checks(columns, rows, descriptor)
    return descriptor


# ── Shared helpers ────────────────────────────────────────────────────────────

def _quantile(data: list[float], q: float) -> float:
    s = sorted(data)
    n = len(s)
    idx = q * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = min(len(x), len(y))
    if n < 3:
        return None
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    dy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _assign_column_groups(descriptor: DatasetDescriptor) -> None:
    for col in descriptor.columns:
        st = col.semantic_type
        if st == "temporal":
            descriptor.time_columns.append(col.name)
        elif st in ("geo_lat", "geo_lon"):
            descriptor.geo_columns.append(col.name)
        elif st == "text":
            descriptor.text_columns.append(col.name)
        elif st == "identifier":
            descriptor.identifier_columns.append(col.name)
        elif st == "measure":
            descriptor.measure_columns.append(col.name)
        elif st == "categorical":
            descriptor.categorical_columns.append(col.name)


def _basic_statistics(columns: list[str], rows: list[dict], descriptor: DatasetDescriptor) -> None:
    stats: dict[str, dict[str, float]] = {}
    for col in columns:
        series = _numeric_series(col, rows)
        if not series:
            continue
        n = len(series)
        mean = sum(series) / n
        std = math.sqrt(sum((v - mean) ** 2 for v in series) / max(n - 1, 1)) if n > 1 else 0.0
        stats[col] = {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": min(series),
            "max": max(series),
            "median": round(_quantile(series, 0.5), 4),
        }
    if stats:
        descriptor.statistics["numeric_summary"] = stats


def _quality_checks(columns: list[str], rows: list[dict], descriptor: DatasetDescriptor) -> None:
    report: dict[str, Any] = {}
    high_missing = [
        {"column": c.name, "missing_pct": c.missing_pct}
        for c in descriptor.columns
        if c.missing_pct > 40
    ]
    if high_missing:
        report["high_missingness"] = high_missing
    constant = [c.name for c in descriptor.columns if c.unique_count <= 1 and descriptor.row_count > 1]
    if constant:
        report["constant_columns"] = constant
    total_cells = descriptor.row_count * descriptor.column_count
    total_missing = sum(c.missing_count for c in descriptor.columns)
    report["total_missing"] = total_missing
    report["overall_completeness_pct"] = (
        round((1 - total_missing / total_cells) * 100, 2) if total_cells else 100.0
    )
    descriptor.quality_report = report


def _infer_dataset_type(descriptor: DatasetDescriptor) -> None:
    if descriptor.time_columns and descriptor.measure_columns:
        descriptor.type = "time_series"
    elif descriptor.text_columns and not descriptor.measure_columns:
        descriptor.type = "text"
    elif descriptor.geo_columns:
        descriptor.type = "geospatial"
    else:
        descriptor.type = "tabular"
