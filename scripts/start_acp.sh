#!/usr/bin/env bash
# Launch the ACP web server using the project-local virtualenv.
#
# Designed to be invoked by the systemd unit (ExecStart=) or by hand.
# All paths are resolved relative to this script so the deployment is
# relocatable — copy the project directory, re-run bootstrap_venv.sh,
# and this script keeps working without edits.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "ERROR: $VENV_DIR/bin/uvicorn not found." >&2
  echo "Run: scripts/bootstrap_venv.sh" >&2
  exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export ACP_RUN_ROOT="${ACP_RUN_ROOT:-/var/lib/acp/runs}"
export ACP_HOST="${ACP_HOST:-0.0.0.0}"
export ACP_PORT="${ACP_PORT:-8765}"

exec "$VENV_DIR/bin/uvicorn" acp.api.server:app \
  --host "$ACP_HOST" \
  --port "$ACP_PORT" \
  --app-dir "$PROJECT_ROOT/src"
