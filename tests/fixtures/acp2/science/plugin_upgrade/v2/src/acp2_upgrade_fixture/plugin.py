"""Constructed read-only validator for the A-23 v2 plugin distribution.

The capability reads a fixed JSON single-point record and returns the built-in
``TaskResult`` envelope with explicit units and input atom order.  It imports
no task-layer module, starts no subprocess and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from acp2_upgrade_fixture import __version__
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

PLUGIN_NAME = "acp2_upgrade.native_singlepoint"
CAPABILITY_NAME = "acp2_upgrade_native_singlepoint"
SCHEMA = "acp2.science.external.upgrade_singlepoint"
SCHEMA_VERSION = 2
NATIVE_SCHEMA = "acp2.native.singlepoint.v1"

OUTPUT_SPEC = PluginOutputSpec(
    units=(("coordinates", UNIT_ANGSTROM), ("energy", UNIT_HARTREE)),
    atom_mapping="input_order",
    allows_partial=False,
)

_SYMBOL_RE = re.compile(r"^[A-Z][a-z]?$")


@dataclass(frozen=True, slots=True)
class NativeRecord:
    """Parsed native single-point JSON record (input order preserved)."""

    symbols: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    energy_hartree: float
    charge: int
    multiplicity: int
    native_schema: str


def _finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskInputError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TaskInputError(f"{label} must be finite")
    return number


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskInputError(f"{label} must be an integer")
    return value


def parse_native_record(text: str) -> NativeRecord:
    """Parse and validate one ``acp2.native.singlepoint.v1`` JSON record."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskInputError(f"native record is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TaskInputError("native record must be a JSON object")
    schema = payload.get("schema")
    if schema != NATIVE_SCHEMA:
        raise TaskInputError(
            f"native schema {schema!r} is not supported; expected {NATIVE_SCHEMA!r}"
        )
    charge = _integer(payload.get("charge"), "charge")
    multiplicity = _integer(payload.get("multiplicity"), "multiplicity")
    if multiplicity < 1:
        raise TaskInputError("multiplicity must be >= 1")
    atoms = payload.get("atoms")
    if not isinstance(atoms, list) or not atoms:
        raise TaskInputError("atoms must be a non-empty list")
    symbols: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for index, atom in enumerate(atoms):
        if not isinstance(atom, dict):
            raise TaskInputError(f"atoms[{index}] must be an object")
        symbol = atom.get("symbol")
        if not isinstance(symbol, str) or _SYMBOL_RE.fullmatch(symbol) is None:
            raise TaskInputError(f"atoms[{index}].symbol is invalid")
        xyz = atom.get("xyz")
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise TaskInputError(f"atoms[{index}].xyz must be three numbers")
        symbols.append(symbol)
        coordinates.append(
            (
                _finite_float(xyz[0], f"atoms[{index}].xyz"),
                _finite_float(xyz[1], f"atoms[{index}].xyz"),
                _finite_float(xyz[2], f"atoms[{index}].xyz"),
            )
        )
    energy = _finite_float(payload.get("energy_hartree"), "energy_hartree")
    if energy >= 0.0:
        raise TaskInputError("energy_hartree must be negative for a bound electronic state")
    return NativeRecord(
        symbols=tuple(symbols),
        coordinates=tuple(coordinates),
        energy_hartree=energy,
        charge=charge,
        multiplicity=multiplicity,
        native_schema=NATIVE_SCHEMA,
    )


class UpgradeSinglePointValidator:
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
            raise TaskInputError(f"{CAPABILITY_NAME} requires structure.path")
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
            "plugin_version": __version__,
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
                backend="acp2_upgrade_fixture",
                method="native_singlepoint_json_v1",
                version=__version__,
                input_signature=hashlib.sha256(raw).hexdigest(),
            ),
        )


PLUGIN = UpgradeSinglePointValidator()


__all__ = [
    "CAPABILITY_NAME",
    "NATIVE_SCHEMA",
    "NativeRecord",
    "OUTPUT_SPEC",
    "PLUGIN",
    "PLUGIN_NAME",
    "SCHEMA",
    "SCHEMA_VERSION",
    "UpgradeSinglePointValidator",
    "parse_native_record",
]
