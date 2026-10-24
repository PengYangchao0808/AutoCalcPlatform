#!/usr/bin/env bash
# ACP 2.0 C acceptance route probe (T-04/C04).
#
# Purpose: observe `route_select()` from the shared acceptance environment with
# interpreter stubs. It imports no product, opens no model, and touches no
# platform endpoint. Routing logic MUST NOT be copied here — this script only
# sources $ACP2_DATA_ROOT/acceptance-env-c.sh and calls route_select().
#
# Usage: scripts/acceptance/acp2_route_probe.sh <scenario> <timeout> [flags...]
# Output on success (stdout only):
#   interpreter=<abs python> source=<abs source> mode=<fixture|live>
# Output on routing failure:
#   exit=<code>
#
# Exit status mirrors route_select.
set -u

ACP2_ACCEPTANCE_ENV="${ACP2_ACCEPTANCE_ENV:-<acp2-dev>/data/acceptance-env-c.sh}"
if [ ! -f "$ACP2_ACCEPTANCE_ENV" ]; then
  echo "exit=2"
  exit 2
fi

# shellcheck source=/dev/null
. "$ACP2_ACCEPTANCE_ENV"

route_select "$@"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "exit=$rc"
  exit "$rc"
fi

echo "interpreter=$ACP2_SELECTED_INTERPRETER source=$ACP2_SELECTED_SOURCE mode=$ACP2_SELECTED_MODE"
