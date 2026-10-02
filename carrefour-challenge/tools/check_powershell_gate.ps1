[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Evidence)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$localRoot = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot '.local')) + [System.IO.Path]::DirectorySeparatorChar
$evidencePath = [System.IO.Path]::GetFullPath($Evidence)
if (-not $evidencePath.StartsWith($localRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'EVIDENCE_MUST_BE_WORKTREE_LOCAL'
}
New-Item -ItemType Directory -Path $evidencePath -Force | Out-Null
if (Get-ChildItem -LiteralPath $evidencePath -Force | Select-Object -First 1) { throw 'USE_FRESH_EVIDENCE' }
$tokens = $null
$parseErrors = $null
$gate = Join-Path $PSScriptRoot 'verify.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($gate, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'GATE_SYNTAX_INVALID' }
$wrapper = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Docker'}, $true)
if ($null -eq $wrapper) { throw 'NATIVE_WRAPPER_MISSING' }
# Evaluate only this wrapper. Never run the driver or any Docker/Compose command.
. ([scriptblock]::Create($wrapper.Extent.Text))
$python = 'D:/RAG-Local/adk/.venv/Scripts/python.exe'
function docker { & $python @args; $global:LASTEXITCODE = $LASTEXITCODE }
$cases = @(
    @{name='stdout';code="print(123)";expected=0},
    @{name='stderr-success';code="import sys; sys.stderr.write('offline diagnostic'); print(123)";expected=0},
    @{name='native-failure';code="import sys; sys.stderr.write('offline diagnostic'); sys.exit(7)";expected=7}
)
$checks = @()
foreach ($case in $cases) {
    $log = Join-Path $evidencePath ($case.name + '.log')
    $exitCode = Invoke-Docker -Arguments @('-c',$case.code) -Log $log
    if ($exitCode -ne $case.expected) { throw 'NATIVE_EXIT_CONTRACT_FAILED' }
    $checks += @{name=$case.name;expected=$case.expected;actual=$exitCode;passed=$true}
}
@{scope='PowerShell parser and native wrapper only; Docker is replaced by authorized Python';
    complete=$true;checks=$checks;source_sha256=(Get-FileHash -LiteralPath $gate -Algorithm SHA256).Hash.ToLowerInvariant()} |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidencePath 'receipt.json') -Encoding utf8
Write-Output 'PowerShell gate parser/native-wrapper: PASS; no Docker executed.'
