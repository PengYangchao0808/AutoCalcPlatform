# exec-detail — ACP 3.0 任务详情 / 证据面板（UI-03 + A-25 / A-26 / A-27 / A-28）

- 文件：`frontend/js/modules/exec-detail/module.js`
- 样式：`frontend/css/modules/exec-detail.css`
- 测试：`frontend/js/module_tests/acp2_module_exec_detail.test.js`（27 项）
- 注册名：`window.ACPNextModules["exec-detail"]`；descriptor `version: "0.4.0"`,
  `provides: ["task.detail", "task.evidence"]`
- 装配：`frontend/next/index.html` 加载 `exec-detail.css` 与
  `js/modules/exec-detail/module.js`；数据依赖由 Shell 注入
  （`jobDetail` + `request`/transport），模块内不直接 `fetch`。

## 1. 数据边界（只读）

| 数据 | 端点 | 说明 |
|---|---|---|
| 任务身份 / 工件 | `GET /api/v1/jobs/{id}/detail`（经 `jobDetail.fetchDetail`）、`GET /api/v1/jobs/{id}/artifacts` | 既有 UI-03 行为；工件区在 A-28 增强 |
| 阶段进度（A-25） | 同一个 `GET /api/v1/jobs/{id}/detail` 的 `job` 字段 | 不新增端点、不新增缓存 |
| 实时日志（A-26） | `GET /api/v1/jobs/{id}/logs` | stdout/stderr 行游标 |
| IRC ORCA 日志（A-26） | `GET /api/v1/jobs/{id}/irc/log-tail` | 仅 `workflow === "irc"`，字节游标 |
| 文件树（A-27） | 本地 `GET /api/v1/jobs/{id}/files[?path=DIR]`；远程 `GET /api/v1/jobs/{id}/remote-files[?path=DIR]` | 每次只列一层；`path` 是上一层的相对路径 |
| 文本预览（A-27） | 本地 `GET /api/v1/jobs/{id}/files/{path}`；远程 `GET /api/v1/jobs/{id}/remote-files/{path}/preview?mode=auto&lines=400` | 远程预览有服务端上限；本地无独立 preview 端点 |
| 工件清单 / 下载（A-28） | `GET /api/v1/jobs/{id}/artifacts`、`GET /api/v1/artifacts/{artifact_id}/download` | 下载 URL 只由 `artifact_id` 构造 |

不变量：一个 `(projectId, jobId, attempt)` 选择；detail 复用
`ACPClient.jobDetail`（唯一 cache/ResourceStore），日志、文件树、预览与工件读取复用
注入的 `request`/`ACPClient.transport`。**没有**第二套共享缓存、直接 `fetch` 或写入路由。

文件树在内存里只保留“当前选择 + 当前会话”的已加载目录（`files.dirs`）与展开状态，
切换任务 / 刷新 / dispose 立即丢弃；不写 `ACPClient.store`、不跨任务复用、没有按路径
的 TTL 缓存。根目录在每次选择或显式刷新时读取一次；子目录在首次展开时读取一次，
同一会话内再次展开不再请求。

### A-25 读取的字段（全部来自同一个 detail payload）

`job.stage_index`（1-based 当前阶段）、`job.stage_total`、`job.stage_detail`
（例如 `17/40 scan points`）、`job.stage_progress`（当前阶段 0–1）、
`job.progress`（整体 0–1）；当 `job` 顶层缺失时回退同 payload 的
`job.live_status.stage_index / stage_total`。`detail.stages[]`（`stage_name` /
`label` / `status`）与 `job.stage_order[]` 仅用于条带标签/状态；长度与
`stage_total` 不一致时不强行映射。

渲染规则：

- `stage_total` 缺失或 `< 1` → 整个阶段区隐藏，不显示占位数字；
- `stage_index` 缺失 → 只显示已知的 total 条带，不显示 `0 / N`，不标记当前段；
- 数值进度：优先 `progress`；否则用 `stage_progress` 与
  `((index-1)+stage_progress)/total` 合成为 determinate；两者都缺失 → 只展示
  条带与 current/total，`data-acp-stage-progress="none"`，不伪造百分比；
- 阶段条带上限 64 段（超大 total 只保留 current/total 文本，避免 DOM 膨胀）；
- **终态（completed / failed / cancelled / skipped / expired）或存在
  error / error_detail 时清空阶段区**（隐藏、清空 children、去掉 data 属性），
  不保留过期进度；
- 未知字段一律省略；`detail` 仍是加载态时不会出现猜测的 stage 值。

### A-26 增量轮询

```text
GET /api/v1/jobs/{id}/logs?lines=300[&stdout_offset=2&stderr_offset=1]
  -> { stdout, stderr, stdout_lines[], stderr_lines[],
       stdout_next_offset, stderr_next_offset, has_more }

GET /api/v1/jobs/{id}/irc/log-tail?offset=40        # 仅 IRC
  -> { available, lines[], next_offset }
```

- 首次请求是 tail 读（不带 offset），拿到最近 300 行并把
  `stdout_next_offset` / `stderr_next_offset` 作为下一次的 `stdout_offset` /
  `stderr_offset`；之后只 append 服务端返回的新行，不重复渲染旧行。
- 默认 5s 轮询（`deps.logPollMs` 可注入）；`has_more` 时以 0ms 追赶，最多连续
  5 页，仍只保留尾部。
- DOM 上限 400 行（`deps.logLineLimit`），超出从最旧行删除；`data-acp-log-lines`
  暴露当前行数。stdout / stderr / ORCA 用 `data-stream` 区分（stderr、ORCA 的
  前缀仅由 CSS `::before` 提供，不修改正文）。
- 断线：错误时保留已有行与 cursor；有内容 → `reconnecting` 明确文案并指数退避
  （5s/10s/20s/30s 上限），恢复后从原 cursor 继续，不重放、不丢尾部。
- 缺失端点：HTTP 404/405 → `unavailable` 本地态（明确文案 + 重试按钮），停止
  自动轮询；模块整体仍为 `ready`，日志失败不会把详情面板打成 error。
- 空日志：显式“暂无日志输出。”；运行中仍保持轮询（等待新输出），终态只取一次
  最终 tail 后停止。
- 暂停/恢复：Shell `suspend()`、`document.hidden === true`
  （`visibilitychange`）都会清 timer 并显示 `paused`；`resume()` / 页面重新可见
  立即从原 cursor 续拉。`suspend` 与 hidden 同时成立时保持暂停。
- 生命周期：`dispose()` 清 timer、移除 document 监听、丢弃迟到响应；任务切换
  （generation/key + 日志 session 双重守卫）会重置 cursor 并丢弃上一个任务的
  迟到日志。
- 不使用 SSE：`GET /jobs/{id}/events` 是 job 状态事件流，不是日志行；后端没有
  日志 SSE，因此采用轮询（A-26 允许）。

### A-27 文件树与预览

本地 / 远程由 detail 的 `job.node_id`（回退 `job.result.node`）判定，`"local"`
是本地哨兵，与经典页 `jobRemoteNodeName` 一致；本地走 `/files`，远程走
`/remote-files`。远程条目只带 `name`，因此相对路径 = **本次请求的目录 + 服务端
返回的 name**，不拼接任何其他路径。

- 每次只列一层；目录行初始收起，点击才请求该目录（`?path=`），展开/收起只改
  本地展开状态，已加载目录不再重复请求。
- 目录排序：先目录后文件，再按 name；单目录最多渲染 200 项
  （`TREE_CHILD_LIMIT`），超出显示“仅显示前 200 项（共 N 项）”。服务端
  `truncated` 也显式提示。
- 目录状态：`loading` / `ready` / `empty` / `error`；空目录显示“此目录为空。”，
  目录读取失败在行下显示显式错误，再次点击该行重试。
- 根列表状态在文件区上：`data-acp-files-state` = `loading|ready|empty|error|
  unavailable`；`data-acp-files-source` = `local|remote`；远程节点写入
  `data-acp-files-node`。404/405 是 `unavailable`，其他失败是 `error`，都提供
  “重试”；模块整体仍为 `ready`。
- 预览在点击文件行时打开（`data-acp-preview-state`）：
  - **先按 listing 里的 `size` 判断，再决定是否发请求**：
    - 扩展名不在文本白名单（`.log/.out/.txt/.json/.csv/.gjf/.com/.inp/.xyz/
      .sdf/.mol/.yaml/.yml/.md`）→ `refused`（`binary`），不发请求；
    - `size` 缺失 → `refused`（`unknown`），不发请求；
    - `size > 256 KiB`（`PREVIEW_MAX_BYTES`）→ `refused`（`large`），不发请求；
    - `size === 0` → `ready` + “文件为空。”，不发请求；
  - 本地请求 `GET …/files/{path}`；远程请求
    `GET …/remote-files/{path}/preview?mode=auto&lines=400`（服务端自身也有上限，
    并可能返回 `truncated`）。
  - 解码后再次截断：最多 400 行（`PREVIEW_MAX_LINES`）且最多 256 Ki 字符，
    超出显示“仅显示前 400 行”；`data-acp-preview-lines` 暴露实际渲染行数。
  - 预览面板可关闭；任务切换 / 刷新 / dispose 会清空预览，迟到的旧预览响应
    （对象身份 + session/generation/key 守卫）不会覆盖新选择。
- 本模块不提供通用文件下载按钮：A-27 是只读浏览/预览；工件下载属 A-28。

### A-28 工件清单与下载

`GET /jobs/{id}/artifacts` 的每条工件渲染：

- `artifact_type`（缺失显示 `—`，不猜 `file`）；
- `file_path`（相对路径原文，缺失显示 `—`）；
- `size_bytes`（`formatSize`，缺失显示 `—`）；
- `checksum`（存在时缩略显示，`title` 保留完整值；缺失显示 `—`，
  `data-acp-artifact-checksum="missing"`）；
- 下载链接：**仅当服务端返回 `artifact_id` 时**生成
  `/api/v1/artifacts/{artifact_id}/download`；无 id 时显示 `—`
  （`data-acp-artifact-download="none"`），绝不由 `file_path` 拼下载 URL。

工件区本地状态 `data-acp-artifact-state`：

- `ready`：渲染清单；超过 40 项（`ARTIFACT_LIMIT`）时显式提示“仅显示前 40 项
  （共 N 项）”，不静默截断；
- `empty`：`artifacts: []` → “暂无工件记录。”；
- `error` / `unavailable`：读取失败在工件区显示错误 + 重试，模块整体仍为
  `ready`；404/405 归入 `unavailable`。重试只重读工件清单，带 generation/key 与
  artifact token 守卫。

## 2. 状态

模块四态：`empty` / `loading` / `ready` / `error`（`data-acp-module-state`）。
日志区另有本地态：`loading` / `live` / `empty` / `paused` / `reconnecting` /
`unavailable` / `error` / `ended`（`data-acp-log-state` + note + badge），不会出现
无限 spinner。文件区 / 预览 / 工件区各有自己的本地态（见上），任一子区失败都不会
把整个详情面板打成 `error`。

## 3. 装配和样式

- 样式作用域仅 `.acp-ev*`，颜色走 `base.css` / `shell.css` 变量；
- 阶段条：`.acp-ev-stages` / `.acp-ev-stage-step[data-acp-stage-step]`；
- 日志：`.acp-ev-logs[data-acp-log-state]` / `.acp-ev-log-body` /
  `.acp-ev-log-line[data-stream]`；
- 文件树：`.acp-ev-files[data-acp-files-state]` / `.acp-ev-tree-row[data-acp-tree-row]` /
  `.acp-ev-tree-children`；
- 预览：`.acp-ev-preview[data-acp-preview-state]` / `.acp-ev-preview-body` /
  `.acp-ev-preview-note`；
- 工件：`.acp-ev-artifact[data-acp-artifact-id]` / `.acp-ev-artifact-checksum`；
- 模块不提交任何写操作；工件下载走既有 `GET /artifacts/{artifact_id}/download`。

## 4. 测试

```bash
node frontend/js/module_tests/run_all.js acp2_module_exec_detail
```

当前结果：**27 passed / 0 failed**（5 项既有 UI-03 + 11 项 A-25/A-26 + 11 项
A-27/A-28）：

- A-27：本地根树 ready（目录先于文件、大小、仅根请求）、远程树经
  `/remote-files` 且 child path = 请求目录 + name、远程预览 URL、
  空列表显式 empty、读取失败显式 error + 重试、目录懒加载（展开一次 / 收起不请求 /
  再展开不重复请求 / 每个目录首次展开各一次请求）、预览行数上限 400、超大/二进制/
  未知大小显式拒绝且不发请求、空文件不请求、任务切换丢弃迟到列表、
  dispose 丢弃迟到列表与迟到预览；
- A-28：清单 type/path/size/checksum、仅 artifact_id 生成下载链接、无 id 不给链接、
  缺 checksum 显示 `—`、空清单显式 empty、读取失败显式 error + 仅重试工件区；
- A-25/A-26 既有 11 项保持通过；UI-03 既有 5 项保持通过。

## 5. i18n keys（模块本地 zh 兜底；zh/en 词典在 `frontend/js/shell/i18n.js`）

A-25/A-26：`next.detail.stages`、`next.detail.stage_step`、
`next.detail.stage_position`、`next.detail.logs`、`next.detail.log_loading`、
`next.detail.log_empty`、`next.detail.log_paused`、`next.detail.log_reconnecting`、
`next.detail.log_error`、`next.detail.log_unavailable`、`next.detail.log_ended`、
`next.detail.log_retry`、`next.detail.log_no_transport`、
`next.detail.log_irc_empty`、`next.detail.log_irc_unavailable`、
`next.detail.log_state_loading`、`next.detail.log_state_live`、
`next.detail.log_state_paused`、`next.detail.log_state_reconnecting`、
`next.detail.log_state_unavailable`、`next.detail.log_state_ended`、
`next.detail.log_state_error`。

A-27：`next.detail.files`、`next.detail.files_local`、`next.detail.files_remote`、
`next.detail.files_loading`、`next.detail.files_empty`、`next.detail.files_error`、
`next.detail.files_unavailable`、`next.detail.files_no_transport`、
`next.detail.files_retry`、`next.detail.files_refresh`、
`next.detail.files_truncated`、`next.detail.files_listing_truncated`、
`next.detail.folder_empty`、`next.detail.folder_loading`、
`next.detail.folder_error`、`next.detail.preview`、`next.detail.preview_close`、
`next.detail.preview_loading`、`next.detail.preview_empty`、
`next.detail.preview_meta`、`next.detail.preview_no_ext`、
`next.detail.preview_refused_large`、`next.detail.preview_refused_binary`、
`next.detail.preview_refused_unknown`、`next.detail.preview_error`、
`next.detail.preview_truncated`、`next.detail.preview_remote_truncated`。

A-28：`next.detail.artifacts_loading`、`next.detail.artifacts_unavailable`、
`next.detail.artifacts_truncated`、`next.detail.artifact_checksum_missing`、
`next.detail.artifact_no_id`。

## 6. 已知缺口

- `frontend/js/shell/i18n.js` 尚未登记上述 A-25/A-26/A-27/A-28 新 key（不在本模块
  文件所有权内）。在 shell 词典补齐前，英文界面会回落到模块内中文兜底；这是唯一
  已知的 i18n 缺口。
- 本地文件没有独立的“preview/range”端点：本地预览只能在 listing 报出
  `size <= 256 KiB` 后才请求 `GET …/files/{path}`，并在解码后再次按 256 Ki 字符 /
  400 行截断。若服务端 listing 的 size 过期（文件在读取前突然变大）或长度未知，
  本模块会拒绝预览；要彻底消除该窗口需要后端提供本地 bounded preview 端点。
  远程预览本身有服务端 `max_text_preview_bytes` / `read_tail` 上限，客户端仍先做
  同样的 size 门。
- 文件树是快照式：只有显式“刷新”或重新选择任务才重读根目录；运行中的目录变化
  不会自动同步（A-27 未要求轮询，避免与 A-26 的日志轮询叠加）。
- 不提供文件级下载（只读浏览/预览）；工件下载按 A-28 走 artifacts。远程整包下载
  `/remote-files/archive` 不在本模块范围。
- 阶段进度只随一次 detail 读取渲染（选择/重试/语言切换）；没有为阶段新增独立
  轮询，运行中的 stage_index 需要重新触发 detail 读取才会前进。A-25 的 DoD 是
  “与 `/jobs/{id}` 同源”，本次未扩展成实时阶段流。
- 真实浏览器（非 Node harness）下的滚动、可见性事件、树展开与长预览表现尚未执行
  验证；树行提供 `role="button"` / `tabindex`，但没有完整键盘导航（A-63 范围）。
- 无日志 SSE；错误重试与退避是客户端策略，服务端日志轮转/截断没有显式
  “cursor 失效”信号（cursor 超过文件末尾时服务端返回空行，界面保持尾部并继续
  轮询）。
