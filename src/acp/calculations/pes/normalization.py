"""Single-source coordinate normalization for PES and scan submissions.

Round-5 P0: the platform used to pick the first non-empty of
``scan_coordinates`` / ``coordinate`` (and ``coordinates`` / ``coordinate`` in
the PES request), so a submission carrying both a primary coordinate and a
synchronized list could silently scan a different object than the caller
stated.  This module is the one normalization point:

* PES: ``coordinate`` and ``coordinates`` are normalized independently and
  compared as complete definitions; a mismatch is refused (never merged,
  never sorted, never "list contains the primary").
* scan: the string/object wire syntax is preserved, and the same normalization
  is shared by the scheduler flags, the submission validator and the API
  boundary; multiple alias sources (input vs method) and point-count aliases
  must agree.
* Units are normalized per kind (distance -> angstrom, angle/dihedral ->
  degree) and cross-kind units are refused.

The module is a pure function layer: no I/O, no job state, no second science
rule (range/atom-count checks stay in the existing validators).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "CoordinateError",
    "normalize_pes_coordinates",
    "normalize_scan_entries",
    "normalize_scan_points",
    "parse_pes_coordinate",
    "parse_scan_entry",
]

_KIND_ATOMS: dict[str, int] = {"distance": 2, "angle": 3, "dihedral": 4}
_DISTANCE_UNITS = frozenset({"angstrom", "ang", "a"})
_ANGLE_UNITS = frozenset({"degree", "degrees", "deg"})
_CANONICAL_UNIT = {"distance": "angstrom", "angle": "degree", "dihedral": "degree"}
_MIN_POINTS = 3
_MAX_POINTS = 101
_SCAN_POINT_KEYS = ("scan_points", "scan_coordinate_points")


class CoordinateError(ValueError):
    """A typed coordinate normalization error (field/rule for the API envelope)."""

    def __init__(self, message: str, *, field: str, rule: str, **params: Any) -> None:
        super().__init__(message)
        self.field = field
        self.rule = rule
        self.params: dict[str, Any] = {"field": field, "rule": rule, **params}

    def to_params(self) -> dict[str, Any]:
        return dict(self.params)


def _as_finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise CoordinateError(
            f"{field} must be a finite number", field=field, rule="invalid_type"
        )
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CoordinateError(
            f"{field} must be a finite number", field=field, rule="invalid_type"
        ) from exc
    if not math.isfinite(parsed):
        raise CoordinateError(
            f"{field} must be a finite number", field=field, rule="invalid_value"
        )
    return parsed


def _as_atoms(raw: Any, expected: int, *, field: str) -> tuple[int, ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) != expected:
        raise CoordinateError(
            f"{field} requires {expected} atoms",
            field=field,
            rule="invalid_type",
        )
    atoms: list[int] = []
    for index, value in enumerate(raw):
        if isinstance(value, bool):
            raise CoordinateError(
                f"{field}[{index}] must be an integer atom index",
                field=f"{field}[{index}]",
                rule="invalid_type",
            )
        if isinstance(value, int):
            parsed = value
        else:
            try:
                parsed = int(str(value).strip())
            except (TypeError, ValueError) as exc:
                raise CoordinateError(
                    f"{field}[{index}] must be an integer atom index",
                    field=f"{field}[{index}]",
                    rule="invalid_type",
                ) from exc
        if parsed < 0:
            raise CoordinateError(
                f"{field}[{index}] must be 0-based and non-negative",
                field=f"{field}[{index}]",
                rule="invalid_value",
            )
        atoms.append(parsed)
    if len(set(atoms)) != expected:
        raise CoordinateError(
            f"{field} must reference different atoms", field=field, rule="invalid_value"
        )
    return tuple(atoms)


def _kind_atoms(kind: str, *, field: str) -> int:
    expected = _KIND_ATOMS.get(kind)
    if expected is None:
        raise CoordinateError(
            f"{field}.kind must be one of {sorted(_KIND_ATOMS)}",
            field=f"{field}.kind",
            rule="invalid_enum",
            allowed=sorted(_KIND_ATOMS),
        )
    return expected


def _unit_for(kind: str, raw: Any, *, field: str) -> str:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _CANONICAL_UNIT[kind]
    if not isinstance(raw, str):
        raise CoordinateError(
            f"{field}.unit must be a string", field=f"{field}.unit", rule="invalid_type"
        )
    unit = raw.strip().lower()
    allowed = _DISTANCE_UNITS if kind == "distance" else _ANGLE_UNITS
    if unit not in allowed:
        raise CoordinateError(
            f"{field}.unit={raw!r} is not valid for kind {kind!r}",
            field=f"{field}.unit",
            rule="invalid_unit",
            allowed=sorted(allowed),
        )
    return _CANONICAL_UNIT[kind]


def _points(raw: Any, *, field: str, minimum: int = _MIN_POINTS) -> int:
    if isinstance(raw, bool):
        raise CoordinateError(
            f"{field} must be an integer", field=field, rule="invalid_type"
        )
    if isinstance(raw, float) and not raw.is_integer():
        raise CoordinateError(
            f"{field} must be an integer", field=field, rule="invalid_type"
        )
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise CoordinateError(
            f"{field} must be an integer", field=field, rule="invalid_type"
        ) from exc
    if minimum == _MIN_POINTS:
        if not _MIN_POINTS <= parsed <= _MAX_POINTS:
            raise CoordinateError(
                f"{field} must be between {_MIN_POINTS} and {_MAX_POINTS}",
                field=field,
                rule="out_of_range",
                minimum=_MIN_POINTS,
                maximum=_MAX_POINTS,
            )
    elif parsed < minimum:
        # scan points may be small (>= 2); the scientific range check stays in
        # validate_scan_submission
        raise CoordinateError(
            f"{field} must be an integer >= {minimum}",
            field=field,
            rule="out_of_range",
            minimum=minimum,
        )
    return parsed


def parse_pes_coordinate(payload: Any, *, field: str) -> dict[str, Any]:
    """Normalize one PES coordinate object (strict; never fills missing science)."""
    if not isinstance(payload, Mapping):
        raise CoordinateError(
            f"{field} must be an object", field=field, rule="invalid_type"
        )
    if not payload:
        raise CoordinateError(
            f"{field} must not be empty", field=field, rule="missing_field"
        )
    kind = str(payload.get("kind") or "distance").strip().lower()
    expected_atoms = _kind_atoms(kind, field=field)
    atoms = _as_atoms(payload.get("atoms"), expected_atoms, field=f"{field}.atoms")
    unit = _unit_for(kind, payload.get("unit"), field=field)
    n_points_raw = payload.get("n_points")
    values_raw = payload.get("values")
    start_raw = payload.get("start")
    end_raw = payload.get("end")

    values: tuple[float, ...] = ()
    if values_raw is not None:
        if not isinstance(values_raw, (list, tuple)) or len(values_raw) < 2:
            raise CoordinateError(
                f"{field}.values must be a list of at least two numbers",
                field=f"{field}.values",
                rule="invalid_type",
            )
        values = tuple(
            _as_finite(item, field=f"{field}.values[{index}]")
            for index, item in enumerate(values_raw)
        )
        increasing = all(a < b for a, b in zip(values, values[1:]))
        decreasing = all(a > b for a, b in zip(values, values[1:]))
        if not (increasing or decreasing):
            raise CoordinateError(
                f"{field}.values must be strictly monotone",
                field=f"{field}.values",
                rule="invalid_value",
            )
        if start_raw is not None and not math.isclose(
            _as_finite(start_raw, field=f"{field}.start"), values[0], abs_tol=1.0e-9
        ):
            raise CoordinateError(
                f"{field}.start does not match values[0]",
                field=f"{field}.start",
                rule="conflicting_fields",
            )
        if end_raw is not None and not math.isclose(
            _as_finite(end_raw, field=f"{field}.end"), values[-1], abs_tol=1.0e-9
        ):
            raise CoordinateError(
                f"{field}.end does not match values[-1]",
                field=f"{field}.end",
                rule="conflicting_fields",
            )
        start, end = values[0], values[-1]
        if n_points_raw is not None:
            n_points = _points(n_points_raw, field=f"{field}.n_points")
            if n_points != len(values):
                raise CoordinateError(
                    f"{field}.n_points must equal len(values)",
                    field=f"{field}.n_points",
                    rule="conflicting_fields",
                )
        else:
            n_points = len(values)
    else:
        if start_raw is None or end_raw is None:
            raise CoordinateError(
                f"{field} requires both start and end",
                field=f"{field}.start",
                rule="missing_field",
            )
        start = _as_finite(start_raw, field=f"{field}.start")
        end = _as_finite(end_raw, field=f"{field}.end")
        n_points = (
            _points(n_points_raw, field=f"{field}.n_points")
            if n_points_raw is not None
            else 16
        )
    normalized: dict[str, Any] = {
        "kind": kind,
        "atoms": list(atoms),
        "unit": unit,
        "start": start,
        "end": end,
        "n_points": n_points,
    }
    if values:
        normalized["values"] = list(values)
    return normalized


def _definition(coord: Mapping[str, Any]) -> tuple[Any, ...]:
    """Complete definition used for equality (no fuzzy merging)."""
    return (
        coord["kind"],
        tuple(coord["atoms"]),
        coord["unit"],
        float(coord["start"]),
        float(coord["end"]),
        int(coord.get("n_points") or 0),
        tuple(float(value) for value in coord.get("values") or ()),
    )


def normalize_pes_coordinates(
    payload: Mapping[str, Any], *, field_prefix: str = "input"
) -> dict[str, Any]:
    """Normalize PES ``coordinate`` + ``coordinates`` into one ordered set.

    Rules (round-5 plan §5.2): only ``coordinate`` -> one-element list; only
    non-empty ``coordinates`` -> allowed with ``coordinates[0]`` as primary;
    both -> the normalized primary must equal ``coordinates[0]`` completely,
    otherwise ``rule=conflicting_fields``.  Never merge, sort or default-fill.
    """
    raw_primary = payload.get("coordinate")
    raw_coordinates = payload.get("coordinates")
    coordinates: list[dict[str, Any]] = []
    if raw_coordinates is not None:
        if not isinstance(raw_coordinates, (list, tuple)):
            raise CoordinateError(
                f"{field_prefix}.coordinates must be a list of coordinate objects",
                field=f"{field_prefix}.coordinates",
                rule="invalid_type",
            )
        if not raw_coordinates:
            raise CoordinateError(
                f"{field_prefix}.coordinates must not be empty",
                field=f"{field_prefix}.coordinates",
                rule="missing_field",
            )
        coordinates = [
            parse_pes_coordinate(item, field=f"{field_prefix}.coordinates[{index}]")
            for index, item in enumerate(raw_coordinates)
        ]
    primary: dict[str, Any] | None = None
    if raw_primary is not None:
        primary = parse_pes_coordinate(raw_primary, field=f"{field_prefix}.coordinate")
    if primary is None and coordinates:
        primary = coordinates[0]
    if primary is None:
        raise CoordinateError(
            f"{field_prefix} requires a coordinate (or a non-empty coordinates list)",
            field=f"{field_prefix}.coordinate",
            rule="missing_input",
        )
    if raw_primary is not None and coordinates:
        if _definition(primary) != _definition(coordinates[0]):
            raise CoordinateError(
                f"{field_prefix}.coordinate conflicts with {field_prefix}.coordinates[0]",
                field=f"{field_prefix}.coordinate",
                rule="conflicting_fields",
                fields=[f"{field_prefix}.coordinate", f"{field_prefix}.coordinates[0]"],
            )
    return {
        "coordinate": primary,
        "coordinates": coordinates or [primary],
        "n_points": int(primary["n_points"]),
    }


def _scan_points_from_entry(entry: Any, *, field: str) -> dict[str, Any]:
    if isinstance(entry, Mapping):
        if not entry:
            raise CoordinateError(
                f"{field} must not be empty", field=field, rule="missing_field"
            )
        raw_kind = entry.get("kind")
        raw_atoms = entry.get("atoms")
        if raw_kind is None:
            # the string wire infers the kind from the atom count; the object
            # form follows the same rule when kind is omitted
            if isinstance(raw_atoms, (list, tuple)) and len(raw_atoms) in _KIND_ATOMS_BY_COUNT:
                kind = _KIND_ATOMS_BY_COUNT[len(raw_atoms)]
            else:
                raise CoordinateError(
                    f"{field}.atoms must contain 2-4 atoms",
                    field=f"{field}.atoms",
                    rule="invalid_type",
                )
        else:
            kind = str(raw_kind).strip().lower()
            if kind not in _KIND_ATOMS:
                raise CoordinateError(
                    f"{field}: unsupported scan coordinate kind {raw_kind!r}",
                    field=f"{field}.kind",
                    rule="invalid_enum",
                    allowed=sorted(_KIND_ATOMS),
                )
        expected_atoms = _KIND_ATOMS[kind]
        if not isinstance(raw_atoms, (list, tuple)) or len(raw_atoms) != expected_atoms:
            raise CoordinateError(
                f"{field}: {kind} coordinates require {expected_atoms} atoms "
                f"(kind requires {expected_atoms} atoms)",
                field=f"{field}.atoms",
                rule="invalid_type",
            )
        atoms = _as_atoms(raw_atoms, expected_atoms, field=f"{field}.atoms")
        unit = _unit_for(kind, entry.get("unit"), field=field)
        start_raw = entry.get("start")
        end_raw = entry.get("end")
        if start_raw is None or end_raw is None:
            raise CoordinateError(
                f"{field} requires both start and end",
                field=f"{field}.start",
                rule="missing_field",
            )
        return {
            "kind": kind,
            "atoms": list(atoms),
            "unit": unit,
            "start": _as_finite(start_raw, field=f"{field}.start"),
            "end": _as_finite(end_raw, field=f"{field}.end"),
            "start_text": str(start_raw),
            "end_text": str(end_raw),
        }
    if isinstance(entry, str):
        parts = [text.strip() for text in entry.split(",")]
        if len(parts) not in (4, 5, 6):
            raise CoordinateError(
                f"{field} must be atom1,atom2[,atom3[,atom4]],start,end",
                field=field,
                rule="invalid_type",
            )
        atom_total = len(parts) - 2
        kind = _KIND_ATOMS_BY_COUNT.get(atom_total)
        if kind is None:
            raise CoordinateError(
                f"{field} has an unsupported atom count", field=field, rule="invalid_type"
            )
        atoms = _as_atoms(parts[:atom_total], atom_total, field=f"{field}.atoms")
        return {
            "kind": kind,
            "atoms": list(atoms),
            "unit": _CANONICAL_UNIT[kind],
            "start": _as_finite(parts[atom_total], field=f"{field}.start"),
            "end": _as_finite(parts[atom_total + 1], field=f"{field}.end"),
            "start_text": parts[atom_total],
            "end_text": parts[atom_total + 1],
        }
    raise CoordinateError(
        f"{field} must be a coordinate object or an 'atoms,start,end' string",
        field=field,
        rule="invalid_type",
    )


_KIND_ATOMS_BY_COUNT: dict[int, str] = {count: kind for kind, count in _KIND_ATOMS.items()}


def parse_scan_entry(entry: Any, *, field: str) -> dict[str, Any]:
    """Normalize one scan coordinate entry (string or object wire syntax)."""
    return _scan_points_from_entry(entry, field=field)


def _scan_entries_of(raw: Any, *, field: str) -> list[dict[str, Any]]:
    if isinstance(raw, (str, Mapping)):
        return [_scan_points_from_entry(raw, field=field)]
    if isinstance(raw, (list, tuple)):
        if not raw:
            raise CoordinateError(
                f"{field} must not be empty", field=field, rule="missing_field"
            )
        return [
            _scan_points_from_entry(item, field=f"{field}[{index}]")
            for index, item in enumerate(raw)
        ]
    raise CoordinateError(
        f"{field} must be a string, object or a list of them",
        field=field,
        rule="invalid_type",
    )


def normalize_scan_entries(
    payload: Mapping[str, Any] | None, method: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Return the one ordered coordinate list; alias sources must agree.

    ``input`` and ``method`` may each carry ``scan_coordinates``/``coordinate``
    (the scheduler reads both).  When more than one source is present they must
    normalize to the same definitions; otherwise the submission is refused
    instead of the scheduler silently picking one.
    """
    payload_map: Mapping[str, Any] = payload or {}
    method_map: Mapping[str, Any] = method or {}
    found: tuple[str, list[dict[str, Any]]] | None = None
    for source, label in ((payload_map, "input"), (method_map, "method")):
        for key in ("scan_coordinates", "coordinate"):
            raw = source.get(key)
            if raw is None:
                continue
            entries = _scan_entries_of(raw, field=f"{label}.{key}")
            if found is None:
                found = (f"{label}.{key}", entries)
            elif [ _definition(item) for item in entries ] != [
                _definition(item) for item in found[1]
            ]:
                raise CoordinateError(
                    f"{label}.{key} conflicts with {found[0]}",
                    field=f"{label}.{key}",
                    rule="conflicting_fields",
                    fields=[found[0], f"{label}.{key}"],
                )
    if found is None:
        return []
    return found[1]


def normalize_scan_points(
    method: Mapping[str, Any] | None, payload: Mapping[str, Any] | None
) -> int | None:
    """Resolve the scan point count; conflicting aliases are refused."""
    method_map: Mapping[str, Any] = method or {}
    payload_map: Mapping[str, Any] = payload or {}
    candidates: list[tuple[str, Any]] = []
    if method_map.get("scan_points") is not None:
        candidates.append(("method.scan_points", method_map.get("scan_points")))
    levels = method_map.get("levels")
    if isinstance(levels, Mapping):
        scan_level = levels.get("scan") or levels.get("scan_coordinate")
        if isinstance(scan_level, Mapping):
            for key in _SCAN_POINT_KEYS:
                if scan_level.get(key) is not None:
                    candidates.append((f"method.levels.scan.{key}", scan_level[key]))
    for key in _SCAN_POINT_KEYS:
        if payload_map.get(key) is not None:
            candidates.append((f"input.{key}", payload_map[key]))
    resolved: tuple[str, int] | None = None
    for label, raw in candidates:
        parsed = _points(raw, field=label, minimum=2)
        if resolved is None:
            resolved = (label, parsed)
        elif resolved[1] != parsed:
            raise CoordinateError(
                f"{label}={parsed} conflicts with {resolved[0]}={resolved[1]}",
                field=label,
                rule="conflicting_fields",
                fields=[resolved[0], label],
            )
    return None if resolved is None else resolved[1]


def coordinates_match(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    """Complete-definition equality for two normalized coordinates."""
    return _definition(left) == _definition(right)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
