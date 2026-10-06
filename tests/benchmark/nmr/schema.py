"""Benchmark dataset manifest schema (todo 50 / gap §10.3).

The manifest is the single source of truth for what a benchmark run measured:
per-dataset provenance (``source`` / ``license`` / ``version`` / ``hash``) and
per-item records (molecule id, candidate predictions, true-structure
presence, per-signal data).  The validator REJECTS missing provenance with
typed errors — no silent defaults — and re-computes each dataset ``hash``
from its ``items`` block so an edited dataset cannot pass unnoticed.

Schema (``acp-nmr-benchmark-dataset-manifest-v1``)::

    {
      "schema": "acp-nmr-benchmark-dataset-manifest-v1",
      "datasets": [
        {
          "dataset_id": "...", "title": "...", "layer": "synthetic",
          "source": "...", "license": "...", "version": "...",
          "hash": "<sha256 of canonical(items)>",
          "distribution": {
            "note": "balanced by construction; NOT a realistic prevalence",
            "balanced_by_construction": true
          },
          "items": [
            {
              "item_id": "...", "molecule_id": "...",   # molecule_id = cluster key
              "true_structure_present": true,
              "true_structure_candidate_id": "cand-true",  # null when absent
              "spectra_fixture_id": "phase_deviation_proton",  # optional, todo-48 id
              "experimental": {
                "13C": [{"signal_id": "C1", "observed_ppm": 18.0, "atom_label": "C1"}],
                "1H":  [{"signal_id": "H1", "observed_ppm": 1.2, "atom_label": "H1"}]
              },
              "candidates": [
                {
                  "candidate_id": "cand-true",
                  "is_true_structure": true,
                  "status": "valid",
                  "dp4_probability": 0.85,
                  "dp5_probability": 0.88,       # null = unavailable, never 0
                  "signals": {
                    "13C": [{"signal_id": "C1", "predicted_ppm": 18.06, "atom_label": "C1"}],
                    "1H":  [{"signal_id": "H1", "predicted_ppm": 1.21, "atom_label": "H1"}]
                  }
                }
              ]
            }
          ]
        }
      ]
    }

Rules enforced by :func:`validate_dataset_manifest`:

* ``source`` / ``license`` / ``version`` / ``hash`` are required per dataset
  (missing → :class:`MissingProvenanceError`);
* ``hash`` must equal ``sha256(canonical JSON of items)``
  (mismatch → :class:`DatasetHashMismatchError`);
* ``true_structure_present`` is explicit; exactly one candidate carries
  ``is_true_structure=true`` iff the true structure is present;
* non-``valid`` candidates carry no probabilities (null), ``valid``
  candidates carry a DP4 probability and may carry DP5 or null (unavailable);
* every referenced id (true candidate, ``spectra_fixture_id``) must resolve
  (dangling → :class:`ManifestReferenceError`);
* ``distribution.note`` is mandatory so a balanced synthetic set can never be
  presented as a realistic prevalence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "acp-nmr-benchmark-dataset-manifest-v1"

#: Nuclei the harness scores (closed vocabulary for this revision).
NUCLEI: tuple[str, ...] = ("1H", "13C")

#: Candidate status vocabulary (superset-compatible with the report
#: ``ProbabilityStatus``; non-``valid`` statuses never carry probabilities).
CANDIDATE_STATUSES: tuple[str, ...] = (
    "valid",
    "invalid",
    "evidence_insufficient",
    "unavailable",
    "not_applicable",
    "placeholder",
)

#: Benchmark layers from gap §10.3 (smoke / assigned-statistical /
#: stereochemistry / raw spectra / boundary).
LAYERS: tuple[str, ...] = (
    "synthetic",
    "assigned_statistical",
    "stereochemistry",
    "raw_spectra",
    "boundary",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BenchmarkManifestError(ValueError):
    """Base class for typed dataset-manifest failures."""


class ManifestSchemaError(BenchmarkManifestError):
    """Structural violation of the manifest schema."""


class MissingProvenanceError(BenchmarkManifestError):
    """A dataset omitted source/license/version/hash — never defaulted."""


class DatasetHashMismatchError(BenchmarkManifestError):
    """The declared dataset hash does not match the canonical items payload."""


class ManifestReferenceError(BenchmarkManifestError):
    """A referenced id (true candidate, spectra fixture) does not resolve."""


def canonical_dataset_hash(items: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 over the canonical JSON of the dataset ``items`` block."""
    canonical = json.dumps(list(items), sort_keys=True, indent=2) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dataset_hashes(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Declared per-dataset hashes keyed by ``dataset_id``."""
    return {
        str(dataset["dataset_id"]): str(dataset["hash"]) for dataset in manifest.get("datasets", ())
    }


def load_dataset_manifest(
    path: str | Path,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Load and fully validate a dataset manifest.

    Args:
        path: Manifest JSON path.
        spectra_fixture_ids: When given, every ``spectra_fixture_id`` link is
            checked against this collection (todo-48 fixture ids); pass
            ``None`` to skip link resolution explicitly.

    Raises:
        BenchmarkManifestError: typed subclass describing the violation.
    """
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestSchemaError(f"dataset manifest is not valid JSON: {exc}") from exc
    return validate_dataset_manifest(payload, spectra_fixture_ids=spectra_fixture_ids)


def validate_dataset_manifest(
    manifest: Any,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Validate a parsed manifest and return it unchanged (same object)."""
    if not isinstance(manifest, Mapping):
        raise ManifestSchemaError("dataset manifest must be a JSON object")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ManifestSchemaError(
            f"unsupported dataset manifest schema: {manifest.get('schema')!r} "
            f"(expected {MANIFEST_SCHEMA!r})"
        )
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ManifestSchemaError("dataset manifest carries no datasets")
    seen_dataset_ids: set[str] = set()
    for index, dataset in enumerate(datasets):
        where = f"datasets[{index}]"
        _validate_dataset(dataset, where, spectra_fixture_ids)
        dataset_id = str(dataset["dataset_id"])
        if dataset_id in seen_dataset_ids:
            raise ManifestSchemaError(f"{where}: duplicate dataset_id {dataset_id!r}")
        seen_dataset_ids.add(dataset_id)
    return manifest


# ---------------------------------------------------------------------------
# internal validation
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestSchemaError(f"{where} must be a JSON object")
    return value


def _require_non_empty_str(mapping: Mapping[str, Any], field: str, where: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ManifestSchemaError(f"{where}: {field!r} must be a non-empty string")
    return value


def _require_finite_float(mapping: Mapping[str, Any], field: str, where: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestSchemaError(f"{where}: {field!r} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ManifestSchemaError(f"{where}: {field!r} must be finite, got {value!r}")
    return result


def _require_probability(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestSchemaError(f"{where} must be null or a number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ManifestSchemaError(f"{where} must be in [0, 1], got {value!r}")
    return result


def _validate_dataset(
    dataset: Any,
    where: str,
    spectra_fixture_ids: Collection[str] | None,
) -> None:
    dataset = _require_mapping(dataset, where)
    dataset_id = _require_non_empty_str(dataset, "dataset_id", where)
    scope = f"dataset {dataset_id!r}"
    for field in ("source", "license", "version", "hash"):
        value = dataset.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise MissingProvenanceError(
                f"{scope}: missing required provenance field {field!r} "
                "(no silent defaults; record the real source/license/version/hash)"
            )
    _require_non_empty_str(dataset, "source", scope)
    _require_non_empty_str(dataset, "license", scope)
    _require_non_empty_str(dataset, "version", scope)
    declared_hash = _require_non_empty_str(dataset, "hash", scope)
    if not _SHA256_RE.match(declared_hash):
        raise ManifestSchemaError(f"{scope}: hash must be a 64-char lowercase sha256 hex digest")
    layer = _require_non_empty_str(dataset, "layer", scope)
    if layer not in LAYERS:
        raise ManifestSchemaError(f"{scope}: layer {layer!r} not in {LAYERS}")
    distribution = _require_mapping(dataset.get("distribution"), f"{scope}.distribution")
    _require_non_empty_str(distribution, "note", f"{scope}.distribution")
    balanced = distribution.get("balanced_by_construction")
    if not isinstance(balanced, bool):
        raise ManifestSchemaError(
            f"{scope}.distribution: 'balanced_by_construction' must be an explicit boolean"
        )
    items = dataset.get("items")
    if not isinstance(items, list) or not items:
        raise ManifestSchemaError(f"{scope}: carries no items")
    recomputed = canonical_dataset_hash(items)
    if recomputed != declared_hash:
        raise DatasetHashMismatchError(
            f"{scope}: declared hash {declared_hash} does not match the canonical items "
            f"hash {recomputed}; recompute and re-sign the dataset before benchmarking"
        )
    seen_item_ids: set[str] = set()
    for index, item in enumerate(items):
        _validate_item(
            item,
            f"{scope}.items[{index}]",
            spectra_fixture_ids,
        )
        item_id = str(item["item_id"])
        if item_id in seen_item_ids:
            raise ManifestSchemaError(f"{scope}: duplicate item_id {item_id!r}")
        seen_item_ids.add(item_id)


def _validate_item(
    item: Any,
    where: str,
    spectra_fixture_ids: Collection[str] | None,
) -> None:
    item = _require_mapping(item, where)
    _require_non_empty_str(item, "item_id", where)
    _require_non_empty_str(item, "molecule_id", where)
    present = item.get("true_structure_present")
    if not isinstance(present, bool):
        raise ManifestSchemaError(f"{where}: 'true_structure_present' must be an explicit boolean")
    true_candidate_id = item.get("true_structure_candidate_id")
    if present:
        if not isinstance(true_candidate_id, str) or not true_candidate_id:
            raise ManifestSchemaError(
                f"{where}: true structure present requires true_structure_candidate_id"
            )
    elif true_candidate_id is not None:
        raise ManifestSchemaError(
            f"{where}: true_structure_candidate_id must be null when the true structure is absent"
        )
    spectra_fixture_id = item.get("spectra_fixture_id")
    if spectra_fixture_id is not None:
        if not isinstance(spectra_fixture_id, str) or not spectra_fixture_id:
            raise ManifestSchemaError(f"{where}: spectra_fixture_id must be a non-empty string")
        if spectra_fixture_ids is not None and spectra_fixture_id not in spectra_fixture_ids:
            raise ManifestReferenceError(
                f"{where}: spectra_fixture_id {spectra_fixture_id!r} not in the todo-48 "
                "fixture manifest"
            )
    experimental = _require_mapping(item.get("experimental"), f"{where}.experimental")
    if not experimental:
        raise ManifestSchemaError(f"{where}.experimental: at least one nucleus is required")
    for nucleus, signals in experimental.items():
        _validate_signal_list(signals, f"{where}.experimental.{nucleus}", nucleus, "observed_ppm")
    candidates = item.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ManifestSchemaError(f"{where}: carries no candidates")
    seen_candidate_ids: set[str] = set()
    true_flags = 0
    for index, candidate in enumerate(candidates):
        _validate_candidate(candidate, f"{where}.candidates[{index}]")
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in seen_candidate_ids:
            raise ManifestSchemaError(f"{where}: duplicate candidate_id {candidate_id!r}")
        seen_candidate_ids.add(candidate_id)
        if candidate["is_true_structure"]:
            true_flags += 1
    if present:
        if true_flags != 1:
            raise ManifestSchemaError(
                f"{where}: true structure present requires exactly one candidate with "
                f"is_true_structure=true, found {true_flags}"
            )
        if true_candidate_id not in seen_candidate_ids:
            raise ManifestReferenceError(
                f"{where}: true_structure_candidate_id {true_candidate_id!r} does not "
                "reference a candidate of this item"
            )
        referenced = next(
            candidate for candidate in candidates if candidate["candidate_id"] == true_candidate_id
        )
        if not referenced["is_true_structure"]:
            raise ManifestSchemaError(
                f"{where}: referenced true candidate {true_candidate_id!r} lacks "
                "is_true_structure=true"
            )
    elif true_flags:
        raise ManifestSchemaError(
            f"{where}: true structure absent but {true_flags} candidate(s) carry "
            "is_true_structure=true"
        )


def _validate_signal_list(signals: Any, where: str, nucleus: Any, value_field: str) -> None:
    if nucleus not in NUCLEI:
        raise ManifestSchemaError(f"{where}: nucleus {nucleus!r} not in {NUCLEI}")
    if not isinstance(signals, list) or not signals:
        raise ManifestSchemaError(f"{where}: at least one signal is required")
    seen: set[str] = set()
    for index, signal in enumerate(signals):
        signal_where = f"{where}[{index}]"
        signal = _require_mapping(signal, signal_where)
        signal_id = _require_non_empty_str(signal, "signal_id", signal_where)
        if signal_id in seen:
            raise ManifestSchemaError(f"{where}: duplicate signal_id {signal_id!r}")
        seen.add(signal_id)
        _require_finite_float(signal, value_field, signal_where)
        label = signal.get("atom_label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ManifestSchemaError(f"{signal_where}: atom_label must be a non-empty string")


def _validate_candidate(candidate: Any, where: str) -> None:
    candidate = _require_mapping(candidate, where)
    _require_non_empty_str(candidate, "candidate_id", where)
    is_true = candidate.get("is_true_structure")
    if not isinstance(is_true, bool):
        raise ManifestSchemaError(f"{where}: 'is_true_structure' must be an explicit boolean")
    status = candidate.get("status")
    if status not in CANDIDATE_STATUSES:
        raise ManifestSchemaError(f"{where}: status {status!r} not in {CANDIDATE_STATUSES}")
    dp4 = _require_probability(candidate.get("dp4_probability"), f"{where}.dp4_probability")
    dp5 = _require_probability(candidate.get("dp5_probability"), f"{where}.dp5_probability")
    if status != "valid" and (dp4 is not None or dp5 is not None):
        raise ManifestSchemaError(
            f"{where}: non-valid candidate {status!r} must not carry probabilities "
            "(null means unavailable — never fabricate a number)"
        )
    if status == "valid" and dp4 is None:
        raise ManifestSchemaError(f"{where}: valid candidate requires a dp4_probability")
    signals = _require_mapping(candidate.get("signals"), f"{where}.signals")
    for nucleus, entries in signals.items():
        if entries:
            _validate_signal_list(entries, f"{where}.signals.{nucleus}", nucleus, "predicted_ppm")
    if status == "valid" and not any(signals.values()):
        raise ManifestSchemaError(f"{where}: valid candidate carries no predicted signals")


__all__ = [
    "CANDIDATE_STATUSES",
    "LAYERS",
    "MANIFEST_SCHEMA",
    "NUCLEI",
    "BenchmarkManifestError",
    "DatasetHashMismatchError",
    "ManifestReferenceError",
    "ManifestSchemaError",
    "MissingProvenanceError",
    "canonical_dataset_hash",
    "dataset_hashes",
    "load_dataset_manifest",
    "validate_dataset_manifest",
]
