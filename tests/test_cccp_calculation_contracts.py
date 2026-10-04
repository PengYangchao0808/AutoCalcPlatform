# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""Contract-layer tests for ``cccp.calculation`` (todo 11).

Covers: options type mismatch -> TaskInputError, to_dict/from_dict
round-trips (unknown fields / invalid enum / NaN / Inf / schema_version),
TaskRequest/TaskContext separation, platform-identity quarantine, record
identity, TaskContext runtime rules, pure-type import isolation, and the
field-level mapping table of docs/ACP_CCCP_Task_API_DevDoc.md.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import cccp.calculation.contracts as contracts
from cccp.calculation import (
    P2_TASK_CONTRACTS,
    TASK_OPTIONS_TYPES,
    TASK_PAYLOAD_TYPES,
    TASK_REQUEST_SCHEMA_VERSION,
    TASK_RESULT_SCHEMA_VERSION,
    ArtifactRef,
    BackendInputFragment,
    BackendInputKind,
    CasscfOptions,
    CensoLevelOverride,
    CensoRefineOptions,
    ClusteringOptions,
    ConformerSearchOptions,
    ErrorKind,
    FragmentConflictRule,
    FrequencyOptions,
    IrcDirection,
    IrcOptions,
    MdSamplingOptions,
    MethodSpec,
    NmrShieldingOptions,
    OptimizeOptions,
    OrcaGradientOptions,
    ProgressCallbackError,
    ProgressEvent,
    ProgressEventKind,
    RescueSpec,
    ScanCoordinateSpec,
    ScanFrame,
    ScanMode,
    ScanOptions,
    SinglePointOptions,
    StructureInput,
    TaskCancelledError,
    TaskContext,
    TaskInputError,
    TaskKind,
    TaskRequest,
    TaskResources,
    TaskResult,
    ThermochemistryOptions,
    TsSpec,
    XtbPathSearchOptions,
    fragment_structured_conflicts,
    resolve_context,
    resolve_fragment_conflicts,
    validate_request,
)
from cccp.calculation.contracts import (
    CASSCFSpec,
    ElectronicStateSpec,
    GuessSpec,
    OptimizationMode,
    validate_electronic_state_spec,
)
from cccp.calculation.contracts import (
    Provenance as TaskProvenance,
)
from cccp.calculation.results import (
    CasscfPayload,
    CensoRefinePayload,
    CensoRefineRecord,
    ClusterAssignment,
    ClusteringPayload,
    ConformerEnergy,
    ConformerSearchPayload,
    FrequencyAnalysis,
    FrequencyPayload,
    IrcDirectionResult,
    IrcPayload,
    MdSamplingPayload,
    NmrShielding,
    NmrShieldingPayload,
    OptimizePayload,
    OrcaGradientPayload,
    ScanPayload,
    SinglePointPayload,
    ThermochemistryPayload,
    XtbPathFrame,
    XtbPathSearchPayload,
)

SEVEN_CORE = {
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
    "thermochemistry",
}

P2_TASKS = {
    "conformer_search",
    "md_sampling",
    "clustering",
    "censo_refine",
    "nmr_shielding",
    "xtb_path_search",
    "orca_gradient",
}

BANNED_IDENTITY_NAMES = {
    "workflow",
    "profile",
    "candidate_id",
    "trajectory_item_id",
    "state_sweep",
}

BANNED_PES2TS_IDENTITY_NAMES = {
    "reaction_id",
    "plan_sha256",
    "request_sha256",
    "config_digest",
    "adapter_version",
}


def _structure() -> StructureInput:
    return StructureInput(path=Path("input/mol.xyz"), elements=("C", "H"), source="upload")


def _request(task: TaskKind, options=None) -> TaskRequest:
    return TaskRequest(task=task, structure=_structure(), options=options)


# ── table ①: seven-core options/payload types ───────────────────────────


def test_seven_core_options_registry_complete() -> None:
    assert set(TASK_OPTIONS_TYPES) == set(TaskKind)
    assert {kind.value for kind in TaskKind} == SEVEN_CORE | P2_TASKS
    assert TASK_OPTIONS_TYPES[TaskKind.SINGLEPOINT] is SinglePointOptions
    assert TASK_OPTIONS_TYPES[TaskKind.OPTIMIZE] is OptimizeOptions
    assert TASK_OPTIONS_TYPES[TaskKind.FREQUENCY] is FrequencyOptions
    assert TASK_OPTIONS_TYPES[TaskKind.SCAN] is ScanOptions
    assert TASK_OPTIONS_TYPES[TaskKind.IRC] is IrcOptions
    assert TASK_OPTIONS_TYPES[TaskKind.CASSCF] is CasscfOptions
    assert TASK_OPTIONS_TYPES[TaskKind.THERMOCHEMISTRY] is ThermochemistryOptions


def test_seven_core_payload_registry_complete() -> None:
    assert set(TASK_PAYLOAD_TYPES) == set(TaskKind)
    assert TASK_PAYLOAD_TYPES[TaskKind.SINGLEPOINT] is SinglePointPayload
    assert TASK_PAYLOAD_TYPES[TaskKind.OPTIMIZE] is OptimizePayload
    assert TASK_PAYLOAD_TYPES[TaskKind.FREQUENCY] is FrequencyPayload
    assert TASK_PAYLOAD_TYPES[TaskKind.SCAN] is ScanPayload
    assert TASK_PAYLOAD_TYPES[TaskKind.IRC] is IrcPayload
    assert TASK_PAYLOAD_TYPES[TaskKind.CASSCF] is CasscfPayload
    assert TASK_PAYLOAD_TYPES[TaskKind.THERMOCHEMISTRY] is ThermochemistryPayload


def test_options_type_mismatch_raises_task_input_error() -> None:
    request = _request(TaskKind.OPTIMIZE, SinglePointOptions())
    with pytest.raises(TaskInputError, match="options type mismatch"):
        validate_request(request)


def test_options_type_match_accepted() -> None:
    validate_request(_request(TaskKind.OPTIMIZE, OptimizeOptions()))
    validate_request(_request(TaskKind.SCAN, ScanOptions(points=11)))


# ── P2 task contracts (todo 24) ─────────────────────────────────────────


def test_p2_registries_cover_all_tasks() -> None:
    assert set(TASK_OPTIONS_TYPES) == set(TaskKind)
    assert set(TASK_PAYLOAD_TYPES) == set(TaskKind)
    assert P2_TASKS <= {kind.value for kind in TaskKind}
    assert set(P2_TASK_CONTRACTS) == {TaskKind(name) for name in P2_TASKS}


def _p2_options_cases():
    return [
        (TaskKind.CONFORMER_SEARCH, ConformerSearchOptions(energy_window=6.0, gfn_level=2)),
        (
            TaskKind.MD_SAMPLING,
            MdSamplingOptions(
                md_method="gfnff",
                gfn_level=0,
                temperature_k=400.0,
                time_ps=100.0,
                dump_fs=100.0,
                step_fs=1.0,
                hmass=1.0,
                shake=True,
                nvt=True,
                seed=42,
            ),
        ),
        (
            TaskKind.CLUSTERING,
            ClusteringOptions(edis=0.5, gdis=0.25, temperature_k=298.15, nout=10),
        ),
        (
            TaskKind.CENSO_REFINE,
            CensoRefineOptions(
                preset="censo-light",
                level_overrides=(CensoLevelOverride("refinement", "wb97m-v", "def2-tzvpp", 0.99),),
                temperature_k=298.15,
            ),
        ),
        (TaskKind.NMR_SHIELDING, NmrShieldingOptions(atom_indices=(0, 3), atom_index_base=0)),
        (
            TaskKind.XTB_PATH_SEARCH,
            XtbPathSearchOptions(
                end_structure=StructureInput(path=Path("end.xyz")),
                gfn_level=2,
                uhf=0,
                seed=7,
                backend_inputs=(
                    BackendInputFragment(
                        kind=BackendInputKind.PATH_INP_TEXT,
                        source="recipe.path_inp_text",
                        content="$path\n$end\n",
                        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
                    ),
                    BackendInputFragment(
                        kind=BackendInputKind.EXTRA_ARGS,
                        source="recipe.extra_args",
                        content=("--input", "path.inp"),
                        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
                    ),
                ),
            ),
        ),
        (
            TaskKind.ORCA_GRADIENT,
            OrcaGradientOptions(
                backend_inputs=(
                    BackendInputFragment(
                        kind=BackendInputKind.ROUTE_EXTRAS,
                        source="route_extras",
                        content=("TightSCF",),
                        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
                    ),
                    BackendInputFragment(
                        kind=BackendInputKind.EXTRA_BLOCKS,
                        source="extra_blocks",
                        content=("%pal nprocs 2 end",),
                        conflict_rule=FragmentConflictRule.REJECT_ON_CONFLICT,
                    ),
                    BackendInputFragment(
                        kind=BackendInputKind.OUTPUT_NAME,
                        source="output_name",
                        content="grad",
                        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
                    ),
                )
            ),
        ),
    ]


def test_p2_options_round_trip_all_seven() -> None:
    for task, options in _p2_options_cases():
        request = TaskRequest(task=task, structure=_structure(), options=options)
        validate_request(request)
        payload = request.to_dict()
        assert TaskRequest.from_dict(payload) == request, task


def _p2_payload_cases():
    return [
        (
            TaskKind.CONFORMER_SEARCH,
            ConformerSearchPayload(
                ensemble_ref=ArtifactRef(path=Path("crest_conformers.xyz"), type="ensemble"),
                conformer_count=2,
                energy_table=(
                    ConformerEnergy("c1", 0, -1.0),
                    ConformerEnergy("c2", 1, -0.9),
                ),
            ),
        ),
        (
            TaskKind.MD_SAMPLING,
            MdSamplingPayload(
                trajectory_ref=ArtifactRef(path=Path("traj.xyz"), type="trajectory"),
                n_frames=120,
            ),
        ),
        (
            TaskKind.CLUSTERING,
            ClusteringPayload(
                assignments=(
                    ClusterAssignment(0, 3, (0, 3, 5)),
                    ClusterAssignment(1, 4, (4,)),
                ),
                clustered_ref=ArtifactRef(path=Path("cluster.xyz"), type="clustered_ensemble"),
            ),
        ),
        (
            TaskKind.CENSO_REFINE,
            CensoRefinePayload(
                records=(
                    CensoRefineRecord("c1", 0, -1.0, -1.1, 0.7),
                    CensoRefineRecord("c2", 1, -0.9, -1.0, 0.3),
                ),
                refined_ensemble_ref=ArtifactRef(path=Path("refined.xyz"), type="ensemble"),
            ),
        ),
        (
            TaskKind.NMR_SHIELDING,
            NmrShieldingPayload(
                shieldings={
                    0: NmrShielding("H", 31.2),
                    3: NmrShielding("C", 120.5),
                }
            ),
        ),
        (
            TaskKind.XTB_PATH_SEARCH,
            XtbPathSearchPayload(
                trajectory_ref=ArtifactRef(path=Path("xtbpath.xyz"), type="trajectory"),
                frames=(XtbPathFrame(0, -1.0), XtbPathFrame(1, -1.05), XtbPathFrame(2, -0.9)),
                start_frame_index=0,
                end_frame_index=2,
            ),
        ),
        (
            TaskKind.ORCA_GRADIENT,
            OrcaGradientPayload(
                gradients=((0.0, 0.0, 0.01), (0.0, 0.0, -0.01)),
                energy_hartree=-1.0,
            ),
        ),
    ]


def test_p2_result_round_trip_all_seven() -> None:
    for task, payload in _p2_payload_cases():
        result = TaskResult(
            task=task,
            status="failed",
            complete=False,
            error_kind=ErrorKind.NOT_CONVERGED,
            errors=("partial",),
            symbols=("H", "H"),
            artifacts=(ArtifactRef(path=Path("WORK/a.out"), type="out"),),
            payload=payload,
        )
        encoded = result.to_dict()
        assert TaskResult.from_dict(encoded) == result, task


def test_p2_mapping_table_covers_all_axes() -> None:
    for task, mapping in P2_TASK_CONTRACTS.items():
        assert mapping.input_shape in {
            "single_structure",
            "ensemble",
            "structure_pair",
        }, task
        assert mapping.capability, task
        assert mapping.backends, task
        assert mapping.success and mapping.partial and mapping.empty, task
        assert mapping.artifact_identity and mapping.record_identity, task


def test_backend_input_fragment_is_scoped_and_digests() -> None:
    fragment = BackendInputFragment(
        kind=BackendInputKind.PATH_INP_TEXT,
        source="recipe.path_inp_text",
        content="$path\n   nrun=100\n$end\n",
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    assert fragment.content_digest == fragment.compute_digest()
    same = BackendInputFragment(
        kind=BackendInputKind.PATH_INP_TEXT,
        source="other.field",
        content="$path\n   nrun=100\n$end\n",
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    assert same.content_digest == fragment.content_digest
    other = BackendInputFragment(
        kind=BackendInputKind.PATH_INP_TEXT,
        source="recipe.path_inp_text",
        content="$path\n   nrun=200\n$end\n",
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    assert other.content_digest != fragment.content_digest
    assert BackendInputFragment.from_dict(fragment.to_dict()) == fragment
    assert fragment.cache_signature() == {
        "kind": "path_inp_text",
        "content_digest": fragment.content_digest,
    }


def test_backend_input_fragment_rejects_unknown_kind_and_wrong_rule() -> None:
    with pytest.raises(TaskInputError, match="fragment kind must be one of"):
        BackendInputFragment(
            kind="renamed_passthrough",  # type: ignore[arg-type]
            source="x",
            content="y",
            conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
        )
    with pytest.raises(TaskInputError, match="must use conflict_rule"):
        BackendInputFragment(
            kind=BackendInputKind.EXTRA_BLOCKS,
            source="extra_blocks",
            content=("%pal end",),
            conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
        )


def test_fragment_conflicts_are_deterministic() -> None:
    agreeing = BackendInputFragment(
        kind=BackendInputKind.EXTRA_ARGS,
        source="recipe.extra_args",
        content=("--gfn", "2"),
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    contradicting = BackendInputFragment(
        kind=BackendInputKind.EXTRA_ARGS,
        source="recipe.extra_args",
        content=("--gfn", "3"),
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    structured = {"gfn_level": 2}
    assert fragment_structured_conflicts(agreeing, structured) == ()
    assert fragment_structured_conflicts(contradicting, structured) == ("gfn_level",)
    assert resolve_fragment_conflicts(agreeing, structured) == ()
    assert resolve_fragment_conflicts(contradicting, structured) == ("gfn_level",)


def test_fragment_reject_on_conflict_raises() -> None:
    fragment = BackendInputFragment(
        kind=BackendInputKind.EXTRA_BLOCKS,
        source="extra_blocks",
        content=("%pal nprocs 8 end",),
        conflict_rule=FragmentConflictRule.REJECT_ON_CONFLICT,
    )
    with pytest.raises(TaskInputError, match="contradicts structured knob"):
        resolve_fragment_conflicts(fragment, {"nproc": 2})
    assert resolve_fragment_conflicts(fragment, {"nproc": 8}) == ()


def test_fragment_scope_per_task_is_closed() -> None:
    wrong = BackendInputFragment(
        kind=BackendInputKind.OUTPUT_NAME,
        source="output_name",
        content="grad",
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    with pytest.raises(TaskInputError, match="xtb_path_search accepts fragments"):
        XtbPathSearchOptions(backend_inputs=(wrong,))
    orca_only = BackendInputFragment(
        kind=BackendInputKind.PATH_INP_TEXT,
        source="recipe.path_inp_text",
        content="x",
        conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    )
    with pytest.raises(TaskInputError, match="orca_gradient accepts fragments"):
        OrcaGradientOptions(backend_inputs=(orca_only,))


def test_p2_request_cache_signature_covers_fragments() -> None:
    def signature(options: XtbPathSearchOptions) -> dict:
        return dict(options.cache_signature())

    base = XtbPathSearchOptions(
        gfn_level=2,
        backend_inputs=(
            BackendInputFragment(
                kind=BackendInputKind.PATH_INP_TEXT,
                source="recipe.path_inp_text",
                content="$path\n$end\n",
                conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
            ),
        ),
    )
    changed = XtbPathSearchOptions(
        gfn_level=2,
        backend_inputs=(
            BackendInputFragment(
                kind=BackendInputKind.PATH_INP_TEXT,
                source="recipe.path_inp_text",
                content="$path\n   nrun=5\n$end\n",
                conflict_rule=FragmentConflictRule.STRUCTURED_FIELDS_WIN,
            ),
        ),
    )
    assert signature(base) != signature(changed)
    assert (
        signature(base)["backend_inputs"][0]["content_digest"]
        == base.backend_inputs[0].content_digest
    )
    request = TaskRequest(task=TaskKind.XTB_PATH_SEARCH, structure=_structure(), options=base)
    request_changed = TaskRequest(
        task=TaskKind.XTB_PATH_SEARCH, structure=_structure(), options=changed
    )
    assert request.to_dict()["options"] != request_changed.to_dict()["options"]


def test_xtb_path_search_requires_structure_pair() -> None:
    with pytest.raises(TaskInputError, match="end_structure"):
        validate_request(
            TaskRequest(
                task=TaskKind.XTB_PATH_SEARCH,
                structure=_structure(),
                options=XtbPathSearchOptions(),
            )
        )


def test_nmr_shielding_key_shape_atom_to_symbol_isotropic() -> None:
    payload = NmrShieldingPayload(shieldings={0: NmrShielding("H", 31.2)})
    encoded = payload.to_dict()
    assert encoded["shieldings"] == {"0": {"symbol": "H", "isotropic": 31.2}}
    restored = NmrShieldingPayload.from_dict(encoded)
    assert restored == payload
    assert set(restored.shieldings[0].to_dict()) == {"symbol", "isotropic"}


def test_censo_refine_carries_no_template_text() -> None:
    names = {f.name for f in dataclasses.fields(CensoRefineOptions)}
    assert not any("template" in name or "rcfile" in name or "text" in name for name in names)
    payload = CensoRefineOptions(preset="censo-light").to_dict()
    assert "template" not in payload and "rcfile" not in payload


# ── serialization rules S1–S7 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("task", "options"),
    [
        (TaskKind.SINGLEPOINT, SinglePointOptions(stability_check=True)),
        (
            TaskKind.OPTIMIZE,
            OptimizeOptions(
                mode=OptimizationMode.TRANSITION_STATE,
                initial_hessian="calculate",
                recalc_hess=5,
                trust_radius=0.2,
                max_cycles=60,
                geom_maxiter=120,
                ts=TsSpec(enabled=True, mode_index=3),
                rescue=RescueSpec(policy="off", max_rescue=1, failure_type="bad_okay"),
            ),
        ),
        (TaskKind.FREQUENCY, FrequencyOptions()),
        (
            TaskKind.SCAN,
            ScanOptions(
                coordinates=(
                    ScanCoordinateSpec(atoms=(3, 4), start=1.0, end=3.0, atom_index_base=1),
                ),
                points=21,
                values=(1.0, 2.0),
                mode=ScanMode.RELAXED,
            ),
        ),
        (
            TaskKind.IRC,
            IrcOptions(directions=(IrcDirection.FORWARD,), maxpoints=20, step=0.15),
        ),
        (
            TaskKind.CASSCF,
            CasscfOptions(spec=CASSCFSpec(active_electrons=2, active_orbitals=2, nroots=2)),
        ),
        (
            TaskKind.THERMOCHEMISTRY,
            ThermochemistryOptions(
                freq_log_path=Path("freq.log"),
                sp_energy_hartree=-40.5,
                temperature_k=298.15,
                pressure_atm=1.0,
                standard_state="1M",
                scl_zpe=1.0,
                ilowfreq=2,
                imagreal=0,
                conc=1.0,
            ),
        ),
    ],
)
def test_task_request_round_trip_all_seven(task: TaskKind, options) -> None:
    request = TaskRequest(
        task=task,
        structure=None if task is TaskKind.THERMOCHEMISTRY else _structure(),
        charge=-1,
        multiplicity=2,
        level=MethodSpec(method="wB97X-D4", basis="def2-TZVPP", solvent="water"),
        backend="orca",
        electronic_state=ElectronicStateSpec(state_id="s1", target_multiplicity=2),
        options=options,
        resources=TaskResources(nproc=4, mem="8GB", maxcore=1000, timeout_s=60.0),
        output_dir=Path("out"),
    )
    validate_request(request)
    payload = request.to_dict()
    assert payload["schema_version"] == TASK_REQUEST_SCHEMA_VERSION
    assert TaskRequest.from_dict(payload) == request


@pytest.mark.parametrize(
    ("task", "payload"),
    [
        (TaskKind.SINGLEPOINT, SinglePointPayload(electronic_state={"s2": 0.1})),
        (
            TaskKind.OPTIMIZE,
            OptimizePayload(
                optimization_status="converged",
                rescue_failure_type="bad_okay",
                rescue_actions=("increase_trust",),
                rescue_attempts=1,
                electronic_state={"state_id": "s1"},
                trajectory_ref=ArtifactRef(path=Path("WORK/t.json"), type="trajectory"),
            ),
        ),
        (
            TaskKind.FREQUENCY,
            FrequencyPayload(
                n_imaginary=1,
                freq_log_ref=ArtifactRef(path=Path("freq.log"), type="frequency_log"),
                analysis=FrequencyAnalysis(
                    frequencies=(-797.72, 1411.55),
                    imaginary_frequencies=(-797.72,),
                    ir_intensities=(66.542, 81.914),
                    mode_frequencies={0: 0.0, 6: -797.72},
                    mode_vectors={6: ((0.01, 0.02, 0.03),)},
                    mode_ir_intensities={6: 66.542},
                ),
            ),
        ),
        (
            TaskKind.SCAN,
            ScanPayload(
                frames=(
                    ScanFrame(index=0, values=(1.0,), energy_hartree=-1.0, converged=True),
                    ScanFrame(index=1, values=(1.1,), success=False, converged=False),
                    ScanFrame(index=2, values=(1.2,), energy_hartree=-1.1, converged=True),
                ),
                profile_ref=ArtifactRef(path=Path("profile.json"), type="scan_profile"),
            ),
        ),
        (
            TaskKind.IRC,
            IrcPayload(
                directions=(
                    IrcDirectionResult(
                        direction=IrcDirection.FORWARD,
                        energy_hartree=-2.0,
                        coordinates=((0.0, 0.0, 0.0),),
                        symbols=("H",),
                        converged=True,
                        steps=12,
                    ),
                    IrcDirectionResult(direction=IrcDirection.REVERSE, success=False),
                )
            ),
        ),
        (
            TaskKind.CASSCF,
            CasscfPayload(
                root_energies=(-1.0, -0.9),
                natural_occupations=(1.98, 0.02),
                nevpt2_energies=(-1.1, -1.0),
                active_space="(2,2)",
            ),
        ),
        (
            TaskKind.THERMOCHEMISTRY,
            ThermochemistryPayload(
                enthalpy_hartree=-40.0,
                gibbs_hartree=-40.2,
                entropy_au=0.01,
                gibbs_source="g_sum",
                standard_state="1atm",
            ),
        ),
    ],
)
def test_task_result_round_trip_all_seven(task: TaskKind, payload) -> None:
    result = TaskResult(
        task=task,
        status="failed",
        complete=False,
        error_kind=ErrorKind.NOT_CONVERGED,
        errors=("unit 2 did not converge",),
        energy_hartree=-40.5,
        coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        symbols=("H", "H"),
        frequencies=(100.0, 200.0),
        converged=False,
        artifacts=(ArtifactRef(path=Path("WORK/a.out"), type="out"),),
        provenance=TaskProvenance(
            backend="orca", method="m", version="6.0", input_signature="sha256:x"
        ),
        payload=payload,
        metadata={"extra": 1},
    )
    payload_dict = result.to_dict()
    assert payload_dict["schema_version"] == TASK_RESULT_SCHEMA_VERSION
    assert TaskResult.from_dict(payload_dict) == result


def test_unknown_fields_are_ignored_on_from_dict() -> None:
    payload = {
        "schema_version": 1,
        "task": "singlepoint",
        "structure": {"path": "w.xyz", "role": "minimum", "source": ""},
        "totally_unknown": {"nested": 1},
    }
    request = TaskRequest.from_dict(payload)
    assert "totally_unknown" not in request.to_dict()
    assert request.task is TaskKind.SINGLEPOINT


def test_unknown_schema_version_rejected() -> None:
    with pytest.raises(TaskInputError, match="unsupported schema_version"):
        TaskRequest.from_dict({"schema_version": 99, "task": "singlepoint"})
    with pytest.raises(TaskInputError, match="unsupported schema_version"):
        TaskResult.from_dict({"schema_version": 2, "task": "optimize"})


def test_invalid_enum_rejected() -> None:
    with pytest.raises(TaskInputError, match="task must be one of"):
        TaskRequest.from_dict({"schema_version": 1, "task": "not-a-task"})
    with pytest.raises(TaskInputError, match="options.mode must be one of"):
        TaskRequest.from_dict(
            {
                "schema_version": 1,
                "task": "optimize",
                "options": {"mode": "sideways"},
            }
        )
    with pytest.raises(TaskInputError, match="error_kind must be one of"):
        TaskResult.from_dict({"schema_version": 1, "task": "optimize", "error_kind": "mystery"})


def test_nan_inf_rejected_both_directions() -> None:
    result = TaskResult(task=TaskKind.SINGLEPOINT, energy_hartree=float("nan"))
    with pytest.raises(TaskInputError, match="finite"):
        result.to_dict()
    with pytest.raises(TaskInputError, match="finite"):
        TaskResult.from_dict(
            {"schema_version": 1, "task": "singlepoint", "energy_hartree": float("inf")}
        )
    request = TaskRequest(
        task=TaskKind.OPTIMIZE,
        structure=_structure(),
        options=OptimizeOptions(trust_radius=float("nan")),
    )
    with pytest.raises(TaskInputError, match="finite"):
        request.to_dict()


def test_type_mismatch_rejected_strict() -> None:
    with pytest.raises(TaskInputError, match="charge must be an integer"):
        TaskRequest.from_dict({"schema_version": 1, "task": "singlepoint", "charge": "zero"})
    with pytest.raises(TaskInputError, match="structure.role must be one of"):
        TaskRequest.from_dict(
            {
                "schema_version": 1,
                "task": "singlepoint",
                "structure": {"path": "w.xyz", "role": "banana"},
            }
        )


# ── request/context separation + identity quarantine ───────────────────


def test_task_request_and_context_are_separate() -> None:
    request_fields = {f.name for f in dataclasses.fields(TaskRequest)}
    context_fields = {f.name for f in dataclasses.fields(TaskContext)}
    for runtime_name in ("config", "workdir", "progress"):
        assert runtime_name not in request_fields
        assert runtime_name in context_fields
    for intent_name in ("task", "options", "level", "resources"):
        assert intent_name in request_fields
        assert intent_name not in context_fields


def test_platform_identity_absent_from_cccp_contracts() -> None:
    checked = (
        TaskRequest,
        TaskResult,
        TaskResources,
        MethodSpec,
        StructureInput,
        SinglePointOptions,
        OptimizeOptions,
        FrequencyOptions,
        ScanOptions,
        ScanFrame,
        ScanCoordinateSpec,
        IrcOptions,
        CasscfOptions,
        ThermochemistryOptions,
        TsSpec,
        RescueSpec,
        TaskProvenance,
        contracts.StructureArtifact,
        contracts.ArtifactRef,
        ElectronicStateSpec,
        CASSCFSpec,
        TaskContext,
        ProgressEvent,
        ConformerSearchOptions,
        MdSamplingOptions,
        ClusteringOptions,
        CensoRefineOptions,
        NmrShieldingOptions,
        XtbPathSearchOptions,
        OrcaGradientOptions,
        BackendInputFragment,
    )
    for cls in checked:
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & BANNED_IDENTITY_NAMES, (cls.__name__, names & BANNED_IDENTITY_NAMES)
        assert not names & BANNED_PES2TS_IDENTITY_NAMES, (
            cls.__name__,
            names & BANNED_PES2TS_IDENTITY_NAMES,
        )


def test_state_sweep_absent_from_cccp_surface() -> None:
    assert not hasattr(contracts, "ElectronicStateConfig")
    assert not hasattr(contracts, "ElectronicStateExecutionMode")


# ── resource semantics + input-shape rules ──────────────────────────────


def test_mem_maxcore_relation_enforced() -> None:
    with pytest.raises(TaskInputError, match="cannot cover"):
        validate_request(
            TaskRequest(
                task=TaskKind.SINGLEPOINT,
                structure=_structure(),
                resources=TaskResources(nproc=4, maxcore=2000, mem="4GB"),
            )
        )
    validate_request(
        TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=_structure(),
            resources=TaskResources(nproc=4, maxcore=1000, mem="8GB"),
        )
    )


def test_backend_auto_rejected() -> None:
    with pytest.raises(TaskInputError, match="backend='auto'"):
        validate_request(
            TaskRequest(task=TaskKind.SINGLEPOINT, structure=_structure(), backend="auto")
        )


def test_input_shape_rules() -> None:
    with pytest.raises(TaskInputError, match="requires a structure"):
        validate_request(TaskRequest(task=TaskKind.SINGLEPOINT))
    with pytest.raises(TaskInputError, match="frequency log"):
        validate_request(
            TaskRequest(task=TaskKind.THERMOCHEMISTRY, options=ThermochemistryOptions())
        )
    with pytest.raises(TaskInputError, match="takes no structure"):
        validate_request(
            TaskRequest(
                task=TaskKind.THERMOCHEMISTRY,
                structure=_structure(),
                options=ThermochemistryOptions(freq_log_path=Path("f.log")),
            )
        )
    with pytest.raises(TaskInputError, match="requires ThermochemistryOptions"):
        validate_request(TaskRequest(task=TaskKind.THERMOCHEMISTRY))
    validate_request(
        TaskRequest(
            task=TaskKind.THERMOCHEMISTRY,
            options=ThermochemistryOptions(freq_log_path=Path("f.log")),
        )
    )
    inline = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("H",)),
    )
    validate_request(inline)


# ── record identity + partial-result contracts ─────────────────────────


def test_scan_failed_frames_keep_original_index() -> None:
    payload = ScanPayload(
        frames=(
            ScanFrame(index=0, energy_hartree=-1.0),
            ScanFrame(index=1, success=False),
            ScanFrame(index=2, energy_hartree=-1.2),
        )
    )
    result = TaskResult(task=TaskKind.SCAN, complete=False, status="failed", payload=payload)
    restored = TaskResult.from_dict(result.to_dict())
    frames = restored.payload.frames  # type: ignore[union-attr]
    assert [frame.index for frame in frames] == [0, 1, 2]
    assert frames[1].success is False
    assert frames[1].energy_hartree is None
    assert restored.complete is False
    assert restored.status == "failed"


def test_irc_one_way_keeps_valid_sub_result() -> None:
    payload = IrcPayload(
        directions=(
            IrcDirectionResult(direction=IrcDirection.FORWARD, energy_hartree=-2.0, steps=10),
            IrcDirectionResult(direction=IrcDirection.REVERSE, success=False),
        )
    )
    result = TaskResult(
        task=TaskKind.IRC,
        status="failed",
        complete=False,
        error_kind=ErrorKind.BACKEND_FAILURE,
        payload=payload,
    )
    restored = TaskResult.from_dict(result.to_dict())
    directions = restored.payload.directions  # type: ignore[union-attr]
    assert [entry.direction for entry in directions] == [IrcDirection.FORWARD, IrcDirection.REVERSE]
    assert directions[0].energy_hartree == -2.0
    assert directions[1].success is False
    assert restored.complete is False


def test_error_kind_taxonomy_and_no_partial_status() -> None:
    values = {kind.value for kind in ErrorKind}
    assert {
        "invalid_input",
        "unsupported_capability",
        "backend_unavailable",
        "backend_failure",
        "not_converged",
        "timeout",
        "parse_failure",
        "cancelled",
    } <= values
    assert "partial" not in values


def test_atom_index_base_explicit() -> None:
    guess = GuessSpec(flip_atoms=(1, 2), atom_index_base=1)
    assert guess.orca_flip_atoms() == (0, 1)
    zero = GuessSpec(flip_atoms=(0, 1), atom_index_base=0)
    assert zero.orca_flip_atoms() == (0, 1)
    coordinate = ScanCoordinateSpec(atoms=(1, 2), start=1.0, end=2.0, atom_index_base=1)
    assert coordinate.atom_index_base == 1
    with pytest.raises(ValueError):
        ScanCoordinateSpec(atoms=(1, 2), atom_index_base=2)


def test_per_state_validation_keeps_legacy_messages() -> None:
    state = ElectronicStateSpec(state_id="s1")
    outcome = validate_electronic_state_spec(state, backend="xtb", n_atoms=1)
    assert outcome.is_valid
    flipped = ElectronicStateSpec(
        state_id="bs",
        spin_mode="broken_symmetry",
    )
    outcome = validate_electronic_state_spec(flipped, backend="xtb")
    assert any("broken_symmetry is only supported on the ORCA backend" in e for e in outcome.errors)


# ── TaskContext runtime rules R1/R6/R8 ──────────────────────────────────


def test_resolve_context_defaults_rule_r1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    request = TaskRequest(task=TaskKind.SINGLEPOINT, structure=_structure())
    context = resolve_context(request, None)
    assert context.config is None  # config acquisition deferred to execution
    assert context.workdir == Path.cwd()
    assert context.input_root() == Path.cwd()
    with_dir = TaskRequest(
        task=TaskKind.SINGLEPOINT, structure=_structure(), output_dir=tmp_path / "out"
    )
    assert resolve_context(with_dir, None).workdir == tmp_path / "out"
    explicit = TaskContext(workdir=tmp_path)
    assert resolve_context(request, explicit) is explicit


class _ExplodingSink:
    def emit(self, event: ProgressEvent) -> None:
        raise RuntimeError("sink exploded")


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


def test_progress_failure_isolated_from_scientific_failure() -> None:
    context = TaskContext(progress=_ExplodingSink())
    delivered = context.emit_progress(ProgressEvent(kind=ProgressEventKind.METRIC, stage="sp"))
    assert delivered is False
    assert context.progress_errors()
    # the context itself stays usable; failures never raise out of emit_progress
    assert context.is_cancelled() is False


def test_progress_thread_safety_rule_r6() -> None:
    sink = _RecordingSink()
    context = TaskContext(progress=sink)

    def _emit(index: int) -> None:
        context.emit_progress(ProgressEvent(kind=ProgressEventKind.METRIC, stage=f"t{index}"))

    threads = [threading.Thread(target=_emit, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(sink.events) == 8
    assert not context.progress_errors()


def test_cancellation_rule_r8() -> None:
    flags = {"cancelled": False}
    context = TaskContext(cancelled=lambda: flags["cancelled"])
    assert context.is_cancelled() is False
    flags["cancelled"] = True
    assert context.is_cancelled() is True


def test_progress_event_carries_no_ui_fields() -> None:
    names = {f.name for f in dataclasses.fields(ProgressEvent)}
    assert "label_key" not in names
    assert "priority" not in names
    assert {"kind", "stage", "metric", "value", "unit", "index"} <= names


# ── errors.py extension (no redefinition) ───────────────────────────────


def test_error_taxonomy_extension() -> None:
    from cccp.calculation.errors import CalculationError

    assert issubclass(TaskCancelledError, CalculationError)
    assert issubclass(ProgressCallbackError, CalculationError)
    assert issubclass(TaskInputError, ValueError)


# ── import isolation probes (fresh processes) ───────────────────────────


def test_package_import_does_not_load_tasks() -> None:
    code = (
        "import sys, cccp.calculation as m\n"
        "assert not any(n.startswith('cccp.calculation.tasks') for n in sys.modules)\n"
        "assert not any(n.startswith('cccp.qc') for n in sys.modules)\n"
        "m.TaskRequest; m.TaskResult\n"
        "assert not any(n.startswith('cccp.calculation.tasks') for n in sys.modules)\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_contracts_pure_type_isolation() -> None:
    code = (
        "import sys, cccp.calculation.contracts as m\n"
        "bad = ('cccp.calculation.tasks', 'cccp.qc.interfaces')\n"
        "assert not any(n.startswith(bad) for n in sys.modules)\n"
        "assert not hasattr(m, 'CalculationPlan')\n"
        "assert not hasattr(m, 'CalculationRequest')\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


# ``test_relocated_types_keep_identity`` moved to tests/test_calculations_contracts.py
# (todo 16: cccp-side tests must not import the acp package).


# ── the current spec document (single source) ───────────────────────────


def test_current_spec_document_exists_and_is_complete() -> None:
    doc = Path("docs/ACP_CCCP_Task_API_DevDoc.md")
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    for marker in (
        "TaskRequest",
        "TaskContext",
        "TaskResources",
        "TaskResult",
        "ErrorKind",
        "ArtifactRef",
        "SinglePointOptions",
        "OptimizeOptions",
        "FrequencyOptions",
        "ScanOptions",
        "IrcOptions",
        "CasscfOptions",
        "ThermochemistryOptions",
        "SinglePointPayload",
        "OptimizePayload",
        "FrequencyPayload",
        "FrequencyAnalysis",
        "ScanPayload",
        "IrcPayload",
        "CasscfPayload",
        "ThermochemistryPayload",
        "ConformerSearchOptions",
        "MdSamplingOptions",
        "ClusteringOptions",
        "CensoRefineOptions",
        "NmrShieldingOptions",
        "XtbPathSearchOptions",
        "OrcaGradientOptions",
        "BackendInputFragment",
        "ConformerSearchPayload",
        "MdSamplingPayload",
        "ClusteringPayload",
        "CensoRefinePayload",
        "NmrShieldingPayload",
        "XtbPathSearchPayload",
        "OrcaGradientPayload",
        "P2_TASK_CONTRACTS",
        "PES2TS_XTB_PATH_CONVERSION",
        "PES2TS_ORCA_GRADIENT_CONVERSION",
        "field-level mapping table",
        "TaskContext runtime rules",
        "Record identity",
        "Table ①",
        "Table ②",
    ):
        assert marker in text, f"spec document missing {marker!r}"
