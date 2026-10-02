param(
    [Parameter(Mandatory=$true)][string]$QaReceipt,
    [Parameter(Mandatory=$true)][string]$LoadReceipt
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Set-Location -LiteralPath 'D:\RAG-Local\app'
$qa=Get-Content -LiteralPath $QaReceipt -Raw | ConvertFrom-Json
$load=Get-Content -LiteralPath $LoadReceipt -Raw | ConvertFrom-Json
if(-not $qa.passed -or $qa.rounds.Count -ne 2 -or -not $load.passed -or $load.offered -ne 100000){
    throw 'Two clean scoped rounds and the offered-load gate are required.'
}
foreach($receipt in @($qa,$load)){
    foreach($entry in $receipt.source_hashes.PSObject.Properties){
        $path=Join-Path 'D:\RAG-Local' $entry.Name
        if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value){
            throw "Source changed after proof: $($entry.Name)"
        }
    }
}
$image=& docker image inspect rag-local-resilience:0.1.0 --format '{{.Id}}'
if($LASTEXITCODE -ne 0 -or $image -notin $qa.images -or $image -notin $load.images){
    throw 'Backend candidate differs from the tested image.'
}
$profiles=@('infrastructure/compose/runtime/compose.yaml','infrastructure/compose/runtime/compose.retrieval.yaml','infrastructure/compose/runtime/compose.semantic.yaml',
    'infrastructure/compose/runtime/compose.integrations.yaml','infrastructure/compose/labs/compose.gemini-rag.yaml','infrastructure/compose/runtime/compose.documents.yaml',
    'infrastructure/compose/runtime/compose.delivery.yaml','infrastructure/compose/observability/compose.observability.yaml','infrastructure/compose/resilience/compose.resilience.yaml',
    'infrastructure/compose/resilience/compose.resilience-integrations.yaml','infrastructure/compose/observability/compose.grafana.yaml')
$arguments=@('compose', '--project-directory', 'D:\RAG-Local\app','--ansi','never','--progress','plain')
foreach($profile in $profiles){$arguments+=@('-f',$profile)}
# Quiesce old writers before one-time budget initialization. Never remove volumes.
& docker @arguments stop --timeout 45 api worker control delivery integration-gateway
if($LASTEXITCODE -ne 0){throw 'Quiescence failed; migration not started.'}
& docker @arguments up -d --no-build --wait --wait-timeout 480
if($LASTEXITCODE -ne 0){throw 'Local rollout not ready; inspect without deleting data.'}
# Nginx resolves its upstream at startup: refresh after the API container changes.
& docker @arguments restart --no-deps frontend
if($LASTEXITCODE -ne 0){throw 'Frontend restart failed.'}
$end=[DateTime]::UtcNow.AddSeconds(90)
$ready=$false
while([DateTime]::UtcNow -lt $end){
    try {
        $status=Invoke-RestMethod 'http://127.0.0.1:8840/health/ready' -TimeoutSec 5
        if($status.admission_policy -eq 'partitioned_count_bytes_v3'){$ready=$true;break}
    } catch {}
    Start-Sleep -Seconds 2
}
if(-not $ready){throw 'Actual frontend/API readiness was not proven.'}
Write-Output 'local_resilience_rollout_ready=True'
