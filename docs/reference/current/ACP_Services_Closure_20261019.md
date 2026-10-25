# ACP Services 分层收口记录（模块验证 / 默认入口接入 / 真实功能验收）

日期：2026-10-19。计划：`.omo/plans/acp-parallel-plan-2-services.md` todo 69（B13-4，Wave 14）。
体例参照 `docs/reference/current/ACP_Frontend_Closure_20261017.md`；四组合输入消费
`docs/reference/current/ACP_Services_Four_Combination_Acceptance.md`（todo 75，B13-7）。

> **总声明：本记录只关闭 Services 平台能力的「模块验证 + 已接入的默认入口」两层。**
> 真实功能层（浏览器 / 真实科学软件 / LSF / 生产）**逐项 NOT_VERIFIED**，并各自附原因与渠道。
> 模块通过 ≠ 真实通过；字段已交付 ≠ 前端已接入；S0 旧记录不能证明 S1。

---

## 1. 判定口径（三态 / 三层 / 证据等级）

### 1.1 三态（本文件唯一允许的状态词）

| 状态 | 含义 | 使用规则 |
|---|---|---|
| **PASS** | 在**本候选提交**上实际执行且断言通过 | 必须带证据路径；`claim=real` 的 PASS 必须带 tracked 证据 |
| **FAIL** | 在**本候选提交**上实际执行且断言失败 | 必须写失败原因与归属，不得改写成 NOT_VERIFIED |
| **NOT_VERIFIED** | 未执行，或执行环境缺失 | 必须写**逐项原因**与渠道（A05 / C10 / B / A04-lane） |

范围状态词只用于「是否属于本次声明范围」：`IN_SCOPE` / `DEFERRED` / `NOT_APPLICABLE`。
**延期不是通过；已执行失败只能是 FAIL；缺依赖未执行才是 NOT_VERIFIED**（三者不得互替）。

### 1.2 三层（本文件的表格分层）

| 层 | 判定问题 | 允许的证据类型 |
|---|---|---|
| **L1 模块验证** | 模块/契约/状态机在隔离夹具上是否正确？ | 定向 pytest（TestClient、真实 store/checkpoint/manifest 写入、临时 run_root） |
| **L2 默认入口接入** | 该模块是否被默认入口（`acp` CLI / FastAPI 路由 / `JobManager` 装配 / 前端调用点）**真实调用**？ | 入口注册探测 + 调用点源码行 + 该入口的定向测试 |
| **L3 真实功能验收** | 在真实外部依赖（科学软件 / LSF / 浏览器 / 生产）下是否成立？ | 真实执行日志；缺依赖即 NOT_VERIFIED |

### 1.3 证据等级（防「mock 通过记真实通过」）

- `claim=real`：真实外部依赖参与。PASS 时**必须**至少有 1 条 tracked 证据路径（非 `.omo/`）。
- `claim=in_repo`：真实产品代码路径，但外部科学/集群/浏览器被夹具替代。**只能记模块/入口通过**，不得外推。
- `claim=none`：未执行（NOT_VERIFIED）。

`tests/test_services_closure_record.py` 解析 §8 机器可读块并强制以上规则（含 5 条负例自检）。

---

## 2. 证据绑定（每份证据绑定实际提交 / 契约版本 / 配置 / fixture 摘要）

| 绑定项 | 值 |
|---|---|
| 收口候选提交（closure candidate） | `f5a9c9b85392e1ae018a1752db4e036e1a95646d`（branch `feat/frontend-plan1-closure`） |
| 四组合执行提交（t75，**不是**本提交） | `fded1160c4edb0974a6e49b528ee263975fd0075` |
| 本记录执行时的前一个 HEAD | `99b5ee2c5bb9c0249b5ea8cf6d892f67ddc5cc95`（F4 amendment） |
| 解释器 / Node | `python3.11 (3.11.13)` / `v20.19.0` |
| 配置 | 每个套件 `mktemp -d` 隔离 `ACP_RUN_ROOT`；默认配置 `cccp.config._get_default_config()`（built-in 权威，ANTI #3）；**未重启服务、未改配置、未改生产 DB** |
| First Slice 契约版本 | `contract_version = 1`；`docs/reference/next-change/ACP_Services_First_Slice_Contracts.md` sha256[0:16]=`e4e7dc4a0f020e38` |
| A04 事件契约 | sha256[0:16]=`140148c325dd7f42`；状态 **DEFERRED（未接入）** |
| fixture `contract_samples` | 31 文件，sha256[0:16]=`367815d7cb7291bc` |
| fixture `frames_mapping` | 12 文件，sha256[0:16]=`79d229e24101a430` |
| cache-only 切换矩阵 | sha256[0:16]=`6f97e465929bb7c1` |

**fixture 摘要算法**（本文件与 checker 同一实现）：对目录下所有文件按相对 POSIX 路径排序，逐文件
`sha256(relpath_bytes + file_bytes)` 流式累加，取前 16 位十六进制。上表 4 项在 §8 的
`binding.verified_digests` 中由 checker **重新计算并断言**。

**移动目标披露（并发车道）**：`99b5ee2..f5a9c9b` 只改 `src/cccp/analysis/**`、`src/cccp/backends/orca.py`、
`src/cccp/calculation/xtbmd_adapters.py`（lane C 的 Boltzmann 常量单源化）；
`git diff --stat 99b5ee2..f5a9c9b -- src/acp src/acp_services tests/ docs/reference/current/ACP_Services_Four_Combination_Acceptance.md`
为空 → 本记录引用的 Services 表面在 `99b5ee2` 与 `f5a9c9b` 之间**零差异**。

**S0/S1 分界（硬性）**：t75 的四组合在 `fded116` 执行；本记录在 `f5a9c9b` 复验 Services 表面。
`24ffca5c…`（todo 1 基线）等历史记录只作对照引用，**不拼接为本候选的通过证据**。
S1 组合（F0＋S1 / F1＋S1）各自在 `fded116` 有独立 `executed` 记录（由 t75 checker 守护）。

---

## 3. L1 — 模块验证

所有命令均在 `f5a9c9b`（或零 Services 差异的 `99b5ee2`）以 `python3.11 -m pytest … -q` 执行，退出码真实。

| ID | 项 | 状态 | 证据（路径） | 说明 |
|---|---|---|---|---|
| L1-01 | Data 端口 / 只读根 / 持久 prepare / 传输校验 | PASS | `tests/test_services_data_port.py`(11) `tests/test_services_read_root.py`(17) `tests/test_services_prepare_operations.py`(15) `tests/test_services_transfer_verify.py`(20) | 63 passed；claim=in_repo（本地临时 run_root，无远端） |
| L1-02 | 持久事件协议 / 授权流 / 顺序 / freshness | PASS | `tests/test_services_events.py`(24) `tests/test_services_events_authz.py`(19) `tests/test_services_event_ordering.py`(12) `tests/test_services_freshness.py`(21) | 76 passed |
| L1-03 | ExecutionEnvelope / worker 契约 / 整链切换 / 逐工作流启用 | **PASS（F-3 已修复）** | `tests/test_services_envelope.py`(28) `tests/test_services_worker_contract.py`(49 passed) `tests/test_services_chain_switch.py`(22) `tests/test_services_workflow_enablement.py`(26) | `3922f07` 将 `test_runtime_package_has_no_acp_or_db_access` 的守卫收窄到 worker 边界（`python3.11 -m pytest tests/test_services_worker_contract.py -q` → 49 passed，exit 0；`pytest tests/test_services_*.py -q` → 全绿）；负例注入（禁 `import acp` / `attempt += 1`）保留。**原 FAIL 作为被取代审计注记保留在 §6 F-3。** |
| L1-04 | store / schema 迁移等价 | PASS | `tests/test_services_migration_equivalence.py`(22) | 隔离 DB 持久化/CAS + 参数转换 parity + identity + AST 隔离 |
| L1-05 | 执行隔离能力矩阵 | PASS | `tests/test_services_isolation.py`(25) `tests/baseline/services-evidence/isolation_matrix.md` | 矩阵与代码同源；pseudo-isolation 拒绝 |
| L1-06 | 访问边界 / 身份 / API Key / 管理面 | PASS | `tests/test_services_api_keys.py`(32) `tests/test_services_local_boundary.py`(39) `tests/test_services_identity_model.py`(33) `tests/test_services_identity_mapping.py`(19) `tests/test_services_identity_lifecycle.py`(23) `tests/test_services_entry_authz.py`(63) `tests/test_services_admin.py`(52) | 261 passed；OIDC 恒 `OIDC_SUPPORTED=False` |
| L1-07 | 准入 / 配额 / 用量 / 计量政策 | PASS | `tests/test_services_admission.py`(15) `tests/test_services_quota.py`(22) `tests/test_services_usage.py`(32) `tests/test_services_metering_policy.py`(53) `tests/test_services_metering_decoupling.py`(26) | 148 passed；模块正确 ≠ 已接入派发（见 L2-12） |
| L1-08 | 归档导出 / 导入 | PASS | `tests/test_services_archive_export.py`(13) `tests/test_services_archive_import.py`(19) | 32 passed；模块正确 ≠ 已有 CLI/API 入口（见 L2-13） |
| L1-09 | MCP / 机器输出 / 结构化日志 | PASS | `tests/test_services_mcp.py`(39) `tests/test_services_machine_output.py`(18) `tests/test_services_logging.py`(25) | 82 passed；MCP 为 stdio 协议实现，未绑默认入口（见 L2-11） |
| L1-10 | init 统一 / 配置计划与应用 / Lab doctor / Standalone | PASS | `tests/test_services_init_unified.py`(28) `tests/test_services_config_apply.py`(23) `tests/test_services_config_validation.py`(32) `tests/test_services_action_gating.py`(26) `tests/test_services_lab_init.py`(27) `tests/test_services_standalone_recipe.py`(9) `tests/test_services_provisioning.py`(18) | 163 passed；Lab 档位在本机按能力拒绝（不静默降级） |
| L1-11 | 故障注入矩阵（未知提交/重复/崩溃/磁盘满/中断/旧 worker/权限/对账） | PASS | `tests/test_services_fault_matrix.py`(14) `tests/baseline/services-evidence/fault_matrix_report.md` | 14 passed；S08 预算竞争 **DEFERRED**（B11 价格门） |
| L1-12 | 一致备份恢复（DB/工件/密钥/账本） | PASS | `tests/test_services_backup_restore.py`(7) `tests/baseline/services-evidence/backup_restore_drill.md` | 7 passed；恢复后先对账再派发，0 重复 bsub；账本 DEFERRED |
| L1-13 | CAS / 提交回执 / 恢复语义 / Runtime 用例 | PASS | `tests/test_services_cas_fidelity.py`(10) `tests/test_services_submission_fidelity.py`(21) `tests/test_services_recovery_semantics.py`(10) `tests/test_services_runtime_usecases.py`(27) | 68 passed |
| L1-14 | helper 归并 / 注入契约 / worker 装配 / 存储绑定 / 投影 / 查询预算 / 发布端口 | PASS | `tests/test_services_helper_merge.py`(37) `tests/test_services_injection_contracts.py`(24) `tests/test_services_worker_bootstrap.py`(17) `tests/test_services_storage_binding.py`(14) `tests/test_services_task_projection.py`(9) `tests/test_services_query_budget.py`(10) `tests/test_services_publication_data_port.py`(20) | 131 passed |
| L1-15 | 首片契约 / cache-only 切换 / 四组合记录 checker | PASS | `tests/test_services_first_slice_contracts.py`(14) `tests/test_services_cache_only_switch.py`(32) `tests/test_services_four_combination_records.py`(16) `tests/baseline/services-evidence/cache_only_switch_matrix.md` | 62 passed；cache-only 含 F0 对拍与负例 |
| L1-16 | 共享护栏（架构/隔离/frames/markers/发布/执行完整性/恢复） | PASS | `tests/test_architecture_invariants.py`(20) `tests/test_acp_services_isolation.py`(23) `tests/test_cccp_isolation.py`(8) `tests/test_acp_frames.py`(10) `tests/test_scheduler_markers_audit.py`(3) `tests/test_result_publication.py`(11) `tests/test_execution_integrity_e2e.py`(7) `tests/test_checkpoint_resume_semantics.py`(18) `tests/test_recovery_fixtures_smoke.py`(19) `tests/test_recovery_cross_version.py`(8) | 127 passed；四项执行不变量与依赖方向门全绿 |
| L1-17 | grep 门四 pin + F4 共享门 + 打包 | PASS | `scripts/check_grep_gates.py`（`--suite architecture-remediation`）`tests/test_f4_scope_audit.py`(35) `tests/test_packaging_assets.py`(7) `tests/test_services_packaging_roles.py`(9) `tests/baseline/services-evidence/f4_scope_amendment_services.md` | grep gates run 10 / blocking 0 / stale allowlist 0（exit 0）；F4 由 todo 66 的 1 RED 变为 **35 passed**（amendment `99b5ee2`，等效门加强非放宽） |
| L1-18 | 每片验证纪律 / 计划一致性 | PASS | `tests/test_services_plan_consistency.py`(6) `tests/baseline/services-evidence/verification_discipline_report.md` | 83 todos 依赖图无环；Final-wave 排除仅 76 |

**L1 汇总（真实执行）**：`python3.11 -m pytest tests/test_services_*.py -q` → **全绿**（59 文件）。
L1-03 的 F-3 已在 `3922f07` 修复（worker 边界收窄，负例注入保留），摘要记录已刷新；
原始 FAIL 文本作为被取代审计注记保留在 §6，**未删除历史**。

---

## 4. L2 — 默认入口接入

判定 = 「该能力是否被默认入口真实调用」。**字段/模块已交付不算接入**。

| ID | 能力 | 默认入口 | 状态 | 证据（路径 / 源码行） | 说明 |
|---|---|---|---|---|---|
| L2-01 | `acp task` 本地任务管理 11 动词 | CLI `acp task` | PASS | `src/acp/cli.py:2173`（`_TASK_CONTROL_VERBS`）、`:2185`（`"task"` 子解析器）、`:4800`（dispatch）；`tests/test_services_cli_full_loop.py`(28) | `python3.11 -m acp task --help` exit 0，列出 `ingest/plan/execute/query/pause/unpause/continue/cancel/rerun/recover/export` |
| L2-02 | 只读 availability / prepared 新读取面 | HTTP `/api/v1` | PASS | `src/acp/api/v1_routes.py:7542`、`:7614`；`tests/test_services_cache_only_switch.py`(32) `tests/test_services_entry_authz.py`(63) | `create_app().openapi()` 中两条路由均在（共 146 路径） |
| L2-03 | 旧 GET 读取面保留（F0 兼容通道） | HTTP `/api/v1` | PASS | `src/acp/api/v1_routes.py`（legacy GET）；`tests/test_acp_api_v1.py`（t75 F0+S0 219 passed）；`tests/baseline/services-evidence/migration_ledger.md`（5 行过渡兼容面登记） | `/api/v1/jobs`、`/api/v1/jobs/{job_id}` 仍在 OpenAPI；未接入项保持旧功能可用 |
| L2-04 | 持久 prepare 执行器 + outbox 投递 | `JobManager` 装配 | PASS | `src/acp/scheduler/manager.py:4805`（`build_prepare_executor`）、`:80`（`open_outbox_delivery`）；`tests/test_services_prepare_operations.py`(15) `tests/test_services_event_ordering.py`(12) | manager 启动即装配，非仅模块可用 |
| L2-05 | 发布端口（manifest/markers/candidates） | 执行器 + 远端 runner | PASS | `src/acp/calculations/executor.py:754`；`src/acp/scheduler/remote/runner.py:1674`；`tests/test_services_publication_data_port.py`(20) `tests/test_result_publication.py`(11) | 发布顺序与 completed 时序保持 |
| L2-06 | 执行入口单一调度器（envelope/legacy 选择） | 本地 runner + 远端 script_gen | PASS | `src/acp/scheduler/runner.py:62`；`src/acp/scheduler/remote/runner.py:89`；`src/acp/scheduler/remote/script_gen.py:44`；`tests/test_services_chain_switch.py`(22) | 按 attempt 冻结；未就绪回 legacy 且不声称独立 |
| L2-07 | 逐工作流启用（首片 8/15） | `execution_chain` 能力表 | PASS（范围内） | `tests/test_services_workflow_enablement.py`(26) `tests/baseline/services-evidence/workflow_enablement_matrix.md` | `enabled: 8/15; legacy: 7/15; full-coverage gate: OPEN`；7 项 legacy 保持旧路径 |
| L2-08 | Runtime 控制用例（submit/pause/…/purge） | HTTP + CLI 共用 | PASS | `src/acp/api/v1_routes.py:274`（`RuntimeCommands`）、`:306`（`build_runtime_commands`）；`src/acp/cli.py:2114`、`:2530`（recovery）；`tests/test_services_runtime_usecases.py`(27) | 单一执行 owner；Web/CLI 同源 |
| L2-09 | `acp init` Lab doctor 分支 | CLI `acp init` | PASS | `src/acp/init_wizard/flows.py:549`（`render_lab_capability_report`）；`tests/test_services_lab_init.py`(27) | 仅 `tier == LAB` 分支；本机按能力拒绝，不静默降级 |
| L2-10 | 研究证据投影（lineage） | HTTP `/api/v2` | PASS | `src/acp/api/v2_routes.py:84`、`:1722`；`tests/test_services_evidence_projection.py`(14) | 路由 `/api/v2/tasks/{task_id}/lineage/projection` 在 OpenAPI |
| L2-11 | **前端消费 cache-only 新读取面** | 前端调用点 | **NOT_VERIFIED** | `frontend/js/workbench/contracts/task_view_contract.js:125`（`prepareAvailability: false, // NOT in S0`）；前端对 `/availability`、`/prepared/` 的调用点 **0 命中** | 原因：后端字段/端点已交付，但前端仍声明 S0 能力位为 false 且无调用点 → **「已交字段」≠「前端已接入」**；渠道 A05/B |
| L2-12 | 准入/配额/身份生命周期接入派发与 UI | Runtime submit / HTTP / UI | **NOT_VERIFIED** | `src/acp_services/runtime/admission.py`、`quota.py`、`workspace/identity_lifecycle.py`（`src/acp` 侧**零**消费者，grep 命中 0） | 原因：组件已交付（`f00625b`/`9e0185b`/`014234c`），但未接入 HTTP/UI 或 submit 前置；渠道 B（与 t75 §5 一致） |
| L2-13 | 归档导出/导入入口 | CLI / HTTP | **NOT_VERIFIED** | `src/acp/services_data_bootstrap.py:1103/1151/1181`（仅 builder 导出，`src/acp` 无消费者） | 原因：Data 侧构造器已交付，未绑 CLI/API 入口；渠道 B |
| L2-14 | MCP stdio 入口 | `acp` 默认入口 | **NOT_VERIFIED** | `src/acp_services/entrypoints/mcp.py`（`MCP_SERVER_NAME`）；`src/acp` 无 `entrypoints.mcp` 消费者 | 原因：协议实现与 39 项测试已交付，未绑默认入口（todo 43 只要求薄入口契约）；渠道 B |
| L2-15 | 计量解耦（用量采集与科学完成解耦） | 生产路径 | **NOT_VERIFIED** | `src/acp_services/runtime/metering_decoupling.py`（`src/acp` 零消费者） | 原因：模块 + 26 项测试已交付，未接入 Runtime 完成/读取路径；渠道 B |
| L2-16 | A04 刷新协议（cursor/重置/停轮询） | 前端事件消费 | **NOT_VERIFIED（DEFERRED）** | `docs/reference/next-change/ACP_Services_Events_A04_Contract.md`（sha16 `140148c325dd7f42`）；前端 `eventCursor: false`、`globalEventStream: false` | 原因：A04 为 DEFERRED / 未接入；本次交付是**前端兼容重构**而非新刷新协议；渠道 A04-lane |
| L2-17 | 全量 15/15 envelope 覆盖 | `execution_chain` | **NOT_VERIFIED** | `tests/baseline/services-evidence/workflow_enablement_matrix.md`（`full-coverage gate: OPEN`） | 原因：7 项 legacy 闭包由 C01 T20–T30 承担；本计划不声称全覆盖；渠道 C10/C01 |

**L2 结论**：Services 后端能力的**默认入口接入**已成立（L2-01…L2-10 全 PASS）。
前端侧消费（L2-11/L2-16）与若干「已交付未接线」组件（L2-12…L2-15）明确 NOT_VERIFIED，
**旧入口全部保留可用**（L2-03、L2-07 的 7 项 legacy），未接入不构成回归。

---

## 5. L3 — 真实功能验收 + 环境三门

### 5.1 环境三门（与 A05 / C10 / B 分列）

| 门 | 渠道 | 状态 | 依据 | 原因（逐项） |
|---|---|---|---|---|
| **浏览器** | A05 | **NOT_VERIFIED** | `.omo/evidence/acp-parallel-plan-1-frontend/task-31-acp-parallel-plan-1-frontend.md`、`task-32-…md`、`task-33-…md`；`docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md` | A05 的 13+2 浏览器矩阵绑定**其自身提交**（`899c7c0`/`29efba8`），不是本候选 `f5a9c9b`；B 只执行 A 提供的黑盒用例、**不取得 A 源码写权**，本轮未收到候选级黑盒用例；A05 退出门按计划保持 OPEN |
| **科学软件** | C10 | **NOT_VERIFIED** | `tests/baseline/cccp-evidence/c10-final-evidence-index.md`；`tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md` | 真实 ORCA/xTB/CREST/CENSO 未在本候选运行；`xtb`/`crest`/`censo`/`isostat`/`molclus` 不在 PATH；`/home/redacted-user/orca611/orca` 仅回应参数提示，未经 ACP 执行；C10 未在本候选出判定 |
| **LSF / 远端** | C10 / B | **NOT_VERIFIED** | `tests/baseline/services-evidence/isolation_matrix.md`（lsf-identity `configured`，live cluster NOT_VERIFIED）；`tests/test_remote_phase1.py` 系列（夹具） | `/opt/openlava-2.0/bin/{bjobs,bhosts}` 存在但 `lsb_init: Failed … Unable to open file lsf.conf` → 无可用集群；SSH 无节点凭据 |
| **生产** | B | **NOT_VERIFIED** | 计划 todo 76 门（F1–F4 全 APPROVE 后才发布） | 本 todo **未发布、未重启服务、未改 unit**；发布后冒烟归 todo 76 + F5 |

### 5.2 L3 行

| ID | 项 | 状态 | 证据 | 说明 |
|---|---|---|---|---|
| L3-01 | 本地闭环（真实 `acp` 进程：ingest→plan→execute→query→pause/continue/cancel→rerun→recover→export） | PASS（claim=in_repo） | `tests/test_services_cli_full_loop.py`(28) `tests/test_services_cli_offline_loop.py`(25) `.omo/evidence/acp-parallel-plan-2-services/task-74-acp-parallel-plan-2-services.md` | **科学后端为 `tests.conftest.FakeBackend`**；控制/恢复/重连腿为真实 SQLite + 真实进程信号 + 真实 CLI 子进程。**不声明真实科学通过** |
| L3-02 | 离线无网闭环（socket/paramiko 拒绝下仍可跑） | PASS（claim=in_repo） | `tests/test_services_cli_offline_loop.py`（`_block_network` 用例） | 证明本地闭环不依赖 Web/网络 |
| L3-03 | 跨版本恢复（真实 recovery fixtures） | PASS（claim=in_repo） | `tests/test_recovery_fixtures_smoke.py`(19) `tests/test_recovery_cross_version.py`(8) `tests/test_checkpoint_resume_semantics.py`(18) | v1 checkpoint 保守重算；fixtures 未被改动 |
| L3-04 | 一致备份恢复演练（真实 DB/工件/密钥） | PASS（claim=in_repo） | `tests/test_services_backup_restore.py`(7) `tests/baseline/services-evidence/backup_restore_drill.md` | 恢复后**先对账再派发**，0 重复 bsub；跳过对账的对照用例**预期 FAIL**（风险暴露） |
| L3-05 | 真实 ORCA/xTB/CREST/CENSO 科学执行 | **NOT_VERIFIED** | 同 §5.1 科学软件行 | 缺二进制/预算；不得以夹具或前端模拟替代 |
| L3-06 | LSF 提交/监控/停止范围实机 | **NOT_VERIFIED** | 同 §5.1 LSF 行 | 无 `lsf.conf`/无节点凭据 |
| L3-07 | 真实浏览器场景（A05 矩阵） | **NOT_VERIFIED** | 同 §5.1 浏览器行 | 候选级黑盒用例未交付；B 无 A 源码写权 |
| L3-08 | 科学等价（同输入新旧输出对照） | **NOT_VERIFIED** | `tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md`（provider `06ce045d…`，状态 B **pending**）；`tests/baseline/cccp-evidence/c10-final-evidence-index.md` | C10 未在本候选出判定；**禁前端模拟替代** |
| L3-09 | Lab 账户/会话 UI | **NOT_VERIFIED** | `src/acp_services/workspace/identity_lifecycle.py`（`OIDC_SUPPORTED=False`）；`docs/reference/next-change/ACP_Services_Identity_Support_Scope.md` | 组件已交付（`014234c`）未接线；OIDC 恒不可用 |
| L3-10 | 生产发布/静态缓存/旧标签页窗口 | **NOT_VERIFIED** | 计划 todo 76/71 门 | 未发布；兼容窗口归 todo 71 文档 + todo 76 执行 |

---

## 6. 已执行失败与已登记缺陷（不改写为 NOT_VERIFIED）

| # | 项 | 状态 | 归属 / 影响 | 证据 |
|---|---|---|---|---|
| **F-3** | `tests/test_services_worker_contract.py::test_runtime_package_has_no_acp_or_db_access` | **RESOLVED（真实执行，`3922f07`；原始 FAIL 作为被取代注记保留）** | **已修复**：`3922f07`（todo 80，F-wave remediation A）把守卫收窄到 worker 边界；`python3.11 -m pytest tests/test_services_worker_contract.py -q` → **49 passed，exit 0**，`pytest tests/test_services_*.py -q` → **全绿**。负例注入（禁 `import acp` / `attempt += 1`）保留，未放宽/未删除/未跳过。———— **原始 FAIL（已不重现，保留为审计历史）**：`runtime/admission.py`（`f00625b`）与 `runtime/quota.py`（`9e0185b`）按设计导入 `sqlite3`；`runtime/isolation.py` 含 `acp_jobs.db` 字符串；守卫禁止 `src/acp_services/runtime/**` 出现 sqlite3/DB 写入。 | `tests/test_services_worker_contract.py:552`；`.omo/evidence/acp-parallel-plan-2-services/task-67-…md` §F-3 |
| **F-1** | `TransferPipeline.prepare` 在 `OSError(ENOSPC)` 时上抛而非 typed rejection | 已记录（安全性成立） | 无部分发布/无 `.tmp` 残留/永不 READY；worker 记可重试失败。产品行为决策未在本 todo 变更 | `tests/baseline/services-evidence/fault_matrix_report.md` §F-1 |
| **F-2** | schema 迁移台账 forward-only，收缩不自动修复 | 已记录（设计限制） | 收缩/回退 DB 必须走一致备份恢复路径（L3-04），不重跑迁移 | `tests/baseline/services-evidence/fault_matrix_report.md` §F-2 |
| — | todo 66 遗留的 F4 1 RED | **已关闭** | `99b5ee2` 登记 Services migration stations + 6 项等效/更强检查；现 `35 passed`（非放宽） | `tests/test_f4_scope_audit.py`(35)；`tests/baseline/services-evidence/f4_scope_amendment_services.md` |

---

## 7. 四组合消费 / 科学一致性 / A-C 接收与延期

### 7.1 四组合消费（不重跑、不拼接）

本记录**消费** todo 75 的
`docs/reference/current/ACP_Services_Four_Combination_Acceptance.md`（commit `3d2af348680da232aa4e8328483be41a33cc7c0a`），
组合执行与产出归 todo 75；本 todo 不重复执行四组合。

| 组合 | t75 状态 | 本记录如何引用 |
|---|---|---|
| F0＋S0 | PASS_IN_REPO（S0 表面；浏览器 NOT_VERIFIED） | 基线读取流程未回归的输入；**不证明** S1 |
| F1＋S0 | PASS_IN_REPO（前端与 S0 表面；浏览器 NOT_VERIFIED） | 前端兼容重构的输入 |
| F0＋S1 | PASS_IN_REPO（浏览器 NOT_VERIFIED；科学等价 NOT_VERIFIED） | 旧客户端兼容的输入 |
| F1＋S1 | PASS_IN_REPO（浏览器与科学等价 NOT_VERIFIED） | 集成行为的输入 |

**逐项预期能力声明（X3）**：F0/S0 基线通过**不代表**支持新 prepare 或账户 UI；Lab 组合等 todo 73 身份交付；
首批本地组合不被未选 Lab 能力阻塞；A04 未启用 → 交付的是**前端兼容重构**。

### 7.2 科学一致性（固定输入对照，禁前端模拟）

- 判定位置（唯一权威）：C 路 `tests/baseline/cccp-evidence/c10-final-evidence-index.md`（todo 49：NMR×4 / PES×1 / simple×2 / CLI×1 = PASS，失败 0；真实 QC/LSF/Web/MCP 明确 NOT_VERIFIED）+ `tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md`。
- **本候选状态 = NOT_VERIFIED（channel C10）**：C10 判定记录未在 `f5a9c9b` 交付；HN-02 状态 B（跨路端到端）仍 `pending`（provider `06ce0452a714ff1e389e7ed1aedc022e8640cef9`）。
- **禁前端模拟**：本记录**不使用**任何 DOM/浏览器截图/前端断言作为科学等价证据；前端证据只用于前端任务面（§5.1 浏览器行，且为 NOT_VERIFIED）。
- **不拼接**：C 的 `c10-compare-*` 是其候选上的记录，不拼作 `f5a9c9b` 的通过证据。

### 7.3 A/C 接收任务与延期范围（B 交付物）

| 路 | 接收任务 | B 交付物 | 延期范围 | 状态 |
|---|---|---|---|---|
| A | **A05** | 同一集成候选上的浏览器场景 + 供 B 执行的黑盒用例 | 仅浏览器真机；B **不取得 A 源码写权**；A05 现有证据绑定其自身提交，不得借为 `f5a9c9b` 的通过记录 | **REGISTERED**（本记录浏览器门 NOT_VERIFIED） |
| C | **C10** | 同一集成候选上的科学等价判定（固定输入 + 黄金容差 + 恢复 fixtures） | 科学等价与真实软件（ORCA/xTB/CREST/LSF）执行；夹具/纯输入生成不得冒充真实运行 | **REGISTERED**（科学等价 NOT_VERIFIED；HN-02 状态 B pending） |

### 7.4 未接入项延期清单（且旧功能保持可用）

| 项 | 状态 | 旧功能可用性 |
|---|---|---|
| A04 刷新协议（cursor/重置/停轮询） | DEFERRED | 保留现有 per-task SSE + 轮询；前端 `eventCursor:false` 不伪造游标 |
| Lab 身份 HTTP/UI（todo 73） | NOT_VERIFIED | 组件已交付未接线；旧 local-single 路径不变 |
| 准入/配额派发前置 | NOT_VERIFIED | 旧 `JobManager` 派发路径保留 |
| 归档导出/导入 CLI/API 入口 | NOT_VERIFIED | 既有归档读取路径不变 |
| MCP 默认入口绑定 | NOT_VERIFIED | 不影响 `acp run` / `acp task` |
| 前端 cache-only 消费 | NOT_VERIFIED | 旧 GET 回退通道保留（L2-03） |
| 全量 15/15 envelope 覆盖 | NOT_VERIFIED | 7 项 legacy 走旧路径，不声称独立 |
| B11 价格/账本 | DEFERRED（价格政策门） | usage-only/quota-only 可交付；不宣称收费可用 |

### 7.5 F-wave 复核：IS-3 / IS-6 / IS-11 的如实状态（todo 82，2026-10-19）

本轮只做**口径收口**，不改变已执行的模块/入口判定；以下三条是对 F4 ideal-state fidelity
（`.omo/evidence/acp-parallel-plan-2-services/f4-ideal-state-fidelity.md`）发现（B3/B4/B7）的
显式登记。**模块已交付 ≠ 生产接线；本记录不把任何 DEFERRED/OPEN 项写成已关闭。**

T36 为下列 deferred capability 补齐 owner / blocker / entry_condition / next_gate：
account/permission/quota 生产接线、MCP 默认入口、项目包 CLI/API、metering 生产接线、
持久事件 A04、前端 prepare/availability 消费。逐项登记与当前状态（保持
`NOT_VERIFIED`/`DEFERRED`）见 `tests/baseline/services-evidence/handoff_registry.md`
「Deferred capability handoffs (T36)」；本记录 `deferrals` 行同步携带这四个字段。

| IS | 本轮口径 | 依据 / 延期范围 |
|---|---|---|
| **IS-3**（唯一实现根） | **PARTIAL — 注册性延期（registered deferral）**。schema/store、migrations、CLI flag 参数转换、plan/step requirements 已是 Services 唯一实现 + identity 转发；控制/recovery、`PlanExecutionPort`、Data writer/cache、`MethodConfigPort` catalog 归一化仍为 `src/acp`-owned。**不再以「端口收敛」声称唯一实现根已完成。** | `tests/baseline/services-evidence/injection_registry.md`「DEFERRED 注入登记表」逐项登记保留原因、迁移任务、移除任务号或 T78 过渡 controller 边界、验收测试与移除条件；`src/acp_services/compatibility/` 仍不存在（不虚报）。移除条件满足前各行保持 DEFERRED，不得标 CLOSED。 |
| **IS-6**（envelope 覆盖） | **`8/15 envelope + 7 legacy, T34b gate OPEN`**（原文口径，非 15/15）。8 项 enabled：singlepoint / optimize / frequency / scan / irc / casscf / xtb_optimize / OrcaGradient；7 项 legacy：tsmode / nmr / Confsearch / PESsearch / BatchOptimize / XtbPathSearch / XtbConstrainedOpt。 | `tests/baseline/services-evidence/workflow_enablement_matrix.md`（enabled 8/15、legacy 7/15、full-coverage gate OPEN）；`src/acp_services/runtime/workflow_enablement.py::coverage_report()` 为准。7 项闭包由 C01 T20—T30 承担，保持旧路径且不声称独立；T34b 不因本记录关闭。 |
| **IS-11**（台账逐项关闭） | **PARTIAL — 9 行 `[OPEN]` 保留**：5 行 cache-only 旧 GET 兼容面（catalog / manifest / frame_candidates / frequency_modes / geometry）与 4 行生产消费缺口（AdmissionController / Admin / Usage / Quota）。其中 **L2-11**（前端 cache-only 零调用点）、**L2-12**（准入/配额/身份生命周期派发+UI 零消费者）、**L2-15**（计量解耦生产路径零消费者）仍 `NOT_VERIFIED`。 | `tests/baseline/services-evidence/migration_ledger.md` 计数 `[CLOSED] 39 · [RETAINED] 5 · [OPEN] 9 · [DEFERRED] 0`；本记录 §4 L2-11/L2-12/L2-13/L2-14/L2-15/L2-16/L2-17。**延期范围**：生产派发/UI、前端 cache-only 消费、真实数据/计量接线。`todo 81`（F-wave remediation B）负责生产 Data 写入口与准备快照默认；在其证据落地前上述行**保持 OPEN**，不以本轮的文档收口替代实现验证。发布结果核对（todo 76 + F5）仍延后。 |

补充边界：

- **F-3 已修复**（`tests/test_services_worker_contract.py::test_runtime_package_has_no_acp_or_db_access`）：
  `3922f07`（todo 80，F-wave remediation A）把守卫收窄到 worker 边界，负例注入保留；
  `python3.11 -m pytest tests/test_services_worker_contract.py -q` → **49 passed，exit 0**。
  §6 原 FAIL 文本作为**被取代审计注记**保留，未删除历史；本记录不据 F-wave 文档任务改写其他 FAIL/NOT_VERIFIED 行。
- 本 §7.5 是**追加复核**，不删除 §3—§6 的原始 PASS/FAIL/NOT_VERIFIED 与 F-1/F-2/F-3 证据链。

---

## 8. 结论（三分层 + 三门状态）

1. **L1 模块验证**：`tests/test_services_*.py` 59 文件 → **全绿**；共享护栏 127 passed；
   grep gates 0 blocking；F4 35 passed。L1-03 的 F-3 已由 `3922f07` 修复（负例注入保留），
   原始 FAIL 作为被取代审计注记保留在 §6，未放宽/未删除。
2. **L2 默认入口接入**：后端 10 项入口全部 PASS（CLI `acp task`、`/availability`、`/prepared/{resource}`、
   manager 装配、发布端口、执行链、逐工作流 8/15、控制用例、`acp init` Lab、lineage 路由）；
   前端消费与 5 项「已交付未接线」组件 **NOT_VERIFIED**，旧入口全部保留。
3. **L3 真实功能验收**：本地闭环/恢复/备份为 `in_repo` PASS（科学后端为夹具，**不声明真实科学**）；
   浏览器（A05）、真实科学软件（C10）、LSF（C10/B）、生产（B）、科学等价（C10）**逐项 NOT_VERIFIED 并附原因**。
4. **本记录不关闭** A05 退出门、C10 科学等价、todo 76 发布；这些由各自任务与 F1–F5 波关闭。

---

## 9. 机器可读记录（由 `tests/test_services_closure_record.py` 解析守护）

<!-- SERVICES_CLOSURE_RECORD_BEGIN -->
```json
{
  "schema": "acp.services.layered_closure_record",
  "schema_version": 1,
  "plan_todo": 69,
  "generated_at": "2026-10-19T08:30:00Z",
  "binding": {
    "commit": "f5a9c9b85392e1ae018a1752db4e036e1a95646d",
    "branch": "feat/frontend-plan1-closure",
    "previous_head": "99b5ee2c5bb9c0249b5ea8cf6d892f67ddc5cc95",
    "interpreter": "python3.11 (3.11.13)",
    "node": "v20.19.0",
    "config": "isolated ACP_RUN_ROOT per suite (mktemp); default cccp.config._get_default_config(); no service restart; no config/DB mutation",
    "contract_versions": {
      "first_slice_contract_version": 1,
      "first_slice_contract_doc": "docs/reference/next-change/ACP_Services_First_Slice_Contracts.md",
      "first_slice_contract_doc_sha256_16": "e4e7dc4a0f020e38",
      "a04_events_contract_doc": "docs/reference/next-change/ACP_Services_Events_A04_Contract.md",
      "a04_events_contract_sha256_16": "140148c325dd7f42",
      "a04_status": "DEFERRED"
    },
    "verified_digests": {
      "first_slice_contract_doc": {"kind": "file", "path": "docs/reference/next-change/ACP_Services_First_Slice_Contracts.md", "sha256_16": "e4e7dc4a0f020e38"},
      "a04_events_contract_doc": {"kind": "file", "path": "docs/reference/next-change/ACP_Services_Events_A04_Contract.md", "sha256_16": "140148c325dd7f42"},
      "contract_samples": {"kind": "dir", "path": "tests/baseline/services-evidence/contract_samples", "files": 31, "sha256_16": "367815d7cb7291bc"},
      "frames_mapping": {"kind": "dir", "path": "tests/fixtures/science_products/frames_mapping", "files": 12, "sha256_16": "79d229e24101a430"}
    },
    "recorded_digests": {
      "four_combination_record": {"path": "docs/reference/current/ACP_Services_Four_Combination_Acceptance.md", "sha256_16": "8b3e6bb407a0c51b", "commit": "3d2af348680da232aa4e8328483be41a33cc7c0a"},
      "cache_only_switch_matrix": {"path": "tests/baseline/services-evidence/cache_only_switch_matrix.md", "sha256_16": "6f97e465929bb7c1"}
    }
  },
  "consumes": {
    "path": "docs/reference/current/ACP_Services_Four_Combination_Acceptance.md",
    "commit": "3d2af348680da232aa4e8328483be41a33cc7c0a",
    "sha256_16": "8b3e6bb407a0c51b",
    "combination_execution_owner": "plan todo 75",
    "re_executed_here": false,
    "combinations": ["F0+S0", "F1+S0", "F0+S1", "F1+S1"],
    "combination_status": {
      "F0+S0": "PASS_IN_REPO",
      "F1+S0": "PASS_IN_REPO",
      "F0+S1": "PASS_IN_REPO",
      "F1+S1": "PASS_IN_REPO"
    }
  },
  "layers": {
    "module_verification": {
      "id": "L1",
      "rows": [
        {"id": "L1-01", "item": "Data port / read-only root / persistent prepare / transfer verify", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_data_port.py", "tests/test_services_read_root.py", "tests/test_services_prepare_operations.py", "tests/test_services_transfer_verify.py"]},
        {"id": "L1-02", "item": "persistent events / authz stream / ordering / freshness", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_events.py", "tests/test_services_events_authz.py", "tests/test_services_event_ordering.py", "tests/test_services_freshness.py"]},
        {"id": "L1-03", "item": "envelope / worker contract / chain switch / workflow enablement", "status": "PASS", "claim": "in_repo", "note": "F-3 resolved at 3922f07: worker-boundary guard scoped; negative injections retained; pytest tests/test_services_worker_contract.py -q -> 49 passed, exit 0; original FAIL retained as superseded audit note in section 6", "evidence": ["tests/test_services_worker_contract.py", "tests/test_services_envelope.py", "tests/test_services_chain_switch.py", "tests/test_services_workflow_enablement.py"]},
        {"id": "L1-04", "item": "store/schema migration equivalence", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_migration_equivalence.py", "tests/baseline/services-evidence/migration_ledger.md"]},
        {"id": "L1-05", "item": "execution isolation capability matrix", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_isolation.py", "tests/baseline/services-evidence/isolation_matrix.md"]},
        {"id": "L1-06", "item": "access boundary / identity / api keys / admin", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_api_keys.py", "tests/test_services_local_boundary.py", "tests/test_services_identity_model.py", "tests/test_services_identity_mapping.py", "tests/test_services_identity_lifecycle.py", "tests/test_services_entry_authz.py", "tests/test_services_admin.py"]},
        {"id": "L1-07", "item": "admission / quota / usage / metering policy", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_admission.py", "tests/test_services_quota.py", "tests/test_services_usage.py", "tests/test_services_metering_policy.py", "tests/test_services_metering_decoupling.py"]},
        {"id": "L1-08", "item": "archive export / import", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_archive_export.py", "tests/test_services_archive_import.py"]},
        {"id": "L1-09", "item": "MCP / machine output / structured logging", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_mcp.py", "tests/test_services_machine_output.py", "tests/test_services_logging.py"]},
        {"id": "L1-10", "item": "init unified / config apply / lab doctor / standalone / provisioning", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_init_unified.py", "tests/test_services_config_apply.py", "tests/test_services_config_validation.py", "tests/test_services_action_gating.py", "tests/test_services_lab_init.py", "tests/test_services_standalone_recipe.py", "tests/test_services_provisioning.py"]},
        {"id": "L1-11", "item": "fault-injection matrix", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_fault_matrix.py", "tests/baseline/services-evidence/fault_matrix_report.md"]},
        {"id": "L1-12", "item": "consistent backup/restore drill", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_backup_restore.py", "tests/baseline/services-evidence/backup_restore_drill.md"]},
        {"id": "L1-13", "item": "CAS / submission receipt / recovery semantics / runtime use cases", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_cas_fidelity.py", "tests/test_services_submission_fidelity.py", "tests/test_services_recovery_semantics.py", "tests/test_services_runtime_usecases.py"]},
        {"id": "L1-14", "item": "helper merge / injection contracts / worker bootstrap / storage binding / projections / query budget / publication port", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_helper_merge.py", "tests/test_services_injection_contracts.py", "tests/test_services_worker_bootstrap.py", "tests/test_services_storage_binding.py", "tests/test_services_task_projection.py", "tests/test_services_query_budget.py", "tests/test_services_publication_data_port.py"]},
        {"id": "L1-15", "item": "first-slice contracts / cache-only switch / four-combination checker", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_first_slice_contracts.py", "tests/test_services_cache_only_switch.py", "tests/test_services_four_combination_records.py", "tests/baseline/services-evidence/cache_only_switch_matrix.md"]},
        {"id": "L1-16", "item": "shared guard suites (architecture/isolation/frames/markers/publication/integrity/recovery)", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_architecture_invariants.py", "tests/test_acp_services_isolation.py", "tests/test_cccp_isolation.py", "tests/test_acp_frames.py", "tests/test_scheduler_markers_audit.py", "tests/test_result_publication.py", "tests/test_execution_integrity_e2e.py", "tests/test_checkpoint_resume_semantics.py", "tests/test_recovery_fixtures_smoke.py", "tests/test_recovery_cross_version.py"]},
        {"id": "L1-17", "item": "grep gates / F4 shared gate / packaging", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_f4_scope_audit.py", "tests/test_packaging_assets.py", "tests/test_services_packaging_roles.py", "tests/baseline/services-evidence/f4_scope_amendment_services.md"]},
        {"id": "L1-18", "item": "per-slice verification discipline / plan consistency", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_plan_consistency.py", "tests/baseline/services-evidence/verification_discipline_report.md"]}
      ]
    },
    "default_entry_integration": {
      "id": "L2",
      "rows": [
        {"id": "L2-01", "item": "acp task local task-management verbs", "entry": "CLI acp task", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/cli.py", "tests/test_services_cli_full_loop.py"]},
        {"id": "L2-02", "item": "read-only availability and prepared endpoints", "entry": "HTTP /api/v1", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/api/v1_routes.py", "tests/test_services_cache_only_switch.py", "tests/test_services_entry_authz.py"]},
        {"id": "L2-03", "item": "legacy GET read surfaces retained for F0", "entry": "HTTP /api/v1", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/api/v1_routes.py", "tests/baseline/services-evidence/migration_ledger.md"]},
        {"id": "L2-04", "item": "persistent prepare executor and outbox delivery wired in JobManager", "entry": "JobManager assembly", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/scheduler/manager.py", "tests/test_services_prepare_operations.py", "tests/test_services_event_ordering.py"]},
        {"id": "L2-05", "item": "publication port wired into executor and remote runner", "entry": "executor + remote runner", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/calculations/executor.py", "src/acp/scheduler/remote/runner.py", "tests/test_services_publication_data_port.py", "tests/test_result_publication.py"]},
        {"id": "L2-06", "item": "single execution-entry dispatcher wired locally and remotely", "entry": "local runner + script_gen", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/scheduler/runner.py", "src/acp/scheduler/remote/runner.py", "src/acp/scheduler/remote/script_gen.py", "tests/test_services_chain_switch.py"]},
        {"id": "L2-07", "item": "per-workflow enablement (8/15 first batch, 7 legacy)", "entry": "execution_chain capability table", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_workflow_enablement.py", "tests/baseline/services-evidence/workflow_enablement_matrix.md"]},
        {"id": "L2-08", "item": "runtime control use cases shared by HTTP and CLI", "entry": "HTTP + CLI", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/api/v1_routes.py", "src/acp/cli.py", "tests/test_services_runtime_usecases.py"]},
        {"id": "L2-09", "item": "acp init Lab doctor branch", "entry": "CLI acp init", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/init_wizard/flows.py", "tests/test_services_lab_init.py"]},
        {"id": "L2-10", "item": "research evidence projection route", "entry": "HTTP /api/v2", "status": "PASS", "claim": "in_repo", "evidence": ["src/acp/api/v2_routes.py", "tests/test_services_evidence_projection.py"]},
        {"id": "L2-11", "item": "frontend consumption of cache-only read surface", "entry": "frontend call sites", "status": "NOT_VERIFIED", "claim": "none", "channel": "A05", "reason": "backend endpoints delivered but frontend declares prepareAvailability=false and has zero call sites for /availability or /prepared/; field delivered is not frontend integrated", "evidence": ["frontend/js/workbench/contracts/task_view_contract.js"]},
        {"id": "L2-12", "item": "admission/quota/identity-lifecycle wired into dispatch and UI", "entry": "runtime submit / HTTP / UI", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "components delivered (f00625b / 9e0185b / 014234c) but zero consumers in src/acp; not wired to HTTP/UI or submit pre-check", "evidence": ["src/acp_services/runtime/admission.py", "src/acp_services/runtime/quota.py", "src/acp_services/workspace/identity_lifecycle.py"]},
        {"id": "L2-13", "item": "archive export/import bound to CLI or HTTP", "entry": "CLI / HTTP", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "Data-side builders exist but no src/acp consumer binds them to an entry point", "evidence": ["src/acp/services_data_bootstrap.py"]},
        {"id": "L2-14", "item": "MCP stdio entry bound to a default entry", "entry": "acp default entry", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "protocol implementation and 39 tests delivered; no src/acp consumer of entrypoints.mcp", "evidence": ["src/acp_services/entrypoints/mcp.py", "tests/test_services_mcp.py"]},
        {"id": "L2-15", "item": "metering decoupling wired into production path", "entry": "runtime completion/read path", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "module and 26 tests delivered; zero src/acp consumers", "evidence": ["src/acp_services/runtime/metering_decoupling.py", "tests/test_services_metering_decoupling.py"]},
        {"id": "L2-16", "item": "A04 refresh protocol consumption", "entry": "frontend event consumption", "status": "NOT_VERIFIED", "claim": "none", "channel": "A04-lane", "reason": "A04 is DEFERRED / not integrated; delivered artifact is the frontend-compatibility refactor", "evidence": ["docs/reference/next-change/ACP_Services_Events_A04_Contract.md"]},
        {"id": "L2-17", "item": "full 15/15 envelope coverage", "entry": "execution_chain capability table", "status": "NOT_VERIFIED", "claim": "none", "channel": "C10", "reason": "7 workflows remain legacy pending C01 closure T20-T30; full-coverage gate OPEN", "evidence": ["tests/baseline/services-evidence/workflow_enablement_matrix.md"]}
      ]
    },
    "real_function_acceptance": {
      "id": "L3",
      "rows": [
        {"id": "L3-01", "item": "local closed loop through real acp processes", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_cli_full_loop.py", "tests/test_services_cli_offline_loop.py", ".omo/evidence/acp-parallel-plan-2-services/task-74-acp-parallel-plan-2-services.md"], "note": "science backend is tests.conftest.FakeBackend; no real-science claim"},
        {"id": "L3-02", "item": "offline loop with network and paramiko refused", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_cli_offline_loop.py"]},
        {"id": "L3-03", "item": "cross-version recovery with real fixtures", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_recovery_fixtures_smoke.py", "tests/test_recovery_cross_version.py", "tests/test_checkpoint_resume_semantics.py"]},
        {"id": "L3-04", "item": "consistent backup/restore with real DB/artifacts/keys", "status": "PASS", "claim": "in_repo", "evidence": ["tests/test_services_backup_restore.py", "tests/baseline/services-evidence/backup_restore_drill.md"]},
        {"id": "L3-05", "item": "real ORCA/xTB/CREST/CENSO execution", "status": "NOT_VERIFIED", "claim": "none", "channel": "C10", "reason": "xtb/crest/censo/isostat/molclus absent from PATH; ORCA binary present but not executed through ACP on this candidate; no compute budget", "evidence": ["tests/baseline/cccp-evidence/c10-final-evidence-index.md"]},
        {"id": "L3-06", "item": "LSF submit/monitor/stop-scope on a live cluster", "status": "NOT_VERIFIED", "claim": "none", "channel": "C10", "reason": "openlava binaries present but lsb_init fails: Unable to open file lsf.conf; no cluster and no node credentials", "evidence": ["tests/baseline/services-evidence/isolation_matrix.md"]},
        {"id": "L3-07", "item": "real browser scenarios on this candidate", "status": "NOT_VERIFIED", "claim": "none", "channel": "A05", "reason": "A05 browser matrix is bound to its own commit; no candidate-level black-box suite delivered; B has no A source write access", "evidence": [".omo/evidence/acp-parallel-plan-1-frontend/task-31-acp-parallel-plan-1-frontend.md", "docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md"]},
        {"id": "L3-08", "item": "scientific equivalence (same input, old vs new output)", "status": "NOT_VERIFIED", "claim": "none", "channel": "C10", "reason": "C10 judgement not delivered on this candidate; HN-02 cross-lane state B pending; frontend simulation is not accepted as proof", "evidence": ["tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md", "tests/baseline/cccp-evidence/c10-final-evidence-index.md"]},
        {"id": "L3-09", "item": "Lab account/session UI", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "identity components delivered (014234c) but not wired into HTTP/UI; OIDC_SUPPORTED is False", "evidence": ["src/acp_services/workspace/identity_lifecycle.py", "docs/reference/next-change/ACP_Services_Identity_Support_Scope.md"]},
        {"id": "L3-10", "item": "production release / static cache / old-tab window", "status": "NOT_VERIFIED", "claim": "none", "channel": "B", "reason": "no release performed; todo 76 is gated on F1-F4 approval; no service restart", "evidence": ["tests/baseline/services-evidence/packaging_rollout_receipts.md"]}
      ]
    }
  },
  "environment_gates": {
    "browser": {"channel": "A05", "status": "NOT_VERIFIED", "reason": "A05 browser matrix bound to its own commit (899c7c0 / 29efba8); no candidate-level black-box suite delivered; B never gains A source write access", "evidence": [".omo/evidence/acp-parallel-plan-1-frontend/task-31-acp-parallel-plan-1-frontend.md"]},
    "scientific_software": {"channel": "C10", "status": "NOT_VERIFIED", "reason": "real QC binaries unavailable/not executed on this candidate; C10 judgement absent", "evidence": ["tests/baseline/cccp-evidence/c10-final-evidence-index.md"]},
    "lsf": {"channel": "C10", "status": "NOT_VERIFIED", "reason": "no lsf.conf and no node credentials; live cluster enforcement not exercised", "evidence": ["tests/baseline/services-evidence/isolation_matrix.md"]},
    "production": {"channel": "B", "status": "NOT_VERIFIED", "reason": "release is todo 76, gated on F1-F4 approval; nothing was published or restarted", "evidence": ["tests/baseline/services-evidence/packaging_rollout_receipts.md"]}
  },
  "scientific_equivalence": {
    "status": "NOT_VERIFIED",
    "channel": "C10",
    "method": "fixed-input old-vs-new comparison using C golden evidence; frontend/DOM simulation explicitly not accepted",
    "reference": "tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md",
    "reference_commit": "06ce0452a714ff1e389e7ed1aedc022e8640cef9",
    "reference_state_b": "pending",
    "supporting_index": "tests/baseline/cccp-evidence/c10-final-evidence-index.md",
    "reason": "C10 judgement record not delivered on this candidate; HN-02 cross-lane state B pending; real science not executed here",
    "frontend_simulation_used": false
  },
  "lane_receipts": [
    {"lane": "A", "task": "A05", "deliverable": "browser scenarios on the same integrated candidate plus black-box tests for B to execute", "deferral_scope": "browser-only; B executes black-box tests and never gains A source write access; existing A05 evidence stays bound to its own commit", "status": "REGISTERED"},
    {"lane": "C", "task": "C10", "deliverable": "scientific equivalence judgement on the same integrated candidate with fixed input and golden evidence", "deferral_scope": "scientific equivalence and real software execution; fixture-only or frontend simulation is not accepted", "status": "REGISTERED"}
  ],
  "deferrals": [
    {"item": "A04 refresh protocol", "status": "DEFERRED", "old_functionality_kept": "per-task SSE and polling retained; no fabricated cursors", "owner": "A04-lane (A frontend + B Services events)", "blocker": "A04 refresh protocol is DEFERRED; frontend eventCursor:false; no real persistent event protocol/cursor", "entry_condition": "real service protocol and persistent facts; deliver Client cursor/reset/reconnect and refresh switching independently", "next_gate": "L2-16 real service protocol cursor/reset/reconnect; old service and terminal refresh preserved"},
    {"item": "Lab identity HTTP/UI (todo 73)", "status": "NOT_VERIFIED", "old_functionality_kept": "local-single path unchanged", "owner": "B - Runtime/Workspace (L3-09)", "blocker": "identity components delivered (014234c) but not wired into HTTP/UI; OIDC_SUPPORTED=False", "entry_condition": "after local-single, wire identity into HTTP/UI per deployment profile", "next_gate": "L3-09 Lab account/session UI on real HTTP/UI; cross-user/revocation checks"},
    {"item": "admission/quota pre-dispatch wiring", "status": "NOT_VERIFIED", "old_functionality_kept": "legacy JobManager dispatch retained", "owner": "B - Runtime/Workspace (L2-12)", "blocker": "AdmissionController/Admin/Usage/Quota have zero consumers in src/acp; not wired to submit/dispatch", "entry_condition": "after local-single, wire admission/quota into submit and dispatch preconditions", "next_gate": "L2-12 cross-user deny/revoke/duplicate-request/quota-race/isolation"},
    {"item": "archive export/import CLI-API binding", "status": "NOT_VERIFIED", "old_functionality_kept": "existing archive read path unchanged", "owner": "B - Data archive/import (L2-13)", "blocker": "Data-side archive/import builders exist but no CLI/HTTP consumer; command/permission/integrity semantics unbound", "entry_condition": "after M1 stable, deliver as M2 with explicit command/permission/receipt/integrity/idempotent import", "next_gate": "L2-13 local export -> new-environment import -> read/evidence complete; import does not auto-run tasks"},
    {"item": "MCP default-entry binding", "status": "NOT_VERIFIED", "old_functionality_kept": "acp run and acp task unaffected", "owner": "B - Services entrypoints (L2-14)", "blocker": "protocol implementation and tests delivered, but no src/acp default entry consumer and no real-client interop", "entry_condition": "Runtime/Data/Workspace use cases stable and an explicit default entry defined", "next_gate": "L2-14 at least one real client; identity/failure/cancel/idempotency parity with GUI/CLI"},
    {"item": "frontend cache-only consumption", "status": "NOT_VERIFIED", "old_functionality_kept": "legacy GET fallback retained", "owner": "A05 frontend (L2-11)", "blocker": "backend availability/prepared endpoints delivered but prepareAvailability=false and zero call sites", "entry_condition": "after D04-D06/D11, wire explicit prepare and read-only queries per real capability/deployment config; keep S0 adapter", "next_gate": "L2-11 default page queued/running/failed/ready, restart retry, terminal background prefetch; old service receives no new protocol; no GET implicit prepare"},
    {"item": "full 15/15 envelope coverage", "status": "NOT_VERIFIED", "old_functionality_kept": "7 legacy workflows run on the old path without independence claims", "owner": "B/C - closure and activation (L2-17)", "blocker": "7 workflows remain legacy pending C01 closure; default set is only singlepoint", "entry_condition": "per-workflow request builder + publication/recovery evidence; add to default set one at a time", "next_gate": "15/15 gate after T29/T30/T31/T39 per-workflow evidence"},
    {"item": "B11 price/ledger", "status": "DEFERRED", "old_functionality_kept": "usage-only and quota-only deliverable; no billing claims", "owner": "B - Runtime metering/quota (B11)", "blocker": "price policy gate; usage-only/quota-only deliverable, no billing semantics", "entry_condition": "independent price/ledger policy approval", "next_gate": "B11 price/ledger gate; no billing claim before closure"},
    {"item": "metering production wiring", "status": "NOT_VERIFIED", "old_functionality_kept": "existing runtime completion/read path unchanged; no usage fact required for job completion", "owner": "B - Runtime metering (L2-15)", "blocker": "metering_decoupling module and 26 tests delivered but zero consumers on Runtime completion/recovery/read paths", "entry_condition": "wire the delivered decoupling module into real Runtime completion/recovery/read paths and record usage facts separately", "next_gate": "L2-15 duplicate/out-of-order receipts do not double-meter; metering failure never changes science completion or result readability; pricing is a separate gate"}
  ],
  "findings": [
    {"id": "F-3", "status": "RESOLVED", "owner": "todo 80 F-wave remediation A (worker-boundary guard scope)", "summary": "runtime package sqlite3/DB guard is green at 3922f07 (guard scoped to the worker boundary, negative injections retained); the original FAIL is retained as a superseded audit note in section 6", "evidence": ["tests/test_services_worker_contract.py", ".omo/evidence/acp-parallel-plan-2-services/task-67-acp-parallel-plan-2-services.md"]},
    {"id": "F-1", "status": "RECORDED", "owner": "Data transfer", "summary": "ENOSPC surfaces as OSError instead of a typed rejection; safety holds (no partial publish, no tmp residue)", "evidence": ["tests/baseline/services-evidence/fault_matrix_report.md"]},
    {"id": "F-2", "status": "RECORDED", "owner": "store migrations", "summary": "migration ledger is forward-only; contracted DB must be recovered from backup, not by re-running migrations", "evidence": ["tests/baseline/services-evidence/fault_matrix_report.md"]}
  ],
  "evidence_index": [
    {"path": "docs/reference/current/ACP_Services_Four_Combination_Acceptance.md", "kind": "tracked"},
    {"path": "docs/reference/current/ACP_Frontend_Plan1_Acceptance_20261019.md", "kind": "tracked"},
    {"path": "docs/reference/next-change/ACP_Services_First_Slice_Contracts.md", "kind": "tracked"},
    {"path": "docs/reference/next-change/ACP_Services_Events_A04_Contract.md", "kind": "tracked"},
    {"path": "docs/reference/next-change/ACP_Services_Identity_Support_Scope.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/verification_discipline_report.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/fault_matrix_report.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/backup_restore_drill.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/f4_scope_amendment_services.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/migration_ledger.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/handoff_registry.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/packaging_rollout_receipts.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/workflow_enablement_matrix.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/isolation_matrix.md", "kind": "tracked"},
    {"path": "tests/baseline/services-evidence/cache_only_switch_matrix.md", "kind": "tracked"},
    {"path": "tests/baseline/cccp-evidence/c10-final-evidence-index.md", "kind": "tracked"},
    {"path": "tests/baseline/cccp-evidence/handoff_registry.md", "kind": "tracked"},
    {"path": "tests/baseline/cccp-evidence/handoffs/HN-01-b06-closure.md", "kind": "tracked"},
    {"path": "tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md", "kind": "tracked"},
    {"path": ".omo/evidence/acp-parallel-plan-1-frontend/task-31-acp-parallel-plan-1-frontend.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-66-acp-parallel-plan-2-services.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-67-acp-parallel-plan-2-services.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-68-acp-parallel-plan-2-services.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-74-acp-parallel-plan-2-services.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-75-acp-parallel-plan-2-services.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-3-cccp/c10-old-new-comparison.md", "kind": "local"},
    {"path": ".omo/evidence/acp-parallel-plan-2-services/task-69-acp-parallel-plan-2-services.md", "kind": "local"}
  ]
}
```
<!-- SERVICES_CLOSURE_RECORD_END -->
