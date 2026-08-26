[CmdletBinding()]
param(
    [ValidateSet('backfill', 'incremental', 'status')]
    [string]$Command = 'backfill',
    [switch]$DryRun,
    [ValidateRange(1, [int]::MaxValue)]
    [int]$Limit = 0,
    [switch]$VerboseLogs
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Virtual environment belum tersedia. Jalankan: yarn setup'
}

$cliArgs = @('-m', 'alignment_collector', $Command)
if ($DryRun -and $Command -ne 'status') { $cliArgs += '--dry-run' }
if ($Limit -gt 0 -and $Command -ne 'status') { $cliArgs += @('--limit', "$Limit") }
if ($VerboseLogs -and $Command -ne 'status') { $cliArgs += '--verbose' }

Set-Location -LiteralPath $repoRoot
& $pythonPath @cliArgs
exit $LASTEXITCODE
