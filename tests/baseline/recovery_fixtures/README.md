# Cross-version recovery fixtures（跨版本恢复 fixtures）

Wave 0 / todo 1 of the acp→cccp architecture remediation (baseline `main@2a23b93`).
Generated from the **current** code's real formats via `generate_fixtures.py`
(same directory) so that post-migration code can be verified to still recover
from pre-migration state (A8: `continue` 不错误重算已完成步骤、不丢工件、不复用不兼容缓存).

Regenerate: `python3.11 tests/baseline/recovery_fixtures/generate_fixtures.py`
Byte-stable: no timestamps (batch manifest keeps `created_at`/`updated_at` empty),
no machine paths (`remote_work_dir` is a synthetic remote-style placeholder;
`plan_fingerprint.json` items are repo-relative; the v2 identity is
content-bound and never hashes item paths).  Regeneration must leave
`git diff` empty.  The batch SP cache filename is the sha256 geometry key —
its name is content-derived and deterministic.

**Two write paths, deliberately separated (acp-execution-integrity todo 14):**

* **Legacy v1 path (a–e) — byte-frozen.**  The five original fixtures
  reproduce the generation-time bytes exactly and must never change:
  `checkpoint_mixed`, `partial_failure`, `historical_manifest`,
  `batch_sp_cache`, `remote_path_reference`.  The v1 `checkpoint.json`
  predates `identity_schema` (todo 10), the `attempts`→`resume_count` rename
  (todo 11) and `StepState.result_ref` (todo 12), so it is written as the raw
  legacy payload — never through `write_checkpoint` / `StepState.to_dict()`,
  whose current output carries those newer keys.  `_plan_fingerprint` (the
  back-compat alias of `legacy_plan_fingerprint`) stays importable from the
  generator for this purpose.  `tests/test_recovery_fixtures_smoke.py`
  pins the sha256 of every frozen file (`test_v1_fixtures_byte_frozen`) and
  asserts the legacy `attempts` key (never `resume_count`/`identity_schema`).
* **v2 path (f–h) — controlled regeneration.**  `identity_schema=2`
  checkpoints, durable `step_result.json` and publication-sequence states,
  written with the CURRENT production writers (`write_checkpoint`,
  `write_step_result`, `publish_result`/`save_scientific_result`).
  Regenerating these is allowed and must stay byte-reproducible: fixed input
  content, no wall-clock time, no machine paths; `config_digest` is pinned to
  `null` (attempt metadata, never science) so executor runs in the smoke
  suite pin `current_config_digest` to `None` while adopting.

Loadability is smoke-checked by `tests/test_recovery_fixtures_smoke.py`
(must stay green; it exercises the CURRENT readers only; it never reads
`.omo/evidence` and depends on no wall-clock time or machine path —
executor scenarios run on tmp copies of the fixtures).

## Fixtures

### (a) `checkpoint_mixed/` — checkpoint with completed + incomplete steps coexisting
- `WORK/00_RUNTIME/checkpoint.json` — written by `acp.calculations.checkpoint.write_checkpoint`
  (`Checkpoint` payload: `plan_fingerprint`, `step_states`, `items_state`, `attempts`).
- Steps: `singlepoint` completed (energy −100.5), `optimize` completed (energy −100.75),
  `frequency` **failed** (synthetic error), `thermochemistry` **pending**;
  `items_state.__handoff__` carries the coordinate-handoff shape written by
  `CalculationPlanExecutor._persist_checkpoint`.
- `plan_fingerprint.json` — the fingerprint + the plan content
  (`_plan_fingerprint` consumes `str(step.spec)`; contract/defaults/repr changes can
  change it — future tests must verify explicit compatibility or versioning).
- **Future tests must verify**: `load_checkpoint(..., expected_fingerprint)` returns the
  checkpoint; resume/`continue` treats the two `completed` steps as done (no recompute),
  re-runs `failed`/`pending` steps, keeps artifacts, and either accepts the stored
  fingerprint or applies a documented versioned-compat rule.
- **Fingerprint rule (defined, todo 40 / A8)**: scheme `sha256-16` — first 16
  lowercase hex chars of sha256 over canonical JSON (`sort_keys`) whose step
  values embed `str(step.spec)`. It is the checkpoint identity; a spec/defaults
  change is a new identity. Verified by `tests/test_recovery_cross_version.py`
  (`test_plan_fingerprint_*`).

### (b) `partial_failure/` — partially-failed results
- `RESULT/result_manifest.json` — `ResultManifest` (v2) with `status="failed"` and the
  two products that survived the failure (structure + energy report).
- `batch_items_manifest.json` — `BatchCalculationManifest` (`batch_calculation_v1`) with
  two `completed` items and one `failed` item (with error text).
- **Future tests must verify**: partially-failed results stay readable, surviving
  products are not dropped on recovery/refresh, and failed items remain
  distinguishable from completed ones after migration.

### (c) `historical_manifest/` — historical manifests (must remain readable)
- `RESULT_v2/result_manifest.json` — pre-migration v2 `result_manifest.json`
  (becomes "historical" once the migration lands).
- `batch_calculation_manifest_v1.json` — legacy aggregate as accepted by
  `acp.compat.legacy.manifests.read_batch_calculation_manifest`.
- **Future tests must verify**: both shapes still load (v2 manifest + compat reader);
  no field silently dropped when the storage layer is re-pointed at `cccp.calculation`.

### (d) `batch_sp_cache/` — batch single-point cache entry
- `<cache_key>.json` — cache record as written by `acp.backends.batch._write_cache`
  (`{"energy_hartree", "output_ref"}`), file name = `_geometry_cache_key` of
  `cache_input.json` (fixed water geometry, `r2SCAN-3c`).
- `frames/frame_000/sp_output.log` — stub output so `_read_cache` can resolve
  `output_ref` (tracked via the `!tests/baseline/recovery_fixtures/**` gitignore exemption).
- **Cache-version rule (defined, todo 40 / A8):**
  - This entry is **legacy format version `0` / `legacy-geometry-key`**: no
    explicit version field; the filename *is* the identity.  The geometry key
    is sha256 over symbols + 10-decimal coordinates + charge + multiplicity +
    method + basis + solvent, so any spec/geometry change changes the key and
    an incompatible specification can never collide with an old entry.
  - **Old-cache reuse conditions (legacy reader `acp.backends.batch._read_cache`)**:
    the key-named file loads, `energy_hartree` is numeric, `output_ref` is a
    string, and the referenced output file exists.  A bare key match with a
    missing artifact is **rejected** (forces recompute).
  - **Not reusable by the new engine**: `cccp.calculation.batch` uses the
    explicit `CACHE_SCHEMA_VERSION` (currently `1`) and requires a full
    `CacheRecord` (identity digest / schema / complete / software version /
    artifacts + hashes).  A legacy geometry-key record lacks those fields, so
    `CacheRecord.from_dict` raises and `FileSystemCacheStore.read` returns
    `None` — an **explicit miss**, never an incompatible reuse.  Bumping
    `CACHE_SCHEMA_VERSION` invalidates all older-format records.
  - The key need not stay frozen forever; the rule above is the contract that
    keeps the *reuse* decision explicit.
- **Verified by**: `tests/test_recovery_cross_version.py`
  (`test_legacy_sp_cache_*`, `test_cccp_cache_*`).

### (e) `remote_path_reference/` — remote path reference
- `remote_job_paths.json` — sftp job record (`remote_work_dir`, `remote_job_id`,
  `node_id`, `storage_mode`) + the per-workflow catalog `rel_paths` taken from
  `acp.results.remote_structure_cache._CATALOG_FETCH_PATHS` at generation time.
- **Future tests must verify**: remote result reads still resolve through
  `_job_read_root` / `RemoteStructureCache` (`cache_path` must stay inside the
  cache root for every listed rel_path), and pending-fetch prefetch keys survive.

## v2 fixtures（acp-execution-integrity todo 14 — controlled regeneration）

Shared shape: the same content-bound SP→FREQ plan (`workflow=optimize`,
`profile=r2SCAN-3c`, one water item).  Each fixture carries
`structures/input.xyz` (fixed bytes) + `identity.json`
(`plan_identity` / `step_identities` / `plan_repr`); the identity is
computed from item **content** + effective science parameters only — paths,
`job_id`/`attempt`/`code_release` and execution-domain keys
(`output_dir`/`nproc`/…) never enter the hash.  The completed SP step is
recorded exactly as production contract C dictates: ① `WORK/05_SP/step_result.json`
(schema 1, `step_identity`, stable `step_id=step_0_singlepoint`, artifact
sha256 over `sp_output.log`) → ② checkpoint `result_ref` → ③ publication
files → ④ `publication_state.json` marker.

### (f) `v2_checkpoint_mixed/` — completed + incomplete mix, partial failure, publish complete
- `WORK/00_RUNTIME/checkpoint.json` — `identity_schema=2`,
  `plan_fingerprint = plan_identity`, `resume_count` key (v2 serialisation),
  SP `completed` (with `result_ref`) + FREQ `failed`; `items_state.__handoff__`
  carries `single_point_energy` + `energy_unit="hartree"` (V03).
- `RESULT/result_manifest.json` — task manifest with `status="failed"` and the
  surviving SP products (`step_0_singlepoint_output`,
  `step_0_singlepoint_energy`) — the v2 partial-failure shape.
- **Verified by** `test_v2_mixed_fixture_resumes_without_recompute` —
  executor resume adopts SP (QC count 0) and recomputes only the failed FREQ;
  product ids stay stable and duplicate-free.  Also the baseline for the
  crash-window scenarios (a–c below).

### (g) `v2_publish_interrupted/` — publish interruption (marker absent)
- Same checkpoint/step_result as (f) but FREQ `pending`; `WORK/05_SP/` holds
  `scientific_result.json` + `result_manifest.json` **without**
  `publication_state.json` (contract ③ never flipped) and the task-level
  `RESULT/result_manifest.json` is absent.
- **Verified by** `test_crash_window_published_not_marked_complete` — resume
  performs a publish-only retry (SP QC 0): the marker flips, the RESULT
  manifest is published exactly once, product ids are duplicate-free.

### (h) `v2_checkpoint_malformed/` — malformed `step_states` for stable-id validation
- Valid SP state (position 0, with `result_ref`), a **position-conflicting
  duplicate** (claims `step_0` while sitting at position 1), a **non-mapping
  corruption** (`"not-a-step-state"`) and an **extra §9.5 stability node**
  (`step_2_singlepoint`, index 2 beyond the 2-step plan) — 4 states for a
  2-step plan, so a pure-length check is meaningless.
- **Verified by** `test_malformed_checkpoint_validated_by_stable_step_id` —
  validation by stable `step_{index}_{kind}` ids accepts the SP state and the
  stability node, rejects the corrupt entries for rebuild, and a full executor
  run never raises `IndexError` nor reuses the mis-bound completed fact
  (FREQ recomputes; adopted SP QC stays 0).

### (i) `v2_casscf_not_converged/` — converged=false-completed CASSCF counterexample (plan todo 11)
- Single CASSCF-step plan (`workflow=casscf`, content-bound identity) whose
  `WORK/08_CASSCF/` carries BOTH durable receipts as a stale `completed`
  state: `step_result.json` (schema 1, `step_identity`, artifact sha256 over
  `casscf.log` + `active_space.json`, `config_digest: null`) and the
  publication-sequence `scientific_result.json` / `result_manifest.json` /
  `publication_state.json` — every record's CAS convergence fact
  (`metadata.multireference.converged`) is **false**; the checkpoint marks
  the step `completed` with its `result_ref`.
- **Verified by** (smoke) `test_v2_casscf_not_converged_never_adopted` —
  the `step_result.json` adoption entry refuses the receipt through the
  shared `validate_casscf_completion` validator (`recovery.step_not_adopted`
  + `cas_not_converged`) and conservatively recomputes; and
  `test_v2_casscf_not_converged_record_entry_never_restored` — with the
  receipt removed, the `scientific_result.json` publish-retry entry refuses
  through the SAME validator (`recovery.scientific_result_not_reusable`)
  instead of restoring `completed`.  Never regenerated (counterexample is
  frozen state).

## v1 recovery semantics frozen by the smoke suite

- **Default = conservative recompute + event.**  A schema-less (schema 1)
  checkpoint loads as `None` under the default
  `load_checkpoint(..., allow_legacy_fingerprint=False)` with the logger
  event `identity_unverifiable_legacy` — never a silent or raising reuse.
  The explicit batch-path switch (`allow_legacy_fingerprint=True`) is covered
  separately.  Mismatch/unknown-schema coverage is per-schema logger events
  (`identity_fingerprint_mismatch`, `identity_unknown_schema`) — the old
  `pytest.raises(CheckpointMismatchError)` semantics are gone (the exception
  class remains API-compat only, no load path raises it).
- **Fingerprint stability across todo-11-style field moves (Metis Q7)**:
  `test_fingerprint_stable_across_contract_field_moves` pins that the frozen
  v1 fingerprint still matches `legacy_plan_fingerprint` after the
  `attempts`→`resume_count` rename, the `StepState.result_ref` addition and
  the V03 handoff extension, and that the v2 identity ignores spec key order
  and execution-domain keys (machine paths) while changing on science order.
