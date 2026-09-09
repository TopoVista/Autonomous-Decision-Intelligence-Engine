"""Causal Analysis Specialist for causal inference tasks."""

from __future__ import annotations

import math
from typing import Any

from app.core.registry import skill


def _correlation(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    xn, yn = x[:n], y[:n]
    mean_x = sum(xn) / n
    mean_y = sum(yn) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xn, yn))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in xn))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in yn))
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def _extract_column(data: list[dict], column: str) -> list[float]:
    return [float(row.get(column, 0)) for row in data if column in row]


class CausalSpecialist:
    name: str = "causal_specialist"
    description: str = "Causal inference: DAGs, confounder detection, effect estimation"

    @skill("detect_confounders")
    async def detect_confounders(self, variables: list[str], treatment: str, outcome: str) -> list[dict[str, Any]]:
        if not variables:
            return []
        return []

    @skill("generate_dag")
    async def generate_dag(self, variables: list[str], treatment: str, outcome: str) -> dict[str, Any]:
        nodes = [{"id": v, "role": "treatment" if v == treatment else "outcome" if v == outcome else "other"} for v in variables]
        edges = []
        if treatment in variables and outcome in variables:
            edges.append({"from": treatment, "to": outcome})
        return {"nodes": nodes, "edges": edges}

    @skill("estimate_causal_effect")
    async def estimate_causal_effect(self, data: list[dict], treatment: str, outcome: str, confounders: list[str]) -> dict[str, Any]:
        pairs = []
        for row in data:
            try:
                pairs.append((float(row[treatment]), float(row[outcome])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(pairs) < 2:
            return {"effect_estimate": 0.0, "method": "none", "reason": "insufficient_data"}
        treat_vals = [item[0] for item in pairs]
        outcome_vals = [item[1] for item in pairs]
        treated = [o for t, o in zip(treat_vals, outcome_vals) if t > 0]
        untreated = [o for t, o in zip(treat_vals, outcome_vals) if t == 0]
        if treated and untreated:
            effect = sum(treated) / len(treated) - sum(untreated) / len(untreated)
        else:
            effect = _correlation(treat_vals, outcome_vals)
        return {
            "effect_estimate": round(effect, 4),
            "method": "unadjusted_difference_in_means" if (treated and untreated) else "correlation",
            "treatment": treatment,
            "outcome": outcome,
            "confounders_requested": confounders,
            "confounders_adjusted": [],
            "limitations": ["This lightweight estimator does not adjust for confounders and cannot establish causality."],
            "n_samples": len(pairs),
        }


def register() -> CausalSpecialist:
    return CausalSpecialist()
