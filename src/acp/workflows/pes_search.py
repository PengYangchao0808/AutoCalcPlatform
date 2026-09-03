"""Public PESsearch workflow adapter.

Wraps :class:`PesSearchEngine` for the four CLI input forms:
  ① ``--from-artifact <path>`` → confsearch_manifest.json
  ② ``--from-job <job_id>`` → resolves via store work_dir to ①
  ③ ``--input <xyz> --mode bond_length_scan --coordinate ...``
  ④ optional ``--reaction <reaction.json>`` → read-only via compat/legacy
  ⑤ ``--scan-config <request.json>`` → direct bond-length scan (form ⑤,
     scheduler contract: the full request incl. source ships in one file)

Outputs land in ``RESULT/pes_search/``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.calculations.pes.contracts import ScanCoordinate
from acp.calculations.pes.engine import (
    PesSearchEngine,
    PesSearchError,
    PesSearchResult,
)
from acp.calculations.pes.outputs import copy_xyz_atomic, persist_pes_outputs
from acp.calculations.pes.scan import PES_SCAN_STAGES, run_pes_scan
from acp.calculations.progress import ProgressReporter
from acp.core.workflow import WorkflowResult

logger = logging.getLogger(__name__)

# ── error codes ─────────────────────────────────────────────────────────

PES_E_COORD = "PES_E_COORD"
"""Coordinate atom index out of range for the input structure."""

PES_E_STRATEGY = "PES_E_STRATEGY"
"""Unknown PES search strategy."""

# ── frozen stages ───────────────────────────────────────────────────────

PES_SEARCH_STAGES: tuple[str, ...] = PES_SCAN_STAGES

# ── workflow entry ──────────────────────────────────────────────────────


class PesSearchInputError(ValueError):
    """Raised when a PESsearch source or coordinate is unusable."""


def _validate_coordinate_atoms(coord: ScanCoordinate, n_atoms: int) -> None:
    """Validate atom indices are within range of the structure."""
    for atom_idx in coord.atoms:
        if atom_idx < 0 or atom_idx >= n_atoms:
            raise PesSearchInputError(
                f"[{PES_E_COORD}] Coordinate atom index {atom_idx} is out of range "
                f"for structure with {n_atoms} atoms (valid: 0..{n_atoms - 1})"
            )


def _validate_strategy(strategy: str | None) -> str:
    """Validate and return the PES search strategy."""
    valid = ("guided_scan", "reverse_peb", "direct_ts")
    if strategy is None:
        return "guided_scan"
    normalized = strategy.replace("-", "_")
    if normalized not in valid:
        raise PesSearchInputError(
            f"[{PES_E_STRATEGY}] Unknown strategy {strategy!r}; expected one of: {', '.join(valid)}"
        )
    return normalized


def _load_reaction_definition(reaction_path: Path | None) -> dict[str, Any] | None:
    """Load a reaction definition via compat/legacy reader (read-only)."""
    if reaction_path is None:
        return None
    from acp.compat.legacy.manifests import read_reaction_definition

    try:
        return read_reaction_definition(reaction_path)
    except (ValueError, OSError) as exc:
        raise PesSearchInputError(f"Cannot read reaction definition: {exc}") from exc


def _read_n_atoms(xyz_path: Path) -> int:
    """Read atom count from an XYZ file header."""
    text = xyz_path.read_text(encoding="utf-8").strip()
    first_line = text.split("\n", 1)[0].strip()
    try:
        return int(first_line)
    except ValueError:
        raise PesSearchInputError(f"Cannot parse atom count from XYZ header: {first_line!r}")


def run_pes_search(
    *,
    # Form ①/②: manifest-based
    confsearch_manifest: Path | None = None,
    # Form ③: direct XYZ + coordinate
    input_xyz: Path | None = None,
    coordinate: ScanCoordinate | None = None,
    # Common
    strategy: str | None = None,
    reaction: dict[str, Any] | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: str | Path = "./pes_search_out",
    config: dict[str, Any] | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> WorkflowResult:
    """Run PESsearch from one of the four input forms.

    Args:
        confsearch_manifest: Path to a confsearch manifest (forms ①/②).
        input_xyz: Path to input structure XYZ (form ③).
        coordinate: Scan coordinate definition (form ③, required with input_xyz).
        strategy: Search strategy name (guided_scan/reverse_peb/direct_ts).
        reaction: Optional reaction definition dict (form ④).
        charge: Molecular charge.
        multiplicity: Spin multiplicity.
        output_dir: Output directory root.
        config: QC configuration dict.

    Returns:
        A :class:`WorkflowResult` with status and metadata.
    """
    # Validate inputs
    if confsearch_manifest is None and input_xyz is None:
        raise PesSearchInputError(
            "PESsearch requires either --from-artifact/--from-job (manifest) "
            "or --input (direct XYZ)"
        )
    if confsearch_manifest is not None and input_xyz is not None:
        raise PesSearchInputError(
            "PESsearch accepts either --from-artifact/--from-job OR --input, not both"
        )

    # Validate strategy
    validated_strategy = _validate_strategy(strategy)
    _ = validated_strategy  # used for metadata; engine uses defaults

    # Validate coordinate for direct-input mode
    if input_xyz is not None and coordinate is not None:
        n_atoms = _read_n_atoms(input_xyz)
        _validate_coordinate_atoms(coordinate, n_atoms)

    output_root = Path(output_dir).expanduser()

    # Build and run engine
    engine = PesSearchEngine(
        config=dict(config) if config is not None else None,
        output_dir=output_root,
    )

    try:
        result: PesSearchResult = engine.run(
            structure_xyz=input_xyz,
            confsearch_manifest=confsearch_manifest,
            coordinate=coordinate,
            charge=charge,
            multiplicity=multiplicity,
            progress_reporter=progress_reporter,
        )
    except PesSearchError as exc:
        logger.error("PESsearch failed: %s", exc)
        return WorkflowResult(
            status="failed",
            error=str(exc),
            metadata={"error_code": exc.code},
        )
    except (ValueError, RuntimeError) as exc:
        logger.error("PESsearch failed: %s", exc)
        return WorkflowResult(
            status="failed",
            error=str(exc),
        )

    stages = list(PES_SEARCH_STAGES)
    return WorkflowResult(
        status="completed" if result.status == "complete" else "failed",
        stages_completed=stages if result.status == "complete" else [],
        error=result.error,
        metadata={
            "output_dir": str(output_root),
            "ts_candidates": len(result.ts_candidates),
            "int_candidates": len(result.int_candidates),
            "pes_profile_path": str(result.pes_profile_path) if result.pes_profile_path else None,
            "manifest_path": str(output_root / "RESULT" / "pes_search" / "pes_profile.json"),
            "reaction": reaction,
        },
    )


def run_bond_length_scan(
    *,
    scan_request: dict[str, Any],
    output_dir: str | Path = "./pes_scan_out",
    config: dict[str, Any] | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> WorkflowResult:
    """Run a direct bond-length scan from a full request payload (form ⑤).

    This is the scheduler contract: the complete request
    (``mode``/``source``/``coordinate``/``protocol``) ships as one JSON
    document (``--scan-config``), typically written by
    ``JobRunner._build_pessearch_cmd``. The scan primitive
    :func:`run_pes_scan` materialises the structure, runs the relaxed scan,
    the single points, and recommends TS/INT candidates.

    Args:
        scan_request: Full scan request dict (``PesScanRequest``-compatible).
        output_dir: Task output root (``WORK/`` + ``RESULT/`` are created).
        config: QC configuration dict.

    Returns:
        A :class:`WorkflowResult` with status and metadata; results are
        persisted to ``RESULT/pes_search/pes_profile.json`` and candidate
        structures to ``RESULT/structures/``.
    """
    output_root = Path(output_dir).expanduser()
    try:
        scan_result = run_pes_scan(
            request=scan_request,
            output_dir=output_root,
            config=config,
            progress_reporter=progress_reporter,
        )
    except (ValueError, RuntimeError) as exc:
        logger.error("PESsearch bond_length_scan failed: %s", exc)
        return WorkflowResult(status="failed", error=str(exc))

    ts_recs = list(scan_result.get("ts_recommendations", []))
    int_recs = list(scan_result.get("int_recommendations", []))
    frames = list(scan_result.get("frames", []))
    scan_dir = Path(scan_result.get("scan_dir") or "")
    structures_dir = output_root / "RESULT" / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)

    candidate_structures: dict[str, Path] = {}
    for rec in ts_recs + int_recs:
        candidate_id = str(rec.get("candidate_id", ""))
        geometry_path = str(rec.get("geometry_path", ""))
        if not candidate_id or not geometry_path:
            continue
        src = scan_dir / geometry_path if scan_dir else Path(geometry_path)
        if src.is_file():
            destination = structures_dir / f"{candidate_id}.xyz"
            copy_xyz_atomic(src, destination)
            candidate_structures[candidate_id] = destination

    if progress_reporter is not None:
        progress_reporter.start_stage("finalize")
    pes_profile_path, result_manifest_path = persist_pes_outputs(
        output_root,
        scan_result=scan_result,
        candidate_structures=candidate_structures,
        status="completed",
    )
    if progress_reporter is not None:
        progress_reporter.complete_stage("finalize")

    return WorkflowResult(
        status="completed",
        stages_completed=list(PES_SEARCH_STAGES),
        metadata={
            "output_dir": str(output_root),
            "ts_candidates": len(ts_recs),
            "int_candidates": len(int_recs),
            "frames_count": len(frames),
            "pes_profile_path": str(pes_profile_path),
            "manifest_path": str(pes_profile_path),
            "result_manifest_path": str(result_manifest_path),
            "scan_dir": str(scan_dir),
        },
    )


__all__ = [
    "PES_E_COORD",
    "PES_E_STRATEGY",
    "PES_SEARCH_STAGES",
    "PesSearchInputError",
    "run_bond_length_scan",
    "run_pes_search",
]
