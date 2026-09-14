"""운영 중 혼잡도·승객·배차 시계열 DB 수집."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading
import time

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from congestion_ai.constants import OBSERVATION_INTERVAL_SEC
from extensions import db
from models import (
    CongestionObservation,
    Reservation,
    RouteHeadwaySnapshot,
    Schedule,
    StationPassengerFlow,
    Train,
    TrainLocation,
)

_collector_lock = threading.Lock()
_last_observation_at: dict[int, datetime] = {}
_last_headway_at: dict[int, datetime] = {}

LABEL_TO_RATIO = {"low": 0.25, "medium": 0.58, "high": 0.86}


def label_to_ratio(label: str) -> float:
    return LABEL_TO_RATIO.get(str(label or "").lower(), 0.58)


def _estimate_passenger_flow(train_id: int, route_id: int | None, measured_at: datetime) -> tuple[int, int]:
    window_start = measured_at - timedelta(hours=2)
    booked = (
        db.session.query(func.count(Reservation.id))
        .join(Schedule, Schedule.id == Reservation.schedule_id)
        .filter(
            Schedule.train_id == train_id,
            Reservation.status == "booked",
            Reservation.booked_at >= window_start,
        )
        .scalar()
        or 0
    )
    base = 80 + (train_id % 23) * 17 + booked * 2
    if route_id:
        base += (route_id % 11) * 12
    hour = measured_at.hour
    if hour in {7, 8, 9, 18, 19, 20}:
        base = int(base * 1.35)
    board = max(int(base * 0.56), 10)
    alight = max(int(base * 0.44), 8)
    return board, alight


def _estimate_route_headway(route_id: int, measured_at: datetime) -> tuple[float, int]:
    active = (
        Train.query.join(TrainLocation, TrainLocation.train_id == Train.id)
        .filter(
            Train.route_id == route_id,
            TrainLocation.operation_status.in_(["normal", "delayed"]),
        )
        .count()
    )
    active = max(active, 1)
    headway = max(4.0, min(18.0, 90.0 / active))
    last = _last_headway_at.get(route_id)
    if last and (measured_at - last).total_seconds() < OBSERVATION_INTERVAL_SEC * 2:
        snap = (
            RouteHeadwaySnapshot.query.filter_by(route_id=route_id)
            .order_by(RouteHeadwaySnapshot.measured_at.desc())
            .first()
        )
        if snap:
            return snap.headway_min, snap.active_trains
    return headway, active


def record_operational_snapshot(
    *,
    train_id: int,
    route_id: int | None,
    station_id: int | None,
    payload: dict,
    measured_at: datetime | None = None,
) -> bool:
    """시뮬레이터/API에서 호출 — throttled DB insert."""
    now = measured_at or datetime.now(UTC).replace(tzinfo=None)
    with _collector_lock:
        last = _last_observation_at.get(train_id)
        if last and (now - last).total_seconds() < OBSERVATION_INTERVAL_SEC:
            return False
        _last_observation_at[train_id] = now

    congestion_label = str(payload.get("congestion") or "medium").lower()
    ratio = float(payload.get("congestion_ratio") or label_to_ratio(congestion_label))
    board, alight = _estimate_passenger_flow(train_id, route_id, now)
    headway = 8.0
    active_trains = 1
    if route_id:
        headway, active_trains = _estimate_route_headway(route_id, now)
        _last_headway_at[route_id] = now

    db.session.add(
        CongestionObservation(
            measured_at=now,
            train_id=train_id,
            route_id=route_id,
            station_id=station_id,
            congestion_ratio=ratio,
            congestion_label=congestion_label,
            passengers_board=board,
            passengers_alight=alight,
            headway_min=headway,
            speed_kmh=float(payload.get("speed_kmh") or 0),
            operation_status=str(payload.get("operation_status") or "normal"),
            data_origin="operational",
        )
    )

    if station_id:
        db.session.add(
            StationPassengerFlow(
                measured_at=now,
                station_id=station_id,
                train_id=train_id,
                route_id=route_id,
                passengers_board=board,
                passengers_alight=alight,
                data_origin="operational",
            )
        )

    if route_id:
        db.session.add(
            RouteHeadwaySnapshot(
                measured_at=now,
                route_id=route_id,
                headway_min=headway,
                active_trains=active_trains,
                data_origin="operational",
            )
        )
    return True


def _commit_with_retry(max_attempts: int = 8, delay_seconds: float = 0.25) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            db.session.commit()
            return
        except OperationalError as exc:
            db.session.rollback()
            if "database is locked" not in str(exc).lower() or attempt == max_attempts:
                raise
            time.sleep(delay_seconds * attempt)


def record_observations_batch(items: list[dict]) -> int:
    """시뮬레이터 tick 종료 후 한 번에 관측 저장 (SQLite lock 완화)."""
    if not items:
        return 0

    created = 0
    with db.session.no_autoflush:
        for item in items:
            train_id = int(item["train_id"])
            payload = item.get("payload") or {}
            measured_at = item.get("measured_at") or datetime.now(UTC).replace(tzinfo=None)
            with _collector_lock:
                last = _last_observation_at.get(train_id)
                if last and (measured_at - last).total_seconds() < OBSERVATION_INTERVAL_SEC:
                    continue
                _last_observation_at[train_id] = measured_at

            congestion_label = str(payload.get("congestion") or "medium").lower()
            ratio = float(payload.get("congestion_ratio") or label_to_ratio(congestion_label))
            board = max(int(80 + (train_id % 23) * 17), 10)
            alight = max(int(board * 0.85), 8)
            db.session.add(
                CongestionObservation(
                    measured_at=measured_at,
                    train_id=train_id,
                    route_id=item.get("route_id"),
                    station_id=item.get("station_id"),
                    congestion_ratio=ratio,
                    congestion_label=congestion_label,
                    passengers_board=board,
                    passengers_alight=alight,
                    headway_min=8.0,
                    speed_kmh=float(payload.get("speed_kmh") or 0),
                    operation_status=str(payload.get("operation_status") or "normal"),
                    data_origin="operational",
                )
            )
            created += 1

    if created:
        _commit_with_retry()
    return created


def bootstrap_operational_history(target_samples: int = 600) -> int:
    """초기 학습용 — 운영 테이블이 비어 있으면 합성 규칙으로 bootstrap rows 생성."""
    existing = CongestionObservation.query.count()
    if existing >= target_samples:
        return 0

    from congestion_ai.synthetic_data import generate_synthetic_dataset

    frame = generate_synthetic_dataset(n_trains=20, steps_per_train=max(30, target_samples // 20))
    created = 0
    base_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3)
    for _, row in frame.iterrows():
        measured_at = base_time + timedelta(minutes=int(row["timestamp_min"]))
        train_key = str(row["train_id"])
        train_numeric = int(train_key.replace("T", "")) + 1
        db.session.add(
            CongestionObservation(
                measured_at=measured_at,
                train_id=train_numeric,
                route_id=int(row["route_id"]) + 1,
                station_id=None,
                congestion_ratio=float(row["congestion_current"]),
                congestion_label="high" if row["congestion_current"] >= 0.72 else "medium" if row["congestion_current"] >= 0.42 else "low",
                passengers_board=int(row["passengers_board"]),
                passengers_alight=int(row["passengers_alight"]),
                headway_min=float(row["headway_min"]),
                speed_kmh=float(row["speed_kmh"]),
                operation_status=str(row["operation_status"]),
                data_origin="bootstrap",
            )
        )
        created += 1
    db.session.commit()
    return created


def get_data_collection_stats() -> dict:
    try:
        db.session.connection()
    except RuntimeError:
        return {
            "observation_count": 0,
            "operational_count": 0,
            "bootstrap_count": 0,
            "station_flow_count": 0,
            "headway_snapshot_count": 0,
            "latest_measured_at": None,
            "observation_interval_sec": OBSERVATION_INTERVAL_SEC,
        }
    obs_count = CongestionObservation.query.count()
    operational_count = CongestionObservation.query.filter_by(data_origin="operational").count()
    bootstrap_count = CongestionObservation.query.filter_by(data_origin="bootstrap").count()
    station_flow_count = StationPassengerFlow.query.count()
    headway_count = RouteHeadwaySnapshot.query.count()
    latest = (
        CongestionObservation.query.order_by(CongestionObservation.measured_at.desc()).first()
    )
    return {
        "observation_count": obs_count,
        "operational_count": operational_count,
        "bootstrap_count": bootstrap_count,
        "station_flow_count": station_flow_count,
        "headway_snapshot_count": headway_count,
        "latest_measured_at": latest.measured_at.isoformat() if latest else None,
        "observation_interval_sec": OBSERVATION_INTERVAL_SEC,
    }
