# Round-7 批次 E 证据：MCP 取消有界回包 / 幂等键字符集 / 指南显式化（#5、#7b、#7e、#7f）

- 基线：`main` = `1ee34970`；工作区含**未提交的 guidance keep 基线**（`guidance_pack.py`、`workflow_contracts.py`、`catalog.py` + 两个测试版本 pin）。本批只在其上做增量，未回退/重写。
- 约束遵守：未 commit / 未 push / 未重启服务 / 未跑真实 QC / 未调用外部模型；未新增 store、未改 schema、未放宽护栏。
- 文件范围（批次 E）：`src/acp_services/entrypoints/mcp.py`（仅 cancel 回包段）、`src/acp_services/entrypoints/application_params.py`、`src/acp/api/guidance_pack.py`（增量）、`tests/test_services_mcp.py`、`tests/test_services_machine_output.py`、`tests/test_acp_guidance_pack.py`，新增 `tests/test_acp_round7_idempotency_key.py`、`tests/test_acp_round7_mcp_cancel.py`。`tests/test_acp_api_guidance_routes.py` **本轮未改**（PESsearch/BatchOptimize 版本未变；工作树中该文件的 1.2.0 pin 来自上一轮 keep 基线，因此它会出现在 `git status` 里）。

## 改动

### #5 纯 MCP 会话的 PES→publish→Batch 死路显式化（指南）

`task.find_transition_state` 1.1.0 → **1.2.0**（`src/acp/api/guidance_pack.py`）：

- `chain` step 3 重写为「先自查发布授权，再选路径」：
  - 无管理员预铸有界授权时，`acp_publish_pes_review` **一定会被拒绝**（要求 `authorization_id`、服务端固定 `selector=agent`，不会退化为 manual）；supported 路径 = `input.items` 内联结构 + 显式 `role:"ts"`（沿用 `batch_roles.ts`），**inline 路径是一等路径，不是兜底**；发布只给候选 review revision，不是进入 Batch 的前提。
  - 明确禁止 probe/自造 `authorization_id`：工具只回 typed 拒绝（原因 `authorization_not_found` / `access_denied`），平台不自动铸授权。
  - 已有合法发布（人工 review 或管理员授权下已发布）→ `input.from_artifact` + `selected_ids`。
  - 管理员已铸授权 → `POST /api/v1/selection-authorizations` → `acp_publish_pes_review`（完整链可用），授权是否可用只以调用结果为准。
  - 人工确认仍走 Workbench/REST（`selector=manual`），MCP 不能替人 manual 发布。
- `summary` 改为「有授权才 publish，否则 inline role:"ts" 一等路径直达」；`decisions` 增加第 ⑤ 决策点（进 Batch 的方式）；`not_supported` 把「扫描自动发布（需 review 确认）」改为「无管理员有界授权的自动发布（纯 MCP 走 inline role:"ts"，人工确认走 Workbench/REST）」。
- `PACK_VERSION` 1.3.0 → **1.4.0**（文档约定：任一 entry 变化即 bump）。

### #7f 单点产物两处 `sp.out` 布局（指南，一条）

`topic.artifact_locating` 1.1.0 → **1.2.0**：`result_fields` 增加一条：

> singlepoint 工作流写 `<job_dir>/sp.out`；BatchOptimize（single_flat 布局）的逐 item 单点写 `<job_dir>/WORK/05_SP/sp.out`。通用解析器两处都要认；读数仍以 `RESULT/result_manifest.json` 的产品路径为权威，路径只用于人工核对，不要按文件名猜。

（代码事实核对：`cccp/qc/interfaces/orca.py::single_point` 默认 `output_name="sp"`；`acp/calculations/executor.py` 与 `batch/engine.py` 的 `StepKind.SINGLEPOINT → "05_SP"`，single_flat 下 step dir = `WORK/05_SP`。未改磁盘布局。）

### #7b `idempotency_key` 接受括号/方括号

`src/acp_services/entrypoints/application_params.py`：

- 新增唯一权威常量 `IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:()\[\]-]{8,128}$"`，`_IDEMPOTENCY` 与错误 `params.pattern` 同源引用它（此前 pattern 字面量写了两遍）。
- 用点：`canonical_submit_request` / `normalize_submit` / `normalize_prepare` / `normalize_analyze` —— HTTP 与 MCP（以及 CLI）共用同一 normaliser，故一处放宽即三入口一致。
- 未变：长度 8–128；仍拒绝空白、`/`、引号、反斜线、控制字符。key 只进 DB TEXT 列 / 日志 / canonical request digest，从不进路径或命令行（已核对 `store.claim_idempotency`、`idempotency.py`、CLI/HTTP/MCP 使用点）。

### #7e `acp_cancel` 有界状态回包（HTTP 路由未动）

`src/acp_services/entrypoints/mcp.py`：

- 新增 `_cancel_acknowledgement(record, reference)`：只投影 `job_id` / `attempt` / `reference` / `state` / `status` / `updated_at` / `cancelled` / `already_terminal`（+ terminal 时的 `terminal_state`、round-2 兼容的 `cancel_requested`），`spec_ref`/`trace_ref` **仅在** Controller 行本身携带**且为字符串**时透传（非字符串映射直接丢弃，绝不从 `spec` 派生，绝不编造）——防止未来某个 record 把 spec 对象塞进 `spec_ref` 重新打开泄漏面。
- `_tool_cancel` 改用该投影；工具描述更新为「bounded acknowledgement … query acp_status for the full job row」。
- 未知 job：仍在端口内抛既有 typed 错误（`_ControllerCancel` → 404 → `resource_not_ready`），不会为缺失记录伪造 ack。
- 语义：观察态 `cancelled/canceled` → `cancelled=true`（本次意图已生效，queued cancel 是同步 CANCELLED）；`completed/failed` → `cancelled=false` + `already_terminal=true` + `terminal_state`（round-2 语义保留）；`cancelling` → `cancelled=true`。为此新增常量 `_CANCELLED_STATES`，`_TERMINAL_STATES` 未改。
- HTTP `POST /api/v1/jobs/{id}/cancel`（`v1_routes.py`，批次 D 文件）**未改**，仍返回完整 `V1JobRecordModel`；`tests/test_acp_round7_mcp_cancel.py` 用真实 Controller 断言「同一 job：HTTP 回包含 spec，MCP 回包不含」。

## 测试（命令 + 计数）

全部用 `/opt/acp/venv/bin/python -m pytest -p no:cacheprovider -q --tb=line <files>`：

| 文件 | 结果 |
|---|---|
| `tests/test_services_mcp.py` | 59 passed |
| `tests/test_services_machine_output.py` | 29 passed |
| `tests/test_acp_guidance_pack.py` | 18 passed |
| `tests/test_acp_api_guidance_routes.py` | 6 passed |
| `tests/test_acp_round7_idempotency_key.py`（新） | 26 passed |
| `tests/test_acp_round7_mcp_cancel.py`（新） | 2 passed |
| `tests/test_acp2_mcp_list_jobs_tool.py`（既有 cancel 语义 pin） | 8 passed |
| 合跑上表 7 个文件 | **148 passed** |

相关回归（同命令形态）：

| 命令 | 结果 |
|---|---|
| `tests/test_acp_services_isolation.py tests/test_services_worker_contract.py tests/test_architecture_invariants.py` | 143 passed |
| `tests/test_acp2_runtime_contracts.py tests/test_acp2_m1_01_contract_unification.py tests/test_acp2_request_field_equivalence.py tests/test_acp2_mcp_guidance_tool.py tests/test_acp_mcp_guidance_index.py` | 65 passed |
| `tests/test_acp2_mcp_research_handshake.py` | 16 passed |
| `tests/test_acp2_decision_freeze_integration.py` | 21 passed |
| `tests/test_acp2_submit_policy.py tests/test_acp2_preflight_fingerprint.py tests/test_architecture_invariants.py` | 49 passed |
| `scripts/check_grep_gates.py --suite architecture-remediation --root .` | 10 gates / 0 blocking |
| `scripts/verify_cccp_isolation.py` | PASS（15 tasks） |
| `ruff check`（本批新增/改动且基线干净的文件） | All checks passed |

已知既有问题（非本批引入）：`src/acp_services/entrypoints/mcp.py` 的 `selection_ref` 三元行 105 字符（HEAD 即存在，E501）；`ruff format` 对 `mcp.py`/`guidance_pack.py`/`test_services_mcp.py` 的既有行有格式化差异（HEAD 即存在），本批新增行已按 ruff 风格书写。

## 留给 Lead / 待裁决

1. **`authorization_not_found` → MCP `code` 映射缺失**：`src/acp/services_mcp_bootstrap.py::_TOOL_ERROR_CODES` 无该键，Controller 403 detail 的 `authorization_not_found` 在 MCP 面回落为 `backend_failure`（`access_denied` 映射正确）。指南写的是「带原因的 typed 拒绝（authorization_not_found / access_denied）」；若要求 `code` 字段本身正确，需要在 `services_mcp_bootstrap.py` 加一行映射 —— 该文件不在批次 E 文件范围，未改。
2. **计划 §#5(1)(2) 的 `acp_get_pes_review` 回包 `publishable/next_actions` 字段未做**：本轮 binding 清单只要求指南显式化；`_tool_get_pes_review`/selection port 不在「cancel 回包段」文件中，未改。
3. **`workflow_contracts.py` 的 batch_roles 说明句**（计划 §#2 step 4「role 内写 sp_method/sp_basis；顶层仅 Workbench 镜像，缺 role 会被拒」）属计划里的批次 E，但不在本轮 binding 清单；且它依赖批次 B 的 `mirror_without_role` 最终落地。本批未改，避免与批次 B 冲突。
4. **契约语料 drift**：`docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json`（禁手改的机器契约）仍写旧 pattern `^[A-Za-z0-9._:-]{8,128}$`；运行时 normaliser 已放宽。需要契约版本化更新（或确认以代码为权威），本批未改契约。
5. **HTTP `Idempotency-Key` header-only 路径**（`v1_routes._submit_idempotency_key`）不经过 `_optional_idempotency`，因此 header-only 的畸形 key 不按 pattern 拒绝（body key 会）。`v1_routes.py` 属批次 D，未改。

## NOT_VERIFIED

- **未运行生产服务**：真实 Controller 仅经测试内 uvicorn loopback（B06 harness，submission thread 关闭）验证；`acp.service` 未重启、未做部署冒烟。
- **未跑 `-m contract` SDK lane**（需外部 DSH/Node；本机未声明 `ACP2_DSH_SOURCE`）。
- **未跑真实 QC / LSF**：所有取消测试的 job 停在 QUEUED（submit thread 关闭），不涉及科学执行。
- **未验证外部 agent 原始 50KB 回包**（计划 §「需要用户确认」样本 5 未提供）；体积证据来自「同形状 fake（~57KB spec）」与真实 Controller 的 HTTP/MCP 对照，不是该次调用的逐字节回放。
- 指南未在真实 agent 会话中做行为评估（仅内容/快照/尺寸断言）；`authorization_not_found` 的 MCP 面实际 `code` 见上「留给 Lead」第 1 条。
