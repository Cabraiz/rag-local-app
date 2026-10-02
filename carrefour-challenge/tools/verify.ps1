[CmdletBinding()]
param([string]$Evidence = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'eval/runs/carrefour-final'))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$ragRoot = Split-Path -Parent $projectRoot
$composeFile = Join-Path $projectRoot 'docker-compose.yml'
$script:ComposePrefix = @('compose', '--project-directory', 'D:\RAG-Local\app','-p','carrefour-adk-challenge','-f',$composeFile)
$script:apiUrl = 'http://127.0.0.1:8860'
$script:stepNumber = 0
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
$Evidence = (Resolve-Path -LiteralPath $Evidence).Path
if (Test-Path -LiteralPath (Join-Path $Evidence 'receipt.json')) { throw 'Use a fresh evidence directory.' }

function Invoke-Step {
    param([string]$Name,[string[]]$Arguments,[int[]]$Allowed = @(0))
    $script:stepNumber++
    $log = Join-Path $Evidence ('{0:D3}-{1}.log' -f $script:stepNumber,$Name)
    & docker @script:ComposePrefix @Arguments *> $log
    $stepExit = $LASTEXITCODE
    if ($stepExit -notin $Allowed) {
        Get-Content -LiteralPath $log -Tail 15
        throw "$Name failed with exit $stepExit; proof: $log"
    }
    return @{log=$log;exit_code=$stepExit}
}
function Last-JSON {
    param([string]$Path)
    $line = Get-Content -LiteralPath $Path | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1
    if (-not $line) { throw "Missing structured CLI result: $Path" }
    return $line | ConvertFrom-Json
}
function Wait-API {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        try {
            $health = Invoke-RestMethod -Uri "$script:apiUrl/health" -TimeoutSec 2
            if ($health.ok -and $health.fictional) { return }
        } catch {}
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'API did not recover within budget.'
}
function Snapshot-Sources {
    $hashes = [ordered]@{}
    Get-ChildItem -LiteralPath $projectRoot -Recurse -File | Where-Object {
        $_.FullName -notmatch '\\(evidence|__pycache__|\.pytest_cache|\.local)\\'
    } | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($ragRoot.Length+1).Replace('\','/')
        $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $hashes
}
function Assert-Snapshot {
    $after = Snapshot-Sources
    if (($after | ConvertTo-Json -Compress) -ne ($script:Frozen | ConvertTo-Json -Compress)) {
        throw 'SOURCE_CHANGED_RESET_STREAK'
    }
}
function Check-Lab {
    foreach ($port in @(8840,8850)) {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 8
        if ($response.StatusCode -ne 200) { throw "Existing laboratory unavailable: $port" }
    }
}

$rounds = @()
$freshProject = 'carrefour-eval-' + (Get-Date -Format 'yyyyMMddHHmmss')
$primaryPrefix = $script:ComposePrefix
$report = [ordered]@{complete=$false;consecutive_passes=0;review='new persona-based adversarial discovery plus same-author regression; not independent blind audit';rounds=@();checks=@{}}
try {
    Check-Lab
    $build = Invoke-Step 'build' @('build','api','browser')
    $script:Frozen = Snapshot-Sources
    $report.sources_sha256 = $script:Frozen
    $imageId = & docker image inspect carrefour-adk-challenge:1.0.0 --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:') { throw 'Missing frozen image.' }
    $report.image_id = $imageId
    $browserImage = & docker image inspect carrefour-adk-browser:1.0.0 --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0 -or $browserImage -notmatch '^sha256:') { throw 'Missing frozen browser image.' }
    $report.browser_image_id = $browserImage
    for ($round=1; $round -le 2; $round++) {
        if ($round -eq 2) {
            if (Get-NetTCPConnection -LocalPort 8861 -State Listen -ErrorAction SilentlyContinue) { throw 'Temporary reproduction port 8861 is in use.' }
            $env:CLINIC_API_PORT = '8861'
            $script:ComposePrefix = @('compose', '--project-directory', 'D:\RAG-Local\app','-p',$freshProject,'-f',$composeFile)
            $script:apiUrl = 'http://127.0.0.1:8861'
        }
        $up = Invoke-Step "round-$round-up" @('up','-d','--no-build','--wait','--wait-timeout','60','api','ocr','rag')
        Wait-API
        $seed = @(126021,330994)[$round-1]
        $junit = "round-$round.xml"
        $run = Invoke-Step "round-$round-suite" @('run','--rm','-v',($Evidence.Replace('\','/')+':/evidence'),
            '-e',"CF_SEED=$seed",'tests','python','-m','pytest','-q','-p','no:cacheprovider','tests',"--junitxml=/evidence/$junit")
        [xml]$xml = Get-Content -Raw -LiteralPath (Join-Path $Evidence $junit)
        $suites = @($xml.testsuites.testsuite)
        $testCount = ($suites | ForEach-Object {[int]$_.tests} | Measure-Object -Sum).Sum
        if ($testCount -lt 100 -or ($suites | Where-Object {[int]$_.failures -or [int]$_.errors -or [int]$_.skipped})) {
            throw 'Full regression not completely passed.'
        }
        $matrix = Invoke-Step "round-$round-use-cases" @('run','--rm','--no-deps',
            '-v',($Evidence.Replace('\','/')+':/evidence'), 'tests','python','tools/check_use_cases.py',
            '--junit',"/evidence/$junit",'--output',"/evidence/use-cases-$round.json")
        $transpile = Invoke-Step "round-$round-transpile" @('run','--rm','--no-deps','runner','python','-m','clinic_adk.cli','transpile','--output','proof.py')
        $requestId = [guid]::NewGuid().ToString()
        $positive = Invoke-Step "round-$round-cli" @('run','--rm','--no-deps','runner','python','-m','clinic_adk.cli','run','--agent','proof.py','--request-id',$requestId)
        $result = Last-JSON $positive.log
        if ($result.receipt.request_id -ne $requestId -or $result.receipt.status -ne 'REQUESTED' -or $result.model_calls -ne 0) { throw 'Unverified CLI receipt.' }
        $before = Invoke-RestMethod -Uri "$script:apiUrl/appointments/by-request/$requestId" -TimeoutSec 5
        if ($before.appointment_id -ne $result.receipt.appointment_id) { throw 'CLI receipt missing from API.' }

        $faults = [ordered]@{}
        foreach ($service in @('rag','ocr')) {
            $faultId = [guid]::NewGuid().ToString()
            try {
                $stopped = Invoke-Step "round-$round-stop-$service" @('stop',$service)
                $failure = Invoke-Step "round-$round-fault-$service" @('run','--rm','--no-deps','runner','python','-m','clinic_adk.cli','run','--agent','proof.py','--request-id',$faultId) @(2)
                $errorJson = Last-JSON $failure.log
                if ($errorJson.ok -ne $false -or $errorJson.request_id -ne $faultId) { throw 'Failure lost request correlation.' }
            } finally {
                $recovery = Invoke-Step "round-$round-recover-$service" @('up','-d','--no-build','--wait','--wait-timeout','60',$service)
            }
            $status = 0
            try { $unexpected = Invoke-WebRequest -Uri "$script:apiUrl/appointments/by-request/$faultId" -TimeoutSec 4; $status=$unexpected.StatusCode }
            catch { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -ne 404) { throw "Unavailable $service created a booking." }
            $faults[$service] = @{safe_failure=$true;request_id=$faultId;booking_absent=$true}
        }

        $timeoutId = [guid]::NewGuid().ToString()
        try {
            $paused = Invoke-Step "round-$round-pause-api" @('pause','api')
            $timeout = Invoke-Step "round-$round-timeout" @('run','--rm','--no-deps','runner','python','-m','clinic_adk.cli','run','--agent','proof.py','--request-id',$timeoutId) @(2)
            $timeoutJson = Last-JSON $timeout.log
            if ($timeoutJson.request_id -ne $timeoutId -or $timeoutJson.error -ne 'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY') { throw 'Timeout did not preserve truthful unknown outcome.' }
        } finally {
            $unpaused = Invoke-Step "round-$round-unpause-api" @('unpause','api')
        }
        Wait-API
        $retry = Invoke-Step "round-$round-timeout-replay" @('run','--rm','--no-deps','runner','python','-m','clinic_adk.cli','run','--agent','proof.py','--request-id',$timeoutId)
        $replayed = Last-JSON $retry.log
        $lookup = Invoke-RestMethod -Uri "$script:apiUrl/appointments/by-request/$timeoutId" -TimeoutSec 5
        if ($lookup.appointment_id -ne $replayed.receipt.appointment_id) { throw 'Unknown outcome was not reconciled.' }
        $faults.api_timeout = @{request_id=$timeoutId;unknown_then_reconciled=$true;appointment_id=$lookup.appointment_id}

        $restart = Invoke-Step "round-$round-restart-api" @('restart','api')
        Wait-API
        $allHealthy = Invoke-Step "round-$round-wait-after-restart" @('up','-d','--no-build','--wait','--wait-timeout','60','api','ocr','rag')
        $after = Invoke-RestMethod -Uri "$script:apiUrl/appointments/by-request/$requestId" -TimeoutSec 5
        if (($before | ConvertTo-Json -Compress) -ne ($after | ConvertTo-Json -Compress)) { throw 'Durable receipt lost after restart.' }
        $faults.api_restart = @{receipt_preserved=$true;request_id=$requestId}

        $browserPath = Join-Path $Evidence "browser-$round"
        New-Item -ItemType Directory -Path $browserPath -Force | Out-Null
        $browserRun = Invoke-Step "round-$round-playwright" @('run','--rm','--no-deps',
            '-v',($browserPath.Replace('\','/')+':/evidence'), '-e',"CF_SEED=$seed",
            '-e',"CLINIC_CLI_REQUEST_ID=$requestId",'browser')
        $browserReport = Get-Content -Raw -LiteralPath (Join-Path $browserPath 'browser-report.json') | ConvertFrom-Json
        if ($browserReport.complete -ne $true -or $browserReport.failures -ne 0 -or $browserReport.skipped -ne 0 `
            -or $browserReport.tests -lt 18 -or $browserReport.no_response_mocks -ne $true `
            -or $browserReport.all_contexts_external_blocked -ne $true `
            -or @($browserReport.cases | Where-Object {$_.passed -ne $true -or $_.external_origins.Count -ne 0}).Count) {
            throw 'Actual offline Playwright acceptance not entirely passed.'
        }

        $ledger = Invoke-Step "round-$round-ledger-scan" @('exec','-T','api','python','-c',
            'import sqlite3,json; c=sqlite3.connect("/state/appointments.sqlite3"); rows=c.execute("SELECT request_id,result FROM appointments").fetchall(); text=json.dumps(rows).lower(); assert not any(s in text for s in ("sentinela","example.invalid","123.456.789","90000-1234","ficticio qrs")); assert len(rows)==len(set(r[0] for r in rows)); print(json.dumps({"pii_absent":True,"unique_requests":len(rows)}))')
        $logs = Invoke-Step "round-$round-container-logs" @('logs','--no-color','api','ocr','rag')
        $logText = Get-Content -Raw -LiteralPath $logs.log
        if ($logText -match '(?i)Pessoa Sentinela|example\.invalid|123\.456\.789|90000-1234|Doutor Ficticio QRS|PRIVATE_SENTINEL') { throw 'PII sentinel found in application logs.' }
        $ps = Invoke-Step "round-$round-service-state" @('ps','--format','json')
        $state = Get-Content -LiteralPath $ps.log | Where-Object {$_.StartsWith('{')} | ForEach-Object {$_ | ConvertFrom-Json}
        if (@($state | Where-Object {$_.State -eq 'running' -and $_.Health -eq 'healthy'}).Count -ne 3) { throw 'Not all three services healthy.' }
        $rounds += @{number=$round;seed=$seed;exit_code=$run.exit_code;tests=[int]$testCount;junit=$junit;
            use_cases="use-cases-$round.json";project=($script:ComposePrefix[2]);faults=$faults;cli_receipt=$result.receipt;log=(Split-Path -Leaf $run.log);
            browser=@{tests=$browserReport.tests;failures=0;report="browser-$round/browser-report.json";exit_code=$browserRun.exit_code}}
        Assert-Snapshot
        Check-Lab
        $report.rounds = $rounds
        $report.consecutive_passes = $round
        $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Evidence 'checkpoint.json') -Encoding utf8
        Write-Output ('Round {0}: {1} passed; fault recovery, privacy and persistence verified.' -f $round,$testCount)
    }
    $script:ComposePrefix = $primaryPrefix
    $script:apiUrl = 'http://127.0.0.1:8860'
    $env:CLINIC_API_PORT = '8860'
    $export = Invoke-Step 'export-package' @('run','--rm','--no-deps','-v',($Evidence.Replace('\','/')+'/artifacts:/artifacts'),
        'runner','python','tools/export_examples.py')
    $report.checks = @{real_sse_and_adk=$true;fresh_second_project=$true;real_faults_and_api_timeouts=$true;
        no_pii_in_logs_or_ledger=$true;durable_restart=$true;original_rag_and_grafana_available=$true;
        all_source_hashes_unchanged=$true;package_exported=$true;all_declared_persona_use_cases_executed=$true;
        actual_playwright_offline_swagger=$true;browser_reconciles_real_cli_receipt=$true}
    Assert-Snapshot
    $report.complete = $true
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Evidence 'receipt.json') -Encoding utf8
    Write-Output "Complete: $Evidence"
} catch {
    $report.complete = $false
    $report.consecutive_passes = 0
    $report.failure = $_.Exception.Message
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Evidence 'failed-receipt.json') -Encoding utf8
    throw
} finally {
    $script:ComposePrefix = @('compose', '--project-directory', 'D:\RAG-Local\app','-p',$freshProject,'-f',$composeFile)
    $env:CLINIC_API_PORT = '8861'
    & docker @script:ComposePrefix down *> (Join-Path $Evidence 'temporary-project-cleanup.log')
    $env:CLINIC_API_PORT = '8860'
}
