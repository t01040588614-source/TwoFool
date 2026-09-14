@echo off
chcp 65001 >nul
setlocal EnableExtensions
title SCMAGLEV Flask Server
cd /d "%~dp0"

echo.
echo ========================================
echo   SCMAGLEV 로컬 서버 실행
echo   코드 위치: %~dp0
echo ========================================
echo.

if not exist "%~dp0app.py" (
  echo [ERROR] app.py 를 찾을 수 없습니다.
  echo   이 bat 파일은 book 폴더 안에서 실행해야 합니다.
  echo   경로: %~dp0app.py
  pause
  exit /b 1
)

if not exist "%~dp0templates\dashboard.html" (
  echo [ERROR] templates\dashboard.html 이 없습니다.
  echo   프로젝트 파일이 손상되었을 수 있습니다.
  pause
  exit /b 1
)

if not exist "%~dp0.env" (
  if exist "%~dp0..\.env" (
    echo [INFO] 상위 폴더 .env 사용
  ) else if exist "%~dp0..\백엔드 프로젝트 파일\.env" (
    echo [INFO] 백엔드 프로젝트 파일\.env 사용
  ) else if exist "%~dp0.env.example" (
    echo [INFO] .env 가 없어 .env.example 을 복사합니다...
    copy /Y "%~dp0.env.example" "%~dp0.env" >nul
    echo       ^(JWT_SECRET_KEY 등은 book\.env 에서 수정 가능^)
  ) else (
    echo [WARN] .env 파일이 없습니다. JWT 등 설정이 필요할 수 있습니다.
  )
)

for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr ":5001" ^| findstr "LISTENING"') do (
  echo [INFO] 기존 5001 포트 서버 종료 중... PID %%P
  taskkill /PID %%P /F >nul 2>&1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python이 PATH에 없습니다. python.org 에서 설치 후 다시 실행하세요.
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1
set FLASK_APP=app.py

python -c "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('flask') else 1)"
if errorlevel 1 (
  echo [INFO] Flask 등 의존성 설치 중...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo [ERROR] pip install 실패
    pause
    exit /b 1
  )
)

python -c "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('flask_socketio') else 1)"
if errorlevel 1 (
  echo [INFO] flask-socketio 설치 중...
  python -m pip install flask-socketio
)

python -c "import config"
if errorlevel 1 (
  echo.
  echo [ERROR] config.py 로드 실패 ^(위 Python 오류 확인^)
  echo   - book\.env 파일 확인
  echo   - 또는 ..\백엔드 프로젝트 파일\.env 확인
  echo.
  pause
  exit /b 1
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:5001/dashboard'"

echo.
echo [START] 서버 시작 중...
echo   승객 UI  : http://127.0.0.1:5001/
echo   관제센터 : http://127.0.0.1:5001/dashboard
echo.
echo   종료: 이 창에서 Ctrl+C
echo.

python -B -u "%~dp0app.py"
set EXIT_CODE=%ERRORLEVEL%

echo.
if not "%EXIT_CODE%"=="0" (
  echo [ERROR] 서버가 오류로 종료되었습니다. 코드: %EXIT_CODE%
) else (
  echo [INFO] 서버가 정상 종료되었습니다.
)
pause
exit /b %EXIT_CODE%
