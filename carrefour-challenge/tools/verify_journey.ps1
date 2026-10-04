param([string]$Image = 'carrefour-adk-challenge:1.0.0')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$worktreeRoot = Split-Path $projectRoot -Parent
$receiptRoot = Join-Path $worktreeRoot ('.local/cf-app-03/verification-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $receiptRoot | Out-Null

function Get-Sources {
    $paths = @('src', 'tests', 'data', 'examples', 'requirements.lock',
               'requirements.txt', 'docs/cf-app-03-acceptance.md', 'tools/verify_journey.ps1')
    $manifest = [ordered]@{}
    foreach ($path in $paths) {
        $files = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot $path) -File -Recurse |
                   Where-Object { $_.FullName -notmatch '\\(__pycache__|\.pytest_cache)\\' } |
                   Sort-Object FullName)
        foreach ($file in $files) {
            $relative = [IO.Path]::GetRelativePath($projectRoot, $file.FullName).Replace('\', '/')
            $manifest[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return ($manifest | ConvertTo-Json -Depth 5 -Compress)
}

$imageId = (& docker image inspect $Image --format '{{.Id}}').Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'OWNED_DEPENDENCY_IMAGE_UNAVAILABLE' }
$before = Get-Sources
$before | Set-Content -LiteralPath (Join-Path $receiptRoot 'sources.json') -Encoding utf8
$manifestSha = (Get-FileHash -LiteralPath (Join-Path $receiptRoot 'sources.json') -Algorithm SHA256).Hash
$runs = @()
$exitStatus = 0
foreach ($round in 1..2) {
    $log = Join-Path $receiptRoot "round-$round.log"
    $proofRoot = Join-Path $receiptRoot "round-$round-proof"
    New-Item -ItemType Directory -Path $proofRoot | Out-Null
    $containerName = 'cf-app-03-' + (Split-Path $receiptRoot -Leaf) + "-$round"
    $dockerArgs = @('run', '--rm', '--name', $containerName, '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                   '--memory', '768m', '--pids-limit', '128',
                   '--tmpfs', '/tmp:rw,size=64m', '--tmpfs', '/artifacts:rw,size=16m,uid=10001,gid=10001',
                   '-e', 'CF_SEED=1234', '-e', 'CF_JOURNEY_EVIDENCE=/evidence',
                   '--mount', "type=bind,source=$proofRoot,target=/evidence")
    foreach ($directory in @('src', 'tests', 'data', 'examples')) {
        $dockerArgs += @('--mount', "type=bind,source=$(Join-Path $projectRoot $directory),target=/app/$directory,readonly")
    }
    $dockerArgs += @('--entrypoint', 'python', $imageId, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                     'tests/test_journey.py', 'tests/test_journey_gateway.py',
                     'tests/test_unit.py', 'tests/test_discovery_outcomes.py', 'tests/test_runtime_envelope.py',
                     'tests/test_journey_producer.py')
    & docker @dockerArgs *> $log
    $code = $LASTEXITCODE
    $unchanged = (Get-Sources) -ceq $before
    $summary = @(Get-Content -LiteralPath $log -Tail 8) -join "`n"
    $passed = $code -eq 0 -and $unchanged -and $summary -match '\d+ passed' -and $summary -notmatch '(\d+ skipped|\d+ failed|\d+ error)'
    $proofFiles = @(Get-ChildItem -LiteralPath $proofRoot -Filter 'producer-*.json' -File)
    if ($proofFiles.Count -ne 3) { $passed = $false }
    $runs += @{round=$round; exit_code=$code; sources_unchanged=$unchanged; passed=$passed;
               summary=$summary; log_sha256=(Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash;
               producer_proofs=@($proofFiles | ForEach-Object { @{name=$_.Name; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} })}
    Write-Output "round=$round exit=$code unchanged=$unchanged passed=$passed"
    Write-Output $summary
    if (-not $passed) { $exitStatus = 1; break }
}
$receipt = @{card='CF-APP-03'; branch=(& git -C $worktreeRoot branch --show-current);
             head=(& git -C $worktreeRoot rev-parse HEAD); image_id=$imageId; seed=1234;
             source_manifest_sha256=$manifestSha; rounds=$runs; passed=($exitStatus -eq 0 -and $runs.Count -eq 2);
             card_done=$false; merge_sha=$null; status='BLOCKED_PRODUCER_CONTRACT';
             dependency_refs=@{api=(& git -C $worktreeRoot rev-parse codex/carrefour-exec-06);
                               cli=(& git -C $worktreeRoot rev-parse codex/carrefour-exec-08)};
             scope='Domain/adapter spies; real ADK and RAG MCP SSE plus real HTTP/SQLite negative producer compatibility; SSE envelope regression';
             blockers=@('Available API lacks slots, consent request fields, CONFIRMED receipt and cancellation',
                        'Positive integrated journey and CLI unavailable', 'Independent review/merge are Central-only');
             limits=@('Negative producer compatibility is proven, not a positive booking journey',
                      'Checkpoint must be durably saved by host; no cross-process session lock',
                      'One exam per journey')}
$receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $receiptRoot 'receipt.json') -Encoding utf8
Write-Output "receipt=$receiptRoot/receipt.json"
exit $exitStatus
