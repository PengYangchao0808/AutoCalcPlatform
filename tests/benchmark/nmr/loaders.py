"""Benchmark dataset loaders for layers 2-5 (todo 53 / gap §10.3 layers 2-5).

The todo-50 harness (``schema.py`` / ``harness.py``) owns the shared dataset
manifest schema ``acp-nmr-benchmark-dataset-manifest-v1``.  This module adds
the **per-layer field profiles** for the four external-benchmark layers and
typed loaders that validate a manifest before it can be fed to
``run_harness``:

* layer 2 ``assigned_statistical`` — assigned shift comparisons with reference
  labels and an explicit train/evaluation split declaration;
* layer 3 ``stereochemistry`` — candidate vs true stereoisomer labels, per
  candidate provenance and the original authors' reported accuracy (which is
  never an ACP claim);
* layer 4 ``raw_spectra`` — links to raw Bruker fixtures, per-fixture content
  hashes, annotation references and an explicit processing status
  (``measured`` / ``not_verified`` with a ``NOT_VERIFIED`` note);
* layer 5 ``boundary`` — hard cases (charged, exchangeable H, overlap, ...)
  with an explicit boundary record that either defers the case to a follow-up
  plan or records why it is covered.

The base validator is reused unchanged, so its typed errors apply here too:
missing ``source`` / ``license`` / ``version`` / ``hash`` →
:class:`~tests.benchmark.nmr.schema.MissingProvenanceError`; an edited items
block → :class:`~tests.benchmark.nmr.schema.DatasetHashMismatchError`;
dangling ``spectra_fixture_id`` → :class:`ManifestReferenceError`.  Layer
violations raise :class:`LayerSchemaError` (missing/invalid layer field) or
:class:`UnsupportedLayerError` (dataset layer not loadable / not requested).

Loaders are pure and deterministic: they read one committed local JSON file,
never fetch anything from the network, never touch a clock and never mutate
the parsed payload.  The external **dataset campaign** (acquiring public
datasets, running them, and any accuracy/calibration claim) is explicitly out
of scope for this work plan — see ``RUNBOOK.md``.

Per-layer required fields beyond the base schema (mirrored by
``LAYER_FIELD_SPECS`` and the runbook):

* layer 2 — dataset ``split`` (``train_disjoint`` / ``split_by`` / ``note``);
  item ``reference`` (``reference_id`` / ``source`` / ``version``);
  experimental signals carry ``reference_label``.
* layer 3 — dataset ``external_reference`` (``citation`` /
  ``reported_accuracy`` / ``reported_accuracy_note``); item
  ``true_stereoisomer_label``; candidates carry ``stereoisomer_label`` and
  ``source``; the true candidate's label must match the item's true label.
* layer 4 — item ``spectra_fixture_id`` plus a non-empty ``raw_spectra`` list
  (``fixture_id`` / ``fixture_sha256`` / ``nucleus`` / ``annotations_ref`` /
  ``processing`` with explicit ``status`` / ``metrics_ref`` / ``note``); the
  item's primary fixture id must appear among the records and every record id
  must resolve against the todo-48 fixture id set when it is supplied.
* layer 5 — item ``boundary`` (``kind`` / ``status`` / ``reason`` /
  ``deferred_to``); deferred cases require a non-null target, covered cases
  require an explicit null.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.benchmark.nmr.schema import (
    NUCLEI,
    BenchmarkManifestError,
    ManifestReferenceError,
    load_dataset_manifest,
)
from tests.nmr_spectra_benchmark import NOT_VERIFIED

#: Version of the layer field profiles enforced by this module.
LAYER_PROFILE = "acp-nmr-benchmark-layer-profile-v1"

#: Benchmark layers 2-5 from gap §10.3 (layer 1 is the todo-51 QC smoke set;
#: ``synthetic`` is the todo-50 machinery self-test layer, never loadable here).
BENCHMARK_LAYERS_2_5: tuple[str, ...] = (
    "assigned_statistical",
    "stereochemistry",
    "raw_spectra",
    "boundary",
)

#: Boundary-case vocabulary from gap §10.3 layer 5.
BOUNDARY_KINDS: tuple[str, ...] = (
    "charged",
    "exchangeable_hydrogen",
    "strong_overlap",
    "few_carbons",
    "halogen_or_domain_edge",
    "large_molecule",
    "symmetry_change",
    "truncated_ensemble",
)

_PROCESSING_STATUSES: tuple[str, ...] = ("measured", "not_verified")
_BOUNDARY_STATUSES: tuple[str, ...] = ("deferred", "covered")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DatasetLoaderError(BenchmarkManifestError):
    """Base class for layer-loader failures (benchmark layers 2-5)."""


class UnsupportedLayerError(DatasetLoaderError):
    """A dataset's layer is not loadable here (or not in the requested set)."""


class LayerSchemaError(DatasetLoaderError):
    """A layer-specific field or per-item record is missing or invalid."""


@dataclass(frozen=True)
class LayerFieldSpec:
    """Layer-specific required fields enforced beyond the todo-50 base schema.

    The runbook (``RUNBOOK.md``) documents every field and the acceptance suite
    pins that each name appears there, so the loader contract and the runbook
    cannot drift apart.  ``all_fields`` concatenates every nesting level.
    """

    layer: str
    dataset_fields: tuple[str, ...] = ()
    item_fields: tuple[str, ...] = ()
    candidate_fields: tuple[str, ...] = ()
    signal_fields: tuple[str, ...] = ()
    record_fields: tuple[str, ...] = ()

    @property
    def all_fields(self) -> tuple[str, ...]:
        """Every required field name for this layer (all nesting levels)."""
        return (
            self.dataset_fields
            + self.item_fields
            + self.candidate_fields
            + self.signal_fields
            + self.record_fields
        )


LAYER_FIELD_SPECS: Mapping[str, LayerFieldSpec] = {
    "assigned_statistical": LayerFieldSpec(
        layer="assigned_statistical",
        dataset_fields=("split",),
        item_fields=("reference",),
        signal_fields=("reference_label",),
        record_fields=("train_disjoint", "split_by", "reference_id"),
    ),
    "stereochemistry": LayerFieldSpec(
        layer="stereochemistry",
        dataset_fields=("external_reference",),
        item_fields=("true_stereoisomer_label",),
        candidate_fields=("stereoisomer_label", "source"),
        record_fields=("citation", "reported_accuracy", "reported_accuracy_note"),
    ),
    "raw_spectra": LayerFieldSpec(
        layer="raw_spectra",
        item_fields=("spectra_fixture_id", "raw_spectra"),
        record_fields=(
            "fixture_id",
            "fixture_sha256",
            "nucleus",
            "annotations_ref",
            "processing",
            "metrics_ref",
            "note",
        ),
    ),
    "boundary": LayerFieldSpec(
        layer="boundary",
        item_fields=("boundary",),
        record_fields=("kind", "status", "reason", "deferred_to"),
    ),
}


@dataclass(frozen=True)
class LayerDataset:
    """One validated benchmark dataset of layer 2-5.

    Attributes:
        dataset_id: Manifest dataset id.
        layer: One of :data:`BENCHMARK_LAYERS_2_5`.
        title: Human-readable dataset title.
        source: Provenance source string from the manifest.
        license: License string exactly as published by the source.
        version: Dataset version / commit string.
        content_hash: Declared dataset hash, re-verified on every load against
            ``canonical_dataset_hash(items)`` by the todo-50 base validator.
        distribution_note: The mandatory ``distribution.note``; balanced sets
            must say so and are never presented as a realistic prevalence.
        items: Validated per-item mappings in manifest order.
        raw: The full validated dataset mapping (items plus layer records).
        layer_profile: Layer profile version enforced by this loader.
    """

    dataset_id: str
    layer: str
    title: str
    source: str
    license: str
    version: str
    content_hash: str
    distribution_note: str
    items: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    layer_profile: str = LAYER_PROFILE

    @property
    def item_ids(self) -> tuple[str, ...]:
        """Item ids in manifest order."""
        return tuple(str(item["item_id"]) for item in self.items)

    @property
    def molecule_ids(self) -> tuple[str, ...]:
        """Bootstrap cluster keys (``molecule_id``) in manifest order."""
        return tuple(str(item["molecule_id"]) for item in self.items)


# ---------------------------------------------------------------------------
# field helpers (no silent defaults: a missing key is an error, even when
# null would be an acceptable value)
# ---------------------------------------------------------------------------


def _require_key(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise LayerSchemaError(f"{where}: missing required field {key!r} (no silent defaults)")
    return mapping[key]


def _require_non_empty_str(mapping: Mapping[str, Any], key: str, where: str) -> str:
    value = _require_key(mapping, key, where)
    if not isinstance(value, str) or not value.strip():
        raise LayerSchemaError(f"{where}: {key!r} must be a non-empty string")
    return value


def _require_mapping(mapping: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = _require_key(mapping, key, where)
    if not isinstance(value, Mapping):
        raise LayerSchemaError(f"{where}: {key!r} must be a JSON object")
    return value


def _require_bool(mapping: Mapping[str, Any], key: str, where: str) -> bool:
    value = _require_key(mapping, key, where)
    if not isinstance(value, bool):
        raise LayerSchemaError(f"{where}: {key!r} must be an explicit boolean")
    return value


def _require_nullable_str(mapping: Mapping[str, Any], key: str, where: str) -> str | None:
    value = _require_key(mapping, key, where)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LayerSchemaError(f"{where}: {key!r} must be null or a non-empty string")
    return value


def _require_sha256(mapping: Mapping[str, Any], key: str, where: str) -> str:
    value = _require_non_empty_str(mapping, key, where)
    if not _SHA256_RE.match(value):
        raise LayerSchemaError(f"{where}: {key!r} must be a 64-char lowercase sha256 hex digest")
    return value


# ---------------------------------------------------------------------------
# per-layer validation
# ---------------------------------------------------------------------------


def _validate_assigned_statistical(dataset: Mapping[str, Any], where: str) -> None:
    split = _require_mapping(dataset, "split", where)
    split_where = f"{where}.split"
    _require_bool(split, "train_disjoint", split_where)
    _require_non_empty_str(split, "split_by", split_where)
    _require_non_empty_str(split, "note", split_where)
    for item_index, item in enumerate(dataset["items"]):
        item_where = f"{where}.items[{item_index}]"
        reference = _require_mapping(item, "reference", item_where)
        reference_where = f"{item_where}.reference"
        for field in ("reference_id", "source", "version"):
            _require_non_empty_str(reference, field, reference_where)
        for nucleus, signals in item["experimental"].items():
            for signal_index, signal in enumerate(signals):
                _require_non_empty_str(
                    signal,
                    "reference_label",
                    f"{item_where}.experimental.{nucleus}[{signal_index}]",
                )


def _validate_stereochemistry(dataset: Mapping[str, Any], where: str) -> None:
    external = _require_mapping(dataset, "external_reference", where)
    external_where = f"{where}.external_reference"
    _require_non_empty_str(external, "citation", external_where)
    accuracy = _require_key(external, "reported_accuracy", external_where)
    if accuracy is not None:
        valid_number = (
            not isinstance(accuracy, bool)
            and isinstance(accuracy, (int, float))
            and math.isfinite(float(accuracy))
            and 0.0 <= float(accuracy) <= 1.0
        )
        if not valid_number:
            raise LayerSchemaError(
                f"{external_where}: 'reported_accuracy' must be null or a number in [0, 1] "
                "(the original authors' value, never an ACP claim)"
            )
    _require_non_empty_str(external, "reported_accuracy_note", external_where)
    for item_index, item in enumerate(dataset["items"]):
        item_where = f"{where}.items[{item_index}]"
        true_label = _require_nullable_str(item, "true_stereoisomer_label", item_where)
        for candidate_index, candidate in enumerate(item["candidates"]):
            candidate_where = f"{item_where}.candidates[{candidate_index}]"
            _require_non_empty_str(candidate, "stereoisomer_label", candidate_where)
            _require_non_empty_str(candidate, "source", candidate_where)
        if item["true_structure_present"]:
            if true_label is None:
                raise LayerSchemaError(
                    f"{item_where}: true structure present requires a non-empty "
                    "'true_stereoisomer_label'"
                )
            true_id = item["true_structure_candidate_id"]
            referenced = next(
                candidate
                for candidate in item["candidates"]
                if candidate["candidate_id"] == true_id
            )
            if referenced["stereoisomer_label"] != true_label:
                raise LayerSchemaError(
                    f"{item_where}: true candidate {true_id!r} carries stereoisomer_label "
                    f"{referenced['stereoisomer_label']!r}, expected {true_label!r} from "
                    "true_stereoisomer_label"
                )
        elif true_label is not None:
            raise LayerSchemaError(
                f"{item_where}: true structure absent requires 'true_stereoisomer_label': null"
            )


def _validate_raw_spectra(
    dataset: Mapping[str, Any],
    where: str,
    spectra_fixture_ids: Collection[str] | None,
) -> None:
    for item_index, item in enumerate(dataset["items"]):
        item_where = f"{where}.items[{item_index}]"
        primary = _require_non_empty_str(item, "spectra_fixture_id", item_where)
        records = _require_key(item, "raw_spectra", item_where)
        if not isinstance(records, list) or not records:
            raise LayerSchemaError(f"{item_where}: 'raw_spectra' must be a non-empty list")
        record_ids: list[str] = []
        for record_index, record in enumerate(records):
            record_where = f"{item_where}.raw_spectra[{record_index}]"
            if not isinstance(record, Mapping):
                raise LayerSchemaError(f"{record_where}: must be a JSON object")
            fixture_id = _require_non_empty_str(record, "fixture_id", record_where)
            record_ids.append(fixture_id)
            _require_sha256(record, "fixture_sha256", record_where)
            nucleus = _require_non_empty_str(record, "nucleus", record_where)
            if nucleus not in NUCLEI:
                raise LayerSchemaError(f"{record_where}: nucleus {nucleus!r} not in {NUCLEI}")
            _require_non_empty_str(record, "annotations_ref", record_where)
            processing = _require_mapping(record, "processing", record_where)
            processing_where = f"{record_where}.processing"
            status = _require_non_empty_str(processing, "status", processing_where)
            if status not in _PROCESSING_STATUSES:
                raise LayerSchemaError(
                    f"{processing_where}: status {status!r} not in {_PROCESSING_STATUSES}"
                )
            metrics_ref = _require_key(processing, "metrics_ref", processing_where)
            note = _require_key(processing, "note", processing_where)
            if status == "measured":
                if not isinstance(metrics_ref, str) or not metrics_ref.strip():
                    raise LayerSchemaError(
                        f"{processing_where}: measured processing requires a non-empty "
                        "'metrics_ref'"
                    )
                if note is not None and (not isinstance(note, str) or not note.strip()):
                    raise LayerSchemaError(
                        f"{processing_where}: 'note' must be null or a non-empty string"
                    )
            else:
                if metrics_ref is not None:
                    raise LayerSchemaError(
                        f"{processing_where}: not_verified processing must carry "
                        "'metrics_ref': null (never a fabricated reference)"
                    )
                if not isinstance(note, str) or not note.strip():
                    raise LayerSchemaError(
                        f"{processing_where}: not_verified processing requires a non-empty 'note'"
                    )
                if not note.startswith(NOT_VERIFIED):
                    raise LayerSchemaError(
                        f"{processing_where}: not_verified note must start with {NOT_VERIFIED!r}"
                    )
            if spectra_fixture_ids is not None and fixture_id not in spectra_fixture_ids:
                raise ManifestReferenceError(
                    f"{record_where}: fixture_id {fixture_id!r} not in the todo-48 fixture id set"
                )
        if primary not in record_ids:
            raise LayerSchemaError(
                f"{item_where}: spectra_fixture_id {primary!r} must appear among the raw_spectra "
                f"fixture_ids {record_ids}"
            )


def _validate_boundary(dataset: Mapping[str, Any], where: str) -> None:
    for item_index, item in enumerate(dataset["items"]):
        item_where = f"{where}.items[{item_index}]"
        boundary = _require_mapping(item, "boundary", item_where)
        boundary_where = f"{item_where}.boundary"
        kind = _require_non_empty_str(boundary, "kind", boundary_where)
        if kind not in BOUNDARY_KINDS:
            raise LayerSchemaError(f"{boundary_where}: kind {kind!r} not in {BOUNDARY_KINDS}")
        status = _require_non_empty_str(boundary, "status", boundary_where)
        if status not in _BOUNDARY_STATUSES:
            raise LayerSchemaError(
                f"{boundary_where}: status {status!r} not in {_BOUNDARY_STATUSES}"
            )
        _require_non_empty_str(boundary, "reason", boundary_where)
        deferred_to = _require_nullable_str(boundary, "deferred_to", boundary_where)
        if status == "deferred" and deferred_to is None:
            raise LayerSchemaError(
                f"{boundary_where}: deferred boundary cases require a non-empty 'deferred_to' "
                "(the follow-up plan / scope that owns them)"
            )
        if status == "covered" and deferred_to is not None:
            raise LayerSchemaError(
                f"{boundary_where}: covered boundary cases must carry 'deferred_to': null"
            )


def _validate_layer_dataset(
    dataset: Mapping[str, Any],
    where: str,
    spectra_fixture_ids: Collection[str] | None,
) -> None:
    layer = dataset["layer"]
    if layer == "assigned_statistical":
        _validate_assigned_statistical(dataset, where)
    elif layer == "stereochemistry":
        _validate_stereochemistry(dataset, where)
    elif layer == "raw_spectra":
        _validate_raw_spectra(dataset, where, spectra_fixture_ids)
    elif layer == "boundary":
        _validate_boundary(dataset, where)
    else:  # pragma: no cover - the caller rejects non-2-5 layers first
        raise UnsupportedLayerError(f"{where}: layer {layer!r} is not loadable")


def _layer_dataset(dataset: Mapping[str, Any]) -> LayerDataset:
    dataset_id = str(dataset["dataset_id"])
    title = dataset["title"]
    if not isinstance(title, str) or not title.strip():
        raise LayerSchemaError(f"dataset {dataset_id!r}: 'title' must be a non-empty string")
    distribution = dataset["distribution"]
    return LayerDataset(
        dataset_id=dataset_id,
        layer=str(dataset["layer"]),
        title=title,
        source=str(dataset["source"]),
        license=str(dataset["license"]),
        version=str(dataset["version"]),
        content_hash=str(dataset["hash"]),
        distribution_note=str(distribution["note"]),
        items=tuple(dataset["items"]),
        raw=dataset,
    )


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def load_layer_datasets(
    path: str | Path,
    *,
    layers: Sequence[str] | None = None,
    spectra_fixture_ids: Collection[str] | None = None,
) -> tuple[LayerDataset, ...]:
    """Load and fully validate a layers 2-5 dataset manifest.

    Args:
        path: Manifest JSON path (a committed local file; never fetched).
        layers: Layers to load; ``None`` loads all of
            :data:`BENCHMARK_LAYERS_2_5`.  Every dataset in the file must
            belong to the requested set — the loader never silently skips a
            dataset.
        spectra_fixture_ids: When given, every ``spectra_fixture_id`` (item and
            raw-spectra record level) must resolve against this todo-48 fixture
            id collection; pass ``None`` to skip link resolution explicitly.

    Returns:
        One :class:`LayerDataset` per dataset, in manifest order.

    Raises:
        UnsupportedLayerError: unknown/not requested layer, or a ``synthetic``
            self-test manifest passed to a layer loader.
        LayerSchemaError: a layer-specific field is missing or invalid.
        MissingProvenanceError / DatasetHashMismatchError / ManifestSchemaError
        / ManifestReferenceError: from the todo-50 base validator.
    """
    requested = BENCHMARK_LAYERS_2_5 if layers is None else tuple(layers)
    if not requested:
        raise DatasetLoaderError("at least one benchmark layer must be requested")
    for layer in requested:
        if layer not in BENCHMARK_LAYERS_2_5:
            raise UnsupportedLayerError(
                f"unknown benchmark layer {layer!r}; loadable layers are {BENCHMARK_LAYERS_2_5}"
            )
    manifest = load_dataset_manifest(path, spectra_fixture_ids=spectra_fixture_ids)
    loaded: list[LayerDataset] = []
    for index, dataset in enumerate(manifest["datasets"]):
        where = f"datasets[{index}]"
        layer = dataset.get("layer")
        if layer not in requested:
            raise UnsupportedLayerError(
                f"{where}: dataset {dataset.get('dataset_id')!r} has layer {layer!r}, not in "
                f"requested {requested}; layer loaders never silently skip datasets"
            )
        _validate_layer_dataset(dataset, where, spectra_fixture_ids)
        loaded.append(_layer_dataset(dataset))
    return tuple(loaded)


def load_assigned_statistical(
    path: str | Path,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> tuple[LayerDataset, ...]:
    """Load the assigned-statistical layer (gap §10.3 layer 2)."""
    return load_layer_datasets(
        path, layers=("assigned_statistical",), spectra_fixture_ids=spectra_fixture_ids
    )


def load_stereochemistry(
    path: str | Path,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> tuple[LayerDataset, ...]:
    """Load the stereochemistry layer (gap §10.3 layer 3)."""
    return load_layer_datasets(
        path, layers=("stereochemistry",), spectra_fixture_ids=spectra_fixture_ids
    )


def load_raw_spectra(
    path: str | Path,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> tuple[LayerDataset, ...]:
    """Load the raw-spectra layer (gap §10.3 layer 4)."""
    return load_layer_datasets(
        path, layers=("raw_spectra",), spectra_fixture_ids=spectra_fixture_ids
    )


def load_boundary(
    path: str | Path,
    *,
    spectra_fixture_ids: Collection[str] | None = None,
) -> tuple[LayerDataset, ...]:
    """Load the boundary layer (gap §10.3 layer 5)."""
    return load_layer_datasets(path, layers=("boundary",), spectra_fixture_ids=spectra_fixture_ids)


__all__ = [
    "BENCHMARK_LAYERS_2_5",
    "BOUNDARY_KINDS",
    "LAYER_FIELD_SPECS",
    "LAYER_PROFILE",
    "DatasetLoaderError",
    "LayerDataset",
    "LayerFieldSpec",
    "LayerSchemaError",
    "UnsupportedLayerError",
    "load_assigned_statistical",
    "load_boundary",
    "load_layer_datasets",
    "load_raw_spectra",
    "load_stereochemistry",
]
