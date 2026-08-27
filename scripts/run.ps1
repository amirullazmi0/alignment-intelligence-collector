[CmdletBinding()]
param(
    [ValidateSet('backfill', 'incremental', 'status', 'reset-failed', 'import-sqlite', 'seed-schedules')]
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

$cliArgs = @('-m', 'app', $Command)
$stateOnly = @('status', 'reset-failed', 'import-sqlite', 'seed-schedules')
if ($DryRun -and $stateOnly -notcontains $Command) { $cliArgs += '--dry-run' }
if ($Limit -gt 0 -and $stateOnly -notcontains $Command) { $cliArgs += @('--limit', "$Limit") }
if ($VerboseLogs -and $stateOnly -notcontains $Command) { $cliArgs += '--verbose' }

Set-Location -LiteralPath $repoRoot
& $pythonPath @cliArgs
exit $LASTEXITCODE
