#!/bin/bash
set -euo pipefail
STUDIO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$STUDIO_ROOT"
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export PATH="$STUDIO_ROOT/.venv/bin:$HOME/.local/bin:$PATH"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${VIDEO_STUDIO_PORT:-8900}" --timeout-graceful-shutdown 10
