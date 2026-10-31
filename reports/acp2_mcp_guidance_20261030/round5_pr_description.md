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

## 验收证据（占位，验收后回填）

- 定向验收套件（各 PR 的新测试 + 受影响面）
- `scripts/acceptance/validate_acp2_contracts.py` / `validate_acp2_gap_coverage.py` VALID
- 全仓非 slow 套件（CI 同款）结果
- 独立子 agent 验收报告（只读评审 + 对照计划验收条目）

## 已知 NOT_VERIFIED（诚实标注）

- **PR-04 具体投诉运行**（60 帧双向轨迹 failed）：需要用户提供原始 `.inp`/`.out`/轨迹/manifest 样本才能复现关闭；当前以 H2O2 fixture 证明逻辑（双向 found 但 iteration_limit → not_converged）。
- **真实 MCP SDK contract**（`tests/test_acp2_mcp_sdk_contract.py -m contract`）：取决于本机 SDK/DSH 环境，未跑则记 NOT_VERIFIED，不以 handler 单测替代。
- **远端写回全故障矩阵**：已覆盖 502 + publication 回执与幂等恢复；逐上传边界的完整矩阵未逐一构造。
- **PR-06 授权管理入口**：管理员创建走 Services 层 `SelectionAuthorizationService.create(is_admin=True)`；独立的管理 API 入口未新增（PR-07 写工具只做发布）。
- **PR-08 示例**：两候选拆两请求与幂等建议已写入指南文本；示例未作为可执行 fixture 纳入 CI。

## 回滚

每个 PR 一个提交，可单独 revert。关闭自动发布：`ACP_SELECTION_AUTOPUBLISH=0`（人工路径与已冻结 Batch 不受影响；PR-07 读工具继续可用）。回滚端点解析保留原始输出与 PR-02 防护，未知格式恢复为 unknown 而非默认收敛。
