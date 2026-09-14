"""ML/LSTM unavailable or training failed — rule-based congestion fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from congestion_ai.constants import FORECAST_HORIZONS_MIN, PRIMARY_HORIZON_MIN
from congestion_ai.ensemble import ratio_to_label
from congestion_ai.synthetic_data import label_to_pct


def estimate_current_ratio(
    snapshot: dict | None,
    passengers: int,
    hour: int,
    is_event_day: bool,
) -> float:
    snapshot = snapshot or {}
    label_ratio = {
        "low": 0.25,
        "medium": 0.58,
        "high": 0.86,
    }.get(str(snapshot.get("congestion", "")).lower())
    if label_ratio is not None:
        base = label_ratio
    else:
        base = min(max(passengers / 2200.0, 0.15), 0.9)
    if hour in {7, 8, 9, 18, 19, 20}:
        base += 0.06
    if is_event_day:
        base += 0.05
    return float(np.clip(base, 0.05, 0.98))


def build_heuristic_comparison(
    *,
    hour: int,
    route_name: str,
    recent_passengers: int,
    is_event_day: bool = False,
) -> dict:
    ratio = estimate_current_ratio({}, recent_passengers, hour, is_event_day)
    label = ratio_to_label(ratio)
    pct = label_to_pct(ratio)
    base = {
        "label": label,
        "confidence": round(max(ratio, 0.05), 3),
        "congestion_pct": pct,
        "horizon_min": PRIMARY_HORIZON_MIN,
        "data_status": "규칙 기반 fallback",
        "features": {
            "hour": hour,
            "route_name": route_name,
            "recent_passengers": recent_passengers,
            "is_event_day": bool(is_event_day),
        },
    }
    return {
        "ml": {**base, "model_type": "ml", "model_name": "RuleBased", "library": "heuristic"},
        "dl": {**base, "model_type": "dl", "model_name": "RuleBased", "library": "heuristic"},
        "ensemble": {
            "label": label,
            "risk_score": round(ratio, 3),
            "congestion_pct": pct,
            "disagreement": 0.0,
            "guidance": "ML warm-up 전 · 규칙 기반 추정",
            "method": "rule_based_fallback",
            "ml_weight": 1.0,
            "dl_weight": 0.0,
        },
        "data_status": base["data_status"],
    }


def build_heuristic_forecast(
    *,
    hour: int,
    route_name: str,
    recent_passengers: int,
    is_event_day: bool = False,
    train_id: int | None = None,
) -> dict:
    ratio = estimate_current_ratio({}, recent_passengers, hour, is_event_day)
    label = ratio_to_label(ratio)
    pct = label_to_pct(ratio)
    now = datetime.now(UTC)
    forecasts = {}
    for horizon in FORECAST_HORIZONS_MIN:
        drift = min(0.08, horizon / 100.0)
        adj_ratio = float(np.clip(ratio + drift, 0.05, 0.98))
        forecasts[str(horizon)] = {
            "minutes_ahead": horizon,
            "ml_pct": label_to_pct(adj_ratio),
            "dl_pct": label_to_pct(adj_ratio),
            "ensemble_pct": label_to_pct(adj_ratio),
            "ml_label": ratio_to_label(adj_ratio),
            "dl_label": ratio_to_label(adj_ratio),
            "ensemble_label": ratio_to_label(adj_ratio),
        }
    primary = forecasts[str(PRIMARY_HORIZON_MIN)]
    return {
        "train_id": train_id,
        "reference_time": now.isoformat(),
        "route_name": route_name,
        "current_congestion_pct": pct,
        "current_congestion_label": label,
        "operation_status": "normal",
        "speed_kmh": 0,
        "forecasts": forecasts,
        "primary_horizon_min": PRIMARY_HORIZON_MIN,
        "primary_forecast_pct": primary["ensemble_pct"],
        "primary_forecast_label": primary["ensemble_label"],
        "models": {
            "ml": {
                "model": "RuleBased",
                "library": "heuristic",
                "metrics_test": {"mae": None, "r2": None},
            },
            "dl": {
                "model": "RuleBased (disabled)",
                "framework": "heuristic",
                "metrics_test": {"mae": None, "r2": None},
            },
        },
        "ensemble": {
            "ml_weight": 1.0,
            "dl_weight": 0.0,
            "method": "rule_based_fallback",
            "primary_pct": primary["ensemble_pct"],
            "primary_label": primary["ensemble_label"],
        },
        "chart_series": {
            "history": [
                {
                    "kind": "actual",
                    "minutes_offset": 0,
                    "timestamp": now.isoformat(),
                    "congestion_pct": pct,
                }
            ],
            "forecast": [
                {
                    "kind": "forecast",
                    "minutes_offset": horizon,
                    "timestamp": now.isoformat(),
                    "ml_pct": forecasts[str(horizon)]["ml_pct"],
                    "dl_pct": forecasts[str(horizon)]["dl_pct"],
                    "ensemble_pct": forecasts[str(horizon)]["ensemble_pct"],
                }
                for horizon in FORECAST_HORIZONS_MIN
            ],
        },
        "data_source": "rule_based_fallback",
        "data_status": "규칙 기반 fallback",
        "model_version": None,
        "prediction_status": "fallback",
        "reliability_note": "ML 모델 미준비 · 규칙 기반 추정값",
    }
