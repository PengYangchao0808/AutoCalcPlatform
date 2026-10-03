#!/usr/bin/env python3.11
"""Pre-migration goldens generator — Wave 0 / todo 2.

Imports the CURRENT (pre-migration) code and dumps normalized translation
artifacts to this directory so the A7 equivalence work (todo 39) can compare
post-migration behavior against a frozen record of today's semantics.

Coverage (plan §A7 / todo 2):

* ``orca_routes.json``              — ORCA route lines + parsed method/basis/
  dispersion/auxJ/auxC/solvent/grid/SCF + Hessian interval emission
* ``hessian_resolution.json``       — ``resolve_recalc_hess`` resolution matrix
* ``unit_strings.json``             — unit-bearing strings/keys across products
* ``error_tokens.json``             — failure-classification tokens, rescue
  strategy tokens, typed workflow error codes
* ``optimize_rescue.json``          — ``build_rescue_plan`` full matrix
* ``scan.json``                     — scan multi-coordinate plan + partial
  failure semantics (stubbed backend, no live QC)
* ``irc.json``                      — IRC one-way failure semantics (stubbed)
* ``casscf_nevpt2.json``            — CASSCF/NEVPT2 input text, parsed-output
  semantics, spec validation tokens
* ``shermo_standard_state.json``    — standard-state correction + Gibbs
  selection + metadata unit keys
* ``censo_records.json``            — CENSO record identity + free energies
* ``nmr_shielding.json``            — NMR atom-index/shielding parse
* ``workflow_requests.json``        — XtbPathSearch/OrcaGradient converted
  inputs + artifact digests

Determinism contract: sorted-key JSON, no timestamps, no machine paths
(temp roots are rewritten to ``<TMP>``), floats rounded to 10 decimals where
computed here.  Two generations at the same commit MUST be byte-identical.

Usage (from anywhere):

    python3.11 tests/baseline/cccp_calculation_goldens/generate_goldens.py
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

GOLDENS_DIR = Path(__file__).resolve().parent

ORCA_CONFIG: dict[str, Any] = {
    "executables": {"orca": {"path": "orca", "nproc": 4, "maxcore": 2000}},
    "resources": {"mem": "8GB", "nproc": 4},
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dump(name: str, payload: Any) -> Path:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path = GOLDENS_DIR / name
    path.write_text(text, encoding="utf-8")
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _r(value: Any) -> Any:
    """Round floats for cross-run stability; recurse through containers."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return round(value, 10)
    if isinstance(value, dict):
        return {str(k): _r(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_r(v) for v in value]
    return value


def _normalize(obj: Any, tmp_root: Path) -> Any:
    """Rewrite machine paths (temp roots) to ``<TMP>``; recurse everywhere."""
    marker = str(tmp_root)
    if isinstance(obj, str):
        return obj.replace(marker, "<TMP>")
    if isinstance(obj, Path):
        return str(obj).replace(marker, "<TMP>")
    if isinstance(obj, dict):
        return {str(k): _normalize(v, tmp_root) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v, tmp_root) for v in obj]
    return obj


def _git_commit() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# 1. ORCA route lines / parsed translation fields
# ---------------------------------------------------------------------------

_ORCA_CASES: list[dict[str, Any]] = [
    {
        "id": "b3lyp_opt_full_layer",
        "calc_type": "opt",
        "method": "B3LYP",
        "basis": "def2-TZVPP",
        "symbols": ["C", "O", "H", "H"],
        "recalc_hess": 5,
        "kwargs": {
            "dispersion": "D4",
            "aux_j_basis": "def2/J",
            "aux_c_basis": "def2-TZVPP/C",
            "solvent": "Water",
            "solvent_model": "SMD",
            "grid": "DefGrid2",
            "scf_options": {"maxiter": 200},
            "opt_level": "Tight",
        },
    },
    {
        "id": "dlpno_sp_default_aux",
        "calc_type": "sp",
        "method": "DLPNO-CCSD(T)",
        "basis": "def2-TZVPP",
        "symbols": ["C", "H"],
        "recalc_hess": None,
        "kwargs": {},
    },
    {
        "id": "r2scan3c_opt_composite_auto_hess",
        "calc_type": "opt",
        "method": "r2SCAN-3c",
        "basis": None,
        "symbols": ["C", "H"],
        "recalc_hess": "auto",
        "kwargs": {"dispersion": "D4", "route_extras": ["RIJCOSX"]},
    },
    {
        "id": "gfn2_alpb_solvent",
        "calc_type": "sp",
        "method": "GFN2-xTB",
        "basis": None,
        "symbols": ["C", "H"],
        "recalc_hess": None,
        "kwargs": {"solvent": "Toluene", "solvent_model": "ALPB"},
    },
    {
        "id": "wb97xd4_grid_and_dispersion",
        "calc_type": "opt",
        "method": "wB97X-D4",
        "basis": "def2-TZVPP",
        "symbols": ["Fe", "C"],
        "recalc_hess": "auto",
        "kwargs": {"grid": "DefGrid3", "dispersion": "D4", "trust_radius": 0.2},
    },
    {
        "id": "b3lyp_freq_smd_is_numfreq",
        "calc_type": "freq",
        "method": "B3LYP",
        "basis": "def2-TZVPP",
        "symbols": ["C", "H"],
        "recalc_hess": None,
        "kwargs": {"solvent": "Water", "solvent_model": "SMD"},
    },
    {
        "id": "pwpb95_double_hybrid_needs_aux_c",
        "calc_type": "sp",
        "method": "PWPB95",
        "basis": "def2-TZVPP",
        "symbols": ["C"],
        "recalc_hess": None,
        "kwargs": {"aux_j_basis": "def2/J", "aux_c_basis": "def2-TZVPP/C"},
    },
]


def _extract_fields(text: str) -> dict[str, Any]:
    """Deterministic extraction of the translated fields from a rendered input."""
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
    geom_block = _block("%geom")
    cpcm_block = _block("%cpcm")
    scf_block = _block("%scf")
    casscf_block = _block("%casscf")

    aux_j = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxJ")), None)
    aux_c = next((ln.split('"')[1] for ln in basis_block if ln.startswith("auxC")), None)
    inline_basis = next(
        (ln.split('"')[1] for ln in basis_block if ln.startswith("basis ")), None
    )
    recalc = next(
        (int(ln.split()[1]) for ln in geom_block if ln.startswith("Recalc_Hess")), None
    )
    return {
        "route_line": route_line,
        "route_tokens": route_line.lstrip("! ").split(),
        "basis_inline": inline_basis,
        "aux_j": aux_j,
        "aux_c": aux_c,
        "recalc_hess_interval": recalc,
        "solvent_block": cpcm_block,
        "scf_block": scf_block,
        "casscf_block": casscf_block,
    }


def build_orca_routes() -> dict[str, Any]:
    from cccp.qc.interfaces.orca import ORCAInterface

    cases: list[dict[str, Any]] = []
    for case in _ORCA_CASES:
        kwargs = dict(case["kwargs"])
        interface = ORCAInterface(
            dict(ORCA_CONFIG), method=case["method"], basis=case["basis"] or ""
        )
        text, resolution = interface._build_input_blocks(
            calc_type=case["calc_type"],
            symbols=list(case["symbols"]),
            recalc_hess=case["recalc_hess"],
            **kwargs,
        )
        cases.append(
            {
                "id": case["id"],
                "input_params": {
                    "calc_type": case["calc_type"],
                    "method": case["method"],
                    "basis": case["basis"],
                    "symbols": list(case["symbols"]),
                    "recalc_hess": case["recalc_hess"],
                    **kwargs,
                },
                "rendered_input": text,
                "parsed_fields": _extract_fields(text),
                "hessian_resolution": (
                    None
                    if resolution is None
                    else {
                        "interval": resolution.interval,
                        "source": resolution.source,
                        "reason": resolution.reason,
                        "enabled": resolution.enabled,
                        "heavy_elements": list(resolution.heavy_elements),
                        "triggering_elements": list(resolution.triggering_elements),
                    }
                ),
            }
        )
    return {
        "description": (
            "ORCA route lines + parsed method/basis/dispersion/auxJ/auxC/"
            "solvent/grid/SCF + Hessian interval emission (integration path)"
        ),
        "config_summary": {"nproc": 4, "maxcore": 2000},
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# 2. Hessian resolution matrix
# ---------------------------------------------------------------------------


def build_hessian_resolution() -> dict[str, Any]:
    from acp.chem.composition import (
        AUTO_RECALC_HESS,
        MAX_RECALC_HESS_INTERVAL,
        NON_LIGHT_DEFAULT_INTERVAL,
        resolve_recalc_hess,
    )

    matrix = [
        {"explicit": None, "configured": None, "symbols": ["C", "H"]},
        {"explicit": None, "configured": None, "symbols": ["Fe", "C"]},
        {"explicit": None, "configured": None, "symbols": ["S", "P"]},
        {"explicit": 0, "configured": 10, "symbols": ["Fe"]},
        {"explicit": 5, "configured": 0, "symbols": ["C"]},
        {"explicit": AUTO_RECALC_HESS, "configured": 7, "symbols": ["Fe"]},
        {"explicit": None, "configured": AUTO_RECALC_HESS, "symbols": ["C"]},
        {"explicit": 1, "configured": None, "symbols": None},
        {"explicit": MAX_RECALC_HESS_INTERVAL, "configured": None, "symbols": None},
    ]
    rows: list[dict[str, Any]] = []
    for case in matrix:
        resolution = resolve_recalc_hess(
            case["explicit"], case["configured"], case["symbols"]
        )
        rows.append(
            {
                **case,
                "result": {
                    "interval": resolution.interval,
                    "source": resolution.source,
                    "reason": resolution.reason,
                    "enabled": resolution.enabled,
                    "heavy_elements": list(resolution.heavy_elements),
                    "triggering_elements": list(resolution.triggering_elements),
                },
            }
        )
    rejected = []
    for value in (MAX_RECALC_HESS_INTERVAL + 1, -1, True, 1.5, "2.5", "abc"):
        try:
            resolve_recalc_hess(value, None, None)
        except ValueError as error:
            rejected.append({"value": repr(value), "error": str(error)})
        else:
            rejected.append({"value": repr(value), "error": None})
    return {
        "description": "resolve_recalc_hess resolution matrix + boundary rejects",
        "constants": {
            "AUTO_RECALC_HESS": AUTO_RECALC_HESS,
            "MAX_RECALC_HESS_INTERVAL": MAX_RECALC_HESS_INTERVAL,
            "NON_LIGHT_DEFAULT_INTERVAL": NON_LIGHT_DEFAULT_INTERVAL,
        },
        "matrix": rows,
        "boundary_rejections": rejected,
    }


# ---------------------------------------------------------------------------
# 3. Unit strings
# ---------------------------------------------------------------------------


def build_unit_strings() -> dict[str, Any]:
    from acp.calculations.primitives._thermochemistry_support import (
        ThermochemistryContext,
        ThermochemistryOutcome,
        ShermoSettings,
        build_metadata,
    )
    from acp.calculations.primitives._thermochemistry_input import (
        ValidatedRequest,
        standard_state_correction_kcal,
    )
    from acp.results.frequencies import build_normal_modes_product

    request = ValidatedRequest(
        freq_log_path=Path("freq.log"),
        sp_energy_hartree=-40.5,
        temperature=298.15,
        pressure=1.0,
        standard_state="1M",
    )
    context = ThermochemistryContext(
        config=None,
        output_dir=Path("out"),
        output_file=Path("out/Shermo.sum"),
        runner_options={},
        standard_state="1M",
    )
    settings = ShermoSettings(
        shermo_bin="Shermo", scl_zpe=1.0, ilowfreq=2, imagreal=0, concentration=1.0, qrrho=True
    )
    outcome = ThermochemistryOutcome(
        values={"u_sum": -40.4, "h_sum": -40.3, "g_sum": -40.6, "g_conc": -40.7, "s_total": 0.1},
        gibbs=-40.7,
        gibbs_source="g_conc",
        standard_delta=None,
    )
    metadata = build_metadata(request, context, settings, outcome, success=True)

    class _Calc:
        mode_vectors = {0: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))}
        mode_frequencies = {0: 123.4}
        mode_ir_intensities = {0: 5.5}

    modes_product = build_normal_modes_product(
        _Calc(), geometry_product_id="geometry", atom_count=2
    )
    return {
        "description": "unit-bearing strings and keys across translation products",
        "thermochemistry_metadata_unit_keys": {
            key: metadata[key]
            for key in sorted(metadata)
            if key.endswith(("_hartree", "_kcal_mol", "_au", "_k", "_atm"))
            or key in {"standard_state", "temperature", "pressure"}
        },
        "thermochemistry_all_keys": sorted(metadata),
        "standard_state_correction_kcal_at_298K": round(
            standard_state_correction_kcal(298.15), 10
        ),
        "normal_modes_units": modes_product["units"],
        "gradient_product_units": {
            "gradient_unit": "hartree/bohr",
            "energy_unit": "hartree",
            "gradient_convention": "energy_gradient_dE_dX",
            "gradient_conversion": "hartree_per_bohr / 0.529177210903",
        },
    }


# ---------------------------------------------------------------------------
# 4. Error tokens
# ---------------------------------------------------------------------------


def build_error_tokens(tmp_root: Path) -> dict[str, Any]:
    from cccp.qc.interfaces.orca import classify_orca_failure
    from acp.calculations.primitives.optimize import (
        FAILURE_EXIT,
        _FAILURE_TYPES,
        _RESCUE_DESCRIPTIONS,
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

    samples = {
        "scf_not_converged": "SCF NOT CONVERGED\n",
        "diis_failure": "DIIS convergence not achieved\n",
        "opt_not_converged": "THE OPTIMIZATION HAS NOT CONVERGED\n",
        "memory": "std::bad_alloc\n",
        "clean": "ORCA TERMINATED NORMALLY\n",
    }
    classification: dict[str, str] = {}
    for name, body in sorted(samples.items()):
        path = tmp_root / f"{name}.out"
        path.write_text(body, encoding="utf-8")
        classification[name] = classify_orca_failure(path)
    missing = classify_orca_failure(tmp_root / "does_not_exist.out")
    return {
        "description": "failure-classification tokens, rescue strategies, typed error codes",
        "classify_orca_failure_samples": classification,
        "classify_orca_failure_missing_file": missing,
        "failure_types": sorted(_FAILURE_TYPES),
        "failure_exit": sorted(FAILURE_EXIT),
        "rescue_strategies": _RESCUE_DESCRIPTIONS,
        "typed_error_codes": {
            "xtb_path": sorted(
                [
                    XTB_PATH_E_SCHEMA,
                    XTB_PATH_E_SOURCE,
                    XTB_PATH_E_CHARGE,
                    XTB_PATH_E_RECIPE,
                    XTB_PATH_E_XTB,
                    XTB_PATH_E_OUTPUT,
                ]
            ),
            "orca_gradient": sorted(
                [
                    ORCA_GRADIENT_E_SCHEMA,
                    ORCA_GRADIENT_E_GEOMETRY,
                    ORCA_GRADIENT_E_ELECTRONIC_STATE,
                    ORCA_GRADIENT_E_BACKEND,
                    ORCA_GRADIENT_E_GRADIENT,
                    ORCA_GRADIENT_E_OUTPUT,
                ]
            ),
        },
    }


# ---------------------------------------------------------------------------
# 5. Optimize rescue matrix
# ---------------------------------------------------------------------------


def build_optimize_rescue() -> dict[str, Any]:
    from acp.calculations.primitives.optimize import (
        _RESCUE_MATRIX,
        build_rescue_plan,
    )

    cells = []
    for failure_type, structure_kind in sorted(_RESCUE_MATRIX):
        for explicit_target in (None, 3):
            plan = build_rescue_plan(
                failure_type, structure_kind, explicit_ts_target=explicit_target
            )
            cells.append(
                {
                    "failure_type": failure_type,
                    "structure_kind": structure_kind,
                    "explicit_ts_target": explicit_target,
                    "terminal": plan.terminal,
                    "actions": [asdict(action) for action in plan.actions],
                }
            )
    return {
        "description": "build_rescue_plan matrix incl. explicit-TS-target filtering",
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# Stub backend for scan / IRC goldens (no live QC)
# ---------------------------------------------------------------------------


class _StubBackend:
    name = "orca"

    def __init__(self) -> None:
        self.scan_result: Any = None
        self.irc_result: Any = None
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def relaxed_scan(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("relaxed_scan")
        return self.scan_result

    def irc(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("irc")
        return self.irc_result


# ---------------------------------------------------------------------------
# 6. Scan multi-coordinate & partial failure
# ---------------------------------------------------------------------------


def build_scan_goldens(tmp_root: Path) -> dict[str, Any]:
    from acp.backends.base import QCResult
    from acp.calculations.contracts import CalculationRequest, StructureArtifact
    from acp.calculations.primitives.scan import (
        _build_scan_plan,
        _plan_metadata,
        run_scan,
    )
    from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult
    from cccp.utils import file_io

    work = tmp_root / "scan_work"
    work.mkdir(parents=True, exist_ok=True)
    xyz = work / "input.xyz"
    file_io.write_xyz(
        xyz,
        np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0], [0.0, 1.1, 0.0], [1.2, 1.1, 0.0]]),
        ["C", "C", "O", "H"],
        title="scan golden input",
    )

    multi_coords = ["0,1,1.2,2.4", "2,3,0.5,1.5"]
    plan_request = CalculationRequest(
        input_artifact=StructureArtifact(path=xyz, elements=["C", "C", "O", "H"]),
        method="B3LYP",
        resources={
            "backend": "orca",
            "output_dir": str(work),
            "result_dir": str(work / "RESULT"),
            "scan_coordinates": list(multi_coords),
            "scan_points": 4,
        },
        workflow="scan",
    )
    plan = _build_scan_plan(plan_request)
    plan_case = {
        "scan_coordinates": list(multi_coords),
        "scan_points": 4,
        "plan_metadata": _plan_metadata(plan),
    }

    def _run(case_id: str, result: Any) -> dict[str, Any]:
        stub = _StubBackend()
        stub.scan_result = result
        request = CalculationRequest(
            input_artifact=StructureArtifact(path=xyz, elements=["C", "C", "O", "H"]),
            method="B3LYP",
            resources={
                "backend": "orca",
                "output_dir": str(work / case_id),
                "result_dir": str(work / case_id / "RESULT"),
                "scan_coordinates": list(multi_coords),
                "scan_points": 4,
            },
            workflow="scan",
        )
        with patch("acp.backends.get_backend", lambda name: stub):
            calc = run_scan(request)
        return _r(
            _normalize(
                {
                    "status": calc.status,
                    "errors": list(calc.errors),
                    "energy": calc.energy,
                    "metadata": dict(calc.metadata),
                    "artifacts": [
                        {"type": a.type, "name": Path(a.path).name} for a in calc.artifacts
                    ],
                },
                tmp_root,
            )
        )

    def _point(index: int, ok: bool, energy: float | None) -> RelaxedScanPoint:
        coords = (
            np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0], [0.0, 1.1, 0.0], [1.2, 1.1, 0.0]])
            + index * 0.05
        )
        return RelaxedScanPoint(
            frame_index=index,
            progress=index / 3,
            coordinates=coords if ok else None,
            symbols=["C", "C", "O", "H"] if ok else None,
            energy_hartree=energy,
            success=ok,
            coordinate_values={"rc1": 1.2 + index * 0.1, "rc2": 0.5 + index * 0.1},
        )

    all_ok = RelaxedScanResult(
        points=[
            _point(0, True, -100.0),
            _point(1, True, -100.1),
            _point(2, True, -100.2),
            _point(3, True, -100.05),
        ],
        input_xyz=work / "input.xyz",
        scan_dir=work,
        success=True,
    )
    partial = RelaxedScanResult(
        points=[
            _point(0, True, -100.0),
            _point(1, False, None),
            _point(2, True, -100.2),
            _point(3, False, None),
        ],
        input_xyz=work / "input.xyz",
        scan_dir=work,
        success=False,
        message="relaxed scan aborted: 2 of 4 frames failed",
    )
    return {
        "description": "scan multi-coordinate plan + partial-failure result semantics",
        "multi_coordinate_plan": plan_case,
        "complete_run": _run("complete", all_ok),
        "partial_failure_run": _run("partial", partial),
    }


# ---------------------------------------------------------------------------
# 7. IRC one-way failure
# ---------------------------------------------------------------------------


def build_irc_goldens(tmp_root: Path) -> dict[str, Any]:
    from acp.backends.base import QCResult
    from acp.calculations.contracts import StructureArtifact, StructureRole
    from acp.calculations.primitives.irc import _completed_directions, _resolve_direction, run_irc
    from cccp.utils import file_io

    work = tmp_root / "irc_work"
    work.mkdir(parents=True, exist_ok=True)
    xyz = work / "ts.xyz"
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [-0.5, 0.9, 0.0],
            [-0.5, -0.9, 0.0],
            [2.0, 0.9, 0.0],
            [2.0, -0.9, 0.0],
        ]
    )
    symbols = ["C", "C", "H", "H", "H", "H"]
    file_io.write_xyz(xyz, coords, symbols, title="TS golden input")
    artifact = StructureArtifact(
        path=xyz, elements=symbols, role=StructureRole.TRANSITION_STATE, source="golden"
    )

    direction_map = {
        "both": _resolve_direction(("forward", "reverse")),
        "forward_only": _resolve_direction(("forward",)),
        "reverse_only": _resolve_direction(("reverse",)),
        "empty_falls_back": _resolve_direction(()),
    }

    # completed-direction semantics against recorded backend metadata
    raw = QCResult(
        success=True,
        metadata={
            "direction_status": {"forward": "completed", "reverse": "max_iterations"}
        },
    )
    one_way_completed = sorted(_completed_directions(raw, {"forward": 1, "reverse": 2}, True))
    raw_fail = QCResult(success=False, metadata={"direction_status": {"forward": "completed"}})
    one_way_on_failure = sorted(_completed_directions(raw_fail, {"forward": 1}, False))

    def _run(case_id: str, result: Any, directions: tuple[str, ...]) -> dict[str, Any]:
        stub = _StubBackend()
        stub.irc_result = result
        with patch("acp.backends.get_backend", lambda name: stub):
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
        return _r(
            _normalize(
                {
                    "status": calc.status,
                    "errors": list(calc.errors),
                    "metadata": dict(calc.metadata),
                    "artifacts": [
                        {"type": a.type, "name": Path(a.path).name} for a in calc.artifacts
                    ],
                },
                tmp_root,
            )
        )

    # one direction completed, the other hit the iteration limit
    fwd = np.asarray(coords) + 0.1
    file_io.write_xyz(work / "end_f.xyz", fwd, symbols, title="IRC forward endpoint")
    one_way = QCResult(
        success=True,
        energy=-77.0,
        coordinates=coords,
        symbols=symbols,
        converged=True,
        metadata={
            "direction_status": {"forward": "completed", "reverse": "max_iterations"},
            "endpoints": {"forward": str(work / "end_f.xyz")},
        },
    )
    # one-way request whose single direction fails
    one_way_failed = QCResult(
        success=False,
        error_message="IRC forward direction failed to converge",
        metadata={"direction_status": {"forward": "failed"}},
    )
    return {
        "description": "IRC direction resolution + one-way failure semantics",
        "direction_resolution": direction_map,
        "completed_direction_semantics": {
            "one_direction_maxiter": one_way_completed,
            "backend_failure": one_way_on_failure,
        },
        "both_requested_reverse_maxiter": _run(
            "one_way", one_way, ("forward", "reverse")
        ),
        "forward_only_failure": _run("fwd_fail", one_way_failed, ("forward",)),
    }


# ---------------------------------------------------------------------------
# 8. CASSCF / NEVPT2
# ---------------------------------------------------------------------------

_CASSCF_RECORDED_OUTPUT = """\
ORCA TERMINATED NORMALLY
ORBITAL OPTIMIZATION HAS CONVERGED
FINAL SINGLE POINT ENERGY       -108.9876543210

Natural Orbital Occupation Numbers
  N[   1] =    1.98123
  N[   2] =    1.95211
  N[   3] =    0.04789
  N[   4] =    0.01877

CAS-SCF RESULTS
  MULT 1, ROOT 0   -108.9812345678
  MULT 1, ROOT 1   -108.8123456789

NEVPT2 Results
  MULT 1, ROOT 0
  Zero Order Energy      : E0 =   -108.9812345678
  Total Energy Correction: dE =     -0.1234567890
  Total Energy (E0+dE)   : E  =   -109.1046913568

NEVPT2 Results
  MULT 1, ROOT 1
  Zero Order Energy      : E0 =   -108.8123456789
  Total Energy Correction: dE =     -0.1111111111
  Total Energy (E0+dE)   : E  =   -108.9234567900
"""


def build_casscf_nevpt2(tmp_root: Path) -> dict[str, Any]:
    from cccp.qc.interfaces.orca import ORCAInterface, parse_casscf_output
    from cccp.software import SoftwareNotFoundError
    from acp.calculations.contracts import casscf_spec_from_dict, validate_casscf_spec

    outdir = tmp_root / "casscf"
    outdir.mkdir(parents=True, exist_ok=True)
    interface = ORCAInterface(dict(ORCA_CONFIG), method="B3LYP", basis="def2-TZVPP")

    captured: dict[str, Any] = {}
    for label, kwargs in {
        "nevpt2_two_roots": {
            "active_electrons": 2,
            "active_orbitals": 2,
            "nroots": 2,
            "state_weights": (0.5, 0.5),
            "dynamic_correlation": "sc_nevpt2",
            "max_iterations": 50,
        },
        "fic_nevpt2_unfrozen": {
            "active_electrons": 4,
            "active_orbitals": 4,
            "dynamic_correlation": "fic_nevpt2",
            "frozen_core": False,
        },
    }.items():
        case_dir = outdir / label
        with patch.object(
            ORCAInterface, "_run_orca", side_effect=SoftwareNotFoundError("golden stub")
        ):
            interface.casscf(
                np.zeros((2, 3)),
                ["H", "H"],
                output_dir=case_dir,
                **kwargs,
            )
        captured[label] = {
            "kwargs": {
                key: (list(value) if isinstance(value, tuple) else value)
                for key, value in kwargs.items()
            },
            "input_text": (case_dir / "casscf.inp").read_text(encoding="utf-8"),
        }

    log = tmp_root / "casscf_recorded.out"
    log.write_text(_CASSCF_RECORDED_OUTPUT, encoding="utf-8")
    parsed = parse_casscf_output(log)

    spec_payload = {
        "active_electrons": 2,
        "active_orbitals": 2,
        "multiplicity": 1,
        "nroots": 2,
        "state_weights": [0.5, 0.5],
        "dynamic_correlation": "sc_nevpt2",
    }
    spec = casscf_spec_from_dict(dict(spec_payload))
    invalid = casscf_spec_from_dict({"active_electrons": 6, "active_orbitals": 2})
    return {
        "description": "CASSCF/NEVPT2 input generation, output parse, spec validation",
        "inputs": captured,
        "recorded_output": _CASSCF_RECORDED_OUTPUT,
        "parsed_output": _r(parsed),
        "spec_roundtrip": {
            "payload": spec_payload,
            "signature": spec.active_space_signature(),
            "validation_errors": validate_casscf_spec(spec, n_electrons=2),
        },
        "spec_invalid_errors": validate_casscf_spec(invalid, n_electrons=None),
    }


# ---------------------------------------------------------------------------
# 9. Shermo standard state
# ---------------------------------------------------------------------------


def build_shermo_standard_state() -> dict[str, Any]:
    from acp.calculations.primitives._thermochemistry_input import (
        standard_state_correction_kcal,
        _normalize_standard_state,
    )
    from acp.calculations.primitives._thermochemistry_support import (
        parse_shermo_result,
        select_gibbs,
    )

    tokens = {}
    for raw in ["1atm", "1M", "1mol/L", "solution", "1M ", "SOLUTION1M", "bogus"]:
        tokens[raw] = _normalize_standard_state(raw)

    selections = []
    for g_sum, g_conc, state in [
        (-40.6, -40.7, "1M"),
        (-40.6, None, "1M"),
        (-40.6, None, "1atm"),
        (None, -40.7, "1M"),
        (None, None, "1M"),
    ]:
        gibbs, source, delta = select_gibbs(g_sum, g_conc, 298.15, state)
        selections.append(
            {
                "g_sum": g_sum,
                "g_conc": g_conc,
                "standard_state": state,
                "gibbs": gibbs,
                "gibbs_source": source,
                "standard_delta": None if delta is None else round(delta, 10),
            }
        )
    return {
        "description": "Shermo standard-state correction + Gibbs selection semantics",
        "standard_state_token_normalization": tokens,
        "correction_kcal_mol": {
            "273.15K": round(standard_state_correction_kcal(273.15), 10),
            "298.15K": round(standard_state_correction_kcal(298.15), 10),
            "350.00K": round(standard_state_correction_kcal(350.0), 10),
        },
        "gibbs_selection": selections,
        "parse_shermo_result_keys": sorted(
            parse_shermo_result({"u_sum": 1.0, "h_sum": 2.0, "g_sum": 3.0, "g_conc": 4.0, "s_total": 5.0})
        ),
    }


# ---------------------------------------------------------------------------
# 10. CENSO record identity / free energy
# ---------------------------------------------------------------------------

_CENSO_XYZ = (
    "3\nCONF1\n"
    "C    0.000000    0.000000    0.000000\n"
    "H    0.000000    0.000000    1.089000\n"
    "H    1.026719    0.000000   -0.362999\n"
    "3\nCONF2\n"
    "C    0.000000    0.000000    0.000000\n"
    "H    0.000000    0.000000    1.089000\n"
    "H   -1.026719    0.000000   -0.362999\n"
)

_CENSO_JSON = {
    "part_name": "screening",
    "data": {
        "CONF1": {
            "energy": -154.912345,
            "gsolv": -0.004521,
            "grrho": 0.082341,
            "gtot": -154.834525,
        },
        "CONF2": {
            "energy": -154.911876,
            "gsolv": -0.004612,
            "grrho": 0.082455,
            "gtot": -154.834033,
        },
    },
}


def build_censo_records(tmp_root: Path) -> dict[str, Any]:
    from cccp.qc.interfaces.censo import CensoInterface, CensoRunResult

    json_path = tmp_root / "1_SCREENING.json"
    xyz_path = tmp_root / "1_SCREENING.xyz"
    json_path.write_text(json.dumps(_CENSO_JSON, indent=2), encoding="utf-8")
    xyz_path.write_text(_CENSO_XYZ, encoding="utf-8")

    interface = CensoInterface({})
    records = interface.parse_censo_json(json_path, xyz_path)
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
    run_result = CensoRunResult(preset="light", records=list(records), temperature=298.15)
    weights = run_result.boltzmann_weights()
    run_result.sort_by_gtot()
    return {
        "description": "CENSO record identity (conf_id/frame_index) + free energies",
        "inputs": {
            "json": _CENSO_JSON,
            "xyz": _CENSO_XYZ,
        },
        "records": identity,
        "boltzmann_weights": {k: round(v, 10) for k, v in sorted(weights.items())},
        "sort_by_gtot_order": [r.conf_id for r in run_result.records],
    }


# ---------------------------------------------------------------------------
# 11. NMR atom index / shielding
# ---------------------------------------------------------------------------

_NMR_TENSOR_LOG = """\
                       NMR SHIELDING TENSOR (PPM)
  Nucleus   1C:     isotropic=   140.5000   anisotropy=    10.0000
     XX =   135.1000   XY =     1.2000   XZ =     0.3000
     YY =   142.2000   YZ =    -0.4000   ZZ =   144.2000

  Nucleus   2H:     isotropic=    30.1000   anisotropy=     1.5000
     XX =    29.8000   XY =     0.1000   XZ =     0.0000
     YY =    30.2000   YZ =     0.0000   ZZ =    30.3000
"""

_NMR_SUMMARY_LOG = """\
CHEMICAL SHIELDING SUMMARY (ppm)
 Nucleus   Element   Isotropic(ppm)
    0         6 C       140.5000
    1         1 H        30.1000
"""


def build_nmr_shielding(tmp_root: Path) -> dict[str, Any]:
    from cccp.qc.interfaces.orca import NmrShieldingParser

    tensor_log = tmp_root / "nmr_tensor.out"
    tensor_log.write_text(_NMR_TENSOR_LOG, encoding="utf-8")
    summary_log = tmp_root / "nmr_summary.out"
    summary_log.write_text(_NMR_SUMMARY_LOG, encoding="utf-8")

    tensor = NmrShieldingParser.parse(tensor_log, expected_symbols=["C", "H"])
    summary = NmrShieldingParser.parse(summary_log, expected_symbols=["C", "H"])
    mismatch = None
    try:
        NmrShieldingParser.parse(tensor_log, expected_symbols=["H", "C"])
    except ValueError as error:
        mismatch = str(error)
    return {
        "description": "NMR shielding parse — 0-based atom index + shielding values",
        "inputs": {"tensor_log": _NMR_TENSOR_LOG, "summary_log": _NMR_SUMMARY_LOG},
        "tensor_block_parse": _r(tensor),
        "summary_block_parse": _r(summary),
        "symbol_mismatch_error": mismatch,
    }


# ---------------------------------------------------------------------------
# 12. XtbPathSearch / OrcaGradient converted inputs & artifact digests
# ---------------------------------------------------------------------------

_START_XYZ = (
    "3\nH3 start\n"
    "H 0.000000 0.000000 0.000000\n"
    "H 1.000000 0.000000 0.000000\n"
    "H 0.000000 1.000000 0.000000\n"
)
_END_XYZ = (
    "3\nH3 end\n"
    "H 0.000000 0.000000 0.000000\n"
    "H 1.500000 0.000000 0.000000\n"
    "H 0.000000 1.000000 0.000000\n"
)
_PATH_INP = (
    "$path\n"
    "   nrun=1\n"
    "   npoint=50\n"
    "   anopt=10\n"
    "   kpush=0.003\n"
    "   kpull=-0.015\n"
    "   ppull=0.05\n"
    "   alp=0.5\n"
    "$end\n"
)

_XTB_PATH_PAYLOAD = {
    "schema_version": "pes2ts_xtb_path_request_v1",
    "reaction_id": "RXN_GOLDEN_0001",
    "source": {
        "source_type": "xyz_text_pair",
        "start_xyz": _START_XYZ,
        "end_xyz": _END_XYZ,
        "charge": 0,
        "multiplicity": 1,
    },
    "recipe": {
        "path_inp_text": _PATH_INP,
        "gfn_level": 2,
        "uhf": 0,
        "threads": 4,
        "timeout_seconds": 1800,
        "seed": None,
        "extra_args": [],
    },
    "provenance": {
        "plan_sha256": None,
        "config_digest": "cfgdigest_golden",
        "request_sha256": "reqsha_golden",
        "adapter_version": "pes2ts_xtb_path_request_v1",
    },
}

_GRADIENT_PAYLOAD = {
    "schema_version": "pes2ts_orca_gradient_request_v1",
    "xyz": (
        "2\nOrcaGradient golden geometry\n"
        "H 0.0000000000 0.0000000000 0.0000000000\n"
        "H 0.7400000000 0.0000000000 0.0000000000\n"
    ),
    "method": "B3LYP",
    "basis": "def2-SVP",
    "charge": 0,
    "multiplicity": 1,
    "route_extras": ["TightSCF"],
    "timeout_seconds": 600,
    "nproc": 2,
    "extra_blocks": [],
    "scf_convergence": "TightSCF",
    "output_name": "grad",
}


def build_workflow_requests(tmp_root: Path) -> dict[str, Any]:
    from acp.workflows.orca_gradient import (
        GRADIENT_PRODUCT_SCHEMA,
        ENERGY_PRODUCT_SCHEMA,
        _persist_orca_gradient_outputs,
        _validate_gradient_request,
    )
    from acp.workflows.xtb_path import _validate_path_request

    path_request = _validate_path_request(_XTB_PATH_PAYLOAD)
    grad_request = _validate_gradient_request(_GRADIENT_PAYLOAD)

    # XtbPathSearch converted inputs: written texts + their digests
    written: dict[str, str] = {}
    for name, text in {
        "start.xyz": path_request.start_xyz_text,
        "end.xyz": path_request.end_xyz_text,
        "path.inp": path_request.path_inp_text,
    }.items():
        written[name] = _sha256_text(text)

    # OrcaGradient artifact digests from the real persist step
    grad_out = tmp_root / "gradient_out"
    gradient = np.array([[0.1, -0.2, 0.3], [-0.1, 0.2, -0.3]])
    gradient_path, manifest_path = _persist_orca_gradient_outputs(
        output_root=grad_out,
        request=grad_request,
        energy=-1.23456789,
        gradient=gradient,
        gradient_source="golden_recorded",
        provenance={"request_sha256": grad_request.request_sha256},
    )
    result_dir = grad_out / "RESULT"
    digests = {}
    for rel in (
        "geometry/geometry.xyz",
        "gradient/gradient.json",
        "energy/energy.json",
        "result_manifest.json",
        "result_summary.json",
    ):
        path = result_dir / rel
        if path.is_file():
            digests[rel] = _sha256_file(path)

    return {
        "description": "XtbPathSearch/OrcaGradient converted inputs + artifact digests",
        "xtb_path": {
            "payload": _XTB_PATH_PAYLOAD,
            "converted_request": {
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
            },
            "converted_input_text_sha256": written,
        },
        "orca_gradient": {
            "payload": _GRADIENT_PAYLOAD,
            "converted_request": {
                "schema_version": grad_request.schema_version,
                "method": grad_request.method,
                "basis": grad_request.basis,
                "charge": grad_request.charge,
                "multiplicity": grad_request.multiplicity,
                "route_extras": list(grad_request.route_extras),
                "timeout_seconds": grad_request.timeout_seconds,
                "nproc": grad_request.nproc,
                "extra_blocks": list(grad_request.extra_blocks),
                "scf_convergence": grad_request.scf_convergence,
                "output_name": grad_request.output_name,
                "request_sha256": grad_request.request_sha256,
                "xyz_text": grad_request.xyz_text,
                "xyz_text_sha256": _sha256_text(grad_request.xyz_text),
            },
            "artifact_digests": digests,
            "product_schemas": {
                "gradient": GRADIENT_PRODUCT_SCHEMA,
                "energy": ENERGY_PRODUCT_SCHEMA,
            },
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    source_commit = _git_commit()
    generated: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="acp_goldens_") as tmp:
        tmp_root = Path(tmp)
        generated["orca_routes.json"] = build_orca_routes()
        generated["hessian_resolution.json"] = build_hessian_resolution()
        generated["unit_strings.json"] = build_unit_strings()
        generated["error_tokens.json"] = build_error_tokens(tmp_root)
        generated["optimize_rescue.json"] = build_optimize_rescue()
        generated["scan.json"] = build_scan_goldens(tmp_root)
        generated["irc.json"] = build_irc_goldens(tmp_root)
        generated["casscf_nevpt2.json"] = build_casscf_nevpt2(tmp_root)
        generated["shermo_standard_state.json"] = build_shermo_standard_state()
        generated["censo_records.json"] = build_censo_records(tmp_root)
        generated["nmr_shielding.json"] = build_nmr_shielding(tmp_root)
        generated["workflow_requests.json"] = build_workflow_requests(tmp_root)

        written_paths: dict[str, Path] = {}
        for name, payload in sorted(generated.items()):
            written_paths[name] = _dump(name, _normalize(_r(payload), tmp_root))

    manifest = {
        "schema": "cccp_calculation_goldens_manifest_v1",
        "source_commit": source_commit,
        "generator": "tests/baseline/cccp_calculation_goldens/generate_goldens.py",
        "generator_sha256": _sha256_file(Path(__file__).resolve()),
        "inputs": {
            "orca_routes.json": "ORCAInterface._build_input_blocks case matrix (see cases[].input_params)",
            "hessian_resolution.json": "resolve_recalc_hess explicit/configured/symbols matrix",
            "unit_strings.json": "Thermochemistry metadata + normal-modes product + gradient product units",
            "error_tokens.json": "classify_orca_failure sample outputs + token tables",
            "optimize_rescue.json": "build_rescue_plan (failure_type x structure_kind x explicit_ts_target)",
            "scan.json": "scan_coordinates 4-point two-coordinate plans + stubbed RelaxedScanResult",
            "irc.json": "TS artifact + stubbed direction_status metadata",
            "casscf_nevpt2.json": "CASSCF input kwargs + recorded ORCA CASSCF/NEVPT2 output text",
            "shermo_standard_state.json": "pure standard-state / Gibbs-selection functions",
            "censo_records.json": "inline CENSO 1_SCREENING.json + .xyz fixture",
            "nmr_shielding.json": "inline ORCA NMR tensor-block and summary-block logs",
            "workflow_requests.json": "pes2ts_xtb_path_request_v1 + pes2ts_orca_gradient_request_v1 payloads",
        },
        "config_summary": {
            "orca_interface": {"nproc": 4, "maxcore": 2000, "mem": "8GB"},
            "shermo": {"temperature_K": 298.15, "pressure_atm": 1.0, "standard_state": "1M"},
            "float_rounding_decimals": 10,
            "machine_paths": "temp roots normalized to <TMP>",
        },
        "files": {
            name: {"sha256": _sha256_file(path), "bytes": path.stat().st_size}
            for name, path in sorted(written_paths.items())
        },
    }
    _dump("manifest.json", manifest)
    print(f"goldens written: {len(written_paths)} artifacts + manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
