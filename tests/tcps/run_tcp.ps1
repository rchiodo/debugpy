$ErrorActionPreference = 'Stop'

Get-Command python -ErrorAction Stop | Out-Null
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = Join-Path $repoRoot 'src'
    & python (Join-Path $PSScriptRoot 'run_tcps_experiment.py') --tcp-only
    if ($LASTEXITCODE -ne 0) {
        throw "TCP experiment failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
