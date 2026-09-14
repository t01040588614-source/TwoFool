# GitHub push + PR 생성 (t01040588614-source/TwoFool)
# 사용: PowerShell에서 book 폴더 기준
#   gh auth login   ← 최초 1회 (t01040588614-source 계정)
#   .\scripts\push_github_pr.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $RepoRoot

Write-Host ">>> GitHub CLI 인증 확인..."
gh auth status
if ($LASTEXITCODE -ne 0) {
    Write-Host "gh auth login 실행 후 t01040588614-source 계정으로 로그인하세요." -ForegroundColor Yellow
    gh auth login
}

$branch = git branch --show-current
Write-Host ">>> Push branch: $branch"
git push -u origin $branch

Write-Host ">>> PR 생성 (base: main)..."
gh pr create --base main --head $branch --title "SCMAGLEV: congestion AI, ACK fixes, dashboard live forecast" --body @"
## Summary
- Production congestion AI (Random Forest + LSTM ensemble, operational DB pipeline)
- Fix ACK / fault recovery under SQLite lock (write lock + retry)
- Dashboard forecast panel and AI cards unified via applyForecastState
- Improved run_server.bat and Render blueprint env

## Test plan
- [ ] Local: run_server.bat → /dashboard login + ACK + fault clear
- [ ] Live forecast updates every 5s with train selected
- [ ] Render deploy after merge
"@

Write-Host ">>> 완료. PR URL은 위 출력을 확인하세요."
