# SCMAGLEV — Render Existing Image 배포 (가구 Mood Code 와 동일 방식)
# 사용: .\scripts\deploy-render.ps1

$ErrorActionPreference = "Stop"

$ServiceName = "scmaglev"
$ImagePath = "docker.io/gygs1090/scmaglev:latest"
$KeyFile = Join-Path $env:LOCALAPPDATA "scmaglev-render-api.key"
$HookFile = Join-Path $env:LOCALAPPDATA "scmaglev-render-deploy-hook.txt"
$MoodKeyFile = Join-Path $env:LOCALAPPDATA "mood-code-render-api.key"
$MoodHookFile = Join-Path $env:LOCALAPPDATA "mood-code-render-deploy-hook.txt"

function Invoke-DeployHook {
    param([string]$HookUrl)
    Write-Host ">>> Deploy Hook으로 재배포 트리거..."
    Invoke-RestMethod -Uri $HookUrl.Trim() -Method Post | Out-Null
    Write-Host ">>> Deploy Hook 전송 완료" -ForegroundColor Green
    Write-Host ""
    Write-Host "2~3분 후 확인:" -ForegroundColor Green
    Write-Host "  https://scmaglev-latest.onrender.com/"
    Write-Host "  https://scmaglev-latest.onrender.com/dashboard"
    exit 0
}

if ($env:RENDER_DEPLOY_HOOK) {
    Invoke-DeployHook -HookUrl $env:RENDER_DEPLOY_HOOK
}

foreach ($hookPath in @($HookFile, $MoodHookFile)) {
    if (Test-Path $hookPath) {
        $hook = (Get-Content $hookPath -Raw).Trim()
        if ($hook -like "https://api.render.com/deploy/*") {
            Invoke-DeployHook -HookUrl $hook
        }
    }
}

$ApiKey = $env:RENDER_API_KEY
if ($ApiKey) { $ApiKey = $ApiKey.Trim() } else { $ApiKey = "" }

foreach ($keyPath in @($KeyFile, $MoodKeyFile)) {
    if (-not $ApiKey -and (Test-Path $keyPath)) {
        $ApiKey = (Get-Content $keyPath -Raw).Trim()
        break
    }
}

if (-not $ApiKey) {
    Write-Host ""
    Write-Host "Render API Key 가 필요합니다." -ForegroundColor Yellow
    Write-Host "만드는 곳: https://dashboard.render.com/u/settings?add-api-key"
    Write-Host "  Create API Key -> rnd_ 로 시작하는 키 복사"
    Write-Host ""
    $ApiKey = (Read-Host "API Key 붙여넣기").Trim()
    if ($ApiKey) {
        Set-Content -Path $KeyFile -Value $ApiKey -NoNewline
    }
}

if (-not $ApiKey) {
    Write-Host "API Key 가 비어 있습니다." -ForegroundColor Red
    exit 1
}

if ($ApiKey -like "dckr_pat_*") {
    Write-Host "Docker Hub 토큰이 입력됐습니다. Render API Key (rnd_...) 가 필요합니다." -ForegroundColor Red
    exit 1
}

$headers = @{
    Authorization  = "Bearer $ApiKey"
    Accept         = "application/json"
    "Content-Type" = "application/json"
}

function Invoke-RenderApi {
    param(
        [string]$Method,
        [string]$Uri,
        [string]$Body = $null
    )
    try {
        if ($Body) {
            return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method -Body $Body
        }
        return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 401) {
            Write-Host "Unauthorized — API Key 가 틀렸습니다." -ForegroundColor Red
            exit 1
        }
        throw
    }
}

Write-Host ">>> Render 워크스페이스 조회..."
$ownersRaw = Invoke-RenderApi -Method Get -Uri "https://api.render.com/v1/owners?limit=20"
$ownerEntry = if ($ownersRaw -is [array]) { $ownersRaw[0] } else { $ownersRaw }
$ownerId = $ownerEntry.owner.id
if (-not $ownerId) {
    Write-Host "워크스페이스 ID를 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

Write-Host ">>> 기존 서비스 확인..."
$servicesRaw = Invoke-RenderApi -Method Get -Uri "https://api.render.com/v1/services?limit=50"
$serviceList = @($servicesRaw | ForEach-Object { $_.service })
$existing = $serviceList | Where-Object { $_.name -eq $ServiceName -or $_.name -like "scmaglev*" } | Select-Object -First 1

if ($existing) {
    $serviceId = $existing.id
    $url = $existing.serviceDetails.url
    Write-Host ">>> 이미 있음 — 재배포: $($existing.name)"
    try {
        Invoke-RenderApi -Method Post -Uri "https://api.render.com/v1/services/$serviceId/deploys" -Body "{}" | Out-Null
    } catch {
        Write-Host "    (재배포 트리거 생략 — 대시보드에서 Manual Deploy 가능)"
    }
    Write-Host ""
    Write-Host "과제/팀 공유 URL:" -ForegroundColor Green
    Write-Host "  $url"
    Write-Host "  $url/dashboard"
    Write-Host "Health: $url/api/health"
    exit 0
}

Write-Host ">>> 새 Web Service 생성 (Existing Image)..."
$controllerPasswordBytes = New-Object byte[] 24
$controllerPasswordRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$controllerPasswordRng.GetBytes($controllerPasswordBytes)
$generatedControllerPassword = [System.Convert]::ToBase64String($controllerPasswordBytes)

$body = @{
    type    = "web_service"
    name    = $ServiceName
    ownerId = $ownerId
    image   = @{
        ownerId   = $ownerId
        imagePath = $ImagePath
    }
    envVars = @(
        @{ key = "JWT_SECRET_KEY"; value = "scmaglev-render-jwt-secret-change-me-32chars" }
        @{ key = "CONTROLLER_USERNAME"; value = "gygs1010" }
        @{ key = "CONTROLLER_PASSWORD"; value = $generatedControllerPassword }
        @{ key = "SCMAGLEV_MAX_TRACKED_TRAINS"; value = "200" }
        @{ key = "TOSS_PAYMENTS_MOCK_ONLY"; value = "1" }
        @{ key = "OPENAI_ENABLED"; value = "auto" }
    )
    serviceDetails = @{
        runtime         = "docker"
        plan            = "free"
        region          = "singapore"
        healthCheckPath = "/api/health"
    }
} | ConvertTo-Json -Depth 6

try {
    $result = Invoke-RenderApi -Method Post -Uri "https://api.render.com/v1/services" -Body $body
} catch {
    Write-Host "Render 서비스 생성 실패:" -ForegroundColor Red
    Write-Host $_.ErrorDetails.Message
    exit 1
}

$url = $result.service.serviceDetails.url
Write-Host ""
Write-Host "배포 시작됨! 2~5분 후 접속하세요." -ForegroundColor Green
Write-Host "승객 URL: $url" -ForegroundColor Green
Write-Host "관제 URL: $url/dashboard" -ForegroundColor Green
Write-Host "Health: $url/api/health"
