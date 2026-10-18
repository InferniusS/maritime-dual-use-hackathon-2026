#!/usr/bin/env bash
# Usage: bash scripts/setup_mac.sh [path-to-python3.14]
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_python="${1:-}"
venv_python="$project_root/.venv/bin/python"

fail() { printf 'Setup failed: %s\n' "$*" >&2; exit 1; }
is_python314() {
  "$1" -c 'import platform, sys, sysconfig; sys.exit(0 if platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 14) and not sysconfig.get_config_var("Py_GIL_DISABLED") else 1)' >/dev/null 2>&1
}

[[ $# -le 1 ]] || fail "Usage: bash scripts/setup_mac.sh [path-to-python3.14]"
[[ "$(uname -s)" == "Darwin" ]] || fail "This script is for macOS. Use setup_windows.ps1 on Windows."

if [[ -n "$project_python" ]]; then
  is_python314 "$project_python" || fail "The supplied interpreter must be standard CPython 3.14.x (not free-threaded): $project_python"
elif [[ -x "$venv_python" ]]; then
  is_python314 "$venv_python" || fail "Existing .venv uses another Python version. Move it aside, then rerun with Python 3.14."
  project_python="$venv_python"
else
  for candidate in python3.14 /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 python3; do
    if is_python314 "$candidate"; then project_python="$candidate"; break; fi
  done
  [[ -n "$project_python" ]] || fail "Python 3.14.x was not found. Install it, then rerun with its full path. Use a standard CPython 3.14 build."
fi

if [[ -e "$project_root/.venv" || -L "$project_root/.venv" ]]; then
  [[ -x "$venv_python" ]] || fail "Existing .venv is incomplete or belongs to another operating system. Move it aside before retrying."
  is_python314 "$venv_python" || fail "Existing .venv must use Python 3.14.x. Move it aside before retrying."
else
  "$project_python" -m venv "$project_root/.venv"
fi

"$venv_python" -m pip --version >/dev/null || fail "pip is missing from .venv. Recreate the environment using a Python 3.14 installation with pip."
"$venv_python" -m pip install --only-binary=:all: -r "$project_root/requirements-lock.txt"
"$venv_python" -m pip check
"$venv_python" "$project_root/scripts/check_setup.py"
printf '\nSetup complete. Activate with:\n  source "%s/.venv/bin/activate"\n' "$project_root"
