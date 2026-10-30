# ACP 2.0 前端升级 UI-00 基线：DSH 能力核验与旧入口事实清单

日期：2026-10-30
阶段：UI-00（基线交付，方案 `ACP_2.0_前端同步升级开发方案_20261029.md` §6）
性质：静态源码核对 + 一次模块测试实测。**不表示 DSH 已安装、未运行 rph build/test、未启动服务、未做发行验收**（与方案 §0 审核结论一致）。本文中 PASS 仅指「静态事实核对通过」，凡运行时/宿主行为一律 NOT_VERIFIED，不得据本文宣称能力可用。

配套交付：`ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md`（模块/工作区配方契约草案）。
证据约定：所有断言带路径与行号，行号锚定下述 HEAD；无行号者为目录级事实。抽查由本文作者于 2026-10-30 执行。

---

## 1. 源码基线

| 项 | 事实 | 证据 |
|---|---|---|
| 提交 | HEAD `484764f6310945d1fa54ecf20cb739279f5ec861`（main，Merge PR #36 feat/frontend-plan1-closure） | `git rev-parse HEAD` |
| 工作区 | 除未跟踪文件 `docs/reference/next-change/RPH_Agent_Three_Phase_Roadmap_20261027.md` 外干净（方案 §0 已声明该文件非冻结基线） | `git status --porcelain`（采集时点） |
| Node | v22.23.3 | `node --version` |
| 模块测试 | `node frontend/js/module_tests/run_all.js` = **444 passed / 0 failed / exit 0**（2026-10-30 实测）；过滤器 `acp2_` = 115 passed / 0 failed / exit 0 | 实测输出；运行器退出语义见 `frontend/js/module_tests/run_all.js:72`（失败 exit 1）、`:74-77`（零测试 exit 2） |
| 浏览器/机器/数据集/服务状态 | 未起服务、未开浏览器；性能与网络请求基线未采集（方案 §6 UI-00 要求项中属「标注未验证」处理） | — |

> 注意：采集期间工作区出现并行车道对 `src/acp/api/server.py` 的**未提交**改动（追加 `/next/` 路由与身份豁免前缀）。本文所有行号锚定已提交的 HEAD `484764f6`；并车改动不属于本基线，实现车道开工时须以自己的 HEAD 重新核对服务路由事实（见 §3 表第 15 行备注）。

## 2. 旧入口事实清单（frontend/ACP_Workbench_v2.html 体系）

本节为已核实事实的压缩清单，供实现车道直接引用；抽验方式为静态读取，未做浏览器运行验证（除 §1 的 run_all 实测外）。

| # | 事实 | 证据 |
|---|---|---|
| 2.1 | 唯一生产入口 `frontend/ACP_Workbench_v2.html`（另有遗留 `ACP_Workbench.html`、`ACP_Workbench_minimal.html`）；服务 `/` 返回 v2 HTML，`/legacy/` 返回 v1 | `src/acp/api/server.py:453-469`（HEAD） |
| 2.2 | v2 HTML 的 **script 顺序被测试 pin**：`test_default_page_loads_modules_in_dependency_order` 断言每个脚本只加载一次且依赖脚本 index 小于消费者 | `tests/test_frontend_integration.py:16-47` |
| 2.3 | `js/workbench/bootstrap.js`（714 行）`setupEventListeners()` 从第 1 行起绑定约 80 个固定 DOM id，无幂等守卫，在 `:646` 被调用一次 | `frontend/js/workbench/bootstrap.js:1,646` |
| 2.4 | `js/workbench/compat/shared-state.js`（441 行）暴露 **73 个顶层 `let/var/const` 页面级全局**：`selectedProjectId/selectedJobId/jobsCache/activeSSE/jobsPollTimer/viewer/currentModel/layoutState/energyGraphState(:350)/taskViewPrefs` 等；classic script 无模块系统，同名顶层声明即互相覆盖 | `frontend/js/workbench/compat/shared-state.js`（全局计数实测 73） |
| 2.5 | 13 个 feature 中 **3 个为 IIFE + DOMContentLoaded 自动挂载**：`research-approval.js:1073→mount(document)`、`scientific-evidence.js:877→mount(document)`、`science-question.js:1209→(ACPClient.science.init + mount(document))`；三者**均支持 container 注入的显式 mount**（签名见契约草案 §6） | `frontend/js/workbench/features/{research-approval:1073-1079,994; scientific-evidence:877-885,690; science-question:1209-1216,1173}.js` |
| 2.6 | 加载期副作用清单（脚本被 `<script>` 拉起即执行，副作用依赖页面全局）：`queue.js:12` `ACPClient.jobCommands.init({v1,v2,onInvalidate})`；`wizard.js:354` wizardEndpoints init 守卫；`reaction-builder.js:1329` `ACPWorkbench.controllers` 守卫；`energy.js:171`、`nmr.js:354` `registerCanvasLoader`；`compat/api.js:31-56` ACPClient 全家 init 链（events/systemEndpoints/taskView/research/eventInvalidation/jobDetail） | 各文件对应行 |
| 2.7 | controllers 三个**模块级单例**（脚本全局 IIFE，无工厂）：`task_list_controller`、`task_session_controller`、`job_detail_controller`；`init(deps)` 缺依赖即抛错；`task_session_controller` 用**单调 token** 防 A→B→A 与同任务重选迟到响应 | `frontend/js/workbench/controllers/task_list_controller.js:58`；`task_session_controller.js:56,92`（`++tokenSeq`） |
| 2.8 | `js/client` **12 个模块**挂 `window.ACPClient.*`（transport/event-stream/polling-strategy/resource-store/task-view-query/job-commands/job-detail-query/research-query/science-query/system-endpoints/event-invalidation/wizard-endpoints）；命名模式 `window.ACPClient = window.ACPClient \|\| {}`；**resource-store 引用计数、events 单连接 SSE（打开前先关旧连接）、polling 单 owner（generation 守卫）** | `frontend/js/client/`（目录）；`transport.js:33`；`event-stream.js:26,32,97-101,144`；`polling-strategy.js:17,104` |
| 2.9 | `js/visualization` **4 个可复用模块**（scene_instance/mol3d_adapter/framing/energy_chart_renderer），挂 `window.ACPVisualization.*`；**`mol3d_adapter` 是唯一允许接触 `$3Dmol` 的模块**（文件头声明 + 测试 pin） | `frontend/js/visualization/mol3d_adapter.js:2-3,18`；`scene_instance.js:56`；pin 测试见 §4 |
| 2.10 | 几何编辑状态为 **window 级单例** `window.ACPGeometryStore`（非按文档工厂） | `frontend/js/geometry_store.js:6,396` |
| 2.11 | 非 GET 请求经页面 `api()`/`apiV2()` chokepoint **统一触发 task-view metadata 失效**；jobCommands 回执再经该链失效 detail | `frontend/js/workbench/compat/api.js:14,21,73` |

## 3. DSH 能力核验表（dsh-capabilities，方案 §3.6/§6 UI-00 产出）

静态事实（已核实）：
- rph 四包 `bridge/research/dsh-bundle/cli` 均 `version: 0.0.0`、`private: true`、**无 `bin` 字段**、`main` 指向 `./dist/...`，但**四包 `dist/` 与 `node_modules/`（含 rph 根）全部不存在 → TS 从未编译、依赖从未安装**（`rph/packages/*/package.json`；目录实测）。
- `@deepseek-ai/dsh-mcp-client` 在源码中**仅是字符串常量**（`rph/packages/dsh-bundle/src/index.ts:12`）+ profiles 配置声明（`rph/packages/dsh-bundle/profiles/web.cordis.yml:23-24`、`headless.cordis.yml:18-19`；web 另声明 `@deepseek-ai/dsh-agent-preset/dsh-persona/dsh-time-context/dsh-tool-todo`，`web.cordis.yml:60-78`）；**`rph/pnpm-lock.yaml` 中 `@deepseek-ai/*` 条目数 = 0 → DSH 客户端未安装**。
- dsh-bundle 导出：`bundleProfile()` 对 web/headless 恒返回 `localOnly: true`（`dsh-bundle/src/index.ts:43-53`）；四 skill + 单 Supervisor 导出面（`dsh-bundle/skills/index.ts:6-12`：schema/models/interpreter/synthesizer/reviewer/planner/supervisor）。
- bridge 七操作 `discover/validate/submit/status/evidence/prepare/read`（`bridge/src/index.ts:16-24`，canonical 列表 `:27-35`）；`OPERATION_TOOL` 映射（`bridge/src/index.ts:277-289`）→ `acp_capabilities/preflight/submit/status/analyze/prepare_artifact/read_artifact`，与 ACP MCP 冻结八工具面 `src/acp_services/entrypoints/mcp.py:211-226`（`acp_capabilities/acp_preflight/acp_submit/acp_status/acp_cancel/acp_prepare_artifact/acp_read_artifact/acp_analyze`）**一致**（bridge 侧无 cancel 操作；`acp_cancel` 仅存在于 MCP 工具面，属 status/cancel 家族）。
- MCP 传输 stdio only（`mcp.py:143`）、协议锁定 `2025-06-18`（`mcp.py:130`）、入口 `acp mcp serve`（`src/acp/cli.py:2298-2301`）。
- 依赖方向冻结 `cli → dsh-bundle → bridge → research`（各 `package.json` dependencies 均 `workspace:*` 单向）。

| # | 能力 | 静态证据（路径:行） | 运行时缺口 | 状态 | 降级行为 |
|---|---|---|---|---|---|
| 1 | rph 四包源码存在、依赖方向冻结 | `rph/packages/{cli,dsh-bundle,bridge,research}/package.json`（deps 单向） | 无（纯静态事实） | PASS | — |
| 2 | TS 编译产物（浏览器可消费 dist） | 四包 `main`→`./dist/...`；四包 `dist/` 目录不存在 | 从未 `tsc -b`；无任何可分发 JS | **FAIL** | 任何宿主消费先在 CI/build 产 dist；不 build 不得引用 rph 包 |
| 3 | Node 依赖安装（含 DSH 客户端插件） | 四包及 rph 根 `node_modules/` 不存在；`rph/pnpm-lock.yaml` 无 `@deepseek-ai/*` | DSH 官方 MCP 客户端未安装，认证/工具发现无从运行 | **FAIL** | DSH 不可用时：禁用「新起会话」入口，已有研究记录只读展示 |
| 4 | DSH 公开接口名声明（插件包名 + web/headless profile） | `dsh-bundle/src/index.ts:12-16`；`profiles/{web,headless}.cordis.yml` | 声明 ≠ 可用：宿主是否存在该插件、接口版本均未验证 | PASS（仅声明面） | 以声明为接口草案，方法名待真实宿主核验后冻结（方案 §3.6） |
| 5 | bundleProfile 恒 `localOnly: true` | `dsh-bundle/src/index.ts:47-52`（web/headless 同） | 无（纯静态事实） | PASS | — |
| 6 | 四 skill + 单 Supervisor 导出面 | `dsh-bundle/skills/index.ts:6-12` | 未 build、未 test、未装载；skill 装载格式未核验（方案 §3.6） | PASS（仅导出面） | 单 Agent 顺序执行起步，不把四 skill 展示为多个并行 Agent |
| 7 | bridge 七操作 ↔ MCP 八工具冻结面一致 | `bridge/src/index.ts:16-24,277-289` ↔ `mcp.py:211-226` | 映射一致性为静态核对；真实调用未跑 | PASS（静态交叉核对） | 消费侧只认 OPERATION_TOOL 单一映射，不自建第二映射 |
| 8 | MCP 传输/协议/入口 | `mcp.py:130,143`；`cli.py:2298-2301` | 未起 stdio 进程做端到端探针（B05 证据属 Services 车道，本文未复验） | PASS（静态事实） | 无 stdio 入口则整体禁用 DSH 侧工具链 |
| 9 | DSH 实际安装、认证、工具发现与调用 | 无正面证据（仅 #3/#4 的反面与声明） | 未安装、未运行、未认证 | **NOT_VERIFIED** | 同 #3：禁新会话、研究记录只读；不另建 MCP transport（方案 §3.6） |
| 10 | 会话创建/恢复 | 无宿主侧源码可查；`frontend/next` 不存在 | 宿主会话 API、生命周期、恢复语义均未核验 | **NOT_VERIFIED** | 禁新会话；已有记录走 ACP 只读研究面板 |
| 11 | 工具开始/结束事件（流式） | bridge 无事件传输实现（`bridge/src/index.ts:1-8` 头声明 no transport） | 缺事件接口时不得推测进度 | **NOT_VERIFIED** | 只显示服务可读状态（job/attempt 状态经现有 API），不补造开始时间或进度 |
| 12 | 审批交互（P2 `approval.*`） | P2 操作存在：`bridge/src/index.ts:405-409`（request/query/approve/reject/revoke）、`:507-619`（P2_OPERATION_MAP，其中 approval.* 映射 `:537-580`）；**P2 操作须 negotiate 扩展，未协商即契约拒绝**（`bridge/src/index.ts:211-217` BridgeContractError 文档注释） | negotiation 运行时未发生、未验证 → 按方案口径「未 negotiate 不可见」 | **NOT_VERIFIED** | 审批一律走现有 Web 统一审批入口；ACP 侧审批 UI 不依赖 P2 approval.* 直连 |
| 13 | 取消语义（会话取消 ≠ 任务取消） | `acp_cancel` 工具存在于 `mcp.py:223`；bridge 无 cancel 操作 | DSH 会话停止/后续计划停止/已提交任务取消三者映射未核验 | **NOT_VERIFIED** | 三动作分别呈现（方案 §3.6）；页面卸载只解除 UI 订阅，不隐式取消任务 |
| 14 | 断线恢复（research.resume） | `rph/packages/research/src/`（cas/index/memory/proposal/record/store.ts）grep `resume` **零匹配 → resume 未实施** | 无恢复接口；重连后时间线完整性无法自证 | **NOT_VERIFIED** | 缺恢复接口时标记历史不完整，以服务权威快照恢复任务卡（方案 §7.1） |
| 15 | UI 挂载/跳转（公开扩展点） | HEAD `server.py:443-451` 仅挂 `/js`、`/css` 静态；`/`→v2 HTML（`:453-460`）、`/legacy/`（`:462-469`）；`_IDENTITY_EXEMPT_PREFIXES` 无 `/next`（`server.py:238`）；`frontend/next` 目录不存在 | 无任何已发布的新入口路由、无 DSH 公开挂载/跳转接口证据 | **NOT_VERIFIED** | 无公开 UI 扩展点时：独立 DSH 入口 + ACP 只读研究面板联动；不默认 iframe、不注入私有 DOM（方案 §3.6）。*备注（非基线）：采集期间观察到并车对 server.py 的未提交 `/next/` 路由改动，落地后本行须在新 HEAD 复核* |
| 16 | 认证与访问边界（宿主侧） | stdio scope 常量存在：`mcp.py:143-149`（`stdio-api-key`、非管理权限） | DSH 宿主侧认证、连接配置与凭据边界未核验 | **NOT_VERIFIED** | 连接配置和凭据留在宿主/服务安全边界，不进入布局或研究卡（方案 §3.6） |

**表末声明**：本表为**静态源码核对 + 一次 `run_all` 实测**（§1）。未安装 DSH、未运行 rph `build/test/check`、未启动 ACP 服务、未做浏览器联调 —— 与方案 §0「本地源码事实与宿主待核验能力分开」一致。FAIL 行（#2/#3）在补齐前，UI-03B/05 的 DSH 依赖项全部阻塞（见 §6）。

## 4. Python 守护测试影响面

新增 `frontend/next/`、`frontend/js/shell/`、`frontend/js/modules/` 等目录**不会**触发既有前端守护的失败，依据如下：

| 守护 | 扫描范围 | 对新增目录的影响 |
|---|---|---|
| `tests/_frontend_sync_shared.py` | `read_workbench_scripts()` 只读 **v2 HTML 引用的** `js/workbench|js/client|js/visualization` 脚本（`:30-41`）；另有两个**非递归**定点 glob：`FRONTEND_WORKBENCH_CSS_DIR.glob("*.css")`（`:78`）、`workbench/features` 目录 glob（`:94`） | 新目录不在引用集 → 不触发 |
| `tests/test_frontend_sync_viewer_framing.py:104 test_dollar3dmol_single_station_pin` | **递归扫描** `FRONTEND_JS_DIR.rglob("*.js")`（`:116`），跳过 `vendor/`、`module_tests/`；断言含 `$3Dmol` 字面量的文件集**恰为**固定集：`visualization/mol3d_adapter.js`（唯一可执行站）+ `visualization/energy_chart_renderer.js`、`workbench/controllers/candidate_review_controller.js`（仅注释允许，`:110-113`；集合断言 `:128-131`） | **新增 JS 文件禁止出现 `$3Dmol` 字面量**（注释也不行——新文件不在 allowed_comment_only 集合内） |
| `tests/test_frontend_sync_viewer_framing.py:136 test_add_model_result_restricted_methods_pin` | 同样 rglob 全部 js（`:144`）；约束「捕获 `addModel()` 返回值后只允许调用 `setStyle/addStyle/setClickable/setHoverable/selectedAtoms` 白名单方法」 | **新 JS 文件不得直接对 vendor viewer 调 `addModel(`**；必须经 `visualization/scene_instance` 的 facade（facade 自身提供 `addModel`，返回不透明 token，`scene_instance.js:322`），最终经 `mol3d_adapter`。这是契约层纪律；测试硬门仅为上两条 |
| `tests/test_acp_api.py:254 test_frontend_static_assets_served` | `GET /` 取 v2 HTML，提取其引用的全部 `js|css` 资源并断言 HTTP 200 + content-type | 新入口资源不被 v2 HTML 引用则不触发；一旦引用必须可达 |
| `tests/test_acp_api.py:272 test_frontend_static_assets_cover_viewer_modules` | pin 具体 viewer 资源路径可达 | 不受影响 |
| `tests/test_frontend_integration.py:16` | pin v2 HTML script 清单顺序与「每脚本只加载一次」 | 只约束 v2 HTML；新入口 HTML 独立维护（方案 §7.3：旧页面断言与新页面分开） |
| C33/C34 静态资源 manifest | `static-assets.manifest.json` **磁盘不存在**（find 零结果）；AGENTS.md B25b 记录为 `NOT_VERIFIED (c_static_assets_absent)` | **不得改写为 PASS**；新入口若涉及发行资源，状态由 B 车道按实际证据更新 |

## 5. 迁移地雷清单（15 条）

| # | 地雷 | 证据 | 纪律 |
|---|---|---|---|
| M1 | **全局符号冲突**：shared-state 73 个顶层全局 + `window.ACPWorkbench/ACPClient/ACPVisualization/ACPGeometryStore` 多命名空间并存；classic script 无模块边界，同名顶层声明互相覆盖 | `compat/shared-state.js`（73 顶层声明实测）；`client/transport.js:33`；`geometry_store.js:396` | 新代码一律进 `window.ACPShell` / `window.ACPNextModules` 命名空间（契约草案 §7）；不新增顶层全局，不改旧全局 |
| M2 | **加载期副作用**：脚本被拉起即执行 init（jobCommands/wizardEndpoints/registerCanvasLoader/ACPClient 全家 init），依赖 `API_BASE` 等页面全局与既定求值顺序 | `queue.js:12`；`wizard.js:354`；`reaction-builder.js:1329`；`energy.js:171`；`nmr.js:354`；`compat/api.js:31-56` | 旧脚本若在新入口复用，必须按 `compat/api.js → queue.js` 顺序加载并提供所需全局；否则用显式 mount 缝隙注入（契约草案 §6） |
| M3 | **DOMContentLoaded 自动挂载竞态**：三面板 IIFE 在 `document` 的 DOMContentLoaded 自动 `mount(document)`；事件已触发后再动态插入脚本则回调永不执行（自动挂载静默不触发），解析期加载则自动挂载触发 → 同页双入口会**双挂载** | `research-approval.js:1073-1079`；`scientific-evidence.js:877-885`；`science-question.js:1209-1216` | 新入口动态加载旧脚本后**必须显式 mount**（依赖自动挂载会静默丢失）；禁止旧入口+新入口同页同时拉起同一 feature 脚本 |
| M4 | **setupEventListeners 无守卫**：约 80 个固定 DOM id 一次性绑定，无幂等保护；二次调用即双重绑定 | `bootstrap.js:1,646` | 新入口不得整体复用 bootstrap.js；只允许按面板复用并注入 host |
| M5 | **单例 controller 重 init**：三个 controller 是脚本全局单例，`init(deps)` 非重入安全；session token 只在**单实例内**防串扰，不能跨面板共享单例 | `task_list_controller.js:58`；`task_session_controller.js:56,92` | 多面板场景先工厂化/实例包装（方案 §3.3），保留旧页面单例兼容入口 |
| M6 | **shared-state 双写**：多个 feature 直接读写页面全局（`selectedJobId`、`jobsCache`、`viewer`…），新模块回写即产生跨面板隐式耦合 | `compat/shared-state.js`；`energy.js:91-138`（写 `energyGraphState`） | 新模块不读写旧全局；上下文一律经 `WorkspaceContext`（契约草案 §3） |
| M7 | **固定 DOM id**：bootstrap/features 以 `getElementById` 硬取约 80 个 id；工作区多实例下同 id 多份 DOM 即取错节点 | `bootstrap.js`（setupEventListeners 全文）；`research-approval.js:1004`（`research-approval-body`） | 旧 feature 复用必须走 `options.container` 注入缝隙，不用页面 id |
| M8 | **GeometryStore window 单例**：`window.ACPGeometryStore` 为页面级单例而非按文档工厂，多 viewer 实例共享几何编辑状态 | `geometry_store.js:6,396` | P0 只承诺 Viewer 单活动实例（方案 §4.1）；多实例前先改按文档工厂 + 兼容 facade（UI-06） |
| M9 | **SSE + polling 全局唯一**：`activeSSE` 单连接（开新必关旧）、polling 单 owner（`pollGeneration` 守卫）；第二个模块自开连接/轮询即打乱守卫、双倍流量 | `event-stream.js:32,97-101`；`polling-strategy.js:17,104` | 事件与轮询订阅集中走共享 client 层，订阅引用计数；面板不自建无限轮询（方案 §4.2） |
| M10 | **v2 script 顺序 pin**：脚本清单顺序与「只加载一次」被测试锁定 | `tests/test_frontend_integration.py:16-47` | 改 v2 HTML script 列表必须保依赖序；新入口 HTML 与旧断言分开维护 |
| M11 | **api() 非 GET 副作用**：任何非 GET 经页面 chokepoint 即失效 task-view metadata；jobCommands 走页面 api 适配器 → 双入口并发时重复失效/竞态 | `compat/api.js:14,21,73`；`queue.js:12-17` | 新入口命令走同一 client 写链但独立实例化；不复制第二套失效逻辑 |
| M12 | **energyGraphState 巨对象**：页面级可变大对象（帧候选、jobId、会话…）被 energy/renderer 直接共享读写 | `compat/shared-state.js:350`；`energy.js:34,91-138` | 首期不在新入口复用 energy 面板；复用前先拆注入（UI-03/04 范畴） |
| M13 | **3Dmol async 不阻塞语义**：场景创建异步，mount 返回 ≠ 场景就绪；旧体系靠 container 稳定等待 + framing 兜底 | `tests/test_frontend_sync_viewer_framing.py:39 test_viewer_framing_container_stability_wait`；`mol3d_adapter.js` 头注释 | ModuleInstance.mount 的 resolve 语义按契约草案 §2（迟到结果 generation/AbortSignal 守卫）；facing 就绪用状态回调，不阻塞 Shell |
| M14 | **run_all 零测试 exit 2**：过滤器无匹配/未注册测试时拒绝而非通过 | `frontend/js/module_tests/run_all.js:74-77` | 新增 `acp2_` 测试后以 `node frontend/js/module_tests/run_all.js acp2_` 运行；空结果是失败（方案 §7.2） |
| M15 | **新旧目录无既有约定**：`frontend/next` 不存在；HEAD 服务只挂 `/js`、`/css` 与 `/`、`/legacy/`；身份豁免前缀无 `/next` | `server.py:238,443-469`（HEAD） | 新入口的服务路由、静态挂载、鉴权豁免均为 B 车道交付项；不得假定 `/next/` 已可用（方案 §2）。开工前在自己 HEAD 复核（§1 注意事项） |

## 6. 实现风险与门禁映射（方案 §6/§7 对齐）

| 阶段 | DSH 相关阻塞项 | 依据 |
|---|---|---|
| UI-01 Shell | 无 DSH 阻塞。**服务侧前置**：`/next/` 路由 + 静态挂载 + 鉴权边界由 B 车道交付并在真实安装包验证（§3 表 #15 NOT_VERIFIED） | 方案 §6 UI-01 退出条件；§3 表 #15 |
| UI-02 Viewer+Explorer | 无 DSH 阻塞。前置为旧整页 bootstrap 解耦与 token 适配（M5/M7） | 方案 §6 UI-02 |
| UI-03 Config+Evidence | 无 DSH 阻塞。**A/B 门**：A 的能力/证据契约与正负 fixture、B 的幂等/权限契约；全部现役工作流覆盖矩阵 | 方案 §6 UI-03、§8 |
| UI-03A Agent 只读首片 | **不依赖 DSH/模型**（方案 §4.6 明确）；门：已有研究记录读取契约、fixture 与真实数据标识分开 | 方案 §6 UI-03A |
| UI-03B DSH 受控交互 | **全阻塞于 §3 表 #2/#3（FAIL：dist 未构建、DSH 客户端未安装）与 #9-#16（NOT_VERIFIED）**。解锁条件：①真实 DSH 安装 + 模型；②会话/事件/取消/断线恢复能力核验 PASS；③真实模型 + `xtb_optimize` 闭环；④重连不重复提交且任务在旧 Workbench 可见 | 方案 §6 UI-03B；§3 表 |
| UI-03B 审批线 | **审批 negotiation 门**：P2 `approval.*` 需 negotiate 扩展（`bridge/src/index.ts:211-217,537-580`），未协商即不可见/契约拒绝 → 受控交互首期审批只经现有 Web 统一审批入口 + 服务权威状态，不做 DSH 直连审批 | §3 表 #12；方案 §3.4「前端不能自行宣布审批有效」 |
| UI-04 网络+2D | 无 DSH 阻塞。门：A 的科学 fixture、B 的引用/权限契约；无服务时标 fixture | 方案 §6 UI-04 |
| UI-05 DSH 完整联动 | 依赖 §3 表全部转 PASS；未过则**独立 Web 先交付，DSH 保持 NOT_VERIFIED**（不得因赶期降格改写） | 方案 §6 UI-05、§0 |
| UI-06 可选能力 | 多 Viewer/GeometryStore（M8）、终端（方案 §4.7：P0-P2 不依赖）各自独立门禁 | 方案 §6 UI-06 |

通用风险：
1. **证据等级不可互替**（方案 §7.1）：fixture PASS、ACP 真实只读、DSH 工具连接、真实模型闭环、双宿主导航五级分别登记；本文 §3 的 PASS 均为最低级（静态核对），不得上翻为高级别证据。
2. **rph 改动成本**：涉及 rph 修改时须执行其 `build/test/check` 并记录测试数与退出码（方案 §7.1）；当前四包从未 build/test（§3 #2），首次成本未计入任何估算。
3. **并车工作区污染**：§1 注意事项——多车道共享检出，开工时以自己 HEAD 重核服务路由与本文行号。

---

*本文为 UI-00 基线文档交付，不修改任何代码/测试；所有 PASS/FAIL/NOT_VERIFIED 判定可按文内路径行号复核。*
