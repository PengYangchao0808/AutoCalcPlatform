# ACP 前端 UI-01 Shell 设计规格（实验入口 `frontend/next/index.html`）

日期：2026-10-30
性质：UI/UX 设计规格（设计稿，未实现；不含任何 `frontend/` 代码改动）
上游方案：`ACP_2.0_前端同步升级开发方案_20261029.md` §3.5（模块/组件/配方/宿主）、§4.1–4.3（Viewer / Task Explorer / Task Config+Evidence）、§4.6（Agent Workspace 只读首片）、§5（工作区体验与可访问性）
适用阶段：UI-01（Shell + mock 模块）、UI-02（Viewer + Explorer 接入）、UI-03A（Agent 只读卡片）共用本规格；UI-03B 起的交互扩展只复用命名，不在此范围。
视觉基线：`frontend/css/workbench/base.css`（token）、`layout.css`（骨架/面板/开关）、`features.css` + `queue-detail.css`（chip/badge/占位）、`frontend/ACP_Workbench_v2.html`（结构模式）、`frontend/js/workbench/i18n.js`（双语机制）。

读者：实现车道。本文所有 class/token/断点/文案 key 均为最终命名，可直接照做；标注「保留」的条目本期只占位不启用。

---

## 0. 视觉基线与文件拆分

### 0.1 设计立场

与旧 Workbench 同族：同一深色 token、同一 pill/badge/面板语言、同一 64px 顶栏节奏，用户切换入口无突兀感。差异只做三处可识别的新身份：顶栏「实验入口」徽章（amber）、工作区切换 tablist（Research / Molecular / Execution）、全局焦点指示 chip。不引入新字体、新图标库、新依赖；图标全部用内联 SVG sprite，描边风格与旧页 `viewBox="0 0 20 20" stroke="currentColor" fill="none" stroke-width="1.5"` 一致。

### 0.2 CSS 文件拆分

| 文件 | 内容 | 加载方式 |
|---|---|---|
| `css/workbench/base.css` | 既有共享 token 与全局 reset（**只读复用，不修改**） | `next/index.html` 静态 `<link>`，第一顺位 |
| `css/shell/shell.css` | Shell 骨架：新 token、顶栏、工作区切换、焦点 chip、槽位栅格、面板 chrome、四态（加载/空/错误/无权限）、骨架屏、窄屏降级、a11y 工具类 | 静态 `<link>` |
| `css/shell/agent.css` | Agent 只读卡片：`acp-card-*`、`acp-timeline-*`、`acp-tag-*`、模型解释 vs 验证事实、连接/缺失态 | 静态 `<link>`（首期 research 为默认工作区，无需动态注入；后续若做按需加载，由 loader 给 `<link>` 加 `data-acp-module` 再开关，本期不做） |
| `css/modules/<module>.css` | 模块私有样式：每个 UI-02/UI-03 模块一个文件（`css/modules/molecule-viewer.css`、`css/modules/task-explorer.css`…），类名一律 `acp-mod-<module>-*` 前缀，只消费 base.css token，不碰 shell/agent 的类 | 静态 `<link>`，与模块脚本同期落地（2026-10-30 修订：补充模块级 CSS 拆分，UI-02 起生效） |

classic script 环境：无 CSS-in-JS、无构建步骤、无 CSS `@import`（旧页也不用）。所有新 token 挂在 `.acp-shell` 作用域下（不用 `:root`），保证 shell.css 意外被旧页引用时不污染旧 token。

### 0.3 复用 base.css 的 token（不重定义）

| Token | 值 | Shell 用途 |
|---|---|---|
| `--bg-base / --bg-panel / --bg-elevated / --bg-canvas` | `#0b0f14 / #111821 / #1a2332 / #0e1419` | 页面底 / 面板底 / 浮层底 / 3D 画布底 |
| `--border-subtle / --border-default` | `#1e2832 / #24303b` | 卡片边 / 面板与槽位分隔边 |
| `--text-primary / --secondary / --tertiary` | `#e5edf5 / #b0bec8 / #6b7b8a` | 正文 / 次要 / 装饰性小节标签（用途限制见 §5.3） |
| `--accent / --accent-soft` | `#4ea1ff / rgba(78,161,255,.14)` | 工作区激活、已提交态、焦点指示 |
| `--green / --amber / --red` | `#38c172 / #f2b84b / #e55353` | 验证结论 / 实验徽章与缺失 / 错误 |
| `--radius / --radius-sm` | `10px / 6px` | 卡片 / 小按钮 |
| `--transition` | `0.15s ease` | 全部 hover/active 过渡 |
| `--shadow-panel` | 见 base.css | fatal 层、浮层 |
| `--nav-control-height / --toolbar-control-height / --small-button-height / --status-chip-height` | `38/30/30/22px` | 顶栏控件 / 面板头按钮 / 行内按钮 / chip 高度 |

`prefers-reduced-motion` 全局动画终止由 base.css 已有规则覆盖，shell.css 新增动画自动继承，不再重复写。

### 0.4 新增 token（声明于 `.acp-shell`，全部带 `--shell-` 前缀）

```css
.acp-shell {
  /* 尺寸 */
  --shell-topbar-h: 64px;                 /* 对齐旧 .topbar */
  --shell-switch-h: 32px;                 /* 工作区切换按钮高 */
  --shell-panel-head-h: 42px;             /* 面板头最小高 */
  --shell-side-w: clamp(280px, 24vw, 400px);      /* side 槽宽 */
  --shell-side-rail-w: 32px;              /* side 折叠后竖条宽 */
  --shell-bottom-h: clamp(160px, 30vh, 340px);    /* bottom 槽高 */
  --shell-timeline-rail: 22px;            /* 时间线左轨宽 */
  /* 颜色：审批紫，取值提取自旧 badge.waiting_review（#d7b8ff / rgba(186,122,255,*)），旧文件不动，新侧走 token */
  --shell-violet: #d7b8ff;
  --shell-violet-border: rgba(186, 122, 255, 0.34);
  --shell-violet-bg: rgba(186, 122, 255, 0.14);
  /* 骨架屏 */
  --shell-skel-base: rgba(255, 255, 255, 0.045);
  --shell-skel-hl: rgba(255, 255, 255, 0.10);
  /* 状态边界 */
  --shell-danger-border: rgba(229, 83, 83, 0.45); /* 面板错误内描边 */
  --shell-pop-shadow: 0 8px 24px rgba(0, 0, 0, 0.45); /* 提取自旧 filter-panel 阴影值 */
}
```

---

## 1. Shell 骨架

### 1.1 页面整体结构（HTML 约定）

语义标签 + `acp-shell-*` 命名。三个工作区 section 常驻 DOM，非激活的加 `hidden`；面板元素由 layout-manager 创建/迁移到当前工作区的槽容器内。

```html
<body>
<!-- ① 图标 sprite：一次性内联，classic 环境无依赖 -->
<svg xmlns="http://www.w3.org/2000/svg" hidden aria-hidden="true">
  <defs><!-- symbol 列表见 §1.6 --></defs>
</svg>

<div class="acp-shell" id="acp-shell" data-workspace="research" data-acp-ready="false">
  <a class="acp-skip-link" href="#acp-ws-research" data-i18n="shell.a11y.skip">跳到工作区</a>

  <header class="acp-shell-topbar"><!-- 见 §1.2 --></header>

  <main class="acp-shell-main" id="acp-shell-main">
    <section class="acp-workspace" id="acp-ws-research" role="tabpanel"
             aria-labelledby="acp-tab-research" tabindex="0"
             data-workspace-id="research" data-has-side="true" data-has-bottom="true">
      <div class="acp-slot" data-slot="main"   id="acp-slot-research-main"></div>
      <div class="acp-slot" data-slot="side"   id="acp-slot-research-side"></div>
      <div class="acp-slot" data-slot="bottom" id="acp-slot-research-bottom"></div>
    </section>
    <section class="acp-workspace" id="acp-ws-molecular" role="tabpanel"
             aria-labelledby="acp-tab-molecular" tabindex="0" hidden
             data-workspace-id="molecular" data-has-side="true" data-has-bottom="false">
      <!-- 同上三槽（bottom 槽可省略） -->
    </section>
    <section class="acp-workspace" id="acp-ws-execution" role="tabpanel"
             aria-labelledby="acp-tab-execution" tabindex="0" hidden
             data-workspace-id="execution" data-has-side="true" data-has-bottom="false">
    </section>
  </main>

  <!-- 整页致命错误层（registry/bootstrap 失败时替换 main 显示），见 §2.2 -->
  <div class="acp-shell-fatal" id="acp-shell-fatal" hidden role="alert"></div>
</div>
</body>
```

ID 规约：工作区 `acp-ws-{id}`、tab `acp-tab-{id}`、槽 `acp-slot-{ws}-{slot}`、面板 `acp-panel-{instanceId}`。`instanceId` 即配方里的 instanceId（如 `research-viewer`），保证全局唯一。

### 1.2 顶栏

单行 64px，与旧 `.topbar` 同构（底边框 + `rgba(11,15,20,0.92)` + `backdrop-filter: blur(16px)`）。从左到右四组，`display:flex; gap:14px; padding:0 18px; align-items:center`：

```html
<header class="acp-shell-topbar">
  <div class="acp-shell-brand">
    <div class="acp-shell-brand-mark" aria-hidden="true">A</div>  <!-- 34×34, radius 11, 同旧 .brand-mark 渐变 -->
    <div class="acp-shell-brand-copy">
      <h1 data-i18n="shell.app.title">ACP 分子计算工作台 · Next</h1>
      <span class="acp-shell-badge-lab" data-i18n="shell.app.badge">实验入口</span>
    </div>
  </div>

  <nav class="acp-shell-switch" role="tablist" aria-label="" data-i18n-aria="shell.workspace.switch_label">
    <button class="acp-shell-tab is-active" id="acp-tab-research" role="tab"
            aria-selected="true" aria-controls="acp-ws-research" data-workspace-id="research"
            title="" data-i18n-title="shell.workspace.shortcut_hint">
      <svg class="acp-shell-tab-icon" aria-hidden="true"><use href="#acp-i-research"/></svg>
      <span data-i18n="shell.workspace.research">研究</span>
    </button>
    <!-- molecular / execution 同构 ×2 -->
  </nav>

  <div class="acp-shell-focus" id="acp-focus-chip" role="status" aria-live="polite">
    <!-- 见 §1.2.2 -->
  </div>

  <div class="acp-shell-actions">
    <a class="acp-shell-link-classic" href="/"> <!-- (2026-10-30 修订) 服务路由根 /，不再硬编码经典页路径 -->
      <span data-i18n="shell.link.classic">经典工作台</span>
      <svg aria-hidden="true"><use href="#acp-i-external"/></svg>
    </a>
    <button class="acp-shell-lang" id="acp-lang-toggle" data-i18n="shell.lang.toggle">中文 | EN</button>
    <div class="acp-shell-pill" id="acp-conn-pill" data-conn="unknown" data-i18n="shell.conn.unknown">连接状态未知</div>
  </div>
</header>
```

**实验徽章 `.acp-shell-badge-lab`**：新身份标记，唯一一处醒目差异。规格：`font-size:10px; letter-spacing:0.06em; padding:2px 8px; border-radius:999px; color:var(--amber); border:1px solid rgba(242,184,75,0.35); background:rgba(242,184,75,0.10)`（沿用 `.node-badge` amber 家族，语义＝「可用但未定型」）。不做闪烁/动画。

**工作区切换 `.acp-shell-switch`**：沿用旧 `.section-switch` 药丸分段控件的视觉（外框 `--border-default`、radius 999、内 padding 2px、背景 `rgba(255,255,255,0.03)`）。按钮 `.acp-shell-tab`：高 `--shell-switch-h`、padding `0 12px`、radius 999、12px 文本 + 16px 图标。激活态与旧 `.section-switch-btn.active` 同值：`border-color:rgba(78,161,255,0.44); background:rgba(78,161,255,0.11); color:var(--text-primary)`；非激活 `color:var(--text-secondary)`、hover 提亮 text-primary。快捷键提示放 `title`（`Alt+1/2/3`），不在按钮上显示数字角标（保持顶栏干净）。

#### 1.2.1 顶栏规格速查

| 项 | 值 |
|---|---|
| 高度 | `--shell-topbar-h`（64px），窄屏可换行为两行（§5.4） |
| brand-copy h1 | 16px / 700 / `#eef6ff`，同旧 brand |
| switch 最大宽 | `min(420px, 36vw)`，超出时按钮 label 收缩为图标+短名（CSS `max-width` + ellipsis） |
| actions | 右对齐，`flex-shrink:0`；lang/conn pill 复用旧 `.lang-toggle`/`.status-pill` 视觉，类名换新 |

#### 1.2.2 全局焦点指示（focus chip）

显示当前 `WorkspaceContext` 焦点的只读状态条（首期不可点击导航；交互留给后续命令面板）。视觉复用旧 `.file-tree-task-name` 的 accent 渐变药丸语言：

```html
<div class="acp-shell-focus" role="status" aria-live="polite">
  <span class="acp-shell-focus-item" data-ref-type="structure">
    <svg aria-hidden="true"><use href="#acp-i-structure"/></svg>
    <span class="acp-shell-focus-name mono">caffeine_conf1</span>
  </span>
  <span class="acp-shell-focus-sep" aria-hidden="true">›</span>
  <span class="acp-shell-focus-item" data-ref-type="task">
    <svg aria-hidden="true"><use href="#acp-i-task"/></svg>
    <span class="acp-shell-focus-name mono">20261030_001_Optimize</span>
    <span class="acp-shell-focus-meta">attempt 1</span>
  </span>
</div>
```

- 有焦点 item：`border:1px solid rgba(78,161,255,0.24); background:linear-gradient(90deg, rgba(78,161,255,0.14), rgba(78,161,255,0.05)); color:#dcecff; font-size:11px; padding:3px 9px; radius:7px`，前置 5px accent 圆点（复用旧 task-name 的 dot+halo 画法）。
- 无焦点：`data-empty="true"` → 虚线边、`--text-tertiary`、文案 `shell.focus.empty`。
- chip 整体 `min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap`；单条焦点名超长时截断，`title` 放全名。焦点变更时 chip 加一次性 `is-pulse`（600ms accent 呼吸，见 §3.2）。
- revision/attempt 等次级信息放 `.acp-shell-focus-meta`（10px，`--text-secondary`），不与主名抢宽度。

### 1.3 槽位栅格

栅格挂在 `.acp-workspace` 上（每个工作区独立配方独立尺寸），`side` 通栏两行、`bottom` 只占 main 下方：

```
┌───────────────────────────────┬──────────────┐
│              main             │              │
│                               │     side     │
├───────────────────────────────┤  (通栏两行)   │
│             bottom            │              │
└───────────────────────────────┴──────────────┘
```

```css
.acp-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--shell-side-w);
  grid-template-rows: minmax(0, 1fr) var(--shell-bottom-h);
  grid-template-areas: "main side" "bottom side";
  height: 100%;
  min-height: 0;
}
.acp-workspace[data-has-side="false"] {
  grid-template-columns: minmax(0, 1fr);
  grid-template-areas: "main" "bottom";
}
.acp-workspace[data-has-bottom="false"] {
  grid-template-rows: minmax(0, 1fr);
  grid-template-areas: "main side";
}
.acp-workspace[data-has-side="false"][data-has-bottom="false"] {
  grid-template-areas: "main";
}
.acp-slot { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.acp-slot[data-slot="side"]   { grid-area: side;   border-left: 1px solid var(--border-default); }
.acp-slot[data-slot="bottom"] { grid-area: bottom; border-top: 1px solid var(--border-default); }
.acp-slot[data-slot="main"]   { grid-area: main; }
```

| 槽 | 尺寸 | 最小 | 最大 | 说明 |
|---|---|---|---|---|
| main | 剩余空间 `minmax(0,1fr)` | 内容自定（≥0） | — | 唯一弹性槽 |
| side | `clamp(280px, 24vw, 400px)` | 280px | 400px | 折叠为 `--shell-side-rail-w` 竖条（§1.4.2） |
| bottom | `clamp(160px, 30vh, 340px)` | 160px | 340px | 折叠为仅面板头（§1.4.2） |

**首期不做拖拽**（方案 §5）。可调手段只有折叠开关；未来拖拽把手将挂在现有 `border-left/border-top` 位置，本期不预置任何 handle DOM。

外层：`.acp-shell { display:grid; grid-template-rows: var(--shell-topbar-h) minmax(0,1fr); height:100vh; }`，`.acp-shell-main { min-height:0; position:relative; }`。

### 1.4 面板 chrome

#### 1.4.1 结构

```html
<section class="acp-panel" id="acp-panel-research-agent" role="region"
         aria-labelledby="acp-panel-research-agent-title"
         data-instance-id="research-agent" data-module-id="agent-workspace"
         data-slot="main" data-acp-state="loading" data-acp-generation="3">
  <header class="acp-panel-head">
    <div class="acp-panel-title-group">
      <span class="acp-panel-focus-dot" data-follow="true" title="" data-i18n-title="shell.panel.follow_focus"></span>
      <h2 class="acp-panel-title" id="acp-panel-research-agent-title" tabindex="-1"
          data-i18n="shell.module.agent_workspace">Agent 工作区</h2>
      <span class="acp-panel-instance mono" title="research-agent">research-agent</span>
    </div>
    <div class="acp-panel-head-right">
      <span class="acp-panel-status" hidden></span>       <!-- 模块自设状态 chip，如「只读模式」 -->
      <button class="acp-panel-btn" data-action="reload"
              data-i18n-title="shell.panel.reload" title="重新加载">
        <svg aria-hidden="true"><use href="#acp-i-reload"/></svg>
      </button>
      <button class="acp-panel-btn" data-action="close" hidden
              data-i18n-title="shell.panel.close" title="关闭面板"></button>  <!-- 保留：本期恒 hidden -->
    </div>
  </header>
  <div class="acp-panel-body"><!-- 模块 mount 宿主 / 四态渲染位 --></div>
</section>
```

#### 1.4.2 chrome 规格

| 元素 | 规格 |
|---|---|
| `.acp-panel` | `display:flex; flex-direction:column; min-width:0; min-height:0; background:var(--bg-panel); overflow:hidden; position:relative;` 面板之间用槽位 border 分隔，不加圆角浮卡（同旧 workbench 的分栏语言） |
| `.acp-panel-head` | `min-height:var(--shell-panel-head-h); padding:10px 14px; border-bottom:1px solid var(--border-subtle); display:flex; justify-content:space-between; gap:8px; flex:0 0 auto;` |
| `.acp-panel-title` | 复用旧 `.panel-head h2` 画法：`12px; uppercase; letter-spacing:0.12em; color:var(--text-tertiary); margin:0;` |
| `.acp-panel-instance` | 复用旧 `.backend-version` 画法：mono 10px、`--border-subtle` 边、radius 999、`rgba(255,255,255,0.03)` 底、`--text-secondary`；title 放全 id |
| `.acp-panel-btn` | 22×22、radius 6、透明底、`--text-tertiary`；hover `border-color:var(--accent); color:var(--text-primary)`（同旧 `.panel-collapse-btn`） |
| `.acp-panel-focus-dot` | 6px 圆点。`follow-focus` 绑定＝accent 实心 + `box-shadow:0 0 0 3px rgba(78,161,255,0.10)`（同旧 task-name dot）；固定引用（`is-pinned`，保留态）＝amber 描边空心 + pin 图标 title。面板不绑定时整点隐藏 |
| `.acp-panel-status` | 高 `--status-chip-height` 的 chip，具体着色由模块给 `acp-tag--*`（§4.5） |
| `.acp-panel-body` | `flex:1 1 auto; min-height:0; overflow:auto; padding:12px 14px;`（模块可自带 padding 重置：加 class `is-flush` → `padding:0`，3D 画布用） |
| 关闭按钮 | **保留占位**：DOM 存在、恒 `hidden`、无图标内容；头布局已按最多三个 22px 按钮预留宽度。UI-06 前不启用 |

**折叠（首期唯一布局调节）**：面板头不放进折叠按钮；折叠开关放槽级——`side` 折叠后变 32px 竖条（竖排 `writing-mode:vertical-rl` 显示面板标题 + 展开按钮 `.acp-slot-rail`），`bottom` 折叠后只留 42px 面板头。状态类挂在 `.acp-slot` 上：`.is-collapsed`。触发按钮放该槽第一个面板的 head 右侧（`data-action="collapse-slot"`，图标 `#acp-i-chevron` 旋转 90°/180° 区分方向）。每工作区折叠态持久化到 `localStorage["acp-next-collapse"]`（JSON `{wsId:{side,bottom}}`），损坏即忽略回默认展开。**（2026-10-30 修订）rail 仅折叠态可见**：`.acp-slot-rail` 元素常驻槽内但带 `hidden` 属性，仅当槽持 `.is-collapsed` 时移除 `hidden`（展开态无常驻竖条）；**head 的 chevron 按钮是双向 toggle**——展开时点击折叠、折叠时点击展开（配合 title 在 `shell.panel.collapse`/`shell.panel.expand` 间切换），不是一次性折叠触发器。

**错误态面板描边**：`.acp-panel[data-acp-state="error"] { box-shadow: inset 0 0 0 1px var(--shell-danger-border); }`——不换边框色（避免与槽位 border 打架），用内描边。

### 1.5 class 命名规约（全表）

| 前缀 | 归属 | 例 |
|---|---|---|
| `acp-shell-*` | 页框：顶栏/切换/焦点/actions/fatal/skip | `acp-shell-topbar` `acp-shell-tab` `acp-shell-focus` |
| `acp-workspace` / `acp-slot` | 工作区与槽容器 | `acp-slot` `acp-slot-rail` |
| `acp-panel-*` | 面板 chrome | `acp-panel-head` `acp-panel-instance` |
| `acp-state-*` / `acp-skel` | 四态与骨架 | `acp-state-error` `acp-skel--card` |
| `acp-btn` | 按钮（镜像旧 `.btn` 三变体） | `acp-btn--primary` `acp-btn--ghost` |
| `acp-card-*` | Agent 卡片（agent.css） | `acp-card-summary` |
| `acp-timeline-*` | 计划时间线（agent.css） | `acp-timeline-item` |
| `acp-tag--*` | 语义 chip（agent.css，两处 css 共用） | `acp-tag--pass` `acp-tag--model` |
| `acp-i-*` | SVG sprite symbol id | `acp-i-reload` |
| 状态修饰 | 一律 `is-` 前缀 | `is-active` `is-error` `is-pinned` `is-collapsed` `is-pulse` `is-entering` |

工具类：`.mono { font-family:"SFMono-Regular", Consolas, monospace; }`、`.acp-visually-hidden`（clip 式隐藏，供屏幕阅读器）。两者都定义在 shell.css，旧页无同名类。

### 1.6 图标 sprite（内联，一次定义）

13 个 symbol，全部 `viewBox="0 0 20 20"`、`stroke="currentColor" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"`（`acp-i-spark` 例外，fill currentColor），引用统一 `<svg class="…" width="16" height="16" aria-hidden="true"><use href="#acp-i-*"/></svg>`：

| id | 用途 | path（实现照抄） |
|---|---|---|
| `acp-i-research` | 研究 tab | `M7 3h6M9 3v4l-4.5 8a2 2 0 0 0 1.8 3h7.4a2 2 0 0 0 1.8-3L11 7V3` |
| `acp-i-structure` | 结构焦点/结构 tab | `M10 2l7 4v8l-7 4-7-4V6l7-4zM3 6l7 4 7-4M10 10v8` |
| `acp-i-task` | 任务焦点/执行 tab | `M7 5h9M7 10h9M7 15h9M4 5h.01M4 10h.01M4 15h.01` |
| `acp-i-reload` | 面板重载/重试 | `M16.5 10a6.5 6.5 0 1 1-2-4.7M16.5 3v3.5H13` |
| `acp-i-error` | 错误态 | `M10 3l8 14H2L10 3zM10 8v4M10 14.5h.01` |
| `acp-i-lock` | 无权限 | `M5 9h10v8H5zM7.5 9V7a2.5 2.5 0 0 1 5 0v2` |
| `acp-i-external` | 跳经典/外部链接 | `M9 4H4v12h12v-5M11 4h5v5M16 4l-7 7` |
| `acp-i-spark` | 模型解释标记 | `M10 2l1.6 4.9L16.5 8.5l-4.9 1.6L10 15l-1.6-4.9L3.5 8.5l4.9-1.6L10 2zM17 14l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2z`（fill currentColor） |
| `acp-i-shield` | 验证事实标记 | `M10 2l6 2.4v4.1c0 3.8-2.8 6.7-6 8.5-3.2-1.8-6-4.7-6-8.5V4.4L10 2zM7.4 9.6l2 2 3.4-4` |
| `acp-i-pin` | 固定引用 | `M10 4a3 3 0 0 1 3 3c0 2.4-3 4.5-3 4.5S7 9.4 7 7a3 3 0 0 1 3-3zM10 11.5V17` |
| `acp-i-chevron` | 折叠方向 | `M5 8l5 5 5-5` |
| `acp-i-copy` | 复制错误详情 | `M8 8h8v8H8zM5 12V4h8` |
| `acp-i-signal` | 连接状态（未知/离线） | `M4 16v-2M8 16v-5M12 16v-8M16 16V4` |

---

## 2. 状态设计（加载 / 空 / 错误 / 无权限）

### 2.1 四态统一矩阵

所有状态渲染在 `.acp-panel-body` 内（或替换画布槽的 `is-flush` 内容），由 Shell 统一提供 DOM 与样式；模块也可借用同一套 class 自渲染局部状态。面板根元素 `data-acp-state` 同步当前态（`loading|empty|error|denied|ready`），供集成测试断言。**（2026-10-30 修订）**模块内部状态另用**模块级**钩子 `data-acp-module-state`（挂在模块自渲染的根节点上，如 `.acp-mod-viewer`/`.acp-mod-tasks`/`.acp-agent-root`），与 Shell 面板级 `data-acp-state` 分名，避免同名歧义；集成测试断言面板级用 `data-acp-state`，断言模块内部态用 `data-acp-module-state`。

| 态 | 触发 | 视觉 | ARIA | 内部构成 |
|---|---|---|---|---|
| loading | 模块脚本/首屏数据未就绪 | 骨架屏（结构化面板）或 spinner+label（画布面板），见 §2.3 | 容器 `role="status" aria-live="polite" aria-busy="true"`；骨架 `aria-hidden="true"` + `.acp-visually-hidden` 文案 | `.acp-state-loading` |
| empty | 就绪但无数据/未选择 | 复用旧 `.placeholder` 画法：`min-height:120px; border:1px dashed var(--border-default); radius:var(--radius); background:rgba(255,255,255,0.02); 居中 12px --text-tertiary` | `role="status"` | `.acp-state-empty`（标题 12px `--text-secondary` + 提示 11px `--text-tertiary`） |
| error | 脚本加载失败 / `mount()` reject / 模块 `reportError` | 面板内错误边界：`is-flush` 内衬块 + 面板 `inset` 红描边；面板头与 chrome 保持可见 | `role="alert"` | `.acp-state-error`，见 §2.2 |
| denied | 能力协商 403 / 引用跨项目被拒 | 与 empty 同布局，但图标用 `#acp-i-lock`、左侧 2px amber 边、标题 `--text-secondary`；**不用红色**（非故障，是状态） | `role="status"` | `.acp-state-denied` |

空态文案必须是「未选择/暂无」类事实句 + 一条指引（如「在执行工作区选择任务后，此处显示其结构」），禁止「即将上线」类营销语。

### 2.2 错误边界：面板局部 vs 整页

**边界划分**：单个面板的脚本加载失败、mount 失败、运行时异常 → 局部错误边界，**绝不**冒泡成整页错误，兄弟面板与 Shell 不受影响（方案 §7.1）。只有 Shell 自身启动链失败（registry 清单拉取失败/解析失败、宿主 adapter 初始化失败）→ 整页 fatal。

**局部错误块**（渲染于 `.acp-panel-body`，面板头保留，可在头上看到 reload 按钮）：

```html
<div class="acp-state acp-state-error" role="alert">
  <svg class="acp-state-icon" aria-hidden="true"><use href="#acp-i-error"/></svg>
  <div class="acp-state-copy">
    <p class="acp-state-title" data-i18n="shell.state.error_title">面板加载失败</p>
    <p class="acp-state-msg mono">molecule-viewer: script error …</p>  <!-- module id + 一行摘要，最多 2 行截断 -->
  </div>
  <div class="acp-state-actions">
    <button class="acp-btn acp-btn--primary" data-action="retry"
            data-i18n="shell.state.error_retry">重试</button>
    <button class="acp-btn acp-btn--ghost" data-action="copy-error"
            data-i18n="shell.state.error_copy">复制错误详情</button>
  </div>
</div>
```

- 视觉：`padding:18px; display:grid; grid-template-columns:auto 1fr; gap:6px 12px; align-items:start;` 图标 20px `--red`；标题 13px `--text-primary`；msg 11px mono `--text-secondary`。actions 跨两列右对齐（`grid-column:1/-1; justify-self:end; display:flex; gap:8px`）。
- **重试按钮位置约定**：局部错误的重试永远在错误块右下（actions 行首，primary 样式）；错误块顶部不放按钮；面板头的 reload 图标按钮等效触发同一 retry。
- 重试行为契约：清错误 → `data-acp-generation` +1 → 显示骨架屏 → 重新走 loader（失败缓存已被移除，允许真重试，见方案 §3.2）；迟到旧 generation 的结果一律丢弃。
- 「复制错误详情」复制完整 error message + stack + instanceId + generation（剪贴板 API，失败降级为选中 msg 文本）。

**整页 fatal**（`.acp-shell-fatal`，替换 `main` 显示；顶栏保留以便用户切回经典）。CSS 联动：`#acp-shell-main[hidden] { display:none; }`，fatal 显示时 Shell 给 `main` 加 `hidden`、fatal 移除 `hidden`；fatal 自身 `position:absolute; inset:var(--shell-topbar-h) 0 0 0; display:grid; place-items:center; padding:24px; background:var(--bg-base); z-index:40;`：

```html
<div class="acp-shell-fatal" role="alert">
  <div class="acp-shell-fatal-card">   <!-- 居中卡：radius 18、border-subtle、blur 底，同旧 .viewer-empty-inner 画法 -->
    <p class="acp-state-title" data-i18n="shell.fatal.title">实验入口初始化失败</p>
    <p class="acp-state-msg mono">registry 404: /next/registry.json</p>
    <div class="acp-state-actions">
      <button class="acp-btn acp-btn--primary" data-action="fatal-retry" data-i18n="shell.fatal.retry">重试初始化</button>
      <a class="acp-btn acp-btn--ghost" href="/" data-i18n="shell.fatal.back_classic">返回经典工作台</a> <!-- (2026-10-30 修订) 服务路由根 / -->
    </div>
  </div>
</div>
```

### 2.3 骨架屏 vs spinner 取舍

| 场景 | 用 | 理由 |
|---|---|---|
| 文本/列表/卡片型面板（task-explorer、agent-workspace） | 骨架屏 `.acp-skel` | 布局已知，占位防跳动 |
| 画布型面板（molecule-viewer 3D 区、图表） | spinner + label | 画布无法用块状骨架伪造，假骨架反而误导 |
| 按钮级短异步（重试、复制、切换项目） | 按钮内 14px spinner，`<400ms` 不显示 | 避免闪烁 |
| 工作区切换（实例存活） | 什么都不显示 | 实例 DOM 迁移/唤醒，无加载语义 |

骨架屏规格：块 `background:var(--shell-skel-base)`、radius 6、`animation: acp-skel-pulse 1.2s ease-in-out infinite`（0%/100% opacity .55，50% 1；reduced-motion 下被 base.css 全局终止，静态呈现即最终态）。三种预置变体：

- `.acp-skel--list`：6 行 `height:14px; margin-bottom:10px`，宽度依次 82%/65%/74%/58%/80%/40%（模拟任务列表）。
- `.acp-skel--card`：3 张卡骨架，每张 = `height:12px` 标题行 + 两行 10px 内容行 + `height:26px` actions 行（模拟 agent 卡片）。
- `.acp-skel--canvas`：居中 `.acp-spinner`（16px，2px `--border-default` 环 + 上 1/4 弧 `--accent`，`animation:acp-spin .8s linear infinite`）+ 12px label（`shell.state.loading`）。

spinner 变体 `.acp-spinner--btn`（14px，按钮内嵌）。

---

## 3. 工作区配方视觉

### 3.1 三配方槽位分配

与方案 §3.5 配方形状一一对应（instanceId 沿用方案示例命名）：

| 工作区 | main | side | bottom | data-has-* |
|---|---|---|---|---|
| research | `research-agent`（agent-workspace，readonly 模式） | `research-viewer`（molecule-viewer，单活动实例） | `research-tasks`（task-explorer，紧凑行高 30px） | side+bottom |
| molecular | `mol-viewer`（molecule-viewer，同一单活动实例迁移至此） | `mol-config`（首期占位面板，UI-03 接 task-config） | — | side |
| execution | `exec-tasks`（task-explorer，完整行高 36px + 工具栏） | `exec-detail`（任务详情 + 工件/证据浏览，首期由 task-explorer 模块自带 detail 视图填充） | — | side |

- **Viewer 单活动实例**：research 与 molecular 共用同一实例，切换时 DOM 节点迁移到目标槽。视觉契约：迁移过程不得出现重载闪白（3D canvas 内容保持），面板头 instance chip（`research-viewer`→不变，实例 id 不随槽改名；若实现选择销毁重建则必须先骨架后恢复，且 camera/样式状态经显示状态恢复）。实例 id 固定为 `shared-viewer` 亦可，实现车道定夺后写进配方 JSON，本文不锁死。
- **三配方 × 单活动 viewer 矩阵（2026-10-30 修订）**：viewer 只驻两个配方——research（side 槽）/ molecular（main 槽）；**execution 不放 viewer**，其 side 槽留 `exec-detail` 占位面板（任务/工件/配置语义，见上游方案 §5）直至 UI-03 接入。即迁移路径只在 research↔molecular 之间发生，execution 配方永远不触发 viewer 元素迁移。
- **占位面板**（未知/未交付模块，含 `mol-config`）：`.acp-panel` 头正常（title=模块名、instance chip、无 reload），body 用 empty 态画法 + `acp-tag--neutral`「待接入」chip（`shell.slot.pending_module`）+ 一行说明该模块归属哪个 UI 阶段。配方 JSON 引用不存在模块 id 时同样落到此占位（方案 §3.5「未知模块显示占位」）。
- 每工作区 `document.title` 加后缀：`{app.title} · {workspace 名}`（i18n key 复用 `shell.workspace.*`）。

### 3.2 切换与焦点反馈

一次工作区切换的视觉序列（全部 ≤200ms，一次性、不循环）：

1. **tab**：目标 `.acp-shell-tab` 加 `is-active` + `aria-selected="true"`，其余移除。激活底色 rgba(78,161,255,0.11) 即时切换（`--transition`）。
2. **工作区入场**：目标 `.acp-workspace` 移除 `hidden` 并加 `is-entering` —— `animation: acp-enter 140ms ease-out`（from `opacity:0; transform:translateY(4px)`），`animationend` 后移除类。旧工作区直接 `hidden`，无退出动画（切换要快）。
3. **焦点 chip**：若切换由焦点驱动（如 openStructure 命令落点在 Molecular），chip 更新文本并 `is-pulse` 一次（`animation: acp-pulse 600ms ease-out`：accent 阴影 0→6px→0）。
4. **跟随面板指示**：所有 `binding="follow-focus"` 的面板 focus dot 在收到上下文变更时 `is-pulse` 一次（同上动画）。用户在面板内做了局部固定选择（未来 `is-pinned`）时 dot 换 amber 空心 + pin 图标，不参与 pulse。
5. **标题**：`document.title` 后缀更换。

「当前工作区」除此之外没有额外大块高亮——tab 激活态 + 内容即全部指示；顶栏不做工作区色彩主题化（三工作区同色，避免暗示数据隔离）。

### 3.3 焦点流向（视觉职责）

- 全局焦点变更 → 顶栏 chip + 各 follow 面板 dot pulse（§3.2.3–4）。
- 面板收到与自身无关的焦点（如 structureRef 变更对 task-explorer）→ 面板内容静默不动，不闪不加載。
- 面板数据因焦点刷新 → 面板内部走自己的 loading（列表用骨架行，不整屏白）。

---

## 4. Agent 只读卡片（UI-03A）

全部位于 `css/shell/agent.css`，只服务 `agent-workspace` 模块（research main 槽）。首片只读：无输入框、无提交按钮、无审批操作；所有动作按钮只做**导航**（openStructure / openTask / 查看工件），写链路永远不在这张卡上（方案 §4.6）。

### 4.1 卡片通用骨架与布局

`.acp-card` 通用画法（沿用旧 `.mech-panel-block` 家族）：`border:1px solid var(--border-subtle); border-radius:var(--radius); background:rgba(255,255,255,0.02); padding:12px 14px; margin-bottom:12px;`。

```html
<article class="acp-card acp-card-summary">
  <header class="acp-card-head">          <!-- flex, space-between, gap 8, margin-bottom 10 -->
    <h3 class="acp-card-title" data-i18n="agent.summary.title">研究摘要</h3>
    <span class="acp-card-meta mono">record r7</span>  <!-- 11px --text-tertiary -->
  </header>
  <div class="acp-card-body"> …rows / timeline / notes… </div>
  <footer class="acp-card-actions">       <!-- flex, gap 8, margin-top 10; 按钮一律 acp-btn--ghost 小号 -->
    <button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openStructure" data-ref="…"
            data-i18n="agent.action.open_structure">打开结构</button>
  </footer>
</article>
```

research main 槽内卡片纵向单列排布（宽面板不折双列——卡片行长短不一，双列会拉出大量空白）；side/bottom 不放卡片。

### 4.2 研究摘要卡 `acp-card-summary`

内容为 `<dl>` 行式键值（`dt` 11px `--text-tertiary` 固定 88px 宽；`dd` 12px `--text-secondary`，值类 id 用 `.mono`）：

| 行（dt key） | dd 内容 | 缺失时 |
|---|---|---|
| `agent.summary.goal` 研究目标 | 目标文本（ResearchRecord 授权引用） | `—` |
| `agent.summary.input_structures` 输入结构 | 结构名列表，每项带 `#acp-i-structure` 前缀图标 + mono 名 | 空数组 → 「无」 |
| `agent.summary.record_version` 记录版本 | `record r{n}` mono chip | `—` |
| `agent.summary.related_tasks` 关联任务 | 任务 chip 列表（mono 名 + 服务状态 badge 色，仅服务权威状态） | 「无」 |

actions：`打开结构`（`openStructure`，多个输入时打开当前焦点那个）、`打开任务`（`openTask` → 跳 Execution 并聚焦该 job/attempt）。引用无效（跨项目/已删除）时按钮 disabled + title 说明，不隐藏。

### 4.3 计划/步骤时间线 `acp-card-plan`

候选与执行分开、首版列表式垂直时间线：

```html
<ol class="acp-timeline">
  <li class="acp-timeline-item is-candidate">
    <span class="acp-timeline-node" aria-hidden="true"></span>
    <div class="acp-timeline-content">
      <div class="acp-timeline-row">
        <span class="acp-timeline-title">scan 3,4 · 1.0→3.0 Å</span>
        <span class="acp-tag acp-tag--candidate" data-i18n="agent.plan.state.candidate">候选</span>
      </div>
      <p class="acp-timeline-meta mono">proposal r3 · 20261030_002_Scan</p>
      <div class="acp-timeline-actions">
        <button class="acp-btn acp-btn--ghost acp-btn--sm" data-i18n="agent.action.open_task">打开任务</button>
      </div>
    </div>
  </li>
  <!-- ×N -->
</ol>
```

结构：`.acp-timeline { list-style:none; margin:0; padding:0; position:relative; }` 左轨 `--shell-timeline-rail` 宽；`.acp-timeline-item { position:relative; padding:0 0 14px 0; }` 轨线 `.acp-timeline::before { content:""; position:absolute; left:10px; top:6px; bottom:6px; width:1px; background:var(--border-default); }`；节点 `.acp-timeline-node { position:absolute; left:5px; top:4px; width:11px; height:11px; border-radius:50%; }`。

**四态视觉映射**（状态只来自 Proposal 与服务回执，前端不推断）：

| 状态类 | 节点 | tag | 语义/来源 |
|---|---|---|---|
| `is-candidate` 候选 | 空心：`border:1.5px solid var(--text-tertiary); background:var(--bg-panel)` | `acp-tag--candidate`（中性灰，同旧 `.mech-manual-chip` 画法） | Proposal 生成、未送审批 |
| `is-pending` 待审批 | 空心虚线：`border:1.5px dashed var(--shell-violet-border)` | `acp-tag--pending`（violet 家族，对齐旧 `badge.waiting_review`） | 服务侧审批中/WAITING_REVIEW |
| `is-submitted` 已提交 | 实心 accent + 2px halo（同焦点 dot 画法） | `acp-tag--submitted`（accent 家族，同旧 `.badge.running`） | 已受理、任务 RUNNING 等 active 态 |
| `is-result` 结果 | 实心，颜色随结论：green/red/amber | `acp-tag--result` + 结论 badge（复用旧 `.badge.completed/.failed` 配色） | 服务终态 + 验证器结论分开两行显示：任务 badge 在上，验证结论 tag 在下；**任务 COMPLETED ≠ 验证 PASS**，两者都存在才都显示 |

时间线 meta 行只放可验证引用（proposalRevision、taskRef、eventId），mono 10–11px。缺字段 → 显示 `—` + `.acp-tag--missing`（见 §4.6），不省略行。

### 4.4 证据卡 `acp-card-evidence`

```html
<article class="acp-card acp-card-evidence">
  <header class="acp-card-head">
    <h3 class="acp-card-title" data-i18n="agent.evidence.title">证据</h3>
    <span class="acp-card-meta mono">tsmode · attempt 2</span>
  </header>
  <dl class="acp-card-rows">
    <div class="acp-card-row"><dt data-i18n="agent.evidence.source">来源</dt>
      <dd class="mono">20261028_011_Tsmode / mode 7</dd></div>
    <div class="acp-card-row"><dt data-i18n="agent.evidence.version">版本</dt>
      <dd class="mono">input sha256:ab12…</dd></div>
    <div class="acp-card-row"><dt data-i18n="agent.evidence.report">验证报告</dt>
      <dd><span class="acp-tag acp-tag--pass"><svg aria-hidden="true"><use href="#acp-i-shield"/></svg>
          <span data-i18n="agent.verify.pass">验证通过</span></span>
          <button class="acp-btn acp-btn--ghost acp-btn--sm" data-i18n="agent.evidence.open_report">查看报告</button></dd></div>
    <div class="acp-card-row is-missing"><dt data-i18n="agent.evidence.missing">缺失项</dt>
      <dd><span class="acp-tag acp-tag--missing" data-i18n="agent.missing.irc">IRC 连接性未验证</span></dd></div>
  </dl>
  <footer class="acp-card-actions">
    <button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openStructure" data-i18n="agent.action.open_structure">打开结构</button>
    <button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openArtifact" data-i18n="agent.action.open_artifact">查看工件</button>
  </footer>
</article>
```

行规则：`dt` 固定宽与摘要卡一致；`dd` 内多个值用 chip 并排 `flex-wrap`。`is-missing` 行 dt/dd 前置 amber 左边条（`border-left:2px solid rgba(242,184,75,0.5); padding-left:8px`），把注意力引向缺口。缺失即列缺失，**禁止**从关联任务「顺推」一个结论补位（方案 §4.6：缺失保留缺失原因，`title` 放原因文本）。

### 4.5 模型解释 vs 科学验证 —— 强制视觉二分

Agent 卡片内任何文字只属于两种来源之一，样式不得互换：

| | 模型解释 `acp-model-note` | 验证事实 `acp-verified-fact` |
|---|---|---|
| 容器 | `border:1px dashed var(--border-default); background:rgba(255,255,255,0.02); radius:var(--radius-sm); padding:8px 10px` | `border-left:2px solid {语义色}; background:rgba(255,255,255,0.02); padding:6px 10px`；语义色随结论 pass/fail/warn = green/red/amber |
| 标签 chip | `acp-tag--model`：中性灰底（同 `.mech-manual-chip`）+ `#acp-i-spark` 图标 + 文案「模型解释」 | `acp-tag--pass/--fail/--warn` + `#acp-i-shield` 图标 + 文案「已验证事实」 |
| 文字 | 12px `--text-secondary` | 数据行 `.mono` 12px `--text-primary` |
| 数据来源 | 模型会话文字（DSH/宿主事件） | 服务权威状态 + 验证器报告 |

禁止清单（实现与评审共同核对）：

1. 模型文字容器禁止出现 green/red/amber 任何形式（边、底、chip、图标）。
2. `acp-tag--pass/--fail` 只能由服务数据渲染；模型文字里即使含「已完成/已验证」字样也不得升级为该 chip。
3. 工具调用成功 ≠ 任务完成 ≠ 验证通过：三者在时间线/证据卡里是三个独立 tag，禁止合并成一个「✓ 完成」。
4. 只有结束事件的工具调用不补造开始时间，显示 `—`；长时间无消息显示连接状态未知（§4.6），不显示推测进度。

`acp-tag--model` 的 spark 图标是「生成内容」标记，不是「AI 助手」品牌装饰——凡是模型输出的自由文本块，无论出现在哪张卡，都必须包 `acp-model-note`。UI-03B 工具调用时间线（`acp-card-tools`）与审批卡（`acp-card-approval`）本期**保留类名不实现**，防止后续命名冲突。

### 4.6 连接态与数据缺失态（「空」而非臆造）

agent-workspace 面板头的 `.acp-panel-status` 承载连接 chip；面板体按数据可用性降级：

| 态 | 头部 chip | 面板体 |
|---|---|---|
| 正常（只读数据就绪） | `acp-tag--neutral`「只读模式」 | 三张卡 |
| 未连接 DSH/模型 | `acp-tag--conn`「只读 · 未连接模型」+ `#acp-i-signal`（2026-10-30 修订：连接态改用中性 `acp-tag--conn`；`acp-tag--model` 语义保留给模型解释输出专用） | 照常渲染服务侧可得卡片；不显示任何会话/工具卡 |
| 连接状态未知（长时间无事件） | `acp-tag--warn`「连接状态未知」 | 时间线正常 + 头部 chip 示警；不暂停其他卡 |
| 无研究记录 | 无 chip | 整面板 empty 态：`agent.empty.no_record` 标题 + 指引副句（先在 Execution 选择任务/研究） |
| 单卡数据缺失 | — | 该卡照常渲染，缺失行 `—` + `acp-tag--missing`（amber 虚线边），title=缺失原因 |

`acp-tag--missing`：`border:1px dashed rgba(242,184,75,0.5); color:var(--amber); background:rgba(242,184,75,0.07)`。所有「未知/缺失」一律显式呈现，不留空白假装成功（方案 §7.1 来源一致性门）。

### 4.7 Agent 相关 class 清单（agent.css 全量）

`acp-card` `acp-card-summary` `acp-card-plan` `acp-card-evidence` `acp-card-head` `acp-card-title` `acp-card-meta` `acp-card-body` `acp-card-rows` `acp-card-row`（`is-missing`）`acp-card-actions`；`acp-timeline` `acp-timeline-item`（`is-candidate|is-pending|is-submitted|is-result`）`acp-timeline-node` `acp-timeline-content` `acp-timeline-row` `acp-timeline-title` `acp-timeline-meta` `acp-timeline-actions`；`acp-tag`（`--neutral` `--model` `--conn` `--candidate` `--pending` `--submitted` `--result` `--pass` `--fail` `--warn` `--missing`；`--conn` 为 2026-10-30 修订新增的连接态中性 chip，`--model` 保留给模型解释专用）；`acp-model-note` `acp-verified-fact`（`is-pass|is-fail|is-warn`）；保留：`acp-card-tools` `acp-card-approval`。

`acp-tag` 基础规格（与旧 `.badge` 同族）：`display:inline-flex; align-items:center; gap:5px; border-radius:999px; border:1px solid; padding:2px 9px; font-size:11px; font-weight:600; line-height:1.2; height:var(--status-chip-height)`，各语义色取 §0.3/§0.4 对应 rgba 组合。

---

## 5. 可访问性

### 5.1 键盘模型

| 键 / 焦点位置 | 行为 |
|---|---|
| `Alt+1` / `Alt+2` / `Alt+3`（任意位置） | 切换 Research / Molecular / Execution（避免 Ctrl+数字：浏览器标签快捷键冲突） |
| `←` / `→`（焦点在 switch 内） | roving tabindex 相邻 tab 移动并激活（即时切换，无 delay） |
| `Home` / `End`（焦点在 switch 内） | 首 / 末工作区 |
| `Tab` 全局序 | skip-link → 顶栏（brand → switch → focus chip（不可聚焦，status）→ actions）→ 当前工作区 main 面板 → side 面板 → bottom 面板。DOM 顺序即视觉顺序（§1.1 槽顺序已保证），不用 tabindex 正数调序 |
| 面板内 | 模块自管；面板标题 `tabindex="-1"` 仅作编程焦点落点 |

**焦点恢复**：Shell 维护 `Map<workspaceId, lastFocusedElement>`。切走时记录 `document.activeElement`（若在将被隐藏的工作区内）；切回时元素仍 connected → `.focus()`，否则 → 聚焦该工作区 main 面板标题（`.acp-panel-title`，`tabindex="-1"`）。经 `openTask`/`openStructure` 命令切换时，落点直接是目标面板标题（焦点跟随用户意图），不回旧焦点。

### 5.2 ARIA 角色清单

| 位置 | 角色/属性 |
|---|---|
| 工作区切换 | `nav[role=tablist]` + `button[role=tab][aria-selected][aria-controls]`；roving tabindex（激活 tab `tabindex=0`，其余 `-1`） |
| 工作区 | `section[role=tabpanel][aria-labelledby=tab id][tabindex=0]` |
| 面板 | `section[role=region][aria-labelledby=面板标题 id]` |
| 顶栏焦点 chip | `role=status aria-live=polite`（文本变化播报） |
| 面板错误块 | `role=alert`（插入即播报，无需 live） |
| loading | 容器 `aria-busy=true`；骨架 `aria-hidden=true` + `.acp-visually-hidden` 播报文案 |
| empty/denied | `role=status` |
| fatal 层 | `role=alert` |
| 骨架/装饰图标/spinner | 一律 `aria-hidden=true` |

`data-i18n-aria`（如 tablist 的 `aria-label`）为新增第四个 i18n 属性（旧机制只有 text/title/ph/opt；实现时在 shell 的 i18n 应用器里补这一个属性扫描，属 shell.css/js 新文件内实现，不改旧 i18n.js）。

### 5.3 颜色对比约束（实测值，深色底）

| 组合 | 对比度 | 规则 |
|---|---|---|
| `--text-primary` #e5edf5 / #0b0f14 | ≈16.2:1 | 无限制 |
| `--text-secondary` #b0bec8 / #111821 | ≈9.4:1 | 正文、状态标题一律用它 |
| `--text-tertiary` #6b7b8a / #0b0f14 | ≈4.3:1 | **低于 AA 4.5**。只准用于：面板标题等大写装饰标签、meta 行、空态副句（沿用旧页既定用法）；禁止用于错误/无权限/验证结论等状态关键文案 |
| `--accent` / 底 | ≈7.2:1 | 自由使用 |
| `--green` / #111821 | ≈7.7:1 | 自由使用 |
| `--amber` / 底 | ≈10.6:1 | 自由使用 |
| `--red` #e55353 / #111821 | ≈4.8:1 | 限 11px/600 以上 chip 文字与图标/描边；**错误正文用 `--text-primary`**，红只做图标、tag 与 inset 描边 |
| `--shell-violet` #d7b8ff / 底 | >9:1 | 自由使用 |

另：状态永不只靠颜色区分——tag 均带文字；时间线节点空心/实心/虚线三态 + tag 文案双通道（§4.3）；无权限用图标（lock）+ 文字。

### 5.4 窄屏（<1024px）降级为单列

断点沿用旧页 `@media (max-width: 1023px)`（`.mobile-status-bar` 同款切点）：

```css
@media (max-width: 1023px) {
  .acp-shell { grid-template-rows: auto auto minmax(0, 1fr); height: auto; min-height: 100vh; overflow: auto; }
  .acp-shell-topbar { height: auto; flex-wrap: wrap; padding: 10px 14px; row-gap: 8px; }
  .acp-shell-switch { order: 5; flex-basis: 100%; overflow-x: auto; }   /* 换行到第二行，横向滚动 */
  .acp-shell-focus { order: 6; flex-basis: 100%; max-width: none; }      /* 第三行整行 */
  .acp-shell-link-classic span { display: none; }                        /* 只留图标省宽 */
  .acp-workspace {
    grid-template-columns: minmax(0, 1fr) !important;
    grid-template-areas: "main" "side" "bottom" !important;
    grid-template-rows: none !important;
    height: auto;
  }
  .acp-slot { min-height: 320px; border: none !important; border-top: 1px solid var(--border-default) !important; }
  .acp-slot[data-slot="side"].is-collapsed, .acp-slot[data-slot="bottom"].is-collapsed { min-height: 0; }
}
```

单列顺序 main → side → bottom（信息优先级即配方优先级）；折叠槽只留头/竖条。3D 画布在窄屏按容器实际尺寸 resize（模块 `resize()` 生命周期钩子，非本文范围但布局依赖它）。窄屏不做旧页那种 mobile-tab 二级切换——单列直排 + 顶栏 tablist 已可达全部内容。

---

## 6. i18n

### 6.1 机制契约（复用现有机制，不复制旧词典）

新入口不加载旧 `js/workbench/i18n.js`（其 `applyI18n` 绑定旧页大量渲染函数，不可整用）。在 `frontend/js/shell/i18n.js` 内实现**同契约**的独立实例：

- 词典形状：`{ "zh-CN": {key: str}, "en-US": {key: str} }`；词典挂在 `window.ACPShell.i18n`，不占用旧全局 `I18N`/`t`。
- `t(key, vars)` 回退顺序与旧版逐字一致：当前语言 → `zh-CN` → 返回 key 本身；`{var}` 插值同旧实现（split/join）。
- 属性扫描：`data-i18n`（textContent）、`data-i18n-title`、`data-i18n-ph`、`data-i18n-opt` + 新增 `data-i18n-aria`（aria-label）；应用后设 `document.documentElement.lang = currentLang`。
- 语言存储：**共用旧 key `localStorage["acp-lang"]`，取值 `zh-CN` / `en-US`**（刻意共享：用户在任一入口切语言，两边一致）。
- 语言切换按钮 `#acp-lang-toggle` 行为与旧页相同：翻转 `currentLang`、写 storage、重扫属性 + 重渲染当前动态内容（卡片等由模块在语言变更事件里自刷，Shell 发 `acp:langchange` 自定义事件）。
- 所有 key 以 `shell.` / `agent.` 前缀命名空间化，即使将来与旧词典合并也不冲突（旧页已有 `tab.*` `status.*` 等，勿撞）。
- 模块键命名空间（2026-10-30 修订）：模块自有文案 key 允许 `<module>.*` 前缀（如 `next.viewer.*`、`next.tasks.*`），但**必须收录于本共享词典**（`js/shell/i18n.js`）——模块不得携带私有词典副本；双语切换统一由 Shell 词典 + `acp:langchange` 事件驱动。

### 6.2 文案 key 清单（初稿 copy，待校对）

**shell — 页框/工作区**

| key | zh-CN | en-US |
|---|---|---|
| `shell.app.title` | ACP 分子计算工作台 · Next | ACP Molecular Workbench · Next |
| `shell.app.badge` | 实验入口 | Experimental |
| `shell.lang.toggle` | 中文 \| EN | 中文 \| EN |
| `shell.link.classic` | 经典工作台 | Classic workbench |
| `shell.workspace.switch_label` | 工作区 | Workspaces |
| `shell.workspace.shortcut_hint` | Alt+1/2/3 切换工作区 | Alt+1/2/3 to switch workspace |
| `shell.workspace.research` | 研究 | Research |
| `shell.workspace.molecular` | 分子 | Molecular |
| `shell.workspace.execution` | 执行 | Execution |
| `shell.focus.empty` | 未选择结构或任务 | No structure or task selected |
| `shell.focus.attempt` | 第 {n} 次尝试 | attempt {n} |
| `shell.conn.unknown` | 连接状态未知 | Connection unknown |
| `shell.conn.online` | 在线 | Online |
| `shell.conn.offline` | 离线 | Offline |

**shell — 面板/槽位**

| key | zh-CN | en-US |
|---|---|---|
| `shell.panel.reload` | 重新加载 | Reload |
| `shell.panel.close` | 关闭面板 | Close panel（保留，本期不显示） |
| `shell.panel.follow_focus` | 跟随全局焦点 | Follows global focus |
| `shell.panel.pinned` | 已固定引用 | Pinned reference（保留） |
| `shell.panel.collapse` | 收起此栏 | Collapse |
| `shell.panel.expand` | 展开此栏 | Expand |
| `shell.module.agent_workspace` | Agent 工作区 | Agent Workspace |
| `shell.module.molecule_viewer` | 结构查看器 | Structure Viewer |
| `shell.module.task_explorer` | 任务浏览 | Task Explorer |
| `shell.module.task_detail` | 任务详情与证据 | Task Detail & Evidence |
| `shell.module.task_config` | 任务配置 | Task Config（占位标题） |
| `shell.slot.pending_module` | 待接入 | Coming in later phase |
| `shell.slot.pending_hint` | 该面板将在 {phase} 阶段接入 | This panel lands in phase {phase} |

**shell — 四态/fatal/a11y**

| key | zh-CN | en-US |
|---|---|---|
| `shell.state.loading` | 加载中… | Loading… |
| `shell.state.error_title` | 面板加载失败 | Panel failed to load |
| `shell.state.error_retry` | 重试 | Retry |
| `shell.state.error_copy` | 复制错误详情 | Copy error details |
| `shell.state.denied_title` | 无权限访问 | Access denied |
| `shell.state.denied_body` | 缺少能力：{capability} | Missing capability: {capability} |
| `shell.state.denied_hint` | 切换项目或联系管理员后重试 | Switch project or contact your admin, then retry |
| `shell.fatal.title` | 实验入口初始化失败 | Failed to start the experimental workspace |
| `shell.fatal.retry` | 重试初始化 | Retry startup |
| `shell.fatal.back_classic` | 返回经典工作台 | Back to classic workbench |
| `shell.a11y.skip` | 跳到工作区 | Skip to workspace |

**agent — 卡片（UI-03A）**

| key | zh-CN | en-US |
|---|---|---|
| `agent.summary.title` | 研究摘要 | Research summary |
| `agent.summary.goal` | 研究目标 | Goal |
| `agent.summary.input_structures` | 输入结构 | Input structures |
| `agent.summary.record_version` | 记录版本 | Record version |
| `agent.summary.related_tasks` | 关联任务 | Related tasks |
| `agent.summary.none` | 无 | None |
| `agent.plan.title` | 计划与步骤 | Plan & steps |
| `agent.plan.empty` | 暂无计划步骤 | No plan steps yet |
| `agent.plan.state.candidate` | 候选 | Candidate |
| `agent.plan.state.pending` | 待审批 | Pending approval |
| `agent.plan.state.submitted` | 已提交 | Submitted |
| `agent.plan.state.result` | 结果 | Result |
| `agent.evidence.title` | 证据 | Evidence |
| `agent.evidence.source` | 来源 | Source |
| `agent.evidence.version` | 版本 | Version |
| `agent.evidence.report` | 验证报告 | Validation report |
| `agent.evidence.missing` | 缺失项 | Missing |
| `agent.evidence.open_report` | 查看报告 | View report |
| `agent.verify.pass` | 验证通过 | Verified |
| `agent.verify.fail` | 验证未通过 | Not verified |
| `agent.verify.warn` | 部分验证 | Partially verified |
| `agent.note.model` | 模型解释 | Model explanation |
| `agent.note.verified` | 已验证事实 | Verified fact |
| `agent.action.open_structure` | 打开结构 | Open structure |
| `agent.action.open_task` | 打开任务 | Open task |
| `agent.action.open_artifact` | 查看工件 | View artifact |
| `agent.connect.readonly` | 只读模式 | Read-only |
| `agent.connect.no_model` | 只读 · 未连接模型 | Read-only · no model connected |
| `agent.connect.unknown` | 连接状态未知 | Connection unknown |
| `agent.empty.no_record` | 暂无研究记录 | No research record |
| `agent.empty.no_record_hint` | 在执行工作区选择任务后，此处显示关联研究 | Select a task in the Execution workspace to see linked research |
| `agent.missing` | 缺失 | Missing |
| `agent.missing.reason` | 缺失原因：{reason} | Missing: {reason} |
| `agent.missing.irc` | IRC 连接性未验证 | IRC connectivity unverified |

状态类文案（任务 RUNNING/COMPLETED 等）不在本清单——直接复用旧词典同名语义 key（`status.running` 等族），实现时把这几条同步进 shell 词典（key 不改名），保证两入口状态词一致。

---

## 7. 实现车道交付清单（对照用）

1. 新建：`frontend/next/index.html`（§1.1 结构 + §1.6 sprite + CSS/script 顺序：base.css → shell.css → agent.css；`js/shell/i18n.js` → workspace-context → module-registry → module-loader → commands → layout-manager → recipes → bootstrap，全 classic script 显式顺序。2026-10-30 修订：更正为实际正确序——context 先于 registry/loader（layout 的 deps 注入依赖它），commands/layout 先于 recipes/bootstrap（bootstrap.start 组装链按此序消费））。
2. 新建：`frontend/css/shell/shell.css`（§0.4 token + §1 骨架 + §2 四态 + §5.4 窄屏）、`frontend/css/shell/agent.css`（§4 全量 class）。
3. 不改动：`frontend/ACP_Workbench_v2.html`、`css/workbench/*`、`js/workbench/*`、`js/vendor/*`。
4. 测试钩子（已内置在规格里）：`data-acp-state` / `data-acp-module-state`（2026-10-30 修订：模块内部状态钩子，与面板级分名）/ `data-acp-generation` / `data-instance-id` / `data-workspace` / `data-has-side|bottom` / `data-conn` / `#acp-shell[data-acp-ready]`；集成断言用这些属性，不解析视觉类。
5. UI-01 验收对照（方案 §6）：加载失败可重试（§2.2 重试契约）→ 断言 `data-acp-state` 从 `error` 经 `loading` 回 `ready`；重复挂载无残留 → generation 递增 + 旧 body 清空；键盘可达 → §5.1 全表；窄屏 → §5.4。
6. 已知保留项（不做、不删）：面板 close 按钮（恒 hidden）、`is-pinned`、`acp-card-tools`、`acp-card-approval`、拖拽把手。

## 8. 未决项（不阻塞 UI-01）

- `shared-viewer` 实例 id 与迁移/重建策略二选一（§3.1），实现车道定夺后回写配方 JSON。
- execution side 的「详情/证据」在 UI-02 由 task-explorer detail 视图承载还是独立 evidence 模块，待 UI-03 Evidence 拆分方案定。
- DSH 连接态数据源（事件接口 vs 快照轮询）待 UI-00 核验表；本规格只承诺三种头部 chip 的视觉（§4.6）。
- `--shell-violet` 族是否回灌 base.css 统一旧 badge.waiting_review 的硬编码值——旧文件不动原则下本期不做，记为后续 token 收敛项。
