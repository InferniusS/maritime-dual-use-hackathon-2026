#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv /workspace/pipeforge-cad-venv
/workspace/pipeforge-cad-venv/bin/python -m pip install --timeout 120 --cache-dir /workspace/.cad-cache -r requirements-cad.txt
/workspace/pipeforge-cad-venv/bin/python -c 'import OCP; print("Optional OpenCascade kernel available")'
