# P2 验收记录（自描述：workflow 契约查询）

> 执行：2026-10-29T17:45–18:00 +08:00
> 分支：`acp2/mcp-ux-remediation`
> 状态：**待外部验收**

## P2-01 Controller 契约投影

- 新增 `src/acp/api/workflow_contracts.py`：从 `submission_contract` + `catalog` 派生
  `workflow_contract`（coverage / input / method / resources / examples / constraints）。
- `GET|POST /api/capabilities?workflow=`：
  - 大小写不敏感归一（`confsearch` → `Confsearch`）；
  - 未知 ID → 422 `input_invalid` + `params.rule=unknown_workflow` + `allowed` + `suggestion`；
  - `Confsearch` / `BatchOptimize` → `coverage=partial`，各 2 个示例（四个示例全部经 `submission_contract` 校验）；
  - 其余 **13 个** active 工作流 → `coverage=not_provided`（明确不提供 schema，而不是假装完整）；
  - 无 `workflow` 参数时**不出现** `workflow_contract` / `status_vocabulary`，既有形状不变；语汇表随契约块返回。
- 示例由 `submission_contract` 校验器验证（发布即合法）；四个示例（两个工作流各两个）全部有断言。
- 冻结契约已同步：`application-api.v1.json` 1.7.0 增加可选 `workflow` 请求字段与 `workflow_contract` 响应块；manifest 的 entry sha / `contract_digest` / 自校验摘要 / BOM 嵌入摘要按规则重算，`validate_acp2_contracts.py` → **VALID**。
- 约束写进投影：`screen` 零精修、纯 xTB 不做 DFT 精修、`max_conformers` 仅 NMR。

## P2-02 MCP 贯通

- `_CAPABILITIES_SCHEMA` 增加可选 `workflow`；`CapabilityPort.capabilities(*, workflow=None)`；
  `_tool_capabilities` 仅在有值时透传（无值路径对旧 fake/旧 Controller 完全不变）。
- `_ControllerCapabilities` → `ControllerClient.capabilities(workflow=...)` → POST body；`workflow=None` 时**不传 kwargs**，旧 fake/旧 Controller 路径不变。
- 测试覆盖：
  - **真实 SDK 客户端**（冻结的 DSH → `@modelcontextprotocol/client` 2.2.0 客户端脚本）：`acp_capabilities{workflow:"Confsearch"}` 返回 `coverage=partial` 与状态词表；随后再次 `tools/list` 仍可见研究工具（协商状态保持）。`tests/test_acp2_mcp_sdk_contract.py` 8 passed（`ACP2_DSH_SOURCE=/ACPServer/acp2-tools/deepseek-harness`）；
  - stdio 进程 → MCP → HTTP，`workflow=Confsearch` 被真实转发并在结果中返回 `workflow_contract`；
  - 无参调用形状不变、八工具集合不变；
  - 研究协商后再次 `tools/list` 可见研究工具；
  - 旧 Controller 返回 422 时 MCP 报 `input_invalid`，不降级为成功。

## P2-03 文案与词表

- `_PREFLIGHT_SCHEMA.idempotency_key` 单独描述为可选（submit 仍必填）。
- prepare 的 `task_ref` / `job_id` 描述去掉 route-path 说法。
- 公开描述移除 `M1-01b` 等里程碑编号。
- `project_id` 补充"省略落到 uncategorized 默认项目"。
- `status_vocabulary`（envelope ok/error vs task state，legacy alias）随 `workflow_contract` 返回，不再无条件改变无参形状。

## 证据

- `/ACPServer/acp_mcp_ux_20261029/evidence/p2/p2_batch.txt`：命令、cwd、模块路径、文件哈希、退出码。
- 批次：P2 discovery / P1 契约 / services-mcp / research handshake / p2 tools / mcp process / runtime contracts → **189 passed，exit 0**。

## 边界（不冒充 PASS）

- 冻结契约已按 manifest 规则同步（application-api 1.7.0，entry/digest/self/BOM 重算，`validate_acp2_contracts.py` VALID）。
- 完整 schema 仍只覆盖 Confsearch / BatchOptimize；其余 13 个 active 工作流明确 `not_provided`。
- 真实 SDK 已用 DSH/Node 客户端验证（8 passed）；非 DSH 第三方客户端仍 `NOT_VERIFIED`。
- 冻结契约已按 manifest 规则同步（见上）；仍属"待冻结实现"的只有真实第三方非 DSH 客户端。
- 选择器查询不改变协商状态；真实第三方客户端未接入（沿用 P0 的 NOT_VERIFIED）。
