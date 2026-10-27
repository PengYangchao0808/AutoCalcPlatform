"""M4-04 — the limited Diels–Alder acceptance wiring (fast, no QC).

The real QC chain and the recorded evidence live in
``reports/acp2_remediation_20261026/m4-04/`` and are produced by
``scripts/acceptance/acp2_m404_da.py``.  These tests pin the *contract wiring* of
that acceptance script without starting a single QC process:

* the frozen fixture is read as a digest-bound bijection;
* the frozen criteria fingerprint still equals the ADR-008 value;
* the public bounded-recipe entry refuses the frozen nine-step recipe with a
  typed error (recorded as evidence, not swallowed);
* the criterion → validator mapping consumes real ``TaskResult`` shapes and
  produces the four-valued verdicts the ADR freezes (including the honest
  negatives: one-sided IRC, missing frequency, endpoint mismatch);
* ``scenario_result`` never decides ``claim_status``;
* a forged ``supported`` record is rejected;
* H4 can never be closed by the Local row alone;
* the RPH decision and the headless Workbench projection read the same view.

Author: QCcalc Team
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "acceptance" / "acp2_m404_da.py"


def _load_module():
    import sys

    spec = importlib.util.spec_from_file_location("acp2_m404_da", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through ``sys.modules``.
    sys.modules["acp2_m404_da"] = module
    spec.loader.exec_module(module)
    return module


M = _load_module()


# ── constructed real-shaped task results (16 atoms, ADR-008 system) ────────


def _frequency_result(
    *,
    imaginary_mode_index: int = 6,
    imaginary_frequency: float = -520.0,
    atom_count: int = 16,
    energy: float = -233.0,
):
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import FrequencyAnalysis, FrequencyPayload, TaskResult

    mode_count = 3 * atom_count - 6
    mode_frequencies = {}
    mode_vectors = {}
    vector = tuple((0.01, 0.0, 0.0) for _ in range(atom_count))
    for index in range(mode_count):
        frequency = imaginary_frequency if index == imaginary_mode_index else 100.0 + index
        mode_frequencies[index] = frequency
        mode_vectors[index] = vector
    frequencies = tuple(mode_frequencies[index] for index in sorted(mode_frequencies))
    analysis = FrequencyAnalysis(
        frequencies=frequencies,
        imaginary_frequencies=(imaginary_frequency,) if imaginary_frequency < 0 else (),
        mode_frequencies=mode_frequencies,
        mode_vectors=mode_vectors,
    )
    return TaskResult(
        task=TaskKind.FREQUENCY,
        status="completed",
        complete=True,
        energy_hartree=energy,
        symbols=("C",) * atom_count,
        coordinates=tuple((0.0, 0.0, float(index)) for index in range(atom_count)),
        frequencies=frequencies,
        payload=FrequencyPayload(n_imaginary=1, analysis=analysis),
    )


def _irc_result(run, *, directions: tuple[str, ...] = ("forward", "reverse"), endpoints=None):
    from cccp.calculation.requests import IrcDirection, TaskKind
    from cccp.calculation.results import IrcDirectionResult, IrcPayload, TaskResult

    if endpoints is None:
        product_symbols, product_coordinates = run.frozen.product_in_reactant_order()
        endpoints = {
            "forward": (
                list(run.frozen.reactant_symbols),
                [tuple(row) for row in run.frozen.reactant_coordinates],
            ),
            "reverse": (list(product_symbols), [tuple(row) for row in product_coordinates]),
        }
    entries = []
    for name in directions:
        symbols, coordinates = endpoints[name]
        entries.append(
            IrcDirectionResult(
                direction=IrcDirection(name),
                energy_hartree=-233.0,
                coordinates=tuple(coordinates),
                symbols=tuple(symbols),
                converged=True,
                steps=20,
                success=True,
            )
        )
    return TaskResult(
        task=TaskKind.IRC,
        status="completed",
        complete=len(entries) == 2,
        payload=IrcPayload(directions=tuple(entries)),
        metadata={"direction_status": {name: "converged" for name in directions}},
    )


def _singlepoint_result(energy: float):
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import TaskResult

    return TaskResult(task=TaskKind.SINGLEPOINT, status="completed", energy_hartree=energy)


def _step_document(result, *, task_ref: str, entry: str = "test") -> dict:
    return {
        "step_id": task_ref,
        "status": "completed",
        "complete": True,
        "entry": entry,
        "task_ref": task_ref,
        "duration_seconds": 0.0,
        "task_result": None if result is None else result.to_dict(),
        "native_files": [],
        "products": [],
        "error": None,
    }


@pytest.fixture()
def run(tmp_path):
    """An M404 instance with constructed (not executed) step documents."""
    args = M.build_parser().parse_args(["--out", str(tmp_path / "out"), "--phase", "evaluate"])
    instance = M.M404(args)
    instance.steps = {
        "endpoint_prep": {
            "step_id": "endpoint_prep",
            "status": "completed",
            "entry": "test",
            "native_files": [],
            "sub_executions": [],
        },
        "endpoint_prep_butadiene": _step_document(
            _singlepoint_result(-155.0), task_ref="endpoint_prep_butadiene"
        ),
        "endpoint_prep_ethene": _step_document(
            _singlepoint_result(-78.0), task_ref="endpoint_prep_ethene"
        ),
        "candidate_frequency": _step_document(
            _frequency_result(imaginary_frequency=-480.0), task_ref="candidate_frequency"
        ),
        "ts_optimize": _step_document(_singlepoint_result(-232.9), task_ref="ts_optimize"),
        "ts_frequency": _step_document(_frequency_result(), task_ref="ts_frequency"),
        "irc": _step_document(_irc_result(instance), task_ref="irc"),
        "endpoint_reactants": _step_document(
            _singlepoint_result(-233.0), task_ref="endpoint_reactants"
        ),
        "endpoint_product": _step_document(
            _singlepoint_result(-233.5), task_ref="endpoint_product"
        ),
        "energy_thermochemistry": _step_document(None, task_ref="energy_thermochemistry"),
    }
    return instance


# ── frozen inputs ─────────────────────────────────────────────────────────


def test_frozen_inputs_are_a_digest_bound_bijection():
    frozen = M.load_frozen_inputs()
    assert frozen.reaction_id == "reaction.diels_alder.butadiene_ethene_cyclohexene.v1"
    assert frozen.reactant_count == 16
    assert frozen.product_count == 16
    mapping = dict(frozen.atom_mapping)
    assert sorted(mapping) == list(range(16))
    assert sorted(mapping.values()) == list(range(16))
    for left, right in frozen.atom_mapping:
        assert frozen.reactant_symbols[left] == frozen.product_symbols[right]
    symbols, coordinates = frozen.product_in_reactant_order()
    assert symbols == frozen.reactant_symbols
    assert len(coordinates) == 16
    # The digest map is the fixture's own committed digest set.
    assert frozen.digests["reactants.xyz"] == M.bare_digest(
        REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "v1" / "reactants.xyz"
    )


def test_criteria_fingerprint_is_the_frozen_adr_value():
    from cccp.analysis.research.science_criteria import criteria_definition_digest

    assert criteria_definition_digest() == M.EXPECTED_CRITERIA_DIGEST
    adr = M.ADR_PATH.read_text(encoding="utf-8")
    assert M.EXPECTED_CRITERIA_DIGEST in adr


def test_public_entry_refuses_the_frozen_nine_step_recipe():
    from acp_services.runtime.step_definition import (
        ADR008_REQUIRED_STEPS,
        compile_step_dag_definition,
    )

    recipe = json.loads((M.FIXTURE_V1 / "recipe.json").read_text(encoding="utf-8"))
    with pytest.raises(Exception) as error:
        compile_step_dag_definition(recipe, {}, task_ref="test")
    message = str(error.value)
    assert "unsupported_step_kind" in message or "dispatchable" in message
    open_steps = [row.step_id for row in ADR008_REQUIRED_STEPS if row.mapping != "mapped"]
    assert open_steps == ["endpoint_prep", "candidate_path", "energy_thermochemistry"]


def test_adr_step_table_matches_the_frozen_recipe():
    recipe = json.loads((M.FIXTURE_V1 / "recipe.json").read_text(encoding="utf-8"))
    recipe_steps = {step["step_id"]: step for step in recipe["steps"]}
    assert [step["step_id"] for step in M.ADR_STEPS] == list(recipe_steps)
    for step in M.ADR_STEPS:
        assert recipe_steps[step["step_id"]]["required"] is True


# ── criterion mapping on real TaskResult shapes ───────────────────────────


def test_supported_case_maps_every_criterion_through_the_frozen_validators(run):
    assessments = run._assessments()
    for criterion_id in M.CRITERION_ORDER:
        claims = assessments[criterion_id]
        assert claims, criterion_id
        statuses = {claim.status for claim in claims}
        assert "supported" in statuses, (criterion_id, statuses)


def test_one_sided_irc_is_inconclusive_never_refuted(run):
    run.steps["irc"] = _step_document(
        _irc_result(run, directions=("forward",)), task_ref="irc-one-sided"
    )
    assessments = run._assessments()
    completion = assessments["DA-C04-irc-bidirectional"][0]
    assert completion.status == "inconclusive"
    assert completion.status != "refuted"
    endpoint_claims = assessments["DA-C05-endpoint-association"]
    assert endpoint_claims
    # The criterion verdict is the weaker of the two directions: one direction
    # alone can never make the endpoint association supported.
    assert M.M404._weakest(endpoint_claims).status != "supported"


def test_missing_frequency_is_not_evaluated(run):
    run.steps["ts_frequency"] = _step_document(None, task_ref="ts_frequency-missing")
    run.steps["candidate_frequency"] = _step_document(None, task_ref="candidate_frequency-missing")
    assessments = run._assessments()
    assert assessments["DA-C01-ts-single-imaginary-mode"][0].status == "not_evaluated"
    assert assessments["DA-C03-frequency-evidence"][0].status == "not_evaluated"
    assert all(
        claim.status != "refuted"
        for criterion_id in ("DA-C01-ts-single-imaginary-mode", "DA-C03-frequency-evidence")
        for claim in assessments[criterion_id]
    )


def test_endpoint_mismatch_refutes_connectivity(run):
    # A rigid translation is removed by superposition, so the constructed
    # mismatch deforms the geometry: the bond topology no longer matches the
    # frozen endpoint, which is an affirmative contradiction (refuted).
    product_symbols, product_coordinates = run.frozen.product_in_reactant_order()
    strained = [(x * 1.6, y * 1.6, z * 1.6) for x, y, z in product_coordinates]
    shifted = {
        "forward": (
            list(run.frozen.reactant_symbols),
            [(x + 6.0, y, z) for x, y, z in run.frozen.reactant_coordinates],
        ),
        "reverse": (list(product_symbols), strained),
    }
    run.steps["irc"] = _step_document(_irc_result(run, endpoints=shifted), task_ref="irc-mismatch")
    assessments = run._assessments()
    assert any(claim.status == "refuted" for claim in assessments["DA-C05-endpoint-association"])


def test_energy_comparability_and_stoichiometry_are_supported(run):
    assessments = run._assessments()
    assert assessments["DA-C06-energy-comparability"][0].status == "supported"
    assert assessments["DA-C07-reactant-stoichiometry"][0].status == "supported"
    assert assessments["DA-C06-energy-comparability"][0].validator_id == (
        "check.activation_energy_comparability"
    )


# ── scenario_result vs claim_status ───────────────────────────────────────


def test_scenario_result_never_changes_claim_status(tmp_path):
    from cccp.analysis.research import validators
    from cccp.analysis.research.evidence import (
        ArtifactSourceRef,
        ClaimScope,
        Digest,
        ExecutionFacts,
        assemble_claim,
        claim_draft_from_assessment,
    )
    from cccp.analysis.research.science_criteria import (
        criterion_evidence_from_claim,
        evaluate_criteria,
    )

    assessment = validators.validate_ts_frequency_count(_frequency_result())
    scope = ClaimScope(subject="s", applicability="system_specific")
    artifact = tmp_path / "native.out"
    artifact.write_text("native QC output\n", encoding="utf-8")
    claim = assemble_claim(
        claim_draft_from_assessment(
            assessment,
            scope=scope,
            source_refs=(
                ArtifactSourceRef(
                    artifact_path="native.out",
                    digest=Digest.from_bytes(artifact.read_bytes()),
                    role="native_output",
                    units=None,
                ),
            ),
        ),
        ExecutionFacts(terminal_state="completed", argv=("x",), exit_code=0),
    )
    record = criterion_evidence_from_claim(claim, "DA-C01-ts-single-imaginary-mode")
    statuses = {}
    for scenario in ("PASS", "FAIL", "NOT_VERIFIED"):
        evaluation = evaluate_criteria((record,), scenario_result=scenario)
        statuses[scenario] = (
            evaluation.overall_claim_status,
            evaluation.criterion("DA-C01-ts-single-imaginary-mode").claim_status,
        )
    assert len(set(statuses.values())) == 1
    assert statuses["PASS"][1] == "supported"


def test_forged_supported_record_is_rejected():
    from cccp.analysis.research.science_criteria import (
        CriterionEvidence,
        ScienceCriteriaError,
        digest_evidence,
    )

    payload = {"claim": {"claim_id": "forged", "status": "supported"}}
    with pytest.raises(ScienceCriteriaError):
        CriterionEvidence(
            criterion_id="DA-C01-ts-single-imaginary-mode",
            claim_id="forged",
            status="supported",
            validator_id="check.ts_saddle_candidate",
            validator_version="1.0.0",
            evidence=payload,
            evidence_digest="0" * 64,
            source_digests=(),
        )
    with pytest.raises(ScienceCriteriaError):
        CriterionEvidence(
            criterion_id="DA-C01-ts-single-imaginary-mode",
            claim_id="forged",
            status="supported",
            validator_id="check.ts_saddle_candidate",
            validator_version="1.0.0",
            evidence={"claim": {"claim_id": "forged"}, "criteria_passed": True},
            evidence_digest=digest_evidence(
                {"claim": {"claim_id": "forged"}, "criteria_passed": True}
            ),
            source_digests=(),
        )
    # A supported verdict without a digested source is downgraded, never kept.
    record = CriterionEvidence(
        criterion_id="DA-C01-ts-single-imaginary-mode",
        claim_id="unsourced",
        status="supported",
        validator_id="check.ts_saddle_candidate",
        validator_version="1.0.0",
        evidence={"claim": {"claim_id": "unsourced"}},
        evidence_digest=digest_evidence({"claim": {"claim_id": "unsourced"}}),
        source_digests=(),
    )
    from cccp.analysis.research.science_criteria import evaluate_criteria

    evaluation = evaluate_criteria((record,), scenario_result="PASS")
    assert evaluation.criterion("DA-C01-ts-single-imaginary-mode").claim_status == "inconclusive"


def test_path_frames_split_and_the_barrier_top_is_selected(tmp_path):
    """``_xyz_frames`` still splits a multi-frame XYZ document."""
    frozen = M.load_frozen_inputs()
    frames = []
    for index, scale in enumerate((1.0, 1.4, 1.1)):
        coordinates = [(x + scale, y, z) for x, y, z in frozen.reactant_coordinates]
        frames.append(
            M._xyz_text(frozen.reactant_symbols, coordinates, f"frame {index} E={-155 + index}")
        )
    trajectory = tmp_path / "path.xyz"
    trajectory.write_text("".join(frames), encoding="utf-8")
    parsed = M._xyz_frames(trajectory.read_text(encoding="utf-8"))
    assert len(parsed) == 3
    assert parsed[0][0] == list(frozen.reactant_symbols)
    assert parsed[0][1][0][0] == pytest.approx(frozen.reactant_coordinates[0][0] + 1.0)


# ── ADR-008 Amendment A: TS-guess route ──────────────────────────────────


REFERENCE = REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "reference-case"


def test_adr_amendment_a_replaces_only_the_step_two_producer():
    step = M.ADR_STEPS[1]
    assert step["step_id"] == "candidate_path"
    # The frozen recipe contract label is untouched; only the producer changes.
    assert step["task_kind"] == "xtb_path_search"
    assert step["executed_as"] == "pes_scan_frame_batch_optimize"
    assert step["route_amendment"] == M.ROUTE_AMENDMENT_ID
    assert [row["index"] for row in M.ADR_STEPS] == list(range(1, 10))
    # The frozen criteria definition does not carry the route: digest unchanged.
    from cccp.analysis.research.science_criteria import criteria_definition_digest

    assert criteria_definition_digest() == M.EXPECTED_CRITERIA_DIGEST
    adr = M.ADR_PATH.read_text(encoding="utf-8")
    assert "Amendment A" in adr
    assert M.ROUTE_ID in adr


def test_amendment_a_frame_rule_reproduces_the_reference_manual_selection():
    """The explicit rule picks frame 005, the frame the operator selected."""
    profile = json.loads((REFERENCE / "pes" / "pes_profile.json").read_text(encoding="utf-8"))
    selected, table = M.select_scan_frame(profile)
    assert selected is not None
    assert int(selected["index"]) == M.ROUTE_REFERENCE_SELECTED_FRAME == 5
    assert float(selected["reaction_progress"]) == pytest.approx(0.6346877412644145)
    # Frame 4 is below the midpoint, frame 5 is the first eligible frame above it.
    assert table[4]["reaction_progress"] < M.ROUTE_FRAME_PROGRESS_THRESHOLD
    assert table[5]["selected"] is True
    assert sum(1 for row in table if row["selected"]) == 1
    # The user's remembered frame_009 exists in the profile but was never
    # selected; the file evidence (frame 005) wins over the recollection.
    assert any(row["index"] == 9 for row in table)
    assert not any(row["selected"] for row in table if row["index"] == 9)


def test_amendment_a_frame_rule_has_no_fallback_pick():
    profile = {
        "frames": [
            {
                "index": index,
                "reaction_progress": 0.1 * index,
                "optimization_converged": True,
                "constraint_residual_ok": True,
                "invalid_reasons": [],
            }
            for index in range(3)
        ]
    }
    selected, table = M.select_scan_frame(profile)
    assert selected is None
    assert all(row["selected"] is False for row in table)
    # A non-converged frame above the threshold is not eligible either.
    profile["frames"][2]["reaction_progress"] = 0.9
    profile["frames"][2]["optimization_converged"] = False
    selected, _ = M.select_scan_frame(profile)
    assert selected is None


def test_reference_level_variant_is_an_explicit_deviation(tmp_path):
    frozen = M.M404(M.build_parser().parse_args(["--out", str(tmp_path / "frozen")]))
    assert frozen.level_mode == "frozen"
    assert (frozen.method, frozen.basis) == (M.FROZEN_METHOD, M.FROZEN_BASIS)
    reference = M.M404(
        M.build_parser().parse_args(["--out", str(tmp_path / "ref"), "--level", "reference"])
    )
    assert reference.level_mode == "reference"
    assert (reference.method, reference.basis) == (M.REFERENCE_METHOD, M.REFERENCE_BASIS)
    # The criteria definition is level-independent: only the protocol deviation moves.
    from cccp.analysis.research.science_criteria import criteria_definition_digest

    assert criteria_definition_digest() == M.EXPECTED_CRITERIA_DIGEST


def test_reference_case_fixture_is_digest_bound_to_the_real_jobs():
    provenance = json.loads((REFERENCE / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["reference_jobs"]["pes_search"] == M.ROUTE_REFERENCE_JOB_PES
    assert provenance["reference_jobs"]["batch_optimize"] == M.ROUTE_REFERENCE_JOB_BATCH
    assert provenance["selected_frame"]["index"] == M.ROUTE_REFERENCE_SELECTED_FRAME
    for relative, expected in provenance["file_digests"].items():
        path = REFERENCE / relative
        assert path.is_file(), relative
        assert M.bare_digest(path) == expected, relative
    # The batch input is the selected scan frame geometry (the job payload
    # re-prints the coordinates at higher precision, so the comparison is
    # geometric, not byte-for-byte).
    selected = REFERENCE / provenance["selected_frame"]["geometry_path"]
    batch_input = REFERENCE / "batch" / "input.xyz"
    selected_symbols, selected_coordinates = M._xyz_records(selected.read_text(encoding="utf-8"))
    input_symbols, input_coordinates = M._xyz_records(batch_input.read_text(encoding="utf-8"))
    assert selected_symbols == input_symbols
    for left, right in zip(selected_coordinates, input_coordinates, strict=True):
        for first, second in zip(left, right, strict=True):
            assert first == pytest.approx(second, abs=1e-6)
    # The reference TS frequency carries exactly one imaginary mode.
    frequencies = provenance["batch_ts_frequency"]["frequencies"]
    assert sum(1 for value in frequencies if value < 0) == 1
    assert frequencies[0] == pytest.approx(-456.57)
    assert provenance["batch_ts_frequency"]["ts_validation"] == "passed"


# ── H4 ────────────────────────────────────────────────────────────────────


def test_h4_never_closes_on_the_local_row_alone(run, tmp_path):
    run.criteria = {
        "scenario_result": "PASS",
        "claim_status": "supported",
        "evaluation": {
            "criteria": [
                {"criterion_id": criterion_id, "claim_status": "supported"}
                for criterion_id in M.CRITERION_ORDER
            ]
        },
    }
    run.build_h4()
    assert run.h4["closed"] is False
    assert run.h4["state"] == "open"
    lsf = next(row for row in run.h4["rows"] if row["row_id"] == "DA-ENV-LSF")
    assert lsf["required"] is True
    assert lsf["scenario_result"] == "NOT_VERIFIED"
    assert any("LSF" in reason for reason in run.h4["reasons"])


# ── readers (Node) ────────────────────────────────────────────────────────


def test_rph_decision_and_headless_workbench_read_the_same_view():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    run_args = M.build_parser().parse_args(["--out", "/tmp/acp2-m404-views-test"])
    instance = M.M404(run_args)
    view = {
        "research": {"research_id": "r", "round": 0},
        "task_ref": {"task_id": "t", "attempt": 1, "release": {"release_id": "rel"}},
        "execution_terminal_state": "completed",
        "evidence_status": "complete",
        "overall_claim_status": "inconclusive",
        "blocking_missing_checks": ["artifact_missing"],
        "report": {"report_id": "report"},
        "projection_digest": "sha256:" + "a" * 64,
        "evidence_refs": [{"artifact_path": "native.out", "digest": "a" * 64}],
        "claims": [{"claim_id": "c1", "status": "inconclusive"}],
    }
    decision = instance._rph_decision(view)
    assert decision["available"] is True
    # The frozen policy escalates when a blocking missing check remains: an
    # inconclusive claim is never silently accepted.
    assert decision["decision"] == "escalate"
    workbench = instance._workbench_view(view)
    assert workbench["available"] is True
    assert workbench["supported"] is False
    labels = [row["label"] for row in workbench["rows"]]
    assert "evidence" in labels and "claims" in labels and "execution" in labels
    assert any(label.startswith("blocking[") for label in labels)


# ── recorded evidence replay ──────────────────────────────────────────────


def test_recorded_evidence_separates_scenario_and_claim():
    evidence_dir = REPO / "reports" / "acp2_remediation_20261026" / "m4-04"
    summary = evidence_dir / "summary.json"
    required = ("criteria-table.json", "h4.json", "preflight.json")
    if not summary.is_file() or not all((evidence_dir / name).is_file() for name in required):
        pytest.skip("no complete recorded M4-04 evidence in this checkout")
    document = json.loads(summary.read_text(encoding="utf-8"))
    assert document["schema"] == M.SCHEMA
    assert document["scenario_result"] in ("PASS", "FAIL", "NOT_VERIFIED")
    assert document["claim_status"] in (
        "supported",
        "refuted",
        "inconclusive",
        "not_evaluated",
    )
    criteria = json.loads((evidence_dir / "criteria-table.json").read_text(encoding="utf-8"))
    assert criteria["scenario_result"] == document["scenario_result"]
    assert len(criteria["criteria"]) == 7
    # Every row carries both facts separately.
    for row in criteria["criteria"]:
        assert row["scenario_result"] == criteria["scenario_result"]
        assert row["claim_status"] in (
            "supported",
            "refuted",
            "inconclusive",
            "not_evaluated",
        )
    h4 = json.loads((evidence_dir / "h4.json").read_text(encoding="utf-8"))
    assert h4["closed"] is False
    assert {row["row_id"] for row in h4["rows"]} == {"DA-ENV-LOCAL", "DA-ENV-LSF"}
    preflight = json.loads((evidence_dir / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["criteria"]["digest"] == M.EXPECTED_CRITERIA_DIGEST
