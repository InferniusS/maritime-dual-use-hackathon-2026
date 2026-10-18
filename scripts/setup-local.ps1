param([switch]$SkipViewer, [string]$PythonCommand = 'python')
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $RepoRoot
try {
    & $PythonCommand -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed. Install Python 3.12 and retry.' }
    $PythonExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    & $PythonExe -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (-not $SkipViewer) {
        Push-Location (Join-Path $RepoRoot 'frontend')
        try {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
        } finally { Pop-Location }
    }
    Write-Host 'Ready. Run .\scripts\start-local.ps1 and open http://127.0.0.1:8000.'
} finally { Pop-Location }
