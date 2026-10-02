$ErrorActionPreference = 'Stop'
Set-Location 'D:\RAG-Local\app'
& 'D:\Docker\App\resources\bin\docker.exe' compose --project-directory 'D:\RAG-Local\app' -f infrastructure/compose/runtime/compose.yaml -f infrastructure/compose/runtime/compose.retrieval.yaml -f infrastructure/compose/runtime/compose.semantic.yaml up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw 'Demo Compose startup failed' }
while ($true) {
  try { Invoke-RestMethod 'http://127.0.0.1:8840/health/ready' -TimeoutSec 5 | Out-Null }
  catch { Write-Warning 'Local demo health unavailable; Docker restart policies remain active.' }
  Start-Sleep -Seconds 30
}
