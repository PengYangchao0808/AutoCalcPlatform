# ACP 前端界面与 HTML 拆分范围 — Antigravity／Gemini

日期：2026-10-16。状态：**不重设计界面；新增 HTML 维护性拆分任务由 Antigravity 执行。** 保留本文件名，作为此前任务书的范围修正入口。

上位方案：[完整架构调整方案 v1.12 第 40 节](../../../ACP_Architecture_Adjustment_Plan_20261016.md#40-antigravity-与-opencode-并行开发)，第 39 节仍规定 HTML 维护性目标。

- 本次实际派发：[ACP 主 HTML 拆分方案 — Antigravity](ACP_HTML_Decomposition_Antigravity_20261016.md)，末尾附可直接派发的提示词。
- 同时开发模块：[OpenCode 前端架构任务书](ACP_Frontend_Architecture_OpenCode_20261016.md)。

## 1. 保留现有界面

本轮仍不安排布局优化、美化、主题重做、组件重设计、文案整理或新功能页面。上一版“无需 Gemini 界面开发”的含义保留，但用户新增了主 HTML 过大、维护困难的文件拆分需求，因此 Antigravity 现在有明确的结构维护任务。

目标是保留当前页面和功能，将约 2.9 万行内联 JS、4,590 行内联 CSS 分离，并进一步按功能/依赖拆成可维护文件。主页面保留约 1,500 行静态结构，不通过删除功能或压缩换行减行数。

## 2. 两个阶段的边界

| 阶段 | 执行者 | 内容 |
|---|---|---|
| H：HTML 维护性拆分 | Antigravity | 原样外置 CSS/JS，按完整依赖组分文件、保持作用域/顺序、适配位置测试、验证旧页面/旧服务 |
| AR：前端职责重构 | OpenCode | 在专属目录同时实现 Client、Visualization、controller 和专属测试，提供逐片接入包 |

默认 H 与 AR 同时开发。主 HTML、CSS、旧页面代码/bootstrap 和共享页面测试由 Antigravity 独写；新模块/contracts/controllers/专属测试由 OpenCode 独写。每片模块就绪后由 Antigravity 接入并移除旧 owner，不等整个 H 或 AR 完成。已迁移内容直接复用，不搬回或复制。

H 阶段可移动旧 api*/绘图代码，但不改网络/刷新/状态/科学规则，不新建第二套客户端或 renderer。更深的依赖改造交给 AR，完整循环依赖组可以登记例外，不强行切碎。

## 3. 独立运行与验证

两个阶段分别遵守原后端不变仍可运行的要求。新资源只使用现有 `/js`、`/css` 挂载，不增加后端模板/静态路由、运行时 HTML fetch 或新框架/构建工具。

Antigravity 验证页面拆分和生产接线，OpenCode 验证新模块内部行为/契约；双方对每个已接入功能片验证原界面、真实 S0、唯一 owner 和回退。分别记录模块已验证、页面已接入、功能片已验收；实际尚未验证的部分标 NOT_VERIFIED。

后续如确有已确认的界面增量，仍需单独明确区域、接口、写权和验收，不从此次维护拆分自动扩成界面美化。
