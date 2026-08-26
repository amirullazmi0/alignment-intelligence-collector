[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    python -m venv (Join-Path $repoRoot '.venv')
}

Set-Location -LiteralPath $repoRoot
& $pythonPath -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $pythonPath -m pip install -e '.[dev]'
exit $LASTEXITCODE
