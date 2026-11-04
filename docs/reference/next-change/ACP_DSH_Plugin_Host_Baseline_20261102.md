# DSH 宿主能力基线（P0 核验记录）

> **2026-11-04 双人开发同步**：此文是原宿主能力核查快照；不作为化学编译/科学插件交付依据。双人新增工作按[架构 §10](ACP_3.0_Architecture_20261031.md)，宿主实现/提案按插件计划 §13.13—15 分别验收。

> **2026-11-04 停靠复核**：已重新读取已安装包的 layout/sidebar-right README，核实原生 push/fullscreen、`openResource/openTab` 与 keyed body 能力。当前 ACP `shell.overlay` rail 不是原生右侧工作区；修复方案见[插件计划 §13.14](ACP_Frontend_DSH_Pluginization_Plan_20261102.md#native-docking-fix-20261104)，逐项来源与范围见[诊断记录](../../../reports/dsh_acp_docking_diagnosis_20261104.md)。本条不提升真实宿主修复验收状态。

**日期**：2026-11-02
**上位计划**：[ACP-NEXT：DSH 化学工作台插件与 RPH 接入修改计划](ACP_Frontend_DSH_Pluginization_Plan_20261102.md) §13.5 P0
**状态**：宿主契约已从**所安装版本**实证（非文档推断）；插件实现见 `frontend/dsh-plugin/`。真实页面渲染仍未验收。

本文件只记录“从本机已安装 DSH 读到的可执行事实”，以及计划首稿中**被实证推翻**的假设。凡本文件未列出的行为，一律不得当作已验收；三态记法沿用仓库约定（PASS / FAIL / NOT_VERIFIED）。

---

## 1. 核验环境

| 项 | 值 | 证据来源 |
|---|---|---|
| DSH 版本 | `0.2.1-rc.5` | `dsh --version` 实际执行 |
| 构建提交 | `d4db512a969fccf0c5cc7ec8fe38e9c1d27af916`，`dshBuildDirty: false` | `app.asar/package.json` |
| DSH 安装形态 | Electron 应用 + 打包 asar，**无 TypeScript 源码树** | `resources/app.asar`（`dsh/node_modules/@deepseek-ai/*`，307 包，仅 `lib/*.js` + README） |
| DSH home / profile | `C:\Users\彭扬超\.dsh`，`DSH_PROFILE=desktop` | 环境变量 + `profiles/desktop/` |
| 捆绑工具链 | Node `24.21.0`、pnpm `11.28.5` | `resources/runtime/versions.json`；`resources/runtime/primary-runtime/dependencies/{node,pnpm}` |
| `dsh` CLI 在 PATH | **否**（`Get-Command dsh` 失败）；入口 `resources/runtime/cli/bin/dsh.cmd` | 实际执行 |
| `$ACP2_TOOLS` | **未设置**；`Documents/deepseek-harness` 只有空 `default-workspace/` | `Get-ChildItem Env:` |

**结论**：AGENTS.md 中“DSH 本体在外部 `$ACP2_TOOLS/deepseek-harness`”在本机**不成立**。本机唯一权威是安装包内 `app.asar` 的构建产物（`lib/*.js` + README）。这决定了：宿主契约只能按 README + 构建产物 + 官方样例插件反推，且**不能引用 `.d.ts`**。

**权威参考件**：安装包自带官方插件开发 skill —
`app.asar → dsh/node_modules/@deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/`
（`SKILL.md` + `references/{host-plugin,ui-plugin,practices,user-actions,verification,mcp-bundle}.md` + `templates/{decoration,mcp}/`）。本文件所有契约结论均可回溯到该目录或 `@deepseek-ai/dsh-host-webserver`、`dsh-client-modules`、`dsh-client-ui-renderer`、`dsh-package-manifest` 的 README/build。

---

## 2. 插件清单（`package.json` 的 `dsh` 字段）

校验器：`dsh/node_modules/@deepseek-ai/dsh-client-modules/lib/client.js:62-75`（`parseDshClient`）。

```
dsh.client = {
  platform: string,          // 必填；现有全部插件与文档均为 "web"
  inject?: string[],         // 模块到达依赖，只在“图行(graph row)”里解析，不是 Cordis inject
  external?: string[],       // 声明的非基座模块请求，参与模块图排序
  immediately?: boolean      // true = boot 时预取
}
```
- `dsh.bundle.patch` = 一个或多个 patch 文件路径（相对包根，按序应用为一层 bundle）。
- `dsh.manifestVersion` 声明值 `1`；`engines.dsh` 为 SemVer 区间。**当前安装器与加载器都不强制校验这两项**（README 明示），因此版本锁定必须由发布流程自证，不能依赖加载器拒绝。
- 真实双半插件样例：`E:\Calculations\Plugins\Oh-My-CoHarness\package.json`。

> **对首稿的纠正**：`dsh.client.inject` **不是** Cordis `inject`，写 `"slots"` 进去不会生效（它只解析图行）。Cordis 级依赖必须在**客户端插件对象**上声明 `inject: ['slots']`（见 §5）。

## 3. `cordis.patch.yml`

顶层 YAML 数组；条目要么 `- insert: [{id, name, config?}]`，要么直接 `- id / name / config` 覆盖已发行行（**覆盖会替换整段 `config`**）。`!!js` 可用；`disabled: false` 用于启用默认禁用的行。

```yaml
- insert:
    - id: acp-panels
      name: '@acp/dsh-panels'
      config:
        controller: http://127.0.0.1:8765
```

`profiles/<p>/cordis.yml` 恒为 `[]`，**不要编辑**；用户层是 `cordis.patch.yml`。

## 4. Host 半

**导出形态**（官方 `references/host-plugin.md`）：`export function apply(ctx, config) {}`（可选 `export const inject = [...]`、`export const Config`），**或** service class 默认导出；两种不得混用。资源一律在 `apply` 内用 `ctx.effect` / `ctx.on` 注册并返回清理函数。

**HTTP 路由** —— 服务 `ctx.webServer`：

```
register(route): () => void     // route = { kind: 'exact' | 'prefix', path, handler(req,res) }
registerUpgrade(route)
registerFallback(handler)       // 全局唯一，第二次注册抛错
tapIndex(transform) / renderIndex(html) / collectIndexInjections() / getOrigin()
```
匹配顺序：**exact 表 → 最长 prefix → fallback**；`(kind, path)` 重复注册抛错；prefix 只匹配 `path` 与 `path + "/"` 前缀（`/plugins` 不匹配 `/pluginsfoo`）。handler 完全自持 `node:http` 响应；handler 抛错会被答成 400。

> **对首稿的纠正**：计划首稿 §3.4 写的 `register({ path, match: 'prefix' })` 不存在。字段名是 **`kind`**。

**index.html 注入** —— `ctx.on("webserver/index-inject", table => table.push(row))`。行的种类是**封闭集合**（`dsh-host-webserver/lib/index.js:29-57`）：

| kind | 渲染结果 | 位置 |
|---|---|---|
| `global` | `<script>globalThis["name"] = <json></script>` | head |
| `script` | `<script>text</script>` | `placement` |
| `script-src` | `<script src="…"></script>` | `placement` |
| `script-preload` | `<link rel="preload" as="script" href="…">` | head |
| `style` | `<style>text</style>` | head |
| `html` | 原样 HTML | `placement` |

**没有 `style-src`**：外链样式表只能走 `{kind:'html', html:'<link rel="stylesheet" …>', placement:'head'}`。

**反向代理**：**不存在内置前缀代理**。`@deepseek-ai/dsh-http-proxy` 是**出站**代理（`http_proxy` 等环境代理，供 Node `fetch` 使用），不是本地反向代理。→ 转发 ACP Controller 必须自写 `kind:'prefix'` handler。

> **对首稿的纠正**：计划首稿暗示 Host 半可以直接“注册前缀转发”。实际必须手写 `node:http` 转发（含 SSE 直通、超时、错误映射），这正是 `frontend/dsh-plugin/src/proxy.js` 的职责。

## 5. Client 半

**不是 ES module**：浏览器半是 **ModuleLoader bundle**，执行 `window.__ModuleLoader__.load({ id: <包名>, factory(require) { … return exports } })`，其 `factory` 返回 **Cordis 风格插件对象**：

```js
return { inject: ['slots'], apply(ctx) { ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({ name:'sidebar.right.pane.tab', id:'acp-tasks', order:5 }, Component)) } }
```
- 执行脚本**只注册 factory**；模块体副作用（含 CSS）在首次 materialize 时才发生。
- `require` 可解析的**基座模块只有 11 个**：`react`、`react/jsx-runtime`、`react-dom`、`react-dom/client`、`@deepseek-ai/cordis`、`zod`、`@tanstack/react-virtual`、`@deepseek-ai/dsh-client-store`、`@deepseek-ai/dsh-client-ui-slots`、`@deepseek-ai/dsh-client-ui-primitives`、`@deepseek-ai/dsh-client-ui-dockkit`。其余必须在 `dsh.client.external` 声明。**禁止自带 React/CDN/UMD**。
- 提供的内置物：`ctx.{get,on,provide,effect}`、`React.{createElement,useState,useEffect}`、`host.call(method,args)`、`styles.insert(css)`、`console.*`。
- 官方纪律（`references/ui-plugin.md`）：“不要把 app root 换掉，也不要往 `document.body` 追加第二个应用”——面板必须落在槽位里。

**槽位（VERIFIED）**：`ctx.slots.inject(key, callback)` 与 `ctx.slots.register(options, component)`；`_register` 要求 `options.name`，并读取 `store` / `registrant`；常用选项 `name`（必填）、`key`、`id`、`order`、`priority`、`select`、`store`。运行时枚举用 inspect 提供者 `Slots.listSubTree`。

**真实槽位 id（来自已发行 bundle）**——本插件只用得着这几个：

| 用途 | 槽位 |
|---|---|
| 右侧栏标签页 | `sidebar.right.pane.tab`（配 `sidebar.right.pane.tab.title`） |
| 对话区整列 | `conversation.view` |
| 主面板 | `main` / `root` |
| 浮层 | `shell.overlay` |

> **对首稿的纠正**：计划首稿 §3.5 的 `ctx.slots.inject("sidebar.right.tab", …)` 与 §4 的 `conversation-view` **都不存在**。正确 id 是 `sidebar.right.pane.tab` 与 `conversation.view`。**通用 `tab` 槽位不存在。**

## 6. 安装与分发

- CLI：`dsh plugin --profile <name> <pnpm args>`（把参数转发给 profile 目录里的 pnpm）。
- 会话内（官方 skill 首选）：agent 工具 `plugin_manager`，`action: install_bundle` + `target: <包绝对路径>`。
- **桌面 profile 由应用独占**：不得用 CLI 直改 `profiles/desktop`；走 GUI 插件页或 `plugin_manager`。
- 客户端 bundle 由 DSH 自己在 `/plugins` 前缀路由下分发（combo `/plugins/??<ids>&rev=<rev>`、单文件 `/plugins/<pkg>/client.<name>.js?rev=<rev>`、HMR `/plugins/events`）。**插件不要自己再发客户端 bundle。**

## 7. 本轮据此确定的设计决定

1. **Host 半手写两个前缀路由**：`<apiPrefix>/`（转发 Controller）与 `<uiBase>/`（静态资源），另加两个 exact 路由 `<apiPrefix>/health`、`<apiPrefix>/ui/focus`。exact 优先于 prefix，无需额外仲裁。
2. **不使用 `/api` 前缀**：DSH 自身占用 `/api`（认证 RPC）。改用 `/acp-api`，由客户端 URL 重写壳把 `/api/*` 映射过去。
3. **客户端插件对象声明 `inject: ['slots']`**，`dsh.client.inject` 留空（它只解析图行）。
4. **样式表用 `{kind:'html'}`** 注入 `<link>`，不用不存在的 `style-src`。
5. **资源随包分发**：`frontend/dsh-plugin/assets/` 由 `scripts/build.mjs` 从 `frontend/next/index.html` 与 `frontend/js/**` 确定性生成（含 sha256 清单），Host 半只从包内 `assets/` 读，开发 checkout 不作为运行期依赖。
6. **面板渲染复用现有 shell**：`assets/skel.js`（从 `next/index.html` 提取的骨架）+ `core.bundle.js` + 按需 `modules/<id>.js`，因此模块零重写、两宿主共享同一视图层。

## 8. 仍未验证（NOT_VERIFIED）

| 项 | 缺的证据 |
|---|---|
| 真实 DSH 页面内面板渲染、槽位布局与销毁 | 需在实际 DSH 中安装并截图/日志；本环境未安装插件 |
| `sidebar.right.pane.tab` 的精确 props/选项 schema | 需运行中 `cordis_inspect_query` → `Slots.listSubTree` |
| 页面 CSP 是否允许同源 `<script src>` 动态注入 | 需真实页面实验 |
| HMR 热更新对已挂载面板的行为 | 需真实升级/重载实验 |
| 多实例面板与单活动 Viewer 的冲突 | 需真实多面板实验 |
| Desktop profile 的安装/卸载/回退流程 | 需应用内 GUI 插件页操作；不触碰生产 profile |
| `dsh.client.platform` 的其它取值 | 仅 `"web"` 有实证 |
| RPH Supervisor 是否已被当前 DSH 加载 | 需真实会话中的工具列表与能力协商回执 |

以上任何一项未通过，都不得写成插件“已交付”。


## 9. 2026-11-03 实际加载纠正

以上 P0 是 11-02 的历史核查，不能代替真实启动。实际安装验证补充：

- `sidebar.right.pane.tab` 与 `.title` 为 keyed，必须传 `key` 并以 `slots.inject` 等待声明。服务 `inject:["slots"]` 不等于目标已声明。
- `conversation.session.header.corner` 是已占用的 single，不能追加普通按钮；本插件使用 session 级 list `conversation.session.header.utilities`，以 `id` 注册入口。
- Host 的 `Config` 必须实现 `~standard.validate`；普通字段说明对象会在 apply 前失败。本插件已去掉该伪 Schema，以 normalizeConfig 处理局部诊断。
- `ctx.env` 不是可随意读取的环境字典；未声明服务访问会抛错。Host 读取 process.env。
- 原生桌面的 index injections 通过启动 IPC 应用，只有 Host 真正激活并注册注入后才存在。客户端按钮出现不能证明 Host 已激活。
- 同源页面内 ACP 网络与状态须隔离；已提交资源包含真实模块和五个适配器，不能只验证文件名或模拟注册函数。

实现及验证见 [插件 README](../../../frontend/dsh-plugin/README.md) 和 [11-03 修复记录](../../../reports/dsh_acp_ui_repair_20261103/REPORT.md)。真实 Controller / 科学整链、HMR、多面板仍为 NOT_VERIFIED。
