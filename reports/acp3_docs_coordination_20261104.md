# ACP3 双人开发文档同步回执

**日期**：2026-11-04
**性质**：文档修订与检查记录，不是功能验收
**基线**：本地 HEAD d4f4ede6 + 用户已有未提交/未跟踪修改；没有清理或重置现有实现。

## 输入与修改范围

已读取《完善分子反应网络》与《GitHub竞品分析》的可用文字记录。未取得历史附件图像，本批不据此改变界面草图。本地原缺少对话引用的 CCCP × ACR 后端文档，读取 GitHub main 的 blob 56b570a72d58e699ec7caac0d845f1dc79ce8a1d 补齐本地参考并加协作修订，未更新远端。

核对当前工作区 identity.py、plugin metadata、migrations、相关设计与 AGENTS；不是全量代码审查。

- 架构文档 §10 作为双人责任、交接、C0—C5/P0—P6 排序和进度的唯一总纲，未新增平行总方案。
- A 主写化学语义/ResearchPlan/网络适配/前端；B 主写科学 Workflow 编译、插件 Renderer/Parser/Validator 与最小 ACR/Data 接入；共享接口双人评审。
- 增加 IR/Plan/目录/原生包/结果/证据关联交接清单、三图/多层身份、未知态、科学门、恢复、兼容和可拆工任务。
- 后端旧工作站优先周历改为扩展参考；撤销旧 024—027 迁移号，不预占新号；前端追加缺口更正和 A 线增量。
- 保留网络 B0/B1 契约，澄清身份关联、业务归并、严格物种/构象/TS 和化学边的区别；更正身份键 24 hex 截断说明及 B0 历史状态歧义。
- 同步前端目标/DSH 插件/宿主基线/备选 bridge、RPH、拓扑、MCP 计划、parity 范围、存储参考、文档导航、根 README 和相关 AGENTS。
- 未改应用代码、冻结机器契约、历史证据或发布门；没有提交、生产服务重启、真实模型/QC/LSF 计算。

## 检查

| 检查 | 结果 | 范围/限制 |
|---|---|---|
| python scripts/check_docs_links.py | PASS（收尾复跑确认） | 相对链接；不检查远端 URL 或 Markdown 锚点 |
| 首次直接 pytest 两个文档文件 | NOT_VERIFIED | 当前解释器未安装 acp，conftest 导入失败；随后以 sys.path 加入 src 重跑 |
| test_docs_links.py + test_services_doc_sync.py（sys.path 加入 src） | 10 PASS / 2 FAIL / 2 ERROR | 两个 Services 符号/identity-forwarder 测试因 Windows 缺 fcntl 失败；两项 tmp_path 测试因临时目录访问拒绝出错 |
| test_docs_links.py（工作区 basetemp） | 1 PASS / 2 ERROR，进程 exit 1 | tmp_path 与收尾仍访问拒绝；没有修改权限或跳过测试 |

未执行全量回归、浏览器、真实计算或部署验收。环境受限项需在可用测试环境原样复跑，不能由链接检查通过替代。C0/P0 及后续新功能证据仍为 NOT_VERIFIED。
