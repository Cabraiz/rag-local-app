param(
    [string]$Evidence,
    [string]$RuntimeImage = 'carrefour-adk-challenge:1.0.0'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Evidence) {
    $Evidence = Join-Path $projectRoot ('.local/cf-app-10/' + (Get-Date -Format 'yyyyMMddTHHmmss'))
}
$Evidence = [IO.Path]::GetFullPath($Evidence)
if (Test-Path -LiteralPath $Evidence) { throw 'Use a new evidence directory' }
New-Item -ItemType Directory -Path $Evidence | Out-Null
$candidate = Join-Path $Evidence 'candidate'
New-Item -ItemType Directory -Path $candidate | Out-Null
$directories = @('src', 'tests', 'data', 'examples', 'tools', 'docs')
$files = @('requirements.lock', 'requirements.txt', 'docker-compose.yml', 'security.compose.yml')
foreach ($directory in $directories) {
    $destination = Join-Path $candidate $directory
    New-Item -ItemType Directory -Path $destination | Out-Null
    $prefix = Join-Path $projectRoot $directory
    Get-ChildItem -LiteralPath $prefix -File -Recurse | Where-Object {
        $_.FullName -notmatch '[\\/](?:__pycache__|\.local|\.pytest_cache)[\\/]' -and
        $_.Extension -notin @('.pyc', '.log', '.sqlite3')
    } | ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($prefix, $_.FullName)
        $target = Join-Path $destination $relative
        New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target
    }
}
foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $candidate }
function Fingerprint([string]$root) {
    $rows = @(Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName | ForEach-Object {
        [ordered]@{path = [IO.Path]::GetRelativePath($root, $_.FullName).Replace('\', '/');
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    return ($rows | ConvertTo-Json -Compress)
}
$frozen = Fingerprint $candidate
$frozen | Set-Content -LiteralPath (Join-Path $Evidence 'sources.json') -Encoding utf8
$image = (& docker image inspect $RuntimeImage --format '{{.Id}}').Trim()
if ($LASTEXITCODE -ne 0 -or $image -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Local image unavailable' }
$config = [ordered]@{image=$image; seed='1010'; network='none'; readonly=$true;
    memory='1536m'; cpus='2'; pids=192; tmpfs=@('/tmp:rw,size=128m', '/artifacts:rw,size=16m,uid=10001,gid=10001,mode=0700'); source=$candidate;
    note='Cached runtime; all candidate sources frozen and dependencies verified against lock.'}
$config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence 'config.json') -Encoding utf8
$mounts = @()
foreach ($directory in $directories) {
    $mounts += @('--mount', "type=bind,source=$(Join-Path $candidate $directory),target=/app/$directory,readonly")
}
foreach ($file in $files) {
    $mounts += @('--mount', "type=bind,source=$(Join-Path $candidate $file),target=/app/$file,readonly")
}
$mounts += @('--mount', "type=bind,source=$(Join-Path $candidate 'examples'),target=/samples,readonly")
foreach ($round in 1..2) {
    if ((Fingerprint $candidate) -cne $frozen) { throw 'Frozen candidate changed; restart both rounds' }
    $name = 'cf-app10-' + [Guid]::NewGuid().ToString('N')
    $arguments = @('run', '--pull=never', '--rm', '--name', $name, '--network', 'none',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '1536m', '--cpus', '2', '--pids-limit', '192',
        '--add-host', 'api:127.0.0.1', '--add-host', 'ocr:127.0.0.1', '--add-host', 'rag:127.0.0.1',
        '--tmpfs', '/tmp:rw,size=128m', '--tmpfs', '/artifacts:rw,size=16m,uid=10001,gid=10001,mode=0700',
        '-e', 'CF_SEED=1010') + $mounts +
        @($image, 'python', '/app/tools/run_cf_app_10.py')
    $arguments | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence "round-$round-command.json") -Encoding utf8
    & docker @arguments *> (Join-Path $Evidence "round-$round.log")
    $result = $LASTEXITCODE
    if ((Fingerprint $candidate) -cne $frozen) { throw 'Frozen candidate changed; restart both rounds' }
    if ($result -ne 0) {
        Get-Content -LiteralPath (Join-Path $Evidence "round-$round.log") -Tail 24
        throw "Round $round failed: $result"
    }
    Select-String -Path (Join-Path $Evidence "round-$round.log") -Pattern '\d+ passed|locked_dependencies_checked'
}
@{rounds=2; result='PASS'; image=$image; seed=1010; independent_review='PENDING'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence 'receipt.json') -Encoding utf8
Write-Output "Evidence: $Evidence"
