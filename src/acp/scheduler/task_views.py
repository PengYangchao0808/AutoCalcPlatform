"""
Task View Engine
================

Whole-project task view: group/filter/sort/count/facets for the ACP
scheduler task index.  Pure-read module — no writes to any table.

Counting semantics contract
---------------------------
* ``total`` = ``COUNT(DISTINCT task_id)`` over the post-filter scope.
  Unaffected by ``group_limit`` or ``max_total`` truncation.
* ``counts`` = full-scope status dict (all ``JobStatus`` keys), also
  unaffected by truncation.
* ``groups[].count`` = per-group task count.  Under ``GroupBy.tag``
  the same task may appear in multiple groups, so
  ``sum(group.count) >= total``.  The UI uses ``total`` for the
  header; batch-select deduplicates by task_id.
* ``truncated`` = ``True`` when any group is capped by ``group_limit``
  or the total row count hits ``max_total``.
* ``group_limit`` keeps per-group ``count`` whole-scope (only
  ``jobs[]`` is capped).
* ``max_total`` caps the flat row set before grouping — so per-group
  ``count`` under ``max_total`` reflects the *returned* (truncated)
  rows, not the full scope.  Only ``total`` and ``counts`` are
  guaranteed whole-scope under ``max_total``.

Module-level probe
------------------
``_JSON1_OK`` is set once at import time by attempting
``SELECT count(*) FROM json_each('["a"]')``.  When ``False``, tag
*filtering* falls back to Python-side filtering (identical semantics);
tag *grouping* raises ``ValueError``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON1 probe (one-time at import)
# ---------------------------------------------------------------------------

_JSON1_OK: bool = True
try:
    conn = sqlite3.connect(":memory:")
    conn.execute("SELECT count(*) FROM json_each('[\"a\"]')")
    conn.close()
except Exception:
    _JSON1_OK = False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GroupBy(str, Enum):
    """Grouping dimension for the task view."""

    molecule = "molecule"
    remark = "remark"
    workflow = "workflow"
    batch = "batch"
    tag = "tag"
    none = "none"


class TaskSort(str, Enum):
    """Sort order for the task view."""

    created_desc = "created_desc"
    created_asc = "created_asc"
    completed_desc = "completed_desc"
    activity_desc = "activity_desc"
    name_asc = "name_asc"
    name_desc = "name_desc"


class ArchivedFilter(str, Enum):
    """How to handle archived tasks in the result set."""

    exclude = "exclude"
    include = "include"
    only = "only"


# ---------------------------------------------------------------------------
# Query specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskViewQuery:
    """Immutable specification for a task view query."""

    project_id: str | None = None  # None = cross-project scope
    group_by: GroupBy = GroupBy.molecule
    sort: TaskSort = TaskSort.created_desc
    statuses: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    molecule_keys: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    batch_ids: tuple[str, ...] = ()
    remarks: tuple[str, ...] = ()
    search: str = ""
    archived: ArchivedFilter = ArchivedFilter.exclude
    running_first: bool = False
    group_limit: int = 200
    max_total: int = 5000


# ---------------------------------------------------------------------------
# Active statuses (for running_first partition)
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        "queued",
        "starting",
        "pending",
        "running",
        "paused",
        "cancelling",
        "waiting_review",
    }
)


# ---------------------------------------------------------------------------
# Retired-workflow lookup (lazy, cached)
# ---------------------------------------------------------------------------

_RETIREMENT_CACHE: dict[str, bool] | None = None


def _is_retired(workflow: str) -> bool:
    global _RETIREMENT_CACHE  # noqa: PLW0603
    if _RETIREMENT_CACHE is None:
        _RETIREMENT_CACHE = {}
        try:
            from acp.catalog import WORKFLOW_CATALOG

            for entry in WORKFLOW_CATALOG:
                _RETIREMENT_CACHE[entry["id"]] = entry.get("status") == "retired"
        except ImportError:
            pass
    return _RETIREMENT_CACHE.get(workflow, False)


# ---------------------------------------------------------------------------
# Search escaping
# ---------------------------------------------------------------------------


def _escape_like(value: str) -> str:
    """Escape ``%``, ``_``, and ``\\`` for SQLite LIKE."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# SQL ORDER BY clause
# ---------------------------------------------------------------------------

_SORT_SQL: dict[TaskSort, str] = {
    TaskSort.created_desc: "t.created_at DESC",
    TaskSort.created_asc: "t.created_at ASC",
    TaskSort.completed_desc: "COALESCE(t.completed_at, t.created_at) DESC",
    TaskSort.activity_desc: "COALESCE(t.last_activity_at, t.created_at) DESC",
    TaskSort.name_asc: "t.created_at DESC",  # pinyin sort is frontend's job
    TaskSort.name_desc: "t.created_at DESC",
}


# ---------------------------------------------------------------------------
# Project name resolution
# ---------------------------------------------------------------------------


def _resolve_project_names(
    conn: sqlite3.Connection,
) -> dict[str, str]:
    """Return project_id → name mapping from the projects table."""
    try:
        rows = conn.execute("SELECT project_id, name FROM projects").fetchall()
        return {row["project_id"]: row["name"] for row in rows}
    except sqlite3.OperationalError:
        return {}


# ---------------------------------------------------------------------------
# Core query function
# ---------------------------------------------------------------------------


def query_project_tasks(
    index: Any,  # TaskIndex
    q: TaskViewQuery,
) -> dict[str, Any]:
    """Query tasks with group/filter/sort/count/facets.

    Args:
        index: A :class:`~acp.scheduler.tasks.TaskIndex` instance.
        q: The query specification.

    Returns:
        A dict with keys: groups, facets, total, truncated, counts, query.
    """
    where_clauses, params, tag_python_fallback = _build_where_clauses(q)

    # --- search (case-insensitive, LIKE with escaping) ---
    search_clauses: list[str] = []
    if q.search.strip():
        escaped = _escape_like(q.search.strip())
        pattern = f"%{escaped}%"
        for col in ("t.molecule_name", "t.task_name", "t.remark", "t.display_name"):
            search_clauses.append(f"LOWER({col}) LIKE ? ESCAPE '\\'")
            params.append(pattern.lower())

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    base_sql = f"FROM tasks t WHERE {where_sql}"
    if search_clauses:
        base_sql += f" AND ({' OR '.join(search_clauses)})"

    with index._lock:
        conn = index._connect()
        try:
            project_names = _resolve_project_names(conn)
            from acp.scheduler.jobs import JobStatus as _JobStatus

            total_row = conn.execute(
                f"SELECT COUNT(DISTINCT t.task_id) as cnt {base_sql}", params
            ).fetchone()
            total: int = total_row["cnt"] if total_row else 0

            counts_sql = (
                f"SELECT t.status, COUNT(DISTINCT t.task_id) as cnt {base_sql} GROUP BY t.status"
            )
            counts_rows = conn.execute(counts_sql, params).fetchall()
            counts: dict[str, int] = {s.value: 0 for s in _JobStatus}
            for row in counts_rows:
                counts[row["status"]] = row["cnt"]

            sort_sql = _SORT_SQL[q.sort]
            fetch_sql = (
                f"SELECT t.task_id, t.job_id, t.project_id, t.molecule_name, t.task_name, "
                f"t.remark, t.display_name, t.workflow, t.task_dir_name, t.status, "
                f"t.node_id, t.node_path, t.input_hash, t.result_manifest_path, "
                f"t.current_stage, t.storage_mode, t.layout_version, t.created_at, "
                f"t.updated_at, t.molecule_key, t.tags, t.archived, t.batch_id, "
                f"t.last_activity_at, t.started_at, t.completed_at, t.group_id, t.progress "
                f"{base_sql} ORDER BY {sort_sql}"
            )
            all_rows = conn.execute(fetch_sql, params).fetchall()

            facets = _build_facets(conn, q)
        finally:
            if index._shared_conn is None:
                conn.close()

    if tag_python_fallback and q.tags:
        tag_set = set(q.tags)
        filtered = []
        for row in all_rows:
            task_tags = json.loads(row["tags"]) if row["tags"] else []
            if tag_set & set(task_tags):
                filtered.append(row)
        all_rows = filtered
        task_ids = {r["task_id"] for r in all_rows}
        total = len(task_ids)
        counts = {s.value: 0 for s in _JobStatus}
        seen_ids: set[str] = set()
        for r in all_rows:
            if r["task_id"] not in seen_ids:
                seen_ids.add(r["task_id"])
                counts[r["status"]] = counts.get(r["status"], 0) + 1

    if q.running_first:
        active = [r for r in all_rows if r["status"] in _ACTIVE_STATUSES]
        inactive = [r for r in all_rows if r["status"] not in _ACTIVE_STATUSES]
        all_rows = active + inactive

    truncated_total = len(all_rows) > q.max_total
    if truncated_total:
        all_rows = all_rows[: q.max_total]

    rows = [dict(r) for r in all_rows]
    groups = _build_groups(rows, q, project_names)

    return {
        "groups": groups,
        "facets": facets,
        "total": total,
        "truncated": truncated_total or any(g["truncated"] for g in groups),
        "counts": counts,
        "query": {
            "project_id": q.project_id,
            "group_by": q.group_by.value,
            "sort": q.sort.value,
            "statuses": q.statuses,
            "workflows": q.workflows,
            "molecule_keys": q.molecule_keys,
            "tags": q.tags,
            "batch_ids": q.batch_ids,
            "remarks": q.remarks,
            "search": q.search,
            "archived": q.archived.value,
            "running_first": q.running_first,
        },
    }


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _build_groups(
    rows: list[dict[str, Any]],
    q: TaskViewQuery,
    project_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Group rows by the specified dimension and apply group_limit."""
    group_by = q.group_by

    if group_by == GroupBy.none:
        # Single group
        capped = len(rows) > q.group_limit
        return [
            {
                "key": "__all__",
                "display_name": "__all__",
                "count": len(rows),
                "truncated": capped,
                "jobs": [_row_to_task(r, project_names) for r in rows[: q.group_limit]],
            }
        ]

    if group_by == GroupBy.tag:
        return _build_tag_groups(rows, q, project_names)

    # Molecule / remark / workflow / batch
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    if group_by == GroupBy.molecule:
        for r in rows:
            key = r["molecule_key"] or ""
            if not key:
                key = "__unassigned__"
            buckets[key].append(r)

    elif group_by == GroupBy.remark:
        for r in rows:
            key = r["remark"] or ""
            if not key:
                key = "__unassigned__"
            buckets[key].append(r)

    elif group_by == GroupBy.workflow:
        for r in rows:
            key = r["workflow"] or "unknown"
            buckets[key].append(r)

    elif group_by == GroupBy.batch:
        for r in rows:
            key = r["batch_id"] if r["batch_id"] is not None else "__singles__"
            buckets[key].append(r)

    # Sort groups by sort's representative value.
    # Rule: desc sorts → groups DESCENDING by rep, key ASC secondary.
    #        asc sorts → groups ASCENDING by rep, key ASC secondary.
    # Python's sorted() is stable, so sort key-ASC first then rep-DESC
    # yields same-rep groups in key-ASC order.
    def _rep(item: tuple[str, list[dict[str, Any]]]) -> str:
        key, group_rows = item
        if q.sort in (TaskSort.created_desc, TaskSort.created_asc):
            times = [r["created_at"] for r in group_rows if r.get("created_at")]
            if q.sort == TaskSort.created_desc:
                return max(times) if times else ""
            return min(times) if times else ""
        if q.sort == TaskSort.completed_desc:
            times = [
                r["completed_at"] or r["created_at"]
                for r in group_rows
                if r.get("completed_at") or r.get("created_at")
            ]
            return max(times) if times else ""
        if q.sort == TaskSort.activity_desc:
            times = [
                r["last_activity_at"] or r["created_at"]
                for r in group_rows
                if r.get("last_activity_at") or r.get("created_at")
            ]
            return max(times) if times else ""
        return key

    is_desc = q.sort in (
        TaskSort.created_desc,
        TaskSort.completed_desc,
        TaskSort.activity_desc,
        TaskSort.name_desc,
    )
    items = list(buckets.items())
    # Step 1: stable sort by key ASC
    items.sort(key=lambda x: x[0])
    # Step 2: stable sort by representative (ASC or DESC)
    items.sort(key=lambda x: _rep(x), reverse=is_desc)

    result: list[dict[str, Any]] = []
    for key, group_rows in items:
        capped = len(group_rows) > q.group_limit
        group: dict[str, Any] = {
            "key": key,
            "display_name": key,
            "count": len(group_rows),
            "truncated": capped,
            "jobs": [_row_to_task(r, project_names) for r in group_rows[: q.group_limit]],
        }
        if key == "__unassigned__":
            group["unassigned"] = True
        if group_by == GroupBy.workflow:
            group["retired"] = _is_retired(key)
        if group_by == GroupBy.batch and key != "__singles__":
            # min_created_at for batch display
            times = [r["created_at"] for r in group_rows if r.get("created_at")]
            group["min_created_at"] = min(times) if times else None
        result.append(group)

    return result


def _build_tag_groups(
    rows: list[dict[str, Any]],
    q: TaskViewQuery,
    project_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Build tag groups — a task can appear in multiple groups."""
    if not _JSON1_OK:
        raise ValueError("tag grouping requires SQLite JSON1")

    tag_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    untagged: list[dict[str, Any]] = []

    for r in rows:
        task_tags = json.loads(r["tags"]) if r["tags"] else []
        if not task_tags:
            untagged.append(r)
        else:
            for tag in task_tags:
                tag_buckets[tag].append(r)

    result: list[dict[str, Any]] = []

    # Tag groups sorted by count descending, then by tag name
    for tag_name in sorted(tag_buckets, key=lambda t: (-len(tag_buckets[t]), t)):
        group_rows = tag_buckets[tag_name]
        capped = len(group_rows) > q.group_limit
        result.append(
            {
                "key": tag_name,
                "display_name": tag_name,
                "count": len(group_rows),
                "truncated": capped,
                "jobs": [_row_to_task(r, project_names) for r in group_rows[: q.group_limit]],
            }
        )

    if untagged:
        capped = len(untagged) > q.group_limit
        result.append(
            {
                "key": "__untagged__",
                "display_name": "__untagged__",
                "count": len(untagged),
                "truncated": capped,
                "jobs": [_row_to_task(r, project_names) for r in untagged[: q.group_limit]],
                "unassigned": True,
            }
        )

    return result


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def _build_where_clauses(
    q: TaskViewQuery,
) -> tuple[list[str], list[Any], bool]:
    """Build WHERE clauses for the given query.

    Returns (clauses, params, tag_python_fallback).
    """
    clauses: list[str] = []
    params: list[Any] = []
    tag_python_fallback = False

    if q.project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(q.project_id)

    if q.archived == ArchivedFilter.exclude:
        clauses.append("t.archived = 0")
    elif q.archived == ArchivedFilter.only:
        clauses.append("t.archived = 1")

    if q.statuses:
        placeholders = ", ".join("?" for _ in q.statuses)
        clauses.append(f"t.status IN ({placeholders})")
        params.extend(q.statuses)

    if q.workflows:
        placeholders = ", ".join("?" for _ in q.workflows)
        clauses.append(f"t.workflow IN ({placeholders})")
        params.extend(q.workflows)

    if q.molecule_keys:
        placeholders = ", ".join("?" for _ in q.molecule_keys)
        clauses.append(f"t.molecule_key IN ({placeholders})")
        params.extend(q.molecule_keys)

    if q.batch_ids:
        placeholders = ", ".join("?" for _ in q.batch_ids)
        clauses.append(f"t.batch_id IN ({placeholders})")
        params.extend(q.batch_ids)

    if q.remarks:
        placeholders = ", ".join("?" for _ in q.remarks)
        clauses.append(f"t.remark IN ({placeholders})")
        params.extend(q.remarks)

    if q.tags:
        if _JSON1_OK:
            tag_conditions = []
            for tag_val in q.tags:
                tag_conditions.append(
                    "EXISTS (SELECT 1 FROM json_each(t.tags) WHERE json_each.value = ?)"
                )
                params.append(tag_val)
            clauses.append(f"({' OR '.join(tag_conditions)})")
        else:
            tag_python_fallback = True

    return clauses, params, tag_python_fallback


def _build_facets(
    conn: sqlite3.Connection,
    q: TaskViewQuery,
) -> dict[str, Any]:
    """Build facets scoped to project + archived but without drill-down filters.

    Project scope and archived filter apply; all drill-down filters
    (statuses, workflows, molecule_keys, tags, batch_ids, remarks,
    search) are removed so each facet dimension shows its own full
    cardinality within the project scope.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if q.project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(q.project_id)
    if q.archived == ArchivedFilter.exclude:
        clauses.append("t.archived = 0")
    elif q.archived == ArchivedFilter.only:
        clauses.append("t.archived = 1")
    where = " AND ".join(clauses) if clauses else "1=1"
    base = f"FROM tasks t WHERE {where}"

    facets: dict[str, Any] = {}

    rows = conn.execute(
        f"SELECT t.status, COUNT(DISTINCT t.task_id) as cnt {base} GROUP BY t.status",
        params,
    ).fetchall()
    facets["statuses"] = {row["status"]: row["cnt"] for row in rows}

    rows = conn.execute(
        f"SELECT t.workflow, COUNT(DISTINCT t.task_id) as cnt {base} GROUP BY t.workflow",
        params,
    ).fetchall()
    facets["workflows"] = [
        {"key": row["workflow"], "count": row["cnt"]}
        for row in sorted(rows, key=lambda r: -r["cnt"])
    ]

    rows = conn.execute(
        f"SELECT t.molecule_key, t.molecule_name, "
        f"COUNT(DISTINCT t.task_id) as cnt "
        f"{base} GROUP BY t.molecule_key",
        params,
    ).fetchall()
    facets["molecules"] = [
        {
            "key": row["molecule_key"] or "__unassigned__",
            "name": row["molecule_name"] or (row["molecule_key"] or "__unassigned__"),
            "count": row["cnt"],
        }
        for row in sorted(rows, key=lambda r: -r["cnt"])
    ]

    if _JSON1_OK:
        tag_base = base.replace(
            "FROM tasks t",
            "FROM tasks t, json_each(t.tags) je",
        )
        rows = conn.execute(
            f"SELECT je.value as tag, COUNT(DISTINCT t.task_id) as cnt "
            f"{tag_base} GROUP BY je.value",
            params,
        ).fetchall()
        facets["tags"] = [
            {"tag": row["tag"], "count": row["cnt"]}
            for row in sorted(rows, key=lambda r: -r["cnt"])
        ]
    else:
        facets["tags"] = []

    rows = conn.execute(
        f"SELECT t.batch_id, MIN(t.created_at) as min_ca, "
        f"COUNT(DISTINCT t.task_id) as cnt "
        f"{base} GROUP BY t.batch_id",
        params,
    ).fetchall()
    facets["batches"] = [
        {
            "batch_id": row["batch_id"] or "__singles__",
            "min_created_at": row["min_ca"],
            "count": row["cnt"],
        }
        for row in sorted(rows, key=lambda r: -r["cnt"])
    ]

    return facets


# ---------------------------------------------------------------------------
# Row → TaskRow dict
# ---------------------------------------------------------------------------


def _row_to_task(
    row: dict[str, Any],
    project_names: dict[str, str],
) -> dict[str, Any]:
    """Convert a DB row dict to a TaskRow dict aligned with V1JobRecordModel."""
    tags = json.loads(row["tags"]) if row.get("tags") else []
    project_id = row.get("project_id") or ""
    return {
        "id": row["task_id"],
        "status": row["status"],
        "group_id": row.get("group_id"),
        "project_id": project_id,
        "project_name": project_names.get(project_id, ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "last_activity_at": row.get("last_activity_at"),
        "current_stage": row.get("current_stage"),
        "progress": row.get("progress"),
        "molecule_name": row["molecule_name"],
        "task_name": row["task_name"],
        "remark": row["remark"],
        "display_name": row["display_name"],
        "task_dir_name": row["task_dir_name"],
        "workflow": row["workflow"],
        "tags": tags,
        "archived": bool(row.get("archived")),
        "batch_id": row.get("batch_id"),
        "spec": {
            "workflow": row["workflow"],
            "molecule_name": row["molecule_name"],
            "task_name": row["task_name"],
            "remark": row["remark"],
            "tags": tags,
        },
    }


__all__ = [
    "ArchivedFilter",
    "GroupBy",
    "TaskSort",
    "TaskViewQuery",
    "query_project_tasks",
]
