"""ACP2 scientific-provenance science type (``provenance.schema.json``, v1).

Provenance records **how a result was derived**, never whether a claim is
supported.  This module mirrors the frozen contract in
``docs/reference/next-change/acp2-contracts/science/provenance.schema.json``:

* ``result_kind: electronic_structure`` requires ``method`` + ``basis`` +
  ``electronic_state`` and a non-empty ``software_version``;
* ``result_kind: pure_analysis`` requires ``algorithm`` + ``algorithm_version``
  + ``input_digest`` and must not invent a quantum method, basis or state;
* ``conditions.temperature_kelvin``, ``standard_state`` and
  ``reference_state`` are required fields that are explicitly nullable — an
  inapplicable condition is ``null``, never silently omitted;
* every input artifact carries a relative path and a sha256 digest.

External plugin identities are part of the audit view only.  The frozen
schema has no plugin field, so :meth:`ScientificProvenance.to_dict` stays
schema-conforming while :meth:`ScientificProvenance.to_audit_dict` adds the
explicit plugin list (empty means "no plugin participated" rather than an
omission).

The module is pure: standard library only, no task/QC/platform import and no
subprocess.

Runtime calculation records are adapted **into** these science types, never
redefined here.  The ``cccp.calculation.contracts.Provenance`` /
``ArtifactRef`` classes are imported lazily inside the adapter functions so
that importing this module stays independent of the execution layer.  The
runtime record knows backend/method/version/input-signature and an artifact
path/type/checksum; it knows nothing about basis or electronic state, so those
stay absent (a caller must supply them from the calculation spec) instead of
being invented.  An older payload is readable through
:func:`read_historical_provenance` with every field preserved and no execution
or recovery eligibility.

Author: QCcalc Team
"""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from cccp.analysis.research._common import (
    ScienceContractError,
    canonical_json,
    reject_unknown_keys,
    require_int,
    require_mapping,
    require_number,
    require_sequence,
    require_string,
)
from cccp.analysis.research.evidence import (
    DIGEST_KEYS,
    Digest,
    EvidenceContractError,
    artifact_source_ref_from_calculation_artifact,
    digest_from_checksum,
    verify_artifact_digest,
)

logger = logging.getLogger(__name__)

__all__ = [
    "METHOD_FAMILIES",
    "PROVENANCE_RESULT_KINDS",
    "PROVENANCE_SCHEMA_VERSION",
    "SPIN_MODES",
    "BasisIdentity",
    "Conditions",
    "ElectronicStateIdentity",
    "ElectronicStructureDerivation",
    "HistoricalProvenanceRecord",
    "InputIdentity",
    "MethodIdentity",
    "PluginIdentity",
    "ProvenanceArtifactRef",
    "ProvenanceContractError",
    "ProvenanceDerivationError",
    "ProvenanceDigestMismatchError",
    "ProvenanceSourceUnavailableError",
    "PureAnalysisDerivation",
    "ScientificProvenance",
    "SoftwareIdentity",
    "UnsupportedProvenanceSchemaVersionError",
    "canonical_provenance_json",
    "input_digest_from_calculation_provenance",
    "method_identity_from_calculation_provenance",
    "provenance_artifact_ref_from_calculation_artifact",
    "provenance_from_dict",
    "read_historical_provenance",
    "software_identity_from_calculation_provenance",
    "unknown_provenance_field_paths",
    "verify_provenance_inputs",
]

# ── frozen vocabulary ──────────────────────────────────────────────────────

PROVENANCE_SCHEMA_VERSION: Final = 1
PROVENANCE_RESULT_KINDS: Final[frozenset[str]] = frozenset(
    {"electronic_structure", "pure_analysis"}
)
METHOD_FAMILIES: Final[frozenset[str]] = frozenset(
    {"dft", "wavefunction", "semiempirical", "force_field", "composite", "unknown"}
)
SPIN_MODES: Final[frozenset[str]] = frozenset(
    {"auto", "restricted", "unrestricted", "broken_symmetry"}
)

SCIENCE_IDENTITY_PATTERN: Final = re.compile(r"^v2:[0-9a-f]{32}$")
_VERSION_PATTERN: Final = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")

_PROVENANCE_KEYS: Final = frozenset(
    {
        "schema_version",
        "provenance_id",
        "result_kind",
        "software",
        "derivation",
        "input",
        "conditions",
        "limitations",
        "science_identity",
    }
)
_ARTIFACT_KEYS: Final = frozenset({"relative_path", "digest", "role", "units"})
_SOFTWARE_KEYS: Final = frozenset(
    {"software_id", "software_version", "distribution", "executable_path"}
)
_CONDITION_KEYS: Final = frozenset(
    {
        "temperature_kelvin",
        "standard_state",
        "reference_state",
        "pressure_pa",
        "phase",
        "solvent",
    }
)
_INPUT_KEYS: Final = frozenset({"artifacts", "structure_digest"})
_METHOD_KEYS: Final = frozenset({"method_id", "method_family", "dispersion", "hamiltonian"})
_BASIS_KEYS: Final = frozenset({"basis_id", "basis_family", "auxiliary_basis_id"})
_ELECTRONIC_STATE_KEYS: Final = frozenset({"charge", "multiplicity", "state_label", "spin_mode"})
_ELECTRONIC_DERIVATION_KEYS: Final = frozenset(
    {"method", "basis", "electronic_state", "model_chemistry"}
)
_PURE_ANALYSIS_KEYS: Final = frozenset(
    {"algorithm", "algorithm_version", "input_digest", "parameters"}
)


class ProvenanceContractError(EvidenceContractError):
    """Base class for rejected provenance payloads."""


class UnsupportedProvenanceSchemaVersionError(ProvenanceContractError):
    """The provenance declares a schema major version this module cannot read."""


class ProvenanceDerivationError(ProvenanceContractError):
    """The derivation does not satisfy its declared ``result_kind``."""


class ProvenanceSourceUnavailableError(ProvenanceContractError):
    """A provenance input artifact is absent or unreadable."""


class ProvenanceDigestMismatchError(ProvenanceContractError):
    """A provenance input artifact digest disagrees with its bytes."""


# ── helpers ────────────────────────────────────────────────────────────────


def _mapping(value: Any, where: str) -> dict[str, Any]:
    try:
        return require_mapping(value, where)
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _sequence(value: Any, where: str) -> list[Any]:
    try:
        return require_sequence(value, where)
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _string(value: Any, where: str) -> str:
    try:
        return require_string(value, where)
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _optional_string(value: Any, where: str) -> str | None:
    return None if value is None else _string(value, where)


def _optional_number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    try:
        return float(require_number(value, where, minimum=0.0))
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _int(value: Any, where: str) -> int:
    try:
        return require_int(value, where)
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _require_keys(mapping: Mapping[str, Any], required: Iterable[str], where: str) -> None:
    missing = sorted(set(required) - set(mapping))
    if missing:
        raise ProvenanceContractError(f"{where}: missing required field(s) {missing}")


def _reject_unknown(mapping: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    try:
        reject_unknown_keys(mapping, allowed, where)
    except ScienceContractError as exc:
        raise ProvenanceContractError(str(exc)) from exc


def _relative_path(value: Any, where: str) -> str:
    path = _string(value, where)
    pure = path.replace("\\", "/")
    if pure.startswith("/") or re.match(r"^[A-Za-z]:/", pure):
        raise ProvenanceContractError(f"{where}: artifact path must be relative, got {path!r}")
    if any(part == ".." for part in pure.split("/")):
        raise ProvenanceContractError(
            f"{where}: artifact path must not traverse parents, got {path!r}"
        )
    return path


# ── component identities ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SoftwareIdentity:
    """The executable or pure-analysis implementation that produced a result."""

    software_id: str
    software_version: str
    distribution: str | None = None
    executable_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "software_id", _string(self.software_id, "software.software_id"))
        object.__setattr__(
            self,
            "software_version",
            _string(self.software_version, "software.software_version"),
        )
        object.__setattr__(
            self, "distribution", _optional_string(self.distribution, "software.distribution")
        )
        object.__setattr__(
            self,
            "executable_path",
            _optional_string(self.executable_path, "software.executable_path"),
        )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "software") -> SoftwareIdentity:
        """Read a software identity."""
        data = _mapping(payload, where)
        _reject_unknown(data, _SOFTWARE_KEYS, where)
        _require_keys(data, ("software_id", "software_version"), where)
        return cls(
            software_id=_string(data["software_id"], f"{where}.software_id"),
            software_version=_string(data["software_version"], f"{where}.software_version"),
            distribution=_optional_string(data.get("distribution"), f"{where}.distribution"),
            executable_path=_optional_string(
                data.get("executable_path"), f"{where}.executable_path"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped software identity."""
        return {
            "software_id": self.software_id,
            "software_version": self.software_version,
            "distribution": self.distribution,
            "executable_path": self.executable_path,
        }


@dataclass(frozen=True, slots=True)
class MethodIdentity:
    """A canonical quantum-chemistry method identity."""

    method_id: str
    method_family: str = "unknown"
    dispersion: str | None = None
    hamiltonian: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method_id", _string(self.method_id, "method.method_id"))
        if self.method_family not in METHOD_FAMILIES:
            raise ProvenanceContractError(
                f"method.method_family must be one of {sorted(METHOD_FAMILIES)}, "
                f"got {self.method_family!r}"
            )
        object.__setattr__(
            self, "dispersion", _optional_string(self.dispersion, "method.dispersion")
        )
        object.__setattr__(
            self, "hamiltonian", _optional_string(self.hamiltonian, "method.hamiltonian")
        )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "method") -> MethodIdentity:
        """Read a method identity."""
        data = _mapping(payload, where)
        _reject_unknown(data, _METHOD_KEYS, where)
        _require_keys(data, ("method_id",), where)
        return cls(
            method_id=_string(data["method_id"], f"{where}.method_id"),
            method_family=_string(data.get("method_family", "unknown"), f"{where}.method_family"),
            dispersion=_optional_string(data.get("dispersion"), f"{where}.dispersion"),
            hamiltonian=_optional_string(data.get("hamiltonian"), f"{where}.hamiltonian"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped method identity."""
        return {
            "method_id": self.method_id,
            "method_family": self.method_family,
            "dispersion": self.dispersion,
            "hamiltonian": self.hamiltonian,
        }


@dataclass(frozen=True, slots=True)
class BasisIdentity:
    """A basis-set identity."""

    basis_id: str
    basis_family: str | None = None
    auxiliary_basis_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis_id", _string(self.basis_id, "basis.basis_id"))
        object.__setattr__(
            self, "basis_family", _optional_string(self.basis_family, "basis.basis_family")
        )
        object.__setattr__(
            self,
            "auxiliary_basis_id",
            _optional_string(self.auxiliary_basis_id, "basis.auxiliary_basis_id"),
        )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "basis") -> BasisIdentity:
        """Read a basis identity."""
        data = _mapping(payload, where)
        _reject_unknown(data, _BASIS_KEYS, where)
        _require_keys(data, ("basis_id",), where)
        return cls(
            basis_id=_string(data["basis_id"], f"{where}.basis_id"),
            basis_family=_optional_string(data.get("basis_family"), f"{where}.basis_family"),
            auxiliary_basis_id=_optional_string(
                data.get("auxiliary_basis_id"), f"{where}.auxiliary_basis_id"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped basis identity."""
        return {
            "basis_id": self.basis_id,
            "basis_family": self.basis_family,
            "auxiliary_basis_id": self.auxiliary_basis_id,
        }


@dataclass(frozen=True, slots=True)
class ElectronicStateIdentity:
    """Charge, multiplicity and optional state labels/spin mode."""

    charge: int
    multiplicity: int
    state_label: str | None = None
    spin_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "charge", _int(self.charge, "electronic_state.charge"))
        object.__setattr__(
            self,
            "multiplicity",
            _int(self.multiplicity, "electronic_state.multiplicity"),
        )
        if self.multiplicity < 1:
            raise ProvenanceContractError("electronic_state.multiplicity must be >= 1")
        object.__setattr__(
            self,
            "state_label",
            _optional_string(self.state_label, "electronic_state.state_label"),
        )
        if self.spin_mode is not None and self.spin_mode not in SPIN_MODES:
            raise ProvenanceContractError(
                f"electronic_state.spin_mode must be one of {sorted(SPIN_MODES)} or null"
            )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "electronic_state") -> ElectronicStateIdentity:
        """Read an electronic-state identity."""
        data = _mapping(payload, where)
        _reject_unknown(data, _ELECTRONIC_STATE_KEYS, where)
        _require_keys(data, ("charge", "multiplicity"), where)
        return cls(
            charge=_int(data["charge"], f"{where}.charge"),
            multiplicity=_int(data["multiplicity"], f"{where}.multiplicity"),
            state_label=_optional_string(data.get("state_label"), f"{where}.state_label"),
            spin_mode=_optional_string(data.get("spin_mode"), f"{where}.spin_mode"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped electronic-state identity."""
        return {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "state_label": self.state_label,
            "spin_mode": self.spin_mode,
        }


@dataclass(frozen=True, slots=True)
class Conditions:
    """Declared thermodynamic conditions.

    ``temperature_kelvin``, ``standard_state`` and ``reference_state`` are
    required by the frozen schema even when they do not apply; ``None`` is the
    explicit inapplicable declaration.
    """

    temperature_kelvin: float | None
    standard_state: str | None
    reference_state: str | None
    pressure_pa: float | None = None
    phase: str | None = None
    solvent: str | None = None

    def __post_init__(self) -> None:
        if self.temperature_kelvin is not None:
            temperature = float(self.temperature_kelvin)
            if temperature <= 0.0:
                raise ProvenanceContractError(
                    "conditions.temperature_kelvin must be positive or null"
                )
            object.__setattr__(self, "temperature_kelvin", temperature)
        if self.pressure_pa is not None:
            pressure = float(self.pressure_pa)
            if pressure <= 0.0:
                raise ProvenanceContractError("conditions.pressure_pa must be positive or null")
            object.__setattr__(self, "pressure_pa", pressure)
        object.__setattr__(
            self,
            "standard_state",
            _optional_string(self.standard_state, "conditions.standard_state"),
        )
        object.__setattr__(
            self,
            "reference_state",
            _optional_string(self.reference_state, "conditions.reference_state"),
        )
        object.__setattr__(self, "phase", _optional_string(self.phase, "conditions.phase"))
        object.__setattr__(self, "solvent", _optional_string(self.solvent, "conditions.solvent"))

    @classmethod
    def from_dict(cls, payload: Any, where: str = "conditions") -> Conditions:
        """Read conditions; the three nullable keys must be present."""
        data = _mapping(payload, where)
        _reject_unknown(data, _CONDITION_KEYS, where)
        _require_keys(data, ("temperature_kelvin", "standard_state", "reference_state"), where)
        return cls(
            temperature_kelvin=_optional_number(
                data["temperature_kelvin"], f"{where}.temperature_kelvin"
            ),
            standard_state=_optional_string(data["standard_state"], f"{where}.standard_state"),
            reference_state=_optional_string(data["reference_state"], f"{where}.reference_state"),
            pressure_pa=_optional_number(data.get("pressure_pa"), f"{where}.pressure_pa"),
            phase=_optional_string(data.get("phase"), f"{where}.phase"),
            solvent=_optional_string(data.get("solvent"), f"{where}.solvent"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped conditions."""
        return {
            "temperature_kelvin": self.temperature_kelvin,
            "standard_state": self.standard_state,
            "reference_state": self.reference_state,
            "pressure_pa": self.pressure_pa,
            "phase": self.phase,
            "solvent": self.solvent,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceArtifactRef:
    """One input artifact with a relative path and sha256 digest."""

    relative_path: str
    digest: Digest
    role: str
    units: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relative_path",
            _relative_path(self.relative_path, "input.artifacts.relative_path"),
        )
        if not isinstance(self.digest, Digest):
            raise ProvenanceContractError("input.artifacts.digest must be a Digest")
        object.__setattr__(self, "role", _string(self.role, "input.artifacts.role"))
        object.__setattr__(self, "units", _optional_string(self.units, "input.artifacts.units"))

    @classmethod
    def from_dict(cls, payload: Any, where: str = "input.artifact") -> ProvenanceArtifactRef:
        """Read one provenance input artifact."""
        data = _mapping(payload, where)
        _reject_unknown(data, _ARTIFACT_KEYS, where)
        _require_keys(data, ("relative_path", "digest", "role"), where)
        return cls(
            relative_path=_string(data["relative_path"], f"{where}.relative_path"),
            digest=Digest.from_dict(data["digest"], f"{where}.digest"),
            role=_string(data["role"], f"{where}.role"),
            units=_optional_string(data.get("units"), f"{where}.units"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped input artifact."""
        return {
            "relative_path": self.relative_path,
            "digest": self.digest.to_dict(),
            "role": self.role,
            "units": self.units,
        }


@dataclass(frozen=True, slots=True)
class InputIdentity:
    """The input artifact set and optional structure digest."""

    artifacts: tuple[ProvenanceArtifactRef, ...]
    structure_digest: Digest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if not self.artifacts:
            raise ProvenanceContractError("input.artifacts must contain at least one artifact")
        for index, artifact in enumerate(self.artifacts):
            if not isinstance(artifact, ProvenanceArtifactRef):
                raise ProvenanceContractError(
                    f"input.artifacts[{index}] must be a ProvenanceArtifactRef"
                )
        if self.structure_digest is not None and not isinstance(self.structure_digest, Digest):
            raise ProvenanceContractError("input.structure_digest must be a Digest or None")

    @classmethod
    def from_dict(cls, payload: Any, where: str = "input") -> InputIdentity:
        """Read an input identity."""
        data = _mapping(payload, where)
        _reject_unknown(data, _INPUT_KEYS, where)
        _require_keys(data, ("artifacts",), where)
        raw_digest = data.get("structure_digest")
        return cls(
            artifacts=tuple(
                ProvenanceArtifactRef.from_dict(item, f"{where}.artifacts[{index}]")
                for index, item in enumerate(_sequence(data["artifacts"], f"{where}.artifacts"))
            ),
            structure_digest=(
                None
                if raw_digest is None
                else Digest.from_dict(raw_digest, f"{where}.structure_digest")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped input identity."""
        document: dict[str, Any] = {
            "artifacts": [artifact.to_dict() for artifact in self.artifacts]
        }
        if self.structure_digest is not None:
            document["structure_digest"] = self.structure_digest.to_dict()
        return document


@dataclass(frozen=True, slots=True)
class ElectronicStructureDerivation:
    """Method/basis/electronic-state derivation for a QC result."""

    method: MethodIdentity
    basis: BasisIdentity
    electronic_state: ElectronicStateIdentity
    model_chemistry: str | None = None

    def __post_init__(self) -> None:
        for name, value, expected in (
            ("method", self.method, MethodIdentity),
            ("basis", self.basis, BasisIdentity),
            ("electronic_state", self.electronic_state, ElectronicStateIdentity),
        ):
            if not isinstance(value, expected):
                raise ProvenanceDerivationError(f"derivation.{name} must be a {expected.__name__}")
        object.__setattr__(
            self,
            "model_chemistry",
            _optional_string(self.model_chemistry, "derivation.model_chemistry"),
        )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "derivation") -> ElectronicStructureDerivation:
        """Read an electronic-structure derivation."""
        data = _mapping(payload, where)
        _reject_unknown(data, _ELECTRONIC_DERIVATION_KEYS, where)
        _require_keys(data, ("method", "basis", "electronic_state"), where)
        return cls(
            method=MethodIdentity.from_dict(data["method"], f"{where}.method"),
            basis=BasisIdentity.from_dict(data["basis"], f"{where}.basis"),
            electronic_state=ElectronicStateIdentity.from_dict(
                data["electronic_state"], f"{where}.electronic_state"
            ),
            model_chemistry=_optional_string(
                data.get("model_chemistry"), f"{where}.model_chemistry"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped derivation."""
        return {
            "method": self.method.to_dict(),
            "basis": self.basis.to_dict(),
            "electronic_state": self.electronic_state.to_dict(),
            "model_chemistry": self.model_chemistry,
        }


@dataclass(frozen=True, slots=True)
class PureAnalysisDerivation:
    """Algorithm derivation for a pure analysis over existing products.

    Quantum method, basis and electronic-state fields are forbidden: a pure
    analysis consumes existing products and never invents a QC calculation.
    """

    algorithm: str
    algorithm_version: str
    input_digest: Digest
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", _string(self.algorithm, "derivation.algorithm"))
        version = _string(self.algorithm_version, "derivation.algorithm_version")
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ProvenanceDerivationError(
                "derivation.algorithm_version must start with a digit and contain only "
                "version characters"
            )
        if not isinstance(self.input_digest, Digest):
            raise ProvenanceDerivationError("derivation.input_digest must be a Digest")
        parameters = {} if self.parameters is None else dict(self.parameters)
        for key, value in parameters.items():
            if not isinstance(key, str):
                raise ProvenanceDerivationError("derivation.parameters keys must be strings")
            if isinstance(value, Mapping):
                raise ProvenanceDerivationError(
                    "derivation.parameters values must be scalar or arrays, not objects"
                )
        object.__setattr__(self, "algorithm_version", version)
        object.__setattr__(self, "parameters", parameters)

    @classmethod
    def from_dict(cls, payload: Any, where: str = "derivation") -> PureAnalysisDerivation:
        """Read a pure-analysis derivation and reject quantum-method fields."""
        data = _mapping(payload, where)
        forbidden = sorted(set(data) & {"method", "basis", "electronic_state"})
        if forbidden:
            raise ProvenanceDerivationError(
                f"{where}: pure_analysis must not declare quantum field(s) {forbidden}"
            )
        _reject_unknown(data, _PURE_ANALYSIS_KEYS, where)
        _require_keys(data, ("algorithm", "algorithm_version", "input_digest"), where)
        raw_parameters = data.get("parameters", {})
        parameters = _mapping(raw_parameters, f"{where}.parameters")
        return cls(
            algorithm=_string(data["algorithm"], f"{where}.algorithm"),
            algorithm_version=_string(data["algorithm_version"], f"{where}.algorithm_version"),
            input_digest=Digest.from_dict(data["input_digest"], f"{where}.input_digest"),
            parameters=parameters,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped derivation."""
        return {
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "input_digest": self.input_digest.to_dict(),
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True, slots=True)
class PluginIdentity:
    """An external science plugin identity (audit view only, not v1 schema)."""

    plugin_id: str
    version: str
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugin_id", _string(self.plugin_id, "plugin.plugin_id"))
        object.__setattr__(self, "version", _string(self.version, "plugin.version"))
        object.__setattr__(self, "role", _optional_string(self.role, "plugin.role"))

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe plugin identity."""
        return {"plugin_id": self.plugin_id, "version": self.version, "role": self.role}


# ── provenance record ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScientificProvenance:
    """The derivation record attached to one scientific result."""

    provenance_id: str
    result_kind: str
    software: SoftwareIdentity
    derivation: ElectronicStructureDerivation | PureAnalysisDerivation
    input: InputIdentity
    conditions: Conditions
    limitations: tuple[str, ...] = ()
    science_identity: str | None = None
    plugins: tuple[PluginIdentity, ...] = ()
    schema_version: int = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ProvenanceContractError("provenance.schema_version must be an integer")
        if self.schema_version != PROVENANCE_SCHEMA_VERSION:
            raise UnsupportedProvenanceSchemaVersionError(
                f"unsupported provenance schema_version {self.schema_version!r}; "
                f"only {PROVENANCE_SCHEMA_VERSION} is readable"
            )
        object.__setattr__(
            self, "provenance_id", _string(self.provenance_id, "provenance.provenance_id")
        )
        if self.result_kind not in PROVENANCE_RESULT_KINDS:
            raise ProvenanceContractError(
                f"provenance.result_kind must be one of {sorted(PROVENANCE_RESULT_KINDS)}, "
                f"got {self.result_kind!r}"
            )
        if not isinstance(self.software, SoftwareIdentity):
            raise ProvenanceContractError("provenance.software must be a SoftwareIdentity")
        if not isinstance(self.input, InputIdentity):
            raise ProvenanceContractError("provenance.input must be an InputIdentity")
        if not isinstance(self.conditions, Conditions):
            raise ProvenanceContractError("provenance.conditions must be Conditions")
        expected_type: type[Any] = (
            ElectronicStructureDerivation
            if self.result_kind == "electronic_structure"
            else PureAnalysisDerivation
        )
        if not isinstance(self.derivation, expected_type):
            raise ProvenanceDerivationError(
                f"result_kind {self.result_kind!r} requires a {expected_type.__name__}"
            )
        object.__setattr__(
            self,
            "limitations",
            tuple(_string(item, "provenance.limitations[]") for item in self.limitations),
        )
        if self.science_identity is not None:
            identity = _string(self.science_identity, "provenance.science_identity")
            if SCIENCE_IDENTITY_PATTERN.fullmatch(identity) is None:
                raise ProvenanceContractError(
                    "provenance.science_identity must match ^v2:[0-9a-f]{32}$"
                )
            object.__setattr__(self, "science_identity", identity)
        object.__setattr__(self, "plugins", tuple(self.plugins))
        for index, plugin in enumerate(self.plugins):
            if not isinstance(plugin, PluginIdentity):
                raise ProvenanceContractError(
                    f"provenance.plugins[{index}] must be a PluginIdentity"
                )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "provenance") -> ScientificProvenance:
        """Read a closed provenance record; unknown fields are rejected."""
        data = _mapping(payload, where)
        _reject_unknown(data, _PROVENANCE_KEYS, where)
        _require_keys(
            data,
            (
                "schema_version",
                "provenance_id",
                "result_kind",
                "software",
                "derivation",
                "input",
                "conditions",
            ),
            where,
        )
        version = data["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise ProvenanceContractError(f"{where}.schema_version must be an integer")
        if version != PROVENANCE_SCHEMA_VERSION:
            raise UnsupportedProvenanceSchemaVersionError(
                f"{where}.schema_version is {version!r}; only "
                f"{PROVENANCE_SCHEMA_VERSION} is readable"
            )
        result_kind = _string(data["result_kind"], f"{where}.result_kind")
        raw_derivation = _mapping(data["derivation"], f"{where}.derivation")
        if result_kind == "electronic_structure":
            derivation: ElectronicStructureDerivation | PureAnalysisDerivation = (
                ElectronicStructureDerivation.from_dict(raw_derivation, f"{where}.derivation")
            )
        elif result_kind == "pure_analysis":
            derivation = PureAnalysisDerivation.from_dict(raw_derivation, f"{where}.derivation")
        else:
            raise ProvenanceContractError(
                f"{where}.result_kind must be one of {sorted(PROVENANCE_RESULT_KINDS)}, "
                f"got {result_kind!r}"
            )
        raw_limitations = data.get("limitations", [])
        raw_plugins = data.get("plugins", [])
        plugins: list[PluginIdentity] = []
        for index, item in enumerate(_sequence(raw_plugins, f"{where}.plugins")):
            plugin_data = _mapping(item, f"{where}.plugins[{index}]")
            plugins.append(
                PluginIdentity(
                    plugin_id=_string(
                        plugin_data.get("plugin_id"),
                        f"{where}.plugins[{index}].plugin_id",
                    ),
                    version=_string(
                        plugin_data.get("version"), f"{where}.plugins[{index}].version"
                    ),
                    role=_optional_string(
                        plugin_data.get("role"), f"{where}.plugins[{index}].role"
                    ),
                )
            )
        return cls(
            provenance_id=_string(data["provenance_id"], f"{where}.provenance_id"),
            result_kind=result_kind,
            software=SoftwareIdentity.from_dict(data["software"], f"{where}.software"),
            derivation=derivation,
            input=InputIdentity.from_dict(data["input"], f"{where}.input"),
            conditions=Conditions.from_dict(data["conditions"], f"{where}.conditions"),
            limitations=tuple(
                _string(item, f"{where}.limitations[{index}]")
                for index, item in enumerate(_sequence(raw_limitations, f"{where}.limitations"))
            ),
            science_identity=_optional_string(
                data.get("science_identity"), f"{where}.science_identity"
            ),
            plugins=tuple(plugins),
            schema_version=version,
        )

    @property
    def plugin_versions(self) -> dict[str, str]:
        """Map plugin id to version; empty means no external plugin participated."""
        return {plugin.plugin_id: plugin.version for plugin in self.plugins}

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped provenance (plugins omitted by contract)."""
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "provenance_id": self.provenance_id,
            "result_kind": self.result_kind,
            "software": self.software.to_dict(),
            "derivation": self.derivation.to_dict(),
            "input": self.input.to_dict(),
            "conditions": self.conditions.to_dict(),
            "limitations": list(self.limitations),
        }
        if self.science_identity is not None:
            document["science_identity"] = self.science_identity
        return document

    def to_audit_dict(self) -> dict[str, Any]:
        """Return the audit view including explicit plugin versions.

        This document is **not** the frozen provenance schema: the v1 contract
        has no plugin field.  It is written alongside the schema document so a
        reader can distinguish "no plugin participated" from an omission.
        """
        document = self.to_dict()
        document["plugins"] = [plugin.to_dict() for plugin in self.plugins]
        document["plugin_versions"] = self.plugin_versions
        return document

    def content_digest(self) -> Digest:
        """Return the sha256 digest of the canonical schema document."""
        return Digest.from_bytes(canonical_json(self.to_dict()).encode("utf-8"))


def verify_provenance_inputs(
    provenance: ScientificProvenance, read_root: Path
) -> tuple[dict[str, Any], ...]:
    """Verify every provenance input artifact below *read_root*."""
    if not isinstance(provenance, ScientificProvenance):
        raise ProvenanceContractError("verify_provenance_inputs requires ScientificProvenance")
    rows: list[dict[str, Any]] = []
    for artifact in provenance.input.artifacts:
        try:
            _resolved, actual = verify_artifact_digest(
                artifact.relative_path, artifact.digest, read_root
            )
        except EvidenceContractError as exc:
            message = str(exc)
            if "digest mismatch" in message:
                raise ProvenanceDigestMismatchError(message) from exc
            raise ProvenanceSourceUnavailableError(message) from exc
        rows.append(
            {
                "relative_path": artifact.relative_path,
                "declared_digest": artifact.digest.value,
                "actual_digest": actual,
                "role": artifact.role,
                "status": "PASS",
            }
        )
    if provenance.input.structure_digest is not None:
        structure_artifacts = [
            artifact
            for artifact in provenance.input.artifacts
            if artifact.role in ("input_structure", "optimized_structure", "structure")
        ]
        if not structure_artifacts:
            raise ProvenanceDerivationError(
                "input.structure_digest is declared but no structure-role input artifact exists"
            )
        if all(
            artifact.digest.value != provenance.input.structure_digest.value
            for artifact in structure_artifacts
        ):
            raise ProvenanceDigestMismatchError(
                "input.structure_digest does not match any structure-role input artifact"
            )
    return tuple(rows)


def canonical_provenance_json(provenance: ScientificProvenance) -> str:
    """Return the frozen canonical JSON encoding of the schema document."""
    if not isinstance(provenance, ScientificProvenance):
        raise ProvenanceContractError("canonical_provenance_json requires ScientificProvenance")
    return canonical_json(provenance.to_dict())


def provenance_from_dict(payload: Any) -> ScientificProvenance:
    """Convenience wrapper around :meth:`ScientificProvenance.from_dict`."""
    return ScientificProvenance.from_dict(payload)


# ── calculation-contract adapters (one direction, lazy import) ─────────────


def _as_calculation_provenance(provenance: Any) -> Any:
    """Import and type-check the runtime ``Provenance`` lazily."""
    from cccp.calculation.contracts import Provenance as CalculationProvenance

    if not isinstance(provenance, CalculationProvenance):
        raise ProvenanceContractError(
            f"expected a cccp.calculation.contracts.Provenance, got {type(provenance).__name__}"
        )
    return provenance


def software_identity_from_calculation_provenance(
    provenance: Any,
    *,
    software_id: str | None = None,
    distribution: str | None = None,
    executable_path: str | None = None,
) -> SoftwareIdentity:
    """Adapt the runtime backend/version pair into a :class:`SoftwareIdentity`.

    The runtime ``Provenance`` carries no distinct software id, so ``backend``
    supplies it unless the caller names one explicitly.  A missing version is
    rejected: the frozen schema requires a non-empty ``software_version`` and a
    placeholder would make the record look more complete than it is.
    """
    runtime = _as_calculation_provenance(provenance)
    backend = _string(runtime.backend, "calculation_provenance.backend")
    version = _string(runtime.version, "calculation_provenance.version")
    if not version.strip():
        raise ProvenanceContractError(
            "calculation provenance carries no software version; refusing to invent one"
        )
    return SoftwareIdentity(
        software_id=backend
        if software_id is None
        else _string(software_id, "software.software_id"),
        software_version=version,
        distribution=distribution,
        executable_path=executable_path,
    )


def method_identity_from_calculation_provenance(
    provenance: Any,
    *,
    method_family: str = "unknown",
    dispersion: str | None = None,
    hamiltonian: str | None = None,
) -> MethodIdentity:
    """Adapt the runtime method string into a :class:`MethodIdentity`.

    The runtime record names a method but not its family, dispersion or
    Hamiltonian; the family therefore defaults to the frozen ``unknown``
    member instead of being guessed.  Basis and electronic state are not part
    of the runtime record at all and are never produced by this adapter.
    """
    runtime = _as_calculation_provenance(provenance)
    method_id = _string(runtime.method, "calculation_provenance.method")
    if not method_id.strip():
        raise ProvenanceContractError(
            "calculation provenance carries no method; refusing to invent one"
        )
    return MethodIdentity(
        method_id=method_id,
        method_family=method_family,
        dispersion=dispersion,
        hamiltonian=hamiltonian,
    )


def input_digest_from_calculation_provenance(provenance: Any) -> Digest:
    """Return the runtime ``input_signature`` as a :class:`Digest` when it is sha256.

    The signature is free text, not a digest type.  It is accepted only when it
    is a bare or ``sha256:``-prefixed sha256 value; anything else (for example
    a composite signature) raises instead of being truncated or hashed into a
    digest that does not describe the inputs.
    """
    runtime = _as_calculation_provenance(provenance)
    signature = _string(runtime.input_signature, "calculation_provenance.input_signature")
    try:
        return digest_from_checksum(signature)
    except EvidenceContractError as exc:
        raise ProvenanceContractError(
            f"calculation input_signature is not a sha256 digest: {exc}"
        ) from exc


def provenance_artifact_ref_from_calculation_artifact(
    artifact: Any,
    *,
    role: str,
    units: str | None = None,
    read_root: Path | None = None,
) -> ProvenanceArtifactRef:
    """Adapt a runtime ``ArtifactRef`` into a :class:`ProvenanceArtifactRef`.

    Delegates path/digest handling to the evidence adapter so both science
    contracts agree, then drops the claim-only ``quantity`` slot.  ``role`` is
    required and ``units=None`` is explicit.
    """
    source_ref = artifact_source_ref_from_calculation_artifact(
        artifact,
        role=role,
        units=units,
        read_root=read_root,
    )
    return ProvenanceArtifactRef(
        relative_path=source_ref.artifact_path,
        digest=source_ref.digest,
        role=source_ref.role,
        units=source_ref.units,
    )


# ── historical payload adapter ─────────────────────────────────────────────

_PROVENANCE_FIELD_TREE: Final[dict[str, Any]] = {
    "keys": _PROVENANCE_KEYS,
    "children": {
        "software": {"keys": _SOFTWARE_KEYS},
        "derivation": {
            "keys": _ELECTRONIC_DERIVATION_KEYS | _PURE_ANALYSIS_KEYS,
            "children": {
                "method": {"keys": _METHOD_KEYS},
                "basis": {"keys": _BASIS_KEYS},
                "electronic_state": {"keys": _ELECTRONIC_STATE_KEYS},
                "input_digest": {"keys": DIGEST_KEYS},
            },
        },
        "input": {
            "keys": _INPUT_KEYS,
            "children": {
                "artifacts": {"keys": _ARTIFACT_KEYS, "list": True},
                "structure_digest": {"keys": DIGEST_KEYS},
            },
        },
        "conditions": {"keys": _CONDITION_KEYS},
    },
}


def _collect_unknown_provenance_paths(value: Any, spec: Mapping[str, Any], path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    declared: frozenset[str] = spec["keys"]
    children: Mapping[str, Mapping[str, Any]] = spec.get("children", {})
    found: list[str] = []
    for key, child_value in value.items():
        child_path = f"{path}.{key}"
        if key not in declared:
            found.append(child_path)
            continue
        child_spec = children.get(key)
        if child_spec is None:
            continue
        if child_spec.get("list"):
            if not isinstance(child_value, (list, tuple)):
                continue
            for index, item in enumerate(child_value):
                found.extend(
                    _collect_unknown_provenance_paths(item, child_spec, f"{child_path}[{index}]")
                )
        else:
            found.extend(_collect_unknown_provenance_paths(child_value, child_spec, child_path))
    return found


def unknown_provenance_field_paths(payload: Any) -> tuple[str, ...]:
    """Return the sorted JSON paths a v1 reader would reject, without coercion."""
    if not isinstance(payload, Mapping):
        return ()
    return tuple(sorted(_collect_unknown_provenance_paths(payload, _PROVENANCE_FIELD_TREE, "$")))


@dataclass(frozen=True, slots=True)
class HistoricalProvenanceRecord:
    """A readable historical provenance payload with no execution eligibility.

    The payload is preserved verbatim; a missing field stays missing and an
    unknown version is never upgraded to the current one.  Recovered results
    must not be built from this record: only :meth:`ScientificProvenance.from_dict`
    yields an object eligible for new work.
    """

    payload: dict[str, Any]
    schema_version: int | None
    adapter: str = "historical_provenance_v1"
    execution_eligible: bool = False
    recovery_eligible: bool = False

    @property
    def historical(self) -> bool:
        """Whether the record predates the current provenance schema."""
        return self.schema_version != PROVENANCE_SCHEMA_VERSION

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        """Undeclared field paths retained verbatim in :attr:`payload`."""
        return unknown_provenance_field_paths(self.payload)

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the preserved payload (byte-stable re-read)."""
        return copy.deepcopy(self.payload)


def read_historical_provenance(
    payload: Any, *, schema_version: int | None = None
) -> HistoricalProvenanceRecord:
    """Read an older provenance payload without canonical coercion.

    No field is defaulted and no unknown field is removed; the record is
    explicitly not eligible for execution or recovery.
    """
    data = _mapping(payload, "historical_provenance")
    declared = schema_version
    if declared is None:
        raw = data.get("schema_version")
        if isinstance(raw, int) and not isinstance(raw, bool):
            declared = raw
    return HistoricalProvenanceRecord(
        payload=copy.deepcopy(data),
        schema_version=declared,
        execution_eligible=False,
        recovery_eligible=False,
    )
