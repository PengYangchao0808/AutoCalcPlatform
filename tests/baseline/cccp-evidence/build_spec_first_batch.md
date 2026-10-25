# C08-s0 (T56 / C39a) — First-batch build & asset change spec (tracked)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 56
- **Base:** worktree `pyproject.toml` blob `25404ebd884066268a428948de8eb7d4cab23d22`
  (last commit touching it `af54329c6424989f940f26a246219e372484a447`); snapshot HEAD at
  spec time `02949631eb3fbae3ac48960d824ae7147707d19e` (shared dirty worktree — other lanes active).
- **Patch file:** `tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch`
  (sha256 `0059eb6b20d0ea05b231fdf3e8d0b854718b166789d59e6f2dd1ff23621f3450`)
- **Raw evidence log:** `tests/baseline/cccp-evidence/patches/c39a-build-proof.txt`
- **Scope:** **FIRST BATCH ONLY** — package-data so the `cccp` wheel ships the NMR/DP5 model
  assets at their single CCCP location. The **full release layer** (root LICENSE/NOTICE,
  `py.typed`, extras split, node-dep sync, multi-wheel ownership) is **T39**, not this file.

## 0. Status: 补丁就绪 (ready) vs 集成应用 (applied) — do not conflate

| Item | State |
|---|---|
| Patch text produced + `git apply --check` PASS | **补丁就绪** — this file + `patches/*.patch` |
| `pyproject.toml` modified in live tree | **NOT done** — C side is `B请求` (§4.8 of `write_registry.md`); B owns integration |
| Out-of-tree wheel build with the patch | **PASS** (temp dir only) — see §5 |
| B applies patch + T55 asset move + test update | **集成应用** — by B, must be one integration batch |
| Real 62.5 MiB assets shipped in a repo wheel | **NOT built** with real assets (62 MB copy skipped; mechanics proven with the existing `acp.nmr` rule + a synthetic asset) |

> Sequence (assumption, matches plan T55): `T56` (this spec, ready) → B40a applies patch →
> T55 relocates the 8 files `src/acp/nmr/models/` → `src/cccp/analysis/nmr/model_assets/` and
> switches all readers **in the same batch** → T39 completes the release layer (removes the
> stale `acp.nmr` line, adds root LICENSE/NOTICE/`py.typed`/extras). The added key globs
> nothing until T55 lands, so applying it slightly early is harmless; applying it **without**
> T55 leaves the current `acp.nmr` rule in force, so no regression either way.

## 1. Build / package-data patch text (B请求 — C cannot edit `pyproject.toml`)

### 1a. What already holds (no change needed)

`[tool.setuptools.packages.find]` already discovers the CCCP tree — package discovery is
**not** part of the first-batch delta:

```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["cccp*", "acp*", "acp_services*"]     # `cccp*` already covers cccp + cccp.analysis + cccp.analysis.nmr
```

Build A (§5) empirically confirms `cccp*` discovery: the wheel contains
`cccp/analysis/__init__.py` and `cccp/analysis/identity.py` from the concurrently-created
`src/cccp/analysis/` package with **no** pyproject change.

### 1b. The first-batch delta — add the CCCP asset package-data rule

The only substantive change: key `[tool.setuptools.package-data]` to the owning CCCP package
so an independently-built `cccp` wheel ships the DP5/FCHL artifacts once T55 moves them.

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -84,6 +84,16 @@
 # locked by tests/test_packaging_assets.py).
 [tool.setuptools.package-data]
 "acp.nmr" = ["models/*"]
+# --- C08-s0 (T56/C39a) first-batch CCCP asset rule -------------------------
+# T55 relocates the DP5/FCHL artifacts (atomic_reps.gz, frag_reps.gz, *.p,
+# tms_references.txt, LICENSE-DP5, NOTICE.md) from `acp/nmr/models/` to the
+# single CCCP asset location `src/cccp/analysis/nmr/model_assets/`
+# (a data dir with no __init__.py). Keying package-data to the owning CCCP
+# package makes an independently-built `cccp` wheel ship them. The glob
+# matches nothing until the T55 move lands, so this hunk is safe to apply in
+# the same integration batch as T55. T39 removes the stale `acp.nmr` line
+# above once the old directory is gone.
+"cccp.analysis.nmr" = ["model_assets/*"]
 
 [tool.pytest.ini_options]
 testpaths = ["tests"]
```

Notes:
- `"acp.nmr" = ["models/*"]` is **kept** in this batch (transition-safe: it matches nothing
  once T55 deletes `src/acp/nmr/models/`, and T39 removes the line). Not removing it here
  avoids a window where mid-migration trees still have the old dir but lose the rule.
- `cccp.analysis.nmr` must be a real package (has `__init__.py`) for `cccp*` discovery to pick
  it up — T55's new `src/cccp/analysis/nmr/__init__.py` satisfies this. `model_assets/` stays
  a data dir **without** `__init__.py` (mirrors today's `models/`).

### 1c. Applicability evidence (raw in `patches/c39a-build-proof.txt` §5–§7)

```
$ git apply --check --verbose tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch
正在检查补丁 pyproject.toml...
CHECK_EXIT=0
$ git apply --stat tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch
 pyproject.toml |   10 ++++++++++
 1 file changed, 10 insertions(+)
```

`git apply --check` runs directly against the **live worktree** `pyproject.toml` (verified
untouched: `git diff HEAD -- pyproject.toml` empty; worktree sha256
`0c24617eb1a6ef5970e159fccf89a211e42183ddbbf356d105be4097ff274829`). No pristine-copy
fallback was needed because no other lane has modified `pyproject.toml`.

### 1d. Required companion change (B请求 — packaging test)

`tests/test_packaging_assets.py` currently asserts against the **old** location; it must move
in the **same** integration batch or it will fail (its file-list becomes empty after T55):

- `test_package_data_covers_all_nmr_model_assets` (L147-158): read patterns from
  `["cccp.analysis.nmr"]` (not `["acp.nmr"]`) and iterate
  `ROOT/"src"/"cccp"/"analysis"/"nmr"/"model_assets"` with rel `model_assets/<name>`.
- `test_critical_nmr_assets_exist` (L161-164) and `_CRITICAL_ASSETS` path references: point at
  the CCCP asset dir.

This is C→B as a test patch request, not a live edit (test file is a shared packaging test).

## 2. Asset digest table — the 8 files (input list for T55 → B40a)

Location **today:** `src/acp/nmr/models/` · `du -sb` = **65,513,350 B** (≈62.5 MiB) \
`du -sh` = 63M · sum of file bytes = **65,509,254** (difference 4096 = directory entry) · 8 files.

| # | File | Bytes | sha256 | License / notice |
|---|---|---|---|---|
| 1 | `atomic_reps.gz` | 22,171,041 | `bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 2 | `frag_reps.gz` | 18,044,581 | `e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 3 | `i_w_kde_mean_s_0.025.p` | 24,353,668 | `2d523808d3859e8a48c695f5dbf8f9b9fe1fd3f02ee2dc70c8016414cf8f383e` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 4 | `folded_scaled_errors.p` | 851,489 | `c59bd8d3218b28db0e5dde3cf70911688ce530351c45f7930a4b470bd9d200dd` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 5 | `c_w_kde_mean_s_0.025.p` | 79,518 | `246025abffd97e32cc405d8956deaf3fd03e7426ff688e7052340135e79dc309` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 6 | `tms_references.txt` | 4,578 | `5ff2429aa73ade13aa6a98b3cc6433cc8eedd9d2a687c6b9fa907be03be7dcf7` | DP5 repo (MIT) — `NOTICE.md`/`LICENSE-DP5` |
| 7 | `LICENSE-DP5` | 1,175 | `55bc399b93914549e797365ac07b46527775f9aa28275ccb9fe43c4ebaf08146` | MIT text (PyDP4 v0.4, © 2015 Ermanis & Goodman) |
| 8 | `NOTICE.md` | 3,204 | `f99522ae9c481ceacba628e994cdd8a958b6febaa7ae66136020849cc6925ad0` | NOTICE/attribution (DP5 source revision `b6cf559007a5d13fe79654f37daf945ee1661a23`) |

- **License provenance:** all six `.gz`/`.p`/`.txt` artifacts redistributed from
  `Goodman-lab/DP5`, MIT (`LICENSE-DP5`); attribution in `NOTICE.md` (Smith & Goodman JACS
  2010 DP4; Howarth, Ermanis & Goodman Chem. Sci. 2021 DP5). Both license files travel with
  the assets — keep them in `model_assets/` and ship them in the wheel.
- **`dp5q_stub_params.json` is MISSING** and is registered in T2/T4 as a **gap, not a
  regression**. Do **not** synthesize it here; `dp5q_stub.py:110-111` must keep its
  baseline "params missing" state (T55 acceptance).
- Raw outputs: `patches/c39a-build-proof.txt` §2 (sha256sum), §3 (stat), §4/§4b (du + sum).

## 3. Verification commands for B40a (after B applies patch + T55 lands)

Run out-of-tree in a temp dir (never the repo), repository root as `$REPO`:

```bash
# 0) patch applied cleanly on B's branch
git -C "$REPO" apply --check tests/baseline/cccp-evidence/patches/c39a-pyproject-first-batch.patch

# 1) build the wheel out-of-tree
rm -rf /tmp/b40a-wheel && cd "$REPO"
python3.11 -m build --wheel --no-isolation --outdir /tmp/b40a-wheel .

# 2) wheel namelist: the 8 CCCP assets must be present at the single location
python3.11 - <<'PY'
import glob, zipfile
w = sorted(glob.glob("/tmp/b40a-wheel/*.whl"))[-1]
n = set(zipfile.ZipFile(w).namelist())
must = [
    "cccp/analysis/nmr/model_assets/atomic_reps.gz",
    "cccp/analysis/nmr/model_assets/frag_reps.gz",
    "cccp/analysis/nmr/model_assets/i_w_kde_mean_s_0.025.p",
    "cccp/analysis/nmr/model_assets/c_w_kde_mean_s_0.025.p",
    "cccp/analysis/nmr/model_assets/folded_scaled_errors.p",
    "cccp/analysis/nmr/model_assets/tms_references.txt",
    "cccp/analysis/nmr/model_assets/LICENSE-DP5",
    "cccp/analysis/nmr/model_assets/NOTICE.md",
]
missing = [m for m in must if m not in n]
assert not missing, f"MISSING assets in wheel: {missing}"
print(f"OK: all {len(must)} NMR/DP5 assets in {w}")
PY

# 3) in-wheel sha256 must equal the source digests in §2
python3.11 - <<'PY'
import glob, hashlib, zipfile
w = sorted(glob.glob("/tmp/b40a-wheel/*.whl"))[-1]
expected = {
    "cccp/analysis/nmr/model_assets/atomic_reps.gz": "bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65",
    "cccp/analysis/nmr/model_assets/frag_reps.gz": "e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64",
}
z = zipfile.ZipFile(w)
for name, want in expected.items():
    got = hashlib.sha256(z.read(name)).hexdigest()
    assert got == want, f"{name}: {got} != {want}"
    print("digest OK", name)
PY

# 4) stale rule cleanup check (T39): after T55 deletes src/acp/nmr/models/, either the
#    "acp.nmr" line is gone (T39) or it matches nothing (harmless) — both acceptable
python3.11 -c "import tomllib,sys; d=tomllib.load(open('$REPO/pyproject.toml','rb')); print(d['tool']['setuptools']['package-data'])"

# 5) packaging tests (after the L147/L161 companion update of §1d)
python3.11 -m pytest tests/test_packaging_assets.py -q

# 6) no-acp leakage check for the standalone cccp install (T40a/T54)
#    build cccp-only wheel, install into clean venv with --no-index, then:
python3.11 -c "import importlib.util,sys; assert importlib.util.find_spec('acp') is None; print('acp not discoverable: OK')"
```

## 4. Early node dependency increment (first batch)

**None yet.** The first batch is asset relocation + package-data only — it introduces **no new
runtime import** on compute nodes. `requirements-node.txt` already ships
`numpy>=2.1.0 rdkit>=2022.09.1 pyyaml>=6.0 scipy>=1.10 matplotlib>=3.7 openpyxl>=3.1
nmrglue>=0.10`; no addition is required and no line changes. (`qml`/`openbabel` stay
deliberately node-forbidden; unchanged.) If a later batch adds a `acp run`-reachable import,
the node sync request is T42 (HN-05), not this file.

## 5. Temp build proof (out-of-tree, raw in `patches/c39a-build-proof.txt` §8–§11)

Toolchain: `/usr/bin/python3.11` (3.11.13), `build 1.6.1`, `setuptools 65.5.1`,
`wheel 0.48.0` (installed to `/tmp/opencode/builddeps` for `--no-isolation`; no repo change).
Builds ran under `/tmp/opencode/t56-build/` — **never in the repo**.

| # | Tree | pyproject | Result | Namelist evidence |
|---|---|---|---|---|
| A | current tree copy | **patched** | wheel built | `acp/nmr/models/{atomic_reps.gz,frag_reps.gz,*.p,tms_references.txt,LICENSE-DP5,NOTICE.md}` present; `cccp/` 90 entries incl. `cccp/analysis/{__init__,identity}.py` |
| B | A + synthetic `src/cccp/analysis/nmr/{__init__.py,model_assets/atomic_reps.gz}` | **patched** | wheel built | `cccp/analysis/nmr/model_assets/atomic_reps.gz` **present** |
| Control | same synthetic tree as B | **pristine** (patch reverted) | wheel built | `cccp/analysis/nmr/model_assets/atomic_reps.gz` **ABSENT** |

Causality: B (patched) ships the asset **iff** the package-data key exists — Control (unpatched)
drops it. The synthetic asset stands in for the 62.5 MiB real assets (mechanics proof);
full-content assertion with real assets is deferred to T40a per plan.

## 6. QA summary

- **Patch applicability:** `git apply --check` → `CHECK_EXIT=0` (happy path PASS).
- **Build:** temp out-of-tree wheel builds with patched config (A/B/Control all exit 0).
- **Asset ship causality:** proven with patched vs pristine control.
- **Assets:** read-only digests via `sha256sum`/`stat`/`du -sb` (no asset file touched).
- **NOT_VERIFIED:** real 62.5 MiB assets inside a built wheel (deferred to T40a); the patch was
  not applied to the live tree (B请求 by design).

### Assumptions recorded

1. T55 target path is `src/cccp/analysis/nmr/model_assets/` per plan T55/T39; the package is
   `cccp.analysis.nmr`.
2. Patch applies in the same integration batch as T55 (key globs nothing until then; safe).
3. `tests/test_packaging_assets.py` companion update (§1d) rides with B40a.
4. `dp5q_stub_params.json` stays absent (registered gap) and is not part of the shipped set.
