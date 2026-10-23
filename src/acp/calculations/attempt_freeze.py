"""ACP-side singlepoint freeze spec + immutable local code release (07-A / todo 25).

This module is the ACP-to-Services bridge for the default worker chain:

* :func:`build_singlepoint_freeze_spec` builds the *same* typed
  :class:`cccp.calculation.requests.TaskRequest` the singlepoint workflow
  executes (through the existing ``CalculationRequest`` →
  ``legacy_adapters.to_task_request`` path and the existing
  ``workflows.simple`` request builder), derives the ACP scientific plan/step
  identities with :func:`acp.calculations.identity.compute_identity`, computes
  the deterministic submission id, and returns a typed
  :class:`acp_services.runtime.attempt_freeze.AttemptFreezeSpec` for the
  Services-side group writer (task 24).
* :func:`build_local_release` materialises a verified immutable copy of the
  execution tree under ``<run_root>/releases/<release_id>/`` and hashes every
  file.  ``release_id`` stays the compatibility 16-hex content id; the full
  :attr:`LocalRelease.release_digest` covers the complete file map.
* :func:`local_release_identity` is the pure, remote-free identity view used
  by the freeze builder (importing it never needs the ``[remote]`` extra).

The module deliberately does **not** activate anything: it defines no
default-envelope workflow set, hooks no ``JobRunner``, and every ordinary
workflow keeps its registered entry until task 29.  Task 25 only builds and
tests the freeze/release module behind a controlled fixture.

Path contract
-------------
The frozen task request is work-dir-relative: ``structure.path`` is
``input.xyz`` and ``output_dir`` is ``.``.  The worker runs with
``cwd=<task work dir>``; every task artifact is resolved inside it.  Code is
served from the immutable release via ``PYTHONPATH=<release>/src`` so the live
checkout is never the executed code.

Import direction
----------------
``acp`` may import ``acp_services``; this module imports the Services freeze
writer/contracts one-way and never the reverse.  No jobs DB is touched.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from acp.calculations.contracts import (
    CalculationRequest,
    StepKind,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.identity import compute_identity
from acp.calculations.legacy_adapters import (
    pes2ts_orca_gradient_to_task_request,
    to_task_request,
)
from acp.core.config import load_config
from acp.workflows.simple import (
    _AdapterContext,
    _build_plan,
    _build_request,
    _context,
    _RequestDefinition,
)
from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    ScientificClosure,
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime.attempt_freeze import (
    AttemptFreezeSpec,
    write_frozen_attempt,
)
from acp_services.runtime.execution_chain import (
    FrozenGroup,
    frozen_group_dir,
    load_frozen_group,
)
from acp_services.runtime.worker import (
    FrozenPlan,
    FrozenPlanIdentity,
    FrozenStep,
    frozen_plan_body_digest,
)
from acp_services.worker_bootstrap import (
    load_frozen_envelope,
    load_frozen_plan,
    load_storage_binding,
)
from cccp.analysis.identity import identity_fingerprint
from cccp.calculation.requests import TaskKind, TaskRequest

__all__ = [
    "COMPLETE_MARKER",
    "DEFAULT_RELEASE_SCHEMA",
    "RELEASE_MANIFEST_FILENAME",
    "UNVERSIONED_RELEASE_ID",
    "AttemptFreezeConflictError",
    "FreezeSpecError",
    "LocalRelease",
    "ReleaseIdentity",
    "ReleaseIdentityError",
    "StaleReleaseError",
    "FREEZE_BUILDERS",
    "build_freeze_spec",
    "build_local_release",
    "build_singlepoint_freeze_spec",
    "freeze_singlepoint_attempt",
    "local_release_identity",
]

# ── release identity ───────────────────────────────────────────────────────

DEFAULT_RELEASE_SCHEMA: Final = 1
RELEASE_MANIFEST_FILENAME: Final = "release_manifest.json"
COMPLETE_MARKER: Final = ".complete"
UNVERSIONED_RELEASE_ID: Final = "unversioned-shared"

_RELEASE_DIRNAME: Final = "releases"
_RELEASE_STAGING_DIRNAME: Final = ".staging"

#: Directories never copied into a release (same exclusions as the remote sync
#: set plus ``src/acp_services``, which the remote sync list adds in task 30).
_EXCLUDED_DIRS: Final[frozenset[str]] = frozenset(
    {"__pycache__", ".git", ".mypy_cache", ".ruff_cache"}
)
_EXCLUDED_SUFFIXES: Final[tuple[str, ...]] = (".pyc", ".pyo")
_ACP_EXCLUDED_SUBDIRS: Final[frozenset[str]] = frozenset({"api", "scheduler"})

#: Marker written by py_compile/pytest inside a release; never a source member.
_IGNORED_RELEASE_SUFFIXES: Final[tuple[str, ...]] = (".pyc", ".pyo")

#: Packages that make up the execution tree.  In a source checkout they live
#: under ``<project_root>/src``; an installed distribution places them under
#: ``site-packages``, where the installed package roots are authoritative.
_EXECUTION_PACKAGES: Final[tuple[str, ...]] = ("acp", "acp_services", "cccp")


class ReleaseIdentityError(RuntimeError):
    """The local code release cannot be identified, verified or materialised."""


class StaleReleaseError(ReleaseIdentityError):
    """A release no longer matches the live source tree it was built from."""


class FreezeSpecError(ValueError):
    """A singlepoint freeze spec cannot be built from the given record/input."""


class AttemptFreezeConflictError(RuntimeError):
    """An existing frozen group belongs to a different attempt (archive T32)."""


@dataclass(frozen=True)
class ReleaseIdentity:
    """Content identity of the local execution tree.

    ``release_id`` is the existing 16-hex compatibility id
    (``sha256(canonical files map)[:16]``); ``release_digest`` is the full
    64-hex canonical digest over schema + release id + every file digest, so
    two trees that merely share an id prefix can never share a release claim.
    """

    project_root: Path
    release_id: str
    release_digest: str
    files: Mapping[str, Mapping[str, str | int]]

    @property
    def file_digests(self) -> dict[str, str]:
        return {rel: str(meta["sha256"]) for rel, meta in self.files.items()}

    def assert_current(self, project_root: Path | str | None = None) -> None:
        """Raise :class:`StaleReleaseError` when the live tree changed."""
        root = Path(project_root) if project_root is not None else self.project_root
        current = local_release_identity(root)
        if current.release_digest != self.release_digest:
            raise StaleReleaseError(
                "local release is stale: live source digest "
                f"{current.release_digest} != frozen release digest {self.release_digest}; "
                "build a new release instead of reusing this one"
            )


@dataclass(frozen=True)
class LocalRelease:
    """A verified immutable release directory (or explicit unversioned dev)."""

    identity: ReleaseIdentity
    root: Path
    unversioned: bool = False

    @property
    def project_root(self) -> Path:
        return self.identity.project_root

    @property
    def release_id(self) -> str:
        return self.identity.release_id

    @property
    def release_digest(self) -> str:
        return self.identity.release_digest

    @property
    def files(self) -> Mapping[str, Mapping[str, str | int]]:
        return self.identity.files

    @property
    def worker_pythonpath(self) -> Path:
        """``PYTHONPATH`` root that makes the release the executed code."""
        src = self.root / "src"
        return src if src.is_dir() else self.root

    def assert_current(self) -> None:
        if self.unversioned:
            return
        self.identity.assert_current(self.project_root)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _walk_files(root: Path, *, exclude_dirs: frozenset[str] = _EXCLUDED_DIRS) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in exclude_dirs)
        for name in sorted(filenames):
            if name.endswith(_EXCLUDED_SUFFIXES):
                continue
            files.append(Path(dirpath) / name)
    return files


def _release_source_files(project_root: Path) -> list[Path]:
    """The immutable execution tree: ACP (no api/scheduler), Services, CCCP."""
    files: list[Path] = []
    acp_root = project_root / "src" / "acp"
    if acp_root.is_dir():
        files.extend(
            _walk_files(
                acp_root,
                exclude_dirs=_EXCLUDED_DIRS | _ACP_EXCLUDED_SUBDIRS,
            )
        )
    services_root = project_root / "src" / "acp_services"
    if services_root.is_dir():
        files.extend(_walk_files(services_root))
    cccp_root = project_root / "src" / "cccp"
    if cccp_root.is_dir():
        files.extend(_walk_files(cccp_root))
    extras = (
        project_root / "requirements-node.txt",
        project_root / "config" / "defaults.yaml",
    )
    for extra in extras:
        if extra.is_file():
            files.append(extra)
    return sorted(files)


def _installed_package_root(name: str) -> Path | None:
    """Return the installed package directory for *name*, or ``None``."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None:
        return None
    for location in spec.submodule_search_locations or ():
        candidate = Path(location)
        if candidate.is_dir():
            return candidate.resolve()
    origin = spec.origin
    if origin and origin not in ("built-in", "frozen"):
        candidate = Path(origin)
        if candidate.is_file():
            return candidate.parent.resolve()
    return None


def _installed_release_sources() -> dict[str, Path]:
    """Map stable package-relative keys (``acp/...``) to installed files."""
    sources: dict[str, Path] = {}
    missing: list[str] = []
    for name in _EXECUTION_PACKAGES:
        package_root = _installed_package_root(name)
        if package_root is None:
            missing.append(name)
            continue
        exclude = _EXCLUDED_DIRS | (_ACP_EXCLUDED_SUBDIRS if name == "acp" else frozenset())
        for path in _walk_files(package_root, exclude_dirs=exclude):
            key = f"{name}/{path.relative_to(package_root).as_posix()}"
            sources[key] = path
    if missing:
        raise ReleaseIdentityError(
            "missing installed package(s) "
            + ", ".join(missing)
            + "; cannot identify the installed execution tree"
        )
    return sources


def _release_sources(project_root: Path) -> dict[str, Path]:
    """Return ``relative key -> on-disk file`` for a source or installed tree."""
    if any((project_root / "src" / name).is_dir() for name in _EXECUTION_PACKAGES):
        return {
            path.relative_to(project_root).as_posix(): path
            for path in _release_source_files(project_root)
        }
    return _installed_release_sources()


def _files_map(sources: Mapping[str, Path]) -> dict[str, dict[str, str | int]]:
    return {
        rel: {"sha256": digest, "size": size}
        for rel, (digest, size) in ((rel, _sha256_file(path)) for rel, path in sources.items())
    }


def _release_digest(files: Mapping[str, Mapping[str, str | int]], release_id: str) -> str:
    payload = {
        "schema": DEFAULT_RELEASE_SCHEMA,
        "release_id": release_id,
        "files": {rel: dict(meta) for rel, meta in files.items()},
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _identity_from_sources(project_root: Path, sources: Mapping[str, Path]) -> ReleaseIdentity:
    files = _files_map(sources)
    if not files:
        raise ReleaseIdentityError(
            f"no execution-tree files found under {project_root} "
            "(expected src/acp, src/acp_services, src/cccp or installed "
            "acp, acp_services, cccp packages)"
        )
    release_id = hashlib.sha256(_canonical_json(files).encode("utf-8")).hexdigest()[:16]
    return ReleaseIdentity(
        project_root=project_root,
        release_id=release_id,
        release_digest=_release_digest(files, release_id),
        files=files,
    )


def local_release_identity(project_root: Path | str) -> ReleaseIdentity:
    """Compute the content identity of *project_root* without any remote import.

    This is the single release-identity authority for the local freeze path.
    A source checkout hashes ``<root>/src/{acp,acp_services,cccp}``; an
    installed distribution (no ``<root>/src``) hashes the installed package
    roots under stable ``acp/...``, ``acp_services/...``, ``cccp/...`` keys.
    It is deliberately pure (stdlib only): importing it never pulls
    ``acp.scheduler.remote`` (and therefore never requires the ``[remote]``
    extra / paramiko).
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ReleaseIdentityError(f"project root is not a directory: {root}")
    return _identity_from_sources(root, _release_sources(root))


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _copy_verified(source: Path, target: Path, expected: Mapping[str, str | int]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as src, target.open("wb") as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    actual = digest.hexdigest()
    if actual != expected["sha256"] or size != int(expected["size"]):
        raise ReleaseIdentityError(
            f"release copy of {source} differs from the live tree "
            f"(sha256 {actual}, size {size}); refusing a mutable release"
        )


def _read_release_manifest(release_dir: Path) -> dict[str, Any]:
    path = release_dir / RELEASE_MANIFEST_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseIdentityError(f"release manifest unreadable at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseIdentityError(f"release manifest at {path} is not a mapping")
    return payload


def _verify_existing_release(release_dir: Path, identity: ReleaseIdentity) -> LocalRelease:
    marker = release_dir / COMPLETE_MARKER
    if not marker.is_file():
        raise ReleaseIdentityError(
            f"release directory {release_dir} exists without {COMPLETE_MARKER}; refusing adoption"
        )
    manifest = _read_release_manifest(release_dir)
    if manifest.get("schema") != DEFAULT_RELEASE_SCHEMA:
        raise ReleaseIdentityError(
            f"release manifest {release_dir} schema {manifest.get('schema')!r} != "
            f"{DEFAULT_RELEASE_SCHEMA}"
        )
    if manifest.get("release_id") != identity.release_id:
        raise ReleaseIdentityError(
            f"release dir {release_dir} id {manifest.get('release_id')!r} != expected "
            f"{identity.release_id!r}"
        )
    if manifest.get("release_digest") != identity.release_digest:
        raise ReleaseIdentityError(
            f"release dir {release_dir} digest {manifest.get('release_digest')!r} != expected "
            f"{identity.release_digest!r}"
        )
    raw_files = manifest.get("files")
    if not isinstance(raw_files, Mapping):
        raise ReleaseIdentityError(f"release manifest {release_dir} has no files map")
    if {str(rel) for rel in raw_files} != set(identity.files):
        raise ReleaseIdentityError(
            f"release manifest {release_dir} member set differs from the frozen identity"
        )
    for rel, expected in identity.files.items():
        try:
            actual_digest, actual_size = _sha256_file(release_dir / rel)
        except OSError as exc:
            raise ReleaseIdentityError(f"release {release_dir} is missing {rel}: {exc}") from exc
        if actual_digest != expected["sha256"] or actual_size != int(expected["size"]):
            raise ReleaseIdentityError(
                f"release {release_dir} member {rel!r} no longer matches its manifest"
            )
    complete = marker.read_text(encoding="utf-8").strip()
    if complete != identity.release_digest:
        raise ReleaseIdentityError(
            f"release {release_dir} completion marker {complete!r} != {identity.release_digest!r}"
        )
    return LocalRelease(identity=identity, root=release_dir)


def _unversioned_allowed() -> bool:
    return os.environ.get("ACP_ALLOW_UNVERSIONED") == "1"


def _unversioned_release(project_root: Path) -> LocalRelease:
    identity = ReleaseIdentity(
        project_root=project_root,
        release_id=UNVERSIONED_RELEASE_ID,
        release_digest="",
        files={},
    )
    return LocalRelease(identity=identity, root=project_root, unversioned=True)


def build_local_release(project_root: Path | str, run_root: Path | str) -> LocalRelease:
    """Materialise a verified immutable copy under ``<run_root>/releases/<id>``.

    Publication is stage-then-rename: every file is copied and hash-checked,
    the manifest is written, the ``.complete`` marker is written last inside
    the staging directory, and the whole directory is renamed atomically.
    An existing directory is adopted only after re-verifying every manifest
    member; a mismatch is a typed refusal (never overwrite a release).

    On copy/staging failure the operation fails closed.  With an explicit
    ``ACP_ALLOW_UNVERSIONED=1`` the caller gets an ``unversioned-shared``
    declaration (never a frozen-version claim) and the live checkout remains
    the executed tree.
    """
    root = Path(project_root).resolve()
    destination_root = Path(run_root).resolve() / _RELEASE_DIRNAME
    try:
        sources = _release_sources(root)
        identity = _identity_from_sources(root, sources)
    except ReleaseIdentityError:
        if _unversioned_allowed():
            return _unversioned_release(root)
        raise

    final = destination_root / identity.release_id
    if final.exists():
        return _verify_existing_release(final, identity)

    staging_root = destination_root / _RELEASE_STAGING_DIRNAME
    staging = staging_root / f"{identity.release_id}.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        staging.mkdir(parents=True)
        for rel, expected in identity.files.items():
            _copy_verified(sources[rel], staging / rel, expected)
        manifest = {
            "schema": DEFAULT_RELEASE_SCHEMA,
            "release_id": identity.release_id,
            "release_digest": identity.release_digest,
            "files": {rel: dict(meta) for rel, meta in identity.files.items()},
        }
        _write_json(staging / RELEASE_MANIFEST_FILENAME, manifest)
        (staging / COMPLETE_MARKER).write_text(identity.release_digest + "\n", encoding="utf-8")
        _fsync_dir(staging)
        destination_root.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(staging, final)
        except OSError:
            # A concurrent builder won the rename; verify its publication.
            if final.is_dir():
                shutil.rmtree(staging, ignore_errors=True)
                return _verify_existing_release(final, identity)
            raise
        _fsync_dir(destination_root)
    except (OSError, ReleaseIdentityError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if _unversioned_allowed():
            return _unversioned_release(root)
        raise ReleaseIdentityError(
            f"cannot create immutable release under {destination_root}: {exc}"
        ) from exc
    return LocalRelease(identity=identity, root=final)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── singlepoint freeze spec ────────────────────────────────────────────────

_SIMPLE_METHOD_KEYS: Final[tuple[str, ...]] = (
    "basis",
    "dispersion",
    "solvent_model",
    "solvent",
    "aux_j_basis",
    "aux_c_basis",
    "ri_approximation",
    "grid",
    "scf_convergence",
    "geom_maxiter",
    "opt_convergence",
    "max_steps",
    "opt_level",
    "gfn",
)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _record_config(record: Any) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    resources = record.spec.resources if isinstance(record.spec.resources, Mapping) else {}
    nproc = resources.get("nproc")
    mem = resources.get("mem")
    if nproc is not None:
        overrides.setdefault("resources", {})["nproc"] = nproc
        overrides.setdefault("executables", {}).setdefault("orca", {})["nproc"] = nproc
    if mem is not None:
        overrides.setdefault("resources", {})["mem"] = mem
    return load_config(
        config_path=record.spec.config_path,
        overrides=overrides or None,
    )


def _singlepoint_method_kwargs(method: Mapping[str, Any]) -> dict[str, Any]:
    """Map the persisted ``JobSpec.method`` levels onto simple-workflow kwargs.

    Mirrors the CLI ``_build_simple_method_kwargs`` surface without
    re-implementing the science request builder: the resulting kwargs are
    handed to the existing ``workflows.simple._build_request``.
    """
    kwargs: dict[str, Any] = {}
    levels = method.get("levels")
    if isinstance(levels, Mapping):
        for level in levels.values():
            if not isinstance(level, Mapping) or level.get("_disabled"):
                continue
            functional = level.get("functional")
            if functional:
                kwargs["method"] = str(functional)
            for key in _SIMPLE_METHOD_KEYS:
                value = level.get(key)
                if value is not None and value != "":
                    kwargs[key] = value
            electronic_state = level.get("electronic_state")
            if isinstance(electronic_state, Mapping):
                kwargs["electronic_state"] = dict(electronic_state)
    direct_method = method.get("method")
    if direct_method:
        kwargs["method"] = str(direct_method)
    extras = method.get("route_extras")
    if extras:
        kwargs["route_extras"] = extras
    return kwargs


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _materialize_input(work_dir: Path, input_path: Path | str | None) -> Path:
    """Freeze the input as ``<work_dir>/input.xyz`` (byte-identical)."""
    target = work_dir / "input.xyz"
    if input_path is None:
        if not target.is_file():
            raise FreezeSpecError(f"singlepoint freeze requires an input file; expected {target}")
        return target
    source = Path(input_path)
    if not source.is_file():
        raise FreezeSpecError(f"singlepoint input file not found: {source}")
    if source.resolve() == target.resolve():
        return target
    data = source.read_bytes()
    if not target.is_file() or target.read_bytes() != data:
        _write_bytes_atomic(target, data)
    return target


def _assert_relative_inside(path_value: Any, work_dir: Path, label: str) -> None:
    if not isinstance(path_value, str) or not path_value:
        return
    candidate = Path(path_value)
    if candidate.is_absolute():
        raise FreezeSpecError(f"{label} must be work-dir-relative, got absolute {path_value!r}")
    resolved = (work_dir / candidate).resolve()
    if not resolved.is_relative_to(work_dir):
        raise FreezeSpecError(f"{label} escapes the task work dir: {path_value!r}")


def _assert_task_request_paths(payload: Mapping[str, Any], work_dir: Path) -> None:
    for key, value in payload.items():
        if key == "output_dir" or key == "path" or key.endswith("_path"):
            _assert_relative_inside(value, work_dir, key)
        elif isinstance(value, Mapping):
            _assert_task_request_paths(value, work_dir)


def _local_binding(work_dir: Path, release: LocalRelease, input_file: Path) -> StorageBinding:
    return StorageBinding(
        binding_id=f"local:{release.release_id}:{work_dir.name}",
        kind="local",
        endpoint="local",
        root=str(work_dir.parent),
        relative_path=work_dir.name,
        artifact_digests={"input.xyz": f"sha256:{_sha256_bytes(input_file.read_bytes())}"},
    )


def _owner_token(attempt: int) -> str:
    return f"local:{socket.gethostname()}:{os.getpid()}:{attempt}"


def _build_envelope(
    record: Any,
    plan: FrozenPlan,
    binding: StorageBinding | None,
    release: LocalRelease,
    *,
    task_kind: str = TaskKind.SINGLEPOINT.value,
) -> ExecutionEnvelope:
    identity = plan.identity
    if identity is None:  # pragma: no cover - built immediately above
        raise FreezeSpecError("frozen plan carries no identity")
    release_id = UNVERSIONED_RELEASE_ID if release.unversioned else release.release_id
    release_digest = "" if release.unversioned else release.release_digest
    return ExecutionEnvelope(
        envelope_version=1,
        job_id=str(record.id),
        attempt=int(record.attempt),
        submission_id=expected_submission_id(str(record.id), int(record.attempt)),
        release_id=release_id,
        release_digest=release_digest,
        owner=_owner_token(int(record.attempt)),
        fencing_token=secrets.token_hex(16),
        plan_digest=identity.plan_digest,
        plan_identity=identity.plan_identity,
        step_identities=identity.step_identities,
        protocol="local",
        entrypoint="worker.bootstrap",
        required_capabilities=(task_kind,),
        data_bindings=(binding,) if binding is not None else (),
        scientific_closure=ScientificClosure(task_kind=task_kind),
    )


def _existing_spec(
    work_dir: Path, attempt: int, *, workflow: str | None = None
) -> AttemptFreezeSpec | None:
    if not frozen_group_dir(work_dir).exists():
        return None
    group = load_frozen_group(work_dir)
    if workflow is not None and group.manifest.workflow != workflow:
        raise AttemptFreezeConflictError(
            f"existing frozen group under {work_dir} is for workflow "
            f"{group.manifest.workflow!r}, not {workflow!r}"
        )
    if group.manifest.attempt != attempt:
        raise AttemptFreezeConflictError(
            f"existing frozen group under {work_dir} belongs to attempt "
            f"{group.manifest.attempt}, current attempt is {attempt}; archive/re-freeze per "
            "the same-attempt protocol (task 32) instead of overwriting it"
        )
    envelope = load_frozen_envelope(group.envelope_path)
    plan = load_frozen_plan(group.plan_path)
    binding = load_storage_binding(group.binding_path) if group.binding_path is not None else None
    return AttemptFreezeSpec(
        workflow=group.manifest.workflow,
        envelope=envelope,
        plan=plan,
        binding=binding,
    )


def _build_singlepoint_request(
    record: Any,
    work_dir: Path,
    input_file: Path,
) -> tuple[_AdapterContext, CalculationRequest, TaskRequest]:
    """Backward-compatible singlepoint entry over the generic builder."""
    return _build_simple_request(
        record,
        work_dir,
        input_file,
        workflow="singlepoint",
        step_kind=StepKind.SINGLEPOINT,
        backend="orca",
        task_kind=TaskKind.SINGLEPOINT,
    )


_SIMPLE_WORKFLOW_TASKS: Final[dict[str, tuple[StepKind, str, TaskKind]]] = {
    "singlepoint": (StepKind.SINGLEPOINT, "orca", TaskKind.SINGLEPOINT),
    "optimize": (StepKind.OPTIMIZE, "orca", TaskKind.OPTIMIZE),
    "frequency": (StepKind.FREQUENCY, "orca", TaskKind.FREQUENCY),
    "casscf": (StepKind.CASSCF, "orca", TaskKind.CASSCF),
    "xtb_optimize": (StepKind.OPTIMIZE, "xtb", TaskKind.OPTIMIZE),
}


def _build_simple_request(
    record: Any,
    work_dir: Path,
    input_file: Path,
    *,
    workflow: str,
    step_kind: StepKind,
    backend: str,
    task_kind: TaskKind,
) -> tuple[_AdapterContext, CalculationRequest, TaskRequest]:
    """Build the same simple-workflow request through the existing builder."""
    config = _record_config(record)
    input_payload = record.spec.input if isinstance(record.spec.input, Mapping) else {}
    context = _context(
        str(input_file),
        config,
        _optional_int(input_payload.get("charge")),
        _optional_int(input_payload.get("multiplicity")),
        record.spec.name or None,
    )
    method_kwargs = _singlepoint_method_kwargs(
        record.spec.method if isinstance(record.spec.method, Mapping) else {}
    )
    request = _build_request(
        context,
        _RequestDefinition(workflow, backend, method_kwargs),
    )
    # Freeze the request paths relative to the worker CWD (the task work dir).
    request = replace(
        request,
        input_artifact=replace(context.artifact, path=Path("input.xyz")),
        resources={**request.resources, "output_dir": "."},
    )
    task_request, _legacy_binding = to_task_request(request, task_kind)
    if task_request.structure is None or task_request.structure.path != Path("input.xyz"):
        raise FreezeSpecError(
            f"the {workflow} request did not freeze a work-dir-relative structure path"
        )
    if task_request.output_dir != Path("."):
        raise FreezeSpecError(
            f"the {workflow} request did not freeze '.' as output_dir: {task_request.output_dir!r}"
        )
    _assert_task_request_paths(task_request.to_dict(), work_dir)
    return context, request, task_request


def build_simple_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
    *,
    workflow: str,
) -> AttemptFreezeSpec:
    """Build one simple-workflow freeze spec through the existing builders."""
    try:
        step_kind, backend, task_kind = _SIMPLE_WORKFLOW_TASKS[workflow]
    except KeyError as exc:
        raise FreezeSpecError(f"no simple freeze builder for workflow {workflow!r}") from exc
    if record.spec.workflow != workflow:
        raise FreezeSpecError(f"{workflow} freeze builder cannot build {record.spec.workflow!r}")
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    existing = _existing_spec(work, int(record.attempt), workflow=workflow)
    if existing is not None:
        return existing
    if release is None:
        raise FreezeSpecError("an immutable LocalRelease is required for a new freeze")
    release.assert_current()

    input_file = _materialize_input(work, input_path)
    context, request, task_request = _build_simple_request(
        record,
        work,
        input_file,
        workflow=workflow,
        step_kind=step_kind,
        backend=backend,
        task_kind=task_kind,
    )
    calculation_plan = _build_plan(step_kind, [request], [step_kind])
    scientific = compute_identity(calculation_plan, location_root=work)
    step_payload = task_request.to_dict()
    body = FrozenPlan(
        payload=step_payload,
        steps=(FrozenStep(kind=step_kind.value, payload=step_payload),),
        item_symbols=tuple(context.symbols),
    )
    plan = replace(
        body,
        identity=FrozenPlanIdentity(
            plan_digest=frozen_plan_body_digest(body),
            plan_identity=scientific.plan_identity,
            step_identities=scientific.step_identities,
        ),
    )
    binding = _local_binding(work, release, input_file)
    envelope = _build_envelope(
        record,
        plan,
        binding,
        release,
        task_kind=task_request.task.value,
    )
    return AttemptFreezeSpec(
        workflow=workflow,
        envelope=envelope,
        plan=plan,
        binding=binding,
    )


def build_singlepoint_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> AttemptFreezeSpec:
    """Build the frozen singlepoint spec for one ``(job, attempt)``."""
    return build_simple_freeze_spec(
        record,
        work_dir,
        input_path,
        release,
        workflow="singlepoint",
    )


def _plan_from_task_request(
    task_request: TaskRequest,
    *,
    step_kind: str,
    item_symbols: tuple[str, ...],
    input_file: Path | None,
) -> FrozenPlan:
    """Frozen plan whose identity is bound to the task request + input bytes.

    Used by the non-``compute_identity`` workflows (IRC / OrcaGradient) where
    the executor consumes a standalone typed request rather than a
    CalculationPlan.  The body digest remains the T24 tamper anchor; the
    declared plan/step identity uses the shared content fingerprint.
    """
    payload = task_request.to_dict()
    body = FrozenPlan(
        payload=payload,
        steps=(FrozenStep(kind=step_kind, payload=payload),),
        item_symbols=item_symbols,
    )
    input_digest = _sha256_bytes(input_file.read_bytes()) if input_file is not None else ""
    plan_identity = identity_fingerprint(
        {
            "scope": "attempt-freeze",
            "task": payload,
            "item_symbols": list(item_symbols),
            "input_sha256": input_digest,
        }
    )
    step_identity = identity_fingerprint(
        {
            "scope": "attempt-freeze-step",
            "index": 0,
            "step_kind": step_kind,
            "task": payload,
            "item_symbols": list(item_symbols),
            "input_sha256": input_digest,
        }
    )
    return replace(
        body,
        identity=FrozenPlanIdentity(
            plan_digest=frozen_plan_body_digest(body),
            plan_identity=plan_identity,
            step_identities=(step_identity,),
        ),
    )


def build_scan_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> AttemptFreezeSpec:
    """Build a frozen scan spec through the existing scan request adapter."""
    if record.spec.workflow != "scan":
        raise FreezeSpecError(f"scan freeze builder cannot build {record.spec.workflow!r}")
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    existing = _existing_spec(work, int(record.attempt), workflow="scan")
    if existing is not None:
        return existing
    if release is None:
        raise FreezeSpecError("an immutable LocalRelease is required for a new freeze")
    release.assert_current()
    input_file = _materialize_input(work, input_path)
    config = _record_config(record)
    input_payload = record.spec.input if isinstance(record.spec.input, Mapping) else {}
    context = _context(
        str(input_file),
        config,
        _optional_int(input_payload.get("charge")),
        _optional_int(input_payload.get("multiplicity")),
        record.spec.name or None,
    )
    method_kwargs = _singlepoint_method_kwargs(
        record.spec.method if isinstance(record.spec.method, Mapping) else {}
    )
    method = str(method_kwargs.pop("method", "") or "r2SCAN-3c")
    resources: dict[str, Any] = {
        **method_kwargs,
        "backend": "orca",
        "config": config,
        "output_dir": ".",
        "result_dir": ".",
    }
    coordinate = input_payload.get("scan_coordinates") or input_payload.get("coordinate")
    if coordinate is not None:
        resources["scan_coordinates"] = coordinate
    for key in ("scan_points", "use_scants"):
        value = input_payload.get(key)
        if value is not None:
            resources[key] = value
    request = CalculationRequest(
        input_artifact=replace(context.artifact, path=Path("input.xyz")),
        method=method,
        resources=resources,
        workflow="scan",
        profile="default",
    )
    task_request, _binding = to_task_request(request, TaskKind.SCAN)
    _assert_task_request_paths(task_request.to_dict(), work)
    plan = _plan_from_task_request(
        task_request,
        step_kind=StepKind.SCAN.value,
        item_symbols=tuple(context.symbols),
        input_file=input_file,
    )
    binding = _local_binding(work, release, input_file)
    envelope = _build_envelope(record, plan, binding, release, task_kind=task_request.task.value)
    return AttemptFreezeSpec(workflow="scan", envelope=envelope, plan=plan, binding=binding)


def build_irc_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> AttemptFreezeSpec:
    """Build a frozen IRC spec through the existing typed request adapter."""
    if record.spec.workflow != "irc":
        raise FreezeSpecError(f"irc freeze builder cannot build {record.spec.workflow!r}")
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    existing = _existing_spec(work, int(record.attempt), workflow="irc")
    if existing is not None:
        return existing
    if release is None:
        raise FreezeSpecError("an immutable LocalRelease is required for a new freeze")
    release.assert_current()
    input_file = _materialize_input(work, input_path)
    config = _record_config(record)
    input_payload = record.spec.input if isinstance(record.spec.input, Mapping) else {}
    method_payload = record.spec.method if isinstance(record.spec.method, Mapping) else {}
    context = _context(
        str(input_file),
        config,
        _optional_int(input_payload.get("charge")),
        _optional_int(input_payload.get("multiplicity")),
        record.spec.name or None,
    )
    method_kwargs = _singlepoint_method_kwargs(method_payload)
    method = str(method_kwargs.pop("method", "") or "r2SCAN-3c")
    basis = str(method_kwargs.pop("basis", "") or "")
    raw_directions = input_payload.get("directions") or method_payload.get("direction")
    if isinstance(raw_directions, str):
        if raw_directions == "both":
            directions = ("forward", "reverse")
        else:
            directions = (raw_directions,)
    elif isinstance(raw_directions, (list, tuple)) and raw_directions:
        directions = tuple(str(value) for value in raw_directions)
    else:
        directions = ("forward", "reverse")
    resources: dict[str, Any] = {
        **method_kwargs,
        "backend": "orca",
        "config": config,
        "output_dir": ".",
        "result_dir": ".",
        "basis": basis,
        "max_iter": _optional_int(input_payload.get("maxpoints")) or 100,
        "step": float(input_payload.get("step") or 0.1),
    }
    request = CalculationRequest(
        input_artifact=StructureArtifact(
            path=Path("input.xyz"),
            elements=list(context.symbols),
            role=StructureRole.TRANSITION_STATE,
            source="irc",
        ),
        method=method,
        resources=resources,
        workflow="irc",
        profile="default",
    )
    task_request, _binding = to_task_request(request, TaskKind.IRC, directions=directions)
    _assert_task_request_paths(task_request.to_dict(), work)
    plan = _plan_from_task_request(
        task_request,
        step_kind="irc",
        item_symbols=tuple(context.symbols),
        input_file=input_file,
    )
    binding = _local_binding(work, release, input_file)
    envelope = _build_envelope(record, plan, binding, release, task_kind=task_request.task.value)
    return AttemptFreezeSpec(workflow="irc", envelope=envelope, plan=plan, binding=binding)


def build_orca_gradient_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> AttemptFreezeSpec:
    """Build a frozen OrcaGradient spec from its existing frozen payload."""
    if record.spec.workflow != "OrcaGradient":
        raise FreezeSpecError(f"OrcaGradient freeze builder cannot build {record.spec.workflow!r}")
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    existing = _existing_spec(work, int(record.attempt), workflow="OrcaGradient")
    if existing is not None:
        return existing
    if release is None:
        raise FreezeSpecError("an immutable LocalRelease is required for a new freeze")
    release.assert_current()
    input_payload = record.spec.input if isinstance(record.spec.input, Mapping) else {}
    raw = (
        input_payload.get("gradient_request")
        or input_payload.get("orca_gradient_request")
        or input_payload
    )
    if not isinstance(raw, Mapping):
        raise FreezeSpecError("OrcaGradient freeze requires a gradient_request mapping")
    from acp.workflows.orca_gradient import _validate_gradient_request

    validated = _validate_gradient_request(raw)
    task_request, _binding = pes2ts_orca_gradient_to_task_request(validated)
    task_request = replace(task_request, output_dir=Path("."))
    _assert_task_request_paths(task_request.to_dict(), work)
    plan = _plan_from_task_request(
        task_request,
        step_kind="orca_gradient",
        item_symbols=tuple(validated.symbols),
        input_file=None,
    )
    envelope = _build_envelope(record, plan, None, release, task_kind=task_request.task.value)
    return AttemptFreezeSpec(workflow="OrcaGradient", envelope=envelope, plan=plan, binding=None)


def build_freeze_spec(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> AttemptFreezeSpec:
    """Dispatch to the ONE registered builder for ``record.spec.workflow``."""
    workflow = str(record.spec.workflow)
    builder = FREEZE_BUILDERS.get(workflow)
    if builder is None:
        raise FreezeSpecError(f"no freeze builder registered for workflow {workflow!r}")
    return builder(record, work_dir, input_path, release)


def freeze_singlepoint_attempt(
    record: Any,
    work_dir: Path | str,
    input_path: Path | str | None,
    release: LocalRelease,
) -> FrozenGroup:
    """Build + publish one singlepoint group through the task-24 writer."""
    spec = build_singlepoint_freeze_spec(record, work_dir, input_path, release)
    return write_frozen_attempt(work_dir, spec)


#: The single builder registry used by the activation hook (no scattered branches).
FREEZE_BUILDERS: Final[dict[str, Any]] = {
    "singlepoint": build_singlepoint_freeze_spec,
    "optimize": lambda record, work, inp, rel: build_simple_freeze_spec(
        record, work, inp, rel, workflow="optimize"
    ),
    "frequency": lambda record, work, inp, rel: build_simple_freeze_spec(
        record, work, inp, rel, workflow="frequency"
    ),
    "casscf": lambda record, work, inp, rel: build_simple_freeze_spec(
        record, work, inp, rel, workflow="casscf"
    ),
    "xtb_optimize": lambda record, work, inp, rel: build_simple_freeze_spec(
        record, work, inp, rel, workflow="xtb_optimize"
    ),
    "scan": build_scan_freeze_spec,
    "irc": build_irc_freeze_spec,
    "OrcaGradient": build_orca_gradient_freeze_spec,
}
