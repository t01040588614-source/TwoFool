"""팀원 배포용 ZIP 생성 — templates·AI 포함 백엔드 폴더 단일 패키지."""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "백엔드 프로젝트 파일"
DESKTOP = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
ZIP_PATH = DESKTOP / "SCMAGLEV_기차프로젝트.zip"

EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
EXCLUDE_FILES = {".env", "app.db", "app.db-shm", "app.db-wal"}


def sync_templates() -> None:
    src = ROOT / "templates"
    dest = BACKEND / "templates"
    if not src.is_dir():
        raise SystemExit(f"templates 폴더 없음: {src}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def should_skip(path: Path) -> bool:
    if set(path.parts) & EXCLUDE_DIRS:
        return True
    if path.suffix == ".pyc":
        return True
    if path.name in EXCLUDE_FILES:
        return True
    return False


def main() -> None:
    sync_templates()
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    root_items = [
        ROOT / "SCMAGLEV_발표용.txt",
        ROOT / "SCMAGLEV_프로젝트_분석요약.md",
        ROOT / "팀원_실행안내.txt",
        ROOT / "AI_연동안내.txt",
        BACKEND,
    ]

    count = 0
    with zipfile.ZipFile(
        ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
    ) as zf:
        for item in root_items:
            if not item.exists():
                continue
            if item.is_file():
                zf.write(item, item.name)
                count += 1
                continue
            for path in item.rglob("*"):
                if should_skip(path) or path.is_dir():
                    continue
                arc = str(path.relative_to(ROOT)).replace("\\", "/")
                try:
                    zf.write(path, arc)
                    count += 1
                except PermissionError:
                    pass

    ai_templates = [
        n
        for n in zipfile.ZipFile(ZIP_PATH).namelist()
        if "templates/" in n and n.endswith(".html")
    ]
    ai_py = [
        n for n in zipfile.ZipFile(ZIP_PATH).namelist() if "openai_" in n
    ]
    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"ZIP: {ZIP_PATH}")
    print(f"files={count} size_mb={size_mb:.2f}")
    print(f"templates={ai_templates}")
    print(f"openai={ai_py}")


if __name__ == "__main__":
    main()
