"""Direct assertions for the frozen ACP2 Diels-Alder v1 science fixture.

This probe is deliberately *not* the DA pytest selection (that is A-14b / todo
28).  It is the static, runnable assertion the plan requires for the freeze
itself: every fixture document parses, the authoritative
``cccp.analysis.research.reaction`` / ``question`` contracts accept it, the atom
map is unique and one-to-one, elements/charge are conserved, no endo/exo
request exists, the budget/tolerances are frozen with an honest unmeasured
timing basis, and the XYZ atom order is byte-stable against the declared
``atom_ids`` order.

Run it directly::

    python tests/fixtures/acp2/science/diels_alder/v1/validate.py

``--fixture-dir`` lets QA run the same assertions against a deliberately broken
copy (for example one mapping entry removed) without touching the frozen tree.
Standard library plus the pure CCCP research types only; no QC subprocess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from cccp.analysis.research._common import ScienceContractError
from cccp.analysis.research.question import (
    CLAIM_STATUSES,
    EVIDENCE_TYPES,
    Question,
)
from cccp.analysis.research.reaction import (
    Reaction,
    canonical_json,
)

FIXTURE_DIR = Path(__file__).resolve().parent
FIXTURE_ID = "diels_alder-butadiene-ethene-wb97xd4-def2tzvp"
QUESTION_TYPES = {"path_connectivity", "activation_energy"}
SCENARIO_STATUSES = {"PASS", "FAIL", "NOT_VERIFIED"}

_REQUIRED_FILES = (
    "reaction.json",
    "question.json",
    "reactants.xyz",
    "product.xyz",
    "fixture-manifest.json",
    "acceptance.json",
)
_DIGESTED_FILES = (
    "reaction.json",
    "question.json",
    "reactants.xyz",
    "product.xyz",
    "acceptance.json",
)

#: Keys whose numeric value would be a fabricated energy/TS/reference number.
_FORBIDDEN_NUMERIC_KEYS = frozenset(
    {
        "energy_hartree",
        "activation_energy",
        "activation_energy_hartree",
        "activation_energy_kcal_per_mol",
        "ts_energy_hartree",
        "reference_energy_hartree",
        "delta_e_dagger_hartree",
        "delta_e_dagger_kcal_per_mol",
        "delta_g_dagger_hartree",
        "delta_g_dagger_kcal_per_mol",
    }
)

_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    assert isinstance(payload, dict), f"{path.name} must parse as a JSON object"
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _formula_counts(formula: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    position = 0
    for match in _FORMULA_TOKEN.finditer(formula):
        assert match.start() == position, f"malformed formula {formula!r}"
        symbol, raw_count = match.group(1), match.group(2)
        counts[symbol] += int(raw_count) if raw_count else 1
        position = match.end()
    assert position == len(formula) and counts, f"malformed formula {formula!r}"
    return counts


def _parse_xyz(path: Path) -> tuple[str, list[str], list[tuple[float, float, float]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines, f"{path.name} is empty"
    atom_count = int(lines[0].strip())
    comment = lines[1] if len(lines) > 1 else ""
    atom_lines = [line for line in lines[2:] if line.strip()]
    assert len(atom_lines) == atom_count, (
        f"{path.name}: declared {atom_count} atoms but found {len(atom_lines)}"
    )
    symbols: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in atom_lines:
        fields = line.split()
        assert len(fields) == 4, f"{path.name}: malformed atom line {line!r}"
        symbols.append(fields[0])
        values = tuple(float(value) for value in fields[1:])
        assert len(values) == 3
        coordinates.append(values)  # type: ignore[arg-type]
    return comment, symbols, coordinates


def _walk(node: Any, path: str = "$") -> Any:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def _assert_no_selectivity_request(payload: dict[str, Any], name: str) -> None:
    """Reject any endo/exo request; allow only the explicit refusal records."""

    for path, node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            lowered = key.lower()
            if key == "requested":
                assert value is False, f"{name}:{path}.requested must be false, got {value!r}"
            if "endo" not in lowered and "exo" not in lowered:
                continue
            where = f"{name}:{path}.{key}"
            if lowered == "endo_exo_selectivity_supported":
                assert value is False, f"{where} must be false"
            elif lowered == "endo_exo_selectivity":
                if isinstance(value, dict):
                    assert value.get("requested") is False, f"{where}.requested must be false"
                    assert value.get("supported_by_fixture", False) is False, (
                        f"{where}.supported_by_fixture must be false"
                    )
                    assert value.get("status") in {None, "not_evaluated"}, (
                        f"{where}.status must stay not_evaluated"
                    )
                else:
                    assert value is False, f"{where} must be the explicit false refusal"
            else:
                raise AssertionError(
                    f"{where} looks like an endo/exo request/field; the fixture forbids it"
                )


def _assert_atom_order(
    reaction: Reaction,
    reactants_symbols: list[str],
    product_symbols: list[str],
) -> None:
    left_symbols: list[str] = []
    for structure in reaction.left:
        left_symbols.extend(structure.elements)
    right_symbols: list[str] = []
    for structure in reaction.right:
        right_symbols.extend(structure.elements)
    assert left_symbols == reactants_symbols, (
        "reactants.xyz atom order must equal the declared left atom_ids/elements order: "
        f"xyz={reactants_symbols} declared={left_symbols}"
    )
    assert right_symbols == product_symbols, (
        "product.xyz atom order must equal the declared right atom_ids/elements order: "
        f"xyz={product_symbols} declared={right_symbols}"
    )


def _assert_mapping(reaction: Reaction) -> None:
    left_to_right = reaction.atom_mapping.left_to_right
    right_to_left = reaction.atom_mapping.right_to_left
    assert left_to_right, "atom mapping must not be empty"
    assert len(set(left_to_right)) == len(left_to_right), "duplicate left atom in map"
    assert len(set(left_to_right.values())) == len(left_to_right), (
        "the map is not one-to-one: a right atom is claimed twice"
    )
    assert right_to_left == {right: left for left, right in left_to_right.items()}, (
        "left_to_right and right_to_left are not exact inverses"
    )
    left_addresses = {
        f"{structure.structure_id}:{atom_id}"
        for structure in reaction.left
        for atom_id in structure.atom_ids
    }
    right_addresses = {
        f"{structure.structure_id}:{atom_id}"
        for structure in reaction.right
        for atom_id in structure.atom_ids
    }
    assert set(left_to_right) == left_addresses, "every left atom must be mapped exactly once"
    assert set(right_to_left) == right_addresses, "every right atom must be mapped exactly once"
    assert not reaction.atom_mapping.unmapped_left
    assert not reaction.atom_mapping.unmapped_right


def _assert_conservation(reaction: Reaction) -> tuple[Counter[str], Counter[str]]:
    left_elements: Counter[str] = Counter()
    left_formula: Counter[str] = Counter()
    left_charge = 0
    for structure in reaction.left:
        assert len(structure.atom_ids) == len(structure.elements), (
            f"{structure.structure_id}: atom_ids/elements length mismatch"
        )
        left_elements.update(structure.elements)
        formula = _formula_counts(structure.formula)
        assert formula == Counter(structure.elements), (
            f"{structure.structure_id}: formula {structure.formula} disagrees with elements"
        )
        left_formula.update(formula)
        left_charge += structure.charge

    right_elements: Counter[str] = Counter()
    right_charge = 0
    total_atoms = 0
    for structure in reaction.right:
        assert len(structure.atom_ids) == len(structure.elements), (
            f"{structure.structure_id}: atom_ids/elements length mismatch"
        )
        right_elements.update(structure.elements)
        formula = _formula_counts(structure.formula)
        assert formula == Counter(structure.elements), (
            f"{structure.structure_id}: formula {structure.formula} disagrees with elements"
        )
        right_charge += structure.charge
        total_atoms += len(structure.atom_ids)

    assert left_elements == right_elements, (
        f"elements not conserved: left={dict(left_elements)} right={dict(right_elements)}"
    )
    assert left_formula == right_elements, "side formulas do not balance"
    assert left_charge == right_charge, (
        f"net charge not conserved: left={left_charge} right={right_charge}"
    )
    assert total_atoms == sum(len(structure.atom_ids) for structure in reaction.left)
    return left_elements, right_elements


def _assert_frozen_science(manifest: dict[str, Any]) -> None:
    params = manifest["science_parameters"]
    assert params["method"] == "wB97X-D4"
    assert params["basis"] == "def2-TZVP"
    assert params["solvent"] is None and params["phase"] == "gas"
    assert params["temperature_k"] == 298.15
    assert params["pressure_pa"] == 100000.0
    assert params["standard_state"] == "1_bar_ideal_gas"
    assert params["charge"] == 0 and params["multiplicity"] == 1
    assert params["closed_shell"] is True
    assert params["electronic_state"]["state_id"] == "state.singlet.s0"

    cap = manifest["candidate_cap"]
    sampling = manifest["sampling_boundaries"]
    assert cap["max_candidate_paths"] == 1
    assert cap["max_ts_candidates"] == 1
    assert cap["max_conformers_per_endpoint"] == 1
    assert sampling["single_conformation"] is True
    assert sampling["conformer_search"] == "none"
    assert sampling["md_sampling"] == "none"
    assert sampling["monte_carlo_sampling"] == "none"
    assert sampling["max_candidate_paths"] == cap["max_candidate_paths"]
    assert sampling["max_ts_candidates_per_path"] == cap["max_ts_candidates"]
    assert sampling["max_conformers_per_endpoint"] == cap["max_conformers_per_endpoint"]

    budget = manifest["resource_budget"]
    assert budget["constructed"] is True
    assert budget["budget_timing_basis"] == "unmeasured"
    assert budget["timing_evidence"] == []
    assert budget["measured_step_timings"] is None
    consequence = budget["h4_open_consequence"]
    assert "H4" in consequence and "OPEN" in consequence.upper()

    tolerances = manifest["tolerances"]
    assert tolerances["atom_count_tolerance"] == 0
    assert tolerances["coordinate_abs_tolerance_angstrom"] == 0.001
    assert tolerances["imaginary_mode_threshold_cm1"] == 0.0
    assert tolerances["low_frequency_threshold_cm1"] == 50.0
    assert tolerances["source_module"] == "cccp.analysis.research.validators"
    assert tolerances["validator_version"] == "1.0.0"


def _assert_run_spec(manifest: dict[str, Any]) -> None:
    run_spec = manifest["run_spec"]
    science = run_spec["science_deadline_seconds"]
    export = run_spec["export_budget_seconds"]
    aggregate = run_spec["aggregate_budget_seconds"]
    assert isinstance(science, int) and science > 0
    assert isinstance(export, int) and export > 0
    assert isinstance(aggregate, int) and aggregate > 0
    assert aggregate == science + export, "aggregate budget must equal science + export deadlines"
    assert run_spec["started_at"] == manifest["frozen_at"], (
        "one shared started_at must be used by every deadline"
    )

    queue = run_spec["queue_accounting"]
    assert queue["queue_wait_counts_toward_total_wall_time"] is True
    assert "science_deadline_seconds" in queue["applies_to"]
    assert "aggregate_budget_seconds" in queue["applies_to"]
    assert "export_budget_seconds" in queue["excluded_from"]
    assert queue["policy"]

    stop = run_spec["in_flight_stop_rule"]
    assert stop["requires_stop_confirmation"] is True
    assert stop["partial_results_eligible"] is False
    assert stop["rule"]

    export_window = run_spec["export_window_rule"]
    assert export_window["reserved_seconds"] == export
    assert export_window["dispatching_allowed"] is False
    assert export_window["rule"]


def _assert_acceptance(acceptance: dict[str, Any]) -> None:
    scenario = acceptance["scenario_result"]
    assert scenario["status"] in SCENARIO_STATUSES
    assert scenario["status"] == "NOT_VERIFIED", (
        "a static fixture can never report a PASS scenario result"
    )
    claims = acceptance["claim_status"]
    for question_id in ("path_connectivity", "activation_energy"):
        assert claims[question_id]["status"] in CLAIM_STATUSES
        assert claims[question_id]["status"] == "not_evaluated"
    assert claims["endo_exo_selectivity"]["requested"] is False
    assert claims["endo_exo_selectivity"]["supported_by_fixture"] is False
    h4 = acceptance["h4_closure"]
    assert h4["status"] == "open"
    assert h4["closed"] is False
    assert acceptance["h4_rows"], "H4 rows must be enumerated"
    for row in acceptance["h4_rows"]:
        assert row["state"] == "open"
        assert row["scenario_result"] in SCENARIO_STATUSES
        assert row["claim_status"] in CLAIM_STATUSES
        assert row["scenario_result"] == "NOT_VERIFIED"
        assert row["claim_status"] == "not_evaluated"
    # Scenario result, claim status and H4 closure stay three separate records.
    assert {"scenario_result", "claim_status", "h4_closure"} <= set(acceptance)


def _assert_no_fabricated_numbers(payload: dict[str, Any], name: str) -> None:
    for path, node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key in _FORBIDDEN_NUMERIC_KEYS and isinstance(value, (int, float)):
                raise AssertionError(
                    f"{name}:{path}.{key} carries a fabricated numeric energy/TS value"
                )


def validate_fixture(fixture_dir: Path = FIXTURE_DIR) -> dict[str, Any]:
    """Validate the frozen fixture and return the mapped-reaction report."""

    for name in _REQUIRED_FILES:
        path = fixture_dir / name
        assert path.is_file(), f"missing required fixture file {name}"

    reaction_payload = _load_json(fixture_dir / "reaction.json")
    question_set = _load_json(fixture_dir / "question.json")
    manifest = _load_json(fixture_dir / "fixture-manifest.json")
    acceptance = _load_json(fixture_dir / "acceptance.json")
    _parse_xyz(fixture_dir / "reactants.xyz")
    _parse_xyz(fixture_dir / "product.xyz")

    # 1. Frozen digests: the committed bytes must match the manifest.
    file_digests = manifest["file_digests"]
    for name in _DIGESTED_FILES:
        assert file_digests[name] == _sha256(fixture_dir / name), (
            f"{name} bytes drifted from the frozen manifest digest"
        )

    # 2. Authoritative reaction contract (v1) and byte-stable canonical form.
    reaction = Reaction.from_dict(reaction_payload)
    reaction_text = (fixture_dir / "reaction.json").read_text(encoding="utf-8")
    assert reaction.to_json() == reaction_text, (
        "reaction.json is not the canonical byte form of the parsed reaction"
    )
    assert reaction.reaction_scope == "net_reaction", (
        "contract v1 carries no TS/IRC evidence field, so an elementary-step claim must not "
        "be asserted here"
    )
    intended_kind = manifest["scope"]["intended_candidate_kind"]
    assert intended_kind == "single_elementary_concerted_step", (
        "the intended candidate kind must stay explicitly recorded as intent, not a claim"
    )
    assert reaction.schema_version == 1
    assert manifest["reaction"]["reaction_id"] == reaction.reaction_id
    assert manifest["fixture_id"] == FIXTURE_ID
    assert acceptance["fixture_id"] == FIXTURE_ID

    # 3. Unique one-to-one map and full coverage.
    _assert_mapping(reaction)

    # 4. Conserved elements, formulas and net charge.
    left_elements, right_elements = _assert_conservation(reaction)

    # 5. Atom order byte-stability against the declared ids.
    _, reactant_symbols, _ = _parse_xyz(fixture_dir / "reactants.xyz")
    _, product_symbols, _ = _parse_xyz(fixture_dir / "product.xyz")
    _assert_atom_order(reaction, reactant_symbols, product_symbols)

    # 6. Questions: only the two allowed types/ids, authoritative v1 contract.
    assert set(question_set) >= {"questions", "allowed_question_ids", "out_of_scope"}
    assert question_set["fixture_id"] == FIXTURE_ID
    questions = [Question.from_dict(item) for item in question_set["questions"]]
    assert len(questions) == 2
    assert {question.question_type for question in questions} == QUESTION_TYPES
    assert {question.question_id for question in questions} == set(
        question_set["allowed_question_ids"]
    )
    for question, payload in zip(questions, question_set["questions"], strict=True):
        assert json.loads(question.to_json()) == payload, (
            f"{question.question_id} does not round-trip byte-stably"
        )
        assert set(question.required_evidence_types) <= EVIDENCE_TYPES
        assert "energy_difference" in question.required_evidence_types or (
            question.question_type == "path_connectivity"
        )

    # 7. Explicit endo/exo refusal; no request anywhere in any document.
    for name, payload in (
        ("reaction.json", reaction_payload),
        ("question.json", question_set),
        ("fixture-manifest.json", manifest),
        ("acceptance.json", acceptance),
    ):
        _assert_no_selectivity_request(payload, name)

    # 8. Frozen science parameters, candidate cap, timing basis and tolerances.
    _assert_frozen_science(manifest)
    _assert_run_spec(manifest)

    # 9. Honest acceptance: scenario result, claim status and H4 closure separate.
    _assert_acceptance(acceptance)
    assert manifest["constructed"] is True and acceptance["constructed"] is True
    assert manifest["real_run"] is False
    assert manifest["run_key"] is None and manifest["run_key_gap"]
    assert manifest["frozen_before_real_run"] is True
    _assert_no_fabricated_numbers(manifest, "fixture-manifest.json")
    _assert_no_fabricated_numbers(acceptance, "acceptance.json")

    mapped_reaction = [
        f"{left} -> {right}" for left, right in sorted(reaction.atom_mapping.left_to_right.items())
    ]
    bond_changes = [
        "{}: {} ({}) -> {} ({})".format(
            change.change_type,
            " + ".join(change.left_atoms or ()),
            change.bond_order_left,
            " + ".join(change.right_atoms or ()),
            change.bond_order_right,
        )
        for change in reaction.bond_changes
    ]
    formula = "".join(f"{symbol}{count}" for symbol, count in sorted(right_elements.items()))
    return {
        "fixture_id": FIXTURE_ID,
        "files": list(_REQUIRED_FILES),
        "reaction_id": reaction.reaction_id,
        "reaction_scope": reaction.reaction_scope,
        "questions": [question.question_type for question in questions],
        "mapped_pairs": len(reaction.atom_mapping.left_to_right),
        "mapped_reaction": mapped_reaction,
        "bond_changes": bond_changes,
        "conserved_formula": formula,
        "charge_left": sum(structure.charge for structure in reaction.left),
        "charge_right": sum(structure.charge for structure in reaction.right),
        "atom_order": {
            "reactants": reactant_symbols,
            "product": product_symbols,
        },
        "budget_timing_basis": manifest["resource_budget"]["budget_timing_basis"],
        "scenario_result": acceptance["scenario_result"]["status"],
        "claim_status": {key: value["status"] for key, value in acceptance["claim_status"].items()},
        "h4_closure": acceptance["h4_closure"]["status"],
    }


def _print_report(report: dict[str, Any]) -> None:
    print(f"fixture: {report['fixture_id']}")
    print(f"reaction: {report['reaction_id']} ({report['reaction_scope']})")
    print(f"questions: {', '.join(report['questions'])}")
    print(f"mapped pairs: {report['mapped_pairs']}")
    for line in report["mapped_reaction"]:
        print(f"  {line}")
    print("bond changes:")
    for line in report["bond_changes"]:
        print(f"  {line}")
    print(
        "conserved elements: "
        f"{report['conserved_formula']} | charge {report['charge_left']} -> "
        f"{report['charge_right']}"
    )
    print(
        "atom order: reactants="
        + "".join(report["atom_order"]["reactants"])
        + " product="
        + "".join(report["atom_order"]["product"])
    )
    print(f"budget timing basis: {report['budget_timing_basis']}")
    print(f"scenario result: {report['scenario_result']}")
    print(f"claim status: {report['claim_status']}")
    print(f"H4 closure: {report['h4_closure']}")
    print("OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert the frozen ACP2 Diels-Alder v1 science fixture."
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=FIXTURE_DIR,
        help="fixture directory to validate (default: this file's directory)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = validate_fixture(args.fixture_dir)
    except (AssertionError, ScienceContractError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(canonical_json(report))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
