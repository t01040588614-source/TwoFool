@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  SCMAGLEV - Render 배포 (가구 사이트 방식)
echo ========================================
echo.

echo [1/3] Docker 이미지 빌드 + Hub push...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\deploy-docker.ps1" -Push
if errorlevel 1 (
    echo Docker 단계 실패. Docker Desktop Engine running 확인 후 다시 실행하세요.
    pause
    exit /b 1
)

echo.
echo [2/3] 로컬 Docker 테스트 (http://localhost:5001)...
docker rm -f scmaglev-local 2>nul
docker run -d --name scmaglev-local -p 5001:10000 ^
  -e PORT=10000 ^
  -e JWT_SECRET_KEY=local-docker-test-secret-min-32-characters ^
  -e SCMAGLEV_MAX_TRACKED_TRAINS=100 ^
  -e TOSS_PAYMENTS_MOCK_ONLY=1 ^
  gygs1090/scmaglev:latest
if errorlevel 1 (
    echo 로컬 컨테이너 시작 실패
    pause
    exit /b 1
)
echo    승객: http://localhost:5001/
echo    관제: http://localhost:5001/dashboard

echo.
echo [3/3] Render 배포...
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\deploy-render.ps1"

echo.
echo ========================================
echo  제출/공유 URL = Render Live 주소 (*.onrender.com)
echo  localhost 는 본인 테스트용
echo ========================================
pause
