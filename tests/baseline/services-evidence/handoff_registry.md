# Handoff registry — 跨路交接登记表（B00-3 / todo 3，与 C-T5 共同维护）

**Created:** 2026-10-18，基线 HEAD `5184f1452de1fdad73e2a78b5739daa6e671eb61`。
**Owner of this file:** B 路集成写者（见 `single_writer_map.md` 第 4/7 行）；A/C 引用**同一版本**的本表，不各自复制。
**含义:** 每次跨路交接（文件/契约/符号从一路交给另一路）在此登记。每片交接须能回溯：谁提供、谁消费、基于哪个基线、前置、验收方式、补丁是否已交付、是否已在消费者分支集成。

## 列定义

| 列 | 含义 |
|---|---|
| owner | 本片交接的负责路（A/B/C） |
| 可修改范围 | 交接涉及的文件/符号范围（glob 或符号名） |
| provider | 提供方（哪一路产出该工件/接口） |
| consumer | 消费方（哪一路接收并集成/消费） |
| base_commit | 该交接的基线 revision（SHA） |
| prerequisites | 前置条件（依赖的任务号/工件/契约版本） |
| acceptance | 验收方式（消费者侧如何判定可用） |
| applied_commit | 补丁「已交付」的提交；若已集成，记录消费者分支的集成提交 |
| 状态 | handoff 状态枚举：pending / delivered / integrated / rejected |

## 状态语义（补丁「已交付」与「已集成」分开）

- `pending`：交接已登记，尚无交付提交；消费者不得据此开发真实功能。
- `delivered`：提供方已提交该工件/契约补丁**到共享分支**，但消费者尚未应用并通过测试。
- `integrated`：消费者分支已应用该补丁且通过验收测试（`applied_commit` 记集成提交）。
- `rejected`：该交接被驳回或撤销，须在 prerequisites/acceptance 记录理由。

**规则：** 只检查「样例是合法 JSON」不算 integrated；必须由消费者侧通过契约/行为测试。提供方单方提交只能记 `delivered`。

## 交付物与机制版本（与 X2 对齐）

本表登记**交接这一动作**；交付物的字段语义冻结在 `docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`（版本随该文件头部冻结）。每份 X2 交付物至少一行：Access 与配置边界、Data prepare/read、事件流、科学产品与帧 adapter、执行协议与科学闭包、发行与资产。

## 登记表

| owner | 可修改范围 | provider | consumer | base_commit | prerequisites | acceptance | applied_commit | 状态 |
|---|---|---|---|---|---|---|---|---|
| A | `frontend/**`、Node 与前端专属测试 | A | B/C 提供接口与 fixture | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | A00 前端基线地图（`docs/reference/current/ACP_Frontend_Baseline_Map_20261018.md`） | B 以黑盒浏览器场景执行；C 判定科学等价，A 源码写权不转移 | n/a | pending |
| B | `src/acp_services/**`、scheduler/api、平台 planning/recovery、部署/config/CLI/init | B | A 消费；C 提供科学入口 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | 首片契约冻结（本表 + `ACP_Services_First_Slice_Contracts.md`） | B 保持现有 `src/acp` 兼容路径并通过定向测试 | n/a | pending |
| C | `src/cccp/**`、登记移交的旧 acp 科学专属文件 | C | B/C 先固定逐文件或逐符号边界 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | C00 闭包地图（渠道 `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C00/C01） | C 提取唯一实现与旧 shim 后由 B 应用接线 | n/a | pending |
| B | 混合的 workflows/catalog/executor/results 等旧文件 | B 为接入写者 | C 提交符号迁移补丁与调用替换 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | C 提交具体符号迁移补丁；登记移交后才转移文件写权 | 禁止双方整文件搬迁；逐符号边界由 `migration_ledger.md` 守护 | n/a | pending |
| C | 科学 schema、黄金 fixture、数值/单位/模式身份 | C | B/A 审阅可执行性与可显示性 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | cccp 任务契约（`cccp/calculation/requests.py`+`results.py`） | 平台 manifest/frames adapter 由 B 写并消费 C 的同一组 fixture | n/a | pending |
| B | 平台 API/事件/prepare/envelope schema | B | A/C 共同审阅 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | 先固定正常/空/部分/错误/旧版本样例（见 `contract_samples/`） | 消费者对提供方发布的同一组 fixture 做契约测试 | n/a | pending |
| B | `pyproject.toml`、`requirements-node.txt`、根 README/AGENTS、主方案、全局架构门、共享集成测试 | B 为集成写者 | A/C 提交精确修改需求 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | 科学/QC 门修订由 C 提供证据 | 各专属清单由所属路维护；B 只集成不越权改科学语义 | n/a | pending |
| B | 组件 manifest（`docs/reference/next-change/ACP_Services_Component_Manifest.md`） | B | A/C 引用 | `7074692` | 主方案 §27 模块归属 | manifest 状态枚举与 §27.1 对齐；候选不进 pyproject | `7074692` | delivered |
| B | 首片契约 + 逐符号台账 + 单写者表（本目录） | B | A/C 消费同一版本 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | B00-3 验收；X2 六交付物冻结 | checker `tests/test_services_first_slice_contracts.py` PASS | n/a | pending |
| B | 现有 `acp.backends` re-export shim → `cccp.backends` 移交（前序计划样板） | B（历史） | 全 acp 消费者 | `2a23b93` | 前序迁移计划收口 | `test_architecture_invariants.py` 依赖方向门 | `66222f7` | integrated |
| C | C01 逐工作流闭包（HN-01，8 工作流）+ C00 闭包地图 | C（HN-01 `ffcf1b8`；`tests/baseline/cccp-evidence/handoffs/HN-01-b06-closure.md`） | B（B06-3 todo 32 应用接线） | `ffcf1b8` | 计划三 C01（HN-01 delivered） | `tests/test_services_chain_switch.py`（能力表 8 integrated / 7 legacy；唯一调度器 + 按 attempt 冻结）+ 定向回归 PASS；真实科学 envelope 启用归 todo 34 | `f7a3321` | integrated |
| A | A00 前端基线接管地图 | A | B/C 引用 | `5184f1452de1fdad73e2a78b5739daa6e671eb61` | 无 | 地图被 `ACP_Frontend_Baseline_Map_20261018.md` 引用 | `f410ac9d`（地图基线） | delivered |
| B | 科学产品 → 帧 adapter（`src/acp/results/frames_adapter.py`、`tests/fixtures/science_products/frames_mapping/`；消费 C 的 HN-02） | C（HN-02 映射表 + 五态 fixture，`4d2c6f8d`） | B（adapter）+ A（显示） | `48b3d2f` | C-T13/HN-02 状态 A「契约可消费」；`src/acp/results/frames.py` 只读（ANTI #21） | B 经 TrajectoryFrame.to_node 与 TrajectoryAnnotation.to_annotation 投影并通过 HN-02 normal/empty/partial/corrupt/old 五态 fixture；固定输入经 Data 发布与读取 | 随 B28b 应用提交 | integrated |
| B | `src/acp_services/store/**`、`src/acp_services/runtime/method_flags.py`、旧 `acp` 转发路径（store/migrations/naming/molecule_groups/storage layout+record/jobs types、catalog flag 转发） | B | A/C 消费（旧 import 路径不变，identity 转发） | `c874676b`（本切片起点；lane C 并发提交 `29b2e8f`） | todo 72 注入登记表（schema/store、migrations、参数转换行）；todo 6/11/25/31 已交付 | `tests/test_services_migration_equivalence.py`（隔离 DB 持久化/CAS + 参数转换 parity + identity + AST 隔离）+ 定向回归套件 PASS；`JobStorePort`/`MigrationsPort` 注入已删 | `1688a5a`（refactor(services): migrate injected store/migrations in-package） | integrated |
| B | `src/acp_services/runtime/{plan_contracts,step_result,step_requirements}.py`、旧 `acp/calculations/{contracts,step_result,step_requirements}.py` 转发路径、`acp_services/runtime/recovery.py` 默认门 | B | A/C 消费（旧 import 路径不变，identity 转发） | `d45c1dd`（本切片起点） | todo 72 注入登记表（plan/step requirements 行）；todo 27/31 已交付；todo 78 需要 Services-native 步骤门 | `tests/test_services_migration_equivalence.py`（plan 闭包 identity + evaluate_prerequisite/validate_plan parity + AST 隔离）+ 678 定向回归 PASS；`ServicesPorts.step_requirements` 注入已删 | `0286b2b`（refactor(services): migrate plan/step-requirements closure in-package） | integrated |
| C | `src/cccp/software.py::resolve_executable` / `_resolve`（显式配置无效时静默回落 PATH） | B（todo 79 发现并登记，证据 `tests/baseline/services-evidence/b41b_defect_fixes.md`） | C（修复解析器）；B（`acp doctor` 已用 source-aware 解析自保） | `15e568c` | todo 79 S4c 负例（`acp doctor` 显式无效 orca path）；B41b §2.1 S4c | C 修复后：显式配置但无效 → 返回 None（不回落），`acp init` 内嵌能力报告随之 FAIL；`tests/test_services_b41b_defect_fixes.py` + init capability 负例 PASS | n/a | pending |

| B | `src/acp_services/runtime/{attempt_freeze,execution_chain}.py` + `tests/test_services_chain_switch.py` | B | B/A | `878e355` | T24（组提交与执行权）；T25（singlepoint freeze）；T30（远端上传）；T32（同 attempt 协议） | `tests/test_services_chain_switch.py`（组提交、混合组、双进程、远端）与 `tests/test_services_worker_bootstrap.py` PASS | `c12490e` | integrated |
| B | `src/acp_services/runtime/attempt_completion.py` + `src/acp/services_completion_bootstrap.py` | B | B | `878e355` | T26（receipt/CAS/pending）；T27（终端路径）；T28（重启只重试发布） | `tests/test_services_worker_contract.py` 与 `tests/test_services_chain_switch.py` PASS | `399ccd7` | integrated |
| B | `src/acp_services/runtime/{receipt_identity,errors}.py` | B | B | `878e355` | T6（全字段身份）；T7（失败路径冲突） | `tests/test_services_worker_contract.py`（导入无环、单字段矩阵、冲突字节不变）PASS | `1f15bfe` | integrated |
| B | `src/acp_services/runtime/worker.py`（D01 truth table 与 task_result） | B | B | `878e355` | T5（完成事实权威） | `tests/test_services_worker_contract.py` 与 `tests/test_services_worker_bootstrap.py`（行矩阵、真实子进程、fail-closed）PASS | `5530495` | integrated |
| B | `src/acp_services/data/operations.py`（content identity、dedup epoch、find_by_identity） | B | B | `878e355` | T12（schema）；T13（epoch/并发）；T14（availability by identity） | `tests/test_services_prepare_operations.py` PASS | `ce326d4` | integrated |
| B | `src/acp_services/data/read_root.py`（ReadRoot.attempt、CacheIndexFacts.snapshot、CacheIndexPort） | B | B | `878e355` | T17（D04 快照）；T18（D11 attempt） | `tests/test_services_read_root.py` 与 `tests/test_services_cache_only_switch.py` PASS | `125c459` | integrated |
| B | `src/acp_services/data/transfer.py`（identity_check、强制 authorization） | B | B | `878e355` | T15（lease 身份）；T16（request factory 与发布守卫） | `tests/test_services_transfer_verify.py` 与 `tests/test_services_prepare_operations.py` PASS | `f25f908` | integrated |
| B | `src/acp/calculations/attempt_publication.py` + `src/acp/scheduler/manager.py`（publication pending） | B | B | `878e355` | T28（task_result 绑定、partial、重启恢复） | `tests/test_services_chain_switch.py` 与 `tests/test_acp_workflows_simple.py` PASS | `399ccd7` | integrated |
| B | `src/acp_services/runtime/execution_chain.py`（DEFAULT_ENVELOPE_WORKFLOWS）+ `src/acp/scheduler/runner.py` hook | B | B | `878e355` | T29（singlepoint 默认）；T32（先协议后激活） | `tests/test_services_workflow_enablement.py` 与 `tests/test_services_chain_switch.py` PASS；15 工作流兼容性保持 | `2b594d2` | integrated |
| B | `src/acp/scheduler/remote/{runner,sync}.py`（远端冻结组、release、receipt 与 task_result） | B | B | `878e355` | T30（冻结先于选择、manifest 最后提交、三窗口） | `tests/test_services_chain_switch.py` 与 `tests/test_remote_phase1.py` PASS；真实 LSF 为 NOT_VERIFIED | `c12490e` | integrated |
| B | `src/cccp/analysis/structure/parsers.py`（path_safe）+ `src/acp/intake/parsers.py`（Path_safe 旧导入面） | B | A/B | `878e355` | T33（命名诊断与兼容别名） | `tests/test_acp_intake.py` 与 T33 import smoke PASS；旧 `Path_safe` 仍可导入 | `ab987e5` | integrated |

| B | `packaging/roles/**`、`scripts/build_release.py`、`release/release-manifest.json`（0.3.0 角色 wheel/sdist） | B | A（A-27b）、C（C33）、B（B25b/B26a） | `672b651` | B25a 冻结；A-27a 打包规格；C32 静态资产请求 | `scripts/acceptance/check_release_artifacts.py` + `release_role_probe.py`；B25b `package-install` 校验摘要与安装 | B25b（本提交） | integrated |
| B | 独立安装验收与发布路径（`scripts/acceptance/acp2_package_install.py`、`reports/acp2_implementation/bb45a46/B/B25/`） | B | B26a/B26b、A/C 文档引用 | `672b651` | B25a 实际制品；A-27b/A-28 原始回执（C33/C34 未发布） | 干净 venv 安装四个发行、CCCP 无 Controller、Lab/MCP/CLI、资产/notice/license、四版本位置；`package-install` 返回 0 | B25b（本提交） | integrated |

## B25b raw install/operation aggregation (2026-10-23)

> 本表只汇总 A/C 已落盘的原始事实。B25b 不重签科学或体验结论；缺失路径保留 `NOT_VERIFIED` 与预期路径。

| track | task | registered path | observed | B25b treatment |
|---|---|---|---|---|
| A | A-27b | `/ACPServer/acp2-evidence/a-science-20261022/A/A-27/a27b-20261023T042054Z` | raw result present; its own `platform_row` still records `NOT_VERIFIED` because it was produced before the B25a candidate | aggregated as raw evidence; no science re-sign-off |
| A | A-28 | `/ACPServer/acp2-evidence/a-science-20261022/A/A-28/20261023T044051Z` | raw result `PASS` on the A-built cccp-0.2.0 wheel | aggregated; does not replace B25b's 0.3.0 install |
| C | C33 | `/ACPServer/acp2-development/evidence/C/clean-install-live` | absent | `NOT_VERIFIED` at the registered path |
| C | C34 | `/ACPServer/acp2-development/evidence/C/operator-runbook-live` | absent | `NOT_VERIFIED` at the registered path |
| C | static assets | `/ACPServer/AutoCalcPlatform/docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json` | absent | `NOT_VERIFIED(c_static_assets_absent)`; no mount fabricated |
| C | C35 | n/a | not a B25b prerequisite | not signed |

## Deferred capability handoffs (T36)

**Scope:** 以下六项是 GAP-14 的显式 deferred capability handoff。它们**不是**本批实施任务；
状态保持 `NOT_VERIFIED`/`DEFERRED`，不得改写成 `PASS`。owner 使用现有 closure/plan
渠道与路名，不新增未登记 owner。

| capability | owner | blocker | entry_condition | next_acceptance_gate | status | evidence |
|---|---|---|---|---|---|---|
| account/permission/quota production wiring | B — Runtime/Workspace (L2-12, L3-09) | 组件已交付但 `src/acp` 零消费者；OIDC 恒不可用；尚未接到 HTTP/CLI 提交与派发前置 | 先 local-single 跑通，再按部署档位接入 Runtime 提交/派发前置；Workspace 提供身份/授权 | L2-12/L3-09 实测跨用户拒绝、撤权、重复请求、配额竞争与执行隔离；未通过不声明多租户上线 | NOT_VERIFIED | `docs/reference/current/ACP_Services_Closure_20261019.md`（L2-12/L3-09）；`src/acp_services/runtime/admission.py`；`src/acp_services/workspace/identity_lifecycle.py` |
| MCP default entry | B — Services entrypoints (L2-14) | `entrypoints.mcp` 已交付且有测试，但 `src/acp` 无默认入口消费者；未与真实客户端联调 | Runtime/Data/Workspace 用例稳定并定义明确默认入口后接入 | L2-14 至少一个真实客户端联调；身份、失败、取消、幂等与 GUI/CLI 一致，不复制调度器 | NOT_VERIFIED | `src/acp_services/entrypoints/mcp.py`；`tests/test_services_mcp.py` |
| project-package CLI/API | B — Data archive/import (L2-13) | archive/import builder 已交付但未绑定 CLI/HTTP；命令、权限、完整性语义未接线 | M1 稳定后按 M2 交付；明确命令/权限/操作回执/完整性校验/幂等导入 | L2-13 本地导出→新环境导入→读取与证据完整；导入不自动执行任务 | NOT_VERIFIED | `src/acp/services_data_bootstrap.py`；`src/acp_services/data/archive.py` |
| metering production wiring | B — Runtime metering (L2-15) | 解耦模块与 26 项测试已交付，但 Runtime 完成/恢复/读取路径零消费者 | 在真实 Runtime 完成/恢复及读取路径接入已交付解耦模块；单独记录用量事实 | L2-15 重复/乱序回执不重复计量；计量失败不改变科学完成或结果可读性；收费另过独立门 | NOT_VERIFIED | `src/acp_services/runtime/metering_decoupling.py`；`tests/test_services_metering_decoupling.py` |
| persistent event A04 | A04-lane (A frontend + B Services events) | A04 刷新协议仍 DEFERRED；前端 `eventCursor:false`；没有真实持久事件协议/游标实现 | 真实服务协议和持久事实落地后独立交付 Client cursor/重置/重连与刷新切换 | L2-16 真实服务协议下验证 cursor/重置/重连；只关闭事件实际覆盖的轮询；旧服务/终态可用性刷新保持 | NOT_VERIFIED | `docs/reference/next-change/ACP_Services_Events_A04_Contract.md` |
| frontend prepare/availability consumption | A05 frontend (L2-11) | 后端 availability/prepared 端点已交付，但前端 `prepareAvailability=false` 且 `/availability`、`/prepared/` 零调用点 | D04—D06/D11 通过后，按真实 capability/部署配置显式接入 prepare 与只读查询；保留 S0 adapter | L2-11 默认页验证 queued/running/failed/ready、重启重试、终态后台预取；旧服务不收新协议；没有 GET 隐式 prepare | NOT_VERIFIED | `frontend/js/workbench/contracts/task_view_contract.js`；`docs/reference/current/ACP_Services_Closure_20261019.md`（L2-11） |

**Non-claims:** 上表六项均为 handoff registration，不是 implementation。模块/测试已交付的行
保持 `NOT_VERIFIED`；不得把“已交字段”写成“前端已接入”或把 `DEFERRED` 写成 PASS。

## 维护约定

- 本表**与 C-T5 共同维护**：C 路新增科学闭包/移交行后，B 在整合时更新 `applied_commit` 与状态。
- A 路引用本表时使用**同一版本**（同一提交）；禁止在 A/C 清单内复制一份状态不同的表。
- 行内不得出现 `|`；glob 中的 `/**` 允许。
