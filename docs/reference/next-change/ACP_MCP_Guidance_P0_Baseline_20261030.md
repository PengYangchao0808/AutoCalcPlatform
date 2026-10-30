# ACP MCP 指南与自动科研链 — P0 基线与首批切片交付

日期：2026-10-30。核查与实施 HEAD：本批次提交（基于 `fc089830`）。
状态：**P0 基线 + P1a/P1b 首个切片已实施**；P2—P5 保持未实现，本文如实记录边界。
上位计划：[ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md](ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md)（§9 批次定义）。

## 0. 本批次实际交付（代码事实，非计划）

| 交付 | 驻点 | 证明 |
|---|---|---|
| 指南内容包 v1.0.0（`acp.guidance.v1`） | `src/acp/api/guidance_pack.py` | `tests/test_acp_guidance_pack.py` |
| 指南只读路由（发现/读取/版本/字节预算） | `src/acp/api/v1_guidance.py`（挂载于 `server.py`） | `tests/test_acp_api_guidance_routes.py` |
| MCP 工具 `acp_get_guidance`（第 9 个基础工具，只读） | `src/acp_services/entrypoints/mcp.py` + `controller_client.py::guidance` + `services_mcp_bootstrap.py::_ControllerGuidance` | `tests/test_acp2_mcp_guidance_tool.py` |
| PESsearch（bond_length_scan 分支）+ scan 提交契约 | `src/acp/api/submission_contract.py` | `tests/test_acp_submission_contract_first_batch.py` |
| capabilities 投影 guide_ref + PES/scan partial 契约块 | `src/acp/api/workflow_contracts.py` | 同上 + `tests/test_acp_mcp_ux_p2_discovery.py` |
| 工具面注册的三方同步（代码枚举 / 契约 JSON / 前端契约 / 验收脚本） | `p2-research-operations.v1.json`、`mcp-launch.schema.json`、`manifest.json`、`frontend/js/workbench/contracts/experience_contract.js`、`scripts/acceptance/validate_acp2_contracts.py` | `tests/test_acp2_runtime_contracts.py`、`validate_acp2_contracts.py` = VALID |

冻结决定（本文件即这些决定的 P0 记录）：

1. **工具名**：指南读取 = `acp_get_guidance`，基础面注册（未协商可见），参数表闭合（`guide_id` 必填；`section`/`version`/`max_bytes` 可选；`additionalProperties:false`）。
2. **内容格式**：`acp.guidance.v1`，包版本 `1.0.0`；每条目带 `version` 与 `sha256` 摘要（对 id/version/workflows/sections 的 canonical JSON）；字节预算 8192 B/指南、1024 B/发现卡（UTF-8 字节，非 token）。超限返回 `omitted_reason:"budget"` + 分节大小，绝不静默截断。
3. **单源关系**：参数/枚举/默认值权威仍是 `submission_contract.py`；指南正文不含参数 schema；`describe_workflow` 注入 `guide_ref` 但 coverage 语义不变（partial/not_provided 照实）。
4. **错误映射**：未知 guide → HTTP 404 `not_found`（含 allowed+suggestion）→ MCP 面 `resource_not_ready`（复用 ControllerClient `_HTTP_ERRORS` 通用表，不开第二映射）；未知 section → 422 `input_invalid`；过期 version 缓存 → 409 `revision_conflict`。

## 1. U01–U09 对账表（P0 出口项 1）

图例：**已有**＝前序 MCP UX（P1–P4，`reports/acp2_mcp_ux_20261029/`，rev 见各 ACCEPTANCE）/ **本批**＝本批次新增 / **待补**＝未实现 / **待验**＝代码在、需在新 HEAD 复验。

| U | 现状 | 证据/缺口 |
|---|---|---|
| U01 发现 | 已有：协议/扩展/工具/workflow selector（`capabilities(workflow=)`）；**本批**：guide_ref、PES/scan 契约块、`acp_get_guidance` | readiness configured/verified 分级仍部分待验（M0-10 三态） |
| U02 参数 | 已有：Confsearch/BatchOptimize 契约；**本批**：PESsearch(bond_length_scan)+scan 契约与示例；嵌套 levels 值语义 partial（gaps 如实声明） | 其余工作流仍 not_provided；嵌套值级校验待后续批次 |
| U03 纠错 | 已有：field/rule/allowed 结构化错误 | 建议补全（suggestion 只在 workflow/guide 两处，未全局） |
| U04 资源 | 已有：资源别名归一、admission 上限、fingerprint/plan digest | 语义分级投影（默认/上限/终止/累计）在 execution_limits_statement 已有，MCP 面摘要化待验 |
| U05 状态 | 已有：workflow/进度/产物描述、有界等待（research wait） | Batch item/stage 级失败定位在状态面的呈现待验 |
| U06 产物 | 已有：descriptor→prepare→read、attempt/generation/digest、正文内容策略 | 冷远端 unknown+零隐式 fetch 保持；跨截断/编码拒绝负例已在 P3 验收 |
| U07 摘要 | 已有：requested/effective 区分、构象计数语义、非 verified 不升格 | 科学摘要的操作化（vibration.analyze 等）按能力注册表逐项 |
| U08 找回 | 已有：任务查询/幂等对账/回包丢失找回（P3 链） | 研究/选点/Batch 的完整续接 = P4，未实现 |
| U09 轻量 | 已有：concise 结果、显式分段；**本批**：指南按需读取 + 字节预算 | 两个 harness 等价场景待 P5 真实复验 |

## 2. 首批四工作流 映射（P0 出口项 1 续）

| 工作流 | 输入契约 | 产物→候选 | Batch 消费 |
|---|---|---|---|
| Confsearch | complete（前序） | confsearch_manifest + 帧候选（`frame_candidates.json` 权威） | `items`/`from_artifact`（manual 来源 loader 过滤，见 §3 待补） |
| BatchOptimize | complete（前序） | 逐 item 结构/能量/频率/热化学 | —（自身即 Batch） |
| PESsearch | **本批 partial**：`mode=bond_length_scan` 直扫分支（source/coordinate[s]/selection/protocol；0-based 索引；`pes_bond_scan` level 结构） | `RESULT/pes_search/` profile/frames + S2 候选推荐 | PES review 发布链 = P2/P3，未实现；当前经发布门（manual）或 Batch inline items 进入 |
| scan | **本批 partial**：坐标（method 或 input；compact/object 双形）、`dft_scan` levels、ScanTS opt-in | scan 帧候选（通用 `frame_candidates.json`） | 同上：scan→Batch 受控适配 = P2/P3 |

负例 fixture 指针：`tests/test_acp_submission_contract_first_batch.py`（未知键/坏枚举/越界 level/镜像冲突回归）；PES 多峰/缺点等曲线级负例属 P2（提案/选点层），尚未实现。

## 3. 明确未实现（不因本批冒充完成）

- **P2 曲线与初猜提案**：概况/邻域点表按需读取、已有帧提案持久化、补算提案区分——全部未实现。
- **P3 自动授权与发布**：范围授权 schema、selection revision、PES writer/scan adapter/Batch loader 的 agent 来源同批整改——未实现；loader 仍只认 manual/manual_frame（`src/acp/calculations/batch/loaders.py`），**不得**以放宽字符串集合代替。
- **P4 Batch 整链恢复**：U08 研究级续接、崩溃矩阵、持久提交意图——未实现。
- **P5 真实使用与发行**：真实 SDK 双 harness、安装后验收、Local/LSF 分列——未实现；本批不关闭任何 G/H 门。
- 指南 CI 守护（新增工作流漏检指南的机器门）目前以 `tests/test_acp_guidance_pack.py::test_every_active_workflow_has_exactly_one_guide` 承担 catalog 对账；发行角色打包（acp-web 随包安装）依赖既有打包管线，未单独验收。

## 4. 真实验收场景（P0 出口项 5，范围声明）

- **基础门**（§3.3）：连接→发现→构造请求→预检→提交→等待→产物→read→找回。当前可通过既有 8+1 工具完成；本批新增指南读取一步。
- **已验证**（2026-10-30，回执 `reports/acp2_mcp_guidance_20261030/`）：DA 科研门真实链路 PASS-with-notes（codex/gpt-6-astra 独立验收）。同轮登记的缺陷：①BatchOptimize inline item 未标 role 时静默按 INT+默认方法执行、TS 意图无 typed 提示（负例 job 005，归 P1a/P3）；②PES coordinate.unit 非法枚举未在提交校验拦截（负例 job 001，归 P1a）；③pes_profile geometry_path 与落盘不一致等观察项（归 P1a/P2）。
- **科研门**（DA 验证，用户授权的真实 QC）：环戊二烯+乙烯 DA，经 MCP 提交 PESsearch（bond_length_scan，新建 C–C 键）→ 读曲线/帧 → 选初猜 → BatchOptimize（opt_freq，TS 角色）→ 读逐项结果判定虚频。**当前链路的发布段走现有确认门**；P2/P3 落地前不宣称"全自动发布已交付"。验证回执落 `reports/`（另行登记）。

## 5. 同步清单（本批触碰面）

- 代码：`guidance_pack.py`(新)、`v1_guidance.py`(新)、`server.py`、`routes.py`（`_MCP_TOOLS` 改为 allowlist 派生，消第二手抄表）、`workflow_contracts.py`、`submission_contract.py`、`mcp.py`、`controller_client.py`、`services_mcp_bootstrap.py`。
- 契约语料：`p2-research-operations.v1.json`（p1_advertised 增补 + 措辞）、`mcp-launch.schema.json`（name enum + tool_inputs.acp_get_guidance）、`manifest.json`（两个 sha256 + contract_digest + self digest 重算）。
- 前端：`experience_contract.js` ACP_TOOLS 增补（守护测试强制同步）。
- 验收脚本：`validate_acp2_contracts.py`（P1 面期望改为 冻结八名 ∪ {acp_get_guidance}）。
- 测试计数：8→9、18→19 全部改为随注册表派生或与 `MCP_TOOL_NAMES`/`TOOL_ALLOWLIST` 集合比较（tests/AGENTS 2026-10-30 约定：不永久钉死数量）。

## 6. 服务生效说明

生产 Controller（systemd `acp.service`，editable 指向本 checkout）与常驻 `acp mcp serve` 桥需重启后新路由/新工具才可见；重启属受控操作，由用户执行。重启前旧客户端行为完全不变（纯增量注册）。
