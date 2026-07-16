#!/usr/bin/env bash
# Install / refresh the systemd unit for the ACP web service.
#
# Resolves the current project location from this script's path and writes
# /etc/systemd/system/acp.service with the correct absolute paths, so the
# project can be relocated freely — just move the directory and re-run this
# script.
#
# Usage: scripts/install_systemd.sh [--user <user>] [--enable]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE_USER="<user>"
ENABLE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) SERVICE_USER="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

UNIT_FILE="/etc/systemd/system/acp.service"
GROUP="$(id -gn "$SERVICE_USER")"

echo ">> Project root : $PROJECT_ROOT"
echo ">> Service user : $SERVICE_USER ($GROUP)"
echo ">> Unit file    : $UNIT_FILE"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=ACP — Auto-Calc Platform Web Dashboard
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$GROUP
WorkingDirectory=$PROJECT_ROOT
Environment=ACP_RUN_ROOT=/var/lib/acp/runs
StateDirectory=acp
LogsDirectory=acp
ExecStart=$PROJECT_ROOT/scripts/start_acp.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo ">> Reloaded systemd."

if [[ "$ENABLE" -eq 1 ]]; then
  sudo systemctl enable --now acp
  echo ">> Enabled + started acp.service"
else
  sudo systemctl restart acp
  echo ">> Restarted acp.service"
fi

systemctl is-active acp
