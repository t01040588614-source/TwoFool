@echo off
setlocal
title My Board Flask Server
cd /d "%~dp0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
  echo Stopping the existing server on port 5001...
  taskkill /PID %%P /F >nul 2>&1
)

set PYTHONUNBUFFERED=1
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:5001'"

python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('flask_socketio') else 1)"
if errorlevel 1 (
  echo Installing missing dependencies for realtime dashboard...
  python -m pip install -r "%~dp0requirements.txt"
)

echo.
echo Starting My Board server...
echo Open http://127.0.0.1:5001 in your browser.
echo Recovery codes are sent by email.
echo.
python -B -u "%~dp0app.py"

echo.
echo The server has stopped.
pause
