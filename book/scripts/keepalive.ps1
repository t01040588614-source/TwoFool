# Render 무료 플랜 슬립 방지 — 14분마다 실행 권장 (작업 스케줄러 / cron)
$uri = if ($env:SCMAGLEV_URL) { $env:SCMAGLEV_URL.TrimEnd('/') } else { "https://scmaglev.onrender.com" }
try {
    Invoke-WebRequest -Uri "$uri/ping" -UseBasicParsing -TimeoutSec 30 | Out-Null
    Write-Host "keepalive ok: $uri/ping"
} catch {
    Write-Host "keepalive fail: $($_.Exception.Message)"
    exit 1
}
