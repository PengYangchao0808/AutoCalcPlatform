# Task 7 — P2 Tag Registry + Batch Operations API

**Date:** 2026-09-16
**Status:** ✅ Complete

## Changes

### 1. `src/acp/scheduler/tasks.py`
- Added `rewrite_tags(project_id, transform) -> int` method to `TaskIndex`
- Under a single lock + connection: SELECT all rows, apply transform, write back changed rows, commit once
- Returns count of rows with modified tags
- Handles corrupt JSON gracefully (treats as empty list)

### 2. `src/acp/api/v2_schemas.py`
- Added 8 new Pydantic models:
  - `V2TagInfo` (tag + count)
  - `V2TagRenameRequest` (source, target with validation)
  - `V2TagMergeRequest` (sources list + target with validation)
  - `V2TagDeleteRequest` (tag)
  - `V2TagOpResult` (updated count)
  - `V2BatchOpsRequest` (task_ids, op, payload)
  - `V2BatchOpItemResult` (task_id, ok, error)
  - `V2BatchOpsResult` (results list + updated count)

### 3. `src/acp/api/v2_routes.py`
- `GET /projects/{project_id}/tags` — aggregated tag counts via JSON1 or Python fallback
- `POST /projects/{project_id}/tags/rename` — exact-match rename via rewrite_tags
- `POST /projects/{project_id}/tags/merge` — merge N sources into target
- `POST /projects/{project_id}/tags/delete` — remove tag from all tasks
- `POST /tasks/batch-ops` — batch operations: add_tags, remove_tags, archive, unarchive, set_molecule_name
  - Archive validates terminal status; rejects with 400 + offending ids (NO partial execution)
  - Per-task error isolation; unknown ids → ok:false (never 500)
  - Tags validated: strip, non-empty, ≤32 chars, ≤20 total

### 4. `tests/test_acp_api_v2_tag_registry.py`
- 17 tests covering acceptance criteria 1-8:
  - ① Tags aggregation counts shared tags correctly
  - ② Rename — old name gone, task count unchanged, updated correct
  - ③ Merge 3→1
  - ④ Delete only unmarks (task rows intact)
  - ⑤ Batch add/remove idempotent (re-adding same tag doesn't duplicate)
  - ⑥ Archive with active task → 400 + offending ids + NO partial execution
  - ⑦ set_molecule_name recomputes molecule_key
  - ⑧ Unknown task_id → ok:false not 500
  - Edge cases: >500 task_ids → 422, corrupt tags JSON, rename same source==target, merge sources contain target → 422, archive mixed terminal+active, batch add empty tags

## Test Results

```
tests/test_acp_api_v2_tag_registry.py: 17 passed
Regression (test_acp_api_v2_task_view.py + test_acp_api_v2.py + test_acp_scheduler.py): 80 passed
ruff check: All checks passed
```

## QA Script

`/tmp/opencode/t7-qa/qa.py` — Full lifecycle on temp run_root:
- GET tags → rename → GET tags (old gone) → merge → delete
- Batch add/remove idempotent
- Archive terminal-only (plus 400 attempt with queued task)
- set_molecule_name recomputes key
- Raw sqlite3 cross-checks after each mutation
- **ALL QA CHECKS PASSED**
