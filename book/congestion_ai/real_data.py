"""DB 운영 시계열 → 학습용 DataFrame (실데이터 우선, 부족 시 합성 보강)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from congestion_ai.constants import (
    DATA_SOURCE_MIXED,
    DATA_SOURCE_OPERATIONAL,
    DATA_SOURCE_SYNTHETIC,
    FORECAST_HORIZONS_MIN,
    MIN_MIXED_SAMPLES,
    MIN_OPERATIONAL_SAMPLES,
    SYNTHETIC_RULES_DOC,
)
from congestion_ai.synthetic_data import STATUS_CODES, generate_synthetic_dataset
from extensions import db
from models import CongestionObservation


def _find_future_ratio(group: pd.DataFrame, current_time, horizon_min: int) -> float | None:
    target = current_time + timedelta(minutes=horizon_min)
    tolerance = timedelta(minutes=2.5)
    candidates = group[
        (group["measured_at"] >= target - tolerance) & (group["measured_at"] <= target + tolerance)
    ]
    if candidates.empty:
        later = group[group["measured_at"] > current_time].sort_values("measured_at")
        if later.empty:
            return None
        return float(later.iloc[0]["congestion_current"])
    return float(candidates.iloc[0]["congestion_current"])


def _has_app_context() -> bool:
    try:
        db.session.connection()
        return True
    except RuntimeError:
        return False


def load_operational_frame(limit: int | None = None) -> pd.DataFrame:
    if not _has_app_context():
        return pd.DataFrame()
    query = CongestionObservation.query.order_by(CongestionObservation.measured_at.asc())
    if limit:
        rows = query.limit(limit).all()
    else:
        rows = query.all()
    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows:
        measured = row.measured_at
        records.append(
            {
                "train_id": f"T{row.train_id:04d}",
                "route_id": row.route_id or 0,
                "timestamp_min": int(measured.timestamp() // 60),
                "measured_at": measured,
                "hour": measured.hour,
                "day_of_week": measured.weekday(),
                "congestion_current": float(row.congestion_ratio),
                "passengers_board": float(row.passengers_board),
                "passengers_alight": float(row.passengers_alight),
                "headway_min": float(row.headway_min),
                "speed_kmh": float(row.speed_kmh),
                "operation_status": row.operation_status,
                "operation_status_code": STATUS_CODES.get(row.operation_status, 0),
                "data_origin": row.data_origin,
            }
        )

    frame = pd.DataFrame(records)
    output_rows = []
    for train_id, group in frame.groupby("train_id", sort=False):
        group = group.sort_values("measured_at").reset_index(drop=True)
        for idx, row in group.iterrows():
            targets = {}
            valid = True
            for horizon in FORECAST_HORIZONS_MIN:
                future = _find_future_ratio(group, row["measured_at"], horizon)
                if future is None:
                    valid = False
                    break
                targets[f"target_{horizon}m"] = future
            if not valid:
                continue
            output_rows.append({**row.to_dict(), **targets})

    if not output_rows:
        return pd.DataFrame()
    return pd.DataFrame(output_rows).reset_index(drop=True)


def build_training_dataset() -> tuple[pd.DataFrame, dict]:
    operational = load_operational_frame()
    operational_count = len(operational)
    if operational.empty:
        pure_operational_count = 0
    elif "data_origin" in operational.columns:
        pure_operational_count = len(operational[operational["data_origin"] == "operational"])
    else:
        pure_operational_count = operational_count

    if pure_operational_count >= MIN_OPERATIONAL_SAMPLES:
        return operational, {
            "data_source": DATA_SOURCE_OPERATIONAL,
            "data_status": "운영 DB 시계열 기반 학습",
            "operational_sample_count": pure_operational_count,
            "sample_count": operational_count,
            "data_rules": "congestion_observations + station_passenger_flows + route_headway_snapshots",
        }

    if operational_count >= MIN_MIXED_SAMPLES:
        synthetic = generate_synthetic_dataset(n_trains=30, steps_per_train=120)
        synthetic["data_origin"] = "synthetic_augment"
        mixed = pd.concat([operational, synthetic], ignore_index=True, sort=False)
        mixed = mixed.sort_values("timestamp_min").reset_index(drop=True)
        return mixed, {
            "data_source": DATA_SOURCE_MIXED,
            "data_status": f"운영 DB {operational_count}건 + 합성 보강 {len(synthetic)}건",
            "operational_sample_count": operational_count,
            "sample_count": len(mixed),
            "data_rules": f"operational + synthetic augment. {SYNTHETIC_RULES_DOC}",
        }

    synthetic = generate_synthetic_dataset()
    return synthetic, {
        "data_source": DATA_SOURCE_SYNTHETIC,
        "data_status": "운영 데이터 부족 · 합성 데이터 기반 시뮬레이션",
        "operational_sample_count": operational_count,
        "sample_count": len(synthetic),
        "data_rules": SYNTHETIC_RULES_DOC,
    }


def count_observations_since(since) -> int:
    if not _has_app_context():
        return 0
    if since is None:
        return CongestionObservation.query.count()
    return (
        CongestionObservation.query.filter(CongestionObservation.measured_at > since)
        .count()
    )
