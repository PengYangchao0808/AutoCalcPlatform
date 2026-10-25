"""A-24b: cross-domain compatibility for NMR and ensemble thermochemistry.

Every value in the two ``tests/fixtures/acp2/science/cross_domain/`` fixtures is
**constructed** and labelled (``constructed=true``, ``real_run=false``).  No QC
program ran, no experimental spectrum was acquired and the Gibbs values are
placeholders.

The suite covers valid, missing, partial and corrupt data for both domains and
pins the shared contract:

* the same ``EvidenceReport``/provenance types and claim-status vocabulary;
* raw weights (sum 0.8) are normalized with the policy, tolerance and factor
  recorded; explicitly normalized weights outside tolerance are rejected;
* negative, zero and non-finite weights are rejected;
* a missing component is reported incomplete, never silently dropped;
* unit/mapping/reference/version rules are documented and domain-separated.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Final

import pytest

from cccp.analysis.ensemble.science import (
    ENSEMBLE_WEIGHT_POLICY_VERSION,
    EnsembleComponent,
    EnsembleThermochemistryInput,
    WeightPolicyError,
    build_ensemble_thermochemistry,
    normalize_ensemble_weights,
)
from cccp.analysis.ensemble.thermo import ensemble_total_gibbs
from cccp.analysis.nmr.comparison import (
    NmrAssignmentInvalidatedError,
    NmrAtomMapping,
    NmrAtomMappingError,
    NmrComparisonInputError,
    NmrExperimentalSource,
    NmrReferenceConditions,
    NmrShieldingComparisonInput,
    compare_nmr_shielding,
    model_asset_record,
)
from cccp.analysis.nmr.equivalence import build_all_labels
from cccp.analysis.nmr.models import ExperimentalPeak
from cccp.analysis.research.evidence import (
    CLAIM_STATUSES,
    Digest,
    EvidenceReport,
    canonical_evidence_json,
    evidence_report_from_dict,
    verify_claim_sources,
)
from cccp.analysis.research.provenance import (
    ProvenanceArtifactRef,
    provenance_from_dict,
)
from cccp.calculation.capabilities import (
    DOMAIN_CAPABILITY_IDS,
    UNSUPPORTED_FAMILIES,
    bind_capability,
    describe_domain_family,
    domain_registry,
)
from cccp.utils.constants import HARTREE_TO_KCAL

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "cross_domain"
NMR_FIXTURE = FIXTURES / "nmr_shielding_constructed.json"
THERMO_FIXTURE = FIXTURES / "thermochemistry_weights_constructed.json"
RULES_DOC = (
    REPO
    / "docs"
    / "reference"
    / "next-change"
    / "acp2-contracts"
    / "science"
    / "cross-domain-compatibility.md"
)

_UNSET: Final = object()


# ── shared helpers ─────────────────────────────────────────────────────────


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(path: Path) -> Digest:
    return Digest.from_bytes(path.read_bytes())


def _verify(report: EvidenceReport, root: Path) -> None:
    rows = verify_claim_sources(report, root)
    assert rows
    assert all(row.to_dict()["status"] == "PASS" for row in rows)


# ── NMR helpers ────────────────────────────────────────────────────────────


def _nmr_input(
    root: Path,
    *,
    doc: dict[str, Any] | None = None,
    model_assets_dir: Path | None = None,
) -> NmrShieldingComparisonInput:
    payload = copy.deepcopy(doc if doc is not None else _load(NMR_FIXTURE))
    root.mkdir(parents=True, exist_ok=True)
    (root / "shielding_table.json").write_text(
        json.dumps(payload["shieldings"], sort_keys=True), encoding="utf-8"
    )
    (root / "experimental_spectrum.json").write_text(
        json.dumps(payload["experiment"], sort_keys=True), encoding="utf-8"
    )
    (root / "reference_conditions.json").write_text(
        json.dumps(payload["reference"], sort_keys=True), encoding="utf-8"
    )
    version, available, _hashes = model_asset_record(model_assets_dir)
    (root / "model_assets_manifest.json").write_text(
        json.dumps({"model_asset_version": version, "model_assets_available": available}),
        encoding="utf-8",
    )
    roles = {
        "shielding_table.json": "shielding_table",
        "experimental_spectrum.json": "experimental_spectrum",
        "reference_conditions.json": "reference_conditions",
        "model_assets_manifest.json": "model_assets_manifest",
    }
    artifacts = tuple(
        ProvenanceArtifactRef(
            relative_path=name,
            digest=_digest(root / name),
            role=role,
        )
        for name, role in roles.items()
    )
    peaks = tuple(ExperimentalPeak(**peak) for peak in payload["experiment"]["peaks"])
    return NmrShieldingComparisonInput(
        molecule_id=payload["molecule_id"],
        mapping=NmrAtomMapping(
            tuple(payload["symbols"]),
            tuple(payload["atom_labels"]),
            int(payload["atom_index_base"]),
        ),
        shieldings={int(key): value for key, value in payload["shieldings"].items()},
        experiment=NmrExperimentalSource(
            source_ref=payload["experiment"]["source_ref"],
            peaks=peaks,
            equivalence_groups=tuple(
                tuple(group) for group in payload["experiment"]["equivalence_groups"]
            ),
        ),
        reference=NmrReferenceConditions(**payload["reference"]),
        source_artifacts=artifacts,
        model_assets_dir=model_assets_dir,
    )


def _permute(doc: dict[str, Any], order: tuple[int, ...]) -> dict[str, Any]:
    permuted = copy.deepcopy(doc)
    old_to_new = {old: new for new, old in enumerate(order)}
    permuted["symbols"] = [doc["symbols"][old] for old in order]
    permuted["atom_labels"] = build_all_labels(permuted["symbols"])
    permuted["shieldings"] = {
        str(old_to_new[int(key)]): value for key, value in doc["shieldings"].items()
    }
    groups = [[old_to_new[index] for index in group] for group in doc["equivalence_groups"]]
    permuted["equivalence_groups"] = groups
    permuted["experiment"]["equivalence_groups"] = groups
    return permuted


# ── thermochemistry helpers ────────────────────────────────────────────────


def _thermo_input(
    root: Path,
    *,
    doc: dict[str, Any] | None = None,
    components: tuple[EnsembleComponent, ...] | None = None,
    weights_mode: str | None = None,
    standard_state: object = _UNSET,
    tolerance: float | None = None,
) -> EnsembleThermochemistryInput:
    payload = copy.deepcopy(doc if doc is not None else _load(THERMO_FIXTURE))
    root.mkdir(parents=True, exist_ok=True)
    (root / "component_energy_table.json").write_text(
        json.dumps({"components": payload["components"]}, sort_keys=True), encoding="utf-8"
    )
    artifact = ProvenanceArtifactRef(
        relative_path="component_energy_table.json",
        digest=_digest(root / "component_energy_table.json"),
        role="component_energy_table",
        units="hartree",
    )
    entries = (
        components
        if components is not None
        else tuple(EnsembleComponent(**component) for component in payload["components"])
    )
    kwargs: dict[str, Any] = {}
    if standard_state is not _UNSET:
        kwargs["standard_state"] = standard_state
    else:
        kwargs["standard_state"] = payload["standard_state"]
    return EnsembleThermochemistryInput(
        ensemble_id=payload["ensemble_id"],
        components=entries,
        temperature_kelvin=float(payload["temperature_kelvin"]),
        weights_mode=weights_mode or payload["weights_mode"],
        source_artifacts=(artifact,),
        weight_tolerance=(
            float(payload["weight_tolerance"]) if tolerance is None else float(tolerance)
        ),
        **kwargs,
    )


def _component(component_id: str, gibbs: float | None, weight: float | None) -> EnsembleComponent:
    return EnsembleComponent(component_id=component_id, gibbs_hartree=gibbs, weight=weight)


# ── fixtures + documentation ───────────────────────────────────────────────


def test_cross_domain_fixtures_are_constructed_and_labelled() -> None:
    for path, domain in ((NMR_FIXTURE, "nmr"), (THERMO_FIXTURE, "thermochemistry")):
        doc = _load(path)
        assert doc["schema"] == "acp2.science.domain-fixture"
        assert doc["schema_version"] == 1
        assert doc["domain"] == domain
        assert doc["constructed"] is True
        assert doc["real_run"] is False
        assert "constructed" in doc["description"].lower()


def test_cross_domain_document_states_unit_mapping_reference_version_rules() -> None:
    text = RULES_DOC.read_text(encoding="utf-8")
    for heading in (
        "## 2. Unit rules",
        "## 3. Mapping rules",
        "## 4. Reference rules",
        "## 5. Version rules",
        "## 6. Missing, partial and corrupt data",
        "## 7. Non-mixing rule",
    ):
        assert heading in text
    for token in (
        "HARTREE_TO_KCAL",
        "atom_mapping_digest",
        "reference_state_unspecified",
        "standard_state_unspecified",
        "NMR_COMPARISON_VERSION",
        "ENSEMBLE_WEIGHT_POLICY_VERSION",
        "model asset",
    ):
        assert token in text


def test_domain_extension_declares_both_families() -> None:
    assert DOMAIN_CAPABILITY_IDS == (
        "nmr.compare",
        "nmr.shielding",
        "thermochemistry.ensemble",
    )
    assert describe_domain_family("nmr").described is True
    thermochemistry = describe_domain_family("thermochemistry")
    assert thermochemistry.described is True
    assert thermochemistry.capability_ids == ("thermochemistry.ensemble",)
    assert "thermochemistry" in UNSUPPORTED_FAMILIES


# ── NMR: valid / missing / partial / corrupt ───────────────────────────────


def test_nmr_valid_data_is_supported_with_the_shared_contract(tmp_path: Path) -> None:
    root = tmp_path / "nmr"
    outcome = compare_nmr_shielding(_nmr_input(root))
    assert outcome.status == "supported"
    report = outcome.evidence
    assert evidence_report_from_dict(report.to_dict()) == report
    assert canonical_evidence_json(report) == canonical_evidence_json(
        evidence_report_from_dict(report.to_dict())
    )
    assert report.overall_claim_status == "supported"
    _verify(report, root)


def test_nmr_missing_model_asset_is_unavailable(tmp_path: Path) -> None:
    empty = tmp_path / "no-assets"
    empty.mkdir()
    outcome = compare_nmr_shielding(_nmr_input(tmp_path / "nmr", model_assets_dir=empty))
    assert outcome.status == "unavailable"
    claim = outcome.evidence.claim("nmr.model_assets")
    assert claim is not None and claim.status == "not_evaluated"
    assert {check.check_id for check in claim.missing_checks} == {"artifact_missing"}


def test_nmr_partial_data_is_inconclusive(tmp_path: Path) -> None:
    doc = _load(NMR_FIXTURE)
    doc["experiment"]["peaks"].append({"shift_ppm": 9.9, "element": "H", "multiplicity": 1})
    outcome = compare_nmr_shielding(_nmr_input(tmp_path / "nmr", doc=doc))
    assert outcome.status == "inconclusive"
    assert outcome.unmatched_peaks
    claim = outcome.evidence.claim("nmr.assignment_coverage")
    assert claim is not None and claim.status == "inconclusive"
    assert {check.check_id for check in claim.missing_checks} == {"partial_output"}


def test_nmr_corrupt_mapping_or_value_is_rejected(tmp_path: Path) -> None:
    doc = _load(NMR_FIXTURE)
    doc["atom_labels"][0], doc["atom_labels"][1] = (
        doc["atom_labels"][1],
        doc["atom_labels"][0],
    )
    with pytest.raises(NmrAtomMappingError):
        compare_nmr_shielding(_nmr_input(tmp_path / "corrupt", doc=doc))

    doc = _load(NMR_FIXTURE)
    doc["shieldings"]["0"]["isotropic"] = float("nan")
    with pytest.raises(NmrComparisonInputError):
        _nmr_input(tmp_path / "non-finite", doc=doc)


# ── thermochemistry: weight policy ────────────────────────────────────────


def test_raw_weights_are_normalized_with_recorded_provenance(tmp_path: Path) -> None:
    outcome = build_ensemble_thermochemistry(_thermo_input(tmp_path / "thermo"))
    assert outcome.status == "supported"
    assert outcome.weights is not None
    record = outcome.weights
    assert record.mode_supplied == "raw"
    assert record.supplied_sum == pytest.approx(0.8)
    assert record.tolerance == pytest.approx(1e-9)
    assert record.normalization == "renormalized_from_raw_weights"
    assert record.normalization_factor == pytest.approx(1.25)
    assert record.normalized == pytest.approx((0.5, 0.375, 0.125))
    assert record.normalized_sum == pytest.approx(1.0)

    parameters = outcome.provenance.derivation.parameters
    assert parameters["weight_mode_supplied"] == "raw"
    assert parameters["weight_tolerance"] == pytest.approx(1e-9)
    assert parameters["weights_supplied_sum"] == pytest.approx(0.8)
    assert parameters["weights_normalized"] == pytest.approx([0.5, 0.375, 0.125])
    assert parameters["weights_normalization"] == "renormalized_from_raw_weights"
    assert outcome.provenance.derivation.algorithm_version == "1"

    assert outcome.rank1_component_id == "conf-1"
    assert outcome.rank1_weight == pytest.approx(0.5)
    expected_total = ensemble_total_gibbs(-100.0, 0.5, 298.15)
    assert outcome.total_gibbs_hartree == pytest.approx(expected_total)
    assert outcome.total_gibbs_kcal_mol == pytest.approx(expected_total * HARTREE_TO_KCAL)
    assert outcome.evidence.overall_claim_status == "supported"
    assert provenance_from_dict(outcome.provenance.to_dict()) == outcome.provenance
    _verify(outcome.evidence, tmp_path / "thermo")


def test_explicitly_normalized_weights_within_tolerance_are_accepted(
    tmp_path: Path,
) -> None:
    outcome = build_ensemble_thermochemistry(
        _thermo_input(
            tmp_path / "thermo",
            weights_mode="normalized",
            components=(
                _component("conf-1", -100.0, 0.5),
                _component("conf-2", -99.998, 0.375),
                _component("conf-3", -99.995, 0.125),
            ),
        )
    )
    assert outcome.status == "supported"
    assert outcome.weights is not None
    assert outcome.weights.mode_supplied == "normalized"
    assert outcome.weights.normalization == "accepted_explicitly_normalized_within_tolerance"
    assert outcome.weights.supplied_sum == pytest.approx(1.0)


def test_explicitly_normalized_weights_outside_tolerance_are_rejected() -> None:
    with pytest.raises(WeightPolicyError) as raised:
        normalize_ensemble_weights([0.5, 0.2, 0.1], mode="normalized")
    assert raised.value.supplied_sum == pytest.approx(0.8)
    assert raised.value.tolerance == pytest.approx(1e-9)
    assert "within" in raised.value.reason


@pytest.mark.parametrize(
    "weights",
    [
        (-0.4, 0.3, 0.1),
        (0.0, 0.0, 0.0),
        (float("nan"), 0.3, 0.1),
        (float("inf"), 0.3, 0.1),
    ],
)
def test_negative_zero_and_non_finite_weights_are_rejected(
    weights: tuple[float, ...],
) -> None:
    with pytest.raises(WeightPolicyError):
        normalize_ensemble_weights(list(weights), mode="raw")


def test_unknown_weight_mode_and_non_positive_tolerance_are_rejected() -> None:
    with pytest.raises(WeightPolicyError):
        normalize_ensemble_weights([0.5, 0.5], mode="silent")
    with pytest.raises(WeightPolicyError):
        normalize_ensemble_weights([0.5, 0.5], mode="raw", tolerance=0.0)


# ── thermochemistry: missing / partial / corrupt ──────────────────────────


def test_missing_component_is_incomplete_and_not_dropped(tmp_path: Path) -> None:
    outcome = build_ensemble_thermochemistry(
        _thermo_input(
            tmp_path / "thermo",
            components=(
                _component("conf-1", -100.0, 0.4),
                _component("conf-2", -99.998, None),
                _component("conf-3", -99.995, 0.1),
            ),
        )
    )
    assert outcome.status == "incomplete"
    assert outcome.components_missing == ("conf-2",)
    assert outcome.total_gibbs_hartree is None
    assert outcome.total_gibbs_kcal_mol is None
    assert outcome.weights is None
    total_claim = outcome.evidence.claim("thermochemistry.total_gibbs")
    assert total_claim is not None and total_claim.status == "inconclusive"
    assert {check.check_id for check in total_claim.missing_checks} == {"partial_output"}
    completeness = outcome.evidence.claim("thermochemistry.ensemble_completeness")
    assert completeness is not None and completeness.status == "inconclusive"
    assert "conf-2" in completeness.missing_checks[0].reason


def test_missing_gibbs_value_is_incomplete(tmp_path: Path) -> None:
    outcome = build_ensemble_thermochemistry(
        _thermo_input(
            tmp_path / "thermo",
            components=(
                _component("conf-1", -100.0, 0.4),
                _component("conf-2", None, 0.3),
                _component("conf-3", -99.995, 0.1),
            ),
        )
    )
    assert outcome.status == "incomplete"
    assert outcome.components_missing == ("conf-2",)
    assert outcome.total_gibbs_hartree is None


def test_invalid_weights_are_rejected_even_when_a_component_is_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(WeightPolicyError):
        build_ensemble_thermochemistry(
            _thermo_input(
                tmp_path / "thermo",
                components=(
                    _component("conf-1", -100.0, -0.4),
                    _component("conf-2", -99.998, None),
                ),
            )
        )


def test_duplicate_component_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(WeightPolicyError):
        _thermo_input(
            tmp_path / "thermo",
            components=(
                _component("conf-1", -100.0, 0.4),
                _component("conf-1", -99.998, 0.3),
            ),
        )


def test_non_finite_component_gibbs_is_rejected() -> None:
    with pytest.raises(WeightPolicyError):
        _component("conf-1", float("nan"), 0.5)


def test_unspecified_standard_state_is_reported(tmp_path: Path) -> None:
    outcome = build_ensemble_thermochemistry(
        _thermo_input(tmp_path / "thermo", standard_state=None)
    )
    assert outcome.status == "inconclusive"
    assert outcome.standard_state_supplied is None
    assert outcome.standard_state_normalized == "1atm"
    claim = outcome.evidence.claim("thermochemistry.conditions")
    assert claim is not None and claim.status == "not_evaluated"
    assert {check.check_id for check in claim.missing_checks} == {"standard_state_unspecified"}
    assert "standard_state_unspecified" in outcome.limitations


def test_thermochemistry_provenance_records_conditions(tmp_path: Path) -> None:
    outcome = build_ensemble_thermochemistry(_thermo_input(tmp_path / "thermo"))
    parameters = outcome.provenance.derivation.parameters
    assert parameters["temperature_kelvin"] == pytest.approx(298.15)
    assert parameters["standard_state_supplied"] == "1atm"
    assert parameters["standard_state_normalized"] == "1atm"
    assert parameters["weight_policy_version"] == ENSEMBLE_WEIGHT_POLICY_VERSION
    assert parameters["energy_unit"] == "hartree"
    assert parameters["derived_energy_unit"] == "kcal_per_mol"
    assert outcome.provenance.conditions.temperature_kelvin == pytest.approx(298.15)
    assert outcome.provenance.conditions.standard_state == "1atm"
    assert outcome.provenance.conditions.reference_state is None


# ── cross-domain contract equality + binding ──────────────────────────────


def test_same_evidence_contract_for_both_domains(tmp_path: Path) -> None:
    nmr_root = tmp_path / "nmr"
    thermo_root = tmp_path / "thermo"
    nmr = compare_nmr_shielding(_nmr_input(nmr_root))
    thermo = build_ensemble_thermochemistry(_thermo_input(thermo_root))
    for report, root in ((nmr.evidence, nmr_root), (thermo.evidence, thermo_root)):
        assert isinstance(report, EvidenceReport)
        assert report.schema_version == 1
        assert evidence_report_from_dict(report.to_dict()) == report
        assert {claim.status for claim in report.claims} <= set(CLAIM_STATUSES)
        assert report.claim_statuses
        assert report.overall_claim_status in CLAIM_STATUSES
        _verify(report, root)
    assert nmr.provenance.schema_version == thermo.provenance.schema_version == 1
    assert nmr.provenance.result_kind == thermo.provenance.result_kind == "pure_analysis"


def test_claim_namespaces_are_disjoint_and_units_are_not_mixed(tmp_path: Path) -> None:
    nmr = compare_nmr_shielding(_nmr_input(tmp_path / "nmr"))
    thermo = build_ensemble_thermochemistry(_thermo_input(tmp_path / "thermo"))
    nmr_claims = {claim.claim_id for claim in nmr.evidence.claims}
    thermo_claims = {claim.claim_id for claim in thermo.evidence.claims}
    assert all(claim_id.startswith("nmr.") for claim_id in nmr_claims)
    assert all(claim_id.startswith("thermochemistry.") for claim_id in thermo_claims)
    assert nmr_claims.isdisjoint(thermo_claims)
    assert nmr.provenance.derivation.parameters["energy_unit"] == "not_applicable"
    assert thermo.provenance.derivation.parameters["energy_unit"] == "hartree"
    assert thermo.total_gibbs_kcal_mol == pytest.approx(
        thermo.total_gibbs_hartree * HARTREE_TO_KCAL
    )


def test_both_domain_bindings_execute_through_the_domain_registry(
    tmp_path: Path,
) -> None:
    nmr_payload = _nmr_input(tmp_path / "nmr")
    thermo_payload = _thermo_input(tmp_path / "thermo")
    nmr_binding = bind_capability("nmr.compare", nmr_payload, registry=domain_registry())
    thermo_binding = bind_capability(
        "thermochemistry.ensemble", thermo_payload, registry=domain_registry()
    )
    assert nmr_binding.is_analysis is True
    assert thermo_binding.is_analysis is True
    assert nmr_binding.preflight().ready is True
    assert thermo_binding.preflight().ready is True
    assert nmr_binding.delegation_target == ("cccp.analysis.nmr.comparison.compare_nmr_shielding")
    assert thermo_binding.delegation_target == (
        "cccp.analysis.ensemble.science.build_ensemble_thermochemistry"
    )
    assert nmr_binding.execute().status == "supported"
    assert thermo_binding.execute().status == "supported"


def test_atom_order_change_still_invalidates_the_nmr_assignment(tmp_path: Path) -> None:
    doc = _load(NMR_FIXTURE)
    original = compare_nmr_shielding(_nmr_input(tmp_path / "original", doc=doc))
    swapped = _permute(doc, (1, 0) + tuple(range(2, len(doc["symbols"]))))
    replacement = _nmr_input(tmp_path / "swapped", doc=swapped)
    assert original.assignment_is_current(replacement) is False

    with pytest.raises(NmrAssignmentInvalidatedError):
        compare_nmr_shielding(
            NmrShieldingComparisonInput(
                molecule_id=replacement.molecule_id,
                mapping=replacement.mapping,
                shieldings=replacement.shieldings,
                experiment=replacement.experiment,
                reference=replacement.reference,
                source_artifacts=replacement.source_artifacts,
                expected_mapping_digest=original.atom_mapping_digest,
            )
        )
