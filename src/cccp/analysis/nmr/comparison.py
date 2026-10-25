"""NMR shielding/comparison capability bound to the ACP2 science protocol (A-24a).

This module is the *typing + evidence* seam around the existing NMR science; it
does not add a second NMR engine.  It consumes

* an existing GIAO shielding table (the ``nmr_shielding`` task payload shape),
* the declared structure-order atom mapping,
* a supplied experimental peak list,
* the reference/solvent/scaling conditions, and
* the packaged DP5/FCHL model-asset set,

and returns one :class:`NmrComparisonOutcome` carrying a frozen
:class:`~cccp.analysis.research.evidence.EvidenceReport` plus a
:class:`~cccp.analysis.research.provenance.ScientificProvenance` record.

The scientific work is delegated verbatim:

* TMS conversion + equivalence averaging:
  :func:`cccp.analysis.nmr.averaging.boltzmann_average_shieldings`;
* peak/signal matching: :func:`cccp.analysis.nmr.assignment.match_assigned`;
* internal calibration: :func:`cccp.analysis.nmr.scaling.fit_scaling_goodman`
  or :func:`cccp.analysis.nmr.scaling.fit_regression`;
* model-asset identity: :func:`cccp.analysis.nmr.reference_validation.asset_hashes`
  and :func:`cccp.analysis.nmr.error_model.dp5_model_available`.

Atom-order safety is structural.  :func:`atom_mapping_digest` hashes the
ordered ``(atom index, element, declared label, shielding)`` rows, so *any*
reindexing of the structure — including a permutation of two atoms of the same
element, where the element/label sequence is unchanged — invalidates the old
assignment key.  A caller may pin the digest (``expected_mapping_digest``); a
mismatch raises :class:`NmrAssignmentInvalidatedError` instead of silently
reusing the previous assignment.

The module is pure analysis: no subprocess, no task import and no QC import.
The only file I/O is the read-only model-asset hashing.  Missing model assets
or an incomplete reference/coverage set never produce a full-confidence
result: the outcome is ``unavailable``/``inconclusive`` with a typed missing
check and an explicit limitation.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from cccp.analysis.nmr.assignment import AssignmentResult, match_assigned
from cccp.analysis.nmr.averaging import boltzmann_average_shieldings
from cccp.analysis.nmr.error_model import dp5_model_available
from cccp.analysis.nmr.models import (
    ConformerShielding,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    normalize_symbol,
)
from cccp.analysis.nmr.reference_validation import asset_hashes
from cccp.analysis.nmr.scaling import fit_regression, fit_scaling_goodman
from cccp.analysis.research._common import canonical_json
from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    ClaimDraft,
    ClaimScope,
    Digest,
    EvidenceMissingCheck,
    EvidenceReport,
    EvidenceValidator,
    ExecutionFacts,
    Limitation,
    Subject,
    assemble_evidence_report,
)
from cccp.analysis.research.provenance import (
    Conditions,
    InputIdentity,
    ProvenanceArtifactRef,
    PureAnalysisDerivation,
    ScientificProvenance,
    SoftwareIdentity,
)
from cccp.analysis.research.validators import VALIDATOR_VERSION

logger = logging.getLogger(__name__)

__all__ = [
    "ATOM_MAPPING_DIGEST_ALGORITHM",
    "DEFAULT_SCALING_MODE",
    "NMR_COMPARISON_MODULE",
    "NMR_COMPARISON_SCHEMA",
    "NMR_COMPARISON_SCHEMA_VERSION",
    "NMR_COMPARISON_VERSION",
    "NMR_MAPPING_VALIDATOR_ID",
    "NMR_REFERENCE_VALIDATOR_ID",
    "NMR_COVERAGE_VALIDATOR_ID",
    "NMR_CALIBRATION_VALIDATOR_ID",
    "NMR_MODEL_ASSET_VALIDATOR_ID",
    "NMR_EXPERIMENT_SOURCE_VALIDATOR_ID",
    "SCALING_MODES",
    "NmrAssignmentInvalidatedError",
    "NmrAtomMapping",
    "NmrAtomMappingError",
    "NmrComparisonContractError",
    "NmrComparisonInputError",
    "NmrComparisonOutcome",
    "NmrExperimentalSource",
    "NmrReferenceConditions",
    "NmrShieldingComparisonInput",
    "atom_mapping_digest",
    "compare_nmr_shielding",
    "model_asset_record",
]

#: Frozen identity of this pure-analysis implementation.
NMR_COMPARISON_MODULE: Final = "cccp.analysis.nmr.comparison"
NMR_COMPARISON_SCHEMA: Final = "acp2.science.nmr-comparison"
NMR_COMPARISON_SCHEMA_VERSION: Final = 1
NMR_COMPARISON_VERSION: Final = "1"

#: Bare sha256 of the ordered assignment key (see :func:`atom_mapping_digest`).
ATOM_MAPPING_DIGEST_ALGORITHM: Final = "sha256"

#: The two calibration regressions the existing scaling module provides.
SCALING_MODES: Final[tuple[str, ...]] = ("goodman", "ols")
DEFAULT_SCALING_MODE: Final = "goodman"

#: Deterministic validator ids (namespace ``check.``).
NMR_MAPPING_VALIDATOR_ID: Final = "check.nmr_atom_mapping"
NMR_EXPERIMENT_SOURCE_VALIDATOR_ID: Final = "check.nmr_experimental_source"
NMR_REFERENCE_VALIDATOR_ID: Final = "check.nmr_reference_conditions"
NMR_COVERAGE_VALIDATOR_ID: Final = "check.nmr_assignment_coverage"
NMR_CALIBRATION_VALIDATOR_ID: Final = "check.nmr_calibration"
NMR_MODEL_ASSET_VALIDATOR_ID: Final = "check.nmr_model_assets"

#: Source-artifact roles this capability understands.
_ROLE_SHIELDING_TABLE: Final = "shielding_table"
_ROLE_EXPERIMENTAL_SPECTRUM: Final = "experimental_spectrum"
_ROLE_REFERENCE_CONDITIONS: Final = "reference_conditions"
_ROLE_MODEL_ASSETS: Final = "model_assets_manifest"
_ROLE_STRUCTURE: Final = "structure"

_NMR_ACTIVE_ELEMENTS: Final = frozenset({"H", "C", "N", "F", "P"})


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class NmrComparisonContractError(ValueError):
    """Base class for rejected NMR comparison payloads."""


class NmrComparisonInputError(NmrComparisonContractError):
    """A required input field is absent, malformed or non-finite."""


class NmrAtomMappingError(NmrComparisonContractError):
    """The declared atom mapping is inconsistent with the shielding table."""


class NmrAssignmentInvalidatedError(NmrComparisonContractError):
    """An old assignment key no longer matches the supplied atom order."""

    def __init__(self, *, expected_digest: str, actual_digest: str) -> None:
        """Create the typed invalidation with both assignment keys."""
        super().__init__(
            "the declared atom order changed: the previous assignment key "
            f"{expected_digest} does not match the current key {actual_digest}; "
            "the old assignment cannot be reused"
        )
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest


# ---------------------------------------------------------------------------
# Declared mapping / conditions / experimental source
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NmrAtomMapping:
    """Structure-order atom mapping declared for one shielding table.

    Attributes:
        symbols: Element symbols in structure order.
        atom_labels: Declared label per structure-order atom (parallel to
            ``symbols``); labels are unique and stable for the comparison.
        atom_index_base: Base of the shielding-table keys (``0`` or ``1``).
        label_scheme: ``per-element`` (``C1``/``H2``) or ``explicit`` (a
            caller-supplied stable scheme).
        source: Provenance note describing where the mapping came from.
    """

    symbols: tuple[str, ...]
    atom_labels: tuple[str, ...]
    atom_index_base: int = 0
    label_scheme: str = "per-element"
    source: str = "declared"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(str(item) for item in self.symbols))
        object.__setattr__(self, "atom_labels", tuple(str(item) for item in self.atom_labels))
        if not self.symbols:
            raise NmrAtomMappingError("atom mapping requires at least one atom")
        if len(self.symbols) != len(self.atom_labels):
            raise NmrAtomMappingError(
                "atom mapping symbols and atom_labels must have the same length "
                f"({len(self.symbols)} != {len(self.atom_labels)})"
            )
        if len(set(self.atom_labels)) != len(self.atom_labels):
            raise NmrAtomMappingError("atom mapping labels must be unique")
        if any(not label.strip() for label in self.atom_labels):
            raise NmrAtomMappingError("atom mapping labels must be non-blank")
        if isinstance(self.atom_index_base, bool) or not isinstance(self.atom_index_base, int):
            raise NmrAtomMappingError("atom_index_base must be an integer")
        if self.atom_index_base < 0:
            raise NmrAtomMappingError("atom_index_base must be >= 0")
        if self.label_scheme not in ("per-element", "explicit"):
            raise NmrAtomMappingError(
                f"label_scheme must be 'per-element' or 'explicit', got {self.label_scheme!r}"
            )
        if not self.source.strip():
            raise NmrAtomMappingError("atom mapping source must be non-blank")
        object.__setattr__(self, "symbols", tuple(normalize_symbol(item) for item in self.symbols))

    def index_of(self, key: int) -> int:
        """Return the structure-order index for a declared-base *key*."""
        index = int(key) - self.atom_index_base
        if not 0 <= index < len(self.symbols):
            raise NmrAtomMappingError(
                f"shielding key {key!r} is outside the declared atom range "
                f"[{self.atom_index_base}, {self.atom_index_base + len(self.symbols) - 1}]"
            )
        return index

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe mapping record."""
        return {
            "symbols": list(self.symbols),
            "atom_labels": list(self.atom_labels),
            "atom_index_base": self.atom_index_base,
            "label_scheme": self.label_scheme,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class NmrReferenceConditions:
    """Solvent/reference/scaling conditions in force for the comparison.

    ``tms_shieldings`` carries the reference shielding per nucleus (the TMS
    reference in force).  ``required_nuclei`` declares which nuclei must have a
    reference before a full-confidence comparison is allowed — a missing
    required reference is reported, never silently replaced by gas phase.
    """

    method: str
    basis: str
    solvent: str | None
    solvent_model: str
    tms_shieldings: Mapping[str, float]
    reference_source: str
    required_nuclei: tuple[str, ...] = ("1H", "13C")
    scaling_mode: str = DEFAULT_SCALING_MODE
    temperature_kelvin: float | None = 298.15
    error_model: str = "goodman-legacy"

    def __post_init__(self) -> None:
        if not str(self.method).strip() or not str(self.basis).strip():
            raise NmrComparisonInputError("reference conditions require method and basis")
        if not str(self.reference_source).strip():
            raise NmrComparisonInputError("reference conditions require a reference source")
        if self.scaling_mode not in SCALING_MODES:
            raise NmrComparisonInputError(
                f"scaling_mode must be one of {SCALING_MODES}, got {self.scaling_mode!r}"
            )
        if self.temperature_kelvin is not None:
            temperature = float(self.temperature_kelvin)
            if not math.isfinite(temperature) or temperature <= 0.0:
                raise NmrComparisonInputError(
                    "temperature_kelvin must be a positive finite number or None"
                )
            object.__setattr__(self, "temperature_kelvin", temperature)
        references: dict[str, float] = {}
        for nucleus, value in dict(self.tms_shieldings).items():
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise NmrComparisonInputError(
                    f"tms_shieldings[{nucleus!r}] is not a number"
                ) from exc
            if not math.isfinite(number):
                raise NmrComparisonInputError(f"tms_shieldings[{nucleus!r}] must be finite")
            references[str(nucleus)] = number
        if not references:
            raise NmrComparisonInputError("reference conditions require at least one TMS value")
        object.__setattr__(self, "tms_shieldings", references)
        object.__setattr__(
            self, "required_nuclei", tuple(str(item) for item in self.required_nuclei)
        )
        object.__setattr__(self, "solvent", None if self.solvent is None else str(self.solvent))
        object.__setattr__(self, "solvent_model", str(self.solvent_model))
        object.__setattr__(self, "error_model", str(self.error_model))

    def missing_nuclei(self) -> tuple[str, ...]:
        """Return required nuclei without a declared reference."""
        return tuple(
            nucleus for nucleus in self.required_nuclei if nucleus not in self.tms_shieldings
        )

    def reference_state_label(self) -> str | None:
        """Return the declared reference state, or ``None`` when incomplete."""
        if self.missing_nuclei():
            return None
        return f"TMS:{self.reference_source}"

    def to_config(self) -> NmrConfig:
        """Return the existing :class:`NmrConfig` this record describes."""
        return NmrConfig(
            nuclei=tuple(self.tms_shieldings),
            nmr_method=self.method,
            nmr_basis=self.basis,
            solvent=self.solvent,
            solvent_model=self.solvent_model,
            tms_shieldings=dict(self.tms_shieldings),
            boltzmann_temp=self.temperature_kelvin or 298.15,
            error_model=self.error_model,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe conditions record."""
        return {
            "method": self.method,
            "basis": self.basis,
            "solvent": self.solvent,
            "solvent_model": self.solvent_model,
            "tms_shieldings": dict(self.tms_shieldings),
            "reference_source": self.reference_source,
            "required_nuclei": list(self.required_nuclei),
            "scaling_mode": self.scaling_mode,
            "temperature_kelvin": self.temperature_kelvin,
            "error_model": self.error_model,
        }


@dataclass(frozen=True, slots=True)
class NmrExperimentalSource:
    """Experimental peak list and the artifact it came from.

    Attributes:
        source_ref: Relative path of the recorded experimental source.
        peaks: Experimental peaks (any mix of labeled and unlabeled).
        equivalence_groups: Optional 0-based structure-index groups; members
            are averaged to one computed signal before matching.
        omit_atoms: Atom labels excluded from the comparison.
    """

    source_ref: str
    peaks: tuple[ExperimentalPeak, ...]
    equivalence_groups: tuple[tuple[int, ...], ...] = ()
    omit_atoms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.source_ref).strip():
            raise NmrComparisonInputError("experimental source requires a source_ref")
        peaks = tuple(self.peaks)
        if not peaks:
            raise NmrComparisonInputError("experimental source requires at least one peak")
        for peak in peaks:
            if not isinstance(peak, ExperimentalPeak):
                raise NmrComparisonInputError("experimental peaks must be ExperimentalPeak")
            if not math.isfinite(float(peak.shift_ppm)):
                raise NmrComparisonInputError("experimental peak shifts must be finite")
            if int(peak.multiplicity) < 1:
                raise NmrComparisonInputError("experimental peak multiplicity must be >= 1")
        object.__setattr__(self, "peaks", peaks)
        object.__setattr__(
            self,
            "equivalence_groups",
            tuple(tuple(int(index) for index in group) for group in self.equivalence_groups),
        )
        object.__setattr__(self, "omit_atoms", tuple(str(item) for item in self.omit_atoms))

    def to_experiment(self) -> ExperimentalNmr:
        """Return the existing :class:`ExperimentalNmr` this record describes."""
        grouped: dict[str, list[ExperimentalPeak]] = {}
        for peak in self.peaks:
            element = normalize_symbol(peak.element)
            grouped.setdefault(element, []).append(peak)
        normalized: dict[str, list[ExperimentalPeak]] = {}
        for element, peaks in grouped.items():
            normalized[element] = [
                ExperimentalPeak(
                    shift_ppm=float(peak.shift_ppm),
                    element=element,
                    atom_label=peak.atom_label,
                    multiplicity=int(peak.multiplicity),
                    label_candidates=peak.label_candidates,
                    index=peak.index if peak.index is not None else position,
                )
                for position, peak in enumerate(peaks)
            ]
        return ExperimentalNmr(
            peaks=normalized,
            equivalence_groups=[list(group) for group in self.equivalence_groups],
            omit_atoms=list(self.omit_atoms),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe experimental record."""
        return {
            "source_ref": self.source_ref,
            "peaks": [peak.to_dict() for peak in self.peaks],
            "equivalence_groups": [list(group) for group in self.equivalence_groups],
            "omit_atoms": list(self.omit_atoms),
        }


# ---------------------------------------------------------------------------
# Comparison input
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NmrShieldingComparisonInput:
    """One NMR shielding/comparison request over existing artifacts.

    The shielding table uses the ``nmr_shielding`` task payload shape
    (``{atom_key: {"symbol": str, "isotropic": float}}``); keys are interpreted
    in ``mapping.atom_index_base``.  ``source_artifacts`` are the digested
    inputs the evidence claims cite; they are never read by this pure function.
    """

    molecule_id: str
    mapping: NmrAtomMapping
    shieldings: Mapping[int, Mapping[str, object]]
    experiment: NmrExperimentalSource
    reference: NmrReferenceConditions
    source_artifacts: tuple[ProvenanceArtifactRef, ...]
    expected_mapping_digest: str | None = None
    model_assets_dir: Path | None = None

    def __post_init__(self) -> None:
        if not str(self.molecule_id).strip():
            raise NmrComparisonInputError("molecule_id must be non-blank")
        if not isinstance(self.mapping, NmrAtomMapping):
            raise NmrComparisonInputError("mapping must be an NmrAtomMapping")
        if not isinstance(self.experiment, NmrExperimentalSource):
            raise NmrComparisonInputError("experiment must be an NmrExperimentalSource")
        if not isinstance(self.reference, NmrReferenceConditions):
            raise NmrComparisonInputError("reference must be an NmrReferenceConditions")
        artifacts = tuple(self.source_artifacts)
        if not artifacts:
            raise NmrComparisonInputError(
                "at least one digested source artifact is required: an unrecorded "
                "comparison cannot produce evidence"
            )
        for artifact in artifacts:
            if not isinstance(artifact, ProvenanceArtifactRef):
                raise NmrComparisonInputError(
                    "source_artifacts entries must be ProvenanceArtifactRef"
                )
        object.__setattr__(self, "source_artifacts", artifacts)
        table = _validated_shieldings(self.shieldings, self.mapping)
        object.__setattr__(self, "shieldings", table)
        if self.expected_mapping_digest is not None:
            expected = str(self.expected_mapping_digest)
            if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                raise NmrComparisonInputError(
                    "expected_mapping_digest must be a bare 64-character lowercase sha256"
                )
            object.__setattr__(self, "expected_mapping_digest", expected)
        if self.model_assets_dir is not None:
            object.__setattr__(self, "model_assets_dir", Path(self.model_assets_dir))

    def preflight_checks(self) -> tuple[dict[str, object], ...]:
        """Return side-effect-free scientific preconditions for the binding."""
        checks: list[dict[str, object]] = []
        checks.append(
            {
                "id": "atom_mapping_declared",
                "satisfied": True,
                "reason": "a structure-order atom mapping with unique labels was declared",
                "expected": "ordered symbols + unique labels",
                "actual": {
                    "atoms": len(self.mapping.symbols),
                    "atom_index_base": self.mapping.atom_index_base,
                },
            }
        )
        digest = atom_mapping_digest(self.mapping, self.shieldings)
        if self.expected_mapping_digest is None:
            digest_ok: bool | None = None
            digest_reason = "no previous assignment key was pinned"
        else:
            digest_ok = digest == self.expected_mapping_digest
            digest_reason = (
                "the supplied atom order matches the pinned assignment key"
                if digest_ok
                else "the atom order changed; the old assignment key is invalid"
            )
        checks.append(
            {
                "id": "atom_order_unchanged",
                "satisfied": digest_ok,
                "reason": digest_reason,
                "expected": self.expected_mapping_digest,
                "actual": digest,
            }
        )
        required = self.reference.required_nuclei
        missing = self.reference.missing_nuclei()
        checks.append(
            {
                "id": "reference_conditions_declared",
                "satisfied": not missing,
                "reason": (
                    "every required nucleus has a declared TMS reference"
                    if not missing
                    else f"missing TMS reference(s) for {list(missing)}"
                ),
                "expected": list(required),
                "actual": {
                    "declared": sorted(self.reference.tms_shieldings),
                    "missing": list(missing),
                },
            }
        )
        checks.append(
            {
                "id": "experimental_spectrum_declared",
                "satisfied": bool(self.experiment.peaks),
                "reason": (
                    "an experimental peak list was supplied"
                    if self.experiment.peaks
                    else "no experimental peaks were supplied"
                ),
                "expected": ">= 1 experimental peak",
                "actual": len(self.experiment.peaks),
            }
        )
        complete = all(
            self.mapping.atom_index_base + index in self.shieldings
            for index in range(len(self.mapping.symbols))
        )
        checks.append(
            {
                "id": "shielding_table_complete",
                "satisfied": complete,
                "reason": (
                    "every declared atom has a shielding entry"
                    if complete
                    else "some declared atoms have no shielding entry"
                ),
                "expected": len(self.mapping.symbols),
                "actual": len(self.shieldings),
            }
        )
        return tuple(checks)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validated_shieldings(
    shieldings: Mapping[int, Mapping[str, object]],
    mapping: NmrAtomMapping,
) -> dict[int, dict[str, object]]:
    """Return the normalized shielding table or raise a typed mapping error."""
    if not isinstance(shieldings, Mapping):
        raise NmrComparisonInputError("shieldings must be a mapping")
    table: dict[int, dict[str, object]] = {}
    upper = mapping.atom_index_base + len(mapping.symbols) - 1
    for raw_key, raw_entry in shieldings.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise NmrAtomMappingError(f"shielding key {raw_key!r} is not an integer") from exc
        if not mapping.atom_index_base <= key <= upper:
            raise NmrAtomMappingError(
                f"shielding key {key!r} is outside the declared atom range "
                f"[{mapping.atom_index_base}, {upper}]"
            )
        if not isinstance(raw_entry, Mapping):
            raise NmrComparisonInputError(
                f"shielding entry {key!r} must be a mapping with 'isotropic'"
            )
        index = mapping.index_of(key)
        declared_symbol = mapping.symbols[index]
        raw_symbol = raw_entry.get("symbol")
        if raw_symbol is not None and normalize_symbol(str(raw_symbol)) != declared_symbol:
            raise NmrAtomMappingError(
                f"shielding entry {key!r} declares symbol {raw_symbol!r}, "
                f"but the mapping declares {declared_symbol!r} at that position"
            )
        raw_isotropic = raw_entry.get("isotropic")
        try:
            isotropic = float(raw_isotropic)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise NmrComparisonInputError(
                f"shielding entry {key!r} has no finite numeric 'isotropic'"
            ) from exc
        if not math.isfinite(isotropic):
            raise NmrComparisonInputError(f"shielding entry {key!r} 'isotropic' must be finite")
        table[key] = {"symbol": declared_symbol, "isotropic": isotropic}
    return table


def atom_mapping_digest(
    mapping: NmrAtomMapping,
    shieldings: Mapping[int, Mapping[str, object]],
) -> str:
    """Return the ordered assignment key of one shielding table.

    The key hashes the ordered ``(atom key, element, declared label, shielding)``
    rows.  Because the table itself is part of the key, permuting two atoms of
    the same element (identical element/label sequence) still changes the key:
    the old index-keyed assignment is not reusable.
    """
    if not isinstance(mapping, NmrAtomMapping):
        raise NmrComparisonInputError("atom_mapping_digest requires an NmrAtomMapping")
    rows: list[list[object]] = []
    for index, symbol in enumerate(mapping.symbols):
        key = mapping.atom_index_base + index
        entry = shieldings.get(key)
        isotropic: float | None = None
        if isinstance(entry, Mapping):
            raw = entry.get("isotropic")
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                value = math.nan
            isotropic = value if math.isfinite(value) else None
        rows.append([key, symbol, mapping.atom_labels[index], isotropic])
    payload = {
        "algorithm": ATOM_MAPPING_DIGEST_ALGORITHM,
        "atom_index_base": mapping.atom_index_base,
        "label_scheme": mapping.label_scheme,
        "rows": rows,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _asset_record(models_dir_text: str | None) -> tuple[str, bool, dict[str, str | None]]:
    """Return ``(version, available, hashes)`` for one model-asset directory."""
    models_dir = None if models_dir_text is None else Path(models_dir_text)
    hashes = dict(asset_hashes(models_dir))
    available = bool(dp5_model_available(models_dir))
    version = "sha256:" + hashlib.sha256(canonical_json(hashes).encode("utf-8")).hexdigest()
    return version, available, hashes


def model_asset_record(models_dir: Path | None = None) -> tuple[str, bool, dict[str, str | None]]:
    """Return the deterministic model-asset version, availability and hashes."""
    return _asset_record(None if models_dir is None else str(models_dir))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NmrComparisonOutcome:
    """One NMR comparison result with provenance and a frozen evidence report."""

    status: str
    molecule_id: str
    atom_mapping_digest: str
    atom_index_base: int
    atom_labels: tuple[str, ...]
    model_asset_version: str
    model_assets_available: bool
    required_nuclei: tuple[str, ...]
    missing_reference_nuclei: tuple[str, ...]
    peak_count: int
    paired_peak_count: int
    unmatched_peaks: tuple[dict[str, object], ...]
    unmatched_signals: tuple[str, ...]
    regressions: tuple[dict[str, object], ...]
    provenance: ScientificProvenance
    evidence: EvidenceReport
    limitations: tuple[str, ...] = ()
    schema: str = NMR_COMPARISON_SCHEMA
    schema_version: int = NMR_COMPARISON_SCHEMA_VERSION

    def assignment_is_current(self, payload: NmrShieldingComparisonInput) -> bool:
        """Return whether *payload* still carries this outcome's assignment key."""
        if not isinstance(payload, NmrShieldingComparisonInput):
            raise NmrComparisonInputError("assignment_is_current requires a comparison input")
        return atom_mapping_digest(payload.mapping, payload.shieldings) == (
            self.atom_mapping_digest
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe comparison record."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "status": self.status,
            "molecule_id": self.molecule_id,
            "atom_mapping_digest": self.atom_mapping_digest,
            "atom_index_base": self.atom_index_base,
            "atom_labels": list(self.atom_labels),
            "model_asset_version": self.model_asset_version,
            "model_assets_available": self.model_assets_available,
            "required_nuclei": list(self.required_nuclei),
            "missing_reference_nuclei": list(self.missing_reference_nuclei),
            "assignment": {
                "peak_count": self.peak_count,
                "paired_peak_count": self.paired_peak_count,
                "unmatched_peaks": [dict(row) for row in self.unmatched_peaks],
                "unmatched_signals": list(self.unmatched_signals),
            },
            "regressions": [dict(row) for row in self.regressions],
            "limitations": list(self.limitations),
            "provenance": self.provenance.to_dict(),
            "evidence": self.evidence.to_dict(),
        }


def compare_nmr_shielding(payload: NmrShieldingComparisonInput) -> NmrComparisonOutcome:
    """Compare an existing shielding table with a supplied experimental list.

    Args:
        payload: The typed comparison request (mapping, shieldings, experiment,
            reference/scaling conditions and digested source artifacts).

    Returns:
        The typed outcome.  ``status`` is ``supported`` only when the mapping,
        experimental source, reference conditions, assignment coverage,
        calibration and model assets all verify.  Missing assets/references or
        partial coverage downgrade to ``unavailable``/``inconclusive`` with
        explicit missing checks and limitations.

    Raises:
        NmrComparisonInputError: A required value is malformed or non-finite.
        NmrAtomMappingError: The shielding table disagrees with the mapping.
        NmrAssignmentInvalidatedError: ``expected_mapping_digest`` does not
            match the current ordered assignment key.
    """
    if not isinstance(payload, NmrShieldingComparisonInput):
        raise NmrComparisonInputError("compare_nmr_shielding requires a comparison input")

    mapping = payload.mapping
    table = _validated_shieldings(payload.shieldings, mapping)
    digest = atom_mapping_digest(mapping, table)
    if payload.expected_mapping_digest is not None and (payload.expected_mapping_digest != digest):
        raise NmrAssignmentInvalidatedError(
            expected_digest=payload.expected_mapping_digest,
            actual_digest=digest,
        )

    model_version, model_available, _hashes = model_asset_record(payload.model_assets_dir)
    missing_reference = payload.reference.missing_nuclei()

    config = payload.reference.to_config()
    conformer = ConformerShielding(
        conformer_id=payload.molecule_id,
        boltzmann_weight=1.0,
        shieldings={
            key: {"symbol": entry["symbol"], "isotropic": entry["isotropic"]}
            for key, entry in table.items()
        },
        symbols=list(mapping.symbols),
    )
    shifts = boltzmann_average_shieldings(
        [conformer],
        list(mapping.symbols),
        config,
        equivalence_groups=(
            payload.experiment.equivalence_groups if payload.experiment.equivalence_groups else None
        ),
        omit_atom_indices=[
            index
            for index, label in enumerate(mapping.atom_labels)
            if label in set(payload.experiment.omit_atoms)
        ],
    )
    _validate_shift_labels(shifts, mapping)

    experiment = payload.experiment.to_experiment()
    assignment = match_assigned(shifts, experiment, use_intensity_weight=True)
    regressions = _fit_regressions(assignment, payload.reference.scaling_mode)
    coverage = _coverage(assignment, shifts, experiment)
    return _assemble_outcome(
        payload=payload,
        digest=digest,
        model_version=model_version,
        model_available=model_available,
        missing_reference=missing_reference,
        shifts=tuple(shifts),
        regressions=regressions,
        coverage=coverage,
    )


def _validate_shift_labels(shifts: Sequence[Any], mapping: NmrAtomMapping) -> None:
    """Reject a shift table whose labels no longer follow the declared mapping."""
    for shift in shifts:
        atom_index = int(shift.atom_index)
        if not 0 <= atom_index < len(mapping.atom_labels):
            raise NmrAtomMappingError(
                f"computed shift references atom index {atom_index} outside the mapping"
            )
        expected = mapping.atom_labels[atom_index]
        if shift.atom_label != expected:
            raise NmrAtomMappingError(
                f"computed shift label {shift.atom_label!r} at atom index {atom_index} "
                f"does not match the declared label {expected!r}; the atom order changed"
            )


def _fit_regressions(
    assignment: AssignmentResult,
    scaling_mode: str,
) -> tuple[dict[str, object], ...]:
    """Fit the existing calibration regression per nucleus over matched pairs."""
    rows: list[dict[str, object]] = []
    pairs = assignment.pairs
    for nucleus in sorted(pairs):
        group = pairs[nucleus]
        if not group:
            continue
        calc = [float(signal.shift_ppm) for signal, _peak in group]
        exp = [float(peak.shift_ppm) for _signal, peak in group]
        fit = fit_scaling_goodman if scaling_mode == "goodman" else fit_regression
        regression, scaled, residuals = fit(calc, exp, nucleus)
        rows.append(
            {
                "nucleus": nucleus,
                "mode": scaling_mode,
                "pair_count": len(group),
                "slope": regression.slope,
                "intercept": regression.intercept,
                "r_squared": regression.r_squared,
                "mae": regression.mae,
                "residual_convention": (
                    "scaled - exp" if scaling_mode == "goodman" else "exp - scaled"
                ),
                "pairs": [
                    {
                        "atom_label": signal.atom_label,
                        "element": signal.symbol,
                        "calc_ppm": calc[index],
                        "exp_ppm": exp[index],
                        "scaled_ppm": scaled[index],
                        "residual": residuals[index],
                    }
                    for index, (signal, peak) in enumerate(group)
                ],
            }
        )
    return tuple(rows)


def _coverage(
    assignment: AssignmentResult,
    shifts: Sequence[Any],
    experiment: ExperimentalNmr,
) -> dict[str, object]:
    """Summarize peak/signal coverage without dropping any diagnostic."""
    unmatched_peaks = tuple(
        {
            "element": entry.element,
            "index": entry.index,
            "shift_ppm": entry.shift_ppm,
            "reason": entry.reason,
            "atom_label": entry.atom_label,
            "label_candidates": list(entry.label_candidates),
        }
        for entry in assignment.unmatched
    )
    matched_labels = {
        signal.atom_label for group in assignment.pairs.values() for signal, _peak in group
    }
    unmatched_signals = tuple(
        sorted(shift.atom_label for shift in shifts if shift.atom_label not in matched_labels)
    )
    peak_count = sum(len(group) for group in experiment.peaks.values())
    paired_peak_count = sum(len(group) for group in assignment.pairs.values())
    return {
        "peak_count": peak_count,
        "paired_peak_count": paired_peak_count,
        "unmatched_peaks": unmatched_peaks,
        "unmatched_signals": unmatched_signals,
        "complete": not unmatched_peaks and not unmatched_signals,
    }


def _source_refs(
    artifacts: Sequence[ProvenanceArtifactRef],
    role: str,
) -> tuple[ArtifactSourceRef, ...]:
    """Return the digested source references for one artifact role."""
    return tuple(
        ArtifactSourceRef(
            artifact_path=artifact.relative_path,
            digest=artifact.digest,
            role=artifact.role,
            units=artifact.units,
        )
        for artifact in artifacts
        if artifact.role == role
    )


def _calibration_complete(
    regressions: Sequence[Mapping[str, object]], shifts: Sequence[Any]
) -> bool:
    """Whether every computed nucleus has a usable, finite calibration."""
    nuclei = {str(shift.nucleus) for shift in shifts}
    if not nuclei:
        return False
    rows = {str(row["nucleus"]): row for row in regressions}
    for nucleus in nuclei:
        row = rows.get(nucleus)
        if row is None or int(row["pair_count"]) < 2:  # type: ignore[arg-type]
            return False
        for name in ("slope", "intercept", "r_squared"):
            value = row.get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return False
    return True


def _assemble_outcome(
    *,
    payload: NmrShieldingComparisonInput,
    digest: str,
    model_version: str,
    model_available: bool,
    missing_reference: tuple[str, ...],
    shifts: tuple[Any, ...],
    regressions: tuple[dict[str, object], ...],
    coverage: Mapping[str, object],
) -> NmrComparisonOutcome:
    """Assemble provenance + evidence from the already-computed comparison facts."""
    mapping = payload.mapping
    artifacts = payload.source_artifacts
    shift_table_refs = _source_refs(artifacts, _ROLE_SHIELDING_TABLE)
    experiment_refs = _source_refs(artifacts, _ROLE_EXPERIMENTAL_SPECTRUM)
    reference_refs = _source_refs(artifacts, _ROLE_REFERENCE_CONDITIONS)
    asset_refs = _source_refs(artifacts, _ROLE_MODEL_ASSETS)

    calibration_ok = _calibration_complete(regressions, shifts)
    coverage_ok = bool(coverage["complete"])
    reference_ok = not missing_reference
    has_shifts = bool(shifts)

    limitation_records: list[Limitation] = [
        Limitation(
            limitation_id="nmr_comparison_scope",
            description=(
                "This report compares an existing GIAO shielding table with a supplied "
                "experimental peak list. It does not re-run shielding calculations, "
                "conformer sampling, DP4/DP5 probabilities or a quantitative validation."
            ),
        )
    ]
    if missing_reference:
        limitation_records.append(
            Limitation(
                limitation_id="nmr_reference_incomplete",
                description=(
                    "One or more required nuclei have no declared TMS reference; the "
                    "shielding-to-shift conversion for those nuclei is not claimed."
                ),
            )
        )
    if not coverage_ok:
        limitation_records.append(
            Limitation(
                limitation_id="nmr_coverage_incomplete",
                description=(
                    "The experimental peak list and the computed signal set do not fully "
                    "cover each other; unmatched entries are reported, not dropped."
                ),
            )
        )
    if not calibration_ok:
        limitation_records.append(
            Limitation(
                limitation_id="nmr_calibration_incomplete",
                description=(
                    "At least one nucleus has fewer than two matched pairs or a non-finite "
                    "regression; no complete calibration is claimed."
                ),
            )
        )
    if not model_available:
        limitation_records.append(
            Limitation(
                limitation_id="nmr_model_assets_unavailable",
                description=(
                    "The packaged DP5/FCHL model assets are missing or unreadable, so no "
                    "model-based statistical result can be claimed for this comparison."
                ),
            )
        )
    limitation_ids = tuple(record.limitation_id for record in limitation_records)

    # ── scientific provenance ──────────────────────────────────────────────
    payload_document = {
        "molecule_id": payload.molecule_id,
        "mapping": mapping.to_dict(),
        "atom_mapping_digest": digest,
        "shieldings": {str(key): dict(entry) for key, entry in sorted(payload.shieldings.items())},
        "experiment": payload.experiment.to_dict(),
        "reference": payload.reference.to_dict(),
        "source_artifacts": [artifact.to_dict() for artifact in artifacts],
        "expected_mapping_digest": payload.expected_mapping_digest,
    }
    input_digest = Digest.from_bytes(canonical_json(payload_document).encode("utf-8"))
    structure_digest = next(
        (
            artifact.digest
            for artifact in artifacts
            if artifact.role in ("structure", "input_structure")
        ),
        None,
    )
    provenance = ScientificProvenance(
        provenance_id=f"nmr-comparison:{payload.molecule_id}",
        result_kind="pure_analysis",
        software=SoftwareIdentity(
            software_id=NMR_COMPARISON_MODULE,
            software_version=NMR_COMPARISON_VERSION,
            distribution="cccp",
        ),
        derivation=PureAnalysisDerivation(
            algorithm="nmr_shielding_comparison",
            algorithm_version=NMR_COMPARISON_VERSION,
            input_digest=input_digest,
            parameters={
                "molecule_id": payload.molecule_id,
                "atom_mapping_digest": digest,
                "atom_index_base": mapping.atom_index_base,
                "atom_label_scheme": mapping.label_scheme,
                "atom_labels": list(mapping.atom_labels),
                "atom_count": len(mapping.symbols),
                "experimental_source_ref": payload.experiment.source_ref,
                "experimental_peak_count": int(coverage["peak_count"]),  # type: ignore[arg-type]
                "solvent": payload.reference.solvent or "none",
                "solvent_model": payload.reference.solvent_model,
                "reference_source": payload.reference.reference_source,
                "reference_state": payload.reference.reference_state_label(),
                "reference_shieldings": [
                    f"{nucleus}={value}"
                    for nucleus, value in sorted(payload.reference.tms_shieldings.items())
                ],
                "required_nuclei": list(payload.reference.required_nuclei),
                "scaling_mode": payload.reference.scaling_mode,
                "temperature_kelvin": payload.reference.temperature_kelvin,
                "error_model": payload.reference.error_model,
                "model_asset_version": model_version,
                "model_assets_available": model_available,
                "energy_unit": "not_applicable",
            },
        ),
        input=InputIdentity(artifacts=artifacts, structure_digest=structure_digest),
        conditions=Conditions(
            temperature_kelvin=payload.reference.temperature_kelvin,
            standard_state=None,
            reference_state=payload.reference.reference_state_label(),
            pressure_pa=None,
            phase="solution" if payload.reference.solvent else "gas",
            solvent=payload.reference.solvent,
        ),
        limitations=limitation_ids,
    )

    # ── deterministic claims ───────────────────────────────────────────────
    molecule_scope = f"{payload.molecule_id}"
    drafts: list[ClaimDraft] = [
        ClaimDraft(
            claim_id="nmr.atom_mapping",
            statement=(
                "The shielding table is bound to the declared structure-order atom "
                "mapping and every computed signal label follows it."
            ),
            status="supported",
            scope=ClaimScope(
                subject=f"{molecule_scope}#atom-mapping",
                applicability="system_specific",
                conditions=(
                    f"atom_index_base={mapping.atom_index_base}; "
                    f"label_scheme={mapping.label_scheme}"
                ),
                not_in_scope=("atom renumbering after this assignment key",),
            ),
            source_refs=shift_table_refs,
            validator=EvidenceValidator(
                NMR_MAPPING_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=("nmr_comparison_scope",),
        ),
        ClaimDraft(
            claim_id="nmr.experimental_source",
            statement=(
                "The experimental peak list used for the comparison is recorded as a "
                "digested source artifact."
            ),
            status="supported" if experiment_refs else "inconclusive",
            scope=ClaimScope(
                subject=f"{molecule_scope}#experimental-source",
                applicability="condition_specific",
                conditions=f"source={payload.experiment.source_ref}",
            ),
            source_refs=experiment_refs,
            missing_checks=(
                ()
                if experiment_refs
                else (
                    EvidenceMissingCheck(
                        check_id="external_artifact_unavailable",
                        reason=(
                            "no digested 'experimental_spectrum' source artifact was "
                            "supplied for the peak list"
                        ),
                    ),
                )
            ),
            validator=EvidenceValidator(
                NMR_EXPERIMENT_SOURCE_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=("nmr_comparison_scope",),
        ),
        ClaimDraft(
            claim_id="nmr.reference_conditions",
            statement=(
                "Every required nucleus has a declared TMS reference and the solvent/"
                "method/basis/scaling conditions in force are recorded."
            ),
            status="supported" if reference_ok else "inconclusive",
            scope=ClaimScope(
                subject=f"{molecule_scope}#reference-conditions",
                applicability="condition_specific",
                conditions=(
                    f"{payload.reference.method}/{payload.reference.basis}; "
                    f"solvent={payload.reference.solvent or 'gas'}; "
                    f"reference={payload.reference.reference_source}; "
                    f"scaling={payload.reference.scaling_mode}"
                ),
            ),
            source_refs=reference_refs,
            missing_checks=(
                ()
                if reference_ok
                else (
                    EvidenceMissingCheck(
                        check_id="reference_state_unspecified",
                        reason=(
                            "missing TMS reference(s) for required nuclei: "
                            f"{', '.join(missing_reference)}"
                        ),
                    ),
                )
            ),
            validator=EvidenceValidator(
                NMR_REFERENCE_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=(
                ("nmr_reference_incomplete",) if not reference_ok else ("nmr_comparison_scope",)
            ),
        ),
        ClaimDraft(
            claim_id="nmr.assignment_coverage",
            statement=(
                "Every experimental peak and every computed signal was paired exactly once."
            ),
            status="supported" if coverage_ok else "inconclusive",
            scope=ClaimScope(
                subject=f"{molecule_scope}#assignment-coverage",
                applicability="system_specific",
                conditions=(
                    f"peaks={coverage['peak_count']}; paired={coverage['paired_peak_count']}"
                ),
            ),
            source_refs=shift_table_refs + experiment_refs,
            missing_checks=(
                ()
                if coverage_ok
                else (
                    EvidenceMissingCheck(
                        check_id="partial_output",
                        reason=(
                            f"{len(coverage['unmatched_peaks'])} unmatched peak(s) and "
                            f"{len(coverage['unmatched_signals'])} unmatched signal(s)"
                        ),
                    ),
                )
            ),
            validator=EvidenceValidator(
                NMR_COVERAGE_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=(
                ("nmr_coverage_incomplete",) if not coverage_ok else ("nmr_comparison_scope",)
            ),
        ),
        ClaimDraft(
            claim_id="nmr.calibration",
            statement=(
                "Each computed nucleus has at least two matched pairs and a finite "
                "internal-scaling regression."
            ),
            status=(
                "supported"
                if calibration_ok
                else ("not_evaluated" if not has_shifts else "inconclusive")
            ),
            scope=ClaimScope(
                subject=f"{molecule_scope}#calibration",
                applicability="condition_specific",
                conditions=f"scaling={payload.reference.scaling_mode}",
            ),
            source_refs=shift_table_refs + experiment_refs,
            missing_checks=(
                ()
                if calibration_ok
                else (
                    EvidenceMissingCheck(
                        check_id="partial_output",
                        reason=(
                            "no computed shifts survived averaging"
                            if not has_shifts
                            else "at least one nucleus has fewer than two matched pairs "
                            "or a non-finite regression"
                        ),
                    ),
                )
            ),
            validator=EvidenceValidator(
                NMR_CALIBRATION_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=(
                ("nmr_calibration_incomplete",) if not calibration_ok else ("nmr_comparison_scope",)
            ),
        ),
        ClaimDraft(
            claim_id="nmr.model_assets",
            statement=(
                "The packaged DP5/FCHL model assets are present and their content-addressed "
                "version is recorded."
            ),
            status="supported" if model_available else "not_evaluated",
            scope=ClaimScope(
                subject=f"{molecule_scope}#model-assets",
                applicability="universal",
                conditions=f"model_asset_version={model_version}",
            ),
            source_refs=asset_refs,
            missing_checks=(
                ()
                if model_available
                else (
                    EvidenceMissingCheck(
                        check_id="artifact_missing",
                        reason=(
                            "the packaged DP5/FCHL model assets are missing or unreadable "
                            f"at model_asset_version={model_version}"
                        ),
                    ),
                )
            ),
            validator=EvidenceValidator(
                NMR_MODEL_ASSET_VALIDATOR_ID, VALIDATOR_VERSION, "deterministic"
            ),
            limitations=(
                ("nmr_model_assets_unavailable",)
                if not model_available
                else ("nmr_comparison_scope",)
            ),
        ),
    ]

    evidence = assemble_evidence_report(
        report_id=f"nmr-comparison:{payload.molecule_id}",
        subject=Subject(
            subject_id=payload.molecule_id,
            subject_kind="calculation_result",
            description=(
                "NMR shielding comparison over an existing GIAO shielding table with a "
                "supplied experimental peak list"
            ),
            input_digests=tuple(artifact.digest for artifact in artifacts),
        ),
        execution=ExecutionFacts(
            terminal_state="completed",
            argv=(f"{NMR_COMPARISON_MODULE}.compare_nmr_shielding",),
            exit_code=None,
        ),
        drafts=drafts,
        limitations=tuple(limitation_records),
        provenance_digest=provenance.content_digest(),
    )

    if not model_available:
        status = "unavailable"
    elif not has_shifts:
        status = "inconclusive"
    elif all(claim.status == "supported" for claim in evidence.claims):
        status = "supported"
    else:
        status = "inconclusive"

    return NmrComparisonOutcome(
        status=status,
        molecule_id=payload.molecule_id,
        atom_mapping_digest=digest,
        atom_index_base=mapping.atom_index_base,
        atom_labels=tuple(mapping.atom_labels),
        model_asset_version=model_version,
        model_assets_available=model_available,
        required_nuclei=tuple(payload.reference.required_nuclei),
        missing_reference_nuclei=tuple(missing_reference),
        peak_count=int(coverage["peak_count"]),  # type: ignore[arg-type]
        paired_peak_count=int(coverage["paired_peak_count"]),  # type: ignore[arg-type]
        unmatched_peaks=tuple(coverage["unmatched_peaks"]),  # type: ignore[arg-type]
        unmatched_signals=tuple(coverage["unmatched_signals"]),  # type: ignore[arg-type]
        regressions=regressions,
        provenance=provenance,
        evidence=evidence,
        limitations=limitation_ids,
    )
