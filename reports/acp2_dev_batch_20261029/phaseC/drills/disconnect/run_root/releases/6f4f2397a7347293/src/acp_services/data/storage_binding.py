"""StorageBinding allocation authority — the single owner of logical storage identity.

B02-2 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 12).  This module
is the **allocator** side of the frozen
:class:`acp_services.contracts.envelope.StorageBinding` carrier (created by
todo 30): Data allocates and freezes the logical binding exactly once, Runtime
only ever consumes the immutable value frozen into a job/attempt.

Design constraints (all binding here):

* **Single allocation authority.**  There is deliberately no second directory
  allocator: :class:`StorageBindingAllocator` derives the identity through an
  injected :class:`StorageAllocationPort`.  The legacy implementation
  (``acp/scheduler/remote/paths.py``: ``storage_relative_path`` /
  ``compose_remote_dir`` / ``resolve_remote_dir`` / the exclusive claim) is
  injected from the legacy side (``acp.services_data_bootstrap``) and never
  imported here — ``src/acp_services/**`` must stay free of the application
  package.
* **Human-readable directories, no path naming change.**  The relative path is
  the project leaf + task leaf (plus any ``__NN`` dedupe suffix) exactly as the
  existing mechanism produces it; the job id / timestamps never enter it.
* **New allocations never land flat.**  A relative path without a project leaf
  would compose to the legacy flat ``<remote_work_dir>/<task_leaf>`` layout;
  :meth:`StorageBindingAllocator.allocate` refuses it
  (:class:`FlatAllocationRefusedError`).  Flat directories are **adopt-only**
  (legacy read-only acceptance), which is why :meth:`StorageBindingAllocator.adopt`
  is a separate, explicitly-named operation.
* **Reuse across attempts.**  The binding is a function of the logical identity
  (tenant/project/endpoint/relative path/mapping version), never of ``attempt``;
  rerun and continue therefore reuse the same binding and directory while
  receipts stay isolated under ``WORK/00_RUNTIME/attempts/<N>/`` by the
  scheduler.
* **Strict conflicts without deletion.**  The exclusive claim surfaces the
  legacy conflict unchanged; the allocator never swallows it and never deletes.

Author: ACP Services (B lane, todo 12).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from acp_services.contracts.envelope import StorageBinding

__all__ = [
    "BINDING_ID_PREFIX",
    "MAPPING_VERSION_CURRENT",
    "MAPPING_VERSION_LEGACY",
    "PATH_SOURCE_DERIVED",
    "PATH_SOURCE_FALLBACK",
    "PATH_SOURCE_LEGACY_FLAT",
    "PATH_SOURCE_STORED",
    "FlatAllocationRefusedError",
    "LegacyResolution",
    "StorageAllocationError",
    "StorageAllocationPort",
    "StorageAllocationRequest",
    "StorageBindingAllocator",
    "binding_id_for",
    "freeze_binding",
]

#: Deterministic prefix for a frozen binding id (parallels ``ss_`` for sources).
BINDING_ID_PREFIX = "sb_"

#: Mapping version of the current (project-leaf/task-leaf) layout.
MAPPING_VERSION_CURRENT = 1
#: Mapping version of an adopted legacy flat directory (pre-v2 layout).
MAPPING_VERSION_LEGACY = 0

# Path-source vocabulary mirroring ``acp/scheduler/remote/paths.py``.  Named here
# so the Services-side allocation record can classify an adoption without naming
# a legacy module.
PATH_SOURCE_STORED = "stored"
PATH_SOURCE_DERIVED = "derived"
PATH_SOURCE_LEGACY_FLAT = "legacy_flat"
PATH_SOURCE_FALLBACK = "fallback"

_LEGACY_PATH_SOURCES = frozenset({PATH_SOURCE_LEGACY_FLAT, PATH_SOURCE_FALLBACK})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StorageAllocationError(RuntimeError):
    """Base error for storage-binding allocation."""


class FlatAllocationRefusedError(StorageAllocationError):
    """A new allocation would land flat (no project leaf) and was refused.

    Flat directories belong to the legacy pre-v2 layout: they may be *adopted*
    read-only, but a fresh allocation must never create one.
    """


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StorageAllocationRequest:
    """The logical identity an allocation is derived from.

    ``work_dir`` is the already-named task directory (the scheduler owns the
    naming; see ANTI #15) and ``run_root`` is the controlled root it lives
    under.  ``endpoint`` names the storage endpoint (remote node); ``""`` means
    the local endpoint.  ``attempt`` is carried for the adoption/claim paths but
    never enters the binding, so rerun/continue reuse the same identity.
    """

    job_id: str
    attempt: int
    work_dir: str
    run_root: str
    project_id: str = ""
    task_leaf: str = ""
    endpoint: str = ""
    tenant_id: str = ""
    mapping_version: int = MAPPING_VERSION_CURRENT

    def __post_init__(self) -> None:
        if not str(self.job_id).strip():
            raise ValueError("job_id must be a non-empty string")
        if not str(self.work_dir).strip():
            raise ValueError("work_dir must be a non-empty string")
        if not str(self.run_root).strip():
            raise ValueError("run_root must be a non-empty string")
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")


@dataclass(frozen=True)
class LegacyResolution:
    """One legacy-directory adoption outcome (never a fresh allocation).

    ``path_source`` is the vocabulary of ``remote/paths.py`` and tells the
    caller how the directory was resolved; ``mapping_version`` is
    :data:`MAPPING_VERSION_LEGACY` for a flat/fallback adoption and
    :data:`MAPPING_VERSION_CURRENT` for a mapped one.
    """

    remote_dir: str
    relative_path: str
    path_source: str
    fallback: bool = False
    mapping_version: int = MAPPING_VERSION_CURRENT

    def is_legacy(self) -> bool:
        """True when the adopted directory is a legacy flat layout."""
        return self.mapping_version < MAPPING_VERSION_CURRENT


# ---------------------------------------------------------------------------
# Injected allocation mechanism
# ---------------------------------------------------------------------------


@runtime_checkable
class StorageAllocationPort(Protocol):
    """The *existing* storage allocation mechanism, injected behind a seam.

    The legacy adapter wraps ``acp/scheduler/remote/paths.py`` and the exclusive
    claim; this Protocol names only the operations the allocator needs.  It is
    deliberately the mechanism (derive/compose/resolve/claim), never the policy
    (fresh-vs-flat refusal lives in the allocator).
    """

    def storage_relative_path(self, *, work_dir: str, run_root: str) -> str:
        """Return ``work_dir`` relative to ``run_root`` (POSIX form)."""
        ...

    def compose_remote_dir(self, relative_path: str, *, endpoint: str) -> str:
        """Join ``relative_path`` under the endpoint's remote work directory."""
        ...

    def resolve_legacy_dir(
        self,
        *,
        job_id: str,
        attempt: int,
        work_dir: str,
        run_root: str,
        endpoint: str,
        task_leaf: str = "",
        explicit: str | None = None,
        persisted_remote_dir: str | None = None,
        persisted_relative: str | None = None,
        probe: Callable[[str], bool] | None = None,
    ) -> LegacyResolution:
        """Resolve an existing (possibly legacy) directory for adoption only."""
        ...

    def claim_dir(
        self,
        *,
        remote_dir: str,
        job_id: str,
        attempt: int,
        work_dir: str,
        endpoint: str,
    ) -> str:
        """Exclusively claim ``remote_dir``; return ``created`` or ``reused``."""
        ...


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def binding_id_for(
    *,
    tenant_id: str,
    project_id: str,
    endpoint: str,
    relative_path: str,
    mapping_version: int,
) -> str:
    """Return the deterministic binding id of one logical storage identity.

    The id is stable across attempts (rerun/continue reuse it) and distinct
    across project leaves, endpoints, paths and mapping versions.
    """
    payload = "\x1f".join(
        [
            str(tenant_id),
            str(project_id),
            str(endpoint),
            str(relative_path),
            str(int(mapping_version)),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{BINDING_ID_PREFIX}{digest}"


def freeze_binding(binding: StorageBinding) -> StorageBinding:
    """Validate + freeze *binding* for Runtime consumption.

    The carrier is already an immutable dataclass; this entry point is the one
    place Runtime (and persistence) may obtain it from, so it enforces the
    minimum invariants a stored binding must satisfy and derives a missing
    :attr:`StorageBinding.binding_id` deterministically.
    """
    if not isinstance(binding, StorageBinding):
        raise StorageAllocationError("freeze_binding requires a StorageBinding")
    relative = binding.relative_path.strip()
    if not relative:
        raise StorageAllocationError("storage binding requires a non-empty relative_path")
    if relative.startswith("/") or "\\" in relative:
        raise StorageAllocationError(f"relative_path must be a POSIX relative path: {relative!r}")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise StorageAllocationError(f"relative_path must be normalised: {relative!r}")
    if int(binding.mapping_version) < 0:
        raise StorageAllocationError("mapping_version must be >= 0")
    if not binding.binding_id.strip():
        binding = replace(
            binding,
            binding_id=binding_id_for(
                tenant_id=binding.tenant_id,
                project_id=binding.project_id,
                endpoint=binding.endpoint,
                relative_path=relative,
                mapping_version=binding.mapping_version,
            ),
        )
    return binding


def _project_leaf(relative_path: str) -> str:
    """Return the leading project leaf of a relative path (``""`` when flat)."""
    head, sep, _tail = relative_path.partition("/")
    return head if sep else ""


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StorageBindingAllocator:
    """The single allocation authority for logical storage identity.

    Wraps an injected :class:`StorageAllocationPort`; owns only the policy
    (new allocations must not land flat, bindings are attempt-independent).
    """

    port: StorageAllocationPort
    default_mapping_version: int = MAPPING_VERSION_CURRENT

    def allocate(self, request: StorageAllocationRequest) -> StorageBinding:
        """Allocate + freeze the binding for a *new* directory.

        Raises:
            FlatAllocationRefusedError: When the derived relative path has no
                project leaf (it would compose to the legacy flat layout).
        """
        relative = str(
            self.port.storage_relative_path(work_dir=request.work_dir, run_root=request.run_root)
        ).strip()
        if not relative or "/" not in relative:
            raise FlatAllocationRefusedError(
                "new allocation would land flat (no project leaf): "
                f"relative_path={relative!r}; legacy flat dirs are adopt-only"
            )
        endpoint = request.endpoint
        if endpoint:
            # Compose once so a broken endpoint/relative combination fails here,
            # at allocation time, instead of at first remote submission.
            self.port.compose_remote_dir(relative, endpoint=endpoint)
        binding = StorageBinding(
            binding_id="",
            tenant_id=request.tenant_id,
            project_id=request.project_id or _project_leaf(relative),
            endpoint=endpoint,
            root=request.run_root,
            relative_path=relative,
            mapping_version=request.mapping_version,
        )
        return freeze_binding(binding)

    def adopt(
        self,
        request: StorageAllocationRequest,
        *,
        explicit: str | None = None,
        persisted_remote_dir: str | None = None,
        persisted_relative: str | None = None,
        probe: Callable[[str], bool] | None = None,
    ) -> StorageBinding:
        """Adopt an existing (possibly legacy) directory read-only.

        This is the **only** path that may consume the legacy chain
        (explicit → persisted ``remote_dir`` → persisted ``remote.relative`` →
        dual-candidate probe → flat fallback).  It never allocates or creates a
        directory; a flat/fallback adoption is tagged legacy via
        :attr:`StorageBinding.mapping_version`.
        """
        resolution = self.port.resolve_legacy_dir(
            job_id=request.job_id,
            attempt=request.attempt,
            work_dir=request.work_dir,
            run_root=request.run_root,
            endpoint=request.endpoint,
            task_leaf=request.task_leaf,
            explicit=explicit,
            persisted_remote_dir=persisted_remote_dir,
            persisted_relative=persisted_relative,
            probe=probe,
        )
        relative = resolution.relative_path.strip()
        binding = StorageBinding(
            binding_id="",
            tenant_id=request.tenant_id,
            project_id=request.project_id or _project_leaf(relative),
            endpoint=request.endpoint,
            root=request.run_root,
            relative_path=relative,
            mapping_version=resolution.mapping_version,
        )
        return freeze_binding(binding)

    def claim(self, request: StorageAllocationRequest, binding: StorageBinding) -> str:
        """Exclusively claim the binding's remote directory.

        Returns the claim disposition (``"created"`` / ``"reused"``).  A
        foreign or marker-less directory raises the injected mechanism's
        conflict error unchanged — the directory is never deleted.
        """
        if not binding.endpoint:
            raise StorageAllocationError("claim requires a remote endpoint")
        remote_dir = self.port.compose_remote_dir(binding.relative_path, endpoint=binding.endpoint)
        return self.port.claim_dir(
            remote_dir=remote_dir,
            job_id=request.job_id,
            attempt=request.attempt,
            work_dir=request.work_dir,
            endpoint=request.endpoint,
        )

    def remote_dir(self, binding: StorageBinding) -> str:
        """Return the composed storage directory for a bound remote endpoint."""
        if not binding.endpoint:
            raise StorageAllocationError("remote_dir requires a remote endpoint")
        return self.port.compose_remote_dir(binding.relative_path, endpoint=binding.endpoint)
