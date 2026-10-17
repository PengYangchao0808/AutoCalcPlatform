# ACP 文档入口

下一次修改只执行一份方案：[ACP 完整架构调整方案 v1.12](ACP_Architecture_Adjustment_Plan_20261016.md)。后续进展、切片验收与方案修订在该文件维护，不再另建并行总方案。

- [现行运行契约与开发参考](reference/current/)：仍约束实际行为，文件归类不表示逐条内容已重新审计，冲突以代码与 AGENTS.md 为准。
- [下一轮方案附件](reference/next-change/)：模块解释、接口、候选选型、战略及代码质量证据，实施顺序统一服从主方案。
- [历史归档](archive/2026-10-16/README.md)：旧方案、历史证据和展示材料，保留追溯，不自动构成待办。

此前已整理文档与引用；本轮另完成首批前端代码对接，未为此修改后端运行配置、数据库 schema 或科学计算逻辑。

双人开发的命名、写权、接口交接与切片顺序统一见[第 37 节](ACP_Architecture_Adjustment_Plan_20261016.md#37-统一命名与双人并行开发计划)。

本轮保持现有界面与操作流程，Antigravity 拆主 HTML 的同时，OpenCode 实现独立模块；按功能片接入。写权与当前协作方式以[第 40 节](ACP_Architecture_Adjustment_Plan_20261016.md#40-antigravity-与-opencode-并行开发)为准，第 38 节的兼容门和第 39 节的 HTML 维护性目标继续有效：

- [Antigravity：主 HTML 拆分方案](reference/next-change/ACP_HTML_Decomposition_Antigravity_20261016.md)——原样外置并按依赖/功能拆分 CSS/JS，保留现有页面；附可直接派发的提示词。
- [Antigravity／Gemini：界面范围说明](reference/next-change/ACP_Frontend_UI_Antigravity_Gemini_20261016.md)——无界面重设计任务；新增文件拆分职责已明确。
- [OpenCode：前端架构并行开发](reference/next-change/ACP_Frontend_Architecture_OpenCode_20261016.md)——可立即开发的 OC-0—OC-5、专属文件/测试、逐片接入包及可直接派发的提示词。

两份提示词可以同时派发。Antigravity 独写主页面/CSS/旧页面脚本并接入模块，OpenCode 独写 Client/Visualization/controllers/contracts 与专属测试；不用等全部 HTML 拆完。共享入口按小片串行接入，同符号只迁一次，不进行界面美化。附件不形成第二套总方案。

本轮前端以“**其他部分保持现有版本，前端单独修改后原功能仍可运行**”为硬性验收门。独立切片、旧 `/js`/`/css` 静态路径、前后端兼容矩阵及回退见主方案 38.6—38.10 和 OpenCode 任务书第 9—11 节。新 prepare、事件与科学产品等后续能力单独接入，不阻塞本轮兼容重构完成。

2026-10-16 首批模块已完成原页面对接：[前端对接验收记录](reference/current/ACP_Frontend_Integration_20261016.md)。相关 pytest 808 项、Node 模块 45 项、真实浏览器 8 场景通过；真实科学软件/LSF、生产部署与全量三层迁移不在本次完成声明内。

对接后追加[前端冗余与维护性审计](reference/next-change/ACP_Frontend_Redundancy_Audit_20261016.md)：列出重复函数、启动即移除的旧 S2 弹窗、请求封装和大函数等 11 项候选，包含双人分工、兼容门与几何公式不可直接互换的反例。本轮仅审计，未删除运行代码；实施仍服从主方案。

2026-10-17 剩余任务前端收口工作统一见[主方案第 42 节](ACP_Architecture_Adjustment_Plan_20261016.md#42-前端任务部分剩余收口方案2026-10-17)：按当前代码剔除已完成的重复清理、默认值共享、部分请求迁移与三维创建入口统一，再依次推进任务会话/详情、事件/刷新、命令/创建编辑、候选结果及可视化实例边界。此次仅制定后续方案；当前复核为前端相关 pytest 508 项、Node 行为 75 项通过，浏览器未重新执行，不构成剩余批次已完成的声明。
