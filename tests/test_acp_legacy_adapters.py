# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""Legacy adapter tests (todo 11): in-memory round-trip, no file writes.

The adapter is data-transform only: round-trips must be lossless for
committed legacy fields (no new defaults, no lost fields, candidate/profile
restored via the ACP-side binding) and must not write any files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    Provenance,
    StructureArtifact,
)
from acp.calculations.legacy_adapters import (
    LegacyBinding,
    to_legacy_request,
    to_legacy_result,
    to_task_request,
    to_task_result,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    CasscfOptions,
    IrcDirection,
    OptimizeOptions,
    ScanOptions,
    SinglePointOptions,
    TaskKind,
    ThermochemistryOptions,
)
from cccp.calculation.results import OptimizePayload, ThermochemistryPayload


def _legacy_request(**resources: object) -> CalculationRequest:
    return CalculationRequest(
        input_artifact=StructureArtifact(
            path=Path("input/ts_001.xyz"),
            elements=["C", "H"],
            role="transition_state",
            source="upload",
            candidate_id="cand_001",
        ),
        method="wB97X-D4",
        resources=dict(resources),  # type: ignore[arg-type]
        workflow="BatchOptimize",
        profile="opt_freq",
    )


def test_named_exports_importable() -> None:
    from acp.calculations.legacy_adapters import to_legacy_result as a
    from acp.calculations.legacy_adapters import to_task_request as b

    assert callable(a) and callable(b)


def test_request_round_trip_exact_no_new_defaults() -> None:
    request = _legacy_request(
        basis="def2-TZVPP",
        charge=0,
        multiplicity=1,
        coordinates=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        symbols=["H", "H"],
        nproc=4,
        mem="8GB",
        output_dir="out/dir",
        engine="orca",
        structure_kind="ts",
        opt_rescue_policy="adaptive",
        opt_max_rescue=2,
        failure_type="bad_okay",
        trust_radius=0.2,
        unknown_key="kept",
        trajectory_item_id="item_9",
    )
    task_request, binding = to_task_request(request, "optimize")
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request
    # candidate/profile restored via the ACP-side binding
    assert rebuilt.input_artifact.candidate_id == "cand_001"
    assert rebuilt.profile == "opt_freq"
    assert rebuilt.workflow == "BatchOptimize"
    # no new defaults: absent keys stay absent
    assert "max_cycles" not in rebuilt.resources
    assert "geom_maxiter" not in rebuilt.resources
    assert "basis" in rebuilt.resources


def test_request_round_trip_minimal_stays_minimal() -> None:
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=Path("a.xyz")),
        method="r2SCAN-3c",
    )
    task_request, binding = to_task_request(request, "singlepoint")
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request
    assert rebuilt.resources == {}
    assert rebuilt.workflow == ""
    assert rebuilt.profile is None


def test_typed_homes_are_projected() -> None:
    request = _legacy_request(
        basis="def2-TZVPP",
        charge=-1,
        multiplicity=2,
        engine="orca",
        structure_kind="ts",
        opt_rescue_policy="off",
        opt_max_rescue=1,
        failure_type="bad_okay",
        trust_radius=0.15,
        recalc_hess=5,
        geom_maxiter=100,
        max_cycles=50,
        ts_mode=3,
        trajectory_item_id="item_9",
    )
    task_request, binding = to_task_request(request, "optimize")
    assert task_request.backend == "orca"
    assert task_request.level.method == "wB97X-D4"
    assert task_request.level.basis == "def2-TZVPP"
    assert task_request.charge == -1
    assert task_request.multiplicity == 2
    options = task_request.options
    assert isinstance(options, OptimizeOptions)
    assert options.mode.value == "transition_state"
    assert options.trust_radius == 0.15
    assert options.recalc_hess == 5
    assert options.geom_maxiter == 100
    assert options.max_cycles == 50
    assert options.ts is not None and options.ts.mode_index == 3
    assert options.rescue is not None
    assert options.rescue.policy == "off"
    assert options.rescue.max_rescue == 1
    assert options.rescue.failure_type == "bad_okay"
    # platform identity only on the binding
    assert binding.candidate_id == "cand_001"
    assert binding.trajectory_item_id == "item_9"
    assert binding.workflow == "BatchOptimize"
    assert binding.profile == "opt_freq"


def test_state_sweep_rejected_before_conversion() -> None:
    request = _legacy_request(
        electronic_state={
            "execution_mode": "state_sweep",
            "states": [
                {"state_id": "s1", "target_multiplicity": 1},
                {"state_id": "s2", "target_multiplicity": 3},
            ],
        }
    )
    with pytest.raises(TaskInputError, match="state_sweep"):
        to_task_request(request, "optimize")


def test_single_state_electronic_state_projected_and_raw_kept() -> None:
    raw_state = {
        "execution_mode": "single",
        "default_state_id": "s2",
        "states": [
            {"state_id": "s1", "target_multiplicity": 1},
            {"state_id": "s2", "target_multiplicity": 3, "spin_mode": "unrestricted"},
        ],
    }
    request = _legacy_request(electronic_state=raw_state)
    task_request, binding = to_task_request(request, "singlepoint")
    assert task_request.electronic_state is not None
    assert task_request.electronic_state.state_id == "s2"
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt.resources["electronic_state"] == raw_state


def test_thermochemistry_options_projection() -> None:
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=Path("ignored.xyz")),
        method="",
        resources={
            "freq_log_path": "freq.log",
            "sp_energy_hartree": -40.5,
            "temperature": 298.15,
            "pressure": 1.0,
            "standard_state": "1M",
            "scale_factor": 1.0,
            "ilowfreq": 2,
            "imagreal": 0,
            "conc": 1.0,
        },
    )
    task_request, binding = to_task_request(request, TaskKind.THERMOCHEMISTRY)
    options = task_request.options
    assert isinstance(options, ThermochemistryOptions)
    assert options.freq_log_path == Path("freq.log")
    assert options.sp_energy_hartree == -40.5
    assert options.temperature_k == 298.15
    assert options.pressure_atm == 1.0
    assert options.standard_state == "1M"
    assert options.scl_zpe == 1.0
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request


def test_scan_options_projection() -> None:
    request = _legacy_request(
        coordinate="3,4,1.0,3.0",
        scan_points=11,
    )
    task_request, binding = to_task_request(request, "scan")
    options = task_request.options
    assert isinstance(options, ScanOptions)
    assert options.points == 11
    assert len(options.coordinates) == 1
    assert options.coordinates[0].atoms == (3, 4)
    assert options.coordinates[0].start == 1.0
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request


def test_irc_directions_from_out_of_band_argument() -> None:
    request = _legacy_request()
    task_request, binding = to_task_request(request, "irc", directions=("forward",))
    options = task_request.options
    assert options is not None
    assert tuple(options.directions) == (IrcDirection.FORWARD,)
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request


def test_casscf_options_projection() -> None:
    request = _legacy_request(
        casscf={"active_electrons": 2, "active_orbitals": 2, "nroots": 2},
    )
    task_request, binding = to_task_request(request, "casscf")
    options = task_request.options
    assert isinstance(options, CasscfOptions)
    assert options.spec.active_electrons == 2
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request


def test_result_round_trip_exact_and_profile_restored() -> None:
    root = Path("/abs/task-root")
    result = CalculationResult(
        energy=-1.0,
        coords=[[0.0, 0.0, 0.0]],
        frequencies=[100.0],
        artifacts=[
            ArtifactRef(path=root / "WORK/a.out", type="out"),
            ArtifactRef(path=Path("/elsewhere/b.log"), type="log"),
        ],
        status="completed",
        errors=["note"],
        provenance=Provenance(
            backend="orca",
            method="wB97X-D4",
            profile="opt_freq_sp_thermo",
            version="6.0.1",
            input_signature="sha256:abc",
        ),
        metadata={
            "optimization_status": "converged",
            "rescue_attempts": 1,
            "rescue_actions": ["increase_trust"],
            "failure_type": "bad_okay",
            "electronic_state": {"state_id": "s1"},
            "custom_key": 42,
        },
    )
    binding = LegacyBinding(artifact_root=root)
    task_result, bound = to_task_result(result, "optimize", binding=binding)
    # root-relative artifact mapping (outside-root paths pass through)
    assert task_result.artifacts[0].path == Path("WORK/a.out")
    assert task_result.artifacts[1].path == Path("/elsewhere/b.log")
    payload = task_result.payload
    assert isinstance(payload, OptimizePayload)
    assert payload.optimization_status == "converged"
    assert payload.rescue_attempts == 1
    assert payload.rescue_actions == ("increase_trust",)
    assert payload.rescue_failure_type == "bad_okay"

    rebuilt = to_legacy_result(task_result, bound)
    assert rebuilt == result
    assert rebuilt.provenance is not None
    assert rebuilt.provenance.profile == "opt_freq_sp_thermo"


def test_thermochemistry_metadata_projection_round_trip() -> None:
    result = CalculationResult(
        metadata={
            "enthalpy_hartree": -40.0,
            "gibbs_hartree": -40.2,
            "entropy_au": 0.01,
            "selected_gibbs_source": "g_sum",
            "standard_state": "1atm",
            "legacy_shermo_result": {"g_sum": -40.2},
        }
    )
    task_result, bound = to_task_result(result, "thermochemistry")
    payload = task_result.payload
    assert isinstance(payload, ThermochemistryPayload)
    assert payload.gibbs_source == "g_sum"
    rebuilt = to_legacy_result(task_result, bound)
    assert rebuilt == result


def test_adapter_writes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("adapter must not write files")

    monkeypatch.setattr(Path, "write_text", _blocked)
    monkeypatch.setattr(Path, "write_bytes", _blocked)
    monkeypatch.chdir(tmp_path)

    request = _legacy_request(basis="def2-TZVPP", output_dir=str(tmp_path))
    task_request, binding = to_task_request(request, "optimize")
    to_legacy_request(task_request, binding)
    result = CalculationResult(
        energy=-1.0,
        artifacts=[ArtifactRef(path=tmp_path / "a.out", type="out")],
        provenance=Provenance(
            backend="orca", method="m", profile="p", version="v", input_signature="s"
        ),
    )
    task_result, bound = to_task_result(
        result, "optimize", binding=LegacyBinding(artifact_root=tmp_path)
    )
    to_legacy_result(task_result, bound)
    assert list(tmp_path.iterdir()) == []


def test_task_only_fields_dropped_in_legacy_projection() -> None:
    from cccp.calculation.contracts import Provenance as TaskProvenance
    from cccp.calculation.results import TaskResult as CccpTaskResult

    task_result = CccpTaskResult(
        task=TaskKind.SINGLEPOINT,
        symbols=("H", "H"),
        complete=False,
        converged=True,
        provenance=TaskProvenance(backend="orca", method="m", version="v", input_signature="s"),
    )
    legacy = to_legacy_result(task_result, LegacyBinding(profile="p"))
    assert legacy.provenance is not None
    assert legacy.provenance.profile == "p"
    assert "symbols" not in legacy.metadata


def test_singlepoint_stability_check_home() -> None:
    request = _legacy_request(stability_check=True)
    task_request, binding = to_task_request(request, "singlepoint")
    options = task_request.options
    assert isinstance(options, SinglePointOptions)
    assert options.stability_check is True
    rebuilt = to_legacy_request(task_request, binding)
    assert rebuilt == request
    # for other tasks the key stays verbatim on the residue
    task_request2, binding2 = to_task_request(request, "optimize")
    assert "stability_check" in binding2.resources_extra
    assert to_legacy_request(task_request2, binding2) == request
