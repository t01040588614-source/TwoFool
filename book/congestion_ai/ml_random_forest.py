from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler

from congestion_ai.constants import FORECAST_HORIZONS_MIN, RF_MODEL_FILENAME, SCALER_FILENAME
from congestion_ai.features import ML_FEATURE_COLUMNS, TARGET_COLUMNS, build_feature_matrix
from congestion_ai.metrics import horizon_metrics, regression_metrics


class RandomForestCongestionModel:
    LIBRARY = "scikit-learn"
    MODEL_NAME = "RandomForestRegressor"

    def __init__(self) -> None:
        self.model: RandomForestRegressor | None = None
        self.scaler: StandardScaler | None = None
        self.metrics: dict = {}

    def train(self, frame, *, test_ratio: float = 0.2) -> dict:
        ordered = frame.sort_values("timestamp_min").reset_index(drop=True)
        split_idx = int(len(ordered) * (1.0 - test_ratio))
        train_frame = ordered.iloc[:split_idx]
        test_frame = ordered.iloc[split_idx:]
        x_train, y_train = build_feature_matrix(train_frame)
        x_test, y_test = build_feature_matrix(test_frame)

        self.scaler = StandardScaler()
        x_train_scaled = self.scaler.fit_transform(x_train)
        x_test_scaled = self.scaler.transform(x_test)

        n_estimators = 80 if os.getenv("PYTEST_CURRENT_TEST") else 180
        self.model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=12,
            min_samples_leaf=4,
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(x_train_scaled, y_train)
        train_pred = self.model.predict(x_train_scaled)
        test_pred = self.model.predict(x_test_scaled)

        horizon_labels = [f"{h}m" for h in FORECAST_HORIZONS_MIN]
        self.metrics = {
            "train": horizon_metrics(y_train, train_pred, horizon_labels),
            "test": horizon_metrics(y_test, test_pred, horizon_labels),
            "overall_test": regression_metrics(y_test.reshape(-1), test_pred.reshape(-1)),
            "feature_columns": ML_FEATURE_COLUMNS,
            "target_columns": TARGET_COLUMNS,
        }
        return self.metrics

    def predict(self, feature_vector: np.ndarray) -> np.ndarray:
        if self.model is None or self.scaler is None:
            raise RuntimeError("Random Forest model is not trained.")
        scaled = self.scaler.transform(feature_vector.reshape(1, -1))
        return np.clip(self.model.predict(scaled)[0], 0.05, 0.98)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, directory / RF_MODEL_FILENAME)
        joblib.dump(self.scaler, directory / SCALER_FILENAME)

    def load(self, directory: Path) -> bool:
        model_path = directory / RF_MODEL_FILENAME
        scaler_path = directory / SCALER_FILENAME
        if not model_path.exists() or not scaler_path.exists():
            return False
        self.model = joblib.load(model_path)
        self.scaler = joblib.load(scaler_path)
        return True
