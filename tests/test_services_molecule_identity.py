"""Molecule identity persistence (chemistry-network B1b): migration 029 + store.

Covers the migration (fresh DB, re-run no-op, additive upgrade of a hand-built
legacy DB) and the computation-free store
(:mod:`acp_services.store.molecule_identity`).  Stdlib ``sqlite3`` only — no
``acp`` import anywhere (architecture gate, ANTI #29).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from acp_services.store import molecule_identity as store
from acp_services.store.migrations import get_schema_version, migrate

_IDENTITY_COLUMNS = {
    "project_id",
    "identity_key",
    "scheme",
    "canonical_smiles",
    "charge",
    "multiplicity",
    "formula",
    "inchikey",
    "first_seen_at",
    "updated_at",
}

_LEGACY_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL DEFAULT '{}',
    work_dir TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    project_id TEXT,
    molecule_name TEXT NOT NULL DEFAULT '',
    task_name TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    task_dir_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    node_id TEXT,
    node_path TEXT,
    input_hash TEXT,
    result_manifest_path TEXT,
    current_stage TEXT,
    storage_mode TEXT NOT NULL DEFAULT 'local',
    layout_version INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _entry(key: str = "mol_0123456789abcdef01234567", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "identity_key": key,
        "scheme": "mol_identity_v1",
        "canonical_smiles": "CCO",
        "charge": 0,
        "multiplicity": 1,
        "formula": "C2H6O",
        "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    }
    entry.update(overrides)
    return entry


def _table_sql(db: Path, kind: str, name: str) -> list[str]:
    conn = _connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name=?",
            (kind, name),
        ).fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        conn.close()


def _columns(db: Path) -> set[str]:
    conn = _connect(db)
    try:
        return {str(row["name"]) for row in conn.execute("PRAGMA table_info(structure_identities)")}
    finally:
        conn.close()


def _migration_ids(db: Path) -> list[str]:
    conn = _connect(db)
    try:
        return [str(row["id"]) for row in conn.execute("SELECT id FROM _schema_migrations")]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration 029
# ---------------------------------------------------------------------------


def test_migration_creates_table_and_index_on_fresh_db(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    migrate(db)

    assert _table_sql(db, "table", "structure_identities") == ["structure_identities"]
    assert _table_sql(db, "index", "idx_structure_identities_project") == [
        "idx_structure_identities_project"
    ]
    assert _columns(db) == _IDENTITY_COLUMNS
    assert _migration_ids(db).count("029") == 1

    conn = _connect(db)
    try:
        version = get_schema_version(conn)
    finally:
        conn.close()
    assert version == len(_migration_ids(db))


def test_rerun_migrate_is_noop_and_records_029_once(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    assert migrate(db) > 0
    first_ids = _migration_ids(db)

    assert migrate(db) == 0
    assert _migration_ids(db) == first_ids
    assert _migration_ids(db).count("029") == 1


def test_legacy_db_without_table_is_upgraded(tmp_path: Path) -> None:
    """The missing "legacy DB upgrades" case: jobs/tasks exist, no 029 table."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO jobs (id, spec_json, work_dir) VALUES (?, ?, ?)",
            ("20260101_001_Confsearch", '{"molecule_name": "ethanol"}', "/runs/ethanol"),
        )
        conn.commit()
    finally:
        conn.close()

    assert _table_sql(db, "table", "structure_identities") == []

    applied = migrate(db)

    assert applied > 0
    assert _table_sql(db, "table", "structure_identities") == ["structure_identities"]
    assert _table_sql(db, "index", "idx_structure_identities_project") == [
        "idx_structure_identities_project"
    ]
    assert _columns(db) == _IDENTITY_COLUMNS
    assert _migration_ids(db).count("029") == 1
    # The pre-existing rows are untouched by the additive migration.
    conn = _connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
    finally:
        conn.close()


def test_migration_029_entry_shape() -> None:
    from acp_services.store.migrations import _MIGRATIONS

    ids = [migration["id"] for migration in _MIGRATIONS]
    assert ids.count("029") == 1
    assert "026" not in ids
    # 029 is appended after the previous latest id 028 and is the tail of the list.
    assert ids.index("029") == ids.index("028") + 1
    assert ids[-1] == "029"
    entry = next(migration for migration in _MIGRATIONS if migration["id"] == "029")
    assert entry["sql"].startswith("-- handled in Python")
    assert entry["description"].strip()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path):
    db = tmp_path / "jobs.db"
    migrate(db)
    connection = _connect(db)
    try:
        yield connection
    finally:
        connection.close()


def test_upsert_then_reupsert_refreshes_and_preserves_first_seen(conn) -> None:
    written = store.upsert_identities(
        conn, project_id="p1", identities=[_entry()], now="2026-11-03T00:00:00+00:00"
    )
    assert written == 1
    first = store.get_identity(conn, project_id="p1", identity_key=_entry()["identity_key"])
    assert first is not None
    assert first["canonical_smiles"] == "CCO"
    assert first["first_seen_at"] == "2026-11-03T00:00:00+00:00"
    assert first["updated_at"] == "2026-11-03T00:00:00+00:00"

    written = store.upsert_identities(
        conn,
        project_id="p1",
        identities=[_entry(canonical_smiles="CCO[H]", formula="C2H6O")],
        now="2026-11-04T00:00:00+00:00",
    )
    assert written == 1
    updated = store.get_identity(conn, project_id="p1", identity_key=_entry()["identity_key"])
    assert updated is not None
    assert updated["canonical_smiles"] == "CCO[H]"
    assert updated["first_seen_at"] == "2026-11-03T00:00:00+00:00"
    assert updated["updated_at"] == "2026-11-04T00:00:00+00:00"
    assert store.count_project_identities(conn, project_id="p1") == 1


@pytest.mark.parametrize("missing_field", ["inchikey", "canonical_smiles", "formula"])
def test_upsert_does_not_erase_stored_metadata(conn, missing_field: str) -> None:
    store.upsert_identities(conn, project_id="p1", identities=[_entry()])
    bare = _entry()
    bare.pop(missing_field)
    store.upsert_identities(
        conn, project_id="p1", identities=[bare], now="2026-11-05T00:00:00+00:00"
    )

    row = store.get_identity(conn, project_id="p1", identity_key=_entry()["identity_key"])
    assert row is not None
    assert row["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert row["canonical_smiles"] == "CCO"
    assert row["formula"] == "C2H6O"
    assert row["scheme"] == "mol_identity_v1"
    assert row["updated_at"] == "2026-11-05T00:00:00+00:00"


def test_upsert_never_erases_inchikey_with_explicit_none(conn) -> None:
    store.upsert_identities(conn, project_id="p1", identities=[_entry()])
    store.upsert_identities(conn, project_id="p1", identities=[_entry(inchikey=None)])

    row = store.get_identity(conn, project_id="p1", identity_key=_entry()["identity_key"])
    assert row is not None
    assert row["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"


def test_upsert_keeps_nullable_ints_when_absent(conn) -> None:
    store.upsert_identities(conn, project_id="p1", identities=[_entry(charge=-1, multiplicity=2)])
    bare = _entry()
    bare.pop("charge")
    bare.pop("multiplicity")
    store.upsert_identities(conn, project_id="p1", identities=[bare])

    row = store.get_identity(conn, project_id="p1", identity_key=_entry()["identity_key"])
    assert row is not None
    assert row["charge"] == -1
    assert row["multiplicity"] == 2


@pytest.mark.parametrize(
    "bad_entry",
    [
        {"scheme": "mol_identity_v1"},
        {"identity_key": "mol_x"},
        {"identity_key": "", "scheme": "mol_identity_v1"},
        {"identity_key": "mol_x", "scheme": "   "},
        {"identity_key": 42, "scheme": "mol_identity_v1"},
    ],
)
def test_invalid_entries_raise_and_write_nothing(conn, bad_entry: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        store.upsert_identities(conn, project_id="p1", identities=[bad_entry])
    assert store.count_project_identities(conn, project_id="p1") == 0


def test_rejected_batch_writes_nothing(conn) -> None:
    """A valid entry before an invalid one must not be half-written."""
    with pytest.raises(ValueError):
        store.upsert_identities(
            conn,
            project_id="p1",
            identities=[_entry(), {"identity_key": "mol_bad"}],
        )
    assert store.count_project_identities(conn, project_id="p1") == 0


def test_get_identity_unknown_returns_none(conn) -> None:
    assert store.get_identity(conn, project_id="p1", identity_key="mol_missing") is None


def test_upsert_empty_iterable_writes_nothing(conn) -> None:
    assert store.upsert_identities(conn, project_id="p1", identities=[]) == 0
    assert store.count_project_identities(conn, project_id="p1") == 0


def test_list_orders_by_identity_key_and_clamps(conn) -> None:
    keys = [f"mol_{index:024x}" for index in (3, 1, 2)]
    store.upsert_identities(conn, project_id="p1", identities=[_entry(key=k) for k in keys])

    listed = store.list_project_identities(conn, project_id="p1")
    assert [row["identity_key"] for row in listed] == sorted(keys)

    assert [row["identity_key"] for row in store.list_project_identities(
        conn, project_id="p1", limit=1
    )] == [sorted(keys)[0]]
    assert store.list_project_identities(conn, project_id="p1", offset=3) == []
    # Clamping: 0 and negative limits still page, huge limits do not explode.
    assert len(store.list_project_identities(conn, project_id="p1", limit=0)) == 1
    assert len(store.list_project_identities(conn, project_id="p1", limit=-5)) == 1
    assert len(store.list_project_identities(conn, project_id="p1", limit=10_000)) == 3
    assert len(
        store.list_project_identities(conn, project_id="p1", limit=10_000, offset=-7)
    ) == 3
    assert store.list_project_identities(conn, project_id="other") == []


def test_project_isolation_same_key_two_rows(conn) -> None:
    shared = _entry()
    store.upsert_identities(conn, project_id="p1", identities=[shared])
    store.upsert_identities(
        conn, project_id="p2", identities=[{**shared, "canonical_smiles": "CC"}]
    )

    assert store.count_project_identities(conn, project_id="p1") == 1
    assert store.count_project_identities(conn, project_id="p2") == 1
    p1 = store.get_identity(conn, project_id="p1", identity_key=shared["identity_key"])
    p2 = store.get_identity(conn, project_id="p2", identity_key=shared["identity_key"])
    assert p1 is not None and p2 is not None
    assert p1["canonical_smiles"] == "CCO"
    assert p2["canonical_smiles"] == "CC"


def test_store_does_not_commit_on_its_own(tmp_path: Path) -> None:
    """The caller owns the transaction: an uncommitted upsert is not visible."""
    db = tmp_path / "jobs.db"
    migrate(db)
    writer = _connect(db)
    writer.isolation_level = None  # autocommit off is emulated by explicit BEGIN
    writer.execute("BEGIN")
    try:
        store.upsert_identities(writer, project_id="p1", identities=[_entry()])
        observer = sqlite3.connect(str(db))
        try:
            observer.row_factory = sqlite3.Row
            seen = observer.execute(
                "SELECT COUNT(*) AS n FROM structure_identities"
            ).fetchone()["n"]
        finally:
            observer.close()
        assert seen == 0
        writer.execute("ROLLBACK")
    finally:
        writer.close()


def test_store_module_has_no_acp_import() -> None:
    """Anti-pattern #29: ``acp_services`` must never import ``acp`` (nor ``cccp``)."""
    source = Path(store.__file__).read_text(encoding="utf-8")
    forbidden = re.compile(r"^\s*(?:import|from)\s+(acp|cccp)(?:\.|\s|$)", re.MULTILINE)
    assert forbidden.findall(source) == []
    # Deeper forms the guard family also forbids.
    for needle in ("importlib", "__import__", "TYPE_CHECKING", "sys.modules"):
        assert needle not in source


def test_store_module_line_length() -> None:
    """ruff is not installed in this environment; check E501 (100 cols) by hand."""
    source = Path(store.__file__).read_text(encoding="utf-8")
    too_long = [line for line in source.splitlines() if len(line) > 100]
    assert too_long == []
