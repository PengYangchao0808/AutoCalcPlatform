# ACP 2.0 Recovery and Rollback Runbook (H6)

Status: **published** · H6: **OPEN** · production release: **false** · production restart executed: **false**.

Owner: Plan B runtime (B24b, todo 29).
Frozen candidate: `f32c6704e0348585a5a58ce8516450ab3afc7077` (`feat/frontend-plan1-closure`).
A reference: `1b580a3dcda1351a0869353e6a505acde10dcfe0` (`acp2/a-science`).
C reference: `39d4aeb20451c984e20d27e12270a15070adce12` (`acp2/c-experience`).

This runbook is operational guidance for the **B runtime/services layer** only. It does not
sign A science scope, C experience scope, a production release, a production restart, LSF,
DSH, or a real Diels–Alder run. Every open row is listed in
`reports/acp2_implementation/bb45a46/B/B24b/open-rows.json` and in the H6 receipt.

## 0. Vocabulary, oldest supported version and hard boundaries

Every command block below starts with a `# label:` line:

- `# label: REVERSIBLE` — the command only reads, copies, validates, or operates on an
  isolated test copy. A failed command leaves the current live state usable.
- `# label: IRREVERSIBLE` — the command overwrites/deletes live DB or artifact state, or
  advances a schema. A backup must exist and be verified **before** it runs. Checkpoint
  rollback is the verified backup from §2, not a "try it again" operation.

**Oldest supported database schema: 21.** The supported upgrade path is migration
`021 → 022 → 023`; the post-migration schema is `23`. A database below schema `21` is
not supported: keep it read-only and restore a schema-21-or-newer backup. Historical v1
checkpoints/artifacts remain readable through `acp.compat.legacy`, but they are
conservatively recomputed rather than silently adopted.

**No production restart boundary.** H6 is a fixture/local recovery gate. Do **not** restart,
stop, reload, or redeploy the production `acp.service` on port `8765`; do **not** write to
`/var/lib/acp/runs`; do **not** run any command that targets the production unit. The
production service is outside this runbook and outside H6 scope.

Environment for every command (absolute paths; source only, never execute the env file):

```bash
# label: REVERSIBLE
export ACP2_REPO=<repo>
source <acp2-dev>/data/b/acceptance-env.sh
export PYTHONPATH="$ACP2_REPO/src:$ACP2_REPO"
export B24B_WORKSPACE=<acp2-dev>/data/b/b24b-migration
export B24B_EVIDENCE="$ACP2_EVIDENCE/B/B24b"
export B24B_FIXTURE=<acp2-dev>/data/b/fixtures/migration/b24b-migration.json
export B24B_CANDIDATE=f32c6704e0348585a5a58ce8516450ab3afc7077
export ACP_CANDIDATE_REVISION="$B24B_CANDIDATE"
```

## 1. Preflight — prove the candidate and the isolation boundary

```bash
# label: REVERSIBLE
cd "$ACP2_REPO"
git rev-parse HEAD
git status --short -- src/ | head -20
test -f "$B24B_WORKSPACE/.b24-migration-workspace"
test -f "$B24B_EVIDENCE/migration/summary.json"
ss -ltnp | grep ':8765' || true
```

Acceptance: `git rev-parse HEAD` is `f32c6704e0348585a5a58ce8516450ab3afc7077`; the B24b
workspace marker exists; the production listener is observed but never touched. The B24b
migration run was executed from this exact commit; a different SHA's evidence is not reused.

## 2. Backup — read-only and safe to repeat (REVERSIBLE)

The backup is a SQLite online backup (WAL-safe) of the jobs DB and the admission DB, plus a
byte copy of the task/artifact tree and a digest manifest. It never deletes live state.

```bash
# label: REVERSIBLE
mkdir -p "$B24B_WORKSPACE/backup"
/usr/bin/python3.11 - "$B24B_WORKSPACE/db/acp_jobs.db" "$B24B_WORKSPACE/db/acp_admission.db" "$B24B_WORKSPACE/backup" <<'PY'
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sources = [Path(sys.argv[1]), Path(sys.argv[2])]
out = Path(sys.argv[3])
manifest = {"schema": "acp2.b24b.backup.v1", "generated_at": datetime.now(timezone.utc).isoformat(), "databases": {}}
for src in sources:
    target = out / src.name
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    conn = sqlite3.connect(target)
    try:
        version = conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
    except sqlite3.OperationalError:
        version = 0
    finally:
        conn.close()
    manifest["databases"][src.name] = {
        "path": str(target),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "schema_version": version,
    }
(out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2, sort_keys=True))
PY
```

```bash
# label: REVERSIBLE
/usr/bin/python3.11 - "$B24B_WORKSPACE" "$B24B_WORKSPACE/backup/artifacts" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
target = Path(sys.argv[2])
files = {}
target.mkdir(parents=True, exist_ok=True)
for name in ("tasks", "source-copy"):
    source = workspace / name
    if not source.is_dir():
        continue
    destination = target / name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            files[path.relative_to(target).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
(target.parent / "artifacts-digests.json").write_text(
    json.dumps({"schema": "acp2.b24b.backup.artifacts.v1", "files": files}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"backed up {len(files)} artifact files to {target}")
PY
```

Verification (REVERSIBLE): `backup/manifest.json` lists both databases with non-empty
sha256 values and `schema_version: 23`; `backup/artifacts-digests.json` is non-empty.

## 3. Restore — overwrites live DB/artifacts (IRREVERSIBLE)

Precondition: §2 has run, the backup manifest is present, and the live writers are stopped.
Keep a pre-restore copy so a failed restore is attributable and re-drivable.

```bash
# label: IRREVERSIBLE
test -f "$B24B_WORKSPACE/backup/manifest.json"
cp -a "$B24B_WORKSPACE/db/acp_jobs.db" "$B24B_WORKSPACE/db/acp_jobs.db.pre-restore"
cp -a "$B24B_WORKSPACE/db/acp_admission.db" "$B24B_WORKSPACE/db/acp_admission.db.pre-restore"
/usr/bin/python3.11 - "$B24B_WORKSPACE/backup" "$B24B_WORKSPACE/db" <<'PY'
import sqlite3
import sys
from pathlib import Path

backup = Path(sys.argv[1])
live = Path(sys.argv[2])
for name in ("acp_jobs.db", "acp_admission.db"):
    source = backup / name
    target = live / name
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    print(f"restored {target}")
PY
rm -rf "$B24B_WORKSPACE/tasks" "$B24B_WORKSPACE/source-copy"
cp -a "$B24B_WORKSPACE/backup/artifacts/tasks" "$B24B_WORKSPACE/tasks"
cp -a "$B24B_WORKSPACE/backup/artifacts/source-copy" "$B24B_WORKSPACE/source-copy"
```

Post-restore verification (REVERSIBLE):

```bash
# label: REVERSIBLE
/usr/bin/python3.11 - "$B24B_WORKSPACE/db/acp_jobs.db" "$B24B_WORKSPACE/db/acp_admission.db" <<'PY'
import sqlite3
import sys

for database in sys.argv[1:]:
    conn = sqlite3.connect(database)
    version = conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(database, "schema", version, "tables", len(tables))
    conn.close()
PY
```

Expected: schema `23`; `jobs`, `tasks`, `approval_records`, `idempotency_records`,
`research_budget_*` tables still present. Restore is not a substitute for a scientific
re-run; it restores persisted facts only.

## 4. Interrupted migration — schema stays at 21, restore, then upgrade

The supported interrupted-migration state is a readable DB at schema **21** with the
unapplied migration `023` absent. Do not hand-edit `_schema_migrations`. Restore the
verified backup first; then rerun the real migration. Both the restore and the schema
advance are irreversible without another backup.

Detect the interrupted state (REVERSIBLE):

```bash
# label: REVERSIBLE
/usr/bin/python3.11 - "$B24B_WORKSPACE/legacy/acp_jobs.db" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
try:
    applied = conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print({"schema_version": applied, "jobs": jobs, "readable": True})
finally:
    conn.close()
PY
```

Restore the interrupted DB from the verified backup (IRREVERSIBLE):

```bash
# label: IRREVERSIBLE
test -f "$B24B_WORKSPACE/backup/legacy-acp_jobs.db"
cp -a "$B24B_WORKSPACE/legacy/acp_jobs.db" "$B24B_WORKSPACE/legacy/acp_jobs.db.interrupted"
/usr/bin/python3.11 - "$B24B_WORKSPACE/backup/legacy-acp_jobs.db" "$B24B_WORKSPACE/legacy/acp_jobs.db" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print("restored", target)
PY
```

Run the real migration on the restored schema-21 DB (IRREVERSIBLE, backup required):

```bash
# label: IRREVERSIBLE
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py migrate --db "$B24B_WORKSPACE/legacy/acp_jobs.db"
```

Expected: `applied: 2`, `schema_version: 23`, jobs count unchanged, and
`approval_records` + `idempotency_records` now exist. If the migration itself was killed
again, repeat §4 from the verified backup; never delete `_schema_migrations` rows.

## 5. Publication-only retry — never re-charges computation (REVERSIBLE)

A publication failure or a publication-pending state retries **publication only**. It does
not invoke QC, does not create a new attempt, and does not re-charge a budget. Run it on the
isolated fixture copy and keep the before/after marker evidence.

```bash
# label: REVERSIBLE
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py publication \
  --fixture "$B24B_WORKSPACE/source-copy/v2_publish_interrupted" \
  --scratch "$B24B_WORKSPACE/drills"
```

Expected: `marker_before: null`, `marker_after_complete: true`, `manifest_exists: true`,
`qc_calls: 0`, `qc_executed: false`, `recovered: true`. A retry that changes `qc_calls` or
creates a second product id is a regression; stop and keep the evidence.

## 6. Approval, budget, idempotency and cursor recovery

### 6.1 Ledger verification after restore (REVERSIBLE)

```bash
# label: REVERSIBLE
APPROVAL_REF=$(/usr/bin/python3.11 -c 'import json,sys; print(json.load(open(sys.argv[1]))["seed"]["approvals"]["approved"])' "$B24B_EVIDENCE/migration/migration-log.json")
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py verify \
  --db "$B24B_WORKSPACE/db/acp_jobs.db" \
  --admission-db "$B24B_WORKSPACE/db/acp_admission.db" \
  --approval-ref "$APPROVAL_REF" \
  --budget-id mig-budget
```

Expected: approval state `approved`, revision `2`, binding present; budget id `mig-budget`
with limits and remaining counters; idempotency claim state `committed`, task
`mig_idem_job`; terminal job `mig_done` still `completed`. A duplicate receipt/claim replay
is idempotent and adds no second row. A budget is never reset by changing `run_id`;
recovery replays reservations and receipts rather than resetting the batch.

### 6.2 Lost worker recovery (IRREVERSIBLE)

This transitions a RUNNING job with a dead PID to FAILED and proves a terminal job cannot
be revived. It changes the job row; back up first (§2). Precondition: `mig_lost` is
actually RUNNING. After a previous drill the preserved workspace already has it FAILED;
reseed an isolated workspace before repeating:

```bash
# label: REVERSIBLE
export B24B_WORKSPACE=/tmp/b24b-lost-fresh
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py seed \
  --db "$B24B_WORKSPACE/db/acp_jobs.db" \
  --admission-db "$B24B_WORKSPACE/db/acp_admission.db" \
  --root "$B24B_WORKSPACE"
```

```bash
# label: IRREVERSIBLE
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py lost-worker \
  --db "$B24B_WORKSPACE/db/acp_jobs.db" \
  --running mig_lost \
  --terminal mig_done
```

After the drill, reset `B24B_WORKSPACE=<acp2-dev>/data/b/b24b-migration`
before §7. Expected: `mig_lost` → `failed` with `worker_lost:pid=999999:no_live_process`;
`terminal_revival_blocked: true`; `terminal_selected_by_recovery_scan: false`;
`terminal_unchanged: true`.

### 6.3 Cursor/lease expiry and takeover (IRREVERSIBLE)

Expiry changes the idempotency row; back up first. The takeover must create a new row rather
than duplicate the expired one. Precondition: a stale/expired lease. A live lease correctly
reports `in_progress` and must not be forced; use a fresh seeded workspace (the §6.2 seed
block with a different `B24B_WORKSPACE`) when a prior drill already took over the cursor key.

```bash
# label: IRREVERSIBLE
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py cursor \
  --db "$B24B_WORKSPACE/db/acp_jobs.db"
```

Expected: expired state `expired`, takeover kind `created`, `no_duplicate_row: true`, and
the row count unchanged before/after.

### 6.4 Old and duplicate receipts (REVERSIBLE — isolated copies)

```bash
# label: REVERSIBLE
/usr/bin/python3.11 scripts/acceptance/acp2_migration_helper.py receipts \
  --fixtures "$B24B_WORKSPACE/source-copy" \
  --scratch "$B24B_WORKSPACE/drills"
```

Expected: an old v1 checkpoint is **not** adopted by default
(`default_decision: conservative_recompute`), is adopted only by explicit legacy opt-in;
duplicate publication keeps one complete marker and unique product ids.

## 7. Four-combination and legacy-reader regression (REVERSIBLE)

The S1 cells are the H6 candidate-service combinations. S0 is the pre-P4 baseline service and
is not an H6 execution target; its earlier baseline assertion failure is retained in B24 raw
evidence, not reused as an H6 PASS. The candidate overlay is recorded because
`f32c670` alone cannot construct `OriginPolicy(public_origin=...)` and does not forward the
ordinary `BatchOptimize --method`; the overlay is the same working-tree delta B24a used.
It changes no evidence claim.

```bash
# label: REVERSIBLE
cd "$ACP2_REPO"
/usr/bin/python3.11 scripts/acceptance/four_combinations.py \
  --out "$B24B_EVIDENCE/four-combination" \
  --f1-revision "$B24B_CANDIDATE" \
  --cells 'F0/S1,F1/S1' \
  --candidate-patch "$B24B_EVIDENCE/candidate-runnable-overlay.patch"
```

Expected: exit `0`; `summary.json` has `F0/S1` PASS and `F1/S1` PASS; every one of the seven
ordinary legacy readers (`Confsearch`, `PESsearch`, `BatchOptimize`, `XtbPathSearch`,
`XtbConstrainedOpt`, `tsmode`, `nmr`) is PASS; the browser step is PASS. A missing browser or
service prerequisite must be recorded as `NOT_VERIFIED` with that prerequisite, never as PASS.

```bash
# label: REVERSIBLE
cd "$ACP2_REPO"
/usr/bin/python3.11 -m pytest \
  tests/test_legacy_read_only.py \
  tests/test_legacy_layout_compat.py \
  tests/test_recovery_fixtures_smoke.py \
  tests/test_recovery_cross_version.py -q
```

Expected: `41 passed`. These are the read-only compat readers for historical manifests,
layouts and cross-version recovery fixtures.

## 8. Test-resource cleanup — fixed order

Clean up in this order. Stopping a harness-started process is recoverable; deleting a
detached worktree or scratch directory is not.

1. Stop only harness-started services; never stop the production service.

```bash
# label: REVERSIBLE
for pid in $(pgrep -f 'uvicorn acp.api.server:app.*--port 4[0-9][0-9][0-9][0-9]' || true); do
  kill "$pid"
done
```

2. Remove detached git worktrees created by the harness.

```bash
# label: IRREVERSIBLE
cd "$ACP2_REPO"
git worktree prune
git worktree list
```

3. Remove temporary scratch directories outside the evidence tree.

```bash
# label: IRREVERSIBLE
rm -rf /tmp/acp-four-* /tmp/opencode/b24b-* /tmp/b24b-*.log
```

4. Preserve the backup + restore workspace for inspection; do not delete it.

```bash
# label: REVERSIBLE
test -f "$B24B_WORKSPACE/backup/manifest.json"
test -f "$B24B_WORKSPACE/backup/acp_jobs.db"
test -f "$B24B_WORKSPACE/backup/acp_admission.db"
test -f "$B24B_WORKSPACE/backup/legacy-acp_jobs.db"
```

5. Confirm no test listener or helper process remains; confirm the production listener was
   not touched.

```bash
# label: REVERSIBLE
pgrep -af 'four_combinations.py|acp2_migration_helper.py' || true
ss -ltnp | grep -E ':(18781|8765)\b' || true
```

Expected: no `four_combinations.py` or `acp2_migration_helper.py` process remains; the
production port observation does not appear as a process started by this runbook.

## 9. H6 receipt, open rows and rollback summary

The H6 receipt is `reports/acp2_implementation/bb45a46/B/B24b/h6-receipt.json`
(`acp2.h6.receipt.v1`). It records the frozen candidate SHA, the contract digest, the
four-combination results, the legacy-reader results, the B-owned rows, every open row and
`production_release: false` / `production_restart_executed: false`. The open-row file is
`reports/acp2_implementation/bb45a46/B/B24b/open-rows.json`.

Validate the frozen contracts and the receipt (REVERSIBLE):

```bash
# label: REVERSIBLE
cd "$ACP2_REPO"
/usr/bin/python3.11 scripts/acceptance/validate_acp2_contracts.py --manifest docs/reference/next-change/acp2-contracts/manifest.json
/usr/bin/python3.11 scripts/acceptance/acp2_runtime.py --check-only "$B24B_EVIDENCE/migration/summary.json"
/usr/bin/python3.11 -m pytest tests/test_acp2_runtime_recovery_runbook.py -q
```

Expected: `VALID` for the manifest and summary; the runbook test exits `0`.

**Irreversible-step summary**

| Step | Label | What cannot be undone without the backup |
| --- | --- | --- |
| §3 restore | IRREVERSIBLE | Overwrites the live jobs/admission DB and the task/artifact tree. |
| §4 interrupted migration restore + upgrade | IRREVERSIBLE | Replaces the interrupted DB and advances schema 21→23. |
| §6.2 lost-worker recovery | IRREVERSIBLE | RUNNING job becomes FAILED. |
| §6.3 cursor expiry/takeover | IRREVERSIBLE | Idempotency lease state changes and a takeover row is created. |
| §8.2 worktree removal | IRREVERSIBLE | Detached worktrees are deleted. |
| §8.3 scratch removal | IRREVERSIBLE | Temporary scratch directories are deleted. |

**Rollback to the verified backup** is §3 followed by §6.1. It restores persisted DB and
artifact facts; it does not undo a scientific result and does not re-charge computation.
There is no automated rollback of an already-published artifact beyond restoring the backup
and retrying publication (§5).

## 10. Explicit NOT_VERIFIED rows at H6 publication

| Row | Owner | Status | Why |
| --- | --- | --- | --- |
| LSF live execution | B | `NOT_VERIFIED(lsf_prerequisite_missing)` | No `ACP_LSF_NODE`/`LSF_ENVDIR`/`bsub`/`bjobs`/`bhosts`/`bqueues`/SSH credentials. |
| DSH live session | C | `NOT_VERIFIED(c_dsh_h6_absent)` | C has no H6 DSH readability/session receipt at this freeze. |
| C readability / T-40b signoff | C | `NOT_VERIFIED(c_readability_absent)` | No `experience:cross-domain-live` record and no C T-40b signoff. |
| A readability / A-26 receipt | A | `NOT_VERIFIED(a_readability_absent)` | A published `handoff-H6.json`, but no A-26 readability receipt for B data at this SHA. |
| Selectivity / B23b | B | `NOT_VERIFIED(c_t40a_absent)` | C T-40a submission absent; no authorized two-path artifact. |
| Real Diels–Alder runtime | B | `NOT_VERIFIED(c24a_absent)` | C24a approved DA submission absent. |
| Production release | B | `NOT_APPLICABLE` | H6 does not claim a production release or restart. |

H6 remains **OPEN** while any required row above is not `PASS`.
