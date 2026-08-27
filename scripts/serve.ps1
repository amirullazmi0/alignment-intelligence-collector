[CmdletBinding()]
param(
    [string]$ServiceHost = '',
    [ValidateRange(1, 65535)]
    [int]$Port = 0,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Virtual environment belum tersedia. Jalankan: yarn setup'
}

$cliArgs = @('-m', 'app', 'serve')
if ($ServiceHost) { $cliArgs += @('--host', $ServiceHost) }
if ($Port -gt 0) { $cliArgs += @('--port', "$Port") }
if ($Reload) { $cliArgs += '--reload' }

Set-Location -LiteralPath $repoRoot
& $pythonPath @cliArgs
exit $LASTEXITCODE
