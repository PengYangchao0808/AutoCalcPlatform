# acp/workflows/ — Workflow Modules

## OVERVIEW
Workflow implementations + the registry that maps CLI subcommands to `WorkflowSpec` builders. **14 active workflows** (registry-driven, catalog `status:"active"`): `Confsearch, PESsearch, BatchOptimize, XtbPathSearch, OrcaGradient, irc, scan, tsmode, casscf, nmr, singlepoint, optimize, frequency, xtb_optimize`. Simple 系列 + scan/irc 由 `simple.py` 组装；XtbPathSearch/OrcaGradient 消费冻结 `pes2ts_*_request_v1` payload（PES2TS → ACP 执行统一，本地专用，不在远程允许集）。`ensemble.py`/`energy.py`/`energy_shared.py`/`xtbmd_censo_energy.py`/`xtbmd_md.py` 是**退役工作流引擎**（CLI 已拦截），但仍被 `confsearch/protocols/` 懒加载复用为 Confsearch 协议引擎。

## STRUCTURE
```
workflows/
├── __init__.py              # PEP 562 lazy re-exports (import acp.workflows is cheap/side-effect free)
├── _helpers.py              # Small shared helpers
├── registry.py              # CLI subcommand → WorkflowSpec builder mapping (catalog.SUPPORTED_WORKFLOWS-driven)
├── simple.py                # `acp run singlepoint|optimize|frequency|scan|irc|xtb_optimize` — single-request tasks
├── pes_search.py            # `acp run PESsearch` — confsearch manifest → PES scan → TS/INT candidates
├── batch_optimize.py        # `acp run BatchOptimize` — per-item Opt/TS + freq + SP + thermochemistry
├── irc.py                   # `acp run irc` — TS → IRC both directions → endpoint classification
├── tsmode.py                # `acp run tsmode` — 选虚频 → 源 Hessian → 定向 OptTS → 验证
├── nmr.py                   # `acp run nmr` — 输入/谱处理 → conformer search → GIAO → Boltzmann averaging → 归属 → DP4/DP5（证据门/协议 spec/诊断/报告）；另含 revise_nmr_analysis（峰值修订只重算分析）
├── xtb_path.py              # `acp run XtbPathSearch` — GFN2-xTB PATH metadynamics (frozen pes2ts_xtb_path_request_v1)
├── orca_gradient.py         # `acp run OrcaGradient` — ORCA EnGrad (frozen pes2ts_orca_gradient_request_v1)
├── ensemble.py              # RETIRED engine — CREST → CENSO preset+screening；仍作 Confsearch 协议引擎
├── energy.py + energy_shared.py   # RETIRED engine + shared helpers（opt/freq handoff、Boltzmann、ensemble summary）
├── ensemble_thermo.py       # Ensemble total-Gibbs helpers（retired 引擎配套）
└── xtbmd_md.py + xtbmd_censo_energy.py  # RETIRED engine — xTB-MD 采样 + ISOSTAT + CENSO 管线；仍作 xtb-md/xtbmd-censo 协议引擎
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| CLI → workflow mapping | `registry.py` | `list_workflow_entries()` / `get_workflow_entry()` — driven by `catalog.SUPPORTED_WORKFLOWS`（14 active）|
| Simple workflows | `simple.py` | singlepoint/optimize/frequency/scan/irc/xtb_optimize — 经 `acp.calculations.primitives.*` 兼容面（纯转发 → `cccp.calculation` 任务核心）|
| PES search | `pes_search.py` | 编排 `acp/calculations/pes/engine.py`（规划层）；执行 dispatch 到 `cccp.calculation` 任务核心 |
| Batch optimize | `batch_optimize.py` | 编排 `acp/calculations/batch/engine.py`；批量底层 `cccp.calculation.batch` |
| IRC | `irc.py` | 经 `acp.calculations.primitives.irc` 转发到 `cccp.calculation.tasks.irc`；端点分类 `cccp/calculation/irc_endpoints.py` |
| TS mode | `tsmode.py` | 编排 `acp/calculations/tsmode/engine.py` |
| NMR | `nmr.py` | `run_nmr_analysis` + `revise_nmr_analysis`（AnalysisRevision，谱峰修订只重算分析不重跑 QC）；直连 `cccp.calculation.tasks.{conformer_search,censo_refine,nmr_shielding}`（TaskContext/typed contracts）|
| XtbPathSearch | `xtb_path.py` | `run_xtb_path_search()` 薄封套 → `cccp.calculation.tasks.xtb_path_search.run_xtb_path_search`（TaskContext/typed errors）|
| OrcaGradient | `orca_gradient.py` | `run_orca_gradient()` 薄封套 → `cccp.calculation.tasks.orca_gradient.run_orca_gradient` |
| Confsearch 协议引擎 | `ensemble.py` / `xtbmd_censo_energy.py` | `confsearch/protocols/` 懒加载复用（勿删）；helper 共享于 `energy_shared.py` |
| Multi-replica MD sampling | `xtbmd_md.py` | `run_md_replicas()` — seed 递增 + RDKit multi-start；单轨迹职责在 `MolclusBackend.run_md` |

## CONVENTIONS
- **Lazy loading**: `__init__.py` uses PEP 562 `__getattr__` + `_LAZY_SOURCES` so importing one workflow does not pull in the others.
- **Task execution routing**: 计算执行一律经 `cccp.calculation` 任务核心（`run_*` task / `cccp.calculation.batch`）；workflow 层只做编排、payload 适配与产物发布（root ANTI #17）。
- **Backend layer**: QC capability 调用经 backends 兼容面（`get_backend(...)`，实现体 `cccp/backends/`）或直连 `cccp.backends.*`；从 workflow 层禁用 raw `cccp.qc.interfaces`。
- **Config**: `cccp.config.load_config()` resolves the merged config; workflows receive it as a dict.
- **Retired workflows**: `ensemble`/`energy`/`xtbmd_censo_energy`/`mechanism`/`mech-*`/`optfreq*`/`Lowconfirm`/`Highconfirm`/`conformer`/`benchmark` CLI 均已拦截（exit 2 双语提示）；catalog 保留 `status:"retired"` + `visible:False` 供历史作业展示。**勿为退役 id 重建 registry 条目**；退役引擎文件因 Confsearch 协议复用而保留，勿删。
- **XtbPathSearch/OrcaGradient**: 冻结 payload 契约（`pes2ts_*_request_v1`）经 `--path-config`/`--gradient-config` 提供；本地专用（不在 `_ALLOWED_REMOTE_WORKFLOWS`）。

## ANTI-PATTERNS
- **勿在 workflow 层新增计算任务实现体** — 实现体唯一驻点 `src/cccp/calculation/tasks/`（`test_unique_primitive_definitions` 守护）；workflow 只写薄封套/编排。
- **`# pyright:` suppressions**: a few type-checking rules suppressed; pyright is not in the project toolchain.
- **`CrestBackend.optimize()` / a few Protocol methods raise `NotImplementedError`** but structurally satisfy their capability Protocol (`isinstance(...)` is True) — capability declaration vs actual usability mismatch (pre-existing).
- **energy_shared.py 体量大** — 共享 helper 集中于此（Boltzmann/handoff/writers/resolvers），勿回灌进 energy.py，勿复制进新代码。
