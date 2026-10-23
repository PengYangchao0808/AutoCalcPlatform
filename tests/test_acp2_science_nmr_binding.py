"""A-24a: the NMR shielding/comparison capability bound to the science protocol.

Every NMR value in ``tests/fixtures/acp2/science/cross_domain/
nmr_shielding_constructed.json`` is **constructed** and labelled as such.  No
QC program ran and no experimental spectrum was acquired; the numbers only make
the TMS conversion, assignment and calibration rules testable.

The suite pins the A-24a contract:

* the registered descriptor declares atom mapping and reference conditions;
* the binding executes exactly one existing task/analysis implementation;
* the ordered assignment key invalidates an old assignment after an atom-order
  change (including a same-element permutation);
* a missing model asset yields a typed ``unavailable``/``not_evaluated`` result;
* the comparison emits the same frozen ``EvidenceReport``/provenance contract
  as the other science domains.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from cccp.analysis.nmr.comparison import (
    NMR_COMPARISON_MODULE,
    NMR_COMPARISON_VERSION,
    NmrAssignmentInvalidatedError,
    NmrAtomMapping,
    NmrAtomMappingError,
    NmrComparisonInputError,
    NmrExperimentalSource,
    NmrReferenceConditions,
    NmrShieldingComparisonInput,
    atom_mapping_digest,
    compare_nmr_shielding,
    model_asset_record,
)
from cccp.analysis.nmr.equivalence import build_all_labels
from cccp.analysis.nmr.models import ExperimentalPeak
from cccp.analysis.research.evidence import (
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
from cccp.calculation.capabilities.task_binding import (
    TASK_ENTRYPOINTS,
    ScientificPreconditionError,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = (
    REPO
    / "tests"
    / "fixtures"
    / "acp2"
    / "science"
    / "cross_domain"
    / "nmr_shielding_constructed.json"
)
MODULE_PATH = REPO / "src" / "cccp" / "analysis" / "nmr" / "comparison.py"


# ── fixture builders ───────────────────────────────────────────────────────


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _experimental(doc: dict[str, Any]) -> NmrExperimentalSource:
    experiment = doc["experiment"]
    peaks = tuple(ExperimentalPeak(**peak) for peak in experiment["peaks"])
    return NmrExperimentalSource(
        source_ref=experiment["source_ref"],
        peaks=peaks,
        equivalence_groups=tuple(tuple(group) for group in experiment["equivalence_groups"]),
        omit_atoms=tuple(experiment["omit_atoms"]),
    )


def _write_artifacts(
    root: Path,
    doc: dict[str, Any],
    *,
    model_assets_dir: Path | None = None,
) -> tuple[ProvenanceArtifactRef, ...]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "shielding_table.json").write_text(
        json.dumps(doc["shieldings"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "experimental_spectrum.json").write_text(
        json.dumps(doc["experiment"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "reference_conditions.json").write_text(
        json.dumps(doc["reference"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    version, available, _hashes = model_asset_record(model_assets_dir)
    (root / "model_assets_manifest.json").write_text(
        json.dumps(
            {
                "model_asset_version": version,
                "model_assets_available": available,
                "constructed": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    roles = {
        "shielding_table.json": "shielding_table",
        "experimental_spectrum.json": "experimental_spectrum",
        "reference_conditions.json": "reference_conditions",
        "model_assets_manifest.json": "model_assets_manifest",
    }
    return tuple(
        ProvenanceArtifactRef(
            relative_path=name,
            digest=Digest.from_bytes((root / name).read_bytes()),
            role=role,
        )
        for name, role in roles.items()
    )


def _build_input(
    root: Path,
    *,
    doc: dict[str, Any] | None = None,
    model_assets_dir: Path | None = None,
    expected_mapping_digest: str | None = None,
) -> NmrShieldingComparisonInput:
    payload = copy.deepcopy(doc if doc is not None else _fixture())
    mapping = NmrAtomMapping(
        symbols=tuple(payload["symbols"]),
        atom_labels=tuple(payload["atom_labels"]),
        atom_index_base=int(payload["atom_index_base"]),
        source="constructed fixture",
    )
    shieldings = {int(key): value for key, value in payload["shieldings"].items()}
    artifacts = _write_artifacts(root, payload, model_assets_dir=model_assets_dir)
    return NmrShieldingComparisonInput(
        molecule_id=payload["molecule_id"],
        mapping=mapping,
        shieldings=shieldings,
        experiment=_experimental(payload),
        reference=NmrReferenceConditions(**payload["reference"]),
        source_artifacts=artifacts,
        expected_mapping_digest=expected_mapping_digest,
        model_assets_dir=model_assets_dir,
    )


def _permute_atoms(doc: dict[str, Any], order: tuple[int, ...]) -> dict[str, Any]:
    """Return *doc* with structure-order atoms and shieldings permuted by *order*."""
    permuted = copy.deepcopy(doc)
    old_to_new = {old: new for new, old in enumerate(order)}
    permuted["symbols"] = [doc["symbols"][old] for old in order]
    if list(doc["atom_labels"]) == build_all_labels(list(doc["symbols"])):
        permuted["atom_labels"] = build_all_labels(permuted["symbols"])
    else:
        permuted["atom_labels"] = [doc["atom_labels"][old] for old in order]
    permuted["shieldings"] = {
        str(old_to_new[int(key)]): value for key, value in doc["shieldings"].items()
    }
    groups = [[old_to_new[index] for index in group] for group in doc["equivalence_groups"]]
    permuted["equivalence_groups"] = groups
    permuted["experiment"]["equivalence_groups"] = groups
    return permuted


def _nmr_request_payload() -> dict[str, object]:
    doc = _fixture()
    return {
        "schema_version": 1,
        "task": "nmr_shielding",
        "charge": 0,
        "multiplicity": 1,
        "level": {"method": "mPW1PW91", "basis": "6-311G(d)"},
        "structure": {
            "coordinates": [[float(index), 0.0, 0.0] for index in range(len(doc["symbols"]))],
            "symbols": list(doc["symbols"]),
            "role": "minimum",
            "source": "constructed",
        },
        "options": {"atom_index_base": 0},
        "resources": {"nproc": 1},
    }


# ── fixture provenance ─────────────────────────────────────────────────────


def test_fixture_is_constructed_and_never_claims_a_real_run() -> None:
    doc = _fixture()
    assert doc["schema"] == "acp2.science.domain-fixture"
    assert doc["schema_version"] == 1
    assert doc["constructed"] is True
    assert doc["real_run"] is False
    assert doc["domain"] == "nmr"
    assert "constructed" in doc["description"].lower()
    assert len(doc["symbols"]) == len(doc["atom_labels"]) == len(doc["shieldings"])


# ── descriptor + registration ──────────────────────────────────────────────


def test_descriptor_declares_atom_mapping_and_reference_conditions() -> None:
    descriptor = domain_registry().require("nmr.compare").descriptor
    precondition_ids = {item.id for item in descriptor.preconditions}
    assert {
        "atom_mapping_declared",
        "experimental_spectrum_recorded",
        "reference_conditions_declared",
        "model_assets_available",
    } <= precondition_ids
    completion_ids = {item.id for item in descriptor.completion_checks}
    assert {
        "atom_mapping_consistent",
        "assignment_coverage_complete",
        "reference_conditions_bound",
        "model_assets_present",
    } <= completion_ids
    assert descriptor.provenance_requirements.method_identity == "required"
    notes = " ".join(descriptor.provenance_requirements.notes or ())
    for required in ("mapping", "experimental-spectrum", "solvent", "model-asset"):
        assert required in notes
    assert descriptor.delegates_to_task_kind is None


def test_nmr_shielding_descriptor_delegates_to_the_existing_task_core() -> None:
    descriptor = domain_registry().require("nmr.shielding").descriptor
    assert descriptor.delegates_to_task_kind == "nmr_shielding"
    assert descriptor.execution_semantics.delegation_count == 1
    assert descriptor.execution_semantics.starts_subprocess is True
    assert TASK_ENTRYPOINTS["nmr_shielding"].module == "cccp.calculation.tasks.nmr_shielding"


def test_nmr_family_is_declared_by_the_domain_extension() -> None:
    assert DOMAIN_CAPABILITY_IDS == (
        "nmr.compare",
        "nmr.shielding",
        "thermochemistry.ensemble",
    )
    family = describe_domain_family("nmr")
    assert family.described is True
    assert family.supported is True
    assert family.capability_ids == ("nmr.compare", "nmr.shielding")
    assert "nmr" in UNSUPPORTED_FAMILIES
    assert describe_domain_family("thermochemistry").described is True


# ── binding: exactly one existing implementation ───────────────────────────


def test_compute_binding_reaches_exactly_one_nmr_shielding_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run(request: Any, *, context: object | None = None) -> dict[str, str]:
        calls.append(request.task.value)
        return {"task": request.task.value}

    monkeypatch.setattr("cccp.calculation.tasks.nmr_shielding.run_nmr_shielding", fake_run)
    binding = bind_capability("nmr.shielding", _nmr_request_payload(), registry=domain_registry())
    assert binding.preflight().ready is True
    assert binding.execute() == {"task": "nmr_shielding"}
    assert calls == ["nmr_shielding"]


def test_comparison_binding_executes_the_existing_analysis(tmp_path: Path) -> None:
    payload = _build_input(tmp_path / "artifacts")
    binding = bind_capability("nmr.compare", payload, registry=domain_registry())
    assert binding.is_analysis is True
    assert binding.task_kind is None
    assert binding.delegation_target == f"{NMR_COMPARISON_MODULE}.compare_nmr_shielding"
    assert binding.preflight().ready is True

    outcome = binding.execute()
    assert outcome.status == "supported"
    assert outcome.molecule_id == "constructed-ethanol"


def test_comparison_preflight_writes_nothing(tmp_path: Path) -> None:
    payload = _build_input(tmp_path / "artifacts")
    binding = bind_capability("nmr.compare", payload, registry=domain_registry())
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    binding.preflight()
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


# ── happy path + shared evidence contract ──────────────────────────────────


def test_constructed_comparison_is_supported(tmp_path: Path) -> None:
    outcome = compare_nmr_shielding(_build_input(tmp_path / "artifacts"))
    assert outcome.status == "supported"
    assert outcome.model_assets_available is True
    assert outcome.missing_reference_nuclei == ()
    assert outcome.peak_count == outcome.paired_peak_count == 5
    assert outcome.unmatched_peaks == ()
    assert outcome.unmatched_signals == ()
    assert [row["nucleus"] for row in outcome.regressions] == ["13C", "1H"]
    assert all(row["pair_count"] >= 2 for row in outcome.regressions)
    assert {claim.claim_id for claim in outcome.evidence.claims} == {
        "nmr.atom_mapping",
        "nmr.experimental_source",
        "nmr.reference_conditions",
        "nmr.assignment_coverage",
        "nmr.calibration",
        "nmr.model_assets",
    }
    assert outcome.evidence.overall_claim_status == "supported"


def test_evidence_uses_the_shared_contract_and_sources_verify(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    outcome = compare_nmr_shielding(_build_input(root))
    report = outcome.evidence
    assert isinstance(report, EvidenceReport)
    assert evidence_report_from_dict(report.to_dict()) == report
    assert canonical_evidence_json(report) == canonical_evidence_json(
        evidence_report_from_dict(report.to_dict())
    )
    rows = verify_claim_sources(report, root)
    assert rows
    assert all(row.to_dict()["status"] == "PASS" for row in rows)


def test_provenance_records_mapping_reference_scaling_and_model_assets(
    tmp_path: Path,
) -> None:
    outcome = compare_nmr_shielding(_build_input(tmp_path / "artifacts"))
    provenance = outcome.provenance
    assert provenance_from_dict(provenance.to_dict()) == provenance
    assert provenance.result_kind == "pure_analysis"
    assert provenance.software.software_id == NMR_COMPARISON_MODULE
    assert provenance.derivation.algorithm_version == NMR_COMPARISON_VERSION
    parameters = provenance.derivation.parameters
    assert parameters["atom_mapping_digest"] == outcome.atom_mapping_digest
    assert parameters["atom_labels"] == list(outcome.atom_labels)
    assert parameters["experimental_source_ref"] == "experimental_spectrum.json"
    assert parameters["solvent"] == "chloroform"
    assert parameters["scaling_mode"] == "goodman"
    assert parameters["reference_source"] == "goodman-tmsdata"
    assert parameters["model_asset_version"] == outcome.model_asset_version
    assert parameters["model_assets_available"] is True
    assert provenance.conditions.solvent == "chloroform"
    assert provenance.conditions.reference_state == "TMS:goodman-tmsdata"
    assert provenance.conditions.temperature_kelvin == pytest.approx(298.15)
    assert provenance.input.artifacts


# ── atom-order invalidation ────────────────────────────────────────────────


def test_atom_order_change_invalidates_the_old_assignment(tmp_path: Path) -> None:
    original_doc = _fixture()
    original = _build_input(tmp_path / "original", doc=original_doc)
    outcome = compare_nmr_shielding(original)

    swapped = _permute_atoms(original_doc, (1, 0) + tuple(range(2, len(original_doc["symbols"]))))
    replacement = _build_input(tmp_path / "swapped", doc=swapped)
    assert replacement.mapping.symbols == original.mapping.symbols
    assert replacement.mapping.atom_labels == original.mapping.atom_labels
    assert atom_mapping_digest(replacement.mapping, replacement.shieldings) != (
        outcome.atom_mapping_digest
    )
    assert outcome.assignment_is_current(replacement) is False

    with pytest.raises(NmrAssignmentInvalidatedError) as raised:
        compare_nmr_shielding(
            _build_input(
                tmp_path / "pinned",
                doc=swapped,
                expected_mapping_digest=outcome.atom_mapping_digest,
            )
        )
    assert raised.value.expected_digest == outcome.atom_mapping_digest
    assert raised.value.actual_digest != outcome.atom_mapping_digest

    restored = compare_nmr_shielding(_build_input(tmp_path / "restored", doc=original_doc))
    assert restored.atom_mapping_digest == outcome.atom_mapping_digest
    assert outcome.assignment_is_current(
        _build_input(tmp_path / "restored-check", doc=original_doc)
    )


def test_atom_order_change_is_refused_by_binding_preflight(tmp_path: Path) -> None:
    original_doc = _fixture()
    original = compare_nmr_shielding(_build_input(tmp_path / "original", doc=original_doc))
    swapped = _permute_atoms(original_doc, (1, 0) + tuple(range(2, len(original_doc["symbols"]))))
    binding = bind_capability(
        "nmr.compare",
        _build_input(
            tmp_path / "swapped",
            doc=swapped,
            expected_mapping_digest=original.atom_mapping_digest,
        ),
        registry=domain_registry(),
    )
    preflight = binding.preflight()
    assert preflight.ready is False
    failed = {check.id for check in preflight.failures()}
    assert "atom_order_unchanged" in failed
    with pytest.raises(ScientificPreconditionError):
        binding.execute()


def test_label_order_mismatch_is_rejected(tmp_path: Path) -> None:
    doc = _fixture()
    doc["atom_labels"][0], doc["atom_labels"][1] = doc["atom_labels"][1], doc["atom_labels"][0]
    with pytest.raises(NmrAtomMappingError):
        compare_nmr_shielding(_build_input(tmp_path / "artifacts", doc=doc))


# ── missing assets / reference / coverage ─────────────────────────────────


def test_missing_model_asset_yields_typed_unavailable_result(tmp_path: Path) -> None:
    empty = tmp_path / "missing-assets"
    empty.mkdir()
    outcome = compare_nmr_shielding(_build_input(tmp_path / "artifacts", model_assets_dir=empty))
    assert outcome.status == "unavailable"
    assert outcome.model_assets_available is False
    claim = outcome.evidence.claim("nmr.model_assets")
    assert claim is not None
    assert claim.status == "not_evaluated"
    assert {check.check_id for check in claim.missing_checks} == {"artifact_missing"}
    assert "nmr_model_assets_unavailable" in outcome.limitations
    assert outcome.evidence.overall_claim_status == "not_evaluated"


def test_missing_required_reference_is_inconclusive(tmp_path: Path) -> None:
    doc = _fixture()
    doc["reference"]["tms_shieldings"] = {
        "1H": doc["reference"]["tms_shieldings"]["1H"],
    }
    outcome = compare_nmr_shielding(_build_input(tmp_path / "artifacts", doc=doc))
    assert outcome.status == "inconclusive"
    assert outcome.missing_reference_nuclei == ("13C",)
    claim = outcome.evidence.claim("nmr.reference_conditions")
    assert claim is not None
    assert claim.status == "inconclusive"
    assert {check.check_id for check in claim.missing_checks} == {"reference_state_unspecified"}
    assert "nmr_reference_incomplete" in outcome.limitations


def test_unmatched_peak_is_reported_not_dropped(tmp_path: Path) -> None:
    doc = _fixture()
    doc["experiment"]["peaks"].append({"shift_ppm": 9.9, "element": "H", "multiplicity": 1})
    outcome = compare_nmr_shielding(_build_input(tmp_path / "artifacts", doc=doc))
    assert outcome.status == "inconclusive"
    assert len(outcome.unmatched_peaks) == 1
    assert outcome.unmatched_peaks[0]["reason"] == "no_available_signal"
    claim = outcome.evidence.claim("nmr.assignment_coverage")
    assert claim is not None
    assert claim.status == "inconclusive"
    assert {check.check_id for check in claim.missing_checks} == {"partial_output"}
    assert "nmr_coverage_incomplete" in outcome.limitations


# ── corrupt input ──────────────────────────────────────────────────────────


def test_non_finite_shielding_is_rejected(tmp_path: Path) -> None:
    doc = _fixture()
    doc["shieldings"]["0"]["isotropic"] = float("nan")
    with pytest.raises(NmrComparisonInputError):
        _build_input(tmp_path / "artifacts", doc=doc)


def test_invalid_scaling_mode_is_rejected() -> None:
    doc = _fixture()
    with pytest.raises(NmrComparisonInputError):
        NmrReferenceConditions(**{**doc["reference"], "scaling_mode": "bogus"})


def test_shielding_symbol_mismatch_is_rejected(tmp_path: Path) -> None:
    doc = _fixture()
    doc["shieldings"]["0"]["symbol"] = "H"
    with pytest.raises(NmrAtomMappingError):
        _build_input(tmp_path / "artifacts", doc=doc)


def test_model_assets_and_notices_are_preserved() -> None:
    asset_root = REPO / "src" / "cccp" / "analysis" / "nmr" / "model_assets"
    assert (asset_root / "NOTICE.md").is_file()
    assert (asset_root / "LICENSE-DP5").is_file()
    _version, available, hashes = model_asset_record()
    assert available is True
    assert set(hashes) == {
        "atomic_reps.gz",
        "c_w_kde_mean_s_0.025.p",
        "folded_scaled_errors.p",
        "frag_reps.gz",
        "i_w_kde_mean_s_0.025.p",
        "tms_references.txt",
    }
    assert all(value is not None for value in hashes.values())


# ── architecture guard ─────────────────────────────────────────────────────


def test_comparison_module_never_imports_the_task_or_qc_layer() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    forbidden = ("cccp.calculation.tasks", "cccp.qc")
    assert not [name for name in modules if name.startswith(forbidden)]
