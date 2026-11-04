# task-config — ACP Next 任务配置模块（UI-03 + A-13/A-14/A-15）

- 文件：`frontend/js/modules/task-config/{model.js,module.js}`（classic script，无构建、无 ESM）
- 样式：`frontend/css/modules/task-config.css`（仅 `.acp-cfg*` 作用域，复用 `base.css` 变量）
- 测试：`frontend/js/module_tests/acp2_module_task_config.test.js`
  （`node frontend/js/module_tests/run_all.js acp2_module_task_config`）
- 注册名：`window.ACPNextModules["task-config"]`（`descriptor.version = "0.2.0"`）

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["task-config"] = {
  descriptor: {
    id: "task-config", interfaceVersion: 1, version: "0.2.0",
    provides: ["task.config"], binds: ["projectId"],
    capabilities: { requires: [], provides: ["task.config"] },
    hosts: ["acp-web"], supportedInstancing: "single",
    configSchemaVersion: 1,
    error: { titleKey: "next.config.errorTitle", messageKey: "next.config.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise`、`updateContext(snapshot)`、`suspend()`、
`resume()`、`dispose()`。

- 依赖注入：`deps = { model?, request?, doc?, context?, commands? }`。
  `request` 缺省回落 `ACPClient.transport.request`；**不定义 fetch**，模块自身无直接网络调用。
- `request`/`doc`/`model` 缺失时 `mount` 立即抛错（测试覆盖 “missing client”）。
- 所有异步结果带 **generation + per-action token** 守卫；任务切换 / dispose 后迟到结果不写 DOM、
  不发命令。
- 提交路径（`/validate-method` → `/preflight` → `POST /jobs`）沿用 UI-03 冻结契约，
  本次 A-13/A-14/A-15 的 **结构来源面板不提交任何任务**。

## 2. A-13 结构来源列表（本地 / 远程 / 历史任务）

结构区新增一个默认折叠的 `<details data-role="source-picker">`，展开后按三个来源分组。
全部走**现有端点**（未新增/发明路径）：

| 分组 | 列表端点 | 单源文本端点（每源恰好 1 次） | 来源事实 |
|---|---|---|---|
| 历史任务结构 | `GET /api/v1/structure-sources/recent?project_id=…\|all_projects=true&limit=20` | `GET /api/v1/structure-sources/{source_id}`（`structure.xyz`） | `StructureSourceSummary`（`job_status` / `source_kind` / `remote` / `needs_fetch`） |
| 任务产物（本地） | `GET /api/v1/jobs/{jobId}/structure-viewer`（catalog `groups/entries`） | `GET /api/v1/jobs/{jobId}/structure-viewer/entries/{entryId}/geometry` | 条目 `role`/`status`/`energy`/`geometry.endpoint` |
| 远程文件 | `GET /api/v1/jobs/{jobId}/remote-files` | `GET /api/v1/jobs/{jobId}/remote-files/{path}/preview?mode=structure`（`{content}`） | 只列 `.xyz` 非目录项 |

- `/jobs/{id}/structure-sources` **在当前后端不存在**，因此没有使用；历史来源走
  `structure-sources` 真实路由，任务产物/远程文件走既有任务读取路由（已逐条核对 `v1_routes.py`）。
- 任务产物/远程文件分组需要 Shell `taskRef.jobId`；未绑定任务时显示空态提示，不发请求。
- **可用性按行显示**：历史 `needs_fetch` → “需取回”；远端 → “远程”；
  catalog 条目无 `geometry.endpoint` → “不可用”且按钮禁用并给出原因；
  载入失败的来源在行内保留并显示真实错误（可再次点击重试），**从不生成假行**。
- 列表失败（500/网络）→ 分组 error 态 + 重试按钮；`remote-files` 的 400（非远程任务）/
  503（未启用远程）是**空态 + 明确原因**，不是伪造错误行。
- 无任何 fixture 行；`data-acp-fixture` 不在此面板出现。

## 3. A-14 多帧选择与预览

- 选中一个来源后，**只发一次**文本请求，帧表由这份文本经
  `ACPView.model.parseXyzFrames`（`view_model.js`，唯一解析器）派生；选帧/上下一帧
  **不再发第二次几何/缓存请求**（测试断言 preview 请求计数不变）。
- UI：`帧` 下拉（`data-role="frame-select"`，选项 `帧 N`，有能量时附 `… Eh`）、
  `共 N 帧`、`上一帧`/`下一帧`、选择身份预览。
- **越界钳制**：`index < 0 → 0`，`index ≥ N → N-1`，并在预览里显式写“请求帧越界，已钳制到末帧 N”
  （不静默）；更晚的显式切帧会清除该提示。
- 预览身份（`data-role="source-preview"`）包含：
  `帧 i/N · {kind} · jobId · path|entryId`（公式/原子数有则追加），并带
  `data-acp-frame-index` / `data-acp-frame-count` / `data-acp-source-kind` /
  `data-acp-source-ref`（`kind:jobId:path#frame`）/ `data-acp-source-emitted`。

## 4. A-15 structureRef 桥接

- 唯一出口 `emitStructureRef(ref)`：先校验/归一化为 molecule-viewer README §2 冻结形状，
  再优先调用 Shell `commands.openStructure({ source: "task-config", …ref })`
  （该命令本身写 `WorkspaceContext` 并投递给已挂载 viewer），否则回落
  `context.setFocus({ structureRef }, "task-config")`。**不写 viewer DOM、不创建第二个 viewer。**
- 四种 canonical ref（均由本模块可产出）：

```js
{ kind: "job-file",         jobId, path, frameIndex }        // 历史本地来源
{ kind: "job-remote-file",  jobId, path, frameIndex }        // 历史远程来源 / 远程文件
{ kind: "structure-entry",  jobId, entryId, frameIndex }     // 任务产物（catalog 条目）
{ kind: "inline-xyz",       text, frameIndex }               // 既有粘贴/解析几何
```

- `frameIndex` 是“所选来源文本内的帧序号”，0 起，越界由本模块先钳制；viewer 侧仍会按
  `molecule-viewer` 契约再次钳制（兼容多入口）。
- `source` 是 Shell 命令载荷的附加字段（与 `energy-profile` 一致），不属于 geometry 身份；
  `molecule-viewer::normalizeStructureRef` 忽略它。
- 任务切换 / dispose 时 `structureRef` 通道不会被本模块二次写入；迟到的选择结果不会发 ref。

## 5. 四态与生命周期

| 面 | 状态载体 | 说明 |
|---|---|---|
| 模块整体 | `data-acp-module-state` | UI-03 既有 `loading/ready/error` |
| 结构来源分组 | `data-acp-source-state`（面板/列表） | `loading / ready / empty / error`（`empty` 含明确原因） |
| 单个来源行 | `data-acp-source-state`（`li`） | `ready / pending / error` |
| 帧选择 | `data-acp-source-preview[data-acp-source-state]` | `loading / ready / error` |

- 展开分组时才加载（无隐藏请求）；同一分组已加载不重复请求；error 态提供重试。
- `dispose`：清计时器/监听/订阅、清 host、重置来源状态并递增 token；迟到响应全部作废。
- `projectId` 变化会重置历史分组；`taskRef.jobId` 变化会重置任务产物/远程分组并按需重载。

## 6. i18n 键（zh 默认值在 `module.js`，`data-i18n` 供 Shell 切换）

`next.config.sources_title` · `sources_hint` · `sources_tab_history` · `sources_tab_local` ·
`sources_tab_remote` · `sources_idle` · `sources_loading` · `sources_empty_history` ·
`sources_empty_local` · `sources_empty_remote` · `sources_empty_no_task` · `sources_error` ·
`sources_retry` · `sources_pending` · `sources_remote_chip` · `sources_unavailable` ·
`sources_source_loading` · `sources_source_error` · `sources_row_error` · `sources_no_text` ·
`sources_parser_missing` · `sources_parse_error` · `sources_no_geometry` ·
`sources_pending_catalog` · `sources_remote_not_remote` · `sources_remote_disabled` ·
`sources_source_frame` · `sources_atoms` · `sources_clamped` · `sources_emitted` ·
`sources_not_emitted` · `frames_title` · `frames_count` · `frames_option` · `frames_prev` ·
`frames_next` · `frame_identity` · `frame_identity_entry`（均以 `next.config.` 为前缀）

## 7. 已知缺口 / 边界

- 来源面板只做“引用注入”，**不把来源文本回填到结构输入框**，也不改写提交载荷
  （结构输入本身属于 A-02/A-06；本批不含，避免越权改动提交契约）。
- 历史来源经 `/structure-sources/{id}` 返回服务端规范化后的单个几何帧（后端
  `single_geometry` 策略），因此历史来源通常显示 1 帧；多帧主要来自 catalog 条目几何
  文件或远程 preview 的整文件文本。
- 只在 Node vm 沙箱验证（23 项）；`/next/` 真实浏览器 click-through 与真实服务未执行，
  不记为 PASS。
- 远程文件列表只列任务根目录一层（端点默认语义），不含子目录递归。
- 不提供 MOL/SDF 帧解析（与 `molecule-viewer` P0 缺口一致，仅 XYZ）。
- A-13 的“本地任务产物”使用 structure-viewer catalog（任务结构产物索引）；
  `/jobs/{id}/files` 原始文件树不在本批范围。
