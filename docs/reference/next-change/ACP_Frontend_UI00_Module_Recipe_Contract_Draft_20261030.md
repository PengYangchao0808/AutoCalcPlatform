# ACP 2.0 前端模块/工作区配方契约草案（schemaVersion: 0-draft）

日期：2026-10-30
阶段：UI-00（契约草案，方案 `ACP_2.0_前端同步升级开发方案_20261029.md` §3.2-3.5）
性质：**draft，未冻结**。`schemaVersion: 0-draft` 表示所有形状可随 UI-01/02 实测修订；字段实际形状最终以服务已冻结契约为准（方案 §3.3），本文不冒充现有 API。
配套基线：`ACP_Frontend_UI00_DSH_Capability_Baseline_20261030.md`（旧入口事实与地雷 M1-M15）。
视觉/布局姊妹文档：`ACP_Frontend_UI01_Shell_Design_20261030.md`（槽位栅格、面板 chrome、class 全表；冲突时布局细节以其为准、生命周期语义以本文为准）。

契约原则（方案 §3.5）：模块=可注册业务能力；工作区=声明式配方；宿主=ACP Web / DSH。配方决定「放哪些模块、放在哪里、跟随哪个上下文」，**不拥有科学规则、任务缓存或第二套提交链**。模块之间只经 Shell 命令与上下文通信，禁止访问另一模块 DOM、内部 3Dmol 实例或私有变量（方案 §3.1）。

---

## 1. 模块描述符（`js/shell/module-registry.js` 消费）

```json
{
  "id": "molecule-viewer",
  "interfaceVersion": 1,
  "dependencies": ["client-core", "visualization"],
  "capabilities": {
    "requires": ["structure-read"],
    "provides": ["structure-view", "atom-selection"]
  },
  "hosts": ["acp-web"],
  "supportedInstancing": "single",
  "configSchemaVersion": 1,
  "load": "(deps) => Promise<ModuleFactory>",
  "error": { "titleKey": "shell.module.load_failed", "messageKey": "shell.module.load_failed_detail" },
  "degradesTo": "placeholder"
}
```

字段语义（方案 §3.2/§3.5 落地）：

| 字段 | 约束 |
|---|---|
| `id` | 稳定字符串，全局唯一；配方与注册清单的唯一键；改名=新模块 |
| `interfaceVersion` | 模块公开接口版本（**正整数**，UI-01 实现已冻结此类型：`1,2,…`；Shell 校验 ≥1 且 ≤ 宿主接口版本），与模块自身版本分开；Shell 检查不兼容即拒载并显示明确原因 |
| `dependencies` | 声明式依赖（共享 client 层/visualization 等能力名，不是 DOM id）；Shell 检测缺失与**循环依赖**后才决定加载 |
| `capabilities.requires/provides` | `requires` 缺失 → 不加载该模块、配方其余部分照常；`provides` 供宿主能力协商 |
| `hosts` | 支持的宿主（`acp-web` / `dsh`）；首期仅 `acp-web` |
| `supportedInstancing` | `"single"`（全局唯一实例，如首期 molecule-viewer）或 `"multi"`；Shell 在装配时强制，**不因配方复用暗中开启多实例**（方案 §3.5） |
| `configSchemaVersion` | 面板 `config` 的 schema 版本；布局恢复先迁移配方版本再验证（方案 §3.5） |
| `load` | 返回 `Promise<ModuleFactory>` 的加载函数；**加载器缓存同一模块的加载 Promise**，失败时**移除失败缓存并允许重试**（方案 §3.2）。注册清单来自受控发行资源，**不接受研究内容指定任意脚本 URL**（方案 §3.5） |
| `error` | 加载/依赖失败时面板 chrome 展示的 i18n key（标题+消息），局部错误不拖垮 Shell 与兄弟面板（方案 §7.1） |
| `degradesTo` | 缺可选能力时的降级视图（`"placeholder"` 占位面板 = UI-01 设计 §3.1「待接入」chip）；缺 `requires` 时其他模块正常使用 |

注册清单（registry）职责：依赖缺失/循环依赖/版本不兼容的**装配前校验** + 失败原因透出；模块版本与接口版本分开记录。

## 2. ModuleInstance 接口

```ts
type ModuleFactory = (deps: ModuleDependencies) => ModuleInstance;

interface ModuleInstance {
  mount(host: HTMLElement, initial: ContextSnapshot): Promise<void>;
  updateContext(next: ContextSnapshot): void;
  resize?(): void;
  suspend?(): void;   // 隐藏（保状态、停渲染/订阅降级）
  resume?(): void;
  dispose(): void | Promise<void>;  // 销毁（可再次 mount = 全新生命周期）
}
```

生命周期纪律（方案 §3.2 落地，实现车道必须遵守）：

1. **dispose 幂等**：重复 dispose 不抛错、不重复清理；dispose 后所有订阅/定时器/监听/观察器/WebGL 场景已释放，资源回到基线（方案 §7.5 反复开合检查）。隐藏（`suspend`）与销毁（`dispose`）语义严格分开。
2. **mount 失败自清理**：mount 中途失败时模块**清理自身已创建资源**并把局部错误交给面板 chrome 展示（`error` key）；不得残留半挂载状态，不得使 Shell 或兄弟面板失效（方案 §7.1）。
3. **迟到异步结果不得重挂载**：每个实例持 generation 计数或 AbortSignal；dispose/suspend/切换上下文后到达的异步结果**整体作废**（对比旧体系 session token 防 A→B→A 的同类保护，见基线 §2.7/M5）。关闭面板后迟到结果既不重挂载、不回写、不复活旧上下文。
4. **mount 返回 ≠ 数据就绪**：3D 场景创建异步（基线 M13），模块以自身状态回调上报就绪；Shell 不阻塞等待。
5. **旧入口兼容**：旧脚本自动挂载仅在旧入口启用；旧脚本的自动挂载逻辑**一行不改**，旧页面回归行为保持原样（方案 §3.2、§7.1）。

## 3. WorkspaceContext

保存「关注点」，**不复制任务内容**（方案 §3.3）。

```ts
interface ContextSnapshot {
  projectId?: string;
  researchRef?: string;            // 研究记录引用（形状以服务冻结契约为准）
  taskRef?: { jobId: string; attempt: number };
  structureRef?: string;           // 结构/工件引用
  networkNodeId?: string;
  revision: number;                // 单调递增
  source: string;                  // 变更来源标识（模块 id / user / command）
}

interface WorkspaceContext {
  getSnapshot(): ContextSnapshot;
  subscribe(fn: (next: ContextSnapshot) => void): () => void;  // 返回取消函数
  setFocus(patch: Partial<ContextSnapshot>, source: string): void;
}
```

上下文纪律：

1. **原子更新**：`setFocus` 一次调用 = 一次 revision 递增 + 一次通知；订阅者收到一致快照，不观察到半更新。
2. **项目切换清除子引用**：`projectId` 变更时原子清除 `researchRef/taskRef/structureRef/networkNodeId` 等不再有效的子引用（方案 §3.3），下游显示「缺少引用原因」而非静默空转（方案 §5）。
3. **禁止通知回写同值**：模块在响应订阅通知时**不得无条件 `setFocus` 回写同值**，防通知循环（方案 §3.3）。
4. **焦点 vs 局部选择**：面板默认 `binding: follow-focus` 跟随全局焦点；`binding: pinned` 面板持自身引用，**不得反向覆盖全局焦点**，除非用户显式「设为当前」（方案 §3.3、§5）。
5. **异步核对**：模块异步结果同时核对项目、任务、attempt 与实例 token；跨项目引用拒绝解析（方案 §7.1）；A→B→A、同任务 rerun、关面板均作废旧结果。
6. **任务事实不入上下文**：任务/attempt 以 Services 为准；上下文只带引用（方案 §1、§3.3）。DSH 会话事件不能改写任务或科学验证结果（方案 §3.6）。

## 4. 配方 JSON（三工作区首期固定布局）

`slots ∈ {main, side, bottom}`（与 UI-01 Shell 设计 §1.3 栅格一一对应）；`binding ∈ {"follow-focus", "pinned"}`。首期**固定布局、无拖拽**（方案 §5、UI-01 §1.3）。

```json
{
  "id": "research",
  "schemaVersion": 0,
  "panels": [
    { "instanceId": "research-main",   "moduleId": "agent-workspace",  "slot": "main",   "binding": "follow-focus", "config": { "mode": "readonly" } },
    { "instanceId": "research-viewer", "moduleId": "molecule-viewer",  "slot": "side",   "binding": "follow-focus" },
    { "instanceId": "research-tasks",  "moduleId": "task-explorer",    "slot": "bottom", "binding": "follow-focus", "config": { "rowHeight": "compact" } }
  ]
}
```

```json
{
  "id": "molecular",
  "schemaVersion": 0,
  "panels": [
    { "instanceId": "mol-viewer", "moduleId": "molecule-viewer", "slot": "main", "binding": "follow-focus" },
    { "instanceId": "mol-config", "moduleId": "task-config",     "slot": "side", "binding": "follow-focus" }
  ]
}
```

```json
{
  "id": "execution",
  "schemaVersion": 0,
  "panels": [
    { "instanceId": "exec-tasks", "moduleId": "task-explorer", "slot": "main", "binding": "follow-focus", "config": { "rowHeight": "full", "toolbar": true } },
    { "instanceId": "exec-detail","moduleId": "task-explorer", "slot": "side", "binding": "follow-focus", "config": { "view": "detail" } }
  ]
}
```

装配规则：

1. **Viewer 单活动实例**（`supportedInstancing: "single"`）：research 与 molecular 共用同一实例，切换配方时**迁移 DOM 节点到目标槽或销毁后重建**；不因配方复用暗中开多实例（方案 §3.5）。实例 id 与槽位归属的最终取值（`research-viewer` vs 共享 `shared-viewer`）由 UI-01/02 实现车道定夺后回写本文——**UI-01 设计稿 §3.1 使用 `research-viewer`/`mol-viewer`/`exec-tasks`/`exec-detail` 命名，方案 §3.5 示例使用 `research-main`，两稿未冻结，冲突待 UI-01 收敛**。
2. **占位降级**：配方引用不存在/未交付模块 id（如首期 `task-config`）→ 该槽渲染占位面板（待接入 chip + 归属阶段说明），其余模块照常工作（方案 §3.5「未知模块显示占位」；视觉见 UI-01 §3.1）。
3. **布局恢复**：持久化仅保存模块 ID、位置、面板 config 与配方版本（本文 schemaVersion），**不保存密钥、任务事实、审批**（方案 §5）；恢复先迁移配方版本再验证模块与引用权限；损坏回退默认布局。
4. **配方不拥有提交链**：Agent Workspace 可组合组件，但不能重新实现 Viewer/Task Explorer/Task Config（方案 §3.5）。

## 5. 命令总线（window.ACPShell.commands）

Shell 提供**有类型的导航命令**（方案 §3.5）：

```ts
interface ShellCommands {
  openStructure(ref: StructureRef, source: string): void;
  openTask(ref: { jobId: string; attempt?: number }, source: string): void;
  openEvidence(ref: EvidenceRef, source: string): void;
  openProposal(ref: ProposalRef, source: string): void;
}
```

纪律：

1. **负载只带可验证引用 + 来源**：不传 DOM 节点、场景对象、凭据（方案 §3.5）。引用解析失败时显示原因，**不自动创建任务**（方案 §5）。
2. **导航与写操作严格分离**：模块**不得**把 `openProposal` 自动解释成提交（方案 §3.5）。写操作（提交/审批/远程操作）仍走 Task Config/Services 唯一链，服务逐次检查权限（方案 §1、§3.6）。
3. **Shell 负责路由**：命令由 Shell 选择目标实例与焦点恢复（如 `openStructure` → Molecular 工作区 viewer + `setFocus`）；模块只处理自己的渲染与局部状态。
4. 命令失败为**同步可见的局部错误**（面板 chrome），不静默。

## 6. 挂载纪律（新入口显式挂载）

1. **新入口显式挂载**：`frontend/next/index.html`（UI-01）下所有模块一律由 Shell loader 显式 `mount(hostEl, snapshot)`；不依赖任何 DOMContentLoaded 自动挂载。
2. **旧脚本复用尽量走 container 注入缝隙**（支持程度见表）。三个支持显式 mount 的旧面板签名（已核实，行号锚定 HEAD `484764f6`）：

   | 面板 | mount 签名 | container 缝隙 | 依赖前置 |
   |---|---|---|---|
   | research-approval | `mount(doc, options)`（`workbench/features/research-approval.js:994`，导出 `:1115`） | **支持** `options.container`，缺省回退 `doc.getElementById("research-approval-body")`（`:1002-1005`） | `init(options)` 内联调用；`ACPClient.research` 若在则绑定（`:998-1001`） |
   | scientific-evidence | `mount(doc, options)`（`workbench/features/scientific-evidence.js:690`，导出表 `:889-912`） | **支持** `options.container`，缺省回退 `getElementById("scientific-evidence-body")`（`:709-711`） | 可注入 `sceneFactory/onSaveCandidate/persistence/science/research`（`:693-706`）；fixture 缝 `window.ACP_EVIDENCE_BUNDLE_FIXTURE`（`:716-718`） |
   | science-question | `mount(doc, options)`（`workbench/features/science-question.js:1173`，导出表 `:1223-1255`） | **不支持 `options.container`**：render 固定取 `domDoc.getElementById("science-question-body")`（`:927`）→ 复用时须在 host 内提供同 id 节点或由适配层包一层固定 id 容器 | 可注入 `fixtureMode/autoDiscover`（`:1175,1198`）；缺 science/wizardEndpoints/submit 时回退页面全局（`:1181-1191`）；**提交链等价性另需 `init(deps)` 注入**（`:230`；缺 science/buildJobBody/submit 即 reject，`:408,619,700,790`）；自动挂载时还会 `ACPClient.science.init`（`:1212`）+ `mount(document)`（`:1214`） |

3. **禁止双挂载的加载顺序规则**（基线地雷 M3）：三个面板的 IIFE 在 `document` 的 `DOMContentLoaded` 回调里自动 `mount(document)`（`research-approval.js:1073`、`scientific-evidence.js:877`、`science-question.js:1209`）。**在 DOMContentLoaded 之后动态加载的脚本，其自动挂载不会触发**（事件已过，回调永不执行）——因此新入口动态加载这些脚本后必须显式 mount；反之若把脚本作为解析期 `<script>` 引入，自动挂载会触发，此时再显式 mount 即**双挂载**。规则：同一 feature 脚本在同一页内**要么**走旧入口自动挂载、**要么**走新入口显式 mount，永不同时。
4. **副作用脚本复用顺序**：若新入口复用 `compat/api.js`/`queue.js` 等副作用脚本，必须保持 `compat/api.js → queue.js` 求值顺序并提供 `API_BASE`/`api`/`apiV2` 等页面全局（基线 M2）；否则不用，改为注入。

## 7. 命名空间（与旧全局隔离）

| 命名空间 | 内容 | 纪律 |
|---|---|---|
| `window.ACPShell` | `registry`（模块注册/校验）、`loader`（加载/缓存/重试）、`context`（WorkspaceContext）、`layout`（配方装配/占位/持久化）、`commands`（导航命令总线） | 新 Shell 唯一顶层入口 |
| `window.ACPNextModules.<id>` | 各模块工厂（`<id>` = 描述符 id，如 `ACPNextModules["molecule-viewer"]`） | 模块不得挂其它顶层名 |
| CSS `acp-shell-*` / `acp-card-*` | 页框/面板 chrome 用 `acp-shell-*`、`acp-workspace`、`acp-slot`、`acp-panel-*`；Agent 卡片用 `acp-card-*`（全表见 UI-01 设计 §1.5） | 与旧样式零同名 |

**隔离约束**：
- **不改旧全局** `window.ACPWorkbench` / `window.ACPClient` / `window.ACPVisualization` / `window.ACPGeometryStore`：不重命名、不删字段、不改语义；旧入口行为逐字节保持（方案 §3.2）。新 Shell **不读写**这些全局与 shared-state 页面级 73 个全局（基线 M1/M6），旧 feature 面板经 §6 mount 缝隙注入时才触碰其自身命名空间。
- 旧 client/visualization 能力复用走**只读消费**（注入实例），不通过旧全局反向耦合新模块；模块间禁止跨模块私有导入（方案 §3.5 模块完成定义）。
- classic script 与 ESM：首期新模块加载格式以构建/发行验证为前提，不假定浏览器可把旧脚本当 ESM 导入（方案 §3.1）；本文不冻结模块脚本格式。

## 8. 测试命名

- 新增前端模块测试一律 **`acp2_` 前缀**：`frontend/js/module_tests/acp2_*.test.js`，运行 `node frontend/js/module_tests/run_all.js acp2_`（零匹配 exit 2 = 拒绝，非通过；基线 M14）。
- **必须避开的现有 8 个 acp2 测试名**（同前缀已占用）：`acp2_approval` / `acp2_client` / `acp2_client_events` / `acp2_evidence` / `acp2_layout` / `acp2_manual` / `acp2_preflight_concurrency` / `acp2_question_reaction`（目录 `frontend/js/module_tests/`，2026-10-30 实测存在）。
- 新测试覆盖目标（方案 §7.2）：registry 依赖错误、并发加载去重/失败重试、上下文原子更新、实例清理、异步过期（generation/AbortSignal）、session token、不同项目与 attempt 隔离。
- Python 侧守护边界见基线 §4：新 JS **禁止 `$3Dmol` 字面量**（含注释），viewer 只经 `mol3d_adapter`/`scene_instance` facade。
- 浏览器集成：新入口用例与旧页面断言分开维护（方案 §7.3）。

---

*schemaVersion 0-draft，未冻结；UI-01/02 实测后按修订流程更新版本号再冻结。本文只定义契约，不含实现代码。*
