[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Evidence)
$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\RAG-Local'
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $Evidence 'receipt.json')) { throw 'FRESH_EVIDENCE_REQUIRED' }
$sourceNames = @('app/advanced/dependencies/requirements.in','app/advanced/dependencies/requirements.lock',
    'app/advanced/dependencies/compile-lock.ps1','app/advanced/containers/Dockerfile',
    'app/advanced/dependencies/dependency_checks.py','app/advanced/dependencies/verify-dependencies.ps1',
    'app/advanced/models/free_model.py','app/advanced/tasks/task_lab.py','app/advanced/evaluation/judge_lab.py')
function Snapshot {
    $items = [ordered]@{}
    foreach ($name in $sourceNames) {
        $items[$name] = (Get-FileHash -LiteralPath (Join-Path $taskRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $items
}
$frozen = Snapshot
$image = & docker image inspect rag-local-advanced:0.1.0 --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or $image -notmatch '^sha256:') { throw 'MISSING_IMAGE' }
$rounds = @()
for ($round=1; $round -le 2; $round++) {
    $sdkLog = Join-Path $Evidence "round-$round-sdk.log"
    & docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges `
        --tmpfs /tmp:size=128m,mode=1777 --tmpfs /home/rag:size=16m,mode=1777 `
        --memory 768m --cpus 1 --pids-limit 128 --entrypoint sh $image `
        -c 'python -m pip check && python -m dependency_checks' *> $sdkLog
    if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $sdkLog -Tail 10; throw 'SDK_DEPENDENCY_CHECK_FAILED' }
    $payload = Get-Content -LiteralPath $sdkLog | Where-Object { $_.StartsWith('{') } | Select-Object -Last 1 | ConvertFrom-Json
    if (-not $payload.passed -or $payload.model_calls -ne 0 -or $payload.checks.Count -ne 6) { throw 'SDK_PROOF_INVALID' }
    $compilerLog = Join-Path $Evidence "round-$round-compiler.log"
    & docker run --rm --entrypoint sh rag-local-backend:0.1.0 -c `
        'python -m pip install --quiet --target /tmp/compiler pip-tools==7.5.2 pip==25.1.1 && PYTHONPATH=/tmp/compiler python -m piptools compile --help && PYTHONPATH=/tmp/compiler python -c "import pip,piptools; from pip._internal.utils.compat import stdlib_pkgs; print(pip.__version__)"' *> $compilerLog
    if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $compilerLog -Tail 10; throw 'PINNED_COMPILER_FAILED' }
    if ((Get-Content -LiteralPath $compilerLog | Select-Object -Last 1).Trim() -ne '25.1.1') { throw 'WRONG_COMPILER_PIP' }
    $after = Snapshot
    if (($after | ConvertTo-Json -Compress) -ne ($frozen | ConvertTo-Json -Compress)) { throw 'SOURCE_CHANGED_RESET_STREAK' }
    $rounds += @{number=$round;sdk=$payload;compiler_exit_code=0;sdk_log=(Split-Path -Leaf $sdkLog);compiler_log=(Split-Path -Leaf $compilerLog)}
    Write-Output "Dependency regression round $round passed; no model calls."
}
$receipt = @{complete=$true;consecutive_passes=2;rounds=$rounds;sources_sha256=$frozen;image_id=$image;
    proof_boundary='offline SDK and pinned compiler regression; not DeepAgents task execution or judge calibration'}
$receipt | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath (Join-Path $Evidence 'receipt.json') -Encoding utf8
