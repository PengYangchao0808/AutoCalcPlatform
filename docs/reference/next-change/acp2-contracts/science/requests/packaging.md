# ACP2 Plan A — Independent science packaging request (A-27a)

- **Task**: A-27a (todo 48) — audit the science import closure and deliver the packaging delta to B25a.
- **Owner of this request**: A (cccp science) · **implementer**: B (platform packaging).
- **Closes (after B applies)**: GAP-11 (independent science distribution), GAP-12 (standalone validation assets).
- **Branch / base**: `acp2/a-science` @ `bcb9fa9654d2c5a0aae59a24f8c1ed5766cf94f9`.
- **Evidence run**: `$ACP2_EVIDENCE/A/A-27/a27a-20261023T040251Z/` (`result.json`, raw audit stdout/stderr, script copy).
- **Status**: request + exact patch delivered. **No wheel is built or installed by A here** — that is A-27b
  (todo 49) against B25a's actual artifact. Nothing below is a third-party install success claim.

This request extends the **existing** role release. It does not re-plan `packaging/README.md`, the role
configs, `scripts/build_release.py`, `check_release_artifacts.py` or `release_role_probe.py`, and it does
not touch the root distribution, CI or any version constant. The repo has **three** role configs
(`packaging/roles/{cccp,worker,web}`), not four; there is no fourth role to extend.

---

## 1. Audit result — science import closure

Method: AST walk from the declared entry modules, following relative and absolute `cccp.*` imports,
including ancestor packages; imports under `if TYPE_CHECKING:` and function-local imports are excluded
from the runtime closure but still scanned for forbidden roots. A second, independent check imports every
entry in a **fresh interpreter** from `/tmp` with `PYTHONPATH=<repo>/src` and inspects `sys.modules`.
The audit script is preserved verbatim in the evidence run; exact invocation:

```bash
/ACPServer/acp2-development/envs/a-python/bin/python packaging_audit.py \
  --root /ACPServer/acp2-development/A \
  --output $ACP2_EVIDENCE/A/A-27/a27a-20261023T040251Z/packaging-audit.raw.json
# exit 0
```

| Entry module | Resolved `cccp` modules | Forbidden `acp`/`acp_services`/`dsh` | Module-level third-party imports |
|---|---:|---|---|
| `cccp.analysis.research` | 11 | `[]` | none |
| `cccp.analysis.research.validators` | 11 | `[]` | none |
| `cccp.calculation.capabilities` | 17 | `[]` | none |
| `cccp.analysis.nmr.comparison` | 32 | `[]` | `numpy`, `scipy.optimize`, `scipy.special` |
| `cccp.analysis.ensemble.science` | 54 | `[]` | `numpy` (and the `cccp.qc` subprocess tree it imports) |
| `cccp.analysis.frequency_products` | 10 | `[]` | none |
| **Union** | **89** | **`[]`** | `numpy`, `scipy.optimize`, `scipy.special` |

- **Forbidden result**: `forbidden_module_level = []`, `forbidden_any_level = []` (function-local and
  `TYPE_CHECKING` imports included in the scan). `dsh` is not a Python module in this tree; the DSH/MCP
  surface is `acp_services.entrypoints.mcp` (see
  `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md`), so any
  `acp_services.*` import is caught by the same root check.
- **Fresh-interpreter probe**: union probe `{"forbidden": [], "calculation_tasks": [], "qc": [22 modules]}`,
  exit 0. Research-only probe `cccp.analysis.research` → `{"forbidden": [], "calculation_tasks": [], "qc": []}`,
  exit 0 — importing the research contract package pulls no `cccp.calculation.tasks`, no `cccp.qc`, no
  `acp`/`acp_services`, matching the A-04b purity guard.
- **Full resolved list** (89 modules, sorted), per-module `file:line` third-party imports, and both probe
  payloads are in `packaging-audit.json` next to this request.
- `rdkit` is **not** in the module-level closure: `cccp/analysis/structure/topology.py:24` imports it only
  under `if TYPE_CHECKING:`; the runtime `rdkit` use sits behind function calls (still covered by the
  declared role dependency).
- **No `matplotlib` or `openpyxl` module-level import exists in this closure** (they belong to the NMR
  report/XLSX path, outside the science entry set); no module-level import is undeclared.
- `scipy.special` (`analysis/nmr/error_model.py:33`) and `scipy.optimize`
  (`analysis/nmr/assignment.py:24`, `analysis/nmr/iterative_assignment.py:75`) are **already declared** in
  the `cccp` role and already present in `requirements-node.txt`; therefore this audit raises **no new
  dependency request**. The brief's parenthetical “numpy/rdkit/pyyaml only at module level” does not match
  the current authoritative `requirements-node.txt`, which also pins scipy/matplotlib/openpyxl/nmrglue.
  This request follows the file, not the stale prose.

## 2. What already exists (record, do not rebuild)

Quoted from the current tree:

`packaging/roles/cccp/pyproject.toml`:

```toml
requires-python = ">=3.10"
dependencies = [
    "numpy>=2.1.0", "rdkit>=2022.09.1", "pyyaml>=6.0", "scipy>=1.10",
    "matplotlib>=3.7", "openpyxl>=3.1", "typing-extensions>=4.1",
]
...
[tool.setuptools.packages.find]
where = ["src"]
include = ["cccp", "cccp.*"]

[tool.setuptools.package-data]
"cccp.analysis.nmr" = ["model_assets/*"]
"cccp" = ["py.typed"]
```

`scripts/build_release.py` already copies the whole owning package and stages the sdist inputs:

```python
(stage / "MANIFEST.in").write_text(
    "include VERSION requirements-role.txt\nrecursive-include src *\n", encoding="utf-8"
)
...
_copy_tree(source / "src" / package, stage / "src" / package)
```

`scripts/acceptance/check_release_artifacts.py` already enforces byte equality between the staged tree and
the wheel, and byte-compares shipped NMR assets back to the source tree:

```python
assert files == staged, f"wheel omitted or changed staged source/assets: {wheel}"
...
if name.startswith("cccp/analysis/nmr/model_assets/"):
```

`scripts/acceptance/release_role_probe.py` already probes an **installed** `cccp` role from outside the
repo and asserts the notice/licence/model files are present:

```python
assets = Path(cccp.__file__).parent / "analysis/nmr/model_assets"
assert {"NOTICE.md", "LICENSE-DP5", "atomic_reps.gz", "frag_reps.gz"} <= hashes.keys()
```

`packaging/README.md` already documents the three disjoint wheels/sdists, the empty-`--out` rule,
`release-manifest.json` SHA-256, clean-venv install, and the three acceptance commands.

**Conclusion**: package inclusion (`cccp/**`), Python range, role dependencies and the NMR assets need
**no change**. The only real gap is in section 3.

## 3. The science delta only

**Confirmed gap (R1 from the A-00b preview).** The frozen science contract corpus exists only under
`docs/reference/next-change/acp2-contracts/science/` and is **not** in the `cccp` wheel:

- `stage_role()` copies only `src/<package>` (`_copy_tree(source / "src" / package, ...)`), never `docs/`.
- The role `package-data` matches only `cccp.analysis.nmr:model_assets/*` and `cccp:py.typed`.
- Therefore a standalone user can `import cccp.analysis.research` and call the typed readers, but cannot
  validate a payload against the frozen JSON Schema or use the documented example payloads without a repo
  checkout. The repo's own schema test proves the corpus is authoritative and repo-relative:
  `tests/test_acp2_science_schema.py::CONTRACT_FILE_NAMES` lists exactly 14 files.

**Requested delta (data only; A implements the files in todo 49/50, not here):**

1. A-49/50 adds a data directory `src/cccp/analysis/research/contracts/` containing **byte-identical
   copies** of exactly these 14 files, sourced from the docs directory (the docs remain the authority; an
   A-owned drift test must byte-compare the copies):
   - `capability.schema.json`, `task-binding.schema.json`, `errors.schema.json`, `reaction.schema.json`,
     `question.schema.json`, `evidence.schema.json`, `provenance.schema.json`, `recipe.schema.json`
   - `semantic-rules.md`, `evidence-semantics.md`
   - `examples/capability-energy.json`, `examples/task-binding-singlepoint.json`,
     `examples/reaction-da.json`, `examples/question-activation.json`
2. A-49/50 adds `src/cccp/analysis/research/contracts.py` with read-only
   `contract_root()` / `read_contract(name)` (path-traversal rejected, bytes/text only). **Not implemented
   by this task**; no docs file is moved or copied here.
3. B adds the matching `package-data` declaration (section 7). No other build change is needed:
   `build_release.py` already copies `src/cccp/**` and the sdist `recursive-include src *` already captures
   the new data; `check_release_artifacts.py`'s `files == staged` assertion already fails if the wheel
   omits it.

**Out of scope / not requested**: `baseline.md`, `packaging-preview.md`, `path-map.md`,
`cross-track-dependencies.json`, `handoff-H*.json`, `selectivity-evidence-report.json` and the other
planning docs stay repo-only. They are plan/evidence records, not user-facing contract inputs.

**Sequencing (important for B25a)**: the package-data glob is inert until A-49/50 lands the corpus. A
frozen B25a build cut **before** that will not contain the corpus; A-27b must record that as a
`NOT_VERIFIED` contract-data gap, not as a packaging PASS. Applying this patch now is still correct and
safe — it cannot break a wheel while the directory is absent.

**Existing test impact**: `tests/test_packaging_assets.py` reads the **root** `pyproject.toml` (not the
role config) and only the NMR `model_assets/` package-data; none of its assertions is changed or relaxed by
this patch. No test edit is part of the patch; the A-owned drift test for the in-package corpus lands with
A-49/50.

## 4. Runtime dependencies

| Surface | Value | Evidence |
|---|---|---|
| `cccp` role declared | `numpy>=2.1.0`, `rdkit>=2022.09.1`, `pyyaml>=6.0`, `scipy>=1.10`, `matplotlib>=3.7`, `openpyxl>=3.1`, `typing-extensions>=4.1` | `packaging/roles/cccp/pyproject.toml:12-15` |
| Science closure module-level imports | `numpy`, `scipy.optimize`, `scipy.special` | `packaging-audit.json` |
| Dependency additions requested | **none** | closure ⊆ declared |
| Remote-node floor | `requirements-node.txt` unchanged (numpy, rdkit, pyyaml, scipy, matplotlib, openpyxl, nmrglue, typing-extensions) | file read |

No new dependency is justified by an actual import. The optional `nmr` extra (`nmrglue`, `openbabel`,
`qml`) is not imported at module level by this closure and stays optional. `requirements-node.txt` remains
the remote-node floor and is **not** part of the patch.

## 5. Assets and licences

`cccp` wheel packages `src/cccp/analysis/nmr/model_assets/` via the existing glob
`"cccp.analysis.nmr" = ["model_assets/*"]`; notices must not be stripped.

| Asset | Size (bytes) | SHA-256 |
|---|---:|---|
| `atomic_reps.gz` | 22171041 | `bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65` |
| `frag_reps.gz` | 18044581 | `e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64` |
| `folded_scaled_errors.p` | 851489 | `c59bd8d3218b28db0e5dde3cf70911688ce530351c45f7930a4b470bd9d200dd` |
| `i_w_kde_mean_s_0.025.p` | 24353668 | `2d523808d3859e8a48c695f5dbf8f9b9fe1fd3f02ee2dc70c8016414cf8f383e` |
| `c_w_kde_mean_s_0.025.p` | 79518 | `246025abffd97e32cc405d8956deaf3fd03e7426ff688e7052340135e79dc309` |
| `tms_references.txt` | 4578 | `5ff2429aa73ade13aa6a98b3cc6433cc8eedd9d2a687c6b9fa907be03be7dcf7` |
| `NOTICE.md` | 3204 | `f99522ae9c481ceacba628e994cdd8a958b6febaa7ae66136020849cc6925ad0` |
| `LICENSE-DP5` | 1175 | `55bc399b93914549e797365ac07b46527775f9aa28275ccb9fe43c4ebaf08146` |
| `src/cccp/py.typed` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty file) |
| `LICENSE` (root, MIT) | see tree | copied into every role stage; sdist checker asserts it |

- `NOTICE.md` records the DP5 provenance and the `qml`/`openbabel` constraints; `LICENSE-DP5` is the
  upstream MIT notice. Both must ship and must not be rewritten or dropped.
- `check_release_artifacts.py` byte-compares every shipped `model_assets/*` against `src/` and
  `release_role_probe.py --role cccp` asserts `NOTICE.md`, `LICENSE-DP5`, `atomic_reps.gz`,
  `frag_reps.gz` exist in the installed package.
- The new contract corpus is MIT-covered by the root `LICENSE`; its 14-file SHA-256 manifest is in
  `packaging-audit.json::contract_corpus`.

## 6. Python support range

`requires-python = ">=3.10"` in all three role configs (`cccp`, `worker`, `web`); this request does **not**
narrow, raise or otherwise change it. The audit ran on **CPython 3.11.13**
(`/ACPServer/acp2-development/envs/a-python/bin/python`). No version constant is touched; the version is
still read by `build_release.py::release_version()` from `src/cccp/version.py`.

## 7. Exact patch

The complete, applyable unified diff is `packaging.patch` in this directory (identical content below).
It touches **one platform-owned file** and nothing else:

```diff
diff --git a/packaging/roles/cccp/pyproject.toml b/packaging/roles/cccp/pyproject.toml
index 2edace2..d31b777 100644
--- a/packaging/roles/cccp/pyproject.toml
+++ b/packaging/roles/cccp/pyproject.toml
@@ -26,4 +26,5 @@ include = ["cccp", "cccp.*"]
 
 [tool.setuptools.package-data]
 "cccp.analysis.nmr" = ["model_assets/*"]
+"cccp.analysis.research" = ["contracts/*.json", "contracts/*.md", "contracts/examples/*.json"]
 "cccp" = ["py.typed"]
```

Why only this file:

| Candidate | Decision | Reason |
|---|---|---|
| `packaging/roles/cccp/pyproject.toml` | **change** | package-data is the only gate that keeps non-`.py` files out of the wheel |
| `scripts/build_release.py` | no change | already copies `src/<package>/**` and writes `recursive-include src *` |
| `scripts/acceptance/check_release_artifacts.py` | no change | `assert files == staged` already fails on an omitted data file; model-asset byte check unchanged |
| `scripts/acceptance/release_role_probe.py` | no change | installed-role probe and asset assertions unchanged |
| `tests/test_packaging_assets.py` | no change | it locks the root `pyproject.toml` + NMR assets; no assertion is affected |
| root `pyproject.toml`, CI, versions | **never** | platform-owned, explicitly excluded by A-27a |

Dry-run receipt (performed in a scratch copy under `/tmp/opencode/a27/scratch`, **not** in the A worktree):

```text
$ git apply --check --verbose /.../packaging.patch
Checking patch packaging/roles/cccp/pyproject.toml...
$ git apply /.../packaging.patch
$ git status --porcelain
M packaging/roles/cccp/pyproject.toml
```

Applied file list: `packaging/roles/cccp/pyproject.toml` only.

## 8. Rollback note

One step, from the repository root:

```bash
git apply -R docs/reference/next-change/acp2-contracts/science/requests/packaging.patch
# or equivalently: git checkout -- packaging/roles/cccp/pyproject.toml
```

What becomes invalid if reverted:

- The `cccp` wheel stops shipping `cccp/analysis/research/contracts/**`; any A-27b/A-28 evidence that reads
  the in-package contract corpus (schema validation, documented examples) no longer holds and must be
  re-recorded as `NOT_VERIFIED`.
- Existing science imports, runtime dependencies and NMR model assets are unaffected (they never depended on
  this key), so `release_role_probe.py --role cccp` keeps passing — that is exactly why the revert is safe
  to reason about and why the contract check must be explicit in A-27b.
- No version, manifest schema or dependency pin changes, so no dependency evidence is invalidated.

## 9. Acceptance commands for B (and what A-27b will verify)

Run from the repository root; `<out>`, `<roundtrip>` and `<evidence>` must be fresh/empty directories.

```bash
# 1. Focused cccp role build (the delta under test)
python scripts/build_release.py --source "$PWD" --out <out-cccp> --role cccp

# 2. Full role acceptance (check_release_artifacts requires exactly 3 wheels + 3 sdists)
python scripts/build_release.py --source "$PWD" --out <out-all>
python -m pip wheel --no-deps --no-build-isolation --wheel-dir <roundtrip> \
  <out-all>/cccp-*.tar.gz <out-all>/acp-worker-*.tar.gz <out-all>/acp-web-*.tar.gz
python scripts/acceptance/check_release_artifacts.py \
  --artifacts <out-all> --roundtrip-wheels <roundtrip> --output <evidence>/artifacts.json

# 3. Installed cccp probe from outside the repo (after clean-venv install of the role)
env -u PYTHONPATH <cccp-venv>/bin/python scripts/acceptance/release_role_probe.py \
  --role cccp --output <evidence>/cccp-role-probe.json
```

Pass criteria for the delta:

- Focused wheel namelist contains `cccp/analysis/research/contracts/<all 14 files>` once A-49/50 lands the
  corpus (before that, the glob is inert and the build is still valid).
- `check_release_artifacts.py` prints `"result": "PASS"` and its `files == staged` / sdist-roundtrip
  assertions stay green.
- `release_role_probe.py --role cccp` reports `controlled_science: PASS` and the model-asset hashes, with no
  `acp`, `acp_services` or `fastapi` importable in that environment.

A-27b (todo 49), with B25a's **actual** artifact, will verify and record: clean venv install without source
path or editable install; `cccp.__file__` provenance; the same forbidden-root import audit; model-asset
SHA-256 equality; `contract_root()` listing the 14 frozen files and `read_contract()` digests matching
`packaging-audit.json`; `release_role_probe.py --role cccp`; and that missing external QC software yields a
typed unavailable error at execution time rather than an import crash.

**NOT_VERIFIED today (A-27a)**: no wheel/sdist was built, no clean venv was created, no installation was
performed, and no B25a artifact exists. The contract corpus is not yet in `src/`; this request is the
platform delta, not the implementation. Dependency/asset/import facts above are static + import-probe facts
from the audited commit, and every state that needs an artifact stays `NOT_VERIFIED`.
