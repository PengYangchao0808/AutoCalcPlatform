# T30 (C05-s6) — NMR asset & packaging closure final audit

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 30
- **Base:** fresh snapshot 2026-10-19 (shared dirty worktree — only T30 files staged)
- **Interpreter:** `/usr/bin/python3.11` (3.11.13)
- **Raw evidence:** `.omo/evidence/acp-parallel-plan-3-cccp/{t30-probe.json,t30-probe.err,t30-audit-raw.txt,t30-probe.py}` (gitignored, `.omo/`)
- **Tracked deliverables:** this file + `nmr_asset_manifest.md` (T39 input)
- **Closes:** GAP-6, GAP-9 (partial) · **Blocked by:** 26, 28, 29 · **Blocks:** 39, 42

> **This is an audit of the final state — T30 migrates nothing.** The asset relocation and
> reader switch were delivered by **T55** (`t55-verification-summary.md`); this task neither
> constitutes nor reports a merge prerequisite for T26 (review P0-2). It changes no asset
> content, checksum, or packaging file.

## 0. Tri-state summary

| Item | State | Basis |
|---|---|---|
| `sha256sum` 8/8 == pre-migration values | **PASS** | §2 |
| Single asset location, no second source copy | **PASS** | §4 |
| All five readers resolve via `cccp.analysis.nmr.assets` | **PASS** | §3 |
| No per-node "non-empty → None" digest degradation (T25–T29) | **PASS** | §5 |
| Source-env full probe | **PASS** | §3, §6 |
| Wheel-env cross-check (frozen `5518aa6`) | **VERIFIED via T40a citation** | §6 |
| Full wheel re-run at current HEAD | **NOT_VERIFIED** | T54 (post-B40a) |
| Packaging expected-RED window (2 tests) | **DOCUMENTED, not chased** | §7 |
| Real QC programs | **NOT_VERIFIED** | not needed |

## 1. Scope

Audit the *final* state after T55 moved the 8 DP5/FCHL artifacts
(`atomic_reps.gz`, `frag_reps.gz`, 3 × `*.p`, `tms_references.txt`, `LICENSE-DP5`, `NOTICE.md`)
from `src/acp/nmr/models/` to the single CCCP location
`src/cccp/analysis/nmr/model_assets/` and switched all readers to the unified entry point
`cccp.analysis.nmr.assets`. Confirm: one location (no `__init__.py`), no second copy, every
reader resolves through the entry, no digest regression across merged nodes T25–T29, and
produce the consolidated manifest for T39. **No migration, no checksum change, no packaging edit.**

## 2. Acceptance — `sha256sum` 8/8 identical

Fresh `sha256sum` (T30) == T55 pre-move == T55 post-move == T56 master. Full table in
`nmr_asset_manifest.md` §2. Artifact bytes: `du -sb` = **65,513,350 B**. **8/8 PASS.**

## 3. Reader resolution probes (cold-start subprocess)

Command (fresh `-B` interpreter, caches removed, `PYTHONPATH=src`):
`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3.11 -B t30-probe.py` → `EXIT=0`, empty stderr.

| Reader | Probe | Result | Unified entry proof |
|---|---|---|---|
| **models / TMS** (`_load_tms_table`) | table size | 96 | `models.tms_resolved_path` = `…/model_assets/tms_references.txt` |
| models / TMS | `lookup_tms_shieldings("mPW1PW91","6-311G(d)","chloroform")` | `(188.452125, 32.1243166667)` | matches T40a/T25 golden |
| models / TMS | unknown level | `(None, None)` | typed baseline (not a degradation) |
| **error_model** | `dp5_model_available()` | `True` | `error_model._MODELS_DIR` = unified dir |
| **fchl** | `fchl_assets_available()` | `True` | `fchl._MODELS_DIR` = unified dir |
| fchl | `load_atomic_reps()` shape | `(53208, 5, 86)` | gzip pickle from `atomic_reps.gz` |
| fchl | `load_atomic_reps(use_frag=True)` shape | `(63541, 5, 53)` | from `frag_reps.gz` |
| **reference_validation** | `asset_hashes()` | 6 non-None digests | `root = models_dir or asset_dir()`; `_NOTICE` path exists |
| reference_validation | asset_hashes digests | == manifest §2 (6 covered assets) | all non-empty |
| **dp5q_stub** | `dp5q_stub_status()` | `status=unavailable`, `reason=optional_dependency_missing`, `parameters_present=False` | `DP5Q_STUB_PARAMETERS_PATH == resolve_asset("dp5q_stub_params.json")` = **True** |
| **assets locator** | `asset_dir()` | `…/src/cccp/analysis/nmr/model_assets` | `is_dir=True`, `has_init=False` |
| assets locator | `resolve_asset(<8 names>)` | 8/8 `exists=True` | all paths under unified dir |

- **dp5q missing-parameter status preserved per baseline:** `dp5q_stub_params.json` absent →
  `parameters_present=False`; the path now resolves to the unified dir (T55 moved the pointer
  off the deleted legacy dir) while the absent-file baseline state is unchanged.
- Only `assets.py` uses `__file__` (`assets.py:41`); `grep __file__` over all NMR readers finds
  no other path derivation. `grep "import acp\|from acp"` over `assets.py` + the 5 readers = **NONE**.

## 4. Single location / no second copy

| Check | Result |
|---|---|
| `model_assets/__init__.py` | **absent** (data dir) |
| Union of 8 basenames under repo (excl. `.git`) | 8 hits at unified location only |
| Extra hits | `build/lib/acp/nmr/models/<name>` — **gitignored** (`.gitignore:7 build/`), untracked, dated 2026-09-06 (setuptools `build/` artifact predating the move; **not a source second copy**) |
| `git ls-files src/acp/nmr/models/` | **∅** — legacy dir empty & untracked |
| `ls src/acp/nmr/models/` | 0 files (empty dir note) |
| `src/acp/nmr/models.py` | compatibility **shim module** (re-exports `cccp.analysis.nmr.models`), not a data dir |
| Only path deriver | `cccp.analysis.nmr.assets` |

**No second content copy in the source tree.** The stale `build/` mirror is a build output, not
a source-of-truth and not tracked.

## 5. Per-node digest regression check (T25–T29)

Reconstructed from the per-node capture evidence in `.omo/evidence/acp-parallel-plan-3-cccp/`
(`t25-tms-{pre,post}.json`, `t25-serialization-{pre,post}.json`, `t26-capture-{pre,post}.json`,
`t27-paths-{pre,post}.json`, `t28-capture-{pre,post}.json`, `t29-capture-{pre,post}.json`,
`t{25,26,28}-coldstart-probe.txt`, `t27-probes.txt`, `t55-{pre,post}-probe.json`) plus the
T30 fresh probe as the terminal point.

| Node | Asset-relevant field | PRE | POST | Verdict |
|---|---|---|---|---|
| T25 | `tms_table_size` / `tms_table_sha256` | 96 / `799a3c30…597a` | 96 / `799a3c30…597a` | no change |
| T25 | TMS lookups (6 combos) | non-empty | identical | no change (pre==post file IDENTICAL) |
| T25 | serialized `tms_13c`/`tms_1h` | 188.452125 / 32.1243166667 | identical | no change |
| T26 | `dp5_model_available` | True | True | no change |
| T26 | `model_probability` | 0.6569411645020534 | identical | no change |
| T26 | `fchl_assets_available` | True | True | no change |
| T26 | `atomic_reps_shape` / `frag_reps_shape` | (53208,5,86)/(63541,5,53) | identical | no change |
| T26 | `fragment_path_status` | `openbabel-missing` (fields `None`) | `openbabel-missing` (`None`) | **baseline preserved** — was already `None` pre (openbabel absent), not a non-empty→None drop |
| T27 | processor module ids | `acp.nmr.{carbon,proton}_processor` | `cccp.analysis.nmr.{carbon,proton}_processor` | path move only; no asset digest |
| T27 | nmrglue-missing degradation | typed | typed | baseline preserved |
| T28 | `asset_hashes` (6) | 6 non-None digests | identical | no change (pre==post file IDENTICAL) |
| T28 | identity `{acp,cccp}_hash` | `v2:ebc8587b…` | identical | no change |
| T29 | `dp5q_params_exists` | False | False | baseline preserved |
| T29 | `dp5q_params_path` | `acp/nmr/models/…` | `cccp/…/model_assets/…` | path move only; absent→absent |
| T29 | report/revision/method_config module ids | `acp.nmr.*` | `cccp.analysis.nmr.*` | path move only |

**No merged node exhibits a non-empty → `None` asset-digest degradation.** `t25-tms`,
`t25-serialization`, `t26-capture`, `t28-capture` are byte-identical pre==post; `t27-paths` and
`t29-capture` differ only in module path strings (and T29 source-file sha256, expected for files
edited during migration — **not** an asset digest field). The T29 report trio still emits
JSON(schema v2)+XLSX+PNG (`t29-report-trio.txt`) and typed degradation when openpyxl/matplotlib
are absent (`t29-qa-missing-deps.txt`, `QA_PROBE_RESULT: PASS`).

**Terminal confirmation (T30 fresh):** `t55-{pre,post}` and the T30 probe agree — digest_dir
moved to the unified location, all digests/dimensions/values unchanged.

## 6. Source-env vs wheel-env evidence

| Evidence class | State | What was actually run/cited |
|---|---|---|
| **SOURCE (current HEAD) — full probe** | **VERIFIED (T30, fresh)** | `sha256sum` 8/8; `du -sb`; cold-start reader probes (§3); `verify_cccp_isolation.py` 15/15; `verify_cccp_slice_guard.py` 5/5; acceptance pytest |
| **WHEEL (frozen commit `5518aa6`) — cross-check** | **VERIFIED (T40a, cited — not re-run by T30)** | `git archive HEAD src/cccp` snapshot; out-of-tree `cccp-0.2.0-py3-none-any.whl` sha256 `bf0a44ce…31c3ca0a2`; namelist **8/8** assets at `cccp/analysis/nmr/model_assets/`, in-wheel digests == T56 master; **0** `acp*` entries (178 total); clean venv `find_spec("acp") is None`; scoped samples 16/16. T41 §4 cross-checks declaration vs namelist with zero diff. |
| **WHEEL re-run at current HEAD** | **NOT_VERIFIED** | T54 candidate re-verification (after B40a applies the patches + integration candidate freezes) |
| **Packaging under live tree (patch not applied)** | **expected-RED documented** | §7 — driven by B40a, not a T30 defect |
| **Real QC programs** | **NOT_VERIFIED** | out of scope |

A full wheel re-run at the current HEAD is T54's, not T30's. T30 does not claim it.

## 7. Packaging two-state + B change request (restated precisely)

**B request (no live `pyproject.toml`/test edit by C):**

1. **Patch A** — `tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch`
   (T56/C39a). Adds to `[tool.setuptools.package-data]`:
   ```toml
   "cccp.analysis.nmr" = ["model_assets/*"]
   ```
   Keeps the stale `"acp.nmr" = ["models/*"]` line (transition-safe; T39 removes it after the
   legacy dir is gone). `git apply --check` re-run by T30 → **EXIT 0**; **NOT applied**.
2. **Patch B** — `tests/baseline/cccp-evidence/patches/t55-c39a-packaging-test.patch`.
   Moves `test_package_data_covers_all_nmr_model_assets` /
   `test_critical_nmr_assets_exist` to read `["cccp.analysis.nmr"]` and iterate
   `src/cccp/analysis/nmr/model_assets/` with rel `model_assets/<name>`.
   `git apply --check` re-run by T30 → **EXIT 0**; **NOT applied**.
3. **B40a must apply both patches in the same integration batch as T55's asset move.** T39
   then owns the full release layer (remove stale `acp.nmr` line, root LICENSE/NOTICE, `py.typed`,
   extras split).

**Live-tree state (no patch applied):**

```
$ python3.11 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['tool']['setuptools']['package-data'])"
{'acp.nmr': ['models/*']}
$ python3.11 -m pytest tests/test_packaging_assets.py -q
2 failed, 5 passed
  FAILED test_package_data_covers_all_nmr_model_assets   (AssertionError: expected ... artifacts ... assert [])
  FAILED test_critical_nmr_assets_exist                  (AssertionError: missing NMR model asset: atomic_reps.gz)
```

This **expected-RED window (2 tests)** is the designed transition state until B40a applies both
patches; it is **documented, not chased** (T55 §4).

## 8. Acceptance run

| Command | Result |
|---|---|
| `python3.11 -m pytest tests/test_acp_nmr_dp5q_isolation.py tests/test_acp_nmr_fchl_golden.py tests/test_cccp_isolation.py -q` | **41 passed, 1 skipped** |
| `python3.11 scripts/verify_cccp_slice_guard.py` | **5/5 PASS** (`RESULT: PASS`, EXIT 0) |
| `python3.11 scripts/verify_cccp_isolation.py` | **15/15, leaked none, EXIT 0** |
| `sha256sum` 8/8 vs pre-migration | **PASS** (§2) |
| Cold-start reader probes | **PASS** (§3) |
| No second copy | **PASS** (§4) |
| Per-node no non-empty→None | **PASS** (§5) |
| `tests/test_packaging_assets.py` (expected-RED) | **2 failed, 5 passed** (documented, B40a) |

## 9. QA verdict

- happy: **8/8 hashes identical + 5 reader probes + no second copy + no per-node regression + manifest/B-request/docs.**
- failure check: no digest regression found; no second source copy found; no non-empty→None
  degradation found → **no FAIL, no T55 back-link required.**
- The only negative observation (live-tree packaging 2-test RED) is the *expected* pre-B40a
  window, not a T30 finding.
