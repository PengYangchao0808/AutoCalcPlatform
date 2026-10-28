"""B08-3 (todo 44) — versioned archive export: schemas, digests, exclusion.

Covers the five acceptance clauses of
``.omo/plans/acp-parallel-plan-2-services.md`` todo 44:

1. the three package-class schemas (``full`` / ``products_only`` / ``reference``)
   with explicit gaps — a reference package never claims to be self-contained;
2. per-file digest + size verification (tamper detection);
3. credential exclusion by path *and* by content, plus a host-path leak scan;
4. the consistent-snapshot contract for an in-flight task (point-in-time facts,
   no execution authority) and for a source that keeps changing;
5. package-class boundaries (extension is not a format contract).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp_services.data.archive import (
    ARCHIVE_MANIFEST_FILENAME,
    ARCHIVE_MANIFEST_VERSION,
    ArchiveDigestError,
    ArchiveFileEntry,
    ArchiveManifest,
    ArchivePackageClass,
    ArchiveSnapshotError,
    ArchiveStatus,
    ArchiveVersionError,
    ExecutionState,
    LocalArchiveSource,
    SnapshotFence,
    build_archive_manifest,
    detect_content_type,
    find_host_paths,
    is_credential_path,
    scan_manifest_for_leaks,
    verify_archive_manifest,
)

_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 1.0\nH 0.0 1.0 0.0\n"
_CREATED = "2026-10-19T00:00:00+00:00"


def _write_tree(root: Path) -> None:
    (root / "RESULT" / "structures").mkdir(parents=True)
    (root / "RESULT" / "reports").mkdir(parents=True)
    (root / "WORK" / "opt").mkdir(parents=True)
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "task_1", "products": []}), encoding="utf-8"
    )
    (root / "RESULT" / "structures" / "final.xyz").write_text(_XYZ, encoding="utf-8")
    (root / "RESULT" / "reports" / "energy.json").write_text(
        json.dumps({"energy": -76.4}), encoding="utf-8"
    )
    (root / "WORK" / "opt" / "opt.out").write_text("ORCA TERMINATED NORMALLY\n", encoding="utf-8")


def _manifest(tmp_path: Path, package_class: ArchivePackageClass | str) -> ArchiveManifest:
    source = LocalArchiveSource(tmp_path, task_id="task_1", generation=4, revision=2)
    return build_archive_manifest(source, package_class=package_class, created_at=_CREATED)


# ---------------------------------------------------------------------------
# 1. the three package-class schemas
# ---------------------------------------------------------------------------


def test_full_package_schema_and_digests(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = LocalArchiveSource(tmp_path, task_id="task_1", generation=4, revision=2)
    manifest = build_archive_manifest(source, package_class="full", created_at=_CREATED)

    assert manifest.manifest_version == ARCHIVE_MANIFEST_VERSION
    assert manifest.to_dict()["package_class"] == "full"
    assert manifest.package_class is ArchivePackageClass.FULL
    assert manifest.status is ArchiveStatus.COMPLETE
    assert manifest.self_contained is True
    assert manifest.execution_authority is False
    assert manifest.point_in_time is False
    assert manifest.generation == 4
    assert manifest.revision == 2
    assert manifest.task_id == "task_1"
    assert manifest.gap_codes() == ()

    paths = {entry.relative_path for entry in manifest.files}
    assert paths == {
        "RESULT/result_manifest.json",
        "RESULT/structures/final.xyz",
        "RESULT/reports/energy.json",
        "WORK/opt/opt.out",
    }
    for entry in manifest.files:
        assert len(entry.sha256) == 64
        assert entry.size_bytes > 0
        assert entry.content_type
        assert entry.source in {"result", "work", "metadata"}

    verification = verify_archive_manifest(manifest, source)
    assert verification.ok is True
    assert verification.verified_files == 4
    assert verification.verified_references == 0


def test_products_only_omits_work_with_explicit_gap(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    manifest = _manifest(tmp_path, "products_only")

    assert manifest.package_class is ArchivePackageClass.PRODUCTS_ONLY
    assert manifest.status is ArchiveStatus.COMPLETE
    assert manifest.self_contained is False
    paths = {entry.relative_path for entry in manifest.files}
    assert paths and all(path.startswith("RESULT/") for path in paths)
    assert "WORK/opt/opt.out" not in paths
    assert "products_only_missing_work" in manifest.gap_codes()


def test_reference_package_is_not_self_contained(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    manifest = _manifest(tmp_path, "reference")

    assert manifest.package_class is ArchivePackageClass.REFERENCE
    assert manifest.self_contained is False
    included = {entry.relative_path for entry in manifest.files}
    referenced = {reference.relative_path for reference in manifest.references}
    # Only the identity descriptor is bundled; payloads are referenced by digest.
    assert included == {"RESULT/result_manifest.json"}
    assert {"WORK/opt/opt.out", "RESULT/structures/final.xyz", "RESULT/reports/energy.json"} == (
        referenced
    )
    assert "reference_not_self_contained" in manifest.gap_codes()
    for reference in manifest.references:
        assert len(reference.sha256) == 64
        assert reference.reason == "external_reference"


# ---------------------------------------------------------------------------
# 2. digest verification (tamper detection)
# ---------------------------------------------------------------------------


def test_digest_verification_detects_tampering(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = LocalArchiveSource(tmp_path, task_id="task_1")
    manifest = build_archive_manifest(source, package_class="full", created_at=_CREATED)
    assert verify_archive_manifest(manifest, source).ok is True

    (tmp_path / "RESULT" / "structures" / "final.xyz").write_text(
        "4\ntampered\nHe 0.0 0.0 0.0\nHe 0.0 0.0 1.0\nHe 0.0 1.0 0.0\nHe 0.0 1.0 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ArchiveDigestError):
        verify_archive_manifest(manifest, source)


def test_manifest_write_read_round_trip_and_version_guard(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = LocalArchiveSource(tmp_path, task_id="task_1")
    manifest = build_archive_manifest(source, package_class="full", created_at=_CREATED)
    path = manifest.write(tmp_path / "export" / ARCHIVE_MANIFEST_FILENAME)

    assert path.name == ARCHIVE_MANIFEST_FILENAME
    loaded = ArchiveManifest.read(path)
    assert loaded.to_dict() == manifest.to_dict()

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest_version"] = 999
    bad = tmp_path / "bad_manifest.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArchiveVersionError):
        ArchiveManifest.read(bad)


# ---------------------------------------------------------------------------
# 3. credential exclusion + host-path leak scan
# ---------------------------------------------------------------------------


def test_credentials_excluded_by_path_and_content(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n", encoding="utf-8"
    )
    (tmp_path / "RESULT" / ".env").write_text(
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )
    # Path looks innocent; only the *content* marks it as a credential.
    (tmp_path / "RESULT" / "notes.txt").write_text("password = hunter2secret\n", encoding="utf-8")

    manifest = _manifest(tmp_path, "full")
    included = {entry.relative_path for entry in manifest.files}
    assert ".ssh/id_rsa" not in included
    assert "RESULT/.env" not in included
    assert "RESULT/notes.txt" not in included
    assert {".ssh/id_rsa", "RESULT/.env", "RESULT/notes.txt"} <= set(manifest.excluded_paths)
    assert scan_manifest_for_leaks(manifest) == ()


def test_injected_credential_content_is_excluded(tmp_path: Path) -> None:
    """QA failure scenario: an injected API key file must be excluded."""
    (tmp_path / "RESULT").mkdir(parents=True)
    (tmp_path / "RESULT" / "app_config.yaml").write_text(
        "api_key: sk-abcdefghijklmnopqrstuvwx123456\n", encoding="utf-8"
    )
    manifest = _manifest(tmp_path, "full")
    assert "RESULT/app_config.yaml" not in {entry.relative_path for entry in manifest.files}
    assert "RESULT/app_config.yaml" in manifest.excluded_paths
    assert scan_manifest_for_leaks(manifest) == ()


def test_leak_scanner_catches_injected_host_path(tmp_path: Path) -> None:
    entry = ArchiveFileEntry(
        relative_path="/home/alice/.ssh/id_rsa",
        sha256="0" * 64,
        size_bytes=10,
        content_type="text/plain",
        source="metadata",
    )
    manifest = ArchiveManifest(
        manifest_version=ARCHIVE_MANIFEST_VERSION,
        package_class=ArchivePackageClass.FULL,
        task_id="task_1",
        generation=0,
        revision=0,
        status=ArchiveStatus.COMPLETE,
        files=(entry,),
    )
    leaks = scan_manifest_for_leaks(manifest)
    assert leaks
    assert any("host path" in item for item in leaks)
    assert any("credential" in item for item in leaks)

    assert find_host_paths("root at /var/lib/runs/task") == ("/var/lib/runs/task",)
    assert is_credential_path("WORK/.aws/credentials") is True
    assert is_credential_path("RESULT/structures/final.xyz") is False


# ---------------------------------------------------------------------------
# 4. consistent-snapshot contract
# ---------------------------------------------------------------------------


def test_in_flight_task_is_point_in_time_without_execution_authority(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = LocalArchiveSource(
        tmp_path,
        task_id="task_1",
        generation=3,
        revision=1,
        execution_state=ExecutionState.IN_FLIGHT,
    )
    manifest = build_archive_manifest(source, package_class="full", created_at=_CREATED)

    assert manifest.status is ArchiveStatus.PARTIAL
    assert manifest.point_in_time is True
    assert manifest.execution_authority is False
    assert manifest.self_contained is False
    assert "in_flight_point_in_time" in manifest.gap_codes()
    # The fence still records exactly which instant the facts belong to.
    assert manifest.generation == 3
    assert manifest.revision == 1


class _UnstableSource(LocalArchiveSource):
    """A source whose fence moves on every observation (artifacts changing)."""

    def fence(self) -> SnapshotFence:
        self._observations = getattr(self, "_observations", 0) + 1
        return SnapshotFence(generation=self._observations, revision=0)


def test_changing_source_becomes_explicit_partial(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = _UnstableSource(tmp_path, task_id="task_1")
    manifest = build_archive_manifest(
        source, package_class="full", max_attempts=3, created_at=_CREATED
    )

    assert manifest.status is ArchiveStatus.PARTIAL
    assert manifest.self_contained is False
    assert "snapshot_unstable" in manifest.gap_codes()
    assert manifest.export_attempts == 3


def test_expected_fence_pins_and_rejects_moved_source(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    source = LocalArchiveSource(tmp_path, task_id="task_1", generation=7, revision=3)
    manifest = build_archive_manifest(
        source,
        package_class="full",
        expected_fence=SnapshotFence(7, 3),
        created_at=_CREATED,
    )
    assert manifest.generation == 7
    assert manifest.revision == 3

    with pytest.raises(ArchiveSnapshotError):
        build_archive_manifest(
            source,
            package_class="full",
            expected_fence=SnapshotFence(8, 3),
            created_at=_CREATED,
        )


# ---------------------------------------------------------------------------
# 5. package-class boundaries — extension is not a format contract
# ---------------------------------------------------------------------------


def test_content_type_is_sniffed_not_from_extension(tmp_path: Path) -> None:
    assert detect_content_type(_XYZ.encode("utf-8")) == "chemical/x-xyz"
    assert detect_content_type(json.dumps({"a": 1}).encode("utf-8")) == "application/json"

    (tmp_path / "data.json").write_text(_XYZ, encoding="utf-8")
    (tmp_path / "molecule.xyz").write_text(json.dumps({"a": 1}), encoding="utf-8")
    source = LocalArchiveSource(tmp_path, task_id="task_1")
    manifest = build_archive_manifest(source, package_class="full", created_at=_CREATED)
    by_path = {entry.relative_path: entry.content_type for entry in manifest.files}
    assert by_path["data.json"] == "chemical/x-xyz"
    assert by_path["molecule.xyz"] == "application/json"


def test_unsafe_absolute_source_path_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "RESULT").mkdir(parents=True)
    (tmp_path / "RESULT" / "ok.txt").write_text("hello\n", encoding="utf-8")
    manifest = _manifest(tmp_path, "full")
    assert all(not entry.relative_path.startswith("/") for entry in manifest.files)
    assert scan_manifest_for_leaks(manifest) == ()
