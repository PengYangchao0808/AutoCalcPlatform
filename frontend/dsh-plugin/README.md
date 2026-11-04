# `@acp/dsh-panels` — ACP-NEXT 的 DSH 宿主插件

ACP-NEXT（化学工作台）在 DeepSeek Harness（DSH）中的**插件宿主**：Host 半转发 ACP Controller 并把视图层资源按包分发；Client 半在 DSH 槽位里挂载同一份 ACP shell 运行时。

本包**不是**一个新的前端应用，也不是第二个数据契约。视图层仍是 `frontend/js/{shell,modules,client,visualization}`，两宿主共用。

- 上位计划：[ACP-NEXT：DSH 化学工作台插件与 RPH 接入修改计划](../../../docs/reference/next-change/ACP_Frontend_DSH_Pluginization_Plan_20261102.md) §13
- 宿主契约实证：[DSH 宿主能力基线](../../../docs/reference/next-change/ACP_DSH_Plugin_Host_Baseline_20261102.md)（DSH `0.2.1-rc.5`）

---

## 1. 形态

```
package.json        dsh.bundle.patch + dsh.client{platform:web}
cordis.patch.yml    insert 行：id acp-panels / name '@acp/dsh-panels' / config
src/index.js        Host 半：apply(ctx, config) —— 路由 + index 注入
src/config.js       配置归一化与校验（纯函数）
src/proxy.js        白名单前缀转发（node:http，SSE 直通）
src/static-assets.js  已提交资源树 + sha256 清单
src/focus-store.js  会话焦点存储（generation/revision 栅栏）
src/injection.js    index.html 注入行
client/client.js    Client 半：ModuleLoader bundle（React 槽位容器）
client/mount.js     挂载运行时（window.ACPDSH）
client/runtime-scope.js  每次挂载的私有状态、原生 DOM 与资源生命周期
client/host.css     DSH 原生停靠容器适配样式（不修改宿主布局，见 §7）
assets/             构建产物（提交入库，勿手改）
scripts/build.mjs   确定性构建（无 bundler）
test/*.test.mjs     node:test 套件
```

## 2. Host 半路由

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET`/`HEAD` | `<apiPrefix>/health` | 探测 Controller（`GET <controller>/api/backends`，2.5s 超时），只回结构不泄凭据 |
| `GET`/`HEAD` | `<apiPrefix>/ui/focus` | 无 `session` → `{sessions:[...]}`；`?session=` → 该会话最新快照 |
| `POST` | `<apiPrefix>/ui/focus` | 焦点上行；按 `session` 做 generation + revision 单调栅栏（409） |
| `ANY` | `<apiPrefix>/*` | 白名单前缀转发到 `<controller>/api/*`，SSE 不缓冲 |
| `GET` | `<uiBase>/*` | 已提交资源树；带 `ETag`/304、`Cache-Control: no-cache` |

默认：`apiPrefix=/acp-api`、`uiBase=/acp`。**不使用 `/api`** —— DSH 自身占用该命名空间。

安全不变式（`src/proxy.js::resolveUpstream`，纯函数，测试覆盖）：只接受白名单首段；拒绝 `..`、`//`、反斜杠、控制字符；上游 URL 由 `controller + "/api" + rest` 字符串拼接，绝不解析请求方提供的绝对 URL；逐跳头与 `host`/`cookie` 被剥除；`x-api-key` 只来自宿主进程环境（`apiKeyEnv` 只配**变量名**）。

## 3. 配置

```yaml
- insert:
    - id: acp-panels
      name: '@acp/dsh-panels'
      config:
        controller: http://127.0.0.1:8765
        uiBase: /acp
        apiPrefix: /acp-api
        apiKeyEnv: ACP_API_KEY
        readOnly: true
```

`readOnly: true`（默认）会把面板数据客户端换成只读 facade：`jobCommands` / `researchCommands` / `wizardEndpoints` 不暴露，`science.preflight` 与 `frameCandidates.save/remove` 被剥离。模块本身不改——`task-explorer` 等在缺少写命名空间时已自动隐藏写操作。

配置非法时**不注册任何路由**，只注入一行诊断行（`__ACP_PANELS_CONFIG__ = {ok:false, code, field}`），客户端据此显示可读错误而不是白屏。

## 4. 构建与验证

```bash
# 资源构建（读 frontend/next/index.html 的顺序，产出 assets/）
node frontend/dsh-plugin/scripts/build.mjs
# 确定性与漂移门（CI 用；两次构建必须字节一致）
node frontend/dsh-plugin/scripts/build.mjs --check
# 测试
node --test "frontend/dsh-plugin/test/*.test.mjs"
```

`assets/manifest.json`（schema `acp2.dsh.assets.v1`）记录每个文件的 `sha256`/`size`/`media_type`，顶层 `digest` 为按 `"<path> <sha256>\n"` 排序拼接后的 sha256。`core`/`modules`/`css` 字段保留源文件顺序，`mount` 记录 `present|absent`。

资源全部随包分发：Host 半只读包内 `assets/`，**不依赖开发 checkout**。

## 5. 安装

Desktop profile 由 DSH 应用独占管理，不得用 CLI 直改：

- 应用内插件页安装本地包（`link:`），或
- 会话内 `plugin_manager` 工具 `action:"install_bundle"` + `target:"<绝对包目录>"`。

升级需重启（DSH 不支持原地升级）；卸载不影响已提交的科学任务。

## 6. 原生加载修复（2026-11-03）

- 入口：DSH 会话顶部 **ACP-RPH** 按钮；通过 `sidebarRightTabs` 注册资源类型，再以相同 `key` 注册内容和标题；`slots.inject` 等待父槽位声明。
- 宿主不导出伪 `Config`：Cordis 的该导出必须是 Standard Schema。环境来自 `process.env`，不能读取未声明的 `ctx.env` 服务。
- `core.bundle.js` 现在是 CSP 兼容的工厂，包含原有核心、全部真实模块和五个适配器。每次挂载创建独立运行时，共用原生 DOM，关闭时释放模块、监听器、定时器、动画帧和请求。没有 iframe，也不替换 DSH 的 fetch/EventSource。
- 样式通过 Chromium `@scope (.acp-dsh-host)` 限定；页面级根规则映射为容器根，窄侧栏使用容器查询适配。
- 会话身份来自 DSH 槽位注入。重开时以 `POST /ui/focus {operation:"acquire", session, expectedGeneration}` 执行 CAS 交接；旧 generation 的 publish 仍被拒绝，不靠客户端递增绕过栅栏。
- `readOnly` 由宿主配置约束，组件参数不能将只读配置降级为可写。只读 facade 保留读取模块的 init。
- 该插件提供 ACP 视图与 Controller 代理；不因面板加载成功就宣称 RPH Supervisor、模型或科学计算整链已经运行。

浏览器回归（使用本机已安装 Chrome 的独立临时资料，不触碰现有浏览器）：

```powershell
node frontend/dsh-plugin/test/browser-smoke.mjs
# 非默认 Chrome 路径可通过 CHROME_PATH 指定。
# 实际 DSH Cordis 独立验证：使用 DSH 的 Electron Node 模式运行
# test/installed-host.mjs，并传入所安装的 app.asar 路径；不使用 desktop profile。
```

## 7. 窗口级固定工作台（2026-11-04）

ACP 工作台是**窗口级单实例**：DSH 对话只是它的关联对象，不再是各自一份实例。显示位置仍是原生 `sidebar.right.pane.tab`，宽度、分隔条、分屏与全屏继续由 DSH 拥有；没有 `shell.overlay`、不写宿主 padding/私有 localStorage、不向 `document.body` 追加应用。

### 7.1 三种身份

| 身份 | 落点 | 含义 | 切换浏览对话时 |
|---|---|---|---|
| 工作台实例 | 模块级 `owner` 单例 | 本窗口的长期显示实例；只管理显示生命周期，不作为授权/研究身份 | 保持不变 |
| 停靠会话 `dockedSessionId` | `owner` + `AcpBody` | 此刻原生 tab body 属于哪个 DSH 对话，只决定「显示在哪」 | 跟随宿主 |
| 关联研究会话 `linkedSessionId` | `owner` + 焦点桥 | Agent/焦点桥/研究引用真正关联的会话 | pinned 保持；follow 跟随；仅显式关联才改 |

项目、结构、任务/attempt、候选与研究记录仍以既有 WorkspaceContext/Controller/Services 为权威；`owner` 不复制任何科学对象状态。

### 7.2 附着与交接

- 首次附着创建稳定根节点 `<div class="acp-dsh-workbench" data-acp-workbench-root>`（标题条 + mount 点），并只调用一次 `ACPDSH.mount(mountPoint, { sessionId: linkedSessionId })`。
- 之后切换对话**只移动同一个 root 节点**（`container.appendChild(root)`）：内部 DOM、事件监听、Viewer、相机、曲线帧、滚动与草稿全部保留。不重建运行时、不重放请求、不调用 `suspend/resume`。
- 每个 attachment 递增 `revision`；cleanup 只在 `owner.container === container` 且 `root.parentNode === container` 时移除节点，旧容器的迟到 cleanup 不能搬走/卸载已附着到新位置的 root。
- 用户关闭原生 ACP tab：`registerCloseHandler` 只把 `intentOpen` 置 false（并持久化）并 detach，运行时与上下文保留；只有插件 disposer 才 `ACPDSH.unmount()`。

### 7.3 自动就位（pinned）

订阅 `ctx.sidebarRight.mounted`（唯一前景会话来源；`getSnapshot()/subscribe()`）。仅当 `mode === 'pinned'`、`intentOpen`、`(owner.visible || !owner.everAttached)` 时尝试：

- 已有 `{ sessionId, kind: 'acp-workbench' }` 的打开 tab → 不调用（不抢焦点）。
- 没有 → `openResource('dsh-resource://acp-workbench/' + encodeURIComponent(sid))`。store 尚未 mint 时会抛 `sidebarRight: no session surface is mounted`，此时**有界重试** 10 次 × 80ms，每次前核对 `mounted` 仍是该会话且 `intentOpen` 仍为真；不盲调、不无限循环。
- 前台意图在 `mounted` 变化的瞬间记录（`dockIntent`），因此旧 body cleanup 到新 body attach 的空窗不会被误判为「用户隐藏」；dock 重试使用独立定时器，关闭旧 tab 不会取消它。

### 7.4 模式与关联

- 会话胶囊：单一 ACP 顶栏内的 `对话 …`、保持/跟随、关联提示和「关联当前对话」。胶囊在 Shell 挂载后移入 `#acp-dsh-session-slot`；旧 `ACP 工作台` 标题条已删除，不再有第二条顶栏。当 `dockedSessionId !== linkedSessionId` 时显示 `当前浏览对话 B · 关联研究：对话 A`，并保留 `POST /ui/focus` 的 generation CAS 交接。
- 「关联当前对话」调用 `ACPDSH.reassociate(dockedSessionId)`：只交接 identity（`session.sessionId`、`ACP_ENV.sessionId`、焦点桥 generation/租约 CAS acquire），不 unmount、不重建 shell、不清空画布、不重放任务；已有研究来源、任务与证据不被改写。
- 偏好持久化在 `acp-dsh:` 命名空间：`acp-dsh:workbench.mode`（`pinned|follow`）、`acp-dsh:workbench.linked`、`acp-dsh:workbench.intent`（`1|0`）。
- follow 模式：不再自动打开/抢回原生 tab；已附着时把 `linkedSessionId` 跟随当前 `dockedSessionId`。本轮仍是**单实例**，不提供「每个对话各自独立的工作台上下文」。

### 7.5 尊重显示意图

用户手动切到文件/终端 tab、收起右栏或关闭 ACP 后，切换对话不会抢回，也不会反复自动打开。`owner.visible` 由 `info.tab.visible` 维护，并结合 `info.sidebar` 的已知布尔字段（`expanded/open/visible/collapsed`）防守式判断；字段缺失时按「展开」处理。再次手动打开（头部 ACP-RPH 按钮）才恢复，且状态仍在。

### 7.6 验证范围与未验证项

| 范围 | 状态 | 证据 |
|---|---|---|
| 插件完整 node:test | **PASS，99 项**（原 88 项基线未回归） | 窗口级 owner、附着/交接、迟到 cleanup、自动就位/有界重试、关联、模式与关闭、`reassociate` 四种返回 |
| 插件构建与漂移 | **PASS** | `build.mjs --check`：34 个生成资源，digest `4eadd3b0acf389ee` |
| 真实 Chromium + fixture API | **PASS** | 既有五档容器宽度/停用恢复/关闭重开，另加 `reassociate` 保持同一 host/shell 与 `state().sessionId` 交接 |
| 真实 DSH 跨对话固定旅程（§13.15 S3） | **NOT_VERIFIED** | 本轮未操作真实 DSH、未重启生产、未执行科学闭环 |

未验证项（必须在真实 DSH 中验收）：

- 切换对话时原生 `sidebarRight` surface 的实际收缩/闪烁与动画时序；零闪烁常驻尚未证实。
- 收起右栏、文件/终端 tab 的意图判定：`useTabInfo()` 是否提供 `sidebar.expanded` 之类字段尚未核实，当前为防守式降级。
- `ctx.sidebarRight.mounted` / `openTabs` / `registerCloseHandler` 在真实宿主中的具体快照形状与调用时序（当前按契约名与已知字段防守式实现）。
- 多窗口各自持有独立实例、互不抢画布；刷新恢复与窗口关闭。
- 真实 DSH 会话切换下的焦点租约是否始终成功（`reassociate` 若 acquire 失败只记录 `bridgeError`，不回滚 identity）。

## 8. 原生停靠修复轮验证范围（2026-11-04，历史范围）

> 本节记录上一轮「原生停靠修复」的验证范围；窗口级固定工作台轮的验证见 §7.6（插件套件现为 99 项）。

| 范围 | 状态 | 证据 |
|---|---|---|
| 插件完整 node:test | PASS，88 项 | 原生类型/导航、隐藏体不挂载、单所有者、重试取消、scope 暂停/恢复和既有代理/只读护栏 |
| 前端模块套件 | PASS，1173 项 | 含项目列表畸形响应的恢复错误态；没有把失败回包当空项目 |
| Python 插件契约 + 当前静态资产一致性 | PASS，14 项 | 13 项插件契约与 M5-01 checkout 静态清单门；不是完整安装验收 |
| 真实 Chromium，fixture API | PASS | 容器尺寸、单实例、会话停用/恢复、未保存输入保留、关闭重开项目引用、CAS 与旧回包隔离 |
| 当前实际 DSH 桌面 | PASS（限定范围） | 原生 ACP tab、宿主全屏/恢复、分隔条拖宽、项目/中央视图切换、D/G 正常显示；见下方回执 |
| 全新冷启动、全部业务交互 | NOT_VERIFIED | 退出提示运行中任务将中断，已取消退出；不为本次验收打断其它任务 |
| 模型/QC/LSF 与科研闭环 | NOT_VERIFIED | 未提交任何新计算或模型消息；读取已有任务/项目不能替代科研链验收 |

[本轮实现与验收回执](../../../reports/dsh_acp_docking_repair_20261104/REPORT.md)。[11-03 的加载修复记录](../../../reports/dsh_acp_ui_repair_20261103/REPORT.md)保留历史范围，不能作为本轮冷启动通过的依据。

部署时通过 DSH 支持的重载/重启方式装载整套新资源。当前运行实例已经观察到客户端热更新和原生停靠；这不等于验证了完整升级或冷启动流程。若宿主提示有运行中的任务，等待其结束后再重启，不取消或强制终止。
