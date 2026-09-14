from __future__ import annotations

from datetime import UTC, datetime
import json
import threading
from typing import Any

import numpy as np

from congestion_ai.constants import (
    DATA_SOURCE_MIXED,
    DATA_SOURCE_OPERATIONAL,
    DATA_SOURCE_SYNTHETIC,
    DISABLE_DL,
    FORECAST_HORIZONS_MIN,
    LAZY_MODEL_LOAD,
    METADATA_FILENAME,
    MODELS_DIR,
    PRIMARY_HORIZON_MIN,
)
from congestion_ai.data_collector import get_data_collection_stats
from congestion_ai.ensemble import (
    blend_predictions,
    compute_ensemble_weights,
    primary_index,
    ratio_to_label,
)
from congestion_ai.fallback import (
    build_heuristic_comparison,
    build_heuristic_forecast,
    estimate_current_ratio,
)
from congestion_ai.features import (
    build_sequence_from_history,
    features_from_train_context,
    history_to_ratios,
)
from congestion_ai.ml_random_forest import RandomForestCongestionModel
from congestion_ai.model_registry import (
    active_model_dir,
    get_active_version,
    list_versions,
    make_version_tag,
    promote_version,
    record_training_run,
    save_version,
)
from congestion_ai.real_data import build_training_dataset
from congestion_ai.synthetic_data import label_to_pct
from extensions import db

_service: "CongestionForecastService | None" = None
_service_lock = threading.Lock()


def _create_lstm_model():
    from congestion_ai.dl_lstm import LSTMCongestionModel

    return LSTMCongestionModel()


class CongestionForecastService:
    def __init__(self) -> None:
        self.models_dir = MODELS_DIR
        self.rf = RandomForestCongestionModel()
        self._lstm = None
        self.metadata: dict[str, Any] = {}
        self._ready = False
        self._training = False
        self._train_lock = threading.Lock()
        self._heuristic_only = False

    @property
    def lstm(self):
        if DISABLE_DL:
            return None
        if self._lstm is None:
            self._lstm = _create_lstm_model()
        return self._lstm

    @lstm.setter
    def lstm(self, value):
        self._lstm = value

    def _dl_model_meta(self) -> dict[str, Any]:
        if DISABLE_DL:
            return {
                "model": "LSTM (disabled)",
                "framework": "none",
                "metrics_test": {"mae": None, "r2": None},
            }
        return {
            "model": self.lstm.MODEL_NAME,
            "framework": self.lstm.FRAMEWORK,
            "metrics_test": self.metadata["models"]["dl"]["metrics"]["overall_test"],
        }

    def _ml_metrics_test(self) -> dict[str, Any]:
        return (
            self.metadata.get("models", {})
            .get("ml", {})
            .get("metrics", {})
            .get("overall_test", {"mae": None, "r2": None})
        )

    def ensure_trained(self, *, force: bool = False) -> None:
        if self._ready and not force:
            return
        with self._train_lock:
            if self._ready and not force:
                return
            if not force and self._load_artifacts():
                self._ready = True
                return
            self._training = True
            try:
                self.train_all(force=force)
                self._ready = True
                self._heuristic_only = False
            except Exception:
                self._heuristic_only = True
                self._ready = True
                self.metadata = {
                    "data_source": "rule_based_fallback",
                    "data_status": "ML 학습 실패 · 규칙 기반 fallback",
                }
            finally:
                self._training = False

    def train_all(self, *, force: bool = False, promote: bool = True) -> dict[str, Any]:
        frame, dataset_info = build_training_dataset()
        if frame.empty:
            raise RuntimeError("학습 데이터가 비어 있습니다.")

        self.rf = RandomForestCongestionModel()
        rf_metrics = self.rf.train(frame)
        if DISABLE_DL:
            lstm_metrics = {
                "overall_test": {"mae": rf_metrics["overall_test"]["mae"], "r2": 0.0},
            }
            weights = {"ml_weight": 1.0, "dl_weight": 0.0, "method": "ml_only_render_lite"}
            self._lstm = None
        else:
            self.lstm = _create_lstm_model()
            lstm_metrics = self.lstm.train(frame)
            ml_mae = rf_metrics["overall_test"]["mae"]
            dl_mae = lstm_metrics["overall_test"]["mae"]
            dl_r2 = lstm_metrics["overall_test"].get("r2")
            weights = compute_ensemble_weights(ml_mae, dl_mae, dl_r2=dl_r2)
        version = make_version_tag()

        self.metadata = {
            "version": version,
            "trained_at": datetime.now(UTC).isoformat(),
            "forecast_horizons_min": list(FORECAST_HORIZONS_MIN),
            "primary_horizon_min": PRIMARY_HORIZON_MIN,
            "models": {
                "ml": {
                    "name": self.rf.MODEL_NAME,
                    "library": self.rf.LIBRARY,
                    "metrics": rf_metrics,
                },
                "dl": {
                    "name": "LSTM (disabled)" if DISABLE_DL else self.lstm.MODEL_NAME,
                    "framework": "none" if DISABLE_DL else self.lstm.FRAMEWORK,
                    "metrics": lstm_metrics,
                },
            },
            "ensemble": weights,
            "collection_stats": get_data_collection_stats(),
            **dataset_info,
        }
        self._save_artifacts(version=version, promote=promote)
        self._ready = True
        return self.metadata

    def _save_artifacts(self, *, version: str | None = None, promote: bool = True) -> None:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        version = version or self.metadata.get("version") or make_version_tag()
        save_version(version, self.metadata, self.rf, None if DISABLE_DL else self.lstm)
        if promote:
            promote_version(version)
            try:
                record_training_run(version, self.metadata)
            except Exception:
                db.session.rollback()
        legacy_dir = self.models_dir
        self.rf.save(legacy_dir)
        if not DISABLE_DL:
            self.lstm.save(legacy_dir)
        (legacy_dir / METADATA_FILENAME).write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_artifacts(self) -> bool:
        load_dir = active_model_dir()
        if not self.rf.load(load_dir):
            if load_dir != self.models_dir and self.rf.load(self.models_dir):
                load_dir = self.models_dir
            else:
                return False
        if not DISABLE_DL and not self.lstm.load(load_dir):
            return False
        meta_path = load_dir / METADATA_FILENAME
        if not meta_path.exists():
            meta_path = self.models_dir / METADATA_FILENAME
        if meta_path.exists():
            self.metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        return True

    def get_model_info(self) -> dict[str, Any]:
        try:
            if not self._ready and LAZY_MODEL_LOAD:
                if self._load_artifacts():
                    self._ready = True
            elif not self._ready:
                self.ensure_trained()
        except Exception:
            self._heuristic_only = True
            self._ready = True
        payload = {
            "ready": self._ready,
            "training": self._training,
            "heuristic_only": self._heuristic_only,
            "active_version": get_active_version(),
            "versions": list_versions(),
            "collection_stats": get_data_collection_stats(),
        }
        payload.update(self.metadata or {})
        if self._heuristic_only:
            payload.setdefault("data_status", "규칙 기반 fallback")
        return payload

    def _build_context(
        self,
        *,
        hour: int,
        route_name: str,
        recent_passengers: int,
        is_event_day: bool,
        train_snapshot: dict | None = None,
        history: list[dict] | None = None,
    ) -> dict[str, Any]:
        snapshot = train_snapshot or {}
        history = history or []
        ratios = history_to_ratios(history)
        current_ratio = ratios[-1] if ratios else estimate_current_ratio(
            snapshot, recent_passengers, hour, is_event_day
        )
        day_of_week = datetime.now().weekday()
        board = max(recent_passengers * 0.55, 40)
        alight = max(recent_passengers * 0.45, 30)
        if is_event_day:
            current_ratio = float(np.clip(current_ratio + 0.08, 0.05, 0.98))
        return {
            "hour": hour,
            "day_of_week": day_of_week,
            "route_name": route_name or snapshot.get("route_name") or "",
            "current_ratio": current_ratio,
            "current_pct": label_to_pct(current_ratio),
            "passengers_board": board,
            "passengers_alight": alight,
            "headway_min": 8.0 if "직통" in (route_name or "") else 6.5,
            "speed_kmh": float(snapshot.get("speed_kmh") or 0),
            "operation_status": str(snapshot.get("operation_status") or "normal"),
            "history_ratios": ratios,
        }

    def forecast(
        self,
        *,
        hour: int,
        route_name: str,
        recent_passengers: int,
        is_event_day: bool = False,
        train_id: int | None = None,
        train_snapshot: dict | None = None,
        history: list[dict] | None = None,
    ) -> dict[str, Any]:
        try:
            self.ensure_trained()
            if self._heuristic_only:
                raise RuntimeError("heuristic mode")
            return self._forecast_ml(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                train_id=train_id,
                train_snapshot=train_snapshot,
                history=history,
            )
        except Exception:
            return build_heuristic_forecast(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                train_id=train_id,
            )

    def _forecast_ml(
        self,
        *,
        hour: int,
        route_name: str,
        recent_passengers: int,
        is_event_day: bool,
        train_id: int | None,
        train_snapshot: dict | None,
        history: list[dict] | None,
    ) -> dict[str, Any]:
        ctx = self._build_context(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
            train_snapshot=train_snapshot,
            history=history,
        )

        feature_vector = features_from_train_context(
            hour=ctx["hour"],
            day_of_week=ctx["day_of_week"],
            current_congestion_ratio=ctx["current_ratio"],
            passengers_board=ctx["passengers_board"],
            passengers_alight=ctx["passengers_alight"],
            headway_min=ctx["headway_min"],
            speed_kmh=ctx["speed_kmh"],
            operation_status=ctx["operation_status"],
            history_ratios=ctx["history_ratios"],
        )

        feature_rows = []
        for idx, ratio in enumerate(ctx["history_ratios"][-12:]):
            feature_rows.append(
                features_from_train_context(
                    hour=ctx["hour"],
                    day_of_week=ctx["day_of_week"],
                    current_congestion_ratio=ratio,
                    passengers_board=ctx["passengers_board"],
                    passengers_alight=ctx["passengers_alight"],
                    headway_min=ctx["headway_min"],
                    speed_kmh=ctx["speed_kmh"],
                    operation_status=ctx["operation_status"],
                    history_ratios=ctx["history_ratios"][: idx + 1],
                )
            )
        if not feature_rows:
            feature_rows = [feature_vector]
        sequence = build_sequence_from_history(feature_rows)

        ml_pred = self.rf.predict(feature_vector)
        if DISABLE_DL or self.lstm is None:
            dl_pred = ml_pred
            weights = {"ml_weight": 1.0, "dl_weight": 0.0, "method": "ml_only_render_lite"}
            ensemble_pred = ml_pred
        else:
            dl_pred = self.lstm.predict(sequence)
            weights = self.metadata.get("ensemble") or compute_ensemble_weights(
                self.metadata["models"]["ml"]["metrics"]["overall_test"]["mae"],
                self.metadata["models"]["dl"]["metrics"]["overall_test"]["mae"],
                dl_r2=self.metadata["models"]["dl"]["metrics"]["overall_test"].get("r2"),
            )
            ensemble_pred = blend_predictions(
                ml_pred,
                dl_pred,
                ml_weight=weights["ml_weight"],
                dl_weight=weights["dl_weight"],
            )

        now = datetime.now(UTC)
        history_points = [
            {
                "kind": "actual",
                "minutes_offset": -((len(ctx["history_ratios"]) - idx) * 3),
                "timestamp": now.isoformat(),
                "congestion_pct": label_to_pct(ratio),
            }
            for idx, ratio in enumerate(ctx["history_ratios"])
        ] or [
            {
                "kind": "actual",
                "minutes_offset": 0,
                "timestamp": now.isoformat(),
                "congestion_pct": ctx["current_pct"],
            }
        ]

        forecast_points = []
        for idx, horizon in enumerate(FORECAST_HORIZONS_MIN):
            forecast_points.append(
                {
                    "kind": "forecast",
                    "minutes_offset": horizon,
                    "timestamp": now.isoformat(),
                    "ml_pct": label_to_pct(ml_pred[idx]),
                    "dl_pct": label_to_pct(dl_pred[idx]),
                    "ensemble_pct": label_to_pct(ensemble_pred[idx]),
                }
            )

        primary_idx = primary_index()
        primary_ratio = float(ensemble_pred[primary_idx])
        reliability_note = self._reliability_note(ctx, sequence)

        return {
            "train_id": train_id,
            "reference_time": now.isoformat(),
            "route_name": ctx["route_name"],
            "current_congestion_pct": ctx["current_pct"],
            "current_congestion_label": ratio_to_label(ctx["current_ratio"]),
            "operation_status": ctx["operation_status"],
            "speed_kmh": ctx["speed_kmh"],
            "forecasts": {
                str(h): {
                    "minutes_ahead": h,
                    "ml_pct": label_to_pct(ml_pred[i]),
                    "dl_pct": label_to_pct(dl_pred[i]),
                    "ensemble_pct": label_to_pct(ensemble_pred[i]),
                    "ml_label": ratio_to_label(float(ml_pred[i])),
                    "dl_label": ratio_to_label(float(dl_pred[i])),
                    "ensemble_label": ratio_to_label(float(ensemble_pred[i])),
                }
                for i, h in enumerate(FORECAST_HORIZONS_MIN)
            },
            "primary_horizon_min": PRIMARY_HORIZON_MIN,
            "primary_forecast_pct": label_to_pct(primary_ratio),
            "primary_forecast_label": ratio_to_label(primary_ratio),
            "models": {
                "ml": {
                    "model": self.rf.MODEL_NAME,
                    "library": self.rf.LIBRARY,
                    "metrics_test": self._ml_metrics_test(),
                },
                "dl": self._dl_model_meta(),
            },
            "ensemble": {
                **weights,
                "primary_pct": label_to_pct(primary_ratio),
                "primary_label": ratio_to_label(primary_ratio),
            },
            "chart_series": {
                "history": history_points,
                "forecast": forecast_points,
            },
            "data_source": self.metadata.get("data_source", DATA_SOURCE_SYNTHETIC),
            "data_status": self.metadata.get("data_status", "합성 데이터 기반 시뮬레이션"),
            "model_version": self.metadata.get("version") or get_active_version(),
            "prediction_status": reliability_note["status"],
            "reliability_note": reliability_note["message"],
        }

    def predict_legacy(
        self,
        *,
        hour: int,
        route_name: str,
        recent_passengers: int,
        is_event_day: bool,
        model_type: str = "ml",
    ) -> dict[str, Any]:
        forecast = self.forecast(
            hour=hour,
            route_name=route_name,
            recent_passengers=recent_passengers,
            is_event_day=is_event_day,
        )
        primary = forecast["forecasts"][str(PRIMARY_HORIZON_MIN)]
        if model_type == "dl":
            label = primary["dl_label"]
            confidence = round(primary["dl_pct"] / 100.0, 3)
        else:
            label = primary["ml_label"]
            confidence = round(primary["ml_pct"] / 100.0, 3)
        dl_meta = self._dl_model_meta()
        return {
            "label": label,
            "confidence": confidence,
            "congestion_pct": primary["ml_pct"] if model_type != "dl" else primary["dl_pct"],
            "model_type": model_type,
            "model_name": self.rf.MODEL_NAME if model_type != "dl" else dl_meta["model"],
            "library": self.rf.LIBRARY if model_type != "dl" else dl_meta["framework"],
            "horizon_min": PRIMARY_HORIZON_MIN,
            "data_status": forecast["data_status"],
            "features": {
                "hour": hour,
                "route_name": route_name,
                "recent_passengers": recent_passengers,
                "is_event_day": bool(is_event_day),
            },
        }

    def compare_legacy(
        self,
        *,
        hour: int,
        route_name: str,
        recent_passengers: int,
        is_event_day: bool,
        train_id: int | None = None,
        train_snapshot: dict | None = None,
        history: list[dict] | None = None,
    ) -> dict[str, Any]:
        try:
            forecast = self.forecast(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                train_id=train_id,
                train_snapshot=train_snapshot,
                history=history,
            )
            primary = forecast["forecasts"][str(PRIMARY_HORIZON_MIN)]
            ml = self.predict_legacy(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                model_type="ml",
            )
            dl = self.predict_legacy(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                model_type="dl",
            )
            ensemble_ratio = primary["ensemble_pct"] / 100.0
            disagreement = abs(primary["ml_pct"] - primary["dl_pct"]) / 100.0
            if disagreement >= 20:
                guidance = "모델 간 편차가 큽니다. 관제사의 확인을 권장합니다."
            elif primary["ensemble_label"] == "high":
                guidance = "고혼잡 구간입니다. 배차 조정 또는 안내 발송이 필요합니다."
            elif primary["ensemble_label"] == "medium":
                guidance = "중간 혼잡 구간입니다. 혼잡 모니터링을 유지하세요."
            else:
                guidance = "혼잡 리스크가 낮은 구간입니다."

            return {
                "ml": ml,
                "dl": dl,
                "ensemble": {
                    "label": primary["ensemble_label"],
                    "risk_score": round(ensemble_ratio, 3),
                    "congestion_pct": primary["ensemble_pct"],
                    "disagreement": round(disagreement, 3),
                    "guidance": guidance,
                    "method": forecast["ensemble"]["method"],
                    "ml_weight": forecast["ensemble"]["ml_weight"],
                    "dl_weight": forecast["ensemble"]["dl_weight"],
                },
                "forecast": forecast,
                "data_status": forecast["data_status"],
            }
        except Exception:
            comparison = build_heuristic_comparison(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
            )
            comparison["forecast"] = build_heuristic_forecast(
                hour=hour,
                route_name=route_name,
                recent_passengers=recent_passengers,
                is_event_day=is_event_day,
                train_id=train_id,
            )
            return comparison

    def _reliability_note(self, ctx: dict, sequence: np.ndarray | None) -> dict[str, str]:
        source = self.metadata.get("data_source", DATA_SOURCE_SYNTHETIC)
        if source == DATA_SOURCE_OPERATIONAL:
            if len(ctx["history_ratios"]) < 5:
                return {
                    "status": "limited_history",
                    "message": "운영 DB 기반 모델이지만 선택 열차 런타임 이력이 부족합니다.",
                }
            return {"status": "operational", "message": "운영 DB 시계열 기반 예측"}
        if source == DATA_SOURCE_MIXED:
            return {
                "status": "mixed",
                "message": "운영 DB + 합성 보강 데이터로 학습된 모델입니다.",
            }
        if len(ctx["history_ratios"]) < 5:
            return {
                "status": "limited_history",
                "message": "합성 데이터 기반 모델 + 런타임 이력 부족으로 예측 신뢰성이 제한됩니다.",
            }
        return {
            "status": "synthetic_demo",
            "message": "운영 데이터 부족 · 합성 시뮬레이션 모델입니다.",
        }


def get_forecast_service() -> CongestionForecastService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CongestionForecastService()
    return _service
