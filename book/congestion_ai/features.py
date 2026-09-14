from __future__ import annotations

import numpy as np
import pandas as pd

from congestion_ai.constants import FORECAST_HORIZONS_MIN, SEQUENCE_LENGTH
from congestion_ai.synthetic_data import STATUS_CODES, pct_to_ratio

ML_FEATURE_COLUMNS = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "congestion_current",
    "passengers_board",
    "passengers_alight",
    "headway_min",
    "speed_kmh",
    "operation_status_code",
    "lag_1",
    "lag_2",
    "lag_3",
]

TARGET_COLUMNS = [f"target_{h}m" for h in FORECAST_HORIZONS_MIN]


def _label_to_ratio(label: str | None) -> float:
    mapping = {"low": 0.25, "medium": 0.58, "high": 0.86}
    return mapping.get(str(label or "").lower(), 0.58)


def enrich_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["hour_sin"] = np.sin(2 * np.pi * data["hour"] / 24.0)
    data["hour_cos"] = np.cos(2 * np.pi * data["hour"] / 24.0)
    data["dow_sin"] = np.sin(2 * np.pi * data["day_of_week"] / 7.0)
    data["dow_cos"] = np.cos(2 * np.pi * data["day_of_week"] / 7.0)
    grouped = data.groupby("train_id", sort=False)["congestion_current"]
    data["lag_1"] = grouped.shift(1).fillna(data["congestion_current"])
    data["lag_2"] = grouped.shift(2).fillna(data["congestion_current"])
    data["lag_3"] = grouped.shift(3).fillna(data["congestion_current"])
    return data.dropna(subset=TARGET_COLUMNS)


def build_feature_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    enriched = enrich_dataframe(frame)
    x = enriched[ML_FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    y = enriched[TARGET_COLUMNS].to_numpy(dtype=np.float32)
    return x, y


def build_lstm_sequences(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    enriched = enrich_dataframe(frame)
    sequences: list[np.ndarray] = []
    targets: list[np.ndarray] = []
    feature_cols = ML_FEATURE_COLUMNS

    for _, group in enriched.groupby("train_id", sort=False):
        values = group[feature_cols + TARGET_COLUMNS].to_numpy(dtype=np.float32)
        if len(values) <= SEQUENCE_LENGTH:
            continue
        for idx in range(SEQUENCE_LENGTH, len(values)):
            sequences.append(values[idx - SEQUENCE_LENGTH : idx, : len(feature_cols)])
            targets.append(values[idx, len(feature_cols) :])

    if not sequences:
        return np.empty((0, SEQUENCE_LENGTH, len(feature_cols)), dtype=np.float32), np.empty((0, len(TARGET_COLUMNS)), dtype=np.float32)

    return np.stack(sequences), np.stack(targets)


def features_from_train_context(
    *,
    hour: int,
    day_of_week: int,
    current_congestion_ratio: float,
    passengers_board: float,
    passengers_alight: float,
    headway_min: float,
    speed_kmh: float,
    operation_status: str,
    history_ratios: list[float] | None = None,
) -> np.ndarray:
    history = history_ratios or []
    lag_1 = history[-1] if len(history) >= 1 else current_congestion_ratio
    lag_2 = history[-2] if len(history) >= 2 else lag_1
    lag_3 = history[-3] if len(history) >= 3 else lag_2
    row = {
        "hour_sin": np.sin(2 * np.pi * hour / 24.0),
        "hour_cos": np.cos(2 * np.pi * hour / 24.0),
        "dow_sin": np.sin(2 * np.pi * day_of_week / 7.0),
        "dow_cos": np.cos(2 * np.pi * day_of_week / 7.0),
        "congestion_current": current_congestion_ratio,
        "passengers_board": passengers_board,
        "passengers_alight": passengers_alight,
        "headway_min": headway_min,
        "speed_kmh": speed_kmh,
        "operation_status_code": STATUS_CODES.get(operation_status, 0),
        "lag_1": lag_1,
        "lag_2": lag_2,
        "lag_3": lag_3,
    }
    return np.array([row[col] for col in ML_FEATURE_COLUMNS], dtype=np.float32)


def build_sequence_from_history(
    feature_rows: list[np.ndarray],
) -> np.ndarray | None:
    if not feature_rows:
        return None
    if len(feature_rows) >= SEQUENCE_LENGTH:
        seq = np.stack(feature_rows[-SEQUENCE_LENGTH:])
    else:
        pad_count = SEQUENCE_LENGTH - len(feature_rows)
        pad = np.repeat(feature_rows[0:1], pad_count, axis=0)
        seq = np.concatenate([pad, np.stack(feature_rows)], axis=0)
    return seq.astype(np.float32)


def history_to_ratios(history: list[dict]) -> list[float]:
    ratios: list[float] = []
    for point in history:
        if "congestion_pct" in point:
            ratios.append(pct_to_ratio(float(point["congestion_pct"])))
        elif "congestion_ratio" in point:
            ratios.append(float(point["congestion_ratio"]))
        else:
            ratios.append(_label_to_ratio(point.get("congestion")))
    return ratios
