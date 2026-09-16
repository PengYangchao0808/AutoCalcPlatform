# ACP Task Organization — Design & Contract Document

**Status:** Active (Phase 1+2 shipped, Phase 3 partial)
**Last updated:** 2026-09-16
**Owner:** scheduler module
**Implementation:** `src/acp/scheduler/task_views.py`, `src/acp/scheduler/molecule_groups.py`, `src/acp/api/v2_routes.py`

---

## 1. Overview

The task organization system upgrades the per-project task list from a flat
chronological queue into a **grouped, filterable, sortable, taggable** browsing
surface.  All grouping/filtering/sorting/counting is computed **server-side over
the entire project scope** (not just the current page), so counts remain stable
under pagination and truncation.

### Design goals

- Grouping, filtering, and sorting are **independent, composable** operations.
- Same-dimension multi-select → **union** (OR); cross-dimension → **intersection** (AND).
- User edits (remark, tags, archived, molecule display name) are **never
  overwritten** by automatic sync paths (compare-before-write).
- Manual tags are **fully isolated** from structural TS/INT TAGs, node_tags,
  and real-time job status.

### What it does NOT do

- No task move/copy/reorder.
- No automatic molecule-name merging (suggestions only).
- No automatic archival by completion time.
- No multi-tag cross-combination grouping.
- No write-back to `spec_json`.

---

## 2. Grouping Dimensions

| GroupBy enum | Key source | Sentinel group | Notes |
|---|---|---|---|
| `molecule` | `tasks.molecule_key` | `__unassigned__` (empty key) | Default; case-preserving, whitespace-collapsed |
| `remark` | `tasks.remark` | `__unassigned__` (empty string) | Free-text remark field |
| `workflow` | `tasks.workflow` | — | Group carries `retired: bool` from catalog |
| `batch` | `tasks.batch_id` | `__singles__` (NULL) | "单独提交"; group has `min_created_at` |
| `tag` | `json_each(tasks.tags)` | `__untagged__` | One task can appear in multiple groups |
| `none` | — | `__all__` | Single flat group |

### Sentinel keys

- `__unassigned__` — used when the grouping column is empty string or NULL.
- `__singles__` — batch_id is NULL (no batch submission).
- `__untagged__` — task has no manual tags.
- `__all__` — no grouping (GroupBy.none).

### Tag grouping specifics

Tag grouping uses SQLite `json_each()` to expand the `tasks.tags` JSON array.
Each task may appear in **multiple** groups.  Therefore:

```
sum(group.count) >= total
```

The UI displays `total` (distinct task count) in the header; batch-select
deduplicates by `task_id`.

Tag grouping requires SQLite JSON1 extension.  If unavailable (`_JSON1_OK`
is `False`), tag grouping raises `ValueError`; tag **filtering** falls back
to Python-side filtering with identical semantics.

---

## 3. Filter Semantics

### Multi-value within a dimension → union (OR)

Each filter field accepts a tuple of values.  Within the same dimension,
values are combined with OR:

```
WHERE status IN ('running', 'queued')           -- status filter
WHERE molecule_key IN ('key1', 'key2')          -- molecule filter
WHERE EXISTS (SELECT 1 FROM json_each(tags)
              WHERE value IN ('tag1', 'tag2'))   -- tag filter
```

### Cross-dimension → intersection (AND)

Different filter dimensions are ANDed together:

```
WHERE status IN (...) AND workflow IN (...) AND molecule_key IN (...)
```

### Search

Search applies `LOWER(column) LIKE ?` with `%`/`_`/`\` escaping across
four columns: `molecule_name`, `task_name`, `remark`, `display_name`.

### Archived filter

| Value | Behavior |
|---|---|
| `exclude` (default) | `WHERE archived = 0` |
| `include` | No archived filter (show all) |
| `only` | `WHERE archived = 1` |

### Facets

Facets are computed **under the current project+archived scope** with the
**target dimension's own filter removed** (so selecting a molecule still
shows all workflow types in the workflow facet).  Facet values:

- `statuses` — `{status: count}` dict
- `workflows` — `{workflow: count}` dict
- `molecules` — `[{key, name, count}]`
- `tags` — `[{tag, count}]`
- `batches` — `[{batch_id, min_created_at, count}]`

This is the **T2 design decision**: facets = project+archived scope with
drill-down filters removed.  No nested drill-down within facets.

---

## 4. Sorting Contract

### Server-side time sorts

All time-based sorts are performed server-side on the SQLite `tasks` table:

| Sort enum | ORDER BY |
|---|---|
| `created_desc` | `created_at DESC` |
| `created_asc` | `created_at ASC` |
| `completed_desc` | `completed_at DESC` |
| `activity_desc` | `last_activity_at DESC` |

### Client-side name sorts

Name-based sorts (`name_asc`, `name_desc`) use the browser's
`Intl.Collator` with `{numeric: true, sensitivity: 'base' }` for
**natural sort** (TS2 < TS10) and Chinese pinyin collation.  The server
returns rows in creation order; the client re-sorts.

### running_first stable partition

When `running_first = true`, active-status tasks (queued, starting, pending,
running, paused, cancelling, waiting_review) are **stable-partitioned** to
the top.  Within each partition, the selected sort applies.

### Group ordering

Groups are ordered by a **representative row** from the selected sort
(e.g., for `created_desc`, the group with the newest task sorts first).
Secondary sort: group key ASC.

---

## 5. Counting Contract

### `total` — always whole-scope

```
SELECT COUNT(DISTINCT task_id) FROM tasks WHERE <all filters>
```

Unaffected by `group_limit` or `max_total` truncation.

### `counts` — status dict, always whole-scope

Full-scope status counts (all `JobStatus` enum keys), also unaffected by
truncation.  Compatible with the frontend `getQueueCounts()` function.

### Group counts

| Scenario | Behavior |
|---|---|
| Under `group_limit` | `count` = whole-scope per-group count |
| Under `max_total` | `count` reflects returned (truncated) rows |
| Tag grouping | `sum(group.count) >= total` (documented above) |

### `truncated`

`true` when any group is capped by `group_limit` or total row count hits
`max_total`.  Each group also carries a per-group `truncated: bool`.

---

## 6. Batch ID Lineage

### Convention

`batch_id` is derived from `spec.resources["batch_id"]` — a shared UUID
generated by the frontend batch-submit flow (`ACP_Workbench_v2.html:23207/23593`)
or the v2 `/tasks/batch` endpoint.

### Singles semantics

Tasks without `batch_id` (NULL) are grouped under the `__singles__` sentinel
("单独提交").  Historical tasks that predate the batch system appear here.

### No `jobs.batch_id` column

The `batch_id` lives **only** in `tasks.batch_id` (derived from `spec_json`).
There is no `jobs.batch_id` column — this avoids dual-fact and naming conflict.

---

## 7. Molecule Grouping

### Case-preserving key

`molecule_key` is computed by `molecule_group_key(value)`:

```python
" ".join(value.split()).casefold()
```

Strip → collapse whitespace → casefold.  Empty string stays empty.

### Two-tier alias resolution (migration 015+)

1. **Exact match** — `molecule_aliases.alias_key = ?` (case-sensitive)
2. **Case-insensitive** — `molecule_aliases.alias_key = ? COLLATE NOCASE`

The first match wins.  This allows explicit alias overrides while still
catching case variants.

### Sticky merges on new tasks

When a new task is submitted, `sync_from_job()` runs alias resolution and
sets `molecule_key` to the resolved `group_key`.  If no alias matches, the
canonical key is used directly.

### `set_molecule_name`

Updates the display name for a molecule group (does NOT rewrite
`tasks.molecule_name` — that field is first-write-wins).  Only the group
heading changes.

---

## 8. Tag Registry

### Lifecycle

1. **Seed** — on task creation, `spec.tags` (if present) seeds `tasks.tags`.
2. **Independent evolution** — after creation, tags evolve independently of
   `spec_json`.  Tags are never written back to `spec`.
3. **Operations** — rename, merge (redirect all occurrences), delete (remove
   tag from all tasks, does NOT delete tasks).

### Isolation

Manual tags are **fully isolated** from:
- Structural TS/INT TAGs (XYZ comment `TAG: TS|INT`)
- Node tags (`node_tags` in the frontend)
- Real-time job status

### Scope

Tags are project-scoped.  The tag registry lists all unique tags across
tasks in a project.

---

## 9. Archive

### Terminal-only

Only tasks in terminal status (completed, failed, cancelled) can be archived.
Attempting to archive an active task is rejected.

### Visibility-only

Archiving only affects the **default visibility** (`archived=0` filter).
It does NOT change computation state, file layout, or source associations.

### Unarchive

Archived tasks can be unarchived (set `archived=0`).  This is a visibility
toggle, not a state change.

---

## 10. Saved Views (Phase 3)

Saved views are stored in **project settings** (`projects.settings` JSON
column) as live queries, not snapshots.  Each view stores:

- `group_by`, `sort`, `statuses`, `workflows`, `molecule_keys`, `tags`,
  `batch_ids`, `remarks`, `search`, `archived`, `running_first`

On load, the query is re-executed against the current task index, so saved
views always reflect the latest state.

---

## 11. Lineage

### Discovery

Lineage is computed by BFS from a seed task, following:

- `input.source.source_job_id` — direct upstream job reference
- `input.source.asset_id` — structure asset reference
- `input.from` — legacy from-job reference
- Top-level `input.source_job_id` — shorthand

### Constraints

- BFS depth limit: ≤ 10 hops
- Direct downstream: reverse lookup (tasks whose `input.source` references
  the seed)
- Deduplication by task_id at each BFS level

### Source shapes

Three source shapes are recognized:

1. `input.source.source_job_id` + optional `asset_id` (structured source)
2. Top-level `input.source_job_id` (shorthand)
3. `input.from` (legacy format)

---

## 12. API Endpoint Table

| Method | Path | Description |
|---|---|---|
| GET | `/api/v2/task-view` | Query task view (group/filter/sort/count/facets) |
| PATCH | `/api/v2/tasks/{task_id}` | Update task display fields (remark, tags, archived, molecule_name) |
| POST | `/api/v2/tasks/batch` | Batch-submit new tasks (shared batch_id) |
| POST | `/api/v2/tasks/batch-ops` | Batch operations (add/remove tags, archive/unarchive, set molecule) |
| GET | `/api/v2/projects/{project_id}/tags` | List all tags in project |
| POST | `/api/v2/projects/{project_id}/tags/rename` | Rename a tag |
| POST | `/api/v2/projects/{project_id}/tags/merge` | Merge tags |
| POST | `/api/v2/projects/{project_id}/tags/delete` | Delete a tag |
| GET | `/api/v2/projects/{project_id}/molecule-groups` | List molecule groups with alias info |
| POST | `/api/v2/projects/{project_id}/molecule-groups/merge` | Merge molecule groups |
| GET | `/api/v2/tasks/{task_id}/lineage` | Read-only lineage browse (BFS ≤10) |
| PATCH | `/api/v2/projects/{project_id}` | Update project settings (saved views) |

Legacy v1 endpoints (`/api/v1/jobs/...`) remain unchanged.

---

## 13. i18n

All task organization UI strings use the `queue.view.*` i18n namespace:

- `queue.view.groupBy.molecule` / `.remark` / `.workflow` / `.batch` / `.tag` / `.none`
- `queue.view.sort.createdDesc` / `.createdAsc` / `.completedDesc` / `.activityDesc` / `.nameAsc` / `.nameDesc`
- `queue.view.filter.archived.exclude` / `.include` / `.only`
- `queue.view.sentinel.unassigned` / `.singles` / `.untagged` / `.all`
- `queue.view.action.archive` / `.unarchive` / `.addTag` / `.removeTag` / `.setMolecule`

Both `zh-CN` and `en-US` locales are required.

---

## 14. Test Map

| Test file | Guards |
|---|---|
| `tests/test_acp_scheduler_task_index_org.py` | T1: migration 014 idempotency, overwrite-safe sync, compare-before-write, purge cascade includes tasks, move_job updates project_id |
| `tests/test_acp_scheduler_task_views.py` | T2: query engine — group/filter/sort/count/facets, sentinel keys, tag grouping sum≥total, JSON1 fallback, truncation semantics |
| `tests/test_acp_api_v2_task_view.py` | T3: v2 task-view endpoint — query params, response shape, PATCH task fields, batch-submit |
| `tests/test_acp_api_v2_tag_registry.py` | T7: tag CRUD + rename/merge/delete + batch-ops |
| `tests/test_acp_api_v2_lineage.py` | T12: lineage BFS, source shapes, depth limit |
| `tests/test_acp_api_v2.py` | v2 API surface (projects, tasks, structures, files) |
| `tests/test_frontend_sync.py` | Frontend contract locks: wizard defaults, i18n keys, viewer framing |
