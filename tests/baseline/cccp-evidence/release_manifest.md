# C08-s1b (T39 / C39b) — Full-release build spec, final change request & multi-wheel ownership (tracked)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 39 (C08-s1b / C39b)
- **Base (fresh snapshot):** HEAD `b64dec651ea28446940f24034770e5ac08f58ab4`;
  worktree `pyproject.toml` sha256 `0c24617eb1a6ef5970e159fccf89a211e42183ddbbf356d105be4097ff274829`
  (git blob `25404ebd884066268a428948de8eb7d4cab23d22`; last commit touching it `f5f7064`).
  Shared dirty worktree — only T39 files staged by this task.
- **Scope:** **FULL RELEASE LAYER ONLY.** First-batch rolling packaging (package-data so the
  `cccp` wheel ships the NMR assets) is **T56/C39a**; asset relocation is **T55**. This file
  does the full-release manifest, the precise `pyproject.toml`/build-config change request
  (`B请求`), node-dependency sync **points**, and the multi-wheel file-ownership ruling.
- **Deliverables:**
  - `patches/t39-01-pyproject-ownership.patch` — root `pyproject.toml` M2 ownership delta.
  - `patches/t39-02-cccp-pyproject.patch` — new `packaging/cccp/pyproject.toml` (independent CCCP release config).
  - `patches/t39-build-proof.txt` — raw reproducible build proof (patches → temp wheels → namelist asserts).
- **Consumes:** T56 `build_spec_first_batch.md` (§1/§2/§3) · T30 `nmr_asset_manifest.md` (§2 digest table)
  · T40a `.omo/evidence/acp-parallel-plan-3-cccp/task-40a-*.md` (§4–§7 wheel + clean-env install)
  · T41 `license_availability_statement.md` (§2/§4) · T55 `t55-digest-table.md` · main plan §10/§18.1/§28.3.
- **Status:** change request = **补丁就绪 (ready)**; live `pyproject.toml` = **NOT modified** (`B请求`);
  temp out-of-tree build with the requested text = **PASS** (both wheels); B integration = **pending**.

---

## 0. Status: 补丁就绪 (ready) vs 集成应用 (applied) — do not conflate

| Item | State |
|---|---|
| This manifest + `t39-0{1,2}` patches produced, `git apply --check` PASS | **补丁就绪** (this file) |
| Live `pyproject.toml` / `requirements-node.txt` modified | **NOT done** — C side is `B请求` (`write_registry.md §4.8`); B owns integration |
| Out-of-tree wheel builds with the requested text (both distributions) | **PASS** (temp dir only) — `patches/t39-build-proof.txt` |
| acp packaging test RED window (`tests/test_packaging_assets.py`, 2 tests) | **pending cross-lane** — resolved by the B40a sequence in §3.4 (c39a + T55 test patch + T39) |
| Real `cccp` wheel published to an index + co-install of both wheels | **NOT done / NOT_VERIFIED** (T40b/T54 own full-release verification) |

> **No private online algorithms:** every dependency below is a public PyPI package; the release
> needs no account, network service, IAM, billing, Web, or frontend. The `cccp` wheel's runtime
> science samples run fully offline (T40a §6/§7, `--no-index` wheelhouse).

---

## 1. Full release manifest

### 1.1 Package discovery

| Target distribution | Discovery rule | Tracked packages | Tracked `.py` | Owner |
|---|---|---|---|---|
| `cccp` (independent science) | `include = ["cccp*"]`, `exclude = ["acp*", "acp_services*"]` | 23 (`cccp`, `cccp.*`) | 176 | CCCP (route C) |
| `acp` (compat product + CLI) | `include = ["acp*", "acp_services*"]` | 24 + 1 (`acp.*`, `acp_services.*`) | 270 + 57 | ACP/ACR (route B) |

- Root `pyproject.toml` today discovers `["cccp*", "acp*", "acp_services*"]` in **one** wheel
  (`name = "acp"`). The full-release rule (§3) splits ownership: the `acp` wheel drops `cccp*`
  and depends on `cccp==0.2.0`; the independent `cccp` wheel owns `cccp/**`.
- `python -m cccp` remains unsupported; `cccp` publishes **no** console script.

### 1.2 Dependencies (core + science-required extras)

Public, no Web/IAM/billing in the science minimal package.

| Distribution | Core `dependencies` | `nmr` extra (science-optional) |
|---|---|---|
| `cccp` | `numpy>=2.1.0`, `rdkit>=2022.09.1`, `pyyaml>=6.0`, `scipy>=1.10`, `matplotlib>=3.7`, `openpyxl>=3.1` | `nmrglue>=0.10`, `openbabel>=3.1`, `qml>=0.4` |
| `acp` | above + `cccp==0.2.0`; extras `api` / `remote` / `dev` / `browser` unchanged | inherits `cccp[nmr]` when the science NMR path is needed |

- `openpyxl` is **core** (XLSX report writer; guarded by `tests/test_packaging_assets.py::test_openpyxl_is_a_core_dependency`).
- `qml` only builds against `numpy<2`; FCHL-weighted DP5 degrades to the documented unweighted-KDE
  fallback (`cccp/analysis/nmr/error_model.py`). Not shipped to nodes (see §4).
- **Web/IAM/billing exclusion:** `fastapi`/`uvicorn`/`python-multipart`/`zipstream-ng`/`paramiko`
  stay in the `acp` extras only; none is a `cccp` dependency.

### 1.3 Assets — `cccp/analysis/nmr/model_assets/*` (ship in the `cccp` wheel)

Single location; `du -sb` = **65,513,350 B**; 8 files; no `__init__.py` (package *data*).
Hashes re-verified at this task's fresh snapshot (== T30 §2 == T55 pre/post == T56 master).

| # | File | Bytes | sha256 | Source | License / notice |
|---|---|---|---|---|---|
| 1 | `atomic_reps.gz` | 22,171,041 | `bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65` | Goodman-lab/DP5 @ `b6cf5590…` | MIT (`LICENSE-DP5`) + `NOTICE.md` |
| 2 | `frag_reps.gz` | 18,044,581 | `e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64` | idem | idem |
| 3 | `i_w_kde_mean_s_0.025.p` | 24,353,668 | `2d523808d3859e8a48c695f5dbf8f9b9fe1fd3f02ee2dc70c8016414cf8f383e` | idem | idem |
| 4 | `folded_scaled_errors.p` | 851,489 | `c59bd8d3218b28db0e5dde3cf70911688ce530351c45f7930a4b470bd9d200dd` | idem | idem |
| 5 | `c_w_kde_mean_s_0.025.p` | 79,518 | `246025abffd97e32cc405d8956deaf3fd03e7426ff688e7052340135e79dc309` | idem | idem |
| 6 | `tms_references.txt` | 4,578 | `5ff2429aa73ade13aa6a98b3cc6433cc8eedd9d2a687c6b9fa907be03be7dcf7` | idem | idem |
| 7 | `LICENSE-DP5` | 1,175 | `55bc399b93914549e797365ac07b46527775f9aa28275ccb9fe43c4ebaf08146` | PyDP4 v0.4 (© 2015 Ermanis & Goodman) | MIT text body |
| 8 | `NOTICE.md` | 3,204 | `f99522ae9c481ceacba628e994cdd8a958b6febaa7ae66136020849cc6925ad0` | DP5 attribution | NOTICE / citations |

- Resolved only via `cccp.analysis.nmr.assets.asset_dir()` / `resolve_asset(name)` (single path deriver).
- `dp5q_stub_params.json` is **absent** (registered T2/T4 baseline gap — not a shipped asset, not a regression).
- No unverified asset is claimed: the 8-file set is exactly what the T40a wheel shipped (T40a §5).

### 1.4 Licenses / notices

| Item | Path | Bytes | sha256 | Disposition |
|---|---|---|---|---|
| Project LICENSE (MIT) | `LICENSE` | 1,066 | `7e7e835d97b8b90aef4b5be522aaeb52f8141c842df8f2c1e46e30746eada37d` | Ships in both wheels (`license = {text = "MIT"}` + default `license-files` glob picks up root `LICENSE`; the `cccp` build recipe stages it — §3.2) |
| DP5 license | `src/cccp/analysis/nmr/model_assets/LICENSE-DP5` | 1,175 | `55bc399b…08146` | Ships as package data inside the `cccp` wheel |
| DP5 notice | `src/cccp/analysis/nmr/model_assets/NOTICE.md` | 3,204 | `f99522ae…5ad0` | Ships as package data inside the `cccp` wheel |
| `py.typed` (PEP 561) | `src/cccp/py.typed` / `src/acp/py.typed` | 0 | `e3b0c442…5b855` | Explicit package-data in each owning distribution |

- Full license/availability statement (own code MIT / model assets MIT + notice / external ORCA·xTB·CREST·CENSO·ISOSTAT·Shermo "external, not redistributed"): **T41 `license_availability_statement.md`** — this manifest does not restate it.
- There is **no root `NOTICE.md`**; the authoritative attribution notice travels inside `model_assets/` with the assets (T30 §3). Not claiming otherwise.

### 1.5 Python version

| Field | Value | Source |
|---|---|---|
| `requires-python` | `>=3.10` | root `pyproject.toml:10`; mirrored in the new `packaging/cccp/pyproject.toml` |
| Classifiers | `Programming Language :: Python :: 3.10` | both distributions |
| Verified interpreter (temp builds) | `/usr/bin/python3.11` (3.11.13) | `t39-build-proof.txt` |
| Version constant (4-way sync) | `0.2.0` | `cccp/__init__.py`, `cccp/version.py`, `acp/__init__.py`, `pyproject.toml` (ANTI #2) |

---

## 2. Multi-wheel file-ownership ruling (frozen)

**Rule: every installed path has exactly one owning distribution — no `cccp/**` file may be
installed by the `acp` wheel, and no `acp/**`/`acp_services/**` file may be installed by the
`cccp` wheel.** The full-release (M2) request in §3 enforces this.

| Distribution | Owns (sole owner) | Must NOT ship | Console script | Depends on |
|---|---|---|---|---|
| `cccp` (science) | `cccp/**` (incl. `cccp/analysis/nmr/model_assets/*`, `cccp/py.typed`) | `acp*`, `acp_services*`, `frontend`, `acp` script, Web/IAM/billing deps | **none** (never add `python -m cccp`) | core science deps + `nmr` extra |
| `acp` (compat product + CLI) | `acp/**` + `acp_services/**` (until the ACR split) | `cccp/**` | `acp = acp.cli:main` | `cccp==0.2.0` + `api`/`remote`/`dev`/`browser` extras |

- **Services/CLI ownership:** the `acp` console entry point belongs to the ACP/ACR CLI carrier
  (main plan §10: "CLI 在产品归属上是 ACR 的命令行入口"); `src/acp_services/**` is the
  Services/ACR lane's package (Plan_2) and is **never** part of the CCCP science wheel.
- **Transitional exception (M1, time-boxed):** until a `cccp` artifact exists on the target
  index, the current single-repo `acp` wheel MAY bundle `cccp*` (the T56/c39a first-batch
  state). When it does, the standalone `cccp` wheel **must not** be co-installed with it
  (mutually exclusive install modes). M2 removes this exception.
- **Enforcement evidence:** M2 temp build — `acp` wheel namelist has **zero** `cccp/` entries;
  `cccp` wheel namelist has **zero** `acp*` entries (`t39-build-proof.txt` §4).

---

## 3. Change request (`B请求`) — paste-ready patches + verification

> C cannot edit `pyproject.toml`/`requirements-node.txt` (`write_registry.md §4.5/§4.8`); B integrates.
> **Integration sequence (recommended, matches the plan):**
> `c39a-pyproject-first-batch.patch` (T56) → `t55-c39a-packaging-test.patch` (T55 test) →
> `t39-01-pyproject-ownership.patch` → `t39-02-cccp-pyproject.patch`.
> `t39-01` supersedes the transitional `"cccp.analysis.nmr"` package-data of c39a at M2 (that
> rule stays harmless if left; the acp wheel no longer builds `cccp`). Applying c39a alone
> keeps the M1 single-wheel state working.

### 3.1 `patches/t39-01-pyproject-ownership.patch` (root `pyproject.toml`)

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -35,6 +35,11 @@
     # XLSX can never be silently skipped without a capability declaration.
     # Pure-python, no compiled wheels — safe on slim compute nodes too.
     "openpyxl>=3.1",
+    # --- C08 (T39) multi-wheel ownership: the `acp` distribution no longer
+    # ships cccp/** — the independent `cccp` wheel (packaging/cccp/pyproject.toml)
+    # is the sole owner. Declared here as a runtime dependency so the platform
+    # still resolves the science package without installing cccp/** twice. ---
+    "cccp==0.2.0",
 ]
 
 [project.optional-dependencies]
@@ -73,17 +78,20 @@
 
 [tool.setuptools.packages.find]
 where = ["src"]
-include = ["cccp*", "acp*", "acp_services*"]
+# --- C08 (T39) multi-wheel ownership ---------------------------------------
+# The `acp` distribution owns `acp/**` + `acp_services/**` only. `cccp/**`
+# belongs to the independent `cccp` wheel (packaging/cccp/pyproject.toml);
+# `cccp*` is no longer discovered here so no path under `cccp/` can ever be
+# installed by two wheels at once.
+include = ["acp*", "acp_services*"]
 
-# Ship non-.py package data. `acp/nmr/models/` is a DATA DIRECTORY (no
-# __init__.py — see src/acp/nmr/AGENTS.md): DP5/FCHL binary artifacts
-# (atomic_reps.gz, frag_reps.gz, *.p, tms_references.txt, LICENSE-DP5,
-# NOTICE.md) loaded at runtime via acp.nmr.fchl / error_model. Without this
-# entry setuptools only packs *.py and wheels silently lose ~64MB of model
-# assets (wheel check: `atomic_reps.gz` must appear in the namelist —
-# locked by tests/test_packaging_assets.py).
+# Ship non-.py package data. The DP5/FCHL model assets moved to the CCCP-owned
+# `src/cccp/analysis/nmr/model_assets/` location (plan T55) and now ship inside
+# the independent `cccp` wheel, not here. Only `acp/py.typed` must stay packed
+# for the compatibility distribution. (The former `"acp.nmr" = ["models/*"]`
+# rule is removed: its directory is empty after T55.)
 [tool.setuptools.package-data]
-"acp.nmr" = ["models/*"]
+"acp" = ["py.typed"]
 
 [tool.pytest.ini_options]
 testpaths = ["tests"]
```

### 3.2 `patches/t39-02-cccp-pyproject.patch` (new `packaging/cccp/pyproject.toml`)

Paste-ready **new file** (full text is the patch body at
`tests/baseline/cccp-evidence/patches/t39-02-cccp-pyproject.patch`):

The independent CCCP release config (name `cccp`, `include=["cccp*"]`,
`exclude=["acp*","acp_services*"]`, `package-data {"cccp.analysis.nmr":["model_assets/*"],
"cccp":["py.typed"]}`, science core deps + `nmr` extra, `requires-python=">=3.10"`).

**Build recipe (reproducible, out-of-tree):**

```bash
# $REPO = repository root, $STAGE = throwaway dir OUTSIDE the repo
STAGE=$(mktemp -d)
git -C "$REPO" archive HEAD src/cccp | tar -x -C "$STAGE"          # frozen source tree
cp "$REPO/packaging/cccp/pyproject.toml" "$STAGE/pyproject.toml"   # template → build root
cp "$REPO/LICENSE" "$STAGE/LICENSE"                                # MIT license for the wheel
python3.11 -m build --wheel --no-isolation --outdir "$STAGE/dist" "$STAGE"
```

### 3.3 Verification steps (after B applies the patches)

```bash
# V0) patch applicability on B's branch (live pyproject unchanged in C's tree)
git -C "$REPO" apply --check tests/baseline/cccp-evidence/patches/t39-01-pyproject-ownership.patch
git -C "$REPO" apply --check tests/baseline/cccp-evidence/patches/t39-02-cccp-pyproject.patch

# V1) build the independent cccp wheel (recipe §3.2) and assert the namelist:
python3.11 - <<'PY'
import glob, hashlib, zipfile
w = sorted(glob.glob("<STAGE>/dist/*.whl"))[-1]; n = set(zipfile.ZipFile(w).namelist())
must = ["cccp/analysis/nmr/model_assets/atomic_reps.gz",   # <-- acceptance grep target
        "cccp/analysis/nmr/model_assets/frag_reps.gz",
        "cccp/analysis/nmr/model_assets/i_w_kde_mean_s_0.025.p",
        "cccp/analysis/nmr/model_assets/c_w_kde_mean_s_0.025.p",
        "cccp/analysis/nmr/model_assets/folded_scaled_errors.p",
        "cccp/analysis/nmr/model_assets/tms_references.txt",
        "cccp/analysis/nmr/model_assets/LICENSE-DP5",
        "cccp/analysis/nmr/model_assets/NOTICE.md",
        "cccp/py.typed"]
assert not [m for m in must if m not in n], "missing CCCP assets/py.typed"
assert not [x for x in n if x.startswith(("acp/", "acp_services/", "acp-"))], "acp leak in cccp wheel"
print("OK cccp wheel: 8/8 assets + py.typed; ZERO acp* entries")
PY

# V2) build the acp wheel (M2) and assert it owns acp/** and ships NO cccp/**
#     (stage src/acp + src/acp_services + README.md + LICENSE + t39-01 pyproject)
python3.11 - <<'PY'
import glob, zipfile
w = sorted(glob.glob("<STAGE2>/dist/*.whl"))[-1]; n = set(zipfile.ZipFile(w).namelist())
assert not [x for x in n if x.startswith("cccp/")], "cccp leak in acp wheel"
assert "acp/__init__.py" in n and "acp_services/__init__.py" in n
assert not [x for x in n if x.startswith("acp/nmr/models/")], "stale legacy asset dir shipped"
print("OK acp(M2) wheel: owns acp/**+acp_services/**; ZERO cccp/ entries")
PY

# V3) clean-env install + isolation (T40a/T54 recipe): no acp discoverable in cccp-only venv
python3.11 -c "import importlib.util as u; assert u.find_spec('acp') is None; print('acp not discoverable: OK')"

# V4) packaging tests (after the B40a sequence §3.4)
python3.11 -m pytest tests/test_packaging_assets.py -q
```

### 3.4 Cross-lane RED-window sequence (packaging test)

`tests/test_packaging_assets.py::test_package_data_covers_all_nmr_model_assets` and
`::test_critical_nmr_assets_exist` currently point at the emptied `src/acp/nmr/models/`
(2 RED tests). Order for B40a, all in **one** integration batch:

1. `c39a-pyproject-first-batch.patch` (T56) — transitional package-data.
2. `t55-c39a-packaging-test.patch` (T55) — repoint both tests + `_CRITICAL_ASSETS` at
   `src/cccp/analysis/nmr/model_assets/`.
3. `t39-01` + `t39-02` — M2 ownership (removes the stale `"acp.nmr"` rule; introduces the
   independent `cccp` config).
4. Re-run `tests/test_packaging_assets.py` → green.

---

## 4. Node-dependency sync POINTS (`requirements-node.txt` — fine-grained request is T42)

This file lists the touchpoints only; the per-dependency request is **T42 (HN-05)**.

| # | Touchpoint | Current state | T39 note |
|---|---|---|---|
| N1 | `requirements-node.txt` core block (`numpy/rdkit/pyyaml/scipy/matplotlib/openpyxl`) | verbatim mirrors of `pyproject.toml` core deps (drift-locked by `test_requirements_node_mirrors_every_core_dep`) | Unchanged by T39 — the `cccp` core deps are identical to the platform core deps |
| N2 | `nmrglue>=0.10` (node line, `nmr` extra origin) | present; required for remote `--bruker` stage 0a | Unchanged |
| N3 | `qml` / `openbabel` | deliberately node-forbidden | Unchanged — stay in the `nmr` extra, never on nodes |
| N4 | Any new `acp run`-reachable runtime import from C03–C09 | none added by T39 (packaging-only) | If a later batch adds one → route to T42 |
| N5 | `cccp==0.2.0` distribution dependency | new on the `acp` distribution (not a node runtime import) | Not a node line; nodes still get the science subset via `requirements-node.txt` |

`tests/test_packaging_assets.py` remains the node-sync guardian (do not weaken).

---

## 5. Two-state listing — 首批范围验收 (T40a) vs 完整发行验收 (T40b)

| # | Item | T40a 首批范围验收 | T40b 完整发行验收 |
|---|---|---|---|
| 1 | Package discovery | `cccp*` discovered (standalone cccp wheel built) | `cccp` wheel owns `cccp/**`; `acp` wheel drops `cccp*` and depends on `cccp==0.2.0` (§2 M2) |
| 2 | Dependencies | core science deps only (frozen commit `5518aa6`) | core + `nmr` extra; no Web/IAM/billing in `cccp`; `acp` extras unchanged |
| 3 | Assets | 8/8 `model_assets/*` in the cccp wheel, digests == source | same 8 in the released `cccp` artifact, digest re-check vs manifest |
| 4 | Licenses/notices | `LICENSE` (MIT) + `LICENSE-DP5` + `NOTICE.md` present in wheel | full manifest §1.4 cross-checked against the release namelist |
| 5 | Python version | 3.11.13 build host; `requires-python>=3.10` | same, verified on the release candidate |
| 6 | Install target | clean venv, `--no-index`, no acp discoverable | candidate-commit rebuild + install (T54), release-candidate artifacts |
| 7 | Science samples | 16/16 scoped samples PASS (T40a §7) | full sample set incl. NMR/大数据 per released scope |
| 8 | Real QC programs | NOT_VERIFIED (not needed) | NOT_VERIFIED unless run on the release host |
| 9 | Scope note | **NOT** full-release acceptance | authoritative release acceptance; consumes T39 + T40a |
| 10 | Owner of verification | T40a (done) / T54 candidate re-verification | T40b; production release + post-release check = B-T76/F5 (cross-lane) |

---

## 6. QA — temp build proof (raw in `patches/t39-build-proof.txt`)

Toolchain `/usr/bin/python3.11` (3.11.13), `build 1.6.1`, `setuptools 65.5.1`, `wheel 0.48.0`
(`--no-isolation`, `PYTHONPATH=/tmp/opencode/builddeps`). All builds out-of-tree under
`/tmp/opencode/t39-proof/` — **never in the repo**.

| Step | Command | Result |
|---|---|---|
| Apply patches | `git apply t39-01; git apply t39-02` in a throwaway repo | `PATCH_APPLY_EXIT=0` |
| cccp wheel | `python3.11 -m build --wheel` | **PASS** — `cccp-0.2.0-py3-none-any.whl`, 194 entries |
| cccp namelist | 8 `model_assets/*` + `cccp/py.typed` | present; **zero** `acp*` entries; 2 digest re-checks OK |
| acp wheel (M2) | `python3.11 -m build --wheel` | **PASS** — `acp-0.2.0-py3-none-any.whl`, 346 entries |
| acp namelist | owns `acp/**`+`acp_services/**` | `acp/__init__.py`+`acp/py.typed` present; **zero** `cccp/` entries; stale `acp/nmr/models/` absent |
| `PROOF_EXIT` | end-to-end | **0** |

`failure` branch (per plan QA): a missing dependency surfaces as a build/`pip check`/import-probe
failure → add the dependency to §1.2 and the clean-env probe list. **Not triggered** — no missing
dependency in either wheel.

---

## 7. tri-state evidence table

| Item | State | Evidence |
|---|---|---|
| `git apply --check` (t39-01, t39-02) vs live worktree | **PASS** | `t39-01 CHECK_EXIT=0`; `t39-02 CHECK_EXIT=0`; `BOTH_EXIT=0` |
| Out-of-tree cccp wheel build (patches as source) | **PASS** | `t39-build-proof.txt §2` |
| Out-of-tree acp (M2) wheel build | **PASS** | `t39-build-proof.txt §3` |
| cccp namelist: 8/8 assets + `py.typed`, zero `acp*` | **PASS** | `t39-build-proof.txt §4` |
| acp namelist: zero `cccp/`, owns `acp/**` | **PASS** | `t39-build-proof.txt §4` |
| Asset digests re-verified at fresh snapshot | **PASS** | §1.3 (== T30/T55/T56) |
| Clean-env install + `find_spec("acp") is None` | **PASS (cited T40a)** | `.omo/evidence/.../task-40a-*.md §6` |
| Full-release clean-env install of the published artifact | **NOT_VERIFIED** | T40b/T54 scope |
| Real QC programs on release host | **NOT_VERIFIED** | not run here |
| Live `pyproject.toml` / `requirements-node.txt` applied | **NOT DONE** | `B请求`; B integration |

---

## 8. Assumptions & risks

**Assumptions**
1. Full-release target = M2 split (independent `cccp` distribution + `acp` depends on it). The
   plan (§10/§18.1, PR-01/PR-02) requires CCCP independently installable AND an ACR+CCCP CLI,
   so co-installation must be file-disjoint — that forces M2.
2. `packaging/cccp/pyproject.toml` is a **template** copied to a staging root at build time
   (`where=["src"]` is relative to the build root). The build recipe stages `src/cccp` + `LICENSE`.
3. The standalone `cccp` wheel is built from the frozen `src/cccp` tree (as T40a); the root
   `acp` wheel no longer contributes any `cccp/**` path.
4. `cccp==0.2.0` is the exact-version dependency (4-way version sync, ANTI #2). Bump all four
   sites together on release.
5. No root `NOTICE.md` is added; attribution ships inside `model_assets/` (T41/T30).
6. `packaging/cccp/pyproject.toml` is a `B请求` (build-config file), delivered as a patch; C does
   not create it in the live tree.

**Risks / mitigation**
- **R1 — editable-install breakage during M2 transition:** dropping `cccp*` from the root build
  means `pip install -e .` alone no longer provides `cccp`. Mitigation: apply M2 only when a
  `cccp` artifact is resolvable; until then retain M1 (c39a transitional single wheel). Flagged
  for B's integration timing.
- **R2 — version skew between the two wheels:** the `acp`→`cccp==0.2.0` exact pin plus ANTI #2
  four-way sync prevents silent skew.
- **R3 — packaging-test RED window:** resolved only by the §3.4 batch; until then 2 tests fail.
- **R4 — `license-files` version sensitivity:** `LICENSE` inclusion relies on the setuptools
  default glob (`LICEN[CS]E*`); verified with setuptools 65.5.1 in the temp build. Re-verify on
  the release toolchain.
- **R5 — real QC programs:** never validated here; carried as **NOT_VERIFIED**, not PASS.

## 9. Cleanup receipt

Temp workspaces `/tmp/opencode/t39-build/`, `/tmp/opencode/t39-proof/`, `/tmp/opencode/t39-proof.sh`
are outside the repo and not committed. The live `pyproject.toml` is byte-identical to the base
snapshot (`sha256 0c24617e…`); only T39 evidence files are staged.
