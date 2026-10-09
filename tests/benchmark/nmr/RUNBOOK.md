# Benchmark dataset acquisition & run runbook — layers 2–5 (todo 53)

Gap reference: `.omo/evidence/acp-cccp-remediation/discovered/ACP_NMR_Goodman_Gap_Investigation_2026-10-05.md`
§10.3 (benchmark layering) and §12.2 (primary reference index).

## Scope and non-goals

* This runbook and `loaders.py` define the **layer 2–5 dataset contracts** and a
  small committed self-test fixture per layer. They validate provenance,
  per-layer fields and content hashes before a manifest reaches the harness.
* **The external dataset campaign is not executed in this plan.** Acquiring
  public datasets, running them through the ACP NMR stack and deriving any
  accuracy or calibration claim requires a **follow-up plan**; this document is
  its runbook input, not its result. Until that campaign is executed and
  reviewed, no number in this repository may be quoted as ACP accuracy.
* Loaders are pure, deterministic and **never fetch from the network**. They
  read one committed local manifest JSON, validate it, and return
  `LayerDataset` records (`load_layer_datasets`, `load_assigned_statistical`,
  `load_stereochemistry`, `load_raw_spectra`, `load_boundary`).
* The fixtures under `fixtures/layer{2,3,4,5}_*.json` are synthetic self-tests
  authored in-repo (CC0). They exercise the machinery only and are never
  model-performance evidence.

## Common dataset requirements (all layers)

| where | field | requirement |
|---|---|---|
| manifest | `schema` | `acp-nmr-benchmark-dataset-manifest-v1` |
| dataset | `dataset_id`, `title`, `layer`, `source`, `license`, `version` | non-empty strings |
| dataset | `hash` | 64-char lowercase sha256 of `canonical_dataset_hash(items)`; recomputed on every load — an edited items block raises `DatasetHashMismatchError` |
| dataset | `distribution.note` + `distribution.balanced_by_construction` | mandatory; a balanced set must say so and is never presented as a realistic prevalence |
| item | `item_id`, `molecule_id` | `molecule_id` is the bootstrap cluster key (atoms/conformers of one molecule are never independent samples) |
| item | `true_structure_present`, `true_structure_candidate_id` | explicit; candidate id non-null iff present |
| item | `experimental` | ≥1 nucleus with signals carrying `signal_id` and a finite `observed_ppm` |
| item | `candidates` | ≥1 candidate with `candidate_id`, `is_true_structure`, `status`; non-`valid` candidates carry `null` probabilities (never 0) |

Missing `source` / `license` / `version` / `hash` → `MissingProvenanceError`
(no silent defaults). `hash` mismatch → `DatasetHashMismatchError`. Dangling
`true_structure_candidate_id` / `spectra_fixture_id` → `ManifestReferenceError`.
Layer-specific violations → `LayerSchemaError`; a dataset whose layer is not
loadable / not requested → `UnsupportedLayerError` (loaders never silently skip
datasets).

## Layer 2 — assigned statistical set (`layer: "assigned_statistical"`)

Purpose (gap §10.3): fixed public correct/incorrect candidate pairs, including
negatives where the true structure is absent; the evaluation data must be
disjoint from any training data, split by molecular scaffold where possible.

| where | field | requirement |
|---|---|---|
| dataset | `split` | object with `train_disjoint` (explicit boolean), `split_by` (non-empty, e.g. `molecular_scaffold`) and `note` (non-empty) |
| item | `reference` | object with `reference_id` (external record id), `source` and `version`, all non-empty |
| experimental signal | `reference_label` | non-empty label in the reference source for that signal |

Source requirements: use a fixed public release whose `source`, `license` and
`version` are recorded verbatim; if the license does not permit
redistribution, commit only the manifest plus hashes and keep the payload
outside git. Re-sign the dataset hash after any items edit (see below).

## Layer 3 — stereochemistry set (`layer: "stereochemistry"`)

Purpose (gap §10.3): challenge cases with candidate vs true stereoisomer
labels (DP4-AI / DP5 published cases are candidate external references). The
original authors' reported accuracy is provenance, **never** an ACP accuracy
claim.

| where | field | requirement |
|---|---|---|
| dataset | `external_reference` | object with `citation` (non-empty), `reported_accuracy` (`null` or number in `[0, 1]`) and `reported_accuracy_note` (non-empty; must state the value is the original authors', not ACP's) |
| item | `true_stereoisomer_label` | non-empty string when `true_structure_present`; explicit `null` when absent |
| candidate | `stereoisomer_label` | non-empty; when the true structure is present the true candidate's label must equal the item's `true_stereoisomer_label` |
| candidate | `source` | non-empty (where this candidate structure came from) |

Source requirements: cite the paper/DOI and dataset version per gap §12.2 —
DP4 (JACS 2010, DOI `10.1021/ja105035r`), DP4-AI (Chem Sci 2020, DOI
`10.1039/D0SC00442A`), DP5 (Chem Sci 2022, DOI `10.1039/D1SC04406K`), the
fixed `Goodman-lab/DP5` commit `b6cf559007a5d13fe79654f37daf945ee1661a23`,
DP5q (Chem Sci 2026, PubMed 41859509). Verify and record the actual license
for every SI/challenge-case payload before use; nothing is assumed here.

## Layer 4 — raw spectra set (`layer: "raw_spectra"`)

Purpose (gap §10.3): real Bruker FID plus manual resonance/integration/
assignment annotations, so spectra processing, assignment and final ranking are
evaluated independently. A picked peak table is not DP4-AI-grade automation
acceptance.

| where | field | requirement |
|---|---|---|
| item | `spectra_fixture_id` | non-empty; the item's primary fixture, resolving against the todo-48 fixture id set when supplied |
| item | `raw_spectra` | non-empty list of raw-fixture records; `spectra_fixture_id` must appear among them |
| record | `fixture_id` | non-empty; must resolve against the todo-48 fixture id set when supplied |
| record | `fixture_sha256` | 64-char sha256 pin of the raw fixture content (the todo-48 fixture digest, or sha256 of the exact archive bytes) |
| record | `nucleus` | `1H` or `13C` |
| record | `annotations_ref` | non-empty reference to the manual resonance/integration/assignment annotation record |
| record | `processing.status` | `measured` or `not_verified` (three-state discipline; a skip is never a pass) |
| record | `processing.metrics_ref` | non-empty when `measured`; explicit `null` when `not_verified` |
| record | `processing.note` | `null` when `measured`; when `not_verified` a non-empty note starting with the literal `NOT_VERIFIED` |

Source requirements: instrument-exported data (with consent) or the committed
real fixture `tests/fixtures/bruker_real_group_delay/` (see its README for
provenance and the recorded original full-FID hash). Never scrape sites
without permission; record the acquisition date, instrument and original
archive hash. The committed synthetic layer-4 self-test links only to the
todo-48 synthetic fixtures and pins their real digests; its `metrics_ref` is a
`self-test://` URI, not a real metrics artifact.

## Layer 5 — boundary set (`layer: "boundary"`)

Purpose (gap §10.3): hard/out-of-scope cases — charged molecules, exchangeable
hydrogens, strong overlap, few carbons, halogens / training-domain edge, large
molecules, symmetry changes, truncated ensembles.

| where | field | requirement |
|---|---|---|
| item | `boundary.kind` | one of `charged`, `exchangeable_hydrogen`, `strong_overlap`, `few_carbons`, `halogen_or_domain_edge`, `large_molecule`, `symmetry_change`, `truncated_ensemble` |
| item | `boundary.status` | `deferred` or `covered` |
| item | `boundary.reason` | non-empty (why the case is deferred, or why/how it is covered) |
| item | `boundary.deferred_to` | non-empty when `deferred` (the follow-up plan/scope that owns it); explicit `null` when `covered` |

Source requirements: no external dataset is required to enumerate boundary
cases; deferral records are mandatory so no gap §10.3 case is silently
omitted. A deferred item may carry a candidate with `status: "unavailable"`
and `null` probabilities to state explicitly that no prediction was produced.

## Re-signing and integrity

The dataset `hash` covers only `items`; re-sign after every items edit:

```python
from tests.benchmark.nmr import canonical_dataset_hash

dataset["hash"] = canonical_dataset_hash(dataset["items"])
```

Loaders re-verify the hash on every load: an edited item with a stale hash
raises `DatasetHashMismatchError`. Per-fixture raw-content pins
(`fixture_sha256`) are compared by the acceptance suite against the live
todo-48 fixture digests, so a changed fixture fails loudly instead of going
stale silently.

## Running the loaders and the harness

```bash
# Loader acceptance (self-test fixtures + typed rejections + determinism)
PYTHONPATH=src python3.11 -m pytest tests/benchmark/nmr/test_dataset_loaders.py -q

# Validate a real layer manifest before any harness run
PYTHONPATH=src python3.11 -c "from tests.benchmark.nmr import load_layer_datasets; \
    print(load_layer_datasets('tests/benchmark/nmr/fixtures/layer2_assigned_statistical.json')[0])"

# Harness on a validated manifest (metrics are machinery output, not a claim)
PYTHONPATH=src python3.11 scripts/nmr_benchmark.py \
    --dataset-manifest tests/benchmark/nmr/fixtures/layer2_assigned_statistical.json
```

## Acquisition checklist for the follow-up campaign

1. Pick the source and record the citation, exact version/commit/DOI.
2. Record the license string verbatim; if redistribution is not permitted,
   keep the payload outside git and commit only the manifest + hashes +
   retrieval note.
3. Download only what the license permits; never fetch unauthorized data.
4. Compute `sha256` of the exact bytes/archives obtained; pin per-fixture
   `fixture_sha256` values.
5. Author the manifest with the per-layer fields above; explicit `null` where
   the schema allows it, never an invented value.
6. Re-sign the dataset `hash` with `canonical_dataset_hash(items)`.
7. Run the loader acceptance gate; fix typed rejections before proceeding.
8. Only then run the harness; report `measured` / `not_verified` honestly and
   keep the original authors' numbers separate from ACP results.

## Handoff

* **todo 54 (reference-validation comparison)** — gate manifests with
  `load_layer_datasets`, then feed the same path to `run_harness`; the
  `LayerDataset.content_hash` / `item_ids` / `molecule_ids` fields feed the
  side-by-side receipts.
* **Future campaign plan** — this runbook is the acquisition/run input; the
  campaign itself, and any accuracy/calibration statement, remains unexecuted
  until that plan runs.
