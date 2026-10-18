#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv /workspace/pipeforge-venv
/workspace/pipeforge-venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
(cd frontend && npm ci --cache /workspace/.npm-cache && npm run build)
/workspace/pipeforge-venv/bin/python -m pytest -q
