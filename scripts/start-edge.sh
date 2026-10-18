#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "${PIPEFORGE_PYTHON:-}" ]]; then
  python_bin="$PIPEFORGE_PYTHON"
elif [[ -x .venv-edge/bin/python ]]; then
  python_bin=.venv-edge/bin/python
elif [[ -x .venv/bin/python ]]; then
  python_bin=.venv/bin/python
else
  python_bin=python3
fi
exec "$python_bin" -m uvicorn backend.edge_worker:app --host "${PIPEFORGE_EDGE_HOST:-127.0.0.1}" --port "${PIPEFORGE_EDGE_PORT:-8010}" --workers 1 --no-proxy-headers
