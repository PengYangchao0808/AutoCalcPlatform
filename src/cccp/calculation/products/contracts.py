"""Versioned science-product contracts (C02-s1 / plan todo 11).

This module is the cccp-side single source of truth for the versioned
science-product envelope consumed by Python, CLI and downstream adapters.
A product is a *published view* derived from an existing typed
``TaskResult`` (see :mod:`cccp.calculation.results`) plus its artifacts — it
is deliberately **not** a parallel request/cancellation stack.

Contract skeleton
-----------------
* :data:`PRODUCT_SCHEMA_VERSION` — on-wire schema version.  The reader
  rejects newer versions and converts older ones (see
  :mod:`cccp.calculation.products.serialize`).
* :class:`ProductIdentity` — stable scientific identity.  Platform
  job/account/price identity never enters it.
* :class:`SourceRef` — maps every product item back to its raw source
  (artifact + native frame/mode/point index).
* :class:`ReferenceFrame` — coordinate axes/origin and energy zero.
* :class:`SeriesRef` / :class:`PointRecord` — stable series/point ids plus
  explicit x/y units and a :class:`MissingReason`.  These are the shared
  skeleton the curve/scatter contract (todo 12) extends.
* :class:`ScienceProduct` — envelope that keeps normal payloads separate
  from typed ``unavailable`` / ``failed`` / ``not_prepared`` states.

First product
-------------
:class:`StructureTrajectoryPayload` carries structure/frame id, ``atom_uid``,
element, coordinate units, topology source and frame order / block index.
Its real generating function, timing, identity rule, digest rule,
reader/writer, old-version conversion and failure reasons are declared in
:mod:`cccp.calculation.products.registry`.

No silent zero-fill
-------------------
A field that a source does not provide is represented by an explicit
:class:`MissingReason` and a ``None``/empty value — never by ``0``, ``""`` or
an invented id.  Structurally corrupt payloads raise
:class:`ProductSchemaError`; partial payloads stay usable and mark each
missing sub-item.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from cccp.calculation.contracts import (
    JsonObject,
    ensure_finite_json_number,
    parse_int_strict,
    parse_str_strict,
)
from cccp.calculation.errors import TaskInputError

__all__ = [
    "LEGACY_PRODUCT_SCHEMA_VERSION",
    "PRODUCT_SCHEMA_VERSION",
    "CoordinateAtom",
    "CoordinateUnits",
    "EnergyUnits",
    "FrameRecord",
    "FrequencyUnits",
    "MissingReason",
    "PointRecord",
    "ProductIdentity",
    "ProductKind",
    "ProductPayload",
    "ProductSchemaError",
    "ProductState",
    "ReferenceFrame",
    "ScienceProduct",
    "SeriesRef",
    "SourceRef",
    "StructureTrajectoryPayload",
    "compute_input_digest",
    "frame_stable_id",
    "point_stable_id",
    "series_stable_id",
]

#: Current on-wire schema version for every science product.
PRODUCT_SCHEMA_VERSION = 1
#: Legacy pre-versioning schema the reader migrates from (see ``serialize``).
LEGACY_PRODUCT_SCHEMA_VERSION = 0


class ProductSchemaError(TaskInputError):
    """Structurally corrupt science-product payload.

    Raised when a payload is malformed (wrong JSON type, unknown enum,
    non-finite float, missing a required structural field).  A *partial*
    product — structurally valid but missing a science field — is **not** an
    error: it yields an explicit :class:`MissingReason` instead.  Subclasses
    :class:`~cccp.calculation.errors.TaskInputError` so existing
    input-error handling keeps working.
    """


class ProductKind(str, Enum):
    """Closed union of science-product kinds.

    ``todo 12`` extends this with ``curve`` / ``vibration`` / ``derived``
    kinds; each new kind must register a
    :class:`~cccp.calculation.products.registry.ProductSpec`.
    """

    STRUCTURE_TRAJECTORY = "structure_trajectory"


class ProductState(str, Enum):
    """Typed envelope state, kept strictly separate from payloads.

    ``AVAILABLE`` carries a payload; the other three carry an explicit
    :class:`MissingReason` (and, for ``FAILED``, at least one error string).
    A partial-but-usable product is ``AVAILABLE`` with ``complete=False`` and
    per-item missing reasons — partial is never a global state.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    NOT_PREPARED = "not_prepared"


class MissingReason(str, Enum):
    """Fine-grained, closed vocabulary for missing/unavailable science data.

    Every reason is explicit; consumers must never substitute a zero value.
    """

    # Product/envelope level.
    SOURCE_MISSING = "source_missing"
    NOT_PREPARED = "not_prepared"
    UNSUPPORTED = "unsupported"
    PARSE_FAILURE = "parse_failure"
    PARTIAL_SOURCE = "partial_source"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    # Structure/trajectory item level.
    MISSING_FRAME_ID = "missing_frame_id"
    MISSING_ATOM_UID = "missing_atom_uid"
    MISSING_ELEMENT = "missing_element"
    MISSING_COORDINATES = "missing_coordinates"


class CoordinateUnits(str, Enum):
    """Accepted coordinate units (explicit — no implicit default)."""

    ANGSTROM = "angstrom"
    BOHR = "bohr"
    NANOMETER = "nanometer"


class EnergyUnits(str, Enum):
    """Accepted energy units (used by the series skeleton; todo 12)."""

    HARTREE = "hartree"
    KCAL_PER_MOL = "kcal_mol"
    KJ_PER_MOL = "kj_mol"


class FrequencyUnits(str, Enum):
    """Accepted frequency units (vibration product; todo 12)."""

    CM1 = "cm-1"
    THZ = "thz"


def compute_input_digest(raw_input: object) -> str:
    """Return ``sha256:<hex>`` over the canonical JSON of ``raw_input``.

    Canonicalisation (sorted keys, ASCII, compact separators) makes the
    digest independent of mapping order.  The digest is part of
    :class:`ProductIdentity`; it never encodes platform identity.
    """
    canonical = json.dumps(raw_input, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── stable ids ──────────────────────────────────────────────────────────


def series_stable_id(product_kind: ProductKind, series_key: str) -> str:
    """Stable id for one series inside ``product_kind``."""
    return f"{ProductKind(product_kind).value}:series:{series_key}"


def point_stable_id(series_id: str, point_index: int) -> str:
    """Stable id for one point inside ``series_id`` (index is original)."""
    return f"{series_id}:point:{int(point_index)}"


def frame_stable_id(product_kind: ProductKind, frame_index: int) -> str:
    """Stable id for one structure frame (index is original, never renumbered)."""
    return f"{ProductKind(product_kind).value}:frame:{int(frame_index)}"


# ── source mapping / reference frame ────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Mapping from a product item back to its raw source.

    ``artifact`` is a relative path (never an absolute platform location);
    ``native_index`` is the index in the *source* numbering space (native
    frame / mode / point index), so a re-projection never renumbers it.
    """

    source_kind: str
    artifact: str = ""
    native_index: int | None = None
    location: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            message = "source_kind must be a non-empty string"
            raise ProductSchemaError(message)
        object.__setattr__(self, "source_kind", self.source_kind)
        object.__setattr__(self, "artifact", str(self.artifact))
        object.__setattr__(self, "location", str(self.location))
        if self.native_index is not None:
            if isinstance(self.native_index, bool) or not isinstance(self.native_index, int):
                message = "native_index must be an int"
                raise ProductSchemaError(message)
            object.__setattr__(self, "native_index", int(self.native_index))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"source_kind": self.source_kind, "artifact": self.artifact}
        if self.native_index is not None:
            payload["native_index"] = self.native_index
        if self.location:
            payload["location"] = self.location
        return payload

    @classmethod
    def from_dict(cls, payload: object | None) -> SourceRef | None:
        """Parse strictly; missing mapping yields ``None``."""
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            message = "source must be a mapping"
            raise ProductSchemaError(message)
        source_kind = parse_str_strict(payload, "source_kind")
        if source_kind is None:
            message = "source.source_kind is required"
            raise ProductSchemaError(message)
        return cls(
            source_kind=source_kind,
            artifact=parse_str_strict(payload, "artifact") or "",
            native_index=parse_int_strict(payload, "native_index"),
            location=parse_str_strict(payload, "location") or "",
        )


@dataclass(frozen=True, slots=True)
class ReferenceFrame:
    """Scientific reference frame: axes/origin plus the energy zero.

    Coordinate products state their axes and origin; energy-bearing
    products state the ``energy_zero`` (e.g. ``electronic_ground_state``).
    Absent values stay ``None`` rather than defaulting silently.
    """

    axes: str = "cartesian"
    origin: str = "native_input"
    energy_zero: str | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"axes": self.axes, "origin": self.origin}
        if self.energy_zero is not None:
            payload["energy_zero"] = self.energy_zero
        return payload

    @classmethod
    def from_dict(cls, payload: object | None) -> ReferenceFrame:
        """Parse strictly; a missing mapping yields the documented default."""
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            message = "reference_frame must be a mapping"
            raise ProductSchemaError(message)
        return cls(
            axes=parse_str_strict(payload, "axes") or "cartesian",
            origin=parse_str_strict(payload, "origin") or "native_input",
            energy_zero=parse_str_strict(payload, "energy_zero"),
        )


# ── series / point skeleton (extended by todo 12) ───────────────────────


@dataclass(frozen=True, slots=True)
class SeriesRef:
    """Stable identity and units of one numeric series."""

    series_id: str
    x_unit: str
    y_unit: str
    reference_frame: ReferenceFrame = field(default_factory=ReferenceFrame)
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, str) or not self.series_id.strip():
            message = "series_id must be a non-empty string"
            raise ProductSchemaError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "series_id": self.series_id,
            "x_unit": self.x_unit,
            "y_unit": self.y_unit,
            "reference_frame": self.reference_frame.to_dict(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> SeriesRef:
        """Parse strictly; missing required ids raise."""
        series_id = parse_str_strict(payload, "series_id")
        if series_id is None:
            message = "series.series_id is required"
            raise ProductSchemaError(message)
        return cls(
            series_id=series_id,
            x_unit=parse_str_strict(payload, "x_unit") or "",
            y_unit=parse_str_strict(payload, "y_unit") or "",
            reference_frame=ReferenceFrame.from_dict(payload.get("reference_frame")),
            label=parse_str_strict(payload, "label") or "",
        )


@dataclass(frozen=True, slots=True)
class PointRecord:
    """One series point: stable id, x/y and an explicit missing reason.

    An available point has both ``x`` and ``y`` and no reason; an
    unavailable point carries a reason and **no** fabricated x/y.
    """

    point_id: str
    x: float | None = None
    y: float | None = None
    source: SourceRef | None = None
    missing_reason: MissingReason | None = None

    def __post_init__(self) -> None:
        if self.missing_reason is not None:
            object.__setattr__(self, "missing_reason", MissingReason(self.missing_reason))
            if self.x is not None or self.y is not None:
                message = "a point with missing_reason must not carry x/y values"
                raise ProductSchemaError(message)
        elif self.x is None or self.y is None:
            message = "an available point requires both x and y"
            raise ProductSchemaError(message)

    @property
    def available(self) -> bool:
        """Whether this point carries a value (as opposed to a reason)."""
        return self.missing_reason is None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"point_id": self.point_id}
        if self.missing_reason is not None:
            payload["missing_reason"] = self.missing_reason.value
        else:
            payload["x"] = self.x
            payload["y"] = self.y
        if self.source is not None:
            payload["source"] = self.source.to_dict()
        return payload


# ── product identity ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    """Stable scientific identity of one product.

    Platform job / account / price identity is deliberately absent: the
    identity is the product kind, schema version, generating function,
    raw-input digest and algorithm/params digests only.
    """

    product_kind: ProductKind
    schema_version: int = PRODUCT_SCHEMA_VERSION
    generator: str = ""
    input_digest: str = ""
    algorithm: str = ""
    algorithm_version: str = ""
    params_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_kind", ProductKind(self.product_kind))
        object.__setattr__(self, "schema_version", int(self.schema_version))
        if self.schema_version != PRODUCT_SCHEMA_VERSION:
            message = (
                f"ProductIdentity.schema_version {self.schema_version} is not the "
                f"current contract version {PRODUCT_SCHEMA_VERSION}"
            )
            raise ProductSchemaError(message)
        object.__setattr__(self, "generator", str(self.generator))
        object.__setattr__(self, "input_digest", str(self.input_digest))
        object.__setattr__(self, "algorithm", str(self.algorithm))
        object.__setattr__(self, "algorithm_version", str(self.algorithm_version))
        object.__setattr__(self, "params_digest", str(self.params_digest))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "product_kind": self.product_kind.value,
            "schema_version": self.schema_version,
            "generator": self.generator,
            "input_digest": self.input_digest,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "params_digest": self.params_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ProductIdentity:
        """Parse strictly; missing kinds raise."""
        if not isinstance(payload, Mapping):
            message = "identity must be a mapping"
            raise ProductSchemaError(message)
        kind = parse_str_strict(payload, "product_kind")
        if kind is None:
            message = "identity.product_kind is required"
            raise ProductSchemaError(message)
        try:
            product_kind = ProductKind(kind)
        except ValueError as exc:
            allowed = ", ".join(k.value for k in ProductKind)
            message = f"identity.product_kind must be one of: {allowed}"
            raise ProductSchemaError(message) from exc
        version = payload.get("schema_version", PRODUCT_SCHEMA_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            message = "identity.schema_version must be an integer"
            raise ProductSchemaError(message)
        try:
            return cls(
                product_kind=product_kind,
                schema_version=version,
                generator=parse_str_strict(payload, "generator") or "",
                input_digest=parse_str_strict(payload, "input_digest") or "",
                algorithm=parse_str_strict(payload, "algorithm") or "",
                algorithm_version=parse_str_strict(payload, "algorithm_version") or "",
                params_digest=parse_str_strict(payload, "params_digest") or "",
            )
        except ProductSchemaError:
            raise
        except (TaskInputError, ValueError) as exc:
            raise ProductSchemaError(str(exc)) from exc


# ── structure / trajectory product (first product) ──────────────────────


@dataclass(frozen=True, slots=True)
class CoordinateAtom:
    """One atom in a frame: stable ``atom_uid``, element, coordinate."""

    atom_uid: str
    element: str
    coordinate: tuple[float, float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.atom_uid, str) or not self.atom_uid.strip():
            message = "atom_uid must be a non-empty string"
            raise ProductSchemaError(message)
        if not isinstance(self.element, str) or not self.element.strip():
            message = "element must be a non-empty string"
            raise ProductSchemaError(message)
        coordinate = self.coordinate
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 3:
            message = "coordinate must be a 3-vector"
            raise ProductSchemaError(message)
        object.__setattr__(
            self,
            "coordinate",
            (
                ensure_finite_json_number(coordinate[0], "coordinate[0]"),
                ensure_finite_json_number(coordinate[1], "coordinate[1]"),
                ensure_finite_json_number(coordinate[2], "coordinate[2]"),
            ),
        )

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "atom_uid": self.atom_uid,
            "element": self.element,
            "coordinate": [float(c) for c in self.coordinate],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CoordinateAtom:
        """Parse strictly; missing/wrong fields raise."""
        if not isinstance(payload, Mapping):
            message = "atom entry must be a mapping"
            raise ProductSchemaError(message)
        atom_uid = parse_str_strict(payload, "atom_uid")
        element = parse_str_strict(payload, "element")
        if atom_uid is None or element is None:
            message = "atom requires both 'atom_uid' and 'element'"
            raise ProductSchemaError(message)
        raw = payload.get("coordinate")
        if not isinstance(raw, list) or len(raw) != 3:
            message = "atom.coordinate must be a 3-vector"
            raise ProductSchemaError(message)
        try:
            coordinate = (
                ensure_finite_json_number(raw[0], "coordinate[0]"),
                ensure_finite_json_number(raw[1], "coordinate[1]"),
                ensure_finite_json_number(raw[2], "coordinate[2]"),
            )
        except TaskInputError as exc:
            raise ProductSchemaError(str(exc)) from exc
        return cls(atom_uid=atom_uid, element=element, coordinate=coordinate)


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """One trajectory frame.

    Invariant: an available frame (``missing_reason is None``) carries at
    least one atom; an unavailable frame carries an explicit reason and an
    empty atom tuple — a missing ``atom_uid`` drops the whole frame rather
    than zero-filling a fabricated id.
    """

    frame_index: int
    coordinate_units: CoordinateUnits
    topology_source: str
    frame_id: str | None = None
    atoms: tuple[CoordinateAtom, ...] = ()
    block_index: int | None = None
    label: str = ""
    source: SourceRef | None = None
    missing_reason: MissingReason | None = None

    def __post_init__(self) -> None:
        if self.frame_id is not None:
            if not isinstance(self.frame_id, str) or not self.frame_id.strip():
                message = "frame_id must be a non-empty string when present"
                raise ProductSchemaError(message)
        object.__setattr__(self, "frame_index", int(self.frame_index))
        object.__setattr__(self, "coordinate_units", CoordinateUnits(self.coordinate_units))
        if not isinstance(self.topology_source, str) or not self.topology_source.strip():
            message = "topology_source must be a non-empty string"
            raise ProductSchemaError(message)
        object.__setattr__(self, "atoms", tuple(self.atoms))
        object.__setattr__(self, "label", str(self.label))
        if self.block_index is not None:
            object.__setattr__(self, "block_index", int(self.block_index))
        if self.missing_reason is not None:
            object.__setattr__(self, "missing_reason", MissingReason(self.missing_reason))
            if self.atoms:
                message = (
                    "a frame with missing_reason must not carry atoms "
                    "(missing data must never be zero-filled)"
                )
                raise ProductSchemaError(message)
        else:
            if self.frame_id is None:
                message = "an available frame requires a frame_id"
                raise ProductSchemaError(message)
            if not self.atoms:
                message = "an available frame requires at least one atom"
                raise ProductSchemaError(message)

    @property
    def available(self) -> bool:
        """Whether this frame carries geometry (as opposed to a reason)."""
        return self.missing_reason is None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "coordinate_units": self.coordinate_units.value,
            "topology_source": self.topology_source,
            "atoms": [atom.to_dict() for atom in self.atoms],
        }
        if self.block_index is not None:
            payload["block_index"] = self.block_index
        if self.label:
            payload["label"] = self.label
        if self.source is not None:
            payload["source"] = self.source.to_dict()
        if self.missing_reason is not None:
            payload["missing_reason"] = self.missing_reason.value
        return payload


@dataclass(frozen=True, slots=True)
class StructureTrajectoryPayload:
    """Structure/trajectory product payload (first science product).

    Fields per the §7.3 structure/trajectory row: structure/frame id,
    ``atom_uid``, element, coordinate units, topology source, and frame
    order / block index.  Frames keep their original order; a frame with a
    missing sub-item is retained with an explicit reason.
    """

    coordinate_units: CoordinateUnits
    topology_source: str
    frames: tuple[FrameRecord, ...] = ()
    atom_order: tuple[str, ...] = ()
    reference_frame: ReferenceFrame = field(default_factory=ReferenceFrame)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinate_units", CoordinateUnits(self.coordinate_units))
        if not isinstance(self.topology_source, str) or not self.topology_source.strip():
            message = "topology_source must be a non-empty string"
            raise ProductSchemaError(message)
        object.__setattr__(self, "frames", tuple(self.frames))
        object.__setattr__(self, "atom_order", tuple(str(uid) for uid in self.atom_order))

    def is_complete(self) -> bool:
        """Whether every frame carries geometry."""
        return all(frame.missing_reason is None for frame in self.frames)

    def missing_frames(self) -> tuple[FrameRecord, ...]:
        """Frames that carry an explicit missing reason."""
        return tuple(frame for frame in self.frames if frame.missing_reason is not None)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "coordinate_units": self.coordinate_units.value,
            "topology_source": self.topology_source,
            "atom_order": list(self.atom_order),
            "reference_frame": self.reference_frame.to_dict(),
            "frames": [frame.to_dict() for frame in self.frames],
        }


#: Union of product payloads.  ``todo 12`` widens this with the
#: curve/scatter, vibration and derived-product payloads.
ProductPayload: TypeAlias = StructureTrajectoryPayload


# ── envelope ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScienceProduct:
    """Versioned science-product envelope.

    Normal payloads (``state == AVAILABLE``) are strictly separated from the
    typed ``UNAVAILABLE`` / ``FAILED`` / ``NOT_PREPARED`` states: a
    non-available product carries an explicit :class:`MissingReason` and no
    payload, so consumers can never mistake absence for a zero-valued
    result.  A partial-but-usable product is ``AVAILABLE`` with
    ``complete=False`` and per-item reasons.
    """

    identity: ProductIdentity
    state: ProductState
    payload: ProductPayload | None = None
    complete: bool = True
    missing_reason: MissingReason | None = None
    errors: tuple[str, ...] = ()
    migrations: tuple[str, ...] = ()
    schema_version: int = PRODUCT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProductIdentity):
            message = "identity must be a ProductIdentity"
            raise ProductSchemaError(message)
        state = ProductState(self.state)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "schema_version", int(self.schema_version))
        if self.schema_version != PRODUCT_SCHEMA_VERSION:
            message = (
                f"ScienceProduct.schema_version {self.schema_version} is not the "
                f"current contract version {PRODUCT_SCHEMA_VERSION}"
            )
            raise ProductSchemaError(message)
        object.__setattr__(self, "complete", bool(self.complete))
        object.__setattr__(self, "errors", tuple(str(e) for e in self.errors))
        object.__setattr__(self, "migrations", tuple(str(m) for m in self.migrations))
        if self.payload is not None and not isinstance(self.payload, StructureTrajectoryPayload):
            message = f"unsupported payload type {type(self.payload).__name__}"
            raise ProductSchemaError(message)
        if self.missing_reason is not None:
            object.__setattr__(self, "missing_reason", MissingReason(self.missing_reason))

        if state is ProductState.AVAILABLE:
            if self.payload is None:
                message = "an available product requires a payload"
                raise ProductSchemaError(message)
            if self.missing_reason is not None:
                message = "an available product must not carry a product-level missing_reason"
                raise ProductSchemaError(message)
            if not self.complete and self.payload.is_complete():
                message = "complete=False requires at least one missing sub-item"
                raise ProductSchemaError(message)
            if self.complete and not self.payload.is_complete():
                message = (
                    "a payload with missing frames must be marked complete=False "
                    "(partial products must declare their missing items)"
                )
                raise ProductSchemaError(message)
        elif state is ProductState.FAILED:
            if self.payload is not None:
                message = "a failed product must not carry a payload"
                raise ProductSchemaError(message)
            if not self.errors:
                message = "a failed product requires at least one error string"
                raise ProductSchemaError(message)
            if self.missing_reason is None:
                object.__setattr__(self, "missing_reason", MissingReason.PARSE_FAILURE)
        else:  # UNAVAILABLE / NOT_PREPARED
            if self.payload is not None:
                message = f"a {state.value} product must not carry a payload"
                raise ProductSchemaError(message)
            if self.missing_reason is None:
                message = f"a {state.value} product requires an explicit missing_reason"
                raise ProductSchemaError(message)

    @property
    def available(self) -> bool:
        """Whether this product carries usable data."""
        return self.state is ProductState.AVAILABLE

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "schema_version": self.schema_version,
            "product_kind": self.identity.product_kind.value,
            "identity": self.identity.to_dict(),
            "state": self.state.value,
            "complete": self.complete,
            "errors": list(self.errors),
        }
        if self.missing_reason is not None:
            payload["missing_reason"] = self.missing_reason.value
        if self.migrations:
            payload["migrations"] = list(self.migrations)
        if self.payload is not None:
            payload["payload"] = self.payload.to_dict()
        return payload

    @classmethod
    def make_available(
        cls,
        *,
        identity: ProductIdentity,
        payload: StructureTrajectoryPayload,
        migrations: Sequence[str] = (),
    ) -> ScienceProduct:
        """Build an available product, deriving ``complete`` from the payload."""
        return cls(
            identity=identity,
            state=ProductState.AVAILABLE,
            payload=payload,
            complete=payload.is_complete(),
            migrations=tuple(migrations),
        )

    @classmethod
    def make_unavailable(
        cls,
        *,
        identity: ProductIdentity,
        missing_reason: MissingReason,
        state: ProductState = ProductState.UNAVAILABLE,
        errors: Sequence[str] = (),
    ) -> ScienceProduct:
        """Build a non-available product with an explicit reason."""
        return cls(
            identity=identity,
            state=state,
            payload=None,
            complete=False,
            missing_reason=missing_reason,
            errors=tuple(errors),
        )
