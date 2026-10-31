# ACP 3.0 前端可视化适配计划（v2 · 可视化先行）

**日期**：2026-10-31（v2 改写）· **性质**：实施计划（**前端**；目标为全功能对等 D5；不含后端实现，后端缺口见 §6 登记）
**上位**：[ACP 3.0 架构与共享契约](ACP_3.0_Architecture_20261031.md) · [前端目标形态](ACP_Frontend_Target_Workbench_Plan_20261030.md) · [多用户/DSH 拓扑](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)
**并行**：[ACP 3.0 后端实现计划](ACP_3.0_Backend_Plan_20261031.md)（本计划**不等待**它）
**取代**：本文 v1（`Workbench on DSH` 版）。v1 的宿主假设、三工作区与插件打包内容未删除，改为 §5.V8 的**推迟**阶段。

---

## 0. v2 的五个决定（与 v1 的差别）

| # | 决定 | 影响 |
|---|---|---|
| **D1** | **可视化先在 ACP 自己的壳里做完**（`/next/` + 经典 `/`），不依赖 DSH 安装、不依赖 v3 API | 现在就能开工、能验收；DSH 只作为后续宿主选项 |
| **D2** | 面板只认**视图模型**（§3），数据由**适配层**（§4）喂：`live`（现有 v1/v2）/`fixture`/`v3`（以后） | 后端到位时**只换适配器**，面板不改 |
| **D3** | 后端缺口**冻结为待开发清单**（§6），每个缺口都给出前端降级态；**不伪造数据**，fixture 必须可见 | 前端不会被阻塞；也不会假装能力已存在 |
| **D4** | 宿主形态（拓扑文档 T1 自建壳 / T2 DSH 为壳）**不在本计划裁决**；可视化层与宿主解耦，两种组装都能接 | 避免与 [拓扑 §9](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md) 的结论冲突（见 §8-C4） |
| **D5** | **全功能对等（parity）是本计划的目标，不只是可视化子集**：老 UI 的每个功能面都必须在新 UI 有对应实现并通过测试，**零功能丢失** | 可视化只是**第一批**；功能面清单与迁移矩阵见 §2.4，门禁见 §7 |

**结论**：本计划交付"**老 UI 全部功能在新 UI 可用**，且任意宿主都能组装"，不等后端。可视化先行只是排序，不是范围上限。

---

## 1. 范围

**目标（D5）**：**老 UI 的每个功能面在新 UI 都有对应实现**，功能不丢、行为可验。老 UI 是 parity 的**唯一权威清单**——它的面板、弹窗、操作与文案（`frontend/js/workbench/i18n.js` 共 **1 673** 条标签）不得被"重设计"掉；新 UI 允许改布局与交互，不允许减法。

**排序**：先可视化（F1–F5、F9–F10、F12，见 §2.3），再表单与操作（向导/编辑/队列），再研究面（机理/证据/审批/能力表单），最后宿主。分期见 §5。

**两条工作线（v2.1 拆分）**

| 线 | 内容 | 规模 | 约束 |
|---|---|---|---|
| **Track A** | **老模块全面移植**：P01–P24 共 24 个功能面 → **63 项任务、190 人日**（+装配 8 = 198） | 主线 | 受 **parity 门**约束：24/24 未达标不得下线经典页 |
| **Track B** | **新模块实现**：老 UI 没有的能力（2D、live 网络、波函数、多实例、终端、DSH 宿主、v3 适配）→ **13 项、95 人日** | 副线 | 受 **§6 阻塞**约束：现在只有 **17 人日**可开工（B-06/07/08） |

清单在 §5 末尾（Track A / Track B 两张表），里程碑见"两条线的编排"。**B 线不得抢 A 线资源**。

**不做（本计划内）**：后端端点实现、DSH 插件打包与安装、`/u/{account}/panel/*` 反代、Ketcher 集成、波函数/轨道（后端无产品）、多用户隔离、终端。这些是 §6 的 BE-GAP 或 Track B 的阻塞项。

**已交付的既有能力不重写**（据 [UI-01/02/03A 收口](ACP_Frontend_UI01_UI02_UI03A_Closure_20261030.md) 与 [UI-03/04/05 收口](ACP_Frontend_UI03_UI04_UI05_Closure_20261030.md)）：Shell（registry/loader/context/commands/layout/recipes/i18n）、`molecule-viewer`、`task-explorer`、`agent-workspace`、`task-config`（4 个 simple 工作流端到端）、`exec-detail`（证据面板）、`reaction-network`（只读、历史数据）。

---

## 2. 现状盘点（2026-10-31 代码事实，已逐项核实）

### 2.1 渲染资产与"能不能直接搬"

判定口径同 [`molecule-viewer` README §3](../../../frontend/js/modules/molecule-viewer/README.md)：**纯函数/依赖注入可搬**；**读页面全局、直接造 DOM、绑厂商实例的不搬，重写**。

| 资产 | 位置 | 规模 | 复用判定 |
|---|---|---|---|
| 能量曲线渲染器 | `frontend/js/visualization/energy_chart_renderer.js` | 56 KB，`init(deps)` 注入 | ✅ **可搬**：曲线族唯一实现，禁止再写第二套 |
| 3D 场景实例 | `visualization/scene_instance.js` | 29 KB | ✅ 可搬：唯一 3D 创建点（唯一 3D 创建点；framing 一律走 `scheduleViewerFraming`，ANTI #22） |
| 取景调度 | `visualization/framing.js` | 5 KB | ✅ 可搬 |
| 3Dmol 适配 | `visualization/mol3d_adapter.js` | 2.4 KB | ✅ 可搬；新文件禁 `$3Dmol` 直呼（ANTI #22） |
| 结构查看器状态机 | `frontend/js/structure_viewer.js` | 4025 行 / v0.15.0 | ⚠️ **部分**：catalog→state、stale-guard、overlay、IRC 播放、视图状态可移植；DOM 同步与页面全局必须重写 |
| 振动查看器 | `frontend/js/vibration_viewer.js` | 76 KB | ⚠️ 同上（播放循环需重建在模块生命周期内） |
| NMR 面板 | `workbench/features/nmr.js` | 37 KB | ❌ **重写**：`nmrPanelState` 为页面级单例 |
| 几何编辑 | `structure_editor.js` + `geometry_store.js` | 76 + 19 KB | ⚠️ 纯函数可搬；`ACPGeometryStore` 为页面单例，**多实例前必须工厂化** |
| 整页能量/轨迹 | `workbench/features/energy.js` | 254 KB | ❌ 不复用整页；只取 renderer |
| 反应网络 | `modules/reaction-network/module.js` | 24 KB | ✅ 已在 Next；数据仅历史 mechanism |
| 3D 厂商库 | `js/vendor/3Dmol-2.5.5.min.js` | 538 KB | 唯一第三方 3D 依赖；**无 Ketcher** |

### 2.2 现成数据端点（已核实，前端可直接用）

| 用途 | 端点 |
|---|---|
| 结构目录（多源 + 多帧） | `GET /api/v1/jobs/{id}/structure-viewer`（`structure_viewer_v1`） |
| 单条目几何 | `GET …/structure-viewer/entries/{entry_id}/geometry` |
| 振动数据 | `GET …/structure-viewer/entries/{entry_id}/vibrations` |
| 两结构叠加 + RMSD | `GET …/structure-viewer/overlay` |
| 能量曲线（8 类视图共用） | `GET /api/v1/jobs/{id}/energy-graph` → `series / nodes / annotations` |
| 扫描剖面/候选/帧 | `GET …/s2/profile`、`…/s2/candidates`、`…/s2/frame/{i}` |
| IRC 帧几何 / 日志尾 | `GET …/irc/frames/{direction}/{i}/geometry`、`…/irc/log-tail` |
| PES 人工选点 | `GET/POST …/pes/review`、`POST …/pes/review/restore` |
| 频率来源 | `GET …/frequency-sources` |
| 文件/日志/工件 | `…/files`、`…/files/{path}`、`…/remote-files*`、`…/logs`、`…/artifacts`、`…/availability`、`…/prepared/{resource}` |
| 结构规范化 / 2D↔3D | `POST /api/v1/molecule/resolve`、`POST /api/v1/molecule/embed` |
| 任务/阶段 | `GET /api/v1/jobs/{id}`（`stage_*`）、`…/detail`（`detail.stages`）、`GET /api/v2/task-view` |

> 后端视图契约的权威是 `src/acp/results/frames.py::VIEW_REGISTRY`（9 类：`scan / scan_trajectory / optimization / conformer / sampling / reaction_path / irc / neb / unsupported`）。**曲线族 8 类共用一个渲染器**，不要按视图类型复制 UI。

### 2.3 视图族 × 就绪度

> 编号约定：**`F`＝视图族**（能力单位），**`V`＝分期**（工期单位，见 §5）。两者不是一一对应。

| # | 视图族 | 渲染器 | 数据 | Next 入口现状 | 本计划结论 |
|---|---|---|---|---|---|
| F1 | 3D 单结构 | scene_instance | structure-viewer | `molecule-viewer` 已交付（仅 XYZ、单帧） | **扩**：MOL/SDF、多帧选择 |
| F2 | 帧序列 / IRC 播放 | scene_instance | geometry 多帧、irc frames | 无 | **新建** `frames-player`（含暂停/步进/速度/循环） |
| F3 | 能量曲线 + 候选带 | energy_chart_renderer | energy-graph | 无（仅经典页） | **新建** `energy-profile`（8 类视图共用） |
| F4 | 振动 + 热化学 | scene_instance + 箭头 | vibrations、frequency-sources | 无 | **新建** `vibration-panel`（播放/停止、模式列表） |
| F5 | 结构叠加 / RMSD | scene_instance（第二 model） | overlay | 无 | **并入** `molecule-viewer`（overlay 模式） |
| F6 | NMR 谱图 + 归属 | 新写 | **无端点**（经典页读文件） | 无 | **新建** `nmr-panel`，先走 `live(file)` + fixture |
| F7 | 化学网络 | SVG | **无端点**；`reaction-network` 用历史 mechanism | 已交付（只读、历史） | **保留**；现役数据待 BE-GAP-02 |
| F8 | 2D 结构绘制 | — | **无端点、无 Ketcher** | 无 | **非迁移项**：老 UI 从来没有 2D 绘制；这是目标形态的新能力，阻塞于 BE-GAP-03，本计划**不排期** |
| F9 | 属性/表格（构象、频率、能量） | 原生表格 | detail / artifacts | `exec-detail` 有工件清单 | **扩**：结构化表格 + 排序/筛选 |
| F10 | 工件预览（文本/日志/图片/结构） | 原生 + 结构预览 | files / artifacts / prepared | 部分 | **扩** `exec-detail` 为通用预览 |
| F11 | 波函数/轨道 | — | **无端点** | 无 | **非迁移项**：老 UI 的"波函数" tab 是**占位**（无实现，见 §2.4 判定规则）；新 UI **不复制空入口**，待后端有轨道产品再作为新功能引入（BE-GAP-05） |
| F12 | 研究证据视图 | 原生卡片 | v2 research/science | `agent-workspace` 已交付只读首片 | **保留**，补 fixture/real 标识 |

### 2.4 老 UI 功能面 → 新 UI 迁移矩阵（parity 清单，D5）

> **老 UI 是唯一权威清单**。下表的"未移植"是当前事实，不是可选项；每一项都必须迁完（或走 ADR 明确废弃），否则经典页不得下线。

| # | 功能面 | 老 UI 入口（代码体量） | 新 UI 现状 | 期次 |
|---|---|---|---|---|
| P01 | 任务创建向导（15 工作流 / 方法 / 电荷自旋 / 资源 / 校验 / 预检 / 提交） | `workbench/features/wizard.js`（166 KB）+ `features/wizard/*` | `task-config` 已交付但**仅 4 个 simple 工作流**（其余 11 个显式禁用、给经典入口） | V4 |
| P02 | 任务草稿（自动保存 / 恢复横幅 / 409 冲突 / 提交后删除） | wizard 内 + `GET/POST /api/v1/drafts`（服务端已就绪） | 未移植 | V4 |
| P03 | 修改参数后重算（edit-recalc 全链 + 预览 + coverage） | `job_editor.js`（128 KB） | 未移植 | V4 |
| P04 | 结构来源选择（多来源 / 多帧 / 远程文件） | `structure_source_picker.js`（82 KB） | 未移植（`molecule-viewer` 仅单点 `structureRef`） | V4 |
| P05 | TS Mode 编辑器 | `tsmode_editor.js`（32 KB） | 未移植 | V4 |
| P06 | 任务队列运维（暂停 / 继续 / 重跑 / 清除 / 克隆 / 移动 / 重命名 / 标签） | `features/queue.js`（83 KB） | `task-explorer` 只读列表 | V5 |
| P07 | 项目与任务组织（新建 / 移动 / 重命名 / 删除 / 批量清除 / 管理标签） | `features/queue.js` + 经典页弹窗 | 未移植 | V5 |
| P08 | 任务详情（阶段进度 / 实时日志 / 文件 / 工件） | `features/job-detail.js`（52 KB）+ `files-logs.js` | `exec-detail` 部分（工件清单 + 下载） | V5 |
| P09 | 文件与日志浏览（目录树 / 日志尾 / 预览） | `features/files-logs.js`（26 KB） | 未移植 | V5 |
| P10 | 系统与资源（节点列表 / 状态 / ping / bootstrap、软件发现、后端） | `controllers/system_resources_controller.js`、`client/system-endpoints.js` | Shell 仅读 `/api/backends` | V5 |
| P11 | 结构查看器（**三个有实现的 tab**：结构 / 能量与轨迹 / 反应路径；另有 1 个"功能开发中"占位 tab；叠加、IRC 播放、样式、相机、视图状态、窄屏抽屉） | `structure_viewer.js`（165 KB）+ `molecule-view.js::setViewerTab` | `molecule-viewer` P0（单帧 XYZ、无交互） | V1 / V2 |
| P12 | 能量 / 轨迹与候选评审（曲线、候选带、保存候选、帧联动） | `features/energy.js`（254 KB） | 未移植 | V1 |
| P13 | 振动检查器（频率表 / 箭头 / 动画） | `vibration_viewer.js`（76 KB） | 未移植 | V2 |
| P14 | 结构编辑（测量 / 内坐标编辑 / 事务 / 资产保存） | `structure_editor.js`（77 KB）+ `geometry_store.js` | 未移植 | V2 |
| P15 | 候选详情面板 | `candidate_details.js`（29 KB） | 未移植 | V1 |
| P16 | S2 键长扫描弹窗 | `scan-s2.js`（4 KB） | 未移植 | V5 |
| P17 | 机理构建（反应坐标计划装配） | `features/reaction-builder.js`（157 KB） | `reaction-network` 只读展示 | V6 |
| P18 | NMR 报告（DP4/DP5 可视化） | `features/nmr.js`（37 KB） | 未移植 | V3 |
| P19 | 手工研究（无模型提问入口） | `features/science-question.js`（63 KB） | `agent-workspace` 只读首片 | V6 |
| P20 | 科学证据（claim states 与执行事实并列） | `features/scientific-evidence.js`（39 KB） | `agent-workspace` 证据卡（部分） | V6 |
| P21 | 平台证据投影 | `features/research-evidence-view.js`（4.7 KB） | 未移植 | V6 |
| P22 | 科研审批（持久审批卡、预算绑定） | `features/research-approval.js`（68 KB） | 未移植（提交侧契约已交付） | V6 |
| P23 | 能力表单（schema 驱动） | `features/capability-form.js`（33 KB） | 未移植 | V6 |
| P24 | 语言 / 主题 / 布局 / 窄屏 | `workbench/i18n.js`（196 KB，**1 673** 条标签）、CSS 变量、抽屉 | `shell/i18n.js`（**434** 条）、`layout-manager`/`layout-editor`、Alt+1/2/3 | V7 |

**当前对等度（可机检）**

- 老 UI：**1 673** 条标签，分属 **60** 个前缀组（`modal` 164 / `structure` 139 / `energy` 112 / `queue` 102 / `research` 98 / `picker` 92 / `edit` 87 / `wizard` 80 / `mech` 79 …）。
- 新 Shell：**434** 条（`next` 282 / `shell` 80 / `agent` 40 / `layout` 30）。
- 两套字典**设计上零重叠**（shell 独立字典，见其文件头注释），因此**对等度按功能面判定**，键组只作为每个面的完成度下界：`24 个功能面，6 个已有实现（且均为部分），18 个未移植`。
- 每面完成 = **模块已注册 + 四态可用 + 对应测试通过 + 键组覆盖达标**；四项缺一不算迁移完成。

**判定规则：什么算"一个功能面"（避免把空壳当功能）**

1. **点击后有实现**才算功能：有面板、有处理代码、有数据来源。只有按钮、或点击后显示"功能开发中"占位的**不算**——老 UI 的"波函数" tab 正是这种：`setViewerTab` 只处理 `structure / energy / path`，其它值一律落到 `renderMechanismPlaceholder()`（文案 `viewer.coming_title` = "功能开发中"）。
2. 老 UI 的**占位入口不迁移**。新 UI 若要表达同一意图，只能给显式"未接入"（并挂 BE-GAP），**不得做成空按钮**。
3. 只有"老 UI 有、新 UI 没有"才计入缺口；老 UI 从来没有的能力（如 2D 绘制）属于**新功能**，记 §6 或后续计划，**不算 parity 欠账**。

> 变更规则：老功能**不得因新设计缺位而删除**。确需废弃，必须走 ADR 并在本表标注"ADR 废弃 + 理由"，不得静默消失。

---

## 3. 视图模型契约 `acp.view.v1`（前端冻结）

所有面板**只消费**这个信封，不直接 fetch、不解析后端历史字段。

```js
// window.ACPView.model.normalize(raw, { family, source })
{
  schema: "acp.view.v1",
  viewId: "job:20260823_001_Confsearch:energy",
  family: "energy" | "frames" | "vibration" | "structure" | "overlay"
        | "spectrum" | "network" | "table" | "artifact" | "evidence",
  source: "live" | "fixture",          // fixture 必须在 UI 上有可见标识（沿用 UI-03A 约定）
  availability: "ready" | "empty" | "denied" | "error",
  provenance: { jobId?, attempt?, entryId?, digest?, generatedAt? },
  axes?: { x: {label, unit}, y: {label, unit} },
  series?: [{ id, label, points: [{x, y, frameIndex?}] }],
  frames?: [{ index, label, energy?, geometryRef }],
  annotations?: [{ id, label, kind, x, y, payload? }],
  warnings?: [{ code, message }]        // 缺字段/降级的显式说明，不静默
}
```

**四条硬规则**

1. **四态必显**：`empty / loading / ready / error`（`denied` 并入 error 的专用文案）。状态用模块自身的 `data-acp-module-state`，与 `molecule-viewer` 一致。
2. **fixture 可见**：`source:"fixture"` 时面板渲染 `data-acp-fixture="true"` 徽标 + i18n 文案；浏览器测试断言徽标存在，防止演示数据被当成真实结果。
3. **缺失即声明**：字段缺失写 `warnings[]` 并在 UI 显示"数据不全"；禁止用 0 或占位数值填充。
4. **不越权**：能量/帧面板**只读**，不得提交任务（ANTI #22：帧操作只能"保存为候选"）。

---

## 4. 数据适配层（后端解耦的关键）

```js
// frontend/js/visualization/view_source.js
window.ACPView.source.create({ mode, baseUrl, fetch })
//   mode: "live" | "fixture" | "v3"
//   → { load(viewId, ref) → Promise<AcpView>, health() → {mode, ok} }
```

| 模式 | 实现 | 用途 |
|---|---|---|
| `live`（默认） | 现有 v1/v2 端点（§2.2），逐族映射到 `acp.view.v1` | 今天就能用真实数据 |
| `fixture` | `frontend/js/visualization/fixtures/views/*.js`（确定性数据，含边界样本） | 后端缺口面板、离线演示、契约测试 |
| `v3` | 预留在同一接口后（`/api/v3/...`），**暂不实现** | 后端到位后切换，面板零改动 |

**选择规则**：URL `?view=fixture` 或 `localStorage.acp.view.source` 覆盖；生产默认 `live`；`v3` 未实现时回落到 `live` 并在控制台记一条 warning（不静默）。

**禁止**：面板内直接调用 `fetch("/api/...")`；新增第二套缓存；把 `structure_viewer_v1` 原始字段泄漏到模块外部。

---

## 5. 分期（每期均可无后端验收）

> 单位为人日，按 1 人计。数字由 §5 末尾的**任务清单自下而上累加**（63 项 A 任务 = 190 人日，13 项 B 任务 = 95 人日），比 v2 首版的拍脑袋估算高约 60%——旧口径只算了"把视图接上"，没算表单、运维与操作细节。误差仍按 ±25% 看待，排期前逐项细化。
> 分期只覆盖 **Track A（老模块移植）**；**Track B（新模块）**按阻塞项单独排，见 §5 末尾。
> 依赖：V0 前置全部；V1/V2 可并行；V4 依赖 V1（结构来源与候选联调）；V6 依赖 V3（NMR / 证据）。

| 期 | 内容 | 覆盖功能面（§2.4） | 任务 | 人日 |
|---|---|---|---|---|
| V0 | 契约与骨架 | — | `A-00` | 3 |
| V1 | 结构与能量查看 | P11（部分）/ P12 / P15 | A-32/33/38/39/40/41 | 20 |
| V2 | 帧、振动、结构编辑 | P11（余）/ P13 / P14 | A-34…37/42…47 | 26 |
| V3 | 谱图（网络只读回归） | P18（+ P17 只读回归） | A-52/53 | 5 |
| V4 | **任务配置与编辑** | P01 … P05 | A-01…A-16 | 54 |
| V5 | **任务运维与系统面** | P06 … P10、P16 | A-17…A-28、A-30/31、A-48 | 38 |
| V6 | **研究与证据面** | P17（全）、P19 … P23 | A-49…A-51、A-54…A-60 | 34 |
| V7 | 装配、对等验收与质量 | P24 + parity 门（§7） | A-61…A-63 + 装配 | 18 |
| V8 | 宿主接入（**推迟**） | — | Track B 的 B-09/B-10 | 待定 |

**Track A 合计 = 198 人日**（1 人约 45 周；2 人并行约 23 周）。其中可视化（V1–V3）**51 人日**、配置与运维（V4–V5）**92 人日**、研究面（V6）**34 人日**——**功能对等的主体在 V4–V6**，不能按"前端已做完"估算。

### V0 契约与骨架（3 人日）
- `view_model.js`（normalize + 四态 + warnings）、`view_source.js`（三模式，`v3` 抛未实现）、fixture 目录与 2 个样本族。
- 把 `molecule-viewer` 改为经适配层取数（行为不变，回归全绿）。
- **DoD**：`node frontend/js/module_tests/run_all.js acp2_module_viewer` 通过；新增 `acp2_view_model.test.js`（normalize/四态/warnings/fixture 标记）≥10 项；`?view=fixture` 能驱动 viewer。

### V1 结构与能量（20 人日）
- `energy-profile`：8 类视图共用；候选带/标注、双轴、能量单位、空数据与"不可用"视图（`unsupported`）。
- `molecule-viewer` 扩：MOL/SDF 解析（纯函数自写，参照 README §3 白名单法）、多帧选择、overlay + RMSD。
- **DoD**：能量曲线在 `scan / optimization / conformer / irc / neb` 五个真实任务上与原经典页**逐点一致**（同 job 对比截图或数值断言）；`acp2_energy_profile.test.js` ≥12 项；无任务时 `empty` 态不报错。

### V2 帧、振动与结构编辑（26 人日）
- `frames-player`：步进/播放/暂停/速度/循环、`generation` 守卫（切任务丢弃迟到结果）、`suspend/resume` 释放渲染。
- `vibration-panel`：模式列表、箭头播放/停止、位移幅度、热化学摘要（有则显示，无则 warned）。
- **DoD**：IRC 正/反向播放与经典页帧序一致；连续切换 3 个任务无 viewer 泄漏（沿用 `scene_lifecycle_matrix.test.js` 的计数断言）；`acp2_frames_player.test.js`、`acp2_vibration_panel.test.js` 各 ≥8 项。

### V3 谱图（5 人日）
- `nmr-panel`：先 `live(file)`（读现有文件端点）+ fixture；归属高亮、峰表、多候选切换。
- `reaction-network`：保持只读；补 `source` 标识与"历史数据/示意"文案；不改数据来源。
- **DoD**：无谱图文件时 `empty` 态；fixture 徽标可见；网络仍是只读（不得出现提交/写入按钮）。

### V4 任务配置与编辑（54 人日）
- **向导全量**：把 `wizard.js` 的能力迁进 `task-config`，覆盖 **15 个 active 工作流**（当前仅 4 个 simple）：方法/基组目录投影、语义锁、资源与内存、校验、预检、提交回执。
- **草稿**：自动保存 / 恢复横幅 / 409 冲突 / 提交后删除（服务端 `/api/v1/drafts` 已就绪）。
- **编辑重算**：`job_editor.js` 全链——编辑草稿、预览、执行、coverage registry 提示。
- **结构来源**：`structure_source_picker.js` → 多来源/多帧/远程文件的来源选择面板与命令。
- **TS Mode**：`tsmode_editor.js` → `tsmode-panel`。
- **DoD**：15 个工作流各完成一次提交并拿到回执（本地真实或 `fake` 工作流）；编辑重算在同一任务目录完成 attempt+1；草稿跨刷新恢复；P01–P05 全部 ✅。

### V5 任务运维与系统面（38 人日）
- **队列运维**：pause / unpause / continue / rerun / purge / clone / move / rename / tags，接现有 v1 端点与 CAS 语义。
- **项目与任务组织**：新建 / 移动 / 重命名 / 删除 / 批量清除 / 管理标签。
- **任务详情**：阶段进度、实时日志、文件树、工件（`job-detail.js` + `files-logs.js`）。
- **系统与资源**：节点列表/状态/ping/bootstrap、软件发现、后端。
- **S2 键长扫描**弹窗（`scan-s2.js`）。
- **DoD**：8 个运维命令在真实本地任务上各跑一次且状态机正确；批量清除走 `purge_cascade` 语义（ANTI #13），不误删；P06–P10、P16 全部 ✅。

### V6 研究与证据面（34 人日）
- **机理构建**：`reaction-builder.js` → 反应坐标计划装配面板（复用测量/编辑能力，不重写几何）。
- **手工研究**：`science-question.js`（无模型提问入口）。
- **证据**：`scientific-evidence.js`（claim states）、`research-evidence-view.js`（平台证据投影）。
- **审批**：`research-approval.js`（持久审批卡 + 预算绑定）。
- **能力表单**：`capability-form.js`（schema 驱动）。
- **DoD**：无模型时能完成"提问 → 证据 → 审批"闭环且遵守只读/写入边界；P17、P19–P23 全部 ✅。

### V7 装配、对等验收与质量（18 人日）
- 三工作区装配：Molecular = `molecule-viewer` + `energy-profile` + `vibration-panel` + 编辑；Execution = 队列/配置/详情/文件；Research = agent + 证据 + 审批 + 网络。
- 统一选中态经 Shell 命令（`openStructure/openTask`），**不跨 iframe/DOM**。
- i18n 全键（zh/en）、主题变量、键盘可达、窄屏抽屉、性能（1k 帧虚拟化、渲染降采样）。
- **parity 门**：跑 §7 的 `acp2_parity.test.js`；**24 个功能面全部 ✅ 之前，经典页不得进入退役候选**。
- **DoD**：三工作区切换 ≤1 帧白屏；`run_all.js` 全量 + 浏览器套件全绿；`pytest tests/test_acp_api_next_entry.py` 全绿；无新增 `$3Dmol` 直呼、无新增 `fetch` 越层（grep 门）。

### V8 宿主接入（**推迟**，等 §6 的 BE-GAP + §8-C4 决策）
DSH 插件打包、`/u/{account}/panel/*`、v3 事件流、多用户隔离。v1 的插件打包/安装/兼容矩阵三章可在 git 历史（`0a290736`，`ACP_3.0_Frontend_Plan_20261031.md`）取回，作为该阶段输入。

---

### Track A 任务清单 — 老模块全面移植（63 项，190 人日）

> 每项 1–4 人日、可独立验收。"前置"里 `V0`/`A-00` 指 §5 的契约骨架，其余为同线依赖。
> **通用验收**（下表不再重复）：模块注册到 shell + 四态（empty/loading/ready/error）+ 对应 `acp2_*.test.js` + 至少一条浏览器用例；能量/帧类面板额外要求"只读"（ANTI #22）。
> 表中"验收要点"只写**该项特有**的那一条。

| ID | 任务 | 对应 | 前置 | 人日 | 验收要点 |
|---|---|---|---|---|---|
| A-00 | 视图模型 + 适配层 + fixture 骨架 | V0 | — | 3 | `acp.view.v1` normalize、三模式、四态 |
| A-01 | 向导骨架与步骤导航（工作流/方法/基组目录投影） | P01 | A-00 | 4 | 目录驱动，无硬编码退役 id |
| A-02 | 分子输入与来源（SMILES/文件/粘贴 → `resolve`/`embed`） | P01 | A-01 | 3 | 解析失败有可见原因 |
| A-03 | 电荷 / 自旋 / 多重度与奇偶校验 | P01 | A-01 | 2 | 非法组合就地拦截 |
| A-04 | 资源与内存 / 核数 / 队列（`memory_input`） | P01 | A-01 | 2 | 默认值来自服务端目录 |
| A-05 | 方法校验 + 预检指纹 | P01 | A-03 | 3 | 与提交侧指纹一致 |
| A-06 | 提交与回执（`Idempotency-Key` / `submission_id`） | P01 | A-05 | 2 | 重复提交不产生第二任务 |
| A-07 | 解锁其余 11 个 active 工作流的配置面板 | P01 | A-06 | 12 | 15 个全部可配、可提交 |
| A-08 | 提交检查弹窗（submit check） | P01 | A-05 | 2 | 列出阻塞项，不误报 |
| A-09 | 草稿：自动保存 / 恢复横幅 / 409 / 提交后删除 | P02 | A-06 | 3 | 刷新后草稿可恢复 |
| A-10 | 编辑草稿加载与字段回填 | P03 | A-06 | 3 | 字段与任务真实参数一致 |
| A-11 | 编辑预览（`edit-recalculate/preview`） | P03 | A-10 | 2 | 变更清单与实际执行一致 |
| A-12 | 编辑执行 + attempt+1 回执 + coverage 提示 | P03 | A-11 | 3 | 同目录原地重跑（ANTI #15） |
| A-13 | 结构来源列表（本地 / 远程 / 历史任务） | P04 | A-00 | 4 | COMPLETED 来源可见可引 |
| A-14 | 多帧选择与预览 | P04 | A-13 | 3 | 越界钳制到末帧 |
| A-15 | 引用注入（`structureRef` 桥接） | P04 | A-14 | 2 | 与 `molecule-viewer` 契约一致 |
| A-16 | TS Mode 编辑器（模式选择 / 频率来源 / 提交） | P05 | A-06 | 4 | 模式序号非位置序 |
| A-17 | 队列列表、筛选、状态徽标 | P06 | A-00 | 3 | 状态全表面同步（ANTI #12） |
| A-18 | pause / unpause / continue | P06 | A-17 | 2 | PAUSED 语义与后端一致 |
| A-19 | rerun（原地重跑）/ cancel | P06 | A-17 | 2 | cancel 顺序符合约定 |
| A-20 | purge（含批量清除二次确认） | P06 | A-17 | 3 | 走 `purge_cascade`（ANTI #13） |
| A-21 | clone / move / rename | P06 | A-17 | 3 | 目录名规则不被破坏 |
| A-22 | 项目 CRUD | P07 | A-17 | 3 | 删除前有依赖提示 |
| A-23 | 标签管理 | P07 | A-17 | 2 | 标签不写路径 |
| A-24 | 回收站与批量操作 | P07 | A-20 | 2 | 误删可恢复 |
| A-25 | 阶段进度（`stage_index/total/detail`） | P08 | A-17 | 2 | 与 `/jobs/{id}` 同源 |
| A-26 | 实时日志（SSE/轮询 + `irc/log-tail`） | P08 | A-17 | 3 | 断线可续、不丢尾部 |
| A-27 | 文件树与预览（本地 / 远程） | P08 | A-17 | 4 | 只读；大文件不卡 |
| A-28 | 工件清单与下载 | P08 | A-27 | 2 | 走 artifacts，不猜路径 |
| A-30 | 节点列表 / 状态 / ping / bootstrap | P10 | A-00 | 3 | 失败态明确 |
| A-31 | 软件发现与后端状态 | P10 | A-30 | 2 | 不探测即显示"未知" |
| A-32 | 结构目录与条目列表 | P11 | A-00 | 4 | 多源多帧分组正确 |
| A-33 | 单条目几何加载 + 3D 渲染 | P11 | A-32 | 4 | 唯一 3D 创建栈（ANTI #22） |
| A-34 | 样式预设 / 相机 / 视图状态 | P11 | A-33 | 2 | 刷新后视图可恢复 |
| A-35 | 叠加与 RMSD | P11 | A-33 | 3 | 映射未证实则禁测量 |
| A-36 | IRC 帧播放 | P11 | A-33 | 3 | 正/反向帧序正确 |
| A-37 | 窄屏抽屉与列表/检查器布局 | P11 | A-32 | 2 | 单列可用 |
| A-38 | 能量曲线渲染（复用 renderer，8 类视图） | P12 | A-00 | 4 | 与经典页逐点一致 |
| A-39 | 候选带 / 标注 / 选择联动 | P12 | A-38 | 4 | 点曲线定位条目 |
| A-40 | 保存为候选 | P12 | A-39 | 2 | 只保存候选、不提交（ANTI #22） |
| A-41 | 候选详情面板 | P15 | A-39 | 2 | 与候选清单同源 |
| A-42 | 频率表与模式选择 | P13 | A-33 | 2 | 模式编号与后端一致 |
| A-43 | 箭头与动画播放 | P13 | A-42 | 3 | 切任务无残留 rAF |
| A-44 | 热化学摘要 | P13 | A-42 | 2 | 缺字段写 warnings |
| A-45 | 测量（距离 / 角度 / 二面角） | P14 | A-33 | 3 | 二面角符号与 IUPAC 一致 |
| A-46 | 内坐标编辑与事务 | P14 | A-45 | 4 | 编辑可撤销、可放弃 |
| A-47 | 资产保存（`structure-assets`） | P14 | A-46 | 2 | 保存失败不丢编辑 |
| A-48 | S2 键长扫描弹窗 | P16 | A-06 | 2 | 默认不启 ScanTS |
| A-49 | 机理计划装配（步骤 / 角色） | P17 | A-06 | 6 | 与 Batch 契约一致 |
| A-50 | 路径结构视图与编辑 | P17 | A-49 | 4 | 复用 A-45/46 的测量与编辑 |
| A-51 | 与 PES / Batch 的衔接 | P17 | A-49 | 4 | 选点→Batch 有据可查 |
| A-52 | NMR 报告渲染（DP4/DP5、归属高亮） | P18 | A-00 | 3 | 无谱图时 empty 态 |
| A-53 | 多候选切换 | P18 | A-52 | 2 | 切换不串台（generation 守卫） |
| A-54 | 手工研究提问入口 | P19 | A-06 | 4 | 无模型可用 |
| A-55 | 科学证据（claim states） | P20 | A-54 | 3 | 解释与验证分离 |
| A-56 | 平台证据投影 | P21 | A-55 | 1 | 只读 |
| A-57 | 审批卡（预算 / `plan_digest` 绑定） | P22 | A-06 | 4 | 摘要不匹配即拒绝 |
| A-58 | 批准 / 拒绝 / 撤销 | P22 | A-57 | 2 | 不写执行事实 |
| A-59 | schema 驱动表单渲染与校验 | P23 | A-00 | 4 | 未知字段显式拒绝 |
| A-60 | 提交与错误映射 | P23 | A-59 | 2 | 错误码可读 |
| A-61 | 全键 i18n（zh/en） | P24 | A-00 | 5 | 1 673 条前缀组覆盖 |
| A-62 | 主题与窄屏 | P24 | A-61 | 2 | 继承 shell 主题变量 |
| A-63 | 键盘可达与 a11y | P24 | A-61 | 3 | 焦点顺序可走完 |

**合计 190 人日**（A-00 3 + 迁移 187），加 V7 的装配/集成 8 人日 = **Track A 全程 198 人日**。

---

### Track B 任务清单 — 新模块实现（13 项，95 人日）

> "新模块"＝**老 UI 从来没有**或**后端无产品**的能力。**不占 parity 门**，按阻塞项排期：只有前置解锁才开工，否则保持"未接入"。
> 判定依据见 §2.4 判定规则：占位/空壳不算老功能。

| ID | 新能力 | 阻塞 / 前置 | 人日 | 现在能否开工 |
|---|---|---|---|---|
| B-01 | 2D 结构绘制（Ketcher 集成 + CDXML/MOL/SMILES 往返 + 布局） | BE-GAP-03（无 depict、Ketcher 未 vendor） | 10 | ❌ 等选型 |
| B-02 | 2D ↔ 3D 联动（`depict2d` 或 Ketcher layout + `embed`） | B-01 | 4 | ❌ |
| B-03 | 项目级化学网络（连接性归并、节点/边、证据标注） | BE-GAP-02 + BE-GAP-10 | 12 | ❌ 等后端 |
| B-04 | 网络 ↔ 任务 ↔ 结构联动 | B-03 | 4 | ❌ |
| B-05 | 波函数 / 轨道（等值面、相位） | BE-GAP-05（无端点、无产品） | 8 | ❌ |
| B-06 | 通用轨迹播放（任意多帧产品） | 先用 structure-viewer 多帧；BE-GAP-07 只影响新轨迹产品 | 5 | ✅ 现在可做 |
| B-07 | 多实例 viewer（并排对比） | A-32/33 + `geometry_store` 工厂化 | 6 | ✅ A-33 后 |
| B-08 | 终端集成（DSH 原生，或只读 xterm 兜底） | 无（UI-06 可选） | 6 | ✅ 原型可做 |
| B-09 | DSH 插件打包 + 面板桥（token / 主题 / 尺寸） | §8-C4、C5 决策 + BE-GAP-09 | 10 | ❌ 等决策 |
| B-10 | `/u/{account}/panel/*` 路由 + 多用户隔离 | BE-GAP-09（基础设施 0%） | 8 | ❌ |
| B-11 | v3 适配器（SSE 事件流 + 增量刷新） | BE-GAP-01 | 6 | ❌ |
| B-12 | 研究 Agent 自动闭环（前端显示 + 审批） | 后端 RPH / 研究运行时 | 12 | ❌ |
| B-13 | 跨任务构象身份展示 | BE-GAP-11 | 4 | ❌ |

**现在真正可开工的只有 17 人日**（B-06 5 + B-07 6 + B-08 6）；其余 78 人日等后端或等决策。

### 两条线的编排

| 里程碑 | 内容 | 人日 | 交付后可做什么 |
|---|---|---|---|
| M1 | V0 + V1 | 23 | 看结构、看能量曲线（只读） |
| M2 | V2 + V3 | 31 | 帧/振动/编辑/谱图（只读） |
| M3 | V4 | 54 | **能配任务、能提交、能改参数重算** |
| M4 | V5 | 38 | **日常可用**：队列运维、详情、文件、节点 |
| M5 | V6 + V7 | 52 | 研究面闭环 + **parity 门达标**，经典页可进退役候选 |

- **1 人**：A 线顺序走 M1→M5，约 45 周；B 线只在 M1 后插 B-06/B-07（11 人日）。
- **2 人**：A 线 23 周左右；建议一人 V4（配置），一人 V5（运维）并行，V6 再合流。
- **B 线不得抢 A 线资源**：parity 未达标前，新模块只做"不依赖后端、不阻塞移植"的 B-06/B-07/B-08。

---

## 6. 后端缺口登记（待开发；前端已有降级）

> 触发规则：**只有某缺口成为某期验收的阻塞项时才开发**；否则保持 fixture/降级，不提前实现。

| ID | 缺口（已核实） | 影响 | 前端降级（现在就能交付） | 解锁条件 |
|---|---|---|---|---|
| BE-GAP-01 | 无 v3 端点（`/api/v3/{tasks,artifacts,events/stream}` 等，架构 §3.3 全为"建议路径"） | V8 阶段（宿主）、SSE 续传 | 用 v1/v2 轮询 + 适配层；`mode:"v3"` 预留 | 后端 WP0 冻结 v3 契约 |
| BE-GAP-02 | 无化学网络端点（`/api/v2/projects/{id}/chemical-network` 未实现；`structure_connectivity` 表与索引器未落地） | F7 现役网络 | 只读历史 mechanism 数据 + fixture；显式"历史/示意" | 连接性归并算法 + 索引器就绪 |
| BE-GAP-03 | 无 2D 坐标与 depiction；Ketcher 未 vendor | F8（非迁移项：老 UI 无此功能） | SMILES/文件预览；面板显式"未接入" | 选定 Ketcher 或 `depict2d` |
| BE-GAP-04 | 无 NMR 谱图数据端点（经典页读文件） | F6 | 文件端点 + fixture | 谱图契约冻结 |
| BE-GAP-05 | 无波函数/轨道端点（架构 §2.4 明示 ABSENT） | F11（老 UI 只有占位 tab） | 隐藏面板 + "未接入" | 科学侧定义轨道产品 |
| BE-GAP-06 | `TaskRowProjection` 缺 `stage_order / stage_progress` | F9 进度条准确性 | 读 v1 `stage_*` 字段 | 投影扩展或明确二选一 |
| BE-GAP-07 | 通用多帧端点缺失（仅 s2/irc 专用） | F2 轨迹 | 用 structure-viewer 多帧 XYZ | 轨迹产品契约 |
| BE-GAP-08 | 迁移号冲突：代码已用 **024/025/027**（026 按约定缺号），文档中的 024/025 提案已撞号 | 后端建表 | 与本计划无关（前端不建表） | 后端改用 **028+** |
| BE-GAP-09 | 多用户/宿主基础设施 0%（主机生命周期、acpfs、`/u/{account}` 反代） | V8 阶段 | 单用户本地入口 | WP1/WP4 交付 |
| BE-GAP-10 | 网络算法落点未定：目标形态提到 `acp_services/chemistry/`，但该目录**不存在**，后端计划也无对应工作包 | BE-GAP-02 前置 | 只读历史数据（不变） | 后端补 WP 并定落点 |
| BE-GAP-11 | 跨任务构象身份缺失（`structure_sources.py` 硬编码 `smiles=None`） | F9 跨任务构象表 | 单任务内展示 | 身份链路补齐 |
| BE-GAP-12 | MCP 工具数文档写"八工具"，实际未协商 10 / 协商后 10 / 消费 11 | 文档一致性 | 前端不依赖计数，按 `tools/list` | 文档校正 |

---

## 7. 测试与验收

| 层 | 命令 | 覆盖 |
|---|---|---|
| 模块单元 | `node frontend/js/module_tests/run_all.js`（可加名字过滤） | 现有 **55** 个 `*.test.js`（另 3 个浏览器用例 + 3 个运行辅助，共 61 文件）；新增每族 ≥8 项 |
| 浏览器 | `NODE_PATH=$(npm root -g) node frontend/js/module_tests/acp2_next_entry.browser.js http://127.0.0.1:PORT`（配 `next_entry_server.py`） | 三工作区、窄屏、fixture 徽标 |
| Python 侧 | `pytest tests/test_acp_api_next_entry.py -q` | `/next/` 路由与豁免 |
| 契约 | 新增 `acp2_view_model.test.js` | `acp.view.v1` 形状、四态、warnings、禁止静默填充 |
| 回归 pin | `acp2_layout.test.js`、`scene_lifecycle_matrix.test.js`、`test_frontend_sync_viewer_framing.py` | 布局契约、viewer 生命周期、framing 单一入口 |
| **对等（parity）** | 新增 `acp2_parity.test.js` | §2.4 的 24 个功能面：**模块注册 + 四态可用 + 测试存在 + 键组覆盖**，缺一即 fail；同时输出当前对等度 |

> **本轮基线（2026-10-31 实测）**：`566 passed / 0 failed / 1 skip`，exit 0。唯一 skip 是 `wizard_submit_payload`（需 `ACP_PYTHON=<装了 acp 的解释器>` 才跑 catalog 交叉校验）。开工前先记一次基线，避免把既有红当成本轮引入。

**总验收门（不依赖后端）**
1. 离线（无后端）用 `?view=fixture` 走完 10 个族的面板，四态均可复现；
2. 接真实实例（本地 run_root）时，F1–F5 各族的显示与原经典页**数值一致**；
3. 任一缺口被打开时，面板出现"数据不全/未接入"文案，绝不显示假数值；
4. 关闭适配层后，面板不产生任何直接网络调用（grep 门 + 浏览器断网用例）；
5. **功能对等（D5）**：§2.4 的 24 个功能面全部 ✅（模块注册 + 四态 + 测试 + 键组），否则经典页**不得下线**，也不得对外宣称"新 UI 已替代老 UI"。

---

## 8. 文档冲突与待决（本计划不擅自裁决）

| # | 冲突 | 证据 | 本计划处理 |
|---|---|---|---|
| C1 | 迁移号撞车：代码已用 024/025/027 | `src/acp_services/store/migrations.py:364/374/383` | 前端不建表；**BE-GAP-08** 交后端改 028+ |
| C2 | 宿主命名：`/u/{account}/dsh`（架构 §3.3）vs `/u/<tenant>/dsh`（拓扑 §2.2）；`workstations` vs `user_hosts` | 两份文档各自表述 | 前端只用"相对 base 的路径"，不硬编码前缀 |
| C3 | "前端只依赖 v3"（架构 §3.3）vs 目标形态实际依赖 v1/v2 | 架构 §3.3 与 §5 | 以**现有 v1/v2 为准**；v3 作为未来模式（§4） |
| C4 | 拓扑 §9：T2（DSH 为壳）为目标、T1 仅降级，`/next/` 三工作区"退役" | 拓扑 §9 vs 前端目标 §2.1、§7 | **D4**：视图层与宿主解耦；V1–V5 各阶段在两种宿主下都可运行 |
| C5 | DSH 嵌入能力全项未验证（无 postMessage/嵌入 SDK） | 目标形态 §2.1、§7 | 不进入 V1–V7；推迟到 V8 并先做 spike |
| C6 | Ketcher OCR/NOT_VERIFIED、WASM 体积未测 | 目标形态 §2.3、§6.5 | V8 推迟，选型决策前不动工 |
| C7 | 能量图两套形状不得拼合（`energy-graph` vs 退役 mechanism profile） | 目标形态 §2.4、§6.6 | 只实现 `energy-graph` 一条路径 |
| C8 | 后端 §1 称"迁移最后 023"、文档称 MCP"八工具" | 后端 §1；仓库为 027、工具 10/11 | **BE-GAP-12** 记录，前端不依赖 |
| C9 | 老 UI 有空壳入口：`data-tab="wavefunction"` 只有按钮，点击落到"功能开发中"占位（不是功能） | `molecule-view.js:275-340`、`viewer.coming_title` 文案 | 按 §2.4 判定规则：**占位不算功能面**，不列入 parity；新 UI 不复制空入口 |

---

## 9. 风险

1. **"部分可搬"高估**：`structure_viewer.js` 体积大、隐式依赖页面；每个族必须先做**小样移植**（1 个视图跑通）再全量，避免中途返工。
2. **fixture 长期化**：BE-GAP-02/03/04 若长期不落地，fixture 会变成"事实上的产品"。控制手段：fixture 徽标 + 每季度复核清单（§6 的"解锁条件"）。
3. **重复渲染实现**：曲线族只许 1 个渲染器、3D 只许 1 个创建栈；新增实现需在评审说明为何不能复用。
4. **性能**：1k 帧以上必须虚拟化（`structure_viewer.js` 已有阈值实现可参照），否则浏览器卡死。
5. **双入口债**：经典页与 Next 并存期只保留一个版本；每族迁移完成后在经典页标注"已迁移"，不再双写。
6. **对等功能被低估**：老 UI 约 **2.1 MB** JS（`workbench/features` 1.32 MB + 顶层模块 0.62 MB + i18n 0.20 MB）、**1 673** 条标签；V4–V6 合计 **126 人日**，是可视化（V1–V3 共 51 人日）的约 2.5 倍。只做完可视化就宣称"新 UI 可用"等于砍功能——按 D5，parity 未达标前经典页保持一等入口。

---

## 10. 与其它文档的关系

| 文档 | 关系 |
|---|---|
| [ACP 3.0 架构](ACP_3.0_Architecture_20261031.md) | 上位；§3 契约不变，本计划不引入新契约 |
| [ACP 3.0 后端计划](ACP_3.0_Backend_Plan_20261031.md) | 并行；本计划把其未实现项登记为 §6 |
| [前端目标形态](ACP_Frontend_Target_Workbench_Plan_20261030.md) | 视觉与布局目标不变；本计划**降低**其 V1–V5 对后端的依赖 |
| [UI-00 契约草案](ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md) | 模块契约（descriptor/factory/ModuleInstance）继续有效，本计划复用 |
| [UI-01/02/03A](ACP_Frontend_UI01_UI02_UI03A_Closure_20261030.md)、[UI-03/04/05](ACP_Frontend_UI03_UI04_UI05_Closure_20261030.md) 收口 | 已交付能力基线；本计划只做增量 |
| [能量/轨迹查看器 DevDoc](../current/ACP_Energy_Trajectory_Viewer_DevDoc.md)、[结构查看器 DevDoc](../current/ACP_Structure_Viewer_DevDoc.md) | 帧/视图契约权威；`VIEW_REGISTRY` 与 `to_node()/to_annotation()` 不得绕过（ANTI #21） |
