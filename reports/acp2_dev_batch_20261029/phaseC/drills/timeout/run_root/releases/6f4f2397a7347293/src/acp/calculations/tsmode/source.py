"""Compat shim + ACP path-policy wrapper — TS source science moved to cccp.

The pure frequency-source parsing/validation and vibrational analysis now live
in :mod:`cccp.analysis.tsmode.source` (T23, C04-s4).  This module re-exports
the *same objects* and keeps the two ACP-only responsibilities that are path
policy, not science:

* :func:`load_bundle_from_files` — thin wrapper that applies the optional XYZ
  ``geometry_path`` cross-check via the ACP structure reader and stamps the
  geometry file/sha fields onto the pure bundle.
* :func:`snapshot_bundle_files` / :func:`verify_snapshot_hashes` — the
  ``INPUT/tsmode`` staging layout (plan §11).

It carries no parsing/analysis implementation of its own.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from cccp.analysis.tsmode.contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    FrequencySourceBundle,
    SourceLevelOfTheory,
    TsmodeError,
    sha256_file,
)
from cccp.analysis.tsmode.source import (
    HessianModes,
    build_external_subspace,
    compute_hessian_modes,
    kabsch_rmsd,
    mass_weight_and_project,
)
from cccp.analysis.tsmode.source import (
    load_bundle_from_files as _load_bundle_from_files,
)

__all__ = [
    "HessianModes",
    "build_external_subspace",
    "compute_hessian_modes",
    "kabsch_rmsd",
    "load_bundle_from_files",
    "mass_weight_and_project",
    "snapshot_bundle_files",
]

#: Kept local (ACP path-policy wrapper); mirrors the cccp science constant.
_GEOMETRY_RMSD_TOLERANCE_A = 0.05


def load_bundle_from_files(
    output_path: str | Path,
    hess_path: str | Path,
    *,
    geometry_path: str | Path | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    level: SourceLevelOfTheory | None = None,
    bundle_id: str = "",
    origin: dict[str, object] | None = None,
) -> FrequencySourceBundle:
    """Load a frequency source bundle, applying the ACP XYZ cross-check.

    Delegates the pure parsing/validation to
    :func:`cccp.analysis.tsmode.source.load_bundle_from_files`, then (when
    ``geometry_path`` is supplied) reads the optional matching structure via
    the ACP reader and records the geometry file/hash + RMSD warnings.  See
    the cccp function for the full argument/error contract.
    """
    bundle = _load_bundle_from_files(
        output_path,
        hess_path,
        charge=charge,
        multiplicity=multiplicity,
        level=level,
        bundle_id=bundle_id,
        origin=origin,
    )
    if geometry_path is None:
        return bundle

    warnings = list(bundle.warnings)
    if Path(geometry_path).is_file():
        from acp.io.structures import StructureReader  # local import: IO layer

        structure = StructureReader().read(str(geometry_path))
        if structure.coordinates is not None:
            xyz_coords = np.asarray(structure.coordinates, dtype=np.float64)
            if len(xyz_coords) != bundle.n_atoms:
                warnings.append("Optional XYZ atom count differs from Hessian")
            else:
                coords_angstrom = np.asarray(bundle.coordinates_angstrom, dtype=np.float64)
                xyz_rmsd = kabsch_rmsd(coords_angstrom, xyz_coords)
                if xyz_rmsd > _GEOMETRY_RMSD_TOLERANCE_A:
                    warnings.append(f"Optional XYZ geometry RMSD {xyz_rmsd:.4f} Å above tolerance")

    return replace(
        bundle,
        geometry_file=str(geometry_path) if geometry_path else "",
        geometry_sha256=sha256_file(geometry_path) if geometry_path else "",
        warnings=warnings,
    )


def snapshot_bundle_files(bundle: FrequencySourceBundle, input_dir: str | Path) -> dict[str, Path]:
    """Copy the bundle's source assets into *input_dir* with stable names.

    Layout (plan §11)::

        INPUT/tsmode/source.xyz        geometry bound to the Hessian
        INPUT/tsmode/source.hess       Hessian copy
        INPUT/tsmode/source_modes.json printed modes + hashes
        INPUT/tsmode/source_bundle.json full bundle snapshot
    """
    import json

    target_dir = Path(input_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    hess_source = Path(bundle.hessian_file)
    if not hess_source.is_file():
        raise TsmodeError(HESSIAN_MISSING, f"Cannot snapshot: Hessian missing at {hess_source}")
    hess_target = target_dir / "source.hess"
    if hess_source.resolve() != hess_target.resolve():
        hess_target.write_bytes(hess_source.read_bytes())

    xyz_lines = [str(bundle.n_atoms), bundle.geo_hash]
    for symbol, row in zip(bundle.elements, bundle.coordinates_angstrom):
        xyz_lines.append(f"{symbol:2s} {row[0]:15.10f} {row[1]:15.10f} {row[2]:15.10f}")
    xyz_target = target_dir / "source.xyz"
    xyz_target.write_text("\n".join(xyz_lines) + "\n", encoding="utf-8")

    modes_payload = {
        "schema_version": "source_modes_v1",
        "geometry_hash": bundle.geo_hash,
        "hessian_sha256": bundle.hessian_sha256,
        "modes": [
            {
                "source_mode_index": mode.source_mode_index,
                "frequency_cm1": mode.frequency_cm1,
                "vectors": mode.vectors,
            }
            for mode in bundle.modes
        ],
    }
    modes_target = target_dir / "source_modes.json"
    modes_target.write_text(json.dumps(modes_payload, indent=2), encoding="utf-8")

    snapshot = bundle.to_dict()
    snapshot["files"]["hessian"]["path"] = "source.hess"
    snapshot["files"]["geometry"]["path"] = "source.xyz"
    bundle_target = target_dir / "source_bundle.json"
    bundle_target.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    return {
        "hessian": hess_target,
        "geometry": xyz_target,
        "modes": modes_target,
        "bundle": bundle_target,
    }


def verify_snapshot_hashes(snapshot_dir: str | Path, bundle: FrequencySourceBundle) -> None:
    """Refuse execution when a staged asset no longer hashes to its source."""
    staged = Path(snapshot_dir) / "source.hess"
    if sha256_file(staged) != bundle.hessian_sha256:
        raise TsmodeError(
            FREQUENCY_SOURCE_INCOMPLETE,
            f"Staged Hessian {staged} hash differs from the bundle snapshot",
        )
