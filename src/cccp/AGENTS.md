# cccp/ — Package Root

## OVERVIEW
Top-level package files: config loading, version management, package init. Hub connecting **9 subpackages** (core, io, pipeline, qc, utils, backends, calculation, **analysis**, **plugin**). Library-only — no CLI (deleted in the cccp rename; use the unified `acp` CLI). Version `0.3.0` (synced 4-way: here `__init__.py`/`version.py` + `acp/__init__.py` + `pyproject.toml`, see root AGENTS.md ANTI #2). Since ACP 2.0 (2026-10) cccp is no longer just the "QC 底座": it is also the **science-analysis home** (`analysis/`, 87 .py — NMR/DP4/DP5/FCHL, evidence/provenance contracts) and the **science plugin ecosystem** (`plugin/`). 202 .py total.

## STRUCTURE
```
src/cccp/   # QC 底座 + 科学分析/契约/插件层（four-layer ①qc → ②backends → ③calculation；④acp 在外层）
├── config.py        # 6-source YAML config load/merge (+ remote-cluster sections)
├── software.py      # Centralized QC executable resolution (resolve_executable / detect_version / discover_all)
├── __init__.py      # docstring + __version__ = "0.3.0" ONLY (no re-exports, no __all__)
├── version.py       # __version__ = "0.3.0"
├── core/            # protocols, candidates, state_manager, registry (engine removed in wave-8)
├── io/              # MolecularInputHandler — format detection, RDKit embed
├── pipeline/        # PipelineExecutor — thin orchestration
├── qc/              # QC subprocess layer: interfaces/ (15 modules — ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus + constraints/hess_file/orca_ts/route_render/xtb_path/xtb_scan/xtb_thermo), runners/, cluster/ + shared science adapters at qc/ top level (hessian_policy, method_meta, resolved_spec, shermo_adapter, thermo_normalize, translation, keyword_registry) — see qc/AGENTS.md + qc/interfaces/AGENTS.md
├── backends/        # Capability-Protocol adapter layer (no subprocess; base.py declares 12 Protocols; matrix.py = single capability-declaration authority) — see backends/AGENTS.md
├── calculation/     # Task layer — the SINGLE execution station: tasks/ (15 cores), requests/results contracts, _common, batch, selection, progress + ACP2 NEW capabilities/ products/ recipes/ — see calculation/AGENTS.md
├── analysis/        # Science-analysis home (87 .py): nmr/ (full DP4/DP5/FCHL + model_assets), research/ (evidence/provenance/question/reaction/selectivity + packaged contract corpus), pes/, ensemble/, bounded/, structure/, confsearch/, tsmode/, sampling/, xtbmd/ — see analysis/AGENTS.md
├── plugin/          # Science plugin ecosystem (8 .py): metadata/discovery/host/sdk/errors + readonly_parser & example_xyz pilots — see plugin/AGENTS.md
└── utils/           # 5 shared utility modules (constants, file_io, geometry_tools, resource_utils, solvent_map)
```

**History note:** ACP-only subpackages from the 2026-07 fork (`benchmark/`, `ensemble/`, `recipes/`, `funnel/`, `search/`, `thermo/`) were removed in the 2026-07-13 reverse-sync. **Do not confuse**: the names `ensemble/` and `recipes/` exist again since ACP 2.0 in **different scope** — `analysis/ensemble/` (ensemble science + A-24b protocol binding) and `calculation/recipes/` (recipe DAG compiler). `qc/interfaces/xtb.py` was re-created 2026-07-27; CENSO/ISOSTAT/Molclus subprocess logic consolidated into `qc/interfaces/{censo,isostat,molclus}.py` 2026-08-02; `run_isostat` removed in wave-8.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Config loading | `config.py` | 6-source merge see root AGENTS.md for order |
| **Executable resolution** | **`software.py`** | **THE single resolver — `resolve_executable(name, configured_path)`; all backends/interfaces/API/catalog route through it** |
| Version | `__init__.py` + `version.py` (+ `acp/__init__.py` + `pyproject.toml`) | **4-way sync** — see root ANTI #2 |
| Task cores (execution) | `calculation/tasks/` | Single execution station; 15 `run_*` cores; contracts via `calculation/{requests,results}.py` |
| Capability registry / task binding | `calculation/capabilities/` | ACP2: descriptor → task kind → `TaskRequest`; `builtin.py` 4 built-ins + `domains.py` nmr/thermochemistry |
| Science recipes | `calculation/recipes/` | `compile_recipe` → deterministic TaskRequest DAG; `validate_recipe` |
| Science products | `calculation/products/` | Versioned `ScienceProduct` (structure_trajectory/curve_scatter/vibration/derived) |
| Science analysis (NMR etc.) | `analysis/` | See analysis/AGENTS.md |
| NMR model assets | `analysis/nmr/assets.py` | **Single locator** — `asset_dir()/resolve_asset()`; data dir `analysis/nmr/model_assets/` (8 files, no `__init__.py`); do NOT hardcode paths |
| Science contract corpus | `analysis/research/contracts.py` + `analysis/research/contracts/` | Byte-identical packaged copy of `docs/reference/next-change/acp2-contracts/science/`; drift-guarded by `tests/test_acp2_science_installed_package.py` |
| Plugin discovery/SDK | `plugin/discovery.py`, `plugin/sdk.py` | Entry-point group `cccp.science_plugins`; manifest `cccp_science_plugin.json` |
| Capability backends | `backends/` | Implementation home; `acp/backends/*` are pure re-export shims |
| Subpackage docs | `analysis/`, `plugin/`, `calculation/`, `core/`, `utils/`, `backends/`, `qc/…` AGENTS.md | |

## CONVENTIONS
- **Imports**: full package path; `src/cccp/**` must never import `acp` (incl. lazy/TYPE_CHECKING; guards: `tests/test_cccp_isolation.py`, `scripts/verify_cccp_isolation.py`); backends must not import task modules (root ANTI #17)
- **`__all__`**: most subpackage `__init__.py` re-export public symbols — **exceptions**: root `__init__.py` and `analysis/__init__.py` are docstring-only (no exports)
- **Config defaults**: Python built-in `_get_default_config()` is authoritative, NOT `config/defaults.yaml`
- **`__version__`**: 4-way duplication (`cccp/__init__.py`, `cccp/version.py`, `acp/__init__.py`, `pyproject.toml`) — must update ALL
- **Frozen contracts**: science/contract dataclasses are `@dataclass(frozen=True)`; extend additively

## ANTI-PATTERNS
- **Version duplication**: `__version__` lives in four files across the packages — diverges easily
- **No CLI / no `__main__.py`**: `python -m cccp` does NOT work — use the unified `acp` CLI
- **Second asset path**: NMR model assets are reached only via `analysis/nmr/assets.py` — never a hardcoded `model_assets` path or a re-created `acp/nmr/models/`
- **Stale docstrings**: `calculation/requests.py` docstring says "seven P2 capability tasks" but `TaskKind` has 15 members (7 core + 8 P2) — fix docstrings, not the enum

## INSTALLED ROLE (B25b, 2026-10-23)
- `cccp` installs standalone (`packaging/roles/cccp/pyproject.toml`): no `acp`, `acp_services`, fastapi or Controller is required for the public scientific API.
- Wheel assets include `analysis/nmr/model_assets/{NOTICE.md,LICENSE-DP5,*.gz,*.p,*.txt}`, `analysis/research/contracts/*` + `examples/`, `cccp/py.typed`, `cccp/plugin/sdk.py` and the AGENTS docs; the controlled singlepoint sample runs from the installed venv.
- Version stays `0.3.0` in the four repository locations; a CCCP release must not require DSH/browser dependencies.
