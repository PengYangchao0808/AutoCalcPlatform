"""``mech-conf`` module runner: conformer search → ensemble manifest (M1)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .._helpers import fingerprint, write_json_atomic
from ..engines.conformer import ConformerEngine
from ..providers.contracts import EnsembleProvider
from .schema import (
    FailureRecord,
    ModuleManifest,
    write_module_manifest,
)

logger = logging.getLogger(__name__)


def run_conformer_module(
    structure_source: str,
    *,
    output_dir: Path | str,
    mode: str = "censo-lite",
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    label: str | None = None,
    ensemble_provider: EnsembleProvider | None = None,
) -> ModuleManifest:
    """Run conformer search and persist ensemble + module manifest."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolved_charge = 0 if charge is None else charge
    resolved_multiplicity = 1 if multiplicity is None else multiplicity
    input_payload: dict[str, Any] = {
        "structure_source": structure_source,
        "mode": mode,
        "charge": resolved_charge,
        "multiplicity": resolved_multiplicity,
        "name": name,
    }
    try:
        engine = ConformerEngine(
            config=config,
            work_root=out / "calc",
            mode=mode,
            ensemble_provider=ensemble_provider,
        )
        state = engine.run(
            structure_source,
            charge=resolved_charge,
            multiplicity=resolved_multiplicity,
            name=name,
        )
        output = _write_ensemble_outputs(out, state)
        manifest = ModuleManifest(
            phase="conformer",
            label=label,
            status="validated",
            input=input_payload,
            output=output,
            provenance={
                "provider": engine.provider_name,
                "profile_id": mode,
                "fingerprint": fingerprint({"state_id": state.state_id, "input": input_payload}),
            },
        )
    except Exception as exc:
        logger.exception("mech-conf failed: %s", exc)
        manifest = ModuleManifest(
            phase="conformer",
            label=label,
            status="failed",
            input=input_payload,
            output={},
            failure=FailureRecord(
                stage="conformer",
                reason="conformer_search_failed",
                recoverable=True,
                details={"error": str(exc)},
            ),
            provenance={"profile_id": mode},
        )
    write_module_manifest(out, manifest)
    return manifest


def _write_ensemble_outputs(out: Path, state: Any) -> dict[str, Any]:
    from cccp.utils.file_io import write_xyz

    conformers_dir = out / "conformers"
    conformers_dir.mkdir(parents=True, exist_ok=True)
    ensemble = state.ensemble
    records_payload: list[dict[str, Any]] = []
    representative_xyz = ""
    representative_energy: float | None = None
    ensemble_lines: list[str] = []
    for index, record in enumerate(ensemble.records if ensemble is not None else []):
        symbols = list(record.symbols)
        coordinates = record.coordinates
        xyz_path = conformers_dir / f"{record.id}.xyz"
        if coordinates is not None:
            write_xyz(
                xyz_path,
                coordinates,
                symbols,
                title=f"{record.id} energy={record.energy_hartree}",
            )
            frame_lines = [str(len(symbols)), f"{record.id} energy={record.energy_hartree}"]
            for symbol, coord in zip(symbols, coordinates, strict=True):
                frame_lines.append(f"{symbol} {coord[0]:.8f} {coord[1]:.8f} {coord[2]:.8f}")
            ensemble_lines.extend(frame_lines)
        records_payload.append(
            {
                "candidate_id": record.id,
                "xyz": str(xyz_path),
                "energy_hartree": record.energy_hartree,
                "free_energy_hartree": record.free_energy_hartree,
                "weight": record.weight,
                "rank": record.properties.get("rank", index + 1),
            }
        )
        energy = record.energy_hartree
        if representative_energy is None or (energy is not None and energy < representative_energy):
            representative_energy = energy
            representative_xyz = str(xyz_path)
    ensemble_xyz_path = conformers_dir / "ensemble.xyz"
    if ensemble_lines:
        ensemble_xyz_path.write_text("\n".join(ensemble_lines) + "\n", encoding="utf-8")
    ensemble_manifest_path = out / "ensemble_manifest.json"
    write_json_atomic(
        ensemble_manifest_path,
        {
            "state_id": state.state_id,
            "n_records": len(records_payload),
            "records": records_payload,
        },
    )
    return {
        "ensemble_xyz": str(ensemble_xyz_path),
        "representative_xyz": representative_xyz,
        "ensemble_manifest": str(ensemble_manifest_path),
    }


__all__ = ["run_conformer_module"]
