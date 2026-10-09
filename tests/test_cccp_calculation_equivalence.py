"""A7 three-way equivalence — legacy golden ↔ migrated ACP ↔ standalone cccp.

Plan todo 39 (architecture remediation, acceptance A7).  The frozen
pre-migration goldens in ``tests/baseline/cccp_calculation_goldens/`` are the
record of the *historical integration path* (ACP in place).  Every capability
branch is compared across three surfaces:

* **①** frozen golden → migrated ACP compatibility surface (platform compat);
* **②** frozen golden → standalone cccp core (scientific semantics preserved);
* **③** migrated ACP ↔ standalone cccp (adapter adds no defaults / loses no
  fields — asserted both by object identity for pure re-export shims and by
  field-level conversion checks for the PES2TS adapters).

The goldens are never regenerated post-migration (a backfilled golden would
carry a post-migration commit and defeat the comparison); ``manifest.json``
pins ``source_commit`` + per-file sha256, and every intentional P0 deviation
is authorization-tracked in ``expected_behavior_delta.md``.  Earlier todos
(17–22) accumulated the per-task cases; todo 39 adds the remaining Wave-0
branches (Hessian resolution, thermochemistry units, failure tokens, CENSO
record identity, NMR atom index/shielding, XtbPathSearch/OrcaGradient request
conversion) plus the coverage/delta integrity guards.
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
    inline_basis = next((ln.split('"')[1] for ln in basis_block if ln.startswith("basis ")), None)
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
        _FAILURE_TYPES,
        _RESCUE_DESCRIPTIONS,
        _RESCUE_MATRIX,
        FAILURE_EXIT,
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


# ═══════════════════════════════════════════════════════════════════════
# A7 three-way matrix — remaining Wave-0 branches + integrity guards (todo 39)
# ═══════════════════════════════════════════════════════════════════════
#
# Group legend: ① golden → migrated ACP · ② golden → standalone cccp ·
# ③ ACP ↔ cccp (adapter adds no defaults / loses no fields).

#: Golden file → the A7 test functions pinning that branch (three-way).
_GOLDEN_BRANCH_TESTS: dict[str, tuple[str, ...]] = {
    "orca_routes.json": (
        "test_singlepoint_effective_params_match_goldens",
        "test_frequency_effective_params_match_goldens",
    ),
    "hessian_resolution.json": ("test_hessian_resolution_three_way_matches_goldens",),
    "unit_strings.json": ("test_thermochemistry_unit_strings_three_way_match_goldens",),
    "error_tokens.json": (
        "test_failure_classification_tokens_match_goldens",
        "test_rescue_and_typed_error_tokens_match_goldens",
    ),
    "optimize_rescue.json": ("test_optimize_rescue_metadata_matches_goldens",),
    "scan.json": (
        "test_scan_plan_metadata_matches_goldens",
        "test_scan_runs_match_goldens",
    ),
    "irc.json": (
        "test_irc_direction_resolution_matches_goldens",
        "test_irc_completed_direction_semantics_match_goldens",
        "test_irc_runs_match_goldens",
    ),
    "casscf_nevpt2.json": (
        "test_casscf_input_generation_matches_goldens",
        "test_casscf_output_parse_matches_goldens",
        "test_casscf_spec_contract_matches_goldens",
        "test_casscf_payload_projection_matches_goldens",
    ),
    "shermo_standard_state.json": (
        "test_shermo_standard_state_semantics_match_goldens",
        "test_shermo_units_payload_matches_goldens",
    ),
    "censo_records.json": (
        "test_censo_record_identity_three_way_matches_goldens",
        "test_censo_failure_paths_keep_error_semantics",
    ),
    "nmr_shielding.json": (
        "test_nmr_shielding_three_way_matches_goldens",
        "test_nmr_failure_path_missing_requested_atom_is_parse_failure",
    ),
    "workflow_requests.json": ("test_workflow_request_conversion_three_way_matches_goldens",),
}


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolution_projection(resolution: Any) -> dict[str, Any]:
    return {
        "interval": resolution.interval,
        "source": resolution.source,
        "reason": resolution.reason,
        "enabled": resolution.enabled,
        "heavy_elements": list(resolution.heavy_elements),
        "triggering_elements": list(resolution.triggering_elements),
    }


def test_goldens_manifest_pins_source_commit_and_hashes() -> None:
    """Goldens are non-empty, pre-migration, and hash-pinned by the manifest."""
    import hashlib

    manifest = _load("manifest.json")
    assert manifest["schema"] == "cccp_calculation_goldens_manifest_v1"
    assert manifest["source_commit"] == GOLDENS_SOURCE_COMMIT
    assert manifest["generator"]
    assert manifest["generator_sha256"]
    files = manifest["files"]
    assert set(files) == set(_GOLDEN_BRANCH_TESTS)
    for name, entry in sorted(files.items()):
        path = GOLDENS_DIR / name
        assert path.is_file(), f"golden file missing: {name}"
        raw = path.read_bytes()
        assert raw, f"golden must be non-empty: {name}"
        assert len(raw) == entry["bytes"], f"golden byte size drift: {name}"
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], f"golden hash drift: {name}"


def test_all_golden_branches_have_declared_a7_coverage() -> None:
    """Every golden branch maps to ≥1 performed three-way test in this module."""
    for golden_name, test_names in _GOLDEN_BRANCH_TESTS.items():
        assert (GOLDENS_DIR / golden_name).is_file(), golden_name
        for test_name in test_names:
            assert callable(globals().get(test_name)), f"{golden_name} -> {test_name}"


def test_expected_behavior_delta_tracks_every_golden_and_delta_row() -> None:
    """No golden is silently compared without a delta-table classification."""
    text = (GOLDENS_DIR / "expected_behavior_delta.md").read_text(encoding="utf-8")
    for name in _load("manifest.json")["files"]:
        assert name in text, f"{name} not classified in expected_behavior_delta.md"
    for delta_id in (f"D{index}" for index in range(1, 9)):
        assert f"| {delta_id} |" in text, f"approved delta row {delta_id} missing"


# ── hessian resolution (three-way) ──────────────────────────────────────


def test_hessian_resolution_three_way_matches_goldens() -> None:
    from acp.chem.composition import (
        AUTO_RECALC_HESS as ACP_AUTO,
    )
    from acp.chem.composition import (
        MAX_RECALC_HESS_INTERVAL as ACP_MAX,
    )
    from acp.chem.composition import (
        NON_LIGHT_DEFAULT_INTERVAL as ACP_NON_LIGHT,
    )
    from acp.chem.composition import (
        resolve_recalc_hess as acp_resolve,
    )
    from cccp.qc.hessian_policy import (
        AUTO_RECALC_HESS,
        MAX_RECALC_HESS_INTERVAL,
        NON_LIGHT_DEFAULT_INTERVAL,
    )
    from cccp.qc.hessian_policy import resolve_recalc_hess as cccp_resolve

    golden = _load("hessian_resolution.json")

    # ③ ACP compat module is a pure re-export — no duplicated defaults.
    assert acp_resolve is cccp_resolve
    assert (ACP_AUTO, ACP_MAX, ACP_NON_LIGHT) == (
        AUTO_RECALC_HESS,
        MAX_RECALC_HESS_INTERVAL,
        NON_LIGHT_DEFAULT_INTERVAL,
    )
    assert golden["constants"] == {
        "AUTO_RECALC_HESS": AUTO_RECALC_HESS,
        "MAX_RECALC_HESS_INTERVAL": MAX_RECALC_HESS_INTERVAL,
        "NON_LIGHT_DEFAULT_INTERVAL": NON_LIGHT_DEFAULT_INTERVAL,
    }

    for case in golden["matrix"]:
        # ② standalone cccp == golden
        cccp_resolution = cccp_resolve(case["explicit"], case["configured"], case["symbols"])
        assert _resolution_projection(cccp_resolution) == case["result"], case
        # ① migrated ACP == golden and ③ ACP == cccp
        acp_resolution = acp_resolve(case["explicit"], case["configured"], case["symbols"])
        assert _resolution_projection(acp_resolution) == case["result"], case

    boundary_values = [MAX_RECALC_HESS_INTERVAL + 1, -1, True, 1.5, "2.5", "abc"]
    assert [repr(value) for value in boundary_values] == [
        case["value"] for case in golden["boundary_rejections"]
    ]
    for value, case in zip(boundary_values, golden["boundary_rejections"]):
        for resolve in (cccp_resolve, acp_resolve):
            with pytest.raises(ValueError) as excinfo:
                resolve(value, None, None)
            assert str(excinfo.value) == case["error"], case["value"]


# ── thermochemistry unit strings (three-way) ────────────────────────────


def test_thermochemistry_unit_strings_three_way_match_goldens() -> None:
    from acp.calculations.primitives._thermochemistry_input import (
        ValidatedRequest as AcpValidatedRequest,
    )
    from acp.calculations.primitives._thermochemistry_input import (
        standard_state_correction_kcal as acp_standard_state_correction,
    )
    from acp.calculations.primitives._thermochemistry_support import (
        ShermoSettings,
    )
    from acp.calculations.primitives._thermochemistry_support import (
        build_metadata as acp_build_metadata,
    )
    from acp.results.frequencies import build_normal_modes_product
    from cccp.qc.thermo_normalize import (
        ThermochemistryContext,
        ThermochemistryOutcome,
        ValidatedRequest,
    )
    from cccp.qc.thermo_normalize import build_metadata as cccp_build_metadata
    from cccp.qc.thermo_normalize import (
        standard_state_correction_kcal as cccp_standard_state_correction,
    )

    golden = _load("unit_strings.json")

    # ③ the ACP private surface re-exports the single normalization core.
    assert acp_build_metadata is cccp_build_metadata

    context = ThermochemistryContext(
        config=None,
        output_dir=Path("out"),
        output_file=Path("out/Shermo.sum"),
        runner_options={},
        standard_state="1M",
    )
    settings = ShermoSettings(
        shermo_bin="Shermo",
        scl_zpe=1.0,
        ilowfreq=2,
        imagreal=0,
        concentration=1.0,
        qrrho=True,
    )
    outcome = ThermochemistryOutcome(
        values={"u_sum": -40.4, "h_sum": -40.3, "g_sum": -40.6, "g_conc": -40.7, "s_total": 0.1},
        gibbs=-40.7,
        gibbs_source="g_conc",
        standard_delta=None,
    )

    def _request(cls: type) -> Any:
        return cls(
            freq_log_path=Path("freq.log"),
            sp_energy_hartree=-40.5,
            temperature=298.15,
            pressure=1.0,
            standard_state="1M",
        )

    cccp_metadata = _round_floats(
        cccp_build_metadata(_request(ValidatedRequest), context, settings, outcome, success=True)
    )
    acp_metadata = _round_floats(
        acp_build_metadata(_request(AcpValidatedRequest), context, settings, outcome, success=True)
    )
    # ① migrated ACP == ② standalone cccp == ③ (same implementation)
    assert acp_metadata == cccp_metadata
    unit_suffixes = ("_hartree", "_kcal_mol", "_au", "_k", "_atm")
    for metadata in (cccp_metadata, acp_metadata):
        subset = {
            key: metadata[key]
            for key in sorted(metadata)
            if key.endswith(unit_suffixes) or key in {"standard_state", "temperature", "pressure"}
        }
        assert subset == golden["thermochemistry_metadata_unit_keys"]
        assert sorted(metadata) == golden["thermochemistry_all_keys"]

    expected_correction = golden["standard_state_correction_kcal_at_298K"]
    assert round(cccp_standard_state_correction(298.15), 10) == expected_correction
    assert round(acp_standard_state_correction(298.15), 10) == expected_correction

    class _Calc:
        mode_vectors = {0: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))}
        mode_frequencies = {0: 123.4}
        mode_ir_intensities = {0: 5.5}

    modes_product = build_normal_modes_product(
        _Calc(), geometry_product_id="geometry", atom_count=2
    )
    assert modes_product["units"] == golden["normal_modes_units"]


# ── failure classification tokens / rescue matrix (three-way) ───────────


def test_failure_classification_tokens_match_goldens(tmp_path: Path) -> None:
    """② SCF / timeout / parse failure classification tokens are contract."""
    from cccp.qc.interfaces.orca import classify_orca_failure

    golden = _load("error_tokens.json")
    samples = {
        "scf_not_converged": "SCF NOT CONVERGED\n",
        "diis_failure": "DIIS convergence not achieved\n",
        "opt_not_converged": "THE OPTIMIZATION HAS NOT CONVERGED\n",
        "memory": "std::bad_alloc\n",
        "clean": "ORCA TERMINATED NORMALLY\n",
    }
    for name, body in samples.items():
        log = tmp_path / f"{name}.out"
        log.write_text(body, encoding="utf-8")
        classified = classify_orca_failure(log)
        assert classified == golden["classify_orca_failure_samples"][name], name
    missing = classify_orca_failure(tmp_path / "does_not_exist.out")
    assert missing == golden["classify_orca_failure_missing_file"]


def test_rescue_and_typed_error_tokens_match_goldens() -> None:
    """①/③ rescue tables and typed workflow error codes equal the goldens."""
    from acp.calculations.primitives.optimize import (
        _FAILURE_TYPES,
        _RESCUE_DESCRIPTIONS,
        FAILURE_EXIT,
    )
    from acp.workflows.orca_gradient import (
        ORCA_GRADIENT_E_BACKEND,
        ORCA_GRADIENT_E_ELECTRONIC_STATE,
        ORCA_GRADIENT_E_GEOMETRY,
        ORCA_GRADIENT_E_GRADIENT,
        ORCA_GRADIENT_E_OUTPUT,
        ORCA_GRADIENT_E_SCHEMA,
    )
    from acp.workflows.xtb_path import (
        XTB_PATH_E_CHARGE,
        XTB_PATH_E_OUTPUT,
        XTB_PATH_E_RECIPE,
        XTB_PATH_E_SCHEMA,
        XTB_PATH_E_SOURCE,
        XTB_PATH_E_XTB,
    )
    from cccp.calculation.tasks.optimize import _FAILURE_TYPES as CCCP_FAILURE_TYPES
    from cccp.calculation.tasks.optimize import _RESCUE_DESCRIPTIONS as CCCP_RESCUE_DESCRIPTIONS
    from cccp.calculation.tasks.optimize import FAILURE_EXIT as CCCP_FAILURE_EXIT

    golden = _load("error_tokens.json")
    assert sorted(_FAILURE_TYPES) == golden["failure_types"]
    assert sorted(FAILURE_EXIT) == golden["failure_exit"]
    assert _RESCUE_DESCRIPTIONS == golden["rescue_strategies"]
    # ③ ACP compat re-export == cccp task core (no second rescue table).
    assert sorted(CCCP_FAILURE_TYPES) == sorted(_FAILURE_TYPES)
    assert sorted(CCCP_FAILURE_EXIT) == sorted(FAILURE_EXIT)
    assert CCCP_RESCUE_DESCRIPTIONS == _RESCUE_DESCRIPTIONS

    xtb_codes = sorted(
        [
            XTB_PATH_E_SCHEMA,
            XTB_PATH_E_SOURCE,
            XTB_PATH_E_CHARGE,
            XTB_PATH_E_RECIPE,
            XTB_PATH_E_XTB,
            XTB_PATH_E_OUTPUT,
        ]
    )
    gradient_codes = sorted(
        [
            ORCA_GRADIENT_E_SCHEMA,
            ORCA_GRADIENT_E_GEOMETRY,
            ORCA_GRADIENT_E_ELECTRONIC_STATE,
            ORCA_GRADIENT_E_BACKEND,
            ORCA_GRADIENT_E_GRADIENT,
            ORCA_GRADIENT_E_OUTPUT,
        ]
    )
    assert xtb_codes == golden["typed_error_codes"]["xtb_path"]
    assert gradient_codes == golden["typed_error_codes"]["orca_gradient"]


# ── CENSO record identity / free energy (three-way) ─────────────────────


class _CensoStubBackend:
    name = "censo"

    def __init__(self, result: Any) -> None:
        self.result = result

    def refine_ensemble(self, ensemble_xyz: Path, output_dir: Path, **kwargs: Any) -> Any:
        return self.result


def _censo_run_result(tmp_path: Path, golden: dict[str, Any]) -> Any:
    from cccp.qc.interfaces.censo import CensoInterface, CensoRunResult

    json_path = tmp_path / "1_SCREENING.json"
    xyz_path = tmp_path / "1_SCREENING.xyz"
    json_path.write_text(json.dumps(golden["inputs"]["json"]), encoding="utf-8")
    xyz_path.write_text(golden["inputs"]["xyz"], encoding="utf-8")
    records = CensoInterface({}).parse_censo_json(json_path, xyz_path)
    result = CensoRunResult(
        preset="screening",
        records=records,
        final_part="screening",
        work_dir=tmp_path,
        temperature=298.15,
    )
    result.sort_by_gtot()
    return result


def test_censo_record_identity_three_way_matches_goldens(tmp_path: Path) -> None:
    from acp.backends.censo_backend import CensoInterface as AcpCensoInterface
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import (
        CensoRefineOptions,
        StructureInput,
        TaskKind,
        TaskRequest,
    )
    from cccp.calculation.tasks.censo_refine import run_censo_refine
    from cccp.qc.interfaces.censo import CensoInterface, CensoRunResult, part_index

    golden = _load("censo_records.json")

    # ③ ACP backend is a pure re-export of the single CENSO subprocess layer.
    assert AcpCensoInterface is CensoInterface

    # ② standalone cccp low-level parse == golden (record identity + free energy)
    records = _censo_run_result(tmp_path, golden).records
    identity = [
        {
            "conf_id": record.conf_id,
            "frame_index": record.frame_index,
            "energy": record.energy,
            "gsolv": record.gsolv,
            "grrho": record.grrho,
            "gtot": record.gtot,
            "n_atoms": len(record.symbols),
            "symbols": list(record.symbols),
        }
        for record in records
    ]
    assert identity == golden["records"]

    weights = CensoRunResult(preset="light", records=records, temperature=298.15)
    assert {
        key: round(value, 10) for key, value in sorted(weights.boltzmann_weights().items())
    } == golden["boltzmann_weights"]
    weights.sort_by_gtot()
    assert [record.conf_id for record in weights.records] == golden["sort_by_gtot_order"]

    # ① migrated ACP path executes the task core → same identity/free energy.
    (tmp_path / f"{part_index('screening')}_SCREENING.xyz").write_text(
        "refined ensemble", encoding="utf-8"
    )
    backend = _CensoStubBackend(_censo_run_result(tmp_path, golden))
    request = TaskRequest(
        task=TaskKind.CENSO_REFINE,
        structure=StructureInput(path=tmp_path / "ensemble.xyz"),
        options=CensoRefineOptions(preset="censo-light", temperature_k=298.15),
        output_dir=tmp_path / "out",
    )
    result = run_censo_refine(request, context=TaskContext(backend=backend, input_base=tmp_path))
    assert result.status == "completed"
    assert result.complete is True
    expected = {row["conf_id"]: row for row in golden["records"]}
    assert len(result.payload.records) == len(expected)
    for record in result.payload.records:
        row = expected[record.conf_id]
        assert record.frame_index == row["frame_index"]
        assert record.energy_hartree == pytest.approx(row["energy"])
        assert record.free_energy_hartree == pytest.approx(row["gtot"])
        assert record.weight == pytest.approx(golden["boltzmann_weights"][record.conf_id])


def test_censo_failure_paths_keep_error_semantics(tmp_path: Path) -> None:
    """Failure classification is stable (text may differ; ``error_kind`` is contract)."""
    import numpy as np

    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import (
        CensoRefineOptions,
        StructureInput,
        TaskKind,
        TaskRequest,
    )
    from cccp.calculation.results import ErrorKind
    from cccp.calculation.tasks.censo_refine import run_censo_refine
    from cccp.qc.interfaces.censo import CensoConformerRecord, CensoRunResult, part_index

    golden = _load("censo_records.json")
    (tmp_path / f"{part_index('screening')}_SCREENING.xyz").write_text(
        "refined ensemble", encoding="utf-8"
    )

    def _request() -> TaskRequest:
        return TaskRequest(
            task=TaskKind.CENSO_REFINE,
            structure=StructureInput(path=tmp_path / "ensemble.xyz"),
            options=CensoRefineOptions(),
            output_dir=tmp_path / "out",
        )

    empty = CensoRunResult(
        preset="screening", records=[], final_part="screening", work_dir=tmp_path
    )
    empty_result = run_censo_refine(
        _request(), context=TaskContext(backend=_CensoStubBackend(empty), input_base=tmp_path)
    )
    assert empty_result.status == "failed"
    assert empty_result.complete is False
    assert empty_result.error_kind is ErrorKind.BACKEND_FAILURE

    run_result = _censo_run_result(tmp_path, golden)
    run_result.records.append(
        CensoConformerRecord(
            conf_id="CONF_UNMAPPED",
            frame_index=-1,
            energy=0.0,
            gsolv=0.0,
            grrho=0.0,
            gtot=0.0,
            coordinates=np.zeros((0, 3)),
            symbols=[],
        )
    )
    partial = run_censo_refine(
        _request(),
        context=TaskContext(backend=_CensoStubBackend(run_result), input_base=tmp_path),
    )
    assert partial.status == "failed"
    assert partial.complete is False
    assert [record.conf_id for record in partial.payload.records] == ["CONF1", "CONF2"]
    assert [record.frame_index for record in partial.payload.records] == [0, 1]


# ── NMR atom index / shielding (three-way) ──────────────────────────────


class _NmrStubBackend:
    name = "orca"

    def __init__(self, result: Any) -> None:
        self.result = result

    def nmr_shielding(
        self,
        coordinates: Any,
        symbols: Any,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        return self.result


def _nmr_task_result(
    tmp_path: Path,
    golden: dict[str, Any],
    *,
    options: Any | None = None,
) -> Any:
    from cccp.backends.base import QCResult
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import (
        MethodSpec,
        NmrShieldingOptions,
        StructureInput,
        TaskKind,
        TaskRequest,
    )
    from cccp.calculation.tasks.nmr_shielding import run_nmr_shielding
    from cccp.qc.interfaces.orca import NmrShieldingParser

    geometry = ((0.0, 0.0, 0.0), (0.0, 0.0, 1.089))
    symbols = ("C", "H")
    log = tmp_path / "nmr_tensor.out"
    log.write_text(golden["inputs"]["tensor_log"], encoding="utf-8")
    parsed = NmrShieldingParser.parse(log, expected_symbols=list(symbols))
    backend = _NmrStubBackend(
        QCResult(
            success=True,
            energy=-40.5,
            coordinates=geometry,
            symbols=symbols,
            metadata={"shieldings": parsed},
        )
    )
    request = TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=StructureInput(coordinates=geometry, symbols=symbols),
        level=MethodSpec(),
        options=options if options is not None else NmrShieldingOptions(),
    )
    return run_nmr_shielding(request, context=TaskContext(backend=backend, input_base=tmp_path))


def test_nmr_shielding_three_way_matches_goldens(tmp_path: Path) -> None:
    from cccp.qc.interfaces.orca import NmrShieldingParser

    golden = _load("nmr_shielding.json")

    # ② standalone cccp parser == golden (0-based atom index + shielding).
    tensor_log = tmp_path / "nmr_tensor.out"
    summary_log = tmp_path / "nmr_summary.out"
    tensor_log.write_text(golden["inputs"]["tensor_log"], encoding="utf-8")
    summary_log.write_text(golden["inputs"]["summary_log"], encoding="utf-8")
    tensor = NmrShieldingParser.parse(tensor_log, expected_symbols=["C", "H"])
    summary = NmrShieldingParser.parse(summary_log, expected_symbols=["C", "H"])
    assert {str(key): value for key, value in tensor.items()} == golden["tensor_block_parse"]
    assert {str(key): value for key, value in summary.items()} == golden["summary_block_parse"]

    # Pre-launch rejection: symbol/order mismatch is a ValueError with the
    # recorded classification text (not silent reordering).
    with pytest.raises(ValueError) as excinfo:
        NmrShieldingParser.parse(tensor_log, expected_symbols=["H", "C"])
    assert str(excinfo.value) == golden["symbol_mismatch_error"]

    # ①/③ migrated task core payload == golden at the original atom-index base.
    result = _nmr_task_result(tmp_path, golden)
    assert result.status == "completed"
    expected = golden["tensor_block_parse"]
    assert set(result.payload.shieldings) == {int(key) for key in expected}
    for key, entry in result.payload.shieldings.items():
        assert entry.symbol == expected[str(key)]["symbol"]
        assert entry.isotropic == pytest.approx(expected[str(key)]["isotropic"])


def test_nmr_failure_path_missing_requested_atom_is_parse_failure(tmp_path: Path) -> None:
    from cccp.calculation.requests import NmrShieldingOptions
    from cccp.calculation.results import ErrorKind

    golden = _load("nmr_shielding.json")
    options = NmrShieldingOptions(atom_indices=(0, 5))
    result = _nmr_task_result(tmp_path, golden, options=options)
    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.PARSE_FAILURE
    assert set(result.payload.shieldings) == {0}
    assert result.payload.shieldings[0].symbol == "C"


# ── XtbPathSearch / OrcaGradient request conversion (three-way) ─────────


def test_workflow_request_conversion_three_way_matches_goldens(tmp_path: Path) -> None:
    import numpy as np

    from acp.calculations.legacy_adapters import (
        pes2ts_orca_gradient_to_task_request,
        pes2ts_xtb_path_to_task_request,
    )
    from acp.workflows.orca_gradient import (
        ENERGY_PRODUCT_SCHEMA,
        GRADIENT_PRODUCT_SCHEMA,
        _persist_orca_gradient_outputs,
        _validate_gradient_request,
    )
    from acp.workflows.xtb_path import _validate_path_request

    golden = _load("workflow_requests.json")

    # ---- XtbPathSearch --------------------------------------------------
    path_request = _validate_path_request(golden["xtb_path"]["payload"])
    expected_path = golden["xtb_path"]["converted_request"]
    converted_path = {
        "reaction_id": path_request.reaction_id,
        "charge": path_request.charge,
        "multiplicity": path_request.multiplicity,
        "gfn_level": path_request.gfn_level,
        "uhf": path_request.uhf,
        "threads": path_request.threads,
        "timeout_seconds": path_request.timeout_seconds,
        "seed": path_request.seed,
        "extra_args": list(path_request.extra_args),
        "request_sha256": path_request.request_sha256,
        "config_digest": path_request.config_digest,
        "adapter_version": path_request.adapter_version,
        "plan_sha256": path_request.plan_sha256,
    }
    # ① migrated ACP validation == golden (no recipe knobs defaulted).
    assert converted_path == expected_path
    written = {
        "start.xyz": _sha256_text(path_request.start_xyz_text),
        "end.xyz": _sha256_text(path_request.end_xyz_text),
        "path.inp": _sha256_text(path_request.path_inp_text),
    }
    assert written == golden["xtb_path"]["converted_input_text_sha256"]

    # ③ adapter keeps scientific fields + platform identity; adds no defaults.
    path_task_request, path_binding = pes2ts_xtb_path_to_task_request(path_request)
    assert path_task_request.charge == expected_path["charge"]
    assert path_task_request.multiplicity == expected_path["multiplicity"]
    assert path_task_request.resources.nproc == expected_path["threads"]
    assert path_task_request.resources.timeout_s == expected_path["timeout_seconds"]
    path_options = path_task_request.options
    assert path_options.gfn_level == expected_path["gfn_level"]
    assert path_options.uhf == expected_path["uhf"]
    assert path_options.seed == expected_path["seed"]
    fragments = {fragment.source: fragment.content for fragment in path_options.backend_inputs}
    assert (
        fragments["recipe.path_inp_text"]
        == golden["xtb_path"]["payload"]["recipe"]["path_inp_text"]
    )
    assert fragments["recipe.extra_args"] == tuple(
        golden["xtb_path"]["payload"]["recipe"]["extra_args"]
    )
    assert path_binding.platform_identity["request_sha256"] == expected_path["request_sha256"]

    # ---- OrcaGradient ---------------------------------------------------
    gradient_request = _validate_gradient_request(golden["orca_gradient"]["payload"])
    expected_gradient = golden["orca_gradient"]["converted_request"]
    converted_gradient = {
        "schema_version": gradient_request.schema_version,
        "method": gradient_request.method,
        "basis": gradient_request.basis,
        "charge": gradient_request.charge,
        "multiplicity": gradient_request.multiplicity,
        "route_extras": list(gradient_request.route_extras),
        "timeout_seconds": gradient_request.timeout_seconds,
        "nproc": gradient_request.nproc,
        "extra_blocks": list(gradient_request.extra_blocks),
        "scf_convergence": gradient_request.scf_convergence,
        "output_name": gradient_request.output_name,
        "request_sha256": gradient_request.request_sha256,
        "xyz_text": gradient_request.xyz_text,
        "xyz_text_sha256": _sha256_text(gradient_request.xyz_text),
    }
    assert converted_gradient == expected_gradient
    assert {
        "gradient": GRADIENT_PRODUCT_SCHEMA,
        "energy": ENERGY_PRODUCT_SCHEMA,
    } == golden["orca_gradient"]["product_schemas"]

    output_root = tmp_path / "gradient_out"
    gradient = np.array([[0.1, -0.2, 0.3], [-0.1, 0.2, -0.3]])
    _persist_orca_gradient_outputs(
        output_root=output_root,
        request=gradient_request,
        energy=-1.23456789,
        gradient=gradient,
        gradient_source="golden_recorded",
        provenance={"request_sha256": gradient_request.request_sha256},
    )
    result_dir = output_root / "RESULT"
    import hashlib

    digests = {}
    for relative in (
        "geometry/geometry.xyz",
        "gradient/gradient.json",
        "energy/energy.json",
        "result_manifest.json",
        "result_summary.json",
    ):
        candidate = result_dir / relative
        if candidate.is_file():
            digests[relative] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert digests == golden["orca_gradient"]["artifact_digests"]

    gradient_product = json.loads((result_dir / "gradient" / "gradient.json").read_text("utf-8"))
    energy_product = json.loads((result_dir / "energy" / "energy.json").read_text("utf-8"))
    assert gradient_product["gradient_unit"] == "hartree/bohr"
    assert gradient_product["gradient_convention"] == "energy_gradient_dE_dX"
    assert gradient_product["gradient_conversion"] == "hartree_per_bohr / 0.529177210903"
    assert energy_product["energy_unit"] == "hartree"

    # ③ adapter keeps method/basis/scf/resources and passes raw fragments via.
    gradient_task_request, gradient_binding = pes2ts_orca_gradient_to_task_request(gradient_request)
    assert gradient_task_request.level.method == expected_gradient["method"]
    assert gradient_task_request.level.basis == expected_gradient["basis"]
    assert gradient_task_request.charge == expected_gradient["charge"]
    assert gradient_task_request.multiplicity == expected_gradient["multiplicity"]
    assert gradient_task_request.resources.nproc == expected_gradient["nproc"]
    assert gradient_task_request.resources.timeout_s == expected_gradient["timeout_seconds"]
    gradient_fragments = {
        fragment.source: fragment.content
        for fragment in gradient_task_request.options.backend_inputs
    }
    assert gradient_fragments["route_extras"] == tuple(expected_gradient["route_extras"])
    assert gradient_fragments["extra_blocks"] == tuple(expected_gradient["extra_blocks"])
    assert gradient_fragments["output_name"] == expected_gradient["output_name"]
    assert (
        gradient_binding.platform_identity["request_sha256"] == expected_gradient["request_sha256"]
    )
