"""Canonical ``structure_viewer_v1`` contract — thin policy + entry-id helpers.

This module produces the per-job structure catalog consumed by the frontend
structure-viewer tab.  It is a **thin policy layer**: it selects a source and
delegates to existing authoritative readers (``acp.results.manifest``,
``acp.confsearch.manifest``, ``acp.results.frame_candidate_geometry``,
``acp.compat.legacy.manifests``).  It must **never** re-implement workflow
parsing or duplicate ``StructureSourceService``.

Design doc reference: ``docs/ACP_Structure_Viewer_Modification_Plan.md`` §4.1.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "StructureViewerEntry",
    "StructureViewerGroup",
    "StructureViewerPayload",
    "StructureViewerError",
    "build_structure_viewer_payload",
    "confsearch_entry_id",
    "pes_entry_id",
    "batch_entry_id",
    "simple_entry_id",
    "scan_entry_id",
    "irc_entry_id",
    "manual_entry_id",
    "legacy_entry_id",
    "resolve_collision",
]

logger = logging.getLogger(__name__)

# ── Schema version ──────────────────────────────────────────────────────────

_SCHEMA_VERSION = "structure_viewer_v1"

# ── Primary source files for revision computation (fixed order) ─────────────
# Each tuple is (relative_path, filename_for_hash).
_REVISION_SOURCES: list[tuple[str, str]] = [
    ("RESULT/result_manifest.json", "result_manifest.json"),
    ("RESULT/confsearch/confsearch_manifest.json", "confsearch_manifest.json"),
    ("RESULT/pes_search/pes_profile.json", "pes_profile.json"),
    ("RESULT/pes_search/pes_recommendations.json", "pes_recommendations.json"),
    ("RESULT/pes_search/pes_review.json", "pes_review.json"),
]


# ── Exceptions ──────────────────────────────────────────────────────────────


class StructureViewerError(Exception):
    """Raised when the structure-viewer payload cannot be built."""


# ── Frozen dataclasses ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StructureViewerGeometry:
    """Geometry reference for an entry.

    Attributes:
        endpoint: Relative API suffix (e.g.
            ``/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry``).
        format: Geometry format string (default ``"xyz"``).
    """

    endpoint: str = ""
    format: str = "xyz"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {"endpoint": self.endpoint, "format": self.format}


@dataclass(frozen=True, slots=True)
class StructureViewerEnergy:
    """Energy information for an entry.

    Attributes:
        value: Energy value in the given unit.
        unit: Energy unit (e.g. ``"hartree"``).
        kind: Energy kind (``"electronic"``, ``"gibbs"``, ``"enthalpy"``).
        temperature_k: Temperature for Gibbs/enthalpy, if applicable.
    """

    value: float | None = None
    unit: str = "hartree"
    kind: str = "electronic"
    temperature_k: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {"value": self.value, "unit": self.unit, "kind": self.kind}
        if self.temperature_k is not None:
            d["temperature_k"] = self.temperature_k
        return d


@dataclass(frozen=True, slots=True)
class StructureViewerSource:
    """Source provenance for an entry.

    Attributes:
        kind: One of ``formal_result``, ``algorithm_recommendation``,
            ``manual_review``, ``last_valid_cycle``, ``calculation_input``,
            ``manual_file``, ``confsearch_manifest``.
        product_id: Result-manifest product id, if applicable.
        frame_index: Trajectory frame index, if applicable.
        geometry_ref: Relative path to the geometry file on disk.
    """

    kind: str = ""
    product_id: str | None = None
    frame_index: int | None = None
    geometry_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {"kind": self.kind}
        if self.product_id is not None:
            d["product_id"] = self.product_id
        if self.frame_index is not None:
            d["frame_index"] = self.frame_index
        if self.geometry_ref is not None:
            d["geometry_ref"] = self.geometry_ref
        return d


@dataclass(frozen=True, slots=True)
class StructureViewerVibrations:
    """Vibration availability for an entry.

    Attributes:
        available: Whether vibration data is available.
        endpoint: Relative API suffix for vibration data, if available.
    """

    available: bool = False
    endpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {"available": self.available, "endpoint": self.endpoint}


@dataclass(frozen=True, slots=True)
class StructureViewerEntry:
    """One structure entry in the viewer catalog.

    Attributes:
        id: Stable deterministic entry id.
        group_id: Id of the parent group.
        label: Human-readable label.
        role: Structural role (``"minimum"``, ``"ts"``, ``"endpoint"``, etc.).
        status: Entry status (``"completed"``, ``"failed"``, etc.).
        geometry: Geometry reference.
        energy: Energy information.
        relative_energy_kcal: Energy relative to the group minimum (kcal/mol).
        boltzmann_weight: Boltzmann weight (0–1), if computable.
        source: Source provenance.
        badges: List of display badges.
        vibrations: Vibration availability.
    """

    id: str
    group_id: str = ""
    label: str = ""
    role: str = ""
    status: str = "completed"
    geometry: StructureViewerGeometry = field(default_factory=StructureViewerGeometry)
    energy: StructureViewerEnergy = field(default_factory=StructureViewerEnergy)
    relative_energy_kcal: float | None = None
    boltzmann_weight: float | None = None
    source: StructureViewerSource = field(default_factory=StructureViewerSource)
    badges: tuple[str, ...] = ()
    vibrations: StructureViewerVibrations = field(default_factory=StructureViewerVibrations)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the doc §4.1 entry JSON shape."""
        return {
            "id": self.id,
            "group_id": self.group_id,
            "label": self.label,
            "role": self.role,
            "status": self.status,
            "geometry": self.geometry.to_dict(),
            "energy": self.energy.to_dict(),
            "relative_energy_kcal": self.relative_energy_kcal,
            "boltzmann_weight": self.boltzmann_weight,
            "source": self.source.to_dict(),
            "badges": list(self.badges),
            "vibrations": self.vibrations.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class StructureViewerGroup:
    """A logical grouping of entries (e.g. "final_conformers").

    Attributes:
        id: Group identifier.
        label: Human-readable label.
        kind: Group kind (``"ensemble"``, ``"scan"``, etc.).
    """

    id: str
    label: str = ""
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {"id": self.id, "label": self.label, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class StructureViewerPayload:
    """Top-level structure-viewer payload (doc §4.1).

    Attributes:
        schema_version: Always ``"structure_viewer_v1"``.
        job_id: The job identifier.
        workflow: The workflow name.
        job_status: The job status at payload build time.
        revision: Content fingerprint for change detection.
        default_entry_id: The entry id to select by default.
        groups: Ordered list of entry groups.
        entries: Ordered list of structure entries.
        warnings: Accumulated non-fatal warnings.
    """

    schema_version: str = _SCHEMA_VERSION
    job_id: str = ""
    workflow: str = ""
    job_status: str = ""
    revision: str = "empty"
    default_entry_id: str | None = None
    groups: tuple[StructureViewerGroup, ...] = ()
    entries: tuple[StructureViewerEntry, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the doc §4.1 JSON shape."""
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "workflow": self.workflow,
            "job_status": self.job_status,
            "revision": self.revision,
            "default_entry_id": self.default_entry_id,
            "groups": [g.to_dict() for g in self.groups],
            "entries": [e.to_dict() for e in self.entries],
            "warnings": list(self.warnings),
        }


# ── Entry-id helpers ────────────────────────────────────────────────────────
# Pure functions; todos 2-6 import and reuse these.


def confsearch_entry_id(
    conformer_id: str | None = None,
    rank: int | None = None,
) -> str:
    """Build a Confsearch entry id.

    Prefers ``conformer_id``; falls back to ``conf_rank_<rank>``.

    Args:
        conformer_id: Conformer identifier from the manifest (e.g. ``"0001"``).
        rank: Numeric rank (1-based) when conformer_id is unavailable.

    Returns:
        Deterministic entry id string.
    """
    if conformer_id is not None:
        return f"conf_{conformer_id}"
    if rank is not None:
        return f"conf_rank_{rank}"
    return "conf_unknown"


def pes_entry_id(candidate_id: str) -> str:
    """Build a PES entry id from a candidate id.

    Args:
        candidate_id: PES candidate identifier.

    Returns:
        Deterministic entry id string.
    """
    return f"pes_{candidate_id}"


def batch_entry_id(item_id: str) -> str:
    """Build a BatchOptimize entry id from an item id.

    Args:
        item_id: Batch item identifier.

    Returns:
        Deterministic entry id string.
    """
    return f"batch_{item_id}"


def simple_entry_id(step_kind: str) -> str:
    """Build a simple-workflow entry id from a step kind.

    Args:
        step_kind: Calculation step kind (e.g. ``"optimize"``, ``"singlepoint"``).

    Returns:
        Deterministic entry id string.
    """
    return f"simple_{step_kind}"


def scan_entry_id(frame_index: int) -> str:
    """Build a scan entry id from a frame index.

    Args:
        frame_index: 0-based scan frame index.

    Returns:
        Deterministic entry id string.
    """
    return f"scan_frame_{frame_index}"


def irc_entry_id(endpoint: str, frame_index: int) -> str:
    """Build an IRC entry id from endpoint direction and frame index.

    Args:
        endpoint: IRC endpoint direction (``"forward"`` or ``"reverse"``).
        frame_index: 0-based frame index.

    Returns:
        Deterministic entry id string.
    """
    return f"irc_{endpoint}_{frame_index}"


def manual_entry_id(relpath: str) -> str:
    """Build a manual-file entry id from a relative path.

    The hash is deterministic and collision-resistant.

    Args:
        relpath: Relative file path.

    Returns:
        ``manual_<sha256(relpath)[:12]>``
    """
    digest = hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:12]
    return f"manual_{digest}"


def legacy_entry_id(relpath: str) -> str:
    """Build a legacy-fallback entry id from a relative path.

    Args:
        relpath: Relative file path.

    Returns:
        ``legacy_<sha256(relpath)[:12]>``
    """
    digest = hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:12]
    return f"legacy_{digest}"


def resolve_collision(entry_id: str, geometry_ref: str) -> str:
    """Append a geometry-based suffix to resolve an identity collision.

    When two entries would share the same id, the caller invokes this with
    the geometry reference to produce a unique variant.

    Args:
        entry_id: The base entry id (e.g. ``"conf_0001"``).
        geometry_ref: Geometry file reference (e.g. ``"conformers/conf_0001.xyz"``).

    Returns:
        ``entry_id`` when no collision, or
        ``entry_id_<sha256(geometry_ref)[:6]>`` on collision.
    """
    suffix = hashlib.sha256(geometry_ref.encode("utf-8")).hexdigest()[:6]
    return f"{entry_id}_{suffix}"


# ── Revision computation ────────────────────────────────────────────────────


def _compute_revision(task_root: Path, job_status: str) -> str:
    """Compute the content fingerprint over primary source files.

    SHA-256 over concatenation where each existing source file contributes
    ``filename.encode() + file_bytes`` in fixed order, then
    ``job_status.encode()``.  Returns ``"empty"`` when no sources exist.

    Args:
        task_root: Job working directory.
        job_status: Current job status string.

    Returns:
        16-character hex digest or ``"empty"``.
    """
    hasher = hashlib.sha256()
    found_any = False

    for rel_path, filename in _REVISION_SOURCES:
        full_path = task_root / rel_path
        if full_path.is_file():
            try:
                data = full_path.read_bytes()
            except OSError as exc:
                logger.debug("revision: cannot read %s: %s", full_path, exc)
                continue
            hasher.update(filename.encode("utf-8"))
            hasher.update(data)
            found_any = True

    if not found_any:
        return "empty"

    hasher.update(job_status.encode("utf-8"))
    return hasher.hexdigest()[:16]


# ── Workflow dispatch table ─────────────────────────────────────────────────
# Type alias for resolver functions.
# Each resolver receives (task_root, job_id, warnings) and returns
# (groups, entries, default_entry_id).
_ResolverResult = tuple[
    list[StructureViewerGroup],
    list[StructureViewerEntry],
    str | None,
]
_Resolver = Any  # Callable[[Path, str, list[str]], _ResolverResult]


def _resolve_confsearch(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """Confsearch resolver — placeholder for todo 2."""
    warnings.append("Confsearch resolver not yet implemented")
    return [], [], None


def _resolve_pessearch(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """PES resolver — placeholder for todo 3."""
    warnings.append("PESsearch resolver not yet implemented")
    return [], [], None


def _resolve_batchoptimize(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """BatchOptimize resolver — placeholder for todo 4."""
    warnings.append("BatchOptimize resolver not yet implemented")
    return [], [], None


def _resolve_simple(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """Simple workflow resolver — placeholder for todo 5."""
    warnings.append("Simple workflow resolver not yet implemented")
    return [], [], None


def _resolve_scan(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """Scan resolver — placeholder for todo 5."""
    warnings.append("Scan resolver not yet implemented")
    return [], [], None


def _resolve_irc(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """IRC resolver — placeholder for todo 6."""
    warnings.append("IRC resolver not yet implemented")
    return [], [], None


def _resolve_legacy(task_root: Path, job_id: str, warnings: list[str]) -> _ResolverResult:
    """Legacy fallback resolver — placeholder for todo 6."""
    warnings.append("Legacy resolver not yet implemented")
    return [], [], None


# Dispatch table: workflow string → resolver function.
# Todos 2-6 will replace the placeholder resolvers.
_DISPATCH_TABLE: dict[str, _Resolver] = {
    "Confsearch": _resolve_confsearch,
    "PESsearch": _resolve_pessearch,
    "BatchOptimize": _resolve_batchoptimize,
    "optimize": _resolve_simple,
    "singlepoint": _resolve_simple,
    "frequency": _resolve_simple,
    "xtb-optimize": _resolve_simple,
    "scan": _resolve_scan,
    "irc": _resolve_irc,
}


# ── Public API ──────────────────────────────────────────────────────────────


def _probe_result_manifest(task_root: Path, warnings: list[str]) -> None:
    """Try reading ``RESULT/result_manifest.json``; append warnings on failure."""
    manifest_path = task_root / "RESULT" / "result_manifest.json"
    if not manifest_path.is_file():
        return
    try:
        json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        warnings.append(f"Corrupt result_manifest.json: {exc}")
        logger.debug("corrupt result manifest at %s: %s", manifest_path, exc)


def build_structure_viewer_payload(
    task_root: Path | str,
    *,
    job_id: str,
    workflow: str,
    job_status: str,
    item_id: str | None = None,
) -> StructureViewerPayload:
    """Build a ``structure_viewer_v1`` payload for a completed or failed job.

    This is the **thin policy** entry point.  It computes the revision,
    dispatches to the appropriate workflow resolver, and assembles the
    canonical payload.  It must **never** raise on missing or corrupt
    manifests — errors are collected as warnings.

    Args:
        task_root: Job working directory (must not be embedded in disk paths).
        job_id: Job identifier (used only in API endpoint strings).
        workflow: Workflow name (dispatch key).
        job_status: Job status at build time (incorporated into revision).
        item_id: Optional BatchOptimize item filter.

    Returns:
        A valid ``StructureViewerPayload`` (never raises on manifest errors).
    """
    root = Path(task_root).expanduser().resolve()
    warnings: list[str] = []

    if not root.is_dir():
        warnings.append(f"Task root does not exist: {root}")
        return StructureViewerPayload(
            schema_version=_SCHEMA_VERSION,
            job_id=job_id,
            workflow=workflow,
            job_status=job_status,
            revision="empty",
            default_entry_id=None,
            groups=(),
            entries=(),
            warnings=tuple(warnings),
        )

    _probe_result_manifest(root, warnings)

    revision = _compute_revision(root, job_status)

    resolver = _DISPATCH_TABLE.get(workflow, _resolve_legacy)
    try:
        groups_raw, entries_raw, default_id = resolver(root, job_id, warnings)
    except Exception as exc:
        logger.warning("resolver for %s failed: %s", workflow, exc)
        warnings.append(f"Resolver for {workflow} failed: {exc}")
        groups_raw, entries_raw, default_id = [], [], None

    # Build typed tuples
    groups = tuple(groups_raw)
    entries = tuple(entries_raw)

    return StructureViewerPayload(
        schema_version=_SCHEMA_VERSION,
        job_id=job_id,
        workflow=workflow,
        job_status=job_status,
        revision=revision,
        default_entry_id=default_id,
        groups=groups,
        entries=entries,
        warnings=tuple(warnings),
    )
