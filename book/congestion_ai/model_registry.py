"""모델 버전 저장·로드·활성화."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil

from congestion_ai.constants import (
    CURRENT_VERSION_FILE,
    MAX_STORED_VERSIONS,
    METADATA_FILENAME,
    MODELS_DIR,
    VERSIONS_DIR,
)
from extensions import db
from models import ModelTrainingRun


def _version_tag() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def version_dir(version: str) -> Path:
    return VERSIONS_DIR / version


def list_versions() -> list[dict]:
    if not VERSIONS_DIR.exists():
        return []
    versions = []
    for path in sorted(VERSIONS_DIR.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        meta_path = path / METADATA_FILENAME
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        db_row = ModelTrainingRun.query.filter_by(version_tag=path.name).first() if _db_available() else None
        versions.append(
            {
                "version": path.name,
                "trained_at": meta.get("trained_at") or (db_row.trained_at.isoformat() if db_row else None),
                "data_source": meta.get("data_source"),
                "sample_count": meta.get("sample_count"),
                "is_active": bool(db_row.is_active) if db_row else path.name == get_active_version(),
                "ml_mae": meta.get("models", {}).get("ml", {}).get("metrics", {}).get("overall_test", {}).get("mae"),
                "dl_mae": meta.get("models", {}).get("dl", {}).get("metrics", {}).get("overall_test", {}).get("mae"),
            }
        )
    return versions


def get_active_version() -> str | None:
    if CURRENT_VERSION_FILE.exists():
        payload = json.loads(CURRENT_VERSION_FILE.read_text(encoding="utf-8"))
        return payload.get("version")
    legacy = MODELS_DIR / METADATA_FILENAME
    if legacy.exists():
        return "legacy"
    return None


def active_model_dir() -> Path:
    version = get_active_version()
    if version and version != "legacy":
        path = version_dir(version)
        if path.exists():
            return path
    return MODELS_DIR


def save_version(version: str, metadata: dict, rf, lstm) -> Path:
    target = version_dir(version)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    rf.save(target)
    if lstm is not None:
        lstm.save(target)
    (target / METADATA_FILENAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _prune_old_versions()
    return target


def _db_available() -> bool:
    try:
        db.session.connection()
        return True
    except RuntimeError:
        return False


def promote_version(version: str) -> bool:
    path = version_dir(version)
    if not path.exists():
        return False
    CURRENT_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_VERSION_FILE.write_text(
        json.dumps({"version": version, "promoted_at": datetime.now(UTC).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if _db_available():
        ModelTrainingRun.query.update({ModelTrainingRun.is_active: False})
        row = ModelTrainingRun.query.filter_by(version_tag=version).first()
        if row:
            row.is_active = True
        else:
            db.session.add(ModelTrainingRun(version_tag=version, data_source="unknown", is_active=True))
        db.session.commit()
    return True


def record_training_run(version: str, metadata: dict) -> None:
    if not _db_available():
        return
    ModelTrainingRun.query.filter_by(is_active=True).update({ModelTrainingRun.is_active: False})
    row = ModelTrainingRun.query.filter_by(version_tag=version).first()
    if row:
        row.data_source = str(metadata.get("data_source", ""))
        row.sample_count = int(metadata.get("sample_count", 0))
        row.operational_sample_count = int(metadata.get("operational_sample_count", 0))
        row.ml_mae = metadata.get("models", {}).get("ml", {}).get("metrics", {}).get("overall_test", {}).get("mae")
        row.dl_mae = metadata.get("models", {}).get("dl", {}).get("metrics", {}).get("overall_test", {}).get("mae")
        row.is_active = True
        row.metadata_json = json.dumps(metadata, ensure_ascii=False)
        row.trained_at = datetime.now(UTC)
    else:
        db.session.add(
            ModelTrainingRun(
                version_tag=version,
                data_source=str(metadata.get("data_source", "")),
                sample_count=int(metadata.get("sample_count", 0)),
                operational_sample_count=int(metadata.get("operational_sample_count", 0)),
                ml_mae=metadata.get("models", {}).get("ml", {}).get("metrics", {}).get("overall_test", {}).get("mae"),
                dl_mae=metadata.get("models", {}).get("dl", {}).get("metrics", {}).get("overall_test", {}).get("mae"),
                is_active=True,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                trained_at=datetime.now(UTC),
            )
        )
    db.session.commit()


def _prune_old_versions() -> None:
    if not VERSIONS_DIR.exists():
        return
    dirs = sorted([p for p in VERSIONS_DIR.iterdir() if p.is_dir()], reverse=True)
    active = get_active_version()
    for path in dirs[MAX_STORED_VERSIONS:]:
        if path.name == active:
            continue
        shutil.rmtree(path, ignore_errors=True)


def make_version_tag() -> str:
    return _version_tag()
