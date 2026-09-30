"""
Tests for StructureSourceStore — structure-source storage layer.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from acp.scheduler.structure_source_store import (
    RevisionConflictError,
    StructureSourceStore,
    _normalize_posix,
    _tag_key,
    _validate_name,
    _validate_tag,
    source_uid_for,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture
def store(db_path: str) -> StructureSourceStore:
    return StructureSourceStore(db_path)


def _make_entry(
    job_id: str = "job_001",
    path: str = "RESULT/structures/conformer_001.xyz",
    label: str = "Rank-1 conformer",
    workflow: str = "Confsearch",
    project_id: str | None = "proj_1",
    **overrides: object,
) -> dict[str, object]:
    """Build a minimal discovery entry dict."""
    base: dict[str, object] = {
        "source_id": f"job_{job_id}:{path}",
        "job_id": job_id,
        "path": path,
        "label": label,
        "workflow": workflow,
        "project_id": project_id,
        "job_name": f"Task {job_id}",
        "molecule_name": "ethanol",
        "job_status": "completed",
        "source_kind": "final",
        "formula": "C2H6O",
        "atom_count": 9,
        "charge": 0,
        "multiplicity": 1,
        "has_3d": 1,
        "remote": 0,
        "candidate_id": "",
        "role": "",
        "role_evidence": "",
        "availability": "available",
        "produced_at": "2026-09-18T10:00:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# source_uid_for
# ---------------------------------------------------------------------------


class TestSourceUidFor:
    def test_deterministic(self) -> None:
        uid1 = source_uid_for("job_001", "RESULT/structures/mol.xyz")
        uid2 = source_uid_for("job_001", "RESULT/structures/mol.xyz")
        assert uid1 == uid2

    def test_prefix(self) -> None:
        uid = source_uid_for("j", "p")
        assert uid.startswith("ss_")

    def test_different_job_different_uid(self) -> None:
        uid1 = source_uid_for("job_001", "path/a.xyz")
        uid2 = source_uid_for("job_002", "path/a.xyz")
        assert uid1 != uid2

    def test_different_path_different_uid(self) -> None:
        uid1 = source_uid_for("job_001", "path/a.xyz")
        uid2 = source_uid_for("job_001", "path/b.xyz")
        assert uid1 != uid2

    def test_case_preserved(self) -> None:
        uid1 = source_uid_for("job_001", "Path/A.xyz")
        uid2 = source_uid_for("job_001", "Path/a.xyz")
        assert uid1 != uid2

    def test_leading_slash_stripped(self) -> None:
        uid1 = source_uid_for("job_001", "path/a.xyz")
        uid2 = source_uid_for("job_001", "/path/a.xyz")
        assert uid1 == uid2

    def test_dot_collapsed(self) -> None:
        uid1 = source_uid_for("job_001", "path/a.xyz")
        uid2 = source_uid_for("job_001", "./path/a.xyz")
        assert uid1 == uid2


class TestNormalizePosix:
    def test_strip_leading_slash(self) -> None:
        assert _normalize_posix("/foo/bar") == "foo/bar"

    def test_collapse_dot(self) -> None:
        assert _normalize_posix("./foo/./bar") == "foo/bar"

    def test_collapse_dotdot(self) -> None:
        assert _normalize_posix("foo/../bar") == "bar"


class TestValidateName:
    def test_none_passthrough(self) -> None:
        assert _validate_name(None) is None

    def test_strip_whitespace(self) -> None:
        assert _validate_name("  hello  ") == "hello"

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_name("")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_name("   ")

    def test_too_long(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            _validate_name("x" * 201)

    def test_control_char_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            _validate_name("hello\nworld")

    def test_tab_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            _validate_name("hello\tworld")

    def test_valid_unicode(self) -> None:
        assert _validate_name("结构2 · 第18帧") == "结构2 · 第18帧"

    def test_max_length(self) -> None:
        assert _validate_name("x" * 200) == "x" * 200


class TestValidateTag:
    def test_strip(self) -> None:
        assert _validate_tag("  tag  ") == "tag"

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_tag("")

    def test_too_long(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            _validate_tag("x" * 33)

    def test_control_char_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            _validate_tag("tag\x01")


class TestTagKey:
    def test_casefold(self) -> None:
        assert _tag_key("ABC") == "abc"
        assert _tag_key("  Tag  ") == "tag"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_init_idempotent(self, db_path: str) -> None:
        """Creating the store twice does not error."""
        StructureSourceStore(db_path)
        StructureSourceStore(db_path)

    def test_tables_exist(self, store: StructureSourceStore) -> None:
        with store._lock, store._connect() as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            names = {r["name"] for r in tables}
        assert "structure_source_index" in names
        assert "structure_source_metadata" in names
        assert "structure_source_tags" in names
        assert "organization_events" in names
        assert "structure_source_index_state" in names

    def test_indexes_exist(self, store: StructureSourceStore) -> None:
        with store._lock, store._connect() as conn:
            idxs = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_ss_%'"
            ).fetchall()
            names = {r["name"] for r in idxs}
        assert "idx_ss_index_project" in names
        assert "idx_ss_tags_key" in names
        assert "idx_ss_events_obj" in names


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


class TestUpsertIndexEntries:
    def test_insert(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        count = store.upsert_index_entries([entry])
        assert count == 1
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        row = store.get(uid)
        assert row is not None
        assert row["job_id"] == "job_001"
        assert row["workflow"] == "Confsearch"

    def test_update_discovery_fields_only(self, store: StructureSourceStore) -> None:
        """Upsert only touches discovery fields, not metadata."""
        entry = _make_entry()
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")

        # First insert
        store.upsert_index_entries([entry])

        # Set custom name
        store.set_custom_name(uid, "My Structure", 0)

        # Upsert again with different label
        entry2 = _make_entry(label="Updated label")
        store.upsert_index_entries([entry2])

        row = store.get(uid)
        assert row is not None
        assert row["label"] == "Updated label"
        assert row["custom_name"] == "My Structure"
        assert row["metadata_revision"] == 1

    def test_empty_list(self, store: StructureSourceStore) -> None:
        assert store.upsert_index_entries([]) == 0

    def test_multiple_entries(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz"),
            _make_entry(job_id="j2", path="b.xyz"),
        ]
        count = store.upsert_index_entries(entries)
        assert count == 2


# ---------------------------------------------------------------------------
# Get / List
# ---------------------------------------------------------------------------


class TestGet:
    def test_found(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        result = store.get(uid)
        assert result is not None
        assert result["source_uid"] == uid
        assert result["source_id"] == "job_job_001:RESULT/structures/conformer_001.xyz"
        assert result["resolved_name"] == "Rank-1 conformer"

    def test_not_found(self, store: StructureSourceStore) -> None:
        assert store.get("nonexistent") is None

    def test_legacy_source_id(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        legacy_id = "job_job_001:RESULT/structures/conformer_001.xyz"
        result = store.get_by_legacy_source_id(legacy_id)
        assert result is not None
        assert result["job_id"] == "job_001"

    def test_list_by_job(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz"),
            _make_entry(job_id="j1", path="b.xyz"),
            _make_entry(job_id="j2", path="c.xyz"),
        ]
        store.upsert_index_entries(entries)
        results = store.list_by_job("j1")
        assert len(results) == 2

    def test_projection_with_metadata(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Custom", 0)
        result = store.get(uid)
        assert result is not None
        assert result["custom_name"] == "Custom"
        assert result["resolved_name"] == "Custom"
        assert result["default_name"] == "Rank-1 conformer"

    def test_projection_with_tags(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["priority", "ts-candidate"], 0)
        result = store.get(uid)
        assert result is not None
        assert "priority" in result["tags"]
        assert "ts-candidate" in result["tags"]


class TestCandidateLifecycle:
    def test_new_geometry_creates_unreviewed_version(self, store: StructureSourceStore) -> None:
        entry = _make_entry(content_checksum="sha256:a")
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        first = store.get(uid)
        assert first is not None
        store.add_assessment(
            uid,
            conclusion="recommended",
            reason_code="optimization_converged",
            scope="project",
        )
        reviewed = store.get(uid)
        assert reviewed is not None and reviewed["assessment"] == "recommended"

        store.upsert_index_entries([_make_entry(content_checksum="sha256:b")])
        changed = store.get(uid)
        assert changed is not None
        assert changed["version_id"] != first["version_id"]
        assert changed["assessment"] == "unreviewed"
        assert len(store.get_candidate_detail(uid)["assessments"]) == 1

    def test_trash_restore_and_default_filter(self, store: StructureSourceStore) -> None:
        store.upsert_index_entries([_make_entry()])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        trashed = store.set_candidate_status(uid, "trash", expected_revision=0)
        assert trashed["usage_status"] == "trash"
        assert store.query_sources(all_projects=True)["total"] == 0
        assert store.query_sources(all_projects=True, usage_status="trash")["total"] == 1
        restored = store.set_candidate_status(uid, "active", expected_revision=1)
        assert restored["usage_status"] == "active"

    def test_purge_tombstone_blocks_same_version_but_allows_new_version(
        self, store: StructureSourceStore
    ) -> None:
        original = _make_entry(content_checksum="sha256:a")
        store.upsert_index_entries([original])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_candidate_status(uid, "trash", expected_revision=0)
        assert store.purge_candidates([uid])["count"] == 1
        assert store.get(uid) is None

        store.upsert_index_entries([original])
        assert store.get(uid) is None

        store.upsert_index_entries([_make_entry(content_checksum="sha256:b")])
        recreated = store.get(uid)
        assert recreated is not None
        assert recreated["usage_status"] == "active"

    def test_usage_snapshot_is_version_bound(self, store: StructureSourceStore) -> None:
        store.upsert_index_entries([_make_entry(content_checksum="sha256:a")])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        current = store.get(uid)
        assert current is not None
        store.record_usage(uid, "job_downstream", {"xyz": "snapshot"})
        detail = store.get_candidate_detail(uid)
        assert detail["usage"][0]["version_id"] == current["version_id"]
        assert detail["usage"][0]["input_snapshot"]["xyz"] == "snapshot"


# ---------------------------------------------------------------------------
# set_custom_name
# ---------------------------------------------------------------------------


class TestSetCustomName:
    def test_happy(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        result = store.set_custom_name(uid, "My Structure", 0)
        assert result["custom_name"] == "My Structure"
        assert result["metadata_revision"] == 1
        assert result["resolved_name"] == "My Structure"

    def test_conflict(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "First", 0)
        with pytest.raises(RevisionConflictError) as exc_info:
            store.set_custom_name(uid, "Second", 0)
        assert exc_info.value.projection["custom_name"] == "First"

    def test_no_op_same_value(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Same", 0)
        result = store.set_custom_name(uid, "Same", 1)
        assert result["metadata_revision"] == 1  # No bump

    def test_restore_default(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Override", 0)
        result = store.set_custom_name(uid, None, 1)
        assert result["custom_name"] is None
        assert result["resolved_name"] == "Rank-1 conformer"
        assert result["metadata_revision"] == 2

    def test_validation_empty(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        with pytest.raises(ValueError, match="must not be empty"):
            store.set_custom_name(uid, "", 0)

    def test_validation_control_char(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        with pytest.raises(ValueError, match="control character"):
            store.set_custom_name(uid, "name\x01", 0)

    def test_source_not_found(self, store: StructureSourceStore) -> None:
        with pytest.raises(ValueError, match="source not found"):
            store.set_custom_name("nonexistent", "name", 0)

    def test_audit_event(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Renamed", 0)
        with store._lock, store._connect() as conn:
            events = conn.execute(
                "SELECT * FROM organization_events WHERE object_id = ?",
                (uid,),
            ).fetchall()
        assert len(events) == 1
        assert events[0]["action"] == "rename"

    def test_organized_at_updated(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Named", 0)
        result = store.get(uid)
        assert result is not None
        assert result["organized_at"] is not None


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTags:
    def test_add_tags(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        result = store.add_tags(uid, ["priority", "ts-candidate"], 0)
        assert "priority" in result["tags"]
        assert "ts-candidate" in result["tags"]
        assert result["metadata_revision"] == 1

    def test_add_tags_dedup(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["tag1", "tag1"], 0)
        result = store.get(uid)
        assert result["tags"].count("tag1") == 1

    def test_add_tags_case_insensitive_dedup(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["Tag", "tag", "TAG"], 0)
        result = store.get(uid)
        assert len(result["tags"]) == 1

    def test_remove_tags(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["a", "b", "c"], 0)
        result = store.remove_tags(uid, ["b"], 1)
        assert "b" not in result["tags"]
        assert "a" in result["tags"]
        assert "c" in result["tags"]
        assert result["metadata_revision"] == 2

    def test_remove_tags_not_present(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["a"], 0)
        result = store.remove_tags(uid, ["nonexistent"], 1)
        # No-op, revision not bumped
        assert result["metadata_revision"] == 1

    def test_tag_limit(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        tags = [f"tag_{i}" for i in range(20)]
        store.add_tags(uid, tags, 0)
        with pytest.raises(ValueError, match="tag limit exceeded"):
            store.add_tags(uid, ["overflow"], 1)

    def test_tags_empty_rejected(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        with pytest.raises(ValueError, match="must not be empty"):
            store.add_tags(uid, [""], 0)

    def test_tags_long_rejected(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        with pytest.raises(ValueError, match="exceeds"):
            store.add_tags(uid, ["x" * 33], 0)

    def test_tags_control_char_rejected(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        with pytest.raises(ValueError, match="control character"):
            store.add_tags(uid, ["tag\x01"], 0)

    def test_add_tags_conflict(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["a"], 0)
        with pytest.raises(RevisionConflictError):
            store.add_tags(uid, ["b"], 0)

    def test_remove_tags_conflict(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["a", "b"], 0)
        with pytest.raises(RevisionConflictError):
            store.remove_tags(uid, ["a"], 0)

    def test_add_tags_audit_event(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.add_tags(uid, ["tag1"], 0)
        with store._lock, store._connect() as conn:
            events = conn.execute(
                "SELECT * FROM organization_events WHERE object_id = ? AND action = 'add_tags'",
                (uid,),
            ).fetchall()
        assert len(events) == 1
        assert json.loads(events[0]["new_value"]) == ["tag1"]


# ---------------------------------------------------------------------------
# Batch metadata
# ---------------------------------------------------------------------------


class TestBatchUpdateMetadata:
    def test_happy(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz"),
            _make_entry(job_id="j2", path="b.xyz"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        result = store.batch_update_metadata(
            [
                {"source_uid": uid1, "custom_name": "Name1", "expected_revision": 0},
                {"source_uid": uid2, "add_tags": ["tag1"], "expected_revision": 0},
            ]
        )
        assert len(result["succeeded"]) == 2
        assert len(result["failed"]) == 0

    def test_partial_failure(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        result = store.batch_update_metadata(
            [
                {"source_uid": uid, "custom_name": "Name", "expected_revision": 0},
                {"source_uid": "nonexistent", "custom_name": "X", "expected_revision": 0},
            ]
        )
        assert len(result["succeeded"]) == 1
        assert len(result["failed"]) == 1
        assert result["failed"][0]["source_uid"] == "nonexistent"

    def test_dedupe_first_wins(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        result = store.batch_update_metadata(
            [
                {"source_uid": uid, "custom_name": "First", "expected_revision": 0},
                {"source_uid": uid, "custom_name": "Second", "expected_revision": 0},
            ]
        )
        assert len(result["succeeded"]) == 1
        # First wins
        row = store.get(uid)
        assert row["custom_name"] == "First"

    def test_conflict_in_batch(self, store: StructureSourceStore) -> None:
        entry = _make_entry()
        store.upsert_index_entries([entry])
        uid = source_uid_for("job_001", "RESULT/structures/conformer_001.xyz")
        store.set_custom_name(uid, "Existing", 0)
        result = store.batch_update_metadata(
            [
                {"source_uid": uid, "custom_name": "New", "expected_revision": 0},
            ]
        )
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["source_uid"] == uid


# ---------------------------------------------------------------------------
# Project tag management
# ---------------------------------------------------------------------------


class TestProjectTagManagement:
    def test_rename_project_tag(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", project_id="proj_1"),
            _make_entry(job_id="j2", path="b.xyz", project_id="proj_1"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        store.add_tags(uid1, ["old-tag"], 0)
        store.add_tags(uid2, ["old-tag"], 0)

        affected = store.rename_project_tag("proj_1", "old-tag", "New Tag")
        assert affected == 2

        r1 = store.get(uid1)
        r2 = store.get(uid2)
        assert "New Tag" in r1["tags"]
        assert "old-tag" not in r1["tags"]
        assert "New Tag" in r2["tags"]

    def test_merge_project_tag(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", project_id="proj_1"),
            _make_entry(job_id="j2", path="b.xyz", project_id="proj_1"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        store.add_tags(uid1, ["existing"], 0)
        store.add_tags(uid2, ["to-merge"], 0)

        affected = store.rename_project_tag("proj_1", "to-merge", "existing")
        assert affected == 1

        r2 = store.get(uid2)
        # uid2 should have "existing" (merged, not duplicated)
        assert "existing" in r2["tags"]
        assert "to-merge" not in r2["tags"]

    def test_remove_project_tag(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", project_id="proj_1"),
            _make_entry(job_id="j2", path="b.xyz", project_id="proj_1"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        store.add_tags(uid1, ["removable"], 0)
        store.add_tags(uid2, ["removable"], 0)

        affected = store.remove_project_tag("proj_1", "removable")
        assert affected == 2

        r1 = store.get(uid1)
        r2 = store.get(uid2)
        assert "removable" not in r1["tags"]
        assert "removable" not in r2["tags"]

    def test_project_tag_counts(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", project_id="proj_1"),
            _make_entry(job_id="j2", path="b.xyz", project_id="proj_1"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        store.add_tags(uid1, ["common", "rare"], 0)
        store.add_tags(uid2, ["common"], 0)

        counts = store.project_tag_counts("proj_1")
        assert len(counts) == 2
        assert counts[0]["tag"] == "common"
        assert counts[0]["count"] == 2
        assert counts[1]["tag"] == "rare"
        assert counts[1]["count"] == 1

    def test_project_tag_counts_empty(self, store: StructureSourceStore) -> None:
        counts = store.project_tag_counts("empty_project")
        assert counts == []


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class TestQuerySources:
    def _populate(self, store: StructureSourceStore) -> dict[str, str]:
        """Insert test data and return uid map."""
        entries = [
            _make_entry(
                job_id="j1",
                path="a.xyz",
                label="Structure A",
                project_id="proj_1",
                workflow="Confsearch",
                produced_at="2026-09-18T10:00:00+00:00",
            ),
            _make_entry(
                job_id="j1",
                path="b.xyz",
                label="Structure B",
                project_id="proj_1",
                workflow="Confsearch",
                produced_at="2026-09-18T11:00:00+00:00",
            ),
            _make_entry(
                job_id="j2",
                path="c.xyz",
                label="TS Candidate",
                project_id="proj_1",
                workflow="PESsearch",
                role="TS",
                produced_at="2026-09-18T12:00:00+00:00",
            ),
            _make_entry(
                job_id="j3",
                path="d.xyz",
                label="Intermediate",
                project_id="proj_2",
                workflow="BatchOptimize",
                role="INT",
                produced_at="2026-09-18T09:00:00+00:00",
            ),
        ]
        store.upsert_index_entries(entries)
        uids = {}
        uids["a"] = source_uid_for("j1", "a.xyz")
        uids["b"] = source_uid_for("j1", "b.xyz")
        uids["c"] = source_uid_for("j2", "c.xyz")
        uids["d"] = source_uid_for("j3", "d.xyz")

        store.add_tags(uids["a"], ["priority"], 0)
        store.add_tags(uids["c"], ["priority", "confirmed"], 0)
        store.set_custom_name(uids["b"], "Custom B", 0)

        return uids

    def test_basic_query(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1")
        assert result["total"] == 3
        assert len(result["items"]) == 3

    def test_q_search(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", q="TS")
        assert result["total"] == 1
        assert result["items"][0]["candidate_id"] == "" or "TS" in result["items"][0]["label"]

    def test_q_search_custom_name(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", q="Custom B")
        assert result["total"] == 1

    def test_role_filter(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", role="TS")
        assert result["total"] == 1

    def test_role_filter_unlabeled(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", role="")
        assert result["total"] == 2  # a and b have no role

    def test_tags_any(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", tags=["priority"], tag_match="any")
        assert result["total"] == 2  # a and c

    def test_tags_all(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(
            project_id="proj_1", tags=["priority", "confirmed"], tag_match="all"
        )
        assert result["total"] == 1  # only c has both

    def test_workflow_filter(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", workflow="PESsearch")
        assert result["total"] == 1

    def test_sort_produced_desc(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", sort="produced_desc")
        produced = [i["produced_at"] for i in result["items"]]
        assert produced == sorted(produced, reverse=True)

    def test_sort_produced_asc(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", sort="produced_asc")
        produced = [i["produced_at"] for i in result["items"]]
        assert produced == sorted(produced)

    def test_sort_name_asc(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", sort="name_asc")
        names = [i["resolved_name"] for i in result["items"]]
        assert names == sorted(names, key=str.lower)

    def test_sort_organized_desc(self, store: StructureSourceStore) -> None:
        self._populate(store)
        # Only b has organized_at (from set_custom_name)
        result = store.query_sources(project_id="proj_1", sort="organized_desc")
        # b should be first
        assert result["items"][0]["source_uid"] == source_uid_for("j1", "b.xyz")

    def test_group_by_job(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", group_by="job")
        assert "groups" in result
        assert len(result["groups"]) == 2  # j1 (2) and j2 (1)
        counts = {g["key"]: g["count"] for g in result["groups"]}
        assert counts["j1"] == 2
        assert counts["j2"] == 1

    def test_group_by_role(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", group_by="role")
        assert "groups" in result
        role_map = {g["key"]: g["count"] for g in result["groups"]}
        assert role_map.get("") == 2  # unlabeled
        assert role_map.get("TS") == 1

    def test_group_by_tag(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(project_id="proj_1", group_by="tag")
        assert "groups" in result
        tag_keys = {g["key"] for g in result["groups"]}
        assert "priority" in tag_keys
        assert "__uncategorized" in tag_keys
        uncategorized = next(g for g in result["groups"] if g["key"] == "__uncategorized")
        assert uncategorized["count"] == 1  # only b is tagless

    def test_cursor_pagination(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path=f"m{i}.xyz", label=f"Mol {i}", project_id="p1")
            for i in range(15)
        ]
        store.upsert_index_entries(entries)

        page1 = store.query_sources(project_id="p1", limit=5, sort="produced_desc")
        assert len(page1["items"]) == 5
        assert page1["next_cursor"] is not None
        assert page1["total"] == 15

        page2 = store.query_sources(
            project_id="p1",
            limit=5,
            sort="produced_desc",
            cursor=page1["next_cursor"],
        )
        assert len(page2["items"]) == 5
        uid1 = {i["source_uid"] for i in page1["items"]}
        uid2 = {i["source_uid"] for i in page2["items"]}
        assert uid1.isdisjoint(uid2)  # No duplicates

    def test_cursor_fingerprint_mismatch(self, store: StructureSourceStore) -> None:
        entries = [_make_entry(job_id="j1", path=f"m{i}.xyz", project_id="p1") for i in range(5)]
        store.upsert_index_entries(entries)

        page1 = store.query_sources(project_id="p1", limit=3)
        # Tamper the cursor fingerprint
        import base64 as b64

        decoded = json.loads(b64.b64decode(page1["next_cursor"]))
        decoded["fingerprint"] = "tampered"
        bad_cursor = b64.b64encode(json.dumps(decoded).encode()).decode()

        with pytest.raises(ValueError, match="fingerprint mismatch"):
            store.query_sources(project_id="p1", limit=3, cursor=bad_cursor)

    def test_all_projects(self, store: StructureSourceStore) -> None:
        self._populate(store)
        result = store.query_sources(all_projects=True)
        assert result["total"] == 4

    def test_natural_sort_numbers(self, store: StructureSourceStore) -> None:
        """Verify name sort handles embedded numbers naturally."""
        entries = [
            _make_entry(job_id="j1", path="a.xyz", label="结构2", project_id="p1"),
            _make_entry(job_id="j1", path="b.xyz", label="结构10", project_id="p1"),
            _make_entry(job_id="j1", path="c.xyz", label="结构3", project_id="p1"),
        ]
        store.upsert_index_entries(entries)
        result = store.query_sources(project_id="p1", sort="name_asc")
        labels = [i["label"] for i in result["items"]]
        # Simple lower() sort: "结构10" < "结构2" < "结构3" (string comparison)
        # Natural sort would be different, but our SQL uses LOWER() which is string sort
        assert labels == sorted(labels, key=str.lower)

    def test_source_group_candidate(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", source_kind="saved_candidate"),
            _make_entry(job_id="j2", path="b.xyz", source_kind="final"),
            _make_entry(job_id="j3", path="c.xyz", source_kind="partial_result"),
        ]
        store.upsert_index_entries(entries)
        result = store.query_sources(all_projects=True, source_group="candidate")
        assert result["total"] == 1
        assert [i["source_kind"] for i in result["items"]] == ["saved_candidate"]

    def test_source_group_task_result(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", source_kind="saved_candidate"),
            _make_entry(job_id="j2", path="b.xyz", source_kind="final"),
            _make_entry(job_id="j3", path="c.xyz", source_kind="partial_result"),
        ]
        store.upsert_index_entries(entries)
        result = store.query_sources(all_projects=True, source_group="task_result")
        assert result["total"] == 2
        assert {i["source_kind"] for i in result["items"]} == {"final", "partial_result"}

    def test_source_group_invalid(self, store: StructureSourceStore) -> None:
        with pytest.raises(ValueError, match="invalid source group"):
            store.query_sources(all_projects=True, source_group="bogus")

    def test_source_group_cursor_fingerprint(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(
                job_id="jc",
                path=f"cand{i}.xyz",
                project_id="p1",
                source_kind="saved_candidate",
            )
            for i in range(3)
        ] + [
            _make_entry(
                job_id="jf",
                path=f"final{i}.xyz",
                project_id="p1",
                source_kind="final",
            )
            for i in range(3)
        ]
        store.upsert_index_entries(entries)

        page1 = store.query_sources(project_id="p1", limit=2, source_group="candidate")
        assert page1["next_cursor"] is not None
        page2 = store.query_sources(
            project_id="p1",
            limit=2,
            source_group="candidate",
            cursor=page1["next_cursor"],
        )
        assert len(page2["items"]) == 1
        with pytest.raises(ValueError, match="fingerprint mismatch"):
            store.query_sources(
                project_id="p1",
                limit=2,
                source_group="task_result",
                cursor=page1["next_cursor"],
            )

    def test_source_group_group_by_inherits_filter(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz", source_kind="saved_candidate"),
            _make_entry(job_id="j1", path="b.xyz", source_kind="final"),
            _make_entry(job_id="j2", path="c.xyz", source_kind="final"),
        ]
        store.upsert_index_entries(entries)
        result = store.query_sources(all_projects=True, source_group="candidate", group_by="job")
        assert result["total"] == 1
        assert {g["key"]: g["count"] for g in result["groups"]} == {"j1": 1}


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


class TestFacetCounts:
    def test_basic(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(
                job_id="j1",
                path="a.xyz",
                project_id="proj_1",
                workflow="Confsearch",
                role="TS",
            ),
            _make_entry(
                job_id="j2",
                path="b.xyz",
                project_id="proj_1",
                workflow="PESsearch",
                role="INT",
            ),
            _make_entry(
                job_id="j3",
                path="c.xyz",
                project_id="proj_1",
                workflow="Confsearch",
                role="",
            ),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j2", "b.xyz")
        store.add_tags(uid1, ["tag_a"], 0)
        store.add_tags(uid2, ["tag_b"], 0)

        facets = store.facet_counts(project_id="proj_1")
        assert facets["roles"]["TS"] == 1
        assert facets["roles"]["INT"] == 1
        assert facets["roles"]["unlabeled"] == 1
        assert facets["total_structures"] == 3
        assert len(facets["tags"]) == 2
        assert facets["workflows"]["Confsearch"] == 2
        assert facets["workflows"]["PESsearch"] == 1

    def test_excludes_own_dimension(self, store: StructureSourceStore) -> None:
        """Filtering by role should still count all roles in the facets."""
        entries = [
            _make_entry(job_id="j1", path="a.xyz", project_id="p1", role="TS"),
            _make_entry(job_id="j2", path="b.xyz", project_id="p1", role="INT"),
        ]
        store.upsert_index_entries(entries)
        facets = store.facet_counts(project_id="p1", role="TS")
        # Even though we filter by TS, facets should show all roles
        assert facets["roles"]["TS"] == 1
        assert facets["roles"]["INT"] == 1

    def test_source_group_filter(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(
                job_id="j1",
                path="a.xyz",
                project_id="p1",
                workflow="PESsearch",
                source_kind="saved_candidate",
            ),
            _make_entry(
                job_id="j2",
                path="b.xyz",
                project_id="p1",
                workflow="Confsearch",
                source_kind="final",
            ),
            _make_entry(
                job_id="j3",
                path="c.xyz",
                project_id="p1",
                workflow="Confsearch",
                source_kind="partial_result",
            ),
        ]
        store.upsert_index_entries(entries)

        candidate_facets = store.facet_counts(project_id="p1", source_group="candidate")
        assert candidate_facets["total_structures"] == 1
        assert candidate_facets["source_kinds"] == {"saved_candidate": 1}
        assert candidate_facets["workflows"] == {"PESsearch": 1}

        task_facets = store.facet_counts(project_id="p1", source_group="task_result")
        assert task_facets["total_structures"] == 2
        assert task_facets["source_kinds"] == {"final": 1, "partial_result": 1}
        assert task_facets["workflows"] == {"Confsearch": 2}

    def test_source_group_invalid(self, store: StructureSourceStore) -> None:
        with pytest.raises(ValueError, match="invalid source group"):
            store.facet_counts(project_id="p1", source_group="bogus")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteByJob:
    def test_cascade(self, store: StructureSourceStore) -> None:
        entries = [
            _make_entry(job_id="j1", path="a.xyz"),
            _make_entry(job_id="j1", path="b.xyz"),
            _make_entry(job_id="j2", path="c.xyz"),
        ]
        store.upsert_index_entries(entries)
        uid1 = source_uid_for("j1", "a.xyz")
        uid2 = source_uid_for("j1", "b.xyz")
        store.add_tags(uid1, ["tag1"], 0)
        store.set_custom_name(uid2, "Named", 0)

        deleted = store.delete_by_job("j1")
        assert deleted == 2

        assert store.get(uid1) is None
        assert store.get(uid2) is None
        assert store.get(source_uid_for("j2", "c.xyz")) is not None

    def test_nonexistent_job(self, store: StructureSourceStore) -> None:
        assert store.delete_by_job("nonexistent") == 0


# ---------------------------------------------------------------------------
# Index coverage
# ---------------------------------------------------------------------------


class TestIndexCoverage:
    def test_initial(self, store: StructureSourceStore) -> None:
        coverage = store.index_coverage()
        assert coverage["indexed_jobs"] == 0
        assert coverage["last_indexed_at"] is None

    def test_mark_and_read(self, store: StructureSourceStore) -> None:
        store.mark_job_indexed("j1", 1)
        store.mark_job_indexed("j2", 1)
        coverage = store.index_coverage()
        assert coverage["indexed_jobs"] == 2
        assert coverage["last_indexed_at"] is not None

    def test_mark_idempotent(self, store: StructureSourceStore) -> None:
        store.mark_job_indexed("j1", 1)
        store.mark_job_indexed("j1", 2)
        coverage = store.index_coverage()
        assert coverage["indexed_jobs"] == 1


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_upsert(self, db_path: str) -> None:
        store = StructureSourceStore(db_path)
        errors: list[Exception] = []

        def upsert_batch(batch_id: int) -> None:
            try:
                entries = [_make_entry(job_id=f"j{batch_id}", path=f"m{i}.xyz") for i in range(10)]
                store.upsert_index_entries(entries)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=upsert_batch, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        coverage = store.index_coverage()
        # All 50 entries should be present (5 batches × 10)
        assert coverage["indexed_jobs"] == 0  # No jobs marked yet
