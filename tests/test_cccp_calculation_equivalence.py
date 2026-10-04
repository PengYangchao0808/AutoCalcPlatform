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
    with patch("acp.backends.get_backend", lambda name: stub):
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
