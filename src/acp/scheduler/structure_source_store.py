"""
Structure Source Store
=====================

Persistent index and user-metadata storage for reusable structure sources.
Schema is self-managed via ``CREATE TABLE IF NOT EXISTS`` so the store
can initialise before the migration ``017`` adds these tables.

Design contract (ACP_Task_Structure_Organization_Plan §5.2):

- ``structure_source_index``: discovery-derived fields, rebuildable.
- ``structure_source_metadata``: user edits (custom_name, revision),
  never overwritten by index rebuild.
- ``structure_source_tags``: per-source tag relations, never overwritten
  by index rebuild.
- ``organization_events``: audit trail for metadata mutations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from acp.scheduler.migrations import migrate

logger = logging.getLogger(__name__)

__all__ = [
    "CandidateUseConflictError",
    "RevisionConflictError",
    "StructureSourceStore",
    "source_uid_for",
]

_SCHEMA_CANDIDATES = """
CREATE TABLE IF NOT EXISTS structure_candidates (
    candidate_record_id TEXT PRIMARY KEY,
    source_uid TEXT NOT NULL UNIQUE,
    usage_status TEXT NOT NULL DEFAULT 'active',
    status_reason TEXT,
    status_scope TEXT,
    status_revision INTEGER NOT NULL DEFAULT 0,
    status_updated_at TEXT
);
"""

_SCHEMA_VERSIONS = """
CREATE TABLE IF NOT EXISTS structure_candidate_versions (
    version_id TEXT PRIMARY KEY,
    candidate_record_id TEXT NOT NULL,
    geometry_hash TEXT NOT NULL,
    source_revision TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_record_id, geometry_hash, source_revision)
);
"""

_SCHEMA_ASSESSMENTS = """
CREATE TABLE IF NOT EXISTS structure_candidate_assessments (
    assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_record_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    scope TEXT NOT NULL,
    note TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    attachment_refs_json TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);
"""

_SCHEMA_USAGE = """
CREATE TABLE IF NOT EXISTS structure_candidate_usage (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_record_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    consumer_job_id TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    disabled_exception INTEGER NOT NULL DEFAULT 0,
    exception_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(candidate_record_id, version_id, consumer_job_id)
);
"""

_SCHEMA_TOMBSTONES = """
CREATE TABLE IF NOT EXISTS structure_candidate_tombstones (
    candidate_record_id TEXT NOT NULL,
    geometry_hash TEXT NOT NULL,
    source_revision TEXT NOT NULL DEFAULT '',
    removed_at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (candidate_record_id, geometry_hash, source_revision)
);
"""

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_INDEX = """
CREATE TABLE IF NOT EXISTS structure_source_index (
    source_uid TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    legacy_source_id TEXT NOT NULL,
    project_id TEXT,
    job_name TEXT NOT NULL DEFAULT '',
    molecule_name TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    job_status TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    role_evidence TEXT NOT NULL DEFAULT '',
    formula TEXT NOT NULL DEFAULT '',
    atom_count INTEGER,
    charge INTEGER,
    multiplicity INTEGER,
    has_3d INTEGER NOT NULL DEFAULT 0,
    remote INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'available',
    produced_at TEXT,
    organized_at TEXT,
    content_checksum TEXT,
    discovered_at TEXT NOT NULL,
    discovery_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(job_id, relative_path)
);
"""

_SCHEMA_METADATA = """
CREATE TABLE IF NOT EXISTS structure_source_metadata (
    source_uid TEXT PRIMARY KEY,
    custom_name TEXT,
    metadata_revision INTEGER NOT NULL DEFAULT 0,
    metadata_updated_at TEXT
);
"""

_SCHEMA_TAGS = """
CREATE TABLE IF NOT EXISTS structure_source_tags (
    source_uid TEXT NOT NULL,
    tag_key TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_uid, tag_key)
);
"""

_SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS organization_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL
);
"""

_SCHEMA_INDEX_STATE = """
CREATE TABLE IF NOT EXISTS structure_source_index_state (
    job_id TEXT PRIMARY KEY,
    indexed_at TEXT NOT NULL,
    discovery_version INTEGER NOT NULL
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ss_index_project ON structure_source_index (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_ss_index_job ON structure_source_index (job_id)",
    "CREATE INDEX IF NOT EXISTS idx_ss_index_workflow ON structure_source_index (workflow)",
    "CREATE INDEX IF NOT EXISTS idx_ss_index_role ON structure_source_index (role)",
    "CREATE INDEX IF NOT EXISTS idx_ss_index_produced ON structure_source_index (produced_at)",
    "CREATE INDEX IF NOT EXISTS idx_ss_tags_key ON structure_source_tags (tag_key)",
    "CREATE INDEX IF NOT EXISTS idx_ss_events_obj ON organization_events (object_type, object_id)",
]

_ALL_SCHEMA = "\n".join(
    [
        _SCHEMA_INDEX,
        _SCHEMA_METADATA,
        _SCHEMA_TAGS,
        _SCHEMA_EVENTS,
        _SCHEMA_INDEX_STATE,
        _SCHEMA_CANDIDATES,
        _SCHEMA_VERSIONS,
        _SCHEMA_ASSESSMENTS,
        _SCHEMA_USAGE,
        _SCHEMA_TOMBSTONES,
    ]
)

# ---------------------------------------------------------------------------
# Discovery-only columns (written by upsert_index_entries)
# ---------------------------------------------------------------------------

_DISCOVERY_COLUMNS: tuple[str, ...] = (
    "job_id",
    "relative_path",
    "legacy_source_id",
    "project_id",
    "job_name",
    "molecule_name",
    "workflow",
    "job_status",
    "source_kind",
    "label",
    "candidate_id",
    "role",
    "role_evidence",
    "formula",
    "atom_count",
    "charge",
    "multiplicity",
    "has_3d",
    "remote",
    "availability",
    "produced_at",
    "content_checksum",
    "discovered_at",
    "discovery_version",
)

_VALID_AVAILABILITY = frozenset({"available", "pending_fetch", "unavailable", "pending_sync"})
_VALID_ROLES = frozenset({"TS", "INT", ""})
_VALID_USAGE_STATUSES = frozenset({"active", "trash"})
_VALID_CONCLUSIONS = frozenset({"unreviewed", "recommended", "review", "not_recommended"})
_VALID_SOURCE_GROUPS = frozenset({"candidate", "task_result"})
_MAX_NAME_LEN = 200
_MAX_TAG_LEN = 32
_MAX_TAGS_PER_SOURCE = 20
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_uid_for(job_id: str, relative_path: str) -> str:
    """Deterministic ``source_uid`` from ``(job_id, relative_path)``.

    Path is normalised to POSIX (strip leading ``/``, collapse ``.``, no
    case folding) before hashing.
    """
    posix = _normalize_posix(relative_path)
    raw = f"{job_id}\n{posix}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"ss_{digest}"


def _normalize_posix(path: str) -> str:
    """Normalise a relative path to POSIX without case folding."""
    p = PurePosixPath(path)
    # Strip leading slashes
    parts = p.parts
    while parts and parts[0] == "/":
        parts = parts[1:]
    # Collapse . and ..
    cleaned: list[str] = []
    for part in parts:
        if part == ".":
            continue
        if part == ".." and cleaned:
            cleaned.pop()
        else:
            cleaned.append(part)
    return "/".join(cleaned)


def _validate_name(value: str | None, field: str = "name") -> str | None:
    """Validate a custom name field.

    Rules (plan §3):
    - ``None`` means restore default.
    - Strip whitespace; must be 1–200 Unicode chars.
    - Reject control chars (Unicode category Cc) including ``\\n``, ``\\r``,
      ``\\t``.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > _MAX_NAME_LEN:
        raise ValueError(f"{field} exceeds {_MAX_NAME_LEN} characters")
    for ch in value:
        if unicodedata.category(ch) == "Cc":
            raise ValueError(f"{field} contains control character: {ch!r}")
    return value


def _validate_tag(tag: str) -> str:
    """Validate and normalise a single tag.

    Rules (plan §4.2): strip, 1–32 chars, no control chars.
    """
    tag = tag.strip()
    if not tag:
        raise ValueError("tag must not be empty")
    if len(tag) > _MAX_TAG_LEN:
        raise ValueError(f"tag exceeds {_MAX_TAG_LEN} characters")
    for ch in tag:
        if unicodedata.category(ch) == "Cc":
            raise ValueError(f"tag contains control character: {ch!r}")
    return tag


def _tag_key(tag: str) -> str:
    """Canonical tag key: strip + casefold."""
    return tag.strip().casefold()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RevisionConflictError(Exception):
    """Optimistic-concurrency conflict on metadata revision."""

    def __init__(self, message: str, *, projection: dict[str, Any]):
        super().__init__(message)
        self.projection = projection


class CandidateUseConflictError(Exception):
    """A selected candidate is no longer eligible for normal submission."""

    def __init__(self, projection: dict[str, Any]):
        super().__init__(f"candidate is {projection.get('usage_status', 'unavailable')}")
        self.projection = projection


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StructureSourceStore:
    """Thread-safe SQLite storage for structure source index + metadata + tags.

    Uses the same DB file as :class:`~acp.scheduler.store.JobStore`.
    Schema is self-managed (``CREATE TABLE IF NOT EXISTS``).
    """

    def __init__(self, db_path: str | Any) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_ALL_SCHEMA)
            for idx_sql in _INDEXES:
                conn.execute(idx_sql)
            # The old UI exposed disabled and archived as separate states.  They
            # are intentionally folded into the single recoverable trash state.
            conn.execute(
                "UPDATE structure_candidates SET usage_status='trash', "
                "status_reason=NULL, status_scope='project' "
                "WHERE usage_status IN ('disabled', 'archived')"
            )
            conn.commit()
        # Run project-level migrations for any other tables
        migrate(Path(self._db_path))

    # ------------------------------------------------------------------ #
    # Index (discovery)
    # ------------------------------------------------------------------ #

    def upsert_index_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        discovery_version: int = 1,
    ) -> int:
        """Insert/update discovery fields only.

        Never touches ``structure_source_metadata`` or ``structure_source_tags``.
        Entries keyed by ``source_uid_for(job_id, path)``.
        Returns count of affected rows.
        """
        if not entries:
            return 0
        now = _utc_now_iso()
        count = 0
        with self._lock, self._connect() as conn:
            try:
                for entry in entries:
                    job_id = str(entry.get("job_id") or "")
                    rel_path = str(entry.get("path") or entry.get("relative_path") or "")
                    uid = source_uid_for(job_id, rel_path)
                    legacy_id = str(entry.get("source_id") or "")
                    project_id = entry.get("project_id")
                    params: dict[str, Any] = {
                        "source_uid": uid,
                        "job_id": job_id,
                        "relative_path": _normalize_posix(rel_path),
                        "legacy_source_id": legacy_id,
                        "project_id": project_id,
                        "job_name": str(entry.get("job_name") or ""),
                        "molecule_name": str(entry.get("molecule_name") or ""),
                        "workflow": str(entry.get("workflow") or ""),
                        "job_status": str(entry.get("job_status") or ""),
                        "source_kind": str(entry.get("source_kind") or ""),
                        "label": str(entry.get("label") or ""),
                        "candidate_id": str(entry.get("candidate_id") or ""),
                        "role": str(entry.get("role") or ""),
                        "role_evidence": str(entry.get("role_evidence") or ""),
                        "formula": str(entry.get("formula") or ""),
                        "atom_count": entry.get("atom_count"),
                        "charge": entry.get("charge"),
                        "multiplicity": entry.get("multiplicity"),
                        "has_3d": int(bool(entry.get("has_3d"))),
                        "remote": int(bool(entry.get("remote"))),
                        "availability": str(entry.get("availability") or "available"),
                        "produced_at": entry.get("produced_at"),
                        "content_checksum": entry.get("content_checksum"),
                        "discovered_at": now,
                        "discovery_version": discovery_version,
                    }
                    candidate_record_id = self._candidate_record_id(uid)
                    geometry_hash, source_revision = self._version_identity(params)
                    removed = conn.execute(
                        "SELECT 1 FROM structure_candidate_tombstones "
                        "WHERE candidate_record_id=? AND geometry_hash=? AND source_revision=?",
                        (candidate_record_id, geometry_hash, source_revision),
                    ).fetchone()
                    if removed is not None:
                        # Full rebuilds may have recreated the discovery row before
                        # this exact version was checked.  Keep the tombstone
                        # authoritative without touching source calculation files.
                        conn.execute(
                            "DELETE FROM structure_source_index WHERE source_uid=?", (uid,)
                        )
                        continue
                    cols = ", ".join(params.keys())
                    placeholders = ", ".join("?" for _ in params)
                    update_parts = ", ".join(f"{c}=excluded.{c}" for c in _DISCOVERY_COLUMNS)
                    conn.execute(
                        f"INSERT INTO structure_source_index ({cols}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT(source_uid) DO UPDATE SET {update_parts}",
                        tuple(params.values()),
                    )
                    self._ensure_candidate_version_tx(conn, uid, params)
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    # ------------------------------------------------------------------ #
    # Read (joined projection)
    # ------------------------------------------------------------------ #

    def _join_projection(self, where: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """LEFT JOIN metadata + tags + tasks for each matching source."""
        sql = f"""
            SELECT
                i.*,
                m.custom_name,
                m.metadata_revision,
                m.metadata_updated_at,
                t.custom_name AS task_custom_name,
                t.display_name AS task_display_name,
                c.candidate_record_id,
                c.usage_status,
                c.status_reason,
                c.status_scope,
                c.status_revision,
                c.status_updated_at,
                v.version_id,
                v.geometry_hash,
                v.source_revision,
                (SELECT a.conclusion FROM structure_candidate_assessments a
                 WHERE a.candidate_record_id = c.candidate_record_id
                   AND a.version_id = v.version_id
                 ORDER BY a.assessment_id DESC LIMIT 1) AS assessment,
                (SELECT COUNT(*) FROM structure_candidate_usage u
                 WHERE u.candidate_record_id = c.candidate_record_id) AS usage_count
            FROM structure_source_index i
            LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid
            LEFT JOIN tasks t ON t.task_id = i.job_id
            LEFT JOIN structure_candidates c ON c.source_uid = i.source_uid
            LEFT JOIN structure_candidate_versions v ON v.version_id = (
                SELECT v2.version_id FROM structure_candidate_versions v2
                WHERE v2.candidate_record_id = c.candidate_record_id
                ORDER BY v2.created_at DESC, v2.rowid DESC LIMIT 1)
            {where}
        """
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                sql_fallback = f"""
                    SELECT
                        i.*,
                        m.custom_name,
                        m.metadata_revision,
                        m.metadata_updated_at,
                        c.candidate_record_id,
                        c.usage_status,
                        c.status_reason,
                        c.status_scope,
                        c.status_revision,
                        c.status_updated_at,
                        v.version_id,
                        v.geometry_hash,
                        v.source_revision,
                        (SELECT a.conclusion FROM structure_candidate_assessments a
                         WHERE a.candidate_record_id = c.candidate_record_id
                           AND a.version_id = v.version_id
                         ORDER BY a.assessment_id DESC LIMIT 1) AS assessment,
                        (SELECT COUNT(*) FROM structure_candidate_usage u
                         WHERE u.candidate_record_id = c.candidate_record_id) AS usage_count
                    FROM structure_source_index i
                    LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid
                    LEFT JOIN structure_candidates c ON c.source_uid = i.source_uid
                    LEFT JOIN structure_candidate_versions v ON v.version_id = (
                        SELECT v2.version_id FROM structure_candidate_versions v2
                        WHERE v2.candidate_record_id = c.candidate_record_id
                        ORDER BY v2.created_at DESC, v2.rowid DESC LIMIT 1)
                    {where}
                """
                rows = conn.execute(sql_fallback, params).fetchall()
            all_uids = [row["source_uid"] for row in rows]
            tags_map: dict[str, list[str]] = {}
            if all_uids:
                placeholders = ",".join("?" for _ in all_uids)
                tag_rows = conn.execute(
                    f"SELECT source_uid, tag FROM structure_source_tags "
                    f"WHERE source_uid IN ({placeholders}) ORDER BY tag_key",
                    tuple(all_uids),
                ).fetchall()
                for tr in tag_rows:
                    tags_map.setdefault(tr["source_uid"], []).append(tr["tag"])
        results: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            uid = d["source_uid"]
            d["tags"] = tags_map.get(uid, [])
            d["source_id"] = d.pop("legacy_source_id", "")
            d["default_name"] = d.get("label", "")
            d["resolved_name"] = d.get("custom_name") or d.get("label", "")
            task_custom = d.pop("task_custom_name", None)
            task_display = d.pop("task_display_name", None)
            d["job_resolved_name"] = task_custom or task_display or d.get("job_name", "")
            d["usage_status"] = d.get("usage_status") or "active"
            d["assessment"] = d.get("assessment") or "unreviewed"
            d["usage_count"] = int(d.get("usage_count") or 0)
            results.append(d)
        return results

    @staticmethod
    def _candidate_record_id(source_uid: str) -> str:
        return "cand_" + hashlib.sha256(source_uid.encode("utf-8")).hexdigest()[:24]

    def _ensure_candidate_version_tx(
        self, conn: sqlite3.Connection, source_uid: str, entry: dict[str, Any]
    ) -> None:
        """Create the stable candidate and exact geometry version without touching reviews."""
        now = _utc_now_iso()
        candidate_id = self._candidate_record_id(source_uid)
        conn.execute(
            "INSERT OR IGNORE INTO structure_candidates "
            "(candidate_record_id, source_uid) VALUES (?, ?)",
            (candidate_id, source_uid),
        )
        geometry_hash, source_revision = self._version_identity(entry)
        raw = f"{candidate_id}\n{geometry_hash}\n{source_revision}"
        version_id = "cv_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        conn.execute(
            "INSERT OR IGNORE INTO structure_candidate_versions "
            "(version_id, candidate_record_id, geometry_hash, source_revision, "
            "relative_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                version_id,
                candidate_id,
                geometry_hash,
                source_revision,
                str(entry.get("relative_path") or ""),
                now,
            ),
        )
        if conn.execute(
            "SELECT 1 FROM structure_candidate_tombstones WHERE candidate_record_id=? LIMIT 1",
            (candidate_id,),
        ).fetchone():
            conn.execute(
                "UPDATE structure_candidates SET usage_status='active', status_reason=NULL, "
                "status_scope='project', status_revision=status_revision+1, status_updated_at=? "
                "WHERE candidate_record_id=?",
                (now, candidate_id),
            )

    @staticmethod
    def _version_identity(entry: dict[str, Any]) -> tuple[str, str]:
        """Return the stable identity used by versions and purge tombstones."""
        geometry_hash = str(entry.get("content_checksum") or "")
        if not geometry_hash:
            geometry_hash = "unknown:" + hashlib.sha256(
                f"{entry.get('job_id', '')}\n{entry.get('relative_path', '')}".encode()
            ).hexdigest()
        return geometry_hash, str(entry.get("discovery_version") or "")

    def get(self, source_uid: str) -> dict[str, Any] | None:
        """Get a single source by source_uid."""
        results = self._join_projection("WHERE i.source_uid = ?", (source_uid,))
        return results[0] if results else None

    def get_by_legacy_source_id(self, source_id: str) -> dict[str, Any] | None:
        """Get a single source by legacy ``job_<id>:<path>`` source_id."""
        results = self._join_projection("WHERE i.legacy_source_id = ?", (source_id,))
        return results[0] if results else None

    def list_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """List all sources for a given job."""
        return self._join_projection("WHERE i.job_id = ?", (job_id,))

    # ------------------------------------------------------------------ #
    # Candidate lifecycle, version-bound reviews, and usage provenance
    # ------------------------------------------------------------------ #

    def set_candidate_status(
        self,
        source_uid: str,
        status: str,
        *,
        reason: str = "",
        scope: str = "project",
        expected_revision: int,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Move a candidate between the normal list and the recoverable trash."""
        if status not in _VALID_USAGE_STATUSES:
            raise ValueError(f"invalid usage status: {status}")
        reason = reason.strip()
        scope = scope.strip() or "project"
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structure_candidates WHERE source_uid = ?", (source_uid,)
            ).fetchone()
            if row is None:
                raise ValueError(f"source not found: {source_uid}")
            current_revision = int(row["status_revision"] or 0)
            if current_revision != expected_revision:
                raise RevisionConflictError("status revision mismatch", projection=dict(row))
            if (
                row["usage_status"] == status
                and (row["status_reason"] or "") == reason
                and (row["status_scope"] or "project") == scope
            ):
                unchanged = True
            else:
                unchanged = False
            if unchanged:
                conn.commit()
            else:
                new_revision = current_revision + 1
                conn.execute(
                    "UPDATE structure_candidates SET usage_status=?, status_reason=?, "
                    "status_scope=?, "
                    "status_revision=?, status_updated_at=? WHERE source_uid=?",
                    (status, reason or None, scope, new_revision, now, source_uid),
                )
                conn.execute(
                    "INSERT INTO organization_events "
                    "(object_type, object_id, action, old_value, new_value, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "structure_candidate",
                        row["candidate_record_id"],
                        "set_usage_status",
                        json.dumps({"status": row["usage_status"], "reason": row["status_reason"]}),
                        json.dumps(
                            {"status": status, "reason": reason, "scope": scope, "actor": actor},
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                conn.commit()
        return self.get(source_uid) or {}

    def purge_candidates(
        self,
        source_uids: list[str],
        *,
        project_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Permanently remove trashed library entries, preserving source jobs/files.

        A version-bound tombstone prevents discovery from recreating the same
        candidate.  A later geometry/source revision remains eligible to appear.
        """
        unique_uids = list(dict.fromkeys(source_uids))
        removed: list[str] = []
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            for uid in unique_uids:
                row = conn.execute(
                    "SELECT c.candidate_record_id, c.usage_status, i.project_id, "
                    "v.geometry_hash, v.source_revision FROM structure_candidates c "
                    "JOIN structure_source_index i ON i.source_uid=c.source_uid "
                    "JOIN structure_candidate_versions v ON v.version_id=("
                    "SELECT v2.version_id FROM structure_candidate_versions v2 "
                    "WHERE v2.candidate_record_id=c.candidate_record_id "
                    "ORDER BY v2.created_at DESC, v2.rowid DESC LIMIT 1) "
                    "WHERE c.source_uid=?",
                    (uid,),
                ).fetchone()
                if row is None:
                    continue
                if row["usage_status"] != "trash":
                    raise ValueError(f"candidate is not in trash: {uid}")
                if project_id is not None and row["project_id"] != project_id:
                    raise ValueError(f"candidate is outside current project: {uid}")
                conn.execute(
                    "INSERT OR IGNORE INTO structure_candidate_tombstones "
                    "(candidate_record_id, geometry_hash, source_revision, removed_at, actor) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["candidate_record_id"],
                        row["geometry_hash"],
                        row["source_revision"],
                        now,
                        actor,
                    ),
                )
                conn.execute("DELETE FROM structure_source_tags WHERE source_uid=?", (uid,))
                conn.execute("DELETE FROM structure_source_metadata WHERE source_uid=?", (uid,))
                conn.execute("DELETE FROM structure_source_index WHERE source_uid=?", (uid,))
                conn.execute(
                    "INSERT INTO organization_events "
                    "(object_type, object_id, action, old_value, new_value, created_at) "
                    "VALUES (?, ?, 'purge_candidate', 'trash', ?, ?)",
                    ("structure_candidate", row["candidate_record_id"], actor, now),
                )
                removed.append(uid)
            conn.commit()
        return {"removed": removed, "count": len(removed)}

    def add_assessment(
        self,
        source_uid: str,
        *,
        conclusion: str,
        reason_code: str,
        scope: str,
        note: str = "",
        evidence: list[dict[str, Any]] | None = None,
        attachment_refs: list[str] | None = None,
        actor: str = "user",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        """Append an immutable assessment bound to one exact geometry version."""
        if conclusion not in _VALID_CONCLUSIONS:
            raise ValueError(f"invalid conclusion: {conclusion}")
        if not reason_code.strip():
            raise ValueError("reason_code is required")
        if not scope.strip():
            raise ValueError("scope is required")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT c.candidate_record_id, v.version_id FROM structure_candidates c "
                "JOIN structure_candidate_versions v "
                "ON v.candidate_record_id=c.candidate_record_id "
                "WHERE c.source_uid=? "
                + ("AND v.version_id=? " if version_id else "")
                + "ORDER BY v.created_at DESC, v.rowid DESC LIMIT 1",
                (source_uid, version_id) if version_id else (source_uid,),
            ).fetchone()
            if row is None:
                raise ValueError(f"candidate version not found: {source_uid}")
            now = _utc_now_iso()
            conn.execute(
                "INSERT INTO structure_candidate_assessments "
                "(candidate_record_id, version_id, conclusion, reason_code, scope, note, "
                "evidence_json, attachment_refs_json, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["candidate_record_id"],
                    row["version_id"],
                    conclusion,
                    reason_code.strip(),
                    scope.strip(),
                    note.strip() or None,
                    json.dumps(evidence or [], ensure_ascii=False),
                    json.dumps(attachment_refs or [], ensure_ascii=False),
                    actor,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO organization_events "
                "(object_type, object_id, action, old_value, new_value, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "structure_candidate_version",
                    row["version_id"],
                    "add_assessment",
                    None,
                    json.dumps(
                        {
                            "conclusion": conclusion,
                            "reason_code": reason_code,
                            "scope": scope,
                            "actor": actor,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            conn.commit()
        return self.get_candidate_detail(source_uid)

    def get_candidate_detail(self, source_uid: str) -> dict[str, Any]:
        projection = self.get(source_uid)
        if projection is None:
            raise ValueError(f"source not found: {source_uid}")
        candidate_id = projection.get("candidate_record_id")
        with self._lock, self._connect() as conn:
            versions = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM structure_candidate_versions WHERE candidate_record_id=? "
                    "ORDER BY created_at DESC, rowid DESC",
                    (candidate_id,),
                ).fetchall()
            ]
            assessments = []
            for row in conn.execute(
                "SELECT * FROM structure_candidate_assessments WHERE candidate_record_id=? "
                "ORDER BY assessment_id DESC",
                (candidate_id,),
            ).fetchall():
                item = dict(row)
                item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
                item["attachment_refs"] = json.loads(item.pop("attachment_refs_json") or "[]")
                assessments.append(item)
            usage = []
            for row in conn.execute(
                "SELECT * FROM structure_candidate_usage WHERE candidate_record_id=? "
                "ORDER BY usage_id DESC",
                (candidate_id,),
            ).fetchall():
                item = dict(row)
                item["input_snapshot"] = json.loads(item.pop("input_snapshot_json") or "{}")
                usage.append(item)
            events = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM organization_events WHERE "
                    "(object_type='structure_candidate' AND object_id=?) OR "
                    "(object_type='structure_candidate_version' AND object_id IN "
                    "(SELECT version_id FROM structure_candidate_versions "
                    "WHERE candidate_record_id=?)) "
                    "ORDER BY id DESC",
                    (candidate_id, candidate_id),
                ).fetchall()
            ]
        return {
            **projection,
            "versions": versions,
            "assessments": assessments,
            "usage": usage,
            "history": events,
        }

    def validate_candidate_use(
        self,
        source_uid: str,
        *,
        allow_disabled: bool = False,
        justification: str = "",
    ) -> dict[str, Any]:
        """Re-read status at submit time; disabled use requires an explicit exception."""
        projection = self.get(source_uid)
        if projection is None:
            raise ValueError(f"source not found: {source_uid}")
        status = projection.get("usage_status", "active")
        if status != "active":
            raise CandidateUseConflictError(projection)
        return projection

    def trash_uids(self, project_id: str | None) -> list[str]:
        """Return every trashed candidate in a project, independent of UI filters."""
        with self._lock, self._connect() as conn:
            if project_id is None:
                rows = conn.execute(
                    "SELECT c.source_uid FROM structure_candidates c "
                    "JOIN structure_source_index i ON i.source_uid=c.source_uid "
                    "WHERE c.usage_status='trash' ORDER BY c.source_uid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT c.source_uid FROM structure_candidates c "
                    "JOIN structure_source_index i ON i.source_uid=c.source_uid "
                    "WHERE c.usage_status='trash' AND i.project_id=? ORDER BY c.source_uid",
                    (project_id,),
                ).fetchall()
        return [str(row["source_uid"]) for row in rows]

    def record_usage(
        self,
        source_uid: str,
        consumer_job_id: str,
        snapshot: dict[str, Any],
        *,
        disabled_exception: bool = False,
        exception_reason: str = "",
    ) -> None:
        """Persist an exact candidate/version/input snapshot link after submission."""
        projection = self.get(source_uid)
        if projection is None:
            raise ValueError(f"source not found: {source_uid}")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO structure_candidate_usage "
                "(candidate_record_id, version_id, consumer_job_id, input_snapshot_json, "
                "disabled_exception, exception_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    projection["candidate_record_id"],
                    projection["version_id"],
                    consumer_job_id,
                    json.dumps(snapshot, ensure_ascii=False),
                    int(disabled_exception),
                    exception_reason.strip() or None,
                    _utc_now_iso(),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Metadata mutations
    # ------------------------------------------------------------------ #

    def set_custom_name(
        self,
        source_uid: str,
        custom_name: str | None,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Set or restore default name for a structure source.

        ``custom_name=None`` restores default (clears the override).

        Raises:
            RevisionConflictError: when ``expected_revision`` mismatches.
            ValueError: on validation failure.
        """
        validated = _validate_name(custom_name, "custom_name")
        with self._lock, self._connect() as conn:
            try:
                return self._set_custom_name_tx(conn, source_uid, validated, expected_revision)
            except Exception:
                conn.rollback()
                raise

    def _set_custom_name_tx(
        self,
        conn: sqlite3.Connection,
        source_uid: str,
        custom_name: str | None,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Transactional set_custom_name (single connection, no lock)."""
        now = _utc_now_iso()
        # Read current state
        row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"source not found: {source_uid}")

        current_revision = row["metadata_revision"] or 0
        current_name = row["custom_name"]

        if expected_revision != current_revision:
            projection = self._build_projection(conn, row)
            raise RevisionConflictError(
                f"revision mismatch: expected {expected_revision}, current {current_revision}",
                projection=projection,
            )

        # No-op: same value
        if custom_name == current_name:
            return self._build_projection(conn, row)

        if custom_name is None:
            action = "restore_default_name"
            old_value = current_name
            new_value = None
        else:
            action = "rename"
            old_value = current_name
            new_value = custom_name

        new_revision = current_revision + 1

        # Upsert metadata
        conn.execute(
            "INSERT INTO structure_source_metadata "
            "(source_uid, custom_name, metadata_revision, "
            "metadata_updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source_uid) DO UPDATE SET "
            "custom_name=excluded.custom_name, "
            "metadata_revision=excluded.metadata_revision, "
            "metadata_updated_at=excluded.metadata_updated_at",
            (source_uid, custom_name, new_revision, now),
        )

        # Update organized_at on index
        conn.execute(
            "UPDATE structure_source_index SET organized_at = ? WHERE source_uid = ?",
            (now, source_uid),
        )

        # Audit event
        conn.execute(
            "INSERT INTO organization_events "
            "(object_type, object_id, action, old_value, "
            "new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "structure_source",
                source_uid,
                action,
                old_value,
                new_value,
                now,
            ),
        )
        conn.commit()

        # Re-read for projection
        updated_row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        return self._build_projection(conn, updated_row)

    def _build_projection(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        """Build a full projection dict from a joined row."""
        d = dict(row)
        uid = d["source_uid"]
        tag_rows = conn.execute(
            "SELECT tag FROM structure_source_tags WHERE source_uid = ? ORDER BY tag_key",
            (uid,),
        ).fetchall()
        tags = [tr["tag"] for tr in tag_rows]
        d["tags"] = tags
        d["source_id"] = d.pop("legacy_source_id", "")
        d["default_name"] = d.get("label", "")
        d["resolved_name"] = d.get("custom_name") or d.get("label", "")
        return d

    # ------------------------------------------------------------------ #
    # Tags
    # ------------------------------------------------------------------ #

    def add_tags(
        self,
        source_uid: str,
        tags: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        """Add tags to a structure source.

        Raises:
            RevisionConflictError: when ``expected_revision`` mismatches.
            ValueError: on validation failure or tag limit exceeded.
        """
        normalized = self._normalize_tags(tags)
        with self._lock, self._connect() as conn:
            try:
                return self._add_tags_tx(conn, source_uid, normalized, expected_revision)
            except Exception:
                conn.rollback()
                raise

    def _add_tags_tx(
        self,
        conn: sqlite3.Connection,
        source_uid: str,
        normalized: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        """Transactional add_tags."""
        now = _utc_now_iso()
        row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"source not found: {source_uid}")

        current_revision = row["metadata_revision"] or 0
        if expected_revision != current_revision:
            projection = self._build_projection(conn, row)
            raise RevisionConflictError(
                f"revision mismatch: expected {expected_revision}, current {current_revision}",
                projection=projection,
            )

        # Read existing tags
        existing_rows = conn.execute(
            "SELECT tag_key, tag FROM structure_source_tags WHERE source_uid = ?",
            (source_uid,),
        ).fetchall()
        existing_keys = {r["tag_key"] for r in existing_rows}

        # Check limit
        new_keys = {_tag_key(t) for t in normalized}
        total = len(existing_keys | new_keys)
        if total > _MAX_TAGS_PER_SOURCE:
            raise ValueError(f"tag limit exceeded: {total} tags, maximum {_MAX_TAGS_PER_SOURCE}")

        # Insert new tags
        added: list[str] = []
        for tag in normalized:
            key = _tag_key(tag)
            if key not in existing_keys:
                conn.execute(
                    "INSERT OR IGNORE INTO structure_source_tags "
                    "(source_uid, tag_key, tag, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (source_uid, key, tag, now),
                )
                added.append(tag)

        if not added:
            # No new tags — no-op, return current
            return self._build_projection(conn, row)

        # Bump revision
        new_revision = current_revision + 1
        conn.execute(
            "INSERT INTO structure_source_metadata "
            "(source_uid, custom_name, metadata_revision, "
            "metadata_updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source_uid) DO UPDATE SET "
            "metadata_revision=excluded.metadata_revision, "
            "metadata_updated_at=excluded.metadata_updated_at",
            (source_uid, row["custom_name"], new_revision, now),
        )
        conn.execute(
            "UPDATE structure_source_index SET organized_at = ? WHERE source_uid = ?",
            (now, source_uid),
        )

        # Audit
        conn.execute(
            "INSERT INTO organization_events "
            "(object_type, object_id, action, old_value, "
            "new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "structure_source",
                source_uid,
                "add_tags",
                json.dumps([]),
                json.dumps(added),
                now,
            ),
        )
        conn.commit()

        updated_row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        return self._build_projection(conn, updated_row)

    def remove_tags(
        self,
        source_uid: str,
        tags: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        """Remove tags from a structure source.

        Raises:
            RevisionConflictError: when ``expected_revision`` mismatches.
            ValueError: on validation failure.
        """
        normalized = self._normalize_tags(tags)
        with self._lock, self._connect() as conn:
            try:
                return self._remove_tags_tx(conn, source_uid, normalized, expected_revision)
            except Exception:
                conn.rollback()
                raise

    def _remove_tags_tx(
        self,
        conn: sqlite3.Connection,
        source_uid: str,
        normalized: list[str],
        expected_revision: int,
    ) -> dict[str, Any]:
        """Transactional remove_tags."""
        now = _utc_now_iso()
        row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"source not found: {source_uid}")

        current_revision = row["metadata_revision"] or 0
        if expected_revision != current_revision:
            projection = self._build_projection(conn, row)
            raise RevisionConflictError(
                f"revision mismatch: expected {expected_revision}, current {current_revision}",
                projection=projection,
            )

        removed: list[str] = []
        for tag in normalized:
            key = _tag_key(tag)
            cur = conn.execute(
                "DELETE FROM structure_source_tags WHERE source_uid = ? AND tag_key = ?",
                (source_uid, key),
            )
            if cur.rowcount > 0:
                removed.append(tag)

        if not removed:
            return self._build_projection(conn, row)

        new_revision = current_revision + 1
        conn.execute(
            "INSERT INTO structure_source_metadata "
            "(source_uid, custom_name, metadata_revision, "
            "metadata_updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source_uid) DO UPDATE SET "
            "metadata_revision=excluded.metadata_revision, "
            "metadata_updated_at=excluded.metadata_updated_at",
            (source_uid, row["custom_name"], new_revision, now),
        )
        conn.execute(
            "UPDATE structure_source_index SET organized_at = ? WHERE source_uid = ?",
            (now, source_uid),
        )

        conn.execute(
            "INSERT INTO organization_events "
            "(object_type, object_id, action, old_value, "
            "new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "structure_source",
                source_uid,
                "remove_tags",
                json.dumps(removed),
                json.dumps([]),
                now,
            ),
        )
        conn.commit()

        updated_row = conn.execute(
            "SELECT i.*, m.custom_name, m.metadata_revision "
            "FROM structure_source_index i "
            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
            "WHERE i.source_uid = ?",
            (source_uid,),
        ).fetchone()
        return self._build_projection(conn, updated_row)

    def _normalize_tags(self, tags: list[str]) -> list[str]:
        """Validate, strip, dedupe, and enforce tag constraints."""
        seen: set[str] = set()
        result: list[str] = []
        for raw in tags:
            tag = _validate_tag(raw)
            key = _tag_key(tag)
            if key not in seen:
                seen.add(key)
                result.append(tag)
        return result

    # ------------------------------------------------------------------ #
    # Batch metadata
    # ------------------------------------------------------------------ #

    def batch_update_metadata(
        self,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply per-item metadata updates (name, tags) with independent transactions.

        Each item: ``{source_uid, add_tags?, remove_tags?, custom_name?, expected_revision}``.

        Returns ``{succeeded: [...], failed: [...], conflicts: [...]}``.
        Duplicate ``source_uid``s are deduped (first wins).
        """
        seen_uids: set[str] = set()
        succeeded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []

        for item in items:
            uid = item.get("source_uid", "")
            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            try:
                # Determine if this is a no-op
                has_name = "custom_name" in item
                has_add = "add_tags" in item
                has_remove = "remove_tags" in item
                if not has_name and not has_add and not has_remove:
                    continue

                expected = int(item.get("expected_revision", 0))

                with self._lock, self._connect() as conn:
                    try:
                        # Read current state
                        row = conn.execute(
                            "SELECT i.*, m.custom_name, m.metadata_revision "
                            "FROM structure_source_index i "
                            "LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                            "WHERE i.source_uid = ?",
                            (uid,),
                        ).fetchone()
                        if row is None:
                            raise ValueError(f"source not found: {uid}")

                        current_rev = row["metadata_revision"] or 0
                        if expected != current_rev:
                            projection = self._build_projection(conn, row)
                            raise RevisionConflictError("revision mismatch", projection=projection)

                        now = _utc_now_iso()
                        new_rev = current_rev + 1

                        # Apply custom_name
                        if has_name:
                            name_val = _validate_name(item["custom_name"], "custom_name")
                            if name_val != row["custom_name"]:
                                conn.execute(
                                    "INSERT INTO structure_source_metadata "
                                    "(source_uid, custom_name, "
                                    "metadata_revision, "
                                    "metadata_updated_at) "
                                    "VALUES (?, ?, ?, ?) "
                                    "ON CONFLICT(source_uid) DO UPDATE SET "
                                    "custom_name=excluded.custom_name, "
                                    "metadata_revision="
                                    "excluded.metadata_revision, "
                                    "metadata_updated_at="
                                    "excluded.metadata_updated_at",
                                    (uid, name_val, new_rev, now),
                                )
                                new_rev += 1
                                action = "rename" if name_val else "restore_default_name"
                                conn.execute(
                                    "INSERT INTO organization_events "
                                    "(object_type, object_id, action, "
                                    "old_value, new_value, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?)",
                                    (
                                        "structure_source",
                                        uid,
                                        action,
                                        row["custom_name"],
                                        name_val,
                                        now,
                                    ),
                                )

                        # Add tags
                        if has_add:
                            for tag in self._normalize_tags(item["add_tags"]):
                                key = _tag_key(tag)
                                existing = conn.execute(
                                    "SELECT 1 FROM structure_source_tags "
                                    "WHERE source_uid = ? "
                                    "AND tag_key = ?",
                                    (uid, key),
                                ).fetchone()
                                if existing is None:
                                    conn.execute(
                                        "INSERT INTO "
                                        "structure_source_tags "
                                        "(source_uid, tag_key, tag, "
                                        "created_at) "
                                        "VALUES (?, ?, ?, ?)",
                                        (uid, key, tag, now),
                                    )
                            # Check tag count
                            count_row = conn.execute(
                                "SELECT COUNT(*) as c "
                                "FROM structure_source_tags "
                                "WHERE source_uid = ?",
                                (uid,),
                            ).fetchone()
                            if count_row and count_row["c"] > _MAX_TAGS_PER_SOURCE:
                                raise ValueError(f"tag limit exceeded: {count_row['c']} tags")

                        # Remove tags
                        if has_remove:
                            for tag in self._normalize_tags(item["remove_tags"]):
                                conn.execute(
                                    "DELETE FROM structure_source_tags "
                                    "WHERE source_uid = ? AND tag_key = ?",
                                    (uid, _tag_key(tag)),
                                )

                        # Bump revision once
                        conn.execute(
                            "INSERT INTO structure_source_metadata "
                            "(source_uid, custom_name, "
                            "metadata_revision, "
                            "metadata_updated_at) "
                            "VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(source_uid) DO UPDATE SET "
                            "metadata_revision="
                            "excluded.metadata_revision, "
                            "metadata_updated_at="
                            "excluded.metadata_updated_at",
                            (
                                uid,
                                item.get("custom_name", row["custom_name"]),
                                new_rev,
                                now,
                            ),
                        )
                        conn.execute(
                            "UPDATE structure_source_index "
                            "SET organized_at = ? "
                            "WHERE source_uid = ?",
                            (now, uid),
                        )
                        conn.commit()

                        succeeded.append({"source_uid": uid})
                    except RevisionConflictError as exc:
                        conn.rollback()
                        conflicts.append({"source_uid": uid, "projection": exc.projection})
                    except Exception as exc:
                        conn.rollback()
                        failed.append({"source_uid": uid, "error": str(exc)})

            except Exception as exc:
                failed.append({"source_uid": uid, "error": str(exc)})

        return {"succeeded": succeeded, "failed": failed, "conflicts": conflicts}

    # ------------------------------------------------------------------ #
    # Project tag management
    # ------------------------------------------------------------------ #

    def rename_project_tag(
        self,
        project_id: str,
        source_key: str,
        target_display: str,
    ) -> int:
        """Rename or merge a tag within a project.

        If ``target_key`` already exists, merges (re-points relations, dedupes).
        Bumps metadata_revision on affected structures and writes audit events.
        Returns the number of affected structures.
        """
        target_key = _tag_key(target_display)
        source_key_norm = _tag_key(source_key)

        with self._lock, self._connect() as conn:
            try:
                # Find affected source_uids
                affected_rows = conn.execute(
                    "SELECT DISTINCT t.source_uid "
                    "FROM structure_source_tags t "
                    "JOIN structure_source_index i ON i.source_uid = t.source_uid "
                    "WHERE i.project_id = ? AND t.tag_key = ?",
                    (project_id, source_key_norm),
                ).fetchall()
                if not affected_rows:
                    return 0

                affected_uids = [r["source_uid"] for r in affected_rows]
                now = _utc_now_iso()

                if target_key == source_key_norm:
                    return 0

                # Check if target already exists
                target_exists = conn.execute(
                    "SELECT 1 FROM structure_source_tags t "
                    "JOIN structure_source_index i ON i.source_uid = t.source_uid "
                    "WHERE i.project_id = ? AND t.tag_key = ? LIMIT 1",
                    (project_id, target_key),
                ).fetchone()

                action = "tag_merge" if target_exists else "tag_rename"
                count = 0

                for uid in affected_uids:
                    # Delete source tag
                    conn.execute(
                        "DELETE FROM structure_source_tags WHERE source_uid = ? AND tag_key = ?",
                        (uid, source_key_norm),
                    )
                    # Insert target tag (if not already present)
                    existing = conn.execute(
                        "SELECT 1 FROM structure_source_tags WHERE source_uid = ? AND tag_key = ?",
                        (uid, target_key),
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            "INSERT INTO structure_source_tags "
                            "(source_uid, tag_key, tag, created_at) VALUES (?, ?, ?, ?)",
                            (uid, target_key, target_display, now),
                        )

                    # Bump revision
                    meta = conn.execute(
                        "SELECT custom_name, metadata_revision FROM structure_source_metadata "
                        "WHERE source_uid = ?",
                        (uid,),
                    ).fetchone()
                    rev = (meta["metadata_revision"] if meta else 0) + 1
                    conn.execute(
                        "INSERT INTO structure_source_metadata "
                        "(source_uid, custom_name, metadata_revision, metadata_updated_at) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(source_uid) DO UPDATE SET "
                        "metadata_revision=excluded.metadata_revision, "
                        "metadata_updated_at=excluded.metadata_updated_at",
                        (uid, meta["custom_name"] if meta else None, rev, now),
                    )
                    conn.execute(
                        "UPDATE structure_source_index SET organized_at = ? WHERE source_uid = ?",
                        (now, uid),
                    )

                    # Audit
                    conn.execute(
                        "INSERT INTO organization_events "
                        "(object_type, object_id, action, old_value, new_value, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("structure_source", uid, action, source_key_norm, target_key, now),
                    )
                    count += 1

                conn.commit()
                return count
            except Exception:
                conn.rollback()
                raise

    def remove_project_tag(self, project_id: str, tag_key: str) -> int:
        """Remove a tag relation from all structures in a project.

        Deletes tag relations only, never structures. Returns affected count.
        """
        key = _tag_key(tag_key)
        with self._lock, self._connect() as conn:
            try:
                affected_rows = conn.execute(
                    "SELECT t.source_uid FROM structure_source_tags t "
                    "JOIN structure_source_index i ON i.source_uid = t.source_uid "
                    "WHERE i.project_id = ? AND t.tag_key = ?",
                    (project_id, key),
                ).fetchall()
                if not affected_rows:
                    return 0

                now = _utc_now_iso()
                count = 0
                for row in affected_rows:
                    uid = row["source_uid"]
                    conn.execute(
                        "DELETE FROM structure_source_tags WHERE source_uid = ? AND tag_key = ?",
                        (uid, key),
                    )
                    # Bump revision
                    meta = conn.execute(
                        "SELECT custom_name, metadata_revision FROM structure_source_metadata "
                        "WHERE source_uid = ?",
                        (uid,),
                    ).fetchone()
                    rev = (meta["metadata_revision"] if meta else 0) + 1
                    conn.execute(
                        "INSERT INTO structure_source_metadata "
                        "(source_uid, custom_name, metadata_revision, metadata_updated_at) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(source_uid) DO UPDATE SET "
                        "metadata_revision=excluded.metadata_revision, "
                        "metadata_updated_at=excluded.metadata_updated_at",
                        (uid, meta["custom_name"] if meta else None, rev, now),
                    )
                    conn.execute(
                        "UPDATE structure_source_index SET organized_at = ? WHERE source_uid = ?",
                        (now, uid),
                    )
                    conn.execute(
                        "INSERT INTO organization_events "
                        "(object_type, object_id, action, old_value, new_value, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("structure_source", uid, "remove_project_tag", key, None, now),
                    )
                    count += 1

                conn.commit()
                return count
            except Exception:
                conn.rollback()
                raise

    def project_tag_counts(self, project_id: str) -> list[dict[str, Any]]:
        """Return ``[{tag, tag_key, count}]`` ordered by count desc, then tag."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT t.tag, t.tag_key, COUNT(*) as count "
                "FROM structure_source_tags t "
                "JOIN structure_source_index i ON i.source_uid = t.source_uid "
                "WHERE i.project_id = ? "
                "GROUP BY t.tag_key "
                "ORDER BY count DESC, t.tag",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #

    def query_sources(
        self,
        *,
        project_id: str | None = None,
        all_projects: bool = False,
        q: str | None = None,
        role: str | None = None,
        tags: list[str] | None = None,
        tag_match: str = "any",
        workflow: str | None = None,
        source_kind: str | None = None,
        source_group: str | None = None,
        remote: bool | None = None,
        availability: str | None = None,
        assessment: str | None = None,
        usage_status: str | None = None,
        include_inactive: bool = False,
        sort: str = "produced_desc",
        group_by: str = "none",
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Query structure sources with filtering, sorting, grouping, and pagination.

        Returns ``{items, total, next_cursor, groups}`` where ``groups``
        is present only when ``group_by != 'none'``.
        """
        if limit > 100:
            limit = 100

        if source_group is not None and source_group not in _VALID_SOURCE_GROUPS:
            raise ValueError(f"invalid source group: {source_group}")

        # Parse cursor
        offset = 0
        if cursor:
            try:
                decoded = base64.b64decode(cursor).decode("utf-8")
                cursor_data = json.loads(decoded)
                offset = int(cursor_data.get("offset", 0))
                # Fingerprint validated against current params later
            except (ValueError, json.JSONDecodeError, TypeError):
                raise ValueError("invalid cursor")

        # Build WHERE
        clauses: list[str] = []
        params: list[Any] = []

        if not all_projects and project_id is not None:
            clauses.append("i.project_id = ?")
            params.append(project_id)

        if q:
            like = f"%{q}%"
            clauses.append(
                "(m.custom_name LIKE ? OR i.label LIKE ? "
                "OR i.job_name LIKE ? "
                "OR i.candidate_id LIKE ? OR i.formula LIKE ? "
                "OR i.source_uid IN ("
                "SELECT t2.source_uid "
                "FROM structure_source_tags t2 "
                "WHERE t2.tag LIKE ?))"
            )
            params.extend([like] * 6)

        if role is not None:
            clauses.append("i.role = ?")
            params.append(role)

        if tags:
            if tag_match == "all":
                # Source must have ALL specified tags
                for tag in tags:
                    key = _tag_key(tag)
                    clauses.append(
                        "EXISTS (SELECT 1 FROM structure_source_tags t "
                        "WHERE t.source_uid = i.source_uid AND t.tag_key = ?)"
                    )
                    params.append(key)
            else:
                # Source must have ANY of the specified tags
                keys = [_tag_key(t) for t in tags]
                placeholders = ",".join("?" for _ in keys)
                clauses.append(
                    f"EXISTS (SELECT 1 FROM structure_source_tags t "
                    f"WHERE t.source_uid = i.source_uid AND t.tag_key IN ({placeholders}))"
                )
                params.extend(keys)

        if workflow:
            clauses.append("i.workflow = ?")
            params.append(workflow)

        if source_kind:
            clauses.append("i.source_kind = ?")
            params.append(source_kind)

        if source_group == "candidate":
            clauses.append("i.source_kind = ?")
            params.append("saved_candidate")
        elif source_group == "task_result":
            clauses.append("i.source_kind != ?")
            params.append("saved_candidate")

        if remote is not None:
            clauses.append("i.remote = ?")
            params.append(int(remote))

        if availability:
            clauses.append("i.availability = ?")
            params.append(availability)

        if usage_status:
            if usage_status not in _VALID_USAGE_STATUSES:
                raise ValueError(f"invalid usage status: {usage_status}")
            clauses.append(
                "EXISTS (SELECT 1 FROM structure_candidates c "
                "WHERE c.source_uid=i.source_uid AND c.usage_status=?)"
            )
            params.append(usage_status)
        elif not include_inactive:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM structure_candidates c "
                "WHERE c.source_uid=i.source_uid AND c.usage_status!='active')"
            )

        if assessment:
            if assessment not in _VALID_CONCLUSIONS:
                raise ValueError(f"invalid assessment: {assessment}")
            if assessment == "unreviewed":
                clauses.append(
                    "NOT EXISTS (SELECT 1 FROM structure_candidate_assessments a "
                    "JOIN structure_candidates c ON c.candidate_record_id=a.candidate_record_id "
                    "WHERE c.source_uid=i.source_uid)"
                )
            else:
                clauses.append(
                    "EXISTS (SELECT 1 FROM structure_candidate_assessments a "
                    "JOIN structure_candidates c ON c.candidate_record_id=a.candidate_record_id "
                    "WHERE c.source_uid=i.source_uid AND a.conclusion=?)"
                )
                params.append(assessment)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        # Compute fingerprint for cursor validation
        fp_input = json.dumps(
            {
                "project_id": project_id,
                "all_projects": all_projects,
                "q": q,
                "role": role,
                "tags": sorted(tags or []),
                "tag_match": tag_match,
                "workflow": workflow,
                "source_kind": source_kind,
                "source_group": source_group,
                "remote": remote,
                "availability": availability,
                "assessment": assessment,
                "usage_status": usage_status,
                "include_inactive": include_inactive,
                "sort": sort,
                "group_by": group_by,
            },
            sort_keys=True,
        )
        current_fp = hashlib.sha1(fp_input.encode()).hexdigest()[:16]

        if cursor:
            decoded = base64.b64decode(cursor).decode("utf-8")
            cursor_data = json.loads(decoded)
            if cursor_data.get("fingerprint") != current_fp:
                raise ValueError("cursor fingerprint mismatch — query params changed")

        # Total count
        count_sql = f"""
            SELECT COUNT(DISTINCT i.source_uid) as total
            FROM structure_source_index i
            LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid
            {where}
        """
        with self._lock, self._connect() as conn:
            total_row = conn.execute(count_sql, params).fetchone()
            total = total_row["total"] if total_row else 0

        # Sort
        order = self._sort_clause(sort)

        # Fetch page + 1 to detect next page
        page_sql = f"""
            SELECT i.source_uid
            FROM structure_source_index i
            LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        fetch_limit = limit + 1
        with self._lock, self._connect() as conn:
            page_rows = conn.execute(page_sql, (*params, fetch_limit, offset)).fetchall()

        uids = [r["source_uid"] for r in page_rows]
        has_next = len(uids) > limit
        uids = uids[:limit]

        # Fetch full projections
        items: list[dict[str, Any]] = []
        if uids:
            uid_placeholders = ",".join("?" for _ in uids)
            items = self._join_projection(
                f"WHERE i.source_uid IN ({uid_placeholders})",
                tuple(uids),
            )
            # Preserve ordering
            uid_order = {uid: i for i, uid in enumerate(uids)}
            items.sort(key=lambda x: uid_order.get(x["source_uid"], 0))

        next_cursor = None
        if has_next:
            next_cursor = base64.b64encode(
                json.dumps(
                    {
                        "fingerprint": current_fp,
                        "offset": offset + limit,
                    }
                ).encode()
            ).decode()

        result: dict[str, Any] = {
            "items": items,
            "total": total,
            "next_cursor": next_cursor,
        }

        # Grouping
        if group_by != "none":
            result["groups"] = self._compute_groups(
                clauses,
                params,
                group_by,
                project_id,
                all_projects,
            )

        return result

    def _sort_clause(self, sort: str) -> str:
        """Return SQL ORDER BY clause for the given sort key."""
        if sort == "produced_asc":
            return "COALESCE(i.produced_at, '') ASC, i.source_uid ASC"
        elif sort == "name_asc":
            return "LOWER(COALESCE(m.custom_name, i.label, '')) ASC, i.source_uid ASC"
        elif sort == "name_desc":
            return "LOWER(COALESCE(m.custom_name, i.label, '')) DESC, i.source_uid ASC"
        elif sort == "organized_desc":
            return "COALESCE(m.metadata_updated_at, '') DESC, i.source_uid ASC"
        else:
            # produced_desc (default)
            return "COALESCE(i.produced_at, '') DESC, i.source_uid ASC"

    def _compute_groups(
        self,
        base_clauses: list[str],
        base_params: list[Any],
        group_by: str,
        project_id: str | None,
        all_projects: bool,
    ) -> list[dict[str, Any]]:
        """Compute grouped counts from the filtered set."""
        groups: list[dict[str, Any]] = []

        with self._lock, self._connect() as conn:
            if group_by == "job":
                where = f"WHERE {' AND '.join(base_clauses)}" if base_clauses else ""
                rows = conn.execute(
                    f"SELECT i.job_id, i.job_name, COUNT(*) as count, "
                    f"t.custom_name AS task_custom_name, t.display_name AS task_display_name "
                    f"FROM structure_source_index i "
                    f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                    f"LEFT JOIN tasks t ON t.task_id = i.job_id "
                    f"{where} "
                    f"GROUP BY i.job_id ORDER BY count DESC",
                    tuple(base_params),
                ).fetchall()
                for r in rows:
                    task_custom = r["task_custom_name"]
                    task_display = r["task_display_name"]
                    resolved = task_custom or task_display or r["job_name"] or r["job_id"]
                    groups.append(
                        {
                            "key": r["job_id"],
                            "label": resolved,
                            "count": r["count"],
                        }
                    )

            elif group_by == "role":
                where = f"WHERE {' AND '.join(base_clauses)}" if base_clauses else ""
                rows = conn.execute(
                    f"SELECT i.role, COUNT(*) as count "
                    f"FROM structure_source_index i "
                    f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                    f"{where} "
                    f"GROUP BY i.role ORDER BY count DESC",
                    tuple(base_params),
                ).fetchall()
                role_labels = {"TS": "TS", "INT": "INT", "": "unlabeled"}
                for r in rows:
                    groups.append(
                        {
                            "key": r["role"],
                            "label": role_labels.get(r["role"], r["role"]),
                            "count": r["count"],
                        }
                    )

            elif group_by == "tag":
                where = f"WHERE {' AND '.join(base_clauses)}" if base_clauses else ""
                # Tagged sources
                rows = conn.execute(
                    f"SELECT t.tag, t.tag_key, COUNT(DISTINCT t.source_uid) as count "
                    f"FROM structure_source_tags t "
                    f"JOIN structure_source_index i ON i.source_uid = t.source_uid "
                    f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                    f"{where} "
                    f"GROUP BY t.tag_key ORDER BY count DESC",
                    tuple(base_params),
                ).fetchall()
                for r in rows:
                    groups.append(
                        {
                            "key": r["tag_key"],
                            "label": r["tag"],
                            "count": r["count"],
                        }
                    )

                # Uncategorized
                uncategorized_count = conn.execute(
                    "SELECT COUNT(DISTINCT i.source_uid) as count "
                    "FROM structure_source_index i "
                    "LEFT JOIN structure_source_metadata m "
                    "ON m.source_uid = i.source_uid "
                    "WHERE "
                    + (" AND ".join(base_clauses) + " AND " if base_clauses else "")
                    + "NOT EXISTS ("
                    "SELECT 1 FROM structure_source_tags t "
                    "WHERE t.source_uid = i.source_uid)",
                    tuple(base_params),
                ).fetchone()
                if uncategorized_count and uncategorized_count["count"] > 0:
                    groups.append(
                        {
                            "key": "__uncategorized",
                            "label": "uncategorized",
                            "count": uncategorized_count["count"],
                        }
                    )

        return groups

    # ------------------------------------------------------------------ #
    # Facets
    # ------------------------------------------------------------------ #

    def facet_counts(
        self,
        *,
        project_id: str | None = None,
        all_projects: bool = False,
        q: str | None = None,
        role: str | None = None,
        tags: list[str] | None = None,
        tag_match: str = "any",
        workflow: str | None = None,
        source_kind: str | None = None,
        source_group: str | None = None,
        remote: bool | None = None,
        availability: str | None = None,
        assessment: str | None = None,
        usage_status: str | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """Compute facet counts under the given filter set (excluding the facet's own dimension)."""

        if source_group is not None and source_group not in _VALID_SOURCE_GROUPS:
            raise ValueError(f"invalid source group: {source_group}")

        # Build base WHERE clauses (excluding the specific facet we're counting)
        def build_clauses(exclude: str = "") -> tuple[list[str], list[Any]]:
            clauses: list[str] = []
            params: list[Any] = []
            if not all_projects and project_id is not None:
                clauses.append("i.project_id = ?")
                params.append(project_id)
            if q and exclude != "q":
                like = f"%{q}%"
                clauses.append(
                    "(m.custom_name LIKE ? OR i.label LIKE ? "
                    "OR i.job_name LIKE ? "
                    "OR i.candidate_id LIKE ? OR i.formula LIKE ? "
                    "OR i.source_uid IN ("
                    "SELECT t2.source_uid "
                    "FROM structure_source_tags t2 "
                    "WHERE t2.tag LIKE ?))"
                )
                params.extend([like] * 6)
            if role is not None and exclude != "role":
                clauses.append("i.role = ?")
                params.append(role)
            if tags and exclude != "tags":
                if tag_match == "all":
                    for tag in tags:
                        key = _tag_key(tag)
                        clauses.append(
                            "EXISTS (SELECT 1 FROM structure_source_tags t "
                            "WHERE t.source_uid = i.source_uid AND t.tag_key = ?)"
                        )
                        params.append(key)
                else:
                    keys = [_tag_key(t) for t in tags]
                    placeholders = ",".join("?" for _ in keys)
                    clauses.append(
                        f"EXISTS (SELECT 1 FROM structure_source_tags t "
                        f"WHERE t.source_uid = i.source_uid AND t.tag_key IN ({placeholders}))"
                    )
                    params.extend(keys)
            if workflow and exclude != "workflow":
                clauses.append("i.workflow = ?")
                params.append(workflow)
            if source_kind and exclude != "source_kind":
                clauses.append("i.source_kind = ?")
                params.append(source_kind)
            if source_group == "candidate":
                clauses.append("i.source_kind = ?")
                params.append("saved_candidate")
            elif source_group == "task_result":
                clauses.append("i.source_kind != ?")
                params.append("saved_candidate")
            if remote is not None and exclude != "remote":
                clauses.append("i.remote = ?")
                params.append(int(remote))
            if availability and exclude != "availability":
                clauses.append("i.availability = ?")
                params.append(availability)
            if usage_status and exclude != "usage_status":
                clauses.append(
                    "EXISTS (SELECT 1 FROM structure_candidates c "
                    "WHERE c.source_uid=i.source_uid AND c.usage_status=?)"
                )
                params.append(usage_status)
            elif not include_inactive and exclude != "usage_status":
                clauses.append(
                    "NOT EXISTS (SELECT 1 FROM structure_candidates c "
                    "WHERE c.source_uid=i.source_uid AND c.usage_status!='active')"
                )
            if assessment and exclude != "assessment":
                if assessment == "unreviewed":
                    clauses.append(
                        "NOT EXISTS (SELECT 1 FROM structure_candidate_assessments a "
                        "JOIN structure_candidates c "
                        "ON c.candidate_record_id=a.candidate_record_id "
                        "WHERE c.source_uid=i.source_uid)"
                    )
                else:
                    clauses.append(
                        "EXISTS (SELECT 1 FROM structure_candidate_assessments a "
                        "JOIN structure_candidates c "
                        "ON c.candidate_record_id=a.candidate_record_id "
                        "WHERE c.source_uid=i.source_uid AND a.conclusion=?)"
                    )
                    params.append(assessment)
            return clauses, params

        result: dict[str, Any] = {}

        with self._lock, self._connect() as conn:
            # Roles
            rc, rp = build_clauses(exclude="role")
            rw = f"WHERE {' AND '.join(rc)}" if rc else ""
            role_rows = conn.execute(
                f"SELECT i.role, COUNT(DISTINCT i.source_uid) as count "
                f"FROM structure_source_index i "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"{rw} GROUP BY i.role",
                tuple(rp),
            ).fetchall()
            role_map: dict[str, int] = {"TS": 0, "INT": 0, "unlabeled": 0}
            for r in role_rows:
                key = r["role"] if r["role"] in ("TS", "INT") else "unlabeled"
                role_map[key] = r["count"]
            result["roles"] = role_map

            # Tags
            tc, tp = build_clauses(exclude="tags")
            tw = f"WHERE {' AND '.join(tc)}" if tc else ""
            tag_rows = conn.execute(
                f"SELECT t.tag, t.tag_key, COUNT(DISTINCT t.source_uid) as count "
                f"FROM structure_source_tags t "
                f"JOIN structure_source_index i ON i.source_uid = t.source_uid "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"{tw} GROUP BY t.tag_key ORDER BY count DESC",
                tuple(tp),
            ).fetchall()
            result["tags"] = [{"tag": r["tag"], "count": r["count"]} for r in tag_rows]

            # Workflows
            wc, wp = build_clauses(exclude="workflow")
            ww = f"WHERE {' AND '.join(wc)}" if wc else ""
            wf_rows = conn.execute(
                f"SELECT i.workflow, COUNT(DISTINCT i.source_uid) as count "
                f"FROM structure_source_index i "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"{ww} GROUP BY i.workflow",
                tuple(wp),
            ).fetchall()
            result["workflows"] = {r["workflow"]: r["count"] for r in wf_rows}

            # Source kinds
            skc, skp = build_clauses(exclude="source_kind")
            skw = f"WHERE {' AND '.join(skc)}" if skc else ""
            sk_rows = conn.execute(
                f"SELECT i.source_kind, COUNT(DISTINCT i.source_uid) as count "
                f"FROM structure_source_index i "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"{skw} GROUP BY i.source_kind",
                tuple(skp),
            ).fetchall()
            result["source_kinds"] = {r["source_kind"]: r["count"] for r in sk_rows}

            # Jobs
            jc, jp = build_clauses(exclude="")
            jw = f"WHERE {' AND '.join(jc)}" if jc else ""
            job_rows = conn.execute(
                f"SELECT i.job_id, i.job_name, COUNT(DISTINCT i.source_uid) as count, "
                f"t.custom_name AS task_custom_name, t.display_name AS task_display_name "
                f"FROM structure_source_index i "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"LEFT JOIN tasks t ON t.task_id = i.job_id "
                f"{jw} GROUP BY i.job_id ORDER BY count DESC LIMIT 50",
                tuple(jp),
            ).fetchall()
            result["jobs"] = [
                {
                    "job_id": r["job_id"],
                    "job_name": r["job_name"],
                    "job_resolved_name": (
                        r["task_custom_name"] or r["task_display_name"] or r["job_name"]
                    ),
                    "count": r["count"],
                }
                for r in job_rows
            ]

            # Total structures under full filters
            full_c, full_p = build_clauses(exclude="")
            full_w = f"WHERE {' AND '.join(full_c)}" if full_c else ""
            total_row = conn.execute(
                f"SELECT COUNT(DISTINCT i.source_uid) as count "
                f"FROM structure_source_index i "
                f"LEFT JOIN structure_source_metadata m ON m.source_uid = i.source_uid "
                f"{full_w}",
                tuple(full_p),
            ).fetchone()
            result["total_structures"] = total_row["count"] if total_row else 0

        return result

    # ------------------------------------------------------------------ #
    # Delete / index coverage
    # ------------------------------------------------------------------ #

    def delete_by_job(self, job_id: str) -> int:
        """Cascade remove index + metadata + tags for a purged job.

        Returns the number of index rows deleted.
        """
        with self._lock, self._connect() as conn:
            try:
                uids = conn.execute(
                    "SELECT source_uid FROM structure_source_index WHERE job_id = ?",
                    (job_id,),
                ).fetchall()
                uid_list = [r["source_uid"] for r in uids]
                if not uid_list:
                    return 0

                placeholders = ",".join("?" for _ in uid_list)
                conn.execute(
                    f"DELETE FROM structure_source_tags WHERE source_uid IN ({placeholders})",
                    tuple(uid_list),
                )
                conn.execute(
                    f"DELETE FROM structure_source_metadata WHERE source_uid IN ({placeholders})",
                    tuple(uid_list),
                )
                conn.execute(
                    "DELETE FROM structure_source_index WHERE job_id = ?",
                    (job_id,),
                )
                conn.commit()
                return len(uid_list)
            except Exception:
                conn.rollback()
                raise

    def index_coverage(self) -> dict[str, Any]:
        """Return ``{indexed_jobs, last_indexed_at}`` from index state."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n, MAX(indexed_at) as last_at FROM structure_source_index_state"
            ).fetchone()
        return {
            "indexed_jobs": row["n"] if row else 0,
            "last_indexed_at": row["last_at"] if row else None,
        }

    def mark_job_indexed(self, job_id: str, discovery_version: int = 1) -> None:
        """Mark a job as indexed in the state table."""
        now = _utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO structure_source_index_state (job_id, indexed_at, discovery_version) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET "
                "indexed_at=excluded.indexed_at, "
                "discovery_version=excluded.discovery_version",
                (job_id, now, discovery_version),
            )
            conn.commit()
