# ACP2 Plan A — Packaging Preview (A-00b, plan decision R3-14)

Recorded 2026-10-22 (UTC run `a00b-20261022T121442Z`) by task **A-00b** on `acp2/a-science`
(delivery `2e0ce9c` + docs `7f4944e`/`94c8ea9`). Purpose: freeze **what already exists** —
distribution shape, top-level ownership, dependency closure, the three role configs and the build /
acceptance tooling — plus one minimal wheel-smoke argv. This is a preview, **not** a packaging plan
redesign: the full independent package remains P5 (A-27a/A-27b/A-28, todos 48-50), and per review
R5-06 the existing role build must be **audited and extended, never rebuilt from a blank slate**.

Evidence markers: `static-fact` = read from code/config in this run (or re-executed import-level);
`needs-real-evidence` = requires a build, a clean venv, or another track's artifact.

## 1. Existing distribution capability (already shipped — record, do not re-plan)

| Existing item | State | Evidence |
|---|---|---|
| `packaging/README.md` | Role-release instructions: three disjoint wheels/sdists from one source tree, `release-manifest.json` with per-role source + artifact SHA-256, clean-venv install, acceptance commands | `static-fact` |
| `packaging/roles/cccp/pyproject.toml` | role `cccp`: `packages.find include = ["cccp", "cccp.*"]`, package-data `cccp.analysis.nmr = ["model_assets/*"]` + `cccp = ["py.typed"]`, deps numpy/rdkit/pyyaml/scipy/matplotlib/openpyxl/typing-extensions, extra `nmr` | `static-fact` |
| `packaging/roles/worker/pyproject.toml` | role `acp-worker`: `include = ["acp_services", "acp_services.*"]`, console script `acp-worker = acp_services.worker_bootstrap:main`, deps from generated `requirements-role.txt` (`cccp==<version>`) | `static-fact` |
| `packaging/roles/web/pyproject.toml` | role `acp-web`: `include = ["acp", "acp.*", "acp_web_assets"]`, console script `acp = acp.cli:main`, package-data `acp_web_assets = ["**/*"]`, deps `cccp` + `acp-worker` + fastapi/uvicorn/python-multipart/zipstream-ng/paramiko | `static-fact` |
| `scripts/build_release.py` | `--source/--out/--role {cccp,worker,web,all}`; stages one package owner + LICENSE/README/VERSION/MANIFEST.in/`requirements-role.txt`, builds wheel **and** sdist in-process via `setuptools.build_meta` (`build_wheel`/`build_sdist`), writes `release-manifest.json` with SHA-256; `--out` must be empty | `static-fact` |
| `scripts/acceptance/check_release_artifacts.py` | Verifies manifest digests, 3 wheels, disjoint top-level ownership (no duplicate installed file), sdist round-trip wheel equality, model-assets bytes vs `src/cccp/.../model_assets/*`, frontend bytes vs `src/acp_web_assets/*`, sdists contain `VERSION`/`requirements-role.txt`/`MANIFEST.in`/`LICENSE`; writes `{"result": "PASS", "wheels": …}` | `static-fact` (code read; not executed by A-00b) |
| `scripts/acceptance/release_role_probe.py` | `--role {cccp,worker,web} --output <file>` from **outside** the repo (`env -u PYTHONPATH`); `science_probe()` runs `run_singlepoint` on a controlled backend and asserts `NOTICE.md`, `LICENSE-DP5`, `atomic_reps.gz`, `frag_reps.gz` are present in the installed `cccp/analysis/nmr/model_assets` | `static-fact` (code read; execution = `needs-real-evidence`) |
| Repair report evidence | `reports/architecture_repair_20261022/REPORT.md` (read **read-only from the primary checkout** — that directory is absent from the A worktree): three roles built and installed locally, disjoint ownership, sdist round-trip reproducible, three fresh venvs accepted, independent worker re-ran real xTB with artifacts inside the task dir; gate G4 = “three roles pass in the declared local install/run scope”; artifacts staged at `/tmp/acp-repair-release-v2/` (temporary, not a published repo) | `static-fact` as a recorded report; the underlying runs are historical, **not** re-executed by A — `needs-real-evidence` for this candidate |
| Root `pyproject.toml` | Single-distribution `acp` install path kept for existing deployments (`build-system requires setuptools>=65.0, wheel`); one environment takes **either** the root `acp` distribution **or** the three role distributions | `static-fact` |
| Version source | `src/cccp/version.py::__version__` is the single version read by `build_release.py::release_version` (no fifth version constant) | `static-fact` |

## 2. Top-level package / file / asset ownership

| Path | Owner surface | Ships in | Notes | Evidence |
|---|---|---|---|---|
| `src/cccp` | science library | **`cccp`** wheel | four-layer stack layer ①②③ (`qc`, `backends`, `calculation`) + `analysis`/`io`/`utils`; `py.typed` shipped | `static-fact` |
| `src/acp` | ACP CLI/web application | **`acp-web`** wheel | workflows, scheduler, API, NMR, intake; console script `acp` | `static-fact` |
| `src/acp_services` | platform services layer | **`acp-worker`** wheel | workspace/data/runtime/store/contracts/entrypoints; console script `acp-worker` | `static-fact` |
| `frontend` | Workbench assets | **`acp-web`** wheel (copied to `src/acp_web_assets/` at stage time, `__init__.py` added) | bytes compared back to `frontend/` by `check_release_artifacts.py` | `static-fact` |
| `packaging` | build configuration | not shipped | `README.md` + the three role `pyproject.toml` — build inputs only | `static-fact` |
| `scripts` | build + acceptance tooling | not shipped | `build_release.py`, `acceptance/{check_release_artifacts,release_role_probe}.py`, … | `static-fact` |
| `tests` | test suite | not shipped | guards `test_architecture_invariants` / `test_cccp_isolation` / `test_recovery_fixtures_smoke` + the A-00b validator | `static-fact` |
| `docs` | design/contract documents | not shipped | includes this `science/` directory | `static-fact` |
| `src/cccp/analysis/nmr/model_assets/` | NMR model assets | **`cccp`** wheel (package-data `model_assets/*`) | `NOTICE.md`, `LICENSE-DP5`, `atomic_reps.gz`, `frag_reps.gz`, `folded_scaled_errors.p`, `*_kde_mean_s_0.025.p`, `tms_references.txt`; licence obligations ride with the wheel; **never deletable** (ANTI #31, guarded by `tests/test_packaging_assets.py` and `release_role_probe.py::science_probe`) | `static-fact` |
| `src/cccp/py.typed` | typing marker | **`cccp`** wheel (package-data) | declared in `packaging/roles/cccp/pyproject.toml` | `static-fact` |
| `LICENSE`, `README.md` | legal/readme | copied into **every** role stage | `build_release.py::stage_role` | `static-fact` |
| `VERSION`, `MANIFEST.in`, `requirements-role.txt` | generated stage files | inside each sdist | written by `stage_role`, listed in the sdist check | `static-fact` |
| `pyproject.toml` (root), `requirements*.txt`, CI, version constants, `AGENTS.md` | shared / B-owned | root distribution only | **A must not edit** (path-map §3) | `static-fact` |

## 3. Dependency closure (as declared today)

| Layer | Declared dependencies | Evidence |
|---|---|---|
| `cccp` role | `numpy>=2.1.0`, `rdkit>=2022.09.1`, `pyyaml>=6.0`, `scipy>=1.10`, `matplotlib>=3.7`, `openpyxl>=3.1`, `typing-extensions>=4.1`; extra `nmr`: `nmrglue`, `openbabel`, `qml` | `static-fact` |
| `acp-worker` role | generated `requirements-role.txt` = `cccp==<version>` only (plus stdlib) | `static-fact` |
| `acp-web` role | generated = `cccp==<version>`, `acp-worker==<version>`, `fastapi`, `uvicorn`, `python-multipart`, `zipstream-ng`, `paramiko` | `static-fact` |
| Remote compute nodes | `requirements-node.txt` = `numpy`, `rdkit`, `pyyaml` — new production modules may import **only** stdlib + these three at module level (no `scipy`/`matplotlib`/`openpyxl`) | `static-fact` (node budget is a hard constraint, see risk R3) |
| Import direction | `cccp` never imports `acp` (`tests/test_cccp_isolation.py`); `acp_services` never imports `acp` (`tests/test_acp_services_isolation.py`); `acp` → `acp_services` single direction; `acp.backends` / `acp.calculations.primitives` are shims **into** `cccp` | `static-fact` (guards green in this run) |
| Build machine | `setuptools>=65.0` **and** `wheel` must be present; `$ACP2_A_PYTHON` has `setuptools 65.5.1` but **no `wheel`** → `bdist_wheel` is unavailable in the role interpreter (same reason the A-00a editable install needed the local build env on `PYTHONPATH`, zero network) | `static-fact` (env probe); wheel build `needs-real-evidence` |

## 4. Machine-readable summary

```json
{
  "packages": [
    {"path": "src/cccp", "kind": "package", "owner": "science library", "ships_in": "cccp", "evidence": "static-fact"},
    {"path": "src/acp", "kind": "package", "owner": "ACP CLI/web application", "ships_in": "acp-web", "evidence": "static-fact"},
    {"path": "src/acp_services", "kind": "package", "owner": "platform services layer", "ships_in": "acp-worker", "evidence": "static-fact"},
    {"path": "frontend", "kind": "asset-tree", "owner": "Workbench frontend (C-owned)", "ships_in": "acp-web (as src/acp_web_assets)", "evidence": "static-fact"},
    {"path": "packaging", "kind": "build-config", "owner": "role release configs", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "scripts", "kind": "tooling", "owner": "build + acceptance tools", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "tests", "kind": "test-suite", "owner": "guards + acceptance", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "docs", "kind": "documentation", "owner": "design + contract docs", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "src/cccp/analysis/nmr/model_assets", "kind": "package-data", "owner": "NMR model assets incl. NOTICE.md + LICENSE-DP5", "ships_in": "cccp (package-data model_assets/*)", "evidence": "static-fact"},
    {"path": "src/cccp/py.typed", "kind": "package-data", "owner": "typing marker", "ships_in": "cccp", "evidence": "static-fact"},
    {"path": "packaging/roles/cccp/pyproject.toml", "kind": "role-config", "owner": "cccp role", "ships_in": "build-input", "evidence": "static-fact"},
    {"path": "packaging/roles/worker/pyproject.toml", "kind": "role-config", "owner": "acp-worker role", "ships_in": "build-input", "evidence": "static-fact"},
    {"path": "packaging/roles/web/pyproject.toml", "kind": "role-config", "owner": "acp-web role", "ships_in": "build-input", "evidence": "static-fact"},
    {"path": "scripts/build_release.py", "kind": "build-tool", "owner": "three-role out-of-tree builder", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "scripts/acceptance/check_release_artifacts.py", "kind": "acceptance-tool", "owner": "artifact/ownership/round-trip checker", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "scripts/acceptance/release_role_probe.py", "kind": "acceptance-tool", "owner": "installed-role probe (cccp/worker/web)", "ships_in": "not-shipped", "evidence": "static-fact"},
    {"path": "LICENSE", "kind": "legal", "owner": "MIT license copied into every stage", "ships_in": "all roles", "evidence": "static-fact"}
  ],
  "wheel_smoke_argv": [
    "<acp2-dev>/envs/a-python/bin/python",
    "scripts/build_release.py",
    "--source", "<acp2-dev>/A",
    "--out", "/tmp/opencode/acp2-a00b-wheel-smoke",
    "--role", "cccp"
  ]
}
```

Path/token rules enforced by `tests/test_acp2_science_inventory.py`: every `packages[].path` must
exist in the A worktree; every `wheel_smoke_argv` token must be an absolute path or a repo-relative
path that exists (flags and value words contain no separator).

## 5. Minimal wheel smoke plan (argv + preconditions)

| Item | Value |
|---|---|
| Command | `wheel_smoke_argv` above (single role `cccp`, out-of-tree stage, empty `--out`) |
| Preconditions | build env provides `setuptools>=65` **and** `wheel`; in `$ACP2_A_PYTHON` `wheel` is missing, so run with the already-present local build env on `PYTHONPATH` (`/tmp/acp-repair-build-env/lib/python3.11/site-packages`, wheel 0.48.0) exactly as A-00a did — **no network install, no new package** |
| Expected output | `cccp-<version>-*.whl`, `cccp-<version>.tar.gz`, `release-manifest.json` (SHA-256 per artifact) under the `--out` directory |
| Full three-role path (existing capability, not rebuilt) | `scripts/build_release.py --out <new-dir>` (role `all`), then `scripts/acceptance/check_release_artifacts.py --artifacts <new-dir> --roundtrip-wheels <rebuild-dir> --output <evidence>.json`, then `env -u PYTHONPATH <role-env>/bin/python scripts/acceptance/release_role_probe.py --role cccp --output <evidence>.json` (verbatim commands: `packaging/README.md:36-46`) |
| Execution status **at A-00b** | **preview only — not executed by this task**; `needs-real-evidence` (A-27b todo 49 runs it in a clean environment and records digests) |
| Why not now | A-00b is a docs/ledger task; running a wheel build belongs to the P5 packaging todos, and the role interpreter still lacks `wheel` (risk R4) |

## 6. Risks flagged early (missing package-data / import-boundary)

| # | Risk | Detail | Handling |
|---|---|---|---|
| R1 | **Package-data gap for future science assets** | Roles ship only what `packages.find` + `[tool.setuptools.package-data]` declare. New science modules are `.py` under `src/cccp/**` → automatically in the `cccp` wheel. Any **non-`.py`** science asset intended to ship (contract JSON schemas, SDK examples, fixtures) is **not** packaged today: `cccp` package-data covers `model_assets/*` and `py.typed` only, and `packaging/roles/*/pyproject.toml` are **B-owned** (A must not edit) | If A-28's SDK needs shipped JSON/examples, A-27a raises `science/requests/packaging.md` to the platform owner instead of editing the role config. A's contract files under `docs/reference/next-change/acp2-contracts/science/` are documentation and are intentionally **not** wheel content |
| R2 | **Import-boundary risk** | Role split only holds while `cccp` never imports `acp` and `acp_services` never imports `acp`; the web role's dependency on `acp-worker` is declared in the generated `requirements-role.txt`, not import magic. A new science module importing `acp`/`acp_services` (even lazily or by string) would break both the guards and the installed roles | Guards: `tests/test_cccp_isolation.py`, `tests/test_acp_services_isolation.py` (green in this run); A's new code stays under `src/cccp/**` |
| R3 | **Remote-node import budget** | The `cccp` wheel *declares* scipy/matplotlib/openpyxl, but LSF nodes only have `requirements-node.txt` (numpy/rdkit/pyyaml). A module-level `import scipy` in a new science module would pass the local wheel smoke and still fail on a real node dispatch | No new third-party runtime dependency is allowed; any new import need becomes a `science/requests/` dependency request (plan verification strategy) |
| R4 | **Build-tool gap** | `wheel` absent from `$ACP2_A_PYTHON`; root/role build-system requires `wheel`, so PEP 517 build isolation would reach for the network | Use the in-process `setuptools.build_meta` path with the local build env on `PYTHONPATH` (A-00a precedent, zero network); never network-install |
| R5 | **Dual-distribution mixing** | Installing root `acp` and the role distributions into one environment double-claims the same paths | `packaging/README.md:30-32`: one environment picks one distribution set; `check_release_artifacts.py` already fails on duplicate installed files |
| R6 | **Model-asset / licence integrity** | The `cccp` wheel must keep shipping `model_assets/*` incl. `NOTICE.md`/`LICENSE-DP5` | Existing guards: `tests/test_packaging_assets.py`, `check_release_artifacts.py` byte comparison, `release_role_probe.py::science_probe`; A never deletes assets (ANTI #31) |

## 7. Science delta proposed by A-00b (nothing else)

1. **Record only (done here):** existing ownership, three role configs, build/acceptance tooling and
   the minimal smoke argv — no new role, no new build script, no rewritten packaging task.
2. **A-27a (todo 48):** audit this preview against the frozen candidate and, only if a real gap is
   found (R1/R3), raise `science/requests/packaging.md` / `science/requests/` dependency request to
   the platform owner. The packaging request is written, not implemented, by A.
3. **A-27b (todo 49):** execute `wheel_smoke_argv` + the three-role acceptance commands in a clean
   environment, record digests as `needs-real-evidence` → evidence; **A-28 (todo 50):** document the
   runnable SDK/plugin commands. P5 (todo 51) closes H7.
4. **Out of scope for A:** editing `packaging/**`, `pyproject.toml`, `requirements*.txt`, version
   constants or CI (all B-owned); claiming any wheel/install PASS before it is executed.
