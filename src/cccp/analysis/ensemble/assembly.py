"""Ensemble assembly / frame ordering (retired-engine closure, T24).

Verbatim relocation of the pure candidate->ensemble conversions and frame
ordering from ``acp.workflows.ensemble`` / ``acp.workflows.energy_shared`` into
the CCCP analysis domain.  The historical ACP path re-exports the same objects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from cccp.analysis.structure.structures import (
    Structure,
    StructureEnsemble,
    StructureRecord,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cccp.backends.censo_backend import CensoRunResult

logger = logging.getLogger(__name__)


def _build_ensemble_from_censo(
    result: CensoRunResult,
    structure: Structure,
) -> StructureEnsemble:
    records: list[StructureRecord] = []
    weights = result.boltzmann_weights()

    for rec in result.records:
        conf_struct = Structure(
            id=f"{structure.id}_{rec.conf_id.lower()}",
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            symbols=rec.symbols,
            coordinates=rec.coordinates,
            metadata={"conf_id": rec.conf_id, "frame_index": rec.frame_index},
        )
        records.append(
            StructureRecord(
                structure=conf_struct,
                energy_hartree=rec.energy,
                free_energy_hartree=rec.gtot,
                weight=weights.get(rec.conf_id, 0.0),
                properties={
                    "gsolv": rec.gsolv,
                    "grrho": rec.grrho,
                    "gtot": rec.gtot,
                },
            )
        )

    records.sort(key=lambda r: r.free_energy_hartree if r.free_energy_hartree is not None else 0.0)
    return StructureEnsemble(records=records)


def _ensemble_xyz_frames(
    candidates: list[dict[str, Any]],
    external_weights: dict[str, float] | None,
    screening_records: list[Any] | None,
) -> list[dict[str, Any]]:
    """Frames to serialize into ``all_conformers.xyz``.

    Workflow 1 (no external table): the refined candidates themselves —
    already 1:1 with the DFT Boltzmann table rows.

    Workflow 2 (external CENSO/xTB table): *every* screened conformer,
    ordered exactly like the table rows in :func:`build_ensemble_summary`
    (weight descending, ``conf_id`` as tie-break). The refined ``conf_id``
    contributes its fine (DFT) geometry and energy while the remaining
    rows contribute their screening geometry, so the file stays 1:1 with
    ``ensemble_thermo.json`` / ``boltzmann_table.json``.

    Records are duck-typed (``.conf_id`` / ``.coordinates`` / ``.symbols``
    / ``.energy`` / ``.gtot``, e.g. :class:`CensoConformerRecord`).  When
    no *screening_records* are available the writer degrades to the old
    candidates-only behaviour so the file never disappears.
    """
    if external_weights is None or not screening_records:
        return list(candidates)

    def record_id(rec: Any) -> str:
        return str(getattr(rec, "conf_id", ""))

    refined = {str(c.get("source", "")): c for c in candidates}
    ordered = sorted(
        screening_records,
        key=lambda r: (-float(external_weights.get(record_id(r), 0.0)), record_id(r)),
    )
    frames: list[dict[str, Any]] = []
    for rank, rec in enumerate(ordered, start=1):
        conf_id = record_id(rec)
        weight = float(external_weights.get(conf_id, 0.0))
        cand = refined.get(conf_id)
        if cand is not None:
            frame = dict(cand)
            frame["rank"] = rank
            frame["weight"] = weight
            frames.append(frame)
            continue
        coordinates = getattr(rec, "coordinates", None)
        symbols = getattr(rec, "symbols", None)
        if coordinates is None or symbols is None:
            logger.warning(
                "Screening record %s has no geometry — skipped in all_conformers.xyz",
                conf_id,
            )
            continue
        frames.append(
            {
                "index": rank - 1,
                "coordinates": np.asarray(coordinates, dtype=float),
                "symbols": list(symbols),
                "energy": getattr(rec, "energy", None),
                "gibbs": getattr(rec, "gtot", None),
                "rank": rank,
                "weight": weight,
                "source": conf_id,
            }
        )
    # A refined conformer whose id is missing from the screening table
    # (frame-based id mismatch) must not silently vanish from the file.
    record_ids = {record_id(rec) for rec in ordered}
    for cand in candidates:
        if str(cand.get("source", "")) not in record_ids:
            frame = dict(cand)
            frame["rank"] = len(frames) + 1
            frames.append(frame)
    return frames


def build_result_ensemble(
    candidates: list[dict[str, Any]],
    structure: Structure,
) -> StructureEnsemble:
    records: list[StructureRecord] = []
    for c in candidates:
        conf_id = c.get("source") or f"{structure.id}_conf{c['index']:03d}"
        conf_struct = Structure(
            id=conf_id,
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            symbols=c["symbols"],
            coordinates=c["coordinates"].tolist()
            if isinstance(c["coordinates"], np.ndarray)
            else c["coordinates"],
            metadata={
                "rank": c.get("rank"),
                "source": c.get("source"),
                # Screening join key consumed by the Confsearch protocols.
                "conf_id": conf_id,
            },
        )
        records.append(
            StructureRecord(
                structure=conf_struct,
                energy_hartree=c.get("energy"),
                free_energy_hartree=c.get("gibbs"),
                weight=c.get("weight"),
                properties={
                    "gibbs_correction": c.get("gibbs_correction"),
                    "g_conc": c.get("g_conc"),
                },
            )
        )
    return StructureEnsemble(records=records)


def censo_record_to_candidate(rec: Any, index: int = 0) -> dict[str, Any]:
    """Convert a CensoConformerRecord (cheap path) into a candidate dict.

    On the ``--no-opt`` path the CENSO gtot already is the final free energy
    (E_SP + δGsolv + G_mRRHO at xTB level).
    """
    return {
        "index": index,
        "coordinates": np.asarray(rec.coordinates),
        "symbols": list(rec.symbols),
        "energy": rec.energy,
        "gibbs": rec.gtot,
        "gibbs_correction": rec.grrho,
        "h_correction": None,
        "u_correction": None,
        "s_total": None,
        "g_conc": None,
        "source": rec.conf_id,
    }


__all__ = [
    "_build_ensemble_from_censo",
    "_ensemble_xyz_frames",
    "build_result_ensemble",
    "censo_record_to_candidate",
]
