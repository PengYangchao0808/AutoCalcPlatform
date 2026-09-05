# pyright: reportAny=false
"""Read-only access to manifests from retired ACP workflow paths."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Final, TypeAlias, TypeGuard

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

_S2_V1: Final = "s2_path_v1"
_S2_V2: Final = "s2_path_v2"
_S3_SCHEMA: Final = "s3_lowconfirm_v1"
_S4_SCHEMA: Final = "s4_highconfirm_v1"
_REFINEMENT_V1: Final = "refinement_manifest_v1"
_LEGACY_S3_SCHEMA: Final = "s3_low_level_v3"
_LEGACY_S4_SCHEMA: Final = "s4_high_level_v4"
_BATCH_SCHEMA: Final = "batch_calculation_v1"
_REVIEW_NAME: Final = "s2_review.json"
_CANDIDATE_NAME: Final = "s2_candidate_manifest.json"

__all__ = [
    "read_s2_path_manifest",
    "read_s3_lowconfirm_manifest",
    "read_s4_highconfirm_manifest",
    "read_refinement_manifest",
    "read_batch_calculation_manifest",
    "read_result_summary",
    "read_reaction_definition",
    "read_s2_review",
    "read_s2_candidate_manifest",
]


def _read_json_object(path: Path | str, label: str) -> JsonObject:
    source = Path(path)
    try:
        payload: JsonValue = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read {label}: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {source}") from exc
    if not _is_json_object(payload):
        raise ValueError(f"{label} is not a JSON object: {source}")
    return payload


def _read_optional_json_object(path: Path) -> JsonObject | None:
    if not path.is_file():
        return None
    try:
        payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable legacy manifest %s: %s", path, exc)
        return None
    if not _is_json_object(payload):
        return None
    return payload


def _sidecar_path(path: Path | str, filename: str) -> Path:
    source = Path(path)
    if source.name == filename:
        return source
    if source.name == "s2_path_manifest.json" or not source.is_file():
        return source.with_name(filename)
    try:
        payload: JsonValue = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return source.with_name(filename)
    if not _is_json_object(payload):
        return source.with_name(filename)
    if filename == _REVIEW_NAME:
        is_direct = "selected" in payload or (
            "candidates" in payload
            and ("selected_ts" in payload or "selected_intermediates" in payload)
        )
    else:
        is_direct = payload.get("schema_version") == "s2_candidate_v1"
    return source if is_direct else source.with_name(filename)


def _is_json_object(value: JsonValue) -> TypeGuard[JsonObject]:
    return isinstance(value, dict)


def _hash_candidates(payload: JsonObject) -> set[str]:
    candidates: set[str] = set()
    for excluded in (
        ("content_hash",),
        ("config_hash",),
        ("content_hash", "config_hash"),
    ):
        unhashed = {key: value for key, value in payload.items() if key not in excluded}
        canonical = json.dumps(
            unhashed,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        candidates.add("sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    return candidates


def read_s2_path_manifest(path: Path | str) -> JsonObject:
    """Read an S2 path manifest while retaining v1 historical payloads."""
    payload = _read_json_object(path, "S2 path manifest")
    schema = payload.get("schema_version")
    if schema == _S2_V2:
        if payload.get("mode") != "bond_length_scan":
            raise ValueError(
                f"Not a bond_length_scan manifest (mode={payload.get('mode')!r}): {path}"
            )
    elif schema == _S2_V1:
        if payload.get("workflow") != "PESsearch":
            raise ValueError(f"Not a PESsearch S2 path manifest: {path}")
    else:
        raise ValueError(f"Not an S2 path manifest (schema_version={schema!r}): {path}")
    return payload


def read_s3_lowconfirm_manifest(path: Path | str) -> JsonObject:
    """Read and validate a historical Lowconfirm manifest."""
    payload = _read_json_object(path, "S3 manifest")
    if payload.get("schema_version") != _S3_SCHEMA or payload.get("workflow") != "Lowconfirm":
        raise ValueError(f"Not a Lowconfirm s3_lowconfirm manifest: {path}")
    return payload


def read_s4_highconfirm_manifest(path: Path | str) -> JsonObject:
    """Read and validate a historical Highconfirm manifest."""
    payload = _read_json_object(path, "S4 manifest")
    if payload.get("schema_version") != _S4_SCHEMA or payload.get("workflow") != "Highconfirm":
        raise ValueError(f"Not a Highconfirm s4_highconfirm manifest: {path}")
    return payload


def read_refinement_manifest(path: Path | str) -> JsonObject:
    """Read a refinement manifest and adapt its two legacy schemas to v1 fields."""
    payload = _read_json_object(path, "refinement manifest")
    schema = str(payload.get("schema_version") or "")
    if schema == _LEGACY_S3_SCHEMA:
        normalized = dict(payload)
        if "stage" not in normalized:
            normalized["stage"] = "S3"
        if "fidelity" not in normalized:
            normalized["fidelity"] = "low"
        if "profile_id" not in normalized:
            normalized["profile_id"] = "b97_3c_r2scan_3c_v1_legacy"
        structures = payload.get("structures")
        if isinstance(structures, dict):
            normalized["structures"] = list(structures.values())
        elif structures is None:
            normalized["structures"] = []
        return normalized
    if schema == _LEGACY_S4_SCHEMA:
        normalized = dict(payload)
        if "stage" not in normalized:
            normalized["stage"] = "S4"
        if "fidelity" not in normalized:
            normalized["fidelity"] = "high"
        if "profile_id" not in normalized:
            normalized["profile_id"] = "m062x_wb97mv_v1_legacy"
        if "structures" not in normalized:
            normalized["structures"] = payload.get("structures") or []
        return normalized
    return payload


def read_batch_calculation_manifest(path: Path | str) -> JsonObject | None:
    """Read a batch calculation manifest, returning None when unavailable."""
    payload = _read_optional_json_object(Path(path))
    if payload is None:
        return None
    schema = payload.get("schema_version")
    if schema is not None and schema != _BATCH_SCHEMA:
        logger.warning("Unknown batch manifest schema_version=%r at %s", schema, path)
    return payload


def read_result_summary(path: Path | str) -> JsonObject:
    """Read a legacy result-summary pointer payload."""
    payload = _read_json_object(path, "result summary")
    products = payload.get("products")
    if products is not None and not isinstance(products, list):
        raise ValueError(f"result summary 'products' must be a list: {path}")
    return payload


def read_reaction_definition(path: Path | str) -> JsonObject:
    """Read a reaction definition and verify its content/configuration hash."""
    payload = _read_json_object(path, "reaction.json")
    stored_hashes = {
        key: str(payload.get(key) or "")
        for key in ("content_hash", "config_hash")
        if payload.get(key)
    }
    if not stored_hashes:
        raise ValueError(f"reaction.json is missing config_hash/content_hash: {path}")
    candidates = _hash_candidates(payload)
    for key, stored in stored_hashes.items():
        if stored not in candidates:
            expected = sorted(candidates)
            raise ValueError(
                f"reaction.json {key} mismatch: stored={stored!r}; expected one of {expected!r}"
            )

    if any(key in payload for key in ("reactant", "product", "atom_mapping")):
        try:
            raw_schema_version = payload.get("schema_version")
            raw_index_base = payload.get("index_base")
            schema_version = (
                int(raw_schema_version) if isinstance(raw_schema_version, (str, int, float)) else 2
            )
            index_base = int(raw_index_base) if isinstance(raw_index_base, (str, int, float)) else 0
        except (TypeError, ValueError) as exc:
            raise ValueError(f"reaction.json has invalid schema/index fields: {path}") from exc
        if schema_version != 2:
            raise ValueError(
                f"reaction.json schema_version must be 2, got {schema_version}: {path}"
            )
        if index_base != 0:
            raise ValueError(f"reaction.json index_base must be 0, got {index_base}: {path}")
    return payload


def read_s2_review(path: Path | str) -> JsonObject | None:
    """Read an S2 review payload or its sibling sidecar when present."""
    review_path = _sidecar_path(path, _REVIEW_NAME)
    return _read_optional_json_object(review_path)


def read_s2_candidate_manifest(path: Path | str) -> JsonObject | None:
    """Read an S2 candidate manifest or its sibling sidecar when present."""
    candidate_path = _sidecar_path(path, _CANDIDATE_NAME)
    return _read_optional_json_object(candidate_path)
