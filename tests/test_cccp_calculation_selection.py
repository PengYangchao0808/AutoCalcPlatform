"""Two-step backend selection tests (plan todo 13).

Coverage: selection-record fields, explicit-ORCA strictness (missing ORCA
raises ``BackendUnavailableError`` and never falls back to xTB), unknown
capability rejection, per-context program-path precheck (two contexts, each
checked against its own pins — never a global-config reread), the seven-core
task→capability mapping (incl. casscf/NEVPT2), the semantic acceptance
matrix (normal vs TS optimization, scan mode/constraints, CASSCF±NEVPT2),
both entry shapes (``select_backend`` and ``select_semantic`` +
``precheck_runtime``), fresh-process import-order cycles, and the explicit
distinction between "backend method implemented" and "public task callable".
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from cccp.backends.matrix import CAPABILITY_MATRIX, TASK_CAPABILITY_MAP
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import (
    CASSCFSpec,
    DynamicCorrelation,
    OptimizationMode,
    StructureRole,
)
from cccp.calculation.errors import (
    BackendUnavailableError,
    TaskInputError,
    UnsupportedCapabilityError,
)
from cccp.calculation.requests import (
    CasscfOptions,
    MethodSpec,
    OptimizeOptions,
    ScanCoordinateSpec,
    ScanOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    ThermochemistryOptions,
    TsSpec,
)
from cccp.calculation.selection import (
    BackendSelection,
    ProgramRequirement,
    capability_requirement,
    precheck_runtime,
    select_backend,
    select_capability,
    select_semantic,
)

SEVEN_CORE = {"singlepoint", "optimize", "frequency", "scan", "irc", "casscf", "thermochemistry"}


# ── helpers ─────────────────────────────────────────────────────────────


def _fake_executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _context(**pins: Path) -> TaskContext:
    config = {"executables": {name: {"path": str(path)} for name, path in pins.items()}}
    return TaskContext(config=config)


def _structure(role: StructureRole = StructureRole.MINIMUM) -> StructureInput:
    return StructureInput(path=Path("molecule.xyz"), role=role)


def _request(task: TaskKind = TaskKind.SINGLEPOINT, **overrides: object) -> TaskRequest:
    payload: dict[str, object] = {
        "task": task,
        "structure": _structure(),
        "level": MethodSpec(method="wB97X-D4", basis="def2-SVP"),
        "charge": 0,
        "multiplicity": 1,
    }
    payload.update(overrides)
    return TaskRequest(**payload)  # type: ignore[arg-type]


def _forbid_global_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str, configured_path: object = None) -> Path:
        raise AssertionError(f"global program resolution consulted for {name!r}")

    monkeypatch.setattr("cccp.calculation.selection.resolve_executable", _boom)


# ── seven-core vocabulary and mapping ───────────────────────────────────


def test_taskkind_covers_seven_core() -> None:
    assert SEVEN_CORE <= set(TaskKind)


def test_seven_core_capability_mapping_includes_casscf() -> None:
    assert SEVEN_CORE <= set(TASK_CAPABILITY_MAP)
    assert "casscf" in TASK_CAPABILITY_MAP["casscf"]
    assert "nevpt2" in TASK_CAPABILITY_MAP["casscf"]
    for task_name, capabilities in TASK_CAPABILITY_MAP.items():
        assert capabilities, task_name
        for capability in capabilities:
            assert capability in CAPABILITY_MATRIX["orca"], (task_name, capability)


def test_thermochemistry_requires_shermo_program() -> None:
    request = _request(
        TaskKind.THERMOCHEMISTRY,
        structure=None,
        options=ThermochemistryOptions(freq_log_path=Path("freq.log")),
    )
    selection = select_semantic(request)
    assert selection.backend == "external"
    assert selection.capability == "thermochemistry"
    assert [program.name for program in selection.required_programs] == ["shermo"]


# ── acceptance matrix (seven core only; P2 rows land in todo 24) ────────


def _matrix_cases() -> list[tuple[str, TaskRequest, str, tuple[str, ...]]]:
    return [
        (
            "optimize_normal",
            _request(TaskKind.OPTIMIZE, options=OptimizeOptions()),
            "geometry_optimization",
            (),
        ),
        (
            "optimize_ts_mode",
            _request(
                TaskKind.OPTIMIZE,
                options=OptimizeOptions(mode=OptimizationMode.TRANSITION_STATE),
            ),
            "transition_state",
            (),
        ),
        (
            "optimize_ts_structure_role",
            _request(TaskKind.OPTIMIZE, structure=_structure(StructureRole.TRANSITION_STATE)),
            "transition_state",
            (),
        ),
        (
            "optimize_ts_spec",
            _request(TaskKind.OPTIMIZE, options=OptimizeOptions(ts=TsSpec(enabled=True))),
            "transition_state",
            (),
        ),
        (
            "optimize_constrained",
            _request(
                TaskKind.OPTIMIZE,
                options=OptimizeOptions(mode=OptimizationMode.CONSTRAINED),
            ),
            "constrained_optimization",
            (),
        ),
        (
            "scan_mode_relaxed",
            _request(
                TaskKind.SCAN,
                options=ScanOptions(
                    coordinates=(ScanCoordinateSpec(atoms=(0, 1), start=1.0, end=2.0),)
                ),
            ),
            "relaxed_scan",
            (),
        ),
        (
            "scan_with_constraints",
            _request(
                TaskKind.SCAN,
                options=ScanOptions(
                    coordinates=(
                        ScanCoordinateSpec(atoms=(0, 1), start=1.0, end=2.0),
                        ScanCoordinateSpec(atoms=(1, 2)),
                    )
                ),
            ),
            "constrained_relaxed_scan",
            (),
        ),
        (
            "casscf_plain",
            _request(
                TaskKind.CASSCF,
                options=CasscfOptions(spec=CASSCFSpec(active_electrons=2, active_orbitals=2)),
            ),
            "casscf",
            (),
        ),
        (
            "casscf_sc_nevpt2",
            _request(
                TaskKind.CASSCF,
                options=CasscfOptions(
                    spec=CASSCFSpec(
                        active_electrons=2,
                        active_orbitals=2,
                        dynamic_correlation=DynamicCorrelation.SC_NEVPT2,
                    )
                ),
            ),
            "casscf",
            ("nevpt2",),
        ),
        (
            "casscf_fic_nevpt2",
            _request(
                TaskKind.CASSCF,
                options=CasscfOptions(
                    spec=CASSCFSpec(
                        active_electrons=2,
                        active_orbitals=2,
                        dynamic_correlation=DynamicCorrelation.FIC_NEVPT2,
                    )
                ),
            ),
            "casscf",
            ("nevpt2",),
        ),
    ]


@pytest.mark.parametrize(
    ("case_name", "task_request", "capability", "also_required"),
    _matrix_cases(),
    ids=[case[0] for case in _matrix_cases()],
)
def test_semantic_acceptance_matrix(
    case_name: str,
    task_request: TaskRequest,
    capability: str,
    also_required: tuple[str, ...],
) -> None:
    selection = select_semantic(task_request)
    assert selection.capability == capability, case_name
    assert selection.also_required == also_required, case_name
    assert selection.backend in CAPABILITY_MATRIX
    assert selection.reason
    assert selection.params["capability"] == capability
    assert selection.runtime_checked is False


# ── selection record fields ─────────────────────────────────────────────


def test_selection_record_fields() -> None:
    selection = select_semantic(_request())
    record = selection.to_dict()
    for key in (
        "task",
        "capability",
        "also_required",
        "backend",
        "reason",
        "params",
        "required_programs",
        "explicit_backend",
        "candidates",
        "runtime_checked",
    ):
        assert key in record, key
    assert selection.backend == "orca"
    assert selection.params["method"] == "wB97X-D4"
    assert selection.params["basis"] == "def2-SVP"
    assert selection.params["charge"] == 0
    assert selection.params["multiplicity"] == 1
    assert selection.candidates
    program_fields = {field.name for field in ProgramRequirement.__dataclass_fields__.values()}
    assert {"name", "configured_path", "resolved_path", "available", "source"} <= program_fields
    assert isinstance(selection, BackendSelection)


def test_final_params_carry_electronic_state_and_option_constraints() -> None:
    from cccp.calculation.contracts import ElectronicStateSpec

    request = _request(
        TaskKind.OPTIMIZE,
        electronic_state=ElectronicStateSpec(state_id="s1", target_multiplicity=3),
        options=OptimizeOptions(mode=OptimizationMode.TRANSITION_STATE),
    )
    params = select_semantic(request).params
    assert params["electronic_state"]["state_id"] == "s1"
    assert params["electronic_state"]["target_multiplicity"] == 3
    assert params["optimization_mode"] == "transition_state"
    assert params["structure_role"] == "minimum"


# ── both entry shapes: combined and split ───────────────────────────────


def test_select_backend_matches_split_forms(tmp_path: Path) -> None:
    orca = _fake_executable(tmp_path / "orca", "orca")
    context = _context(orca=orca)
    request = _request()
    combined = select_backend(request, context=context)
    split = precheck_runtime(select_semantic(request), context)
    assert combined == split
    assert combined.runtime_checked is True
    assert combined.required_programs[0].available is True
    assert combined.required_programs[0].resolved_path == str(orca.resolve())
    assert combined.required_programs[0].source == "config"


# ── explicit ORCA strictness (no silent switch) ─────────────────────────


def test_explicit_orca_missing_binary_unavailable_and_never_switches_to_xtb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xtb = _fake_executable(tmp_path / "xtb", "xtb")
    context = _context(orca=tmp_path / "missing-orca", xtb=xtb)
    request = _request(backend="orca")

    semantic = select_semantic(request)
    assert semantic.backend == "orca"
    assert semantic.explicit_backend is True

    def _always_available(name: str, configured_path: object = None) -> Path:
        return xtb

    monkeypatch.setattr("cccp.calculation.selection.resolve_executable", _always_available)
    with pytest.raises(BackendUnavailableError) as excinfo:
        select_backend(request, context=context)
    message = str(excinfo.value)
    assert "orca" in message
    assert "xtb" not in message


def test_explicit_backend_without_capability_is_unsupported() -> None:
    with pytest.raises(UnsupportedCapabilityError):
        select_semantic(_request(TaskKind.FREQUENCY, backend="xtb"))


def test_auto_backend_string_rejected() -> None:
    with pytest.raises(TaskInputError):
        select_semantic(_request(backend="auto"))


# ── unknown capability → UnsupportedCapabilityError ─────────────────────


def test_unknown_capability_unsupported() -> None:
    with pytest.raises(UnsupportedCapabilityError):
        select_capability("not_a_capability")


def test_reserved_capability_without_implementer_unsupported() -> None:
    with pytest.raises(UnsupportedCapabilityError):
        select_capability("rigid_scan")


def test_explicit_backend_nevpt2_requires_orca() -> None:
    request = _request(
        TaskKind.CASSCF,
        backend="xtb",
        options=CasscfOptions(
            spec=CASSCFSpec(
                active_electrons=2,
                active_orbitals=2,
                dynamic_correlation=DynamicCorrelation.SC_NEVPT2,
            )
        ),
    )
    with pytest.raises(UnsupportedCapabilityError):
        select_semantic(request)


# ── per-context runtime precheck (no global-config reread) ──────────────


def test_two_contexts_precheck_their_own_program_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orca_a = _fake_executable(tmp_path / "install-a", "orca")
    orca_b = _fake_executable(tmp_path / "install-b", "orca")
    context_a = _context(orca=orca_a)
    context_b = _context(orca=orca_b)
    request = _request()

    _forbid_global_resolution(monkeypatch)
    selection_a = select_backend(request, context=context_a)
    selection_b = select_backend(request, context=context_b)
    assert selection_a.required_programs[0].resolved_path == str(orca_a.resolve())
    assert selection_b.required_programs[0].resolved_path == str(orca_b.resolve())
    assert (
        selection_a.required_programs[0].resolved_path
        != selection_b.required_programs[0].resolved_path
    )


def test_missing_binary_in_this_context_raises_despite_other_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orca = _fake_executable(tmp_path / "elsewhere", "orca")
    context = _context(orca=tmp_path / "not-there-orca")

    def _always_available(name: str, configured_path: object = None) -> Path:
        return orca

    monkeypatch.setattr("cccp.calculation.selection.resolve_executable", _always_available)
    with pytest.raises(BackendUnavailableError):
        select_backend(_request(), context=context)


def test_pinned_context_never_consults_environment_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orca = _fake_executable(tmp_path, "orca")
    _forbid_global_resolution(monkeypatch)
    selection = select_backend(_request(), context=_context(orca=orca))
    assert selection.required_programs[0].source == "config"


def test_context_none_uses_environment_chain_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Path("/nonexistent/orca-binary")
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable", lambda name, configured_path=None: fake
    )
    selection = select_backend(_request(), context=None)
    assert selection.runtime_checked is True
    assert selection.required_programs[0].source == "environment"
    assert selection.required_programs[0].configured_path is None

    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable", lambda name, configured_path=None: None
    )
    with pytest.raises(BackendUnavailableError):
        select_backend(_request(), context=None)
    with pytest.raises(BackendUnavailableError):
        select_backend(_request(), context=TaskContext())


def test_selection_module_never_reads_default_config() -> None:
    import ast

    source = (Path(__file__).resolve().parents[1] / "src/cccp/calculation/selection.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    assert not any(name.startswith("cccp.config") for name in imports), imports
    assert not any(name.startswith("acp") for name in imports), imports
    for forbidden in ("load_config", "_get_default_config", "get_default_config"):
        assert forbidden not in calls, forbidden


# ── import-order cycles and laziness (fresh processes) ──────────────────

_IMPORT_PROBE = (
    "import sys\n"
    "{order}\n"
    "assert not any(n.startswith('cccp.calculation.tasks') for n in sys.modules)\n"
    "print('ok')\n"
)


def test_fresh_process_import_order_backends_first() -> None:
    code = _IMPORT_PROBE.format(
        order="import cccp.backends\nimport cccp.calculation\nimport cccp.calculation.selection"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_fresh_process_import_order_calculation_first() -> None:
    code = _IMPORT_PROBE.format(
        order="import cccp.calculation\nimport cccp.backends\nimport cccp.calculation.selection"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_selection_module_does_not_load_tasks() -> None:
    code = (
        "import sys, cccp.calculation.selection\n"
        "assert not any(n.startswith('cccp.calculation.tasks') for n in sys.modules)\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


# ── mapping done ≠ task executable ──────────────────────────────────────


def test_backend_method_implemented_is_not_task_callable() -> None:
    import cccp.calculation
    from cccp.backends.orca import ORCABackend

    assert callable(getattr(ORCABackend, "transition_state_opt", None))
    assert callable(getattr(ORCABackend, "casscf", None))
    assert callable(cccp.calculation.run_singlepoint), "singlepoint landed in todo 17"
    assert callable(cccp.calculation.run_optimize), "optimize landed in todo 18"
    assert callable(cccp.calculation.run_frequency), "frequency landed in todo 19"
    assert callable(cccp.calculation.run_scan), "scan landed in todo 20"
    assert callable(cccp.calculation.run_irc), "irc landed in todo 21"
    assert callable(cccp.calculation.run_casscf), "casscf landed in todo 22"
    assert callable(cccp.calculation.run_thermochemistry), "thermochemistry landed in todo 22"
    for name in (
        "execute",
    ):
        assert not hasattr(cccp.calculation, name), name

    assert importlib.util.find_spec("cccp.calculation.tasks.singlepoint") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.optimize") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.frequency") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.scan") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.irc") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.casscf") is not None
    assert importlib.util.find_spec("cccp.calculation.tasks.thermochemistry") is not None
    requirement = capability_requirement(_request())
    assert requirement.capability == "single_point"


def test_selection_import_does_not_load_tasks_in_process() -> None:
    code = (
        "import sys\n"
        "import cccp.calculation.selection as selection\n"
        "from cccp.calculation.contracts import StructureRole\n"
        "from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest\n"
        "request = TaskRequest(\n"
        "    task=TaskKind.SINGLEPOINT,\n"
        "    structure=StructureInput(\n"
        "        coordinates=((0.0, 0.0, 0.0),), symbols=('C',), role=StructureRole.MINIMUM\n"
        "    ),\n"
        ")\n"
        "selection.select_semantic(request)\n"
        "assert not any(n.startswith('cccp.calculation.tasks') for n in sys.modules)\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
