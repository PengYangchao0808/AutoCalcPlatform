# energy-profile — ACP 3.0 能量曲线面板（A-38 / A-39）

- 文件：`frontend/js/modules/energy-profile/module.js`
- 样式：`frontend/css/modules/energy-profile.css` + 复用 `frontend/css/workbench/energy.css`
- 测试：`frontend/js/module_tests/acp2_energy_profile.test.js`（24 项）
- 注册名：`window.ACPNextModules["energy-profile"]`
- 装配：Molecular 工作区 bottom（`recipes.js` 的 `mol-energy`）

## 1. 数据与渲染边界

模块只消费 `acp.view.v1` 的 `energy` 族（`ACPView.source` 归一化）。`toRendererData`
把信封投影回唯一 `energy_chart_renderer.js` 所需的 `nodes` / `series.values` /
`x_values` 形状；**不写第二套曲线、坐标或 SVG 实现**。渲染器仍由页面通过
`energyChart.init(deps)` 注入依赖；本模块提供只读的候选带/标注渲染（A-39），
不写任务。

## 2. 状态

`empty` / `loading` / `ready` / `error`（`denied` 并入 error）由
`data-acp-module-state` 表达；`source:"fixture"` 渲染 `data-acp-fixture="true"`
徽标；`warnings[]` 显示在底部条并写入 `data-acp-warning-count`；缺失点保持
`null`，由渲染器跳过，绝不补 0。

## 3. 装配

`frontend/next/index.html` 在模块前加载 `energy_chart_renderer.js`、`view_model.js`、
`view_source.js` 与 `fixtures/views/energy.js`，然后加载本模块。`recipes.js` 的
Molecular bottom 使用 `energy-profile`，并支持布局编辑器换入/换出。

## 4. 选择联动（A-39）

- 候选带 / TS/INT 标注由模块的 `renderTrajectoryAnnotations` 实现，内部复用
  renderer 的 `energyCandidateBandLayout` 与 `energyCandidateBandPreHeight`，
  不复制布局算法。
- 曲线点、候选带 chip、标注 marker 的点击统一解析 SVG 上的
  `data-energy-frame` / `data-energy-candidate-jump` / `data-energy-annotation`，
  选中后重绘 selected guide，并通过 `commands.openStructure({kind:"job-file",
  jobId, path: geometryRef, frameIndex})` 把结构定位到 viewer。
- 拖拽被锁定为只读；本模块不提交任务，保存候选是独立的显式按钮。

## 5. 保存候选（A-40）

- 选中 frame 后显示候选条：`TS` / `INT` 角色与“保存为候选”按钮。
- 保存经 `ACPClient.frameCandidates.save` → `POST /api/v1/jobs/{id}/frame-candidate`，
  body 含 `view_type` / `frame_index` / `role` / `expected_revision`，可选
  `frame_id`（节点 id）与 `item_id`（BatchOptimize 作用域）。
- 成功：更新 revision/list、重绘候选带并显示 candidate id；重复保存同一帧时
  按钮禁用。
- 409：重新拉取候选列表并显示“候选已更新，请重试”，不自动重试。
- `?view=fixture` 或未注入 `ACPClient.frameCandidates` 时保存按钮显式禁用，
  绝不伪造成功。

## 6. 候选详情（A-41）

- 顶部“候选 (N)”打开候选抽屉；数据与候选带同源（`graphState.frameCandidates.list`）。
- 行显示 role / label / frame / candidate_id；点击行选中 frame 并同步详情。
- 详情显示 candidate id、role、frame、name、saved_at、structure_path。
- 重命名复用幂等 `save` 路径（同一 frame/role，带 `name` 与 `expected_revision`），不产生新 candidate id。
- 删除经 `ACPClient.frameCandidates.remove` → `DELETE …/frame-candidate/{id}?expected_revision=`；
  409 刷新服务端列表并显示“候选已更新，请重试”；删除有 `confirm` 保护。
- 空列表显示“暂无候选”，不伪造条目。

## 7. 已知缺口

- 候选列表 / overflow 展开尚未接入（候选带本身可用）。
- 真实 `energy-graph` 五类任务的逐点一致性仍需在可运行实例上做数值对比（A-38 DoD）。
- 真实浏览器点击联动与保存/重命名/删除候选（chart → viewer/catalog → POST/DELETE）尚未执行验证。
