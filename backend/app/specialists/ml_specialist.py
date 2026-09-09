"""ML Specialist for model training and evaluation."""

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


def _linear_regression_fit(x: list[float], y: list[float]) -> dict[str, Any]:
    """Simple linear regression using least squares."""
    n = len(x)
    if n < 2:
        return {"error": "need at least 2 samples"}

    mean_x = _mean(x)
    mean_y = _mean(y)
    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den = sum((xi - mean_x) ** 2 for xi in x)
    if den == 0:
        return {"error": "zero variance in feature"}

    slope = num / den
    intercept = mean_y - slope * mean_x
    predictions = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y, predictions))
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "slope": round(slope, 4),
        "intercept": round(intercept, 4),
        "r_squared": round(max(r_squared, 0.0), 4),
        "method": "linear_regression",
    }


class MLSpecialist:
    """ML Specialist for model training and evaluation."""

    name: str = "ml_specialist"
    description: str = "Train/evaluate models: regression, classification, prediction"

    def __init__(self) -> None:
        self._model: dict[str, Any] | None = None

    @skill("train_model")
    async def train_model(
        self,
        data: list[dict],
        target: str,
        features: list[str],
        task: str = "regression",
    ) -> dict[str, Any]:
        """Train a model on row-based data.

        Args:
            data: List of dicts (rows).
            target: Name of the target column.
            features: Names of feature columns.
            task: 'regression' or 'classification'.
        """
        if not data or len(data) < 2:
            return {"error": "insufficient data", "model_type": task}

        y = _extract_column(data, target)
        if not y or len(y) < 2:
            return {"error": "target column not found or insufficient", "model_type": task}

        if len(features) == 1:
            x = _extract_column(data, features[0])
            result = _linear_regression_fit(x, y)
            result["model_type"] = task
            result["target"] = target
            result["features"] = features
            if "error" not in result:
                self._model = {
                    "slope": result.get("slope"),
                    "intercept": result.get("intercept"),
                    "target": target,
                    "features": features,
                    "task": task,
                }
                # Feature importance for single feature
                result["feature_importance"] = {features[0]: round(abs(result.get("slope", 0)), 4)}
            return result
        else:
            # Multi-feature: report per-feature correlation as importance
            importance = {}
            for feat in features:
                feat_vals = _extract_column(data, feat)
                if feat_vals and y:
                    n = min(len(feat_vals), len(y))
                    mx = _mean(feat_vals[:n])
                    my = _mean(y[:n])
                    num = sum((feat_vals[i] - mx) * (y[i] - my) for i in range(n))
                    dx = math.sqrt(sum((v - mx) ** 2 for v in feat_vals[:n]))
                    dy = math.sqrt(sum((v - my) ** 2 for v in y[:n]))
                    corr = num / (dx * dy) if dx > 0 and dy > 0 else 0.0
                    importance[feat] = round(abs(corr), 4)
            return {
                "error": "multi_feature_regression_not_supported",
                "message": "This lightweight specialist can train only one-feature linear regression; no prediction model was created.",
                "model_type": task,
                "target": target,
                "features": features,
                "feature_association": importance,
                "n_samples": len(data),
            }

    @skill("predict")
    async def predict(self, data: list[dict]) -> list[dict]:
        """Make predictions using the trained model."""
        if self._model is None:
            return [{"error": "no model trained"}]

        predictions = []
        slope = self._model.get("slope")
        intercept = self._model.get("intercept")
        features = self._model.get("features", [])

        for row in data:
            if slope is not None and intercept is not None and features:
                x_val = float(row.get(features[0], 0))
                pred = slope * x_val + intercept
                predictions.append({"prediction": round(pred, 4)})
            else:
                predictions.append({"error": "trained model has no prediction coefficients"})

        return predictions


def register() -> MLSpecialist:
    return MLSpecialist()
