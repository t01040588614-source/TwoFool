@echo off
chcp 65001 >nul
setlocal
title SCMAGLEV Flask Server
cd /d "%~dp0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
  echo Stopping the existing server on port 5001...
  taskkill /PID %%P /F >nul 2>&1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python이 PATH에 없습니다. Python 설치 후 다시 실행하세요.
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1

python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('flask_socketio') else 1)"
if errorlevel 1 (
  echo Installing missing dependencies for realtime dashboard...
  python -m pip install -r "%~dp0requirements.txt"
)

python -c "import config"
if errorlevel 1 (
  echo.
  echo [ERROR] 환경변수 로드 실패 ^(위 Python 오류 참고^)
  echo   - book\.env 파일을 만들거나
  echo   - 백엔드 프로젝트 파일\.env 가 있는지 확인하세요.
  echo.
  pause
  exit /b 1
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5001'"

echo.
echo Starting SCMAGLEV server...
echo   Passenger : http://127.0.0.1:5001/
echo   Dashboard : http://127.0.0.1:5001/dashboard
echo.
python -B -u "%~dp0app.py"

echo.
echo The server has stopped.
pause
