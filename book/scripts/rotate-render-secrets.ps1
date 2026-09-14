# Render scmaglev — CONTROLLER_PASSWORD 회전 + 재배포
$ErrorActionPreference = "Stop"

$ServiceName = "scmaglev"
$KeyFile = Join-Path $env:LOCALAPPDATA "scmaglev-render-api.key"
$SecretFile = Join-Path $env:LOCALAPPDATA "scmaglev-controller-password.txt"

$ApiKey = $env:RENDER_API_KEY
if (-not $ApiKey -and (Test-Path $KeyFile)) {
    $ApiKey = (Get-Content $KeyFile -Raw).Trim()
}
if (-not $ApiKey) {
    Write-Host "Render API Key 가 없습니다: $KeyFile" -ForegroundColor Red
    exit 1
}

$headers = @{
    Authorization  = "Bearer $ApiKey"
    Accept         = "application/json"
    "Content-Type" = "application/json"
}

function Invoke-RenderApi {
    param([string]$Method, [string]$Uri, [string]$Body = $null)
    if ($Body) {
        return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method -Body $Body
    }
    return Invoke-RestMethod -Uri $Uri -Headers $headers -Method $Method
}

$servicesRaw = Invoke-RenderApi -Method Get -Uri "https://api.render.com/v1/services?limit=50"
$serviceList = @($servicesRaw | ForEach-Object { $_.service })
$service = $serviceList | Where-Object { $_.name -eq $ServiceName -or $_.name -like "scmaglev*" } | Select-Object -First 1
if (-not $service) {
    Write-Host "Render 서비스 '$ServiceName' 를 찾을 수 없습니다." -ForegroundColor Red
    exit 1
}

$serviceId = $service.id
$passwordBytes = New-Object byte[] 24
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($passwordBytes)
$newPassword = [Convert]::ToBase64String($passwordBytes)

Write-Host ">>> CONTROLLER_PASSWORD 회전 (service: $($service.name))"
$body = @{ value = $newPassword } | ConvertTo-Json
Invoke-RenderApi -Method Put -Uri "https://api.render.com/v1/services/$serviceId/env-vars/CONTROLLER_PASSWORD" -Body $body | Out-Null

$userBody = @{ value = "gygs1010" } | ConvertTo-Json
Invoke-RenderApi -Method Put -Uri "https://api.render.com/v1/services/$serviceId/env-vars/CONTROLLER_USERNAME" -Body $userBody | Out-Null

Set-Content -Path $SecretFile -Value @(
    "Render service: $($service.name)"
    "URL: $($service.serviceDetails.url)"
    "CONTROLLER_USERNAME=gygs1010"
    "CONTROLLER_PASSWORD=$newPassword"
    "Saved: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
) -Encoding UTF8

Write-Host ">>> 재배포 트리거..."
Invoke-RenderApi -Method Post -Uri "https://api.render.com/v1/services/$serviceId/deploys" -Body "{}" | Out-Null

Write-Host ""
Write-Host "관제 비밀번호 저장:" -ForegroundColor Green
Write-Host "  $SecretFile"
Write-Host "  gygs1010 / $newPassword"
Write-Host ""
Write-Host "GitHub·코드에는 저장하지 마세요." -ForegroundColor Yellow
