[CmdletBinding()]
param(
    [string]$Evidence,
    [ValidateRange(1024,65535)][int]$FirstApiPort = 18860,
    [ValidateRange(1024,65535)][int]$SecondApiPort = 18861
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$ragRoot = Split-Path -Parent $projectRoot
$composeFile = Join-Path $projectRoot 'docker-compose.yml'
if ([string]::IsNullOrWhiteSpace($Evidence)) {
    $Evidence = Join-Path $ragRoot ('.local/carrefour-gates/' + [guid]::NewGuid().ToString('N'))
}
if ($FirstApiPort -eq $SecondApiPort) { throw 'Use distinct isolated API ports.' }
$runId = [guid]::NewGuid().ToString('N')
$primaryProject = 'carrefour-eval-' + $runId + '-1'
$freshProject = 'carrefour-eval-' + $runId + '-2'
$runtimeImage = 'carrefour-gate-runtime:' + $runId
$browserImageTag = 'carrefour-gate-browser:' + $runId
$script:apiUrl = "http://127.0.0.1:$FirstApiPort"
$script:stepNumber = 0
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
$Evidence = (Resolve-Path -LiteralPath $Evidence).Path
if (Get-ChildItem -LiteralPath $Evidence -Force | Select-Object -First 1) { throw 'Use a fresh evidence directory.' }
$gateEnv = Join-Path $Evidence 'compose-gate.env'
function Compose-Prefix {
    param([string]$Project)
    # Explicit env file prevents Compose from reading a developer's .env.
    return @('compose','--project-directory',$projectRoot,'-p',$Project,'-f',$composeFile,'--env-file',$gateEnv)
}
$script:ComposePrefix = Compose-Prefix $primaryProject
$lockDirectory = Join-Path $ragRoot '.local'
New-Item -ItemType Directory -Path $lockDirectory -Force | Out-Null
$lockPath = Join-Path $lockDirectory 'carrefour-gate.lock'
$gateLock = $null
$ownedProjects = @()
$previousEnvironment = @{}
foreach ($name in @('CLINIC_API_PORT','CLINIC_IMAGE','CLINIC_BROWSER_IMAGE')) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Invoke-Docker {
    param([string[]]$Arguments,[string]$Log)
    # Windows PowerShell 5 treats redirected native stderr as an ErrorRecord.
    # Diagnose the native exit code, not the existence of diagnostic output.
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    & docker @Arguments 1> $Log 2> ($Log + '.stderr.log')
    $nativeExit = $global:LASTEXITCODE
    if ($null -eq $nativeExit) { throw 'DOCKER_PROCESS_NOT_STARTED' }
    return [int]$nativeExit
}
function Invoke-Step {
    param([string]$Name,[string[]]$Arguments,[int[]]$Allowed = @(0))
    $script:stepNumber++
    $log = Join-Path $Evidence ('{0:D3}-{1}.log' -f $script:stepNumber,$Name)
    $stepExit = Invoke-Docker -Arguments (@($script:ComposePrefix) + $Arguments) -Log $log
    if ($stepExit -notin $Allowed) {
        Get-Content -LiteralPath $log -Tail 15
        Get-Content -LiteralPath ($log + '.stderr.log') -Tail 15
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
        $_.FullName -notmatch '\\(evidence|__pycache__|\.pytest_cache|\.local|node_modules)\\' -and $_.Name -ne '.env'
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
$primaryPrefix = $script:ComposePrefix
$report = [ordered]@{complete=$false;consecutive_passes=0;run_id=$runId;
    projects=@($primaryProject,$freshProject);source_root=$projectRoot;
    review='new persona-based adversarial discovery plus same-author regression; not independent blind audit';rounds=@();checks=@{}}
try {
    $gateLock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    foreach ($port in @($FirstApiPort,$SecondApiPort)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            throw "Isolated API port already in use: $port"
        }
    }
    foreach ($project in @($primaryProject,$freshProject)) {
        $inventoryLog = Join-Path $Evidence "$project-containers.log"
        $inventoryExit = Invoke-Docker -Arguments @('ps','-a','--filter',"label=com.docker.compose.project=$project",'--format','{{.ID}}') -Log $inventoryLog
        $existing = Get-Content -LiteralPath $inventoryLog
        if ($inventoryExit -ne 0 -or $existing) { throw 'Fresh project already exists or Docker inventory failed.' }
        $volumeLog = Join-Path $Evidence "$project-volumes.log"
        $volumeExit = Invoke-Docker -Arguments @('volume','ls','--filter',"label=com.docker.compose.project=$project",'--format','{{.Name}}') -Log $volumeLog
        $existingVolumes = Get-Content -LiteralPath $volumeLog
        if ($volumeExit -ne 0 -or $existingVolumes) { throw 'Fresh project has existing volumes or Docker inventory failed.' }
        $ownedProjects += $project
    }
    $env:CLINIC_API_PORT = [string]$FirstApiPort
    $env:CLINIC_IMAGE = $runtimeImage
    $env:CLINIC_BROWSER_IMAGE = $browserImageTag
    @("CLINIC_API_PORT=$FirstApiPort", "CLINIC_IMAGE=$runtimeImage", "CLINIC_BROWSER_IMAGE=$browserImageTag") |
        Set-Content -LiteralPath $gateEnv -Encoding ascii
    @{run_id=$runId;projects=$ownedProjects;source_root=$projectRoot;images=@($runtimeImage,$browserImageTag);
        ports=@($FirstApiPort,$SecondApiPort);volumes_preserved=$true} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Evidence 'ownership.json') -Encoding utf8
    Check-Lab
    # Freeze before build; a mutation during build invalidates its evidence too.
    $script:Frozen = Snapshot-Sources
    $build = Invoke-Step 'build' @('build','api','browser')
    Assert-Snapshot
    $report.sources_sha256 = $script:Frozen
    $imageLog = Join-Path $Evidence 'runtime-image.log'
    $imageExit = Invoke-Docker -Arguments @('image','inspect',$runtimeImage,'--format','{{.Id}}') -Log $imageLog
    $imageId = Get-Content -LiteralPath $imageLog
    if ($imageExit -ne 0 -or $imageId -notmatch '^sha256:') { throw 'Missing frozen image.' }
    $report.image_id = $imageId
    $browserImageLog = Join-Path $Evidence 'browser-image.log'
    $browserImageExit = Invoke-Docker -Arguments @('image','inspect',$browserImageTag,'--format','{{.Id}}') -Log $browserImageLog
    $browserImage = Get-Content -LiteralPath $browserImageLog
    if ($browserImageExit -ne 0 -or $browserImage -notmatch '^sha256:') { throw 'Missing frozen browser image.' }
    $report.browser_image_id = $browserImage
    for ($round=1; $round -le 2; $round++) {
        $projectForRound = $primaryProject
        if ($round -eq 2) {
            $projectForRound = $freshProject
            $env:CLINIC_API_PORT = [string]$SecondApiPort
            $script:ComposePrefix = Compose-Prefix $freshProject
            $script:apiUrl = "http://127.0.0.1:$SecondApiPort"
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

        $ledger = Invoke-Step "round-$round-ledger-scan" @('exec','-T','api','python','tools/scan_ledger.py')
        $logs = Invoke-Step "round-$round-container-logs" @('logs','--no-color','api','ocr','rag')
        $logText = (Get-Content -Raw -LiteralPath $logs.log) + (Get-Content -Raw -LiteralPath ($logs.log + '.stderr.log'))
        if ($logText -match '(?i)Pessoa Sentinela|example\.invalid|123\.456\.789|90000-1234|Doutor Ficticio QRS|PRIVATE_SENTINEL') { throw 'PII sentinel found in application logs.' }
        $ps = Invoke-Step "round-$round-service-state" @('ps','--format','json')
        $state = Get-Content -LiteralPath $ps.log | Where-Object {$_.StartsWith('{')} | ForEach-Object {$_ | ConvertFrom-Json}
        if (@($state | Where-Object {$_.State -eq 'running' -and $_.Health -eq 'healthy'}).Count -ne 3) { throw 'Not all three services healthy.' }
        $rounds += @{number=$round;seed=$seed;exit_code=$run.exit_code;tests=[int]$testCount;junit=$junit;
            use_cases="use-cases-$round.json";project=$projectForRound;faults=$faults;cli_receipt=$result.receipt;log=(Split-Path -Leaf $run.log);
            browser=@{tests=$browserReport.tests;failures=0;report="browser-$round/browser-report.json";exit_code=$browserRun.exit_code}}
        Assert-Snapshot
        Check-Lab
        $report.rounds = $rounds
        $report.consecutive_passes = $round
        $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $Evidence 'checkpoint.json') -Encoding utf8
        Write-Output ('Round {0}: {1} passed; fault recovery, privacy and persistence verified.' -f $round,$testCount)
    }
    $script:ComposePrefix = $primaryPrefix
    $script:apiUrl = "http://127.0.0.1:$FirstApiPort"
    $env:CLINIC_API_PORT = [string]$FirstApiPort
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
    # Only new projects whose empty inventory was verified under this lock.
    try {
        foreach ($project in $ownedProjects) {
            try {
                $script:ComposePrefix = Compose-Prefix $project
                $cleanupExit = Invoke-Docker -Arguments (@($script:ComposePrefix) + @('down')) -Log (Join-Path $Evidence "$project-cleanup.log")
                if ($cleanupExit -ne 0) { Write-Warning "Owned project cleanup failed: $project; inspect its log." }
            } catch { Write-Warning "Owned project cleanup could not start: $project; inspect its log." }
        }
    } finally {
        foreach ($name in $previousEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
        }
        if ($null -ne $gateLock) { $gateLock.Dispose() }
    }
}
