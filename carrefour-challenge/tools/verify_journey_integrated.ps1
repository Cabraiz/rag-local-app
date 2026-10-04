param([string]$Image = 'carrefour-adk-challenge:1.0.0',
      [string]$ProducerRef = '5f13c25c4d1fb86bf88bb26f97a7a2e75ad15c9b',
      [string]$CliRoot = 'D:/RAG-Worktrees/rag-exec-08/carrefour-challenge',
      [string]$PrivacyRoot = '',
      [ValidatePattern('^[a-f0-9]{64}$')][string]$PrivacySinkSha = '960069e7f4532c8c60371c59e3d6a67dc014ed3da17d302ec08d3b44db9f6811',
      [ValidatePattern('^[a-f0-9]{64}$')][string]$PrivacyModuleSha = '6b6b718267bb95631d2794264faef98da220966abac753b117dd635f169ed17e',
      [ValidateRange(1,2)][int]$RoundCount = 2,
      [ValidateSet('baseline','live','privacy')][string[]]$ProfileNames = @('baseline','live','privacy'))
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$worktreeRoot = Split-Path $projectRoot -Parent
if (-not $PrivacyRoot) { $PrivacyRoot = $projectRoot }
$finalBaseSha = (& git -C $worktreeRoot rev-parse HEAD).Trim()
$receiptRoot = Join-Path $worktreeRoot ('.local/cf-app-03/integrated-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $receiptRoot | Out-Null
$producerSha = (& git -C $worktreeRoot rev-parse --verify "$ProducerRef^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $producerSha -notmatch '^[a-f0-9]{40}$') { throw 'PRODUCER_COMMIT_UNAVAILABLE' }
$archive = Join-Path $receiptRoot 'producer.zip'
& git -C $worktreeRoot archive --format=zip "--output=$archive" $producerSha carrefour-challenge
if ($LASTEXITCODE -ne 0) { throw 'PRODUCER_SNAPSHOT_FAILED' }
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $receiptRoot 'producer')
$producerSnapshotRoot = Join-Path $receiptRoot 'producer/carrefour-challenge'
$producerRoot = Join-Path $receiptRoot 'integration'
$baselineRoot = Join-Path $receiptRoot 'baseline'
foreach ($root in @($baselineRoot,$producerRoot)) {
    New-Item -ItemType Directory -Path $root | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'src') -Destination $root -Recurse
}
$producerFiles = @('api.py','contracts.py','reservation_api.py','reservation_store.py','reservation_contracts.py')
foreach ($file in $producerFiles) {
    Copy-Item -LiteralPath (Join-Path $producerSnapshotRoot "src/clinic_adk/$file") -Destination (Join-Path $producerRoot 'src/clinic_adk')
}
$cliFiles = @('cli.py','cli_gateway.py','cli_journey.py','cli_dialogue.py')
$cliPins = @{'cli.py'='c5a445174531d27b673f1c287b5c633555cae6c9fbc4393277043a581ee1a643';
             'cli_dialogue.py'='6d05c2a8fc1bdf41cb0479113f36e15239fe04518a846fa80ee658044990ac6a';
             'cli_gateway.py'='304e92edc9c238df519384e41bd0f67884002acd1f617e60d63e4e0d240ef034';
             'cli_journey.py'='f95d656aa82f73d5c59b7da8b609034c58d1a8abc259d684704aa847087c77e6'}
foreach ($file in $cliFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $CliRoot "src/clinic_adk/$file"))) { throw 'CLI_CONSUMER_UNAVAILABLE' }
    if ((Get-FileHash -LiteralPath (Join-Path $CliRoot "src/clinic_adk/$file") -Algorithm SHA256).Hash.ToLowerInvariant() -cne $cliPins[$file]) { throw 'CLI_CHECKPOINT_HASH_MISMATCH' }
    # Nested file mounts need existing targets under the read-only package mount.
    # Empty placeholders in our private snapshot are shadowed by owner read-only
    # mounts. No CLI source is copied or edited.
    $target = Join-Path $producerRoot "src/clinic_adk/$file"
    if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType File -Path $target | Out-Null }
}
$privacyFiles = @('src/clinic_adk/privacy.py','src/clinic_adk/privacy_sinks.py',
                  'tests/test_cf07_privacy_sinks.py','tests/test_cf07_privacy_adapters.py',
                  'tests/test_cf07_mcp_egress.py',
                  'tools/scan_cf07_sinks.py','docs/cf-app-07-privacy-contract.md')
foreach ($file in $privacyFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PrivacyRoot $file))) { throw 'PRIVACY_CONTRACT_UNAVAILABLE' }
}
$actualSinkSha = (Get-FileHash -LiteralPath (Join-Path $PrivacyRoot 'src/clinic_adk/privacy_sinks.py') -Algorithm SHA256).Hash.ToLowerInvariant()
$actualModuleSha = (Get-FileHash -LiteralPath (Join-Path $PrivacyRoot 'src/clinic_adk/privacy.py') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSinkSha -cne $PrivacySinkSha -or $actualModuleSha -cne $PrivacyModuleSha) { throw 'PRIVACY_RECEIPT_HASH_MISMATCH' }
foreach ($root in @($baselineRoot,$producerRoot)) {
    $target = Join-Path $root 'src/clinic_adk/privacy_sinks.py'
    if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType File -Path $target | Out-Null }
}
function Get-Sources {
    $manifest = [ordered]@{final_base=$finalBaseSha; producer_commit=$producerSha}
    foreach ($scope in @(@{root=$projectRoot; prefix='consumer'; paths=@('src','tests','data','examples','requirements.lock','requirements.txt','docs/cf-app-03-acceptance.md','tools/verify_journey.ps1','tools/verify_journey_integrated.ps1')},
                          @{root=$producerRoot; prefix='integration'; paths=@('src')},
                          @{root=$producerSnapshotRoot; prefix='producer-snapshot'; paths=@('src','data','requirements.lock')},
                          @{root=$baselineRoot; prefix='baseline'; paths=@('src')})) {
        foreach ($path in $scope.paths) {
            $files = @(Get-ChildItem -LiteralPath (Join-Path $scope.root $path) -File -Recurse |
                       Where-Object { $_.FullName -notmatch '\\(__pycache__|\.pytest_cache)\\' } | Sort-Object FullName)
            foreach ($file in $files) {
                $relative = [IO.Path]::GetRelativePath($scope.root, $file.FullName).Replace('\','/')
                $manifest[($scope.prefix + '/' + $relative)] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    }
    foreach ($file in $cliFiles) {
        $manifest[('cli-readonly/' + $file)] = (Get-FileHash -LiteralPath (Join-Path $CliRoot "src/clinic_adk/$file") -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    foreach ($file in $privacyFiles) {
        $manifest[('privacy-readonly/' + $file)] = (Get-FileHash -LiteralPath (Join-Path $PrivacyRoot $file) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return ($manifest | ConvertTo-Json -Depth 5 -Compress)
}
$imageId = (& docker image inspect $Image --format '{{.Id}}').Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'DEPENDENCY_IMAGE_UNAVAILABLE' }
$before = Get-Sources
$before | Set-Content -LiteralPath (Join-Path $receiptRoot 'sources.json') -Encoding utf8
$profiles = @(
    @{name='baseline'; source=$baselineRoot; proofs=3; tests=@('tests/test_journey.py','tests/test_journey_gateway.py','tests/test_unit.py','tests/test_discovery_outcomes.py','tests/test_runtime_envelope.py','tests/test_journey_producer.py')},
    @{name='live'; source=$producerRoot; proofs=9; tests=@('tests/test_journey_live.py')},
    @{name='privacy'; source=$producerRoot; proofs=0; tests=@('tests/test_journey_privacy.py',
        '/privacy-tests/test_cf07_privacy_adapters.py',
        '/privacy-tests/test_cf07_mcp_egress.py',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_retrieval_rejects_canary_before_node_event',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_retrieval_drops_remote_metadata_and_copies_canonical_rows',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_node_exception_does_not_publish_remote_message',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_adk_session_and_outputs_are_private_before_return[forged_rag]',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_adk_session_and_outputs_are_private_before_return[sdk_error]',
        '/privacy-tests/test_cf07_privacy_sinks.py::test_adk_session_and_outputs_are_private_before_return[sdk_timeout]')})
$runs = @()
$profiles = @($profiles | Where-Object { $_.name -in $ProfileNames })
$exitStatus = 0
foreach ($round in 1..$RoundCount) {
    foreach ($profile in $profiles) {
        $name = $profile.name
        $log = Join-Path $receiptRoot "round-$round-$name.log"
        $proofRoot = Join-Path $receiptRoot "round-$round-$name-proof"
        New-Item -ItemType Directory -Path $proofRoot | Out-Null
        $containerName = 'cf-app-03-' + (Split-Path $receiptRoot -Leaf) + "-$round-$name"
        $dockerArgs = @('run','--rm','--name',$containerName,'--network','none','--read-only','--cap-drop','ALL',
                       '--security-opt','no-new-privileges:true','--memory','768m','--pids-limit','128',
                       '--tmpfs','/tmp:rw,size=64m','--tmpfs','/artifacts:rw,size=16m,uid=10001,gid=10001',
                       '-e','CF_SEED=1234','-e','CF_JOURNEY_EVIDENCE=/evidence',
                       '--mount',"type=bind,source=$proofRoot,target=/evidence",
                       '--mount',"type=bind,source=$(Join-Path $profile.source 'src'),target=/app/src,readonly",
                       '--mount',"type=bind,source=$(Join-Path $projectRoot 'tests'),target=/app/tests,readonly",
                       '--mount',"type=bind,source=$(Join-Path $projectRoot 'data'),target=/app/data,readonly",
                       '--mount',"type=bind,source=$(Join-Path $projectRoot 'examples'),target=/app/examples,readonly")
        foreach ($file in @('privacy.py','privacy_sinks.py')) {
            $dockerArgs += @('--mount',"type=bind,source=$(Join-Path $PrivacyRoot "src/clinic_adk/$file"),target=/app/src/clinic_adk/$file,readonly")
        }
        if ($name -in @('live','privacy')) {
            foreach ($file in $cliFiles) {
                $dockerArgs += @('--mount',"type=bind,source=$(Join-Path $CliRoot "src/clinic_adk/$file"),target=/app/src/clinic_adk/$file,readonly")
            }
        }
        if ($name -eq 'privacy') {
            $dockerArgs += @('--mount',"type=bind,source=$(Join-Path $PrivacyRoot 'tests'),target=/privacy-tests,readonly",
                             '--mount',"type=bind,source=$(Join-Path $PrivacyRoot 'tools'),target=/app/tools,readonly")
        }
        $dockerArgs += @('--entrypoint','python',$imageId,'-m','pytest','-q','-p','no:cacheprovider') + $profile.tests
        & docker @dockerArgs *> $log
        $code = $LASTEXITCODE
        $unchanged = (Get-Sources) -ceq $before
        $summary = @(Get-Content -LiteralPath $log -Tail 8) -join "`n"
        $proofFiles = @(Get-ChildItem -LiteralPath $proofRoot -Filter 'producer-*.json' -File)
        $passed = $code -eq 0 -and $unchanged -and $summary -match '\d+ passed' -and $summary -notmatch '(\d+ skipped|\d+ failed|\d+ error)' -and $proofFiles.Count -eq $profile.proofs
        $runs += @{round=$round; profile=$name; exit_code=$code; sources_unchanged=$unchanged; passed=$passed; summary=$summary;
                   log_sha256=(Get-FileHash -LiteralPath $log -Algorithm SHA256).Hash;
                   proofs=@($proofFiles | ForEach-Object { @{name=$_.Name; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} })}
        Write-Output "round=$round profile=$name exit=$code unchanged=$unchanged passed=$passed"
        Write-Output $summary
        if (-not $passed) { $exitStatus=1; break }
    }
    if ($exitStatus -ne 0) { break }
}
$receipt = @{card='CF-APP-03'; branch=(& git -C $worktreeRoot branch --show-current); head=(& git -C $worktreeRoot rev-parse HEAD);
             final_base=$finalBaseSha; final_tree='Consumer/compiler/catalog/RAG/helpers from reconciled main tree; five immutable CF06 API modules overlay only for live/privacy';
             producer_commit=$producerSha; image_id=$imageId; seed=1234; rounds=$runs; cli_readonly_root=$CliRoot;
             privacy_readonly_root=$PrivacyRoot;
             privacy_sinks_sha256=$PrivacySinkSha; privacy_module_sha256=$PrivacyModuleSha;
             source_manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $receiptRoot 'sources.json') -Algorithm SHA256).Hash;
             passed=($exitStatus -eq 0 -and $runs.Count -eq 6); card_done=$false; merge_sha=$null;
             status=$(if ($exitStatus -eq 0 -and $runs.Count -eq 6) {'READY_FOR_CENTRAL_REVIEW'} elseif ($exitStatus -eq 0) {'DEVELOPMENT_ONLY'} else {'VERIFICATION_FAILED'});
             scope='Regressions and real ADK/SSE/HTTP/SQLite CF06 integration, unchanged CF08 CLI, pinned corrected CF07 helper, 89 egress regressions and 10 original runtime/ADK canary oracles';
             limits=@('Composite immutable local snapshot, not merged branch or production deployment',
                      'Fault before request or withheld reply after actual HTTP commit; no packet-level disconnect',
                      'Host persists private checkpoint; no cross-process session lock','One exam per journey',
                      'Original CF07 legacy success expects unconsented REQUESTED POST; incompatible with Journey gate; not silently rewritten',
                      'CLI filename privacy finding remains owner CF08 scope',
                      'Independent review and merge require Central; generated-JSON compiler exercised in privacy faults, not live positive profile')}
$receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $receiptRoot 'receipt.json') -Encoding utf8
Write-Output "receipt=$receiptRoot/receipt.json"
exit $exitStatus
