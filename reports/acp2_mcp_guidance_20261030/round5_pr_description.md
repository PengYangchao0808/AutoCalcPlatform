# Round-5 MCP 易用性加固 — 统一 PR 描述

> 执行基线：`docs/reference/next-change/ACP_MCP_Round5_Execution_Plan_20261031.md`（astra 计划，9 个 PR）。
> 本分支把 9 个 PR 的可独立审查提交放在同一分支，最后统一开一个 PR；每个提交对应一个 PR 范围，可单独回滚。

## 背景

真实外部测试 Agent 在第 4 轮后报告了新的投诉集：PES/scan 坐标被静默覆盖、preflight 可行但 submit 缺字段、指南指向不存在的 PES 评审端点、BatchOptimize 一结构惊讶、推荐为空、IRC 60 帧 failed 且 `step` 被静默忽略、状态/prepare 摩擦。本轮按计划逐项关闭。

## 9 个 PR（每个一个提交）

| PR | 提交主题 | 核心改动 |
|---|---|---|
| PR-01 | 坐标单一规范化源 | 新 `pes/normalization.py`：PES coordinate/coordinates 完整定义比对、冲突 422 `conflicting_fields`、coordinates-only、单位归一；scan flags/validator/API 共用；完成点数同一来源 |
| PR-02 | IRC step 止损 + 首帧防护 | 契约/describe/CLI/runner/remote/typed/ORCA 接口全链拒绝显式 step；平台不再注入默认 step；`initial_hessian` 拒绝；新 `cccp/io/xyz_validation.py` 严格单帧，端点读取拒绝多帧冒充 |
| PR-03 | 共享只读校验与参数回执 | 新 `calculations/submission_validation.py`（PES/scan/irc/tsmode 只读分析）+ preflight requested/effective/executed 回执 + `api/parameter_effects.py` 登记全部契约字段（新键未登记 CI 失败）+ `input.scan_request` 消费比对 |
| PR-04 | IRC 逐方向证据 | `IrcDirectionResult` 增 endpoint_discovery/convergence/证据来源/frame_identity/last_observed_geometry；过滤前诊断进 metadata；收敛必须有真实证据（无证据 unknown）；整轮 converged 判定 |
| PR-05 | PES 发布事务 | 发布组 backup→structures→manifest(pending)→review→manifest(commit)；migration 028 持久意图 + lease fencing；进程锁；源复检；reader 拒绝 pending 混合版本；幂等重放 |
| PR-06 | 受限自动授权与 Batch 绑定 | `acp2.selection.v1` 契约 + 授权服务（管理员创建、逐动作校验、CAS 配额、重放免扣）；agent 发布带授权回执；loader 要求回执并把 consumed revision pin 到 item lineage；API 接入 + `ACP_SELECTION_AUTOPUBLISH` 开关；Batch spec 冻结 |
| PR-07 | MCP 查询/发布工具 | `acp_get_pes_review`（cache-only 状态）+ `acp_publish_pes_review`（授权发布）；端口装配才可见；Controller client/bootstrap/capabilities 贯通；selection_ref 供 Batch |
| PR-08 | Batch 基数/推荐/容量 | manifest 展开后单结构门（layout_limit + limit/received/hint）；空推荐 = disabled/manual_only_policy（含 PR-06 后说明）；有效容量 = `registry.local.max_jobs`，`max_running` 标 legacy_noop |
| PR-09 | 状态/prepare 体验 | `storage_locator`（相对标识、远端/缓存不冒充本地、unknown_reason）；live_status 带 observed_at/source/unknown_reason；prepare 本地快速路径（已就绪产物直接 ready，缓存 reader 判定） |

## 验收证据

- 独立子 agent 只读验收（对照计划逐条）：首轮 **FAIL**（3 个 P0 红灯 + P1/P2），修复后复跑：
  - `tests/test_irc.py` + `test_acp_guidance_pack.py` + `test_acp_round5_artifact_status.py` + `test_acp2_runtime_mcp_process.py` + `test_qc_interfaces_orca.py`：**122 passed / 1 skipped**
  - 真实 MCP SDK contract lane（`-m contract tests/test_acp2_mcp_sdk_contract.py`）：**8 passed**（Node/DSH live client）
  - 最终验收批次（33 文件：各 PR 新测试 + 受影响面）：**733 passed / 1 skipped**
  - `scripts/acceptance/validate_acp2_contracts.py` → VALID；`validate_acp2_gap_coverage.py` → VALID（39/32/26/8/3，5 gaps open，完整发布仍 NOT_VERIFIED）
  - 契约/参数守护：`validate_acp2_contracts.py` VALID；`test_acp_parameter_effects` 四工作流全登记
- 全仓非 slow 套件（CI 同款 `pytest -m "not slow and not contract"`）：本地运行中，CI 矩阵（3.10/3.11/3.12）为最终权威；任何失败在合并前修复。

## 独立验收发现与修复（提交 `349d3401` + 后续）

| 发现 | 级别 | 修复 |
|---|---|---|
| `workflows/irc.py` 读不存在的 `CalculationResult.converged/complete` → 独立 IRC 工作流报告崩溃（3 个 test_irc 失败） | P0 | 从 `direction_diagnostics` 推导整轮 converged/complete；`test_irc.py` 回归通过 |
| 真实 SDK live harness `EXPECTED_TOOLS` 仍 10 个 → `mcp-conformance` 门红 | P0 | harness 同步 12 个工具；SDK contract lane 8 passed |
| 指南 BatchOptimize 版本 pin 1.1.0 vs 1.2.0 → 默认 CI 矩阵红 | P0 | pin 更新为 1.2.0（保留 v1.1.0 要点断言） |
| 选择来源的 profile/geometry digest 仅来自客户端（PR-06 第 4 条） | P1 | 服务端从任务工件计算摘要（`_server_source_digests`）；客户端声明不一致 → 403 `stale_source`；授权/发布绑定服务端值；新增 stale 用例 |
| live_status 有 metrics 无 stage 时无 unknown reason（PR-09 第 3 条） | P1 | `stage_unknown` 稳定原因 + 用例 |
| `scan_request` carrier 测试被放宽为 `in (200, 422)` | P1 | 恢复两个确定性用例（等价 carrier 200；冲突 422 `conflicting_fields`，carrier 冲突改为 typed CoordinateError） |
| `step/initial_hessian` 守卫被无界替换复制到 8 个 ORCA 方法，且把 `optimize` 的 initial_hessian 转发变为不可达 | P1 | 仅 `irc()` 保留守卫；`optimize` 转发恢复（ORCA 套件 47 passed） |
| `loaders.py` 未导入 `Any`（F821）；agent 发布 body `actor` 被静默忽略；状态投影缺 `source.attempt`；工具总数固定断言 | P2 | 导入修复；body actor 显式 422 `server_identity_owned`；投影加 attempt；握手/能力测试改子集断言 |

## 验收后续（工具清单契约漂移、迁移基线与静态资产）

修复 P0/P1 后复跑全仓 `test_acp2_*` 发现的是「工具清单/契约副本漂移」而不是行为回归，均已按权威源同步：

- 前端 `experience_contract.js` 的 `ACP_TOOLS`、三个 acceptance receipt 脚本的工具元组、`runtime/mcp-launch.schema.json` 的 `tool_inputs`、`runtime/p2-research-operations.v1.json` 的 `p1_allowlist_unchanged` 与 `tool_sets.p1_advertised`、以及对应测试常量都补上 `acp_get_pes_review`/`acp_publish_pes_review`；validator 的 P1 期望改为 registry-derived 集合并保留移除即失败。
- migration acceptance fixture 的 target/legacy 改为 helper 的「已应用迁移条数」口径（含 migration 028）。
- `experience/static-assets.manifest.json` 与 `release-manifest.json` 的 static_assets 按产品 helper 重新生成（origin/main 上原本已 stale；本轮 frontend 契约更新使其必须刷新），文件数断言改为 build-derived；契约 digest 重锚，两个 validator VALID。
- 本地 `test_acp2_*` 非 slow 全量：除 `test_fixture_wheel_offline_toolchains[build]`（本机 venv 缺 `build` 模块的环境依赖）外全绿；该测试在 CI 的 `[dev]` 环境另验。
- CI 的 `mcp-conformance` 首轮红是已知 flaky `test_signal_shutdown_is_clean[2]`（子进程 300 s 超时，非本轮代码），structural 子集本地 36 passed；重跑/新提交后复验。

## CI 全矩阵回归修复（本地 acp2 子集看不到的部分）

CI 的 `pytest -m "not slow and not contract"` 全仓矩阵又暴露了 8 个真实回归，均已按行为修复而非放宽断言：

- **scan flags generator**：`runtime_contract_repros` 明确记录「generator 宽松、边界严格」是设计（plan todo 9），而 PR-01 曾把 generator 也严格化 → 恢复 `scan_method_flags` 的生成器语义（严格拒绝保留在 `validate_scan_submission` 边界），坐标一致性由边界保证。
- **F4 scope audit**：PR-02 的 `step/initial_hessian` 守卫曾被无界替换复制到 7 个非 IRC 方法（首次修复只重建了 block 未真正删除）；现在只剩 `irc()` 一处（`optimize` 的 initial_hessian 转发恢复），并为 `_read_endpoint_geometry` 的严格读取范围登记 Amendment Z。
- **CCCP 序列化误伤**：`IrcDirectionResult` 的新字段曾被误插到 `OptimizePayload`/`MdSamplingPayload`/`ScanFrame`（F821 类错误），已收敛回单一类；`irc.json` goldens 与 manifest hash 随之重生成。
- **scan edit**：投影展示显式点数（覆盖 stale level 镜像）、坐标与 flags 独立解析、partial edit 同步镜像而不是报冲突。
- **工具清单副本**：`test_services_mcp`、`test_v1_summary` 的工具集合/live-status 往返按新契约更新（无 selection 端口的 fake server 断言基础集合）。
- **文档**：移除未提交的 `ACP_Simple_Workflows_DevDoc.html` 链接（.txt 为权威），并记录 IRC `--step` 拒绝语义。

本地复核：scan/repro 177 passed、services_mcp/live-status 66 passed、docs 21 passed、F4 37 passed；全仓非 slow 套件与 CI 矩阵在本提交上重跑。

## 独立验收标注的覆盖缺口（诚实保留）

- PR-01 条目 4/5/7（prepare/预算/派发 spy、执行输入探针、历史/rerun）；PR-02 条目 9（旧回执读取）；PR-03 条目 2/3（不可变性、冷远端 preflight）；PR-04 条目 6/7（关联失败交科学验证、失败诊断找回）；PR-05 条目 4/6/9/10（4 个故障点只覆盖 2 个、远端上传边界、restore 事务、发布能力关闭）；PR-06 条目 5/9（approval 复用、旧 attempt/冻结历史恢复）；PR-07 条目 8（真实 SDK 全选择链，本次只同步工具集）/9（初猜状态）；PR-08 条目 1(部分)/2/4/8/9/10（单 item 多帧、manifest 单结构正例、两拆分示例 preflight、远端容量、TaskRef 示例、幂等服务端用例）；PR-09 条目 5/6/7/8/9（真实 reader 快速路径、负例矩阵、竞争、回退、QUEUED+ABSENT 语义）。
- 这些条目在计划验收表中为 NOT_VERIFIED，不以“已实现”表述；后续可用同一子 agent 复验。

## 已知 NOT_VERIFIED（诚实标注）

- **PR-04 具体投诉运行**（60 帧双向轨迹 failed）：需要用户提供原始 `.inp`/`.out`/轨迹/manifest 样本才能复现关闭；当前以 H2O2 fixture 证明逻辑（双向 found 但 iteration_limit → not_converged）。
- **真实 MCP SDK contract**（`tests/test_acp2_mcp_sdk_contract.py -m contract`）：取决于本机 SDK/DSH 环境，未跑则记 NOT_VERIFIED，不以 handler 单测替代。
- **远端写回全故障矩阵**：已覆盖 502 + publication 回执与幂等恢复；逐上传边界的完整矩阵未逐一构造。
- **PR-06 授权管理入口**：管理员创建走 Services 层 `SelectionAuthorizationService.create(is_admin=True)`；独立的管理 API 入口未新增（PR-07 写工具只做发布）。
- **PR-08 示例**：两候选拆两请求与幂等建议已写入指南文本；示例未作为可执行 fixture 纳入 CI。

## 回滚

每个 PR 一个提交，可单独 revert。关闭自动发布：`ACP_SELECTION_AUTOPUBLISH=0`（人工路径与已冻结 Batch 不受影响；PR-07 读工具继续可用）。回滚端点解析保留原始输出与 PR-02 防护，未知格式恢复为 unknown 而非默认收敛。
