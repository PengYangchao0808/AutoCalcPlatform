# ACP 3.0 前端可视化适配计划（v2 · 可视化先行）

**日期**：2026-10-31（v2 改写）· **性质**：实施计划（**前端**；不含后端实现，后端缺口见 §6 登记）
**上位**：[ACP 3.0 架构与共享契约](ACP_3.0_Architecture_20261031.md) · [前端目标形态](ACP_Frontend_Target_Workbench_Plan_20261030.md) · [多用户/DSH 拓扑](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)
**并行**：[ACP 3.0 后端实现计划](ACP_3.0_Backend_Plan_20261031.md)（本计划**不等待**它）
**取代**：本文 v1（`Workbench on DSH` 版）。v1 的宿主假设、三工作区与插件打包内容未删除，改为 §5.V6 的**推迟**阶段。

---

## 0. v2 的四个决定（与 v1 的差别）

| # | 决定 | 影响 |
|---|---|---|
| **D1** | **可视化先在 ACP 自己的壳里做完**（`/next/` + 经典 `/`），不依赖 DSH 安装、不依赖 v3 API | 现在就能开工、能验收；DSH 只作为后续宿主选项 |
| **D2** | 面板只认**视图模型**（§3），数据由**适配层**（§4）喂：`live`（现有 v1/v2）/`fixture`/`v3`（以后） | 后端到位时**只换适配器**，面板不改 |
| **D3** | 后端缺口**冻结为待开发清单**（§6），每个缺口都给出前端降级态；**不伪造数据**，fixture 必须可见 | 前端不会被阻塞；也不会假装能力已存在 |
| **D4** | 宿主形态（拓扑文档 T1 自建壳 / T2 DSH 为壳）**不在本计划裁决**；可视化层与宿主解耦，两种组装都能接 | 避免与 [拓扑 §9](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md) 的结论冲突（见 §8-C4） |

**结论**：本计划交付"**在任何宿主里都能用的可视化能力 + 可验收的降级契约**"，不等后端。

---

## 1. 范围

**做**：3D 结构、帧序列播放、能量曲线（含候选带）、振动、结构叠加、NMR 谱、化学网络、任务/工件预览、研究证据视图；统一视图模型与四态；面板装配；模块/浏览器/契约测试。

**不做（本计划内）**：DSH 插件打包与安装、`/u/{account}/panel/*` 反代、v3 端点实现、Ketcher 集成、波函数/轨道、多用户隔离、终端。

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
| F8 | 2D 结构绘制 | — | **无端点、无 Ketcher** | 未交付 | **推迟**（BE-GAP-03），用 SMILES/文件预览兜底 |
| F9 | 属性/表格（构象、频率、能量） | 原生表格 | detail / artifacts | `exec-detail` 有工件清单 | **扩**：结构化表格 + 排序/筛选 |
| F10 | 工件预览（文本/日志/图片/结构） | 原生 + 结构预览 | files / artifacts / prepared | 部分 | **扩** `exec-detail` 为通用预览 |
| F11 | 波函数/轨道 | — | **无端点** | 无 | **推迟**（BE-GAP-05），显式"未接入"态 |
| F12 | 研究证据视图 | 原生卡片 | v2 research/science | `agent-workspace` 已交付只读首片 | **保留**，补 fixture/real 标识 |

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

> 单位为人日；依赖关系为串行主干，阶段 V1/V2 可并行。

### V0 契约与骨架（3 人日）
- `view_model.js`（normalize + 四态 + warnings）、`view_source.js`（三模式，`v3` 抛未实现）、fixture 目录与 2 个样本族。
- 把 `molecule-viewer` 改为经适配层取数（行为不变，回归全绿）。
- **DoD**：`node frontend/js/module_tests/run_all.js acp2_module_viewer` 通过；新增 `acp2_view_model.test.js`（normalize/四态/warnings/fixture 标记）≥10 项；`?view=fixture` 能驱动 viewer。

### V1 结构与能量（8 人日）
- `energy-profile`：8 类视图共用；候选带/标注、双轴、能量单位、空数据与"不可用"视图（`unsupported`）。
- `molecule-viewer` 扩：MOL/SDF 解析（纯函数自写，参照 README §3 白名单法）、多帧选择、overlay + RMSD。
- **DoD**：能量曲线在 `scan / optimization / conformer / irc / neb` 五个真实任务上与原经典页**逐点一致**（同 job 对比截图或数值断言）；`acp2_energy_profile.test.js` ≥12 项；无任务时 `empty` 态不报错。

### V2 帧序列与振动（8 人日）
- `frames-player`：步进/播放/暂停/速度/循环、`generation` 守卫（切任务丢弃迟到结果）、`suspend/resume` 释放渲染。
- `vibration-panel`：模式列表、箭头播放/停止、位移幅度、热化学摘要（有则显示，无则 warned）。
- **DoD**：IRC 正/反向播放与经典页帧序一致；连续切换 3 个任务无 viewer 泄漏（沿用 `scene_lifecycle_matrix.test.js` 的计数断言）；`acp2_frames_player.test.js`、`acp2_vibration_panel.test.js` 各 ≥8 项。

### V3 谱图与网络（6 人日）
- `nmr-panel`：先 `live(file)`（读现有文件端点）+ fixture；归属高亮、峰表、多候选切换。
- `reaction-network`：保持只读；补 `source` 标识与"历史数据/示意"文案；不改数据来源。
- **DoD**：无谱图文件时 `empty` 态；fixture 徽标可见；网络仍是只读（不得出现提交/写入按钮）。

### V4 面板装配（6 人日）
- Molecular = `molecule-viewer` + `energy-profile` + `vibration-panel`；Execution = `task-explorer` + `exec-detail`（F9/F10）+ `reaction-network`；Research = `agent-workspace`。
- 统一选中态经 Shell 命令（`openStructure/openTask`），**不跨 iframe/DOM**；布局沿用现有 `layout-manager`。
- **DoD**：三工作区切换 ≤1 帧白屏；同一结构在 Molecular 选中后 Execution 视图可定位；`acp2_next_entry.browser.js` 全绿（含窄屏单列）。

### V5 质量与发布（5 人日）
- i18n（zh/en 全键）、主题变量、键盘可达（roving tabindex 复用）、窄屏抽屉、性能（1k 帧虚拟化、渲染降采样）。
- **DoD**：`run_all.js` 全量 + 浏览器套件全绿；`pytest tests/test_acp_api_next_entry.py` 全绿；无新增 `$3Dmol` 直呼、无新增 `fetch` 越层（grep 门）。

### V6 宿主接入（**推迟**，等 §6 的 BE-GAP + §8-C4 决策）
DSH 插件打包、`/u/{account}/panel/*`、v3 事件流、多用户隔离。v1 的插件打包/安装/兼容矩阵三章可在 git 历史（`0a290736`，`ACP_3.0_Frontend_Plan_20261031.md`）取回，作为该阶段输入。

---

## 6. 后端缺口登记（待开发；前端已有降级）

> 触发规则：**只有某缺口成为某期验收的阻塞项时才开发**；否则保持 fixture/降级，不提前实现。

| ID | 缺口（已核实） | 影响 | 前端降级（现在就能交付） | 解锁条件 |
|---|---|---|---|---|
| BE-GAP-01 | 无 v3 端点（`/api/v3/{tasks,artifacts,events/stream}` 等，架构 §3.3 全为"建议路径"） | V6 阶段（宿主）、SSE 续传 | 用 v1/v2 轮询 + 适配层；`mode:"v3"` 预留 | 后端 WP0 冻结 v3 契约 |
| BE-GAP-02 | 无化学网络端点（`/api/v2/projects/{id}/chemical-network` 未实现；`structure_connectivity` 表与索引器未落地） | F7 现役网络 | 只读历史 mechanism 数据 + fixture；显式"历史/示意" | 连接性归并算法 + 索引器就绪 |
| BE-GAP-03 | 无 2D 坐标与 depiction；Ketcher 未 vendor | F8 | SMILES/文件预览；面板显式"未接入" | 选定 Ketcher 或 `depict2d` |
| BE-GAP-04 | 无 NMR 谱图数据端点（经典页读文件） | F6 | 文件端点 + fixture | 谱图契约冻结 |
| BE-GAP-05 | 无波函数/轨道端点（架构 §2.4 明示 ABSENT） | F11 | 隐藏面板 + "未接入" | 科学侧定义轨道产品 |
| BE-GAP-06 | `TaskRowProjection` 缺 `stage_order / stage_progress` | F9 进度条准确性 | 读 v1 `stage_*` 字段 | 投影扩展或明确二选一 |
| BE-GAP-07 | 通用多帧端点缺失（仅 s2/irc 专用） | F2 轨迹 | 用 structure-viewer 多帧 XYZ | 轨迹产品契约 |
| BE-GAP-08 | 迁移号冲突：代码已用 **024/025/027**（026 按约定缺号），文档中的 024/025 提案已撞号 | 后端建表 | 与本计划无关（前端不建表） | 后端改用 **028+** |
| BE-GAP-09 | 多用户/宿主基础设施 0%（主机生命周期、acpfs、`/u/{account}` 反代） | V6 阶段 | 单用户本地入口 | WP1/WP4 交付 |
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

> **本轮基线（2026-10-31 实测）**：`566 passed / 0 failed / 1 skip`，exit 0。唯一 skip 是 `wizard_submit_payload`（需 `ACP_PYTHON=<装了 acp 的解释器>` 才跑 catalog 交叉校验）。开工前先记一次基线，避免把既有红当成本轮引入。

**总验收门（不依赖后端）**
1. 离线（无后端）用 `?view=fixture` 走完 10 个族的面板，四态均可复现；
2. 接真实实例（本地 run_root）时，F1–F5 各族的显示与原经典页**数值一致**；
3. 任一缺口被打开时，面板出现"数据不全/未接入"文案，绝不显示假数值；
4. 关闭适配层后，面板不产生任何直接网络调用（grep 门 + 浏览器断网用例）。

---

## 8. 文档冲突与待决（本计划不擅自裁决）

| # | 冲突 | 证据 | 本计划处理 |
|---|---|---|---|
| C1 | 迁移号撞车：代码已用 024/025/027 | `src/acp_services/store/migrations.py:364/374/383` | 前端不建表；**BE-GAP-08** 交后端改 028+ |
| C2 | 宿主命名：`/u/{account}/dsh`（架构 §3.3）vs `/u/<tenant>/dsh`（拓扑 §2.2）；`workstations` vs `user_hosts` | 两份文档各自表述 | 前端只用"相对 base 的路径"，不硬编码前缀 |
| C3 | "前端只依赖 v3"（架构 §3.3）vs 目标形态实际依赖 v1/v2 | 架构 §3.3 与 §5 | 以**现有 v1/v2 为准**；v3 作为未来模式（§4） |
| C4 | 拓扑 §9：T2（DSH 为壳）为目标、T1 仅降级，`/next/` 三工作区"退役" | 拓扑 §9 vs 前端目标 §2.1、§7 | **D4**：视图层与宿主解耦；V1–V5 各阶段在两种宿主下都可运行 |
| C5 | DSH 嵌入能力全项未验证（无 postMessage/嵌入 SDK） | 目标形态 §2.1、§7 | 不进入 V1–V5；推迟到 V6 并先做 spike |
| C6 | Ketcher OCR/NOT_VERIFIED、WASM 体积未测 | 目标形态 §2.3、§6.5 | V8 推迟，选型决策前不动工 |
| C7 | 能量图两套形状不得拼合（`energy-graph` vs 退役 mechanism profile） | 目标形态 §2.4、§6.6 | 只实现 `energy-graph` 一条路径 |
| C8 | 后端 §1 称"迁移最后 023"、文档称 MCP"八工具" | 后端 §1；仓库为 027、工具 10/11 | **BE-GAP-12** 记录，前端不依赖 |

---

## 9. 风险

1. **"部分可搬"高估**：`structure_viewer.js` 体积大、隐式依赖页面；每个族必须先做**小样移植**（1 个视图跑通）再全量，避免中途返工。
2. **fixture 长期化**：BE-GAP-02/03/04 若长期不落地，fixture 会变成"事实上的产品"。控制手段：fixture 徽标 + 每季度复核清单（§6 的"解锁条件"）。
3. **重复渲染实现**：曲线族只许 1 个渲染器、3D 只许 1 个创建栈；新增实现需在评审说明为何不能复用。
4. **性能**：1k 帧以上必须虚拟化（`structure_viewer.js` 已有阈值实现可参照），否则浏览器卡死。
5. **双入口债**：经典页与 Next 并存期只保留一个版本；每族迁移完成后在经典页标注"已迁移"，不再双写。

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
