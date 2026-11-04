"""B1c: molecule-identity write path + backfill for indexed structures.

Contract: ``docs/reference/next-change/ACP_Chemistry_Network_Contract_20261103.md``
§8 (B1 upgrade path: canonical identity persisted on the index, indexer
backfill).

Platform limit (recorded as an explicit skip, never a fake pass):
``acp.scheduler`` cannot be imported on Windows — ``acp/scheduler/__init__.py``
constructs the job manager, which imports
``acp_services.runtime.attempt_freeze`` → ``import fcntl``.  Every test in this
module therefore runs on POSIX/CI only and is **NOT_VERIFIED locally**.

Covered here:

1. a legacy ``structure_source_index`` (no ``molecule_identity_key``) gains the
   nullable column on reopen and keeps its rows;
2. ``upsert_index_entries`` writes the key and upserts the identity registry row
   in the same transaction;
3. an entry without a resolved identity leaves the column ``NULL`` and writes no
   registry row (no fabricated identity);
4. a failing batch rolls back both the index row and the registry row;
5. the identity backfill is idempotent and skips unreadable files without
   raising.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "fcntl",
    reason=(
        "acp.scheduler imports acp_services.runtime.attempt_freeze, which needs "
        "fcntl (POSIX only) — NOT_VERIFIED on this Windows host"
    ),
)

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus  # noqa: E402
from acp.scheduler.store import JobStore  # noqa: E402
from acp.scheduler.structure_source_indexer import StructureSourceIndexer  # noqa: E402
from acp.scheduler.structure_source_store import (  # noqa: E402
    StructureSourceStore,
    source_uid_for,
)
from acp.scheduler.structure_sources import molecule_identity_for_text  # noqa: E402

#: Registry ownership: batch B1b adds ``acp_services.store.molecule_identity``.
_REGISTRY_MODULE = "acp_services.store.molecule_identity"

_XYZ_WATER = """\
3
water
O 0.000000 0.000000 0.000000
H 0.950000 0.000000 0.000000
H -0.950000 0.000000 0.000000
"""

_XYZ_METHANOL = """\
6
methanol
C 0.000000 0.000000 0.000000
O 1.400000 0.000000 0.000000
H -0.360000 1.030000 0.000000
H -0.360000 -0.510000 0.890000
H -0.360000 -0.510000 -0.890000
H 1.760000 0.890000 0.000000
"""

_IDENTITY_WATER = {
    "identity_key": "mol_" + "1" * 24,
    "scheme": "mol_identity_v1",
    "canonical_smiles": "O",
    "charge": 0,
    "multiplicity": 1,
    "formula": "H2O",
    "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
}

#: The pre-B1c index shape: no ``molecule_identity_key``, no ``policy_json``.
_LEGACY_INDEX_SCHEMA = """
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


# ------------------------------------------------------------------ #
# Fixtures / helpers
# ------------------------------------------------------------------ #


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture()
def source_store(db_path: str) -> StructureSourceStore:
    return StructureSourceStore(db_path)


@pytest.fixture()
def job_store(db_path: str) -> JobStore:
    return JobStore(db_path)


def _make_entry(
    job_id: str = "job_001",
    path: str = "RESULT/structures/mol.xyz",
    *,
    project_id: str | None = "proj_1",
    checksum: str | None = "sha256:" + "a" * 64,
    identity: dict[str, Any] | None = None,
    availability: str = "available",
) -> dict[str, Any]:
    """Minimal discovery entry as produced by ``StructureSourceService._entry``."""
    entry: dict[str, Any] = {
        "source_id": f"job_{job_id}:{path}",
        "job_id": job_id,
        "path": path,
        "label": "structure",
        "workflow": "Confsearch",
        "project_id": project_id,
        "job_name": f"Task {job_id}",
        "molecule_name": "water",
        "job_status": "completed",
        "source_kind": "final",
        "formula": "H2O",
        "atom_count": 3,
        "charge": 0,
        "multiplicity": 1,
        "has_3d": 1,
        "remote": 0,
        "candidate_id": "",
        "role": "",
        "role_evidence": "",
        "availability": availability,
        "produced_at": "2026-09-18T10:00:00+00:00",
        "content_checksum": checksum,
        "structure_facts": {},
    }
    if identity is not None:
        entry["molecule_identity"] = identity
    return entry


def _row(store: StructureSourceStore, uid: str) -> dict[str, Any]:
    """Read the raw index row (bypasses the joined projection)."""
    with store._lock, store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM structure_source_index WHERE source_uid = ?", (uid,)
        ).fetchone()
    assert row is not None
    return dict(row)


def _registry_rows(db_path: str) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM structure_identities").fetchall()
    return [dict(row) for row in rows]


def _write_xyz(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _seed_job(job_store: JobStore, run_root: Path, job_id: str, rel: str, xyz: str) -> JobRecord:
    work_dir = run_root / job_id
    _write_xyz(work_dir / rel, xyz)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="Confsearch", name=f"Task {job_id}", project_id="proj_1"),
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        created_at="2026-09-01T09:00:00+00:00",
        updated_at="2026-09-01T10:00:00+00:00",
        completed_at="2026-09-01T10:00:00+00:00",
        project_id="proj_1",
    )
    job_store.create(record)
    return record


# ------------------------------------------------------------------ #
# 1. Legacy DB gains the nullable identity column
# ------------------------------------------------------------------ #


def test_legacy_index_gains_identity_column(db_path: str) -> None:
    legacy_uid = source_uid_for("legacy_job", "RESULT/structures/old.xyz")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_LEGACY_INDEX_SCHEMA)
        conn.execute(
            "INSERT INTO structure_source_index (source_uid, job_id, relative_path, "
            "legacy_source_id, project_id, content_checksum, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                legacy_uid,
                "legacy_job",
                "RESULT/structures/old.xyz",
                "job_legacy_job:RESULT/structures/old.xyz",
                "proj_1",
                "sha256:" + "b" * 64,
                "2026-08-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        before = {
            r[1] for r in conn.execute("PRAGMA table_info(structure_source_index)").fetchall()
        }
    assert "molecule_identity_key" not in before  # genuinely a pre-B1c database

    store = StructureSourceStore(db_path)

    with store._lock, store._connect() as conn:
        columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(structure_source_index)")
        }
    assert "molecule_identity_key" in columns
    row = _row(store, legacy_uid)
    assert row["content_checksum"] == "sha256:" + "b" * 64
    assert row["molecule_identity_key"] is None
    # Reopening is idempotent (no duplicate-column error, no data loss).
    reopened = StructureSourceStore(db_path)
    assert _row(reopened, legacy_uid)["content_checksum"] == "sha256:" + "b" * 64


# ------------------------------------------------------------------ #
# 2. Identity write path (index column + registry, same transaction)
# ------------------------------------------------------------------ #


def test_upsert_writes_key_and_registry_row(
    source_store: StructureSourceStore, db_path: str
) -> None:
    pytest.importorskip(_REGISTRY_MODULE, reason="B1b identity registry module not present")

    entry = _make_entry(identity=_IDENTITY_WATER)
    assert source_store.upsert_index_entries([entry]) == 1

    uid = source_uid_for("job_001", "RESULT/structures/mol.xyz")
    assert _row(source_store, uid)["molecule_identity_key"] == _IDENTITY_WATER["identity_key"]

    registry = _registry_rows(db_path)
    assert len(registry) == 1
    assert registry[0]["project_id"] == "proj_1"
    assert registry[0]["identity_key"] == _IDENTITY_WATER["identity_key"]
    assert registry[0]["scheme"] == _IDENTITY_WATER["scheme"]
    assert registry[0]["charge"] == 0
    assert registry[0]["multiplicity"] == 1


def test_upsert_without_identity_leaves_null(
    source_store: StructureSourceStore, db_path: str
) -> None:
    assert source_store.upsert_index_entries([_make_entry()]) == 1

    uid = source_uid_for("job_001", "RESULT/structures/mol.xyz")
    row = _row(source_store, uid)
    assert row["molecule_identity_key"] is None
    assert row["content_checksum"] == "sha256:" + "a" * 64
    assert _registry_rows(db_path) == []


def test_upsert_unresolved_identity_dict_is_not_a_key(
    source_store: StructureSourceStore, db_path: str
) -> None:
    """An unresolved identity (``identity_key`` empty) must never fabricate one."""
    entry = _make_entry(identity={"identity_key": None, "scheme": "mol_identity_v1"})
    assert source_store.upsert_index_entries([entry]) == 1

    assert _row(source_store, source_uid_for("job_001", "RESULT/structures/mol.xyz"))[
        "molecule_identity_key"
    ] is None
    assert _registry_rows(db_path) == []


def test_identity_without_project_writes_key_only(
    source_store: StructureSourceStore, db_path: str
) -> None:
    """Registry rows are project-scoped: no project means no registry row."""
    entry = _make_entry(project_id=None, identity=_IDENTITY_WATER)
    assert source_store.upsert_index_entries([entry]) == 1

    uid = source_uid_for("job_001", "RESULT/structures/mol.xyz")
    assert _row(source_store, uid)["molecule_identity_key"] == _IDENTITY_WATER["identity_key"]
    assert _registry_rows(db_path) == []


def test_failing_batch_rolls_back_index_and_registry(
    source_store: StructureSourceStore, db_path: str
) -> None:
    good = _make_entry("job_ok", "RESULT/structures/ok.xyz", identity=_IDENTITY_WATER)
    bad = _make_entry("job_bad", "RESULT/structures/bad.xyz", identity=_IDENTITY_WATER)
    bad["structure_facts"] = {"unserialisable": {1, 2, 3}}  # json.dumps raises TypeError

    with pytest.raises(TypeError):
        source_store.upsert_index_entries([good, bad])

    uid_good = source_uid_for("job_ok", "RESULT/structures/ok.xyz")
    with source_store._lock, source_store._connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM structure_source_index"
        ).fetchone()["n"]
    assert remaining == 0
    assert _registry_rows(db_path) == []
    assert source_store.get(uid_good) is None


# ------------------------------------------------------------------ #
# 3. Backfill pass
# ------------------------------------------------------------------ #


def test_molecule_identity_for_text_resolves_and_never_raises() -> None:
    resolved = molecule_identity_for_text(_XYZ_WATER, charge=0, multiplicity=1)
    assert resolved is not None
    assert str(resolved["identity_key"]).startswith("mol_")
    assert resolved["scheme"] == "mol_identity_v1"
    # No text (remote placeholder path) is "no identity", not an error.
    assert molecule_identity_for_text(None, charge=0, multiplicity=1) is None
    assert molecule_identity_for_text("", charge=0, multiplicity=1) is None
    # Garbage must degrade to "no identity" instead of breaking discovery.
    assert molecule_identity_for_text("not an xyz block", charge=0, multiplicity=1) is None


def test_backfill_is_idempotent_and_skips_unreadable(
    job_store: JobStore,
    source_store: StructureSourceStore,
    tmp_path: Path,
    db_path: str,
) -> None:
    pytest.importorskip(_REGISTRY_MODULE, reason="B1b identity registry module not present")

    _seed_job(job_store, tmp_path, "job_w", "RESULT/structures/w.xyz", _XYZ_WATER)
    # Two pre-B1c rows: one with a readable local file, one whose file is gone.
    readable = _make_entry(
        "job_w",
        "RESULT/structures/w.xyz",
        checksum="sha256:" + "c" * 64,
    )
    unreadable = _make_entry(
        "job_w",
        "RESULT/structures/missing.xyz",
        checksum="sha256:" + "d" * 64,
    )
    assert source_store.upsert_index_entries([readable, unreadable]) == 2

    indexer = StructureSourceIndexer(
        store=job_store, source_store=source_store, run_root=tmp_path
    )
    written = indexer.backfill_molecule_identities()
    assert written == 1  # the unreadable row is skipped, never marked failed

    readable_uid = source_uid_for("job_w", "RESULT/structures/w.xyz")
    backfilled = _row(source_store, readable_uid)["molecule_identity_key"]
    assert str(backfilled).startswith("mol_")
    missing_uid = source_uid_for("job_w", "RESULT/structures/missing.xyz")
    assert _row(source_store, missing_uid)["molecule_identity_key"] is None
    assert len(_registry_rows(db_path)) == 1

    # Second call: nothing new to write, no raise from the still-missing file.
    assert indexer.backfill_molecule_identities() == 0


def test_backfill_skips_rows_without_checksum(
    job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
) -> None:
    """Remote placeholders (no checksum) are never given an identity."""
    _seed_job(job_store, tmp_path, "job_r", "RESULT/structures/r.xyz", _XYZ_METHANOL)
    entry = _make_entry("job_r", "RESULT/structures/r.xyz", checksum=None)
    assert source_store.upsert_index_entries([entry]) == 1
    assert source_store.list_rows_missing_molecule_identity() == []

    indexer = StructureSourceIndexer(
        store=job_store, source_store=source_store, run_root=tmp_path
    )
    assert indexer.backfill_molecule_identities() == 0


def test_identity_payload_is_json_serialisable() -> None:
    """The entry payload must survive the index write (JSON column neighbour)."""
    resolved = molecule_identity_for_text(_XYZ_WATER, charge=0, multiplicity=1)
    assert resolved is not None
    assert set(resolved) == {
        "identity_key",
        "scheme",
        "canonical_smiles",
        "charge",
        "multiplicity",
        "formula",
        "inchikey",
    }
    json.dumps(resolved)  # registry payload is plain JSON
