"""
Recipe Adapter
==============

Bidirectional conversion helpers between legacy candidate models and
CENSO-style funnel records.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import MutableMapping
import re
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from acp.core.models import StructureEnsemble
from conformer_search.core.candidates import CandidateSet, ConformerCandidate
from conformer_search.ensemble.candidate_set import (
    FunnelRecord,
    FunnelRecordSet,
    records_from_paths as _records_from_paths,
)
from conformer_search.utils.file_io import read_xyz


ADAPTER_METADATA_KEY = "_legacy_candidate_adapter"
DEFAULT_FORWARD_ENERGY_KEYS = {
    "energy": "final_sp",
    "gibbs_energy": "final_gibbs",
    "g_used": "final_gibbs",
}
REVERSE_ENERGY_KEY = "final_sp"
REVERSE_GIBBS_KEY = "final_gibbs"
ENERGY_FALLBACK_KEYS = (
    REVERSE_ENERGY_KEY,
    "r2scan3c_sp",
    "low_cost_dft_sp",
    "xtb_sp",
)
FloatArray = NDArray[np.float64]


def _coerce_float(value: object) -> float | None:
    """Return ``value`` as a float when possible."""
    if isinstance(value, (float, int, np.floating, np.integer)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_int(value: object, default: int = 0) -> int:
    """Return ``value`` as an integer when possible."""
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _adapter_payload(metadata: MutableMapping[str, object]) -> dict[str, object]:
    """Return the adapter payload, creating one when absent."""
    payload = metadata.get(ADAPTER_METADATA_KEY)
    if isinstance(payload, dict):
        return payload

    new_payload: dict[str, object] = {}
    metadata[ADAPTER_METADATA_KEY] = new_payload
    return new_payload


def _public_metadata(metadata: MutableMapping[str, object]) -> dict[str, object]:
    """Return record metadata without the adapter payload."""
    return {
        key: value
        for key, value in metadata.items()
        if key != ADAPTER_METADATA_KEY
    }


def _resolve_forward_energy_keys(
    energy_keys: dict[str, str] | None,
) -> dict[str, str]:
    """Return the forward energy-key mapping."""
    mapping = dict(DEFAULT_FORWARD_ENERGY_KEYS)
    if energy_keys is not None:
        mapping.update(energy_keys)
    return mapping


def _candidate_value(candidate: ConformerCandidate, attribute: str) -> float | None:
    """Return a float candidate attribute value when present."""
    if attribute == "g_used":
        return _coerce_float(candidate.g_used)
    return _coerce_float(getattr(candidate, attribute, None))


def _record_energy(record: FunnelRecord, payload: MutableMapping[str, object]) -> float | None:
    """Return the best available record energy for legacy candidate conversion."""
    for key in ENERGY_FALLBACK_KEYS:
        energy = _coerce_float(record.energies.get(key))
        if energy is not None:
            return energy

    return _coerce_float(payload.get("energy"))


def _record_gibbs(record: FunnelRecord, payload: MutableMapping[str, object]) -> float | None:
    """Return the best available record Gibbs energy for legacy conversion."""
    gibbs = _coerce_float(record.energies.get(REVERSE_GIBBS_KEY))
    if gibbs is not None:
        return gibbs

    return _coerce_float(payload.get("gibbs_energy"))


def _candidate_index(record: FunnelRecord, payload: MutableMapping[str, object]) -> int:
    """Return a legacy candidate index for a funnel record."""
    match = re.search(r"(\d+)$", record.conformer_id)
    if match is not None:
        return int(match.group(1))

    payload_index = payload.get("index")
    if isinstance(payload_index, (int, np.integer)):
        return int(payload_index)
    if isinstance(payload_index, str) and payload_index.isdigit():
        return int(payload_index)

    return int(record.input_order)


def _geometry_from_metadata(
    payload: MutableMapping[str, object],
) -> tuple[FloatArray, list[str]] | None:
    """Return geometry embedded in adapter metadata when available."""
    coordinates = payload.get("coordinates")
    symbols = payload.get("symbols")
    if not isinstance(coordinates, list) or not isinstance(symbols, list):
        return None

    normalized_coordinates: FloatArray = np.asarray(coordinates, dtype=np.float64)
    return normalized_coordinates, [str(symbol) for symbol in symbols]


def _geometry_for_record(record: FunnelRecord) -> tuple[FloatArray, list[str]]:
    """Return geometry for a funnel record from XYZ or embedded metadata."""
    payload = _adapter_payload(record.metadata)

    if record.xyz_path is not None:
        xyz_path = Path(record.xyz_path)
        if xyz_path.exists():
            xyz_coordinates_raw, xyz_symbols = read_xyz(xyz_path)
            xyz_coordinates: FloatArray = np.asarray(
                xyz_coordinates_raw,
                dtype=np.float64,
            )
            return xyz_coordinates, list(xyz_symbols)

        geometry = _geometry_from_metadata(payload)
        if geometry is not None:
            return geometry

        raise FileNotFoundError(f"XYZ file not found: {xyz_path}")

    geometry = _geometry_from_metadata(payload)
    if geometry is not None:
        return geometry

    raise FileNotFoundError(
        f"No geometry available for funnel record {record.conformer_id!r}"
    )


def funnel_record_from_candidate(
    candidate: ConformerCandidate,
    energy_keys: dict[str, str] | None = None,
) -> FunnelRecord:
    """Convert a legacy candidate into a funnel record.

    Args:
        candidate: Legacy conformer candidate.
        energy_keys: Optional mapping from candidate attribute names to
            funnel-record energy keys.

    Returns:
        Funnel record with energy, weight, file, and metadata preserved.
    """
    mapping = _resolve_forward_energy_keys(energy_keys)
    metadata: dict[str, object] = dict(candidate.metadata)
    payload = _adapter_payload(metadata)
    payload.update(
        {
            "index": candidate.index,
            "energy": candidate.energy,
            "gibbs_energy": candidate.gibbs_energy,
            "gibbs_correction": candidate.gibbs_correction,
            "h_correction": candidate.h_correction,
            "u_correction": candidate.u_correction,
            "s_total": candidate.s_total,
            "g_conc": candidate.g_conc,
            "rank": candidate.rank,
            "weight": candidate.weight,
            "coordinates": np.asarray(candidate.coordinates, dtype=float).tolist(),
            "symbols": list(candidate.symbols),
        }
    )

    record = FunnelRecord(
        conformer_id=f"conf_{candidate.index:03d}",
        xyz_path=candidate.source_file,
        input_order=candidate.index,
        source_backend=str(metadata.get("source_backend", "") or ""),
        boltzmann_weight=_coerce_float(candidate.weight),
        metadata=metadata,
    )

    current_geometry_level = metadata.get("current_geometry_level")
    if current_geometry_level is not None:
        record.current_geometry_level = str(current_geometry_level)

    for attribute, energy_key in mapping.items():
        value = _candidate_value(candidate, attribute)
        if value is not None:
            record.energies[energy_key] = value

    return record


def candidate_from_funnel_record(record: FunnelRecord) -> ConformerCandidate:
    """Convert a funnel record into a legacy candidate.

    Args:
        record: Funnel record to convert.

    Returns:
        Legacy conformer candidate.

    Raises:
        FileNotFoundError: If geometry cannot be loaded from ``record.xyz_path``
            and no embedded geometry is available.
    """
    payload = _adapter_payload(record.metadata)
    coordinates, symbols = _geometry_for_record(record)
    metadata = _public_metadata(record.metadata)
    metadata["funnel_conformer_id"] = record.conformer_id
    metadata["funnel_status"] = record.status
    metadata["funnel_input_order"] = record.input_order
    if record.removal_reason is not None:
        metadata["funnel_removal_reason"] = record.removal_reason
    if record.relative_kcal:
        metadata["relative_kcal"] = dict(record.relative_kcal)
    if record.history:
        metadata["funnel_history"] = list(record.history)
    if record.current_geometry_level is not None:
        metadata["current_geometry_level"] = record.current_geometry_level
    if record.source_backend:
        metadata["source_backend"] = record.source_backend

    gibbs_energy = _record_gibbs(record, payload)
    boltzmann_weight = _coerce_float(record.boltzmann_weight)
    if boltzmann_weight is None:
        boltzmann_weight = _coerce_float(payload.get("weight"))

    return ConformerCandidate(
        index=_candidate_index(record, payload),
        coordinates=coordinates,
        symbols=symbols,
        energy=_record_energy(record, payload) or 0.0,
        weight=boltzmann_weight if boltzmann_weight is not None else 0.0,
        source_file=record.xyz_path,
        rank=_coerce_int(payload.get("rank", 0)),
        metadata=metadata,
        gibbs_energy=gibbs_energy,
        gibbs_correction=_coerce_float(payload.get("gibbs_correction")),
        h_correction=_coerce_float(payload.get("h_correction")),
        u_correction=_coerce_float(payload.get("u_correction")),
        s_total=_coerce_float(payload.get("s_total")),
        g_conc=_coerce_float(payload.get("g_conc")),
    )


def funnel_records_from_candidate_set(
    candidate_set: CandidateSet,
    energy_keys: dict[str, str] | None = None,
) -> FunnelRecordSet:
    """Convert a legacy candidate set into a funnel record set.

    Args:
        candidate_set: Legacy conformer candidate set.
        energy_keys: Optional mapping from candidate attribute names to
            funnel-record energy keys.

    Returns:
        Funnel record set preserving candidate order and set-level metadata.
    """
    records: list[FunnelRecord] = []
    for input_order, candidate in enumerate(candidate_set):
        record = funnel_record_from_candidate(candidate, energy_keys=energy_keys)
        record.input_order = input_order
        payload = _adapter_payload(record.metadata)
        payload["candidate_set_temperature"] = candidate_set.temperature
        payload["candidate_set_reference_energy"] = candidate_set.reference_energy
        payload["weight"] = candidate.weight
        records.append(record)

    return FunnelRecordSet(records)


def candidate_set_from_funnel_records(records: FunnelRecordSet) -> CandidateSet:
    """Convert a funnel record set into a legacy candidate set.

    Args:
        records: Funnel record set to convert.

    Returns:
        Legacy candidate set.
    """
    candidates = [candidate_from_funnel_record(record) for record in records]

    temperature = 298.15
    reference_energy: float | None = None

    for record in records:
        payload = _adapter_payload(record.metadata)
        payload_temperature = _coerce_float(payload.get("candidate_set_temperature"))
        if payload_temperature is not None:
            temperature = payload_temperature
            break

    for record in records:
        payload = _adapter_payload(record.metadata)
        payload_reference = _coerce_float(payload.get("candidate_set_reference_energy"))
        if payload_reference is not None:
            reference_energy = payload_reference
            break

    if reference_energy is None:
        available_energies = [
            energy
            for record in records
            if (energy := _record_energy(record, _adapter_payload(record.metadata))) is not None
        ]
        if available_energies:
            reference_energy = min(available_energies)

    return CandidateSet(
        candidates=candidates,
        reference_energy=reference_energy,
        temperature=temperature,
    )


def funnel_records_from_structure_ensemble(
    ensemble: StructureEnsemble,
) -> FunnelRecordSet:
    """Convert a structure ensemble into a funnel record set.

    Args:
        ensemble: ACP structure ensemble.

    Returns:
        Funnel record set converted via the legacy candidate-set bridge.
    """
    return funnel_records_from_candidate_set(ensemble.to_candidate_set())


def structure_ensemble_from_funnel_records(
    records: FunnelRecordSet,
) -> StructureEnsemble:
    """Convert a funnel record set into a structure ensemble.

    Args:
        records: Funnel record set.

    Returns:
        ACP structure ensemble converted via the legacy candidate-set bridge.
    """
    return candidate_set_from_funnel_records(records).to_structure_ensemble()


def funnel_records_from_paths(
    paths: list[Path],
    energy_key: str = "xtb_sp",
    energies: list[float] | None = None,
) -> FunnelRecordSet:
    """Create an initial funnel record set from XYZ file paths.

    Args:
        paths: XYZ file paths.
        energy_key: Funnel-record energy key used for ``energies``.
        energies: Optional per-path energies.

    Returns:
        Funnel record set created by the shared convenience constructor.
    """
    return _records_from_paths(paths=paths, energy_key=energy_key, energies=energies)


__all__ = [
    "funnel_record_from_candidate",
    "candidate_from_funnel_record",
    "funnel_records_from_candidate_set",
    "candidate_set_from_funnel_records",
    "funnel_records_from_structure_ensemble",
    "structure_ensemble_from_funnel_records",
    "funnel_records_from_paths",
]
