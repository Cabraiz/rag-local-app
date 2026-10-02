$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\RAG-Local\app'
& 'D:\Docker\App\resources\bin\docker.exe' compose --project-directory 'D:\RAG-Local\app' -f infrastructure/compose/runtime/compose.yaml up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw 'RAG compose startup failed' }
# Task Scheduler owns this process; Docker restart policies own the containers.
# Do not recreate/restart containers just because an HTTP health request is slow.
while ($true) {
  try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8840/health/ready' -TimeoutSec 5 }
  catch { Write-Warning 'RAG health unavailable; check project-specific container logs' }
  Start-Sleep -Seconds 30
}
