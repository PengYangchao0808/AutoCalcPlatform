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
  # Conda-style deployment: no project venv; require acp+uvicorn in PATH instead.
  if python -c 'import uvicorn, acp' >/dev/null 2>&1; then
    echo "NOTE: $VENV_DIR/bin/uvicorn not found — using current environment ($(command -v python))"
  else
    echo "ERROR: $VENV_DIR/bin/uvicorn not found and the current python lacks uvicorn/acp." >&2
    echo "Run: scripts/bootstrap_venv.sh (or activate the conda env with acp installed)" >&2
    exit 1
  fi
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export ACP_RUN_ROOT="${ACP_RUN_ROOT:-/var/lib/acp/runs}"
export ACP_HOST="${ACP_HOST:-0.0.0.0}"
export ACP_PORT="${ACP_PORT:-8765}"

# QC software env (ORCA/OpenMPI) ships as profile.d snippets; systemd does not
# source /etc/profile.d, so load them explicitly or ORCA subprocesses fail fast.
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
for profile_snippet in /etc/profile.d/orca*.sh /etc/profile.d/xtb*.sh; do
  [[ -f "$profile_snippet" ]] && source "$profile_snippet"
done

echo "ACP run root : ${ACP_RUN_ROOT}"

exec uvicorn acp.api.server:app \
  --host "$ACP_HOST" \
  --port "$ACP_PORT" \
  --app-dir "$PROJECT_ROOT/src"
