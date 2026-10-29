"""GFN1 energy-window filter (retired-engine closure, T24).

Verbatim relocation of the pure ``acp.workflows.xtbmd_censo_energy`` energy
filter into the CCCP analysis domain.  The historical ACP path re-exports the
same objects.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cccp.analysis.xtbmd.trajectory import (
    _match_isomers_frame,
    _parse_title_energy_hartree,
    _read_trajectory,
)
from cccp.utils.constants import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)


_SIDECAR_MATCH_TOLERANCE_ANGSTROM = 0.5


@dataclass(frozen=True)
class EnergyFilterResult:
    """Outcome of the GFN1 energy-window filter (doc §8.3)."""

    ensemble_xyz: Path
    ensemble_energies_json: Path
    n_total: int
    n_after_filter: int


def _filter_energy_window(
    cluster_xyz: Path,
    isomers_xyz: Path,
    isomers_energies_json: Path,
    *,
    ewin: float,
    work_dir: Path,
) -> EnergyFilterResult:
    """Filter the ISOSTAT cluster to the GFN1 relative-energy window.

    Reads the cluster representatives plus their GFN1 energies — the
    sidecar (keyed by isomers frame index) is the primary channel and the
    XYZ title the compatibility channel (doc §8.3).  Each cluster frame is
    matched geometrically to its nearest isomers frame (aligned RMSD; ISOSTAT
    representatives are members of the isomers set) and the sidecar energy
    is looked up; frames without a sidecar match fall back to their title
    energy.  Frames within ``ewin`` kcal/mol of the cluster minimum are
    written to ``ensemble_xyz`` with rewritten titles (GFN1 Hartree energy
    embedded, first float — the censo-zero passthrough parses exactly this)
    plus the rebuilt ``ensemble_energies.json`` (cluster order, original
    frame index preserved).

    Raises:
        RuntimeError: No cluster frames, no GFN1 energy recoverable for a
            frame, or an empty filtered ensemble (fail-fast, E5).
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    try:
        symbols, cluster_titles, cluster_frames = _read_trajectory(cluster_xyz)
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            f"energy_filter: ISOSTAT cluster unreadable/empty ({exc}) — "
            f"relax --edis/--gdis or check the batch-optimization output"
        ) from exc
    if not cluster_frames:
        raise RuntimeError(
            "energy_filter: ISOSTAT produced 0 cluster representatives — "
            "relax --edis/--gdis or check the batch-optimization output"
        )

    iso_symbols, iso_titles, iso_frames = _read_trajectory(isomers_xyz)
    if len(symbols) != len(iso_symbols):
        raise RuntimeError(
            f"energy_filter: atom count mismatch between {cluster_xyz} "
            f"({len(symbols)}) and {isomers_xyz} ({len(iso_symbols)})"
        )

    try:
        sidecar = json.loads(Path(isomers_energies_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "energy_filter: isomers energy sidecar unreadable (%s) — "
            "falling back to the title channel",
            exc,
        )
        sidecar = {}
    sidecar_map: dict[int, float] = {}
    for entry in sidecar.get("frames", []):
        if entry.get("status") == "ok" and entry.get("energy") is not None:
            try:
                sidecar_map[int(entry["frame"])] = float(entry["energy"])
            except (TypeError, ValueError):
                continue

    iso_stack = np.stack(iso_frames) if iso_frames else None
    records: list[tuple[int, int | None, float]] = []
    for i, (frame, title) in enumerate(zip(cluster_frames, cluster_titles)):
        nearest: int | None = None
        energy: float | None = None
        if iso_stack is not None:
            nearest = _match_isomers_frame(
                frame,
                iso_stack,
                _SIDECAR_MATCH_TOLERANCE_ANGSTROM,
            )
            if nearest is not None:
                # source_frame is recorded only for a geometrically verified
                # match; title-fallback frames keep it None (unknown).
                energy = sidecar_map.get(nearest)
        if energy is None:
            energy = _parse_title_energy_hartree(title)
        if energy is None:
            raise RuntimeError(
                f"energy_filter: no GFN1 energy recoverable for cluster frame {i} — "
                f"sidecar lookup and title parsing both failed"
            )
        records.append((i, nearest, float(energy)))

    energies = np.asarray([energy for _, _, energy in records], dtype=np.float64)
    relative_kcal = (energies - energies.min()) * HARTREE_TO_KCAL
    kept = [
        (i, source_frame, energy, rel)
        for (i, source_frame, energy), rel in zip(records, relative_kcal)
        if rel <= ewin
    ]
    if not kept:
        raise RuntimeError(
            f"energy_filter: 0 conformers within the {ewin} kcal/mol energy window "
            f"(cluster minimum {energies.min():.8f} Ha) — raise --ewin or relax "
            f"--edis/--gdis"
        )

    ensemble_xyz = work / "ensemble_xyz"
    # NOTE: no blank lines between frames — the censo-zero passthrough
    # (xtb_passthrough_result) scans frame blocks by strict offset.
    with open(ensemble_xyz, "w", encoding="utf-8") as f:
        for j, (i, _source_frame, energy, _rel) in enumerate(kept):
            f.write(f"{len(symbols)}\n")
            f.write(f"Frame {i:04d} E={energy:.8f} Hartree\n")
            for sym, coord in zip(symbols, cluster_frames[i]):
                f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

    ensemble_energies_json = work / "ensemble_energies.json"
    ensemble_energies_json.write_text(
        json.dumps(
            {
                "units": "hartree",
                "ewin_kcal_mol": float(ewin),
                "frames": [
                    {
                        "frame": j,
                        "source_frame": source_frame,
                        "energy": energy,
                        "status": "ok",
                    }
                    for j, (_i, source_frame, energy, _rel) in enumerate(kept)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info(
        "energy_filter: %d/%d cluster conformers within %.1f kcal/mol window → %s",
        len(kept),
        len(records),
        ewin,
        ensemble_xyz,
    )
    return EnergyFilterResult(
        ensemble_xyz=ensemble_xyz,
        ensemble_energies_json=ensemble_energies_json,
        n_total=len(records),
        n_after_filter=len(kept),
    )


__all__ = [
    "EnergyFilterResult",
    "_filter_energy_window",
]
