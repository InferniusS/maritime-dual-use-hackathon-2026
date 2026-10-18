#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec /workspace/pipeforge-venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port "${PIPEFORGE_PORT:-8000}"
