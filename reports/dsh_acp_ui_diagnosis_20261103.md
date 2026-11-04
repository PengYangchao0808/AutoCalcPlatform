# DSH / ACP 原生 UI 集成启动失败诊断

日期：2026-11-03。范围：本机安装的 DSH 0.2.1-rc.5、当前未提交的 ACP 集成代码及现有测试。未修改插件源码、桌面 profile，未重启 DSH 或提交计算。

## 结论与证据边界

崩溃日志 `C:\Users\彭扬超\AppData\Roaming\@deepseek-ai\dsh-desktop\logs\crash-2026-11-03T03-34-03-071Z-web-boot.log` 确认浏览器插件 `@acp/dsh-panels` 激活失败。日志只有聚合异常，没有插件内部栈，不能从该日志确定最先抛出的具体异常。

只读检查安装包 `resources/app.asar`，取实际发布的 SlotCore.register 方法，在 Node VM 中加载当前 client.js 并调用 apply，复现：

```text
Slot not yet declared: slot "sidebar.right.pane.tab" is not declared (a parent entry's children table must declare it)
Declared keyed slot: keyed slot "sidebar.right.pane.tab" requires options.key
```

该复现使用真实注册方法和简化宿主状态，证明接口不兼容；不是 Electron 全页面验收。

## 修复顺序

### P0：修复槽位注册

位置：`frontend/dsh-plugin/client/client.js:194`。

1. `sidebar.right.pane.tab` 和 `.title` 在实际 DSH 右栏插件中都声明为 `kind: "keyed"`，当前传 `id`，必须改为相同的 `key: PANEL_ID`。
2. `inject: ["slots"]` 只保证槽位服务可用，不保证目标槽位已声明。每个注册应通过 `ctx.slots.inject(slotName, callback)` 等待目标声明；这也是已安装 DSH 自带插件的用法。

建议替换两个现有注册块：

```js
ctx.effect(function () {
    return ctx.slots.inject(SLOT_TAB, function () {
        return ctx.slots.register({
            name: SLOT_TAB,
            key: PANEL_ID
        }, AcpWorkbenchPanel);
    });
});

ctx.effect(function () {
    return ctx.slots.inject(SLOT_TAB_TITLE, function () {
        return ctx.slots.register({
            name: SLOT_TAB_TITLE,
            key: PANEL_ID
        }, AcpWorkbenchTabTitle);
    });
});
```

关闭状态的 conversation.view 分支不能直接照搬 key；启用时需单独核实该槽位的 kind/props。不要通过捕获并吞掉注册异常来掩盖失败。

### P1：补齐标签类型与打开入口

当前 client.js 只有槽位贡献，没有右栏标签类型注册和打开动作。槽位注册成功不等于用户能打开 ACP 面板。

按本机 DSH 的 `dsh-client-ui-plan/lib/client.js` 和 `dsh-client-ui-sidebar-right/lib/client.js`，接入 `sidebarRightTabs.register(...)`、`sidebarRight.openResource(...)`，定义 ACP 资源地址、kind、匹配条件、标题及可见打开入口，并声明所用服务依赖。具体资源格式需作为 ACP 自己的契约定义，不能复制 plan 地址。

### P1：隔离共享页面的网络请求

`frontend/js/adapters/dsh/base-url.js:208` 的 installAll 会替换整个 window.fetch / window.EventSource，把相对 `/api/*` 转为 `/acp-api/*`；`client/mount.js:526` 实际调用它。DSH 自己也使用 `/api`，因此面板挂载后会影响宿主请求。卸载当前也未调用 reset 恢复。

应只给 ACPClient、ACP 模块 fetchFn/EventSourceClass 注入带重写的专用实例；保留宿主 window 的原始实现。仅增加卸载恢复仍不能解决面板存续期间的错误路由。

### P1：隔离样式

`assets/panels.css` 直接拼入 `:root`、`html`、`body`、`*`、button/input 等全局规则，Host 将该 CSS 注入 DSH 文档。会影响宿主主题、滚动、字体和控件。

在构建阶段生成以 `.acp-dsh-host` 为边界的样式：把根变量迁入容器、重写页面级布局规则、限定通用选择器和媒体查询内规则；不能仅在文件首尾加一个容器字符串。先验证 ACP 当前依赖 document 查询的方式，再决定是否适合 Shadow DOM。

### P2：修复延迟加载与生命周期

client.js:102 的 setTimeout 只执行一次；500ms 时运行时仍未就绪，ready 不变，依赖 `[ready]` 的 effect 不会再次执行。改为可取消的重复探测或就绪事件，并设置超时错误和重试入口。

mount.js 的 sessionId 当前来自 per-tab sessionStorage，并非实际 DSH 会话；切换会话可能串用焦点。正式联动需通过宿主槽位注入真实会话身份，明确切换、重挂载和 generation 规则。全局 ACPShell 的 started 状态及模块 teardown 也应纳入卸载/重开验证。

## 验证与测试缺口

- 当前 client.test.mjs 的模拟 register 不校验目标声明和 keyed 的 key，因此 21/21 PASS 不能证明真实宿主可加载；测试甚至断言没有调用 slots.inject，需要随修复调整。
- 新测试至少覆盖：父槽位晚于 ACP 激活、父声明撤销再恢复、缺少 key 拒绝、标题与内容 key 一致、插件卸载释放、运行时超过 500ms 才到达、DSH 网络请求保持原路由。
- 本次现有测试：client 21/21 PASS；插件全套 71 PASS / 10 FAIL。10 项为 build.test.mjs 的子进程构建受当前执行环境限制（spawnSync 返回 status=null），不能记为产品逻辑 FAIL，也不能记 PASS。
- 独立执行 build.mjs --check：PASS，25 文件，digest 前缀 17b694d234bd8e02。没有发现当前资源漂移，重建本身不能修复槽位参数错误。
- 最后在真实 DSH 验证：冷启动进入主界面、打开 ACP 标签、Controller 不在线时局部降级、DSH 对话与插件页仍能访问、关闭/重开/切换会话/卸载重载无残留。必须单独记录 live 证据，不能用 fixture 替代。

## 当前安装与恢复建议

桌面 profile 的 @acp/dsh-panels 是 link 到本 checkout 的 frontend/dsh-plugin；修复 client.js 后通过 DSH 支持的重载/重启流程加载。若修改 mount.js 或共享前端源，需运行资源构建，不能只编辑生成文件。

如需先恢复 DSH，可使用报错框提供的“禁用第三方插件、备份 profile patch 并重启”，随后通过应用内插件管理重新启用；此动作会影响其它第三方插件。不要删除 profile、会话或科学任务数据。当前没有执行该恢复操作。


后续：用户已授权并完成实现，见 [修复与验证记录](dsh_acp_ui_repair_20261103/REPORT.md)。本文保留初次诊断的时间点，不作为最终实现状态。
