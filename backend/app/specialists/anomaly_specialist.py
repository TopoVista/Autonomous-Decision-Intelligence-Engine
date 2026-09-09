"""Anomaly/Fraud Specialist for advanced anomaly detection."""

from __future__ import annotations

import math
from typing import Any

from app.core.registry import skill


def _mean(values: list[float]) -> float:
    return sum(values) / max(len(values), 1)


def _std(values: list[float]) -> float:
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / max(len(values) - 1, 1)
    return math.sqrt(variance)


def _extract_column(data: list[dict], column: str) -> list[float]:
    return [float(row.get(column, 0)) for row in data if column in row]


def _zscore_anomaly(values: list[float], threshold: float = 2.0) -> list[dict[str, Any]]:
    """Detect anomalies using z-score method."""
    if len(values) < 3:
        return []
    m = _mean(values)
    s = _std(values)
    if s == 0:
        return []

    anomalies: list[dict[str, Any]] = []
    for i, v in enumerate(values):
        z = abs((v - m) / s)
        if z > threshold:
            anomalies.append({
                "index": i,
                "value": v,
                "z_score": round(z, 3),
                "severity": "high" if z > 3 else "moderate",
            })
    return anomalies


def _iqr_anomaly(values: list[float]) -> list[dict[str, Any]]:
    """Detect anomalies using Interquartile Range method."""
    if len(values) < 4:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q3 = sorted_vals[3 * n // 4]
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    anomalies: list[dict[str, Any]] = []
    for i, v in enumerate(values):
        if v < lower_bound or v > upper_bound:
            anomalies.append({
                "index": i,
                "value": v,
                "bound": "lower" if v < lower_bound else "upper",
                "iqr_multiple": round(min(abs(v - lower_bound), abs(v - upper_bound)) / max(iqr, 0.001), 2),
            })
    return anomalies


def _isolation_forest_simple(values: list[list[float]], contamination: float = 0.1) -> list[int]:
    """Simplified isolation forest using random partitioning heuristic."""
    n = len(values)
    if n < 2:
        return [0] * n

    labels = [0] * n
    for dim in range(len(values[0])):
        dim_vals = [v[dim] for v in values]
        m = _mean(dim_vals)
        s = _std(dim_vals)
        if s == 0:
            continue
        for i, v in enumerate(dim_vals):
            if abs((v - m) / s) > 2.5:
                labels[i] = -1

    means = [_mean([row[j] for row in values]) for j in range(len(values[0]))]
    stds = [_std([row[j] for row in values]) for j in range(len(values[0]))]
    max_deviations = []
    for i, row in enumerate(values):
        total_dev = 0
        for j, v in enumerate(row):
            total_dev += abs((v - means[j]) / max(stds[j], 0.001))
        max_deviations.append((i, total_dev))

    max_deviations.sort(key=lambda x: x[1], reverse=True)
    n_anomalies = max(1, int(n * contamination))
    for idx, _ in max_deviations[:n_anomalies]:
        labels[idx] = -1

    return labels


class AnomalySpecialist:
    """Anomaly/Fraud Specialist for advanced anomaly detection."""

    name: str = "anomaly_specialist"
    description: str = "Advanced anomaly detection: z-score, IQR, isolation forest"

    @skill("detect_anomalies")
    async def detect_anomalies(self, data: list[dict], columns: list[str]) -> dict[str, Any]:
        """Detect anomalies in row-based data using z-score method."""
        if not data:
            return {"anomalies": []}

        all_anomalies: list[dict[str, Any]] = []
        for column in columns:
            values = _extract_column(data, column)
            col_anomalies = _zscore_anomaly(values, threshold=2.0)
            for a in col_anomalies:
                a["column"] = column
            all_anomalies.extend(col_anomalies)

        return {"anomalies": all_anomalies}

    @skill("isolation_forest_detect")
    async def isolation_forest_detect(self, data: list[dict], columns: list[str]) -> dict[str, Any]:
        """Detect anomalies using simplified isolation forest."""
        if not data:
            return {"anomaly_labels": []}

        values: list[list[float]] = []
        for row in data:
            row_vals = [float(row.get(col, 0)) for col in columns]
            values.append(row_vals)

        labels = _isolation_forest_simple(values)
        return {"anomaly_labels": labels}

    @skill("zscore_detect")
    async def zscore_detect(self, data: list[dict], column: str, threshold: float = 2.0) -> dict[str, Any]:
        """Detect outliers using z-score method."""
        values = _extract_column(data, column)
        anomalies = _zscore_anomaly(values, threshold)
        return {"outlier_indices": [a["index"] for a in anomalies]}

    @skill("iqr_detect")
    async def iqr_detect(self, data: list[dict], column: str) -> dict[str, Any]:
        """Detect outliers using IQR method."""
        values = _extract_column(data, column)
        anomalies = _iqr_anomaly(values)
        return {"outlier_indices": [a["index"] for a in anomalies]}


def register() -> AnomalySpecialist:
    return AnomalySpecialist()
