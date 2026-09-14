import numpy as np

from congestion_ai.ensemble import blend_predictions, compute_ensemble_weights, ratio_to_label
from congestion_ai.features import build_feature_matrix, build_lstm_sequences
from congestion_ai.metrics import regression_metrics
from congestion_ai.service import CongestionForecastService
from congestion_ai.synthetic_data import generate_synthetic_dataset


def test_synthetic_dataset_has_future_targets():
    frame = generate_synthetic_dataset(n_trains=4, steps_per_train=40, seed=1)
    assert len(frame) == 160
    assert "target_5m" in frame.columns
    assert "target_10m" in frame.columns
    assert "target_15m" in frame.columns
    assert frame["congestion_current"].between(0.05, 0.98).all()


def test_time_ordered_split_has_no_feature_leakage_shape():
    frame = generate_synthetic_dataset(n_trains=6, steps_per_train=50, seed=2)
    x, y = build_feature_matrix(frame)
    assert x.shape[0] == y.shape[0]
    seq_x, seq_y = build_lstm_sequences(frame)
    assert seq_x.ndim == 3
    assert seq_y.shape[1] == 3


def test_train_models_and_forecast(client):
    with client.application.app_context():
        service = CongestionForecastService()
        metadata = service.train_all()
        assert metadata["data_source"] in {"synthetic_simulation", "mixed_operational_and_synthetic", "operational_timeseries"}
        assert "models" in metadata
        forecast = service.forecast(
            hour=8,
            route_name="서울-부산",
            recent_passengers=900,
            is_event_day=False,
            train_id=1,
            train_snapshot={"congestion": "medium", "speed_kmh": 120, "operation_status": "normal"},
            history=[{"congestion": "medium"}, {"congestion": "high"}],
        )
    assert forecast["current_congestion_pct"] >= 0
    assert "5" in forecast["forecasts"]
    assert "10" in forecast["forecasts"]
    assert "15" in forecast["forecasts"]
    assert forecast["data_status"]


def test_legacy_predict_and_compare_shapes(client):
    with client.application.app_context():
        service = CongestionForecastService()
        service.train_all()
        ml = service.predict_legacy(hour=18, route_name="서울권 순환", recent_passengers=1200, is_event_day=True, model_type="ml")
        dl = service.predict_legacy(hour=18, route_name="서울권 순환", recent_passengers=1200, is_event_day=True, model_type="dl")
        comparison = service.compare_legacy(hour=18, route_name="서울권 순환", recent_passengers=1200, is_event_day=True)

    assert ml["label"] in {"low", "medium", "high"}
    assert dl["label"] in {"low", "medium", "high"}
    assert 0 <= ml["confidence"] <= 1
    assert comparison["ensemble"]["label"] in {"low", "medium", "high"}
    assert "forecast" in comparison


def test_metrics_and_ensemble_weights():
    y_true = np.array([0.4, 0.5, 0.8, 0.7], dtype=np.float32)
    y_pred = np.array([0.42, 0.48, 0.76, 0.73], dtype=np.float32)
    metrics = regression_metrics(y_true, y_pred)
    assert metrics["mae"] >= 0
    assert metrics["rmse"] >= metrics["mae"]
    weights = compute_ensemble_weights(0.05, 0.10)
    blended = blend_predictions(np.array([0.6, 0.7, 0.8]), np.array([0.5, 0.65, 0.75]), ml_weight=weights["ml_weight"], dl_weight=weights["dl_weight"])
    assert blended.shape == (3,)
    assert ratio_to_label(0.8) == "high"


def test_forecast_api(client):
    response = client.post(
        "/api/scmaglev/ml/congestion/forecast",
        json={
            "hour": 9,
            "route_name": "서울-수원",
            "recent_passengers": 800,
            "is_event_day": False,
            "train_snapshot": {"congestion": "medium", "speed_kmh": 90, "operation_status": "normal"},
            "history": [{"congestion": "low"}, {"congestion": "medium"}],
        },
    )
    assert response.status_code == 200
    forecast = response.get_json()["forecast"]
    assert forecast["forecasts"]["10"]["ensemble_pct"] >= 5
    assert forecast["models"]["ml"]["model"] == "RandomForestRegressor"
    assert forecast["models"]["dl"]["model"] in {"LSTM", "LSTM (disabled)"}


def test_model_info_api(client):
    client.post(
        "/api/scmaglev/ml/congestion/forecast",
        json={"hour": 8, "route_name": "서울", "recent_passengers": 500},
    )
    response = client.get("/api/scmaglev/ml/congestion/model-info")
    assert response.status_code == 200
    info = response.get_json()["model_info"]
    assert info["ready"] is True
    assert "ensemble" in info
