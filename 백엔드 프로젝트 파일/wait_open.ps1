$healthUrl = 'http://127.0.0.1:5001/api/health'
$deadline = (Get-Date).AddMinutes(8)

while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            Start-Process 'http://127.0.0.1:5001/'
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 3
    }
}

Write-Host 'Open http://127.0.0.1:5001/ manually if the browser did not open.'
