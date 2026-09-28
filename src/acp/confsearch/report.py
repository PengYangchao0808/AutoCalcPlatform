"""Final Confsearch report artifacts (C2).

Writes the consolidated final report for one Confsearch run::

    RESULT/confsearch/
    ├── final_report.json        ← schema ``confsearch_final_report_v1``
    └── final_conformers.xyz     ← one frame per entry, rank order

and merge-registers both into ``RESULT/result_manifest.json``
(read → ``add_product`` → ``write``; existing products and header fields are
preserved; re-registration is idempotent by product id). Write or
registration failures RAISE — the report is a required deliverable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.storage.manifest import ProductKind, ResultManifest

from .contracts import PURE_XTB_PROTOCOLS, ConformerEntry, ConfsearchRequest, ProtocolOutcome
from .shared.artifacts import write_json_atomic

logger = logging.getLogger(__name__)

FINAL_REPORT_SCHEMA_VERSION = "confsearch_final_report_v1"
REPORT_JSON_NAME = "final_report.json"
XYZ_NAME = "final_conformers.xyz"


def write_final_report(
    confsearch_dir: Path,
    *,
    request: ConfsearchRequest,
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
) -> tuple[Path, Path]:
    """Write ``final_report.json`` + ``final_conformers.xyz`` under *confsearch_dir*.

    Args:
        confsearch_dir: The ``RESULT/confsearch`` directory; geometry refs in
            the report are confsearch-relative (exactly ``entry.geometry``).
        request: The originating request (protocol/profile/refinement_policy).
        entries: Final conformer entries (already ranked/selected by the engine).
        outcome: Protocol outcome carrying temperature and weight provenance.

    Returns:
        ``(report_json_path, xyz_path)``.

    Raises:
        FileNotFoundError: When no entry has a readable geometry file.
    """
    confsearch_dir = Path(confsearch_dir)
    report_path = confsearch_dir / REPORT_JSON_NAME
    xyz_path = confsearch_dir / XYZ_NAME

    metadata = outcome.workflow_metadata or {}
    payload: dict[str, Any] = {
        "schema_version": FINAL_REPORT_SCHEMA_VERSION,
        "workflow": "Confsearch",
        "protocol": request.protocol,
        "profile": request.profile,
        "refinement_policy": request.refinement_policy,
        "temperature_k": outcome.temperature_k,
        "weight_table": {
            "source": outcome.weight_source,
            "method": outcome.weight_method,
            "population_coverage": outcome.population_coverage,
            "reference": metadata.get("boltzmann_table_json"),
        },
        "conformers": [_conformer_block(entry, outcome, request) for entry in entries],
    }
    for key in ("total_gibbs_hartree", "total_gibbs_kcal_mol"):
        if key in metadata:
            payload[key] = metadata[key]

    write_json_atomic(report_path, payload)
    _write_frames_xyz(xyz_path, confsearch_dir, entries, outcome)
    return report_path, xyz_path


def register_final_report(mol_dir: Path, report_path: Path, xyz_path: Path) -> None:
    """Merge-register the final report artifacts into ``<mol_dir>/RESULT``.

    Reads the existing ``result_manifest.json`` when present (preserving its
    header fields and products) and replaces the two report products by id.
    Missing artifacts or write failures raise — never silently skipped.
    """
    mol_dir = Path(mol_dir)
    for artifact in (report_path, xyz_path):
        if not artifact.is_file():
            raise FileNotFoundError(f"cannot register final report — missing artifact: {artifact}")

    result_dir = mol_dir / "RESULT"
    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError:
        manifest = ResultManifest(task_id="", workflow="Confsearch", status="completed")
    manifest.add_product(
        "confsearch_final_report",
        "Confsearch final report",
        f"confsearch/{REPORT_JSON_NAME}",
        ProductKind.REPORT,
    )
    manifest.add_product(
        "confsearch_final_conformers",
        "Refined conformers (XYZ)",
        f"confsearch/{XYZ_NAME}",
        ProductKind.STRUCTURE,
    )
    manifest.write(result_dir)


def _conformer_block(
    entry: ConformerEntry,
    outcome: ProtocolOutcome,
    request: ConfsearchRequest,
) -> dict[str, Any]:
    if entry.refined:
        energy_kind = "dft"
    else:
        default_kind = "xtb" if request.protocol in PURE_XTB_PROTOCOLS else "censo"
        energy_kind = outcome.energy_kind or default_kind
    return {
        "conf_id": entry.conf_id,
        "source_conf_id": entry.source_conf_id,
        "rank": entry.rank,
        "refined": entry.refined,
        "geometry": entry.geometry,
        "energy_hartree": entry.energy_hartree,
        "free_energy_hartree": entry.free_energy_hartree,
        "energy_kind": energy_kind,
        "relative_energy_kcal": entry.relative_energy_kcal,
        "boltzmann_weight": entry.boltzmann_weight,
        "weight_source": entry.weight_source or outcome.weight_source,
    }


def _frame_title(entry: ConformerEntry, outcome: ProtocolOutcome) -> str:
    weight_source = entry.weight_source or outcome.weight_source
    title = (
        f"{entry.conf_id} G={entry.free_energy_hartree} "
        f"w={entry.boltzmann_weight} src={weight_source}"
    )
    if entry.refined:
        title += " refined"
    return title


def _write_frames_xyz(
    xyz_path: Path,
    confsearch_dir: Path,
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
) -> None:
    """Write one XYZ frame per entry (rank order); skip missing geometries."""
    frames: list[str] = []
    for entry in sorted(entries, key=lambda item: item.rank):
        geometry_ref = (entry.geometry or "").strip()
        source = confsearch_dir / geometry_ref if geometry_ref else None
        if not geometry_ref or source is None or not source.is_file():
            logger.warning(
                "final report: skipping %s — geometry file missing: %s",
                entry.conf_id,
                entry.geometry,
            )
            continue
        lines = source.read_text(encoding="utf-8").splitlines()
        try:
            n_declared = int(lines[0].strip())
        except (ValueError, IndexError):
            logger.warning(
                "final report: skipping %s — unreadable XYZ header in %s",
                entry.conf_id,
                source,
            )
            continue
        atom_lines = lines[2 : 2 + n_declared]
        frame = f"{len(atom_lines)}\n{_frame_title(entry, outcome)}\n" + "\n".join(atom_lines)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(
            f"final_conformers.xyz: no readable geometry files for "
            f"{len(entries)} conformer(s) under {confsearch_dir}"
        )
    xyz_path.write_text("\n".join(frames) + "\n", encoding="utf-8")


__all__ = [
    "FINAL_REPORT_SCHEMA_VERSION",
    "register_final_report",
    "write_final_report",
]
