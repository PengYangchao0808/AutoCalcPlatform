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
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "StructureViewerEntry",
    "StructureViewerGroup",
    "StructureViewerPayload",
    "StructureViewerError",
    "build_structure_viewer_payload",
    "make_manual_entry",
    "compute_overlay",
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

_DEFAULT_TEMPERATURE_K = 298.15
_R_KCAL_PER_MOL_K = 1.987204259e-3
_HARTREE_TO_KCAL = 627.5094740631


def _number(value: Any) -> float | None:
    """Return a finite float or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# ── Display-label normalization ─────────────────────────────────────────────

_FORMAL_RESULT_FALLBACK_LABEL = "计算结果"
_OPTIMIZE_RESULT_LABEL = "优化结果"
_TS_OPTIMIZE_RESULT_LABEL = "TS 优化结果"
_SIMPLE_RESULT_LABELS: dict[str, str] = {
    "singlepoint": "单点能计算结构",
    "frequency": "频率计算结构",
}
_OPTIMIZE_WORKFLOW_KEYS = frozenset(
    {"batch", "batchoptimize", "optimize", "xtb-optimize"}
)
_INPUT_LABEL_RE = re.compile(r"^input\b", re.IGNORECASE)
_TS_LABEL_RE = re.compile(r"\bTS\b", re.IGNORECASE)


def _normalize_display_label(
    label: str,
    source_kind: str,
    workflow: str | None = None,
) -> str:
    """Normalize a formal-result display label at projection time.

    The BatchOptimize engine labels CLI ``--items-file`` products as
    ``"{item.name} ({tag}, {profile})"`` where ``item.name`` defaults to
    ``"input"`` — hence historical ``"input (TS, opt_freq)"`` labels in
    ``result_manifest.json``.  The structure viewer must never surface that
    raw ``input`` prefix as the main title of a *formal result*, so it is
    replaced with a workflow-appropriate label here.  Stored data is never
    rewritten.

    Rules:
        * ``source_kind != "formal_result"`` → label unchanged (input
          structures keep their original name).
        * Label not starting with ``input`` → unchanged.
        * Optimize-family workflows → ``"TS 优化结果"`` when the label
          carries a TS tag, else ``"优化结果"``.
        * Simple ``singlepoint``/``frequency`` → dedicated Chinese labels.
        * Anything else → ``"计算结果"``.

    Args:
        label: Raw product label from the result manifest.
        source_kind: Entry source kind (``"formal_result"``,
            ``"calculation_input"``, ...).
        workflow: Optional workflow hint (``"batch"``, ``"optimize"``,
            ``"singlepoint"``, ``"frequency"``, ...).

    Returns:
        The normalized label, or ``label`` unchanged.
    """
    if source_kind != "formal_result" or not label:
        return label
    if not _INPUT_LABEL_RE.match(label):
        return label
    workflow_key = (workflow or "").lower()
    if workflow_key in _OPTIMIZE_WORKFLOW_KEYS:
        if _TS_LABEL_RE.search(label):
            return _TS_OPTIMIZE_RESULT_LABEL
        return _OPTIMIZE_RESULT_LABEL
    simple_label = _SIMPLE_RESULT_LABELS.get(workflow_key)
    if simple_label is not None:
        return simple_label
    return _FORMAL_RESULT_FALLBACK_LABEL


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
    confirmed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {"kind": self.kind}
        if self.product_id is not None:
            d["product_id"] = self.product_id
        if self.frame_index is not None:
            d["frame_index"] = self.frame_index
        if self.geometry_ref is not None:
            d["geometry_ref"] = self.geometry_ref
        if self.confirmed is not None:
            d["confirmed"] = self.confirmed
        return d


@dataclass(frozen=True, slots=True)
class StructureViewerVibrations:
    """Vibration availability for an entry.

    Attributes:
        available: Whether vibration data is available.
        endpoint: Relative API suffix for vibration data, if available.
        imaginary_count: Number of imaginary modes (``freq < 0``), or ``None``
            when unavailable or not yet probed.
        source: Data provenance (``"product"`` or ``"historical_projection"``),
            or ``None`` when unavailable.
    """

    available: bool = False
    endpoint: str | None = None
    imaginary_count: int | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        return {
            "available": self.available,
            "endpoint": self.endpoint,
            "imaginary_count": self.imaginary_count,
            "source": self.source,
        }


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


def make_manual_entry(
    *,
    job_id: str,
    relpath: str,
    label: str,
) -> StructureViewerEntry:
    """Build a ``manual_file`` entry for API-layer injection.

    This is a **pure helper** (no disk I/O) called by the API layer
    (todo 17) after ``build_structure_viewer_payload`` to inject
    manually-selected files into the payload entries.

    Args:
        job_id: Job identifier (for geometry endpoint construction).
        relpath: Relative path to the geometry file.
        label: Human-readable label.

    Returns:
        A ``StructureViewerEntry`` with ``source.kind="manual_file"``
        and ``vibrations.available=False``.
    """
    entry_id = manual_entry_id(relpath)
    return StructureViewerEntry(
        id=entry_id,
        group_id="",
        label=label,
        role="minimum",
        status="completed",
        geometry=StructureViewerGeometry(
            endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
            format="xyz",
        ),
        source=StructureViewerSource(
            kind="manual_file",
            geometry_ref=relpath,
        ),
        vibrations=StructureViewerVibrations(available=False),
    )


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
# Each resolver receives (task_root, workflow, job_id, warnings, item_id) and
# returns (groups, entries, default_entry_id).
_ResolverResult = tuple[
    list[StructureViewerGroup],
    list[StructureViewerEntry],
    str | None,
]
_Resolver = Any  # Callable[[Path, str, str, list[str], str | None], _ResolverResult]


def _compute_boltzmann_weights(
    energies: list[float | None],
    temperature_k: float,
) -> list[float | None]:
    """Compute Boltzmann weights from a list of energies.

    Args:
        energies: Energy values (hartree). ``None`` entries get ``None`` weight.
        temperature_k: Temperature in Kelvin.

    Returns:
        List of Boltzmann weights summing to 1.0, or ``None`` per missing entry.
    """
    valid = [(i, e) for i, e in enumerate(energies) if e is not None]
    if not valid:
        return [None] * len(energies)

    e_min = min(e for _, e in valid)
    rt = _R_KCAL_PER_MOL_K * temperature_k
    raw: list[tuple[int, float]] = []
    for i, e in valid:
        delta_hartree = e - e_min
        delta_kcal = delta_hartree * _HARTREE_TO_KCAL
        raw.append((i, math.exp(-delta_kcal / rt)))

    total = sum(w for _, w in raw)
    if total <= 0:
        return [None] * len(energies)

    result: list[float | None] = [None] * len(energies)
    for i, w in raw:
        result[i] = w / total
    return result


def _resolve_confsearch(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve Confsearch manifest → structure viewer entries.

    Reads ``RESULT/confsearch/confsearch_manifest.json`` via the authoritative
    ``acp.confsearch.manifest`` readers.  Produces one entry per conformer in
    manifest order, with rank-1 as the default selection.
    """
    from acp.confsearch.manifest import find_confsearch_manifest, read_manifest

    manifest_path = find_confsearch_manifest(task_root)
    if manifest_path is None:
        warnings.append("No confsearch manifest found")
        return [], [], None

    try:
        payload = read_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        warnings.append(f"Cannot read confsearch manifest: {exc}")
        return [], [], None

    conformers_raw = payload.get("conformers")
    if not isinstance(conformers_raw, list) or not conformers_raw:
        warnings.append("Confsearch manifest has no conformers")
        return [], [], None

    temperature_k = _number(payload.get("temperature_k")) or _DEFAULT_TEMPERATURE_K

    groups = [StructureViewerGroup(id="final_conformers", label="最终构象", kind="ensemble")]

    # Skip malformed (non-dict) conformer rows BEFORE parsing so that
    # entries and energies_for_weights stay 1:1 aligned by construction —
    # a skipped row must never shift weights onto the wrong conformer.
    valid_conformers = [c for c in conformers_raw if isinstance(c, dict)]
    dropped = len(conformers_raw) - len(valid_conformers)
    if dropped:
        warnings.append(f"Skipped {dropped} malformed conformer entries")

    parsed: list[dict[str, Any]] = []
    for conformer in valid_conformers:
        has_gibbs = conformer.get("free_energy_hartree") is not None
        energy_val = _number(
            conformer.get("free_energy_hartree") if has_gibbs
            else conformer.get("energy_hartree")
        )
        parsed.append({
            "conformer": conformer,
            "has_gibbs": has_gibbs,
            "energy_val": energy_val,
        })

    any_rel_missing = any(
        p.get("conformer", {}).get("relative_energy_kcal") is None
        and p.get("energy_val") is not None
        for p in parsed
        if p
    )
    min_energy: float | None = None
    if any_rel_missing:
        valid_energies = [p["energy_val"] for p in parsed if p.get("energy_val") is not None]
        if valid_energies:
            min_energy = min(valid_energies)

    entries: list[StructureViewerEntry] = []
    seen_ids: set[str] = set()
    rank1_entry_id: str | None = None

    for item in parsed:
        if not item:
            continue
        conformer = item["conformer"]
        has_gibbs: bool = item["has_gibbs"]
        energy_value: float | None = item["energy_val"]

        conf_id = str(conformer.get("conf_id") or "")
        rank_raw = conformer.get("rank")
        rank = int(rank_raw) if isinstance(rank_raw, (int, float)) and rank_raw > 0 else None

        entry_id = confsearch_entry_id(conformer_id=conf_id or None, rank=rank)
        if entry_id in seen_ids:
            geometry_ref = str(conformer.get("geometry") or "")
            entry_id = resolve_collision(entry_id, geometry_ref)
        seen_ids.add(entry_id)

        energy_kind = "gibbs" if has_gibbs else "electronic"

        relative_kcal = _number(conformer.get("relative_energy_kcal"))
        if relative_kcal is None and energy_value is not None and min_energy is not None:
            relative_kcal = (energy_value - min_energy) * _HARTREE_TO_KCAL

        weight = _number(conformer.get("boltzmann_weight"))

        badges: list[str] = []
        if rank is not None:
            badges.append(f"rank-{rank}")
            if rank == 1:
                badges.append("selected")
                rank1_entry_id = entry_id

        geometry_ref = str(conformer.get("geometry") or "")
        full_geometry_ref = f"RESULT/confsearch/{geometry_ref}" if geometry_ref else None

        entries.append(StructureViewerEntry(
            id=entry_id,
            group_id="final_conformers",
            label=f"构象 {conf_id}" if conf_id else f"构象 rank-{rank}",
            role="minimum",
            status="completed",
            geometry=StructureViewerGeometry(
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                format="xyz",
            ),
            energy=StructureViewerEnergy(
                value=energy_value,
                unit="hartree",
                kind=energy_kind,
                temperature_k=temperature_k if has_gibbs else None,
            ),
            relative_energy_kcal=relative_kcal,
            boltzmann_weight=weight,
            source=StructureViewerSource(
                kind="formal_result",
                geometry_ref=full_geometry_ref,
            ),
            badges=tuple(badges),
            vibrations=StructureViewerVibrations(available=False),
        ))

    if not entries:
        warnings.append("No valid conformer entries in confsearch manifest")
        return [], [], None

    any_weight_missing = any(e.boltzmann_weight is None for e in entries)
    if any_weight_missing:
        warnings.append("Boltzmann weights missing from manifest; computed from energies")
        energies_for_weights: list[float | None] = [
            p.get("energy_val") if p else None for p in parsed
        ]
        computed_weights = _compute_boltzmann_weights(energies_for_weights, temperature_k)
        rebuilt: list[StructureViewerEntry] = []
        for entry, cw in zip(entries, computed_weights, strict=True):
            if entry.boltzmann_weight is None and cw is not None:
                rebuilt.append(StructureViewerEntry(
                    id=entry.id,
                    group_id=entry.group_id,
                    label=entry.label,
                    role=entry.role,
                    status=entry.status,
                    geometry=entry.geometry,
                    energy=entry.energy,
                    relative_energy_kcal=entry.relative_energy_kcal,
                    boltzmann_weight=cw,
                    source=entry.source,
                    badges=entry.badges,
                    vibrations=entry.vibrations,
                ))
            else:
                rebuilt.append(entry)
        entries = rebuilt

    if rank1_entry_id is None and entries:
        rank1_entry_id = entries[0].id

    return groups, entries, rank1_entry_id


def _read_pes_json(task_root: Path, relative: str) -> dict[str, Any] | None:
    path = task_root / relative
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("cannot read %s: %s", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


_CONFIDENCE_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


def _resolve_pessearch(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve PESsearch recommendations + review → structure viewer entries."""
    recs_payload = _read_pes_json(task_root, "RESULT/pes_search/pes_recommendations.json")
    review_payload = _read_pes_json(task_root, "RESULT/pes_search/pes_review.json")

    if recs_payload is None and review_payload is None:
        warnings.append("No PES search results found")
        return [], [], None

    groups: list[StructureViewerGroup] = []
    entries: list[StructureViewerEntry] = []
    seen_ids: set[str] = set()
    default_id: str | None = None

    scan_dir = "WORK/07_PATH/pes_scan_001"
    if recs_payload is not None:
        scan_dir = str(recs_payload.get("scan_dir") or scan_dir)

    if review_payload is not None:
        groups.append(StructureViewerGroup(
            id="pes_confirmed", label="人工确认", kind="confirmed"
        ))
        selected = review_payload.get("selected") or []
        for entry_data in selected:
            if not isinstance(entry_data, dict):
                continue
            candidate_id = str(entry_data.get("candidate_id") or "")
            if not candidate_id:
                continue

            entry_id = pes_entry_id(candidate_id)
            if entry_id in seen_ids:
                entry_id = resolve_collision(entry_id, candidate_id)
            seen_ids.add(entry_id)

            frame_index_raw = entry_data.get("frame_index")
            frame_index = int(frame_index_raw) if frame_index_raw is not None else None
            role_raw = str(entry_data.get("role") or "").upper()
            role = "ts" if role_raw == "TS" else "endpoint"
            structure_path = str(entry_data.get("structure_path") or "")
            geometry_ref = structure_path if structure_path else None
            name = str(entry_data.get("name") or candidate_id)

            entries.append(StructureViewerEntry(
                id=entry_id,
                group_id="pes_confirmed",
                label=name,
                role=role,
                status="completed",
                geometry=StructureViewerGeometry(
                    endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                    format="xyz",
                ),
                source=StructureViewerSource(
                    kind="manual_review",
                    frame_index=frame_index,
                    geometry_ref=geometry_ref,
                    confirmed=True,
                ),
                vibrations=StructureViewerVibrations(available=False),
            ))

            if default_id is None:
                default_id = entry_id

    if recs_payload is not None:
        groups.append(StructureViewerGroup(
            id="pes_recommendations", label="自动推荐", kind="recommendations"
        ))
        ts_list = recs_payload.get("ts") or []
        int_list = recs_payload.get("intermediates") or []
        all_recs = list(ts_list) + list(int_list)

        best_ts: dict[str, Any] | None = None
        best_peak: dict[str, Any] | None = None

        for rec in all_recs:
            if not isinstance(rec, dict):
                continue
            candidate_id = str(rec.get("candidate_id") or "")
            if not candidate_id:
                continue

            entry_id = pes_entry_id(candidate_id)
            if entry_id in seen_ids:
                entry_id = resolve_collision(entry_id, candidate_id)
            seen_ids.add(entry_id)

            kind = str(rec.get("kind") or "")
            confidence = str(rec.get("confidence") or "low")
            score = _number(rec.get("score"))
            frame_index_raw = rec.get("frame_index")
            frame_index = int(frame_index_raw) if frame_index_raw is not None else None
            geometry_path = str(rec.get("geometry_path") or "")

            role = "ts" if kind == "ts" else "endpoint"
            geometry_ref = f"{scan_dir}/{geometry_path}" if geometry_path else None

            badges: list[str] = ["未确认"]

            entries.append(StructureViewerEntry(
                id=entry_id,
                group_id="pes_recommendations",
                label=candidate_id,
                role=role,
                status="completed",
                geometry=StructureViewerGeometry(
                    endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                    format="xyz",
                ),
                energy=StructureViewerEnergy(value=score, unit="score", kind="score"),
                source=StructureViewerSource(
                    kind="algorithm_recommendation",
                    frame_index=frame_index,
                    geometry_ref=geometry_ref,
                    confirmed=False,
                ),
                badges=tuple(badges),
                vibrations=StructureViewerVibrations(available=False),
            ))

            if kind == "ts":
                if best_ts is None or _CONFIDENCE_ORDER.get(confidence, 9) < _CONFIDENCE_ORDER.get(str(best_ts.get("confidence") or "low"), 9):
                    best_ts = rec
                    best_ts["_entry_id"] = entry_id
            else:
                if best_peak is None or (score or 0) > (_number(best_peak.get("score")) or 0):
                    best_peak = rec
                    best_peak["_entry_id"] = entry_id

        if default_id is None:
            if best_ts is not None:
                default_id = best_ts.get("_entry_id")
            elif best_peak is not None:
                default_id = best_peak.get("_entry_id")

    if not entries:
        warnings.append("No PES entries found in recommendations or review")

    return groups, entries, default_id


def _resolve_batchoptimize(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve BatchOptimize result_manifest products + failed-item trajectories."""
    import re as _re

    from acp.results.manifest import find_products, load_result_manifest

    manifest = load_result_manifest(task_root)
    if manifest is None:
        warnings.append("No result manifest found for BatchOptimize")
        return [], [], None

    structure_products = find_products(manifest, "structure")
    batch_products = [p for p in structure_products if p.id.startswith("batch_")]

    if item_id is not None:
        target_id = f"batch_{item_id}"
        matched = [p for p in batch_products if p.id == target_id]
        if not matched:
            traj_path = task_root / "WORK" / "03_OPT" / "batch" / item_id / "optimize" / "optimization_trajectory.json"
            if not traj_path.is_file():
                raise StructureViewerError(f"Unknown batch item: {item_id}")
        batch_products = matched

    groups = [StructureViewerGroup(id="batch_items", label="批量优化", kind="batch")]
    entries: list[StructureViewerEntry] = []
    completed_item_ids: set[str] = set()
    default_id: str | None = None

    tag_re = _re.compile(r"__TAG_(TS|INT)__", _re.IGNORECASE)

    for product in batch_products:
        raw_item_id = product.id.removeprefix("batch_")
        completed_item_ids.add(raw_item_id)

        tag_match = tag_re.search(product.path)
        tag = tag_match.group(1).upper() if tag_match else "INT"
        role = "ts" if tag == "TS" else "minimum"

        entry_id = batch_entry_id(raw_item_id)
        geometry_ref = f"RESULT/{product.path}"

        energy_val: float | None = None
        energy_meta = product.metadata.get("energy_hartree") if product.metadata else None
        if energy_meta is not None:
            energy_val = _number(energy_meta)

        # Frequency availability follows the shared 3-tier resolution
        # (product → global → historical) so catalog and endpoint never diverge.
        from acp.results.vibration_projection import probe_vibration_projection

        vib_probe = probe_vibration_projection(
            task_root, item_id=raw_item_id, is_batch=True,
        )
        if vib_probe.available:
            vibrations = StructureViewerVibrations(
                available=True,
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations",
                imaginary_count=vib_probe.imaginary_count,
                source=vib_probe.source,
            )
        else:
            vibrations = StructureViewerVibrations(available=False)

        entry = StructureViewerEntry(
            id=entry_id,
            group_id="batch_items",
            label=_normalize_display_label(product.label or raw_item_id, "formal_result", "batch"),
            role=role,
            status="completed",
            geometry=StructureViewerGeometry(
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                format="xyz",
            ),
            energy=StructureViewerEnergy(value=energy_val, unit="hartree", kind="electronic"),
            source=StructureViewerSource(kind="formal_result", geometry_ref=geometry_ref),
            badges=(tag,),
            vibrations=vibrations,
        )
        entries.append(entry)

        if default_id is None:
            default_id = entry_id

    if item_id is None:
        batch_work = task_root / "WORK" / "03_OPT" / "batch"
        if batch_work.is_dir():
            for child in sorted(batch_work.iterdir()):
                if not child.is_dir():
                    continue
                child_id = child.name
                if child_id in completed_item_ids:
                    continue
                traj_path = child / "optimize" / "optimization_trajectory.json"
                if not traj_path.is_file():
                    continue
                try:
                    traj_payload = json.loads(traj_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(traj_payload, dict):
                    continue
                cycles = traj_payload.get("cycles")
                if not isinstance(cycles, list) or not cycles:
                    continue

                last_cycle = cycles[-1]
                last_geom_ref = str(last_cycle.get("geometry_ref") or "")
                last_energy = _number(last_cycle.get("energy_hartree"))
                cycle_count = len(cycles)

                entry_id = batch_entry_id(child_id)
                geometry_ref = f"WORK/03_OPT/batch/{child_id}/optimize/{last_geom_ref}" if last_geom_ref else None

                entry = StructureViewerEntry(
                    id=entry_id,
                    group_id="batch_items",
                    label=f"{child_id} 未收敛 · 最后有效结构",
                    role="minimum",
                    status="failed",
                    geometry=StructureViewerGeometry(
                        endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                        format="xyz",
                    ),
                    energy=StructureViewerEnergy(value=last_energy, unit="hartree", kind="electronic"),
                    source=StructureViewerSource(kind="last_valid_cycle", geometry_ref=geometry_ref),
                    badges=("failed-last-frame",),
                    # failed items have no frequency product by design — the
                    # frequency step never ran for them
                    vibrations=StructureViewerVibrations(available=False),
                )
                entries.append(entry)

    if not entries:
        warnings.append("No batch items found in result manifest or optimization trajectories")

    return groups, entries, default_id


def _resolve_simple(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve simple workflows (optimize/xtb-optimize/singlepoint/frequency).

    Priority chain: formal RESULT product > optimization trajectory (failed) >
    calculation input.  The workflow string selects which step kind to look for.
    """
    from acp.results.manifest import find_products, load_result_manifest

    # Map workflow name → step kind for product lookup
    _WORKFLOW_STEP_KIND = {
        "optimize": "optimize",
        "xtb-optimize": "optimize",
        "singlepoint": "singlepoint",
        "frequency": "frequency",
    }
    step_kind = _WORKFLOW_STEP_KIND.get(workflow, "optimize")

    # Map workflow name → display label
    _WORKFLOW_LABELS = {
        "optimize": "优化",
        "xtb-optimize": "xTB 优化",
        "singlepoint": "单点能",
        "frequency": "频率",
    }
    label = _WORKFLOW_LABELS.get(workflow, workflow)

    manifest = load_result_manifest(task_root)
    if manifest is None:
        warnings.append(f"No result manifest found for {workflow}")
        return [], [], None

    # Look for formal structure products from this step
    structure_products = find_products(manifest, "structure")
    step_structure = [p for p in structure_products if step_kind in p.id.lower()]

    # Also look for energy products
    energy_products = find_products(manifest, "energy_report")
    step_energy = [p for p in energy_products if step_kind in p.id.lower()]

    groups: list[StructureViewerGroup] = []
    entries: list[StructureViewerEntry] = []
    default_id: str | None = None

    def _freq_vibrations(task_root: Path, job_id: str, entry_id: str) -> StructureViewerVibrations:
        from acp.results.vibration_projection import probe_vibration_projection

        vib_probe = probe_vibration_projection(task_root)
        if vib_probe.available:
            return StructureViewerVibrations(
                available=True,
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations",
                imaginary_count=vib_probe.imaginary_count,
                source=vib_probe.source,
            )
        return StructureViewerVibrations(available=False)

    # Priority 1: formal RESULT structure product
    if step_structure:
        product = step_structure[0]
        entry_id = simple_entry_id(step_kind)
        geometry_ref = f"RESULT/{product.path}" if product.path else None

        # Try to get energy from energy product
        energy_val: float | None = None
        if step_energy:
            energy_meta = step_energy[0].metadata.get("energy_hartree") if step_energy[0].metadata else None
            if energy_meta is not None:
                energy_val = _number(energy_meta)

        vib = _freq_vibrations(task_root, job_id, entry_id) if step_kind == "frequency" else StructureViewerVibrations(available=False)
        entries.append(StructureViewerEntry(
            id=entry_id,
            group_id="",
            label=label,
            role="minimum",
            status="completed",
            geometry=StructureViewerGeometry(
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                format="xyz",
            ),
            energy=StructureViewerEnergy(value=energy_val, unit="hartree", kind="electronic"),
            source=StructureViewerSource(kind="formal_result", geometry_ref=geometry_ref),
            vibrations=vib,
        ))
        default_id = entry_id
        return groups, entries, default_id

    # Priority 2: optimization trajectory (for optimize/xtb-optimize)
    if step_kind == "optimize":
        from acp.results.energy_graph import find_optimization_trajectory

        traj_path, payload = find_optimization_trajectory(task_root)
        if traj_path is not None and payload is not None:
            cycles = payload.get("cycles")
            if isinstance(cycles, list) and cycles:
                last_cycle = cycles[-1]
                last_geom_ref = str(last_cycle.get("geometry_ref") or "")
                last_energy = _number(last_cycle.get("energy_hartree"))
                status = str(payload.get("status") or "").lower()
                converged = bool(payload.get("converged"))

                if not converged or status == "failed":
                    # Failed optimization — show last valid cycle
                    entry_id = simple_entry_id(step_kind)
                    geometry_ref = f"WORK/03_OPT/{last_geom_ref}" if last_geom_ref else None

                    entries.append(StructureViewerEntry(
                        id=entry_id,
                        group_id="",
                        label=f"{label} 未收敛 · 最后有效结构",
                        role="minimum",
                        status="failed",
                        geometry=StructureViewerGeometry(
                            endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                            format="xyz",
                        ),
                        energy=StructureViewerEnergy(value=last_energy, unit="hartree", kind="electronic"),
                        source=StructureViewerSource(kind="last_valid_cycle", geometry_ref=geometry_ref),
                        badges=("failed-last-frame",),
                        vibrations=StructureViewerVibrations(available=False),
                    ))
                    default_id = entry_id
                    return groups, entries, default_id

    # Priority 3: calculation input (for singlepoint/frequency, or as fallback)
    if step_kind in ("singlepoint", "frequency"):
        # For singlepoint/frequency, the input structure IS the structure
        input_xyz = task_root / "input.xyz"
        if input_xyz.is_file():
            entry_id = simple_entry_id(step_kind)
            geometry_ref = "input.xyz"

            # For singlepoint, try to get energy from energy product
            energy_val = None
            if step_kind == "singlepoint" and step_energy:
                energy_meta = step_energy[0].metadata.get("energy_hartree") if step_energy[0].metadata else None
                if energy_meta is not None:
                    energy_val = _number(energy_meta)

            badges: list[str] = []
            if step_kind == "singlepoint":
                badges.append("几何未改变")

            # frequency jobs: the vibrations endpoint serves the
            # normal_modes product AND the WORK/04_FREQ historical
            # projection — the catalog flag may safely enable fetching
            vibrations = _freq_vibrations(task_root, job_id, entry_id) if step_kind == "frequency" else StructureViewerVibrations(available=False)

            entries.append(StructureViewerEntry(
                id=entry_id,
                group_id="",
                label=label,
                role="minimum",
                status="completed",
                geometry=StructureViewerGeometry(
                    endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                    format="xyz",
                ),
                energy=StructureViewerEnergy(value=energy_val, unit="hartree", kind="electronic"),
                source=StructureViewerSource(kind="calculation_input", geometry_ref=geometry_ref),
                badges=tuple(badges),
                vibrations=vibrations,
            ))
            default_id = entry_id
            return groups, entries, default_id

    warnings.append(f"No structure found for {workflow}")
    return groups, entries, default_id


def _resolve_scan(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve scan trajectory → structure viewer entries.

    Reads ``RESULT/trajectories/scan_trajectory.json`` via the scan primitive's
    persisted output.  Produces one entry per frame in file order, with the
    lowest-energy frame as default selection.
    """
    traj_path = task_root / "RESULT" / "trajectories" / "scan_trajectory.json"
    if not traj_path.is_file():
        warnings.append("No scan trajectory found")
        return [], [], None

    try:
        payload = json.loads(traj_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Cannot read scan trajectory: {exc}")
        return [], [], None

    if not isinstance(payload, dict):
        warnings.append("Scan trajectory is not a JSON object")
        return [], [], None

    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        warnings.append("Scan trajectory has no frames")
        return [], [], None

    groups: list[StructureViewerGroup] = []
    entries: list[StructureViewerEntry] = []
    default_id: str | None = None
    min_energy: float | None = None
    min_energy_entry_id: str | None = None

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        frame_index_raw = frame.get("index")
        if frame_index_raw is None:
            continue
        frame_index = int(frame_index_raw)
        entry_id = scan_entry_id(frame_index)

        energy_val = _number(frame.get("energy_hartree"))
        relative_path = str(frame.get("path") or "")
        geometry_ref = f"RESULT/{relative_path}" if relative_path else None
        status = "failed" if energy_val is None else "completed"

        entries.append(StructureViewerEntry(
            id=entry_id,
            group_id="",
            label=f"扫描帧 {frame_index}",
            role="minimum",
            status=status,
            geometry=StructureViewerGeometry(
                endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                format="xyz",
            ),
            energy=StructureViewerEnergy(value=energy_val, unit="hartree", kind="electronic"),
            source=StructureViewerSource(
                kind="formal_result",
                frame_index=frame_index,
                geometry_ref=geometry_ref,
            ),
            vibrations=StructureViewerVibrations(available=False),
        ))

        # Track lowest energy for default selection
        if energy_val is not None:
            if min_energy is None or energy_val < min_energy:
                min_energy = energy_val
                min_energy_entry_id = entry_id

    if not entries:
        warnings.append("No valid scan frames found")
        return groups, entries, None

    # Default = lowest-energy frame (NOT reordered — entries stay in file order)
    default_id = min_energy_entry_id or entries[0].id
    return groups, entries, default_id


def _resolve_irc(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Resolve IRC path files → per-frame structure viewer entries (todo 39).

    Reads ``RESULT/irc/irc_forward.xyz`` + ``irc_reverse.xyz`` (single- or
    multi-frame) and emits ONE ENTRY PER FRAME via ``irc_entry_id(endpoint,
    frame_index)``.  Frames stay in FILE ORDER (IRC path order is physically
    meaningful — never reordered).  ``source.frame_index`` makes the todo-9
    geometry endpoint extract the exact frame block via
    ``read_traj_frame_xyz``.  Groups: ``irc_forward`` (正向) +
    ``irc_reverse`` (反向).  Default entry = ``irc_forward_0`` — frame 0 of
    the forward path as the TS/path-center proxy (doc §5); a pure display
    default, NOT a TS-identity claim.  A missing direction adds a warning
    when the other direction is present.
    """
    from acp.results.irc_projection import IRC_DIRECTIONS, parse_irc_xyz_frames

    direction_labels = {"forward": "正向", "reverse": "反向"}
    groups = [
        StructureViewerGroup(id="irc_forward", label="正向", kind="irc"),
        StructureViewerGroup(id="irc_reverse", label="反向", kind="irc"),
    ]
    entries: list[StructureViewerEntry] = []
    default_id: str | None = None
    found: list[str] = []

    irc_dir = task_root / "RESULT" / "irc"

    for direction in IRC_DIRECTIONS:
        xyz_path = irc_dir / f"irc_{direction}.xyz"
        if not xyz_path.is_file():
            continue
        frames = parse_irc_xyz_frames(xyz_path)
        if not frames:
            warnings.append(f"IRC {direction} file has no parseable frames: irc_{direction}.xyz")
            continue
        found.append(direction)
        geometry_ref = f"RESULT/irc/{xyz_path.name}"

        for frame in frames:
            entry_id = irc_entry_id(direction, frame.index)
            is_endpoint = frame.index == len(frames) - 1
            entries.append(StructureViewerEntry(
                id=entry_id,
                group_id=f"irc_{direction}",
                label=f"IRC {direction_labels[direction]} {frame.index + 1}",
                role="endpoint" if is_endpoint else "path",
                status="completed",
                geometry=StructureViewerGeometry(
                    endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                    format="xyz",
                ),
                source=StructureViewerSource(
                    kind="formal_result",
                    frame_index=frame.index,
                    geometry_ref=geometry_ref,
                ),
                vibrations=StructureViewerVibrations(available=False),
            ))

        if default_id is None and direction == "forward":
            default_id = irc_entry_id("forward", 0)

    if found and len(found) < len(IRC_DIRECTIONS):
        missing = [d for d in IRC_DIRECTIONS if d not in found]
        for direction in missing:
            warnings.append(f"IRC {direction} trajectory missing (irc_{direction}.xyz)")

    if default_id is None and entries:
        # Forward absent → first reverse frame is the path-center proxy.
        default_id = entries[0].id

    return groups, entries, default_id


def _resolve_legacy(task_root: Path, workflow: str, job_id: str, warnings: list[str], item_id: str | None = None) -> _ResolverResult:
    """Legacy fallback for workflows without a dedicated resolver.

    Reads ``result_manifest.json`` products of kind ``structure`` first, then
    falls back to ``result_summary.json`` (via ``acp.compat.legacy.manifests``).
    Each product becomes an entry with ``source.kind="formal_result"`` and
    badge ``兼容模式``.  This covers retired workflows (ensemble, energy,
    mechanism, etc.) and unknown workflow strings.
    """
    from acp.results.manifest import find_products, load_result_manifest

    _STRUCTURE_KINDS = frozenset({"structure", "xyz"})
    groups: list[StructureViewerGroup] = []
    entries: list[StructureViewerEntry] = []
    default_id: str | None = None
    seen_paths: set[str] = set()

    manifest = load_result_manifest(task_root)
    if manifest is not None:
        for kind_str in _STRUCTURE_KINDS:
            for product in find_products(manifest, kind_str):
                if not product.path:
                    continue
                rel_posix = product.path.replace("\\", "/")
                if rel_posix in seen_paths:
                    continue
                seen_paths.add(rel_posix)

                entry_id = legacy_entry_id(rel_posix)
                geometry_ref = f"RESULT/{rel_posix}"

                entries.append(StructureViewerEntry(
                    id=entry_id,
                    group_id="",
                    label=_normalize_display_label(
                        product.label or rel_posix, "formal_result", "legacy"
                    ),
                    role="minimum",
                    status="completed",
                    geometry=StructureViewerGeometry(
                        endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                        format="xyz",
                    ),
                    source=StructureViewerSource(
                        kind="formal_result",
                        product_id=product.id,
                        geometry_ref=geometry_ref,
                    ),
                    badges=("兼容模式",),
                    vibrations=StructureViewerVibrations(available=False),
                ))
                if default_id is None:
                    default_id = entry_id

    if not entries:
        summary_path = task_root / "RESULT" / "result_summary.json"
        if summary_path.is_file():
            try:
                from acp.compat.legacy.manifests import read_result_summary
                summary = read_result_summary(summary_path)
                for product in summary.get("products") or []:
                    if not isinstance(product, dict):
                        continue
                    rel_path = str(product.get("path") or "")
                    if not rel_path:
                        continue
                    rel_posix = rel_path.replace("\\", "/")
                    if rel_posix in seen_paths:
                        continue
                    seen_paths.add(rel_posix)

                    entry_id = legacy_entry_id(rel_posix)
                    geometry_ref = f"RESULT/{rel_posix}"

                    entries.append(StructureViewerEntry(
                        id=entry_id,
                        group_id="",
                        label=_normalize_display_label(
                            str(product.get("label") or rel_posix), "formal_result", "legacy"
                        ),
                        role="minimum",
                        status="completed",
                        geometry=StructureViewerGeometry(
                            endpoint=f"/api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
                            format="xyz",
                        ),
                        source=StructureViewerSource(
                            kind="formal_result",
                            geometry_ref=geometry_ref,
                        ),
                        badges=("兼容模式",),
                        vibrations=StructureViewerVibrations(available=False),
                    ))
                    if default_id is None:
                        default_id = entry_id
            except (ValueError, OSError) as exc:
                warnings.append(f"Cannot read result_summary.json: {exc}")

    if not entries:
        warnings.append(f"No structure products found for workflow '{workflow}'")

    return groups, entries, default_id


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
        groups_raw, entries_raw, default_id = resolver(root, workflow, job_id, warnings, item_id)
    except StructureViewerError:
        raise
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

# ── Structure overlay + RMSD (todo 40) ──────────────────────────────────────


def _parse_xyz_atoms(text: str) -> tuple[list[str], list[list[float]]] | None:
    """Parse the first XYZ frame into (symbols, coords); None when invalid."""
    lines = (text or "").strip().splitlines()
    if len(lines) < 2:
        return None
    try:
        n_atoms = int(lines[0].strip())
    except ValueError:
        return None
    if n_atoms <= 0 or len(lines) < 2 + n_atoms:
        return None
    symbols: list[str] = []
    coords: list[list[float]] = []
    for row in lines[2 : 2 + n_atoms]:
        parts = row.split()
        if len(parts) < 4:
            return None
        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            return None
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None
        symbols.append(parts[0])
        coords.append([x, y, z])
    return symbols, coords


def _read_entry_xyz(
    work_dir: Path, entry: StructureViewerEntry
) -> tuple[list[str], list[list[float]]] | None:
    """Resolve one entry's geometry to (symbols, coords).

    Mirrors the todo-9 geometry endpoint: path-safe ``resolve_safe`` under
    ``work_dir`` (with a bare-ref ``RESULT/`` probe), multi-frame extraction
    via ``read_traj_frame_xyz`` when ``source.frame_index`` is set.
    """
    from acp.confsearch.sampling_models import read_traj_frame_xyz
    from acp.scheduler.files import resolve_safe

    geometry_ref = getattr(entry.source, "geometry_ref", None)
    if not geometry_ref:
        return None
    resolved = resolve_safe(work_dir, geometry_ref)
    if resolved is None and not geometry_ref.startswith(("RESULT/", "WORK/")):
        resolved = resolve_safe(work_dir, f"RESULT/{geometry_ref}")
    if resolved is None:
        return None
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError:
        return None
    frame_index = getattr(entry.source, "frame_index", None)
    if frame_index is not None:
        frame = read_traj_frame_xyz(resolved, int(frame_index))
        if frame is not None:
            text = frame
    return _parse_xyz_atoms(text)


def _kabsch_pair_distances(
    coords_a: list[list[float]],
    coords_b: list[list[float]],
    pairs: list[tuple[int, int]],
) -> tuple[float, list[float]]:
    """Optimal (Kabsch) superposition of the mapped B atoms onto A atoms.

    Returns ``(rmsd, per_pair_distances)`` after superposition — the RMSD is
    the rotation/translation-invariant minimum over the mapped pairs.
    """
    import numpy as np

    p = np.asarray([coords_a[i] for i, _ in pairs], dtype=float)
    q = np.asarray([coords_b[j] for _, j in pairs], dtype=float)
    if len(pairs) == 1:
        return 0.0, [0.0]
    p_c = p - p.mean(axis=0)
    q_c = q - q.mean(axis=0)
    h = p_c.T @ q_c
    u, _s, vt = np.linalg.svd(h)
    d = float(np.sign(np.linalg.det(vt.T @ u.T)))
    diag = np.eye(3)
    diag[2, 2] = d
    rotation = vt.T @ diag @ u.T
    q_aligned = q_c @ rotation
    diff = p_c - q_aligned
    distances = np.sqrt(np.sum(diff * diff, axis=1))
    rmsd = float(np.sqrt(np.mean(distances**2)))
    return rmsd, [float(v) for v in distances]


def _mcs_mapping(
    symbols_a: list[str],
    coords_a: list[list[float]],
    symbols_b: list[str],
    coords_b: list[list[float]],
) -> list[tuple[int, int]] | None:
    """RDKit MCS mapping via the ``acp.calculations.pes.atom_mapping`` pattern.

    Only a UNIQUE candidate is accepted — an ambiguous mapping is never
    guessed.  Returns ``None`` when RDKit is unavailable or the search fails.
    """
    try:
        from acp.calculations.pes.atom_mapping import map_reactant_to_product
    except ImportError:
        logger.debug("atom_mapping import failed; overlay MCS path unavailable")
        return None
    try:
        result = map_reactant_to_product(symbols_a, coords_a, symbols_b, coords_b)
    except Exception as exc:  # noqa: BLE001 — mapping must never raise out
        logger.debug("overlay MCS mapping failed: %s", exc)
        return None
    if result.status != "unique" or not result.candidates:
        return None
    return [(int(i), int(j)) for i, j in result.candidates[0].mapping]


def compute_overlay(
    job_id: str,
    work_dir: Path,
    entry_a: StructureViewerEntry,
    entry_b: StructureViewerEntry,
) -> dict[str, Any]:
    """Compute the overlay mapping + optimal RMSD between two entries.

    Mapping authority (never guessed client-side):
    identity when both symbol sequences match exactly, else an RDKit MCS
    mapping accepted only when unique, else ``mapping=None, reason="unproven"``
    (RMSD withheld).  ``max_displacement`` is the largest mapped-pair distance
    AFTER Kabsch superposition.
    """
    _ = job_id  # reserved for provenance; mapping depends only on geometry
    loaded_a = _read_entry_xyz(Path(work_dir), entry_a)
    loaded_b = _read_entry_xyz(Path(work_dir), entry_b)
    if loaded_a is None or loaded_b is None:
        return {
            "ok": False,
            "mapping": None,
            "rmsd": None,
            "max_displacement": None,
            "n_mapped": 0,
            "reason": "geometry_unreadable",
        }
    symbols_a, coords_a = loaded_a
    symbols_b, coords_b = loaded_b
    norm_a = [s.strip().capitalize() for s in symbols_a]
    norm_b = [s.strip().capitalize() for s in symbols_b]

    if norm_a == norm_b and len(norm_a) == len(norm_b):
        pairs = [(i, i) for i in range(len(norm_a))]
        reason = "identity"
    else:
        pairs = _mcs_mapping(norm_a, coords_a, norm_b, coords_b) or []
        reason = "mcs" if pairs else "unproven"

    if not pairs:
        return {
            "ok": True,
            "mapping": None,
            "rmsd": None,
            "max_displacement": None,
            "n_mapped": 0,
            "reason": "unproven",
        }

    rmsd, distances = _kabsch_pair_distances(coords_a, coords_b, pairs)
    worst = max(range(len(distances)), key=lambda idx: distances[idx])
    max_displacement = {
        "i": int(pairs[worst][0]),
        "j": int(pairs[worst][1]),
        "distance": distances[worst],
    }
    return {
        "ok": True,
        "mapping": [[int(i), int(j)] for i, j in pairs],
        "rmsd": rmsd,
        "max_displacement": max_displacement,
        "n_mapped": len(pairs),
        "reason": reason,
    }
