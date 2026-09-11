@echo off
setlocal EnableExtensions
title SCMAGLEV Flask Server
cd /d "%~dp0"

echo.
echo ========================================
echo  SCMAGLEV Server
echo ========================================
echo.

set "PY_CMD="
set "PY_ARGS="

where python >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python"
  goto PY_OK
)

where py >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py"
  set "PY_ARGS=-3"
  goto PY_OK
)

echo [ERROR] Python not found. Install Python 3.11+ and add to PATH.
pause
exit /b 1

:PY_OK
echo [PYTHON] %PY_CMD% %PY_ARGS%

if not exist "%~dp0..\templates\index.html" (
  echo [ERROR] Missing: ..\templates\index.html
  echo         templates folder must be next to this backend folder.
  pause
  exit /b 1
)

if not exist "%~dp0.env" goto CREATE_ENV
for %%A in ("%~dp0.env") do if %%~zA==0 goto CREATE_ENV
findstr /B /C:"JWT_SECRET_KEY=" "%~dp0.env" >nul
if errorlevel 1 goto CREATE_ENV
goto ENV_OK

:CREATE_ENV
if exist "%~dp0.env.example" (
  copy /Y "%~dp0.env.example" "%~dp0.env" >nul
  echo [.env] Created from .env.example
) else (
  echo JWT_SECRET_KEY=scmaglev-local-dev-secret-change-me> "%~dp0.env"
  echo [.env] Created default .env
)
echo.

:ENV_OK
echo [CLEANUP] Stopping old server on port 5001...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
  taskkill /PID %%P /F >nul 2>&1
)
ping 127.0.0.1 -n 3 >nul

echo [INSTALL] pip install -r requirements.txt
"%PY_CMD%" %PY_ARGS% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

echo [CHECK] config import test...
"%PY_CMD%" %PY_ARGS% -c "from config import Config; assert Config.JWT_SECRET_KEY"
if errorlevel 1 (
  echo [ERROR] JWT_SECRET_KEY missing in .env
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1

if exist "%~dp0wait_open.ps1" (
  start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0wait_open.ps1"
)

echo [START] Running Flask server...
echo         First run may take 1-6 minutes for DB seed.
echo         Wait for: Running on http://127.0.0.1:5001
echo.
echo   Passenger: http://127.0.0.1:5001/
echo   Dashboard: http://127.0.0.1:5001/dashboard
echo.

"%PY_CMD%" %PY_ARGS% -B -u "%~dp0app.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Server exited with code %EXIT_CODE%
) else (
  echo Server stopped.
)
pause
exit /b %EXIT_CODE%
