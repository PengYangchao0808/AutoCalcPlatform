# Round-9：官方 TS 链路自足（工件打码可读、PES→Batch 可寻址、纯 MCP 发布）+ 15 条反馈

## 背景

外部 MCP Agent 从 PES 扫描走到 IRC 后提交 15 条反馈。真正的死路只有一条：**工件正文含路径就整篇拒绝**，
于是 PES 曲线、虚频、ZPE/G 全部读不到，TS 只能停在候选。本轮按
`reports/acp2_mcp_guidance_20261030/round9_plan.md` 分 6 批修复；全部为收紧密封或纯投影，不放宽科学/安全护栏。

## P0（阻断官方 TS 链）

**M1 工件正文打码而不是拒绝**：`acp_read_artifact` 不再因正文含路径/令牌整篇拒绝。正文经统一掩码后返回，并给
`body_masked: true`、`masked_digest`（返回片段的 sha256，含 max_bytes 截断后的片段）、`redaction="paths+secrets"`；
`digest` 始终是**存储体**摘要（`digest_scope=artifact`，恒存在），因此掩码文本不与 digest 比对。完整性顺序是
「读存储体并验真 digest → 整篇掩码 → max_bytes 切片」。只有非 UTF-8/二进制仍拒绝（`artifact_body_unverifiable`）。
旧的严格模式保留为 `body_policy="refuse"`。

**M2 PES→Batch 端点对齐**：PESsearch 现在发布 `RESULT/result_manifest.json`，并为每帧登记 structure 产品
`RESULT/structures/frame_NNN.xyz`（hard-link 自 `scan_frames/`，不复制字节）。metadata 带 `frame_index`、
`role="scan_frame"`、`selection_source="scan"`、`scan_coordinate`、`energy_hartree` 等；`pes_profile` 记
`frames_total/frames_selected/frames_published/frames_missing/truncated/max_published_frames=200`（超限保留两端 +
候选邻域 + 均匀抽样）。`from_artifact` 因此可指向上游 manifest。**原始未确认帧仍受「先发布再消费」约束**
（`loaders.py` 的 manual-only 门未动，不伪造 manual 来源）。

**M3 纯 MCP 发布路径**：local-single 下（operator 身份 + 权限域/部署层 local-single + `ACP_SELECTION_AUTOPUBLISH`
开启）可省略 `authorization_id`：Controller 铸一条 `origin="agent-self"` 的有界授权（policy
`acp2.selection.self-signed.v1`、TTL 1h、候选 ≤8、仅当前 attempt），走**既有** validate/consume 并写审计事件
`selection.self_signed`；响应带 `publication.authorization_origin`。多租户/API-key 或未开启仍要求预铸 id；
显式传入的未知 id 不会被自签替换。MCP `acp_publish_pes_review` 的 `required` 不再要求 `authorization_id`。

## P1

**M4 频率分析通道**：`services_analysis_bootstrap` 增加 `normal_modes_v1` 输入通道（按 prepared 内容探测，可用
`parameters.format` 覆盖），消费 manifest 登记的 `frequency_modes` 产品，`vibration.analyze` 不再因
「frequency products are empty」恒失败。能力视图不再伪造 `unavailable`：拿不到 Controller 投影时显式
`status="unknown"`（summary/full 同一对象），Controller summary 的紧凑 `{id: state}` 由 MCP 适配层转发。

**M5 Batch per-item 能量产物**：每个 item 发布 `RESULT/energy/<item>_<step>.json`（kind `energy_report`，
键集与 optimize 的 `acp_energy_product_v1` 完全一致）；`method` 为实际方法，无法判定时 `null` +
`method_unknown_reason`，绝不再写空字符串。

## P2（契约自洽）

- **M6** 描述符的 `prepare.arguments` 补全 `relative_path`/`media_type`，可**原样照抄**；省略 `relative_path` 时
  服务端按 `artifact_id` 从 manifest 派生（客户端路径不能覆盖 manifest；有可读 manifest 时显式路径必须与产品
  一致，否则 `manifest_mismatch`）。
- **M7** prepare 失败一律带 `params.field`/`params.rule`；「尚未就绪」是可重试的 `not_ready_yet`，与
  `invalid_parameter` 区分；未分类的执行器失败保留冻结码但**不再**冒充可重试。
- **M8** `contract_sections` 贯通 `acp_services` 适配层 → `ControllerClient`（不再出现
  `capability_not_wired`），契约块里 `next` 的指引现在真的可用。
- **M9** `acp_cancel` 接受可选 `reason`（≤200，仅审计：回执回显 `cancel_reason`，不转发）；非字符串/超长按
  `input_invalid` + field/rule 拒绝；不传时旧回包形状不变。
- **M10** operation 引用不再二次序列化：`_reference_from` 不把 Mapping `str()` 成 dict repr，生产端
  `_operation_reference` 直接透传 Controller 的 `operation_ref`。输入侧字符串引用仍保持不透明（round-8 K6 不变）。
- **M11** `limits.max_running` 改为单一投影 `{value, source, legacy_env, legacy_in_effect}`，与
  `capacity.concurrency.max_running` 同源；`status:"legacy_noop"` 的第二状态退役。
- **M12** `RESULT/energy/singlepoint_energy.json` 的 `method` 从 task-result 的 `provenance.method` 读取（此前
  顶层取不到 → 恒为空）。同时修正 singlepoint 的**隐式默认**：无显式方法时按
  `theory.single_point.method`（**wB97M-V**），不再误继承优化的 r2SCAN-3c；优化默认仍是 **r2SCAN-3c**
  （round-8 USER CORRECTION 保留）。证据生产遇到多个 energy_report 时会显式拒绝歧义。
- **P2 描述符分页**：`GET /api/v1/jobs/{id}?artifact_kind=structure` 在 50 条上限**之前**按 kind 过滤，
  200 帧的扫描可按需分页（`artifacts_truncated` 仍报告剩余）。

## P3

- **M13** live_status 增加 `observed_age_seconds`、`stale`（>90s 或状态文件 mtime 明显新于记录时刻）与
  `source_mtime`；写入端每次重写都推进 `observed_at`，`begin_batch_item` 立即落盘。**历史冻结点根因仍
  NOT_VERIFIED**（缺用户样本），本轮只交付可观测契约与防御性重读。
- **M14** 失败诊断不再只有 200 字摘要：`failure_details.diagnostic_message` 携带完整文本（≤4000，宿主路径降
  basename）；摘要被截断时以 `… full text in failure_details.diagnostic_message` 结尾，且不引入会误导救援分类
  器的 token。
- **M15** `acp_status.artifacts[].prepared` 真实翻转（读同一 prepare-operation 行，只读、零副作用），并带
  `prepared_state/generation/digest/at`；拿不到服务时 `prepared_unknown_reason`。

## 治理与验证

- F4 范围审核登记：`src/acp/results/energy_product.py`、`src/acp/workflows/pes_search.py` 为迁移驻点；
  Amendment V 覆盖 ORCA 失败面。契约 digest 链未变（`coverage_sha256 3e12a6ca…`）。
- 指南：`PACK_VERSION 1.6.0`；artifact_locating 1.5.0 / PESsearch 1.3.0 / find_transition_state 1.3.0，
  新增「打码返回」「帧分页」「自签发布」「diagnostic_message」「live 新鲜度」「normal_modes_v1」「limits 单一投影」。
- 本地全仓 `pytest -m "not slow and not contract"`：**13036 passed / 0 failed / 23 skipped / 116 deselected（46:39）**。
- 定向：round-9 新测试 177 passed；独立验收回归批 451 passed；旧严格工件面 27 passed；round-8 K6 单测复跑 1 passed。
- 护栏：`validate_acp2_contracts.py` VALID；`validate_acp2_gap_coverage.py` VALID；`check_docs_links.py` PASS（377 refs）；`check_grep_gates.py --suite architecture-remediation` 10/0；`verify_cccp_isolation.py` PASS（15/15）；F4 scope audit 38 passed。
- 独立只读验收：15 条 + 分页 + 护栏逐项 PASS；2 个 P2（未分类 prepare 误报可重试、显式路径不对账）已在提交前修复。

## NOT_VERIFIED（不得改写为 PASS）

- M13 历史 live_status 冻结根因（需 `_020`/`_022` 的 state.json 与同刻 status 回包）。
- M4b `normal_modes_v1` 的科学充分性（足不足以判定「恰一个虚频 + 方向」）待科学 owner 签收。
- M14 真实下游截断点（需完整 IRC 失败回包；测试为合成夹具）。
- 真实 ORCA/PES/QC/LSF 运行、真实 Controller 上的 prepare→read 与发布往返、真实 MCP SDK contract lane。
- `ACP_SELECTION_AUTOPUBLISH` 默认开启（既有值）：即默认态下 local-single 即可自签，PR 已显式声明；关闭该
  变量即回滚到「必须预铸 authorization_id」。
