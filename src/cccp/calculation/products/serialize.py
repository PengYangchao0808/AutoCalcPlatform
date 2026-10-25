"""Reader/writer for the versioned science-product contract (plan todo 11).

The reader (:func:`product_from_dict`) is strict about structure but lenient
about *science* completeness:

* a structurally corrupt payload (wrong JSON type, unknown enum,
  non-finite float, missing a required structural field) raises
  :class:`~cccp.calculation.products.contracts.ProductSchemaError`;
* a structurally valid payload that is missing a science field (e.g. an
  atom without ``atom_uid``) becomes an explicit
  :class:`~cccp.calculation.products.contracts.MissingReason` — never a
  zero-filled value.

Older schema versions are routed through an explicit conversion path
(:func:`migrate_v0_to_v1`); newer versions are rejected.  The writer
(:func:`product_to_dict`) emits the current version and is the inverse of
the reader for v1 payloads.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

from cccp.calculation.contracts import (
    JsonObject,
    parse_bool_strict,
    parse_int_strict,
    parse_str_strict,
    parse_str_tuple_strict,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.products.contracts import (
    LEGACY_PRODUCT_SCHEMA_VERSION,
    PRODUCT_SCHEMA_VERSION,
    CoordinateAtom,
    CoordinateUnits,
    CurveScatterPayload,
    DerivedPayload,
    FrameRecord,
    MissingReason,
    ProductIdentity,
    ProductKind,
    ProductPayload,
    ProductSchemaError,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    SourceRef,
    StructureTrajectoryPayload,
    VibrationPayload,
    compute_input_digest,
    frame_stable_id,
)

__all__ = [
    "migrate_v0_to_v1",
    "product_from_dict",
    "product_to_dict",
]

_E = TypeVar("_E", bound=Enum)


def _enum(enum_cls: type[_E], value: object, label: str) -> _E:
    """Strict enum parse that raises :class:`ProductSchemaError`."""
    if not isinstance(value, str):
        message = f"{label} must be a string, got {type(value).__name__}"
        raise ProductSchemaError(message)
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        message = f"{label} must be one of: {allowed}; got {value!r}"
        raise ProductSchemaError(message) from exc


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        message = f"{label} must be a mapping, got {type(value).__name__}"
        raise ProductSchemaError(message)
    return value


def _required_str(payload: Mapping[str, object], key: str, label: str) -> str:
    value = parse_str_strict(payload, key)
    if value is None:
        message = f"{label}.{key} is required"
        raise ProductSchemaError(message)
    return value


def product_to_dict(product: ScienceProduct) -> JsonObject:
    """Serialise a :class:`ScienceProduct` to a JSON-safe dict (current version)."""
    if not isinstance(product, ScienceProduct):
        message = f"expected ScienceProduct, got {type(product).__name__}"
        raise ProductSchemaError(message)
    return product.to_dict()


def product_from_dict(payload: Mapping[str, object]) -> ScienceProduct:
    """Parse a science-product payload, converting older schema versions.

    Raises:
        ProductSchemaError: On a structurally corrupt payload or an
            unsupported (newer) schema version.
    """
    mapping = _as_mapping(payload, "product")
    version_raw = mapping.get("schema_version")
    if version_raw is None:
        # Legacy documents used a bare ``version`` key.
        version_raw = mapping.get("version")
    if version_raw is None:
        message = "product.schema_version is required"
        raise ProductSchemaError(message)
    if isinstance(version_raw, bool) or not isinstance(version_raw, int):
        message = "product.schema_version must be an integer"
        raise ProductSchemaError(message)
    version = int(version_raw)
    try:
        if version == PRODUCT_SCHEMA_VERSION:
            return _parse_v1(mapping)
        if version == LEGACY_PRODUCT_SCHEMA_VERSION:
            return migrate_v0_to_v1(mapping)
    except ProductSchemaError:
        raise
    except TaskInputError as exc:
        # Any strict-parse rejection is a corrupt payload, not a science gap.
        raise ProductSchemaError(str(exc)) from exc
    message = (
        f"unsupported science-product schema_version {version}; "
        f"current is {PRODUCT_SCHEMA_VERSION} (legacy {LEGACY_PRODUCT_SCHEMA_VERSION})"
    )
    raise ProductSchemaError(message)


# ── v1 parsing ──────────────────────────────────────────────────────────


def _parse_v1(payload: Mapping[str, object]) -> ScienceProduct:
    kind = _enum(
        ProductKind,
        _required_str(payload, "product_kind", "product"),
        "product.product_kind",
    )
    identity = ProductIdentity.from_dict(_as_mapping(payload.get("identity"), "identity"))
    if identity.product_kind is not kind:
        message = (
            "identity.product_kind disagrees with product.product_kind "
            f"({identity.product_kind.value!r} != {kind.value!r})"
        )
        raise ProductSchemaError(message)
    state = _enum(ProductState, payload.get("state"), "product.state")
    complete_raw = parse_bool_strict(payload, "complete")
    errors = parse_str_tuple_strict(payload, "errors")
    migrations = parse_str_tuple_strict(payload, "migrations")
    missing_reason: MissingReason | None = None
    raw_reason = payload.get("missing_reason")
    if raw_reason is not None:
        missing_reason = _enum(MissingReason, raw_reason, "product.missing_reason")

    payload_dict: ProductPayload | None = None
    raw_payload = payload.get("payload")
    if raw_payload is not None:
        payload_dict = _parse_payload(kind, _as_mapping(raw_payload, "product.payload"))

    return ScienceProduct(
        identity=identity,
        state=state,
        payload=payload_dict,
        complete=True if complete_raw is None else complete_raw,
        missing_reason=missing_reason,
        errors=errors,
        migrations=migrations,
    )


def _parse_payload(kind: ProductKind, raw: Mapping[str, object]) -> ProductPayload:
    """Dispatch a payload document to its kind-specific strict parser."""
    if kind is ProductKind.STRUCTURE_TRAJECTORY:
        return _parse_structure_trajectory(raw)
    if kind is ProductKind.CURVE_SCATTER:
        return CurveScatterPayload.from_dict(raw)
    if kind is ProductKind.VIBRATION:
        return VibrationPayload.from_dict(raw)
    if kind is ProductKind.DERIVED:
        return DerivedPayload.from_dict(raw)
    message = f"no payload parser registered for product_kind {kind.value!r}"
    raise ProductSchemaError(message)


def _parse_structure_trajectory(raw: Mapping[str, object]) -> StructureTrajectoryPayload:
    units_value = raw.get("coordinate_units")
    if units_value is None:
        message = "payload.coordinate_units is required (no implicit default)"
        raise ProductSchemaError(message)
    coordinate_units = _enum(CoordinateUnits, units_value, "payload.coordinate_units")
    topology_source = _required_str(raw, "topology_source", "payload")
    atom_order = parse_str_tuple_strict(raw, "atom_order")
    reference_frame = ReferenceFrame.from_dict(raw.get("reference_frame"))

    raw_frames = raw.get("frames")
    if not isinstance(raw_frames, list):
        message = "payload.frames must be a list"
        raise ProductSchemaError(message)
    frames: list[FrameRecord] = []
    for index, entry in enumerate(raw_frames):
        frames.append(
            _parse_frame(
                _as_mapping(entry, f"payload.frames[{index}]"),
                default_units=coordinate_units,
                default_topology=topology_source,
            )
        )
    return StructureTrajectoryPayload(
        coordinate_units=coordinate_units,
        topology_source=topology_source,
        frames=tuple(frames),
        atom_order=atom_order,
        reference_frame=reference_frame,
    )


def _frame_units(raw: Mapping[str, object], default: CoordinateUnits) -> CoordinateUnits:
    value = raw.get("coordinate_units")
    if value is None:
        return default
    return _enum(CoordinateUnits, value, "frame.coordinate_units")


def _frame_topology(raw: Mapping[str, object], default: str) -> str:
    value = parse_str_strict(raw, "topology_source")
    return default if value is None else value


def _parse_frame(
    raw: Mapping[str, object],
    *,
    default_units: CoordinateUnits,
    default_topology: str,
) -> FrameRecord:
    if "frame_index" not in raw:
        message = "frame.frame_index is required (ordering identity)"
        raise ProductSchemaError(message)
    frame_index = parse_int_strict(raw, "frame_index")
    if frame_index is None:
        message = "frame.frame_index is required (ordering identity)"
        raise ProductSchemaError(message)
    units = _frame_units(raw, default_units)
    topology = _frame_topology(raw, default_topology)
    frame_id = parse_str_strict(raw, "frame_id")
    block_index = parse_int_strict(raw, "block_index")
    label = parse_str_strict(raw, "label") or ""
    source = SourceRef.from_dict(raw.get("source"))
    source_ref = source

    explicit_reason = raw.get("missing_reason")
    if explicit_reason is not None:
        reason = _enum(MissingReason, explicit_reason, "frame.missing_reason")
        if raw.get("atoms"):
            message = "a frame with missing_reason must not carry atoms"
            raise ProductSchemaError(message)
        return FrameRecord(
            frame_index=frame_index,
            coordinate_units=units,
            topology_source=topology,
            frame_id=frame_id,
            atoms=(),
            block_index=block_index,
            label=label,
            source=source_ref,
            missing_reason=reason,
        )

    raw_atoms = raw.get("atoms")
    if not isinstance(raw_atoms, list):
        message = "frame.atoms must be a list"
        raise ProductSchemaError(message)
    derived_reason = _frame_missing_reason(raw_atoms)
    if derived_reason is not None:
        # Explicit reason, no fabricated id/atoms (missing must never zero-fill).
        return FrameRecord(
            frame_index=frame_index,
            coordinate_units=units,
            topology_source=topology,
            frame_id=frame_id,
            atoms=(),
            block_index=block_index,
            label=label,
            source=source_ref,
            missing_reason=derived_reason,
        )
    atoms = tuple(
        CoordinateAtom.from_dict(_as_mapping(entry, "frame.atoms[]")) for entry in raw_atoms
    )
    return FrameRecord(
        frame_index=frame_index,
        coordinate_units=units,
        topology_source=topology,
        frame_id=frame_id,
        atoms=atoms,
        block_index=block_index,
        label=label,
        source=source_ref,
    )


def _frame_missing_reason(raw_atoms: list[object]) -> MissingReason | None:
    """First missing science field among a frame's atoms, or ``None``.

    Wrong types raise (corrupt); only *absent* fields map to a reason.  The
    priority is deterministic: ``missing_atom_uid`` then ``missing_element``
    then ``missing_coordinates``.
    """
    if not raw_atoms:
        return MissingReason.MISSING_COORDINATES
    reason: MissingReason | None = None
    for entry in raw_atoms:
        atom = _as_mapping(entry, "frame.atoms[]")
        if "atom_uid" not in atom or "element" not in atom or "coordinate" not in atom:
            if "atom_uid" not in atom:
                reason = reason or MissingReason.MISSING_ATOM_UID
            if "element" not in atom:
                reason = reason or MissingReason.MISSING_ELEMENT
            if "coordinate" not in atom:
                reason = reason or MissingReason.MISSING_COORDINATES
            continue
        # Present fields must be well-typed even before we accept the frame.
        _required_str(atom, "atom_uid", "atom")
        _required_str(atom, "element", "atom")
    return reason


# ── legacy v0 → v1 conversion ───────────────────────────────────────────


def migrate_v0_to_v1(payload: Mapping[str, object]) -> ScienceProduct:
    """Convert a legacy (v0) trajectory document to the current contract.

    Legacy shape (pre-versioning)::

        {"version": 0, "product_kind": "structure_trajectory",
         "generator": "...", "input_digest": "...",
         "frames": [{"index": 0, "symbols": ["C", "H"],
                     "coordinates": [[x, y, z], ...], "energy_hartree": -40.1}]}

    Conversions (all recorded in ``ScienceProduct.migrations`` — never
    silent): ``atom_uid`` is derived with the documented ``element:rank``
    scheme (rank = per-element source-order occurrence), coordinates are
    declared ``angstrom`` (the documented trajectory convention) and the
    topology source is marked ``legacy_v0``.  Frames are never zero-filled;
    a frame that cannot supply its symbols/coordinates gets an explicit
    :class:`MissingReason`.
    """
    mapping = _as_mapping(payload, "legacy product")
    kind = _enum(
        ProductKind,
        _required_str(mapping, "product_kind", "legacy product"),
        "legacy product.product_kind",
    )
    if kind is not ProductKind.STRUCTURE_TRAJECTORY:
        message = f"legacy migration is only defined for structure_trajectory, got {kind.value!r}"
        raise ProductSchemaError(message)
    raw_frames = mapping.get("frames")
    if not isinstance(raw_frames, list):
        message = "legacy product.frames must be a list"
        raise ProductSchemaError(message)

    generator = parse_str_strict(mapping, "generator") or "legacy.v0"
    raw_digest = parse_str_strict(mapping, "input_digest")
    input_digest = raw_digest or compute_input_digest(raw_frames)

    migrations: list[str] = [
        "v0->v1: atom_uid derived as element:rank (per-element source order)",
        "v0->v1: coordinate_units defaulted to angstrom (documented trajectory convention)",
        "v0->v1: topology_source set to legacy_v0",
    ]
    frames: list[FrameRecord] = []
    for index, entry in enumerate(raw_frames):
        frames.append(_migrate_v0_frame(_as_mapping(entry, f"legacy frames[{index}]")))

    structure = StructureTrajectoryPayload(
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="legacy_v0",
        frames=tuple(frames),
        atom_order=next(
            (tuple(atom.atom_uid for atom in frame.atoms) for frame in frames if frame.available),
            (),
        ),
    )
    identity = ProductIdentity(
        product_kind=kind,
        generator=generator,
        input_digest=input_digest,
        algorithm="legacy_v0_to_v1",
        algorithm_version="1",
    )
    return ScienceProduct.make_available(
        identity=identity, payload=structure, migrations=migrations
    )


def _migrate_v0_frame(raw: Mapping[str, object]) -> FrameRecord:
    if "index" not in raw:
        message = "legacy frame.index is required"
        raise ProductSchemaError(message)
    frame_index = parse_int_strict(raw, "index")
    if frame_index is None:
        message = "legacy frame.index is required"
        raise ProductSchemaError(message)
    frame_id = frame_stable_id(ProductKind.STRUCTURE_TRAJECTORY, frame_index)
    symbols_raw = raw.get("symbols")
    coords_raw = raw.get("coordinates")
    if not isinstance(symbols_raw, list) or not isinstance(coords_raw, list):
        return FrameRecord(
            frame_index=frame_index,
            coordinate_units=CoordinateUnits.ANGSTROM,
            topology_source="legacy_v0",
            frame_id=None,
            missing_reason=MissingReason.MISSING_COORDINATES,
        )
    if len(symbols_raw) != len(coords_raw) or not symbols_raw:
        return FrameRecord(
            frame_index=frame_index,
            coordinate_units=CoordinateUnits.ANGSTROM,
            topology_source="legacy_v0",
            frame_id=None,
            missing_reason=MissingReason.MISSING_COORDINATES,
        )
    counts: dict[str, int] = {}
    atoms: list[CoordinateAtom] = []
    for atom_index, (symbol, coord) in enumerate(zip(symbols_raw, coords_raw, strict=True)):
        if not isinstance(symbol, str) or not symbol.strip():
            return FrameRecord(
                frame_index=frame_index,
                coordinate_units=CoordinateUnits.ANGSTROM,
                topology_source="legacy_v0",
                frame_id=None,
                missing_reason=MissingReason.MISSING_ELEMENT,
            )
        if not isinstance(coord, list) or len(coord) != 3:
            return FrameRecord(
                frame_index=frame_index,
                coordinate_units=CoordinateUnits.ANGSTROM,
                topology_source="legacy_v0",
                frame_id=None,
                missing_reason=MissingReason.MISSING_COORDINATES,
            )
        try:
            atom = CoordinateAtom.from_dict(
                {
                    "atom_uid": f"{symbol}:{counts.get(symbol, 0)}",
                    "element": symbol,
                    "coordinate": coord,
                }
            )
        except TaskInputError as exc:
            raise ProductSchemaError(
                f"legacy frame {frame_index} atom {atom_index}: {exc}"
            ) from exc
        counts[symbol] = counts.get(symbol, 0) + 1
        atoms.append(atom)
    return FrameRecord(
        frame_index=frame_index,
        coordinate_units=CoordinateUnits.ANGSTROM,
        topology_source="legacy_v0",
        frame_id=frame_id,
        atoms=tuple(atoms),
    )
