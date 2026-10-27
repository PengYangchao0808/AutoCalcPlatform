# Auto-Calc Platform (ACP) — Project Knowledge Base

**Generated:** 2026-10-26（ACP 2.0 三线合并收口后全量刷新；行数仅量级参考，以代码为准）
**Branch:** feat/frontend-plan1-closure（收口 HEAD `0d739b2`）

## OVERVIEW
Automated computational chemistry platform. Python 侧三包：`cccp`（科学底座，**202 .py**：QC 子进程层 + 共享科学适配 + backends 能力层 + calculation 任务层 + **analysis 科学分析层** + **plugin 插件生态**）、`acp`（工作流/规划/发布层，**289 .py**：统一 CLI + API/scheduler + init 向导）与 `acp_services`（平台服务层，**99 .py**：workspace/data/runtime/store/contracts/entrypoints；`acp → acp_services` 单向依赖）。Python 3.10+，setuptools，YAML config。

**ACP 2.0（2026-10 并行三线 A/B/C，已合并）**: A 线给 cccp 增加科学契约与插件生态（`analysis/research/` 证据/溯源/问题/反应类型 + 打包契约语料、`analysis/nmr/` NMR 全量迁入、`plugin/` 第三方科学插件、`calculation/{capabilities,products,recipes}/`）；B 线给 acp_services 增加研究运行时（持久预算/审批/跨会话事件游标恢复/attempt 冻结-完成链/基础设施适配器注册表/cgroup 恢复）与 MCP 入口（`acp mcp serve`，协议 `2025-06-18`，8 工具）；C 线交付 Workbench 体验（前端 `js/{client,workbench,visualization}`）与 **`rph/`**（Research Package Hub — git 跟踪的 pnpm TypeScript workspace，DSH/agent 驱动客户端）。四发行 0.3.0：`cccp` / `acp-worker` / `acp-web`（`packaging/roles/`）+ 根 `acp` 二选一安装。

**四层架构（迁移已收口，见 ANTI #17）**: ① `cccp/qc` 子进程接口与共享科学适配 → ② `cccp/backends` 能力 Protocol 适配层（无 subprocess）→ ③ `cccp/calculation` 任务层（**唯一执行驻点**，`tasks/` 15 个任务核心）→ ④ `acp` 工作流/规划/发布层。**Services 平台层**与四层科学栈正交：`acp_services` 持有 store/schema、Runtime 用例、Data prepare/read、研究运行时、事件、访问边界与发行契约的实现根，`acp` 旧路径保留 identity forwarder（见 ANTI #30）。

**可运行工作流（15 active，registry 驱动）**: `acp run Confsearch | PESsearch | BatchOptimize | XtbPathSearch | OrcaGradient | XtbConstrainedOpt | irc | scan | tsmode | casscf | nmr | singlepoint | optimize | frequency | xtb_optimize`（catalog 30 条 = 15 active + 14 retired + 1 planned `custom_sequence`；内部合成 `fake` 仅测试用）。simple 系列 + scan/irc 由 `workflows/simple.py` + `registry.py` 组装；XtbPathSearch/OrcaGradient/XtbConstrainedOpt 消费冻结 `pes2ts_*_request_v1` payload。CLI 子命令：**`run` / `doctor` / `init` / `task` / `mcp`**（`serve` 在 `acp run serve` 下）。

**Retired**（catalog `status:"retired"`，仅历史作业展示，**勿重建/勿扩展 CLI**）: ensemble, energy, xtbmd_censo_energy, mechanism, conformer, benchmark, mech-conf, mech-step, mech-confirm, mech-chain, optfreq, optfreqsp, Lowconfirm, Highconfirm（CLI 拦截 `_CLI_REMOVED_WORKFLOWS` 12 条；`conformer`/`benchmark` 无 parser，argparse 报 invalid choice）。

**Job 生命周期**: QUEUED/RUNNING/PAUSED/WAITING_REVIEW/FAILED/CANCELLED/COMPLETED — PAUSED 见 CONVENTIONS；checkpoint continue / rerun（**原地重跑**：同任务目录、attempt+1、旧 attempt 回执归档至 `WORK/00_RUNTIME/attempts/<N>/`）/ cascade purge；跨会话恢复契约 `acp2.runtime.events-recovery.v1`（事件游标 + outbox，见 acp_services/runtime）。

## STRUCTURE（地图 — 叶级明细一律看嵌套 AGENTS.md）
```
src/cccp/                 # 科学底座（9 子包，202 .py）
├── analysis/             # NEW(ACP2) 科学分析层 87 .py：nmr/（DP4/DP5/FCHL + model_assets 唯一资产位）、research/（证据/溯源/问题/反应/选择性 + 打包契约语料）、pes/ ensemble/ bounded/ structure/ confsearch/ tsmode/ sampling/ xtbmd/ — 见 analysis/AGENTS.md
├── plugin/               # NEW(ACP2) 科学插件生态 8 .py：metadata/discovery/host/sdk/errors + 试点；entry-point 组 cccp.science_plugins；discovery 只读 metadata — 见 plugin/AGENTS.md
├── calculation/          # 任务层 50 .py（唯一执行驻点）：tasks/ 15 核心 + requests/results 契约 + NEW capabilities/ products/ recipes/ + batch — 见 calculation/AGENTS.md
├── qc/                   # 子进程层 25 .py：interfaces/ 15 模块（ORCA/CREST/xTB/CENSO/ISOSTAT/Molclus + constraints/hess_file/orca_ts/route_render/xtb_path/xtb_scan/xtb_thermo）+ runners/ cluster/ + 共享科学适配（hessian_policy/method_meta/resolved_spec/shermo_adapter/thermo_normalize/translation/keyword_registry）
├── backends/             # 能力 Protocol 适配 13 .py：base.py 12 个 Protocol；matrix.py 能力声明唯一权威 — 见 backends/AGENTS.md
├── core/ io/ pipeline/ utils/   # 协议/候选、分子输入、thin 编排、共享工具（5 模块）
└── config.py software.py version.py __init__.py   # 6 源配置 / 可执行解析唯一驻点 / 0.3.0

src/acp/                  # 工作流/规划/发布层（289 .py）
├── cli.py                # argparse 统一入口（5.5k 行）：run/doctor/init/task/mcp；retired 拦截；--json/--dry-run 机器输出
├── init_wizard/          # NEW `acp init` 向导 13 .py：flows/plan（InitPlan/apply_plan）/newnode（TOFU SSH）/options（TIER_SPECS）/capabilities（三态 doctor）/lab_doctor/gating/persist/answers/prompts/sniff_local/sniff_remote
├── services_{,completion_,data_,events_,mcp_,publication_}bootstrap.py   # NEW 6 个组合根（services_data_bootstrap 2.5k 行：DataResourcePort/read_root/cache-only reader；services_mcp_bootstrap:build_mcp_runtime）
├── catalog.py            # WORKFLOW_CATALOG + METHOD_META + METHOD_SCHEMAS（5.3k 行）
├── confsearch/           # 4 协议构象搜索 + sampling 帧/盆/饱和度
├── calculations/         # 规划/发布层：contracts/checkpoint/executor/plans + primitives/（纯转发）+ pes/ batch/ irc/ tsmode/ + NEW attempt_freeze/attempt_publication/identity（attempt 链 + identity v2）
├── api/                  # FastAPI 20 .py / 11 routers：v1_routes(104 路由)/v2_routes + v2_structure_sources + NEW v1_science/v2_science/v2_research/v2_approvals/v2_budgets/science_projection/drafts/job_status_view/batch_preview — 见 api/AGENTS.md
├── scheduler/            # 45 .py（31 顶层 + remote/ 14）：jobs/manager(7k 行)/runner/store(转发)/stage_tasks/tasks/task_views/molecule_groups/structure_source_store/structure_source_indexer/job_edit/events(after_seq 游标)/registration/… — 见 scheduler/AGENTS.md
├── nmr/                  # 转发面（→ cccp.analysis.nmr；仅 analysis_revision.py + method_config.py 有 acp 侧实现）— 见 nmr/AGENTS.md
├── results/ storage/     # manifest v2 读写（ProductKind 15 种）+ frames（VIEW_REGISTRY 9 视图）+ NEW frames_adapter（ScienceProduct→帧契约）
├── core/ backends/ chem/ intake/ io/ compat/legacy/   # 通用机制 / 纯 re-export shim / 化学 / 摄入 / 结构 I/O / 历史只读
└── workflows/            # pes_search/batch_optimize/irc/simple/tsmode/xtb_path/xtb_constrained_opt/orca_gradient/nmr + registry

src/acp_services/         # 平台服务层（126 .py；acp → acp_services 单向；禁反向）
├── bootstrap.py          # ServicesPorts：过渡端口包（收窄至 10 字段；JobStore/Migrations/StepRequirement 端口已就地移除）
├── worker_bootstrap.py   # 独立 worker 组合根（零 acp import / 零 jobs DB；= acp-worker console script）
├── workspace/            # projects/admin/entry_authz/identity_lifecycle/identity_mapping/keys/boundary + NEW http_identity（多用户 HTTP 凭据→principal）/server_identity/command_identity（M0-04 唯一 CommandIdentity：HTTP/CLI/admission/幂等 scope）
├── data/                 # contracts/operations/transfer/resource_read/read_root/storage_binding/namespaces/archive/import_/budget(存储配额)/helpers + NEW artifacts（ArtifactRef 生命周期）/research_resources（Proposal/ResearchRecord CAS）
├── runtime/              # 44 模块 = 原 23 + NEW 21：research_budget/usage_units/approvals/canonical_digest/research_events(游标)/attempt_recovery/cgroup/adapter_registry/adapter_bootstrap/execution_right/receipt_identity/errors/attempt_completion/attempt_freeze/step_dag/science_binding/capability_registry/analysis_capabilities/idempotency/local_admission/metering_decoupling
├── store/                # store(JobStore CAS)/migrations(24 条)/record(s)/layout/naming/legacy_method/molecule_groups
├── contracts/            # access/envelope/events/config_plan/config_validation/action_gating/injection + NEW adapters（AdapterDescriptor）
├── entrypoints/          # diagnostics/machine_output/mcp(TOOL_ALLOWLIST 8 工具) + NEW application_params/controller_client/mcp_stdio/status_projection
└── events.py             # 纯 re-export shim（EventEnvelope/EventCursor/EventScope）

rph/                      # NEW(ACP2-C) Research Package Hub：git 跟踪 pnpm workspace（Node ≥22.19/pnpm 11.28.5）；4 包冻结方向 cli→dsh-bundle→bridge→research；DSH 本体在外部 $ACP2_TOOLS，不 vendor；rph 上层禁止再加 package.json
packaging/                # NEW 发行角色 roles/{cccp,worker,web}/pyproject.toml（第四发行 = 根 pyproject `acp`；acp-web 的 src/acp_web_assets 由 build_release.py 构建期生成，源码树无此目录）
frontend/                 # Workbench：ACP_Workbench_v2.html + js/{client(12),workbench(30),visualization(4),module_tests(33)} + css/workbench + js/vendor(3Dmol)
scripts/                  # build_release.py / install_systemd.sh / migrate_run_root.py / verify_cccp_{isolation,slice_guard}.py / check_grep_gates.py … + acceptance/（35 个验收脚本）
docs/                     # reference/current（29）+ reference/next-change（ACP 2.0 计划/评审/契约语料 acp2-contracts/{science,runtime,experience}）+ archive
reports/                  # 证据库（git 跟踪 ~1.5k 文件，acp2_implementation 为主 + 未跟踪验收残留）
tests/                    # 486 test_*.py（test_services_* 69 / test_acp2_* 66 / test_acp_* 170 / test_cccp_* 47 / browser 4）— 见 tests/AGENTS.md
examples/                 # 仅 example_inputs.txt
```

## 嵌套知识库索引（动手前先读对应 AGENTS.md）
| 知识库 | 覆盖 |
|---|---|
| `src/cccp/AGENTS.md` | cccp 包根：config/software/version + 9 子包导航 |
| `src/cccp/analysis/AGENTS.md` | **NEW** 科学分析层：nmr/research/pes/ensemble/bounded/structure/confsearch/tsmode/sampling/xtbmd + 资产位 + 契约语料 |
| `src/cccp/plugin/AGENTS.md` | **NEW** 科学插件生态：metadata/discovery/host/sdk/errors |
| `src/cccp/calculation/AGENTS.md` | **NEW** 任务层：15 核心 + capabilities/products/recipes |
| `src/cccp/backends/AGENTS.md` | 能力 Protocol 适配层实现体（12 Protocol；matrix.py 声明权威） |
| `src/cccp/qc/AGENTS.md` + `qc/interfaces/AGENTS.md` + `qc/cluster/AGENTS.md` | 子进程层（interfaces 15 模块） |
| `src/cccp/{core,io,pipeline,utils}/AGENTS.md` | ConformerEngine(dormant)/ProtocolSpec、输入处理、thin 编排、共享工具 |
| `src/acp/AGENTS.md` | acp 全模块叶级 WHERE TO LOOK（init_wizard/组合根/api/scheduler/…） |
| `src/acp/api/AGENTS.md` | FastAPI 11 routers、只读 science/research 面、MCP 关系 |
| `src/acp/nmr/AGENTS.md` | NMR 转发面（实现根在 cccp/analysis/nmr）+ 修订/评审面 |
| `src/acp/scheduler/AGENTS.md` + `remote/AGENTS.md` | Job 生命周期、持久化、stage_tasks、LSF 远程执行 |
| `src/acp/{backends,chem,core,intake}/AGENTS.md` | shim 层 / RDKit / 通用机制 / 摄入解析 |
| `src/acp_services/AGENTS.md` | 平台服务层：workspace/data/runtime(研究运行时+attempt 链)/store/contracts/entrypoints |
| `tests/AGENTS.md` | 测试约定、fixture 分层、慢测门控、三态证据规则 |
| **本文件（root）** | 全局契约、ANTI-PATTERNS、COMMANDS |

## WHERE TO LOOK（跨包入口 + 全局契约；模块内细节先查嵌套表）
- **CLI**: `src/acp/cli.py` — `run`（15 工作流 + `serve`）/`doctor`/`init`（init_wizard）/`task`（ingest/plan/execute/query/pause/unpause/continue/cancel/rerun/recover/export，`--controller <url>` 远程）/`mcp serve`（`--transport stdio|--controller`，→ `acp_services.entrypoints.mcp_stdio` + `services_mcp_bootstrap`）
- **工作流注册**: `workflows/registry.py` + `catalog.py`；新增 QC 协议: `cccp/core/protocols.py::_get_default_protocol_config()`
- **配置**: `cccp/config.py`（6 源合并）；ACP 侧 `acp/core/config.py`；数据目录唯一权威 `acp/core/paths.py::resolve_run_root`；可执行解析唯一驻点 `cccp/software.py::resolve_executable`
- **计算基元**: 实现体 `cccp/calculation/tasks/`（门控 `_common.py`；casscf CASSCF/NEVPT2；批量 `batch.py`）；能力/绑定 `calculation/capabilities/`；配方编译 `calculation/recipes/`；`acp.calculations.primitives` 纯转发 + ACP 发布契约；单步经 `acp/calculations/executor.py::CalculationPlanExecutor`，多步 `acp/calculations/batch/engine.py`
- **科学契约/证据**: `cccp/analysis/research/`（evidence/provenance/validators/question/reaction/selectivity/artifact_manifest）+ 打包语料 `analysis/research/contracts/`（与 `docs/reference/next-change/acp2-contracts/science/` 字节一致，drift guard）
- **NMR**: 实现根 `cccp/analysis/nmr/`（模型资产唯一访问 `nmr/assets.py`）；工作流入口 `acp/workflows/nmr.py`；修订/评审 `acp/nmr/analysis_revision.py`
- **插件**: `cccp/plugin/`（entry-point `cccp.science_plugins`，manifest `cccp_science_plugin.json`）；外部参考发行 `tests/fixtures/acp2/science/external_plugin/`
- **结果契约**: 写 `acp/storage/manifest.py`（v2，ProductKind 15 种）→ 读 `acp/results/manifest.py`；帧契约 `results/frames.py::VIEW_REGISTRY`（9 视图）+ `results/frames_adapter.py`（ScienceProduct→帧）；帧候选权威 `frame_candidates.json`
- **调度**: `acp/scheduler/manager.py`（pause/unpause/continue/rerun/purge/resume + prepare/发布/attempt 链 seam）；`job_edit.py`（edit-coverage registry）；事件游标 `scheduler/events.py` + `GET /api/v1/jobs/{id}/events?after_seq=`
- **API**: `api/v1_routes.py`（104 路由）+ `api/v2_routes.py` + v2_structure_sources + **只读 science/research 面** `api/{v1_science,v2_science,v2_research,v2_approvals,v2_budgets}.py`（零副作用，`science_projection.py` 纯投影）
- **研究运行时**: `acp_services/runtime/` — 预算 `research_budget.py`（身份=project+proposal_ref）/审批 `approvals.py`（plan_digest 绑定 + CAS）/事件 `research_events.py`（时间有界游标）/attempt 链 `{attempt_freeze,attempt_completion,execution_right,receipt_identity}.py`/科学绑定 `{step_dag,science_binding}.py`/适配器 `adapter_registry.py`
- **Services 装配**: `acp_services/bootstrap.py::ServicesPorts`（10 端口）+ `worker_bootstrap.py`（acp-worker 入口）+ acp 侧 6 个 `services_*_bootstrap.py`
- **MCP**: 协议 owner `acp_services/entrypoints/mcp.py`（TOOL_ALLOWLIST 8 工具，1:1 绑 Controller HTTP 命令）；stdio 帧实现 `mcp_stdio.py`；`acp mcp serve --controller <url>`
- **rph/**: `rph/README.md` + `rph/packages/{research,bridge,dsh-bundle,cli}`；契约文档 `docs/reference/next-change/acp2-contracts/experience/`
- **发行**: `packaging/roles/{cccp,worker,web}/pyproject.toml` + `scripts/build_release.py`（--out 须为空目录；release-manifest.json SHA-256）；验收 `scripts/acceptance/`（35 脚本：acp2_package_install / acp2_runtime --scenario package-install / release_role_probe / check_release_artifacts / four_combinations / acp2_experience_*）
- **Frontend**: `frontend/ACP_Workbench_v2.html` + `js/client|workbench|visualization`（C 线 Workbench）；能源/轨迹查看器、任务向导、结构来源面板
- **权威设计文档**: `docs/reference/current/`（Job File Layout / Energy Trajectory Viewer / PES Manual Review / Simple Workflows / Services Closure）+ `docs/reference/next-change/`（ACP 2.0 并行计划 A/B/C + 评审 R1-R6 + MCP/DSH 验收 runbook）

## CONVENTIONS
- **Docstrings**: cccp Google 风格；acp 紧凑单行/短块 — 随所在包
- **注解**: 新代码一律 PEP 604 `X | None` + `from __future__ import annotations`
- **Imports**: stdlib → 第三方 → local；每模块 `logger = logging.getLogger(__name__)`；`pathlib.Path` + `os.replace` 原子写（禁 `os.path.*`）
- **Dataclasses**: `@dataclass(frozen=True)` 用于 spec/contracts；可变仅数据容器
- **抽象**: cccp `ABC` vs acp `Protocol`（PEP 544）；例外 `QCBackend(ABC)`、`ErrorModel(ABC)`、`CRESTInterface` 无基类（勿"修正"）
- **风格**: `__all__` 全量 re-export（例外：`cccp/__init__.py` 与 `cccp/analysis/__init__.py` 仅 docstring）；ruff（E/F/I/N/W/UP）+ ruff-format + mypy(strict) + pre-commit
- **Job PAUSED 语义**: active 非终态（计入队列、禁删），poller 跳过；本地 killpg SIGSTOP/SIGCONT，远端 bstop/bresume；restart：本地 → FAILED `[RESTART_FAILED]`，远端重收养保持 PAUSED；cancel 顺序 SIGCONT → SIGTERM → SIGKILL
- **Task custom names / Structure-source store / Job-edit coverage**: 见 ANTI #12/#23 与 `scheduler/AGENTS.md`
- **Services 依赖方向**: `acp_services → acp` 禁止（单向依赖硬门，`acp → acp_services` 允许）；`src/acp_services/**` 禁止导入 `acp`（含懒加载/TYPE_CHECKING/importlib/字符串反射）；旧路径 identity forwarder（`is` 同一对象）
- **Services 台账/证据**: 平台能力登记 `tests/baseline/services-evidence/migration_ledger.md`（6 列）；公开兼容面登记 `public_shim_inventory.md`；三态 PASS/FAIL/NOT_VERIFIED，`NOT_VERIFIED` 不得改写 PASS
- **研究运行时**: 预算身份服务端固定（project+proposal_ref）；审批不写执行事实；`canonical_digest.py` 是唯一 digest 实现；适配器 discovery 永不执行代码
- **ACP 2.0 证据**: `reports/acp2_implementation/`（当前基线 bb45a46）；C33/C34 与 experience/static-assets.manifest.json 仍 `NOT_VERIFIED`

## EXECUTION PROTOCOL（执行完整性四不变量，2026-10-06；ACP 2.0 attempt 链扩展）
> 实现驻点：`acp_services/store/store.py`（CAS）、`acp/scheduler/manager.py`、`scheduler/remote/{paths,release,submission,runner}.py`、`acp/calculations/{executor,identity}.py`、`calculations/batch/engine.py`、**NEW** `acp_services/runtime/{attempt_freeze,attempt_completion,execution_right,receipt_identity}.py` + `acp/calculations/{attempt_freeze,attempt_publication}.py`。

1. **一次 attempt ↔ 一个已确认的存储位置 / 执行版本 / 提交记录**：…（同前）… attempt 冻结产物组提交于 `WORK/00_RUNTIME/frozen/`（`frozen_group.json` sha256 manifest）；提交幂等经 `JobStore.claim_idempotency`（migration 022）。
2. **控制意图不被旧观测覆盖**：… 状态写入一律走 CAS API；worker 侧持 `execution_right`（独占 (job, attempt) 回执写权，fencing）。
3. **恢复只复用身份与工件有效的结果**：… identity v2（`plan_identity` + per-step `step_identities`）；跨会话事件恢复走时间有界游标（过期=显式快照重置，绝不静默跳变）。
4. **下游只消费满足前置条件的结果**：… 配方 DAG 由 `cccp/calculation/recipes/validation.py` 守护（cycle/diagnostic-dep 报错）。

## MIGRATION PERIOD RULES — 已收口（CLOSED at todo 32, 2026-10-05）
> acp→cccp 架构整改迁移期已结束。最终规则：计算任务核心唯一驻点 = `src/cccp/calculation/tasks/`（`acp/calculations/primitives/` 永久纯转发 + ACP 发布契约；`tests/test_architecture_invariants.py::test_unique_primitive_definitions` 守护）；`src/cccp/**` 禁止导入 `acp`（`tests/test_cccp_isolation.py` + `scripts/verify_cccp_isolation.py`）；backends 层禁导任务模块；`scripts/check_grep_gates.py` 四 pin 禁禁用；跨版本恢复 fixtures 固定于 `tests/baseline/recovery_fixtures/`；新计算能力一律先落 cccp 任务层。

## ANTI-PATTERNS（硬性铁律）
1. **NEVER `pymatgen`** — 已移除（零引用）
2. **NEVER 只更新一处 `__version__`** — 4 处同步：`cccp/__init__.py`、`cccp/version.py`、`acp/__init__.py`（importlib.metadata + fallback `0.3.0`）、`pyproject.toml`
3. **YAML defaults 与 Python built-in 须同步** — built-in `_get_default_config()` 权威
4. **协议只进 `_get_default_protocol_config()`** — YAML `protocols` 节不可达
5. **NEVER 在 `__init__.py` 放实现** — 例外仅 `qc/cluster/__init__`、`qc/runners/__init__` 与纯 re-export 壳
6. **`__main__` 仅 acp** — `python -m cccp` 不可用；console script 一律 `acp`（+ 发行角色 `acp-worker`）
7. **ORCA `%geom` 关键字是 `Trust` 非 `TrustRadius`** — `MaxIter` 出自 `max_cycles`/`geom_maxiter`
8. **新代码禁裸 `except Exception:`**
9. **常量单点定义** — 新常量禁复制
10. **CI（更新 2026-10-27/M0-02b）**: push main/feat/** + PR → py3.10/3.11/3.12 compileall + `pytest -m "not slow and not contract"`（`--strict-markers` 默认开启，markers 见 `pyproject.toml`）；install 行必须含全部 extras + nmrglue（`pip install -e '.[dev,api,remote]'`）；browser 套件仅 3.11（`.[browser]` + playwright chromium）；**frontend-module-tests**（Node 22.19，`run_all.js` 非空强制门）；**rph-detect/rph job**（Node 22.19.0 + pnpm 11.28.5，存在 `rph/pnpm-lock.yaml` 才跑：install --frozen-lockfile → build → test → check → **package** → upload）；**mcp-conformance**（Python3.11+Node22.19+pnpm11.28.5）已接线真实门：先跑非空结构子集（mcp_process+mcp_backends 非 contract），再跑 `tests/test_acp2_mcp_sdk_contract.py -m contract`（JUnit 计数防静默空过：空选择/部分 skip/DSH 可得却全 skip 均 fail）；外部 DSH 树（~2.8GB，rev `687f282e`）经 repo variable `ACP2_DSH_SOURCE` 声明，lane 不自行拉取，声明却不可解析即 fail `DSH_DECLARED_BUT_UNRESOLVED`；各 job 记录 wall-clock，超时必须分片不得 skip。lint/format 门仍注释中
11. **`_SCHEDULER_MARKERS` 必须列出调度器预建文件全集** — 守护：`tests/test_scheduler_markers_audit.py`
12. **新增 job status 必须全表面同步** — `jobs.py::JobStatus` is_active/is_terminal + `store.counts()` + 前端 `getStatusClass`/i18n（zh+en）
13. **NEVER 裸 `DELETE FROM jobs`** — 走 `store.purge_cascade`
14. **`resume()` 仅 WAITING_REVIEW review-only** — pause/unpause/continue 是独立方法
15. **NEVER 把 `job_id`/时间戳放进磁盘路径** — job_id 仅 DB PK/job.json/task.json/WORK/00_RUNTIME
16. **mechanism 布局归一化（v1.2）** — 禁直接拼 `mechanism_study/<id>/` 路径
17. **四层架构与唯一执行驻点** — 新计算能力一律先落 `src/cccp/calculation/tasks/`；`acp/calculations/primitives/` 仅纯兼容转发 + ACP 发布契约；`acp/backends/` 仅纯 re-export shim；backends 层禁导任务模块；`src/cccp/**` 禁导入 `acp`
18. **compat/ 只读禁止写入** — 新代码消费数据走 `results/manifest`（v2）
19. **两层目录: 安装目录 ≠ 数据目录** — run_root 须原生文件系统；禁 `./ACP_runs` 相对默认、禁默认 `/tmp`
20. **前端禁硬编码退役 id 作默认值** — 目录驱动 `resolveDefaultWorkflow()`
21. **NEVER 绕过 `acp.results.frames` 契约发视图投影** — 一律经 `to_node()/to_annotation()`；ScienceProduct 经 `results/frames_adapter.py`
22. **energy viewer 禁提交任务** — 帧操作仅"保存为候选"；`energyGraphConfirmAndBatch` 为前端禁止标识符
23. **NEVER 添加 active 工作流而不登记 edit coverage** — `job_edit.py::EDIT_ACTIVE_WORKFLOWS` 必须同步
24. **远程提交必须带 scheduler markers** — `_upload_scheduler_markers` 在 bsub 前调用
25. **keyword choices 大小写折叠只走三个 choke points** — 禁逐参数 `type=` 包装器
26. **远程 `pending_fetch` 禁仅依赖浏览器 `?fetch=1`** — 后台 terminal transition 时 `_queue_catalog_prefetch`
27. **远程作业结果读取必须经 `_job_read_root`** — 远端走 `RemoteStructureCache.fetch_catalog`；写回走 `push_paths`
28. **前端 `api()` 超时禁泄露 raw AbortError** — SFTP 端点用 `apiRemote()`（60s）
29. **`acp_services` 禁止导入 `acp`** — 单向依赖硬门；禁把 `acp.*` 类型放进公开签名。**wire-id 例外（M1-01c，唯一范围）**：仅允许 `src/acp_services/entrypoints/wire_identifiers.py` 中经审查的常量值（当前 `NEGOTIATION_EXTENSION_ID = "acp.negotiation.v1"`，即 `REVIEWED_WIRE_IDENTIFIERS` 元组）；其他模块只能用常量引用，禁再出现字面量、禁 `chr(97)` 之类拼接；import / 懒加载 / `TYPE_CHECKING` / `importlib` / `__import__` / `getattr(...,"acp.*")` / `sys.modules["acp.*"]` 在该模块内亦**永不豁免**；ANTI #32 不新增例外。守护 `tests/test_acp_services_isolation.py`（按常量值+模块 allowlist，不按行号/函数位置豁免）+ `tests/test_services_injection_contracts.py` + `tests/test_services_worker_contract.py`
30. **旧路径只做 identity forwarder，不做第二实现** — 公共服务能力唯一实现根在 `src/acp_services/`
31. **兼容面与资产不可为"减行数"删除** — `acp.calculations.primitives`、`acp.backends`、`compat/legacy/`、退役引擎 shim、`tests/baseline/recovery_fixtures/`、`cccp/analysis/nmr/model_assets/`（NOTICE.md/LICENSE-DP5）与 `frontend/js/vendor/` 均保留
32. **Services 护栏/台账不得放宽或跳过** — `NOT_VERIFIED` 不得改写成 PASS，`已交付字段` 不得写成 `前端已接入`
33. **插件 discovery 永不 import 插件模块**（metadata-only）；enablement 一律 disabled-by-default；冲突/许可/版本/fixture 错误在启动前 fail-closed（`cccp/plugin/errors.py`）；插件不直接起子进程（执行经 calculation 层）。守护 `tests/test_cccp_plugin_{pilot,sdk}.py` + `tests/test_acp2_science_plugin_*.py`
34. **NMR 科学唯一实现根 = `cccp/analysis/nmr/`** — `acp/nmr/` 只做转发 + 修订/评审面；模型资产唯一访问路径 `analysis/nmr/assets.py`（禁硬编码 model_assets 路径；`acp/nmr/models/` 空目录为遗留，勿放回资产）
35. **科学契约语料字节一致** — `cccp/analysis/research/contracts/` 与 `docs/reference/next-change/acp2-contracts/science/` 必须同步修改（drift guard `tests/test_acp2_science_installed_package.py`）；模型/LLM 叙述永不作为证据（`reject_model_conclusion_as_evidence`）
36. **rph 冻结依赖方向 `cli → dsh-bundle → bridge → research`** — `@rph/research` 顶层自由（禁 import）；bridge 禁 fetch/HTTP/spawn；rph 上层不得新增根 package.json；DSH 本体在外部 `$ACP2_TOOLS/deepseek-harness`，不 vendor
37. **`src/acp_web_assets` 是构建期生成物**（build_release.py 从 frontend/ 拷贝）— 源码树无此目录，勿手工创建；发行制品一律经 `scripts/build_release.py` + `scripts/acceptance/` 验收，证据落 `reports/`

## COMMANDS
```bash
# Install
pip install -e .                 # 运行全部 pytest 需 -e '.[dev,api,remote]'（CI 同，另装 nmrglue）
pip install -e '.[api]' / '.[remote]' / '.[browser]' / '.[dev]'

# Run — CLI 子命令 run/doctor/init/task/mcp
acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
acp run PESsearch --from-job 20260823_001_Confsearch --output ./pes_out
acp run BatchOptimize --from-job 20260823_002_PESsearch --output ./batch_out
acp run XtbPathSearch --path-config request.json --output ./path_out      # 另 OrcaGradient / XtbConstrainedOpt
acp run irc --input ts_structure.xyz --input-role transition_state --ts-provenance prov.json --output ./irc_out
acp run scan --input "CCO" --coordinate 3,4,1.0,3.0 --output ./scan_out   # 默认无 ScanTS；--scants 显式 opt-in
acp run nmr --input "CCO" --spectrum exp_spectrum.txt --output ./nmr_out
acp run singlepoint --input molecule.xyz --method "wB97X-D4" --basis "def2-TZVPPD"
acp run optimize --input molecule.xyz --method "r2SCAN-3c"
acp run frequency --input molecule.xyz
acp run xtb_optimize --input molecule.xyz
acp run casscf --input molecule.xyz
acp run serve --port 8765
acp init [--answers answers.yaml | --plan]        # 初始化向导（资源菜单/新节点 TOFU/三态 doctor）
acp task --help                                   # Services 本地任务管理（--controller <url> 远程）
acp mcp serve --controller http://127.0.0.1:8765  # MCP stdio（协议 2025-06-18，8 工具）
acp doctor

# Retired → 现役替代
# ensemble→Confsearch censo-crest screen · energy→Confsearch rank1/cumulative-99 · xtbmd_censo_energy→Confsearch xtbmd-censo
# mechanism→PESsearch → BatchOptimize → irc · optfreq/optfreqsp/Lowconfirm/Highconfirm→BatchOptimize 各 profile

# Release (ACP 2.0 四发行)
python scripts/build_release.py --out dist/release        # cccp/acp-worker/acp-web 角色 wheel
python scripts/acceptance/acp2_package_install.py         # 干净 venv 安装验收
python scripts/acceptance/acp2_runtime.py --scenario package-install

# rph (Research Package Hub)
pnpm --dir rph install && pnpm --dir rph build            # tsc -b
pnpm --dir rph check                                      # check-boundaries.mjs + tsc -b
pnpm --dir rph test && pnpm --dir rph package             # dist-packages/*.tgz

# Test
pytest tests/ -v && pytest tests/ -m "not slow" -q
pytest tests/test_architecture_invariants.py tests/test_acp_services_isolation.py -q   # 架构守护
pytest tests/test_services_doc_sync.py tests/test_services_closure_record.py -q       # Services 收口门
pytest tests/test_cccp_plugin_sdk.py tests/test_acp2_science_recipe.py -q             # ACP2 科学
pytest tests/test_acp2_runtime_event_recovery.py tests/test_acp2_runtime_approvals.py -q

# Lint/format（pre-commit hooks = ruff --fix + ruff-format）
ruff check src tests && ruff format --check src tests
```

## ACP 2.0 RELEASE ROLES（B25b，2026-10-23）
- **四发行**: `cccp` / `acp-worker` / `acp-web` 0.3.0 互不重叠；根 `acp` 单发行保留，二者二选一安装。构建 `scripts/build_release.py`，安装验收 `scripts/acceptance/acp2_package_install.py` + `acp2_runtime.py --scenario package-install`。
- **角色内容**: `cccp` = `cccp/**` + model_assets + research 契约语料 + py.typed（无 CLI）；`acp-worker` = `acp_services/**`（deps cccp；script `acp-worker = acp_services.worker_bootstrap:main`）；`acp-web` = `acp/**` + 构建期生成 `src/acp_web_assets`（deps cccp + acp-worker + fastapi/uvicorn/paramiko；script `acp`）。
- **安装证据**: `reports/acp2_implementation/bb45a46/B/B25/`（summary.json 经 --check-only 通过；四个干净 venv；MCP 协议 2025-06-18 八工具）。
- **未发布**: C33/C34 与 C `experience/static-assets.manifest.json` 为 `NOT_VERIFIED`；生产 service/run_root 未触碰。
- **文档现状提示**: 根 `README.md` 的"开发状态"表止于 Phase 4 + 2026-10-05 收口，未吸收 ACP 2.0/Services/四发行 — 现状以本 KB 与 `docs/reference/next-change/` 为准。

## SYSTEMD SERVICE
- **Service**: `acp.service` · config `/etc/systemd/system/acp.service`（**generated — 编辑 `scripts/install_systemd.sh`，勿手改 unit**）· URL http://127.0.0.1:8765 · 日志 `sudo journalctl -u acp -f`
- **Reload**: 任何代码修改后 `sudo systemctl restart acp`（服务无 `--reload`）
- **发布/重启/冒烟**: 归发布流程（先跑 `scripts/acceptance/` + 更新 `reports/` 证据）
