# P4 验收记录（科学可解释性：执行证据 / 方法身份）

> 执行：2026-10-29T19:05–19:20 +08:00
> 状态：**待外部验收**

## P4-01 CENSO 成功执行证据

- `CensoRunResult` 新增 `execution`（已执行事实，不是请求回显）：
  - `argv`（完整命令行）、`returncode`、`cwd`、`preset`、`final_part`、`temperature_k`；
  - `artifacts.{json,xyz,out}` 的 `{path, sha256}`（读取失败则 digest 为空字符串，不伪造）。
- CENSO 成功后由 `refine_ensemble` 填充；工作台链路由 `energy.py` 转发到
  `WorkflowResult.metadata["censo_execution"]`，再进入 `ProtocolOutcome.workflow_metadata`。
- 证据不来自请求：`request.levels` 只进入 `method_identity.requested`。
- 测试：`test_refine_ensemble_success` 断言 argv/rc/final_part/两种产物 digest。

## P4-02 final_report 方法身份

- `final_report.json` 新增（附加键）：
  ```json
  "method_identity": {
    "requested": {"dft_opt": {...}, "refinement_sp": {...}},
    "executed": {"argv": [...], "returncode": 0, "artifacts": {...}},
    "executed_status": "verified | unverified",
    "energy_kind": "dft | censo | xtb"
  }
  ```
- 每个 conformer 增加：
  - `method_role`：`refinement_sp`（refined）或 `screening`（未精修）；
  - `method_evidence`：`executed`（存在 CENSO 执行事实）或 `unverified`。
- 混合 refined/unrefined 不共用同一方法声明；缺证据写 `unverified`，不猜 `dft`。
- 旧报告仍可读：只新增键，未改既有字段语义。

## 第一轮验收后整改

| 问题 | 整改 |
|---|---|
| interface→task→adapter→workflow 传递链断裂，`censo_execution` 恒空 | `censo_refine` 任务把 `execution` 放进 TaskResult.metadata；`energy_adapters.censo_refine_via_task` 重建 `CensoRunResult(execution=...)`；`energy.py` 转发；新增 `test_acp_mcp_ux_p4_chain` 断言 adapter 保留执行事实 |
| `verified` 只看字典非空，可被 `{"returncode":1}` 伪证 | `_execution_verified` 要求 `returncode==0`、非空 `argv`、`final_part`、json/xyz 两个 64-hex digest；弱证据测试全部 `unverified` |
| 所有构象共用 verified，refined 直接写 dft | 按运行 part 判定：screening 运行时仅 screening 条目 `executed`，refined 条目 `unverified`；refinement 运行时反之；新增 `energy_kind_verified` |
| 非 CENSO 报告无条件加 method_identity | 仅当存在 requested levels 或执行事实时才添加该块 |
| 测试手填 `"a"*64` 固化漏拒 | 改为完整执行事实夹具，并补 rc=1 / 空 argv / 短 digest 三类负例 |

## 第二轮复验整改

| 问题 | 整改 |
|---|---|
| `energy.py` 三处执行分支未接 `censo_result.execution`，转发分支永空 | 三处（screening / refinement / 另一 refinement 分支）均赋值 `censo_execution`，metadata 转发恢复 |
| `final_part` 只按现存文件选，旧 refinement 产物可绑新 rc=0 | 从本次 argv 的 `-P/-S/-R/-O` 推导 enabled parts，`_resolve_final_part(allowed=...)` 只接受本次启用的 part；新增 stale artifact 测试 |
| 角色证据未逐构象对齐 | execution 携带本次 `conformers` 列表；screening 条目必须其 `source_conf_id` 在列表内才 `executed`，否则 `unverified`；新增逐构象负例 |

仍为已知限制：refined 条目的 DFT 执行证据（ORCA 侧）未采集，screening 运行中保持 `unverified`，不冒充。

## 证据

- `evidence/p4/p4_batch.txt`：命令、模块清单、源文件哈希、exit code。
- CENSO/confsearch/report/chain 批次：**192 passed，exit 0**。

## 边界（不冒充 PASS）

- 只有 CENSO 路径产生 `executed` 事实；纯 xTB / CREST-only 路径为 `unverified`（没有可核验的 DFT 执行证据）。
- 非 CENSO 协议的报告不新增方法身份（本轮范围外）。
- 真实 QC 未提交（本轮全部为 mocked subprocess / fixture）。
