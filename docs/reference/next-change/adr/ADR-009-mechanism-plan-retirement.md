# ADR-009 — 机理计划确认（A-49）退役，P17 以只读 + ADR 收口

- **状态**：Accepted（2026-10-31，前端收口；后端/产品负责人保留恢复权）
- **Owner**：Frontend C 线（A-49 模块）+ Backend/Mechanism owner（退役事实）
- **来源**：`docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md` A-49 / P17；`docs/reference/next-change/ACP_V7_Parity_Status_20261031.md`
- **代码驻点**：
  - 后端端点：`src/acp/api/v1_routes.py:3072-3077`
  - 前端模块：`frontend/js/modules/mechanism-plan/`、`path-view/`、`pes-handoff/`
  - 证据：`frontend/js/module_tests/acp2_mechanism_plan.test.js`（29）、`acp2_path_view.test.js`（25）、`acp2_pes_handoff.test.js`（37）

## 1. 背景

P17「机理构建」在原计划中包含反应坐标计划装配与确认。前端 A-49 已实现 study 读取、步骤/角色展示、计划装配和确认请求；但后端：

```python
@router.post("/mechanism-studies/{study_id}/mechanism/plan", ...)
def confirm_mechanism_plan(...):
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")
```

代码库中不存在可接线的 plan 存储/哈希实现。前端无法产生真实 `plan_confirmed`；伪造成功会违反“解释不等于证据”和“不伪造科学状态”。

## 2. 决策

1. **A-49 的 plan 确认动作按退役处理**，不要求前端实现一个后端已明确拒绝的写操作。
2. P17 保留 **只读** 能力：
   - `mechanism-plan` 展示 study/步骤/角色/来源，确认请求只显示真实 410 错误；
   - `path-view` 读取/选择路径驻点结构；
   - `pes-handoff` 从服务端确认候选发起 BatchOptimize。
3. P17 在 parity 矩阵中标记为 **ADR-retired**（仅确认动作退役；只读面仍在）。24/24 门禁以 `23 implemented + 1 ADR-retired` 计，**不得**写成“A-49 已实现”。
4. 若后端恢复 plan 端点，必须新 ADR/变更记录撤销本决定，并补真实 plan_confirmed 端到端验收；当前前端模块无需重写。
5. 本 ADR 不关闭真实服务/Playwright/科学闭环的 `NOT_VERIFIED`，也不授权经典页退役。

## 3. 影响

- Track A：62 个实现 + 1 个 ADR 退役 = 63/63 收口；A-49 不计“已实现”。
- A-49 的 UI 仍可通过工具抽屉/布局编辑器访问，但确认按钮只在真实后端恢复后可用。
- 前端测试 `acp2_parity.test.js` 用本 ADR 作为 P17 的显式豁免证据。

## 4. 证据

- 后端 410 事实：`src/acp/api/v1_routes.py:3072-3077`。
- 前端只读/错误路径：`acp2_mechanism_plan.test.js` 的 410/409/422/500 用例。
- 真实浏览器：`reports/acp2_implementation/next_boot_20261031/`（工具抽屉 12 模块挂载，0 uncaught；机理模块显示真实 404/410 四态，不伪造）。
