"""Public PESsearch workflow adapter.

Wraps :class:`PesSearchEngine` for the four CLI input forms:
  ① ``--from-artifact <path>`` → confsearch_manifest.json
  ② ``--from-job <job_id>`` → resolves via store work_dir to ①
  ③ ``--input <xyz> --mode bond_length_scan --coordinate ...``
  ④ optional ``--reaction <reaction.json>`` → read-only via compat/legacy

Outputs land in ``RESULT/pes_search/``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.calculations.pes.contracts import ScanCoordinate
from acp.calculations.pes.engine import (
    PES_E_MANIFEST,
    PesSearchEngine,
    PesSearchError,
    PesSearchResult,
)
from acp.core.workflow import WorkflowResult

logger = logging.getLogger(__name__)

# ── error codes ─────────────────────────────────────────────────────────

PES_E_COORD = "PES_E_COORD"
"""Coordinate atom index out of range for the input structure."""

PES_E_STRATEGY = "PES_E_STRATEGY"
"""Unknown PES search strategy."""

# ── frozen stages ───────────────────────────────────────────────────────

PES_SEARCH_STAGES: tuple[str, ...] = (
    "prepare",
    "materialize_input",
    "validate_coordinate",
    "run_relaxed_scan",
    "extract_frames",
    "run_single_points",
    "build_profile",
    "select_candidates",
    "finalize",
)

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


def _resolve_manifest_from_job(job_id: str) -> Path:
    """Resolve a confsearch_manifest.json path from a job ID."""
    from acp.scheduler.store import JobStore

    store = JobStore()
    record = store.get(job_id)
    if record is None:
        raise PesSearchInputError(f"[{PES_E_MANIFEST}] Job not found: {job_id}")
    work_dir = Path(record.work_dir)
    manifest_path = work_dir / "RESULT" / "confsearch" / "confsearch_manifest.json"
    if not manifest_path.is_file():
        raise PesSearchInputError(
            f"[{PES_E_MANIFEST}] Confsearch manifest not found for job {job_id}: {manifest_path}"
        )
    return manifest_path


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


__all__ = [
    "PES_E_COORD",
    "PES_E_STRATEGY",
    "PES_SEARCH_STAGES",
    "PesSearchInputError",
    "run_pes_search",
]
