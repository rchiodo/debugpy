$ErrorActionPreference = 'Stop'

Get-Command python, openssl -ErrorAction Stop | Out-Null
$outputDir = Join-Path $PSScriptRoot '_output'
[System.IO.Directory]::CreateDirectory($outputDir) | Out-Null
$cert = Join-Path $outputDir 'cert.pem'
$key = Join-Path $outputDir 'key.pem'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = Join-Path $repoRoot 'src'
    & openssl req -x509 -newkey rsa:2048 -nodes `
        -keyout $key -out $cert -days 1 `
        -subj '/CN=localhost' `
        -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
    if ($LASTEXITCODE -ne 0) {
        throw 'Local test certificate generation failed.'
    }

    & python (Join-Path $PSScriptRoot 'run_tcps_experiment.py')
    if ($LASTEXITCODE -ne 0) {
        throw "TCPS experiment failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    foreach ($path in @($key, $cert)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path
        }
    }
}
