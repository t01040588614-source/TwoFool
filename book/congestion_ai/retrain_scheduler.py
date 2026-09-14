"""주기적 모델 재학습 스케줄러."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import threading
import time

from congestion_ai.constants import (
    MIN_NEW_SAMPLES_FOR_RETRAIN,
    RETRAIN_CHECK_INTERVAL_SEC,
    RETRAIN_INTERVAL_HOURS,
)
from congestion_ai.model_registry import list_versions
from congestion_ai.real_data import count_observations_since
from models import ModelTrainingRun

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _last_training_time():
    active = ModelTrainingRun.query.filter_by(is_active=True).order_by(ModelTrainingRun.trained_at.desc()).first()
    if active:
        return active.trained_at.replace(tzinfo=None)
    versions = list_versions()
    if versions:
        trained_at = versions[0].get("trained_at")
        if trained_at:
            return datetime.fromisoformat(trained_at.replace("Z", "+00:00")).replace(tzinfo=None)
    return None


def should_retrain() -> tuple[bool, str]:
    if os.getenv("CONGESTION_AUTO_RETRAIN", "1") == "0":
        return False, "auto retrain disabled"

    last = _last_training_time()
    if last is None:
        return True, "no trained version yet"

    hours = (datetime.now(UTC).replace(tzinfo=None) - last).total_seconds() / 3600.0
    if hours >= RETRAIN_INTERVAL_HOURS:
        return True, f"scheduled interval reached ({hours:.1f}h >= {RETRAIN_INTERVAL_HOURS}h)"

    new_samples = count_observations_since(last)
    if new_samples >= MIN_NEW_SAMPLES_FOR_RETRAIN:
        return True, f"new operational samples {new_samples} >= {MIN_NEW_SAMPLES_FOR_RETRAIN}"

    return False, f"next check in {RETRAIN_INTERVAL_HOURS - hours:.1f}h or {MIN_NEW_SAMPLES_FOR_RETRAIN - new_samples} more samples"


def run_scheduled_retrain(force: bool = False) -> dict | None:
    from congestion_ai.service import get_forecast_service

    ok, reason = should_retrain()
    if not force and not ok:
        return {"retrained": False, "reason": reason}

    service = get_forecast_service()
    metadata = service.train_all(force=True, promote=True)
    return {"retrained": True, "reason": reason if not force else "manual force", "model_info": metadata}


def _scheduler_loop(app):
    while True:
        time.sleep(RETRAIN_CHECK_INTERVAL_SEC)
        try:
            with app.app_context():
                result = run_scheduled_retrain(force=False)
                if result and result.get("retrained"):
                    app.logger.info("Congestion model retrained: %s", result.get("reason"))
        except Exception as exc:
            app.logger.exception("Congestion retrain scheduler failed: %s", exc)


def start_retrain_scheduler(app) -> None:
    global _scheduler_started
    if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if os.getenv("CONGESTION_AUTO_RETRAIN", "1") == "0":
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        thread = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True, name="congestion-retrain")
        thread.start()
        _scheduler_started = True
