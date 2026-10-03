param(
    [string]$Image = 'sha256:3ac0685898aa7ac27b471ec24e6636440d2abfac9e59cc067cc052c1da1d7272',
    [string]$Evidence = '',
    [int]$Seed = 1234
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$checkout = (Resolve-Path (Join-Path $project '..')).Path
$localRoot = [IO.Path]::GetFullPath((Join-Path $checkout '.local'))
if (-not $Evidence) { $Evidence = Join-Path $localRoot ('cf-app-02/frozen-' + [guid]::NewGuid().ToString('N')) }
$output = [IO.Path]::GetFullPath($Evidence)
if (-not $output.StartsWith($localRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Evidence must stay within this worktree .local directory'
}
if (Test-Path -LiteralPath $output) { throw 'Choose a new evidence directory' }
New-Item -ItemType Directory -Path $output | Out-Null
$imageId = & docker image inspect $Image --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Pinned local image unavailable' }

function Get-FrozenSources {
    $files = @()
    foreach ($part in @('src', 'tests', 'examples', 'data')) {
        $files += Get-ChildItem -LiteralPath (Join-Path $project $part) -File -Recurse |
            Where-Object { $_.FullName -notmatch '[\\/](__pycache__|\.pytest_cache)[\\/]' }
    }
    $files += Get-Item -LiteralPath $PSCommandPath
    $files += Get-Item -LiteralPath (Join-Path $project 'docs/CF-APP-02-contract.md')
    $rows = foreach ($file in ($files | Sort-Object FullName)) {
        [ordered]@{ path = [IO.Path]::GetRelativePath($checkout, $file.FullName).Replace('\', '/');
                    sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    return ConvertTo-Json -InputObject @($rows) -Depth 4 -Compress
}
$frozen = Get-FrozenSources
$frozen | Set-Content -LiteralPath (Join-Path $output 'sources.json') -Encoding utf8
$reports = @()
for ($round = 1; $round -le 2; $round++) {
    if ((Get-FrozenSources) -cne $frozen) { throw 'Sources changed; restart both rounds' }
    $roundPath = Join-Path $output "round-$round"
    New-Item -ItemType Directory -Path $roundPath | Out-Null
    $containerName = 'cf02-proof-' + [guid]::NewGuid().ToString('N')
    $arguments = @('run', '--rm', '--pull', 'never', '--name', $containerName,
        '--label', 'clinic.card=CF-APP-02', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '768m',
        '--pids-limit', '64', '--tmpfs', '/tmp:size=64m,mode=1777',
        '--env', "CF_SEED=$Seed", '--env', 'OTEL_SDK_DISABLED=true')
    foreach ($part in @('src', 'tests', 'examples', 'data')) {
        $sourcePath = (Join-Path $project $part).Replace('\', '/')
        $arguments += @('--mount', "type=bind,source=$sourcePath,target=/app/$part,readonly")
    }
    $arguments += @('--mount', ('type=bind,source=' + $roundPath.Replace('\', '/') + ',target=/evidence'),
        '--entrypoint', 'python', $imageId, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        '--junitxml=/evidence/junit.xml', 'tests/test_compiler_sandbox.py',
        'tests/test_unit.py', 'tests/test_artifact_integrity.py')
    $arguments | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $roundPath 'command.json') -Encoding utf8
    & docker @arguments *> (Join-Path $roundPath 'tests.log')
    $exitCode = $LASTEXITCODE
    $unchanged = (Get-FrozenSources) -ceq $frozen
    $junitPath = Join-Path $roundPath 'junit.xml'
    if (-not (Test-Path -LiteralPath $junitPath)) { throw "Round $round produced no JUnit; see $roundPath/tests.log" }
    [xml]$junit = Get-Content -LiteralPath $junitPath -Raw
    $suite = $junit.testsuites.testsuite
    $passed = $exitCode -eq 0 -and $unchanged -and [int]$suite.failures -eq 0 -and
              [int]$suite.errors -eq 0 -and [int]$suite.skipped -eq 0 -and [int]$suite.tests -gt 0
    $report = [ordered]@{ round=$round; image=$imageId; seed=$Seed; exit_code=$exitCode;
        tests=[int]$suite.tests; failures=[int]$suite.failures; errors=[int]$suite.errors;
        skipped=[int]$suite.skipped; sources_unchanged=$unchanged; passed=$passed;
        junit_sha256=(Get-FileHash -LiteralPath $junitPath -Algorithm SHA256).Hash.ToLowerInvariant() }
    $reports += $report
    $report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $roundPath 'receipt.json') -Encoding utf8
    Write-Output ($report | ConvertTo-Json -Compress)
    if (-not $passed) { throw "Round $round failed; restart both after fixing. See $roundPath/tests.log" }
}
[ordered]@{ card='CF-APP-02'; rounds=$reports; sources_manifest_sha256=
    (Get-FileHash -LiteralPath (Join-Path $output 'sources.json') -Algorithm SHA256).Hash.ToLowerInvariant();
    limits=@('ADK graph real, clinical steps use local probe', 'no OCR/SSE/API E2E',
             'cached pinned dependency image with candidate source mounts, not clean build',
             'CPython audit hooks are defense in depth for fixed emitted code only');
    passed=$true } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'receipt.json') -Encoding utf8
Write-Output "Receipt: $output/receipt.json"
