# SCMAGLEV — Docker 이미지 빌드 (Render Existing Image용)
# 사용:
#   .\scripts\deploy-docker.ps1              # 로컬 빌드
#   .\scripts\deploy-docker.ps1 -Push        # Docker Hub push
#   $env:DOCKERHUB_TOKEN="dckr_pat_..." ; .\scripts\deploy-docker.ps1 -Push

param(
    [switch]$Push
)

$ErrorActionPreference = "Stop"
$DockerHubUser = "gygs1090"
$ImageName = "${DockerHubUser}/scmaglev:latest"

$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if (Test-Path $dockerBin) {
    $env:Path = "$dockerBin;$env:Path"
}

$BackendRoot = Split-Path $PSScriptRoot -Parent
$RepoRoot = Split-Path $BackendRoot -Parent
Set-Location $BackendRoot

# 루트 templates → 백엔드 templates 동기화
$srcTemplates = Join-Path $RepoRoot "templates"
$destTemplates = Join-Path $BackendRoot "templates"
if (Test-Path $srcTemplates) {
    Write-Host ">>> templates 동기화..."
    if (Test-Path $destTemplates) { Remove-Item $destTemplates -Recurse -Force }
    Copy-Item $srcTemplates $destTemplates -Recurse -Force
}

Write-Host ">>> Docker engine 확인..."
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop을 실행하고 Engine running 상태가 될 때까지 기다린 뒤 다시 실행하세요." -ForegroundColor Red
    exit 1
}

function Test-LocalImage {
    docker image inspect $ImageName 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

function Invoke-DockerHubLogin {
    if ($env:DOCKERHUB_TOKEN) {
        Write-Host ">>> Docker Hub 로그인 (토큰)..."
        $env:DOCKERHUB_TOKEN | docker login -u $DockerHubUser --password-stdin 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Docker Hub 토큰 로그인 실패" }
        return
    }
    Write-Host ">>> Docker Hub 로그인 (gygs1090)..."
    docker login -u $DockerHubUser
    if ($LASTEXITCODE -ne 0) { throw "Docker Hub 로그인 실패" }
}

if (-not (Test-LocalImage) -or $Push) {
    Write-Host ">>> 빌드: $ImageName"
    docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) { exit 1 }
} else {
    Write-Host ">>> 로컬 이미지 OK: $ImageName (재빌드: -Push)"
}

if ($Push) {
    try {
        Invoke-DockerHubLogin
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        exit 1
    }
    Write-Host ">>> Docker Hub push..."
    docker push $ImageName
    if ($LASTEXITCODE -ne 0) { exit 1 }
    Write-Host ">>> Docker Hub push 완료" -ForegroundColor Green
} else {
    Write-Host ">>> Docker Hub push 생략 (재업로드: -Push)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Render Existing Image URL:" -ForegroundColor Green
Write-Host "  docker.io/$ImageName"
Write-Host ""
Write-Host "Render 환경 변수:" -ForegroundColor Yellow
Write-Host "  JWT_SECRET_KEY=(Generate Secret)"
Write-Host "  SCMAGLEV_MAX_TRACKED_TRAINS=200"
Write-Host "  TOSS_PAYMENTS_MOCK_ONLY=1"
Write-Host "  OPENAI_API_KEY=(선택)"
