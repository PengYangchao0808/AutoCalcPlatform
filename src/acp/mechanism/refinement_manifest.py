# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnusedCallResult=false
"""Read/write helpers for refinement manifests.

Ports RPH ``manifest_io.py`` semantics for ``refinement_manifest_v1`` plus the
legacy ``s3_low_level_v3`` / ``s4_high_level_v4`` read adapters. Like RPH, this
module is transport-only: caller-supplied SHA-256 signature fields are
preserved verbatim rather than synthesized during I/O.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REFINEMENT_MANIFEST_V1 = "refinement_manifest_v1"
LEGACY_S3_SCHEMA = "s3_low_level_v3"
LEGACY_S4_SCHEMA = "s4_high_level_v4"


@dataclass(frozen=True)
class RefinementManifestStructure:
    """One structure row in a refinement manifest."""

    id: str
    role: str
    kind: str
    status: str = "degraded"
    charge: int = 0
    multiplicity: int = 1
    forming_bonds: list[list[int]] = field(default_factory=list)
    opt_status: str = "not_run"
    frequency_status: str = "not_run"
    canonical_frequency_status: str = "not_run"
    sp_status: str = "not_run"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "status": self.status,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "forming_bonds": [list(pair) for pair in self.forming_bonds],
            "opt_status": self.opt_status,
            "frequency_status": self.frequency_status,
            "canonical_frequency_status": self.canonical_frequency_status,
            "sp_status": self.sp_status,
        }
        payload.update(self.extra)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RefinementManifestStructure:
        core_keys = {
            "id",
            "role",
            "kind",
            "status",
            "charge",
            "multiplicity",
            "forming_bonds",
            "opt_status",
            "frequency_status",
            "canonical_frequency_status",
            "sp_status",
        }
        return cls(
            id=str(payload.get("id") or payload.get("structure_id") or ""),
            role=str(payload.get("role") or ""),
            kind=str(payload.get("kind") or ""),
            status=str(payload.get("status") or "degraded"),
            charge=int(payload.get("charge") or 0),
            multiplicity=int(payload.get("multiplicity") or 1),
            forming_bonds=[
                [int(pair[0]), int(pair[1])]
                for pair in payload.get("forming_bonds") or []
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            ],
            opt_status=str(payload.get("opt_status") or "not_run"),
            frequency_status=str(payload.get("frequency_status") or "not_run"),
            canonical_frequency_status=str(
                payload.get("canonical_frequency_status") or "not_run"
            ),
            sp_status=str(payload.get("sp_status") or "not_run"),
            extra={key: value for key, value in payload.items() if key not in core_keys},
        )


@dataclass(frozen=True)
class RefinementManifestPayload:
    """Typed helper for ``refinement_manifest_v1`` payloads."""

    stage: str
    fidelity: str
    profile_id: str
    structures: list[RefinementManifestStructure] = field(default_factory=list)
    run_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    schema_version: str = REFINEMENT_MANIFEST_V1

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "fidelity": self.fidelity,
            "profile_id": self.profile_id,
            "run_id": self.run_id,
            "structures": [row.to_dict() for row in self.structures],
        }
        payload.update(self.extra)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RefinementManifestPayload:
        normalized = _normalize_manifest_dict(payload)
        core_keys = {
            "schema_version",
            "stage",
            "fidelity",
            "profile_id",
            "run_id",
            "structures",
        }
        return cls(
            schema_version=str(normalized.get("schema_version") or REFINEMENT_MANIFEST_V1),
            stage=str(normalized.get("stage") or ""),
            fidelity=str(normalized.get("fidelity") or ""),
            profile_id=str(normalized.get("profile_id") or ""),
            run_id=(
                None if normalized.get("run_id") is None else str(normalized.get("run_id"))
            ),
            structures=[
                RefinementManifestStructure.from_dict(dict(row))
                for row in normalized.get("structures") or []
                if isinstance(row, dict)
            ],
            extra={key: value for key, value in normalized.items() if key not in core_keys},
        )


def write_refinement_manifest(
    payload: dict[str, Any] | RefinementManifestPayload,
    path: Path,
) -> Path:
    """Write a ``refinement_manifest_v1`` payload atomically."""

    manifest = (
        payload.to_dict()
        if isinstance(payload, RefinementManifestPayload)
        else dict(payload)
    )
    schema = str(manifest.get("schema_version") or "")
    if schema != REFINEMENT_MANIFEST_V1:
        raise ValueError(
            f"refinement manifest schema_version must be {REFINEMENT_MANIFEST_V1!r}, got {schema!r}"
        )
    if not isinstance(manifest.get("structures"), list):
        raise ValueError("refinement manifest 'structures' must be a list")
    return _write_json_atomic(Path(path), manifest)


def read_refinement_manifest(path: Path) -> dict[str, Any]:
    """Read a refinement manifest, adapting legacy schemas to v1 shape."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _normalize_manifest_dict(data, source=Path(path))


def _normalize_manifest_dict(
    data: dict[str, Any],
    *,
    source: Path | None = None,
) -> dict[str, Any]:
    schema = str(data.get("schema_version") or "")
    if schema == REFINEMENT_MANIFEST_V1:
        return data
    if schema == LEGACY_S3_SCHEMA:
        return _adapt_legacy_s3(data)
    if schema == LEGACY_S4_SCHEMA:
        return _adapt_legacy_s4(data)
    if source is not None:
        logger.warning(
            "Unknown refinement manifest schema_version=%r in %s; returning raw payload",
            schema,
            source,
        )
    return data


def _adapt_legacy_s3(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized.setdefault("stage", "S3")
    normalized.setdefault("fidelity", "low")
    normalized.setdefault("profile_id", "b97_3c_r2scan_3c_v1_legacy")
    structures = data.get("structures")
    if isinstance(structures, dict):
        normalized["structures"] = list(structures.values())
    elif structures is None:
        normalized["structures"] = []
    return normalized


def _adapt_legacy_s4(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized.setdefault("stage", "S4")
    normalized.setdefault("fidelity", "high")
    normalized.setdefault("profile_id", "m062x_wb97mv_v1_legacy")
    normalized.setdefault("structures", data.get("structures") or [])
    return normalized


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _ = handle.write(json.dumps(payload, indent=2, default=str))
        os.replace(temporary_path, path)
        return path
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        raise


__all__ = [
    "LEGACY_S3_SCHEMA",
    "LEGACY_S4_SCHEMA",
    "REFINEMENT_MANIFEST_V1",
    "RefinementManifestPayload",
    "RefinementManifestStructure",
    "read_refinement_manifest",
    "write_refinement_manifest",
]
