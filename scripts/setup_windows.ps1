# Usage: .\scripts\setup_windows.ps1
# Optional: -Python "C:\Path\To\Python314\python.exe"
param([string]$Python = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VersionProbe = 'import platform, sys, sysconfig; sys.exit(0 if platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 14) and not sysconfig.get_config_var("Py_GIL_DISABLED") else 1)'

function Test-Python314 {
    param([string]$Command, [string[]]$PrefixArguments = @())
    try {
        $null = & $Command @PrefixArguments -c $VersionProbe 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

try {
    if ($env:OS -ne "Windows_NT") { throw "This script is for Windows. Use setup_mac.sh on macOS." }
    $ProjectPython = $Python
    $PythonArguments = @()
    if ($ProjectPython) {
        if (-not (Test-Python314 -Command $ProjectPython)) {
            throw "The supplied interpreter must be standard CPython 3.14.x (not free-threaded): $ProjectPython"
        }
    } elseif (Test-Path -LiteralPath $VenvPython) {
        if (-not (Test-Python314 -Command $VenvPython)) {
            throw "Existing .venv must use Python 3.14.x. Move it aside before retrying."
        }
        $ProjectPython = $VenvPython
    } elseif ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python314 -Command "py" -PrefixArguments @("-3.14"))) {
        $ProjectPython = "py"
        $PythonArguments = @("-3.14")
    } elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Python314 -Command "python")) {
        $ProjectPython = "python"
    } else {
        throw 'Python 3.14.x was not found. Install it with the Python launcher, or pass -Python "C:\Path\To\Python314\python.exe". Use a standard CPython 3.14 build.'
    }

    $VenvDirectory = Join-Path $ProjectRoot ".venv"
    if (Test-Path -LiteralPath $VenvDirectory) {
        if (-not (Test-Path -LiteralPath $VenvPython)) {
            throw "Existing .venv is incomplete or belongs to another operating system. Move it aside before retrying."
        }
        if (-not (Test-Python314 -Command $VenvPython)) {
            throw "Existing .venv must use Python 3.14.x. Move it aside before retrying."
        }
    } else {
        Invoke-Checked -Command $ProjectPython -Arguments ($PythonArguments + @("-m", "venv", $VenvDirectory))
    }

    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "--version")
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "install", "--only-binary=:all:", "-r", (Join-Path $ProjectRoot "requirements-lock.txt"))
    Invoke-Checked -Command $VenvPython -Arguments @("-m", "pip", "check")
    Invoke-Checked -Command $VenvPython -Arguments @((Join-Path $ProjectRoot "scripts\check_setup.py"))
    Write-Host "`nSetup complete. Use .venv\Scripts\python.exe to run project commands."
    Write-Host "Optional activation: & `"$VenvDirectory\Scripts\Activate.ps1`""
} catch {
    Write-Error "Setup failed: $($_.Exception.Message)" -ErrorAction Continue
    exit 1
}
