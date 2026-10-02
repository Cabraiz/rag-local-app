$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath 'D:\RAG-Local\app'

# Acceptance: preserve volumes and credentials, start the existing local stack,
# verify actual HTTP readiness, and survive the end of the Codex turn.
# Never start the optional advanced/QA profiles or call a remote model here.
$composeProfiles = @(
    'infrastructure/compose/runtime/compose.yaml',
    'infrastructure/compose/runtime/compose.retrieval.yaml',
    'infrastructure/compose/runtime/compose.semantic.yaml',
    'infrastructure/compose/runtime/compose.integrations.yaml',
    'infrastructure/compose/labs/compose.gemini-rag.yaml',
    'infrastructure/compose/runtime/compose.documents.yaml',
    'infrastructure/compose/runtime/compose.delivery.yaml',
    'infrastructure/compose/observability/compose.observability.yaml',
    'infrastructure/compose/resilience/compose.resilience.yaml',
    'infrastructure/compose/resilience/compose.resilience-integrations.yaml',
    'infrastructure/compose/observability/compose.grafana.yaml'
)
$composeArguments = @('compose', '--project-directory', 'D:\RAG-Local\app', '--ansi', 'never', '--progress', 'plain')
foreach ($profile in $composeProfiles) {
    $profilePath = Join-Path 'D:\RAG-Local\app' $profile
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw "Missing runtime profile: $profile"
    }
    $composeArguments += @('-f', $profilePath)
}
& 'D:\Docker\App\resources\bin\docker.exe' @composeArguments up -d --no-build --wait --wait-timeout 480
if ($LASTEXITCODE -ne 0) { throw 'RAG local runtime startup failed; inspect container health.' }

# When explicitly registered, Windows Task Scheduler owns this supervisor.
# Docker restart policies already own the
# long-lived containers. A slow health request must not trigger a global restart.
while ($true) {
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:8840/health/ready' -TimeoutSec 5
    } catch {
        Write-Warning 'RAG readiness unavailable; no automatic destructive recovery was performed.'
    }
    Start-Sleep -Seconds 30
}
