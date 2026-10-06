"""
NMR benchmark harness package (todo 50).

Public seams for later consumers:

* todo 52 (protocol-transfer A/B) — ``run_harness`` / ``evaluate_dataset``
  metric blocks and ``paired_clustered_bootstrap_ci`` for paired deltas;
* todo 53 (dataset loaders 2-5) — ``load_dataset_manifest`` schema and typed
  provenance errors;
* todo 54 (reference-validation comparison) — ``run_harness`` provenance
  (dataset hash / threshold hash / code revision) and ``write_metrics``.

Thresholds are pre-registered in ``thresholds.json``; the loader verifies the
recorded content hash and refuses silent edits (re-freeze via
``refreeze_thresholds`` with an explicit re-measurement note).
"""

from __future__ import annotations

from tests.benchmark.nmr.bootstrap import (
    DEFAULT_ALPHA,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
    BootstrapError,
    BootstrapInterval,
    PairedBootstrapResult,
    clustered_bootstrap_ci,
    paired_clustered_bootstrap_ci,
    pooled_mean,
)
from tests.benchmark.nmr.harness import (
    DEFAULT_DATASET_MANIFEST,
    DEFAULT_THRESHOLDS_PATH,
    HARNESS_SCHEMA,
    NOT_VERIFIED,
    run_harness,
)
from tests.benchmark.nmr.loaders import (
    BENCHMARK_LAYERS_2_5,
    BOUNDARY_KINDS,
    LAYER_FIELD_SPECS,
    LAYER_PROFILE,
    DatasetLoaderError,
    LayerDataset,
    LayerFieldSpec,
    LayerSchemaError,
    UnsupportedLayerError,
    load_assigned_statistical,
    load_boundary,
    load_layer_datasets,
    load_raw_spectra,
    load_stereochemistry,
)
from tests.benchmark.nmr.metrics import (
    ABSENT_CONFIDENCE_THRESHOLD,
    CALIBRATION_BINS,
    LOG_LOSS_EPSILON,
    evaluate_dataset,
)
from tests.benchmark.nmr.schema import (
    CANDIDATE_STATUSES,
    LAYERS,
    MANIFEST_SCHEMA,
    NUCLEI,
    BenchmarkManifestError,
    DatasetHashMismatchError,
    ManifestReferenceError,
    ManifestSchemaError,
    MissingProvenanceError,
    canonical_dataset_hash,
    dataset_hashes,
    load_dataset_manifest,
    validate_dataset_manifest,
)
from tests.benchmark.nmr.thresholds import (
    KNOWN_METRIC_KEYS,
    THRESHOLDS_SCHEMA,
    ThresholdError,
    ThresholdIntegrityError,
    ThresholdSchemaError,
    compare_dataset_metrics,
    load_thresholds,
    metric_value,
    refreeze_thresholds,
    seal_thresholds,
    thresholds_payload_hash,
)
from tests.nmr_spectra_benchmark import canonical_json_bytes, write_metrics

__all__ = [
    "ABSENT_CONFIDENCE_THRESHOLD",
    "BENCHMARK_LAYERS_2_5",
    "BOUNDARY_KINDS",
    "BenchmarkManifestError",
    "BootstrapError",
    "BootstrapInterval",
    "CALIBRATION_BINS",
    "CANDIDATE_STATUSES",
    "DEFAULT_ALPHA",
    "DEFAULT_BOOTSTRAP_SEED",
    "DEFAULT_DATASET_MANIFEST",
    "DEFAULT_N_RESAMPLES",
    "DEFAULT_THRESHOLDS_PATH",
    "DatasetHashMismatchError",
    "DatasetLoaderError",
    "HARNESS_SCHEMA",
    "KNOWN_METRIC_KEYS",
    "LAYER_FIELD_SPECS",
    "LAYER_PROFILE",
    "LAYERS",
    "LOG_LOSS_EPSILON",
    "LayerDataset",
    "LayerFieldSpec",
    "LayerSchemaError",
    "MANIFEST_SCHEMA",
    "ManifestReferenceError",
    "ManifestSchemaError",
    "MissingProvenanceError",
    "NUCLEI",
    "NOT_VERIFIED",
    "PairedBootstrapResult",
    "THRESHOLDS_SCHEMA",
    "ThresholdError",
    "ThresholdIntegrityError",
    "ThresholdSchemaError",
    "UnsupportedLayerError",
    "canonical_dataset_hash",
    "canonical_json_bytes",
    "clustered_bootstrap_ci",
    "compare_dataset_metrics",
    "dataset_hashes",
    "evaluate_dataset",
    "load_assigned_statistical",
    "load_boundary",
    "load_dataset_manifest",
    "load_layer_datasets",
    "load_raw_spectra",
    "load_stereochemistry",
    "load_thresholds",
    "metric_value",
    "paired_clustered_bootstrap_ci",
    "pooled_mean",
    "refreeze_thresholds",
    "run_harness",
    "seal_thresholds",
    "thresholds_payload_hash",
    "validate_dataset_manifest",
    "write_metrics",
]
