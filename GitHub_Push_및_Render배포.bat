@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ========================================
echo   GitHub Push + PR / Render 배포
echo ========================================
echo.
echo [1] GitHub (t01040588614-source 계정 필요)
echo     gh auth login  ^(최초 1회^)
echo     powershell -File book\scripts\push_github_pr.ps1
echo.
echo [2] Render Docker 배포 ^(GitHub 없이^)
echo     Docker Desktop 실행 후
echo     book\실행_Render배포.bat
echo.
echo 현재 커밋:
git log -1 --oneline
echo.
set /p CHOICE="GitHub PR 시도? (Y/N): "
if /I "%CHOICE%"=="Y" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0book\scripts\push_github_pr.ps1"
)
echo.
set /p RENDER="Render Docker 배포 시도? (Y/N): "
if /I "%RENDER%"=="Y" (
  call "%~dp0book\실행_Render배포.bat"
)
pause
