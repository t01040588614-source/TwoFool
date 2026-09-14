import os
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models_store"
VERSIONS_DIR = MODELS_DIR / "versions"
CURRENT_VERSION_FILE = MODELS_DIR / "current_version.json"
METADATA_FILENAME = "metadata.json"
RF_MODEL_FILENAME = "random_forest.joblib"
LSTM_MODEL_FILENAME = "lstm_model.pt"
SCALER_FILENAME = "feature_scaler.joblib"
LSTM_SCALER_FILENAME = "lstm_scaler.joblib"

FORECAST_HORIZONS_MIN = (5, 10, 15)
PRIMARY_HORIZON_MIN = 10
SEQUENCE_LENGTH = 12
SIMULATION_INTERVAL_MIN = 5

LABEL_LOW_MAX = 0.42
LABEL_HIGH_MIN = 0.72

DATA_SOURCE_SYNTHETIC = "synthetic_simulation"
DATA_SOURCE_OPERATIONAL = "operational_timeseries"
DATA_SOURCE_MIXED = "mixed_operational_and_synthetic"

MIN_OPERATIONAL_SAMPLES = int(os.getenv("CONGESTION_MIN_OPERATIONAL_SAMPLES", "500"))
MIN_MIXED_SAMPLES = int(os.getenv("CONGESTION_MIN_MIXED_SAMPLES", "120"))
OBSERVATION_INTERVAL_SEC = int(os.getenv("CONGESTION_OBSERVATION_INTERVAL_SEC", "30"))
RETRAIN_INTERVAL_HOURS = float(os.getenv("CONGESTION_RETRAIN_HOURS", "24"))
RETRAIN_CHECK_INTERVAL_SEC = int(os.getenv("CONGESTION_RETRAIN_CHECK_SEC", "3600"))
MIN_NEW_SAMPLES_FOR_RETRAIN = int(os.getenv("CONGESTION_MIN_NEW_SAMPLES", "300"))
LAZY_MODEL_LOAD = os.getenv("CONGESTION_LAZY_LOAD", "1") != "0"
WARMUP_ON_STARTUP = os.getenv("CONGESTION_WARMUP", "0") == "1"
MAX_STORED_VERSIONS = int(os.getenv("CONGESTION_MAX_MODEL_VERSIONS", "8"))

SYNTHETIC_RULES_DOC = (
    "합성 시계열 규칙: 5분 간격 샘플, 출퇴근(07-09/18-20) 혼잡 가중, "
    "주말 0.75배, 승하차·배차간격·속도·운행상태 반영, "
    "목표값은 동일 규칙으로 5/10/15분 미래 시점 계산 + 가우시안 노ise(σ=4%). "
    "실제 철도 운행 데이터가 아닌 보조 학습용 시뮬레이션 데이터입니다."
)
