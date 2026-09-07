# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Electronic-state module + CASSCF tests (ACP_Electronic_State_CASSCF_Design.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.backends.base import QCResult
from acp.calculations.batch._items import BatchStructureItem, item_cache_key
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationRequest,
    CalculationStep,
    ElectronicStateConfig,
    ElectronicStateSpec,
    GuessStrategy,
    JsonValue,
    SpinMode,
    StepKind,
    StructureArtifact,
    casscf_spec_from_dict,
    electron_parity_ok,
    electronic_state_config_from_dict,
    electronic_state_config_from_preset_mode,
    electronic_state_config_to_dict,
    expected_s2_for_multiplicity,
    orca_xyz_multiplicity,
    state_signature,
    validate_casscf_spec,
    validate_electronic_state,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.primitives._common import (
    assess_state_quality,
    electronic_state_result_metadata,
    load_inputs,
)
from acp.calculations.primitives.casscf import run_casscf
from acp.calculations.primitives.singlepoint import run_singlepoint
from cccp.qc.interfaces.orca import (
    parse_casscf_output,
    parse_electronic_state_diagnostics,
    render_moinp_block,
    render_scf_block,
    scf_route_extras,
)

# ── Fixtures: representative ORCA output snippets (formats verified
# against the ORCA 6.1 manual / tutorials) ────────────────────────────────

SPIN_OUTPUT = """
----------------------
UHF SPIN CONTAMINATION
----------------------

Expectation value of <S**2>     :     0.986100
Ideal value S*(S+1) for S=0.0   :     0.000000
Deviation                       :     0.986100

MULLIKEN ATOMIC CHARGES AND SPIN POPULATIONS
--------------------------------------------
 0 C : -0.120000 0.552000
 1 H : 0.060000 -0.001000
 2 H : 0.060000 -0.001000
 3 C : -0.120000 -0.548000
 4 H : 0.060000 -0.001000

Sum of atomic charges : 0.0000000
Sum of atomic spin populations: 0.0000000
"""

CASSCF_NEVPT2_OUTPUT = """
------------------
CAS-SCF RESULTS
------------------

FINAL SINGLE POINT ENERGY     -108.98888640

===============================================================
                      NEVPT2 Results
===============================================================
   *********************
    MULT 1, ROOT 0
   *********************

---------------------------------------------------------------
         Total Energy Correction : dE = -0.15802909
---------------------------------------------------------------
         Zero Order Energy       : E0 = -108.98888640
---------------------------------------------------------------
         Total Energy (E0+dE)    : E  = -109.14691549
---------------------------------------------------------------

Natural Orbital Occupation Numbers:
N[  0] =   1.99812992
N[  1] =   1.49303660
N[  2] =   0.50696340
N[  3] =   0.00187008
"""


def _flipspin_payload(**overrides: object) -> dict[str, object]:
    state = {
        "state_id": "bs_s1",
        "label": "BS singlet",
        "target_multiplicity": 1,
        "spin_mode": "broken_symmetry",
        "guess": {
            "strategy": "flipspin",
            "reference_multiplicity": 3,
            "final_ms": 0.0,
            "flip_atoms": [17],
            "atom_index_base": 1,
        },
        "spatial_symmetry": "disable",
        "quality_gate": {
            "collapse_policy": "error",
            "s2_min": 0.1,
            "s2_max": 1.5,
            "require_opposite_spin_centers": True,
        },
    }
    state.update(overrides)
    return {"schema_version": 1, "execution_mode": "single", "states": [state]}


# ── §5 contracts ──────────────────────────────────────────────────────────


def test_flipspin_separates_target_and_reference_multiplicity() -> None:
    config = electronic_state_config_from_dict(_flipspin_payload())
    state = config.selected_state()
    assert state is not None
    assert state.target_multiplicity == 1
    assert orca_xyz_multiplicity(state) == 3
    assert state.guess.orca_flip_atoms() == (16,)
    assert state.spin_mode is SpinMode.BROKEN_SYMMETRY
    assert state.spatial_symmetry.value == "disable"


def test_electronic_state_roundtrip() -> None:
    config = electronic_state_config_from_dict(_flipspin_payload())
    restored = electronic_state_config_from_dict(electronic_state_config_to_dict(config))
    assert restored == config


def test_preset_mode_shorthands() -> None:
    automatic = electronic_state_config_from_preset_mode("automatic")
    assert automatic.states[0].spin_mode is SpinMode.AUTO
    restricted = electronic_state_config_from_preset_mode("closed_shell")
    assert restricted.states[0].spin_mode is SpinMode.RESTRICTED
    bs = electronic_state_config_from_preset_mode("bs_singlet")
    assert bs.states[0].guess.strategy is GuessStrategy.FLIPSPIN


def test_validation_rules_sec13() -> None:
    valid = electronic_state_config_from_dict(_flipspin_payload())
    assert validate_electronic_state(valid, backend="orca", n_atoms=20).is_valid

    backend_rejected = validate_electronic_state(valid, backend="xtb")
    assert any("only supported on the ORCA" in e for e in backend_rejected.errors)

    out_of_range = validate_electronic_state(valid, backend="orca", n_atoms=10)
    assert any("outside" in e for e in out_of_range.errors)

    no_flip_atoms = electronic_state_config_from_dict(
        _flipspin_payload(
            guess={"strategy": "flipspin", "reference_multiplicity": 3, "final_ms": 0.0}
        )
    )
    assert any("flip_atoms" in e for e in validate_electronic_state(no_flip_atoms).errors)

    bad_reference = electronic_state_config_from_dict(
        _flipspin_payload(
            guess={
                "strategy": "flipspin",
                "reference_multiplicity": 1,
                "final_ms": 0.0,
                "flip_atoms": [1],
            }
        )
    )
    bad_reference_errors = validate_electronic_state(bad_reference).errors
    assert any("reference_multiplicity" in e for e in bad_reference_errors)

    sweep_needs_two = ElectronicStateConfig(
        execution_mode=electronic_state_config_from_dict({"mode": "automatic"}).execution_mode,
    )
    sweep = electronic_state_config_from_dict(
        {
            "execution_mode": "state_sweep",
            "states": [
                {"state_id": "s1", "target_multiplicity": 1, "spin_mode": "restricted"},
            ],
        }
    )
    assert any("at least two states" in e for e in validate_electronic_state(sweep).errors)
    assert sweep_needs_two.states == ()

    parity = electron_parity_ok(10, 1)
    assert parity and not electron_parity_ok(10, 2)


def test_guessmix_requires_unrestricted() -> None:
    config = electronic_state_config_from_dict(
        {
            "states": [
                {
                    "state_id": "s1",
                    "target_multiplicity": 1,
                    "spin_mode": "restricted",
                    "guess": {"strategy": "guessmix"},
                }
            ]
        }
    )
    errors = validate_electronic_state(config).errors
    assert any("unrestricted" in e for e in errors)


def test_expected_s2_and_signature() -> None:
    assert expected_s2_for_multiplicity(1) == 0.0
    assert expected_s2_for_multiplicity(3) == pytest.approx(2.0)
    state = electronic_state_config_from_dict(_flipspin_payload()).selected_state()
    assert state is not None
    signature = state_signature(state, method="wB97X-D4", basis="def2-SVP")
    assert signature["reference_multiplicity"] == 3
    assert signature["spin_mode"] == "broken_symmetry"


def test_casscf_spec_validation_sec135() -> None:
    spec = casscf_spec_from_dict(
        {"active_electrons": 6, "active_orbitals": 6, "dynamic_correlation": "sc_nevpt2"}
    )
    assert validate_casscf_spec(spec, n_electrons=40) == []
    assert len(spec.active_space_signature()) > 0

    too_many_electrons = casscf_spec_from_dict({"active_electrons": 8, "active_orbitals": 3})
    assert any("cannot exceed" in e for e in validate_casscf_spec(too_many_electrons))

    mismatched_weights = casscf_spec_from_dict(
        {"active_electrons": 2, "active_orbitals": 2, "nroots": 2, "state_weights": [1.0]}
    )
    assert any("state_weights" in e for e in validate_casscf_spec(mismatched_weights))

    with pytest.raises(ValueError, match="active-space"):
        casscf_spec_from_dict({})


# ── §9 ORCA rendering ─────────────────────────────────────────────────────


def test_scf_block_renders_orca_keywords() -> None:
    triplet = render_scf_block({"hf_typ": "UHF"})
    assert triplet == "%scf\n  HFTyp UHF\nend"

    flipspin = render_scf_block({"hf_typ": "UHF", "flip_spin_atoms": [16], "final_ms": 0.0})
    assert "FlipSpin 16" in flipspin
    assert "FinalMs 0" in flipspin

    guessmix = render_scf_block({"hf_typ": "UHF", "guess_mix_angle": 45.0})
    assert "GuessMix 45" in guessmix

    broken_sym = render_scf_block({"broken_sym_na": 2, "broken_sym_nb": 2})
    assert "BrokenSym 2,2" in broken_sym

    stability = render_scf_block({"stab_perform": True, "stab_restart": True})
    assert "STABPerform true" in stability
    assert "STABRestartUHFifUnstable true" in stability

    assert render_scf_block(None) is None
    assert render_scf_block({}) is None
    assert render_scf_block({"no_use_sym": True}) is None


def test_scf_route_extras_and_moinp() -> None:
    assert scf_route_extras({"no_use_sym": True}) == ["NoUseSym"]
    assert scf_route_extras({"mo_read_path": "/x/y.gbw"}) == ["Moread"]
    assert render_moinp_block({"mo_read_path": "/x/y.gbw"}) == '%moinp "/x/y.gbw"'


def test_spin_diagnostics_parser() -> None:
    path = Path("/tmp/opencode/spin.out")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SPIN_OUTPUT, encoding="utf-8")
    diagnostics = parse_electronic_state_diagnostics(path)
    assert diagnostics["s2"] == pytest.approx(0.9861)
    assert diagnostics["expected_s2"] == pytest.approx(0.0)
    assert diagnostics["mulliken_spin_populations"][0] == pytest.approx(0.552)
    assert diagnostics["mulliken_spin_populations"][3] == pytest.approx(-0.548)


def test_casscf_output_parser() -> None:
    path = Path("/tmp/opencode/casscf.out")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CASSCF_NEVPT2_OUTPUT, encoding="utf-8")
    parsed = parse_casscf_output(path)
    assert parsed["casscf_energy"] == pytest.approx(-108.9888864)
    assert parsed["natural_occupations"][0] == pytest.approx(1.99812992)
    assert parsed["natural_occupations"][3] == pytest.approx(0.00187008)
    root = parsed["nevpt2_roots"][0]
    assert root["casscf_energy_hartree"] == pytest.approx(-108.9888864)
    assert root["nevpt2_correction_hartree"] == pytest.approx(-0.15802909)
    assert root["correlated_energy_hartree"] == pytest.approx(-109.14691549)


# ── §10 collapse gate ─────────────────────────────────────────────────────


def _bs_state_with_gate() -> ElectronicStateSpec:
    config = electronic_state_config_from_dict(_flipspin_payload())
    state = config.selected_state()
    assert state is not None
    return state


def test_collapse_verdicts() -> None:
    state = _bs_state_with_gate()

    broken = {"s2": 0.98, "mulliken_spin_populations": {0: 0.55, 3: -0.55}}
    verdict, notes = assess_state_quality(state, broken)
    assert verdict == "accepted" and not notes

    collapsed = {"s2": 0.0001, "mulliken_spin_populations": {0: 0.0001, 3: -0.0001}}
    verdict, notes = assess_state_quality(state, collapsed)
    assert verdict == "collapsed"
    assert notes

    same_sign = {"s2": 0.9, "mulliken_spin_populations": {0: 0.9, 3: 0.9}}
    verdict, _ = assess_state_quality(state, same_sign)
    assert verdict == "warning"


def test_collapse_policy_error_fails_the_step(fake_backend, tmp_path: Path) -> None:
    output_dir = tmp_path / "sp"
    fake_backend.set_result(
        "single_point",
        QCResult(
            success=True,
            energy=-1.0,
            coordinates=None,
            symbols=["C"],
            converged=True,
            metadata={},
        ),
    )
    payload = _flipspin_payload()
    payload["states"][0]["guess"]["flip_atoms"] = [1]  # type: ignore[index]
    request = CalculationRequest(
        input_artifact=StructureArtifact(
            path=tmp_path / "in.xyz", elements=["C", "C"], source="test"
        ),
        method="wB97X-D4",
        resources={
            "backend": "orca",
            "coordinates": [[0, 0, 0], [1, 1, 1]],
            "symbols": ["C", "C"],
            "charge": 0,
            "multiplicity": 1,
            "electronic_state": payload,
            "output_dir": str(output_dir),
        },
        workflow="test",
    )
    inputs = load_inputs(request)
    qc = QCResult(
        success=True,
        energy=-1.0,
        metadata={
            "electronic_state_diagnostics": {
                "s2": 0.0,
                "mulliken_spin_populations": {0: 0.0, 1: 0.0},
            }
        },
    )
    metadata, errors, forced = electronic_state_result_metadata(inputs, qc)
    assert metadata["state_status"] == "collapsed"
    assert forced == "failed"
    assert errors

    accepted_qc = QCResult(
        success=True,
        energy=-1.0,
        metadata={
            "electronic_state_diagnostics": {
                "s2": 0.95,
                "mulliken_spin_populations": {0: 0.7, 1: -0.7},
            }
        },
    )
    metadata2, errors2, forced2 = electronic_state_result_metadata(inputs, accepted_qc)
    assert metadata2["state_status"] == "accepted"
    assert errors2 == [] and forced2 is None


def test_state_sweep_rejected_in_single_primitive(fake_backend, tmp_path: Path) -> None:
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "in.xyz", elements=["C"], source="test"),
        method="wB97X-D4",
        resources={
            "backend": "orca",
            "coordinates": [[0, 0, 0]],
            "symbols": ["C"],
            "electronic_state": {
                "execution_mode": "state_sweep",
                "states": [
                    {"state_id": "s1", "target_multiplicity": 1, "spin_mode": "restricted"},
                    {"state_id": "t1", "target_multiplicity": 3, "spin_mode": "unrestricted"},
                ],
            },
        },
        workflow="test",
    )
    with pytest.raises(ValueError, match="state_sweep"):
        load_inputs(request)


# ── primitive + executor wiring ───────────────────────────────────────────


def test_singlepoint_receives_reference_multiplicity_and_scf_options(
    fake_backend, tmp_path: Path
) -> None:
    output_dir = tmp_path / "sp"
    fake_backend.set_result(
        "single_point",
        QCResult(success=True, energy=-2.0, symbols=["C"], converged=True),
    )
    payload = _flipspin_payload()
    payload["states"][0]["guess"]["flip_atoms"] = [1]  # type: ignore[index]
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "in.xyz", elements=["C"], source="test"),
        method="wB97X-D4",
        resources={
            "backend": "orca",
            "coordinates": [[0, 0, 0]],
            "symbols": ["C"],
            "charge": 0,
            "multiplicity": 1,
            "electronic_state": payload,
            "output_dir": str(output_dir),
        },
        workflow="test",
    )
    result = run_singlepoint(request)
    assert result.status == "completed"
    call = fake_backend.calls[-1]
    assert call.kwargs["multiplicity"] == 3
    scf_options = call.kwargs["scf_options"]
    assert scf_options["hf_typ"] == "UHF"
    assert scf_options["flip_spin_atoms"] == [0]
    assert scf_options["no_use_sym"] is True


def test_casscf_primitive_dispatch_in_executor(fake_backend, tmp_path: Path) -> None:
    fake_backend.set_result(
        "casscf",
        QCResult(
            success=True,
            energy=-109.14691549,
            symbols=["C"],
            converged=True,
            metadata={
                "casscf": {
                    "casscf_energy_hartree": -108.9888864,
                    "nevpt2_correction_hartree": -0.15802909,
                    "correlated_energy_hartree": -109.14691549,
                    "natural_occupations": [1.99, 0.01],
                    "nevpt2_roots": [],
                    "converged": True,
                }
            },
        ),
    )
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    spec: dict[str, JsonValue] = {
        "method": "casscf",
        "backend": "orca",
        "casscf": {
            "active_electrons": 2,
            "active_orbitals": 2,
            "dynamic_correlation": "sc_nevpt2",
        },
    }
    plan = CalculationPlan(
        workflow="casscf",
        profile="default",
        items=[StructureArtifact(path=input_path, elements=["C"], source="test")],
        steps=[CalculationStep(kind=StepKind.CASSCF, spec=spec)],
    )
    execution = CalculationPlanExecutor().execute(plan, tmp_path / "task")
    assert execution.status == "completed", execution.errors
    call = fake_backend.calls[-1]
    assert call.method == "casscf"
    assert call.kwargs["active_electrons"] == 2
    assert call.kwargs["dynamic_correlation"] == "sc_nevpt2"
    assert (tmp_path / "task" / "WORK" / "08_CASSCF").is_dir()
    state = execution.step_states[0]
    assert state.result is not None
    multireference = state.result.metadata["multireference"]
    assert multireference["nevpt2_correction_hartree"] == pytest.approx(-0.15802909)
    assert (tmp_path / "task" / "WORK" / "08_CASSCF" / "active_space.json").is_file()


def test_casscf_primitive_fails_without_active_space(fake_backend, tmp_path: Path) -> None:
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "in.xyz", elements=["C"], source="test"),
        method="casscf",
        resources={
            "backend": "orca",
            "coordinates": [[0, 0, 0]],
            "symbols": ["C"],
            "output_dir": str(tmp_path / "sp"),
        },
        workflow="casscf",
    )
    result = run_casscf(request)
    assert result.status == "failed"
    assert any("active" in e.lower() for e in result.errors)


def test_executor_appends_post_stability_node(fake_backend, tmp_path: Path) -> None:
    fake_backend.set_result(
        "optimize",
        QCResult(
            success=True,
            energy=-1.5,
            coordinates=[[0.0, 0.0, 0.0]],
            symbols=["C"],
            converged=True,
        ),
    )
    fake_backend.set_result(
        "single_point",
        QCResult(success=True, energy=-1.6, symbols=["C"], converged=True),
    )
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    es = _flipspin_payload()
    es["states"][0]["guess"]["flip_atoms"] = [1]  # type: ignore[index]
    es["states"][0]["diagnostics"] = {"stability": "final_geometry"}  # type: ignore[index]
    spec: dict[str, JsonValue] = {
        "method": "wB97X-D4",
        "backend": "orca",
        "electronic_state": es,  # type: ignore[arg-type]
    }
    plan = CalculationPlan(
        workflow="optimize",
        profile="default",
        items=[StructureArtifact(path=input_path, elements=["C"], source="test")],
        steps=[CalculationStep(kind=StepKind.OPTIMIZE, spec=spec)],
    )
    execution = CalculationPlanExecutor().execute(plan, tmp_path / "task")
    assert execution.status == "completed", execution.errors
    assert len(execution.step_states) == 2
    stability_state = execution.step_states[1]
    assert stability_state.kind is StepKind.SINGLEPOINT
    assert stability_state.status == "completed"
    stability_calls = [c for c in fake_backend.calls if c.method == "single_point"]
    assert stability_calls
    last_call = stability_calls[-1].kwargs
    assert last_call.get("stability_check") is True or last_call.get("scf_options", {}).get(
        "stab_perform"
    )
    assert (tmp_path / "task" / "WORK" / "05_SP" / "stability").is_dir()


def test_executor_skips_stability_node_for_plain_plans(fake_backend, tmp_path: Path) -> None:
    fake_backend.set_result(
        "single_point",
        QCResult(success=True, energy=-1.0, symbols=["C"], converged=True),
    )
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    plan = CalculationPlan(
        workflow="singlepoint",
        profile="default",
        items=[StructureArtifact(path=input_path, elements=["C"], source="test")],
        steps=[
            CalculationStep(
                kind=StepKind.SINGLEPOINT,
                spec={"method": "r2SCAN-3c", "backend": "orca"},
            )
        ],
    )
    execution = CalculationPlanExecutor().execute(plan, tmp_path / "task")
    assert execution.status == "completed"
    assert len(execution.step_states) == 1


# ── §8 batch expansion ────────────────────────────────────────────────────


def _engine(tmp_path: Path) -> BatchOptimizeEngine:
    engine = BatchOptimizeEngine.__new__(BatchOptimizeEngine)
    engine._active_layout_mode = "batch"
    engine._work_root = tmp_path / "WORK"
    return engine


def test_item_cache_key_separates_electronic_states(tmp_path: Path) -> None:
    item = BatchStructureItem(item_id="i1", name="M", tag="INT", xyz="1\nx\nC 0 0 0")
    rks = json.dumps({"states": [{"state_id": "s1", "spin_mode": "restricted"}]}, sort_keys=True)
    uks = json.dumps({"states": [{"state_id": "t1", "spin_mode": "unrestricted"}]}, sort_keys=True)
    bs_a = json.dumps(
        {"states": [{"state_id": "bs", "guess": {"flip_atoms": [1]}}]}, sort_keys=True
    )
    bs_b = json.dumps(
        {"states": [{"state_id": "bs", "guess": {"flip_atoms": [2]}}]}, sort_keys=True
    )
    keys = {
        item_cache_key(item, "opt_freq", "sig", signature)
        for signature in ("", rks, uks, bs_a, bs_b)
    }
    assert len(keys) == 5


def test_state_expansion_orders_references_first(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    sweep = {
        "execution_mode": "state_sweep",
        "states": [
            {"state_id": "s1_closed", "target_multiplicity": 1, "spin_mode": "restricted"},
            {
                "state_id": "s1_bs",
                "target_multiplicity": 1,
                "spin_mode": "broken_symmetry",
                "guess": {
                    "strategy": "flipspin",
                    "reference_multiplicity": 3,
                    "final_ms": 0.0,
                    "flip_atoms": [2],
                },
                "reference_state_id": "t1",
            },
            {"state_id": "t1", "target_multiplicity": 3, "spin_mode": "unrestricted"},
        ],
    }
    item = BatchStructureItem(item_id="m1", name="MOL", tag="INT", xyz="2\nx\nH 0 0 0\nH 0 0 1")
    expanded = engine._expand_item_states([item], sweep, 0)
    assert [i.item_id for i in expanded] == ["m1__s1_closed", "m1__t1", "m1__s1_bs"]
    assert expanded[2].multiplicity == 1
    bootstrap = expanded[2].electronic_state or {}
    assert str(bootstrap.get("wavefunction_bootstrap", "")).endswith("m1__t1/optimize/optimize.gbw")


def test_state_expansion_injects_reference_bootstrap(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    bs_only = {
        "execution_mode": "state_sweep",
        "states": [
            {"state_id": "s1_closed", "target_multiplicity": 1, "spin_mode": "restricted"},
            {
                "state_id": "s1_bs",
                "target_multiplicity": 1,
                "spin_mode": "broken_symmetry",
                "guess": {
                    "strategy": "flipspin",
                    "reference_multiplicity": 3,
                    "final_ms": 0.0,
                    "flip_atoms": [2],
                },
                "reference_state_id": "t1",
            },
        ],
    }
    item = BatchStructureItem(item_id="m1", name="MOL", tag="INT", xyz="2\nx\nH 0 0 0\nH 0 0 1")
    expanded = engine._expand_item_states([item], bs_only, 0)
    assert [i.item_id for i in expanded] == ["m1__t1", "m1__s1_closed", "m1__s1_bs"]
    assert expanded[0].reference_only is True
    assert expanded[0].multiplicity == 3


def test_state_expansion_single_state_keeps_item_id(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    single = {
        "execution_mode": "single",
        "states": [{"state_id": "t1", "target_multiplicity": 3, "spin_mode": "unrestricted"}],
    }
    item = BatchStructureItem(item_id="m1", name="MOL", tag="INT", xyz="2\nx\nH 0 0 0\nH 0 0 1")
    expanded = engine._expand_item_states([item], single, 0)
    assert len(expanded) == 1
    assert expanded[0].item_id == "m1"
    assert expanded[0].electronic_state is not None

    untouched = engine._expand_item_states([item], None, 0)
    assert untouched[0].electronic_state is None


def test_step_state_payload_wires_gbw_inheritance(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    opt_payload = {
        "execution_mode": "single",
        "states": [
            {
                "state_id": "t1",
                "target_multiplicity": 3,
                "spin_mode": "unrestricted",
            }
        ],
    }
    item = BatchStructureItem(
        item_id="m1",
        name="MOL",
        tag="INT",
        xyz="2\nx\nH 0 0 0\nH 0 0 1",
        electronic_state=opt_payload,  # type: ignore[arg-type]
    )
    assert (
        engine._step_state_payload(item, StepKind.OPTIMIZE, inherit_gbw="/x/optimize.gbw")
        is opt_payload
    )

    freq_payload = engine._step_state_payload(
        item, StepKind.FREQUENCY, inherit_gbw="/x/optimize.gbw"
    )
    assert freq_payload is not None
    assert freq_payload["wavefunction_bootstrap"] == "/x/optimize.gbw"

    no_inherit = {
        "execution_mode": "single",
        "states": [
            {
                "state_id": "t1",
                "target_multiplicity": 3,
                "spin_mode": "unrestricted",
                "wavefunction": {"inherit_between_steps": False},
            }
        ],
    }
    item_opt_out = BatchStructureItem(
        item_id="m2",
        name="MOL",
        tag="INT",
        xyz="2\nx\nH 0 0 0\nH 0 0 1",
        electronic_state=no_inherit,  # type: ignore[arg-type]
    )
    untouched = engine._step_state_payload(item_opt_out, StepKind.SINGLEPOINT, inherit_gbw="/x.gbw")
    assert untouched is no_inherit


# ── §6 catalog presets / module field ─────────────────────────────────────


def test_catalog_module_field_and_presets() -> None:
    from acp.catalog import (
        ELECTRONIC_STATE_PRESETS,
        FIELD_DEFINITIONS,
        METHOD_SCHEMAS,
        WORKFLOW_CATALOG,
        get_electronic_state_preset,
    )

    field = FIELD_DEFINITIONS["electronic_state"]
    assert field["type"] == "module"
    assert field["default"] == {"*": {"mode": "automatic"}}

    preset = get_electronic_state_preset("bs_singlet_triplet_pair")
    assert preset is not None
    preset["states"][0]["target_multiplicity"] = 9
    fresh = get_electronic_state_preset("bs_singlet_triplet_pair")
    assert fresh is not None and fresh["states"][0]["target_multiplicity"] == 3
    assert get_electronic_state_preset("does_not_exist") is None
    assert "bs_singlet_flipspin" in ELECTRONIC_STATE_PRESETS

    assert any(w["id"] == "casscf" and w["status"] == "active" for w in WORKFLOW_CATALOG)
    casscf_level = METHOD_SCHEMAS["casscf"]["method_levels"][0]
    assert "cas_active_electrons" in casscf_level["fields"]
    assert "electronic_state" in casscf_level["fields"]


def test_normalize_method_expands_module_presets() -> None:
    from acp.catalog import (
        METHOD_SCHEMAS,
        method_levels_to_cli_flags,
        normalize_and_validate_method_config,
    )

    schema = METHOD_SCHEMAS["dft_singlepoint"]
    levels, errors = normalize_and_validate_method_config(
        {
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "electronic_state": {"preset_id": "singlet_triplet_pair"},
                }
            }
        },
        schema,
    )
    assert errors == []
    module = levels["single_point"]["electronic_state"]
    assert module["execution_mode"] == "state_sweep"
    assert len(module["states"]) == 2
    assert "preset_id" not in module

    _, invalid = normalize_and_validate_method_config(
        {
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "electronic_state": {"preset_id": "missing_preset"},
                }
            }
        },
        schema,
    )
    assert any("unknown electronic-state preset" in e for e in invalid)

    flags = method_levels_to_cli_flags(levels)
    assert all("electronic" not in flag for flag in flags)


# ── §14 CLI flags ─────────────────────────────────────────────────────────


def test_cli_spin_flag_resolution(tmp_path: Path) -> None:
    from acp.cli import _resolve_spin_flags

    config_path = tmp_path / "spin.yaml"
    config_path.write_text(
        "electronic_state:\n  execution_mode: state_sweep\n  states:\n"
        "    - state_id: s1\n      target_multiplicity: 1\n      spin_mode: restricted\n"
        "    - state_id: t1\n      target_multiplicity: 3\n      spin_mode: unrestricted\n",
        encoding="utf-8",
    )
    module = _resolve_spin_flags(None, str(config_path))
    assert module is not None and module["execution_mode"] == "state_sweep"

    preset = _resolve_spin_flags("unrestricted_triplet", None)
    assert preset is not None and preset["preset_id"] == "unrestricted_triplet"

    with pytest.raises(FileNotFoundError):
        _resolve_spin_flags(None, str(tmp_path / "missing.yaml"))
    with pytest.raises(ValueError, match="unknown --spin-preset"):
        _resolve_spin_flags("nope", None)


def test_cli_casscf_parser_registered() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "casscf",
            "--input",
            "mol.xyz",
            "--active-electrons",
            "6",
            "--active-orbitals",
            "6",
            "--dynamic-correlation",
            "sc_nevpt2",
        ]
    )
    assert args.workflow == "casscf"
    assert args.active_electrons == 6
    assert args.active_orbitals == 6
    assert args.dynamic_correlation == "sc_nevpt2"


# ── §12.1 typed API models ────────────────────────────────────────────────


def test_typed_electronic_state_api_models() -> None:
    from pydantic import ValidationError

    from acp.api.v1_schemas import CASSCFSpecModel, ElectronicStateModuleModel

    module = ElectronicStateModuleModel.model_validate(
        {
            "execution_mode": "state_sweep",
            "states": [
                {"state_id": "s1", "target_multiplicity": 1, "spin_mode": "restricted"},
                {"state_id": "t1", "target_multiplicity": 3, "spin_mode": "unrestricted"},
            ],
        }
    )
    payload = module.payload()
    assert payload["execution_mode"] == "state_sweep"
    assert payload["states"][1]["target_multiplicity"] == 3

    with pytest.raises(ValidationError):
        ElectronicStateModuleModel.model_validate(
            {"states": [{"state_id": "x", "spin_mode": "nonsense"}]}
        )

    casscf = CASSCFSpecModel.model_validate({"active_electrons": 4, "active_orbitals": 3})
    assert casscf.frozen_core is True
    with pytest.raises(ValidationError):
        CASSCFSpecModel.model_validate({"active_electrons": 0, "active_orbitals": 2})
