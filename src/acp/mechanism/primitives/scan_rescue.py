"""Injected relaxed-scan rescue and shared S2 seed selection.

Scan execution is injected via ``scan_callable``. Plotting is intentionally not
ported; the module only produces the shared JSON profile payload.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from acp.mechanism._helpers import write_json_atomic
from cccp.utils.file_io import read_xyz

from .path_profile import HARTREE_TO_KCAL, PathFrameEvidence, PathProfile, build_orca_scan_profile
from .path_selector import policy_from_config, select_path_seeds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SurfaceScanCoordinate:
    kind: str
    atoms: tuple[int, int]
    start: float
    end: float
    steps: int


@dataclass(frozen=True)
class SurfaceScanSpec:
    method: str
    solvent: str | None = None
    solvent_model: str | None = None
    nproc: int | None = None
    maxcore: int | None = None
    charge: int = 0
    multiplicity: int = 1
    coordinates: tuple[SurfaceScanCoordinate, ...] = ()
    simultaneous: bool = False
    scan_ts: bool = False
    full_scan: bool = True


@dataclass(frozen=True)
class SurfaceScanResult:
    status: str
    output_file: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class B97CRelaxedScanRescuer:
    """Run an injected relaxed scan and delegate seed selection."""

    def __init__(
        self,
        *,
        scan_callable: Any,
        selection_config: Mapping[str, Any] | None = None,
        scan_config: Mapping[str, Any] | None = None,
        enabled: bool = True,
        event_callback: Any = None,
        variant: str | None = None,
        scan_method: str = "B97-3c",
        scan_role: str = "rescue",
    ) -> None:
        self.scan_callable = scan_callable
        self.selection_config = dict(selection_config or {})
        self.scan_config = dict(scan_config or {})
        self.enabled = bool(enabled)
        self.event_callback = event_callback
        self.variant = variant or "product"
        self.scan_method = str(scan_method or "B97-3c")
        self.scan_role = str(scan_role or "rescue")
        method_key = self.scan_method.lower().replace("-", "").replace(" ", "")
        self.scan_method_key = "gfn2" if "gfn2" in method_key else "b973c"
        self._batch_id = f"{self.variant}:{self.scan_method_key}_relaxed_scan_{self.scan_role}"

    @property
    def _scan_label(self) -> str:
        if self.scan_method_key == "gfn2":
            return "ORCA GFN2-xTB relaxed scan"
        return "ORCA B97-3c relaxed-scan rescue"

    @property
    def _scan_phase(self) -> str:
        return (
            "s2_orca_gfn2_relaxed_scan"
            if self.scan_method_key == "gfn2"
            else "s2_b973c_relaxed_scan_rescue"
        )

    def _emit(self, event: str, **fields: Any) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event, {"variant": self.variant, **fields})
        except Exception as exc:  # pragma: no cover - UI isolation
            logger.warning(
                "[S2] Ignoring relaxed-scan rescue UI callback failure for %s: %s",
                event,
                exc,
            )

    def _emit_scan_started(
        self,
        spec: SurfaceScanSpec,
        output_dir: Path,
        *,
        use_scants: bool,
        coordinate_count: int,
        points: int,
    ) -> float:
        started_at = time.time()
        self._emit(
            "step_started",
            step=self._scan_phase,
            label=self._scan_label,
            purpose=(
                "Primary low-cost S2 scan before B97-3c SP refinement"
                if self.scan_method_key == "gfn2"
                else "Recover TS/INT seeds from a topology-distorted S2 scan"
            ),
            index=1 if self.scan_method_key == "gfn2" else 4,
            total_steps=3 if self.scan_method_key == "gfn2" else 4,
            engine="ORCA",
            method=spec.method,
            nprocs=int(spec.nproc or 1),
            scan_mode="ScanTS" if use_scants else "simultaneous relaxed scan",
            coordinate_count=coordinate_count,
            point_count=points,
            output=str(output_dir),
        )
        self._emit(
            "batch_started",
            batch=self._batch_id,
            label=self._scan_label,
            phase=self._scan_phase,
            total=1,
            done=0,
            failed=0,
            running=1,
            current="relaxed_scan",
            started_at=started_at,
            parallel_jobs=1,
            cores_per_job=int(spec.nproc or 1),
            engine="ORCA",
            method=spec.method,
        )
        self._emit(
            "batch_job_started",
            batch=self._batch_id,
            job_id="relaxed_scan",
            engine="ORCA",
            method=spec.method,
            nprocs=int(spec.nproc or 1),
            output=str(output_dir),
            started_at=started_at,
        )
        return time.monotonic()

    def _emit_scan_finished(
        self,
        *,
        status: str,
        error: str | None,
        started: float,
        output_dir: Path,
        method: str,
    ) -> None:
        elapsed = max(0.0, time.monotonic() - started)
        complete = status == "complete"
        self._emit(
            "batch_job_finished" if complete else "batch_job_failed",
            batch=self._batch_id,
            job_id="relaxed_scan",
            engine="ORCA",
            method=method,
            output=str(output_dir),
            elapsed_seconds=elapsed,
            status="complete" if complete else "failed",
            error=error,
        )
        self._emit(
            "batch_finished",
            batch=self._batch_id,
            label=self._scan_label,
            phase=self._scan_phase,
            total=1,
            done=1 if complete else 0,
            failed=0 if complete else 1,
            running=0,
            status="complete" if complete else "failed",
            elapsed_seconds=elapsed,
            method=method,
            error=error,
        )

    def _emit_rescue_finished(self, rescue: Mapping[str, Any], *, started: float) -> None:
        status = str(rescue.get("status") or "failed")
        self._emit(
            "step_finished" if status == "complete" else "step_failed",
            step=self._scan_phase,
            label=self._scan_label,
            index=1 if self.scan_method_key == "gfn2" else 4,
            total_steps=3 if self.scan_method_key == "gfn2" else 4,
            status="complete" if status == "complete" else "failed",
            elapsed_seconds=max(0.0, time.monotonic() - started),
            output=rescue.get("scan_plot") or rescue.get("output_file"),
            resolution=rescue.get("resolution"),
            s2_state=rescue.get("s2_state"),
            seed_evidence=rescue.get("seed_evidence"),
            scan_profile=rescue.get("scan_profile"),
            error=rescue.get("error"),
        )

    @staticmethod
    def _seed_frame_xyz(
        profile_frames: Sequence[PathFrameEvidence],
        seed: Mapping[str, Any] | None,
        *,
        fallback: Path | None = None,
    ) -> str | None:
        if not seed:
            return str(fallback) if fallback is not None else None
        frame_index_raw = seed.get("frame_index")
        if frame_index_raw is None:
            return str(fallback) if fallback is not None else None
        try:
            frame_index = int(frame_index_raw)
        except (TypeError, ValueError):
            return str(fallback) if fallback is not None else None
        if 0 <= frame_index < len(profile_frames):
            return str(Path(profile_frames[frame_index].xyz))
        return str(fallback) if fallback is not None else None

    @staticmethod
    def _candidate_admission(
        diagnostics: Mapping[str, Any],
        *,
        accepted: bool,
        min_prominence: float,
        min_barrier: float,
        require_scaffold: bool = False,
    ) -> dict[str, Any]:
        peak_index = diagnostics.get("ts_frame_index")
        return {
            "peak_index": int(peak_index) if peak_index is not None else None,
            "knee_index": (
                int(diagnostics["knee_frame_index"])
                if diagnostics.get("knee_frame_index") is not None
                else None
            ),
            "ts_right_shift_A": diagnostics.get("ts_right_shift_applied_A"),
            "prominence_kcal_mol": diagnostics.get("selected_peak_prominence_kcal_mol"),
            "barrier_from_reactant_kcal_mol": diagnostics.get("barrier_from_reactant_kcal_mol"),
            "minimum_prominence_kcal_mol": float(min_prominence),
            "minimum_barrier_kcal_mol": float(min_barrier),
            "geometry": dict(diagnostics.get("ts_scaffold_gate") or {}),
            "geometry_required_for_search_seed": bool(require_scaffold),
            "accepted": bool(accepted),
        }

    @staticmethod
    def _selection_rule(rescue: Mapping[str, Any]) -> str:
        evidence = str(rescue.get("seed_evidence") or "none")
        mode = "b973c_scants" if rescue.get("scan_ts") else "b973c_simultaneous_relaxed_scan"
        return f"{mode}_{evidence}"

    @staticmethod
    def _intermediate_selection_mode(rescue: Mapping[str, Any]) -> str:
        seed = dict(rescue.get("int_search_seed") or {})
        if not seed:
            return "not_selected"
        if str(seed.get("selection_mode")) == "ts_to_effective_endpoint_midpoint":
            return "ts_to_effective_endpoint_midpoint"
        if bool(seed.get("shared_with_ts", False)):
            return "shared_ts_fallback"
        if bool(rescue.get("has_independent_int", False)):
            return "stable_basin_candidate"
        return "search_seed"

    @staticmethod
    def _coerce_scan_result(result: SurfaceScanResult | Mapping[str, Any]) -> SurfaceScanResult:
        if isinstance(result, SurfaceScanResult):
            return result
        payload = dict(result)
        output_file_raw = payload.get("output_file")
        return SurfaceScanResult(
            status=str(payload.get("status") or "failed"),
            output_file=(Path(output_file_raw) if output_file_raw else None),
            extra=dict(payload.get("extra") or {}),
            error=(str(payload.get("error")) if payload.get("error") is not None else None),
        )

    def run(
        self,
        product_xyz: Path,
        output_dir: Path,
        forming_bonds: Sequence[tuple[int, int]],
        *,
        trigger_reasons: Sequence[str],
    ) -> dict[str, Any]:
        scan_cfg = dict(self.scan_config)
        selection_cfg = dict(self.selection_config)
        base: dict[str, Any] = {
            "status": "not_run",
            "trigger_reasons": list(trigger_reasons),
            "resolution": "unresolved",
            "s2_state": "unresolved",
            "seed_evidence": "none",
            "ts_search_seed": None,
            "int_search_seed": None,
            "has_independent_int": False,
            "rejection_reason": None,
            "selection_diagnostics": {},
            "endpoint_evidence": None,
            "knee_evidence": None,
            "ts_xyz": None,
            "intermediate_xyz": None,
            "candidate_admission": {},
            "frames": [],
            "energies_hartree": [],
            "energy_source": None,
            "scan_profile": None,
            "scan_plot": None,
            "output_file": None,
            "error": None,
            "scan_engine": "orca",
            "scan_method": self.scan_method,
            "scan_role": self.scan_role,
            "scan_source": (
                "orca_gfn2_relaxed_scan"
                if self.scan_method_key == "gfn2"
                else "orca_b973c_relaxed_scan_rescue"
            ),
            "energy_refinement_engine": "orca",
            "energy_refinement_method": (
                "B97-3c" if self.scan_method_key == "gfn2" else self.scan_method
            ),
        }
        if not self.enabled:
            base["status"] = "disabled"
            return base
        if not forming_bonds:
            base.update(status="failed", error="No forming bonds available for ORCA relaxed scan")
            return base
        try:
            product_coords, _symbols = read_xyz(Path(product_xyz))
        except (OSError, ValueError) as exc:
            base.update(status="failed", error=f"Cannot read ORCA scan product geometry: {exc}")
            return base

        target_end = float(scan_cfg.get("stretch_end_A", 3.40))
        points = max(3, int(scan_cfg.get("points", 17)))
        coordinates: list[SurfaceScanCoordinate] = []
        for atom_i, atom_j in list(forming_bonds)[:3]:
            start = float(np.linalg.norm(product_coords[int(atom_i)] - product_coords[int(atom_j)]))
            coordinates.append(
                SurfaceScanCoordinate(
                    kind="B",
                    atoms=(int(atom_i), int(atom_j)),
                    start=start,
                    end=max(start + 0.05, target_end),
                    steps=points,
                )
            )
        use_scants = len(coordinates) == 1 and bool(
            scan_cfg.get("single_coordinate_use_scants", True)
        )
        spec = SurfaceScanSpec(
            method=self.scan_method,
            solvent=(str(scan_cfg["solvent"]) if scan_cfg.get("solvent") is not None else None),
            solvent_model=(
                str(scan_cfg["solvent_model"])
                if scan_cfg.get("solvent_model") is not None
                else None
            ),
            nproc=(int(scan_cfg["nproc"]) if scan_cfg.get("nproc") is not None else None),
            maxcore=(int(scan_cfg["maxcore"]) if scan_cfg.get("maxcore") is not None else None),
            charge=int(scan_cfg.get("charge", 0)),
            multiplicity=int(scan_cfg.get("multiplicity", 1)),
            coordinates=tuple(coordinates),
            simultaneous=len(coordinates) > 1,
            scan_ts=use_scants,
            full_scan=True,
        )
        if not spec.method:
            base.update(status="failed", error="ORCA relaxed scan requires a configured method")
            return base

        scan_output_dir = Path(output_dir)
        started = self._emit_scan_started(
            spec,
            scan_output_dir,
            use_scants=use_scants,
            coordinate_count=len(coordinates),
            points=points,
        )
        result = self._coerce_scan_result(
            self.scan_callable(spec, Path(product_xyz), scan_output_dir)
        )
        base.update(
            status=result.status,
            output_file=str(result.output_file) if result.output_file else None,
            scan_ts=use_scants,
            coordinates=[
                {
                    "kind": item.kind,
                    "atoms": list(item.atoms),
                    "start_A": item.start,
                    "end_A": item.end,
                    "steps": item.steps,
                }
                for item in coordinates
            ],
        )
        self._emit_scan_finished(
            status=result.status,
            error=result.error,
            started=started,
            output_dir=scan_output_dir,
            method=spec.method,
        )
        if result.status != "complete":
            base["error"] = result.error
            self._emit_rescue_finished(base, started=started)
            return base

        extra = dict(result.extra or {})
        frames = self._flatten_scan_frames(
            [Path(value) for value in extra.get("frames", [])],
            scan_output_dir,
        )
        raw_energies = list(extra.get("energies_hartree", []) or [])
        energy_source = str(extra.get("energy_source", "") or "")
        base["frames"] = [str(frame) for frame in frames]
        base["energies_hartree"] = [
            None if value is None else float(value) for value in raw_energies
        ]
        base["energy_source"] = energy_source
        base["scan_method"] = self.scan_method
        if (
            len(frames) < 3
            or len(raw_energies) != len(frames)
            or any(value is None for value in raw_energies)
        ):
            base.update(status="failed", error="Relaxed scan lacks complete frame-energy coverage")
            self._emit_rescue_finished(base, started=started)
            return base

        energies = [float(value) for value in raw_energies]
        scan_ts_candidate_xyz = extra.get("scan_ts_candidate_xyz")
        scan_ts_candidate_path = Path(scan_ts_candidate_xyz) if scan_ts_candidate_xyz else None
        profile = build_orca_scan_profile(
            frames=frames,
            energies_hartree=energies,
            forming_bonds=forming_bonds,
            product_xyz=Path(product_xyz),
            energy_source=energy_source,
            scan_ts_candidate_xyz=scan_ts_candidate_path,
            source_provenance={
                "input_xyz": Path(product_xyz),
                "output_file": result.output_file,
                "scan_ts": use_scants,
                "coordinates": base.get("coordinates"),
            },
        )
        policy = policy_from_config(selection_cfg, scan_cfg)
        selection = select_path_seeds(profile, policy)
        diagnostics = dict(selection.diagnostics)
        base.update(
            s2_state=(
                "gfn2_seeded"
                if self.scan_role == "primary" and selection.s2_state != "unresolved"
                else selection.s2_state
            ),
            resolution=(
                "direct_seeded"
                if self.scan_role == "primary" and selection.s2_state != "unresolved"
                else selection.s2_state
            ),
            seed_evidence=selection.seed_evidence,
            ts_search_seed=(
                None if selection.ts_search_seed is None else dict(selection.ts_search_seed)
            ),
            int_search_seed=(
                None if selection.int_search_seed is None else dict(selection.int_search_seed)
            ),
            has_independent_int=bool(selection.has_independent_int),
            rejection_reason=selection.rejection_reason,
            selection_diagnostics=diagnostics,
            endpoint_evidence=(
                None if selection.endpoint_evidence is None else dict(selection.endpoint_evidence)
            ),
            knee_evidence=(
                None if selection.knee_evidence is None else dict(selection.knee_evidence)
            ),
            candidate_admission=self._candidate_admission(
                diagnostics,
                accepted=selection.s2_state != "unresolved",
                min_prominence=float(policy.ts_min_prominence_kcal_mol),
                min_barrier=float(policy.ts_min_reactant_barrier_kcal_mol),
                require_scaffold=bool(policy.require_scaffold_for_search_seed),
            ),
        )
        base["ts_xyz"] = self._seed_frame_xyz(
            profile.frames,
            base.get("ts_search_seed"),
            fallback=(scan_ts_candidate_path if selection.s2_state == "rescue_seeded" else None),
        )
        base["intermediate_xyz"] = self._seed_frame_xyz(
            profile.frames,
            base.get("int_search_seed"),
        )
        self._write_scan_profile(base, profile, output_dir)
        self._emit_rescue_finished(base, started=started)
        return base

    @staticmethod
    def _write_scan_profile(
        rescue: dict[str, Any],
        profile: PathProfile,
        output_dir: Path,
    ) -> None:
        frames = [Path(frame.xyz) for frame in profile.frames]
        energies = [frame.energy_hartree for frame in profile.frames]
        coordinates: list[float] = []
        for index, frame in enumerate(frames):
            try:
                atoms, _symbols = read_xyz(frame)
                distances = [
                    float(np.linalg.norm(atoms[int(atom_i)] - atoms[int(atom_j)]))
                    for atom_i, atom_j in profile.forming_bonds
                ]
                coordinates.append(float(np.mean(distances)))
            except (OSError, ValueError, IndexError):
                coordinates.append(float(index))
        reference = next(
            (float(energy) for energy in reversed(energies) if energy is not None),
            0.0,
        )
        relative = [
            None if energy is None else (float(energy) - reference) * HARTREE_TO_KCAL
            for energy in energies
        ]
        ts_seed = dict(rescue.get("ts_search_seed") or {})
        int_seed = dict(rescue.get("int_search_seed") or {})
        ts_index = ts_seed.get("frame_index")
        int_index = int_seed.get("frame_index")
        selection_rule = B97CRelaxedScanRescuer._selection_rule(rescue)
        profile_payload = {
            "profile_schema_version": "s2_relaxed_scan_profile_v2",
            "profile_kind": (
                "orca_gfn2_relaxed_scan"
                if rescue.get("scan_method") == "GFN2-xTB"
                else "relaxed_scan_rescue"
            ),
            "rescue": dict(rescue),
            "selection_policy": {
                "preferred_source": ("b973c" if rescue.get("scan_method") == "B97-3c" else "gfn2"),
                "actual_source": (
                    rescue.get("energy_refinement_method") or rescue.get("scan_method") or "B97-3c"
                ),
                "energy_reference": "stretched_reactant_side_endpoint",
            },
            "selection_decision": {
                "selected_branch": rescue.get("scan_source") or "orca_relaxed_scan",
                "rule": selection_rule,
            },
            "selection_diagnostics": dict(rescue.get("selection_diagnostics") or {}),
            "endpoint_evidence": dict(rescue.get("endpoint_evidence") or {}),
            "knee_evidence": dict(rescue.get("knee_evidence") or {}),
            "reaction_coordinate_angstrom": coordinates,
            "energy_curves": {
                "gfn2": {
                    "energies_hartree": list(rescue.get("gfn2_energies_hartree") or [])
                    if rescue.get("scan_method") == "GFN2-xTB"
                    else [],
                    "relative_energies_kcal_mol": list(
                        rescue.get("gfn2_relative_energies_kcal_mol") or []
                    )
                    if rescue.get("scan_method") == "GFN2-xTB"
                    else [],
                    "status": (
                        "complete" if rescue.get("scan_method") == "GFN2-xTB" else "not_requested"
                    ),
                },
                "b973c": {
                    "energies_hartree": [
                        None if energy is None else float(energy) for energy in energies
                    ],
                    "relative_energies_kcal_mol": relative,
                    "status": "complete",
                },
            },
            "trajectory_quality": {
                "off_path_indices": [int(index) for index in profile.excluded_frames],
                "topology_state": "distorted" if profile.excluded_frames else "valid",
            },
            "selections": {
                "s2_state": rescue.get("s2_state"),
                "seed_evidence": rescue.get("seed_evidence"),
                "ts_search_seed": ts_seed or None,
                "int_search_seed": int_seed or None,
                "has_independent_int": bool(rescue.get("has_independent_int", False)),
                "ts_guess": {
                    "index": None if ts_index is None else int(ts_index),
                    "rule": selection_rule,
                    "frame_xyz": rescue.get("ts_xyz"),
                },
                "intermediate": {
                    "index": None if int_index is None else int(int_index),
                    "rule": selection_rule,
                    "selection_mode": B97CRelaxedScanRescuer._intermediate_selection_mode(rescue),
                    "frame_xyz": rescue.get("intermediate_xyz"),
                },
            },
        }
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        profile_path = output_dir / "scan_profile.json"
        write_json_atomic(profile_path, profile_payload)
        rescue["scan_profile"] = str(profile_path)
        rescue["scan_method"] = rescue.get("scan_method") or "B97-3c"
        rescue["scan_plot"] = None
        rescue["scan_figures"] = {}

    @staticmethod
    def _flatten_scan_frames(frames: Sequence[Path], output_dir: Path) -> list[Path]:
        output_dir = Path(output_dir)
        flattened: list[Path] = []
        frame_dir = output_dir / "scan_frames"
        for frame in frames:
            frame = Path(frame)
            if frame.parent == output_dir:
                flattened.append(frame)
                continue
            target = output_dir / frame.name
            if frame.exists() and frame.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                frame.replace(target)
            flattened.append(target if target.exists() else frame)
        try:
            frame_dir.rmdir()
        except (FileNotFoundError, OSError):
            pass
        return flattened


__all__ = [
    "B97CRelaxedScanRescuer",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
]
