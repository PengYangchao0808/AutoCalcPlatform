# pyright: reportAny=false, reportExplicitAny=false, reportImplicitStringConcatenation=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedImport=false
"""xTB-fast stable-state ensemble provider for the mechanism study layer."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.core.models import HARTREE_TO_KCAL, Structure, StructureEnsemble, StructureRecord
from acp.mechanism._helpers import backend_name as _backend_name
from acp.mechanism._helpers import next_sequence as _next_sequence
from acp.mechanism._helpers import resolve_backend as _resolve_backend
from acp.mechanism._helpers import state_geometry as _state_geometry
from acp.mechanism.models import Provenance, StableState
from acp.workflows.energy_shared import boltzmann_weights as _boltzmann_weights
from cccp.config import load_config
from cccp.utils.file_io import read_xyz_multiframe, write_xyz
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

_TITLE_FLOAT_RE = re.compile(r"[-+]?\d+\.\d+")
_STRATEGY_VERSION = "1.0"


class XtbFastEnsembleProvider:
    """CREST + xTB ranking/dedup provider for ``xtb-fast`` S1 mode."""

    def __init__(
        self,
        crest_backend: str | Any = "crest",
        *,
        config: dict[str, Any] | None = None,
        work_root: Path | str | None = None,
        crest_energy_window_kcal: float = 6.0,
        energy_window_kcal: float = 6.0,
        rmsd_threshold: float = 0.25,
        temperature: float = 298.15,
        crest_search_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._crest_backend_spec = crest_backend
        self.config = dict(config) if config is not None else load_config()
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        self.crest_energy_window_kcal = crest_energy_window_kcal
        self.energy_window_kcal = energy_window_kcal
        self.rmsd_threshold = rmsd_threshold
        self.temperature = temperature
        self.crest_search_kwargs = dict(crest_search_kwargs or {})
        self.calls = _next_sequence(self.work_root, "*/ensemble_*")
        self._crest_backend_instance: Any | None = None

    def generate(self, stable_state: StableState, profile: Any) -> StructureEnsemble:
        """Generate a deduplicated xTB-ranked ensemble for one stable state."""
        self.calls += 1
        coordinates, symbols = _state_geometry(stable_state)
        state_dir = self.work_root / stable_state.state_id / f"ensemble_{self.calls:03d}"
        state_dir.mkdir(parents=True, exist_ok=True)
        initial_xyz = state_dir / f"{stable_state.state_id}.xyz"
        write_xyz(
            initial_xyz,
            coordinates,
            symbols,
            title=f"xtb-fast input for {stable_state.state_id}",
        )

        crest_backend = self._crest_backend()
        crest_energy_window = _profile_float(profile, "crest_ewin", self.crest_energy_window_kcal)
        ensemble_xyz = crest_backend.search(
            initial_xyz=initial_xyz,
            charge=stable_state.charge,
            multiplicity=stable_state.multiplicity,
            output_dir=state_dir,
            energy_window=crest_energy_window,
            **self.crest_search_kwargs,
        )

        parsed_frames, parsed_symbols = _parse_multiframe_xyz(Path(ensemble_xyz))
        energy_window_kcal = _profile_float(profile, "energy_window_kcal", self.energy_window_kcal)
        rmsd_threshold = _profile_float(profile, "rmsd_threshold", self.rmsd_threshold)
        temperature = _profile_float(profile, "temperature", self.temperature)
        kept_frames, duplicates_dropped, window_dropped = _deduplicate_frames(
            parsed_frames,
            energy_window_kcal=energy_window_kcal,
            rmsd_threshold=rmsd_threshold,
        )
        weights = _boltzmann_weights(
            [frame["energy_hartree"] for frame in kept_frames], temperature
        )
        provenance = _build_provenance(
            stable_state=stable_state,
            profile=profile,
            provider_name=_backend_name(self._crest_backend_spec),
        )

        records: list[StructureRecord] = []
        for rank, (frame, weight) in enumerate(zip(kept_frames, weights), start=1):
            structure = Structure(
                id=f"{stable_state.state_id}_{frame['conf_id'].lower()}",
                charge=stable_state.charge,
                multiplicity=stable_state.multiplicity,
                symbols=list(parsed_symbols),
                coordinates=np.asarray(frame["coordinates"], dtype=float),
                metadata={
                    "state_id": stable_state.state_id,
                    "conf_id": frame["conf_id"],
                    "frame_index": frame["frame_index"],
                    "xtb_fast_rank": rank,
                },
            )
            records.append(
                StructureRecord(
                    structure=structure,
                    energy_hartree=float(frame["energy_hartree"]),
                    free_energy_hartree=float(frame["energy_hartree"]),
                    weight=float(weight),
                    properties={
                        "xtb_energy_hartree": float(frame["energy_hartree"]),
                        "title": frame["title"],
                        "rank": rank,
                    },
                )
            )

        ensemble = StructureEnsemble(
            records=records,
            temperature=temperature,
            metadata={
                "strategy": "xtb-fast",
                "source_ensemble_xyz": str(ensemble_xyz),
                "input_frames": len(parsed_frames),
                "retained_frames": len(records),
                "duplicates_dropped": duplicates_dropped,
                "window_dropped": window_dropped,
                "energy_window_kcal": energy_window_kcal,
                "rmsd_threshold": rmsd_threshold,
                "provenance": provenance.to_dict(),
            },
        )
        ensemble.sort_by_energy()
        return ensemble

    def _crest_backend(self) -> Any:
        if self._crest_backend_instance is None:
            self._crest_backend_instance = _resolve_backend(self._crest_backend_spec, self.config)
        return self._crest_backend_instance


def _parse_multiframe_xyz(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    all_coordinates, symbols = read_xyz_multiframe(path)
    n_atoms = len(symbols)
    if n_atoms == 0:
        return [], []
    titles = _read_xyz_titles(path)
    n_frames = len(all_coordinates) // n_atoms
    frames: list[dict[str, Any]] = []
    for index in range(n_frames):
        start = index * n_atoms
        title = titles[index] if index < len(titles) else f"CONF{index + 1}"
        frames.append(
            {
                "conf_id": f"CONF{index + 1}",
                "frame_index": index,
                "title": title,
                "energy_hartree": _title_energy(title),
                "coordinates": np.asarray(all_coordinates[start : start + n_atoms], dtype=float),
            }
        )
    return frames, list(symbols)


def _read_xyz_titles(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    titles: list[str] = []
    offset = 0
    while offset < len(lines):
        try:
            atom_count = int(lines[offset].strip())
        except (IndexError, ValueError):
            break
        if atom_count <= 0 or offset + 1 >= len(lines):
            break
        titles.append(lines[offset + 1].strip())
        offset += atom_count + 2
    return titles


def _title_energy(title: str) -> float:
    match = _TITLE_FLOAT_RE.search(title)
    if match is None:
        logger.warning("No xTB title energy found in %r; using 0.0", title)
        return 0.0
    return float(match.group())


def _deduplicate_frames(
    frames: list[dict[str, Any]],
    *,
    energy_window_kcal: float,
    rmsd_threshold: float,
) -> tuple[list[dict[str, Any]], int, int]:
    if not frames:
        return [], 0, 0
    ordered = sorted(frames, key=lambda frame: float(frame["energy_hartree"]))
    minimum_energy = float(ordered[0]["energy_hartree"])
    window_hartree = float(energy_window_kcal) / HARTREE_TO_KCAL
    kept: list[dict[str, Any]] = []
    duplicates_dropped = 0
    window_dropped = 0
    for frame in ordered:
        energy = float(frame["energy_hartree"])
        if energy > minimum_energy + window_hartree:
            window_dropped += 1
            continue
        if any(
            _aligned_rmsd(frame["coordinates"], accepted["coordinates"]) <= rmsd_threshold
            for accepted in kept
        ):
            duplicates_dropped += 1
            continue
        kept.append(frame)
    if not kept:
        kept.append(ordered[0])
    return kept, duplicates_dropped, window_dropped


def _aligned_rmsd(coords_a: NDArray[np.float64], coords_b: NDArray[np.float64]) -> float:
    aligned = GeometryUtils.align_structures(
        np.asarray(coords_a, dtype=float),
        np.asarray(coords_b, dtype=float),
    )
    return GeometryUtils.rmsd(np.asarray(coords_a, dtype=float), aligned)


def _profile_float(profile: Any, key: str, default: float) -> float:
    if isinstance(profile, dict):
        value = profile.get(key)
    else:
        value = getattr(profile, key, None)
    if value is None:
        return float(default)
    return float(value)


def _build_provenance(
    *,
    stable_state: StableState,
    profile: Any,
    provider_name: str,
) -> Provenance:
    return Provenance(
        provider=provider_name,
        provider_version="unknown",
        provider_commit="",
        strategy="xtb-fast",
        strategy_version=_STRATEGY_VERSION,
        profile_id=str(
            getattr(
                profile,
                "name",
                profile if not isinstance(profile, dict) else profile.get("name", profile),
            )
        ),
        schema_version="m2",
        input_signature=_sha_payload(
            {
                "state_id": stable_state.state_id,
                "role": stable_state.role,
                "charge": stable_state.charge,
                "multiplicity": stable_state.multiplicity,
                "identity_fingerprint": stable_state.identity_fingerprint,
            }
        ),
    )


def _sha_payload(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


__all__ = ["XtbFastEnsembleProvider"]
