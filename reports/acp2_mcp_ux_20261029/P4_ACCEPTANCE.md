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

## 证据

- `evidence/p4/p4_batch.txt`：命令、模块清单、源文件哈希、exit code。
- CENSO/confsearch/report 批次：**188 passed，exit 0**。

## 边界（不冒充 PASS）

- 只有 CENSO 路径产生 `executed` 事实；纯 xTB / CREST-only 路径为 `unverified`（没有可核验的 DFT 执行证据）。
- 非 CENSO 协议的报告不新增方法身份（本轮范围外）。
- 真实 QC 未提交（本轮全部为 mocked subprocess / fixture）。
