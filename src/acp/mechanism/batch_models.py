"""Batch stationary-point models — unified Lowconfirm/Highconfirm input protocol (batch plan §4/§7).

Lowconfirm and Highconfirm share one execution core and differ only through
their profile.  This module defines the *input* side of that contract:

* **TAG parsing** — ``TAG: TS | candidate_id=... | source=... | frame=...``
  comment lines (case-insensitive) map every structure onto the internal
  ``ts`` / ``minimum`` kinds with a documented priority order (user edit >
  S2 candidate manifest > XYZ comment > default INT).
* **BatchStructureItem** — one input structure with identity metadata.
* **BatchCalculationItem / BatchCalculationManifest** — per-item execution
  records (status, outputs, errors) and their persisted aggregate.

Loaders accept the three unified source types: S2 candidate manifests,
multi-frame XYZ files (upload/paste), and stage manifests (S2/S3 fallback).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.confsearch.shared.artifacts import write_json_atomic
from acp.intake import parse_xyz_text

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_CALCULATION_SCHEMA_VERSION",
    "BATCH_REQUEST_SCHEMA_VERSION",
    "BatchCalculationItem",
    "BatchCalculationManifest",
    "BatchStructureItem",
    "TERMINAL_ITEM_STATUSES",
    "apply_user_overrides",
    "build_tag_title",
    "item_cache_key",
    "load_batch_request",
    "load_items_from_s2_candidate_manifest",
    "load_items_from_s2_path_manifest",
    "load_items_from_s3_manifest",
    "load_items_from_xyz_file",
    "load_items_from_xyz_text",
    "normalize_tag",
    "parse_tag_comment",
    "role_for_tag",
    "kind_for_tag",
    "tag_for_kind",
]

BATCH_REQUEST_SCHEMA_VERSION = "batch_structures_v1"
BATCH_CALCULATION_SCHEMA_VERSION = "batch_calculation_v1"

#: Statuses that stop an item's lifecycle (no further execution needed).
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})

_TAG_RE = re.compile(r"\bTAG\s*[:=]\s*([A-Za-z_ ]+?)(?:\s*\||$)", re.IGNORECASE)
_KV_RES = {
    "candidate_id": re.compile(r"\bcandidate_id\s*=\s*([^\s|]+)", re.IGNORECASE),
    "source": re.compile(r"\bsource\s*=\s*([^\s|]+)", re.IGNORECASE),
    "frame": re.compile(r"\bframe\s*=\s*(\d+)", re.IGNORECASE),
}

_TS_ALIASES = frozenset({"ts", "tst", "transition_state", "transition state", "transitionstate"})
_INT_ALIASES = frozenset({"int", "minimum", "intermediate", "min"})

_S2_CANDIDATE_SCHEMA = "s2_candidate_v1"


# ── TAG parsing ──────────────────────────────────────────────────────────


def normalize_tag(value: str | None) -> str | None:
    """Normalize any external TS/INT spelling to ``"TS"`` / ``"INT"``.

    Returns ``None`` when *value* is empty or not a recognized role label.
    Matching is case-insensitive and undelimited (``transition_state``,
    ``Transition State`` and ``ts`` all mean TS).
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in _TS_ALIASES:
        return "TS"
    if text in _INT_ALIASES:
        return "INT"
    return None


def kind_for_tag(tag: str | None) -> str:
    """Internal stationary-point kind for a TAG (``ts`` / ``minimum``)."""
    return "ts" if normalize_tag(tag) == "TS" else "minimum"


def role_for_tag(tag: str | None) -> str:
    """Internal role for a TAG (``transition_state`` / ``intermediate``)."""
    return "transition_state" if normalize_tag(tag) == "TS" else "intermediate"


def tag_for_kind(kind: str | None) -> str:
    """Display TAG for an internal kind (``TS`` / ``INT``)."""
    return "TS" if str(kind or "").lower() in {"ts", "ts_seed", "transition_state"} else "INT"


def parse_tag_comment(comment: str | None) -> dict[str, str | None]:
    """Parse a TAG comment line into ``{tag, candidate_id, source, frame}``.

    Only the well-formed ``TAG: <role> | key=value ...`` prefix is
    interpreted; unknown keys are ignored.  ``tag`` is the normalized
    ``"TS"`` / ``"INT"`` (``None`` when the comment carries no TAG).
    """
    text = str(comment or "")
    out: dict[str, str | None] = {"tag": None, "candidate_id": None, "source": None, "frame": None}
    match = _TAG_RE.search(text)
    if match:
        out["tag"] = normalize_tag(match.group(1))
    for key, regex in _KV_RES.items():
        kv = regex.search(text)
        if kv:
            out[key] = kv.group(1)
    return out


def build_tag_title(
    tag: str | None,
    *,
    candidate_id: str | None = None,
    source: str | None = None,
    frame: int | None = None,
    extra: str = "",
) -> str:
    """Render the canonical TAG comment line (batch plan §4)."""
    parts: list[str] = [f"TAG: {normalize_tag(tag) or 'INT'}"]
    if candidate_id:
        parts.append(f"candidate_id={candidate_id}")
    if source:
        parts.append(f"source={source}")
    if frame is not None:
        parts.append(f"frame={int(frame):03d}")
    if extra:
        parts.append(str(extra))
    return " | ".join(parts)


def _xyz_first_comment(text: str) -> str:
    lines = text.strip().splitlines()
    if len(lines) >= 2:
        try:
            int(lines[0].strip())
        except ValueError:
            return ""
        return lines[1]
    return ""


def _rewrite_comment(xyz_text: str, new_comment: str) -> str:
    """Replace the comment line of a single-frame XYZ block."""
    lines = xyz_text.strip().splitlines()
    if not lines:
        return xyz_text
    try:
        n = int(lines[0].strip())
    except ValueError:
        return xyz_text
    if len(lines) < n + 1:
        return xyz_text
    return "\n".join([lines[0], new_comment, *lines[2 : 2 + n]]) + "\n"


# ── data models ──────────────────────────────────────────────────────────


@dataclass
class BatchStructureItem:
    """One input structure for a Lowconfirm/Highconfirm batch run.

    Attributes:
        item_id: Stable batch-local id (``item_001`` ...), assigned in
            submission order by the loaders.
        name: Display name (shown in the frontend preview table).
        tag: Normalized ``"TS"`` / ``"INT"`` label after priority
            resolution.
        xyz: Inline single-frame XYZ text (comment carries the TAG line).
        candidate_id: Source candidate id (S2 manifests), else the
            ``candidate_id`` parsed from the TAG comment or ``item_id``.
        source_type: ``s2_candidate`` / ``job_artifact`` / ``upload`` /
            ``paste`` / ``s3_confirmed``.
        source_ref: Human-readable source pointer (manifest path + frame,
            upload path, ...).
        charge: Per-item charge override (``None`` = job-level default).
        multiplicity: Per-item multiplicity override.
        atom_count / formula: Parsed convenience metadata.
        include: Frontend checkbox — excluded items are dropped at load.
    """

    item_id: str
    name: str
    tag: str = "INT"
    xyz: str = ""
    candidate_id: str = ""
    source_type: str = "upload"
    source_ref: str = ""
    charge: int | None = None
    multiplicity: int | None = None
    atom_count: int = 0
    formula: str = ""
    include: bool = True

    @property
    def kind(self) -> str:
        return kind_for_tag(self.tag)

    @property
    def role(self) -> str:
        return role_for_tag(self.tag)

    def resolved_charge(self, default: int) -> int:
        return self.charge if self.charge is not None else int(default)

    def resolved_multiplicity(self, default: int) -> int:
        return self.multiplicity if self.multiplicity is not None else int(default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "tag": self.tag,
            "kind": self.kind,
            "role": self.role,
            "candidate_id": self.candidate_id or self.item_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "atom_count": self.atom_count,
            "formula": self.formula,
            "include": self.include,
            "xyz": self.xyz,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BatchStructureItem:
        return cls(
            item_id=str(payload.get("item_id") or ""),
            name=str(payload.get("name") or payload.get("item_id") or ""),
            tag=normalize_tag(payload.get("tag")) or "INT",
            xyz=str(payload.get("xyz") or ""),
            candidate_id=str(payload.get("candidate_id") or ""),
            source_type=str(payload.get("source_type") or "upload"),
            source_ref=str(payload.get("source_ref") or ""),
            charge=(int(payload["charge"]) if payload.get("charge") is not None else None),
            multiplicity=(
                int(payload["multiplicity"]) if payload.get("multiplicity") is not None else None
            ),
            atom_count=int(payload.get("atom_count") or 0),
            formula=str(payload.get("formula") or ""),
            include=bool(payload.get("include", True)),
        )


def _assign_item_ids(items: list[BatchStructureItem]) -> list[BatchStructureItem]:
    """Assign unique ``item_id``s in list order; leave parsed
    ``candidate_id``s untouched (empty ones are filled from the new id)."""
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not item.item_id:
            item.item_id = f"item_{index:03d}"
        while item.item_id in seen:
            item.item_id = f"{item.item_id}_x{len(seen)}"
        seen.add(item.item_id)
        if not item.candidate_id:
            item.candidate_id = item.item_id
    return items


@dataclass
class BatchCalculationItem:
    """Execution record for one batch item (batch plan §7)."""

    item_id: str
    candidate_id: str
    name: str
    tag: str
    status: str = "pending"
    input_xyz: str = ""
    optimized_xyz: str = ""
    charge: int = 0
    multiplicity: int = 1
    source_type: str = ""
    source_ref: str = ""
    cache_key: str = ""
    frequency: dict[str, Any] = field(default_factory=dict)
    single_point: dict[str, Any] = field(default_factory=dict)
    thermochemistry: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    work_dir: str = ""

    @property
    def kind(self) -> str:
        return kind_for_tag(self.tag)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "candidate_id": self.candidate_id,
            "name": self.name,
            "tag": self.tag,
            "kind": self.kind,
            "role": role_for_tag(self.tag),
            "status": self.status,
            "input_xyz": self.input_xyz,
            "optimized_xyz": self.optimized_xyz,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "cache_key": self.cache_key,
            "frequency": dict(self.frequency),
            "single_point": dict(self.single_point),
            "thermochemistry": dict(self.thermochemistry),
            "error": self.error,
            "work_dir": self.work_dir,
        }

    @classmethod
    def from_item(
        cls, item: BatchStructureItem, charge: int, multiplicity: int
    ) -> BatchCalculationItem:
        return cls(
            item_id=item.item_id,
            candidate_id=item.candidate_id or item.item_id,
            name=item.name,
            tag=item.tag,
            charge=item.resolved_charge(charge),
            multiplicity=item.resolved_multiplicity(multiplicity),
            source_type=item.source_type,
            source_ref=item.source_ref,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BatchCalculationItem:
        return cls(
            item_id=str(payload.get("item_id") or ""),
            candidate_id=str(payload.get("candidate_id") or payload.get("item_id") or ""),
            name=str(payload.get("name") or ""),
            tag=normalize_tag(payload.get("tag")) or "INT",
            status=str(payload.get("status") or "pending"),
            input_xyz=str(payload.get("input_xyz") or ""),
            optimized_xyz=str(payload.get("optimized_xyz") or ""),
            charge=int(payload.get("charge") or 0),
            multiplicity=int(payload.get("multiplicity") or 1),
            source_type=str(payload.get("source_type") or ""),
            source_ref=str(payload.get("source_ref") or ""),
            cache_key=str(payload.get("cache_key") or ""),
            frequency=dict(payload.get("frequency") or {}),
            single_point=dict(payload.get("single_point") or {}),
            thermochemistry=dict(payload.get("thermochemistry") or {}),
            error=str(payload.get("error") or ""),
            work_dir=str(payload.get("work_dir") or ""),
        )


@dataclass
class BatchCalculationManifest:
    """Persisted aggregate of a batch run (``batch_calculation_manifest.json``)."""

    profile_level: str
    items: list[BatchCalculationItem] = field(default_factory=list)
    schema_version: str = BATCH_CALCULATION_SCHEMA_VERSION
    workflow: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def counts(self) -> dict[str, int]:
        counts = {"total": len(self.items)}
        for status in ("completed", "failed", "skipped", "cancelled", "pending", "running"):
            counts[status] = sum(1 for item in self.items if item.status == status)
        return counts

    def by_cache_key(self) -> dict[str, BatchCalculationItem]:
        return {item.cache_key: item for item in self.items if item.cache_key}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "batch_calculation_manifest",
            "schema_version": self.schema_version,
            "workflow": self.workflow,
            "profile_level": self.profile_level,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "counts": self.counts,
            "items": [item.to_dict() for item in self.items],
        }

    def write(self, path: Path) -> Path:
        return write_json_atomic(Path(path), self.to_dict())

    @classmethod
    def read(cls, path: Path | str) -> BatchCalculationManifest | None:
        """Read a manifest; ``None`` when absent or unreadable (resume-safe)."""
        manifest_path = Path(path)
        if not manifest_path.is_file():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unreadable batch manifest %s: %s", manifest_path, exc)
            return None
        if not isinstance(payload, dict):
            return None
        return cls(
            profile_level=str(payload.get("profile_level") or ""),
            items=[
                BatchCalculationItem.from_dict(row)
                for row in payload.get("items") or []
                if isinstance(row, dict)
            ],
            workflow=str(payload.get("workflow") or ""),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )


# ── cache key ────────────────────────────────────────────────────────────


def item_cache_key(item: BatchStructureItem, profile_key: str) -> str:
    """Stable per-item cache key: identity × charge × TAG × profile × geometry.

    Two runs with the same key are considered the same calculation, so a
    resumed batch skips items whose key is already ``completed``.
    """
    digest = hashlib.sha256()
    digest.update(str(profile_key).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(item.candidate_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(item.tag.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(item.resolved_charge(0)).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(item.resolved_multiplicity(1)).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(item.xyz.strip().encode("utf-8"))
    return "sha256:" + digest.hexdigest()


# ── user overrides ───────────────────────────────────────────────────────


def apply_user_overrides(
    items: list[BatchStructureItem],
    overrides: dict[str, dict[str, Any]] | None,
) -> list[BatchStructureItem]:
    """Apply frontend edits; user-modified tags/names win over every parser.

    *overrides* maps ``item_id`` (or ``candidate_id``) to
    ``{"tag": "TS", "name": ..., "charge": ..., "multiplicity": ...,
    "include": bool}`` entries; unknown keys are ignored.
    """
    if not overrides:
        return items
    by_key: dict[str, BatchStructureItem] = {}
    for item in items:
        by_key.setdefault(item.item_id, item)
        by_key.setdefault(item.candidate_id, item)
    for key, change in overrides.items():
        item = by_key.get(str(key))
        if item is None or not isinstance(change, dict):
            continue
        tag = normalize_tag(change.get("tag"))
        if tag:
            item.tag = tag
        if change.get("name"):
            item.name = str(change["name"])
        if change.get("charge") is not None:
            item.charge = int(change["charge"])
        if change.get("multiplicity") is not None:
            item.multiplicity = int(change["multiplicity"])
        if change.get("include") is False:
            item.include = False
        elif change.get("include") is True:
            item.include = True
    return items


# ── loaders ──────────────────────────────────────────────────────────────


def _items_from_parse(
    result: Any,
    *,
    source_type: str,
    source_ref: str,
    base_name: str,
) -> list[BatchStructureItem]:
    items: list[BatchStructureItem] = []
    structures = list(getattr(result, "structures", None) or [])
    multi = len(structures) > 1
    for index, structure in enumerate(structures, start=1):
        xyz = str(getattr(structure, "xyz", None) or "")
        if not xyz:
            continue
        comment = _xyz_first_comment(xyz)
        parsed = parse_tag_comment(comment)
        frame_name = f"{base_name}__frame_{index:03d}" if multi else base_name
        tag_hint = parsed["tag"]
        candidate_hint = parsed["candidate_id"]
        if candidate_hint is None and comment:
            # S2 seeds historically annotate titles as "<id> source=...";
            # ts_/int_ prefixes carry the role when no explicit TAG exists.
            stem = comment.split()[0].lower()
            if stem.startswith(("ts_", "int_")):
                candidate_hint = stem
        if tag_hint is None and candidate_hint:
            normalized_stem = str(candidate_hint).lower()
            tag_hint = (
                "TS"
                if normalized_stem.startswith("ts_")
                else ("INT" if normalized_stem.startswith("int_") else None)
            )
        items.append(
            BatchStructureItem(
                item_id="",
                name=str(frame_name),
                tag=tag_hint or "INT",
                xyz=xyz,
                candidate_id=str(candidate_hint or ""),
                source_type=source_type,
                source_ref=source_ref,
                charge=getattr(structure, "charge", None),
                multiplicity=getattr(structure, "multiplicity", None),
                atom_count=int(getattr(structure, "atom_count", 0) or 0),
                formula=str(getattr(structure, "formula", "") or ""),
            )
        )
    return _assign_item_ids(items)


def load_items_from_xyz_text(
    text: str,
    *,
    source_type: str = "paste",
    source_ref: str = "",
    base_name: str = "mol",
) -> list[BatchStructureItem]:
    """Load items from pasted/inline XYZ text (multi-frame supported)."""
    result = parse_xyz_text(text)
    return _items_from_parse(
        result, source_type=source_type, source_ref=source_ref, base_name=base_name
    )


def load_items_from_xyz_file(
    path: Path | str,
    *,
    source_type: str = "upload",
    tag: str | None = None,
) -> list[BatchStructureItem]:
    """Load items from an XYZ file on disk (multi-frame supported)."""
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    items = load_items_from_xyz_text(
        text, source_type=source_type, source_ref=str(file_path), base_name=file_path.stem
    )
    if tag is not None:
        normalized = normalize_tag(tag)
        for item in items:
            if normalized:
                item.tag = normalized
    return items


def _resolve_candidate_geometry(manifest_path: Path, ref: str) -> Path | None:
    """Resolve an S2 candidate geometry reference (RESULT + handoff layouts)."""
    if not ref:
        return None
    probes = [
        manifest_path.parent / ref,
        manifest_path.parent.parent / ref,
    ]
    for probe in probes:
        if probe.is_file():
            return probe.resolve()
    return None


def load_items_from_s2_candidate_manifest(
    manifest_path: Path | str,
    candidate_ids: list[str] | None = None,
) -> list[BatchStructureItem]:
    """Load the user-confirmed S2 candidates from ``s2_candidate_manifest.json``.

    Only *active* candidates load by default — unselected algorithm
    recommendations never enter a batch (batch plan §13).  Explicit
    *candidate_ids* select a subset (must exist in the manifest).
    """
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S2 candidate manifest is not a JSON object: {path}")
    if str(payload.get("schema_version") or "") in {"s2_path_v1", "s2_path_v2"}:
        # An s2_path manifest was handed over — the candidates live in the
        # sibling s2_candidate_manifest.json (batch plan §5).
        sibling = path.with_name("s2_candidate_manifest.json")
        if not sibling.is_file():
            raise ValueError(
                f"No s2_candidate_manifest.json next to {path} — confirm the S2 "
                "candidate selection first"
            )
        return load_items_from_s2_candidate_manifest(sibling, candidate_ids)
    if payload.get("schema_version") != _S2_CANDIDATE_SCHEMA:
        raise ValueError(f"Not an {_S2_CANDIDATE_SCHEMA} manifest: {path}")
    rows = [row for row in payload.get("candidates") or [] if isinstance(row, dict)]
    if candidate_ids:
        wanted = {str(cid) for cid in candidate_ids}
        rows = [row for row in rows if str(row.get("candidate_id")) in wanted]
        missing = wanted - {str(row.get("candidate_id")) for row in rows}
        if missing:
            raise ValueError(
                f"Unknown candidate ids in S2 candidate manifest: {', '.join(sorted(missing))}"
            )
    else:
        rows = [row for row in rows if row.get("active", True)]

    items: list[BatchStructureItem] = []
    for row in rows:
        candidate_id = str(row.get("candidate_id") or "")
        ref = str(row.get("geometry") or row.get("xyz") or "")
        geometry = _resolve_candidate_geometry(path, ref)
        if geometry is None:
            raise FileNotFoundError(
                f"S2 candidate geometry missing for {candidate_id}: {ref} (manifest {path})"
            )
        # Priority (batch plan §4): candidate-manifest role > recommended
        # role > XYZ TAG comment > default INT.
        tag = (
            normalize_tag(row.get("role"))
            or normalize_tag(row.get("recommended_role"))
            or None
        )
        frame_index = row.get("frame_index")
        items.extend(
            _items_from_tagged_geometry(
                geometry.read_text(encoding="utf-8"),
                tag=tag,
                candidate_id=candidate_id,
                source_ref=f"{path}#{candidate_id}",
                frame_index=int(frame_index) if frame_index is not None else None,
                name=str(row.get("name") or candidate_id),
            )
        )
    return _assign_item_ids(items)


def _items_from_tagged_geometry(
    text: str,
    *,
    tag: str | None,
    candidate_id: str,
    source_ref: str,
    frame_index: int | None,
    name: str,
) -> list[BatchStructureItem]:
    result = parse_xyz_text(text)
    structures = list(result.structures)
    parsed_tag = tag
    items: list[BatchStructureItem] = []
    for index, structure in enumerate(structures, start=1):
        xyz = str(structure.xyz or "")
        if not xyz:
            continue
        resolved = parsed_tag or parse_tag_comment(_xyz_first_comment(xyz))["tag"] or "INT"
        title = build_tag_title(
            resolved,
            candidate_id=candidate_id,
            source="PESsearch" if parsed_tag else None,
            frame=frame_index,
        )
        xyz = _rewrite_comment(xyz, title)
        item_name = name or candidate_id
        if len(structures) > 1:
            item_name = f"{item_name}__frame_{index:03d}"
        items.append(
            BatchStructureItem(
                item_id="",
                name=item_name,
                tag=resolved,
                xyz=xyz,
                candidate_id=candidate_id,
                source_type="s2_candidate",
                source_ref=source_ref,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                atom_count=structure.atom_count,
                formula=structure.formula,
            )
        )
    return items


def load_items_from_s2_path_manifest(
    manifest_path: Path | str,
    select: list[str] | None = None,
) -> tuple[list[BatchStructureItem], dict[str, Any]]:
    """Load candidate items from an ``s2_path_manifest.json``.

    Prefers the sibling user-confirmed ``s2_candidate_manifest.json``
    (the PESsearch final selection) when present; falls back to the
    manifest's own v1 candidate rows / v2 algorithm recommendations
    (legacy behaviour).

    Returns:
        ``(items, manifest_payload)`` — the payload feeds downstream
        charge/multiplicity/coordinate-plan resolution.
    """
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S2 manifest is not a JSON object: {path}")

    sibling = path.with_name("s2_candidate_manifest.json")
    if sibling.is_file() and payload.get("schema_version") == "s2_path_v2":
        try:
            items = load_items_from_s2_candidate_manifest(sibling, select)
            if items:
                return _assign_item_ids(items), payload
        except (ValueError, FileNotFoundError) as exc:
            logger.warning(
                "Falling back to S2 recommendations (%s candidate manifest unreadable: %s)",
                path,
                exc,
            )

    rows: list[dict[str, Any]] = list(payload.get("candidates") or [])
    if payload.get("schema_version") == "s2_path_v2":
        rows = _v2_recommendation_rows(payload)
    if not rows:
        raise ValueError("S2 manifest carries no candidates")
    if select:
        by_id = {str(row.get("id")): row for row in rows}
        missing = [cid for cid in select if cid not in by_id]
        if missing:
            raise ValueError(f"Unknown candidate ids in S2 manifest: {', '.join(missing)}")
        rows = [by_id[str(cid)] for cid in select]
    else:
        ts_rows = [row for row in rows if str(row.get("kind")) == "ts_seed"]
        rows = ts_rows or rows

    items: list[BatchStructureItem] = []
    for row in rows:
        candidate_id = str(row.get("id") or "")
        ref = str(row.get("xyz") or row.get("geometry_path") or "")
        geometry = _resolve_candidate_geometry(path, ref)
        if geometry is None and ref:
            scan_dir = str((payload.get("scan") or {}).get("scan_dir") or "")
            if scan_dir:
                via_scan = path.parent.parent.parent / scan_dir / ref
                if via_scan.is_file():
                    geometry = via_scan.resolve()
        if geometry is None:
            raise FileNotFoundError(
                f"S2 candidate geometry missing: {ref} (looked next to {path})"
            )
        kind = str(row.get("kind") or "")
        tag = "TS" if kind in {"ts_seed", "ts"} else "INT"
        items.extend(
            _items_from_tagged_geometry(
                geometry.read_text(encoding="utf-8"),
                tag=tag,
                candidate_id=candidate_id,
                source_ref=f"{path}#{candidate_id}",
                frame_index=None,
                name=str(row.get("name") or candidate_id),
            )
        )
    return _assign_item_ids(items), payload


def _v2_recommendation_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    recommendations = payload.get("recommendations") or {}
    for rec in recommendations.get("ts") or []:
        if isinstance(rec, dict):
            rows.append({**rec, "id": str(rec.get("candidate_id") or ""), "kind": "ts_seed"})
    for rec in recommendations.get("intermediates") or []:
        if isinstance(rec, dict):
            rows.append(
                {**rec, "id": str(rec.get("candidate_id") or ""), "kind": "intermediate_seed"}
            )
    return rows


def load_items_from_s3_manifest(
    manifest_path: Path | str,
    select: list[str] | None = None,
) -> tuple[list[BatchStructureItem], dict[str, Any]]:
    """Load S4-bound items from a ``s3_lowconfirm_manifest.json`` (confirmed rows)."""
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"S3 manifest is not a JSON object: {path}")
    rows = [row for row in payload.get("candidates") or [] if str(row.get("status")) == "confirmed"]
    if not rows:
        raise ValueError("S3 manifest has no confirmed candidates to promote")
    if select:
        by_id = {str(row.get("id")): row for row in payload.get("candidates") or []}
        missing = [cid for cid in select if cid not in by_id]
        if missing:
            raise ValueError(f"Unknown candidate ids in S3 manifest: {', '.join(missing)}")
        chosen = [by_id[str(cid)] for cid in select]
        unconfirmed = [row["id"] for row in chosen if str(row.get("status")) != "confirmed"]
        if unconfirmed:
            raise ValueError(
                f"S3 candidates not confirmed, refuse S4 promotion: {', '.join(unconfirmed)}"
            )
        rows = chosen
    else:
        ts_rows = [row for row in rows if str(row.get("kind")) == "ts"]
        rows = ts_rows or rows

    items: list[BatchStructureItem] = []
    for row in rows:
        candidate_id = str(row.get("id") or "")
        ref = str(row.get("optimized_xyz") or "")
        geometry = (path.parent / ref).resolve() if ref else None
        if geometry is None or not geometry.is_file():
            raise FileNotFoundError(f"S3 optimized geometry missing for {candidate_id}: {ref}")
        tag = tag_for_kind(str(row.get("kind") or ""))
        items.extend(
            _items_from_tagged_geometry(
                geometry.read_text(encoding="utf-8"),
                tag=tag,
                candidate_id=candidate_id,
                source_ref=f"{path}#{candidate_id}",
                frame_index=None,
                name=str(row.get("name") or candidate_id),
            )
        )
    return _assign_item_ids(items), payload


def load_batch_request(payload: dict[str, Any] | Path | str) -> list[BatchStructureItem]:
    """Expand a ``batch_structures_v1`` request into items.

    Accepted entry forms (batch plan §3):

    * inline structures: ``{"name", "tag", "charge", "multiplicity",
      "xyz"}`` (multi-frame XYZ text allowed);
    * S2 candidates: ``{"source_type": "s2_candidates", "manifest",
      "candidate_ids": [...]}``;
    * files: ``{"source_type": "file", "path", "tag"}`` — XYZ on disk;
    * legacy flat ``structures`` / ``manifests`` / ``files`` lists.
    """
    if isinstance(payload, (str, Path)):
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Batch request must be a JSON object")

    entries: list[dict[str, Any]] = []
    for key in ("items", "structures", "manifests", "files"):
        values = payload.get(key)
        if isinstance(values, list):
            entries.extend(value for value in values if isinstance(value, dict))
    overrides: dict[str, dict[str, Any]] = {}
    raw_overrides = payload.get("overrides")
    if isinstance(raw_overrides, dict):
        overrides = {
            str(key): value for key, value in raw_overrides.items() if isinstance(value, dict)
        }
    if not entries:
        raise ValueError("Batch request carries no structure entries")

    items: list[BatchStructureItem] = []
    for entry in entries:
        source_type = str(entry.get("source_type") or "")
        if source_type == "s2_candidates" or entry.get("manifest"):
            manifest = entry.get("manifest") or entry.get("from_artifact")
            if not manifest:
                raise ValueError("s2_candidates entry requires 'manifest'")
            ids = entry.get("candidate_ids") or entry.get("select")
            items.extend(
                load_items_from_s2_candidate_manifest(
                    Path(str(manifest)),
                    [str(cid) for cid in ids] if ids else None,
                )
            )
        elif source_type == "file" or entry.get("path"):
            file_path = Path(str(entry.get("path")))
            items.extend(
                load_items_from_xyz_file(
                    file_path,
                    source_type=str(entry.get("source_label") or "job_artifact"),
                    tag=entry.get("tag"),
                )
            )
        elif entry.get("xyz"):
            text = str(entry["xyz"])
            name = str(entry.get("name") or "mol")
            start = len(items)
            entry_items = load_items_from_xyz_text(
                text,
                source_type=str(entry.get("source_type") or "upload"),
                source_ref=str(entry.get("source_ref") or name),
                base_name=name,
            )
            if entry.get("include") is False:
                for item in entry_items:
                    item.include = False
            items.extend(entry_items)
            tag = normalize_tag(entry.get("tag"))
            if tag:
                for item in items[start:]:
                    item.tag = tag
                    item.xyz = _rewrite_comment(
                        item.xyz,
                        build_tag_title(tag, candidate_id=item.candidate_id, source="user"),
                    )
        else:
            raise ValueError(f"Batch entry has neither xyz, path nor manifest: {entry!r}")

    auto_candidate = re.compile(r"item_\d{3}(_x\d+)?")
    for item in items:
        item.item_id = ""
        if item.candidate_id and auto_candidate.fullmatch(item.candidate_id):
            item.candidate_id = ""
    _assign_item_ids(items)
    apply_user_overrides(items, overrides)
    included = [item for item in items if item.include]
    if not included:
        raise ValueError("Batch request has no included structures")
    return included
