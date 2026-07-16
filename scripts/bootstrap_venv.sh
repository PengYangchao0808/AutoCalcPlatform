#!/usr/bin/env bash
# Bootstrap a project-local virtualenv and install ACP runtime deps.
# Usage: scripts/bootstrap_venv.sh [--dev]
#
# Creates ./.venv inside the project root (this directory travels with the
# project when it is copied/cloned), then installs the package with the
# [api,remote] (and optionally [dev]) extras declared in pyproject.toml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

EXTRAS="api,remote"
if [[ "${1:-}" == "--dev" ]]; then
  EXTRAS="api,remote,dev"
fi

echo ">> Project root : $PROJECT_ROOT"
echo ">> Venv         : $VENV_DIR"
echo ">> Extras       : $EXTRAS"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo ">> Creating venv ..."
  python3 -m venv "$VENV_DIR"
fi

echo ">> Upgrading pip ..."
"$VENV_DIR/bin/pip" install --upgrade pip wheel setuptools

echo ">> Installing ACP (editable, extras=[$EXTRAS]) ..."
"$VENV_DIR/bin/pip" install -e "$PROJECT_ROOT[$EXTRAS]"

echo ">> Done. Activate with: source $VENV_DIR/bin/activate"
