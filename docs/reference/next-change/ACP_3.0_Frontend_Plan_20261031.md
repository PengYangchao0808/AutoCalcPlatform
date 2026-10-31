# ACP 3.0 前端兼容与实施计划（Workbench on DSH）

日期：2026-10-31
配套：[ACP 3.0 架构与共享契约](ACP_3.0_Architecture_20261031.md) · [后端实现计划](ACP_3.0_Backend_Plan_20261031.md) · [前端目标形态](ACP_Frontend_Target_Workbench_Plan_20261030.md)
范围：**前端**（DSH UI 插件、三工作区、ACP 化学面板、旧入口兼容）。后端契约只依赖主文档 §3 与后端计划 §4。

---

## 0. 定位与边界

- **DSH Web 是每个用户的首要交互宿主**：负责 Agent 会话、模型、终端、文件、插件容器与通用交互。
- **ACP 化学面板保持独立**：2D 绘制、3D/波函数、化学网络、任务配置/详情以 ACP 插件/iframe 微前端进入 DSH，不把化学逻辑写进 DSH 核心。
- **手动计算不依赖 LLM**：没有模型或 DSH Agent 不可用时，Molecular/Execution 仍可直接调用 ACP API 提交与查看任务。
- 前端不持有第二套任务/文件权威：只消费 ACP v3 API/SSE；DSH 的 Session/Storage 不是任务真值。

---

## 1. 现状与可复用件

| 资产 | 现状 | 复用方式 |
|---|---|---|
| 经典工作台 `frontend/ACP_Workbench_v2.html` | 全功能单页（向导/能量/结构/文件/日志） | 迁移期保留；抽取共享控制器/渲染器 |
| Next 入口 `frontend/next/` | Shell + 模块（viewer/task-explorer/task-config/exec-detail/agent/reaction-network） | **先以 iframe 面板复用**，成熟后再考虑原生插件 |
| `frontend/js/visualization/energy_chart_renderer.js` | 已按 `init(deps)` 抽取、23 导出、已有测试 | 直接用于能量曲线面板 |
| `frontend/js/structure_editor.js` | 3D 内坐标编辑纯函数 | 抽到共享 controller，供 3D/调整面板 |
| `frontend/js/workbench/features/files-logs.js` | 文件树/日志数据层 controller-ready | 抽 controller；渲染层新写 |
| Ketcher | standalone iframe、`window.ketcher`、CDXML/CDX、Indigo WASM、OCR | 2D 面板直接嵌 |
| ACV/3D viewer | `ACPVisualization.scene` + framing | 3D/波函数面板复用 |
| DSH 原生 | Conversation、Sidebar、Terminal、Files、Layout slots | Agent/终端/文件直接用 DSH，不重造 |

DSH 插件事实（已核实）：插件是 npm bundle，`package.json` 的 `dsh.bundle.patch` 指向 YAML loader patch；安装后进入 `profiles/<name>/package.json` 的 `dsh.profile.bundles`；安装 `dsh plugin --profile rph add <pkg> --registry=<private>`；市场是可选实验 bundle + 用户配置索引，默认不挂载；**没有官方插件脚手架**，模板由 ACP 自维护。

---

## 2. 目标 UI 结构

```
DSH Web（宿主：会话/终端/文件/布局）
└── ACP Workbench 插件（acp-panels）
     ├── Research   : RPH Agent（DSH 原生 Conversation）+ 化学网络（ACP）+ 项目导轨
     ├── Molecular  : 2D 绘图（Ketcher）+ 3D/波函数（ACV）+ 属性（构象/能量/振动…）
     └── Execution  : 任务配置（ACP）+ 任务/进度（ACP）+ 文件（DSH/ACP）+ 终端（DSH）+ 远程动作
```

| 工作区 | 模块 | 归属 | 实现 |
|---|---|---|---|
| Research | RPH Agent 会话 | DSH 原生 + RPH bundle | DSH Conversation |
| Research | 化学反应/研究网络 | ACP | iframe 面板（复用 `chemical-network`） |
| Research | 项目/分子导轨 | ACP | iframe 面板（`project-molecules`） |
| Molecular | 2D 绘制 | ACP | Ketcher iframe + ACP 工具栏 |
| Molecular | 3D/波函数 | ACP | ACV viewer（iframe/组件） |
| Molecular | 结构属性 | ACP | `molecule-properties`（能量曲线先行） |
| Execution | 任务配置 | ACP | 复用 `task-config` |
| Execution | 任务列表/进度 | ACP | 扩展 `task-explorer` + v3 事件 |
| Execution | 文件 | DSH 原生 + ACP 只读 | DSH Files；ACP 面板兜底 |
| Execution | 终端 | DSH 原生 | 不重造 PTY |
| Execution | 远程/主机 | ACP | `remote-actions` |

> DSH 的 Sidebar/Slots 负责**容器与导航**；ACP 面板负责**化学语义**。二者通过 iframe URL（`/u/{account}/panel/...`）与 postMessage/URL 参数握手，不共享 DOM。

### 2.1 现有代码 → 目标面板

| 目标面板 | 现有代码 | 首期复用方式 |
|---|---|---|
| RPH Agent | DSH 原生 Conversation + `rph-dsh-bundle` | 不写 ACP 代码 |
| 化学网络 | `frontend/js/modules/reaction-network` + 新 `chemical-network` | iframe 面板 |
| 项目/分子导轨 | 新 `project-molecules` | iframe 面板 |
| 2D 绘制 | Ketcher vendored | iframe + ACP 工具栏 |
| 3D/波函数 | `molecule-viewer` + `js/visualization` | iframe 面板 |
| 结构属性/能量 | `energy_chart_renderer` + `structure_editor` 纯函数 | iframe 面板 |
| 任务配置 | `modules/task-config` | iframe 面板 |
| 任务/进度 | `modules/task-explorer` + v3 事件 | iframe 面板 |
| 文件 | DSH Files；ACP `file-browser` 兜底 | 原生优先 |
| 终端 | DSH Terminal | 原生，不自建 PTY |
| 远程/主机 | 新 `remote-actions` | iframe 面板 |
| 安装引导 | 新 wizard（ACP 门户） | ACP 页面 → 跳 DSH |

---

## 3. 插件打包与安装

- `dsh-plugins/acp-panels/`：ACP 官方 bundle
  - `package.json`：`name=@acp/dsh-panels`、`version`、`engines.dsh`、`dsh.bundle.patch=./cordis.patch.yml`；
  - `cordis.patch.yml`：插入客户端入口（slots/sidebar 面板）+ 可选 host service（ACP API 代理）；
  - 面板内容：iframe 指向 ACP `/u/{account}/panel/{tasks,network,structure,config}`，或 DSH 原生 slot 组件。
- 安装：onboarding 预装（不走市场）；市场只做发现：
  - `dsh plugin --profile rph add @acp/dsh-panels --registry=https://<controller>/npm`；
  - 升级 = 卸载再装（DSH 不支持原地升级）；版本与 `engines.dsh` 锁定。
- 私有索引：`https://<controller>/plugins/index.json`（format 1），只列 ACP bundle；非管理员不能添加任意索引。
- 审批：安装需管理员/策略批准；插件包按代码审计；不可信插件不得访问集群凭据/跨账户文件。

### 3.1 社区插件参考与策略

| 项目 | 参考点 | 采用策略 |
|---|---|---|
| DSH Better Sidebar | 文件树、编辑器、终端、任务面板与可扩展标签页 | **优先验证复用**文件/终端 UI；化学面板仍自研 |
| DSH SSH 插件 | 远端 Workspace、SSH 主机配置、远端文件工具路由 | 仅作远程工作区原型；**不当 HPC 调度底座** |
| DSH Desktop | Web UI + Host + 终端 + 插件的桌面壳 | 参考桌面壳；首期以 Web 为主，不做强制依赖 |
| 官方插件市场规范 | JSON Index、版本/兼容/安装参数 | 复用安装/版本机制；ACP 增加可信白名单 |

**策略**：发现开放、安装审批、权限隔离。社区插件被收录 ≠ 安全审计；ACP 连接 HPC 账户/SSH 密钥/科研数据，默认只允许 ACP 私有索引 + 管理员批准的插件。

### 3.2 安装引导（与后端 onboarding 对齐）

1. 创建 ACP 工作环境（Local / Hosted；Home、项目目录、DSH Profile）。
2. 配置 AI 模型（DSH 模型页/Provider/凭据；验证连接；**可跳过**）。
3. 连接计算资源（本地 CCCP / SSH HPC；软件与调度器探测）。
4. 初始化 RPH（固定版本 Bundle/Skills/ACP MCP Bridge）。
5. 创建第一个项目（ChemDraw/CDXML、分子文件或手动绘图）。

前端只负责向导 UI 与状态展示；实际写入由后端/Agent Host 完成。跳过模型配置后进入纯手动 ACP，所有 Molecular/Execution 功能仍可用。

---

## 4. 兼容矩阵

| 场景 | 迁移期行为 | 完成态 |
|---|---|---|
| 旧 `/next/` | 保留 + 顶部「新工作台」入口 | 退役，重定向到 `/u/{account}/dsh/` |
| 经典页 `/` | 保留（管理员/回退） | 仅管理员/迁移工具 |
| 旧 API `/api/v1|v2` | 继续可用 | v3 为主，旧面保留一个周期 |
| 深链 `?job=`/`?task=` | 映射到 v3 `task_id` | 统一 `/u/{account}/dsh/?task=...` |
| 无模型/DSH 不可用 | 手动面板直连 ACP API | 同左（底线） |
| 浏览器/APP | 同一 URL；APP WebView | 同一 URL；不存 DSH token |
| 语言/主题 | zh/en + 深色 | 同左；面板继承 DSH 主题变量 |
| 窄屏 | 单列降级 | 同左 |

---

## 5. 工作包（可并行）

### FE-0 插件骨架与宿主集成（2–3 周）
- `dsh-plugins/acp-panels` 最小 bundle：一个 slot + 一个 iframe 面板；本地 profile 加载验证。
- `/u/{account}/panel/*` 路由壳（ACP 侧静态路由）；登录/租户校验由后端反代。
- 主题/语言桥：读取 DSH 当前主题与语言，传给 iframe（URL/`postMessage`）。

### FE-1 Research 工作区（3–4 周）
- Agent：DSH 原生 Conversation + RPH bundle（不在前端重造聊天）。
- 化学网络：复用现有 `chemical-network`/`reaction-network` 渲染，接 v3 `/projects/{id}/network`；节点点击 → 3D/2D/任务。
- 项目导轨：`project-molecules` iframe；与网络共享选中态（通过 ACP URL/事件，不跨 iframe DOM）。

### FE-2 Molecular 工作区（4–6 周）
- Ketcher iframe：CDXML/CDX/MOL/SMILES 往返；`init` 后取句柄；脏标记；保存为候选/新建任务预填。
- 3D：ACV viewer（结构/轨迹/振动）；波函数后续。
- 属性：构象列表、能量曲线（`energy_chart_renderer` + `/energy-graph`）、振动；轨道占位。
- 2D↔3D：`depict2d`/`molecule/embed`；未保存编辑草稿。

### FE-3 Execution 工作区（4–6 周）
- 任务配置：复用 `task-config`，接 v3 tasks/submit（幂等键）。
- 任务/进度：扩 `task-explorer`，消费 `/api/v3/events/stream`；阶段/attempt/重试。
- 文件：优先 DSH Files；ACP `file-browser` 兜底（只读结果 + 下载/预览）。
- 终端：DSH 原生；ACP 只提供「打开终端/命令入口」。
- 远程/主机：节点选择、远程文件、调度入口（v3）。

### FE-4 共享组件（3–4 周）
- `structure_tools_controller`（测量/选择/编辑意图；复用 `geometry_store` + `structure_editor`）。
- `energy_profile`（已有 renderer，不新增第三套）。
- `artifact_viewer`（日志/表格/结构预览；遵循 ACP-VFS 可用性状态）。
- 去掉 Next 里重复的测量数学。

### FE-5 兼容与迁移（2–3 周）
- 双入口开关；`/next/` → DSH 深链映射；旧 API fallback；无模型模式。
- 经典页回退；迁移提示与一次性引导。

### FE-6 质量与发布（2–3 周）
- i18n（zh/en）、主题、a11y（键盘/焦点）、窄屏。
- 测试：Node 模块测试、浏览器用例、DSH 插件 smoke、视觉回归。
- 发布：插件版本 + 私有索引；回滚到旧入口。

---

## 6. 与后端的契约（并行开发用）

- 只依赖主文档 §3.3/§3.4 的 v3 API/事件；**不直接读数据库/文件系统**。
- 工件：`artifact_id` + `availability` + `last_location`；归档/恢复走 API，不猜测路径。
- 任务：`task_id/attempt/status/stage`；提交幂等键由前端生成并复用。
- 研究：`research_run_id` + 事件游标；前端只展示，不写研究状态。
- Mock 策略：后端未就绪时用 fixture（与 v3 schema 同形），fixture 必须有可见标识，不冒充真实数据。

---

## 7. 测试与验收

| 层 | 用例 |
|---|---|
| 单元 | 插件 manifest/patch、iframe 握手、主题/语言桥、深链解析、无模型模式 |
| 模块 | 网络/2D/属性/任务/文件各面板的 loading/empty/error/denied 四态 |
| 浏览器 | 三工作区切换、Ketcher 往返、任务提交回执、SSE 断线续传、刷新恢复 |
| DSH 集成 | profile 加载插件、slot 渲染、终端/文件原生、插件卸载回滚 |
| 隔离 | 两个 account 的 iframe URL/token/文件互不可见 |
| 兼容 | `/next/` 深链映射、旧 API fallback、经典页回退 |

验收门：
1. 无模型时能画分子、配任务、提交、看结果；
2. DSH 重启后 Research 会话与任务从 ACP 状态恢复；
3. 插件安装/卸载不影响其他账户；
4. 旧入口回退可用，数据不回滚。

---

## 8. 发布与回退

- 插件版本与 DSH 版本一起登记；私有索引固定版本；灰度账户。
- 回退：关闭 `acp-panels` 插件 → 回到 `/next/`/经典页；插件卸载不影响 ACP 任务。
- 数据：前端回退不回滚任务/工件；只切入口。

---

## 9. 风险与决策

1. **DSH 插件无官方脚手架**：`dsh.bundle.patch` 自维护，需固定模板与升级测试。
2. **iframe 微前端**：首期快，但跨 iframe 状态/焦点/快捷键需约定；成熟后再评估原生组件。
3. **DSH 版本漂移**：slot 名/主题变量/插件 API 可能变；锁版本 + 兼容层。
4. **Ketcher 体积/WASM**：首屏与离线缓存需实测；CDXML 语义逐项验证。
5. **无模型模式**：必须与模型路径同等测试，避免“没配模型就不能用”。
6. **旧入口维护成本**：双入口只保留一个版本周期，设定退役日期。
7. **APP**：WebView 加载同一 URL；不存 token；离线只缓存壳。
