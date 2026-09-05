"""PES search engine — orchestrates scan → profile → candidate selection.

Migrated strategy logic from ``mechanism/providers/native_peb.py`` and
``mechanism/providers/guided_scan.py``, de-provided and de-studied.
Fidelity parameters inlined.

Pipeline:
    structure / confsearch manifest / coordinate plan →
    run_pes_scan (scan + energy profile + path-selection candidates) →
    write RESULT/structures/<candidate_id>.xyz +
    write RESULT/pes_search/pes_profile.json +
    register result_manifest products
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from acp.calculations.pes.candidates import (
    PathCandidate,
    PathPoint,
    SearchResult,
)
from acp.calculations.pes.contracts import (
    PesScanRequest,
    ScanCoordinate,
    StructureSource,
    build_default_protocol,
)
from acp.calculations.pes.outputs import persist_pes_outputs
from acp.calculations.pes.scan import PES_SCAN_STAGES, run_pes_scan
from acp.calculations.progress import ProgressReporter
from cccp.utils.file_io import read_xyz

logger = logging.getLogger(__name__)

# ── error codes ─────────────────────────────────────────────────────────

PES_E_MANIFEST = "PES_E_MANIFEST"
"""Confsearch manifest is missing, malformed, or has no conformers."""

# ── frozen stage names (generic, used by scheduler stage_tasks) ─────────

PES_SEARCH_STAGES: tuple[str, ...] = PES_SCAN_STAGES


# ── structured error ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PesSearchError(Exception):
    """Structured PES search error with code and message."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ── confsearch manifest loading ─────────────────────────────────────────


@dataclass(frozen=True)
class ConfsearchManifestInput:
    """A confsearch manifest as PES search input."""

    manifest_path: Path
    charge: int = 0
    multiplicity: int = 1


def load_confsearch_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load and validate a confsearch manifest.

    Raises:
        PesSearchError: With code ``PES_E_MANIFEST`` on missing or invalid
            manifest.
    """
    path = Path(manifest_path)
    if not path.is_file():
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=f"Confsearch manifest not found: {path}",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=f"Cannot read confsearch manifest: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=f"Confsearch manifest is not a JSON object: {path}",
        )
    conformers = payload.get("conformers")
    if not conformers or not isinstance(conformers, list):
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=f"Confsearch manifest has no conformers: {path}",
        )
    return payload


def resolve_representative_conformer(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> tuple[str, Path]:
    """Return ``(conf_id, geometry_path)`` for the rank-1 conformer.

    Raises:
        PesSearchError: With code ``PES_E_MANIFEST`` when no conformer has a
            geometry reference.
    """
    conformers = manifest.get("conformers") or []
    if not conformers:
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=f"Confsearch manifest has no conformers: {manifest_path}",
        )
    chosen = min(
        conformers,
        key=lambda entry: int(entry.get("rank") or 999999),
    )
    geometry_ref = str(chosen.get("geometry") or "")
    if not geometry_ref:
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=(
                f"Conformer {chosen.get('conf_id')!r} has no geometry reference in {manifest_path}"
            ),
        )
    geometry_path = (manifest_path.parent / geometry_ref).resolve()
    if not geometry_path.is_file():
        raise PesSearchError(
            code=PES_E_MANIFEST,
            message=(
                f"Conformer geometry not found: {geometry_ref} (looked in {manifest_path.parent})"
            ),
        )
    return str(chosen.get("conf_id", "")), geometry_path


# ── pes search engine ───────────────────────────────────────────────────


@dataclass
class PesSearchResult:
    """Outcome of a PES search engine run."""

    status: str = "pending"
    frames: list[dict[str, Any]] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    ts_candidates: list[PathCandidate] = field(default_factory=list)
    int_candidates: list[PathCandidate] = field(default_factory=list)
    candidate_structures: dict[str, Path] = field(default_factory=dict)
    pes_profile_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "frames": self.frames,
            "profile": self.profile,
            "quality": self.quality,
            "ts_candidates": [c.to_dict() for c in self.ts_candidates],
            "int_candidates": [c.to_dict() for c in self.int_candidates],
            "candidate_structures": {k: str(v) for k, v in self.candidate_structures.items()},
            "pes_profile_path": str(self.pes_profile_path) if self.pes_profile_path else None,
            "metadata": dict(self.metadata),
            "error": self.error,
        }


class PesSearchEngine:
    """Orchestrates the PES search pipeline.

    Inputs: structure / confsearch manifest / coordinate plan.
    Pipeline: scan → BatchSinglePointExecutor → energy profile →
    candidate selection → outputs.

    Outputs:
        * ``RESULT/pes_search/pes_profile.json``
        * ``RESULT/pes_search/pes_recommendations.json`` (audit only)
        * ``result_manifest.json`` registration (profile + audit report;
          structure products appear only after manual review)
    """

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        output_dir: Path | str | None = None,
    ) -> None:
        self.config = dict(config) if config is not None else {}
        self.output_dir = Path(output_dir) if output_dir is not None else Path.cwd()

    def run(
        self,
        *,
        structure_xyz: Path | None = None,
        confsearch_manifest: Path | None = None,
        coordinate: ScanCoordinate | None = None,
        charge: int = 0,
        multiplicity: int = 1,
        progress_reporter: ProgressReporter | None = None,
    ) -> PesSearchResult:
        """Execute the PES search pipeline.

        Args:
            structure_xyz: Path to input structure XYZ.
            confsearch_manifest: Path to a confsearch manifest.  If provided
                and ``structure_xyz`` is not, the rank-1 conformer is used.
            coordinate: Scan coordinate definition.  If ``None``, a default
                bond-length coordinate is used (requires ``structure_xyz``).
            charge: Molecular charge.
            multiplicity: Spin multiplicity.

        Returns:
            A :class:`PesSearchResult` with candidates and output paths.

        Raises:
            PesSearchError: On structured errors (``PES_E_MANIFEST``).
            ValueError: On invalid coordinate or protocol.
            RuntimeError: On QC execution failure.
        """
        out_root = Path(self.output_dir).resolve()
        result_root = out_root / "RESULT"
        pes_dir = result_root / "pes_search"
        pes_dir.mkdir(parents=True, exist_ok=True)

        # Resolve input structure
        manifest_data: dict[str, Any] | None = None
        if confsearch_manifest is not None:
            manifest_data = load_confsearch_manifest(confsearch_manifest)
            if structure_xyz is None:
                _conf_id, structure_xyz = resolve_representative_conformer(
                    confsearch_manifest,
                    manifest_data,
                )

        if structure_xyz is None:
            raise ValueError(
                "PesSearchEngine.run requires either structure_xyz or confsearch_manifest"
            )

        # Build coordinate if not provided
        if coordinate is None:
            coords, symbols = read_xyz(structure_xyz)
            if len(symbols) < 2:
                raise ValueError("Structure must have at least 2 atoms for a scan")
            coordinate = ScanCoordinate(
                kind="distance",
                atoms=(0, 1),
                start=1.0,
                end=3.0,
                n_points=16,
            )

        # Build request
        source = StructureSource(
            source_type="task_artifact",
            artifact_path=str(structure_xyz),
            charge=charge,
            multiplicity=multiplicity,
        )
        request = PesScanRequest(
            mode="bond_length_scan",
            source=source,
            coordinate=coordinate,
            protocol=build_default_protocol(coordinate),
        )

        # Run the scan pipeline
        try:
            scan_result = run_pes_scan(
                request=request,
                output_dir=out_root,
                config=self.config,
                progress_reporter=progress_reporter,
            )
        except (ValueError, RuntimeError) as exc:
            return PesSearchResult(
                status="failed",
                error=str(exc),
                metadata={"engine": "PesSearchEngine"},
            )

        # Extract frames and profile
        frames = scan_result.get("frames", [])
        profile = scan_result.get("profile", {})
        quality = scan_result.get("quality", {})
        ts_recs = scan_result.get("ts_recommendations", [])
        int_recs = scan_result.get("int_recommendations", [])

        # Single source of truth: the persisted scan recommendations are
        # mirrored as engine candidates (no second selection pass).
        path_points = _build_path_points(frames, profile)
        ts_candidates = _candidates_from_recommendations(ts_recs, kind="ts_seed", frames=frames)
        int_candidates = _candidates_from_recommendations(
            int_recs, kind="intermediate_seed", frames=frames
        )

        if progress_reporter is not None:
            progress_reporter.start_stage("finalize")
        # Deliberate: no recommendation materialization here. Guesses live in
        # pes_recommendations.json (audit); RESULT structures come only from
        # the manual review. Do not re-add a copy loop for ts_recs/int_recs.
        scan_dir = Path(str(scan_result.get("scan_dir") or "")).resolve()
        pes_profile_path, result_manifest_path = persist_pes_outputs(
            out_root,
            scan_result=scan_result,
            manifest_source=str(confsearch_manifest) if confsearch_manifest else None,
            status="completed",
        )
        if progress_reporter is not None:
            progress_reporter.complete_stage("finalize")

        # Build search result
        search_result = SearchResult(
            points=path_points,
            candidates=ts_candidates + int_candidates,
            strategy="pes_engine",
            selected_ts_id=(ts_candidates[0].candidate_id if ts_candidates else None),
            selected_int_id=(int_candidates[0].candidate_id if int_candidates else None),
            complete=quality.get("scan_complete", False),
        )

        return PesSearchResult(
            status="complete",
            frames=frames,
            profile=profile,
            quality=quality,
            ts_candidates=ts_candidates,
            int_candidates=int_candidates,
            pes_profile_path=pes_profile_path,
            metadata={
                "engine": "PesSearchEngine",
                "strategy": "pes_engine",
                "search_result": search_result.to_dict(),
                "manifest_source": (str(confsearch_manifest) if confsearch_manifest else None),
                "scan_dir": str(scan_dir),
                "result_manifest_path": str(result_manifest_path),
            },
        )


# ── helpers ─────────────────────────────────────────────────────────────


def _build_path_points(
    frames: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[PathPoint]:
    """Convert scan frames to PathPoint list for candidate selection."""
    raw_hartree = profile.get("raw_hartree", [])
    points: list[PathPoint] = []
    for i, frame in enumerate(frames):
        energy = raw_hartree[i] if i < len(raw_hartree) else None
        energy_key = "sp" if profile.get("energy_source") == "single_point" else "scan"
        points.append(
            PathPoint(
                point_id=f"p{i:03d}",
                progress=float(frame.get("actual_coordinate", 0.0)),
                coordinate_values={
                    "distance": float(frame.get("actual_coordinate", 0.0)),
                },
                energies_hartree={energy_key: energy},
                frame_index=int(frame.get("index", i)),
            )
        )
    return points


def _candidates_from_recommendations(
    recommendations: list[dict[str, Any]],
    *,
    kind: Literal["ts_seed", "intermediate_seed"],
    frames: list[dict[str, Any]],
) -> list[PathCandidate]:
    """Mirror persisted :class:`CandidateRecommendation` dicts as PathCandidates."""
    progress_by_index = {
        int(frame.get("index", i)): float(frame.get("actual_coordinate", 0.0))
        for i, frame in enumerate(frames)
    }
    candidates: list[PathCandidate] = []
    for rec in recommendations:
        frame_index = int(rec.get("frame_index", -1))
        if frame_index < 0:
            continue
        candidates.append(
            PathCandidate(
                candidate_id=str(rec.get("candidate_id", "")),
                kind=kind,
                point_id=f"p{frame_index:03d}",
                reason=str(rec.get("reason") or ""),
                progress=progress_by_index.get(frame_index, 0.0),
                score=float(rec.get("score") or 0.0),
            )
        )
    return candidates


__all__ = [
    "ConfsearchManifestInput",
    "PES_E_MANIFEST",
    "PES_SEARCH_STAGES",
    "PesSearchEngine",
    "PesSearchError",
    "PesSearchResult",
    "load_confsearch_manifest",
    "resolve_representative_conformer",
]
