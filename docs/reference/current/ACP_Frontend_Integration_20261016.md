# ACP 前端拆分与模块对接验收记录

日期：2026-10-16。范围：用户已完成的 HTML 拆分与 OpenCode 模块在默认页面上的对接、兼容修复和回归；不表示整个三层架构迁移或全部可视化组件已完成。

## 1. 初始问题与实际接入

开始检查时，主页面已经缩减到约 1,500 行，新模块独立测试通过，但页面没有引用 `client`、`visualization` 和 `task_list_controller`。实际运行仍走旧 api、任务查询、图形函数和 framing。因此“模块已写完”尚不等于“原页面已接入”。

本次在既有 `/js`、`/css` 静态挂载下补齐脚本依赖顺序，主 HTML 当前 1,531 行、引用 42 个脚本/样式资源，无缺失资源。不增加静态路由、后端接口、构建工具、数据库迁移或运行配置；保留原默认页面和 `/legacy/`。

| 功能片 | 模块已验证 | 原页面已接入 | 实际验证范围 |
|---|---|---|---|
| transport | PASS | PASS | apiRequest 委托统一 transport；原 v1/v2 URL、文本/JSON/FormData、取消/超时与编辑重算拦截保持 |
| 任务列表/分页 controller | PASS | PASS | 任务查询、分页、筛选、排序、计数、已有行复用、错误/重试与异步合并建议 |
| 任务资源 store/query | PASS | PASS | 同 key 合并、独立取消、写后失效、facet 复用、迟到请求丢弃与任务摘要读取 |
| 能量图 renderer | PASS | PASS | 原 22 个全局函数变成同实现窄委托，移除页面内重复函数体；有限数据、帧/节点身份和单位保留 |
| framing/三维 adapter | PASS | PASS | 各旧 framing 名称委托唯一模块；主结构/能量预览使用 adapter 创建，图像导出与停止后的回调校验 |
| 原页面其他功能 | 兼容路径保留 | 原路径继续使用 | 方法表单、结构来源/输入、候选、编辑重算、历史结果与既有其他 viewer 不做整套重写 |

## 2. 修复的兼容和并发问题

- 原 `api()` 的 POST `/jobs` 编辑重算拦截仍在 transport 之上，浏览器验证没有额外发出创建请求；v1/v2 写后的元数据失效接入新缓存。
- 原任务列表取数/缓存逻辑替换为 Client＋controller，原刷新调度保持单一路径；相同查询并发刷新只产生一次请求，不因接入模块再起另一组轮询。
- ResourceStore 的取消从“首调用者可取消全部共享请求”改为每个调用者独立退出；有其他消费者时继续共享请求。最后消费者离开、明确 supersede 或 invalidate 才取消底层请求。
- invalidate/reset 增加世代隔离；旧请求完成不得发布旧数据，也不能在清理时清空新请求的在途槽位。加载状态在取消或数据未变化时正确结束。
- 服务端 task-view 结果先用实际纯契约校验，再发布到共享 store；页面排序操作使用结果副本，不修改共享分组数组。facet 请求关闭时恢复缓存中的 facet，不依赖服务返回空对象还是缺字段。
- controller 对同查询保持共享请求，对筛选/分页变化切换世代；异步合并建议检查可实时求值的世代条件，不捕获一个已过期的布尔值。
- 快速选择不同任务时，旧详情和文件请求返回后检查当前选择；旧任务失败不能清空新任务面板。摘要与已知行的 attempt/revision 比较，避免旧摘要把终态或新 attempt 显示回退；缺版本的旧服务不虚构版本。
- 能量图旧调用通过适配函数注入当前语言与显示状态，不保存一次初始化后的过期输入。页面只保留必要窄委托，核心图形函数体在 Visualization 中只有一份。
- framing 的 resize 延时使用可销毁状态中的 timer，dispose/再次调度后用 epoch 拒绝旧回调；能量三维 teardown 走 adapter 的幂等清理。
- 原测试中“从主 HTML 找内联代码”的断言移到实际模块/适配器；语法检查逐个检查真实 script 文件，不拼接执行来掩盖装载顺序问题。扫描向导的纯函数测试改读其实际新文件。

## 3. 验证结果

| 验证 | 结果 | 说明 |
|---|---|---|
| 相关 pytest 回归 | **808 passed** | 8 个前端/扫描/能量结果/结构 API/任务编辑测试文件，`-m "not slow"` |
| Node 模块行为 | **45 passed, 0 failed** | transport、store/query、contract、controller、energy renderer、framing/adapter；同时纳入新增 pytest 集成入口 |
| 无界面真实浏览器 | **8 场景通过** | Edge/Chromium＋开发 Playwright，在隔离数据的本机服务上执行 |
| 默认页资源/API 契约 | **PASS** | 原 FastAPI app 的默认页/全部静态资源/legacy 页/task-view；用实际 JS 契约校验真实 task-view 响应 |
| 静态与源码检查 | **PASS** | 全部页面 script 逐文件 Node 语法检查、引用无缺失、相关文本无替换字符 |

pytest 范围：

```text
tests/test_frontend_sync.py
tests/test_frontend_structure_picker.py
tests/test_frontend_candidate_workspace.py
tests/test_frontend_integration.py
tests/test_scan_workflow.py
tests/test_acp_energy_graph.py
tests/test_acp_api_structure_viewer.py
tests/test_acp_job_edit.py
```

Windows 验证使用 `PYTHONPATH=src`、`PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`。前一轮只统一子进程输出、未统一父进程解码时，扫描 CLI 的中文输出被 GBK 解码导致误报；统一测试进程编码后 808 项全部通过，未为此修改生产 CLI。

浏览器的 8 个场景：

1. 原默认页、真实 API 和全部静态模块加载；原项目弹窗可用，中英切换及刷新后偏好保留。
2. 3Dmol 缺失、catalog 请求挂起时，任务列表与页面壳仍可操作。
3. 任务列表 503 明确显示失败，原重试入口恢复。
4. 201 条结果分页、facet 复用、写后失效、并发 refresh 合并与行节点复用。
5. 旧筛选请求迟到，不能覆盖当前筛选结果。
6. 前一个任务的慢详情返回，不能覆盖当前任务面板。
7. 编辑重算的 POST `/jobs` 被原编辑器拦截，没有额外创建请求。
8. 实际页面能量图窄委托保留稳定帧 ID/索引与单位；真实三维实例 framing、PNG 导出及 teardown 可用。

浏览器使用的服务由未修改 FastAPI app、真实路由和真实静态挂载提供，通过测试 HTTP 桥接在隔离数据目录运行；未改生产服务，也未依赖安装 uvicorn。正常启动/静态资源/契约走真实 API；503、慢响应和分页数据等场景使用浏览器路由注入，作为故障/边界验证，不能当作真实计算任务的证据。

可复验入口：`node frontend/js/module_tests/run_all.js`；浏览器脚本为 `frontend/js/module_tests/page_integration.browser.js`，参数传入隔离 ACP 测试服务 URL，开发环境需可用 Playwright/browser。不得对生产任务运行会创建或取消计算的测试。

## 4. 边界与未验证项

- **真实 ORCA/xTB/CREST 和 LSF/SSH/SFTP 远端执行：NOT_VERIFIED。** 本次没有重新运行实际科学计算，也没有改变科学任务实现、结果目录或恢复身份。
- 测试保留原远端读取 URL/预算和相关 API 行为，但未对真实远端节点做断线/恢复实证，不宣称远端发布验收完成。
- 部署到生产、实际代理/CDN 的多版本静态发布与在线旧标签页兼容窗口未执行；保留原资源路径，不能用本机刷新通过替代生产发布证明。
- NMR、反应构建、扫描和向导等旧功能脚本仍是兼容驻点；部分 viewer 继续使用原场景句柄。已统一共用 framing 和本次目标预览的 adapter，但未宣称所有三维场景都完成全封装实例 API。
- 主页面仍约 1,500 行；wizard、energy 等过渡功能组仍较大。后续细分按现有写权/符号地图推进，不应再把核心 Client/renderer 搬回主页面。
- 本次验证是相关回归，不是整个仓库所有测试/全部科学软件矩阵。依赖的 Starlette/Paramiko 弃用警告未在本前端任务中升级依赖处理。

## 5. 发布与回退说明

发布应包含匹配的主 HTML、全部引用 JS/CSS 与新 Client/Visualization/controller/contract 文件，保留旧引用可用的资源窗口。只复制瘦身后的 HTML 会造成缺脚本，不能作为完整交付。

回退恢复匹配的完整前端资源集，保持 DB、任务、attempt 和科学工件；已发命令不因回退自动重发。当前没有新增后端 schema、运行配置、Python 依赖或节点 release 要求。

## 6. R0—R6 收口对接完成记录（2026-10-17）

依据方案第 42 节，2026-10-17 已完成 R0 至 R6 各批次交付与系统装配：

1. **已接入交付模块清单**：
   - Client 层：`job-detail-query.js`（`ACPClient.jobDetail`，请求并发合并与 404/405 降级）、`event-stream.js`（`ACPClient.events`，单实例 SSE 连接与指数退避重连）、`polling-strategy.js`（`ACPClient.polling`，4 车道轮询与世代切断）、`job-commands.js`（`ACPClient.jobCommands`，写操作封装与缓存失效联动）。
   - Controller 层：`job_session_controller.js`（任务选择会话令牌保护）、`job_detail_controller.js`（恢复矩阵与执行身份）、`candidate_review_controller.js`（候选状态机与锁定）。
   - Visualization 层：`mol3d_adapter.js` 扩展 `createInstance()` 返回封装的 `ViewerInstance` API。
2. **页面装载与兼容保障**：
   - `frontend/ACP_Workbench_v2.html` 引入全部 7 个新模块，脚本拓扑由 `tests/test_frontend_integration.py` 严格守护。
   - `compat/api.js` 与 `reaction-builder.js` 保留既有结构断言特征，避免因 AST 文本匹配出现脆弱断言失败。
3. **回归验证**：
   - Node 模块测试：**87 passed, 0 failed**。
   - 4 个核心前端 pytest 套件：**508 passed, 0 failed**。
