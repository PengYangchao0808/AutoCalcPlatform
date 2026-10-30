# task-explorer 模块（UI-02b，P0 只读）

ACP 2.0 新实验入口（`frontend/next/`）的任务列表模块。经典脚本（无构建、无 ESM），经
`window.ACPNextModules["task-explorer"] = { descriptor, factory }` 注册，由 Shell
loader（UI-01 车道）消费。契约依据：
`docs/reference/next-change/ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md`
§1/§2/§3/§5（schemaVersion 0-draft，未冻结，形状随实测修订）。

## 文件

| 文件 | 内容 |
|---|---|
| `module.js` | 描述符 + `factory(deps) → ModuleInstance`（mount/updateContext/resize/suspend/resume/dispose） |
| `README.md` | 本文件 |
| `../../css/modules/task-explorer.css` | 模块样式（`acp-mod-tasks-*` 前缀，全部用 `base.css` 变量） |
| `../../module_tests/acp2_module_task_explorer.test.js` | 行为测试（vm harness + fake DOM + mock client，fetch 禁网） |

## ModuleInstance 生命周期

```js
var instance = ACPNextModules["task-explorer"].factory({
  context:  { getSnapshot, subscribe, setFocus? },  // WorkspaceContext（只读 context 时无 setFocus）
  commands: { openTask, openStructure },             // Shell 命令总线（导航用，P0 只用 openTask）
  client:   ACPClient,                               // 可 mock；缺省 window.ACPClient
  onFocus:  function (patch) {},                     // context 不可写时的焦点回写回调（见下）
});
await instance.mount(hostEl, initialContextSnapshot);
instance.updateContext(nextSnapshot);  // 之后由 Shell 推送
instance.suspend(); instance.resume(); // 隐藏/唤醒（保状态、停订阅响应）
instance.dispose();                    // 幂等彻底；之后可再次 mount（全新生命周期）
```

- **mount 失败自清理**：硬依赖（`ACPWorkbench.controllers.createTaskListController /
  createTaskSessionController`、`ACPWorkbench.contracts.taskView`、`client.taskView`）缺失时
  mount reject，已建资源全部清理，不留半挂载残渣。
- **generation 守卫**：项目切换 / dispose 都 bump generation；迟到的列表或详情响应整体作废。
- **mount ≠ 数据就绪**：初始 fetch 是 fire-and-forget，结果经四态渲染器落地。

## 数据流（单缓存、零轮询）

- 列表数据只走注入 `client.taskView`（`ACPClient.taskView.fetchView / fetchSummary /
  snapshotView / subscribeView`），缓存语义由 `ACPClient.store` 提供——**模块不建第二套任务
  缓存**，不写任何页面全局（`#queue-list` / `jobsCache` / `taskViewCache` 一概不碰）。
- **不启动任何定时器/轮询**：刷新只有两个来源——① 工具栏显式刷新按钮；② store 订阅
  （`subscribeView`，外部失效/他人刷新 → 重绘缓存数据或提示 stale，不自动重取）。dispose
  取消全部订阅。
- 项目列表沿用旧端点 `GET /api/v1/projects`（`compat/api.js::api("/projects")` /
  `queue.js::loadProjects` 同一端点与 `body.projects` 形状），经注入 `client.request`（缺省
  `client.transport.request`）获取；P0 不建 projects 客户端模块。

## list-controller 取舍结论（二选一 → 选了工厂实例）

**采用 `ACPWorkbench.controllers.createTaskListController()` 自有实例 + 自渲染 views 注入**，
而不是绕开 list controller 直接消费 taskView 视图模型。理由：

1. list controller 是纯协调器（无 DOM/无全局），其注入缝（`views.onRows/renderQueue/
   renderPagination/onError/afterRender`）本来就是为「渲染目标可替换」设计的——把渲染指到
   模块 host 即可复用，零改动；
2. 直接消费视图模型会丢掉已钉住的遗留保真行为：queryKey 变更重置 offset、offset 越界钳制
   重取、AbortController + 串行防串台、render-key 门控、30s facet 复用、静默取消分类——
   重写一遍等于造第二套（且更弱的）协调逻辑；
3. 工厂化后实例状态（offset/queryKey/serial/abort）完全自有，与旧页面的
   `ACPWorkbench.controllers.taskList` 单例互不干扰（测试「两个 factory 实例互不串扰」钉住）。

模块传入的 prefs 是固定的 `{groupBy:"molecule", sort:"created_desc"}`（P0 无筛选 UI），
`detailCacheRevision` 恒 0（模块无 detail cache——详情摘要直接来自 `fetchSummary`）。

## setFocus 注入方式

以 WorkspaceContext 契约的 `setFocus(patch, source)` 为准：

1. `deps.context.setFocus` 存在 → `context.setFocus({ taskRef: {jobId, attempt} },
   "task-explorer")`（项目下拉变更同理：`setFocus({projectId}, "task-explorer")`）；
2. context 只读（无 `setFocus`）且 `deps.onFocus` 存在 → `onFocus(Object.assign({source},
   patch))` 回调（单参数 patch 形状，source 并入 patch 由宿主自取）；
3. 两者都没有 → 焦点只在模块内部维护（行高亮），不回写。

无论走哪条路，模块都在**本地立即应用**项目切换（`applyProjectId` 幂等，宿主 echo 先到则
本地应用自动 no-op）——宿主不回声也能正常工作。

## 选择与会话

模块持有自有 `createTaskSessionController()` 实例（单调 token，每实例独立）。行点击 →

`session.select(jobId)` → `loadDetail = client.taskView.fetchSummary(jobId)` →
`applyDetail`（token + 项目双校验，校验不过返回 null 终止会话）→ `afterSelection` 中
`isCurrent(session)` 复核后 `setFocus({taskRef})`。覆盖场景（均有测试钉住）：

- A→B→A：旧 A / 被顶掉的 B 的迟到 summary 不渲染、不写 focus；
- 同任务 rerun（attempt 变化）：每次 select 都铸新 token，旧 attempt 响应作废；
- 项目切换：generation bump + session.clear + AbortController 顶掉旧项目在途列表请求。

## 状态呈现

- 状态徽章 / attempt 指示 / 失败原因摘要**全部来自 taskView 视图模型**（行字段 +
  `fetchSummary` 的 `status/attempt/error`），模块不重复实现状态机；
- 终态判定复用 `job_detail_controller` 纯函数（`isTerminalJobStatus`；软依赖，缺失时退到
  `contracts.taskView.isTerminalStatus`）；
- 四态自渲染：loading（骨架行）/ empty / error（可重试）/ forbidden（401/403）；文案 zh +
  `data-i18n`（key 前缀 `next.tasks.*`）。

## P0 边界（刻意不做）

- **只读**：无任何写操作按钮（pause/cancel/rerun/rename/delete… 留给后续阶段）。面板内仅
  刷新 / 分页 / 「打开任务」（`commands.openTask` 导航命令）。
- 无筛选器 / 保存视图 / 批量操作 / 标签管理（旧 queue 的能力，未迁移）。
- `supportedInstancing: "single"`：Shell 装配层强制单实例；execution 工作区的
  「exec-tasks + exec-detail 两个槽位」由后续阶段决定是双视图单实例还是 detail 独立模块
  （UI-01 §8 未决项）。
- store 订阅在 `snap.status === undefined`（缓存被外部失效）时只显示 stale 提示 + 等用户
  点刷新——刻意不自动重取，避免与写方的 invalidation 形成回环。

## 加载顺序（新入口宿主要求）

```
js/client/resource-store.js → js/client/task-view-query.js
js/workbench/contracts/task_view_contract.js
js/workbench/controllers/job_detail_controller.js   (软依赖：终态纯函数)
js/workbench/controllers/task_list_controller.js    (createTaskListController 工厂)
js/workbench/controllers/task_session_controller.js (createTaskSessionController 工厂)
js/modules/task-explorer/module.js
css/modules/task-explorer.css
```

`client.taskView` 的 adapter（`init({v1,v2})`）仍由宿主页面注入（旧页是 `compat/api.js`）；
新入口需自行注入或 mock。

## 测试

```bash
node frontend/js/module_tests/run_all.js acp2_module_task_explorer
```

覆盖：描述符形状；挂载+空态；mock 列表渲染行/徽章/attempt；行点击 setFocus + A→B→A token
防迟到；项目切换作废旧响应；双 factory 实例隔离；dispose 彻底+幂等+迟到写入作废+监听/
订阅计数归零；工厂化后兼容单例旧 API 冒烟；只读面（无写操作按钮）。
