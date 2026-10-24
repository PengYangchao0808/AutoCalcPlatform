# acp-parallel-plan-1-frontend - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**Who this is for and what changes for them:** 面向 ACP 平台的前端负责人与后接手的执行代理：把“前端收口与真实能力接入”这一路现在能做的部分变成一份可直接执行的计划——每片都有定位、验收与证据要求，历史完成口径被校正；普通使用者看到的界面、入口与流程保持不变。

**What you'll get:** 一份仓内基线地图与接管表；三维可视化收敛为“受限实例”的逐场景接入；新建/编辑向导按上传解析、预览校验、草稿表单、提交编辑四块拆开；过期清理项逐条关闭；一轮真实验收与可回退的发布资源记录。每片可独立回退。

**Why this approach:** 先冻结基线再动代码（上一轮证据已丢失）；按文件不相交的小切片推进，“撤旧实现 + 接新实现”同一切片完成，避免双重刷新/双重动画；新服务能力只登记入场门，不提前实装。

**What it will NOT do:** 不改后端、接口、数据库或配置（前端单独运行）；不重设计界面、不换框架；不改科学数值与候选/任务语义；未就绪能力保持未启用、不用假数据占位。

**Effort:** XL
<!-- Effort is exactly ONE band, never hours/days. Quick = single edit, minutes of agent work; Short = one focused change, a few files; Medium = multi-file feature in one session; Large = several waves, one long session; XL = multi-session or architectural work. A written duration is rewritten to a band. -->
**Risk:** Medium - 行为等价迁移面广；浏览器未测时 A05 退出门保持开放（失败与未验证不得互替，评审 A5）。
**Decisions to sanity-check:** ① 范围只含可立即执行的 A00–A03 与验收 A05，A04/A06 以「Gated conditional tasks」登记接续任务（等待交付物版本/owner/退出条件）；② 记录落位：接管表/基线地图/验收记录/入场门必须落仓内 tracked 文档（`.omo/` 只放临时运行日志），主方案不直接改；③ 测试策略按“先迁移至少不弱化的既有护栏，再补新模块行为测试”；④ 每文件单写者、按切片小提交；⑤ 失败/未验证纪律（评审 A5）：已执行失败只能是 FAIL（修复后重跑），缺依赖未执行才是 NOT_VERIFIED；浏览器未测时 **A05 真实浏览器退出门保持开放**（不条件关闭）。

Your next move: 计划按你的要求先跑双通道评审；评审结果与摘要呈现后，由你启动执行（如 `$ulw-execute acp-parallel-plan-1-frontend`）——本计划不自行开工。完整执行细节见下文。

---

> TL;DR (machine): XL · 风险 Medium · 34 实施 todo + 4 终验：A00 基线→A01 实例边界→A02 向导四片→A03 清理→A05 验收（浏览器未测时退出门保持开放）；已按 2026-10-18 外部评审 A1–A7/X 修订；双通道评审按轮记录。

## Scope
### Affected user and ideal state
<!-- From the draft ledger: who this touches and how they use the result, then one IS row per property with its reason, then one GAP row per difference with its reason. Every todo below closes a GAP row; ## Success criteria proves every IS row. -->
**Affected user:** 四类（按影响排序）。**U1 前端负责人（本计划请求者）**：今天靠多份历史记录人工对齐，R6 余项、`NOT_VERIFIED` 与失真声明混杂；之后拿到一份决策完整、可直接执行的计划，A00–A03 逐项关闭、证据在仓、口径校正。**U2 ACP Workbench 科研用户**：今天使用 1252 行默认页与既有 41 script/10 css 资源，多个场景各自持有三维视图；之后是同一 URL/页签/流程、迁移范围零可感回归、隐藏/切换/销毁不留旧回调、缺 3Dmol 原样降级。**U3 并行两端 B/C 与未来集成**：今天前端尚无针对新契约的接入说明；之后拿到 Client 所需端点/错误/availability/事件语义与浏览器证据，且前端不抢先发明其 schema。**U4 未来维护者/审计者**：今天证据目录不在 checkout、完成声明与代码事实不一致；之后有仓内基线地图 + 接管表 + 验收记录，PASS/FAIL/NOT_VERIFIED 分列。另有交接对象：**U5 共享文档集成写者（B/协调者）**——接收 §43/“42.7” 校正补丁（交付=提供补丁；应用归其所有）；**U6 部署/运维接收方**——接收静态发布包与回退步骤（生产发布/systemd 重启在本计划外，记录交接）；**U7 第二前端写者（Antigravity，若仍活跃）**——其 §40 文件写权必须被尊重（主 HTML/bootstrap/共享页面测试），本计划按单写者逐文件串行运行，必要时按 §40 拆工。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | Plan_1 立即可执行范围（A00–A03）逐项关闭且各有证据；A05 验收与静态发布包记录在案 | 清单自述目标：全部关闭表示前端自身目标及实际启用增量完成 |
| IS-2 | 默认页布局/主题/文案/入口/流程不变；同数据对照无可感差异；URL、静态挂载、`/legacy/` 保持 | §38.6 硬门：只改前端，其余保持基线时原功能仍可运行 |
| IS-3 | 迁移范围内业务只持受限实例：无原生 viewer/model 外泄；一 canvas 一实例；framing/PNG/降级保持；反复挂载/隐藏/A→B→A/销毁无旧回调 | A01 退出门；防迟到回调、双动画与句柄外泄 |
| IS-4 | wizard 四片各有单一状态与调用入口；新建/编辑共用 defaults 与校验；保留 `batch_role_defaults.js`、旧草稿 reader、显式 0/false/null、FormData、无超时例外、409/422 与编辑 POST `/jobs` 拦截 | A02 退出门；8304 行单文件是最大维护点 |
| IS-5 | `loadNodes/loadBackends` 等通用职责经 Client facade + Workbench 用例归位；文件/日志、候选与结果加载在 taskSession 下有 identity/revision/dirty/锁定/迟到规则；不重写第二份 controller | A02/A03 退出门；防跨任务状态泄漏 |
| IS-6 | 历史声明按当前代码事实校正并落到可交付位置；F0/F1+S0 与完整资源回退集固定；未知写“未测”；测试计数保留时点/范围 | A00 退出门；上轮证据丢失（.omo 不在 checkout）不得重演 |
| IS-7 | 验收用既有入口（node 套件、frontend/integration pytest、适用 API 回归、具浏览器环境时真实浏览器）并分列 PASS/FAIL/NOT_VERIFIED；被替代实现移除；接管表关闭；A05 浏览器退出门未测时保持开放（不条件关闭） | A05 退出门；禁 mock/单测通过冒充真实通过 |
| IS-8 | A04/A06 以「Gated conditional tasks」登记可接续任务（等待交付物版本/文件 owner/退出条件），不被静默丢弃；到达入场门即可开片；保持 S0 默认路径 | Plan_1 §3/§4 分段启用原则；评审 A7 |
| GAP-1 | 立即可执行 A00–A03/A05 无单一可执行计划（文档为清单/说明，收口记录含 NOT_VERIFIED 与 R6 余项） | 计划本身与全部 todo 关闭 |
| GAP-2 | `scene_instance.js` 未接默认页；adapter 返回原生 viewer；实例依赖外部注入 viewer、`addModel` 透传 model；`forward()` 静默吞异常（错误结果契约缺失）；场景自建/持有 viewer，主 viewer 无销毁、`initViewer` 无重复保护 | IS-3；R5/A01 核心风险 |
| GAP-3 | `wizard.js` 8304 行职责混驻（上传/解析、预览/校验、草稿/表单、提交/编辑重算、方法配置）；`openMethodConfig`（:5014-7370，约 2.4k 行）归属片未指定；`loadNodes/loadBackends` 未归位 | IS-4/IS-5；A02 |
| GAP-4 | `.omo/evidence/` 证据缺失；H/R1–R4 未在当前 revision 复验；F0/F1+S0 未固定；请求/并发/资源占用未测 | IS-6；A00 |
| GAP-5 | FE-04/05/09/10/11 与能量 22 窄委托未逐项关闭；巨页面测试未拆分 | IS-5/IS-7；A03 |
| GAP-6 | 当前 revision 真实浏览器+S0 联合验收与静态发布包未执行（NOT_VERIFIED）；**A05 退出门保持开放：浏览器未测时不条件关闭（失败≠未验证，评审 A5）** | IS-7；A05 |
| GAP-7 | A04/A06 无仓内可执行入场契约 | IS-8；下述「Gated conditional tasks」表 + 基线文档记录 |

### Must have
- A00：基线冻结（revision/环境/F0/S0）；装载/DOM hooks/全局函数/网络资源/缓存写者/SSE/timer/viewer/调用方地图；§43/“42.7” 校正与 H/R1–R4 复验记录；完整资源回退集（含摘要）；未知项标注“未测”。
- A01：受限实例 API（内部创建/持有 viewer；无原生 viewer/model 外泄；覆盖实际调用点的 setData/selection/style/export/lifecycle）并接入默认页；逐场景迁移（主结构 → 能量预览 → 向导 → NMR → 振动 → 结构查看/编辑 → 其他活跃场景）；每场景数据注入/回调/撤旧 owner/验证；生命周期矩阵验证。
- A02：四片提取（上传/解析、预览/校验、草稿/表单、提交/编辑重算）各单一状态与调用入口；新建/编辑共用 defaults 与校验且保留既有行为；通用节点/后端职责经 Client facade + Workbench 用例归位；taskSession 身份/revision/dirty/锁定/迟到响应核对补缺。
- A03：能量 22 窄委托与在用二维视图复核（renderer 纯度：不联网/不读全局任务/不做全量科学分析）；FE-04/05/09/10/11 按调用闭包处置；页面测试拆分保留等效断言/fixture/收集范围。
- A05：Node + 前端 pytest + 适用编辑/结果 API 回归（记录基线/改动后/跳过原因）；隔离数据真实 S0 默认入口走查矩阵；故障注入与真实响应分别标记；静态发布包（资源集/装载序/摘要/冷热身缓存/旧标签页/回退步骤）；五维状态分列（实现/模块/浏览器/服务接入/真实科学·远程·生产）；浏览器未测时 A05 退出门保持开放。
- 记录：`docs/reference/current/ACP_Frontend_Baseline_Map_<YYYYMMDD>.md`（含接管表权威副本）、`docs/reference/current/ACP_Frontend_Plan1_Acceptance_<YYYYMMDD>.md`；`.omo/evidence/acp-parallel-plan-1-frontend/` 只放临时运行日志；Plan_1 清单勾选与状态更新。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改 `src/**`、API/schema、DB、配置、`pyproject.toml`、部署/systemd；保持 F1+S0 独立交付门。
- A00–A03/A05 实施期间不启用 A04/A06 能力（prepare/availability/持久事件/科学产品/账户/配额/计费/项目包）；不预造成功占位；已登记条件任务（GT-1…GT-5）在对应版本交付物通过入场检查后按阶段规则纳入本次范围（评审 P1-2）。
- 不换框架、不加构建步骤、不新增静态路由、不试点 ECharts（无真实视图需求时）。
- 不删除 `/legacy/`、历史报告读取、旧草稿 reader、有调用者的兼容 shim；无调用闭包证据不得删代码。
- 不改变科学数值语义（二面角等）；不把 mock/单测通过写成真实验收；不把 202 或按钮点击当科学完成；不盲重发超时命令。
- 不触碰无关未提交工作；不在同文件并发写入；不整文件 ours/theirs 覆盖另一写者。
- 不做实际部署/发布：本计划只做发布准备与恢复演练（T33）；实际发布由集成发布任务在终验通过后执行（评审 X6）。
- 遵守前端铁律：ANTI #20（向导默认目录驱动 `resolveDefaultWorkflow()`/`ensureWizardWorkflowValid()`；退役 id 仅历史兼容分支）；#21（视图投影一律经 `frames` 契约）；#22（energy viewer 仅保存候选；`energyGraphConfirmAndBatch` 禁止标识符；3Dmol 经 `scheduleViewerFraming`/`frame3DViewer`）。

### Gated conditional tasks (A04/A06, entry-conditioned, GT-1…GT-5)
> **阶段规则（评审 P1-2）**：A00–A03/A05 实施期间不启用新能力；下表已登记的条件任务在对应版本交付物**通过入场检查后纳入本次范围（激活）**，按编号分配实施子项、消费测试与 B/C 侧对接项；未激活项保持登记，不得据其开工。范围状态用 `IN_SCOPE / DEFERRED / NOT_APPLICABLE`，证据仍只用 `PASS / FAIL / NOT_VERIFIED`；延期≠通过。新代码须验证**能力缺失时的 S0 回退**；组合记录以 **F1-base**（本轮 S0 收口前端）与 **F1-integrated**（接入新服务后的前端）区分，不混用 S0 证据（评审 P1-2/P1-4）。未冻结字段不实现（评审 A7/X3/X4）。

| 编号 | 条件任务 | 等待交付物（版本，含提供方） | Blocked by | 文件 owner | 消费测试 | B/C 侧对接项 | 退出门 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GT-1 | prepare/operation 客户端（Client adapter + Workbench 状态） | B02 的 DataResourcePort/prepare 契约 + 正常/空/部分/失败/旧版本 fixture 冻结版 | B02 交付通过入场检查（版本号记入 T6 基线文档） | `frontend/**`（A） | 契约样例 + 能力缺失 S0 回退 | B02（提供方）；接收/联动项由 B 计划登记 | 契约样例通过 + S0 默认可运行保持 |
| GT-2 | 快照/事件刷新（cursor/去重/重连/快照重置） | B03/B04 的 task-view 快照与持久事件协议版本 | GT-1 同批冻结后 | `frontend/**`（A） | 只停被事件覆盖的轮询 + 降级路径 | B03/B04（提供方） | 降级路径可验证 |
| GT-3 | 科学产品 adapter 接入（经 B 的 frames/VIEW_REGISTRY adapter） | C02/C06 产品 schema + B 投影 adapter 版本 | 对应 adapter 落地后 | `frontend/**`（A，消费侧） | 有限展示、不重算权威判据、缺失原因可见 | C02/C06（提供方）+ B 的 frames/annotation adapter（评审 P1-9 要求新增任务或子项） | 单位/身份/缺失原因逐项一致 |
| GT-4 | 四组合浏览器测试（F0+S0 / F1-base+S0 / F0+S1 / F1-integrated+S1） | B 集成环境 + S1 交付版本 | 组合环境就绪 | A 维护测试并提供 S0 证据；B 对选定集成提交执行黑盒测试并产出、T69 消费产出收口 | 每组合证据绑定提交/契约版本/fixture 摘要 | B 侧 T75（执行）/T69（收口）（评审 P1-4） | 每组合有唯一执行任务；发布前门不消费发布后证据 |
| GT-5 | 账户/配额/项目包 UI | B01/B08/B09/B10/B11 与 M2/M3/M4 范围 | 对应里程碑就绪 | `frontend/**`（A） | 权限/错误/回退验证 | B01/B08/B09/B10/B11（提供方） | 仅在真实接口就绪后启用，保持旧功能可用 |

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after + 等价/更强护栏迁移**（迁移型工作以既有 pin 为行为规格；禁止弱化既有 pin、禁止只查目录存在的空断言）；新/改模块补 Node 行为测试（vm harness：`node frontend/js/module_tests/run_all.js [name-filter]`）；前端 pytest 与扩展回归按切片运行；浏览器（Playwright）按仓库 T20 政策先尝试安装——**使用本路隔离 venv 并按本路测试范围安装项目 extras**（例：`<本路 venv>/bin/pip install -e '.[dev,api,remote,browser]'`，再 `<本路 venv>/bin/python -m playwright install chromium`；全部命令使用已记录的本路解释器，评审 X6/P2-1），不可用则记 NOT_VERIFIED 并附尝试记录（但 A05 退出门保持开放，见下）。
- 主要验证入口（按切片选用，不表示全部适用）：
  - `node frontend/js/module_tests/run_all.js`（17 个 `.test.js`，自动发现；当前基线 183 passed 为 2026-10-17 时点，需复验）。
  - 前端测试：拆分前为 `PYTHONPATH=src python3 -m pytest tests/test_frontend_integration.py tests/test_frontend_sync.py tests/test_frontend_structure_picker.py tests/test_frontend_candidate_workspace.py -q`（当前基线 508 passed 时点同上）；**T29 完成后改用其维护的权威前端测试选择清单/marker（评审 P1-1），最终候选命令必须实际收集新旧用例（以负例注入验证），不得仅以全仓 collect-only 可找到为证**。
  - 扩展回归：`tests/test_acp_energy_graph.py`、`tests/test_acp_api_structure_viewer.py`、`tests/test_acp_job_edit.py`、`tests/test_scan_workflow.py`（按职责列出）。
  - 浏览器：`tests/test_frontend_interaction.py` 等 slow 套件 + `frontend/js/module_tests/page_integration.browser.js`（隔离 ACP 测试服务 URL；不得对生产任务运行创建/取消测试）。
  - 语法：页面引用的全部非 vendor 脚本逐个 `node --check`（不拼接执行掩盖装载顺序）。
- 三态纪律：PASS / FAIL / NOT_VERIFIED；跳过≠通过；故障注入与真实响应分别标记；mock 通过只记“模块验证”。
- 等价判据（IS-2）：sync/contract 套件与装载顺序 pin 全绿 + 逐场景行为清单对照原页面；浏览器可用时补截图对照，不可用记 NOT_VERIFIED。GAP-4 测量工具=Playwright network capture（复用 `page_integration.browser.js` 架设方式）；不可用则静态统计四通道+NOT_VERIFIED 行。
- 失败/未验证纪律（评审 A5）：已执行失败只能是 FAIL（修复后重跑）；缺依赖未执行才是 NOT_VERIFIED；两者不得互替。浏览器未测时可交付代码与模块证据，但 **A05 真实浏览器退出门保持开放（OPEN），不得条件关闭**；F4 不得把 A05 未验证标为整路无条件完成。已有基线失败记录归属并证明本次未扩大影响；所修改路径的回归不得以基线债务豁免。
- Evidence: `.omo/evidence/acp-parallel-plan-1-frontend/task-<N>-acp-parallel-plan-1-frontend.<ext>`；交付性记录写 `docs/reference/current/ACP_Frontend_Baseline_Map_<YYYYMMDD>.md` 与 `docs/reference/current/ACP_Frontend_Plan1_Acceptance_<YYYYMMDD>.md`（以执行日期命名）。每条 todo 验收必须实际执行并保留原始输出摘要。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 0（A00 地基，串行）**：T1 → {T2, T3, T4, T5} → T6。T1 未过不得动任何产品文件；T6 未过不得进入 Wave 1。
- **Wave 1（三组并行 + 文件级单写者）**：
  - **G1 实例边界（A01）**：T7 → T8（**含共享 viewer 全部消费者兼容**，评审 A3）→ {T9, T12} → {T13, T14}（仅其余行为迁移与专项覆盖）→ T11 → T10（等 T18 完成后执行）→ T15。
  - **G2 向导四片（A02）**：T16 → T17 → T18 → T19 → T20 → T21；T22 与序列并行（reaction-builder.js 文件不相交）；T23 在 G1 的 T9 之后并行（若改 reaction-builder.js 则纳入该文件串行链）。
  - **G3 视图与清理（A03）**：T24（T23 后）→ T25（T22/T24 后）→ {T26, T27, T28} → T29（T21/T25/T26/T28 后）→ 收口。
  - **文件冲突规则**：同一文件同一时间仅一个 todo 在改。wizard.js 串行序列 = T16→T17→T18→T10→T19→T20；energy.js = T9→T23→T24→T25；reaction-builder.js = T11→T22→T23（如涉及）→T25；scan-s2.js = T23→T25；guard 文件（`tests/test_frontend_sync.py`、`tests/test_frontend_integration.py`）的 pin 更新随所属切片提交；**T29（测试拆分）等待所有会改这批 guard 的切片（T21/T25/T26/T28）完成后再动**（评审 A6）。
  - **共同执行约束（评审 X1/X5/§6.3）**：跨路输入记录契约版本、提供提交、样例摘要与应用回执；可合入切片必须同带唯一实现 + 旧入口兼容 + 消费者调整 + 依赖/资产/护栏同步，未完成应用的跨路补丁不算该切片已集成；各路使用独立 worktree/venv/run_root/端口/临时输出；里程碑只声明实际支持范围，范围延期单独记录（延期≠通过）。
- **Wave 2（A05 收口，串行为主）**：T30 → {T31, T32, T33} → T34。T30 必须在 Wave 1 全部切片合入后运行。
- **Final verification wave**：F1–F4 并行；全数 APPROVE 后向用户汇报并等待显式确认。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | — | T2–T5 | — |
| T2 | T1 | T6 | T3, T4, T5 |
| T3 | T1 | T6 | T2, T4, T5 |
| T4 | T1 | T6, T33 | T2, T3, T5 |
| T5 | T1 | T6 | T2, T3, T4 |
| T6 | T2–T5 | Wave 1 全部 | — |
| T7 | T6 | T8–T14 | T16, T22, T28 |
| T8 | T7 | T13, T14, T15 | T9, T12, T16+（文件不相交） |
| T9 | T7 | T23, T24, T25（energy.js 串行） | T8, T12, T16+ |
| T10 | T7, T18 | T15, T19（wizard.js 串行） | 非 wizard.js 任务 |
| T11 | T7 | T22, T25（reaction-builder.js 串行） | T8, T9, T12 |
| T12 | T7 | T15 | T8, T9, T13, T14 |
| T13 | T7, T8 | T15 | T12, T14, T16+ |
| T14 | T7, T8 | T15 | T12, T13, T16+ |
| T15 | T8–T14 | T27, T30 | T16–T29（文件不相交） |
| T16 | T6 | T17 | T7+, T24, T28 |
| T17 | T16 | T18 | 非 wizard.js 任务 |
| T18 | T17 | T10, T19 | 非 wizard.js 任务 |
| T19 | T18, T10 | T20 | 非 wizard.js 任务 |
| T20 | T19 | T21 | 非 wizard.js 任务 |
| T21 | T17–T20 | T30 | T22, T24–T28 |
| T22 | T11 | T25 | T12, T13, T14, T16+ |
| T23 | T9 | T24 | T8, T12–T14, T16+ |
| T24 | T23 | T25 | T10（如 T18 已完成）, T16+, T26–T28 |
| T25 | T22, T24 | T26, T29 | T10, T16+, T27, T28 |
| T26 | T25 | T29, T30 | T27, T28, T10 |
| T27 | T15 | T30 | T26, T28 |
| T28 | T6 | T29 | T24–T27 |
| T29 | T21, T25, T26, T28 | T30 | T10, T27 |
| T30 | T15, T21, T25, T26, T27, T28, T29 | T31–T33 | — |
| T31 | T30 | T34 | T32, T33 |
| T32 | T30 | T34 | T31, T33 |
| T33 | T30, T4 | T34 | T31, T32 |
| T34 | T31–T33 | F1–F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

执行总则（所有 todo 适用）：所有路径相对仓库根 `<repo>`；证据写入 `.omo/evidence/acp-parallel-plan-1-frontend/`（文件名沿用各 todo 标注）；同一文件同一时间仅一个写者；guard 文件（`tests/test_frontend_sync.py`、`tests/test_frontend_integration.py`）的 pin 更新随所属切片同提交；S0 保持不改（`src/**`、API、DB、配置、`pyproject.toml`、部署文件禁改）；三态纪律 PASS / FAIL / NOT_VERIFIED（FAIL 与 NOT_VERIFIED 不得互替；范围延期单独记录，延期≠通过）；禁止把 mock 通过当真实通过；禁止弱化既有断言换绿；提交格式沿用仓库 conventional commits（`feat|fix|refactor|test|docs|style(frontend): …`）。对当前**无既有护栏**的行为（上传 FormData、显式 0/false/null、409/422、编辑拦截等），先补等效 Node pin 再迁移（tests-after 仅适用于已有护栏覆盖的行为）。跨路补丁区分「已交付/已集成」（接收方实际应用并通过测试才算集成）；未完成的跨路补丁不算该切片已集成（评审 X1/§6.3）。

- [x] 1. 冻结仓库/环境基线与 F0/S0
  What to do / Must NOT do: 记录 `git status --porcelain`、冻结基线提交 `f410ac9dae035eb424c40c599692500fdea457cc`（**经 Git 命令解析与核对**，如 `git rev-parse`/`git show -s`；**不直接读取 `.git/refs/heads/main`**——linked worktree 中 `.git` 为文件、且 main 可能在并行期间推进；记录本任务分支，不要求其等于实时 main；评审 P2-1）、`node --version`、Python/pytest 可用性、Playwright 状态；**本路独立环境（评审 X6/P2-1）**：固定本路 worktree/venv/run_root/端口/临时输出（不与其他路共用 editable 安装；例：本 worktree 专属 venv 或 `~/.venvs/acp-frontend`；记录解释器路径与 `acp`/`cccp` 实际 import 来源）；**按本路测试范围安装项目 extras**（如 `<本路 venv>/bin/pip install -e '.[dev,api,remote,browser]'`）后再执行 `<本路 venv>/bin/python -m playwright install chromium` 并记录结果（评审 P2-1）；固定 F0=当前前端静态文件集、S0=未改动的服务/CCCP/配置。工作区当前含未提交文档（主方案 M + Plan_1/索引等 untracked 文档）：逐一记录归属；仅文档类未提交记为“基线的已确认未提交工作”（不阻塞，F0/S0 以 frontend/** 与 src/** 的洁净状态为准）；若 frontend/ 或 src/** 有未提交改动：停止并报告归属。绝不 reset/checkout/丢弃。若在独立 worktree 中 Plan_1 清单/索引文档不存在：从主工作目录复制进入（记录来源）后开工。证据写入 `task-1-acp-parallel-plan-1-frontend.md`。
  Closes: GAP-4
  Parallelization: Wave 0 | Blocked by: — | Blocks: T2–T5
  References (executor has NO interview context - be exhaustive): 根 `AGENTS.md` COMMANDS 节；`tests/test_frontend_interaction.py:20-30`（T20 skip 政策与安装命令）；`docs/reference/current/ACP_Frontend_Closure_20261017.md:33`（Playwright 历史不可用）；冻结基线经 Git 命令解析（**不直读 `.git/refs/...`**，评审 P2-1）。
  Acceptance criteria (agent-executable): 证据文件存在且含冻结基线提交与文件摘要、工作区状态、工具版本、本路测试范围依赖安装步骤、浏览器尝试命令与结论；基线/分支经 Git 命令复核（不依赖 `.git/refs/...` 直读）；本路解释器路径与 `acp`/`cccp` 实际 import 来源已记录（隔离证明）。
  QA scenarios (name the exact tool + invocation): happy: `git status --porcelain && git rev-parse HEAD && node --version` 输出入库；failure: 浏览器安装失败→记录 NOT_VERIFIED 行（含原始报错）。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-1-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 机械记录与状态核对。

- [x] 2. 前端调用方地图（从实际默认页建立）
  What to do / Must NOT do: 从 `frontend/ACP_Workbench_v2.html`（1252 行）建立：①有序 script/style 清单（含行号）；②DOM hooks 清单（id/data-* 按区段）；③各文件全局符号（window.ACP* 命名空间 + 顶层 function/let）；④网络入口/查询键/缓存写者（api/apiRemote/apiV2/transport/store/jobsCache/taskViewCache/jobDetailCache 投影）；⑤SSE/timers 通道与 owner；⑥viewer 创建/持有/消费者地图（含 `$3Dmol` 单驻点断言）；⑦selectedJobId/selectJob 调用方图。不按旧行号依赖；不修改任何文件。
  Closes: GAP-4
  Parallelization: Wave 0 | Blocked by: T1 | Blocks: T6
  References (executor has NO interview context - be exhaustive): `frontend/ACP_Workbench_v2.html:7,8-17,1211-1250`；`js/visualization/mol3d_adapter.js:31-38,55-57`；`js/visualization/scene_instance.js:6-7,44,97`；`js/workbench/features/molecule-view.js:100,181,204-205,798,820,1063-1071`；`js/workbench/features/energy.js:131,2878`；`js/workbench/features/wizard.js:1685-1748,2053,3850`；`js/workbench/features/reaction-builder.js:537,700`；`js/workbench/features/nmr.js:347,358,726`；`js/structure_viewer.js:898-905,1123-1133`；`js/vibration_viewer.js:455-463,851-870`；`js/structure_editor.js:1530-1537`；`js/workbench/features/scan-s2.js:16-17,65-67`；`js/workbench/compat/shared-state.js:38,40`；`js/workbench/compat/api.js:8-23,137-140`；`js/client/polling-strategy.js`；`js/client/event-stream.js`。
  Acceptance criteria (agent-executable): 地图条目均有 file:line；`grep -rn '\$3Dmol' frontend/js --include=*.js` 结果仅 adapter+vendor+module_tests；每类写者（缓存/timer/SSE/viewer）恰好一个 owner 或标注“未迁移”。
  QA scenarios (name the exact tool + invocation): happy: 上述 grep + 抽查 3 个 DOM hook 与调用方；failure: 发现未登记写者/计时器→补表后再发布。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-2-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: unspecified-high — 多文件清点与调用方图。

- [x] 3. H/R1–R4 复验与历史声明校正
  What to do / Must NOT do: 在 T1 冻结基线上运行 `node frontend/js/module_tests/run_all.js`、`PYTHONPATH=src python3 -m pytest tests/test_frontend_integration.py tests/test_frontend_sync.py tests/test_frontend_structure_picker.py tests/test_frontend_candidate_workspace.py -q`、扩展 `test_acp_energy_graph.py test_acp_api_structure_viewer.py test_acp_job_edit.py test_scan_workflow.py -q`；记录计数+范围+时点；逐模块核对“模块已验证/页面已接入”（HTML 装载、委托存在、单 init owner）；确认 §43/“42.7” 校正事实（实际会话实现为 `task_session_controller.js`、无 `createInstance()`、`scene_instance.js` 未装载、计数不可相加）。只登记缺陷，不修复；不弱化/跳过既有测试。
  Closes: GAP-4
  Parallelization: Wave 0 | Blocked by: T1 | Blocks: T6
  References (executor has NO interview context - be exhaustive): `frontend/js/module_tests/run_all.js:66-73`；`tests/test_frontend_integration.py:16-50`；`docs/reference/next-change/ACP_Architecture_Progress_and_Parallel_Index_20261018.md:44-54`；`docs/ACP_Architecture_Adjustment_Plan_20261016.md:1717-1760`；`docs/reference/current/ACP_Frontend_Closure_20261017.md:22-29`。
  Acceptance criteria (agent-executable): 三套命令均有原始输出与计数；每个 H/R 模块一行状态（PASS/FAIL/未测+证据）；校正清单含 ≥4 项事实核对；失败项记录归属（基线缺陷 vs 本次引入），不得以基线债务豁免本次回归（评审 A5）。
  QA scenarios (name the exact tool + invocation): happy: 三命令复跑并留存输出；failure: 套件失败→登记为基线缺陷（保留输出，不修复）。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-3-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: unspecified-high — 运行套件并做语义级复验。

- [x] 4. F0/F1+S0 组合与完整静态资源回退集（含摘要）
  What to do / Must NOT do: 枚举默认页全部引用资源（HTML + 41 script + 10 css + vendor），逐文件 sha256 与字节数；定义完整前端资源集与回退步骤（恢复资源集；不动 DB/attempt/工件；不重发命令）；用 TestClient 校验默认页资源可达与 no-cache。不新增静态路由；不改 `server.py`。
  Closes: GAP-4
  Parallelization: Wave 0 | Blocked by: T1 | Blocks: T6, T33
  References (executor has NO interview context - be exhaustive): `src/acp/api/server.py:248-273`；`frontend/ACP_Workbench_v2.html` 全量 script/link；`tests/test_frontend_integration.py:53-87`。
  Acceptance criteria (agent-executable): 清单条数=HTML 引用数（41+10）；每条含 sha256；回退步骤列出具体路径。
  QA scenarios (name the exact tool + invocation): happy: `sha256sum` 全清单生成；failure: 资源 404/缺失→登记并阻塞 T33 发布包。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-4-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 机械枚举与摘要。

- [x] 5. 请求数/并发合并/资源占用测量（或标注未测）
  What to do / Must NOT do: 浏览器可用时（依 T1 结论）：隔离数据+本机真实 S0 上，用固定任务/轨迹样本记录列表/详情/摘要/结构/日志请求数、同 key 并发合并、A→B→A 与隐藏/显示后计时器与请求残留；工具=Playwright network capture（沿用 `page_integration.browser.js` 架设方式）逐指标记录；不可用时按静态实现统计四条轮询通道与覆盖资源，标注“未测（环境受限）”。不承诺拆文件加速；不对生产任务运行。
  Closes: GAP-4
  Parallelization: Wave 0 | Blocked by: T1 | Blocks: T6
  References (executor has NO interview context - be exhaustive): `js/client/polling-strategy.js`（四通道）；`js/client/event-stream.js`；`js/workbench/compat/api.js:137-140`；`tests/test_frontend_interaction.py:36`。
  Acceptance criteria (agent-executable): 证据含“实测/未测”分列；每项注明样本与工具。
  QA scenarios (name the exact tool + invocation): happy: 固定样本测量记录；failure: 环境缺浏览器→NOT_VERIFIED 行+尝试记录。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-5-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 受控测量或静态统计。

- [x] 6. 发布基线文档与接管表 + Plan_1 勾选
  What to do / Must NOT do: 汇总 T2–T5 为 `docs/reference/current/ACP_Frontend_Baseline_Map_<YYYYMMDD>.md`（接管表的权威副本在本文档，非 .omo）：装载/DOM/全局/网络/缓存/SSE/viewer/调用方地图；校正与复验结果（计数时点/范围）；F0/F1+S0（含 dirty-state 快照）与资源回退集指针；测量/未测；接管表（每个未迁移符号→目标 owner；已迁移→单实现状态）；A04/A06 入场门（含 B02–B04/C02/C06 与 B08/B01/B09/B10/B11 标识）与交接契约摘要；§43/“42.7” 校正以可应用补丁格式（含 § 锚点+原文引用）列入（供主方案维护者；交付≠应用，不直接编辑主方案）；Plan_1 checkbox→todo 覆盖映射初版（A00×4、A01×5、A02×4、A03×5、A05×5=23）。更新 `docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md` A00 勾选与状态行。**交接登记（评审 X1/X5）**：引用 B-T3/C-T5 共同产出的受版本控制登记表（owner/范围/provider/consumer/base_commit/prerequisites/acceptance/applied_commit）；前端侧逐文件或逐符号登记；拆分后新增测试文件、`tests/conftest.py`、打包测试在开工前逐文件裁定 owner；跨路补丁区分「已交付」与「已集成」（接收方实际应用并通过测试才算集成）；契约/写权/样例/交接状态/验收摘要必须受版本控制，大日志保留在稳定工件位置并登记 URI/摘要/产生提交/环境/命令/退出码，不以「证据路径存在」为唯一验收。
  Closes: GAP-1, GAP-4, GAP-7
  Parallelization: Wave 0 | Blocked by: T2–T5 | Blocks: Wave 1 全部
  References (executor has NO interview context - be exhaustive): T2–T5 证据；`docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md` A00 节（:17-26）；`docs/reference/next-change/ACP_Parallel_Plans_Review_20261018.md`（X1/X2/X5）；`docs/README.md` 归类约定。
  Acceptance criteria (agent-executable): 基线文档含全部小节；未迁移符号逐一有 owner；Plan_1 A00 项已勾选或注明依据；接管表/校正补丁/入场门均在 tracked 文档内（`.omo` 仅日志）；登记表引用同一版本且「已交付/已集成」状态可用；大日志 URI/摘要/环境/命令/退出码已登记。
  QA scenarios (name the exact tool + invocation): happy: 文档路径有效、表格完整（grep 校验小节标题）；failure: 存在“无 owner”符号→补 owner 再发布。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-6-acp-parallel-plan-1-frontend.md`
  Commit: Y | docs(frontend): baseline map and takeover table
  Recommended task executor category: writing — 记录性交付文档。

- [x] 7. 扩展 scene_instance 为受限实例 API v2（模块）
  What to do / Must NOT do: 扩展 `frontend/js/visualization/scene_instance.js`：`create({container, options})` 内部经 `ACPVisualization.mol3d.create` 创建并持有 viewer（不再要求外部 `opts.viewer`；保留显式测试注入 seam 时须标注并限模块测试）；**模型句柄契约**：`addModel(data, spec)` 返回**实例内不透明模型句柄**（bounded model token，非原生 3Dmol model；不暴露原生属性），句柄提供受限模型域操作 `setStyle(sel,style)`、`addStyle(sel,style)`、`setClickable(sel,val,cb)`、`setHoverable(sel,val,cbIn,cbOut)`、`selectedAtoms(sel)`（纯数据原子，保留 `serial`/`index`/`elem`/`x`/`y`/`z` 字段；失败返回 null）；实例级同名方法作用于当前模型（单模型调用点等效）；`removeModel(handle?)` 移除当前/指定模型（覆盖 parse 清理）；`getModelCount()` 返回数量、`getModel(index)` 返回受限 token（多模型枚举/按序操作）；补齐调用点所需受限方法 `getView`（拷贝）、`center`、`zoom`（供 `framing.frame3DViewer`）、`addArrow`（与 shape 同规则转发，清除经 `removeAllShapes`）、`removeShape(handle)`（与 `addSphere`/`addCylinder` 返回的 shape 句柄配对）、`addLabel(text,opts,anchor?)` **返回实例内可移除 label 句柄**且 `removeLabel(handle)` 可移除（`anchor` 支持 `{serial}`，实例注入内部模型；业务不传原生 model），并保留既有风格/shape/camera/animation/frame/dispose 能力；**多模型（overlay）**：每个 `addModel` 的 token 独立映射内部原生模型；B 模型样式经其 token 设置（不影响 A）；对既有模型（如 A）的样式/高亮经 `getModel(index)` token 操作，不使用“当前模型”假定；**全量 token 化（评审 A1）**：模型、shape、label 一律为实例内不透明 token；拒绝来自另一实例的 token；对象被 `removeModel`/`removeShape`/`removeLabel`/清场/销毁后其 token 立即失效（再调用返回失败值）；**mutator 返回布尔值或受限实例，禁止透传原生链式返回值（如原生 `this`）**；`selectedAtoms` 返回字段白名单的**独立快照**（修改快照不影响内部模型）；点击/悬浮回调参数同样转换——原子=数据快照、viewer 参数=受限实例或 `undefined`（保持 `(atom, viewerSlot, event)` 位置不变，事件仍在第三位）、事件仅保留白名单字段，绝不传原生 viewer/model 或带方法的原生对象；场景/模型/任务代次变化后旧回调立即失效（不仅是 `dispose` 检查）；**模型注册表（评审 A2）**：实例自维护 token 顺序与有效性，`getModelCount()`/`getModel(index)` 从实例注册表读取，`removeModel`/`removeAllModels`/`clearScene`/`dispose` 同步更新注册表；**vendor `3Dmol-2.5.5.min.js` 无原生 `getModelCount`，禁止转发**；实例级样式作用域=当前模型、token 级样式=该模型，overlay A/B 样式不得因“当前模型”默认而互扰；**错误结果契约**：受限方法在不可用/已销毁/原生失败时返回显式失败值（null/false），实例提供 `lastError()`（最新失败 `{method,message}` 或 null；**清除规则显式化：仅同一方法其后再次调用且成功时清除该记录；`render`/`resize` 等渲染类成功不清除 `addModel`/`selectedAtoms` 等解析类失败；另提供 `clearError()` 显式清空**；新调用者必须显式处理失败值），替换现有 `forward()` 静默吞异常且返回成功语义的行为；不抛出、不假装成功。迁移场景清单以 A00 地图为准（主结构、能量预览、向导预览、NMR、振动、结构查看/编辑、reaction-builder 反应预览）；地图外死残留（scan-s2 字段）属 T25 清理，不迁移。扩展 `frontend/js/module_tests/scene_instance.test.js`（白名单含全部新增键、token/label 句柄无原生外泄、锚定、错误契约、seam 边界；保持 purity）。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T6 | Blocks: T8–T14
  References (executor has NO interview context - be exhaustive): `js/visualization/scene_instance.js:26-195,71-83（forward 吞异常）`；`js/visualization/mol3d_adapter.js:31-38,55-57`；`js/visualization/framing.js:19-33`；`js/vendor/3Dmol-2.5.5.min.js`（无原生 `getModelCount`）；调用点 `molecule-view.js:181（回调签名含 viewerInstance）,204-205,798,820,1063-1071`；`wizard.js:1518-1535,1738-1747,2053,3850`；`structure_viewer.js:1123-1133`；`vibration_viewer.js:851-870`；`energy.js:127-135`；`nmr.js:347,726`；`bootstrap.js:101-115`；`job_editor.js:1912-1914`；`module_tests/scene_instance.test.js`；`docs/reference/next-change/ACP_Parallel_Plans_Review_20261018.md:105-125（A1/A2）`。
  Acceptance criteria (agent-executable): `node frontend/js/module_tests/run_all.js scene_instance` 全绿且新断言覆盖上述方法（含 `lastError()` 错误契约与 `addModel` 无原生返回）；实例键白名单断言保留（扩展需显式登记）；purity 字符串检查（无 `document.`/`fetch(`/`EventSource`/`$3Dmol`）；**A1 边界替身验证**：原生方法返回 `this` 不得透传、返回原生 shape 必须转 token、回调传原生 viewer 的替身须被转换；修改原子快照不改变内部模型；跨实例/已移除 token 被拒绝；旧模型回调不能修改新任务状态；**A2 模型注册表**：循环 `addModel`/`removeModel`（含 overlay 添加/清除）后 `getModelCount()` 回到基线且注册表无残留；具浏览器环境时用实际 `3Dmol-2.5.5.min.js` 复验（不能只用实现了不存在原生 API 的 stub；不可用环境的替代与边界记入验收记录）。
  QA scenarios (name the exact tool + invocation): happy: `node frontend/js/module_tests/run_all.js scene_instance`；failure: `addModel` 返回原生 3Dmol model（可调用原生方法/属性）时测试必须失败——返回值必须是受限 token；label 句柄/token 外泄原生引用同理。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-7-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(visualization): bounded scene instance owns viewer and model internally
  Recommended task executor category: deep-low — 单模块设计，约束可从调用点读齐。

- [x] 8. 接入默认页并迁移主结构 viewer
  What to do / Must NOT do: ①`frontend/ACP_Workbench_v2.html` 在 `mol3d_adapter.js` 后装载 `js/visualization/scene_instance.js`，同步更新 `tests/test_frontend_integration.py` 顺序 pin 与 `test_frontend_sync.py` 相应 pin。②`js/workbench/features/molecule-view.js`：`initViewer` 改 `scene.create({container,options})` 且已存在则复用；全局 `viewer`（`shared-state.js:38`）语义改为受限实例（保留声明，注明只读投影）；`currentModel/currentModelAtoms` 改为实例内模型语义（`setClickable`/`selectedAtoms`/`setStyle`/`addStyle` 经实例）；`clearViewer` 保持原 removeAll* 行为与导出/风格/测量功能。③主结构全部调用点（133/155-174/181/204/743-746/798/820/1033/1063-1090，含徽标 label 句柄 1079/1086 与 parse 清理）走受限 API。④**消费者兼容表（评审 A3）**：主结构、结构查看、结构编辑、振动、framing、导出、测量、选择——T8 的提交边界包含共享该全局对象的**全部消费者所必需的调整**；T13/T14 仅做其余行为迁移与专项覆盖；禁止以暴露原生 viewer 维持中间态；单片断言只约束已迁移调用点（总量 `$3Dmol` 单驻点断言放 T15/T27）。禁止 `$3Dmol` 逃出 adapter；不改变相机/选择/导出行为；若消费者（structure_viewer/vibration_viewer/structure_editor）无法继续经同一实例工作，必须在同切片一并更新其解析路径——禁止提交会使默认页失效的中间态（§38.8）。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7 | Blocks: T13, T14, T15
  References (executor has NO interview context - be exhaustive): `frontend/ACP_Workbench_v2.html:1235-1250`；`js/workbench/features/molecule-view.js`（76-87,89-104,100,133,155-174,180-245,743-746,798-826,1033-1090）；`js/workbench/compat/shared-state.js:38-42`；`js/visualization/framing.js:67-106`；`tests/test_frontend_integration.py:16-40`；`tests/test_frontend_sync.py`（grep `mol3d|scene_instance|viewer` pin，按符号定位）。
  Acceptance criteria (agent-executable): 默认页加载 scene_instance；`grep -rn '\$3Dmol' frontend/js --include=*.js`（排除 vendor/module_tests）仅 adapter；`node frontend/js/module_tests/run_all.js` 全绿；前端四 pytest 全绿；全部非 vendor 脚本 `node --check`；**T8 单独检出即可完成主结构加载、编辑回推、overlay、振动、测量、PNG 与重复初始化，无需等待 T13/T14 合入才恢复功能（评审 A3）**。
  QA scenarios (name the exact tool + invocation): happy: node+pytest+语法检查；failure: 反复挂载/销毁后旧回调——用模块测试桩验证 dispose 后无 tick。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-8-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): wire bounded scene instance and migrate main structure viewer
  Recommended task executor category: visual-engineering — 前端三维场景迁移。

- [x] 9. 迁移能量预览 viewer
  What to do / Must NOT do: `js/workbench/features/energy.js` 的 `energyGraphState.viewer` 改为实例（创建 131、清理 127、`addModel` 134、`setStyle` 135、销毁 2878）；`energyGraphDestroyViewer` 走 `dispose()`（保持幂等）；保持 PNG 导出与 framing（154/254/2875）；renderer 不直接接触 viewer。不改能量图数据/身份/单位；energy viewer 仍仅保存候选。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7 | Blocks: T23, T24（energy.js 串行）
  References (executor has NO interview context - be exhaustive): `js/workbench/features/energy.js:120-160,254,2869-2885`；`js/visualization/energy_chart_renderer.js`（纯度约束）；`js/visualization/framing.js`；`module_tests/energy_renderer.test.js`。
  Acceptance criteria (agent-executable): `node frontend/js/module_tests/run_all.js` 全绿；`pytest tests/test_acp_energy_graph.py -q` 全绿；grep 断言 energy.js 无 raw viewer 直调（全部经实例）。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 缺 3Dmol 时降级不抛出（stub 构造）。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-9-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): migrate energy preview to bounded scene instance
  Recommended task executor category: visual-engineering — 三维/二维预览迁移。

- [x] 10. 迁移向导预览 viewer（在 T18 提取后执行）
  What to do / Must NOT do: 在 T18 之后的新文件结构上：`wizard.js`（或其拆分片）`initPreviewViewer/renderPreviewStructure3D`（1685-1748）改实例；`previewModel` 全部用法（1518-1535 标签锚定、2053/3850 `setClickable`、2050/2074/2109/2189/3651/3849 真值+结构键守卫）改实例 API（token 真值语义/selectedAtoms 等效/addLabel 内部锚定）；PES/scan 选点 shape 函数（含 `removeShape`，见 T7）经实例；`bootstrap.js:101-115` 与 `job_editor.js:1912-1914` 的用法保持（resize/render/frame 在实例上可用）；`scheduleViewerFraming` getter 返回实例。不改变选点/标签/相机行为；不引入第二 adapter。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7, T18 | Blocks: T15, T19
  References (executor has NO interview context - be exhaustive): `wizard.js:1518-1535,1685-1748,1801-1806,2049-2115,2134-2139,3850`；`bootstrap.js:101-115`；`job_editor.js:1912-1914`；`scene_instance.js`（v2）；`framing.js:19-33`。
  Acceptance criteria (agent-executable): `node run_all`（wizard_endpoints/wizard_candidate_api 等）全绿；前端四 pytest 全绿；缺 3Dmol 时预览空态保持；job_editor 编辑重算路径的预览/重挂载回归（1912-1914）通过。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: `previewModel` 真值守卫失效——结构键守卫测试。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-10-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): migrate wizard preview viewer to scene instance
  Recommended task executor category: visual-engineering — 三维预览与选点迁移。

- [x] 11. 迁移 reaction-builder 反应预览 viewer
  What to do / Must NOT do: `js/workbench/features/reaction-builder.js` 的 `reactionViewer`（537,700,710-715,853-856）改实例；核对并清理与 wizard 共享的 `previewViewer` 重复声明（2367，按实际作用域）。不动 `selectJob`/SSE/`loadNodes`（T22/T23 范围）；不共享跨 canvas viewer。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7 | Blocks: T22, T25（文件串行）
  References (executor has NO interview context - be exhaustive): `reaction-builder.js:301-324（hover shape 回调）,530-545,695-720,740-816（hover 标签/回调）,845-860,2360-2375`；`scene_instance.js`（v2）。
  Acceptance criteria (agent-executable): `node run_all` 全绿；前端四 pytest 全绿；grep 无 raw viewer 直调。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 隐藏/销毁后回调——dispose 测试。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-11-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): migrate reaction preview viewer to scene instance
  Recommended task executor category: visual-engineering — 三维场景迁移。

- [x] 12. 迁移 NMR viewer
  What to do / Must NOT do: `js/workbench/features/nmr.js` `nmrEnsureViewer`（726）与 `info.viewer` 使用点（347、358 销毁、823 framing、标签/箭头）改实例；per-candidate 实例清理走 `dispose`。不改 NMR 谱图/峰选择行为；不删无人值守导出路径。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7 | Blocks: T15
  References (executor has NO interview context - be exhaustive): `nmr.js:340-360,700-760,810-830（含 818-819 getModel 读原子 → 迁为 selectedAtoms 等效；失败返回 null 时兜底 `[]`）`（按符号核对）；`scene_instance.js`（v2）。
  Acceptance criteria (agent-executable): `node run_all` 全绿；前端四 pytest 全绿。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: candidate 切换后旧实例销毁——stub 计数断言。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-12-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): migrate NMR viewers to scene instance lifecycle
  Recommended task executor category: visual-engineering — 三维场景迁移。

- [x] 13. 迁移振动 viewer 动画/箭头生命周期
  What to do / Must NOT do: `js/vibration_viewer.js` 动画帧（851-870）与箭头（applyArrows）经实例；播放/暂停/隐藏改实例 `play/pausePlayback/setHidden`；`_getMainViewer` 解析实例；保留原生模式编号与位移向量显示约定。不在实例外启第二动画；不改振动科学身份；不重构箭头几何（专项覆盖；共享消费者兼容已属 T8 边界，T8 已完成路径只验证/扩展，评审 A3）。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7, T8 | Blocks: T15
  References (executor has NO interview context - be exhaustive): `vibration_viewer.js:198-230,440-470,840-1000`（按符号核对）；`scene_instance.js` 动画契约；`module_tests/viz_framing.test.js`。
  Acceptance criteria (agent-executable): `node run_all` 全绿；单 interval 断言（两实例独立）通过；箭头经实例可绘制（`applyArrows` 经实例返回 >0 且 UI 箭头计数一致）。
  QA scenarios (name the exact tool + invocation): happy: node 全套；failure: 隐藏时 tick 不停——测试必须失败。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-13-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): vibration playback on scene instance lifecycle
  Recommended task executor category: visual-engineering — 动画生命周期迁移。

- [x] 14. 迁移结构查看/编辑交互
  What to do / Must NOT do: `js/structure_viewer.js`（`_canvasViewer` 898-905、IRC loader 1123-1133、overlay 1438-1470（`_removeOverlayModels` 经 `getModelCount()`/`getModel(index)` token；模型 A 高亮经 `getModel(0)` token）、其余 addModel/setStyle/测量）与 `js/structure_editor.js`（1530-1537 推送）经实例；`molecule-view.js:803-855` 桥保持；测量叠加与编辑推送不回归。不改测量/编辑语义；不删历史 reader；不在 structure_viewer 内联网（overlay 等其余行为迁移与专项覆盖；共享消费者兼容已属 T8 边界，T8 已完成路径只验证/扩展、不重复迁移，评审 A3）。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T7, T8 | Blocks: T15
  References (executor has NO interview context - be exhaustive): `structure_viewer.js:890-915,1119-1135,1438-1470`；`structure_editor.js:1525-1545`；`molecule-view.js:803-855`。
  Acceptance criteria (agent-executable): `node run_all`+前端四 pytest 全绿；测量/编辑桥行为对照原页面。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 编辑推送后相机跳变——framing 测试。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-14-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): structure view and edit on restricted instance
  Recommended task executor category: visual-engineering — 结构视图交互迁移。

- [x] 15. 生命周期矩阵与泄漏验证
  What to do / Must NOT do: 对已迁移全部场景跑矩阵：反复挂载、A→B→A、隐藏/显示、暂停/停止、销毁+迟到回调、多实例相机/选择独立、缺 3Dmol 降级、PNG 导出；消费者兼容总表验证（T8 边界 + T13/T14 专项覆盖合并回归）；加 pin：`$3Dmol` 单驻点、`addModel(` 不得直用原生返回值、scan-s2 死 viewer 字段清除（若 T25 未做则在 T25 补）；更新接管表（tracked 基线文档副本）模块状态。不隐藏失败；不弱化断言。
  Closes: GAP-2
  Parallelization: Wave 1 | Blocked by: T8–T14 | Blocks: T27, T30
  References (executor has NO interview context - be exhaustive): `module_tests/scene_instance.test.js`（扩展）、`viz_framing.test.js`、`page_integration.browser.js:187-195`；`scan-s2.js:16-17,65-67`。
  Acceptance criteria (agent-executable): 新增/扩展 Node 测试覆盖矩阵项全绿；pin 字符串检查落地。
  QA scenarios (name the exact tool + invocation): happy: `node run_all`；failure: 注入迟到回调→无 tick 断言必须通过。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-15-acp-parallel-plan-1-frontend.md`
  Commit: Y | test(frontend): scene instance lifecycle matrix and leak pins
  Recommended task executor category: unspecified-high — 跨场景验证矩阵与 pin。

- [x] 16. 冻结四片边界与共享契约
  What to do / Must NOT do: 在 `wizard.js` 内标注四片边界（上传/解析 1113-1280；预览/校验 1281-1750+7132-7180；草稿/表单 380-1053+3890-4370+5014-7370；提交/编辑重算 7378-8304+job_editor 链）与每片状态 owner/调用入口/DOM hooks/跨片依赖；定义目标文件拆分方案（按完整依赖，先记录在证据，名称按仓库惯例）。**（评审 A4）产出逐符号映射**：每个状态变量、监听器、异步请求、清理函数指定唯一 owner；重叠区（如方法配置 5014-7370 与预览 7132-7180）按职责归属，不按行段重复抽取；每个提取片必须含 HTML 脚本装载顺序、全局函数兼容表、拆分前后请求/提交 payload 对照；保留 draft version、0/false/null、FormData 与“结果未知”查询策略；`job_editor` 编辑会话与新建表单共用规则但不共享可变草稿。不动代码；不建空骨架。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T6 | Blocks: T17
  References (executor has NO interview context - be exhaustive): `wizard.js` 上述区段；`js/client/wizard-endpoints.js:124-255`；`js/workbench/compat/api.js:8-23`；`job_editor.js:1534,1850,2066,2115,2130`。
  Acceptance criteria (agent-executable): 边界图含每片状态变量/入口函数/事件/DOM hooks 的唯一 owner；行号经 grep 复核；**无重复状态 owner 断言**（各片状态 owner 清单与 `task_session_controller`/`candidate_review_controller` 不重叠）；逐符号映射与 payload 对照表可供 T17–T20 直接执行；确定性登记 T23 是否修改 `reaction-builder.js` 及其实际前置（写入依赖或文件锁登记，评审 P1-5）；模态框重复打开/关闭不增加监听器、切任务或关闭向导后迟到解析/预览不覆写新草稿（评审 A4）。
  QA scenarios (name the exact tool + invocation): happy: 文档审查对照 grep 验证边界；failure: 边界交叉（状态双写）→先划清再开工。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-16-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: unspecified-high — 大文件结构审计。

- [x] 17. 提取上传/解析片
  What to do / Must NOT do: 按 T16 边界提取上传/解析（上传 FormData、文本 parse、结果/错误呈现），单一状态 owner 与调用入口；保持 `wizardEndpoints.uploadStructure/parseStructure` 契约（URL、`timeoutMs:0`、错误文本推导）；先迁职责再按依赖拆文件；旧入口保留同名薄委托（迁移期）。不改 URL/body/无超时例外；不绕过编辑拦截；不复制状态。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T16 | Blocks: T18
  References (executor has NO interview context - be exhaustive): `wizard.js:1113-1280`；`js/client/wizard-endpoints.js:124-155`；`module_tests/wizard_endpoints.test.js`。
  Acceptance criteria (agent-executable): `node run_all`（wizard_endpoints）全绿；前端四 pytest 全绿；FormData/错误文本行为等价。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 上传 422/网络错误显示路径保持。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-17-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): extract wizard upload/parse slice
  Recommended task executor category: unspecified-high — 多文件职责提取。

- [x] 18. 提取预览/校验片
  What to do / Must NOT do: 提取预览/校验（结构列表/选择/3D 预览重建/校验时机与入口），单一状态 owner；保留 `_refreshWizardInputForm`、列表渲染、警告/错误呈现；T10 后续在此结构上迁实例。不改校验规则/文案；不动提交分支；不重排 DOM。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T17 | Blocks: T10, T19
  References (executor has NO interview context - be exhaustive): `wizard.js:1281-1750,3856,7132-7180`；`wizard-endpoints.js:158`；`js/task_input_workspace.js:115,200`。
  Acceptance criteria (agent-executable): `node run_all`+前端四 pytest 全绿；预览/校验行为对照原页面。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: `jobFormValidationFailed`（7378）路径保持。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-18-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): extract wizard preview/validate slice
  Recommended task executor category: unspecified-high — 多文件职责提取。

- [x] 19. 提取草稿/表单片
  What to do / Must NOT do: 提取草稿（380-618）与表单（620-1053 + 配置卡 3890-4370 + 方法配置 5014-7370 共享部分），单一状态 owner；`batch_role_defaults.js` 消费保持；显式 0/false/null 与旧草稿 reader 保持；`openMethodConfig` 按内部组（公共字段/工作流特有面板/预览请求/监听生命周期）拆分；新建/编辑共用 defaults 与校验。不改字段默认值/方法 schema；不新建第二份默认值；不弱化草稿兼容。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T18, T10 | Blocks: T20
  References (executor has NO interview context - be exhaustive): `wizard.js:380-618,620-1053,3890-4370,5014-7370`；`js/workbench/contracts/batch_role_defaults.js`；`job_editor.js:33,472,637,676`；`tests/test_frontend_sync.py:9419`（batch defaults pin）。
  Acceptance criteria (agent-executable): `node run_all`+前端四 pytest 全绿（含 batch defaults pin）；0/false/null 与旧草稿恢复等价。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 显式 false 被吞→测试必须失败。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-19-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): extract wizard draft/form slice
  Recommended task executor category: deep-low — 大函数拆分需按依赖判断。

- [x] 20. 提取提交/编辑重算片
  What to do / Must NOT do: 提取提交（`handleModalSubmit` 7688、`submitJobModal` 7732、`submitJobBatch` 7383、各工作流分支）与编辑重算（job_editor 链接口），单一入口；保持：编辑 POST `/jobs` 拦截（`compat/api.js:14-18`）、409/422 status/body、超时不重发、结果未知呈现、PES/NMR/机理分支等价、`candidateApi` 走 transport。不改提交 payload；不加新字段；不把 202 当完成。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T19 | Blocks: T21
  References (executor has NO interview context - be exhaustive): `wizard.js:7378-8304`；`job_editor.js:1534,1850,2066,2115,2130`；`compat/api.js:8-23`；`module_tests/job_commands_candidate.test.js`、`wizard_candidate_api.test.js`。
  Acceptance criteria (agent-executable): `node run_all`+前端四 pytest 全绿；编辑重算不创建新任务（拦截回归）。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 编辑 POST 未被拦截→测试必须失败。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-20-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): extract wizard submit/edit-recalc slice
  Recommended task executor category: unspecified-high — 多分支提交提取。

- [x] 21. 共用 defaults/校验与保留行为跨片验证
  What to do / Must NOT do: 对四片做保留行为清单验证：新建/编辑共用 defaults/校验（batch defaults pin）、旧草稿 reader、显式 0/false/null、FormData、无超时例外、409/422、编辑 POST `/jobs` 拦截、15 active 工作流表单等价；只补差缺口，不重写。不放宽断言；不新增默认值来源。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T17–T20 | Blocks: T30
  References (executor has NO interview context - be exhaustive): `tests/test_frontend_sync.py:9130-9436`；Plan_1 A02 第二个 bullet（`:45`）；`batch_role_defaults.js`。
  Acceptance criteria (agent-executable): 清单逐项 PASS/FAIL；FAIL 项修复后重跑（不得以登记关闭；评审 A5）；无重复状态 owner 断言保持（新切片不引入与 task_session/candidate_review 重叠的 owner）。
  QA scenarios (name the exact tool + invocation): happy: 前端四 pytest + sync 全绿；failure: 任一项不等价→修复前不得关闭。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-21-acp-parallel-plan-1-frontend.md`
  Commit: Y | test(frontend): pin wizard shared defaults and compatibility behaviors
  Recommended task executor category: unspecified-high — 跨片行为验证。

- [x] 22. loadNodes/loadBackends 归位（Client facade + Workbench 用例）
  What to do / Must NOT do: 新增 Client 端点 facade（建议 `js/client/system-endpoints.js` → `ACPClient.systemEndpoints`：GET /nodes、/api/backends、/software/discovery，错误/超时沿用 transport 契约）与 Workbench 用例（建议 `js/workbench/controllers/system_resources_controller.js`：面板状态/渲染/重试）；`reaction-builder.js` 改薄委托（保留全局函数名供既有调用者），`bootstrap.js:225-226`、`compat/api.js:167-168`、`job-detail.js:889,1179` 调用者迁移；保留有调用者的历史机理只读逻辑与节点匹配职责。不改 URL/字段；不删旧函数名（迁移期委托）；不把行为改掉；不在 controller 内直接 fetch。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: T11 | Blocks: T25
  References (executor has NO interview context - be exhaustive): `reaction-builder.js:1319-1382,1848-1933`；`bootstrap.js:225-226`；`compat/api.js:167-168`；`job-detail.js:889,1179`；`js/client/transport.js`；`tests/test_frontend_integration.py:16-40`（脚本序 pin 需更新）。
  Acceptance criteria (agent-executable): `node run_all`（含新增测试）全绿；前端四 pytest 全绿；grep 证明状态 owner 唯一（无第二份缓存/请求）。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: `/nodes` 503 时面板错误呈现保持。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-22-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): client facade and controller for nodes/backends
  Recommended task executor category: unspecified-high — 多文件职责搬迁。

- [x] 23. taskSession 核对与补缺（文件/日志、候选、结果）
  What to do / Must NOT do: 逐项核对：文件/日志（`files-logs.js`：filesSessionUnchanged、logPollToken、IRC tail 迟到丢弃）、候选（`candidate_review_controller.js` revision/dirty/锁定/切换重置）、结果加载（energy/scan-s2 候选与结果路径）在 taskSession 下的身份/迟到/取消规则；只补缺口；验证“候选保存不隐式创建任务”。**（评审 A4/P1-5）在 T16 规格冻结时确定性判定本任务是否修改 `reaction-builder.js`；若修改，把实际前置（T11/T22）写入依赖或文件锁登记，并纳入文件串行链（T11→T22→T23→T25）。** 不重写第二份 controller；不改 revision 语义；不动科学身份。
  Closes: GAP-3, GAP-5
  Parallelization: Wave 1 | Blocked by: T9, T11, T22 | Blocks: T24 | （P1-5：T16 已判定 2026-10-18：T23 **涉及** reaction-builder.js（任务会话钩子 reaction-builder.js:2193-2348、候选 reset :2234）；实际前置 T11、T22 已登记；文件串行链 T11→T22→T23→T25）
  References (executor has NO interview context - be exhaustive): `js/workbench/controllers/task_session_controller.js:24-154`；`reaction-builder.js:2193-2348`；`files-logs.js:14-16`；`candidate_review_controller.js:385-410`；`energy.js` 候选区；`scan-s2.js` 候选状态。
  Acceptance criteria (agent-executable): 核对清单每项 PASS/FAIL/补缺记录；`node run_all`（task_session/files_session/candidate）全绿。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: A→B→A 候选泄漏→修复+测试。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-23-acp-parallel-plan-1-frontend.md`
  Commit: Y | fix(frontend): task session identity gap fill for files, candidates, results
  Recommended task executor category: unspecified-high — 跨模块规则核对。

- [x] 24. 能量 22 窄委托与二维视图复核（renderer 纯度）
  What to do / Must NOT do: 复核 `energy_chart_renderer.js` 与 `energy.js` 的 22 窄委托：renderer 不联网、不读全局任务、不做全量科学分析；必要选择由 controller 注入；其他在用二维视图（收敛/采样/IRC/NMR 谱图等）同规则复核；发现违规登记或最小修复（不改科学数值）。不引入 ECharts；不重算权威抽样/判据；不改渲染输出。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T23 | Blocks: T25
  References (executor has NO interview context - be exhaustive): `energy.js:32,763-830,2064-2600`；`js/visualization/energy_chart_renderer.js`；`module_tests/energy_renderer.test.js`；`js/workbench/features/job-detail.js`（其他二维视图按 grep 定位）。
  Acceptance criteria (agent-executable): 复核清单全项有结论；`node run_all`+前端四 pytest 全绿。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: renderer 读 selectedJobId→修复或登记。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-24-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): renderer purity review fixes
  Recommended task executor category: unspecified-high — 多视图纯度审计。

- [x] 25. FE-04/05 关闭（调用闭包验证）
  What to do / Must NOT do: FE-04：scan-s2 残留（旧上传/步进/提交/结果闭包）与 S2 专属样式按调用闭包处置；保留现用选点/几何辅助/候选审查状态（wizard/energy 引用）与历史结果查看；清理死 viewer 字段。FE-05：退役机理写交互/空钩子（reaction-builder/energy）验证无调用者后与按钮/监听/状态一并移除；保留历史机理只读与旧草稿只读进入状态；不重启用退役接口（410 保持）。不整删 scan-s2/reaction-builder；不删有调用者函数；不批量删 s2scan-* 样式/翻译；先替代或确认无用再清理。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T22, T24 | Blocks: T26, T29
  References (executor has NO interview context - be exhaustive): `docs/reference/next-change/ACP_Frontend_Redundancy_Audit_20261016.md:44-62`；`reaction-builder.js:2356,2435,2689,2712`（按符号核对）；`energy.js`（空钩子按符号定位）；`scan-s2.js` 全文件（现 116 行）；`wizard.js:1923-1925`（s2scanDist/Angle/Dihedral 调用；按符号核对）。注意：FE-04 首片已在当前代码执行（HTML 无 `#s2scan-modal`；scan-s2 已缩减），范围以现状重新推导。
  Acceptance criteria (agent-executable): 调用闭包清单（每删除项：零调用者 grep 面=`frontend/**`+HTML+`frontend/js/module_tests/**`+`tests/**`+`/legacy/` 加载 js，删除前记录输出；已替代项注明替代点）；`node run_all`+前端四 pytest 全绿；历史机理报告/旧草稿恢复等价。
  QA scenarios (name the exact tool + invocation): happy: node+pytest；failure: 删除项仍有调用者→恢复该段。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-25-acp-parallel-plan-1-frontend.md`
  Commit: Y | refactor(frontend): close FE-04/FE-05 call-closure cleanups
  Recommended task executor category: deep-low — 闭包推理后处置。

- [x] 26. FE-11 CSS 候选归并
  What to do / Must NOT do: 对 6 个 workbench CSS（4590 行）的候选同声明块，确认媒体条件/特异性/顺序后合并共同声明、保留特有覆盖；先清 FE-04 已证不可达的旧弹窗样式；不改 class/优先级。不批量重排；不为复用改选择器优先级；不删在用样式。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T25 | Blocks: T30
  References (executor has NO interview context - be exhaustive): `ACP_Frontend_Redundancy_Audit_20261016.md:98-102`；`frontend/css/workbench/*.css`（6 文件）；`ACP_Workbench_v2.html:8-17`。
  Acceptance criteria (agent-executable): CSS 比较实际 viewport/主题/语言下的 computed style 与截图差异（选择器文本相同不算等价，评审 A6）；`pytest tests/test_frontend_sync.py -q` 全绿；浏览器不可用时按全局 NOT_VERIFIED 纪律记录，该合并的层叠等价性结论保持开放（不以选择器文本或 pytest 通过替代 computed style/截图）。
  QA scenarios (name the exact tool + invocation): happy: 快照 diff+pytest；failure: 布局回归→回退该组。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-26-acp-parallel-plan-1-frontend.md`
  Commit: Y | style(frontend): consolidate safe CSS duplicates
  Recommended task executor category: quick — 机械归并+快照校验。

- [x] 27. FE-09 创建入口唯一性验证
  What to do / Must NOT do: 验证原 6 个创建点（wizard/NMR/reaction-builder/scan-s2）全部经 scene 实例/单一 adapter；无第二 adapter；scan-s2 死点已随 T25 清理或注明保留理由。不新建 adapter。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T15 | Blocks: T30
  References (executor has NO interview context - be exhaustive): `ACP_Frontend_Redundancy_Audit_20261016.md:85-88`；`grep -rn '\$3Dmol|createViewer' frontend/js`。
  Acceptance criteria (agent-executable): grep 断言 + `node run_all` 全绿。
  QA scenarios (name the exact tool + invocation): happy: grep+node；failure: 发现第二创建路径→登记并修复。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-27-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — grep 断言与核对。

- [x] 28. FE-10 黄金样例保持与差异说明
  What to do / Must NOT do: 保持二面角黄金样例（`module_tests/fe10_dihedral_golden.test.js`）不变；记录 scan-s2 与 GeometryStore 差异及科学约定待 C02/C07 核定的状态；不做统一。不合并两份测量实现；不改数值。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T6 | Blocks: T29
  References (executor has NO interview context - be exhaustive): `ACP_Frontend_Redundancy_Audit_20261016.md:91-96`；`fe10_dihedral_golden.test.js`；`scan-s2.js:102-116`（现行 s2scanDihedral；按符号定位）；`geometry_store.js:326`。
  Acceptance criteria (agent-executable): 黄金样例测试全绿且该测试文件 git diff 为空。
  QA scenarios (name the exact tool + invocation): happy: `node frontend/js/module_tests/run_all.js fe10`；failure: 数值变化→立即回退。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-28-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 保护性核查。

- [x] 29. 页面测试拆分（等效断言）
  What to do / Must NOT do: 将过大的页面测试文件（`test_frontend_sync.py` 等）按 owner 拆分为多文件，保留等效断言、fixture 与收集范围；异常区分缺库/销毁/网络失败；不静默成功/填零。**（评审 A6）拆分以「旧 node ID→新 node ID 映射」及断言/fixture 对照证明等效（不只比较用例数量）；等待所有会修改同一批 guard 的切片（T21/T25/T26/T28）完成后再动；拆分后的新文件逐文件登记 owner（X1）。****（评审 P1-1）同步产出/更新权威的前端测试选择清单（或 marker），T30、CI 与后续回归统一引用**；记录拆分前完整 node ID 集合与拆分后映射，证明最终选择命令**实际收集**这些用例（不仅全仓 collect-only 可找到）。不删断言换绿；不改收集范围掩盖覆盖；不动科学黄金值。
  Closes: GAP-5
  Parallelization: Wave 1 | Blocked by: T21, T25, T26, T28 | Blocks: T30
  References (executor has NO interview context - be exhaustive): `tests/AGENTS.md`；`tests/test_frontend_sync.py`（>11k 行）；`tests/test_frontend_integration.py`。
  Acceptance criteria (agent-executable): 旧→新 node ID 映射表 + 断言/fixture 逐项对照；收集范围等价；全绿；**负例注入验证（评审 P1-1）：在临时副本（不提交）于新拆分测试中注入必然失败断言，运行 T30 的真实选择命令必须失败，移除负例后恢复**；权威测试选择清单已产出且 T30 引用。
  QA scenarios (name the exact tool + invocation): happy: `pytest --collect-only -q` 计数 before/after + 全套运行；failure: 用例数下降→补回。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-29-acp-parallel-plan-1-frontend.md`
  Commit: Y | test(frontend): split oversized page tests preserving equivalent assertions
  Recommended task executor category: unspecified-high — 大测试文件拆分。

- [x] 30. 全量回归（Node + 前端 pytest + 扩展集）
  What to do / Must NOT do: 运行 Node 全套、**T29 维护的权威前端测试选择清单（拆分前为四个现有前端测试文件）**、扩展回归（energy/structure/job-edit/scan，按职责列出）、适用编辑/候选/结果 API 回归；分别记录基线（T3）、改动后结果与跳过原因；全部非 vendor 脚本 `node --check`。**（评审 A5）已执行失败只能是 FAIL：修复后重跑；缺依赖未执行才是 NOT_VERIFIED；不得以“修复或 NOT_VERIFIED 说明”兜底互相替代；已有基线失败记录归属并证明本次未扩大影响；所修改路径的回归不得以基线债务豁免。** 不跳过当通过；不掩盖失败。
  Closes: GAP-6
  Parallelization: Wave 2 | Blocked by: T15, T21, T25, T26, T27, T28, T29 | Blocks: T31–T33
  References (executor has NO interview context - be exhaustive): 本计划“## Verification strategy”；T3 基线记录；`tests/test_frontend_interaction.py:20-30`。
  Acceptance criteria (agent-executable): 三套命令原始输出；FAIL 项均修复后重跑（不得降级为 NOT_VERIFIED）；NOT_VERIFIED 仅限缺依赖未执行且注明依赖与尝试记录；前端测试选择命令实际收集新旧用例（T29 负例注入证明，评审 P1-1）。
  QA scenarios (name the exact tool + invocation): happy: 命令执行；failure: 失败→登记为 FAIL 并修复后重跑（不得降级为 NOT_VERIFIED）。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-30-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 按既定入口运行与记录。

- [x] 31. 真实浏览器 + S0 联合验收（隔离数据）
  What to do / Must NOT do: 依 T1 环境结论执行：隔离数据+本机真实 S0 默认入口跑列表→详情→文件/日志→结果；覆盖分页/筛选、A→B→A、rerun、编辑不新建、候选冲突、缺库、多实例、导出、隐藏/重挂载、中英、断网恢复；Playwright 不可用时 NOT_VERIFIED+尝试记录（**A05 退出门保持开放，不条件关闭**，评审 A5）。不对生产任务运行创建/取消；不用 mock 结果冒充。
  Closes: GAP-6
  Parallelization: Wave 2 | Blocked by: T30 | Blocks: T34
  References (executor has NO interview context - be exhaustive): `frontend/js/module_tests/page_integration.browser.js`；`tests/test_frontend_interaction.py:55-71`；`docs/reference/current/ACP_Frontend_Integration_20261016.md:58-71`（场景样例）。
  Acceptance criteria (agent-executable): Plan_1 A05 bullet-2 场景清单逐项映射到 Playwright 步骤或 NOT_VERIFIED 行；浏览器未测时交付代码与模块证据但 **A05 真实浏览器退出门保持开放（OPEN）**；含截图/日志证据。
  QA scenarios (name the exact tool + invocation): happy: 浏览器执行；failure: 场景失败→登记为 FAIL 并修复后重跑（A05 退出门保持开放）。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-31-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: unspecified-high — 浏览器联合验收。

- [x] 32. 故障注入与真实响应标记 + 远端/生产分列
  What to do / Must NOT do: 对真实响应与故障注入（503、慢响应、断网、409/422）分别标记；真实科学/LSF/SSH/SFTP 与生产另记 PASS/FAIL/NOT_VERIFIED（本轮预期 NOT_VERIFIED，注明未涉及）。不把模拟成功替代真实。
  Closes: GAP-6
  Parallelization: Wave 2 | Blocked by: T30 | Blocks: T34
  References (executor has NO interview context - be exhaustive): Plan_1 A05 清单（`:78-88`）；主方案 §42.5 验证范围。
  Acceptance criteria (agent-executable): 分列表存在；每行有依据链接到证据。
  QA scenarios (name the exact tool + invocation): happy: 表格审计；failure: 模拟写成真实→改正。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-32-acp-parallel-plan-1-frontend.md`
  Commit: N | —
  Recommended task executor category: quick — 证据归类与标记。

- [x] 33. 静态发布包与回退步骤
  What to do / Must NOT do: 交付匹配 HTML/JS/CSS 资源集（复用 T4 摘要、更新为改动后版本）+装载顺序+摘要；验证冷/暖缓存、普通刷新、旧标签页窗口（说明 no-cache 不保证原子替换）；形成一致切入口与回退步骤（恢复资源集；不改 DB/attempt/工件；不重发命令）。**（评审 A6/X6）**资源清单交 B-T71（集成发布）验证“打开的旧页面+资源缓存+新服务”组合；静态资源缺失若影响已声明的 Web 发行，不得仅登记后关闭；本计划只做发布准备与演练，实际部署由集成发布任务在终验通过后执行。发布包与回退说明的接收方=部署/运维 owner；生产发布与 systemd 重启不在本计划（记录交接）。不加 manifest API/构建步骤；不删除旧资源（兼容窗口内）。
  Closes: GAP-6
  Parallelization: Wave 2 | Blocked by: T30, T4 | Blocks: T34
  References (executor has NO interview context - be exhaustive): 主方案 §38.9；§38.6 路径约定；`tests/test_frontend_integration.py:53-87`；T4 资源清单。
  Acceptance criteria (agent-executable): 资源清单与页面引用一致；回退步骤本地演练（目录恢复模拟）。
  QA scenarios (name the exact tool + invocation): happy: 资源集校验+冷/热加载；failure: 缺失资源→修复。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-33-acp-parallel-plan-1-frontend.md`
  Commit: Y | docs(frontend): release resource set and rollback steps
  Recommended task executor category: writing — 发布说明与演练记录。

- [x] 34. 五维状态分列与收尾关闭
  What to do / Must NOT do: 更新 `docs/reference/current/ACP_Frontend_Plan1_Acceptance_<YYYYMMDD>.md`：**五维分列“实现完成 / 模块通过 / 浏览器通过 / 服务接入 / 真实科学·远程·生产状态”**（评审 A5）；删除被替代实现，关闭接管表（tracked）；勾选 Plan_1 清单条目；记录 A04/A06 交接契约与门状态；记录 §43/“42.7” 校正补丁的已交付事实；**记录四组合验收责任（A 维护测试并提供 S0 证据；B 对选定集成提交执行黑盒测试并产出（其 T75），T69 消费其结果收口；C 在同一集成候选判科学等价；组合以 F0+S0 / F1-base+S0 / F0+S1 / F1-integrated+S1 区分，避免混用 S0 证据；评审 X3/P1-4/P1-2）与证据绑定（提交/契约版本/fixture 摘要）**；A05 未验证保持开放、不标整路完成（F4 同）；覆盖映射终版：Plan_1 A00–A03/A05 全部 checkbox 计数=23 且逐条对应 todo（A00×4、A01×5、A02×4、A03×5、A05×5）。不把未测当通过；不把前端完成写成整端完成。
  Closes: GAP-6, GAP-7
  Parallelization: Wave 2 | Blocked by: T31–T33 | Blocks: F1–F4
  References (executor has NO interview context - be exhaustive): Plan_1 A05 退出门（`:88`）；主方案 §42.5；`docs/reference/next-change/ACP_Parallel_Plans_Review_20261018.md`（A5/A7/X3）；T6 基线文档。
  Acceptance criteria (agent-executable): 文档含五维分列、接管表关闭状态、23 条覆盖映射、联合验收责任与（如有）开放项；B-T75 可引用已产出实际前端代码与消费测试的条件任务编号；若本次未启用 A04，记录明确「未接入」且不关闭相关产品/UI 目标（评审 P1-2）；Plan_1 勾选更新。
  QA scenarios (name the exact tool + invocation): happy: 文档审计（grep 五维小节）；failure: 缺列→补齐。Evidence `.omo/evidence/acp-parallel-plan-1-frontend/task-34-acp-parallel-plan-1-frontend.md`
  Commit: Y | docs(frontend): plan1 acceptance record and takeover closure
  Recommended task executor category: writing — 验收记录与关闭。

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Ideal-state fidelity

## Commit strategy
- 从冻结基线 `f410ac9d` 起专用分支/worktree（建议 `feat/frontend-plan1-closure`；若用 `$ulw-execute`，`--worktree`/`--make-pr` 视为等价手段）。绝不在含混工作区开工；不 reset/丢弃既有改动。
- 按切片小步提交：纯搬运与行为激活分开（§40.3）；同一符号只迁移一次；单实现切换（撤旧 owner）与 pin 更新随所属切片同提交。
- 提交只含该切片文件：`frontend/**` + 对应测试/pin + 对应记录文档；**S0 文件一律不提交**（`src/**`、配置、pyproject、部署）。
- 提交信息沿用仓库 conventional commits，scope 用 `frontend`/`visualization`：见各 todo 的 `Commit:` 行（T1–T6 中仅 T6 提交 docs；T7 起按切片提交）。
- 证据文件（`.omo/evidence/**`）与草稿/计划（`.omo/**`）属本地工件，不纳入提交。
- 可选 PR 交付由执行时用户选项决定（`--make-pr`/`--ship`）；无用户指令不推送远端。
- 回退粒度 = 单切片提交回退 + 资源集恢复；回退不修改 DB/attempt/工件、不重发命令（§38.9）。

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | T1–T34（全清单关闭） | T30 全量回归三套命令 + T34 五维状态分列审计 | `.omo/evidence/acp-parallel-plan-1-frontend/task-30-acp-parallel-plan-1-frontend.md`、`task-34-…md` |
| IS-2 | T8–T14（逐场景迁移）+ T15 | T31 隔离数据真实 S0 走查对照原页面（列表→详情→文件/日志→结果） | `task-31-…md` |
| IS-3 | T7–T15 | T15 生命周期矩阵 Node 测试（复挂/AB A/隐藏/销毁无 tick）+ T27 grep 单驻点断言 | `task-15-…md`、`task-27-…md` |
| IS-4 | T16–T21 | T21 跨片保留行为清单（defaults/0-false-null/FormData/409-422/编辑拦截）全 PASS | `task-21-…md` |
| IS-5 | T22、T23 | T22 owner 唯一 grep + `/nodes` 503 错误保持；T23 A→B→A 候选泄漏失败测试 | `task-22-…md`、`task-23-…md` |
| IS-6 | T1–T6 | T6 基线文档小节完整/owner 无遗漏 + T3 计数（时点/范围）+ T4 资源摘要 | `task-6-…md`、`task-3-…md`、`task-4-…md` |
| IS-7 | T29–T34 | T30 三套命令原始输出 + T31 浏览器矩阵 + T32 分列 + T33 发布包演练 | `task-30-…md`、`task-31-…md`、`task-33-…md` |
| IS-8 | T6、T34 | T34 A04/A06 交接契约与门状态文档审计 | `task-34-…md` |

补充判据：
- IS-2 的“无可感差异”以“sync/contract 套件+装载顺序 pin 全绿 + 逐场景行为清单对照”为操作性等价判据；浏览器可用时补截图对照，不可用记 NOT_VERIFIED。
- IS-7 的 A05 浏览器退出门：未测时保持开放（不条件关闭）；已执行失败只能是 FAIL、缺依赖未执行才是 NOT_VERIFIED，两者不得互替；F4 不得把 A05 未验证标为整路无条件完成；验收记录按实现/模块/浏览器/服务接入/真实科学五维分列（评审 A5）。
- GAP 覆盖：GAP-1→全清单；GAP-2→T7–T15；GAP-3→T16–T23；GAP-4→T1–T6；GAP-5→T24–T29；GAP-6→T30–T34（A05 浏览器退出门开放直至 PASS）；GAP-7→T6、T34 + 「Gated conditional tasks」。

Plan_1 checkbox 覆盖映射（23 条计数：A00×4、A01×5、A02×4、A03×5、A05×5）：

| Plan_1 条目 | 覆盖 todo |
| --- | --- |
| A00-1 地图建立 | T2 |
| A00-2 §43/42.7 校正 | T3、T6 |
| A00-3 H/R1–R4 复验 + F0/S0 固定 | T1、T3、T4 |
| A00-4 请求/并发/占用测量 | T5 |
| A01-1 scene_instance 审计扩展 | T7 |
| A01-2 opts.viewer/addModel 边界 | T7 |
| A01-3 逐场景迁移 | T8–T14 |
| A01-4 身份/framing/多实例/PNG/降级 | T8–T15 |
| A01-5 生命周期验证 | T15 |
| A02-1 四片提取 | T16–T20 |
| A02-2 defaults/校验/保留行为 | T19、T21 |
| A02-3 loadNodes/loadBackends 归位 | T22 |
| A02-4 taskSession 核对 | T23 |
| A03-1 能量 22 委托复核 | T24 |
| A03-2 FE-04/05/11 闭包 | T25、T26 |
| A03-3 FE-09 | T27 |
| A03-4 FE-10 | T28 |
| A03-5 测试拆分 | T29 |
| A05-1 Node/pytest 回归 | T30 |
| A05-2 走查矩阵 | T31 |
| A05-3 故障/真实标记 | T32 |
| A05-4 发布包 | T33 |
| A05-5 状态分列/收尾关闭 | T34 |
