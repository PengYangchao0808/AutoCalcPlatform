"""Pre-registered benchmark thresholds (todo 50 / gap §10.3).

Thresholds are frozen in ``thresholds.json`` BEFORE the first metrics run and
sealed with a content hash (all fields except ``content_sha256``).  The loader
re-computes the hash and refuses to silently accept an edited file: a mismatch
raises :class:`ThresholdIntegrityError` and names the sanctioned re-freeze
path — :func:`refreeze_thresholds` with an explicit, non-empty re-measurement
note, which appends a ``remeasurements`` entry recording the previous hash.

A threshold comparison has three states: ``pass``, ``fail`` and
``not_verified`` (the metric has no data — never a silent pass, never a
fabricated number).  Metric keys map to the canonical blocks produced by
``metrics.evaluate_dataset``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.nmr_spectra_benchmark import canonical_json_bytes

THRESHOLDS_SCHEMA = "acp-nmr-benchmark-thresholds-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPS = (">=", "<=")

#: Metric key -> path into the dataset metric record.  The owning block's
#: ``status`` key (path minus the final value key) must be ``measured`` for a
#: comparison to run.
_METRIC_PATHS: dict[str, tuple[str, ...]] = {
    "shift_mae_13c_ppm": ("shift_accuracy", "13C", "mae"),
    "shift_rmse_13c_ppm": ("shift_accuracy", "13C", "rmse"),
    "shift_mae_1h_ppm": ("shift_accuracy", "1H", "mae"),
    "shift_rmse_1h_ppm": ("shift_accuracy", "1H", "rmse"),
    "assignment_accuracy": ("assignment", "accuracy"),
    "top1_dp4": ("ranking", "top1_dp4", "accuracy"),
    "top1_dp5": ("ranking", "top1_dp5", "accuracy"),
    "brier_dp4": ("binary_probability", "dp4", "brier"),
    "log_loss_dp4": ("binary_probability", "dp4", "log_loss"),
    "brier_dp5": ("binary_probability", "dp5", "brier"),
    "log_loss_dp5": ("binary_probability", "dp5", "log_loss"),
    "refusal_rate": ("refusal", "refusal_rate"),
    "item_refusal_rate": ("refusal", "item_refusal_rate"),
    "absent_confident_winner_rate": ("true_structure_absence", "confident_winner_rate"),
}

KNOWN_METRIC_KEYS: tuple[str, ...] = tuple(sorted(_METRIC_PATHS))


class ThresholdError(ValueError):
    """Base class for typed threshold failures."""


class ThresholdSchemaError(ThresholdError):
    """Malformed threshold payload (schema/keys/ops/notes)."""


class ThresholdIntegrityError(ThresholdError):
    """The threshold file's content hash does not match its recorded hash."""


def thresholds_payload_hash(payload: Mapping[str, Any]) -> str:
    """Content hash of the threshold payload excluding ``content_sha256``."""
    body = {key: value for key, value in payload.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def seal_thresholds(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* carrying its recomputed content hash."""
    sealed = dict(payload)
    sealed.pop("content_sha256", None)
    sealed["content_sha256"] = thresholds_payload_hash(payload)
    return sealed


def _validate_thresholds(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != THRESHOLDS_SCHEMA:
        raise ThresholdSchemaError(
            f"unsupported thresholds schema: {payload.get('schema')!r} "
            f"(expected {THRESHOLDS_SCHEMA!r})"
        )
    preregistered_at = payload.get("preregistered_at")
    if not isinstance(preregistered_at, str) or not preregistered_at.strip():
        raise ThresholdSchemaError("thresholds payload requires a non-empty preregistered_at")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, Mapping) or not thresholds:
        raise ThresholdSchemaError("thresholds payload carries no thresholds")
    for key, spec in thresholds.items():
        if key not in _METRIC_PATHS:
            raise ThresholdSchemaError(
                f"unknown threshold metric {key!r}; known metrics: {list(KNOWN_METRIC_KEYS)}"
            )
        if not isinstance(spec, Mapping):
            raise ThresholdSchemaError(f"threshold {key!r} must be an object")
        if spec.get("op") not in _OPS:
            raise ThresholdSchemaError(f"threshold {key!r}: op must be one of {_OPS}")
        value = spec.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ThresholdSchemaError(f"threshold {key!r}: value must be a number")
        if not math.isfinite(float(value)):
            raise ThresholdSchemaError(f"threshold {key!r}: value must be finite")
        rationale = spec.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ThresholdSchemaError(f"threshold {key!r}: rationale is required")
    remeasurements = payload.get("remeasurements")
    if not isinstance(remeasurements, list):
        raise ThresholdSchemaError("thresholds payload requires a 'remeasurements' list")
    for index, entry in enumerate(remeasurements):
        if not isinstance(entry, Mapping):
            raise ThresholdSchemaError(f"remeasurements[{index}] must be an object")
        note = entry.get("note")
        if not isinstance(note, str) or not note.strip():
            raise ThresholdSchemaError(f"remeasurements[{index}]: note is required")
        at = entry.get("at")
        if not isinstance(at, str) or not at.strip():
            raise ThresholdSchemaError(f"remeasurements[{index}]: at is required")
        previous = entry.get("previous_sha256")
        if previous is not None and (
            not isinstance(previous, str) or not _SHA256_RE.match(previous)
        ):
            raise ThresholdSchemaError(
                f"remeasurements[{index}]: previous_sha256 must be null or a sha256 hex digest"
            )


def _verify_integrity(payload: Mapping[str, Any], *, path: Path) -> None:
    stored = payload.get("content_sha256")
    if not isinstance(stored, str) or not _SHA256_RE.match(stored):
        raise ThresholdIntegrityError(
            f"threshold file {path} carries no valid content_sha256; refusing to compare "
            "against an unsealed threshold file (re-freezing requires an explicit "
            "re-measurement note via refreeze_thresholds)"
        )
    actual = thresholds_payload_hash(payload)
    if actual != stored:
        raise ThresholdIntegrityError(
            f"threshold file {path} was edited after pre-registration (content hash mismatch: "
            f"recorded {stored}, recomputed {actual}); refusing to silently accept edited "
            "thresholds — re-freezing requires an explicit re-measurement note via "
            "refreeze_thresholds(note=..., at=...)"
        )


def load_thresholds(path: str | Path) -> dict[str, Any]:
    """Load, integrity-check and schema-validate the threshold file."""
    threshold_path = Path(path)
    try:
        payload = json.loads(threshold_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ThresholdSchemaError(f"threshold file is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ThresholdSchemaError("threshold file must be a JSON object")
    _verify_integrity(payload, path=threshold_path)
    _validate_thresholds(payload)
    return dict(payload)


def refreeze_thresholds(path: str | Path, *, note: str, at: str) -> dict[str, Any]:
    """Re-freeze an edited threshold file with an explicit re-measurement note.

    The note is mandatory (empty → :class:`ThresholdSchemaError`); the previous
    content hash is recorded in ``remeasurements`` so the edit history is
    auditable.  The file is re-sealed and written atomically.
    """
    if not isinstance(note, str) or not note.strip():
        raise ThresholdSchemaError(
            "re-freezing thresholds requires an explicit non-empty re-measurement note"
        )
    if not isinstance(at, str) or not at.strip():
        raise ThresholdSchemaError("re-freezing thresholds requires the 'at' timestamp")
    threshold_path = Path(path)
    try:
        payload = json.loads(threshold_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ThresholdSchemaError(f"threshold file is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ThresholdSchemaError("threshold file must be a JSON object")
    remeasurements = list(payload.get("remeasurements") or [])
    remeasurements.append(
        {
            "note": note.strip(),
            "at": at,
            "previous_sha256": payload.get("content_sha256"),
        }
    )
    updated = dict(payload)
    updated["remeasurements"] = remeasurements
    sealed = seal_thresholds(updated)
    _validate_thresholds(sealed)
    threshold_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = threshold_path.with_name(f".{threshold_path.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(sealed))
    os.replace(temporary, threshold_path)
    return sealed


def _dig(metrics: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    block: Any = metrics
    for part in path:
        if not isinstance(block, Mapping):
            return None
        block = block.get(part)
    return block


def metric_value(metrics: Mapping[str, Any], key: str) -> tuple[float | None, str | None]:
    """Extract a threshold metric: ``(value, reason-if-not-measured)``."""
    if key not in _METRIC_PATHS:
        raise ThresholdSchemaError(f"unknown threshold metric {key!r}")
    path = _METRIC_PATHS[key]
    status = _dig(metrics, path[:-1] + ("status",))
    if status != "measured":
        return None, f"metric_{status if isinstance(status, str) else 'missing'}"
    value = _dig(metrics, path)
    if value is None:
        return None, "metric_missing"
    result = float(value)
    if not math.isfinite(result):
        return None, "metric_non_finite"
    return result, None


def compare_dataset_metrics(
    metrics: Mapping[str, Any], thresholds_payload: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Compare a dataset metric record against the pre-registered thresholds."""
    thresholds = thresholds_payload["thresholds"]
    comparisons: list[dict[str, Any]] = []
    for key in sorted(thresholds):
        spec = thresholds[key]
        value, reason = metric_value(metrics, key)
        if value is None:
            status = "not_verified"
        elif spec["op"] == ">=":
            status = "pass" if value >= float(spec["value"]) else "fail"
        else:
            status = "pass" if value <= float(spec["value"]) else "fail"
        comparisons.append(
            {
                "metric": key,
                "op": spec["op"],
                "threshold": float(spec["value"]),
                "observed": value,
                "status": status,
                "reasons": [reason] if reason else [],
            }
        )
    return comparisons


__all__ = [
    "KNOWN_METRIC_KEYS",
    "THRESHOLDS_SCHEMA",
    "ThresholdError",
    "ThresholdIntegrityError",
    "ThresholdSchemaError",
    "compare_dataset_metrics",
    "load_thresholds",
    "metric_value",
    "refreeze_thresholds",
    "seal_thresholds",
    "thresholds_payload_hash",
]
