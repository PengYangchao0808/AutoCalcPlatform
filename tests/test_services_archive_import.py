"""B08-4 (todo 45) — quota-limited archive import with atomic registration.

Covers the five acceptance clauses of
``.omo/plans/acp-parallel-plan-2-services.md`` todo 45:

1. local archive -> import -> read round-trip (digest-preserving);
2. duplicate import is idempotent (one registration, never an overwrite);
3. corrupt/dangerous archives are rejected (traversal / symlink / zip-bomb /
   digest mismatch / schema / execution-authority) with **no** partial success;
4. the scientific source mapping + external provenance are preserved (and
   different permissions are never merged);
5. import never auto-executes a scientific program and never unpickles Python
   objects (runtime marker + AST guard).

Plus the crash-window contract: a run that dies before the DB commit is never
visible and is reconcilable.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from acp.services_data_bootstrap import (
    build_archive_import_service,
    build_local_import_bundle,
)
from acp_services.data.archive import (
    ARCHIVE_MANIFEST_VERSION,
    ArchiveFileEntry,
    ArchiveManifest,
    ArchivePackageClass,
    ArchiveStatus,
    LocalArchiveSource,
    build_archive_manifest,
)
from acp_services.data.import_ import (
    BundleEntry,
    ExternalProvenance,
    ImportIntegrityError,
    ImportQuota,
    ImportRecordStore,
    ImportRejectedError,
    ImportRequest,
    ImportSizeLimitError,
    ImportState,
    RejectionReason,
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


def _manifest(root: Path, package_class: str = "full") -> ArchiveManifest:
    source = LocalArchiveSource(root, task_id="task_1", generation=4, revision=2)
    return build_archive_manifest(source, package_class=package_class, created_at=_CREATED)


def _service(tmp_path: Path) -> object:
    return build_archive_import_service(tmp_path / "import_root")


def _request(
    manifest: ArchiveManifest,
    *,
    namespace: str = "ext",
    source_id: str = "job-9",
    scope: str = "local-single",
    revision: str = "rev-3",
    provenance: ExternalProvenance | None = None,
) -> ImportRequest:
    return ImportRequest(
        source_namespace=namespace,
        source_id=source_id,
        manifest=manifest,
        permission_scope=scope,
        source_revision=revision,
        provenance=provenance,
    )


class _StubBundle:
    """A minimal :class:`ImportBundlePort` double for hostile-package tests."""

    def __init__(
        self,
        entries: list[BundleEntry],
        data: dict[str, bytes],
        stored: dict[str, int] | None = None,
    ) -> None:
        self._entries = entries
        self._data = data
        self._stored = stored or {}

    def list_entries(self) -> tuple[BundleEntry, ...]:
        return tuple(self._entries)

    def read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        data = self._data[relative_path]
        if len(data) > int(max_bytes):
            raise ImportSizeLimitError(f"{relative_path} exceeds {max_bytes}")
        return data

    def stored_size(self, relative_path: str) -> int | None:
        return self._stored.get(relative_path)


def _hostile_manifest(
    files: tuple[ArchiveFileEntry, ...],
    *,
    execution_authority: bool = False,
    manifest_version: int = ARCHIVE_MANIFEST_VERSION,
) -> ArchiveManifest:
    return ArchiveManifest(
        manifest_version=manifest_version,
        package_class=ArchivePackageClass.FULL,
        task_id="hostile",
        generation=0,
        revision=0,
        status=ArchiveStatus.COMPLETE,
        files=files,
        execution_authority=execution_authority,
    )


# ---------------------------------------------------------------------------
# 1. local -> import -> read round-trip
# ---------------------------------------------------------------------------


def test_local_import_read_round_trip(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)

    service = _service(tmp_path)
    result = service.import_package(_request(manifest), build_local_import_bundle(package))

    assert result.registered is True
    assert result.duplicate is False
    assert result.state is ImportState.REGISTERED
    assert result.execution_authority is False
    assert result.auto_executed is False
    assert result.binding is not None
    assert result.mapping is not None
    assert result.binding.relative_path == f"imports/{result.platform_id}"
    assert {entry.relative_path for entry in result.files} == {
        "RESULT/result_manifest.json",
        "RESULT/structures/final.xyz",
        "RESULT/reports/energy.json",
        "WORK/opt/opt.out",
    }

    # Read every registered file back and prove byte-for-byte equality.
    assert service.read_file(result.platform_id, "RESULT/structures/final.xyz") == _XYZ.encode()
    assert (
        service.read_file(result.platform_id, "WORK/opt/opt.out") == b"ORCA TERMINATED NORMALLY\n"
    )
    stored = service.read_manifest(result.platform_id)
    assert stored["task_id"] == "task_1"
    assert stored["generation"] == 4
    # File rows are durable and readable from a fresh store connection.
    with ImportRecordStore(tmp_path / "import_root" / "acp_jobs.db") as store:
        reread = store.get_import(result.platform_id)
    assert reread is not None
    assert len(reread.files) == 4


# ---------------------------------------------------------------------------
# 2. duplicate import idempotency
# ---------------------------------------------------------------------------


def test_duplicate_import_is_idempotent(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)
    service = _service(tmp_path)

    first = service.import_package(_request(manifest), build_local_import_bundle(package))
    published = (
        Path(first.binding.root) / first.binding.relative_path / "RESULT/structures/final.xyz"
    )
    before = published.read_bytes()

    second = service.import_package(_request(manifest), build_local_import_bundle(package))

    assert second.duplicate is True
    assert second.platform_id == first.platform_id
    assert second.idempotency_key == first.idempotency_key
    assert len(service.list()) == 1
    assert published.read_bytes() == before  # never re-published / overwritten


def test_different_permission_scopes_are_not_merged(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)
    service = _service(tmp_path)

    primary = service.import_package(
        _request(manifest, scope="local-single"), build_local_import_bundle(package)
    )
    shared = service.import_package(
        _request(manifest, scope="team-a"), build_local_import_bundle(package)
    )

    assert primary.platform_id != shared.platform_id
    assert primary.mapping is not None and shared.mapping is not None
    # The *scientific* content identity is identical even though the
    # registrations (and their directories) are distinct.
    assert primary.mapping.content_identity == shared.mapping.content_identity
    assert len(service.list()) == 2


# ---------------------------------------------------------------------------
# 3. corrupt / dangerous archives are rejected with no partial success
# ---------------------------------------------------------------------------


def _assert_rejected_no_partial(
    service: object, tmp_path: Path, request: ImportRequest, reason: RejectionReason
) -> None:
    with pytest.raises(ImportRejectedError) as excinfo:
        service.import_package(request, _request_bundle(tmp_path))
    assert excinfo.value.detail.reason is reason
    # No registered import, no visible half-registration.
    assert service.list() == ()
    assert service.get(request.platform_id) is None
    staging = service.store.get_staging(request.idempotency_key)
    assert staging is not None
    assert staging.state is ImportState.REJECTED


def _request_bundle(tmp_path: Path) -> object:
    package = tmp_path / "empty_package"
    package.mkdir(exist_ok=True)
    return build_local_import_bundle(package)


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="../escape.txt",
                sha256="0" * 64,
                size_bytes=1,
                content_type="text/plain",
                source="result",
            ),
        )
    )
    _assert_rejected_no_partial(service, tmp_path, _request(manifest), RejectionReason.TRAVERSAL)


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="/etc/passwd",
                sha256="0" * 64,
                size_bytes=1,
                content_type="text/plain",
                source="result",
            ),
        )
    )
    _assert_rejected_no_partial(
        service, tmp_path, _request(manifest), RejectionReason.ABSOLUTE_PATH
    )


def test_symlink_entry_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "real.txt").write_text("hello\n", encoding="utf-8")
    (package / "link.txt").symlink_to(package / "real.txt")
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="link.txt",
                sha256="0" * 64,
                size_bytes=6,
                content_type="text/plain",
                source="result",
            ),
        )
    )
    service = _service(tmp_path)
    with pytest.raises(ImportRejectedError) as excinfo:
        service.import_package(_request(manifest), build_local_import_bundle(package))
    assert excinfo.value.detail.reason is RejectionReason.SYMLINK
    assert service.list() == ()


def test_zip_bomb_amplification_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="bomb.bin",
                sha256="0" * 64,
                size_bytes=1_000_000,
                content_type="application/octet-stream",
                source="result",
            ),
        )
    )
    bundle = _StubBundle(
        entries=[BundleEntry(relative_path="bomb.bin", stored_size=10)],
        data={"bomb.bin": b"x" * 1_000_000},
        stored={"bomb.bin": 10},
    )
    with pytest.raises(ImportRejectedError) as excinfo:
        service.import_package(_request(manifest), bundle)
    assert excinfo.value.detail.reason is RejectionReason.QUOTA_AMPLIFICATION
    assert service.list() == ()


def test_oversize_declared_file_is_rejected(tmp_path: Path) -> None:
    service = build_archive_import_service(
        tmp_path / "import_root", quota=ImportQuota(max_file_bytes=8)
    )
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="big.txt",
                sha256="0" * 64,
                size_bytes=100,
                content_type="text/plain",
                source="result",
            ),
        )
    )
    bundle = _StubBundle(
        entries=[BundleEntry(relative_path="big.txt")],
        data={"big.txt": b"x" * 100},
    )
    with pytest.raises(ImportRejectedError) as excinfo:
        service.import_package(_request(manifest), bundle)
    assert excinfo.value.detail.reason is RejectionReason.QUOTA_FILE_BYTES
    assert service.list() == ()


def test_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "final.xyz").write_text(_XYZ, encoding="utf-8")
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="final.xyz",
                sha256="f" * 64,  # deliberately wrong
                size_bytes=len(_XYZ.encode()),
                content_type="chemical/x-xyz",
                source="result",
            ),
        )
    )
    service = _service(tmp_path)
    with pytest.raises(ImportRejectedError) as excinfo:
        service.import_package(_request(manifest), build_local_import_bundle(package))
    assert excinfo.value.detail.reason is RejectionReason.DIGEST_MISMATCH
    assert service.list() == ()


def test_missing_body_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="RESULT/missing.xyz",
                sha256="0" * 64,
                size_bytes=3,
                content_type="chemical/x-xyz",
                source="result",
            ),
        )
    )
    _assert_rejected_no_partial(service, tmp_path, _request(manifest), RejectionReason.MISSING_BODY)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest((), manifest_version=999)
    _assert_rejected_no_partial(
        service, tmp_path, _request(manifest), RejectionReason.SCHEMA_VERSION
    )


def test_execution_authority_package_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _hostile_manifest((), execution_authority=True)
    _assert_rejected_no_partial(
        service, tmp_path, _request(manifest), RejectionReason.EXECUTION_AUTHORITY
    )


def test_rejected_staging_is_reconcilable_and_retryable(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "final.xyz").write_text(_XYZ, encoding="utf-8")
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="final.xyz",
                sha256="f" * 64,
                size_bytes=len(_XYZ.encode()),
                content_type="chemical/x-xyz",
                source="result",
            ),
        )
    )
    service = _service(tmp_path)
    request = _request(manifest)
    with pytest.raises(ImportRejectedError):
        service.import_package(request, build_local_import_bundle(package))

    report = service.reconcile()
    assert report.registered == 0
    assert report.rejected_staging == 1
    assert report.orphan_staging == ()

    # A corrected re-import with the same identity succeeds (nothing was committed).
    correct = LocalArchiveSource(package, task_id="task_1")
    fixed = build_archive_manifest(correct, package_class="full", created_at=_CREATED)
    result = service.import_package(_request(fixed), build_local_import_bundle(package))
    assert result.registered is True


# ---------------------------------------------------------------------------
# 4. source mapping + provenance preserved
# ---------------------------------------------------------------------------


def test_source_mapping_and_provenance_are_preserved(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)
    provenance = ExternalProvenance(
        source_namespace="ext",
        source_id="job-9",
        external_status="RUNNING",
        is_active=True,
        metadata={"node": "cluster-a"},
    )
    service = _service(tmp_path)
    result = service.import_package(
        _request(manifest, provenance=provenance), build_local_import_bundle(package)
    )

    record = service.get(result.platform_id)
    assert record is not None
    assert record.mapping.source_namespace == "ext"
    assert record.mapping.source_id == "job-9"
    assert record.mapping.source_revision == "rev-3"
    assert record.mapping.content_identity == record.package_digest
    assert record.mapping.platform_id == record.platform_id

    # External task state is provenance only: recorded, never adopted.
    assert result.provenance is not None and result.provenance.is_active is True
    assert record.provenance is not None and record.provenance.is_active is True
    assert result.adopted_external_authority is False
    assert result.execution_authority is False
    # A Runtime public use case is asked to register a *historical* projection.
    assert result.task_projection is not None
    assert result.task_projection.historical is True
    assert result.task_projection.execution_authority is False


# ---------------------------------------------------------------------------
# 5. zero auto-execution / no unpickling
# ---------------------------------------------------------------------------


def test_import_does_not_execute_bundled_python(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    marker = tmp_path / "EXECUTED"
    evil = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('executed')\n"
    (package / "evil.py").write_text(evil, encoding="utf-8")
    manifest = _hostile_manifest(
        (
            ArchiveFileEntry(
                relative_path="evil.py",
                sha256=hashlib.sha256(evil.encode()).hexdigest(),
                size_bytes=len(evil.encode()),
                content_type="text/plain",
                source="result",
            ),
        )
    )
    service = _service(tmp_path)
    result = service.import_package(_request(manifest), build_local_import_bundle(package))

    assert result.auto_executed is False
    assert not marker.exists(), "import executed bundled Python"
    # The bytes are still imported as inert data (readable, never run).
    assert service.read_file(result.platform_id, "evil.py") == evil.encode()


def _forbidden_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    offenders: list[str] = []
    forbidden_modules = {"pickle", "subprocess", "importlib", "marshal", "shelve", "dill"}
    forbidden_names = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden_modules:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in forbidden_modules:
                offenders.append(f"from {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in forbidden_names:
                offenders.append(f"call {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in (
                "load",
                "loads",
                "system",
                "popen",
            ):
                if isinstance(func.value, ast.Name) and func.value.id == "pickle":
                    offenders.append(f"call pickle.{func.attr}")
    return offenders


def test_import_module_has_no_execution_primitives() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "acp_services" / "data" / "import_.py"
    ).read_text(encoding="utf-8")
    assert _forbidden_calls(source) == [], "import module contains an execution primitive"


# ---------------------------------------------------------------------------
# crash window: DB commit is the atomic commit point
# ---------------------------------------------------------------------------


def test_crash_before_commit_is_not_visible_and_resumes(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)
    service = _service(tmp_path)
    request = _request(manifest)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(service.store, "commit_import", _boom)
    with pytest.raises(RuntimeError):
        service.import_package(request, build_local_import_bundle(package))

    # The commit never happened: nothing is visible, and it is reconcilable.
    assert service.list() == ()
    assert service.get(request.platform_id) is None
    report = service.reconcile()
    assert report.registered == 0
    assert request.idempotency_key in report.orphan_staging

    monkeypatch.undo()
    resumed = service.import_package(request, build_local_import_bundle(package))
    assert resumed.registered is True
    assert resumed.duplicate is False
    assert len(service.list()) == 1
    assert service.reconcile().orphan_staging == ()


def test_read_file_detects_tampering(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_tree(package)
    manifest = _manifest(package)
    service = _service(tmp_path)
    result = service.import_package(_request(manifest), build_local_import_bundle(package))

    published = (
        Path(result.binding.root) / result.binding.relative_path / "RESULT/structures/final.xyz"
    )
    published.write_text("tampered", encoding="utf-8")
    with pytest.raises(ImportIntegrityError):
        service.read_file(result.platform_id, "RESULT/structures/final.xyz")


def test_read_before_commit_is_unavailable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.get("imp_missing") is None
    assert service.list() == ()
