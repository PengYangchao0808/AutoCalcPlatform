# NMR benchmark harness (todo 50 / gap §10.3)

Benchmark harness for the ACP NMR stack: dataset-manifest schema with typed
provenance validation, layered metrics, molecule-clustered paired confidence
intervals and a pre-registered threshold file. No QC binaries run here — the
dataset manifest supplies candidate predictions; the spectra-processing layer
reuses todo 48's `tests/nmr_spectra_benchmark.py` unchanged.

## Layout

| Path | Role |
|---|---|
| `scripts/nmr_benchmark.py` | runnable CLI entry (`main()` guarded) |
| `schema.py` | dataset-manifest schema + typed validation errors |
| `metrics.py` | metric suite (`evaluate_dataset`) |
| `bootstrap.py` | seeded molecule-clustered bootstrap + paired comparisons |
| `thresholds.py` | pre-registration, hash verification, re-freeze, comparison |
| `harness.py` | `run_harness` orchestration + provenance + spectra layer |
| `thresholds.json` | frozen, sealed thresholds (content hash) |
| `fixtures/synthetic_dataset.json` | synthetic self-test dataset (hash re-signed) |
| `test_harness.py` | acceptance suite |

```bash
# Default: synthetic self-test fixture + committed thresholds, metrics to stdout
PYTHONPATH=src python3.11 scripts/nmr_benchmark.py

# Write canonical metrics JSON (exit 1 if a threshold comparison fails)
PYTHONPATH=src python3.11 scripts/nmr_benchmark.py --output metrics.json

# Deterministic replay (byte-identical across processes)
PYTHONPATH=src python3.11 scripts/nmr_benchmark.py \
    --no-resources --now 2026-10-06T00:00:00+00:00 --output metrics.json

PYTHONPATH=src python3.11 -m pytest tests/benchmark/nmr/test_harness.py -q
```

## Dataset manifest schema (`acp-nmr-benchmark-dataset-manifest-v1`)

The full field documentation lives in `schema.py`'s module docstring. Summary:

* **per dataset**: `dataset_id`, `title`, `layer`
  (`synthetic|assigned_statistical|stereochemistry|raw_spectra|boundary`),
  `source`, `license`, `version`, `hash`, `distribution`
  (`note` + `balanced_by_construction`), `items`.
* **per item**: `item_id`, `molecule_id` (the bootstrap CLUSTER key),
  `true_structure_present` (explicit bool), `true_structure_candidate_id`
  (non-null iff present), optional `spectra_fixture_id` (todo-48 fixture id),
  `experimental` (nucleus → signals with `signal_id` / `observed_ppm` /
  optional `atom_label`), `candidates`.
* **per candidate**: `candidate_id`, `is_true_structure`, `status`
  (`valid|invalid|evidence_insufficient|unavailable|not_applicable|placeholder`),
  `dp4_probability`, `dp5_probability` (null = unavailable, never 0), `signals`
  (nucleus → signals with `signal_id` / `predicted_ppm` / optional
  `atom_label`).

Rules enforced (all violations are typed `ValueError` subclasses):

* missing `source` / `license` / `version` / `hash` →
  `MissingProvenanceError` (no silent defaults);
* `hash` must equal `sha256(canonical JSON of items)` →
  `DatasetHashMismatchError`;
* structural problems (bad schema, non-finite ppm, probabilities on
  non-valid candidates, duplicates, absent item carrying a true candidate)
  → `ManifestSchemaError`;
* dangling `true_structure_candidate_id` / `spectra_fixture_id` →
  `ManifestReferenceError`;
* exactly one candidate carries `is_true_structure=true` iff the true
  structure is present;
* non-`valid` candidates carry **no** probabilities; a `valid` candidate
  carries DP4 and may carry DP5 or null.

Re-signing a dataset after editing its `items`:

```python
from tests.benchmark.nmr import canonical_dataset_hash
dataset["hash"] = canonical_dataset_hash(dataset["items"])
```

## Metrics

`evaluate_dataset` produces one record per dataset. Metric-bearing blocks
carry a `status` of `measured` or `not_verified` (with `reasons`); missing
data is never a silent pass and never a fabricated number.

| Block | Contents |
|---|---|
| `shift_accuracy.{13C,1H}` | `mae`, `rmse`, `max_abs`, `mae_ci`, `rmse_ci` over the TRUE candidate's paired residuals |
| `assignment` | `accuracy` (predicted vs experimental `atom_label`), `n_scored`, `n_correct`, `n_unlabeled`, `accuracy_ci` |
| `ranking.top1_dp4` / `ranking.top1_dp5` | `accuracy` (true candidate ranked first), `n_items`, `n_excluded_items`, `ci` |
| `binary_probability.{dp4,dp5}` | positive/negative `brier`, `log_loss` (clamped at `1e-15`, `clamped` count), `prevalence`, 10-bin `calibration`, `distribution_note` |
| `true_structure_absence` | per-absent-item typed behavior + `confident_winner_rate` (top DP4 >= 0.5) |
| `refusal` | non-valid candidates by status, `refusal_rate`, `item_refusal_rate` |
| `counts`, `items` | cluster/item counts and per-item records (residuals, pairing, absence behavior) |

Per-item behavior when the true structure is absent is typed:
`{"status": "true_structure_absent", "top_candidate_id", "top_probability",
"confident"}` or `{"status": "refused", ...}` when no valid candidate exists.
The dataset `distribution.note` is copied into every binary-probability block
so a balanced synthetic set is never presented as a realistic prevalence.

Threshold metric keys (see `thresholds.py::KNOWN_METRIC_KEYS`):
`shift_mae_13c_ppm`, `shift_rmse_13c_ppm`, `shift_mae_1h_ppm`,
`shift_rmse_1h_ppm`, `assignment_accuracy`, `top1_dp4`, `top1_dp5`,
`brier_dp4`, `log_loss_dp4`, `brier_dp5`, `log_loss_dp5`, `refusal_rate`,
`item_refusal_rate`, `absent_confident_winner_rate`.

## Clustered bootstrap (molecule = cluster)

`clustered_bootstrap_ci(clusters, statistic=pooled_mean, n_resamples, seed,
alpha)` resamples **molecules** with replacement and pools their observations;
atoms/conformers of one molecule are never independent samples.
`paired_clustered_bootstrap_ci` requires identical cluster keys and draws one
resample set shared by both methods, so the delta interval is paired.
Defaults: `seed=20261006`, `n_resamples=2000`, `alpha=0.05`, percentile
method. The harness records the seed/resample settings in
`provenance.bootstrap`.

## Pre-registered thresholds (`thresholds.json`)

* Thresholds were frozen BEFORE the first metrics run; `preregistered_at` and
  the rationale per bound are recorded in the file.
* The file is sealed with `content_sha256` = SHA-256 of the canonical payload
  excluding that field. `load_thresholds` re-computes the hash and raises
  `ThresholdIntegrityError` on any mismatch — the harness refuses to compare
  against an edited threshold file.
* Re-freezing is the only sanctioned edit path: call
  `refreeze_thresholds(path, note=..., at=...)` with a non-empty
  re-measurement note. The previous content hash is appended to
  `remeasurements` so the edit history is auditable. Never edit the JSON by
  hand.
* Comparison statuses are three-state: `pass`, `fail`, `not_verified` (metric
  has no data). `not_verified` never counts as a pass; the CLI exits 1 only on
  `fail` (`--allow-threshold-failures` overrides).
* Threshold values are bounds for **benchmark datasets**, not for the
  synthetic self-test fixture; the fixture merely exercises the machinery.

## Determinism and provenance

* Canonical JSON (sorted keys, 2-space indent) via todo 48's
  `canonical_json_bytes` / `write_metrics` (atomic write).
* With `--now` pinned and `--no-resources` the whole payload is
  byte-identical across processes; with resource accounting enabled only the
  `resources` blocks and the timestamp are volatile (pinned by tests).
* Provenance per run: dataset manifest path/sha256, per-dataset declared
  hashes, threshold file sha256 + content hash + pre-registration timestamp,
  `git rev-parse HEAD` + package version, timestamp, bootstrap settings.
  Every dataset record echoes dataset hash / threshold hash / git head /
  timestamp.
* `resources` accounts wall seconds, user/system CPU seconds and peak RSS
  (`ru_maxrss`, Linux KiB → bytes); `measured: false` in deterministic mode.

## Handoff seams

* **todo 52 (protocol-transfer A/B)** — drive `run_harness` per configuration
  and compare with `paired_clustered_bootstrap_ci` on the metric blocks; the
  harness never changes more than one setting per run.
* **todo 53 (dataset loaders 2-5)** — emit manifests conforming to
  `MANIFEST_SCHEMA` (loader must reject missing license/hash with the typed
  errors) and pass them to `run_harness`; `spectra_fixture_id` links resolve
  against the todo-48 manifest.
* **todo 54 (reference-validation comparison)** — consume the same provenance
  shape and `write_metrics`; reference/migration side-by-side rows can reuse
  the per-item `residuals` / pairing records.

## Notes

* `fixtures/synthetic_dataset.json` is synthetic (CC0) and balanced by
  construction — its metric values are machinery checks, never model claims.
* The spectra layer requires the todo-48 fixtures; without `nmrglue` those
  fixtures are marked `not_verified` by todo 48 and the harness propagates
  that status (it never upgrades a skip to a pass).
