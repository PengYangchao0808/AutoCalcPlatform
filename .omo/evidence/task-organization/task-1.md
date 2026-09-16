# Task 1 — Data Foundation: Evidence

## Test Results

### pytest: test_acp_scheduler_task_index_org.py
```
29 passed in 5.60s
```

All acceptance criteria covered:
- Migration 014 idempotent (run twice, 0 on second pass)
- Migration 014 adds columns: molecule_key, tags, archived, batch_id, last_activity_at, started_at, completed_at, group_id, progress
- Migration 014 adds indexes: idx_tasks_project_archived, idx_tasks_molecule_key, idx_tasks_batch_id
- Backfill fills tasks from jobs (5 rows, batch_id only on 2, group_id matches jobs.group_id)
- Corrupt spec_json yields defaults (molecule_key='', tags='[]')
- Idempotent backfill (no duplicates on re-run)
- Existing task rows not overwritten by backfill
- Overwrite protection: sync_from_job preserves remark/tags/archived/user edits
- sync_job_transition preserves first-write-wins columns
- Compare-before-write: same status/stage → no timestamp change
- Status change → last_activity_at updated, started_at COALESCE
- Progress-only change → progress updated, last_activity_at unchanged
- purge_cascade removes tasks rows
- delete() and update_project() work (including no-op on missing)
- molecule_group_key: basic, strip, collapse, empty

### Targeted regression: test_acp_scheduler*.py
```
49 passed in 12.70s
```
1 pre-existing flaky test (test_batch_parallelism_one_persists_all_jobs_and_dispatches_fifo) — passes in isolation, timing-dependent.

### ruff check
```
All checks passed!
```
Files checked: naming.py, migrations.py, tasks.py, manager.py, store.py, test file.

## Manual QA Driver Output

Script: `/tmp/opencode/t1-qa/drive.py`

```
AFTER FIRST MIGRATION: 6 task rows
  j1: mol='CCO' key='cco' batch=None gid='gid1' tags='[]' status='completed' last_act='2026-01-01T11:00:00'
  j2: mol='BCB-Allene' key='bcb-allene' batch='batch_shared_001' gid='gid2' tags='[]' status='completed' last_act='2026-01-01T11:00:00'
  j3: mol='MeOH' key='meoh' batch='batch_shared_001' gid='gid3' tags='[]' status='completed' last_act='2026-01-01T11:00:00'
  j4: mol='H2O' key='h2o' batch=None gid='gid4' tags='[]' status='completed' last_act='2026-01-01T11:00:00'
  j5: mol='EtOH' key='etoh' batch=None gid='gid5' tags='[]' status='completed' last_act='2026-01-01T11:00:00'
  j_corrupt: mol='' key='' batch=None gid='j_corrupt' tags='[]' status='completed' last_act='2026-01-01T11:00:00'

  batch_id present on: ['j2', 'j3'] ✓
  group_id matches jobs.group_id for all rows ✓
  Corrupt spec yields defaults ✓
  Migrations applied (2nd run): 0 (idempotent) ✓
  Same status → no write (timestamps unchanged) ✓
  Status change → updated (new activity) ✓
  Second same-status → no write again ✓
  After purge_cascade: j_purge task row gone ✓

ALL CHECKS PASSED ✓
```

## Adversarial Classes

| Class | Result |
|-------|--------|
| malformed input | corrupt spec_json → defaults row, no crash ✓ |
| stale state | double-migration + backfill-after-rows-exist → idempotent ✓ |
| misleading success | driver script direct sqlite3 SELECT output confirms ✓ |
| dirty worktree | git status recorded before/after — only changed files committed ✓ |
| repeated interruptions | idempotency probes (re-run everything twice) ✓ |
| flaky tests / hung commands | N/A — 1 pre-existing timing test passes in isolation |
| prompt injection | N/A — no user-facing prompt processing |
| cancel-resume | N/A — not applicable to data foundation changes |

## Cleanup

Driver script kept at `/tmp/opencode/t1-qa/drive.py`. Temp DB files cleaned up by `tempfile.TemporaryDirectory` context manager (auto-deleted on exit).
