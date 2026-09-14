import json

from congestion_ai.data_collector import (
    bootstrap_operational_history,
    get_data_collection_stats,
    record_operational_snapshot,
)
from congestion_ai.model_registry import get_active_version, list_versions, promote_version
from congestion_ai.real_data import build_training_dataset, load_operational_frame
from congestion_ai.service import get_forecast_service
from extensions import db
from models import ModelTrainingRun


def test_operational_observation_collection(client):
    with client.application.app_context():
        created = record_operational_snapshot(
            train_id=1,
            route_id=1,
            station_id=1,
            payload={
                "congestion": "medium",
                "congestion_ratio": 0.58,
                "speed_kmh": 120,
                "operation_status": "normal",
            },
        )
        db.session.commit()
        assert created is True
        stats = get_data_collection_stats()
        assert stats["observation_count"] >= 1


def test_bootstrap_and_real_data_pipeline(client):
    with client.application.app_context():
        bootstrap_operational_history(target_samples=150)
        frame = load_operational_frame()
        assert len(frame) >= 50
        dataset, info = build_training_dataset()
        assert len(dataset) >= 100
        assert info["data_source"] in {
            "synthetic_simulation",
            "mixed_operational_and_synthetic",
            "operational_timeseries",
        }


def test_model_versioning_and_promote(client):
    with client.application.app_context():
        bootstrap_operational_history(target_samples=120)
        service = get_forecast_service()
        metadata = service.train_all(force=True, promote=True)
        version = metadata["version"]
        assert version
        versions = list_versions()
        assert any(v["version"] == version for v in versions)
        assert get_active_version() == version

        service2 = get_forecast_service()
        metadata2 = service2.train_all(force=True, promote=True)
        old_version = version
        metadata2["version"]
        promote_version(old_version)
        assert get_active_version() == old_version


def test_retrain_status_api(client):
    client.post("/api/scmaglev/ml/congestion/forecast", json={"hour": 8, "route_name": "서울", "recent_passengers": 500})
    status = client.get("/api/scmaglev/ml/congestion/retrain-status")
    assert status.status_code == 200
    body = status.get_json()
    assert "should_retrain" in body
    assert "reason" in body


def test_data_stats_and_versions_api(client):
    stats = client.get("/api/scmaglev/ml/congestion/data-stats")
    versions = client.get("/api/scmaglev/ml/congestion/versions")
    assert stats.status_code == 200
    assert versions.status_code == 200
    assert "stats" in stats.get_json()
    assert "versions" in versions.get_json()


def test_manual_retrain_api(client):
    response = client.post("/api/scmaglev/ml/congestion/retrain", json={"force": True})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload.get("retrained") is True


def test_training_run_recorded(client):
    with client.application.app_context():
        bootstrap_operational_history(target_samples=120)
        service = get_forecast_service()
        service.train_all(force=True, promote=True)
        row = ModelTrainingRun.query.filter_by(is_active=True).first()
        assert row is not None
        meta = json.loads(row.metadata_json or "{}")
        assert "models" in meta
