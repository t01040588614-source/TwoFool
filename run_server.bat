@echo off
chcp 65001 >nul
setlocal EnableExtensions
title SCMAGLEV - 로컬 서버 실행
cd /d "%~dp0"

echo.
echo ========================================
echo   SCMAGLEV 프로젝트 런처
echo   루트: %~dp0
echo ========================================
echo.

if not exist "%~dp0book\app.py" (
  echo [ERROR] book\app.py 가 없습니다.
  echo.
  echo   실제 Python 코드는 book 폴더에 있습니다:
  echo     %~dp0book\
  echo.
  echo   book\app.py
  echo     book\templates\dashboard.html
  echo     book\congestion_ai\
  echo.
  pause
  exit /b 1
)

if not exist "%~dp0book\run_server.bat" (
  echo [ERROR] book\run_server.bat 이 없습니다.
  pause
  exit /b 1
)

echo [INFO] book\run_server.bat 실행...
echo.

call "%~dp0book\run_server.bat"
exit /b %ERRORLEVEL%
