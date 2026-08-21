# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedImport=false
"""Native CENSO-lite ensemble provider for mechanism S1 stable states."""

from __future__ import annotations

import logging
import math
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from acp.backends import get_backend
from acp.backends.base import QCResult, SinglePointCalculator
from acp.backends.batch import BatchSpFrameResult, batch_single_point
from acp.chem.embedding import smiles_to_xyz
from acp.core.models import (
    HARTREE_TO_KCAL,
    Structure,
    StructureEnsemble,
    StructureRecord,
)
from acp.mechanism._helpers import next_sequence, write_json_atomic
from acp.mechanism.models import StableState
from acp.mechanism.primitives import DedupRecord, TorsionAwareDeduplicator
from acp.workflows.energy_shared import boltzmann_weights
from cccp.config import load_config
from cccp.utils.file_io import read_xyz_multiframe, write_xyz

logger = logging.getLogger(__name__)

PROVIDER_NAME = "acp-native-censo-lite"
DEFAULT_PROFILE_ID = "rph-censo-lite"
SCHEMA_VERSION = "s1_censo_light_ranking_v4"
TEMPERATURE_K = 298.15
GAS_CONSTANT_KCAL_MOL_K = 0.001987204259
WINDOW_KCAL = 2.5
MIN_KEEP = 3
MAX_KEEP = 10
FloatArray = NDArray[np.float64]


class NativeCensoLiteProvider:
    """CREST → xTB dedup → B97-3c ranking provider for stable states."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        work_root: Path | str | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config) if config is not None else load_config()
        self.work_root: Path = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.calls: int = next_sequence(self.work_root, "*/ensemble_*")

    def generate(self, stable_state: StableState, profile: Any) -> StructureEnsemble:
        """Generate a ranked CENSO-lite style ensemble for one stable state."""
        self.calls += 1
        run_dir = self.work_root / stable_state.state_id / f"ensemble_{self.calls:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved_input = _resolve_ensemble_input(stable_state)
        start_xyz = _materialize_start_xyz(resolved_input, stable_state, run_dir)
        profile_id = _profile_id(profile)

        crest = cast(Any, get_backend("crest")(self.config))
        try:
            ensemble_xyz = Path(
                crest.search(
                    start_xyz,
                    charge=stable_state.charge,
                    multiplicity=stable_state.multiplicity,
                    output_dir=run_dir / "crest",
                )
            )
        except RuntimeError as exc:
            raise RuntimeError(f"CREST unavailable for native censo-lite: {exc}") from exc

        frames, symbols = _load_multiframe_xyz(ensemble_xyz)
        if not frames:
            raise RuntimeError("CREST native censo-lite search returned no conformer frames")

        xtb = get_backend("xtb")(self.config)
        xtb_energies = _xtb_frame_energies(
            xtb,
            frames,
            symbols,
            charge=stable_state.charge,
            multiplicity=stable_state.multiplicity,
            output_dir=run_dir / "xtb_sp",
        )

        deduplicator = TorsionAwareDeduplicator()
        dedup_records = _annotated_records(
            deduplicator,
            stable_state,
            resolved_input,
            frames,
            symbols,
            xtb_energies,
        )
        unique_records = deduplicator.deduplicate(dedup_records)
        if not unique_records:
            raise RuntimeError("Native censo-lite deduplication removed every conformer")

        orca = cast(SinglePointCalculator, cast(object, get_backend("orca")(self.config)))
        sp_result = batch_single_point(
            orca,
            [np.asarray(record.coordinates, dtype=float) for record in unique_records],
            list(symbols),
            charge=stable_state.charge,
            multiplicity=stable_state.multiplicity,
            output_dir=run_dir / "sp",
            method="B97-3c",
            config=self.config,
        )
        failed_frames = [
            f"frame {record.index}: {record.error_message or 'unknown error'}"
            for record in sp_result.records
            if not record.success
        ]
        if failed_frames:
            raise RuntimeError(
                "B97-3c single-point failed for native censo-lite conformers: "
                + "; ".join(failed_frames)
            )
        sp_by_index = {record.index: record for record in sp_result.records}

        provenance = {
            "provider": PROVIDER_NAME,
            "profile_id": profile_id,
            "schema_version": SCHEMA_VERSION,
            "state_id": stable_state.state_id,
            "input": resolved_input,
            "source_ensemble_xyz": str(ensemble_xyz),
            "xtb_cross_validated": False,
        }

        candidate_data = _build_candidate_data(
            unique_records,
            sp_by_index,
            xtb,
            symbols,
            stable_state=stable_state,
            run_dir=run_dir,
            provenance=provenance,
        )
        selected_candidates = _select_candidates(candidate_data)
        selected_weights = _selected_weights(selected_candidates)

        conformer_dir = run_dir / "conformers"
        conformer_dir.mkdir(parents=True, exist_ok=True)

        min_selected_energy = min(
            candidate["electronic_energy_hartree"] for candidate in selected_candidates
        )
        min_selected_gibbs = min(
            candidate["gibbs_free_energy_hartree"] for candidate in selected_candidates
        )

        manifest_candidates: list[dict[str, Any]] = []
        ensemble_records: list[StructureRecord] = []
        for rank, (candidate, weight) in enumerate(
            zip(selected_candidates, selected_weights, strict=True),
            start=1,
        ):
            xyz_path = conformer_dir / f"{candidate['id']}.xyz"
            write_xyz(
                xyz_path,
                np.asarray(candidate["coordinates"], dtype=float),
                list(symbols),
                title=(f"{candidate['id']} | G={candidate['gibbs_free_energy_hartree']:.10f} Eh"),
            )

            relative_energy_kcal = (
                float(candidate["electronic_energy_hartree"]) - float(min_selected_energy)
            ) * HARTREE_TO_KCAL
            relative_free_energy_kcal = (
                float(candidate["gibbs_free_energy_hartree"]) - float(min_selected_gibbs)
            ) * HARTREE_TO_KCAL
            properties: dict[str, object] = {
                "rank": rank,
                "candidate_id": candidate["id"],
                "boltzmann_population": float(weight),
                "degeneracy": int(candidate["degeneracy"]),
                "relative_energy_kcal": float(relative_energy_kcal),
                "relative_free_energy_kcal": float(relative_free_energy_kcal),
                "xtb_mrrho_thermal_correction_hartree": candidate[
                    "xtb_mrrho_thermal_correction_hartree"
                ],
                "provenance": dict(provenance),
            }
            structure = Structure(
                id=str(candidate["id"]),
                charge=stable_state.charge,
                multiplicity=stable_state.multiplicity,
                symbols=list(symbols),
                coordinates=np.asarray(candidate["coordinates"], dtype=float),
                metadata={
                    "state_id": stable_state.state_id,
                    "frame_index": int(candidate["frame_index"]),
                    "merged_from": list(candidate["merged_from"]),
                    "provenance": dict(provenance),
                },
            )
            ensemble_records.append(
                StructureRecord(
                    structure=structure,
                    energy_hartree=float(candidate["electronic_energy_hartree"]),
                    free_energy_hartree=float(candidate["gibbs_free_energy_hartree"]),
                    weight=float(weight),
                    properties=properties,
                    files={"source": xyz_path},
                )
            )
            manifest_candidates.append(
                {
                    "id": candidate["id"],
                    "xyz": str(xyz_path),
                    "electronic_energy_hartree": float(candidate["electronic_energy_hartree"]),
                    "gibbs_free_energy_hartree": float(candidate["gibbs_free_energy_hartree"]),
                    "boltzmann_population": float(weight),
                    "degeneracy": int(candidate["degeneracy"]),
                    "relative_energy_kcal": float(relative_energy_kcal),
                    "relative_free_energy_kcal": float(relative_free_energy_kcal),
                    "xtb_mrrho_thermal_correction_hartree": candidate[
                        "xtb_mrrho_thermal_correction_hartree"
                    ],
                }
            )

        selected_id = str(selected_candidates[0]["id"])
        selected_xyz = conformer_dir / f"{selected_id}.xyz"
        ensemble_thermodynamics = {
            "total_gibbs_hartree": _ensemble_total_gibbs(
                [float(candidate["gibbs_free_energy_hartree"]) for candidate in selected_candidates]
            ),
            "temperature_k": TEMPERATURE_K,
        }
        manifest_path = run_dir / "manifest.json"
        write_json_atomic(
            manifest_path,
            {
                "schema_version": SCHEMA_VERSION,
                "candidates": manifest_candidates,
                "selected": selected_id,
                "ensemble_thermodynamics": ensemble_thermodynamics,
            },
        )

        return StructureEnsemble(
            records=ensemble_records,
            temperature=TEMPERATURE_K,
            metadata={
                "provider": PROVIDER_NAME,
                "profile_id": profile_id,
                "manifest_path": str(manifest_path),
                "selected_id": selected_id,
                "selected_xyz": str(selected_xyz),
                "ensemble_thermodynamics": ensemble_thermodynamics,
                "provenance": provenance,
                "xtb_cross_validated": False,
            },
        )


def _resolve_ensemble_input(stable_state: StableState) -> str:
    for key in ("smiles", "input_smiles", "input", "source_input", "structure_input"):
        value = stable_state.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(stable_state.canonical_geometry.path)


def _materialize_start_xyz(resolved_input: str, stable_state: StableState, run_dir: Path) -> Path:
    start_xyz = run_dir / "start.xyz"
    if _looks_like_smiles(resolved_input):
        _ = start_xyz.write_text(
            smiles_to_xyz(
                resolved_input,
                comment=f"state_id={stable_state.state_id} | source=smiles",
            ),
            encoding="utf-8",
        )
        return start_xyz

    source_path = Path(resolved_input).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Native censo-lite input XYZ not found: {source_path}")
    _ = shutil.copyfile(source_path, start_xyz)
    return start_xyz


def _looks_like_smiles(value: str) -> bool:
    lowered = value.lower()
    return "/" not in value and "\\" not in value and ".xyz" not in lowered


def _load_multiframe_xyz(path: Path) -> tuple[list[FloatArray], list[str]]:
    coordinates, symbols = read_xyz_multiframe(path)
    symbol_list = list(symbols)
    if not symbol_list:
        return [], []

    coordinate_array = np.asarray(coordinates, dtype=float)
    n_atoms = len(symbol_list)
    if coordinate_array.ndim != 2 or coordinate_array.shape[1] != 3:
        raise ValueError(f"Unexpected multiframe XYZ shape for {path}: {coordinate_array.shape}")
    if coordinate_array.shape[0] % n_atoms != 0:
        raise ValueError(f"XYZ frame atom count mismatch for {path}")

    n_frames = coordinate_array.shape[0] // n_atoms
    frames = [
        np.asarray(coordinate_array[index * n_atoms : (index + 1) * n_atoms], dtype=float)
        for index in range(n_frames)
    ]
    return frames, symbol_list


def _xtb_frame_energies(
    backend: Any,
    frames: Sequence[FloatArray],
    symbols: Sequence[str],
    *,
    charge: int,
    multiplicity: int,
    output_dir: Path,
) -> list[float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    energies: list[float] = []
    for index, frame in enumerate(frames):
        result = cast(
            QCResult,
            backend.single_point(
                np.asarray(frame, dtype=float),
                list(symbols),
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
                output_name=f"xtb_frame_{index:04d}",
            ),
        )
        if not result.success or result.energy is None:
            error_message = result.error_message or "missing energy"
            raise RuntimeError(
                f"xTB single-point failed for native censo-lite frame {index}: {error_message}"
            )
        energies.append(float(result.energy))
    return energies


def _annotated_records(
    deduplicator: TorsionAwareDeduplicator,
    stable_state: StableState,
    resolved_input: str,
    frames: Sequence[FloatArray],
    symbols: Sequence[str],
    xtb_energies: Sequence[float],
) -> list[DedupRecord]:
    mol = _dedup_molecule(stable_state, resolved_input)
    records: list[DedupRecord] = []
    for index, (frame, energy) in enumerate(zip(frames, xtb_energies, strict=True)):
        record_id = f"conf_{index:04d}"
        records.append(
            deduplicator.annotate(
                mol,
                record_id,
                np.asarray(frame, dtype=float),
                list(symbols),
                score=float(energy),
                metadata={
                    "frame_index": index,
                    "xtb_energy_hartree": float(energy),
                    "source_conformer_id": record_id,
                },
            )
        )
    return records


def _dedup_molecule(stable_state: StableState, resolved_input: str) -> Any | None:
    smiles_value = None
    for key in ("smiles", "input_smiles", "input", "source_input", "structure_input"):
        value = stable_state.metadata.get(key)
        if isinstance(value, str) and value.strip() and _looks_like_smiles(value.strip()):
            smiles_value = value.strip()
            break
    if smiles_value is None and _looks_like_smiles(resolved_input):
        smiles_value = resolved_input
    if smiles_value is None:
        return None

    try:
        from rdkit import Chem
    except ImportError:
        logger.warning("RDKit unavailable; falling back to geometry-only native censo-lite dedup")
        return None

    mol = Chem.MolFromSmiles(smiles_value)
    if not mol:
        logger.warning(
            "Invalid SMILES %r for native censo-lite dedup; using geometry fallback",
            smiles_value,
        )
        return None
    return Chem.AddHs(mol)


def _build_candidate_data(
    unique_records: Sequence[DedupRecord],
    sp_by_index: Mapping[int, BatchSpFrameResult],
    xtb_backend: Any,
    symbols: Sequence[str],
    *,
    stable_state: StableState,
    run_dir: Path,
    provenance: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(unique_records):
        sp_record = sp_by_index[index]
        if sp_record.energy_hartree is None:
            raise RuntimeError(f"Missing B97-3c energy for native censo-lite frame {index}")

        xtb_energy = _float_or_none(record.metadata.get("xtb_energy_hartree"))
        mrrho_gibbs = _mrrho_gibbs(
            xtb_backend,
            np.asarray(record.coordinates, dtype=float),
            symbols,
            charge=stable_state.charge,
            multiplicity=stable_state.multiplicity,
            output_dir=run_dir / "mrrho" / record.record_id,
            record_id=record.record_id,
        )
        correction = None
        if mrrho_gibbs is not None and xtb_energy is not None:
            correction = float(mrrho_gibbs - xtb_energy)

        electronic_energy = float(sp_record.energy_hartree)
        gibbs_energy = electronic_energy if correction is None else electronic_energy + correction
        merge_count = _safe_int(record.metadata.get("merge_count"), default=1)
        degeneracy = max(_safe_int(record.metadata.get("degeneracy"), default=1), merge_count, 1)
        merged_from_raw = record.metadata.get("merged_from")
        if isinstance(merged_from_raw, list):
            merged_from = [str(item) for item in merged_from_raw]
        else:
            merged_from = [record.record_id]

        candidates.append(
            {
                "id": record.record_id,
                "frame_index": _safe_int(record.metadata.get("frame_index"), default=index),
                "coordinates": np.asarray(record.coordinates, dtype=float),
                "electronic_energy_hartree": electronic_energy,
                "gibbs_free_energy_hartree": float(gibbs_energy),
                "xtb_mrrho_thermal_correction_hartree": correction,
                "xtb_energy_hartree": xtb_energy,
                "degeneracy": degeneracy,
                "merged_from": merged_from,
                "metadata": dict(record.metadata),
                "provenance": dict(provenance),
            }
        )
    return candidates


def _mrrho_gibbs(
    backend: Any,
    coordinates: FloatArray,
    symbols: Sequence[str],
    *,
    charge: int,
    multiplicity: int,
    output_dir: Path,
    record_id: str,
) -> float | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = cast(
            QCResult,
            backend.enso_thermo(
                coordinates,
                list(symbols),
                charge=charge,
                multiplicity=multiplicity,
                output_dir=output_dir,
            ),
        )
    except Exception as exc:
        logger.warning("Native censo-lite mRRHO failed for %s: %s", record_id, exc)
        return None

    if not result.success:
        logger.warning(
            "Native censo-lite mRRHO failed for %s: %s",
            record_id,
            result.error_message or "unknown error",
        )
        return None
    if result.gibbs is not None:
        return float(result.gibbs)
    thermo_payload = result.metadata.get("thermo")
    if isinstance(thermo_payload, Mapping):
        g_total = _float_or_none(thermo_payload.get("g_total"))
        if g_total is not None:
            return g_total
    if result.energy is not None:
        return float(result.energy)
    logger.warning("Native censo-lite mRRHO returned no Gibbs energy for %s", record_id)
    return None


def _select_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda candidate: (candidate["gibbs_free_energy_hartree"], candidate["id"]),
    )
    if not ordered:
        raise RuntimeError("Native censo-lite produced no candidate data")

    minimum_gibbs = float(ordered[0]["gibbs_free_energy_hartree"])
    within_window = [
        candidate
        for candidate in ordered
        if (float(candidate["gibbs_free_energy_hartree"]) - minimum_gibbs) * HARTREE_TO_KCAL
        <= WINDOW_KCAL
    ]
    if len(within_window) >= MIN_KEEP:
        selected = within_window
    else:
        selected = ordered[: min(MIN_KEEP, len(ordered))]
    return selected[:MAX_KEEP]


def _selected_weights(selected_candidates: Sequence[dict[str, Any]]) -> list[float]:
    if not selected_candidates:
        return []
    return [
        float(weight)
        for weight in boltzmann_weights(
            [float(candidate["gibbs_free_energy_hartree"]) for candidate in selected_candidates],
            TEMPERATURE_K,
        )
    ]


def _ensemble_total_gibbs(gibbs_values: Sequence[float]) -> float:
    minimum = min(float(value) for value in gibbs_values)
    rt = GAS_CONSTANT_KCAL_MOL_K * TEMPERATURE_K / HARTREE_TO_KCAL
    partition = sum(math.exp(-(float(value) - minimum) / rt) for value in gibbs_values)
    return float(minimum - rt * math.log(partition))


def _profile_id(profile: Any) -> str:
    if isinstance(profile, Mapping):
        for key in ("profile_id", "id", "name"):
            value = profile.get(key)
            if value is not None:
                return str(value)
        return DEFAULT_PROFILE_ID
    for attribute in ("profile_id", "id", "name"):
        value = getattr(profile, attribute, None)
        if value is not None:
            return str(value)
    return DEFAULT_PROFILE_ID


def _safe_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


__all__ = ["NativeCensoLiteProvider"]
