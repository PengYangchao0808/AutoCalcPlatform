# ACP 2.0 前端升级 UI-00 → UI-03A 收口记录

日期：2026-10-30
性质：实现收口与证据记录；基线 HEAD `484764f6`（main），本轮全部改动未提交（工作区待审）。
上游方案：`/mnt/c/Users/彭扬超/Documents/ChatGPT/Auto calculation platform/ACP_2.0_前端同步升级开发方案_20261029.md`
配套文档：UI-00 基线 `ACP_Frontend_UI00_DSH_Capability_Baseline_20261030.md`、契约草案 `ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md`、设计规格 `ACP_Frontend_UI01_Shell_Design_20261030.md`（已随实现修订 7 处）、B 车道契约请求 `acp2-contracts/experience/requests/B-req-19.md`。

## 1. 交付范围（对照方案 §6 分期）

| 阶段 | 状态 | 说明 |
|---|---|---|
| UI-00 基线 + DSH 核验表 + 契约草案 | **已交付** | DSH 全部运行时能力 = NOT_VERIFIED/FAIL（客户端未安装、TS 未编译），降级路径已写明 |
| UI-01 Shell | **已交付** | 实验入口 `/next/`（server 纯增量路由+豁免）、registry/loader/context/commands/layout/recipes/i18n、mock 模块、局部错误边界、Alt+1/2/3 + roving tabindex |
| UI-02 Viewer + Explorer | **已交付** | 两模块可在**不加载旧整页 bootstrap** 的入口运行；只读；选择 token 防串台；控制器工厂化保留兼容单例 |
| UI-03A Agent 只读首片 | **已交付** | 研究摘要/计划时间线/证据卡；解释 vs 验证强制二分；fixture 与真实数据标识分离；无 DSH 时为正常只读态 |
| UI-03B DSH 受控交互 | **NOT_VERIFIED（阻塞）** | 需真实 DSH 安装 + 模型 + 闭环验证（见 UI-00 核验表） |
| UI-04 网络 + 2D / UI-05 组装深化 / UI-06 可选能力 | 未启动 | 按方案门禁分期 |

## 2. 文件清单

**新增（按区）**
- 入口与服务：`frontend/next/index.html`（真实 Shell 入口，绝对路径资源引用）；`src/acp/api/server.py` 增量（`/next/` 挂载+路由+豁免前缀）；`.gitignore` +`!frontend/next/*.html`；`tests/test_acp_api_next_entry.py`
- Shell：`frontend/js/shell/{i18n,workspace-context,module-registry,module-loader,commands,layout-manager,recipes,bootstrap}.js` + `mock/{mock-alpha,mock-beta}.js`；`frontend/css/shell/{shell,agent}.css`
- 模块：`frontend/js/modules/{molecule-viewer,task-explorer,agent-workspace}/{module.js,README.md}`；`frontend/css/modules/{molecule-viewer,task-explorer}.css`
- 测试：`frontend/js/module_tests/acp2_{shell_registry,shell_loader,shell_context,shell_bootstrap,module_viewer,module_task_explorer,module_agent}.test.js`（55 项新增）+ `acp2_next_entry.browser.js` + `next_entry_server.py`（隔离服务启动器）
- 文档：UI-00 ×2、设计规格、本收口记录、B-req-19

**修改（最小增量，共 4 个代码文件）**
- `js/workbench/controllers/{task_session,task_list}_controller.js`：加 `create*()` 工厂 + 保留兼容单例（旧测试未改一字全绿）
- `js/workbench/features/molecule-view.js`：**零改动**（`#viewer-library` null 守卫经核实本就存在）
- `css/workbench/queue-detail.css`：+1 行 `.right-col > .queue-panel:not(.collapsed){min-height:200px}`（HEAD 既有缺陷修复，见 §4）
- `ACP_Workbench_v2.html` / `js/client/**` / `js/visualization/**` / `compat/shared-state.js` / `bootstrap.js`：**零改动**

## 3. 行为对照

- **旧入口**：脚本清单、功能、测试 pin 全部不变；唯一变化是上述 1 行 CSS 地板——修复"无任务数据时队列面板被 flex 收缩至 0px、新建项目按钮不可点"的 HEAD 既有缺陷（浏览器回归第 1 场景此前必挂，现 8/8）。该修复不违背 `acp2_layout.test.js` pin 的"研究面板不得内滚"契约。
- **新入口**：三工作区（Research=agent+viewer+tasks / Molecular=viewer+config 占位 / Execution=tasks+exec-detail 占位）；单活动 viewer 以 DOM 迁移跨配方不重建；导航命令 `openStructure/openTask/openEvidence/openProposal` 解析到面板并切换工作区聚焦（提供/绑定标签已对齐 descriptor，含跨工作区 openTask 断言）；语言切换共享 `acp-lang` 且缺译文守卫防打碎模块文案；`/next/` 与 `/` identity 豁免同权。
- **数据面**：任务与结构全部经既有 `ACPClient`（taskView/research/science 只读端点）与 v1 files 端点，无第二套缓存、无新端点、无写操作（UI-03A 明确只读、无审批按钮）。

## 4. 测试证据（2026-10-30 实测，单一权威批次）

| 套件 | 结果 |
|---|---|
| `node frontend/js/module_tests/run_all.js` | **499 passed / 0 failed**（基线 444 → +55，零回归） |
| `node … run_all.js acp2_`（新测试族） | 55 项，零空筛选 |
| `pytest tests/test_frontend_sync_*.py tests/test_frontend_integration.py tests/test_acp_api.py tests/test_acp_api_next_entry.py -q` | **454 passed** |
| 浏览器（playwright chromium，隔离真实服务）：新入口 `acp2_next_entry.browser.js` | **10/10**（含资源全 2xx、零致命 pageerror、三真实模块挂载优雅态、Alt 切换、语言切换、刷新存活、无占位残留） |
| 浏览器：旧页面 `page_integration.browser.js` | **8/8** |
| `tests/test_frontend_sync_viewer_framing.py`（`$3Dmol` 单站 pin / addModel 受限白名单 pin） | 通过——模块经 `scene_instance` 受限 API 直呼 `scene.addModel`，token 仅用白名单 `setStyle`，无需放宽守卫 |

## 5. 诚实性台账（NOT_VERIFIED / 分级证据）

- **DSH 全部运行时能力**（会话/事件/审批/取消/断线恢复/UI 挂载/认证）：NOT_VERIFIED——`pnpm-lock` 无 `@deepseek-ai/*`、四包无 dist；本切片零 DSH 依赖（Agent 面板显示"只读 · 未连接模型"正常态）。
- **工具调用时间线**：disabled 占位，无假数据；**审批卡**：类名保留未实现（UI-03B 统一入口）。
- **分级**：模块/面板行为=Node+浏览器双证据；研究/证据卡数据映射=fixture+契约校验器证据（`validateViewModel` 硬门），真实研究记录端到端读取未连调（需真实数据）。
- **性能基线**（方案 §7.5 请求数/字节/TTI 对比）：**未测量**，未设阈值。
- **C33/C34 静态资源 manifest**：未触碰，维持 `NOT_VERIFIED(c_static_assets_absent)`。
- **B-req-19**（`/next/` 路由+豁免）：C 车道已交最小实现+测试，**待 B 签收**。
- 浏览器验证环境为仓库外 workaround（npx 缓存 playwright 1.64.1 + chromium 1243 symlink），未入仓。

## 6. 已知缺口与后续

1. UI-03（wizard 拆分、统一草稿/preflight/提交链）未启动；Execution side 的 exec-detail、Molecular side 的 task-config 均为占位面板（待接入 chip）。
2. viewer 仅 XYZ 多帧；MOL/SDF、帧播放、点选/测量留给后续；多实例前须工厂化 `ACPGeometryStore`（README 已记）。
3. `data-acp-module-state` 已统一为模块级钩子（面板级 `data-acp-state` 属 Shell）；两者契约写入设计规格 §7.4。
4. `next.viewer.errorMessage` 等 3 个 descriptor 错误键已补齐双语（键集 zh/en 对称断言通过）。
5. WebGL 不可用环境：viewer 走 error+重试优雅态（浏览器断言按此验收，非渲染成功）。

## 7. 回退路径

- **实验入口整体**：`/next/` 挂载条件化（目录移除即 404 且不影响 `/`）；删 `frontend/next/` + `js/shell/` + `js/modules/` + 2 个 css 目录即回到原点；server 侧回退=还原 `server.py` diff + `.gitignore` 一行 + 删 `tests/test_acp_api_next_entry.py`。
- **模块单独**：index.html 去掉对应 script 标签 → registry miss → 占位面板，其余面板不受影响（有测试钉住）。
- **旧页面 CSS 修复**：删 `queue-detail.css` 那 1 行即回到 HEAD 行为（即回到按钮不可点缺陷）。
- 数据/任务/审批零前端持久化变更，无回放风险。
