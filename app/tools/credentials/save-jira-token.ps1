# Run manually in your local PowerShell. Never paste the token into chat.
# Stages a replacement privately; does not replace the active token or call cloud APIs.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$secretFolder = 'D:\RAG-Local\.local\secrets'
$pendingToken = Join-Path $secretFolder 'atlassian-mcp-token-pending.txt'
$pendingInfo = Join-Path $secretFolder 'atlassian-mcp-token-pending.json'
if ((Resolve-Path -LiteralPath $secretFolder).Path -ne $secretFolder) { throw 'Pasta de segredos inesperada.' }
if ((Test-Path -LiteralPath $pendingToken) -or (Test-Path -LiteralPath $pendingInfo)) { throw 'Ja existe uma credencial pendente. Nao foi sobrescrita.' }
$tokenExpiry = Read-Host 'Data de expiracao escolhida no Atlassian (AAAA-MM-DD; sugerido 2026-10-08)'
$parsedExpiry = [datetime]::MinValue
if (-not [datetime]::TryParseExact($tokenExpiry, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedExpiry)) { throw 'Data invalida.' }
if ($parsedExpiry.Date -le [datetime]::Today -or $parsedExpiry.Date -gt [datetime]::Today.AddDays(31)) { throw 'Use validade futura de ate 31 dias.' }
$secureToken = Read-Host 'Cole SOMENTE o novo token (entrada oculta)' -AsSecureString
$tokenPointer = [IntPtr]::Zero
$temporaryToken = Join-Path $secretFolder ('.jira-input-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $tokenText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer).Trim()
    if ($tokenText.Length -lt 20 -or $tokenText.Length -gt 4096 -or $tokenText -match '\s') { throw 'Formato de token invalido. Nada salvo.' }
    New-Item -ItemType File -Path $temporaryToken | Out-Null
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $privateAcl = [Security.AccessControl.FileSecurity]::new()
    $privateAcl.SetOwner($currentSid)
    $privateAcl.SetAccessRuleProtection($true, $false)
    $privateAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($currentSid, [Security.AccessControl.FileSystemRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow))
    Set-Acl -LiteralPath $temporaryToken -AclObject $privateAcl
    [IO.File]::WriteAllText($temporaryToken, $tokenText, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryToken -Destination $pendingToken
    $info = @{email='mateusccabr@gmail.com'; expires=$tokenExpiry; staged_at=[datetime]::UtcNow.ToString('o'); verified=$false}
    [IO.File]::WriteAllText($pendingInfo, ($info | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Host 'Credencial salva privadamente como PENDENTE. O token nao foi exibido nem ativado.'
    Write-Host 'Volte ao chat e diga: token salvo. Nao envie o segredo.'
}
catch {
    if (Test-Path -LiteralPath $temporaryToken) { Remove-Item -LiteralPath $temporaryToken }
    Write-Error 'Nao foi possivel concluir o salvamento privado. Nenhum segredo foi exibido. Verifique os arquivos pendentes antes de repetir.'
}
finally {
    if ($tokenPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    $tokenText = $null
    $secureToken.Dispose()
}
