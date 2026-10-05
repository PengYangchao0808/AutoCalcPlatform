# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryIsInstance=false
"""NMR protocol spec: six recorded segments + three honesty modes (todo 29 / gap G04).

An :class:`NmrProtocolSpec` records WHAT ACTUALLY RAN for one candidate —
never an upgrade:

* ``sampling`` — CENSO preset + which parts executed (calling CENSO is not
  DFT-optimized geometry: ``censo-light`` runs prescreening/screening only);
* ``geometry`` — whether an optimization level executed and at which level
  (``None`` when unknown, e.g. a prebuilt/foreign ensemble);
* ``population_energy`` — energy window + Boltzmann temperature in force;
* ``shielding`` — the GIAO method/basis/solvent_model actually executed;
* ``reference`` — TMS source (exact match / gas-phase fallback / custom /
  unknown), the effective solvent, the reference values in force and which
  required nuclei are missing a reference;
* ``statistical_model`` — the DP4 error model + DP5 model/mode presence.

Modes (least-claiming first): ``exploratory`` < ``reference_validation`` <
``acp_calibrated``.

* ``acp_calibrated`` — the statistical model is bound at the recorded level,
  the reference state is present and a DFT optimization actually executed;
* ``reference_validation`` — actual (pinned) reference data was recorded;
* ``exploratory`` — everything else.

Protocol-level validation (``acp.nmr.error_model.validate_protocol_binding``)
turns any mismatch into ``calibration_status == "unvalidated_protocol"`` —
name-only matching of method/basis/model is insufficient (G04).

``fingerprint()`` is recomputable from the recorded values via
``acp.calculations.identity.identity_fingerprint`` (same helper as the T28
GIAO checkpoints): ``from_dict(spec.to_dict()).fingerprint()`` is stable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Final, Literal

from acp.calculations.identity import identity_fingerprint
from acp.nmr.models import lookup_tms_shieldings

__all__ = [
    "GeometrySegment",
    "NmrProtocolSpec",
    "PROTOCOL_MODES",
    "PopulationEnergySegment",
    "ProtocolMode",
    "ReferenceSegment",
    "SPEC_VERSION",
    "SamplingSegment",
    "ShieldingSegment",
    "StatisticalModelSegment",
    "UNVALIDATED_PROTOCOL",
    "aggregate_protocol_block",
    "build_protocol_spec",
    "classify_tms_source",
]

SPEC_VERSION: Final = 1
UNVALIDATED_PROTOCOL: Final = "unvalidated_protocol"

#: Least-claiming order — mixed runs aggregate to the lowest claim.
ProtocolMode = Literal["exploratory", "reference_validation", "acp_calibrated"]
PROTOCOL_MODES: Final[tuple[str, ...]] = ("exploratory", "reference_validation", "acp_calibrated")
_MODE_RANK: Final[dict[str, int]] = {mode: rank for rank, mode in enumerate(PROTOCOL_MODES)}


# ---------------------------------------------------------------------------
# Six segment records (what actually ran)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SamplingSegment:
    """Conformer sampling: preset + which parts executed this run."""

    conformer_preset: str
    crest_executed: bool
    censo_executed: bool
    #: Parts CENSO actually ran; ``()`` = ran none (censo-zero passthrough),
    #: ``None`` = unknown (prebuilt/foreign ensemble).
    parts: tuple[str, ...] | None = None


@dataclass(frozen=True)
class GeometrySegment:
    """Geometry provenance: optimization executed — never upgraded."""

    #: ``True``/``False`` when generation ran here; ``None`` = unknown.
    optimization_executed: bool | None
    #: Level that ACTUALLY executed (``None`` whenever not executed).
    optimization_level: str | None = None


@dataclass(frozen=True)
class PopulationEnergySegment:
    """Population weighting inputs in force."""

    energy_window_kcal: float
    boltzmann_temp: float


@dataclass(frozen=True)
class ShieldingSegment:
    """GIAO level actually executed (effective values, gas solvent = ``""``)."""

    nmr_method: str
    nmr_basis: str
    solvent_model: str


@dataclass(frozen=True)
class ReferenceSegment:
    """TMS reference state: source classification + availability."""

    #: ``exact`` | ``gas_phase_fallback`` | ``custom`` | ``unknown``.
    tms_source: str
    effective_solvent: str
    tms_shieldings: dict[str, float]
    missing_nuclei: tuple[str, ...] = ()
    #: Pinned upstream reference dataset recorded (todo 31 fills); ``False``
    #: means no actual reference data — never auto-set to ``True``.
    reference_data_present: bool = False

    @property
    def available(self) -> bool:
        """Every required nucleus carries a TMS reference."""
        return not self.missing_nuclei


@dataclass(frozen=True)
class StatisticalModelSegment:
    """DP4 error model + DP5 model/mode presence."""

    error_model: str
    dp5_model_id: str | None = None
    dp5_mode: str | None = None
    dp5_model_present: bool = False


# ---------------------------------------------------------------------------
# Spec + fingerprint
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NmrProtocolSpec:
    """Frozen six-segment protocol record with mode + calibration verdict."""

    sampling: SamplingSegment
    geometry: GeometrySegment
    population_energy: PopulationEnergySegment
    shielding: ShieldingSegment
    reference: ReferenceSegment
    statistical_model: StatisticalModelSegment
    spec_version: int = SPEC_VERSION
    #: Conservative defaults: an underived spec claims nothing and validates
    #: nothing — always construct via :func:`build_protocol_spec`.
    mode: str = "exploratory"
    calibration_status: str = UNVALIDATED_PROTOCOL
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record of every value the fingerprint covers."""
        return {
            "spec_version": self.spec_version,
            "mode": self.mode,
            "calibration_status": self.calibration_status,
            "issues": list(self.issues),
            "sampling": asdict(self.sampling),
            "geometry": asdict(self.geometry),
            "population_energy": asdict(self.population_energy),
            "shielding": asdict(self.shielding),
            "reference": asdict(self.reference),
            "statistical_model": asdict(self.statistical_model),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> NmrProtocolSpec:
        """Rebuild a spec from :meth:`to_dict` output (fingerprint-stable)."""
        return cls(
            sampling=SamplingSegment(**dict(payload["sampling"])),  # type: ignore[arg-type]
            geometry=GeometrySegment(**dict(payload["geometry"])),  # type: ignore[arg-type]
            population_energy=PopulationEnergySegment(
                **dict(payload["population_energy"])  # type: ignore[arg-type]
            ),
            shielding=ShieldingSegment(**dict(payload["shielding"])),  # type: ignore[arg-type]
            reference=_reference_from_dict(payload["reference"]),  # type: ignore[arg-type]
            statistical_model=StatisticalModelSegment(
                **dict(payload["statistical_model"])  # type: ignore[arg-type]
            ),
            spec_version=int(payload.get("spec_version", SPEC_VERSION)),
            mode=str(payload.get("mode", "exploratory")),
            calibration_status=str(payload.get("calibration_status", UNVALIDATED_PROTOCOL)),
            issues=tuple(str(item) for item in payload.get("issues", ())),
        )

    def fingerprint(self) -> str:
        """Recomputable protocol identity over the recorded values (v2)."""
        return identity_fingerprint({"scope": "acp_nmr_protocol_spec", "spec": self.to_dict()})

    def derived(self) -> NmrProtocolSpec:
        """Re-derive mode + calibration_status + issues from the segments.

        Issues come from the protocol-level validator in
        ``acp.nmr.error_model`` (deferred import: that module types against
        this one, so the module-level graph stays acyclic).
        """
        from acp.nmr.error_model import validate_protocol_binding

        issues = tuple(validate_protocol_binding(self))
        if self.reference.reference_data_present:
            mode = "reference_validation"
        elif not issues:
            mode = "acp_calibrated"
        else:
            mode = "exploratory"
        status = UNVALIDATED_PROTOCOL if issues else "validated"
        return replace(self, mode=mode, calibration_status=status, issues=issues)


def _reference_from_dict(payload: Mapping[str, Any]) -> ReferenceSegment:
    return ReferenceSegment(
        tms_source=str(payload["tms_source"]),
        effective_solvent=str(payload["effective_solvent"]),
        tms_shieldings={str(k): float(v) for k, v in dict(payload["tms_shieldings"]).items()},
        missing_nuclei=tuple(str(n) for n in payload.get("missing_nuclei", ())),
        reference_data_present=bool(payload.get("reference_data_present", False)),
    )


def build_protocol_spec(
    sampling: SamplingSegment,
    geometry: GeometrySegment,
    population_energy: PopulationEnergySegment,
    shielding: ShieldingSegment,
    reference: ReferenceSegment,
    statistical_model: StatisticalModelSegment,
) -> NmrProtocolSpec:
    """Assemble a spec and derive its mode/verdict from the recorded facts."""
    return NmrProtocolSpec(
        sampling=sampling,
        geometry=geometry,
        population_energy=population_energy,
        shielding=shielding,
        reference=reference,
        statistical_model=statistical_model,
    ).derived()


# ---------------------------------------------------------------------------
# TMS source classification (single vocabulary with _build_nmr_config)
# ---------------------------------------------------------------------------


def classify_tms_source(
    method: str,
    basis: str,
    effective_solvent: str,
    tms_values: Mapping[str, float],
) -> str:
    """Classify where the in-force TMS references came from.

    * ``"exact"`` — values equal the Goodman table row for the run's
      effective solvent (a gas run's ``"none"`` row is keyed, not fallen
      back);
    * ``"gas_phase_fallback"`` — the run is solvated but its solvent has no
      table row, so the silent ``solvent → none`` fallback supplied values;
    * ``"custom"`` — user-supplied values differing from that row;
    * ``"unknown"`` — the table has no row for the level (values are
      unverifiable defaults) or nothing is comparable.
    """
    sol_c, sol_h = lookup_tms_shieldings(method, basis, effective_solvent)
    if sol_c is None and sol_h is None:
        return "unknown"
    table = {"13C": sol_c, "1H": sol_h}
    comparable = {
        nucleus: sigma
        for nucleus, sigma in table.items()
        if sigma is not None and nucleus in tms_values
    }
    if not comparable:
        return "unknown"
    if any(float(tms_values[nucleus]) != sigma for nucleus, sigma in comparable.items()):
        return "custom"
    solvent_key = (effective_solvent or "none").strip().lower()
    if solvent_key in ("", "none"):
        return "exact"
    gas_c, gas_h = lookup_tms_shieldings(method, basis, "none")
    if (gas_c, gas_h) == (sol_c, sol_h):
        return "gas_phase_fallback"
    return "exact"


# ---------------------------------------------------------------------------
# Run-level aggregation (report surface)
# ---------------------------------------------------------------------------


def aggregate_protocol_block(specs: Sequence[NmrProtocolSpec]) -> dict[str, object]:
    """Aggregate per-candidate specs into the report's protocol block.

    Conservative everywhere: the run mode is the least-claiming candidate
    mode, the run is ``unvalidated_protocol`` when ANY candidate is, and the
    run fingerprint chains each recomputable candidate fingerprint.
    """
    candidate_entries: list[dict[str, object]] = []
    fingerprints: list[str] = []
    for spec in specs:
        fingerprint = spec.fingerprint()
        fingerprints.append(fingerprint)
        entry = spec.to_dict()
        entry["fingerprint"] = fingerprint
        candidate_entries.append(entry)
    issues = sorted({issue for spec in specs for issue in spec.issues})
    if specs:
        mode = min((spec.mode for spec in specs), key=lambda m: _MODE_RANK.get(m, 0))
        unvalidated = any(spec.calibration_status == UNVALIDATED_PROTOCOL for spec in specs)
    else:
        # no recorded segments ⇒ nothing to validate, nothing to claim
        mode = "exploratory"
        unvalidated = True
    return {
        "spec_version": SPEC_VERSION,
        "mode": mode,
        "calibration_status": UNVALIDATED_PROTOCOL if unvalidated else "validated",
        "issues": issues,
        "fingerprint": identity_fingerprint(
            {"scope": "acp_nmr_run_protocol", "candidate_fingerprints": fingerprints}
        ),
        "candidates": candidate_entries,
    }
