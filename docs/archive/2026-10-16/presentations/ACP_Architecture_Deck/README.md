# ACP · Architecture Deck / 架构战略演示网站

**版本**：2026-10-16 · ACP 设计方案 v1.5 · **44 张可编辑 HTML 演示页面**

这是一个 **完全静态 / 离线可打开** 的 PPT 风格 HTML 网站，没有 npm、Vite、CDN、在线字体或图表服务。主要使用 HTML / CSS / SVG / 原生 JavaScript 搭建流程图与形状。适用于浏览器演示、屏幕分享，以及继续用 Cursor / OpenCode / Claude Code / Codex 等工具 Vibe Coding。

## 立即使用

1. 双击 `index.html`，或直接打开同目录的 `ACP_Architecture_Deck_Standalone.html`（单文件版与 index.html 同目录）。
2. 使用 ← → / ↑ ↓ / 空格翻页；点击顶部 **目录** 快速跳转，**备注** 查看讲解说明，**⤢** 全屏，**导出** 打印全部页面为 PDF。
3. 在架构总览中点击九个模块卡片，查看对应所有权、职责与接口详情。
4. 地址中的 `#three-tier`、`#milestones` 等哈希可直接定位到某页。

## 代码文件与分工

```
ACP_Architecture_Deck/
├── index.html          # HTML 壳 / 导航按钮 / 弹窗 / 控制布局
├── styles.css          # 整体视觉语言 / 网格 / 卡片 / 流程 / 排版 / 打印样式
├── slides.js           # ★ 44 张页面全部内容：标题、简述、HTML 图形、备注
├── app.js              # 原生 JS：翻页、目录搜索、缩放、打印、模块详情
├── README.md           # 使用与修改说明
└── sources/            # 用户提供的 6 份原始 Markdown 参考文档
```

- **只修改文字和流程图**：主要编辑 `slides.js`，查找每页的 `id` / `title` / `html`。
- **修改全局设计**：编辑 `styles.css` 顶部 `:root` 颜色变量和 `.slide`、`.panel`、`.swimlane` 等可复用样式。
- **修改导航和交互**：编辑 `app.js`，核心函数是 `drawSlide()`、`goto()`、`inspect()` 和 `printSlides()`。
- **新增页面**：复制 `slides.js` 中一个 `{...}` 对象，修改 `id`（须唯一）、`section`、`title`、`subtitle`、`theme`、`status`、`html` 与 `notes`。页数自动更新。
- **流程图**：不是截图，节点是 `.lane-node`、箭头是 `.lane-arrow`；三层九模块用 `.arch-row` / `.arch-unit`。可以直接修改 HTML 文本、增减节点、变更颜色。
- **分子示意**：封面分子图是内联 SVG，可直接编辑坐标或换成你已有的项目 Logo。

## 可直接交给 Vibe Coding Agent 的修改指令（范例）

> 请读取本项目 `README.md`、`slides.js`、`styles.css` 和 `app.js`，维持无外部依赖、离线浏览、按内容需要扩充页面 PPT 式导航。修改架构第 7 页的三层九模块图，将各模块点击弹窗增加“公开 API / 禁止依赖 / 迁移状态”三个分区；不要把设计态写成已实施态，也不要改变 CCCP/ACR/ACP 的职责边界。必要时改样式。使用本地 Chromium 逐页检查溢出和脚本错误。

## 内容准确性说明

- 主线采用 `ACP_Architecture_Adjustment_Plan_20261016.md` v1.5，其余五份文档作为模块/产品/组件细化依据。
- 「目标愿景」「目标架构」「当前静态清点」「组件候选待验证」已在各页右上角区分。
- 当前工作区 **尚未实施代码迁移**。页面中展示的目录结构、MCP / Agent、Credit、插件 SDK、部署档位等按原文所述，均不表示已经发布。
- 竞品和开源候选是附件中的设计研究判断；具体安装兼容、科学计算与恢复能力尚需 PoC、版本锁定和持续回归。
- 产品独立开源、完整 GUI、完整 CLI、核时计价和项目 Archive 在文件中属于产品承诺/实施目标，不等于已独立发包或上线收费。

## 后续推荐工程化路线

目前无构建步骤，适合直接迭代文案与视觉。待原型内容稳定后，若需要动态内容管理，可再逐步迁移为数据驱动（JSON/YAML）或 React/Vite，但**不必为了 Vibe Coding 提前引入复杂依赖**。

---

来源文件位于 `sources/`，保留原文件名。网页为视觉化二次编辑版，不替代权威架构方案及现行代码/规范。打印为 PDF 需要浏览器原生“保存为 PDF”操作；网页本身不会在线上传数据。

## 2026-10-16 架构细节补充

保留原 34 页与视觉体系，在相应章节插入 10 页，共 44 页。新增：分层关系、选帧调用链、执行/科学协议、当前优化调用链、恢复判定、磁盘到视图接力、文件归属、目标文件内容、worker 前置依赖、切片验收。

baseline 表示已对照当前源码的调用骨架；design 表示目标或实施建议。public.py 等建议文件名并非已存在或已冻结 API。sources 原始文档保持不变。
