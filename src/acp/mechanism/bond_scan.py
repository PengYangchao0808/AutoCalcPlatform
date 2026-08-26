"""S2 bond-length-scan orchestrator (docs/ACP_S2_Bond_Length_Scan_MD_Plan.md).

Implements the plan's S2 pipeline (§8.1):

    prepare → materialize_input → validate_protocol → compile_scan_input →
    run_relaxed_scan → extract_scan_frames → run_single_points →
    build_energy_profile → recommend_candidates → finalize_manifest

The scan is driven by ORCA ``%geom Scan B i j = start, end, N`` with
``FullScan true``; the per-point geometry optimisation runs at
GFN2-xTB (default) and a B97-3c single point is recomputed per frame.
TS/INT initial guesses reuse the existing :mod:`path_profile` /
:mod:`path_selector` primitives (§9.2) — no parallel algorithm.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np

from acp.confsearch.shared.artifacts import write_json_atomic
from acp.core.state import WorkflowState
from cccp.qc.interfaces.constraints import CoordinateSpec
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.utils.constants import HARTREE_TO_KCAL
from cccp.utils.file_io import read_xyz, write_xyz
from cccp.utils.geometry_tools import GeometryUtils

from ._helpers import fingerprint
from .scan_manifest import (
    S2_MANIFEST_NAME,
    build_manifest_payload,
    write_scan_manifest,
)
from .scan_models import (
    BondLengthScanRequest,
    CandidateRecommendation,
    EnergyProfile,
    ScanCoordinate,
    ScanFrame,
    ScanProtocol,
    ScanQuality,
    StructureSource,
    coordinate_step_angstrom,
    validate_scan_protocol,
)

logger = logging.getLogger(__name__)

SCAN_DIR_NAME = "s2_bond_scan_001"
BOND_SCAN_STAGES = (
    "prepare",
    "materialize_input",
    "validate_protocol",
    "compile_scan_input",
    "run_relaxed_scan",
    "extract_scan_frames",
    "run_single_points",
    "build_energy_profile",
    "recommend_candidates",
    "finalize_manifest",
)


class _ScanStageState:
    """Tiny stage reporter — no-op when the workflow state is unavailable."""

    def __init__(self, state: WorkflowState | None) -> None:
        self._state = state

    def run(self, name: str) -> None:
        if self._state is not None:
            self._state.set_stage(name)

    def done(self, name: str, result: dict[str, Any] | None = None) -> None:
        if self._state is not None:
            self._state.complete_stage(name, result)

    def fail(self, name: str, error: str) -> None:
        if self._state is not None:
            self._state.fail_stage(name, error)


def run_bond_length_scan(
    *,
    request: dict[str, Any],
    output_dir: Path | str,
    config: dict[str, Any] | None = None,
    source_job_id: str | None = None,
    workflow_state: WorkflowState | None = None,
) -> dict[str, Any]:
    """Run a one-dimensional bond-length relaxed scan (S2 mode).

    Args:
        request: BondLengthScanRequest payload (§7.1) with ``source``,
            ``coordinate`` and ``protocol`` blocks.
        output_dir: Task output root (scheduler job dir or CLI ``--output``).
        config: Merged ACP config dict (executables/resources).
        source_job_id: Fallback source-job id used when
            ``request.source.source_job_id`` is empty.
        workflow_state: Optional :class:`WorkflowState` for stage reporting;
            created at the output root when absent.

    Returns:
        The s2_path_v2 manifest payload (also persisted to
        ``RESULT/mechanism/s2_path_manifest.json``).

    Raises:
        ValueError: On invalid requests, sources or protocol values.
        RuntimeError: On QC execution failure (scan run / SP failures).
    """
    req = BondLengthScanRequest.from_dict(request)
    if req.mode not in ("bond_length_scan", "coordinate_scan"):
        raise ValueError(
            f"request.mode must be 'bond_length_scan' or 'coordinate_scan', got {req.mode!r}"
        )
    if req.source.source_job_id is None and source_job_id:
        # CLI/API fallback: the job id may be carried at the call boundary.
        req = BondLengthScanRequest(
            mode=req.mode,
            source=replace(req.source, source_job_id=source_job_id),
            coordinate=req.coordinate,
            protocol=req.protocol,
            study_id=req.study_id,
            resources=req.resources,
        )

    cfg = config or {}
    out_root = Path(output_dir).resolve()
    work_root = out_root / "WORK"
    result_dir = out_root / "RESULT" / "mechanism"
    result_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = work_root / "02_SEARCH" / SCAN_DIR_NAME
    scan_dir.mkdir(parents=True, exist_ok=True)

    if workflow_state is None:
        workflow_state = WorkflowState(work_dir=out_root, job_name="PESsearch")
        workflow_state.load()
        if not workflow_state.state:
            workflow_state.initialize(
                "bond_length_scan",
                stage_names=list(BOND_SCAN_STAGES),
            )
    stages = _ScanStageState(workflow_state)

    try:
        payload = _run_pipeline(
            req, cfg, out_root, work_root, result_dir, scan_dir, stages, workflow_state
        )
    except Exception as exc:
        stages.fail(str(workflow_state.state.get("current_stage") or "prepare"), str(exc))
        raise
    workflow_state.mark_completed()
    return payload


def _run_pipeline(
    req: BondLengthScanRequest,
    cfg: dict[str, Any],
    out_root: Path,
    work_root: Path,
    result_dir: Path,
    scan_dir: Path,
    stages: _ScanStageState,
    workflow_state: WorkflowState,
) -> dict[str, Any]:
    stages.run("materialize_input")
    coords, symbols, charge, multiplicity, source_info = _materialize_structure(
        req.source,
        work_root,
        out_root,
        cfg,
    )
    for index in req.coordinate.atoms:
        if index < 0 or index >= len(symbols):
            raise ValueError(
                f"Scan atom index {index} is out of range (structure has {len(symbols)} atoms)"
            )
    input_xyz = scan_dir / "input.xyz"
    write_xyz(
        input_xyz,
        coords,
        symbols,
        title=f"{source_info.get('formula') or 'molecule'} charge={charge} mult={multiplicity}",
    )
    stages.done(
        "materialize_input",
        {
            "input_xyz": str(input_xyz),
            "atom_count": len(symbols),
            "source": dict(source_info),
        },
    )

    stages.run("validate_protocol")
    validate_scan_protocol(req.coordinate, req.protocol)
    coordinate = req.coordinate
    protocol = req.protocol
    stages.done(
        "validate_protocol",
        {
            "coordinate": coordinate.to_dict(),
            "step_angstrom": _step_angstrom(coordinate),
            "protocol_name": protocol.name,
        },
    )

    stages.run("compile_scan_input")
    interface = ORCAInterface(cfg, method=protocol.scan_optimizer.method)
    reusable_inp_text = _completed_scan_inp_text(scan_dir)
    scan_inp = _compile_scan_input(
        interface,
        coords,
        symbols,
        charge,
        multiplicity,
        coordinate,
        protocol,
        scan_dir,
    )
    reuse_scan = (
        reusable_inp_text is not None and scan_inp.read_text(encoding="utf-8") == reusable_inp_text
    )
    protocol_path = write_json_atomic(
        scan_dir / "scan_protocol.json",
        {
            "schema": "scan_protocol_v1",
            "request": req.to_dict(),
            "compiled_input": str(scan_inp.relative_to(out_root)),
        },
    )
    stages.done(
        "compile_scan_input",
        {"scan_inp": str(scan_inp), "scan_protocol": str(protocol_path)},
    )

    stages.run("run_relaxed_scan")
    if reuse_scan:
        logger.info("Reusing completed ORCA relaxed scan in %s (scan.inp unchanged)", scan_dir)
        scan_result = interface.parse_relaxed_scan_output(
            scan_dir / "scan.out",
            scan_coordinate=_scan_coordinate_spec(coordinate),
            points=coordinate.n_points,
            output_dir=scan_dir,
            output_name="scan",
        )
    else:
        scan_result = _run_relaxed_scan(
            interface,
            coords,
            symbols,
            charge,
            multiplicity,
            coordinate,
            protocol,
            scan_dir,
            cfg,
        )
    if not scan_result.success and not scan_result.points:
        raise RuntimeError(f"ORCA relaxed scan failed: {scan_result.message}")
    stages.done(
        "run_relaxed_scan",
        {
            "success": bool(scan_result.success),
            "n_frames": len(scan_result.points),
            "message": scan_result.message or "",
            "reused_existing_scan": bool(reuse_scan),
        },
    )

    stages.run("extract_scan_frames")
    frames = _extract_frames(scan_result, coordinate, scan_dir)
    stages.done(
        "extract_scan_frames",
        {
            "n_frames": len(frames),
            "frames_dir": str(scan_dir / "scan_frames"),
        },
    )

    stages.run("run_single_points")
    sp_spec = protocol.single_point
    _run_single_points(interface, frames, charge, multiplicity, sp_spec, scan_dir)
    stages.done("run_single_points", {"enabled": bool(sp_spec.enabled)})

    stages.run("build_energy_profile")
    profile = _build_energy_profile(frames, sp_spec)
    profile_path = write_json_atomic(scan_dir / "profile.json", profile.to_dict())
    stages.done(
        "build_energy_profile",
        {
            "energy_source": profile.energy_source,
            "profile": str(profile_path),
            "sp_incomplete": profile.sp_incomplete,
        },
    )

    stages.run("recommend_candidates")
    ts_recs, int_recs, quality = _recommend_candidates(
        frames,
        coordinate,
        scan_dir,
        profile,
        cfg,
    )
    stages.done(
        "recommend_candidates",
        {
            "ts": [rec.candidate_id for rec in ts_recs],
            "intermediates": [rec.candidate_id for rec in int_recs],
            "needs_review": quality.needs_review,
        },
    )

    stages.run("finalize_manifest")
    scan_dir_rel = str(scan_dir.relative_to(out_root))
    payload = build_manifest_payload(
        request=req.to_dict(),
        coordinate=coordinate,
        protocol=protocol,
        charge=charge,
        multiplicity=multiplicity,
        frames=frames,
        profile=profile,
        quality=quality,
        ts_recommendations=ts_recs,
        int_recommendations=int_recs,
        source=source_info,
        provenance={
            "engine": "acp-pessearch-bond-length-scan",
            "mode": "bond_length_scan",
            "fingerprint": fingerprint(
                {
                    "mode": "bond_length_scan",
                    "coordinate": coordinate.to_dict(),
                    "protocol": protocol.to_dict(),
                    "source": source_info.get("artifact_ref") or source_info.get("source_type"),
                    "charge": charge,
                    "multiplicity": multiplicity,
                }
            ),
        },
        scan_dir_rel=scan_dir_rel,
    )
    manifest_out = write_scan_manifest(payload, result_dir / S2_MANIFEST_NAME)
    stages.done("finalize_manifest", {"manifest": str(manifest_out)})
    return payload


# ── structure materialisation ──────────────────────────────────────────


def _materialize_structure(
    source: StructureSource,
    work_root: Path,
    out_root: Path,
    cfg: dict[str, Any],
) -> tuple[np.ndarray[Any, Any], list[str], int, int, dict[str, Any]]:
    """Resolve the requested structure source to coords/symbols/charge/mult."""
    source_type = source.source_type
    if source_type == "task_artifact":
        xyz_path, provenance = _materialize_task_artifact(source, out_root)
    elif source_type == "structure_asset":
        xyz_path, provenance = _materialize_asset(source)
    elif source_type == "xyz_text":
        xyz_path, provenance = _materialize_xyz_text(source, work_root)
    else:  # pragma: no cover — guarded by StructureSource.from_dict
        raise ValueError(f"Unknown source_type: {source_type!r}")

    coords, symbols = read_xyz(xyz_path)
    coords_arr = np.asarray(coords, dtype=float)
    if coords_arr.ndim != 2 or coords_arr.shape[1] != 3:
        raise ValueError(f"Invalid geometry from {xyz_path}: not an (N,3) coordinate block")
    if not np.all(np.isfinite(coords_arr)):
        raise ValueError(f"Non-finite coordinates in structure source: {xyz_path}")
    if len(symbols) < 2:
        raise ValueError("Structures with fewer than 2 atoms cannot be scanned")

    charge = source.charge
    multiplicity = source.multiplicity
    if charge == 0 and multiplicity == 1:
        comment_charge, comment_mult = _charge_mult_from_xyz(xyz_path)
        if comment_charge is not None and comment_mult is not None:
            charge, multiplicity = comment_charge, comment_mult

    provenance = dict(provenance)
    provenance.update(
        {
            "source_type": source_type,
            "atom_count": int(len(symbols)),
            "formula": _hill_formula([str(s) for s in symbols]),
            "charge": charge,
            "multiplicity": multiplicity,
            "normalized_xyz": str(work_root / "02_SEARCH" / SCAN_DIR_NAME / "input.xyz"),
        }
    )
    return coords_arr, [str(s) for s in symbols], charge, multiplicity, provenance


def _materialize_task_artifact(
    source: StructureSource, out_root: Path
) -> tuple[Path, dict[str, Any]]:
    """Resolve a ``task_artifact`` source to an XYZ path.

    Supports either a Confsearch manifest (structure picked by
    ``structure_selector``) or a direct XYZ file.  ``artifact_path`` may be
    absolute (already pinned by the API) or relative to the source job dir.
    """
    from acp.confsearch.manifest import read_manifest, representative_conformer
    from acp.mechanism.stages.handoff import resolve_source_job_work_dir

    relative = str(source.artifact_path or "").strip()
    if not relative and source.source_job_id:
        relative = "RESULT/confsearch/confsearch_manifest.json"
    if not relative:
        raise ValueError("task_artifact source requires source.artifact_path")

    artifact = Path(relative)
    if not artifact.is_file():
        if not source.source_job_id:
            raise ValueError(f"artifact_path is not a file: {relative}")
        source_dir = resolve_source_job_work_dir(source.source_job_id, root=out_root)
        artifact = source_dir / relative
    if not artifact.is_file():
        raise ValueError(f"Source artifact not found: {artifact}")

    if artifact.suffix.lower() == ".xyz":
        return artifact, {"artifact_path": str(artifact), "artifact_kind": "xyz"}

    # Confsearch manifest (or another JSON manifest exposing conformers).
    payload = read_manifest(artifact)
    selector = source.structure_selector
    conf_id: str | None = None
    frame_index = selector.frame_index if selector.kind == "frame_index" else None
    if frame_index is not None:
        conformers = payload.get("conformers") or []
        if frame_index < 0 or frame_index >= len(conformers):
            raise ValueError(
                f"frame_index {frame_index} out of range "
                f"(manifest has {len(conformers)} conformers)"
            )
        conf_id = str(conformers[frame_index].get("conf_id") or "")
        _conf_id, geometry = representative_conformer(artifact, conf_id)
    else:
        _conf_id, geometry = representative_conformer(artifact)
    return geometry, {
        "artifact_path": str(artifact),
        "artifact_kind": "confsearch_manifest",
        "conf_id": conf_id,
        "selector": selector.to_dict(),
    }


def _materialize_asset(source: StructureSource) -> tuple[Path, dict[str, Any]]:
    if not source.asset_path:
        raise ValueError(
            "structure_asset source requires a resolved asset_path (asset_id must be "
            "resolved by the API layer)"
        )
    asset = Path(source.asset_path)
    if not asset.is_file():
        raise ValueError(f"Structure asset file not found: {asset}")
    return asset, {"asset_id": source.asset_id, "asset_path": str(asset)}


def _materialize_xyz_text(source: StructureSource, work_root: Path) -> tuple[Path, dict[str, Any]]:
    from acp.intake.parsers import parse_xyz_text

    text = str(source.xyz_text or "").strip()
    if not text:
        raise ValueError("xyz_text source requires non-empty source.xyz_text")
    parsed = parse_xyz_text(text)
    if not parsed.structures and parsed.errors:
        raise ValueError(f"Could not parse pasted XYZ: {'; '.join(parsed.errors[:3])}")
    if not parsed.structures:
        raise ValueError("Pasted XYZ contains no structures")
    asset = parsed.structures[0]
    inputs_dir = work_root / "01_PREPARE" / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = inputs_dir / "pasted.xyz"
    xyz_path.write_text(str(asset.xyz or text), encoding="utf-8")
    return xyz_path, {"source_type": "xyz_text", "atom_count": int(asset.atom_count or 0)}


def _charge_mult_from_xyz(xyz_path: Path) -> tuple[int | None, int | None]:
    """Best-effort charge/multiplicity read from an XYZ comment line."""
    try:
        lines = xyz_path.read_text(encoding="utf-8").splitlines()
        if len(lines) >= 2:
            tokens = lines[1].split()
            for token in tokens:
                if "=" in token:
                    key, _, value = token.partition("=")
                    if key.strip().lower() in ("charge", "mult", "multiplicity"):
                        number = int(float(value))
                        if key.strip().lower() == "charge":
                            return number, None
                        return None, number
        if len(lines) >= 2 and len(lines[1].split()) >= 2:
            parts = lines[1].split()
            charge = int(float(parts[0]))
            mult = int(float(parts[1]))
            if -10 <= charge <= 10 and 1 <= mult <= 20:
                return charge, mult
    except (OSError, ValueError):
        return None, None
    return None, None


def _hill_formula(symbols: list[str]) -> str:
    from collections import Counter

    counts: dict[str, int] = Counter(symbols)
    order = ["C", "H"] + sorted(set(counts) - {"C", "H"})
    parts: list[str] = []
    for symbol in order:
        count = counts.get(symbol)
        if count:
            parts.append(f"{symbol}{count if count > 1 else ''}")
    return "".join(parts) or "unknown"


# ── input compilation / scan run ───────────────────────────────────────


def _step_angstrom(coordinate: ScanCoordinate) -> float | None:

    return coordinate_step_angstrom(coordinate)


def _scan_coordinate_spec(coordinate: ScanCoordinate) -> CoordinateSpec:
    """Build the 0-based drive :class:`CoordinateSpec` shared by all scan stages."""
    return CoordinateSpec(
        id=coordinate.kind,
        kind=cast("Literal['distance', 'angle', 'dihedral']", coordinate.kind),
        atoms=tuple(int(atom) for atom in coordinate.atoms),
        role="drive",
        start=coordinate.start,
        end=coordinate.end,
    )


def _compile_scan_input(
    interface: ORCAInterface,
    coords: np.ndarray[Any, Any],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    coordinate: ScanCoordinate,
    protocol: ScanProtocol,
    scan_dir: Path,
) -> Path:
    """Write the ORCA ``scan.inp`` for audit before the run (plan §6.3).

    Reuses the interface's canonical input builder so the compiled input is
    byte-identical to what :meth:`ORCAInterface.relaxed_scan` writes.
    """
    from cccp.qc.interfaces.orca import (
        _is_orca_gfn_xtb_method,
        _orca_scan_line,
        _orca_scan_route_settings,
    )

    spec = _scan_coordinate_spec(coordinate)
    assert spec.start is not None and spec.end is not None
    eff_method = protocol.scan_optimizer.method or "GFN2-xTB"
    eff_basis = "" if _is_orca_gfn_xtb_method(eff_method) else None
    route_extras, solvent, solvent_model = _orca_scan_route_settings(
        eff_method,
        None,
        None,
        bool(protocol.scan_driver.use_scants),
        None,
    )
    geom_extra_lines = [
        "  Scan",
        _orca_scan_line(spec, coordinate.n_points),
        "  end",
    ]
    if bool(protocol.scan_driver.use_scants) and bool(protocol.scan_driver.full_scan):
        geom_extra_lines.append("  FullScan true")
    geom_maxiter = int(
        protocol.scan_optimizer.max_iterations or protocol.scan_driver.max_iterations
    )

    input_file = scan_dir / "scan.inp"
    interface._write_input(  # noqa: SLF001 — canonical builder reuse (plan §6.3)
        input_file,
        coords,
        symbols,
        "opt",
        charge,
        multiplicity,
        method=eff_method,
        basis=eff_basis,  # type: ignore[arg-type]
        route_extras=route_extras,
        geom_maxiter=geom_maxiter,
        solvent=solvent,  # type: ignore[arg-type]
        solvent_model=solvent_model,  # type: ignore[arg-type]
        geom_extra_lines=geom_extra_lines,
    )
    return input_file


def _completed_scan_inp_text(scan_dir: Path) -> str | None:
    """Return the previous ``scan.inp`` text when a completed scan is reusable.

    Reuse requires an existing ``scan.inp`` plus an ``scan.out`` that ended in
    ``ORCA TERMINATED NORMALLY``; the caller additionally checks that the
    freshly compiled input is byte-identical (same request → same scan).
    """
    inp_path = scan_dir / "scan.inp"
    out_path = scan_dir / "scan.out"
    try:
        inp_text = inp_path.read_text(encoding="utf-8")
        out_text = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "ORCA TERMINATED NORMALLY" not in out_text:
        return None
    return inp_text


def _run_relaxed_scan(
    interface: ORCAInterface,
    coords: np.ndarray[Any, Any],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    coordinate: ScanCoordinate,
    protocol: ScanProtocol,
    scan_dir: Path,
    cfg: dict[str, Any],
) -> Any:
    """Execute the ORCA relaxed scan and return the interface result."""
    spec = _scan_coordinate_spec(coordinate)
    nproc = int((cfg.get("resources") or {}).get("nproc") or 1)
    return interface.relaxed_scan(
        coords,
        symbols,
        scan_coordinate=spec,
        points=coordinate.n_points,
        charge=charge,
        multiplicity=multiplicity,
        output_dir=scan_dir,
        output_name="scan",
        method=protocol.scan_optimizer.method,
        basis=None,
        use_scants=bool(protocol.scan_driver.use_scants),
        full_scan=bool(protocol.scan_driver.full_scan),
        nprocs=nproc,
        geom_maxiter=int(
            protocol.scan_optimizer.max_iterations or protocol.scan_driver.max_iterations
        ),
    )


# ── frame extraction / single points ───────────────────────────────────


def _extract_frames(
    scan_result: Any,
    coordinate: ScanCoordinate,
    scan_dir: Path,
) -> list[ScanFrame]:
    """Build per-frame records from the scan result (plan §7.2).

    The interface already wrote ``scan_frames/frame_NNN.xyz``; here we
    compute the actual bond length and map energies to the frame schema.
    """
    frames: list[ScanFrame] = []
    frames_dir = scan_dir / "scan_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for point in scan_result.points:
        index = int(point.frame_index)
        frame_path = frames_dir / f"frame_{index:03d}.xyz"
        coords = np.asarray(point.coordinates, dtype=float)
        write_xyz(
            frame_path,
            coords,
            [str(symbol) for symbol in point.symbols],
            title=f"scan frame {index} target={point.progress:.4f}",
        )
        try:
            actual = _measure_scan_coordinate(coords, coordinate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scan coordinate computation failed for frame %d: %s", index, exc)
            actual = float("nan")
        target = float(point.coordinate_values.get(coordinate.kind, point.progress))
        unit = "angstrom" if coordinate.kind == "distance" else "degree"
        frames.append(
            ScanFrame(
                index=index,
                target_coordinate=target,
                actual_coordinate=actual,
                coordinate_unit=unit,
                geometry_path=f"scan_frames/frame_{index:03d}.xyz",
                scan_energy_hartree=point.energy_hartree,
                single_point_energy_hartree=None,
                optimization_converged=bool(point.success),
                single_point_status="pending",
                source_log="scan.out",
            )
        )
    return frames


def _measure_scan_coordinate(coords: np.ndarray[Any, Any], coordinate: ScanCoordinate) -> float:
    """Measure the driven coordinate on one optimized frame."""
    if coordinate.kind == "distance":
        return float(GeometryUtils.calculate_distance(coords, *coordinate.atoms))
    if coordinate.kind == "angle":
        return float(GeometryUtils.calculate_angle(coords, *coordinate.atoms))
    if coordinate.kind == "dihedral":
        return float(GeometryUtils.calculate_dihedral(coords, *coordinate.atoms))
    raise ValueError(f"Unsupported scan coordinate kind: {coordinate.kind!r}")


def _run_single_points(
    interface: ORCAInterface,
    frames: list[ScanFrame],
    charge: int,
    multiplicity: int,
    sp_spec: Any,
    scan_dir: Path,
) -> None:
    """Run one single point per frame; resume from completed outputs (§8.3).

    On per-frame failure the scan energy is retained and the frame is
    marked ``failed`` — the curve keeps the scan level (plan §9.1), never a
    silently mixed level.
    """
    if not sp_spec.enabled:
        updated = [_replace(frame, single_point_status="skipped") for frame in frames]
        frames[:] = updated
        return
    frames_dir = scan_dir / "scan_frames"
    sp_root = scan_dir / "sp"
    sp_root.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        frame_path = frames_dir / f"frame_{frame.index:03d}.xyz"
        try:
            frame_coords, frame_symbols = read_xyz(frame_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot read frame geometry %s: %s", frame_path, exc)
            frames[frame.index] = _replace(
                frame,
                single_point_energy_hartree=None,
                single_point_status="failed",
            )
            continue
        output = sp_root / f"frame_{frame.index:03d}.out"
        energy = None
        if sp_spec.resume and output.is_file():
            energy = _parse_sp_energy(output)
            if energy is not None and not _sp_output_matches_frame(output, frame_coords):
                logger.info(
                    "Discarding stale SP output %s (geometry does not match frame %d)",
                    output,
                    frame.index,
                )
                energy = None
        if energy is None:
            try:
                route_extras: list[str] = []
                if sp_spec.dispersion and sp_spec.dispersion.lower() != "none":
                    route_extras.append(str(sp_spec.dispersion).upper())
                if sp_spec.ri_approximation and sp_spec.ri_approximation.lower() != "none":
                    route_extras.append(str(sp_spec.ri_approximation).upper())
                grid_keywords = {
                    "sg1": "DEFGRID1",
                    "ultrafine": "DEFGRID3",
                    "superfine": "DEFGRID3",
                }
                grid_keyword = grid_keywords.get(str(sp_spec.grid or "").lower())
                if grid_keyword:
                    route_extras.append(grid_keyword)
                scf_keywords = {"tight": "TightSCF", "verytight": "VeryTightSCF"}
                scf_keyword = scf_keywords.get(str(sp_spec.scf_convergence or "").lower())
                if scf_keyword:
                    route_extras.append(scf_keyword)
                result = interface.single_point(
                    np.asarray(frame_coords, dtype=float),
                    [str(symbol) for symbol in frame_symbols],
                    charge=charge,
                    multiplicity=multiplicity,
                    output_dir=sp_root,
                    output_name=f"frame_{frame.index:03d}",
                    method=sp_spec.method,
                    basis=sp_spec.basis,
                    route_extras=route_extras,
                    solvent=sp_spec.solvent,
                    solvent_model=sp_spec.solvent_model,
                    aux_j_basis=sp_spec.aux_j_basis,
                    aux_c_basis=sp_spec.aux_c_basis,
                )
                energy = result.energy if result.success else None
            except Exception as exc:  # noqa: BLE001 — per-frame failure is data
                logger.warning("SP failed for frame %d: %s", frame.index, exc)
                energy = None
        status = "completed" if energy is not None else "failed"
        frames[frame.index] = _replace(
            frame,
            single_point_energy_hartree=energy,
            single_point_status=status,
        )


def _replace(frame: ScanFrame, **changes: Any) -> ScanFrame:
    return replace(frame, **changes)


def _parse_sp_energy(output: Path) -> float | None:
    """Extract the final SP energy from an ORCA ``.out`` (resume path)."""
    from cccp.utils.geometry_tools import LogParser

    try:
        return LogParser.extract_energy(output, "orca")
    except Exception as exc:  # noqa: BLE001
        logger.debug("SP resume parse failed for %s: %s", output, exc)
        return None


def _sp_output_matches_frame(output: Path, frame_coords: np.ndarray[Any, Any]) -> bool:
    """Check that a cached SP output was computed on this frame's geometry.

    Guards the resume path against stale outputs written for a different
    (e.g. re-extracted) frame geometry.
    """
    from cccp.qc.interfaces.orca import _parse_all_cartesian_blocks

    try:
        text = output.read_text(encoding="utf-8", errors="replace")
        blocks = _parse_all_cartesian_blocks(text)
        if not blocks:
            return False
        sp_coords = np.asarray(blocks[-1][0], dtype=float)
    except (OSError, ValueError) as exc:
        logger.debug("SP geometry check failed for %s: %s", output, exc)
        return False
    frame = np.asarray(frame_coords, dtype=float)
    if sp_coords.shape != frame.shape:
        return False
    return bool(np.allclose(sp_coords, frame, atol=1e-4))


# ── energy profile ─────────────────────────────────────────────────────


def _build_energy_profile(
    frames: list[ScanFrame],
    sp_spec: Any,
) -> EnergyProfile:
    """Build the curve per plan §9.1 (SP complete → SP; else scan energy)."""
    if sp_spec.enabled:
        sp_energies = [frame.single_point_energy_hartree for frame in frames]
        completed = [value for value in sp_energies if value is not None]
        if completed and len(completed) == len(frames):
            energies = sp_energies
            energy_source = "single_point"
            sp_incomplete = False
        else:
            energies = [frame.scan_energy_hartree for frame in frames]
            energy_source = "scan"
            sp_incomplete = True
    else:
        energies = [frame.scan_energy_hartree for frame in frames]
        energy_source = "scan"
        sp_incomplete = False

    reference = next((value for value in energies if value is not None), None)
    reference_index = next(
        (index for index, value in enumerate(energies) if value is not None),
        0,
    )
    relative = [
        None
        if value is None or reference is None
        else (float(value) - float(reference)) * HARTREE_TO_KCAL
        for value in energies
    ]
    return EnergyProfile(
        energy_source=energy_source,
        unit="kcal/mol",
        reference_index=reference_index,
        relative_energies_kcal_mol=tuple(relative),
        raw_hartree=tuple(energies),
        sp_incomplete=sp_incomplete,
    )


# ── candidate recommendation ───────────────────────────────────────────


def _recommend_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    scan_dir: Path,
    profile: EnergyProfile,
    cfg: dict[str, Any],
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend TS/INT initial guesses via the shared primitives (§9.2/§9.3)."""
    if coordinate.kind != "distance":
        return _recommend_coordinate_candidates(frames, coordinate, profile)

    from .primitives import build_orca_scan_profile, policy_from_config, select_path_seeds

    frames_dir = scan_dir / "scan_frames"
    frame_paths = [frames_dir / f"frame_{frame.index:03d}.xyz" for frame in frames]
    energies = list(profile.raw_hartree)

    notes: list[str] = []
    usable_energies = [value for value in energies if value is not None]
    if not usable_energies:
        notes.append("no_energies")
    if profile.sp_incomplete:
        notes.append("sp_incomplete_scan_energy_used")
    if any(not frame.optimization_converged for frame in frames):
        notes.append("non_converged_frames")

    path_profile = build_orca_scan_profile(
        frames=frame_paths,
        energies_hartree=energies,
        forming_bonds=[(int(coordinate.atoms[0]), int(coordinate.atoms[1]))],
        product_xyz=frame_paths[-1],
        energy_source=profile.energy_source,
        source_provenance={
            "mode": "bond_length_scan",
            "coordinate": coordinate.to_dict(),
            "sp_incomplete": profile.sp_incomplete,
        },
    )
    policy = policy_from_config(_selection_config(cfg))
    selection = select_path_seeds(path_profile, policy)

    ts_recs: list[CandidateRecommendation] = []
    int_recs: list[CandidateRecommendation] = []
    needs_review = False

    if selection.ts_search_seed is None:
        needs_review = True
        notes.append(f"no_confident_candidate:{selection.rejection_reason or 'unresolved'}")
    else:
        ts_seed = selection.ts_search_seed
        frame_index = int(ts_seed.get("frame_index") or 0)
        ts_recs.append(
            _ts_recommendation(
                ts_seed,
                frame_index,
                selection,
            )
        )
        # Plan §9.5: a monotonic/edge-curve seed carries only low confidence —
        # the scan is presentable but must go back to the user for review.
        if ts_recs[-1].confidence == "low":
            needs_review = True
            notes.append("low_confidence_ts_candidate")
        if selection.int_search_seed is not None:
            int_seed = selection.int_search_seed
            int_frame_index = int(int_seed.get("frame_index") or 0)
            int_recs.append(
                _int_recommendation(
                    int_seed,
                    int_frame_index,
                )
            )

    # A partial or non-converged scan is useful for diagnosis, but it is not
    # safe to present as a normal review-ready surface.  The previous code
    # only recorded these conditions in notes, which allowed a partial scan
    # with a plausible peak to be promoted to ``ready_for_review``.
    if not path_profile.complete:
        needs_review = True
        notes.append("incomplete_scan")
    if any(not frame.optimization_converged for frame in frames):
        needs_review = True

    if profile.sp_incomplete:
        needs_review = True
    status = "needs_review" if needs_review else "ready_for_review"
    if not usable_energies:
        status = "partial"

    quality = ScanQuality(
        status=status,
        scan_complete=bool(path_profile.complete),
        sp_incomplete=profile.sp_incomplete,
        needs_review=needs_review,
        notes=tuple(dict.fromkeys(notes)),
    )
    return ts_recs, int_recs, quality


def _recommend_coordinate_candidates(
    frames: list[ScanFrame],
    coordinate: ScanCoordinate,
    profile: EnergyProfile,
) -> tuple[list[CandidateRecommendation], list[CandidateRecommendation], ScanQuality]:
    """Recommend extrema for angle/dihedral scans.

    The established path-profile selector is reaction-distance specific.  For
    non-distance coordinates, surface local energy maxima as TS-like guesses
    and local minima as intermediate-like guesses, always requiring user
    review before S3 promotion.
    """
    energies = list(profile.raw_hartree)
    usable = [value for value in energies if value is not None]
    notes = ["coordinate_scan_extrema_heuristic"]
    ts_rows: list[CandidateRecommendation] = []
    int_rows: list[CandidateRecommendation] = []
    for index in range(1, len(frames) - 1):
        energy = energies[index]
        left = energies[index - 1]
        right = energies[index + 1]
        if energy is None or left is None or right is None:
            continue
        if energy > left and energy >= right:
            prominence = float(energy - max(left, right))
            ts_rows.append(
                CandidateRecommendation(
                    candidate_id=f"ts_guess_{len(ts_rows) + 1:03d}",
                    kind="ts",
                    frame_index=frames[index].index,
                    geometry_path=frames[index].geometry_path,
                    score=prominence,
                    confidence="medium" if prominence >= 0.0005 else "low",
                    evidence={
                        "coordinate_kind": coordinate.kind,
                        "coordinate_value": frames[index].actual_coordinate,
                        "prominence_hartree": prominence,
                    },
                    reason=f"局部能量峰（{coordinate.kind} 扫描）",
                )
            )
        if energy < left and energy <= right:
            depth = float(min(left, right) - energy)
            int_rows.append(
                CandidateRecommendation(
                    candidate_id=f"int_guess_{len(int_rows) + 1:03d}",
                    kind="intermediate",
                    frame_index=frames[index].index,
                    geometry_path=frames[index].geometry_path,
                    score=depth,
                    confidence="medium" if depth >= 0.0005 else "low",
                    evidence={
                        "coordinate_kind": coordinate.kind,
                        "coordinate_value": frames[index].actual_coordinate,
                        "basin_depth_hartree": depth,
                    },
                    reason=f"局部能量谷（{coordinate.kind} 扫描）",
                )
            )
    ts_rows.sort(key=lambda row: row.score, reverse=True)
    int_rows.sort(key=lambda row: row.score, reverse=True)
    ts_rows = ts_rows[:5]
    int_rows = int_rows[:5]
    if not usable:
        notes.append("no_energies")
    if profile.sp_incomplete:
        notes.append("sp_incomplete_scan_energy_used")
    if any(not frame.optimization_converged for frame in frames):
        notes.append("non_converged_frames")
    complete = len(frames) >= 3 and all(value is not None for value in energies)
    needs_review = True
    if not ts_rows and not int_rows:
        notes.append("no_coordinate_extrema")
    if not complete:
        notes.append("incomplete_scan")
    quality = ScanQuality(
        status="partial" if not usable else "needs_review",
        scan_complete=complete,
        sp_incomplete=profile.sp_incomplete,
        needs_review=needs_review,
        notes=tuple(dict.fromkeys(notes)),
    )
    return ts_rows, int_rows, quality


def _selection_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Selection policy config for the bond scan (plan §9.5 tolerances)."""
    scan_selection = dict((cfg.get("step2") or {}).get("scan") or {})
    base = dict(scan_selection.get("selection") or {})
    base.setdefault("endpoint_exclusion_frames", 2)
    base.setdefault("ts_min_prominence_kcal_mol", 0.40)
    base.setdefault("int_min_basin_prominence_kcal_mol", 0.50)
    base.setdefault("min_reaction_progress", 0.30)
    base.setdefault("ts_right_shift_override_A", 0.10)
    return base


def _ts_recommendation(
    ts_seed: dict[str, Any],
    frame_index: int,
    selection: Any,
) -> CandidateRecommendation:
    confidence = str(ts_seed.get("confidence") or "medium")
    evidence = {
        "is_local_peak": bool(
            (selection.diagnostics.get("peak_candidates") or [])
            and any(
                int(item.get("frame_index") or -1) == frame_index
                and item.get("accepted_as_knee_anchor")
                for item in selection.diagnostics.get("peak_candidates") or []
            )
        ),
        "has_left_neighbor": frame_index > 0,
        "has_right_neighbor": bool(
            frame_index < int(selection.diagnostics.get("frame_count") or (frame_index + 1)) - 1
        ),
        "is_boundary": bool(
            frame_index in set(selection.diagnostics.get("excluded_frame_indices") or [])
        ),
        "profile_status": "usable",
        "knee_frame_index": (selection.knee_evidence or {}).get("frame_index"),
        "right_shift_A": (selection.knee_evidence or {}).get("right_shift_A"),
        "selection_mode": ts_seed.get("selection_mode"),
    }
    score = _confidence_score(confidence)
    reason = (
        "局部峰值/膝点右侧存在收敛扫描帧，建议进入 S3 TS 优化"
        if evidence["is_local_peak"]
        else "能量曲线上升段膝点附近帧，建议进入 S3 TS 优化"
    )
    return CandidateRecommendation(
        candidate_id=f"ts_guess_{frame_index + 1:03d}",
        kind="ts",
        frame_index=frame_index,
        geometry_path=f"scan_frames/frame_{frame_index:03d}.xyz",
        score=score,
        confidence=confidence,
        evidence=evidence,
        reason=reason,
    )


def _int_recommendation(
    int_seed: dict[str, Any],
    frame_index: int,
) -> CandidateRecommendation:
    confidence = "medium" if not int_seed.get("shared_with_ts") else "low"
    evidence = {
        "shared_with_ts": bool(int_seed.get("shared_with_ts")),
        "selection_mode": int_seed.get("selection_mode"),
    }
    return CandidateRecommendation(
        candidate_id=f"int_guess_{frame_index + 1:03d}",
        kind="intermediate",
        frame_index=frame_index,
        geometry_path=f"scan_frames/frame_{frame_index:03d}.xyz",
        score=_confidence_score(confidence),
        confidence=confidence,
        evidence=evidence,
        reason=(
            "能量平台/TS-端点中段存在稳定结构，建议进入 S3 最小点优化"
            if not int_seed.get("shared_with_ts")
            else "与 TS 初猜共享扫描帧，建议进入 S3 最小点验证"
        ),
    )


def _confidence_score(confidence: str) -> float:
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(str(confidence).lower(), 0.4)


__all__ = [
    "BOND_SCAN_STAGES",
    "SCAN_DIR_NAME",
    "run_bond_length_scan",
]
