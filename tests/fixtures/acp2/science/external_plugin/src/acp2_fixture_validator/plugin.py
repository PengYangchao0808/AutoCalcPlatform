"""Read-only validator for the fixed ACP2 native single-point record (A-22a).

The capability accepts the built-in ``TaskRequest`` envelope, reads the
native record referenced by ``request.structure.path`` and returns the
built-in ``TaskResult`` envelope.  It is a pure-analysis capability:

* no ``cccp.calculation.tasks`` import and no task execution;
* no subprocess, no network, no filesystem writes;
* coordinates are returned in angstrom and the energy in hartree, with the
  input atom order preserved explicitly (``atom_mapping="input_order"``).

The module is intentionally self-contained so the same file can be shipped
in the fixture wheel and audited without the CCCP source tree.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from acp2_fixture_validator import __version__
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import JsonObject, Provenance
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import TaskRequest
from cccp.calculation.results import TaskResult
from cccp.plugin.errors import PluginUnavailableError
from cccp.plugin.sdk import (
    UNIT_ANGSTROM,
    UNIT_HARTREE,
    PluginOutputSpec,
    check_cancelled,
    check_deadline,
)

PLUGIN_NAME = "acp2_external.native_singlepoint"
CAPABILITY_NAME = "acp2_external_native_singlepoint"
SCHEMA = "acp2.science.external.native_singlepoint"
SCHEMA_VERSION = 1
NATIVE_SCHEMA = "acp2.native.singlepoint.v1"

OUTPUT_SPEC = PluginOutputSpec(
    units=(("coordinates", UNIT_ANGSTROM), ("energy", UNIT_HARTREE)),
    atom_mapping="input_order",
    allows_partial=False,
)

_SYMBOL_RE = re.compile(r"^[A-Z][a-z]?$")
_SCALAR_KEYS = frozenset({"schema", "charge", "multiplicity", "atoms", "energy_hartree"})


@dataclass(frozen=True, slots=True)
class NativeRecord:
    """Parsed native single-point record (input order preserved)."""

    symbols: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    energy_hartree: float
    charge: int
    multiplicity: int
    native_schema: str


def parse_native_record(text: str) -> NativeRecord:
    """Parse one ``acp2.native.singlepoint.v1`` record.

    The grammar is line oriented: ``#`` comments and blank lines are ignored,
    scalar keys appear exactly once, and ``atom SYMBOL X Y Z`` lines follow
    the declared ``atoms`` count.  Coordinates are angstrom, the energy is
    hartree and must be finite and negative (a bound electronic state).

    Raises:
        TaskInputError: The record is empty, malformed, incomplete, carries
            an unknown/duplicate key, or a value is outside the declared
            physical domain.
    """
    scalars: dict[str, str] = {}
    atoms: list[tuple[str, tuple[float, float, float]]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        key = fields[0]
        if key == "atom":
            if len(fields) != 5:
                raise TaskInputError(
                    f"line {line_number}: atom requires 'SYMBOL X Y Z', got {len(fields)} field(s)"
                )
            symbol = fields[1]
            if _SYMBOL_RE.fullmatch(symbol) is None:
                raise TaskInputError(f"line {line_number}: invalid element symbol {symbol!r}")
            atoms.append((symbol, _coordinates(fields[2:5], line_number)))
            continue
        if key not in _SCALAR_KEYS:
            raise TaskInputError(f"line {line_number}: unknown key {key!r}")
        if key in scalars:
            raise TaskInputError(f"line {line_number}: duplicate key {key!r}")
        if len(fields) != 2:
            raise TaskInputError(f"line {line_number}: key {key!r} requires exactly one value")
        scalars[key] = fields[1]

    missing = sorted(_SCALAR_KEYS - set(scalars))
    if missing:
        raise TaskInputError(f"native record is missing required key(s): {missing}")
    native_schema = scalars["schema"]
    if native_schema != NATIVE_SCHEMA:
        raise TaskInputError(
            f"native schema {native_schema!r} is not supported; expected {NATIVE_SCHEMA!r}"
        )
    declared = _positive_int(scalars["atoms"], "atoms")
    if len(atoms) != declared:
        raise TaskInputError(
            f"native record declares {declared} atom(s) but carries {len(atoms)} atom line(s)"
        )
    charge = _integer(scalars["charge"], "charge")
    multiplicity = _positive_int(scalars["multiplicity"], "multiplicity")
    energy = _finite_float(scalars["energy_hartree"], "energy_hartree")
    if energy >= 0.0:
        raise TaskInputError("energy_hartree must be negative for a bound electronic state")
    return NativeRecord(
        symbols=tuple(symbol for symbol, _row in atoms),
        coordinates=tuple(row for _symbol, row in atoms),
        energy_hartree=energy,
        charge=charge,
        multiplicity=multiplicity,
        native_schema=native_schema,
    )


def _coordinates(fields: list[str], line_number: int) -> tuple[float, float, float]:
    values = [_finite_float(field, f"coordinate on line {line_number}") for field in fields]
    return (values[0], values[1], values[2])


def _finite_float(raw: str, label: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise TaskInputError(f"{label}: {raw!r} is not a number") from exc
    if not math.isfinite(value):
        raise TaskInputError(f"{label}: {raw!r} is not finite")
    return value


def _integer(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise TaskInputError(f"{label}: {raw!r} is not an integer") from exc


def _positive_int(raw: str, label: str) -> int:
    value = _integer(raw, label)
    if value < 1:
        raise TaskInputError(f"{label} must be >= 1, got {value}")
    return value


class NativeSinglePointValidator:
    """Pure-analysis handler served through the CCCP plugin SDK."""

    def capability_names(self) -> tuple[str, ...]:
        """Return the single capability this fixture serves."""
        return (CAPABILITY_NAME,)

    def output_spec_for(self, capability: str) -> PluginOutputSpec:
        """Return the declared output contract for *capability*."""
        if capability != CAPABILITY_NAME:
            raise PluginUnavailableError(
                PLUGIN_NAME, reason=f"capability {capability!r} is not served by this fixture"
            )
        return OUTPUT_SPEC

    def invoke(
        self,
        request: TaskRequest,
        *,
        context: TaskContext | None = None,
    ) -> TaskResult:
        """Validate the fixed native record and return the built-in result."""
        started_at = time.monotonic()
        check_deadline(PLUGIN_NAME, started_at, context)
        check_cancelled(context)
        structure = request.structure
        if structure is None or structure.path is None:
            raise TaskInputError("acp2_external_native_singlepoint requires structure.path")
        path = Path(structure.path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise TaskInputError(f"could not read native record {path}: {exc}") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TaskInputError(f"native record {path} is not UTF-8 text: {exc}") from exc
        record = parse_native_record(text)
        check_cancelled(context)
        check_deadline(PLUGIN_NAME, started_at, context)
        metadata: JsonObject = {
            "capability": CAPABILITY_NAME,
            "capability_schema": SCHEMA,
            "capability_schema_version": SCHEMA_VERSION,
            "units": {"coordinates": UNIT_ANGSTROM, "energy": UNIT_HARTREE},
            "atom_order": list(record.symbols),
            "native_schema": record.native_schema,
            "declared_atom_count": len(record.symbols),
            "charge": record.charge,
            "multiplicity": record.multiplicity,
        }
        return TaskResult(
            task=request.task,
            status="completed",
            complete=True,
            energy_hartree=record.energy_hartree,
            coordinates=record.coordinates,
            symbols=record.symbols,
            metadata=metadata,
            provenance=Provenance(
                backend="acp2_external_fixture",
                method="native_singlepoint_v1",
                version=__version__,
                input_signature=hashlib.sha256(raw).hexdigest(),
            ),
        )


#: Entry-point target (``acp2_fixture_validator.plugin:PLUGIN``).  The object
#: is constructed at import time; import happens only after explicit
#: enablement, because discovery reads metadata only.
PLUGIN = NativeSinglePointValidator()


__all__ = [
    "CAPABILITY_NAME",
    "NATIVE_SCHEMA",
    "NativeRecord",
    "NativeSinglePointValidator",
    "OUTPUT_SPEC",
    "PLUGIN",
    "PLUGIN_NAME",
    "SCHEMA",
    "SCHEMA_VERSION",
    "parse_native_record",
]
