#!/usr/bin/env bash
# QA helper for the ACP structure viewer (plan todo 46 / F3 manual QA).
#
# Usage:
#   ./scripts/qa_structure_viewer.sh <job_id>   # QA a real job on a REAL server
#   ./scripts/qa_structure_viewer.sh            # pick the newest completed job
#   ./scripts/qa_structure_viewer.sh --selftest # seed a fixture run_root and
#                                               # verify the automated checks
#
# Automated part: starts `acp run serve` on a temporary (or provided) run
# root, waits for /api/status, exercises the structure-viewer catalog +
# geometry + vibrations endpoints, and runs `node --check` on the extracted
# viewer modules. Then prints the MANUAL browser checklist (F3 steps).
#
# Dependencies: bash, curl, python (with acp installed), node.
set -euo pipefail

QA_PORT="${QA_PORT:-8799}"
BASE="http://127.0.0.1:${QA_PORT}"
SELFTEST_JOB_ID="qa-selftest-confsearch"

log() { printf '[qa] %s\n' "$*"; }
die() { printf '[qa] FATAL: %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python >/dev/null 2>&1 || die "python (with acp installed) is required"
command -v node >/dev/null 2>&1 || die "node is required"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_PID=""
cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    log "stopping server (pid ${SERVER_PID})"
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${QA_TMP_ROOT:-}" && -d "${QA_TMP_ROOT}" ]]; then
    rm -rf "${QA_TMP_ROOT}"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# --selftest: seed a fixture run_root with one completed Confsearch job
# (2 conformers with XYZ files + a scheduler DB record), mirroring the
# inline-fixture style of tests/test_acp_api_structure_viewer.py.
# ---------------------------------------------------------------------------
seed_selftest() {
  local run_root="$1"
  python - "$run_root" "${SELFTEST_JOB_ID}" <<'PYSEED'
import json
import sys
from pathlib import Path

run_root = Path(sys.argv[1])
job_id = sys.argv[2]
work = run_root / "jobs" / job_id
conf_dir = work / "RESULT" / "confsearch" / "conformers"
conf_dir.mkdir(parents=True, exist_ok=True)
(work / "job.json").write_text("{}", encoding="utf-8")
(work / "task.json").write_text("{}", encoding="utf-8")

conformers = []
for i, (energy, weight, rel) in enumerate(
    [(-100.0, 0.7, 0.0), (-99.9, 0.3, 62.75)], start=1
):
    name = f"{i:04d}"
    (conf_dir / f"{name}.xyz").write_text(
        f"3\nconformer {name}\nO 0 0 0\nH 0 0 {i}\nH 0 {i} 0\n", encoding="utf-8"
    )
    conformers.append({
        "conf_id": name,
        "rank": i,
        "energy_hartree": energy,
        "free_energy_hartree": energy,
        "relative_energy_kcal": rel,
        "boltzmann_weight": weight,
        "geometry": f"conformers/{name}.xyz",
    })
manifest = {
    "schema_version": "confsearch_v1",
    "workflow": "Confsearch",
    "temperature_k": 298.15,
    "conformers": conformers,
}
(work / "RESULT" / "confsearch" / "confsearch_manifest.json").write_text(
    json.dumps(manifest), encoding="utf-8"
)

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.projects import ProjectManager
from acp.scheduler.store import JobStore

store = JobStore(run_root / "acp_jobs.db")
project_id = ProjectManager(store, run_root).ensure_default_project()
store.create(JobRecord(
    id=job_id,
    spec=JobSpec(workflow="Confsearch", name=job_id, project_id=project_id),
    status=JobStatus.COMPLETED,
    work_dir=str(work),
    project_id=project_id,
))
print(f"seeded {job_id} under {run_root}")
PYSEED
}

# ---------------------------------------------------------------------------
# Start the server on $ACP_RUN_ROOT and wait for /api/status
# ---------------------------------------------------------------------------
start_server() {
  log "starting acp run serve --port ${QA_PORT} (ACP_RUN_ROOT=${ACP_RUN_ROOT})"
  : "${ACP_RUN_ROOT:?ACP_RUN_ROOT must be set}"
  acp run serve --port "${QA_PORT}" >"${QA_LOG:-/dev/null}" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "${BASE}/api/status"; then
      log "server is up (pid ${SERVER_PID})"
      return 0
    fi
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
      die "server exited early — see ${QA_LOG:-server log}"
    fi
    sleep 0.5
  done
  die "server did not answer /api/status within 30s"
}

# ---------------------------------------------------------------------------
# Pick the QA target job id
# ---------------------------------------------------------------------------
pick_job_id() {
  if [[ -n "${1:-}" ]]; then
    printf '%s' "$1"
    return 0
  fi
  curl -sf "${BASE}/api/v1/jobs?status=completed&limit=50" | python -c '
import json, sys
data = json.load(sys.stdin)
jobs = data.get("jobs") or data.get("list") or []
if not jobs:
    sys.exit("no completed jobs on this server")
jobs.sort(key=lambda j: j.get("created_at") or "", reverse=True)
print(jobs[0]["id"])
'
}

# ---------------------------------------------------------------------------
# Exercise the structure-viewer endpoints; prints a compact shape summary.
# In selftest mode the expected values are ASSERTED (exit non-zero on drift).
# ---------------------------------------------------------------------------
probe_job() {
  local job_id="$1" mode="$2"
  log "probing job ${job_id} (${mode})"

  local catalog geometry vibrations
  catalog="$(curl -sf "${BASE}/api/v1/jobs/${job_id}/structure-viewer")" \
    || die "catalog request failed for ${job_id}"
  geometry="$(printf '%s' "${catalog}" | python -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("schema_version"), d.get("availability"), d.get("default_entry_id"), len(d.get("entries") or []))
')"
  read -r schema avail default_entry n_entries <<<"${geometry}"
  log "catalog: schema=${schema} availability=${avail} default=${default_entry} entries=${n_entries}"
  if [[ "${mode}" == "selftest" ]]; then
    [[ "${schema}" == "structure_viewer_v1" ]] || die "schema drift: ${schema}"
    [[ "${avail}" == "ready" ]] || die "availability drift: ${avail}"
    [[ "${default_entry}" == "conf_0001" ]] || die "default drift: ${default_entry}"
    [[ "${n_entries}" == "2" ]] || die "entry count drift: ${n_entries}"
  fi

  local geo_body geo_first vib_line
  geo_body="$(curl -sf "${BASE}/api/v1/jobs/${job_id}/structure-viewer/entries/${default_entry}/geometry")" \
    || die "geometry request failed for ${default_entry}"
  geo_first="$(printf '%s' "${geo_body}" | head -1)"
  log "geometry ${default_entry}: first line (atom count) = ${geo_first}"
  if [[ "${mode}" == "selftest" ]]; then
    [[ "${geo_first}" == "3" ]] || die "geometry atom-count drift: ${geo_first}"
  fi

  vib_line="$(curl -sf "${BASE}/api/v1/jobs/${job_id}/structure-viewer/entries/${default_entry}/vibrations" | python -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("available"), d.get("reason"), len(d.get("modes") or []))
')"
  log "vibrations ${default_entry}: available/reason/modes = ${vib_line}"
  if [[ "${mode}" == "selftest" ]]; then
    [[ "${vib_line}" == "False no_normal_modes 0" ]] || die "vibrations drift: ${vib_line}"
  fi
}

node_checks() {
  local js
  for js in structure_viewer.js structure_editor.js vibration_viewer.js; do
    node --check "${REPO_ROOT}/frontend/js/${js}"
    log "node --check frontend/js/${js}: OK"
  done
}

manual_checklist() {
  local job_id="$1"
  cat <<EOF

==================== F3 MANUAL BROWSER CHECKLIST ====================
Open ${BASE}/  (ACP Workbench) and select job ${job_id}, then verify:

 1. AUTO-LOAD      structure tab shows the conformer list + default geometry
                   immediately after job selection (no manual XYZ open)
 2. ARROWS/ANIM    on a job with frequencies (BatchOptimize opt_freq or a
                   frequency job): pick an imaginary mode -> displacement
                   arrows; Play -> rAF animation; Stop -> EXACT equilibrium
                   geometry restored, camera unchanged
 3. EDIT + SAVE    inspector edit panel: bond/angle/dihedral edit -> undo/
                   redo/reset; dirty badge; 另存为结构资产 -> asset id shown
 4. NARROW SCREEN  narrow the browser (<~900px): list + inspector become
                   drawers with overlay; overlay click closes them
 5. REMOTE PENDING on a remote-unsynced job: catalog shows 等待远程结果;
                   geometry 409 -> auto ?fetch=1 retry downloads once
 6. OVERLAY        list-row 叠合对比 button: second cyan model + RMSD line;
                   unproven pair -> measurements cleared note
 7. IRC PLAYBACK   on an irc job: 播放正向/播放反向 steps frames at ~4fps,
                   camera stable; stop idempotent
 8. LARGE SYSTEM   >200-atom entry: wireframe + 大体系模式 notice
                    (amber, aria-live), style choice persists per entry
Endpoints used: /api/v1/jobs/${job_id}/structure-viewer +
  .../entries/{id}/geometry + .../entries/{id}/vibrations + .../overlay
=====================================================================

EOF
}

# ---------------------------------------------------------------------------
main() {
  local mode="live"
  local job_arg=""
  if [[ "${1:-}" == "--selftest" ]]; then
    mode="selftest"
  elif [[ -n "${1:-}" ]]; then
    job_arg="$1"
  fi

  if [[ "${mode}" == "selftest" ]]; then
    QA_TMP_ROOT="$(mktemp -d /tmp/acp-qa-selftest.XXXXXX)"
    QA_LOG="${QA_TMP_ROOT}/server.log"
    ACP_RUN_ROOT="${QA_TMP_ROOT}/runs"
    export ACP_RUN_ROOT QA_LOG
    mkdir -p "${ACP_RUN_ROOT}"
    log "selftest run_root: ${ACP_RUN_ROOT}"
    seed_selftest "${ACP_RUN_ROOT}"
    start_server
    probe_job "${SELFTEST_JOB_ID}" selftest
    node_checks
    log "SELFTEST PASSED (automated checks green)"
    cleanup
    trap - EXIT
    exit 0
  fi

  # live mode: QA against an existing run root (or a fresh temp one)
  if [[ -z "${ACP_RUN_ROOT:-}" ]]; then
    QA_TMP_ROOT="$(mktemp -d /tmp/acp-qa-live.XXXXXX)"
    ACP_RUN_ROOT="${QA_TMP_ROOT}"
    export ACP_RUN_ROOT
    log "no ACP_RUN_ROOT set — using a FRESH temp root (${ACP_RUN_ROOT});"
    log "re-run with ACP_RUN_ROOT=<your runs dir> and a job id to QA real data"
  fi
  QA_LOG="${QA_LOG:-${ACP_RUN_ROOT}/qa-server.log}"
  export QA_LOG
  start_server
  local job_id
  job_id="$(pick_job_id "${job_arg}")" || die "could not pick a job id"
  probe_job "${job_id}" live
  node_checks
  manual_checklist "${job_id}"
  log "automated checks done — finish the manual checklist above"
}

main "$@"
