from __future__ import annotations

import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.preprocessing import StandardScaler
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from congestion_ai.constants import FORECAST_HORIZONS_MIN, LSTM_MODEL_FILENAME, LSTM_SCALER_FILENAME, SEQUENCE_LENGTH
from congestion_ai.features import ML_FEATURE_COLUMNS, build_lstm_sequences
from congestion_ai.metrics import horizon_metrics, regression_metrics


class CongestionLSTM(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 128, output_size: int = 3) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            batch_first=True,
            num_layers=2,
            dropout=0.2,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 64),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(64, output_size),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(x)
        return self.head(output[:, -1, :])


class LSTMCongestionModel:
    FRAMEWORK = "PyTorch"
    MODEL_NAME = "LSTM"

    def __init__(self) -> None:
        self.model: CongestionLSTM | None = None
        self.scaler: StandardScaler | None = None
        self.metrics: dict = {}
        self.input_size = len(ML_FEATURE_COLUMNS)
        self.training_profile: dict = {}

    def _scale_sequences(self, sequences: np.ndarray, *, fit: bool = False) -> np.ndarray:
        if self.scaler is None:
            self.scaler = StandardScaler()
        flat = sequences.reshape(-1, sequences.shape[-1])
        if fit:
            scaled = self.scaler.fit_transform(flat)
        else:
            scaled = self.scaler.transform(flat)
        return scaled.reshape(sequences.shape).astype(np.float32)

    def train(self, frame, *, test_ratio: float = 0.2) -> dict:
        ordered = frame.sort_values("timestamp_min").reset_index(drop=True)
        split_idx = int(len(ordered) * (1.0 - test_ratio))
        train_frame = ordered.iloc[:split_idx]
        test_frame = ordered.iloc[split_idx:]
        x_train, y_train = build_lstm_sequences(train_frame)
        x_test, y_test = build_lstm_sequences(test_frame)
        if len(x_train) < 48:
            raise RuntimeError("LSTM training requires more sequence samples.")

        x_train = self._scale_sequences(x_train, fit=True)
        x_test = self._scale_sequences(x_test, fit=False)

        val_size = max(16, int(len(x_train) * 0.15))
        x_val, y_val = x_train[-val_size:], y_train[-val_size:]
        x_fit, y_fit = x_train[:-val_size], y_train[:-val_size]

        hidden_size = 64 if os.getenv("PYTEST_CURRENT_TEST") else 128
        max_epochs = 8 if os.getenv("PYTEST_CURRENT_TEST") else 60
        patience = 3 if os.getenv("PYTEST_CURRENT_TEST") else 8
        batch_size = 64

        device = torch.device("cpu")
        self.model = CongestionLSTM(input_size=self.input_size, hidden_size=hidden_size).to(device)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.001, weight_decay=1e-5)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=3)
        loss_fn = nn.MSELoss()

        train_loader = DataLoader(
            TensorDataset(torch.from_numpy(x_fit), torch.from_numpy(y_fit)),
            batch_size=batch_size,
            shuffle=True,
        )

        best_val = float("inf")
        best_state = None
        stale_epochs = 0
        history_loss: list[float] = []

        for epoch in range(max_epochs):
            self.model.train()
            epoch_loss = 0.0
            batches = 0
            for batch_x, batch_y in train_loader:
                batch_x = batch_x.to(device)
                batch_y = batch_y.to(device)
                optimizer.zero_grad()
                pred = self.model(batch_x)
                loss = loss_fn(pred, batch_y)
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()
                epoch_loss += float(loss.item())
                batches += 1

            self.model.eval()
            with torch.no_grad():
                val_pred = self.model(torch.from_numpy(x_val).to(device)).cpu().numpy()
                val_loss = float(np.mean((val_pred - y_val) ** 2))
            scheduler.step(val_loss)
            history_loss.append(val_loss)

            if val_loss + 1e-5 < best_val:
                best_val = val_loss
                best_state = {k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()}
                stale_epochs = 0
            else:
                stale_epochs += 1
                if stale_epochs >= patience:
                    break

        if best_state is not None:
            self.model.load_state_dict(best_state)

        self.model.eval()
        with torch.no_grad():
            train_pred = self.model(torch.from_numpy(x_train)).cpu().numpy()
            test_pred = self.model(torch.from_numpy(x_test)).cpu().numpy()

        horizon_labels = [f"{h}m" for h in FORECAST_HORIZONS_MIN]
        self.metrics = {
            "train": horizon_metrics(y_train, train_pred, horizon_labels),
            "test": horizon_metrics(y_test, test_pred, horizon_labels),
            "overall_test": regression_metrics(y_test.reshape(-1), test_pred.reshape(-1)),
            "sequence_length": SEQUENCE_LENGTH,
            "input_features": ML_FEATURE_COLUMNS,
        }
        self.training_profile = {
            "epochs_ran": len(history_loss),
            "best_val_loss": round(best_val, 6),
            "early_stopping_patience": patience,
            "hidden_size": hidden_size,
            "optimizer": "Adam",
            "scheduler": "ReduceLROnPlateau",
        }
        self.metrics["training_profile"] = self.training_profile
        return self.metrics

    def predict(self, sequence: np.ndarray) -> np.ndarray:
        if self.model is None or self.scaler is None:
            raise RuntimeError("LSTM model is not trained.")
        scaled = self._scale_sequences(sequence.reshape(1, SEQUENCE_LENGTH, -1), fit=False)
        self.model.eval()
        with torch.no_grad():
            tensor = torch.from_numpy(scaled)
            pred = self.model(tensor).cpu().numpy()[0]
        return np.clip(pred, 0.05, 0.98)

    def save(self, directory: Path) -> None:
        if self.model is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.model.state_dict(),
                "input_size": self.input_size,
                "training_profile": self.training_profile,
            },
            directory / LSTM_MODEL_FILENAME,
        )
        if self.scaler is not None:
            joblib.dump(self.scaler, directory / LSTM_SCALER_FILENAME)

    def load(self, directory: Path) -> bool:
        path = directory / LSTM_MODEL_FILENAME
        scaler_path = directory / LSTM_SCALER_FILENAME
        if not path.exists() or not scaler_path.exists():
            return False
        payload = torch.load(path, map_location="cpu", weights_only=False)
        self.input_size = int(payload["input_size"])
        self.training_profile = payload.get("training_profile", {})
        hidden_size = int(self.training_profile.get("hidden_size", 128))
        self.model = CongestionLSTM(input_size=self.input_size, hidden_size=hidden_size)
        self.model.load_state_dict(payload["state_dict"])
        self.model.eval()
        self.scaler = joblib.load(scaler_path)
        return True
