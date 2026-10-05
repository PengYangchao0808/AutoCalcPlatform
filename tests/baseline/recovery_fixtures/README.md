# Cross-version recovery fixtures（跨版本恢复 fixtures）

Wave 0 / todo 1 of the acp→cccp architecture remediation (baseline `main@2a23b93`).
Generated from the **current** code's real formats via `generate_fixtures.py`
(same directory) so that post-migration code can be verified to still recover
from pre-migration state (A8: `continue` 不错误重算已完成步骤、不丢工件、不复用不兼容缓存).

Regenerate: `python3.11 tests/baseline/recovery_fixtures/generate_fixtures.py`
Byte-stable: no timestamps (batch manifest keeps `created_at`/`updated_at` empty),
no machine paths (`remote_work_dir` is a synthetic remote-style placeholder;
`plan_fingerprint.json` items are repo-relative).  Regeneration must leave
`git diff` empty.  The batch SP cache filename is the sha256 geometry key —
its name is content-derived and deterministic.

Loadability is smoke-checked by `tests/test_recovery_fixtures_smoke.py`
(must stay green; it exercises the CURRENT readers only).

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
