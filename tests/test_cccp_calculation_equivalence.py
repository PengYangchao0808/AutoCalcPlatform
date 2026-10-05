"""A7 goldens equivalence — accumulated from plan todo 17 onward.

Group ①/③ of the A7 matrix for ``singlepoint``: an independent cccp call's
effective translation parameters must be identical to the **pre-migration**
goldens in ``tests/baseline/cccp_calculation_goldens/`` (frozen record — a
backfilled golden would carry a post-migration commit).  Future todos append
their capability cases here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import cccp.qc.shermo_adapter as shermo_module
from cccp.calculation._common import level_explicit_fields, render_backend_input, resolve_spec
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
from cccp.calculation.tasks.singlepoint import run_singlepoint

GOLDENS_DIR = Path(__file__).resolve().parent / "baseline" / "cccp_calculation_goldens"

#: Pre-migration generation commit of the frozen goldens (anti-backfill pin).
GOLDENS_SOURCE_COMMIT = "f1c49a148ff85c4fd4ff7167dd7fdeb64e5c434f"


class _CaptureBackend:
    name = "orca"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def single_point(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        from cccp.qc.interfaces.base import QCResult

        self.calls.append({"symbols": list(symbols), "kwargs": dict(kwargs)})
        return QCResult(success=True, energy=-1.0, symbols=list(symbols), converged=True)

    def frequency(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        from cccp.qc.interfaces.base import QCResult

        self.calls.append({"symbols": list(symbols), "kwargs": dict(kwargs)})
        return QCResult(
            success=True,
            energy=-1.0,
            symbols=list(symbols),
            converged=True,
            frequencies=[100.0],
            has_frequencies=True,
        )


def _load(name: str) -> dict[str, Any]:
    return json.loads((GOLDENS_DIR / name).read_text(encoding="utf-8"))


def _extract_fields(text: str) -> dict[str, Any]:
    """Deterministic extraction of the translated fields (mirrors the generator)."""
    lines = text.splitlines()
    route_line = next((ln for ln in lines if ln.startswith("!")), "")

    def _block(name: str) -> list[str]:
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == name)
        except StopIteration:
            return []
        out: list[str] = []
        for ln in lines[start + 1 :]:
            if ln.strip() == "end":
                break
            out.append(ln.strip())
        return out

    basis_block = _block("%basis")
    aux_j = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxJ")), None)
    aux_c = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxC")), None)
    inline_basis = next(
        (ln.split('"')[1] for ln in basis_block if ln.startswith("basis ")), None
    )
    return {
        "route_line": route_line,
        "route_tokens": route_line.lstrip("! ").split(),
        "basis_inline": inline_basis,
        "aux_j": aux_j,
        "aux_c": aux_c,
        "solvent_block": _block("%cpcm"),
        "scf_block": _block("%scf"),
    }


def test_goldens_are_pre_migration_and_non_empty() -> None:
    manifest = _load("manifest.json")
    assert manifest["schema"] == "cccp_calculation_goldens_manifest_v1"
    assert manifest["source_commit"] == GOLDENS_SOURCE_COMMIT
    routes = _load("orca_routes.json")
    assert routes["cases"], "goldens must be non-empty"
    assert any(case["input_params"]["calc_type"] == "sp" for case in routes["cases"])


def test_singlepoint_effective_params_match_goldens() -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    routes = _load("orca_routes.json")
    config: dict[str, Any] = {
        "executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}},
        "resources": {"mem": "8GB", "nproc": 4},
    }
    for case in routes["cases"]:
        params = case["input_params"]
        if params["calc_type"] != "sp":
            continue
        level = MethodSpec(
            method=params["method"],
            basis=params.get("basis") or "",
            dispersion=params.get("dispersion"),
            solvent=params.get("solvent"),
            solvent_model=params.get("solvent_model"),
            integration_grid=params.get("grid"),
            auxiliary_basis_j=params.get("aux_j_basis"),
            auxiliary_basis_c=params.get("aux_c_basis"),
        )
        symbols = list(params["symbols"])
        request = TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(
                coordinates=tuple((0.0, float(i), 0.0) for i in range(len(symbols))),
                symbols=tuple(symbols),
            ),
            level=level,
        )
        backend = _CaptureBackend()
        result = run_singlepoint(request, context=TaskContext(backend=backend))
        assert result.status == "completed", case["id"]
        assert backend.calls, case["id"]

        spec = resolve_spec(level.method or None, explicit=level_explicit_fields(level))
        rendered = render_backend_input(spec, method=level.method or None)
        call_kwargs = dict(rendered)
        interface = ORCAInterface(
            dict(config),
            method=str(call_kwargs.pop("method") or params["method"]),
            basis=str(call_kwargs.pop("basis", None) or "") or params.get("basis") or "",
        )
        text, _resolution = interface._build_input_blocks(
            calc_type="sp",
            symbols=symbols,
            recalc_hess=None,
            **call_kwargs,
        )
        assert text == case["rendered_input"], f"rendered input drifted for {case['id']}"
        extracted = _extract_fields(text)
        for key in ("route_line", "route_tokens", "basis_inline", "aux_j", "aux_c"):
            assert extracted[key] == case["parsed_fields"][key], f"{case['id']}.{key}"
        assert extracted["solvent_block"] == case["parsed_fields"]["solvent_block"]
        assert extracted["scf_block"] == case["parsed_fields"]["scf_block"]

        captured = backend.calls[0]["kwargs"]
        assert captured.get("method") == params["method"], case["id"]


def test_singlepoint_task_request_round_trips_through_serialisation() -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.7)),
            symbols=("H", "H"),
        ),
        level=MethodSpec(method="HF", basis="def2-SVP"),
    )
    restored = TaskRequest.from_dict(request.to_dict())
    assert restored == request


# ── optimize rescue matrix (todo 18) ────────────────────────────────────


def test_optimize_rescue_metadata_matches_goldens() -> None:
    """build_rescue_plan cells + rescue metadata fields == pre-migration goldens."""
    import dataclasses

    from cccp.calculation.tasks.optimize import (
        FAILURE_EXIT,
        _FAILURE_TYPES,
        _RESCUE_DESCRIPTIONS,
        _RESCUE_MATRIX,
        build_rescue_plan,
    )

    golden = _load("optimize_rescue.json")
    cells = golden["cells"]
    assert cells, "optimize_rescue goldens must be non-empty"
    golden_keys = {(cell["failure_type"], cell["structure_kind"]) for cell in cells}
    assert golden_keys == set(_RESCUE_MATRIX)
    for cell in cells:
        plan = build_rescue_plan(
            cell["failure_type"],
            cell["structure_kind"],
            explicit_ts_target=cell["explicit_ts_target"],
        )
        assert plan.failure_type == cell["failure_type"]
        assert plan.rescue_structure_kind == cell["structure_kind"]
        assert plan.terminal == cell["terminal"], cell
        assert [dataclasses.asdict(action) for action in plan.actions] == cell["actions"], cell

    tokens = _load("error_tokens.json")
    assert sorted(_FAILURE_TYPES) == tokens["failure_types"]
    assert sorted(FAILURE_EXIT) == tokens["failure_exit"]
    assert _RESCUE_DESCRIPTIONS == tokens["rescue_strategies"]


def test_optimize_payload_carries_derived_rescue_diagnostics() -> None:
    """Derived diagnostics are typed payload output, never a writable input."""
    from cccp.calculation.requests import OptimizeOptions, RescueSpec
    from cccp.calculation.results import OptimizePayload

    options = OptimizeOptions(rescue=RescueSpec(failure_type="memory_failure"))
    assert options.rescue.failure_type == "memory_failure"
    payload = OptimizePayload(
        optimization_status="converged",
        rescue_failure_type="scf_failure",
        rescue_structure_kind="minimum",
        rescue_actions=("scf_increase_maxiter",),
        rescue_attempts=1,
        rescue_terminal=False,
    )
    assert payload.to_dict()["rescue_failure_type"] == "scf_failure"
    assert payload.to_dict()["rescue_structure_kind"] == "minimum"
    restored = OptimizePayload.from_dict(payload.to_dict())
    assert restored == payload


# ── frequency (todo 19) ────────────────────────────────────────────────


def test_frequency_effective_params_match_goldens() -> None:
    """Group ①: an independent frequency call renders the pre-migration input."""
    from cccp.calculation.tasks.frequency import run_frequency
    from cccp.qc.interfaces.orca import ORCAInterface

    routes = _load("orca_routes.json")
    config: dict[str, Any] = {
        "executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}},
        "resources": {"mem": "8GB", "nproc": 4},
    }
    matched = 0
    for case in routes["cases"]:
        params = case["input_params"]
        if params["calc_type"] != "freq":
            continue
        matched += 1
        level = MethodSpec(
            method=params["method"],
            basis=params.get("basis") or "",
            dispersion=params.get("dispersion"),
            solvent=params.get("solvent"),
            solvent_model=params.get("solvent_model"),
            integration_grid=params.get("grid"),
            auxiliary_basis_j=params.get("aux_j_basis"),
            auxiliary_basis_c=params.get("aux_c_basis"),
        )
        symbols = list(params["symbols"])
        request = TaskRequest(
            task=TaskKind.FREQUENCY,
            structure=StructureInput(
                coordinates=tuple((0.0, float(i), 0.0) for i in range(len(symbols))),
                symbols=tuple(symbols),
            ),
            level=level,
        )
        backend = _CaptureBackend()
        result = run_frequency(request, context=TaskContext(backend=backend))
        assert result.status == "completed", case["id"]
        assert result.frequencies == (100.0,), case["id"]
        assert backend.calls, case["id"]

        spec = resolve_spec(level.method or None, explicit=level_explicit_fields(level))
        rendered = render_backend_input(spec, method=level.method or None)
        call_kwargs = dict(rendered)
        interface = ORCAInterface(
            dict(config),
            method=str(call_kwargs.pop("method") or params["method"]),
            basis=str(call_kwargs.pop("basis", None) or "") or params.get("basis") or "",
        )
        text, _resolution = interface._build_input_blocks(
            calc_type="freq",
            symbols=symbols,
            recalc_hess=None,
            **call_kwargs,
        )
        assert text == case["rendered_input"], f"rendered input drifted for {case['id']}"
        extracted = _extract_fields(text)
        for key in ("route_line", "route_tokens", "basis_inline", "aux_j", "aux_c"):
            assert extracted[key] == case["parsed_fields"][key], f"{case['id']}.{key}"
        assert extracted["solvent_block"] == case["parsed_fields"]["solvent_block"]
        assert extracted["scf_block"] == case["parsed_fields"]["scf_block"]

        captured = backend.calls[0]["kwargs"]
        assert captured.get("method") == params["method"], case["id"]
    assert matched, "frequency goldens must be non-empty"


def test_frequency_payload_round_trips_with_analysis() -> None:
    """FrequencyPayload.analysis round-trips losslessly through serialisation."""
    from cccp.calculation.results import FrequencyAnalysis, FrequencyPayload

    analysis = FrequencyAnalysis(
        frequencies=(-797.72, 1411.55),
        imaginary_frequencies=(-797.72,),
        ir_intensities=(66.542, 81.914),
        mode_frequencies={0: 0.0, 6: -797.72, 8: 1411.55},
        mode_vectors={6: ((0.01, 0.02, 0.03), (0.04, 0.05, 0.06), (0.07, 0.08, 0.09))},
        mode_ir_intensities={6: 66.542},
    )
    payload = FrequencyPayload(n_imaginary=1, analysis=analysis)
    serialised = payload.to_dict()
    assert serialised["analysis"]["mode_frequencies"]["6"] == -797.72  # type: ignore[index]
    restored = FrequencyPayload.from_dict(serialised)
    assert restored == payload
    assert restored.analysis is not None
    assert restored.analysis.mode_vectors[6] == analysis.mode_vectors[6]


# ── scan (todo 20) ──────────────────────────────────────────────────────

_SCAN_ATOMS = ("C", "C", "O", "H")
_SCAN_COORDS = ((0.0, 0.0, 0.0), (1.2, 0.0, 0.0), (0.0, 1.1, 0.0), (1.2, 1.1, 0.0))
_SCAN_COORDINATES = ["0,1,1.2,2.4", "2,3,0.5,1.5"]


class _ScanStubBackend:
    """Frozen-golden scan stub (mirrors ``generate_goldens._StubBackend``)."""

    name = "orca"

    def __init__(self, scan_result: Any) -> None:
        self.scan_result = scan_result
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def relaxed_scan(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("relaxed_scan")
        return self.scan_result


def _scan_point(index: int, ok: bool, energy: float | None) -> Any:
    import numpy as np

    from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint

    coords = np.asarray(_SCAN_COORDS, dtype=float) + index * 0.05
    return RelaxedScanPoint(
        frame_index=index,
        progress=index / 3,
        coordinates=coords if ok else None,
        symbols=list(_SCAN_ATOMS) if ok else None,
        energy_hartree=energy,
        success=ok,
        coordinate_values={"rc1": 1.2 + index * 0.1, "rc2": 0.5 + index * 0.1},
    )


def _scan_stub_result(points: list[tuple[int, bool, float | None]], message: str = "") -> Any:
    from cccp.qc.interfaces.xtb_scan import RelaxedScanResult

    return RelaxedScanResult(
        points=[_scan_point(index, ok, energy) for index, ok, energy in points],
        input_xyz=Path("input.xyz"),
        scan_dir=Path("."),
        success=all(ok for _index, ok, _energy in points),
        message=message,
    )


def _scan_request(tmp_root: Path, case_id: str) -> Any:
    import numpy as np

    from acp.calculations.contracts import CalculationRequest, StructureArtifact
    from cccp.utils import file_io

    work = tmp_root / "scan_work"
    work.mkdir(parents=True, exist_ok=True)
    xyz = work / "input.xyz"
    if not xyz.is_file():
        file_io.write_xyz(xyz, np.asarray(_SCAN_COORDS, dtype=float), list(_SCAN_ATOMS))
    case_dir = work / case_id
    return CalculationRequest(
        input_artifact=StructureArtifact(path=xyz, elements=list(_SCAN_ATOMS)),
        method="B3LYP",
        resources={
            "backend": "orca",
            "output_dir": str(case_dir),
            "result_dir": str(case_dir / "RESULT"),
            "scan_coordinates": list(_SCAN_COORDINATES),
            "scan_points": 4,
        },
        workflow="scan",
    )


def _run_scan_case(tmp_root: Path, case_id: str, scan_result: Any) -> dict[str, Any]:
    """Replay one frozen scan golden through the switched pipeline."""
    from unittest.mock import patch

    from acp.calculations.primitives.scan import run_scan

    stub = _ScanStubBackend(scan_result)
    request = _scan_request(tmp_root, case_id)
    with patch("cccp.backends.registry.get_backend", lambda name: stub):
        calc = run_scan(request)
    return {
        "status": calc.status,
        "errors": list(calc.errors),
        "energy": calc.energy,
        "metadata": dict(calc.metadata),
        "artifacts": [
            {"type": artifact.type, "name": Path(artifact.path).name} for artifact in calc.artifacts
        ],
    }


def test_scan_plan_metadata_matches_goldens() -> None:
    """Group ①: plan compilation reproduces the pre-migration plan metadata."""
    from acp.calculations.primitives.scan import _build_scan_plan, _plan_metadata

    golden = _load("scan.json")
    expected = golden["multi_coordinate_plan"]
    request = _scan_request(Path("/tmp"), "plan_case")
    plan = _build_scan_plan(request)
    assert plan.points == expected["scan_points"]
    assert _plan_metadata(plan) == expected["plan_metadata"]


def test_scan_runs_match_goldens(tmp_path: Path) -> None:
    """Group ③: complete + partial-failure scans equal the frozen goldens.

    Frame/energy-curve semantics (per-frame geometry products, trajectory
    registration, best-point energy, partial-failure status rule) must be
    byte-compatible with the pre-migration record.
    """
    golden = _load("scan.json")

    complete = _run_scan_case(
        tmp_path,
        "complete_run",
        _scan_stub_result(
            [
                (0, True, -100.0),
                (1, True, -100.1),
                (2, True, -100.2),
                (3, True, -100.05),
            ]
        ),
    )
    assert complete == golden["complete_run"]

    partial = _run_scan_case(
        tmp_path,
        "partial_failure_run",
        _scan_stub_result(
            [
                (0, True, -100.0),
                (1, False, None),
                (2, True, -100.2),
                (3, False, None),
            ],
            message="relaxed scan aborted: 2 of 4 frames failed",
        ),
    )
    assert partial == golden["partial_failure_run"]


def test_scan_payload_round_trips_with_frames() -> None:
    """ScanPayload frames keep original indices through serialisation."""
    from cccp.calculation.contracts import ArtifactRef
    from cccp.calculation.results import ScanFrame, ScanPayload

    payload = ScanPayload(
        frames=(
            ScanFrame(index=0, values=(1.2,), energy_hartree=-1.0, converged=True),
            ScanFrame(index=1, values=(1.3,), success=False, converged=False),
            ScanFrame(index=2, values=(1.4,), energy_hartree=-1.1, converged=True),
        ),
        profile_ref=ArtifactRef(path=Path("scan_profile.json"), type="scan_profile"),
    )
    serialised = payload.to_dict()
    assert [frame["index"] for frame in serialised["frames"]] == [0, 1, 2]  # type: ignore[index]
    restored = ScanPayload.from_dict(serialised)
    assert restored == payload
    assert restored.frames[1].index == 1
    assert restored.frames[1].success is False


# ── irc (todo 21) ───────────────────────────────────────────────────────

_IRC_COORDS = (
    (0.0, 0.0, 0.0),
    (1.5, 0.0, 0.0),
    (-0.5, 0.9, 0.0),
    (-0.5, -0.9, 0.0),
    (2.0, 0.9, 0.0),
    (2.0, -0.9, 0.0),
)
_IRC_SYMBOLS = ["C", "C", "H", "H", "H", "H"]


class _IrcStubBackend:
    """Frozen-golden IRC stub (mirrors ``generate_goldens._StubBackend``)."""

    name = "orca"

    def __init__(self, irc_result: Any) -> None:
        self.irc_result = irc_result
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def irc(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("irc")
        return self.irc_result


def _normalize_irc(value: Any, tmp_root: Path) -> Any:
    marker = str(tmp_root)
    if isinstance(value, str):
        return value.replace(marker, "<TMP>")
    if isinstance(value, Path):
        return str(value).replace(marker, "<TMP>")
    if isinstance(value, dict):
        return {str(k): _normalize_irc(v, tmp_root) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_irc(v, tmp_root) for v in value]
    return value


def _irc_artifact(tmp_root: Path) -> Any:
    import numpy as np

    from acp.calculations.contracts import StructureArtifact, StructureRole
    from cccp.utils import file_io

    work = tmp_root / "irc_work"
    work.mkdir(parents=True, exist_ok=True)
    xyz = work / "ts.xyz"
    file_io.write_xyz(
        xyz, np.asarray(_IRC_COORDS, dtype=float), list(_IRC_SYMBOLS), title="TS golden input"
    )
    return StructureArtifact(
        path=xyz,
        elements=list(_IRC_SYMBOLS),
        role=StructureRole.TRANSITION_STATE,
        source="golden",
    )


def _run_irc_case(
    tmp_root: Path, case_id: str, irc_result: Any, directions: tuple[str, ...]
) -> dict[str, Any]:
    """Replay one frozen IRC golden through the switched pipeline."""
    from unittest.mock import patch

    from acp.calculations.primitives.irc import run_irc

    artifact = _irc_artifact(tmp_root)
    work = tmp_root / "irc_work"
    stub = _IrcStubBackend(irc_result)
    with patch("cccp.backends.registry.get_backend", lambda name: stub):
        calc = run_irc(
            artifact,
            directions=directions,
            resources={
                "backend": "orca",
                "output_dir": str(work / case_id),
                "result_dir": str(work / case_id / "RESULT"),
            },
            workflow="irc",
        )
    return _normalize_irc(
        {
            "status": calc.status,
            "errors": list(calc.errors),
            "metadata": dict(calc.metadata),
            "artifacts": [
                {"type": artifact.type, "name": Path(artifact.path).name}
                for artifact in calc.artifacts
            ],
        },
        tmp_root,
    )


def test_irc_direction_resolution_matches_goldens() -> None:
    """Group ①: direction keyword resolution equals the pre-migration record."""
    from cccp.calculation.tasks.irc import resolve_direction

    golden = _load("irc.json")
    expected = golden["direction_resolution"]
    assert resolve_direction(("forward", "reverse")) == expected["both"]
    assert resolve_direction(("forward",)) == expected["forward_only"]
    assert resolve_direction(("reverse",)) == expected["reverse_only"]
    assert resolve_direction(()) == expected["empty_falls_back"]


def test_irc_completed_direction_semantics_match_goldens() -> None:
    """Group ①: one-way completion verdicts equal the pre-migration record."""
    from cccp.backends.base import QCResult
    from cccp.calculation.tasks.irc import completed_directions

    golden = _load("irc.json")
    expected = golden["completed_direction_semantics"]
    raw = QCResult(
        success=True,
        metadata={"direction_status": {"forward": "completed", "reverse": "max_iterations"}},
    )
    assert (
        sorted(completed_directions(raw, {"forward": 1, "reverse": 2}, True))
        == expected["one_direction_maxiter"]
    )
    raw_fail = QCResult(success=False, metadata={"direction_status": {"forward": "completed"}})
    assert (
        sorted(completed_directions(raw_fail, {"forward": 1}, False)) == expected["backend_failure"]
    )


def test_irc_runs_match_goldens(tmp_path: Path) -> None:
    """Group ③: one-way completion + one-way failure equal the frozen goldens.

    Endpoint/direction semantics (iteration-limit endpoints rejected, the
    valid direction kept, failure error text) must be byte-compatible with
    the pre-migration record.
    """
    import numpy as np

    from cccp.backends.base import QCResult
    from cccp.utils import file_io

    golden = _load("irc.json")
    work = tmp_path / "irc_work"
    work.mkdir(parents=True, exist_ok=True)
    fwd = np.asarray(_IRC_COORDS, dtype=float) + 0.1
    file_io.write_xyz(work / "end_f.xyz", fwd, list(_IRC_SYMBOLS), title="IRC forward endpoint")

    one_way = QCResult(
        success=True,
        energy=-77.0,
        coordinates=np.asarray(_IRC_COORDS, dtype=float),
        symbols=list(_IRC_SYMBOLS),
        converged=True,
        metadata={
            "direction_status": {"forward": "completed", "reverse": "max_iterations"},
            "endpoints": {"forward": str(work / "end_f.xyz")},
        },
    )
    one_way_failed = QCResult(
        success=False,
        error_message="IRC forward direction failed to converge",
        metadata={"direction_status": {"forward": "failed"}},
    )

    complete = _run_irc_case(tmp_path, "one_way", one_way, ("forward", "reverse"))
    assert complete == golden["both_requested_reverse_maxiter"]

    forward_failed = _run_irc_case(tmp_path, "fwd_fail", one_way_failed, ("forward",))
    assert forward_failed == golden["forward_only_failure"]


# ── CASSCF / NEVPT2 goldens (todo 22) ────────────────────────────────────


def _round_floats(value: Any, ndigits: int = 10) -> Any:
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {key: _round_floats(entry, ndigits) for key, entry in value.items()}
    if isinstance(value, list):
        return [_round_floats(entry, ndigits) for entry in value]
    return value


class _CasscfStubBackend:
    """Golden CASSCF stub: answers one ``casscf`` capability call."""

    name = "orca"

    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[str] = []

    def casscf(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("casscf")
        return self.response


def test_casscf_input_generation_matches_goldens(tmp_path: Path) -> None:
    """Group ①: ORCAInterface.casscf input text equals the frozen record."""
    from unittest.mock import patch

    import numpy as np

    from cccp.qc.interfaces.orca import ORCAInterface
    from cccp.software import SoftwareNotFoundError

    golden = _load("casscf_nevpt2.json")
    interface = ORCAInterface(
        {"executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}}},
        method="B3LYP",
        basis="def2-TZVPP",
    )
    for label, case in golden["inputs"].items():
        case_dir = tmp_path / label
        case_dir.mkdir(parents=True, exist_ok=True)
        with patch.object(
            ORCAInterface, "_run_orca", side_effect=SoftwareNotFoundError("golden stub")
        ):
            interface.casscf(np.zeros((2, 3)), ["H", "H"], output_dir=case_dir, **case["kwargs"])
        text = (case_dir / "casscf.inp").read_text(encoding="utf-8")
        assert text == case["input_text"], label


def test_casscf_output_parse_matches_goldens(tmp_path: Path) -> None:
    """Group ②: the recorded ORCA CASSCF/NEVPT2 output parses to the record."""
    from cccp.qc.interfaces.orca import parse_casscf_output

    golden = _load("casscf_nevpt2.json")
    log = tmp_path / "casscf_recorded.out"
    log.write_text(golden["recorded_output"], encoding="utf-8")
    parsed = _round_floats(parse_casscf_output(log))
    assert parsed == golden["parsed_output"]


def test_casscf_spec_contract_matches_goldens() -> None:
    """Group ①: spec signature + validation errors equal the frozen record."""
    from acp.calculations.contracts import casscf_spec_from_dict, validate_casscf_spec

    golden = _load("casscf_nevpt2.json")
    expected = golden["spec_roundtrip"]
    spec = casscf_spec_from_dict(dict(expected["payload"]))
    assert spec.active_space_signature() == expected["signature"]
    assert validate_casscf_spec(spec, n_electrons=2) == expected["validation_errors"]
    invalid = casscf_spec_from_dict({"active_electrons": 6, "active_orbitals": 2})
    assert validate_casscf_spec(invalid, n_electrons=None) == golden["spec_invalid_errors"]


def test_casscf_payload_projection_matches_goldens(tmp_path: Path) -> None:
    """Group ③: the typed payload projects the golden parsed output exactly."""
    from cccp.backends.base import QCResult
    from cccp.calculation.context import TaskContext
    from cccp.calculation.contracts import casscf_spec_from_dict
    from cccp.calculation.requests import (
        CasscfOptions,
        MethodSpec,
        StructureInput,
        TaskKind,
        TaskRequest,
    )
    from cccp.calculation.tasks.casscf import run_casscf

    golden = _load("casscf_nevpt2.json")
    parsed = golden["parsed_output"]
    stub = _CasscfStubBackend(
        QCResult(
            success=True,
            energy=-108.987654321,
            symbols=["H", "H"],
            converged=True,
            metadata={"casscf": dict(parsed)},
        )
    )
    spec = casscf_spec_from_dict(dict(golden["spec_roundtrip"]["payload"]))
    request = TaskRequest(
        task=TaskKind.CASSCF,
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 1.4)), symbols=("H", "H")
        ),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="casscf"),
        backend="orca",
        options=CasscfOptions(spec=spec),
        output_dir=tmp_path,
    )
    result = run_casscf(request, context=TaskContext(backend=stub))
    assert result.status == "completed"
    assert stub.calls == ["casscf"]
    payload = result.payload
    assert payload is not None
    roots = parsed["nevpt2_roots"]
    assert list(payload.root_energies) == _round_floats(
        [entry["casscf_energy_hartree"] for entry in roots]
    )
    assert list(payload.nevpt2_energies) == _round_floats(
        [entry["correlated_energy_hartree"] for entry in roots]
    )
    assert list(payload.natural_occupations) == parsed["natural_occupations"]
    assert payload.active_space == spec.active_space_signature()


# ── Shermo standard state goldens (todo 22) ──────────────────────────────


def test_shermo_standard_state_semantics_match_goldens() -> None:
    """Group ①: token normalization + correction + Gibbs selection == record."""
    from cccp.qc.thermo_normalize import (
        normalize_standard_state,
        parse_shermo_result,
        select_gibbs,
        standard_state_correction_kcal,
    )

    golden = _load("shermo_standard_state.json")
    for raw, expected in golden["standard_state_token_normalization"].items():
        assert normalize_standard_state(raw) == expected, raw
    for key, expected in golden["correction_kcal_mol"].items():
        assert standard_state_correction_kcal(float(key.rstrip("K"))) == pytest.approx(
            expected, abs=1e-9
        )
    for case in golden["gibbs_selection"]:
        gibbs, source, delta = select_gibbs(
            case["g_sum"], case["g_conc"], 298.15, case["standard_state"]
        )
        if case["gibbs"] is None:
            assert gibbs is None
        else:
            assert gibbs == pytest.approx(case["gibbs"])
        assert source == case["gibbs_source"]
        assert (delta is None and case["standard_delta"] is None) or delta == pytest.approx(
            case["standard_delta"], abs=1e-9
        )
    parsed_keys = parse_shermo_result(
        {"u_sum": 1.0, "h_sum": 2.0, "g_sum": 3.0, "g_conc": 4.0, "s_total": 5.0}
    )
    assert sorted(parsed_keys) == golden["parse_shermo_result_keys"]


def test_shermo_units_payload_matches_goldens(tmp_path: Path) -> None:
    """Group ③: the typed payload carries Hartree/au values un-scaled."""
    from unittest.mock import patch

    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import (
        MethodSpec,
        TaskKind,
        TaskRequest,
        ThermochemistryOptions,
    )
    from cccp.calculation.tasks.thermochemistry import run_thermochemistry

    freq_log = tmp_path / "frequency.log"
    freq_log.write_text("frequency output", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_run_shermo(**kwargs: Any) -> dict[str, float]:
        captured.update(kwargs)
        Path(kwargs["output_file"]).write_text("Shermo summary", encoding="utf-8")
        return {"u_sum": -40.2, "h_sum": -40.25, "g_sum": -40.6, "s_total": 0.01}

    with patch.object(shermo_module, "run_shermo", fake_run_shermo):
        result = run_thermochemistry(
            TaskRequest(
                task=TaskKind.THERMOCHEMISTRY,
                level=MethodSpec(),
                options=ThermochemistryOptions(
                    freq_log_path=freq_log,
                    sp_energy_hartree=-40.5,
                    temperature_k=298.15,
                    pressure_atm=1.0,
                    standard_state="1M",
                ),
            ),
            context=TaskContext(),
        )
    assert result.status == "completed"
    payload = result.payload
    assert payload is not None
    assert payload.enthalpy_hartree == -40.25
    assert payload.entropy_au == 0.01
    assert payload.standard_state == "1M"
    golden = _load("shermo_standard_state.json")
    expected_case = next(
        case
        for case in golden["gibbs_selection"]
        if case["g_sum"] == -40.6 and case["g_conc"] is None and case["standard_state"] == "1M"
    )
    assert payload.gibbs_hartree == pytest.approx(expected_case["gibbs"], abs=1e-9)
    assert payload.gibbs_source == expected_case["gibbs_source"]
    assert captured["temperature_k"] == 298.15
