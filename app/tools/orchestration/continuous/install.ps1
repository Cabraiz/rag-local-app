param(
    [Parameter(Mandatory=$true)][string]$Database,
    [string]$TaskName = 'Codex RAG Continuous Dispatcher'
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\..'))
$databasePath = [IO.Path]::GetFullPath($Database)
$privateRoot = Join-Path $repoRoot '.local\orchestration\'
if (-not $databasePath.StartsWith($privateRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'DATABASE_OUTSIDE_PRIVATE_ORCHESTRATION'
}
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) { throw 'QUEUE_NOT_INITIALIZED' }
$pythonPath = Join-Path $repoRoot 'adk\.venv\Scripts\pythonw.exe'
$controllerPath = Join-Path $PSScriptRoot 'control.py'
$arguments = '-B "{0}" --database "{1}" serve' -f $controllerPath,$databasePath
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Actions.Execute -ne $pythonPath -or $existing.Actions.Arguments -ne $arguments) {
        throw 'EXISTING_TASK_CONFIG_DIFFERS_PRESERVE_IT'
    }
} else {
    $taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
    $principal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
}
if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') { Start-ScheduledTask -TaskName $TaskName }
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
