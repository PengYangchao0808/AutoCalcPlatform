# ACP 前端 UI-03 / UI-04 / UI-05 收口记录

日期：2026-10-30
性质：实现收口与证据记录；基线 `main@484764f6`，本记录对应的实现提交见 §8。
上游方案：`ACP_2.0_前端同步升级开发方案_20261029.md`（§6 分期）
配套文档：UI-01 设计规格、UI-01/02/03A 收口记录、[差距与修正](ACP_Frontend_差距与修正_20261030.md)
后续：目标形态（DSH 原生、项目化学网络、2D 预留、第三视图重做）与分期见 [前端目标形态方案](ACP_Frontend_Target_Workbench_Plan_20261030.md)。

本轮把 Next 入口从「只读首片」推进到**可配置、可预检、可提交、可看证据**：

| 阶段 | 交付 | 状态 |
|---|---|---|
| UI-03 任务配置 | `task-config` 真模块：结构解析 → 工作流/方法 → 电荷·自旋·资源 → 方法校验 → 预检 → 提交 → 回执 | **已交付**（4 个 simple 工作流端到端；其余 11 个显式禁用并给经典向导入口） |
| UI-03 草稿 | 服务端 `/api/v1/drafts` 持久草稿：自动保存、恢复横幅、409 冲突处理、提交后删除 | **已交付** |
| UI-03 证据 | `exec-detail` 升级为证据面板：任务身份、`plan_digest`/`submission_id`/`release_id`、工件清单 + 下载 | **已交付** |
| UI-03 审批 | 预检/提交两步契约（`preflight_fingerprint`、`Idempotency-Key`） | **提交侧已交付**；独立审批 UI **未做**（见 §6） |
| UI-04 反应网络 | `reaction-network` 只读 SVG 面板（历史 mechanism 研究数据）+ `openNetwork` 命令 + Execution 底部槽位 | **已交付（数据仅历史）**；现役工作流不产出网络 |
| UI-04 二维 | 2D 化学描绘 | **未交付**：服务端无 depiction 路由，2D 组件选型是方案 §8 未决项（见 §6） |
| UI-05 组装 | 配置面板解析后把结构推给共享查看器；Execution 三面板（任务/证据/网络）；旧布局偏好的一次性迁移 | **已交付** |
| UI-03B DSH 受控交互 | — | **NOT_VERIFIED**（无真实 DSH 安装/模型，沿用 UI-00 结论） |
| UI-06 可选能力 | 多实例 viewer、终端、轨迹独立任务 | **未启动**（方案定为可选） |

## 1. 文件清单

**新增**
- `frontend/js/modules/task-config/model.js` — 纯模型：目录投影、profile/字段默认、语义锁（RI/内置色散）、电荷-自旋奇偶、请求/计划身份、草稿快照
- `frontend/js/modules/task-config/module.js` — 面板实现（重写原 43 行占位）
- `frontend/css/modules/task-config.css`、`frontend/css/modules/exec-detail.css`、`frontend/css/modules/reaction-network.css`
- `frontend/js/modules/reaction-network/module.js` — 布局 + SVG 构建 + 模块（`ACPNextReactionNetwork` 测试接缝）
- `frontend/js/module_tests/acp2_module_task_config.test.js`（11 项）
- `frontend/js/module_tests/acp2_module_exec_detail.test.js`（5 项）
- `frontend/js/module_tests/acp2_module_reaction_network.test.js`（6 项）
- `tests/test_acp_preflight_submit_contract.py`（4 项，`fake` 工作流，零副作用）

**修改**
- `frontend/next/index.html` — 新增 3 个 CSS/4 个脚本；Execution 增加 `bottom` 槽位（`data-has-bottom="true"`）
- `frontend/js/shell/recipes.js` — Execution 底部新增 `exec-network` 面板
- `frontend/js/shell/commands.js` — 新增只读命令 `openNetwork(ref)`（`networkNodeId` / `network.read`）
- `frontend/js/shell/layout-preferences.js` — UI-04 迁移：旧 Execution 布局一次性回落到新默认，避免新面板被旧偏好隐藏
- `frontend/js/shell/i18n.js` — `next.config.*`、`next.detail.*`、`next.network.*`、`shell.module.reaction_network` 双语
- `frontend/js/modules/exec-detail/module.js` — 占位重写为证据面板
- `frontend/js/modules/agent-workspace/module.js` — 修复：无 `projectId` 时不再调用 science 适配器（此前 `updateContext` 抛错）
- `frontend/js/module_tests/acp2_workspace_details.test.js` — 两个占位断言改为对真实模块的同等意图断言
- `frontend/js/module_tests/acp2_layout_preferences.test.js` — 增加旧布局迁移用例

## 2. 行为对照

| 场景 | 改动前 | 改动后 |
|---|---|---|
| Molecular 侧栏 | 只列工作流 + 跳回经典向导 | 结构解析、工作流/方法/高级参数、电荷·自旋（奇偶校验）、资源、草稿、校验、预检、提交、回执 |
| 提交 | Next 无提交能力 | `POST /preflight` 取 `plan_digest` → `POST /jobs` 带 `preflight_fingerprint` + `Idempotency-Key`；服务端 `preflight_stale` 有明确恢复提示 |
| 结构 | 无 | `/structures/parse` 支持 SMILES/XYZ/SDF/MOL/GJF/ORCA；解析成功即推 `structureRef{inline-xyz}` 给共享查看器 |
| 证据 | 5 行摘要 | 身份六项 + 工件清单（类型/路径/大小/下载）+ 失败原因 + 项目/attempt 防串扰 |
| Execution | 两面板 | 三面板：任务浏览 / 任务详情与证据 / 反应网络（底部） |
| 布局偏好 | 新面板会被旧偏好隐藏 | 检测到 UI-04 之前的 Execution 布局时回落到新默认（一次） |

## 3. 工作流覆盖矩阵（15 现役）

| 覆盖 | 工作流 | 说明 |
|---|---|---|
| **端到端** | `singlepoint`、`optimize`、`frequency`、`xtb_optimize` | 单结构 + 单层方法 + 无额外必填输入；预检与提交均实测 |
| 可见但禁用 | `scan`、`irc`、`tsmode`、`casscf`、`nmr`、`Confsearch`、`PESsearch`、`BatchOptimize`、`XtbPathSearch`、`OrcaGradient`、`XtbConstrainedOpt` | 需要坐标/TS 来源/bundle/实验谱/协议参数/源任务；面板给出「需经典向导」的原因与链接，不伪造表单 |

目录中的退役条目（`status:"retired"`）不出现于选择器。

## 4. 测试证据（2026-10-30 实测）

| 套件 | 结果 |
|---|---|
| `node frontend/js/module_tests/run_all.js` | **527 passed / 0 failed**（基线 499，+28） |
| `pytest tests/test_acp_preflight_submit_contract.py` | **4 passed** — 预检零副作用、指纹匹配创建、篡改指纹 `preflight_stale` 且不建任务、幂等键重放 |
| `pytest tests/test_acp_api_next_entry.py` | **4 passed** |
| 浏览器（隔离服务 `next_entry_server.py`，chromium） | 目录 15 项/4 可用、解析 C2H6O、`validate-method` 通过、预检通过、提交按钮启用、草稿落库（`/api/v1/drafts` 1 条） |
| 浏览器（真实服务，只读） | 选中真实任务 → 证据面板 ready（4 个工件、时间戳、下载链接）；反应网络空态；**0 console error / 0 failed request** |
| 浏览器（结构交接） | 解析后在共享查看器渲染 3D 乙醇（截图 `reports/frontend-layout-review/ui05-config-viewer.png`） |
| 冷加载基线（chromium 1440×960，本机服务，三次取中位） | 经典 `/`：82 请求 / 3988 KB / DCL 372 ms；Next `/next/`：53 请求 / 1294 KB / DCL 261 ms（`performance.getEntriesByType` transferSize 合计，仅记录不设阈值） |

## 5. 关键实现约束（防止回归）

- **单一载荷所有权**：`model.buildEnvelope()` 优先调用 `ACPWizardSubmitEdit.buildStructureJobBody`，否则复刻同一 5 键信封；面板内不出现第二套提交结构。
- **服务端权威**：方法合法性只认 `/validate-method`，可行性/摘要只认 `/preflight`；客户端校验只做「提前拦截」，不做科学判断。
- **语义锁**：复合方法强制 RI/辅助基组关闭、内置色散固定 `none`，并以文字说明（与经典向导一致）。
- **无假设渲染**：网络节点/边/能垒全部来自冻结报告字段；缺数据是显式空态，不是占位图。
- **异步守卫**：模块 generation + 每动作 token；A→B→A、rerun、项目切换的迟到响应一律丢弃。

## 6. 诚实性台账（未做 / 未验证）

1. **真实科学运行未触发**：预检与提交契约用 `fake` 工作流和真实目录验证；Next 面板未真正提交 ORCA 作业跑完。执行正确性仍由既有 CLI/调度测试覆盖。
2. **审批 UI 未做**：提交侧支持服务端 approval 字段（透传），但 Next 没有审批队列/同意/拒绝界面；research 审批仍在经典页。
3. **DSH 受控交互 NOT_VERIFIED**：无真实 DSH 安装、模型与会话闭环；Agent 面板仍是只读投影。
4. **2D 化学描绘未交付**：仓库无 `depict/svg/png` 路由，浏览器无 RDKit；2D 工具选型在方案 §8 属未决项。本轮唯一的 2D 渲染是网络 SVG 与坐标投影图，不声称是化学二维结构。
5. **化学网络仅历史数据**：`mechanism` 工作流已退役，现役工作流不产出 `reaction_network`；真实环境实测为空态（文案已说明数据来源）。
6. **性能基线**：只记录了单机冷加载的请求数/传输字节/DCL（见 §4），未做 TTI、交互延迟或与经典页的阈值对比，也未覆盖远程/LSF 场景。
7. **窄屏与键盘**：配置面板沿用 Shell 的栅格与按钮，未做专门的 320px 断点回归；键盘可达性沿用原生控件与 Shell 的 tab 模型。

## 7. 回退路径

- 单个模块：`frontend/next/index.html` 移除对应 `<script>` → registry 未注册 → 面板回落占位，兄弟面板不受影响。
- 网络面板：删除 `recipes.js` 的 `exec-network` 条目 + `index.html` 的 Execution 底部槽位即恢复到两面板。
- 偏好迁移：删除 `layout-preferences.js` 的 UI-04 特判即回到旧「已保存即生效」语义。
- 提交链：面板只调用既有 `/preflight`、`/jobs`、`/drafts`，服务端零改动，删除模块即回到改动前状态。

## 8. 提交与证据位置

- 实现提交：`feat(frontend): UI-03 config, evidence, network and assembly`（本记录同批）。
- 截图：`reports/frontend-layout-review/ui03-config-preflight.png`、`ui03-config-draft.png`、`ui03-evidence.png`、`ui04-execution-network.png`、`ui05-config-viewer.png`。
- 运行时服务：`http://127.0.0.1:8765/next/`（静态资源按请求读盘，无需重启即可看到本轮前端改动）。
