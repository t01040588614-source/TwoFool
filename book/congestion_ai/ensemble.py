from __future__ import annotations

import numpy as np

from congestion_ai.constants import FORECAST_HORIZONS_MIN, PRIMARY_HORIZON_MIN


def compute_ensemble_weights(
    ml_mae: float,
    dl_mae: float,
    *,
    dl_r2: float | None = None,
) -> dict[str, float]:
    ml_mae = max(float(ml_mae), 1e-6)
    dl_mae = max(float(dl_mae), 1e-6)
    if dl_r2 is not None and float(dl_r2) < 0:
        dl_mae = dl_mae * 3.0
    ml_weight = 1.0 / ml_mae
    dl_weight = 1.0 / dl_mae
    total = ml_weight + dl_weight
    return {
        "ml_weight": round(ml_weight / total, 4),
        "dl_weight": round(dl_weight / total, 4),
        "method": "validation_mae_inverse_weighted_average",
    }


def blend_predictions(
    ml_pred: np.ndarray,
    dl_pred: np.ndarray,
    *,
    ml_weight: float,
    dl_weight: float,
) -> np.ndarray:
    total = ml_weight + dl_weight
    return np.clip((ml_pred * ml_weight + dl_pred * dl_weight) / total, 0.05, 0.98)


def ratio_to_label(ratio: float) -> str:
    if ratio >= 0.72:
        return "high"
    if ratio >= 0.42:
        return "medium"
    return "low"


def primary_index() -> int:
    try:
        return FORECAST_HORIZONS_MIN.index(PRIMARY_HORIZON_MIN)
    except ValueError:
        return 1
