# task-explorer 模块（UI-02b + A-17…A-24）

ACP 2.0 新实验入口（`frontend/next/`）的任务列表模块。经典脚本（无构建、无 ESM），经
`window.ACPNextModules["task-explorer"] = { descriptor, factory }` 注册，由 Shell
loader（UI-01 车道）消费。契约依据：
`docs/reference/next-change/ACP_Frontend_UI00_Module_Recipe_Contract_Draft_20261030.md`
§1/§2/§3/§5（schemaVersion 0-draft，未冻结，形状随实测修订）。

当前范围：**A-17 队列列表/状态筛选/状态徽标**、**A-18 pause/unpause/continue**、
**A-19 rerun/cancel**、**A-20 purge（单条 + 多选批量，两次确认）**、
**A-21 clone/move/rename**、**A-22 项目 CRUD（列表/新建/重命名/删除 + 依赖提示）**、
**A-23 标签管理（按任务 add_tags/remove_tags）**、**A-24 回收站视图 + 回收站批量操作
（恢复 / 彻底删除）**（Track A / V5）。A-25/A-26（阶段条 / 实时日志）不做；A-22/A-23
已落地行为在本阶段保持不动。

### 2026-11 UI 收口（NEXT）

- **两行工具栏**：第一行是任务标题、`关联任务｜项目全部` 分段和 4 个 26px 图标按钮
  （固定 / 回收站 / 刷新 / 项目）；第二行是 5 个聚合状态胶囊（全部 / 进行中 / 待干预 /
  已完成 / 异常）和紧凑排序入口。全局项目选择器是唯一权威，模块内的隐藏 `<select>` 只保留
  行为状态，不再画第二份选择器；旧的 counts 行已删除。
- **聚合与持久化**：`进行中 = running+starting+queued`、`待干预 =
  waiting_review+pending+paused`、`异常 = failed+cancelled+cancelling`；计数取
  `body.counts` 的整域值。排序默认 `activity_desc`，升序经后端 `activity_asc`；名称/任务类型
  使用本地 `Intl.Collator(undefined,{numeric:true})` 对已加载行排序，任务类型分组使用既有
  `group_by=workflow`。排序、分组方向、折叠组和工具栏查询都写入
  `acp-next-task-view-v3`（storage 命名空间与 Shell 一致）。
- **停靠**：任务模块实例不变；右侧/底部/抽屉只切 CSS 几何，宽度/高度/折叠状态在
  `acp-next-task-dock-v3`，由 `js/shell/global-regions.js` 管理。文件视图仍隐藏整个任务区。

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
  commands: { openTask, openStructure },             // Shell 命令总线（导航用，只用 openTask）
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
- **generation 守卫**：项目切换 / 回收站视图切换 / dispose 都 bump generation；迟到的列表、
  详情**以及命令回执（含 purge 报告、rename 冲突回执、clone/move 回执、回收站的
  restore/彻底删除回执）**整体作废（不再刷新、不再写 busy/error 状态、不再改选择）。
- **mount ≠ 数据就绪**：初始 fetch 是 fire-and-forget，结果经四态渲染器落地。
- **写命令是可选注入**：模块只消费 `client.jobCommands`；`deps.client` 缺省时才回落到
  `window.ACPClient`（不会另建命令客户端）。`client.jobCommands` 缺失 → 全部写按钮**和批量
  选择**隐藏；单个方法缺失 → 该按钮 disabled（title 提示不可用）；rename 在行缺少
  `name_revision` 时单独 disabled（其余 A-21 按钮照常）；`batchOp` 缺失时只有 A-23 标签按钮
  disabled（标签 chip 仍作为只读标签显示）。
- **项目请求是可选注入**：A-22 项目 CRUD 复用工具栏早已使用的项目请求缝
  （`client.request(path, opts)`，Shell 里回落到 `client.transport.request("/api/v1" + path,
  opts)`）。两者都缺失 → 「项目」按钮 disabled（title「当前客户端不支持项目管理」），
  项目对话框不会打开；任务列表读路径不受影响。

## 数据流（单缓存、零轮询）

- 列表数据只走注入 `client.taskView`（`ACPClient.taskView.fetchView / fetchSummary /
  snapshotView / subscribeView`），缓存语义由 `ACPClient.store` 提供——**模块不建第二套任务
  缓存**，不写任何页面全局（`#queue-list` / `jobsCache` / `taskViewCache` 一概不碰）。
- A-24 回收站**不是第二个列表**：它把同一个 `prefs.archived` 切到 `"only"`（队列默认
  `"exclude"`），该字段本来就在 `ACPClient.taskView.queryKeyOf` 里，所以切换只经
  `list.setOffset(0)` / `list.refresh()` 走同一个 store 查询
  （`GET /api/v2/task-view?archived=only`）。进入/离开回收站各只有一次列表查询，**没有第二个
  缓存、没有直接 fetch**。
- **不启动任何定时器/轮询**：刷新只有两个来源——① 工具栏显式刷新按钮；② store 订阅
  （`subscribeView`，外部失效/他人刷新 → 重绘缓存数据或提示 stale，不自动重取）。dispose
  取消全部订阅。
- 写入成功后一律 `list.refresh()`（同一 task-view/store 路径，保留当前 project/filter/
  offset），**没有第二套缓存、没有直接 fetch**；失败（reject）不刷新列表。
- 项目列表沿用旧端点 `GET /api/v1/projects`（`compat/api.js::api("/projects")` /
  `queue.js::loadProjects` 同一端点与 `body.projects` 形状），经注入 `client.request`（缺省
  `client.transport.request("/api/v1" + path)`）获取；**A-21 的 move/clone 目标项目只取自这份
  已加载列表**，不建 projects 客户端、不拼接路径。**A-22 的新建/重命名/删除沿用同一请求缝**
  （`POST /projects`、`PATCH /projects/{id}`、`DELETE /projects/{id}`，见下），成功后重新读取
  同一份 `GET /projects` 列表刷新工具栏选择器。
- A-22 删除前的依赖数量取自服务端既有端点 `GET /projects/{id}/jobs?limit=1000`（不是本地
  统计、不是新端点）：对话框显示任务数与进行中数量，删除后由服务端决定任务迁移/拒绝。
- A-23 标签写操作只经注入 `client.jobCommands.batchOp({ taskIds:[jobId], op:
  "add_tags"|"remove_tags", payload:{tags} })` → `POST /api/v2/tasks/batch-ops`（既有按任务
  标签端点）；标签只是标签，**永不进入路径/目录名**，成功后同样 `list.refresh()`。
- A-20 批量选择（`data-acp-select` 复选框 + 批量条）只保存**模块内 jobId 集合**，不缓存行
  数据；行离开当前结果集（筛选/翻页/换项目）时选择被丢弃，避免批量清除打到不可见任务。

## 列表控制器取舍结论（二选一 → 选了工厂实例）

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

模块 prefs = `{ groupBy:"molecule", sort:"created_desc", filters:{ status: [] },
archived:"exclude" }`（A-24 回收站把 `archived` 切到 `"only"`）；
`detailCacheRevision` 恒 0（模块无 detail cache——详情摘要直接来自 `fetchSummary`）。

## A-17 状态筛选与徽标

- **可见筛选器**：工具栏下一行状态 chip（`data-acp-filters="status"`），每个 chip 是
  `<button aria-pressed>`，文案 + `data-i18n`；另有「全部」chip。
- **词表唯一来源**：chip 在渲染时由 `ACPWorkbench.contracts.taskView.ACTIVE_STATUSES ∪
  TERMINAL_STATUSES`（= 后端 `JobStatus` 的 10 个值）生成，模块**不维护第二份状态清单**；
  `body.counts` 中已出现但契约尚未镜像的新状态会追加为额外 chip（标签回落为原始值），
  避免后端词表新增时被静默隐藏；仍在 `prefs.filters.status` 里的值即使计数消失也保留 chip，
  以免筛选变成无法关闭的孤儿。
- **计数**：chip 计数来自 `body.counts`（`task_views` 契约：counts 是全量口径，不受筛选/
  截断影响）；行徽标同时显示 `data-acp-status`（原始值）与本地化文案。
- **筛选走既有控制器**：点 chip → 新 prefs 对象（`filters.status`）→ `list.refresh()`；
  `ACPClient.taskView.queryKeyOf` 把 statuses 计入 query key，所以控制器自动重置 offset
  并走同一个 store 查询——**没有第二套缓存、没有直接 fetch**。

## A-18/A-19 行操作

行内按钮只由注入的 `client.jobCommands`（`js/client/job-commands.js`）驱动，状态矩阵与后端
命令契约一致（`manager.pause_job / unpause_job / continue_job / rerun_job / cancel`）：

| 行状态 | 可见操作 | 说明 |
|---|---|---|
| RUNNING | 暂停 + 取消 | pause 只对 RUNNING |
| PAUSED | 恢复 + 取消 | PAUSED 是 active 非终态：绝无 pause，绝无 rerun |
| QUEUED / STARTING / PENDING / CANCELLING / WAITING_REVIEW | 取消 | 全部非终态 → cancel 可用 |
| COMPLETED | 重跑 | 终态 → rerun（原地重跑） |
| FAILED / CANCELLED | 续算 + 重跑 | continue 仅 FAILED/CANCELLED；工作流不支持 checkpoint 时由服务端 409 文案就地显示 |
| 任何终态 | 无 cancel | cancel 只对非终态 |

这些状态按钮带 `data-acp-action`；A-20/A-21 的生命周期按钮带 `data-acp-lifecycle`（故意分开，
避免把「状态矩阵」和「生命周期命令」混成一张表）。

- **busy/error**：命令在飞时该行 `data-acp-action-state="busy"`、该行全部按钮 disabled、行内显示
  「暂停中…」等；reject 后 `data-acp-action-state="error"`，行内显示「暂停失败：<服务端
  guidance>」且按钮恢复可重试（`role="alert"`）。状态保存在模块 state 中，列表重绘不会丢。
- **成功后刷新**：走 `list.refresh()`（同一 task-view/store 路径，保留当前 project/filter/
  offset）；若被操作行正是当前选中任务，再用 `client.taskView.fetchSummary` 做一次
  generation/选中态双守卫的摘要刷新（**不**重新发布 focus）。
- **每个 job 同时只允许一个在飞命令**；`dispose`/项目切换后迟到的回执按 generation 丢弃。

## A-20 purge（单条 + 批量，两次确认）

**任何 purge 都需要两次明确确认**：第一次点击只打开模块内模态对话框（`role="dialog"`、
`aria-modal="true"`），第二次在对话框里点「确认清除」才发命令。行按钮与批量条按钮都不会
「一次点击即删除」。

- **唯一传输**：注入的 `client.jobCommands.purge({ jobIds, deleteData })`。
  - 恢复/新增端点一律不做：**不直接 `DELETE`、不自造 purge URL、不绕过 `purge_cascade`**
    （ANTI #13）。真实分发由 `ACPClient.jobCommands` → `POST /api/v1/jobs/purge` →
    `RuntimeCommands.purge` → `manager.purge_jobs` → `store.purge_cascade` 完成。
  - 单条与批量是同一条命令：单条 = `{ jobIds:[id] }`，批量 = 选中集合一次提交（一次请求，
    不是 N 次点击）。
- **批量入口**：每行复选框（`data-acp-select`，仅在 `purge` 可用时出现）+ 批量条
  `已选 N 项 / 全选 / 清空选择 / 批量清除`。批量条只在有选择时显示；选择是模块内 jobId 集合。
- **服务端语义为准**：模块**不复制**终态/active 判定。purge 响应
  `{results:[{job_id, ok, action, error}]}` 按 job 解析：
  - `ok:true` → 清除选择、显示汇总「已清除 N 个任务」；
  - `ok:false` → 该行显示 `清除失败：<服务端 error 原文>`（例如
    `job is active (status=running); use force_cancel to cancel it first`），
    汇总显示「已清除 X 个任务，Y 个失败」。
  - 响应形状未知但请求成功时按已清除处理（与遗留 purge 弹窗的宽容解析一致）。
- **delete_data**：对话框复选框默认 **false**（不删盘），勾选后透传 `deleteData:true`；
  `force_cancel` 不在注入命令的签名里，因此不提供「强制取消后删除」的客户端入口。
- **失败/重试**：请求 reject（HTTP/传输）时对话框保持打开、显示
  `清除失败：<原文>`（`role="alert"`），行内同样进入 error 态，**不刷新列表**；再次点确认
  重试。报告级失败（`ok:false`）不是请求失败，会按服务端报告就地显示并刷新列表。
- **成功后**：关闭对话框 → `list.refresh()`（单缓存路径）。被清除的任务若是当前选中项，
  模块只清理本地选择/详情占位（不猜测后续 id）。

## A-21 clone / move / rename

- **rename**（`client.jobCommands.rename(jobId, customName, expectedRevision)`）：
  对话框预填 `custom_name`；输入 trim 后为空且原本有自定义名 = 恢复默认名（传 `null`）；
  未修改时保存按钮 disabled；>200 字符/控制字符就地拒绝。
  **乐观锁**：`expectedRevision` 取自已渲染行（task-view 契约）的 `name_revision`，即
  `PATCH /api/v2/tasks/{id}` 的 `expected_name_revision`；行缺 `name_revision` 时该按钮单独
  disabled（`缺少名称版本（name_revision），无法重命名`）。409
  `{detail:{error:"name_revision_conflict", current:{name_revision}}}` → 显示
  「名称已被修改…」、采用服务端返回的 revision、并通过控制器刷新列表，对话框保持打开可直接
  重试（不重放旧请求）。
- **move / clone**（`client.jobCommands.move(jobId, projectId)` /
  `.clone(jobId, projectId)`）：对话框的目标项目下拉**只来自已加载的项目列表**
  （`GET /projects`，与项目筛选同一份 `state.projects`）；无项目时显示「暂无可用项目」且确认
  按钮 disabled。不构造目录/路径，克隆后的新任务 id 由服务端返回（模块只刷新列表）。
- **busy/error/刷新**：与 A-18/A-19 共用同一套 busy/error 状态机与 `list.refresh()`；
  rename/move/clone 成功后若该行是当前选中任务，同样做一次受守卫的摘要刷新。
- 服务端拒绝（移动 active 任务、项目不存在、名称冲突等）一律显示服务端/映射后的错误文本，
  客户端不做第二套许可判定。

## A-22 项目 CRUD

工具栏「项目」按钮打开**项目管理对话框**（`role="dialog"`、`aria-modal="true"`），列出
`GET /projects` 已加载的项目（与项目选择器同一份 `state.projects`），并提供新建/重命名/删除：

| 操作 | 唯一传输（既有端点，无自造路径） | 成功行为 |
|---|---|---|
| 列表 | `GET /projects`（工具栏同一请求缝） | 只渲染已加载列表 |
| 新建 | `POST /projects` body `{name}` | 服务端返回 `project_id` → 重新读取项目列表 → 经工具栏同一路径切换作用域（发布 `setFocus({projectId})`，`list.refresh()`） |
| 重命名 | `PATCH /projects/{id}` body `{name}` | 重新读取项目列表 → 回到管理列表并显示成功提示（不需要重取任务列表） |
| 删除 | `DELETE /projects/{id}`（不传 `delete_data`，即服务端默认 false） | 重新读取项目列表；若被删项目正是当前作用域 → 切回「全部项目」经同一路径刷新；否则 `list.refresh()`；随后回到管理列表 |

- **删除前有依赖提示**：打开删除对话框时请求既有端点 `GET /projects/{id}/jobs?limit=1000`，
  显示「该项目包含 N 个任务，其中 M 个进行中」；请求失败降级为「无法获取依赖数量（服务端会
  校验）」，不阻塞删除。进行中任务/默认项目由服务端拒绝（`400` 原文显示，例如
  `Cannot delete project with active jobs: [...]`），客户端不复制这套判定。
- **非破坏性默认**：不传 `delete_data`，服务端的语义是把该项目任务移动回默认项目并删除项目
  目录记录；因此对话框明确写「不会删除任务数据」。A-22 仍不提供项目级删盘
  （`delete_data=true`）；任务级的可恢复删除由 A-24 的回收站另行提供（两件事不混）。
- **busy/error**：单飞（`dialog.busy`）；请求在飞时新建/重命名/删除按钮与输入框 disabled；
  reject 时对话框保持打开、`role="alert"` 内联显示「创建/重命名/删除项目失败：<原文>」，可重试；
  失败不刷新任何列表。
- **generation/dispose 守卫**：打开删除对话框时的依赖查询、以及全部 mutation 回执都带
  generation 快照；项目切换/dispose 后迟到的回执不写 DOM、不刷新、不改选择器。用户在请求在飞
  时关闭对话框不会复活对话框；create/rename/delete 的成功回执仍会重读项目列表（只选一次
  `GET /projects`，不隐式切换作用域），失败回执则完全丢弃。
- **选择器/列表刷新**：项目 CRUD 不建第二份项目缓存；创建后自动进入新项目（与手动选择项目
  同一 `applyProjectId` + `list.refresh()` 路径；对话框已关闭时只刷新选择器），删除当前项目
  切回「全部项目」并发布 `setFocus({projectId:null})`。

## A-23 标签管理

标签是**分组/筛选用的标签**，不是路径：模块只发送标签字符串，从不把它们拼进目录名、文件
路径或任务 id。

- **行内只读 chip**：task-view 行的 `tags[]` 原样渲染为 `data-acp-tag` chip（即使没有命令
  客户端也可读）。
- **编辑器**：每行「标签」按钮（`data-acp-tags`，独立于 `data-acp-action` /
  `data-acp-lifecycle`）打开 `role="dialog"` 编辑器：当前标签 chip（每个带移除按钮）+ 输入框 +
  「添加」。
- **唯一传输**：注入的 `client.jobCommands.batchOp({taskIds:[jobId], op:"add_tags"|"remove_tags",
  payload:{tags:[tag]}})` → `POST /api/v2/tasks/batch-ops`（既有按任务标签端点；项目级
  `tags/rename|merge|delete` 注册表与 legacy `tag-mgmt-modal` **不在此范围**）。不直接 fetch、
  不自造 `/jobs/{id}/tags` 路径。
- **服务端错误内联**：batch-ops 的 200 响应是逐任务报告；`results[0].ok:false`（例如
  `tags must contain at most 20 items`）在对话框 `role="alert"` 内联显示并标记该行 error，
  **不刷新**；传输 reject 同样内联显示 `err.detail/message` 原文。成功后清空输入、编辑器标签
  就地更新，并 `list.refresh()` 走同一 task-view/store 路径；行 chip 随重读的行更新。
- **busy/单飞**：每个 job 同时只允许一条命令（标签写入复用 A-18…A-21 的 `beginBusy`），
  编辑器和该行按钮一起 disabled；行内显示「更新标签中…」。
- **缺省/部分客户端**：无 `client.jobCommands` → 不渲染标签按钮；有命令客户端但缺 `batchOp`
  → 只有标签按钮 disabled（title「当前客户端不支持标签管理」）。

## A-24 回收站与批量操作

### 后端事实（2026-10-31 实读，**不是**假设）

**平台没有任务级回收站端点**：`src/acp/api/v1_routes.py` / `v2_routes.py` 中不存在
`/trash`、`/jobs/trash` 或任何 deleted-jobs 列表路由（全仓唯一 `trash` 路由是
`POST /api/v2/structure-sources/trash/purge`，属于**结构来源候选**，与任务无关）。
`manager.purge_jobs` / `delete_job` → `JobStore.purge_cascade` 直接删除
`stage_tasks / artifacts / tasks / decision_points / mechanism_studies / jobs` 行
（`delete_data=true` 才删盘），**被 purge 的任务无法被任何 API 列出或恢复**。

因此本模块**不发明路径**，回收站视图建立在这条**唯一可恢复的既有状态**上：

| 语义 | 唯一传输（既有端点/既有命令） | 说明 |
|---|---|---|
| 回收站列表 | `GET /api/v2/task-view?archived=only` | 同一 `client.taskView` 查询键（`prefs.archived`），非新端点、非新缓存 |
| 恢复 | `client.jobCommands.batchOp({taskIds, op:"unarchive"})` → `POST /api/v2/tasks/batch-ops` | `v2_routes._execute_batch_op` 的既有 `unarchive` op（legacy 队列「取消归档」同一命令） |
| 彻底删除 | `client.jobCommands.purge({jobIds, deleteData})` → `POST /api/v1/jobs/purge` → `purge_cascade` | 与 A-20 **同一条**命令族；绝不 `DELETE`、绝不自造 cascade（ANTI #13） |
| 移入回收站 | （不在本模块） | 既有 `batch-ops op:"archive"`（legacy 队列「归档」）。本任务要求**正常队列视图不变**，故不在队列行加归档入口；服务端 `_validate_archive_targets` 只允许终态任务 |

> **能力边界**：回收站只包含「已归档（软删除）」任务。已被 A-20 清除的任务由
> `purge_cascade` 删除记录，**不可恢复、不可列出**——UI 在回收站说明条与「彻底删除」确认框里
> 都写明这一点，不把 purge 描述成可撤销。若产品需要“删除后可恢复”，须后端新增任务级
> tombstone/trash 表与列表/恢复路由（BE-GAP，本任务无权改后端）。

### 视图与交互

- **入口**：工具栏「回收站」按钮（`data-acp-trash-toggle`，`aria-pressed` 反映模式；进入后
  文案变「返回任务列表」）。根节点 `data-acp-trash="on|off"`，正文容器
  `data-acp-trash-scope="archived|queue"`。
- **四态**：loading / empty（回收站专用文案 `next.tasks.trash.empty`「回收站为空」）/ error
  （可重试，重试**保持在回收站作用域**，不会静默回落到队列查询）/ forbidden。说明条
  （`data-acp-trash-note`）在回收站内始终可见，写明“已清除任务不可找回”。
- **进入/离开**：`setTrashMode` 改 `prefs.archived`（`"only"` / `"exclude"`）→ bump
  generation → 清空选择/busy/error/batch 汇总/对话框 → `list.setOffset(0)`（一次查询，走
  拥有的控制器）。切项目**保持**回收站模式；`dispose` 后再 `mount` 一律回到队列。
- **回收站行**：`data-acp-trash-row="true"` + `data-acp-archived`；只渲染「恢复」
  （`data-acp-trash-action="restore"`）与「彻底删除」（`="delete"`）。A-18/A-19 状态矩阵、
  A-20/A-21 生命周期命令、A-23 标签编辑器**都不在回收站行出现**（archived 任务已是终态，
  这些不是回收站操作）。
- **恢复 = 非破坏性**：单击即发 `unarchive`（可再次归档，不删数据），成功后
  `list.refresh()`，汇总显示「已恢复 N 个任务」；服务端逐任务 `ok:false` 原文内联、**不刷新**；
  传输失败显示 `恢复失败：<原文>`、不刷新。
- **彻底删除 = 两次确认**：行/批量按钮只打开 `role="dialog"`、`aria-modal="true"` 的
  `trash-delete` 对话框（标题「确认彻底删除」、正文点名任务/数量与 `purge_cascade`、警示
  「此操作不可恢复」、`delete_data` 复选框默认 false），第二次点「彻底删除」才发
  `client.jobCommands.purge`。请求失败对话框保持打开、`role="alert"` 内联
  `彻底删除失败：<原文>`、不刷新、可重试；报告级 `ok:false`（例如服务端拒绝）按原文显示并刷新。
- **批量只作用于回收站行**：复选框只在当前作用域有批量命令时出现（队列 = A-20 purge；
  回收站 = restore/purge）。进入/离开回收站清空选择（队列的 A-20 选择不会带进回收站）；
  批量条 `data-acp-batch-bar="trash"`，按钮为「批量恢复」`data-acp-batch="restore"` 与
  「批量彻底删除」`data-acp-batch="trash-delete"`，**队列的 `purge` 批量按钮不会出现在回收站**。
  `runTrashRestore` / `openTrashDeleteDialog` 对每个 id 先过 `isTrashJob`（当前 body 内、
  且 `archived !== false`），任一不满足即整批拒绝——不会打到队列任务。
- **busy/单飞**：复用 A-18…A-21 的 `beginBusy`/`failBusy`/`syncRowActions`（每个 job 同时
  只允许一条命令），批量为单飞（`trashInFlight`/`purgeInFlight`）。
- **缺省/部分客户端**：无 `client.jobCommands` → 回收站仍可读，但**零**写按钮、**零**复选框，
  说明条显式显示 `next.tasks.trash.readonly`（只读）；有命令客户端但缺 `batchOp` → 只有
  「恢复」disabled（title「当前客户端不支持该回收站操作」），「彻底删除」与批量仍可用。
- **generation/dispose**：在飞的 restore/彻底删除回执、以及离开回收站后才到达的 archived
  列表响应，全部按 generation 作废；`dispose` 后不写 DOM、不刷新、监听器计数归零。

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
  `contracts.taskView.isTerminalStatus`）；PAUSED 永不被当作终态；
- 四态自渲染：loading（骨架行）/ empty / error（可重试）/ forbidden（401/403）；文案 zh +
  `data-i18n`（key 前缀 `next.tasks.*`）。

## 边界（刻意不做）

- **任务级回收站端点不存在，本模块不造**：A-24 的“回收站”= 既有 `archived` 软删除集合
  （见上节）。被 purge 的任务由 `purge_cascade` 删除记录，**不可列出/不可恢复**；若产品要求
  “误删可恢复”也覆盖 purge，后端必须先提供任务级 trash/tombstone 与列表/恢复路由——这是
  BE-GAP，本任务的文件所有权不允许改后端。**正常队列视图保持原样**：不在队列行新增
  “移入回收站（archive）”入口，归档入口仍属既有 legacy 队列（服务端 `archive` op 不变）。
- **A-22 只是项目自身的 CRUD**：move/clone 仍只是**选择已有项目**；不做项目级标签注册表
  （legacy `tag-mgmt-modal` 的 `tags/rename|merge|delete` 项目级操作不在本模块范围）。
- **A-23 只做按任务的 add/remove**：`client.jobCommands.batchOp` 只用于
  `add_tags|remove_tags`；回收站恢复新增使用同一 batch-ops 的 `unarchive`，但**不新增**
  `archive|set_molecule_name` 等队列侧 op；purge 统一走 `client.jobCommands.purge`，不得用
  batchOp 自造删除 op。
- **A-25/A-26 不做**：阶段进度条与实时日志属于 exec-detail 车道（另有模块），本模块不写
  `stage_*` 展示逻辑、不轮询 `/logs`。
- **项目依赖数量**来自既有 `GET /projects/{id}/jobs?limit=1000`（满页显示「至少 N 个」）；
  不自造删除预览端点，也不在本地维护第二份任务统计。
- 无保存视图 / 搜索 / workflow/molecule 等多维筛选——本阶段只做 A-17 的**状态**筛选。
- `supportedInstancing: "single"`：Shell 装配层强制单实例；execution 工作区的
  「exec-tasks + exec-detail 两个槽位」由后续阶段决定是双视图单实例还是 detail 独立模块
  （UI-01 §8 未决项）。
- store 订阅在 `snap.status === undefined`（缓存被外部失效）时只显示 stale 提示 + 等用户
  点刷新——刻意不自动重取，避免与写方的 invalidation 形成回环；写命令成功后的刷新是显式
  的 `list.refresh()`，不是轮询。

## i18n keys（本阶段新增，需同步 `frontend/js/shell/i18n.js` zh/en）

A-17/A-18/A-19 既有键（见上一版）：`next.tasks.filter_status`、`next.tasks.filter_all`、
`next.tasks.status.{queued,starting,pending,running,paused,cancelling,waiting_review,
completed,failed,cancelled}`、`next.tasks.actions_label`、
`next.tasks.action.{pause,unpause,continue,rerun,cancel}`、`next.tasks.action_busy`（`{action}`）、
`next.tasks.action_failed`（`{action}`/`{message}`）、`next.tasks.action_unavailable`、
`next.tasks.action_unknown_error`。

A-20/A-21 新增：

- 动作：`next.tasks.action.{purge,clone,move,rename}`、`next.tasks.action.purge_title`、
  `next.tasks.action_in_flight`；
- 批量：`next.tasks.select_job`、`next.tasks.batch.selected`（`{n}`）、
  `next.tasks.batch.{select_all,clear,purge}`、`next.tasks.purge_report`（`{ok}`/`{fail}`）、
  `next.tasks.purge_report_ok`（`{count}`）、`next.tasks.purge_error`（`{message}`）；
- 对话框：`next.tasks.dialog.{purge_title,purge_single,purge_batch,delete_data,purge_confirm,
  purge_prompt,cancel,rename_title,rename_label,rename_placeholder,rename_save,rename_invalid,
  rename_unavailable,rename_conflict,clone_title,move_title,project_label,clone_confirm,
  move_confirm,no_projects}`。

A-22/A-23 新增：

- 项目：`next.tasks.projects.{manage,title,new_placeholder,create,name_label,rename,rename_title,
  save,delete,delete_title,delete_confirm,delete_warning,deps_loading,deps,deps_active,deps_more,
  deps_active_more,deps_unknown,deps_error,name_invalid,create_failed,rename_failed,delete_failed,
  created,renamed,deleted,empty,unavailable}`；
- 标签：`next.tasks.tags.{manage,title,empty,placeholder,add,remove,unavailable}` 与
  `next.tasks.action.tags`（行内 busy/error 文案复用 `next.tasks.action_busy` /
  `next.tasks.action_failed`）；
- 共享对话框关闭：`next.tasks.dialog.close`。

A-24 新增：

- 回收站：`next.tasks.trash.{open,close,title,toggle_label,note,readonly,empty,restore,delete,
  unavailable,report_ok,report,restore_failed,delete_report_ok,delete_report,delete_failed}`
  （`report_ok`/`delete_report_ok` 用 `{count}`；`report`/`delete_report` 用 `{ok}`/`{fail}`；
  `restore_failed`/`delete_failed` 用 `{message}`）；
- 批量：`next.tasks.batch.{restore,trash_delete}`；
- 对话框：`next.tasks.dialog.{trash_delete_title,trash_delete_single,trash_delete_batch,
  trash_delete_prompt,trash_delete_confirm}`（单条用 `{name}`，批量用 `{count}`）；
- 行内 busy/error 复用 `next.tasks.action_busy` / `next.tasks.action_failed`（label 来自
  `next.tasks.trash.restore` / `next.tasks.trash.delete`）。

> 本模块只在自己的 `ZH` 字典里提供 zh 文案 + `data-i18n` 属性；`frontend/js/shell/i18n.js`
> 不在本文件的所有权范围内，EN 键与词典同步需由 shell 车道补齐。

## 加载顺序（新入口宿主要求）

```
js/client/resource-store.js → js/client/task-view-query.js
js/client/job-commands.js                           (可选注入：client.jobCommands)
js/workbench/contracts/task_view_contract.js
js/workbench/controllers/job_detail_controller.js   (软依赖：终态纯函数)
js/workbench/controllers/task_list_controller.js    (createTaskListController 工厂)
js/workbench/controllers/task_session_controller.js (createTaskSessionController 工厂)
js/modules/task-explorer/module.js
css/modules/task-explorer.css
```

`frontend/next/index.html` 已加载 `js/client/job-commands.js`，`shell/bootstrap.js` 已调用
`ACPClient.jobCommands.init({ v1, v2 })`——**本阶段无需改 index/recipe**；只有 i18n 键需要
补进 shell 词典。`client.taskView` 的 adapter（`init({v1,v2})`）仍由宿主页面注入。

## 测试

```bash
node frontend/js/module_tests/run_all.js acp2_module_task_explorer
```

当前 **58 项**（A-17…A-23 的 48 项全部保持通过 + A-24 新增 10 项）。原有 20 项（描述符；
挂载+空态；行渲染；行点击 setFocus + A→B→A 防迟到；
项目切换作废旧响应；双实例隔离；dispose 彻底+幂等+迟到写入作废；工厂化兼容；A-17 状态
chip/计数/筛选/offset；A-18/A-19 状态矩阵与 PAUSED active；pause busy→刷新；unpause/
continue 接线；rerun/cancel 边界；真实 `ACPClient.jobCommands` v1 接线；失败 error 态可重试；
缺失命令客户端全隐藏；部分缺失仅该按钮 disabled；项目切换迟到回执；成功后摘要刷新）
**全部保持通过**；A-20/A-21 的 13 项继续通过：

- A-20 单条 purge：一次点击只开对话框、二次确认才发命令、busy、报告成功→关闭+刷新、
  delete_data 默认 false；
- A-20 批量 purge：复选框/批量条计数、二次确认、一次请求覆盖选中集合、服务端
  `skipped_active` 原文就地显示、部分成功汇总、选择清空；
- A-20 delete_data 勾选才透传；
- A-20 请求失败：对话框保持打开 + error 文案、不刷新、可重试；
- A-21 clone/move：项目下拉来自已加载项目列表、传参、busy、成功刷新；
- A-21 rename：预填、未修改不可保存、trim、`name_revision` → `expected_name_revision`、
  空值恢复默认名（`null`）、成功后按刷新行的新 revision 再改；
- A-21 rename 409：显示冲突、采用服务端 revision、刷新列表、对话框内重试成功；
- 缺失 `client.jobCommands`：生命周期按钮与批量选择全隐藏且不回落页面全局；
- 部分客户端：仅缺失方法 disabled（缺失 purge 时批量条消失），rename 缺 `name_revision`
  单独 disabled；
- 同一行不会并发第二条命令：purge 双击确认只发一次；行 busy 时生命周期按钮拒绝开新命令；
  已打开对话框在行变 busy 时拒绝提交、行空闲后重试成功；
- dispose 时在飞命令（purge/rename）迟到 resolve/reject 不写 DOM、不刷新、监听器计数归零；
- 项目切换后迟到 purge 回执不刷新新项目、不泄漏 busy/选择状态；
- 真实 `ACPClient.jobCommands` 接线：`POST /jobs/purge`（`job_ids`/`delete_data`）、
  `PATCH /tasks/{id}`（`custom_name`/`expected_name_revision`）、`POST /jobs/{id}/clone`、
  `POST /jobs/{id}/move`，且无任何 `DELETE`。

A-22/A-23 新增 15 项：

- A-22 项目列表/新建：管理对话框只读已加载 `GET /projects` 列表、「项目」入口按 transport
  可用性开关、空名不可创建、`POST /projects` body `{name}`、busy 单飞、服务端返回新 id 后
  经工具栏同一路径切换作用域并 `list.refresh()`（选择器与列表都刷新）；
- A-22 新建在飞时关闭对话框：迟到成功只重读选择器、不复活对话框、不隐式切作用域、不重取任务；
- A-22 重命名：`PATCH /projects/{id}` body `{name}`、未修改不可保存、成功重读列表回到管理
  对话框并提示、不重取任务列表；
- A-22 删除依赖提示：打开对话框请求既有 `GET /projects/{id}/jobs?limit=1000`、显示
  「N 个任务，其中 M 个进行中」、二次确认前不发 `DELETE`、确认后 `DELETE /projects/{id}`、
  删除当前项目切回全部项目（`setFocus({projectId:null})` + 控制器刷新）；
- A-22 删除失败：`400 Cannot delete project with active jobs` 原文内联、对话框保持打开、
  不刷新、可重试成功；
- A-22 缺失请求 transport：「项目」按钮 disabled + title，不打开空对话框；
- A-22 真实 Shell 接线：列表/新建都走 `transport.request` 的 `/api/v1/projects`（无自造路径）；
- A-22 dispose 时在飞创建回执不刷新、监听器归零；
- A-23 标签添加：行 chip 来自 task-view `tags[]`、编辑器展示当前标签、
  `batchOp({taskIds,op:"add_tags",payload:{tags}})`、trim、busy、成功后编辑器与行 chip 更新并
  经 task-view/store 刷新；
- A-23 标签移除：`remove_tags`、成功刷新后行 chip 更新；
- A-23 服务端逐任务拒绝（`results[0].ok:false`）：原文内联、行 error、不刷新、可重试；
- A-23 传输失败：`err.detail` 原文内联、对话框保持打开、输入保留、不刷新；
- A-23 缺失命令客户端：标签按钮隐藏但 chip 仍只读可见；缺 `batchOp` 时仅标签按钮 disabled；
- A-23 项目切换后迟到标签回执不刷新新项目、不泄漏 busy 状态；
- A-23 真实 `ACPClient.jobCommands` 接线：`POST /tasks/batch-ops`
  （`task_ids`/`op`/`payload.tags`），且标签写操作不发 `DELETE`。

A-24 新增 10 项：

- 回收站列表（空/就绪）：工具栏开关 `aria-pressed`、根 `data-acp-trash`、说明条只在回收站内
  可见；进入时**只有一次**列表查询且 `prefs.archived === "only"`（无写请求）；archived 行带
  `data-acp-trash-row`，只出现「恢复/彻底删除」，没有 A-18/A-19 状态命令、没有 A-20/A-21
  生命周期命令、没有标签编辑器；离开后队列行恢复且 `prefs.archived === "exclude"`；
  空回收站显示「回收站为空」专用文案且不发任何命令；
- 回收站 error：列表 reject → error 态 + 重试按钮；重试仍带 `archived:"only"`（不静默回落
  队列），成功后渲染 archived 行；
- 恢复：`batchOp({taskIds:[id], op:"unarchive", payload:{}})` 一次调用、busy（行内按钮
  disabled）、回执前不刷新、成功后经控制器刷新且刷新仍带 `archived:"only"`、汇总
  「已恢复 N 个任务」；
- 彻底删除确认：一次点击只开 `trash-delete` 对话框（`role="dialog"`/`aria-modal`、标题
  「确认彻底删除」、正文点名任务与 `purge_cascade`、`delete_data` 默认 false 且勾选才透传）、
  取消不发命令；二次确认才发 `purge({jobIds, deleteData})`、busy、回执后关闭 + 刷新
  （仍 `archived:"only"`）、汇总「已彻底删除 N 个任务」；全流程无 `DELETE`；
- 彻底删除失败：对话框保持打开、`role="alert"` 内联「彻底删除失败：<原文>」、行 error、
  **不刷新**、确认按钮可重试且重试成功；
- 批量只在回收站作用域：进入回收站清空队列选择（不泄漏）、批量条
  `data-acp-batch-bar="trash"`、无队列 `purge` 按钮；批量恢复一次 `unarchive` 覆盖选中集合，
  服务端逐任务 `ok:false` 原文内联 + 部分成功汇总 + 刷新后行 error；批量彻底删除二次确认后
  一次 `purge`（`deleteData:false`）、汇总、清空选择；
- 缺省/部分客户端：无 `jobCommands` → 回收站可读、零写按钮/零复选框、显式只读说明；
  缺 `batchOp` → 仅「恢复」disabled（title「当前客户端不支持该回收站操作」），
  「彻底删除」与批量选择仍可用；
- 迟到/销毁守卫：restore 在飞时 dispose → 迟到 resolve 不刷新、监听器计数归零；彻底删除
  在飞时 dispose → 迟到 reject 不写 DOM；离开回收站后迟到的 archived 列表响应不渲染进队列，
  实时队列查询胜出；
- 切项目保持回收站作用域：generation bump 后新查询仍带 `archived:"only"` 与新的
  `projectId`，开关 `aria-pressed`/说明条保持，新项目的 archived 行照常渲染；
- 真实 `ACPClient.jobCommands` 接线：恢复 → `POST /tasks/batch-ops`
  （`task_ids`/`op:"unarchive"`/`payload:{}`），彻底删除 → `POST /jobs/purge`
  （`job_ids`/`delete_data`），且回收站操作不发 `DELETE`。
