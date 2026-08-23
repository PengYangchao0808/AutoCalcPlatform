"""Unified Confsearch output manifest (§5).

Every Confsearch protocol writes the same ``RESULT/confsearch/`` tree::

    RESULT/confsearch/
    ├── confsearch_manifest.json   ← the single handoff artifact (S1)
    ├── ensemble.xyz / ensemble.csv / energies.json / boltzmann.json
    ├── conformers/conf_NNNN.xyz
    ├── refinement/                ← fine-DFT artifacts (policy-dependent)
    └── quality_gates.json

All geometry references inside the manifest are relative to the manifest's
own directory so downstream stages (PESsearch / NMR) can resolve them both
in place and from a copied handoff tree (§8).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

import numpy as np

from cccp.utils.file_io import write_xyz

from .contracts import CONFSEARCH_SCHEMA_VERSION, ConformerEntry
from .shared.artifacts import write_json_atomic

logger = logging.getLogger(__name__)

CONFSEARCH_DIR_PARTS = ("RESULT", "confsearch")
MANIFEST_FILENAME = "confsearch_manifest.json"


def confsearch_result_dir(mol_dir: Path) -> Path:
    """Return ``<mol_dir>/RESULT/confsearch``."""
    return mol_dir.joinpath(*CONFSEARCH_DIR_PARTS)


def find_confsearch_manifest(task_root: Path) -> Path | None:
    """Locate a confsearch manifest under a task root (v1.2 layout).

    Probes ``RESULT/confsearch/confsearch_manifest.json`` first, then a
    shallow scan for any ``confsearch_manifest.json`` (≤3 levels).
    """
    primary = confsearch_result_dir(task_root) / MANIFEST_FILENAME
    if primary.is_file():
        return primary
    try:
        matches = sorted(task_root.rglob(MANIFEST_FILENAME))
    except OSError:
        return None
    for match in matches:
        if len(match.relative_to(task_root).parts) <= 4:
            return match
    return None


def write_conformer_geometries(
    confsearch_dir: Path,
    conformers: list[ConformerEntry],
    records: list[dict[str, Any]],
) -> None:
    """Materialize ``conformers/<conf_id>.xyz`` and fill entry geometry refs.

    Entries and records are paired positionally (the engine builds entries
    from the same rank-sorted record list).
    """
    conformers_dir = confsearch_dir / "conformers"
    conformers_dir.mkdir(parents=True, exist_ok=True)
    if len(records) < len(conformers):
        raise ValueError(
            f"Geometry record mismatch: {len(conformers)} conformers vs {len(records)} records"
        )
    for entry, record in zip(conformers, records, strict=False):
        coordinates = record.get("coordinates")
        symbols = record.get("symbols")
        if coordinates is None or symbols is None:
            continue
        xyz_path = conformers_dir / f"{entry.conf_id}.xyz"
        write_xyz(
            xyz_path,
            np.asarray(coordinates, dtype=float),
            [str(symbol) for symbol in symbols],
            title=f"{entry.conf_id} E={entry.energy_hartree}",
        )
        entry.geometry = f"conformers/{entry.conf_id}.xyz"


def write_ensemble_table(confsearch_dir: Path, conformers: list[ConformerEntry]) -> None:
    """Write ``ensemble.xyz``, ``ensemble.csv``, ``energies.json``, ``boltzmann.json``."""
    # ensemble.xyz (multi-frame, single-symbol assumption avoided: parse from files)
    frames: list[str] = []
    symbols: list[str] | None = None
    for entry in conformers:
        xyz_path = confsearch_dir / entry.geometry
        if not xyz_path.is_file():
            continue
        text = xyz_path.read_text(encoding="utf-8").strip()
        if symbols is None:
            symbols = _symbols_from_xyz(xyz_path)
        title = (
            f"{entry.conf_id} E={entry.energy_hartree} "
            f"G={entry.free_energy_hartree} w={entry.boltzmann_weight}"
        )
        lines = text.splitlines()
        atom_lines = lines[2:] if len(lines) > 2 else []
        frames.append(f"{len(atom_lines)}\n{title}\n" + "\n".join(atom_lines))
    if frames:
        (confsearch_dir / "ensemble.xyz").write_text("\n".join(frames) + "\n", encoding="utf-8")

    with (confsearch_dir / "ensemble.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "conf_id",
                "rank",
                "energy_hartree",
                "free_energy_hartree",
                "relative_energy_kcal",
                "boltzmann_weight",
            ]
        )
        for entry in conformers:
            writer.writerow(
                [
                    entry.conf_id,
                    entry.rank,
                    _fmt(entry.energy_hartree),
                    _fmt(entry.free_energy_hartree),
                    _fmt(entry.relative_energy_kcal),
                    _fmt(entry.boltzmann_weight),
                ]
            )

    write_json_atomic(
        confsearch_dir / "energies.json",
        {
            "units": {"energy": "hartree", "relative": "kcal/mol"},
            "conformers": [
                {
                    "conf_id": entry.conf_id,
                    "rank": entry.rank,
                    "energy_hartree": entry.energy_hartree,
                    "free_energy_hartree": entry.free_energy_hartree,
                    "relative_energy_kcal": entry.relative_energy_kcal,
                }
                for entry in conformers
            ],
        },
    )
    write_json_atomic(
        confsearch_dir / "boltzmann.json",
        {
            "weights": {entry.conf_id: entry.boltzmann_weight for entry in conformers},
            "weight_sum": sum(entry.boltzmann_weight or 0.0 for entry in conformers),
        },
    )


def _symbols_from_xyz(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        n = int(lines[0].strip())
    except (ValueError, IndexError):
        return []
    return [line.split()[0] for line in lines[2 : 2 + n] if line.strip()]


def _fmt(value: float | None, digits: int = 10) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def build_manifest_payload(
    *,
    protocol: str,
    profile: str,
    refinement_policy: str,
    backend: str,
    input_block: dict[str, Any],
    sampling: dict[str, Any],
    conformers: list[ConformerEntry],
    selected_conformers: list[str],
    refinement: dict[str, Any],
    provenance: dict[str, Any],
    quality_gates: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the ``confsearch_v1`` manifest payload (§5)."""
    return {
        "schema_version": CONFSEARCH_SCHEMA_VERSION,
        "workflow": "Confsearch",
        "protocol": protocol,
        "profile": profile,
        "refinement_policy": refinement_policy,
        "backend": backend,
        "input": input_block,
        "sampling": sampling,
        "conformers": [entry.to_dict() for entry in conformers],
        "selected_conformers": list(selected_conformers),
        "refinement": refinement,
        "provenance": provenance,
        "quality_gates": quality_gates,
    }


def write_manifest(confsearch_dir: Path, payload: dict[str, Any]) -> Path:
    """Persist ``confsearch_manifest.json`` and ``quality_gates.json``."""
    path = write_json_atomic(confsearch_dir / MANIFEST_FILENAME, payload)
    write_json_atomic(confsearch_dir / "quality_gates.json", payload.get("quality_gates", {}))
    return path


def read_manifest(path: Path) -> dict[str, Any]:
    """Read and minimally validate a confsearch manifest."""
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Confsearch manifest is not a JSON object: {path}")
    if payload.get("schema_version") != CONFSEARCH_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported confsearch manifest schema_version "
            f"{payload.get('schema_version')!r} in {path}"
        )
    if payload.get("workflow") != "Confsearch":
        raise ValueError(f"Not a Confsearch manifest: {path}")
    return payload


def resolve_manifest_geometry(manifest_path: Path, relative_ref: str) -> Path:
    """Resolve a manifest-relative geometry reference to an absolute path."""
    candidate = (manifest_path.parent / relative_ref).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Manifest geometry reference missing: {relative_ref} "
            f"(looked in {manifest_path.parent})"
        )
    return candidate


def representative_conformer(
    manifest_path: Path,
    conf_id: str | None = None,
) -> tuple[str, Path]:
    """Return ``(conf_id, geometry_path)`` for the rank-1 (or requested) conformer."""
    payload = read_manifest(manifest_path)
    conformers = payload.get("conformers") or []
    if not conformers:
        raise ValueError(f"Confsearch manifest has no conformers: {manifest_path}")
    chosen: dict[str, Any] | None = None
    if conf_id:
        for entry in conformers:
            if str(entry.get("conf_id")) == conf_id:
                chosen = entry
                break
        if chosen is None:
            raise ValueError(f"Confsearch manifest has no conformer {conf_id!r}: {manifest_path}")
    else:
        chosen = min(
            conformers,
            key=lambda entry: int(entry.get("rank") or 999999),
        )
    assert chosen is not None
    geometry_ref = str(chosen.get("geometry") or "")
    if not geometry_ref:
        raise ValueError(f"Conformer {chosen.get('conf_id')!r} has no geometry reference")
    return str(chosen["conf_id"]), resolve_manifest_geometry(manifest_path, geometry_ref)


__all__ = [
    "CONFSEARCH_SCHEMA_VERSION",
    "MANIFEST_FILENAME",
    "build_manifest_payload",
    "confsearch_result_dir",
    "find_confsearch_manifest",
    "read_manifest",
    "representative_conformer",
    "resolve_manifest_geometry",
    "write_conformer_geometries",
    "write_ensemble_table",
    "write_manifest",
]
