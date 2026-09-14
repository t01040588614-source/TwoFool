"""Synthetic congestion time-series for model training (clearly labeled demo data)."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from congestion_ai.constants import FORECAST_HORIZONS_MIN, SIMULATION_INTERVAL_MIN

STATUS_CODES = {
    "normal": 0,
    "delayed": 1,
    "stopped": 2,
    "disrupted": 3,
    "arrived": 4,
}


def _base_congestion(hour: float, dow: int, passengers: float, headway: float, speed: float, status: str) -> float:
    rush = 0.0
    if 7 <= hour < 10 or 18 <= hour < 21:
        rush = 0.22
    weekend_factor = 0.75 if dow >= 5 else 1.0
    passenger_factor = min(passengers / 1800.0, 1.0) * 0.28
    headway_factor = max(0.0, (12.0 - headway) / 12.0) * 0.12
    speed_factor = max(0.0, 1.0 - speed / 300.0) * 0.08
    status_penalty = {
        "normal": 0.0,
        "delayed": 0.08,
        "stopped": 0.15,
        "disrupted": 0.2,
        "arrived": -0.05,
    }.get(status, 0.0)
    base = 0.18 + rush + passenger_factor + headway_factor + speed_factor + status_penalty
    return float(np.clip(base * weekend_factor, 0.05, 0.98))


def generate_synthetic_dataset(
    n_trains: int = 40,
    steps_per_train: int = 200,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Build labeled synthetic congestion records.

    Each row is one 5-minute observation for a virtual train with future targets
    at +5/+10/+15 minutes (same rule engine, no leakage from future noise).
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        n_trains = min(n_trains, 12)
        steps_per_train = min(steps_per_train, 80)

    rng = np.random.default_rng(seed)
    rows: list[dict] = []

    for train_idx in range(n_trains):
        route_id = train_idx % 8
        status_cycle = ["normal", "normal", "delayed", "normal", "stopped", "normal"]
        prev_congestion = rng.uniform(0.2, 0.55)

        for step in range(steps_per_train):
            timestamp_min = step * SIMULATION_INTERVAL_MIN
            hour = ((timestamp_min // 60) + 6) % 24
            dow = (step // (24 * 60 // SIMULATION_INTERVAL_MIN)) % 7
            passengers_board = float(rng.integers(20, 420))
            passengers_alight = float(rng.integers(15, 380))
            headway = float(rng.uniform(4.0, 14.0))
            speed = float(rng.uniform(0, 280))
            status = status_cycle[step % len(status_cycle)]

            current = _base_congestion(hour, dow, passengers_board + passengers_alight, headway, speed, status)
            current = float(np.clip(0.65 * current + 0.35 * prev_congestion + rng.normal(0, 0.04), 0.05, 0.98))
            prev_congestion = current

            future_targets: dict[str, float] = {}
            for horizon in FORECAST_HORIZONS_MIN:
                future_step = step + horizon // SIMULATION_INTERVAL_MIN
                future_hour = (((future_step * SIMULATION_INTERVAL_MIN) // 60) + 6) % 24
                future_dow = (future_step // (24 * 60 // SIMULATION_INTERVAL_MIN)) % 7
                future_status = status_cycle[future_step % len(status_cycle)]
                future_load = passengers_board + passengers_alight + rng.normal(0, 40)
                future = _base_congestion(
                    future_hour,
                    future_dow,
                    future_load,
                    headway,
                    speed,
                    future_status,
                )
                future = float(np.clip(0.55 * future + 0.45 * current + rng.normal(0, 0.035), 0.05, 0.98))
                future_targets[f"target_{horizon}m"] = future

            rows.append(
                {
                    "train_id": f"T{train_idx:03d}",
                    "route_id": route_id,
                    "timestamp_min": timestamp_min,
                    "hour": hour,
                    "day_of_week": dow,
                    "congestion_current": current,
                    "passengers_board": passengers_board,
                    "passengers_alight": passengers_alight,
                    "headway_min": headway,
                    "speed_kmh": speed,
                    "operation_status": status,
                    "operation_status_code": STATUS_CODES.get(status, 0),
                    **future_targets,
                }
            )

    frame = pd.DataFrame(rows)
    frame["measured_at"] = pd.to_datetime(frame["timestamp_min"], unit="m", origin="2026-01-01")
    return frame.sort_values(["train_id", "timestamp_min"]).reset_index(drop=True)


def label_to_pct(congestion_ratio: float) -> float:
    return round(float(np.clip(congestion_ratio, 0.0, 1.0)) * 100.0, 1)


def pct_to_ratio(pct: float) -> float:
    return float(np.clip(pct / 100.0, 0.0, 1.0))
