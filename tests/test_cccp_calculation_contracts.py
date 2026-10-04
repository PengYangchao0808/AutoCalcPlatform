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
    TASK_OPTIONS_TYPES,
    TASK_PAYLOAD_TYPES,
    TASK_REQUEST_SCHEMA_VERSION,
    TASK_RESULT_SCHEMA_VERSION,
    ArtifactRef,
    CasscfOptions,
    ErrorKind,
    FrequencyOptions,
    IrcDirection,
    IrcOptions,
    MethodSpec,
    OptimizeOptions,
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
    resolve_context,
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
    FrequencyAnalysis,
    FrequencyPayload,
    IrcDirectionResult,
    IrcPayload,
    OptimizePayload,
    ScanPayload,
    SinglePointPayload,
    ThermochemistryPayload,
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

BANNED_IDENTITY_NAMES = {
    "workflow",
    "profile",
    "candidate_id",
    "trajectory_item_id",
    "state_sweep",
}


def _structure() -> StructureInput:
    return StructureInput(path=Path("input/mol.xyz"), elements=("C", "H"), source="upload")


def _request(task: TaskKind, options=None) -> TaskRequest:
    return TaskRequest(task=task, structure=_structure(), options=options)


# ── table ①: seven-core options/payload types ───────────────────────────


def test_seven_core_options_registry_complete() -> None:
    assert set(TASK_OPTIONS_TYPES) == set(TaskKind)
    assert {kind.value for kind in TaskKind} == SEVEN_CORE
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
    )
    for cls in checked:
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & BANNED_IDENTITY_NAMES, (cls.__name__, names & BANNED_IDENTITY_NAMES)


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
        "field-level mapping table",
        "TaskContext runtime rules",
        "Record identity",
        "Table ①",
        "Table ②",
    ):
        assert marker in text, f"spec document missing {marker!r}"
