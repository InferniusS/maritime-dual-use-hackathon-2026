param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
$PythonExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PythonExe)) { throw 'Run .\scripts\setup-local.ps1 first.' }
Push-Location $RepoRoot
try {
    & $PythonExe -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'PIPEFORGE local service stopped with an error.' }
} finally { Pop-Location }
