# Round-6：外部 Agent MCP 反馈修复

## 背景

一个真实外部测试 agent 通过 MCP 使用平台后提交了 8 条反馈（选点授权形同虚设、IRC directions
枚举与校验两套、contract 示例字段被 preflight 拒绝、preflight 不查 Batch 基数、长任务只能数
文件、IRC 80 点却报 no endpoint、结果结构套娃、CLI/guide/capacity 小刺），并附了公道话。本 PR
按反馈逐条修复，全部为行为修复而非放宽护栏；每项改动可独立回滚。

## 8 条反馈与修复

### 1. authorization_id 在 local-single 下没有被校验（P0）

- 根因：MCP 发布工具不转发 `selector`（schema 也没有该字段）；REST 的
  `PesReviewRequest.selector` 默认 `manual`，只有 `selector=="agent"` 才走授权校验，于是必填的
  `authorization_id` 被 Pydantic 默认值吸收，发布按 manual 落盘。
- 修复：
  - MCP `acp_publish_pes_review` 在转发 dict 中**服务端写入** `selector="agent"`（客户端不可传，
    schema 不暴露）→ MCP 发布永远走授权路径。
  - REST：带 `authorization_id` 但 selector 非 agent → 422 `input_invalid` +
    `params.field=authorization_id`、`rule=inconsistent_selector`（不再静默忽略）。
  - **闭环缺口**（子 agent 报告的 P0）：`SelectionAuthorizationService.create` 原本没有任何生产
    入口，严格化后 MCP 发布会永远 403。新增管理员入口
    `POST /api/v1/selection-authorizations`（仅 local-single operator 身份；owner/id 服务端所有，
    agent 不能自授权）→ 管理员铸造有界授权，agent 用它自动选点发布。
  - 指南文本从“agent 自动发布未开放”改为“人工 review 或受限授权 Agent 自动发布”。
- 测试：`test_publish_tool_forwards_the_server_owned_agent_selector`、
  `test_api_manual_selector_with_authorization_is_refused`、
  `test_api_agent_publication_with_a_probe_authorization_is_refused`、
  `test_api_operator_mints_an_authorization_then_agent_publishes`。

### 2. directions 枚举与真实校验两套

- 新增 `acp/calculations/irc_directions.py::normalize_irc_directions` 作为唯一权威：接受
  `"both"`、`["both"]` 以及 forward/reverse 子集；manager、workflows/irc、attempt_freeze 统一调用。
- 测试：`tests/test_acp_irc_directions.py`（8 个形式）+ 既有 IRC 套件。

### 3. contract 示例里 reuse_previous_geometry 被 preflight 拒绝

- 删除 PESsearch 示例中的该字段（native scan 不支持逐点字段，PR-03 的拒绝保留，不放宽）；
  契约的 protocol 描述不再把 `reuse_previous_geometry` 列为通用可传字段，scan 指南明确它是
  pointwise scan 的逐点字段、native scan 会拒绝（不照旧示例抄）。
- 测试：`tests/test_acp_parameter_effects.py`（拒绝仍生效）+ guidance 测试。

### 4. preflight 不查 Batch 单结构基数

- preflight 在静态校验阶段调用与 submit 相同的 `_validate_batch_cardinality`（只读、零副作用）；
  helper 支持真实 wire 字段 `from_artifact` / `items_file` / `items`；来源不可解析（未确认选点、
  未结算发布）也变成静态 422（`rule=source_not_ready`），不再“preflight 绿灯、submit 才炸”。
- 测试：`test_preflight_and_submit_agree_on_batch_manifest_cardinality`（manifest 两项 →
  preflight/submit 同 code/field/rule）。

### 5. 长任务只能数目录；prepare→read 用不上

- IRC live metrics：`_IrcReporterSink` 在收到 `irc_forward_points/irc_reverse_points` 时除
  stage detail 外还 `update_live_metrics`（`live_status.metrics` 有读数，只读观测，不改结算）。
- prepare fast-path 收据补 `artifact_ref`/`operation_ref`：从已成功 prepare 的权威记录 +
  ArtifactStore 投影五元组（digest/generation 来自已发布事实）；查不到权威引用时**不再**返回
  不可读的 `ready`，而是回退异步 prepare。
- 指南修正：五元组来自 prepare/operation 回包（或已 prepared 的状态），不是目录名。

### 6. IRC 80 点跑完却 “no endpoint geometries”

- 失败表达升级：消息包含已捕获轨迹帧数/方向、明确“last observed geometry 是诊断证据不是正式
  端点”、给出检查/重跑路径；错误分类从 `backend_failure` 改为 `not_converged`。
- 科学边界不放宽：迭代上限端点不升级；具体 80 帧运行的科学根因仍需原始 `.out`/轨迹/端点样本
  （NOT_VERIFIED，见下）。

### 7. 结果结构套娃，不知道字段在哪个文件

- `topic.artifact_locating` 升到 1.1.0，新增 `result_fields` 节：频率（工作流
  `modes[].frequency_cm1`；Batch 的 `metadata.frequency.frequencies` / 逐 item
  `__normal_modes.json`）、`step_result.json` 的内部定位、相对能量（PES
  `relative_energies_kcal_mol`；Batch 按同 method/profile 读取时计算）、IRC 轨迹/端点、
  以及何时走 prepare→read。
- 修正 `docs/reference/current/ACP_Function_Test_Manual.md` 中“normal_modes_v1 含
  frequencies”的过时表述。

### 8. 零碎小刺

- CLI：`setup_logging` 文本 handler 从 stdout 改为 stderr（机器输出独占 stdout）。
- guide 列表：MCP `acp_get_guidance` 无 `guide_id` 时返回与 HTTP 同源的 discovery index
  （schema 不再 required）；不再要求客户端从 capabilities 索引翻 id。
- capacity：`slots_free/max_running` 改用 admission 实际执行的
  `registry.local.max_jobs`（`max_running_source` 标明来源），不再用 legacy
  `manager.max_running`，与 `capabilities.limits.local` 同权威。
- 测试：`test_cli_text_logging_stays_off_stdout`、
  `test_capacity_slots_match_the_effective_local_limit`、
  `test_controller_guidance_without_id_returns_the_index`。

## 另外包含

上一轮未提交的 selection 错误映射：`_ControllerSelection` 捕获 `ControllerClientError`，
`NotPreparedError` → `resource_not_ready` + `field=job_id`、`rule=not_found`（MCP 不再显示
`internal error: NotPreparedError`）。

## 验证

- 独立只读验收 agent（对照 8 条反馈 + 代码/diff + 定向测试）：结论 PASS（可开 PR），并指出
  fast-path 五元组缺测试（P1）与 5 处 P2——本 PR 已补齐：fast-path 五元组测试（artifact_ref/
  operation_ref + 无权威引用回退）、IRC live metrics 正向测试、mint 入口 max_candidates>=1 校验
  与指南入口说明、Batch source 诊断字段修正（from_artifact/items_file）、IRC 轨迹路径修正。
- 定向套件：批 A 194 passed；批 B 252 passed；另 test_acp2_runtime_artifact 14、
  test_acp_parameter_effects 10、架构/隔离/docs 50；fast-path/live-metrics/mint 新测试 88 passed。
- 本地全仓 `pytest -m "not slow and not contract"`（提交前最终版）：**12485 passed / 23 skipped /
  116 deselected**；唯一失败 `test_cccp_mpi_env::test_orca_runtime_env_no_mpi_and_no_ld_inherits`
  是本机 shell 的 MPI/LD 环境变量导致（CI 矩阵中该文件通过，非代码回归）。
- 契约/指南 digest 由代码自动计算；未手改机器契约。validate_acp2_contracts /
  validate_acp2_gap_coverage VALID，check_docs_links PASS。

## 未验证（NOT_VERIFIED）

- #6 的 80 帧运行科学根因：需要该次 `.inp`/完整 `.out`/轨迹与端点 XYZ/attempt 的 IRC 结果
  与 manifest（方向、maxpoints、源 TS）。本 PR 只验证表达/分类已修、不升级端点。
- #5 的具体失败点：需要该次 `acp_status` 回包与 prepare/read 调用序列；本地只验证代码路径与
  `test_acp2_runtime_artifact` 端到端。
- MCP stdio → REST 的整链 agent 发布（真实 controller + 已铸授权）：只验证工具转发单元 + REST 端到端。
- #8 的 CLI 具体日志行/容量回包：结构性缺陷已修，原始回包未采样。
- 真实双并发作业下的 capacity 读数：投影口径已对齐，未复刻并发场景。

## 回滚

每项独立：selector 服务端写入/授权创建入口（可回滚为 403 + 隐藏写工具，语义上不可回滚为
“忽略授权”）；directions 归一化（回滚需同时从契约 enum 删除 both）；preflight 基数调用；
IRC live metrics/失败消息；guidance 字段地图；CLI/capacity。无 schema 迁移，无科学边界放宽。
