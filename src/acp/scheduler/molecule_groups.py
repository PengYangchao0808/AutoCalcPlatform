"""
Molecule Groups & Aliases
=========================

Project-level molecule alias resolution, group merge, and merge
suggestion engine.  Aliases map variant spellings to a canonical
``group_key``; merge rewrites ``tasks.molecule_key`` in bulk so the
task-view groups them under one heading.

Tables (migration 015):
    molecule_groups  — (project_id, group_key) → display_name
    molecule_aliases — (project_id, alias_key)  → group_key
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Protocol for accepting either a TaskIndex or a raw sqlite3.Connection
# ---------------------------------------------------------------------------


class _Queryable(Protocol):
    def _query(self, sql: str, params: tuple[Any, ...] = ...) -> list[sqlite3.Row]: ...

    def _run(self, sql: str, params: tuple[Any, ...] = ...) -> None: ...


def _get_conn(obj: _Queryable | sqlite3.Connection) -> sqlite3.Connection:
    """Return a raw connection from a TaskIndex or pass through a Connection."""
    if isinstance(obj, sqlite3.Connection):
        return obj
    # TaskIndex exposes _connect() for write paths and _query for reads.
    # For alias lookups we need a connection; use _connect under _lock.
    # We work through the existing _query / _run methods to stay thread-safe.
    return obj  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# resolve_molecule_key
# ---------------------------------------------------------------------------


def resolve_molecule_key(
    index_or_conn: _Queryable | sqlite3.Connection,
    project_id: str,
    raw_name: str,
) -> str:
    """Resolve *raw_name* to the effective ``molecule_key``.

    1. Check ``molecule_aliases`` for an alias hit → return its ``group_key``.
    2. Otherwise fall back to ``molecule_group_key(raw_name)``.
    """
    from acp.scheduler.naming import molecule_group_key

    alias_key = molecule_group_key(raw_name)
    if not alias_key:
        return ""

    # Try alias lookup
    if isinstance(index_or_conn, sqlite3.Connection):
        row = index_or_conn.execute(
            "SELECT group_key FROM molecule_aliases "
            "WHERE project_id=? AND alias_key=?",
            (project_id, alias_key),
        ).fetchone()
    else:
        rows = index_or_conn._query(
            "SELECT group_key FROM molecule_aliases "
            "WHERE project_id=? AND alias_key=?",
            (project_id, alias_key),
        )
        row = rows[0] if rows else None

    if row is not None:
        return row["group_key"] if isinstance(row, sqlite3.Row) else row[0]

    return alias_key


# ---------------------------------------------------------------------------
# apply_group_merge
# ---------------------------------------------------------------------------


def apply_group_merge(
    index_or_conn: _Queryable | sqlite3.Connection,
    project_id: str,
    alias_keys: list[str],
    target_key: str,
) -> int:
    """Rewrite ``tasks.molecule_key`` for all tasks whose key is in *alias_keys*.

    Each affected row gets ``molecule_key = target_key``.  Returns the number
    of updated rows.

    Also upserts a ``molecule_groups`` row for the target and registers
    alias entries for each merged key so future resolves hit immediately.
    """
    if not alias_keys:
        return 0

    now = _utc_now_iso()

    if isinstance(index_or_conn, sqlite3.Connection):
        conn = index_or_conn
        placeholders = ",".join("?" for _ in alias_keys)
        params: tuple[Any, ...] = (target_key, now, project_id, *alias_keys)
        cursor = conn.execute(
            f"UPDATE tasks SET molecule_key=?, updated_at=? "
            f"WHERE project_id=? AND molecule_key IN ({placeholders})",
            params,
        )
        updated = cursor.rowcount

        conn.execute(
            "INSERT INTO molecule_groups "
            "(project_id, group_key, display_name, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, group_key) "
            "DO UPDATE SET updated_at=excluded.updated_at",
            (project_id, target_key, target_key, now, now),
        )

        for ak in alias_keys:
            if ak != target_key:
                conn.execute(
                    "INSERT INTO molecule_aliases "
                    "(project_id, alias_key, group_key, "
                    "created_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(project_id, alias_key) "
                    "DO UPDATE SET group_key=excluded.group_key",
                    (project_id, ak, target_key, now),
                )

        conn.commit()
        return updated

    # TaskIndex path — use _run for writes
    idx: _Queryable = index_or_conn
    placeholders = ",".join("?" for _ in alias_keys)
    # We need the rowcount, but _run doesn't return it.  Use a direct query
    # through the connection obtained via a write transaction.
    # TaskIndex._run commits immediately — we must batch the updates.
    with idx._lock if hasattr(idx, "_lock") else _NoopContext():
        conn_raw = idx._connect()  # type: ignore[attr-defined]
        try:
            cursor = conn_raw.execute(
                f"UPDATE tasks SET molecule_key=?, updated_at=? "
                f"WHERE project_id=? AND molecule_key IN ({placeholders})",
                (target_key, now, project_id, *alias_keys),
            )
            updated = cursor.rowcount

            conn_raw.execute(
                "INSERT INTO molecule_groups "
                "(project_id, group_key, display_name, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, group_key) "
                "DO UPDATE SET updated_at=excluded.updated_at",
                (project_id, target_key, target_key, now, now),
            )

            for ak in alias_keys:
                if ak != target_key:
                    conn_raw.execute(
                        "INSERT INTO molecule_aliases "
                        "(project_id, alias_key, group_key, "
                        "created_at) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(project_id, alias_key) "
                        "DO UPDATE SET "
                        "group_key=excluded.group_key",
                        (project_id, ak, target_key, now),
                    )

            conn_raw.commit()
            return updated
        finally:
            if idx._shared_conn is None:  # type: ignore[attr-defined]
                conn_raw.close()


class _NoopContext:
    def __enter__(self) -> _NoopContext:
        return self

    def __exit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# suggest_group_merges  (read-only pure function)
# ---------------------------------------------------------------------------

_SEPARATOR_NORMALIZE_RE = re.compile(r"[-_\s]+")


def _separator_normalize(name: str) -> str:
    """Replace [-_] and whitespace with a single space, then casefold."""
    return _SEPARATOR_NORMALIZE_RE.sub(" ", name).casefold().strip()


def suggest_group_merges(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Produce merge suggestions from a list of task row dicts.

    Each row must have ``molecule_key`` and ``molecule_name``.  The function
    finds pairs of *distinct* ``molecule_key`` values whose display names
    (``molecule_name``) are similar enough to suggest merging.

    Returns a list of ``{"a": key1, "b": key2, "reason": reason}`` dicts.
    ``reason`` is one of:

    * ``"casefold-equal"`` — ``molecule_name.casefold()`` matches but keys differ
    * ``"separator-normalized-equal"`` — after normalizing separators + casefold,
      the names match but keys differ

    Never auto-applies suggestions.
    """
    # Collect distinct (molecule_key → molecule_name) mappings
    key_to_names: dict[str, set[str]] = {}
    for row in rows:
        mk = row.get("molecule_key", "")
        mn = row.get("molecule_name", "")
        if not mk:
            continue
        key_to_names.setdefault(mk, set()).add(mn)

    keys = sorted(key_to_names)
    suggestions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for i, key_a in enumerate(keys):
        names_a = key_to_names[key_a]
        cf_a = {n.casefold() for n in names_a}
        sep_a = {_separator_normalize(n) for n in names_a}

        for key_b in keys[i + 1 :]:
            if (key_a, key_b) in seen:
                continue
            names_b = key_to_names[key_b]
            cf_b = {n.casefold() for n in names_b}
            sep_b = {_separator_normalize(n) for n in names_b}

            # casefold-equal: display names match when casefolded
            if cf_a & cf_b:
                suggestions.append({"a": key_a, "b": key_b, "reason": "casefold-equal"})
                seen.add((key_a, key_b))
                continue

            # separator-normalized-equal
            if sep_a & sep_b:
                suggestions.append(
                    {"a": key_a, "b": key_b, "reason": "separator-normalized-equal"}
                )
                seen.add((key_a, key_b))

    return suggestions


__all__ = [
    "apply_group_merge",
    "resolve_molecule_key",
    "suggest_group_merges",
]
