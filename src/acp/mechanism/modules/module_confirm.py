"""``mech-confirm`` module runner: high-fidelity confirmation (M3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from cccp.utils.file_io import write_xyz

from .._helpers import fingerprint, write_json_atomic
from ..engines.confirmation import ConfirmationEngine
from ..models import StationaryPoint
from ..providers.contracts import RefinementProvider
from .schema import (
    FailureRecord,
    ModuleManifest,
    read_elementary_step_manifest,
    write_module_manifest,
)

logger = logging.getLogger(__name__)


def _write_canonical_xyz(out: Path, point: StationaryPoint, manifest_xyz: str | None) -> str:
    target = out / "canonical.xyz"
    source = Path(point.geometry.path)
    if source.exists() and source.suffix.lower() == ".xyz":
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return str(target)
    if manifest_xyz and Path(manifest_xyz).exists():
        target.write_text(Path(manifest_xyz).read_text(encoding="utf-8"), encoding="utf-8")
        return str(target)
    coordinates = point.metadata.get("coordinates")
    symbols = point.metadata.get("symbols")
    if coordinates is not None and symbols is not None:
        write_xyz(
            target,
            np.asarray(coordinates, dtype=float),
            [str(symbol) for symbol in symbols],
            title=f"confirmed {point.point_id}",
        )
        return str(target)
    return point.geometry.path


def _s3_reference_point(manifest: Any, select: str) -> StationaryPoint | None:
    if select == "ts:canonical":
        point_data = (manifest.transition_state or {}).get("point")
        if isinstance(point_data, dict):
            return StationaryPoint.from_dict(dict(point_data))
        return None
    endpoints = (manifest.irc or {}).get("endpoints") or {}
    for direction in ("forward", "reverse"):
        data = endpoints.get(direction)
        if isinstance(data, dict) and data.get("role") == "sink":
            minimum = data.get("optimized_minimum")
            if isinstance(minimum, dict):
                return StationaryPoint.from_dict(dict(minimum))
    return None


def run_confirm_module(
    step_manifest_path: Path | str,
    select: str = "ts:canonical",
    *,
    fidelity: str = "s4",
    output_dir: Path | str,
    config: dict[str, Any] | None = None,
    label: str | None = None,
    refinement_provider: RefinementProvider | None = None,
) -> ModuleManifest:
    """Confirm one step-manifest artifact at high fidelity; persist manifest."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    input_payload: dict[str, Any] = {
        "step_manifest": str(step_manifest_path),
        "select": select,
        "fidelity": fidelity,
    }
    try:
        step_manifest = read_elementary_step_manifest(Path(step_manifest_path))
        engine = ConfirmationEngine(
            config=config,
            work_root=out / "calc",
            fidelity=fidelity,
            refinement_provider=refinement_provider,
        )
        point = engine.run(step_manifest, select)
        manifest_xyz = (step_manifest.transition_state or {}).get("xyz")
        canonical_xyz = _write_canonical_xyz(
            out, point, manifest_xyz if isinstance(manifest_xyz, str) else None
        )
        stationary_manifest_path = out / "stationary_manifest.json"
        write_json_atomic(stationary_manifest_path, point.to_dict())
        consistency: list[str] = []
        s3_point = _s3_reference_point(step_manifest, select)
        if s3_point is not None:
            consistency = engine._check_s3_s4_consistency(s3_point, point)
        manifest = ModuleManifest(
            phase="confirmation",
            label=label,
            status="validated",
            input=input_payload,
            output={
                "canonical_xyz": canonical_xyz,
                "stationary_manifest": str(stationary_manifest_path),
                "s3_s4_consistency": consistency,
                "point_id": point.point_id,
            },
            provenance={
                "provider": "acp-native-refinement",
                "profile_id": fidelity,
                "parent_manifest": str(step_manifest_path),
                "fingerprint": fingerprint(
                    {
                        "step_manifest": str(step_manifest_path),
                        "select": select,
                        "point_id": point.point_id,
                    }
                ),
            },
        )
    except Exception as exc:
        logger.exception("mech-confirm failed: %s", exc)
        manifest = ModuleManifest(
            phase="confirmation",
            label=label,
            status="failed",
            input=input_payload,
            output={},
            failure=FailureRecord(
                stage="confirmation",
                reason="confirmation_refinement_failed",
                recoverable=True,
                details={"error": str(exc)},
            ),
            provenance={"profile_id": fidelity},
        )
    write_module_manifest(out, manifest)
    return manifest


__all__ = ["run_confirm_module"]
