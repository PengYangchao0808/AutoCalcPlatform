# acp2-plan-c-rph-workbench - Work Plan

## TL;DR (For humans)
<!-- Filled after the detailed plan was written, summarizing the REAL plan. -->

**Who this is for and what changes for them:** 面向执行 Agent C 的 worker、计划 C 负责人、Agent B 集成者、Workbench 科研用户与 RPH 用户。今天这条轨道只有 36 条任务单和三份上位文档，文件落点、先后关系、证据格式和"何时只能记未验证"都要执行者自己推导；完成后，执行者拿到逐条可直接开工的工单，评审者拿到可按门禁机械验收的台账，终端用户拿到"不启动 Agent 也能做手工研究"和"通过真实 DSH 跑通科研闭环"两条可运行路径。

**What you'll get:** 一份 C00–C35 全覆盖的执行计划（46 条实施工单 + 4 条终验）；一个独立可构建的 RPH Node workspace 与体验验收 Harness；Workbench 的科学问题/证据/审批/Schema 表单入口；真实 MCP/DSH/模型/Local/LSF 的实测证据；三份操作手册与一份阶段性发布建议。

**Why this approach:** 先冻结基线与环境，再让 Harness 先于一切场景命令存在（否则"跑过了"无法证明）；fixture 车道永不等待 A/B，live 车道按 H 门逐个激活，未激活行留 `NOT_VERIFIED` 而不阻塞其余工作；所有 C 侧产物只走自己的写域，跨域一律用带正负样例的 request 交 owner。

**What it will NOT do:** 不改 `src/cccp/**`、`src/acp_services/**`、`src/acp/**`、根 `pyproject.toml`、CI 与共享 manifest；不建第二任务数据库/Controller/执行链；不 fork DSH 或重写 MCP 传输；不把 `NOT_VERIFIED` 改写成 `PASS`，不把模型结论当成科学验证器结论。

**Effort:** XL
<!-- Effort is exactly ONE band, never hours/days. Quick = single edit, minutes of agent work; Short = one focused change, a few files; Medium = multi-file feature in one session; Large = several waves, one long session; XL = multi-session or architectural work. A written duration is rewritten to a band. -->
**Risk:** High - 36 项中过半依赖 A/B 尚未交付的真实入口、模型凭据与 LSF 环境；真实安装与真实计算会消耗外网、磁盘和机时。
**Decisions to sanity-check:** ① 执行落在独立 worktree `acp2/c-experience`，基线取 B00 的 `final_debug_candidate`（完整 SHA 或可重建 patch bundle；`096cdff…` 仅历史参照），不动当前分支；② 真实模型/Local/LSF/DA 本轮授权直接执行，但每个 live 场景冻结硬上限，超限记 `FAIL` 并停；③ Node/pnpm/DSH/Chromium 装在仓库外 `$ACP2_TOOLS`（B manifest 优先，否则 `/ACPServer/acp2-tools`）；④ 不新增根 `package.json`，`rph/` 是私有 pnpm workspace；⑤ P3–P5 与 P0–P2 同粒度到文件与命令，但不虚构 A/B 尚未定义的字段。

Your next move: 本计划已按 `docs/reference/next-change/ACP_2.0_Parallel_Plans_Review_20261021.md` 的 C 轨相关建议修订（基线改取 B00、统一回执协议、解除 C35↔B26 环、收入/预算前置拦截、DA 与收口状态分离、证据根由 B 分配等）。上一轮双通道评审的通过绑定的是旧摘要 `3da1bcb2…`，本次修订后需对新摘要重跑评审；通过后由你启动执行会话（如 `$ulw-execute acp2-plan-c-rph-workbench`）。完整执行细节见下文。

---

> TL;DR (machine): XL · 风险 High · 46 实施 todo + 4 终验：T-01 基线/worktree → T-02/T-03 消费契约 → T-04 Node/DSH + T-05 rph workspace → T-06/T-07 Harness 先行 → T-08 Chromium + T-09/T-10 Bridge → T-11–T-13 真实 MCP/DSH/模型 → T-14 H2 回执 → T-15–T-17 Client/手工入口 → T-18–T-22 真实 Local/LSF/三入口/恢复 → T-23–T-36 P2 研究闭环与 DA → T-37–T-46 P3–P5 插件/跨域/发行。

## Scope
### Affected user and ideal state
<!-- From the draft ledger: who this touches and how they use the result, then one IS row per property with its reason, then one GAP row per difference with its reason. Every todo below closes a GAP row; ## Success criteria proves every IS row. -->
**Affected user:** **U1 Agent C 执行者（下游 worker）**：今天只有 36 条任务单 + 三份上位文档，文件落点/顺序/证据格式靠自推；之后拿到逐条可开工的工单。**U2 计划 C 负责人/评审者**：今天只能按章节人工比对；之后按波浪、依赖矩阵、H0–H7 门禁与三态台账机械验收。**U3 Agent B 集成者**：今天收不到结构化 C 侧请求；之后收到 `acp2-contracts/experience/requests/*.md` 与可审查的 RPH 包/静态资产 manifest。**U4 Workbench 科研用户（不启动 Agent）**：今天只有任务向导与结果页；之后能完成"问题→预检→提交→证据/限制查看"。**U5 RPH/Agent 用户**：今天没有 DSH 工具桥；之后通过真实 DSH 完成发现→审批→提交→状态→工件→证据。**U6 维护者/审计者**：今天无法区分"写了"与"验过"；之后有可复跑的三态证据台账。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | C00–C35 每一项都映射到至少一个 `- [ ] N.` todo，含精确文件路径、可执行验收命令、失败断言、证据路径、提交信息与执行类别；执行者无需再提问 | 计划的价值在于消除执行期判断；任务单本身不含落点与证据模板 |
| IS-2 | 写入边界不被突破：只写 `frontend/**`、新增 `rph/**`、`tests/test_acp2_experience_*.py`、`scripts/acceptance/acp2_experience*.py`、`docs/reference/next-change/acp2-contracts/experience/**`、`docs/reference/next-change/ACP_2.0_RPH_*.md`/`ACP_2.0_Workbench_*.md`，以及 B00 分配的绝对 `$ACP2_EVIDENCE/C/**`；`src/**`、根 `pyproject.toml`、CI、共享 manifest 只以 request 交 B | 总索引 §2.2 单写者约定 + §6.3 共享证据根；越权会产生三方冲突与第二实现 |
| IS-3 | `acp2_experience.py` 先于场景命令交付，退出码固定 `0/1/2/3`；`--help/--dry-run` 自身成功但绝不写 PASS；fixture 与 live 分列，live 检出 mock transport/FakeBackend/假模型必须 FAIL | 计划 C §3.2；上一轮的教训是"能跑"被当成"已验证" |
| IS-4 | H2 有真实 stdio MCP 握手 + DSH 官方插件工具发现 + 一次真实模型工具调用；H3 有同一 task/attempt/digest 的 CLI/MCP/Workbench 对照；H4 有 DA 全链 `supported/refuted/inconclusive` 三态 | 总方案 §11.1 把本服务器实测定为交付门 |
| IS-5 | 无第二权威：RPH 不持任务/attempt DB、不写 job status、不直接跑 QC/SSH；MCP 传输由 DSH 官方 client 负责；`ResearchRecord` 与 `EvidenceReport` 分存 | 总方案不变量 2 与计划 C §1.3；复制状态机会产生不可对账的分叉 |
| IS-6 | 关闭 Agent UI 与模型配置时，Workbench 仍可完成手工研究与观察同一任务 | 总方案验收门 1 "Workbench 无 Agent 也可使用" |
| IS-7 | 每个场景有独立证据目录，含脱敏 argv/cwd/退出码/起止时间/真实引用/断言/工件摘要；凭据不入档；必需 live 行非 PASS 时阶段门保持开放 | 计划 C §3.3 与手册 §8；否则无法机械验收 |
| IS-8 | 既有兼容面与铁律不被破坏：15 active workflows、既有 Node 模块测试、`resource-store` 唯一写入口、frames 契约、energy viewer 禁提交、生产服务与数据目录不触碰 | 根 AGENTS ANTI #20/#21/#22；总方案要求保留旧路径 |

| Row | Statement | Reason | Closed by |
| --- | --- | --- | --- |
| GAP-1 | 36 条任务没有 todo 级拆分（无波浪/依赖/提交/执行类别/证据模板），执行者需自行决策 | IS-1 | 全部 todo + `## Execution strategy` |
| GAP-2 | 仓库当前不存在 `rph/`、`acp2-contracts/`、`scripts/acceptance/acp2_experience.py`、`tests/test_acp2_experience_*`；且全仓无 `package.json`（首次引入 Node workspace） | IS-2/IS-3 | T-02, T-03, T-05, T-06 |
| GAP-3 | Client 只有 task-view/job-detail/event-stream/transport/store；无 science/research 查询；SSE `after_seq` 明确不是持久重放 | IS-4/IS-5 | T-15, T-16 |
| GAP-4 | 真实 MCP 默认入口、DSH、模型、Chromium、Node 22.x、LSF 全部未验证（debug 报告 C 轨 `NOT_VERIFIED`，G4 OPEN） | IS-4 | T-04, T-08, T-11, T-12, T-13, T-18, T-19, T-20, T-21, T-22 |
| GAP-5 | Workbench 无科学问题/证据/审批/Schema 表单 Feature；`reaction-builder.js` 是 2504 行全局函数式旧模块 | IS-6 | T-17, T-23, T-27, T-31, T-38 |
| GAP-6 | 无 C 侧证据台账与 H0–H7 关闭矩阵；C 与 A/B 的交接只有散文描述，无文件模板与编号规则 | IS-7/IS-2 | T-01, T-03, T-14, T-36, T-46 |
| GAP-7 | 真实研究循环、研究记录、审批/预算反馈、会话恢复、故障提示均无实现 | IS-4/IS-5 | T-25, T-26, T-29, T-30, T-32, T-33 |

### Must have
- **T-01–T-14（P0）**：worktree 与写域冻结；`manifest-extension`/`view-model`/`tool-semantics`/`proposal`/`research-record` 五个消费契约与正负 fixture；独立 `rph/` 四包 workspace；`acp2_experience.py` Harness 与自检测试；隔离 Node 22.19.0 + pnpm 11.28.5 + 冻结 DSH；Chromium 可启动；Bridge 科研语义映射；真实 MCP 握手/DSH 工具发现/一次真实模型调用；H2 C 侧回执。
- **T-15–T-22（P1）**：`ACPClient.science`/`ACPClient.research` 查询与事件补全；无模型 Manual Research 入口；真实 DSH→MCP→Local 与 LSF singlepoint；optimize/frequency 分能力×backend 矩阵；真实浏览器三入口同任务；跨进程重放与恢复。
- **T-23–T-36（P2）**：ReactionSpec/QuestionSpec 与化学歧义确认；`ResearchRecord`/`Proposal` 持久化与记忆；持久审批与预算反馈；单 Supervisor 研究循环；科学证据视图；DSH 会话与科研轮次恢复；故障边界；限定 Diels–Alder 闭环；两份操作手册。
- **T-37–T-46（P3–P5）**：受权插件发现、Schema 自动表单、升级回退；NMR/热化学与多假设预算；多项目/多身份隔离；研究记录迁移；RPH CLI/包；干净安装；本机运维手册；最终报告。
- **证据与台账**：每个场景写在 B00 分配的绝对 `$ACP2_EVIDENCE/C/<scenario>/<run_id>/`；每个 todo 另存计划回执 `.omo/evidence/acp2-plan-c-rph-workbench/task-<N>-acp2-plan-c-rph-workbench.md`。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改 `src/cccp/**`、`src/acp_services/**`、`src/acp/**`、根 `pyproject.toml`、`requirements-node.txt`、CI、统一启动/安装脚本、四处版本号、根/跨域 `AGENTS.md`、Services 六列台账、B 的总 manifest；需要时只提交 `docs/reference/next-change/acp2-contracts/experience/requests/B-req-NN.md`。
- 不新建第二任务数据库、第二 Controller、第二执行链；RPH 不写 job status；不直接调用 ORCA/xTB/CREST；不通过 shell/SSH/DSH 通用执行工具绕过平台提交。
- 不 fork DSH 核心、不重写 MCP transport、不把 `mcp__acp__` 前缀名发回 MCP 服务端、不用多个 LLM 角色代替科学验证器。
- 不把 `NOT_VERIFIED` 改写成 `PASS`；不把 `COMPLETED` 当机制正确；不把模型自然语言结论写入 `EvidenceReport` 的 `supported`；不把 `DEFERRED` 计入 PASS 数。
- 不删除/重命名既有兼容面（`acp.calculations.primitives`、`acp.backends` shim、`compat/legacy`、recovery fixtures、NMR 模型资产、`frontend/js/vendor/`）。
- 不重启生产 `acp.service`、不写 `/var/lib/acp/runs`、不使用 8765、不替换系统 Node、不用 sudo 装浏览器系统依赖（缺库交 B）；不在 G4 未关闭时宣称独立打包已完成。
- 不在同一文件并发写入；不整文件 ours/theirs 覆盖另一写者；不把未提交的其他轨改动带入本轨提交。

## Verification strategy
> 自动化验证由 Agent 执行；用户批准与独立复现按各自职责留记录，不冒充自动化检查。
- **Test decision:** **tests-after + Harness 先行的 tests-first**。T-06/T-07 的 Harness 与自检测试必须先于任何 `c_accept <scenario>` 命令存在；其余模块采用先实现后补测试，但每个 todo 的验收命令必须真实执行并留原始输出。
- **导入路径检查：** 每次项目测试/harness 前用 `"$ACP2_PYTHON" -c 'import acp, cccp, acp_services; print(acp.__file__, cccp.__file__, acp_services.__file__)'` 断言解析到 `$ACP2_C_SOURCE`（开发轨）或 `$ACP2_INTEGRATION_SOURCE`（集成 live）；`$ACP2_A_SOURCE` 只属于 A，C 不读取；`$ACP2_SOURCE` 仅作为 `$ACP2_INTEGRATION_SOURCE` 的兼容别名，B/C 中不得再表示 A worktree；裸 `python3.11` 仅允许用于 `json.tool` 等非项目工具，不得用于 pytest/harness。
- **三类入口：**
  - Python：`"$ACP2_PYTHON" -m pytest tests/test_acp2_experience_harness.py -q`（以及后续 `tests/test_acp2_experience_*.py`）。
  - Node：`node frontend/js/module_tests/run_all.js acp2_<name>`；过滤器是**文件名子串**，0 个测试执行时退出码 2（`frontend/js/module_tests/run_all.js:58-78`），因此"空跑"永远不算通过。
  - RPH：`pnpm --dir rph build` / `test` / `check` / `package`；空测试集必须失败。
  - 浏览器：Playwright（`$ACP2_BROWSER_PYTHON -m playwright`），真实 Chromium，不用 DOM stub。
- **测试数量纪律：** 测试/用例数量只作零执行保护，不作为质量目标；每个 todo 的通过条件是**命名的行为矩阵全部覆盖**，`N≥...` 只是最低收集数，不得为凑数新增用例；优先覆盖关键行为矩阵（权限、幂等、科学误判、陈旧结果、恢复），普通文档/字段不要求同等成本；live 行必须能核验真实执行来源，不能用字符串扫描证明“绝无 mock”。
- **三态纪律：** 只用 `PASS / FAIL / NOT_VERIFIED`，范围另记 `IN_SCOPE / DEFERRED / NOT_APPLICABLE`。已执行失败只能记 `FAIL`（修复后重跑）；缺入口/环境/凭据才记 `NOT_VERIFIED`。两者不得互替；`DEFERRED` 不进 PASS 数。
- **证据两层：**
  - 产品验收证据（由 B00 分配的绝对根，可被 B 汇总）：`$ACP2_EVIDENCE/C/<scenario>/<run_id>/`，含脱敏 argv 数组、cwd、配置摘要、起止时间、退出码、实际 task/operation/session 引用、断言、工件相对路径/大小/SHA-256。
  - 计划回执（每 todo 一条）：`.omo/evidence/acp2-plan-c-rph-workbench/task-<N>-acp2-plan-c-rph-workbench.md`，记录命令、退出码、结果与上方证据目录的相对路径。
- **候选绑定：** 结果文档的 `candidate` 必须分别记录 `server_revision`、`client_revision`、`harness_revision`（允许三者不同版本，但必须记录实际组合）；fixture 场景记录 producer revision；live 场景必须记录 **B 冻结集成候选**的 revision（`$ACP2_INTEGRATION_SOURCE`），不得用 C 的开发 worktree 冒充；三者与契约/环境摘要一并写入 manifest。
- **失败注入：** 每个 todo 至少一个负向 QA；负向必须真的失败（断言抛错/退出码非 0），不得用"跳过"代替。

## Execution strategy
### Cross-track handoff ledger

> C 侧每个交接（对 A、对 B、对集成）登记一行：`producer_task`、`artifact_id`、`version/schema_digest`、`readiness`（`contract_ready|implementation_ready|integration_ready|verified`；`planned|blocked` 是交付状态，不混入就绪阶梯）、`requires`（显式 AND/OR 列表，不用“必需项”自由文本）、`consumer_task`、`next_integration_window`、`acceptance/confirmation`。C 把本轨行提交给 B 的机器可读依赖清单（`runtime/dependencies.json`，B00/B03 创建并验收，B 单写），不直接编辑它；B 未交付前 C 的行写入 `experience/handoffs/dependencies-c.json` 并记 `NOT_VERIFIED`。`requests/B-req-07.md` 明确要求该文件与缺生产者/悬空引用/跨轨环/阶段顺序的校验。消费 A/B 工件只认版本化路径 + digest，不读对方工作树。缺版本/摘要的交接不得被当作已完成。T-01/T-04 的 B00（baseline-lock/manifest/evidence_root）与 B02a（工具根/数据根/角色解释器/browser venv，不含 Chromium 启动）交接必须在 `dependencies-c.json` 中显式登记；缺任一生产者时对应验收记 `NOT_VERIFIED`，不得自造环境。B01 是 phase/readiness/场景注册表的唯一权威；C 只写 `extensions.experience`，不另定义公共 phase。T-40 的选择性交接使用 `scenario_key=experience:cross-domain-live/selectivity`；T-30 的搜索使用 `research-loop-live/search`。B18a 预检与 B20a 真实执行必须分别使用 `--phase preflight` 与 `--phase da-submit`（或 B01 注册表登记的等价 mode），不得靠输出目录或任务编号隐式分支。`requests/B-req-12.md` 要求 B02a/B02b 拆分且 B02a 不以 Chromium 启动为验收；`requests/B-req-13.md` 要求 B 同步主计划/索引/手册的 phase、readiness、CDXML 范围与 P2 MCP 选择。

### Parallel execution waves
> Waves are dependency gates, not calendar estimates. Size follows the real dependency spine; do not pad or split to hit a fixed count.

- **W0 地基（串行）**：T-01。未冻结 worktree/写域/证据根之前，不得创建任何产品文件。
- **W1（2 并行）**：T-02 ∥ T-04（契约 a 与工具链安装互不依赖）；T-02 完成后做 T-03。
- **W2（2 串行）**：T-05（依赖 T-02/T-04）→ T-06（依赖 T-02/T-05）。
- **W3（1 串行门）**：T-07。在 T-07 通过之前，任何 `c_accept <scenario>` 都不得作为证据执行（Harness 自检未过）。
- **W4（3 并行）**：T-08（依赖 T-06/T-07）、T-09（依赖 T-02/T-05/T-06/T-07）、T-15（依赖 T-02/T-03/T-07）。
- **W5**：T-10（依赖 T-09）、T-16（依赖 T-15）、T-17（依赖 T-08/T-16）。
- **W6（live 车道，按 H2 顺序）**：T-11（等 B05）→ T-12 → T-13（T-13a 代理自测 → T-13b 真实贯通）→ T-14。
- **W7（P1 live 并行）**：T-18（依赖 T-13/T-14/T-17）、T-19（依赖 T-18）、T-20（依赖 T-18/T-19）、T-21（依赖 T-17/T-18；Local 子行不等 LSF）、T-22（依赖 T-18–T-21）。
- **W8（P2 上半）**：T-23 → T-24；T-25（依赖 T-23）→ T-27；T-26（依赖 T-25）；T-28（依赖 T-27）；T-29（依赖 T-23/T-25）。
- **W9（P2 下半）**：T-30（依赖 T-28/T-29）、T-31（依赖 T-24/T-08；live 子行等 T-30a）、T-32（依赖 T-25/T-30）、T-33（依赖 T-28/T-32）、T-34（依赖 T-23–T-33 必需项）、T-35（依赖 T-34）、T-36（依赖 T-34/T-35）。
- **W10（P3+P4）**：T-37 → T-38 → T-39；T-40（入口依赖 T-36/A-24/A-25a/B23a；T-40a 提交 → B23b 执行 → A-25b 审计 → T-40b 签收）∥ T-41（B23a）；T-42（依赖 T-39/T-41）。
- **W11（P5）**：T-43 → T-44 → T-45 → T-46。
- **文件不交叠规则：** `frontend/js/workbench/features/science-question.js` 串行（T-17→T-23→T-24）；`reaction-builder.js` 串行（T-23→T-24）；`scientific-evidence.js` 只在 T-31；`rph/packages/research/**` 串行（T-25→T-26→T-32）；`scripts/acceptance/acp2_experience.py` 场景登记只在 T-06/T-07，后续 todo 只新增场景实现文件，不回改枚举。
- **live 车道门（用户已授权直接执行，但必须逐门关闭）：** H2 需 P0 十项全部 PASS——T-04/T-07 的五个 fixture/环境场景（`baseline`/`contracts`/`workspace`/`harness-selftest`/`node-dsh-install`）、T-08（`browser-install`）、T-09（`bridge-contract`，fixture 证据是必要条件但不单独关闭 H2）与 T-11/T-12/T-13 的 live 行；H3 需 T-18/T-19/T-20/T-21/T-22 必需行 PASS；H4 需 T-34/T-35 与 A/B 对同一作业的回执；H5 需 T-37/T-38/T-39；H6 需 T-40/T-41/T-42；H7 需 T-43–T-46。缺真实凭据/节点/软件时，该行记 `NOT_VERIFIED`，其余无依赖工作继续。

- **批次口径（三轨统一）：** 本计划全程 C00–C35 均在范围内，按 H0–H7 证据门自动推进；进入下一阶段不需要重新征求 go-ahead。只有真实支出的提高、ADR-005 许可/发行政策、ADR-008 科学验收结论和生产发布需要 owner 明确批准。不得在某一阶段自行缩小范围，也不得把“已进入下一阶段”与“本批完成”混用不同范围。

### Budget and spend caps (frozen)

> 用户已授权本轮直接执行真实模型/Local/LSF/DA，但未指定数值。以下为**计划冻结的默认上限**；任何提高都必须新增一条 owner 批准记录并写进 manifest，不能在执行中静默放宽。
>
> **执行前拦截（不只事后审计）**：每次模型调用与 `submit` 在发起前检查剩余额度；科学 CPU/存储由 B 服务端准入强制执行，C 不另建权威预算账本。场景结束的 `usage` 核对只用于审计，不能替代前置拦截。
>
> **计数单位**：分别记录 TaskRef、Recipe step、QC invocation、attempt、`bsub`/本地进程、model request；失败/重放/恢复只在产生真实执行时计数，幂等重放不重复计数。DA 的“12 个任务”按顶层 TaskRef 计，Recipe step 另列。
>
> **超时分层**：队列等待、科学运行、客户端请求、整体验收分别计时；LSF 场景的 Harness 超时必须 ≥ 其声明的最长队列等待，否则该行记 `NOT_VERIFIED(queue_window)` 而不是 FAIL。工具超时后保存引用并对账，不自动重提。
>
> **证据导出预算**：科学计算停止后仍保留有界只读导出时间，不得以墙钟耗尽为由丢弃最终证据页；清理只取消本轮创建的任务并确认远端停止。
>
> **三态判定（唯一口径）：** ①执行前因**缺少 owner 预算批准**或环境不具备而未提交 → `NOT_VERIFIED`，`reason` 必须写 `budget_insufficient` 或缺失项；②**已开始执行且超过冻结上限，或必需操作未完成** → `FAIL` 并停止，保留已得证据（科学结论可另行记 `inconclusive`，但不写进 `result`）；恰好用满上限且全部必需操作完成允许 `PASS`；③因计划范围决定不跑（如能力×backend 未声明支持）→ `DEFERRED`。三者不得互替；负测中预期拒绝（如第 13 次调用被拒）单独判定，不算实际超支。

| 资源 | 冻结上限 | 超出时 |
| --- | --- | --- |
| 单个 live 场景的模型调用 | ≤ 12 次；input ≤ 150k tokens；output ≤ 30k tokens | 执行中超过 → `FAIL`；恰好 12 次且必需操作完成 → `PASS`；执行前无批准 → `NOT_VERIFIED(budget_insufficient)` |
| 单个 live 场景的模型并发 | 1（不并发多 Agent） | 拒绝启动第二路 |
| Local 科学任务（任何提交 Local 任务的 live 场景，含 T-18/T-20/T-22/T-30/T-32/T-37–T-41/T-44） | 每场景 ≤ 2 个任务（1 正式 + 1 负向/对照；**T-20 例外**：Local 允许 2 个正式任务——optimize/local 与 frequency/local 各 1，不计负向）；单任务 ≤ 4 核、≤ 8 GB、≤ 30 min | 执行前预算不足 → `NOT_VERIFIED(budget_insufficient)`；已提交后超限 → `FAIL` |
| LSF 科学任务（任何提交 LSF 任务的 live 场景，含 T-19/T-20/T-37–T-41/T-44） | 每场景 ≤ 2 个任务（**T-20 例外**：LSF 允许 2 个正式任务——optimize/lsf 与 frequency/lsf 各 1，不计负向）；≤ 1 个节点；队列占用 ≤ 24 h | 同上 |
| DA 闭环（T-34/T-35） | 1 条主链；候选 TS ≤ 3；总任务 ≤ 12；墙钟 ≤ 4 h | 停止并保留 `inconclusive` |
| optimize/frequency（T-20） | 每个“能力×backend”格子 ≤ 1 组 | 范围未声明 → `DEFERRED` |
| P4 跨领域（T-40） | `nmr`/`thermo` 复用已有工件（新 TaskRef=0）；`selectivity` 默认最多 1 个新 Local TaskRef；三子行合计新 TaskRef ≤1、科学步骤/QC 按引用登记、总墙钟 ≤3600s | 超过 1 个新 TaskRef 须 owner 批准并记 T-40 例外；否则 `NOT_VERIFIED(budget_insufficient)`；换 run_id 不重置累计 |
| Chromium/Playwright | 单场景 ≤ 1 个浏览器实例、≤ 20 page | 关闭并记 `FAIL` |
| 磁盘 | `$ACP2_TOOLS` ≤ 10 GB；证据根 ≤ 2 GB | 停止并清理中间产物 |
| 生产副作用 | 0（禁生产 DB/run-root/8765/systemd） | 立即中止并记 `FAIL` |

> 每个 live 场景的验收必须包含一条**实际用量断言**（从结果文档或 receipt 读取，与上表比对），由 Harness 在场景结束时自动检查并写入 `usage`；没有 `usage` 字段的场景不得 PASS。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T-01 | B00, B02, B02a | 全部 | — |
| T-02 | T-01 | T-05, T-06, T-09, T-15 | T-04 |
| T-03 | T-01, T-02 | T-15, T-25, T-27 | T-04（T-02 完成后） |
| T-04 | T-01, B02a | T-05, T-11, T-12 | T-02 |
| T-05 | T-02, T-04 | T-06, T-09 | — |
| T-06 | T-02, T-05 | T-07, T-08, T-09 | — |
| T-07 | T-06 | 所有 `c_accept` 证据行 | — |
| T-08 | T-06, T-07 | T-21, T-31, T-38 | T-09, T-15 |
| T-09 | T-02, T-05, T-06, T-07 | T-10, T-11 | T-08, T-15 |
| T-10 | T-09 | H2 汇总（bridge-contract negative 子行） | T-16, T-17 |
| T-11 | T-07, T-09, B05 | T-12, T-13, T-14 | T-16, T-17 |
| T-12 | T-04, T-09, T-11 | T-13, T-14 | T-16, T-17 |
| T-13 | T-12 | T-14, T-18 | T-16, T-17 |
| T-14 | T-04, T-07, T-08, T-09, T-11, T-12, T-13 | H3 车道开启 | T-16, T-17 |
| T-15 | T-02, T-03, T-07 | T-16, T-17, T-21 | T-08, T-09 |
| T-16 | T-15 | T-17 | T-10, T-11 |
| T-17 | T-08, T-16 | T-18, T-21, T-23 | T-11, T-12 |
| T-18 | T-13, T-14, T-17 | T-19, T-20, T-21, T-22 | — |
| T-19 | T-18 | T-20, T-21 | T-22 的确定性部分 |
| T-20 | T-18, T-19 | — | T-21 |
| T-21 | T-08, T-15, T-17, T-18 | T-22 | T-20 |
| T-22 | T-18, T-19, T-20, T-21, B08, B09 | H3 收口 | — |
| T-23 | T-17 | T-24, T-29, T-34 | — |
| T-24 | T-23 | T-31, T-34 | T-25 |
| T-25 | T-03, T-09, T-23, B18a 资源接口 | T-26, T-27, T-29, T-32 | — |
| T-26 | T-25 | T-34 | T-27, T-28 |
| T-27 | T-03, T-25, B16 | T-28, T-34 | T-29 |
| T-28 | T-27 | T-33, T-34 | T-29, T-30 |
| T-29 | T-23, T-25 | T-30 | T-27, T-28 |
| T-30 | T-29, T-28 | T-31, T-32 | — |
| T-31 | T-24, T-08 | T-34 | T-32 |
| T-32 | T-25, T-30, B19 接口（cursor/快照，非整项 live） | T-33 | T-31 |
| T-33 | T-28, T-32 | T-34 | T-31 |
| T-34 | T-23–T-33 必需项, A-14–A-16, B20a | T-35, T-36 | — |
| T-35 | T-34, A-17–A-19, B20b | T-36 | T-31 |
| T-36 | T-34, T-35 | H4 收口 | — |
| T-37 | T-36, A-21, B21 | T-38 | — |
| T-38 | T-37 | T-39, T-42 | — |
| T-39 | T-37, T-38 | T-42 | T-40, T-41 |
| T-40 | T-36, A-24, A-25a, B23a | H6 车道（T-40b） | T-41 |
| T-41 | T-36, B15, B23a | T-42 | T-40 |
| T-42 | T-39, T-41 | H6 收口 | — |
| T-43 | T-42, T-38, A-27a | T-44, T-45 | — |
| T-44 | T-43, B25a | T-45 | — |
| T-45 | T-44 | T-46 | — |
| T-46 | T-43–T-45, B25a | H7 的 C 部分（B26b 单向汇总） | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [ ] 1. T-01 / C00：冻结 worktree、写域与证据根
  What to do / Must NOT do: 在仓库外建 C 轨 worktree 并冻结基线。**基线来源改为 B00 的 `docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json`**：读取 `final_debug_candidate`。允许两种形态：①完整 40-hex（B02 baseline patch 应用并提交后写回的结果 SHA，优先；不得等于历史参照 `096cdff…`）；②`pending_fix_bundle` 指向的 `docs/reference/next-change/acp2-contracts/runtime/final-debug-patch/`——按 `apply-order.md` 逐文件 `git apply`，**逐文件校验预镜像/应用后 SHA-256**，成功后提交到 `acp2/c-experience` 并把结果 HEAD 写回 `ownership.md`；不得只算一个聚合 `git diff` 摘要，也不得把未提交补丁当基线。**worktree/branch 的一次性创建已获本次规划 owner 授权**；若 B 已建好则直接消费并在 `ownership.md` 记录 B 的交接引用。`ACP2_CHECKOUT_ROOT` 优先取 B manifest，未给则用 `/ACPServer/acp2-development`。定义并写入 `experience/ownership.md`：`$ACP2_A_SOURCE`（A 科学 worktree，只读；C 不使用）、`$ACP2_C_SOURCE="$ACP2_CHECKOUT_ROOT/C"`、`$ACP2_INTEGRATION_SOURCE`（B 冻结集成候选）、`$ACP2_SOURCE="$ACP2_INTEGRATION_SOURCE"`（B/C 唯一含义，不得再指 A）、`$ACP2_TOOLS`、`$ACP2_DATA_ROOT`、**角色解释器** `$ACP2_C_PYTHON`（C 开发/Harness，导入 `$ACP2_C_SOURCE`）、`$ACP2_INTEGRATION_PYTHON`（冻结集成 live，导入 `$ACP2_INTEGRATION_SOURCE`）、`$ACP2_BROWSER_PYTHON`（B 安装 `.[browser]` 的浏览器环境）、`$ACP2_PYTHON`（fixture/dry-run 默认指向 `$ACP2_C_PYTHON`）、`$ACP2_CONTRACTS="docs/reference/next-change/acp2-contracts"`（绝对路径）、`$ACP2_EVIDENCE`（**必须等于 B00 `manifest.json`/`baseline-lock.json` 中登记的 `evidence_root` 绝对路径与 candidate ID**；若该字段缺失，先提 `B-req` 要求 B 补字段并记 `NOT_VERIFIED`，不得自造）。**B00/B02a 交接**：baseline-lock/manifest/evidence_root 来自 B00；`$ACP2_TOOLS`/`$ACP2_DATA_ROOT`/三个角色解释器/browser venv 来自 B02a，C 只校验不创建；B02a 不以 Chromium 启动为验收，B02b 的 package smoke 不是 T-04 前置；缺失时按 `NOT_VERIFIED` 记录并保持对应门开放；创建 `requests/B-req-12.md` 要求 B02a/B02b 拆分且 B02a 不以 Chromium 启动为验收。记录 `git rev-parse HEAD`、`git status --short`、`node frontend/js/module_tests/run_all.js` 的完整输出。创建 `docs/reference/next-change/acp2-contracts/experience/ownership.md`：C 写域清单、A/B request 路径与编号规则（`A-req-NN.md`/`B-req-NN.md`）、证据根、candidate ID。**Must NOT**：读写生产 DB/`/var/lib/acp/runs`、修改当前 `feat/frontend-plan1-closure` 工作区、把 `.omo/` 当产品证据根、用旧 HEAD 或未提交补丁冒充最终修复基线。
  Closes: GAP-1, GAP-2, GAP-6
  Parallelization: Wave 0 | Blocked by: B00（baseline-lock/manifest/evidence_root）, B02（baseline patch）, B02a（工具根/数据根/角色解释器/browser venv，不含 Chromium 启动） | Blocks: 全部
  References (executor has NO interview context - be exhaustive): `docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:16-27,169-176`；`docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md:44-58,206-213`；`reports/architecture_remediation_20261020/REPORT.md:25-36`；`frontend/js/module_tests/run_all.js:58-78`；`AGENTS.md` ANTI #15/#19。
  Acceptance criteria (agent-executable): 先记录 worktree 创建瞬间的干净状态（在任何写入之前执行 `test -z "$(git -C "$ACP2_CHECKOUT_ROOT/C" status --porcelain)"` 并落证据）；`test -n "$ACP2_C_PYTHON" -a -n "$ACP2_INTEGRATION_PYTHON" -a -n "$ACP2_BROWSER_PYTHON"`；`"$ACP2_C_PYTHON" -c 'import acp, cccp, acp_services; print(acp.__file__)'` 的路径位于 `$ACP2_C_SOURCE`，`"$ACP2_INTEGRATION_PYTHON" -c 'import acp, cccp, acp_services; print(acp.__file__)'` 位于 `$ACP2_INTEGRATION_SOURCE`；`"$ACP2_PYTHON" - <<'PY'` 读取 B00 `baseline-lock.json`，断言 `final_debug_candidate` 为 40-hex 且不等于 `096cdff…`，或 `pending_fix_bundle` 含 `bundle_dir`、`apply_order`、逐文件 `sha256` 映射；若为补丁：逐文件校验 SHA-256 后按序应用并提交，`git rev-parse HEAD` 等于写回 `ownership.md` 的结果 SHA；若为 40-hex：HEAD 等于该 SHA；`"$ACP2_PYTHON" - <<'PY'` 断言 `ownership.md` 同时包含 `frontend/`、`rph/`、`tests/test_acp2_experience_`、`scripts/acceptance/acp2_experience`、`acp2-contracts/experience/`、`A-req-`、`B-req-`、`ACP2_CONTRACTS`、`ACP2_A_SOURCE`、`ACP2_C_SOURCE`、`ACP2_INTEGRATION_SOURCE`、`ACP2_SOURCE`、`ACP2_C_PYTHON`、`ACP2_INTEGRATION_PYTHON`、`ACP2_BROWSER_PYTHON`、`B02a`、绝对 `$ACP2_EVIDENCE`，且 `$ACP2_SOURCE` 与 `$ACP2_INTEGRATION_SOURCE` 字符串相等、`$ACP2_EVIDENCE` 与 B00 登记的 `evidence_root` 字符串相等、candidate ID 与 B00 `candidate_id` 相等；`node frontend/js/module_tests/run_all.js` 退出码 0 且 `N passed, 0 failed`（N>0）；之后 `git status --porcelain` 只允许出现 `ownership.md` 与本 todo 证据文件。
  QA scenarios: happy = 上述四条命令逐条执行并落 `.omo/evidence/acp2-plan-c-rph-workbench/task-1-acp2-plan-c-rph-workbench.md`；failure = 在 `/tmp` 复制 `ownership.md` 并删去 `A-req-` 行，用同一 python 断言必须返回非 0（证明断言有牙）。Evidence `$ACP2_EVIDENCE/C/baseline/`。
  Commit: Y | `chore(acp2-c): freeze C worktree, write domain and evidence root`
  Recommended task executor category: unspecified-low - 机械的 worktree/基线记录步骤，命令与断言已给定。

- [ ] 2. T-02 / C01a：冻结 manifest 扩展、ViewModel 与工具语义契约
  What to do / Must NOT do: 在 `docs/reference/next-change/acp2-contracts/experience/` 创建 `manifest-extension.schema.json`、`view-model.schema.json`、`tool-semantics.md`。`manifest-extension` 至少描述：`candidate_id`（生产者候选标签；真正的执行 revision 用 `candidate{server_revision,client_revision,harness_revision}`）、`evidence_root`（绝对路径，与 B00 一致）、**B01 公共注册表引用**（scenario/phase/readiness/owner/允许副作用/超时的版本与 digest；C 不复制枚举）、A/B 契约路径与 digest、源码/工具/数据/证据绝对路径、测试 Controller 引用、MCP launch 清单、DSH revision/launcher/profile、浏览器可执行路径、模型 Provider/model ID、**受限凭据来源引用**、fixture 清单、预算/任务数/超时上限；**禁止**出现任何凭据值。C 的 `extensions.experience` 只写 `mode: fixture|live`、`subcase`、`scenario_key`、`interpreter`、`product_import`、`harness_path`、`test_revision` 与 C 自有证据字段；公共 `phase`/`readiness` 必须引用 B01 注册表，不得另定义一份。**Schema 词汇统一**：声明 `schema_dialect: json-schema-2020-12` 与 C 的 UI 支持子集版本/关键词清单；标准约束使用 `type`（含 `"null"` 以表达可空）、`enum`、`required`、`minimum/maximum`、`items`、`properties`、`additionalProperties`；`range`/`nullable` 只能作为明确的 UI 方言并同时给出标准等价物，不得让通用校验器执行自定义业务约束。未知 major version 不进入执行；历史读取保留未知字段，但不自动获得执行/恢复资格。`tool-semantics.md` 写死：raw 工具名 ↔ DSH 模型可见名（`mcp__acp__` 前缀）、`isError`、`structuredContent`、异步/长任务引用、`generation`/`revision`、`prepare`/`read` 边界、project/attempt 归属。`view-model.schema.json` 只描述界面只读快照，明确"不是第二份科学结果权威"。**在 B01 draft Schema 可用时**交付 `experience/fixtures/acceptance-result.valid.json` 与 `.invalid.json`（只引用 B01 公共字段，C 只写 `extensions.experience`），记录 B01 版本/digest；不得复制完整字段清单。创建 `requests/B-req-07.md`，要求 B00/B03 建立并验收 `runtime/dependencies.json`；并创建 `requests/B-req-13.md`，要求 B 同步主计划/索引/手册的 CDXML 范围、BASE_REV、角色环境、P2 MCP、phase/readiness、P4 顺序与 H4/H6 必需行；C 的行写入 `experience/handoffs/dependencies-c.json`。**Must NOT**：复制 A 的科学 Schema 成可编辑权威（只 `$ref`+digest）；在 Browser/RPH 重新定义任务状态机；把 `range`/`nullable` 当 JSON Schema 标准关键词。
  Closes: GAP-2, GAP-6
  Parallelization: Wave 1 | Blocked by: T-01 | Blocks: T-05, T-06, T-09, T-15
  References: 计划 C `:110-113,127-129,181-185`；总索引 `:83-98`（K-EXP/K-MCP 最小内容）；`src/acp_services/entrypoints/mcp.py:186-243`（八工具与 `isError` 语义）；`frontend/js/client/resource-store.js:140`；`frontend/js/client/event-stream.js:14-25`。
  Acceptance criteria: `"$ACP2_PYTHON" -m json.tool <file> >/dev/null` 对两个 `.json` 均退出 0；`"$ACP2_PYTHON" - <<'PY'` 内联断言：两个 Schema 均含 `schema`/`schema_version`/`schema_dialect` 顶层字段，`manifest-extension` 必填集覆盖上述 12 项，且任何属性名匹配 `(?i)(secret|token|password|api[_-]?key)$` 时报错；可空/边界样例分别用标准 `type:["string","null"]` 与 `minimum/maximum` 表达；回执正负样例在 B01 校验器可用时全绿（缺失则记 `NOT_VERIFIED`，不影响本 todo 其余部分），且 invalid 样例因缺 `candidate_revision`/错误平台位置/双份平台标识之一被拒。
  QA scenarios: happy = 两个合法样例通过上述断言；failure = 构造缺 `candidate_id` 的样例与含 `api_key` 的样例，断言必须抛错（非 0）。Evidence `$ACP2_EVIDENCE/C/contracts/`。
  Commit: Y | `docs(acp2-c): freeze experience manifest and view-model contracts`
  Recommended task executor category: deep-high - 跨 RPH/Workbench/B 的三方消费契约，决策无法只靠现有样例判定。

- [ ] 3. T-03 / C01b：冻结 Proposal/ResearchRecord 最小 Schema、正负 fixture 与 A/B 请求
  What to do / Must NOT do: 创建 `proposal.schema.json`、`research-record.schema.json` 的最小引用版（research/project ID、question/reaction revision、hypothesis、recipe digest、round、decision、approval ref、task refs、evidence refs、缺失验证、人工修改），P2 再版本化补全。**明确三层身份**：`plan_definition_digest` 只投影科学定义（Recipe/capability steps、初始输入、方法/映射/电荷自旋、预算、允许的有限替代范围），排除 `approval_ref`、审批状态、更新时间、显示名、后续追加的 TaskRef/evidence refs；`step_identity` 在步骤派发前物化，覆盖实际上下游字节摘要、物化参数与实现版本；`approval_ref` 绑定 `plan_definition_digest + project + budget + 允许变更范围`。创建 `experience/fixtures/` 下四类负向样例（未知工具、非法请求、错误项目、失效引用）与对应正向样例；创建 `requests/A-req-01.md`（科学字段/单位/电荷自旋语义缺口）、`requests/B-req-01.md`（launch/application API 字段缺口），并创建 `requests/A-req-03.md`（选择性唯一 `scenario_key=experience:cross-domain-live/selectivity` 与 T-40a→B23b→A-25b→T-40b 顺序）和 `requests/B-req-10.md`（B23a/B23b 使用同一 key），以及 `requests/A-req-04.md`（候选搜索输入/判据）、`requests/B-req-15.md`（搜索执行 proof）与 `requests/B-req-11.md`（DA run_spec 与 preflight/da-submit 显式 phase）。每个 request 必含：consumer step、owner、当前/期望 Schema revision、最小输入、预期成功输出、**至少一个失败输出**、兼容要求、证据路径。**Must NOT**：把 Proposal 写成任务状态机；把 DSH Session 当 Record；给 A/B 文件直接改字段；把 approval_ref/状态/时间戳计入科学定义摘要。
  Closes: GAP-2, GAP-6
  Parallelization: Wave 1 | Blocked by: T-01, T-02 | Blocks: T-15, T-25, T-27
  References: 计划 C `:181-185,77`；总索引 `:83-92`；`src/acp_services/runtime/plan_contracts.py`（plan/step identity）；`tests/AGENTS.md:39-47`。
  Acceptance criteria: 两个 Schema 通过 `"$ACP2_PYTHON" -m json.tool`；`"$ACP2_PYTHON" - <<'PY'` 断言：`research-record.schema.json` 必填含 `research_id/project_id/round/decision/plan_definition_digest/step_identity_refs/task_refs/evidence_refs`，`proposal.schema.json` 必填含 `proposal_id/project_id/recipe_digest/plan_definition_digest/budget/approval_ref`；**摘要稳定性测试**：改 `approval_ref`/状态/显示名后 `plan_definition_digest` 不变，改 method/映射/初始输入/预算后变化；`test -f "$ACP2_CONTRACTS/experience/requests/A-req-01.md" -a -f "$ACP2_CONTRACTS/experience/requests/B-req-01.md"` 且每份含 `## Failure output` 段（用绝对 `$ACP2_CONTRACTS`，不得保留 `...` 占位符）。
  QA scenarios: happy = 合法 proposal/record 通过断言；failure = 把 `round` 改为字符串、把 `task_refs` 写成裸字符串数组，断言必须抛错；再验证负向 fixture 中的"错误项目"样例被 ViewModel 校验拒绝。Evidence `$ACP2_EVIDENCE/C/contracts/`。
  Commit: Y | `docs(acp2-c): add research record/proposal schemas, fixtures and A/B requests`
  Recommended task executor category: unspecified-high - 多文件 Schema + fixture + 请求文档，模式已由 T-02 确定。

- [ ] 4. T-04 / C04：安装隔离 Node/pnpm/冻结 DSH 并冻结兼容清单
  What to do / Must NOT do: 按手册 §3.2 从官方来源下载 Node `22.19.0` 到 `$ACP2_TOOLS/downloads`，用 `SHASUMS256.txt` 校验后再解包到 `$ACP2_TOOLS`；本终端把 `$ACP2_NODE_HOME/bin` 前置，不替换系统 Node。安装 `pnpm@11.28.5` 到 `$ACP2_TOOLS/pnpm`；`git clone` DSH 并 `checkout --detach 687f282e8625c6b8d11ff58211a90524be2e4317`；安装前先读该 revision 的 `package.json` `engines`，若与 Node 22.19.0 不符则重新选版并记录理由。跑 `pnpm install --frozen-lockfile`、`pnpm run build`、`pnpm dsh --version`。写 `experience/dsh-compatibility.json`（Node/pnpm/DSH/官方 MCP 客户端版本、可执行绝对路径、`pnpm-lock.yaml` SHA-256、engines 原文）。在 `$ACP2_DATA_ROOT/acceptance-env-c.sh`（`chmod 600`）定义 `dsh_acceptance()`、`c_accept()` 与 `ACP2_*` 变量：`export ACP2_C_SOURCE="<C worktree 绝对路径>"`；`export ACP2_A_SOURCE="<A 科学 worktree，只读；C 不使用>"`；`export ACP2_INTEGRATION_SOURCE="<B 冻结集成候选绝对路径>"`；`export ACP2_SOURCE="$ACP2_INTEGRATION_SOURCE"`（兼容别名，唯一含义）；`export ACP2_TOOLS="<工具根绝对路径>"`；`export ACP2_DATA_ROOT="<数据根绝对路径>"`；`export ACP2_DSH_LOADER="<DSH tsx/esm loader>"`；`export ACP2_EVIDENCE="<B00 登记的绝对 evidence_root>"`；`export ACP2_CONTRACTS="<docs/reference/next-change/acp2-contracts 的绝对路径>"`；`export ACP2_C_PYTHON="<C 开发 venv，导入 $ACP2_C_SOURCE>"`；`export ACP2_INTEGRATION_PYTHON="<冻结集成 venv，导入 $ACP2_SOURCE>"`；`export ACP2_BROWSER_PYTHON="<B 安装 .[browser] 的浏览器 venv>"`；`export ACP2_PYTHON="$ACP2_C_PYTHON"`；`export ACP2_NODE_HOME="$ACP2_TOOLS/<node-dist>"`；`export ACP2_DSH_SOURCE="$ACP2_TOOLS/deepseek-harness"`；`c_accept() { [ -n "$ACP2_CONTRACTS" ] || return 2; [ "$ACP2_EVIDENCE" = "$("$ACP2_C_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["evidence_root"])' "$ACP2_CONTRACTS/runtime/baseline-lock.json")" ] || return 2; local scenario="$1" timeout="$2"; shift 2; local phase="main" mode="" subcase="" dry_run=0; local pass=(); while [ $# -gt 0 ]; do case "$1" in --phase) phase="$2"; pass+=("--phase" "$2"); shift 2;; --mode) mode="$2"; pass+=("--mode" "$2"); shift 2;; --subcase) subcase="$2"; pass+=("--subcase" "$2"); shift 2;; --dry-run) dry_run=1; pass+=("--dry-run"); shift;; *) pass+=("$1"); shift;; esac; done; local run_id="${ACP2_RUN_ID:-}"; if [ -z "$run_id" ]; then [ "$phase" = "main" ] || return 3; run_id="$scenario-$(date -u +%Y%m%dT%H%M%S%NZ)-$$"; fi; local src="$ACP2_C_SOURCE" py="$ACP2_C_PYTHON" product_src="$ACP2_C_SOURCE" live_default=0; case "$scenario" in *-live) live_default=1;; esac; if [ "$mode" = "live" ] || { [ -z "$mode" ] && [ "$live_default" -eq 1 ] && [ "$dry_run" -eq 0 ]; }; then py="$ACP2_INTEGRATION_PYTHON"; product_src="$ACP2_INTEGRATION_SOURCE"; fi; [ -f "$src/scripts/acceptance/acp2_experience.py" ] || return 2; ACP2_PRODUCT_SOURCE="$product_src" "$py" "$src/scripts/acceptance/acp2_experience.py" --scenario "$scenario" --timeout "$timeout" --out "$ACP2_EVIDENCE/C/$scenario/$run_id/$phase${mode:+/$mode}${subcase:+/$subcase}" "${pass[@]}"; }`（**phase/mode 契约**：同一 scenario 的多 phase 必须共享 `ACP2_RUN_ID`，各自写入 `<run_id>/<phase>/`（有 mode 时 `<run_id>/<phase>/<mode>/`，有 subcase 时再追加 `<subcase>/`）；aggregate 读取同一 `<run_id>` 下的 phase 子目录（含 `main/<subcase>` 或 `main/<mode>/<subcase>`）；不可覆盖检查只针对当前叶目录，兄弟 phase/mode/subcase 不使本次失败。`--dry-run` 或 `--mode fixture` 用 `$ACP2_C_PYTHON` + `$ACP2_C_SOURCE`；`--mode live` 或注册为 live 且未显式 fixture/dry-run 的场景用 `$ACP2_INTEGRATION_PYTHON` 导入 `$ACP2_INTEGRATION_SOURCE`，Harness 脚本仍取 C revision；结果分别记录 `interpreter`、`product_import`、`harness_path`、`test_revision`，并在 `candidate{server,client,harness}` 中记录实际组合）。**B02a 交接**：`$ACP2_TOOLS`、`$ACP2_DATA_ROOT`、三个角色解释器与 browser venv/extra 由 B02a 提供并登记在 `runtime/dependencies.json`；C 只校验、不创建 venv。B02a 不以 Chromium 启动为验收；Chromium 安装与真实启动由 C T-08 完成，H2 汇总时才要求启动证据。B02b 的 package smoke 不是 T-04 前置。B02a 未交付时，Node/pnpm/DSH 安装仍可完成，但 T-01/T-04 的解释器相关验收记 `NOT_VERIFIED`，T-05/T-06 的 Python 依赖步骤保持开放；不得用 C 开发 venv 冒充集成 venv。Node/pnpm 版本以批准的 compatibility lock 为准；若 engines 不匹配，必须更新该 lock、命令与样例后重验，不得只改安装步骤。新开终端 `source` 后复核。**Must NOT**：用 npm `latest` 代替冻结源码；把 DSH clone 放进 `rph/`；导出含凭据的变量；改 `HOME`/`CODEX_HOME`。
  Closes: GAP-2, GAP-4
  Parallelization: Wave 1 | Blocked by: T-01, B02a（工具根/数据根/角色解释器/browser venv，不含 Chromium 启动） | Blocks: T-05, T-11, T-12
  References: 手册 `:159-244`（ENV-03/§3.2）；`docs/reference/next-change/ACP_2.0_Parallel_Plan_C_RPH_Workbench_20261020.md:205-212`；根 `AGENTS.md` COMMANDS（`pip install -e` 与本轨无关，仅说明环境隔离）。
  Acceptance criteria: `node --version` 与已批准 compatibility lock 的 `node` 一致；`pnpm --version` 与 lock 的 `pnpm` 一致；`git -C "$ACP2_DSH_SOURCE" rev-parse HEAD` 等于锁定的 DSH SHA；`pnpm dsh --version` 退出 0；`test -s "$ACP2_DATA_ROOT/acceptance-env-c.sh"`；新 shell `source` 后 `command -v node` 指向 `$ACP2_NODE_HOME`；`test -n "$ACP2_C_SOURCE" -a -n "$ACP2_SOURCE" -a -n "$ACP2_TOOLS" -a -n "$ACP2_DATA_ROOT" -a -n "$ACP2_DSH_LOADER" -a -n "$ACP2_CONTRACTS"`；`test "$ACP2_SOURCE" = "$ACP2_INTEGRATION_SOURCE"`；且 `test "$ACP2_EVIDENCE" = "$("$ACP2_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["evidence_root"])' "$ACP2_CONTRACTS/runtime/baseline-lock.json")"`；`test -x "$ACP2_C_PYTHON" -a -x "$ACP2_INTEGRATION_PYTHON" -a -x "$ACP2_BROWSER_PYTHON"`；**角色解释器导入来源**：`"$ACP2_C_PYTHON" -c "import acp,os; p=os.path.realpath(acp.__file__); assert p.startswith(os.path.realpath(os.environ['ACP2_C_SOURCE'])+os.sep), p"`、`"$ACP2_INTEGRATION_PYTHON" -c "import acp,os; p=os.path.realpath(acp.__file__); assert p.startswith(os.path.realpath(os.environ['ACP2_INTEGRATION_SOURCE'])+os.sep), p"`、`"$ACP2_BROWSER_PYTHON" -c "import playwright,os,sys; p=os.path.realpath(playwright.__file__); assert p.startswith(os.path.realpath(sys.prefix)+os.sep), p"` 三者均退出 0；**四种选路**：`c_accept baseline 60 --dry-run`、`c_accept evidence-browser-live 60 --mode fixture`、`c_accept mcp-live 60 --dry-run` 三条 fixture/dry-run 行均记录 `interpreter=$ACP2_C_PYTHON`、`product_import=$ACP2_C_SOURCE`；真实 `c_accept mcp-live 60` 记录 `interpreter=$ACP2_INTEGRATION_PYTHON`、`product_import=$ACP2_INTEGRATION_SOURCE`；四条均记录 `harness_path=$ACP2_C_SOURCE/scripts/acceptance/acp2_experience.py` 与 `test_revision`。**`c_accept baseline` 与 Harness 文件存在性检查移到 T-07（Harness 交付后）**，本 todo 不等待。
  QA scenarios: happy = 上述命令 + `c_accept node-dsh-install 1200`（由 T-07 统一实跑并归档，T-04 本身不等待）；failure = 把下载包复制一份并翻转 1 字节，运行校验脚本必须非 0 且不执行解包；把 `engines` 断言改成不匹配值时安装脚本必须停止。Evidence `$ACP2_EVIDENCE/C/node-dsh-install/`。
  Commit: Y | `chore(acp2-c): install isolated node, pnpm and frozen DSH`
  Recommended task executor category: unspecified-low - 手册已给逐条命令，主要工作是校验与记录。

- [ ] 5. T-05 / C02：创建独立 RPH pnpm workspace
  What to do / Must NOT do: 按计划 C §3.1 创建 `rph/`：根 `package.json`（`private:true`、`packageManager:"pnpm@11.28.5"`、`engines.node` 与 DSH 一致、scripts `build/test/check/package`）、`pnpm-workspace.yaml`、`tsconfig.json`、`pnpm-lock.yaml`；四个包 `packages/{bridge,research,dsh-bundle,cli}` 各含 `package.json` 与 `src/index.ts`；`test/` 至少一个真实测试；`fixtures/`、`examples/` 建目录并放 README。写 `rph/README.md` 明确依赖方向：`cli → dsh-bundle → bridge → research`，`bridge` 只依赖公共 JSON 与平台客户端接口，`research` 不导入平台 store。新增 `rph/scripts/check-boundaries.mjs`：扫描 `bridge`/`research` 源码的 **import 与执行 API**（`node:child_process`、`child_process`、`spawn/exec/execFile`、`ssh2`、`node:net` 直连 QC、DSH 私有源码路径），命中即非 0；**科学软件名（orca/xtb/crest 等）允许作为数据、元数据或错误信息出现，不得仅凭字面量误伤**；`dsh-bundle` 只允许引用 DSH 公共插件接口字符串。把 workspace/CI/静态资源挂载需求写成 `requests/B-req-06.md` 交 B（C 的请求编号在创建时分配，不要求与 todo 顺序一致）。**Must NOT**：新增根 `package.json`；把 DSH 源码放进 `rph/`；在 `cli` 里启动 Controller 或执行科学软件。
  Closes: GAP-2
  Parallelization: Wave 2 | Blocked by: T-02, T-04 | Blocks: T-06, T-09
  References: 计划 C `:93-113,187-194`；`glob **/package.json` 零命中（本仓首次引入）；`frontend/js/module_tests/run_all.js`（既有零依赖 Node 习惯，不得被 pnpm 影响）；根 `AGENTS.md` ANTI #5（`__init__.py` 不放实现，类比：包入口只导出）。
  Acceptance criteria: `pnpm --dir rph install` 生成 lock；`pnpm --dir rph install --frozen-lockfile` 退出 0；`pnpm --dir rph build` 退出 0；`pnpm --dir rph test` 退出 0 且输出至少 1 个用例；`pnpm --dir rph check` 退出 0；`node rph/scripts/check-boundaries.mjs` 退出 0；`test ! -f package.json`。
  QA scenarios: happy = 上述六条命令；failure = 临时在 `rph/packages/bridge/src/` 新建 `bad.ts` 内容 `import {spawn} from "node:child_process"`，`check` 必须非 0；清空 `rph/test/` 后 `test` 必须非 0（空测试集不算通过）。Evidence `$ACP2_EVIDENCE/C/workspace/`。
  Commit: Y | `feat(acp2-c): scaffold standalone rph pnpm workspace`
  Recommended task executor category: unspecified-high - 首次引入 Node workspace，构建链与边界门都要一次做对。

- [ ] 6. T-06 / C03a：实现体验 Harness CLI、场景枚举与证据写入
  What to do / Must NOT do: 新建 `scripts/acceptance/acp2_experience.py`，实现三种**互斥**模式：①场景模式 `--scenario <id> --out <dir> --timeout <sec> [--manifest <abs>] [--phase <common-phase>] [--mode fixture|live] [--subcase <id>] [--dry-run]`；②回执校验模式 `--verify-receipt <path>`（不要求 `--manifest/--scenario/--out/--timeout`）；③文档占位符模式 `--check-doc-placeholders <path>`（同样不要求场景参数）。**公共回执 validator/摘要/脱敏/不可覆盖目录由 B 单写；C 复用其 validator，不另写同名不同义实现。**`--help` 在任意模式下退出 0。固定退出码 `0=PASS,1=FAIL,2=NOT_VERIFIED/未执行,3=用法错误`；**dry-run 优先级**：已知场景在 `--dry-run` 下即使实现未完成也返回 0 并写 `NOT_VERIFIED`；非 dry-run 的未实现场景返回 2；未知场景返回 3。`--help` 不初始化模型/DSH、不连接平台、不提交；`--dry-run` 只校验输入并列出动作、写 `NOT_VERIFIED`、退出 0。场景枚举冻结为计划 C §3.3 的 36 个 ID，**逐字**为：P0 `baseline, contracts, workspace, harness-selftest, node-dsh-install, browser-install, bridge-contract, mcp-live, dsh-discovery-live, model-live`；P1 `client-fixtures, manual-browser-fixtures, dsh-local-singlepoint-live, dsh-lsf-singlepoint-live, dsh-optfreq-live, same-task-browser-live, client-recovery-live`；P2 `question-reaction-fixtures, research-record-fixtures, approval-budget-live, research-loop-live, evidence-browser-live, research-resume-live, fault-boundaries-live, da-research-live, p2-closeout`；P3 `plugin-discovery-live, schema-form-live, plugin-upgrade-live`；P4 `cross-domain-live, multi-project-live, experience-upgrade-live`；P5 `client-release, clean-install-live, operator-runbook-live, experience-release-closeout`。非 dry-run 的未实现场景返回 2，未知场景返回 3（dry-run 优先级见上）。结果文档直接引用 B01 的唯一 `acceptance-result.schema.json`（版本+digest 写入 `harness-contract.md`），**不复制、不重定义 B 的公共字段**；C 只定义 `extensions.experience` 与入口别名归一化。B 的公共字段（含 `candidate_revision`、`schema_dialect`、`evidence_kind`、平台标识）以 B Schema 为准；平台标识只写 B 规定的唯一位置（若 B 用顶层 `job_id/attempt/release_id/submission_id/negotiated_protocol`，C 不得再写 `platform{}` 副本；`platform` 仅作为旧别名读取归一化）。C 的扩展字段：`mode`（`fixture|live`；`live` 仅允许注册为 live 的场景，`evidence-browser-live` 的 live 是终态整场景结果，无需也不得再跑 `aggregate`）、`subcase`、`scenario_key`、`interpreter`、`product_import`、`harness_path`、`test_revision`、`run_id`、`sub_results[]`、`usage{tasks,model_calls,input_tokens,output_tokens,total_tokens,wall_seconds,queue_seconds,run_seconds,prepare_seconds,cleanup_seconds,disk_bytes}` 与 `extensions.experience{...}`；禁止 `started_on/finished_on/evidence` 等同义字段。`--verify-receipt` 还要拒绝自引用：receipt 任一行的 `artifacts[]` 若包含该 receipt 自身或其自身结果文件，退出 1。`--out` 指向 `$ACP2_EVIDENCE/C/<scenario>/<run_id>/<phase>/`；若目标目录已存在且非空，退出 3，禁止覆盖既有证据；`--phase aggregate` 从同一 `<run_id>` 的兄弟 phase 目录读取结果（不要求 aggregate 的 `--out` 预先存在），阶段结果不共享 run_id 时明确报错。`--phase` 的退出码只评估该公共 phase；整场景结果由 `--phase aggregate` 读取既有 phase 结果后生成（唯一例外：`evidence-browser-live` 的 `--mode live` 是终态整场景结果，不需要 `aggregate`）。`--phase` 的取值来自 B01 注册表（`main|approval|budget|da-submit|da-evidence|aggregate` 及 B01 登记的扩展如 `preflight`）；C 不新增公共 phase。`--mode fixture|live` 与 phase 正交：`mode=live` 只允许注册为 live 的场景，`mode=fixture` 对所有场景可用；`--subcase` 用于同一 36 场景内的子行（`cross-domain-live` 的 `nmr|thermo|selectivity`、`research-loop-live` 的 `control|search`）。`--verify-receipt <path>` 逐行核对 handoff receipt 与对应场景结果文件：`result`/`command`/`exit_code`/`candidate` 字段相等，`artifacts[]` 每项存在且 SHA-256 匹配，缺任一即退出 1。实现凭据脱敏（键名匹配 `(?i)(secret|token|password|api[_-]?key|authorization)` 一律 `***`）、manifest schema/版本校验、总超时与任务/调用上限、**每次模型调用/提交前检查剩余额度（前置拦截）**，并在场景结束时再审计 `usage`、把超限写成 `FAIL`。**Must NOT**：把执行失败捕获成 `NOT_VERIFIED`；`--help/--dry-run` 写 PASS；自行重启 Controller；在脚本里起第二 Controller。同时把 `c_accept <scenario> <timeout> [--phase <phase>]` 的 argv 约定写进 `harness-contract.md`：wrapper 由 T-04 的 `acceptance-env-c.sh` 定义，**timeout 是必填的第 2 位参数**，`--out` 由 wrapper 按 `$ACP2_EVIDENCE/C/<scenario>/<run_id>` 自动生成；额外参数原样转发给 `acp2_experience.py`，`c_accept` 不得吞掉 `--phase`。**写结果时若 `artifacts[]` 包含自身结果文件的路径或摘要，Harness 必须退出 1（结果级自引用拒绝）。**
  Closes: GAP-2, GAP-6
  Parallelization: Wave 2 | Blocked by: T-02, T-05 | Blocks: T-07, T-08, T-09
  References: 计划 C `:115-152,154-165`；手册 `:483-526`（三态/退出码/证据）；`scripts/acceptance/real_environment.py:25-36`（既有 `acp.acceptance.harness.v1` 与 0/1/2/3，**新 Harness 改用 `acp2.acceptance.v1`，不复用旧 schema 名**）；`docs/reference/next-change/examples/acp_mcp_stdio_probe.py`（只读探针，不可当 Harness）。
  Acceptance criteria: `"$ACP2_PYTHON" scripts/acceptance/acp2_experience.py --help` 退出 0 且 `stdout` 不含任何连接/模型初始化日志；`--scenario does-not-exist --out /tmp/x --timeout 60` 退出 3；`--scenario baseline --out /tmp/x --timeout 60 --dry-run --manifest <sample>` 退出 0 且结果文件 `result=NOT_VERIFIED`；`"$ACP2_PYTHON" -m py_compile scripts/acceptance/acp2_experience.py` 退出 0；脚本内 36 个场景 ID 与计划 C §3.3 逐字一致（用 `python -c` 比对列表）；公共 phase 枚举来自 B01 注册表（版本+digest）且 C 不新增值；`--scenario baseline --mode live --out /tmp/x --timeout 60` 退出 3、`--scenario evidence-browser-live --mode live --dry-run --out /tmp/x --timeout 60` 退出 0；`--subcase` 白名单为 `cross-domain-live:{nmr,thermo,selectivity}` 与 `research-loop-live:{control,search}`；其他场景/子行组合退出 3；`test -f docs/reference/next-change/acp2-contracts/experience/harness-contract.md`；用 B01 的 `acceptance-result.schema.json` 唯一校验器验证 C 的合法/非法样例（缺 `candidate_revision`、平台标识写错位置、双份平台标识、fixture 冒充 live），并与 A/B 样例同批通过后才可冻结；结果必须含 `interpreter`、`product_import`、`harness_path`、`test_revision`，且 `product_import` 与实际导入路径一致、在当前模式下解析到 `$ACP2_C_SOURCE` 或 `$ACP2_INTEGRATION_SOURCE`。
  QA scenarios: happy = 上述六条；failure = 构造缺 `candidate_id` 的 manifest，断言退出 2 且 `reason` 含 `candidate_id`；把某场景实现改为抛异常，断言退出 1 而非 2；在 manifest 写入 `api_key`，断言结果文件出现 `***` 且原值不出现；构造一份 artifact SHA-256 不匹配的 receipt，断言 `--verify-receipt` 退出 1。Evidence `$ACP2_EVIDENCE/C/harness-selftest/`。
  Commit: Y | `feat(acp2-c): add experience acceptance harness with frozen scenarios`
  Recommended task executor category: deep-high - 退出码/三态/脱敏/幂等证据是本案的正确性核心，靠不变量而非样例判断。

- [ ] 7. T-07 / C03b：Harness 自检测试、四个 fixture 场景实跑与全场景 dry-run
  What to do / Must NOT do: 新建 `tests/test_acp2_experience_harness.py`（pytest，裸 assert，`unittest.mock.patch`），覆盖：`help` 无副作用；三种互斥模式的参数解析（场景模式可带 `--phase`/`--mode`/`--subcase`；`--verify-receipt` 与 `--check-doc-placeholders` 不需要场景参数）；`dry-run` 只写 `NOT_VERIFIED`；未知场景 3；缺前置 2；已执行断言失败 1；零断言不得 PASS；`evidence_kind=live` 时检出 mock transport/FakeBackend/假模型必须 `FAIL`；脱敏；总超时；append-only；`--verify-receipt` 的正负样例。再对全部 36 个场景各跑一次 `--dry-run`，确认无进程/模型/平台副作用。**本 todo 还必须实跑五个 P0 fixture/环境场景并留下真实结果**：`baseline`（复跑 T-01 的基线命令）、`contracts`（校验全部 `$ref`、Schema 版本与正负输入）、`workspace`（`pnpm --dir rph build/test/check`）、`harness-selftest`（自检套件）、`node-dsh-install`（复核 T-04 的 Node/pnpm/DSH 安装），五者都必须为 `PASS`；并把 `--help` 输出与结果 Schema 发给 B（`requests/B-req-02.md`）。**Must NOT**：用 `--collect-only` 或空参数化代替真实用例；把 `dry-run` 的 0 记成场景 PASS；在上述场景任一非 PASS 时放行后续 `c_accept` 证据行。
  Closes: GAP-6
  Parallelization: Wave 3 | Blocked by: T-06 | Blocks: 所有 `c_accept` 证据行
  References: 计划 C `:115-124,174,183,192,196-203,210`；`tests/AGENTS.md:39-47`；`scripts/acceptance/real_environment.py:45-57`（缺前置判定写法）；`scripts/acceptance/acp2_experience.py`（T-06 产物）。
  Acceptance criteria: `"$ACP2_PYTHON" -m pytest tests/test_acp2_experience_harness.py -q` 全绿且收集数 ≥16（仅零执行保护；通过条件是命名矩阵：help 无副作用、三模式解析、dry-run、未知场景、缺前置、断言失败、零断言、live mock、脱敏、超时、append-only、receipt 正负、五个 P0 场景）；对 36 个场景逐个 `--dry-run --out "$ACP2_EVIDENCE/C/harness-selftest/dryrun/<id>" --timeout 60` 全部退出 0（含未实现场景，验证 dry-run 优先级）；非 dry-run 的未实现场景返回 2、未知场景返回 3 的自检用例存在；`--mode live` 仅对注册为 live 的场景合法、对其他场景退出 3 的正负自检用例存在；`--subcase` 与场景/模式组合的白名单正负用例存在；`c_accept baseline 300`、`c_accept contracts 300`、`c_accept workspace 300`、`c_accept harness-selftest 600`、`c_accept node-dsh-install 1200` 五条命令均退出 0 且结果 `result=PASS`；`c_accept baseline 300` 在 `$ACP2_EVIDENCE/C/baseline/<run_id>/` 下生成新目录，重复调用生成不同 run_id，固定 `ACP2_RUN_ID` 重跑时因目录非空退出 3。
  QA scenarios: happy = pytest + 36 次 dry-run + 五个 P0 场景 PASS；failure = 新增一个"伪装 live"的测试夹具（mock transport 但 `evidence_kind=live`），断言 Harness 返回 `FAIL`；把 `contracts` 场景的某个 `$ref` 指向不存在文件，断言该场景 `FAIL` 且后续放行检查被阻断；用 `--verify-receipt` 校验一份 SHA 不匹配的回执，断言退出 1。Evidence `$ACP2_EVIDENCE/C/harness-selftest/`。
  Commit: Y | `test(acp2-c): cover harness modes, live guards and five P0 scenarios`
  Recommended task executor category: unspecified-high - 16+ 断言点 + 五个场景实跑，模式明确。

- [ ] 8. T-08 / C05：安装 Chromium 并验证真实浏览器可运行
  What to do / Must NOT do: `export PLAYWRIGHT_BROWSERS_PATH="$ACP2_TOOLS/playwright-browsers"`；B02a 必须在浏览器验收环境安装 `.[browser]` extra（Playwright 不在默认 dev 依赖中）并只交 browser venv/extra，**不以 Chromium 启动为验收**；C 用 `$ACP2_BROWSER_PYTHON -m playwright install chromium` 并完成真实启动，启动回执进入 H2 汇总；脚本启动 headless Chromium、新建 page、`set_content` 一个带标题的空白页、断言标题、截图并断言文件非空；记录浏览器版本/路径/启动日志到 `experience/browser.md`；并确认用户目录、工作目录与缓存均落在 `$ACP2_TOOLS`/`$ACP2_DATA_ROOT`，不落生产位置。缺系统库时只提交包名与报错给 B（`requests/B-req-03.md`），不自行 `install-deps` 或升级系统。**Must NOT**：`import playwright` 成功即算就绪；用 DOM stub 或 `set_content` 冒充真实页面验收（本步只证明环境）；把 Playwright 装进 C 开发 venv 冒充浏览器环境。
  Closes: GAP-4
  Parallelization: Wave 4 | Blocked by: T-06, T-07 | Blocks: T-17, T-21, T-31, T-38
  References: 手册 `:278-295`（ENV-07）；计划 C `:214-221`；`pyproject.toml`（browser extra）；`scripts/acceptance/real_environment.py:52-57`。
  Acceptance criteria: `"$ACP2_BROWSER_PYTHON" -m playwright install chromium` 退出 0；`c_accept browser-install 600` 为 PASS 且结果含 `browser_version`、`pages=1`、`screenshot_bytes>0`、`interpreter=$ACP2_BROWSER_PYTHON`。
  QA scenarios: happy = 上述两条；failure = 把 `PLAYWRIGHT_BROWSERS_PATH` 指向空目录重跑，断言结果为 `NOT_VERIFIED` 或 `FAIL`（不得 PASS）。Evidence `$ACP2_EVIDENCE/C/browser-install/`。
  Commit: Y | `chore(acp2-c): install and verify isolated chromium`
  Recommended task executor category: quick - 单一路径的环境安装与一条断言。

- [ ] 9. T-09 / C06a：实现 Bridge 科研语义映射与契约测试
  What to do / Must NOT do: 在 `rph/packages/bridge/src/` 实现 `discover/validate/submit/status/evidence`：底层只调用 DSH 已注册的官方 MCP 工具；动态查 Capability，不把全部能力展开成顶层模型工具；分离 raw 工具名与模型可见名；工具失败保留 `isError` 与结构化错误；长任务只保存平台引用；大工件只回引用/摘要，读取走 B 的 prepare/read；重复调用不得改变输入科学参数。**P2 研究资源/审批调用映射**（与 `tool-semantics.md` 同步冻结）：Proposal/ResearchRecord create/read/CAS → B Data 类型化资源端点；approval request/query → Runtime；approve/reject/revoke → Runtime 且必须绑定真实批准者身份；waiting/events → Runtime 持久 cursor 或有界查询；research resume → C 研究语义 + B 资源事实。**直接消费 B03 已冻结的版本化 MCP 扩展**；不在本计划添加受限 HTTP 客户端，也不保留“二选一”。为每个 P2 操作登记 `tool/version`、`ControllerClient` 方法、应用用例、权限、错误码与实际接线任务；P1 的“恰好八工具”只限定该契约版本，P2 新增能力按版本化 allowlist 扩展，不得添加任意 shell 或无类型万能执行接口。若 B03 未交付该表，本行记 `NOT_VERIFIED(mcp_extension)` 并提 `requests/B-req-14.md`，不得回退 HTTP。B18a 预检与 B20a 真实执行必须分别使用显式 `--phase preflight`/`--phase da-submit`，缺失时记 `NOT_VERIFIED(phase_dispatch)`。新增 `rph/test/bridge-contract.test.ts` 覆盖上述映射与六条边界。**Must NOT**：把工具错误包装成成功；自动补全/切换方法、电荷、自旋；自行运行量化 shell；`read` 隐式 `prepare`；按操作类型之外的字段做一刀切必填校验。
  Closes: GAP-2
  Parallelization: Wave 4 | Blocked by: T-02, T-05, T-06, T-07 | Blocks: T-10, T-11
  References: 计划 C `:223-230`；`mcp.py:186-243`（八工具与 required action）；`acp2-contracts/experience/tool-semantics.md`（T-02）；`rph/README.md`（T-05 依赖方向）。
  Acceptance criteria: `pnpm --dir rph build`、`pnpm --dir rph test`、`pnpm --dir rph check` 均退出 0；`c_accept bridge-contract 300` 结果为 PASS 且 `evidence_kind=fixture`（不得关闭 H2）；P2 操作表每行含 `tool/version`、`ControllerClient` 方法、应用用例、权限、错误码与实际接线任务，缺一即失败；B03 未交付该表时本行记 `NOT_VERIFIED(mcp_extension)`，不得回退 HTTP。
  QA scenarios: happy = 三条命令 + 契约快照比对；failure = 构造"工具返回 isError 但 Bridge 返回 ok"的夹具，测试必须失败；再构造 `submit` 后把 method 改成另一值的夹具，测试必须失败。Evidence `$ACP2_EVIDENCE/C/bridge-contract/`。
  Commit: Y | `feat(acp2-c): add research-semantics bridge over official MCP tools`
  Recommended task executor category: deep-low - 语义映射规则已由契约冻结，实现细节可从代码读出。

- [ ] 10. T-10 / C06b：Bridge 边界与负向契约测试
  What to do / Must NOT do: 扩充 `rph/test/bridge-contract.test.ts`：重复调用幂等（同参不产生第二引用）；**按操作类型校验必填字段**——discovery 不要求 task/generation，validate/submit 要求 project+input+幂等键但不得要求结果 generation，status 要求 task/attempt，prepare 要求 task/attempt/resource，read 要求 task/attempt/generation/resource；raw 名含 `mcp__acp__` 前缀必须拒绝；`read` 未 `prepare` 返回结构化"未准备"而非触发准备；超大工件只回摘要；结果中不得回传凭据或绝对远端路径。**Must NOT**：用宽松断言（如只断言"不抛异常"）；把所有操作都要求 project/attempt/generation。
  Closes: GAP-2
  Parallelization: Wave 5 | Blocked by: T-09 | Blocks: H2 汇总（bridge-contract negative 子行）
  References: 计划 C `:229-230`；`mcp.py:897-963`（`tools/list`/`unknown_tool` 行为）；`tool-semantics.md`。
  Acceptance criteria: `pnpm --dir rph test` 全绿且新增用例 ≥8；按操作类型的必填矩阵逐行有正负样例；`node rph/scripts/check-boundaries.mjs` 退出 0。
  QA scenarios: happy = 上述两条；failure = 逐个断言"去掉守卫后测试必须变红"（对 6 个守卫做 mutant 检查，至少各 1 次）。Evidence `$ACP2_EVIDENCE/C/bridge-contract/negative/`。
  Commit: Y | `test(acp2-c): harden bridge boundary and negative contracts`
  Recommended task executor category: unspecified-high - 多守卫 mutant 验证，工作量大但判断标准明确。

- [ ] 11. T-11 / C07：真实 MCP 进程握手与无副作用检查（H2，依赖 B05）
  What to do / Must NOT do: 等 B 交付正式 `mcp-launch.json` 与唯一隔离 Controller。**消费顺序按 B04→B05→B06**：先确认 B04 的 Controller/HTTP/CLI 与最小 capabilities 可读，再验 B05 的 stdio/launch/握手；若 B 仍按旧顺序把正式入口放在 B04 之后才交付，本行记 `NOT_VERIFIED` 并提 `B-req` 要求 B 修正顺序，不在 C 侧绕过。校验 launch 的 `command/args/cwd/env` 与测试实例一致；记录 Controller PID 与任务计数；先跑既有 stdlib 探针，再用冻结官方客户端跑 `initialize/initialized/tools/list/capabilities`；记录实际协商版本与八工具的完整 input/output Schema 快照；做一次**无共同支持版本**的负测（版本字符串不同但双方有交集属于正常协商，不算失败）；关闭 MCP 子进程后核对 Controller 仍存活且任务数不变。**Must NOT**：绕过 initialize；改服务端协议常量；在 C 包内修服务协议；探测进程启动第二 Controller 或新增任务；把版本字符串不相等一律判为不兼容。
  Closes: GAP-4
  Parallelization: Wave 6 | Blocked by: T-07, T-09, B05 | Blocks: T-12, T-13, T-14
  References: 计划 C `:232-239`；手册 `:301-322`（T1 通过条件）；`docs/reference/next-change/examples/acp_mcp_stdio_probe.py`；`mcp.py:120-122`（协议锁 `2025-06-18`）。
  Acceptance criteria: 探针命令退出 0 且输出目录含协议收发/stderr/退出码；`c_accept mcp-live 300` 为 PASS；**版本协商矩阵逐行覆盖**：①客户端请求受支持版本→服务端返回同版本；②客户端首选版本不同但仍支持服务端版本→正常协商并记录实际版本；③无共同支持版本→明确失败且不执行后续工具；④未完成 initialize 即调用业务工具→按生命周期拒绝；⑤notification/EOF/断线→不伪造普通响应、不等于取消已提交计算。`negotiated_protocol` 记录实际值（不要求等于 `2025-06-18`）；任务计数前后相等。
  QA scenarios: happy = 探针 + `c_accept`；failure = 用无共同支持版本调探针，断言明确失败且不新增任务；把 launch 的 `cwd` 指向不存在目录，断言启动失败而非静默回退。Evidence `$ACP2_EVIDENCE/C/mcp-live/`。
  Commit: Y | `test(acp2-c): record real MCP handshake and tool schemas`
  Recommended task executor category: unspecified-low - 按手册逐条执行并归档。

- [ ] 12. T-12 / C08：接入 DSH 官方 MCP 插件并验证工具发现
  What to do / Must NOT do: 在 `rph/packages/dsh-bundle/profiles/` 建 Web/headless 专用 overlay：`insert` 一个 Cordis entry，插件名 `@deepseek-ai/dsh-mcp-client`，`command/args/cwd/env` 与 T-11 的 launch **完全相同**；`--dump-config` 保存脱敏副本；启动 Web 只绑定 loopback；读取官方客户端工具注册结果，验证**当前契约版本**的 allowlist（P1 为 8 个 `mcp__acp__*`；P2 扩展按版本化 allowlist 记录，不把 P1 数量当永久上限）。**同时冻结模型可见工具全集快照**：保存 Web 与 headless 各自实际注册的工具名、来源插件与权限；断言除 `mcp__acp__*` 与 DSH 非执行类工具外，不存在 shell/exec/SSH/任意命令工具，也不存在第二个可执行 QC 的入口。强制坏 command 与不兼容协议，确认插件显式失败。**Must NOT**：把 patch 当深合并；把"能聊天/进程仍在"当兼容；把含凭据的展开配置归档；用代码扫描代替真实工具注册快照。
  Closes: GAP-4
  Parallelization: Wave 6 | Blocked by: T-04, T-09, T-11 | Blocks: T-13, T-14
  References: 计划 C `:241-248`；手册 `:326-368`（§5.1 overlay/insert/脱敏）；DSH `apps/cli/config/examples/mcp-memory/mcp-reference-memory.cordis.yml`（手册引用）。
  Acceptance criteria: `dsh_acceptance --profile web --patch ... --dump-config` 退出 0 且脱敏副本不含凭据；`c_accept dsh-discovery-live 300` 为 PASS；工具注册快照含当前契约版本的 allowlist（P1 为 8 个）并列出 P2 扩展；坏 command 场景结果为 `FAIL`。
  QA scenarios: happy = 上述三条；failure = 把插件名改成不存在包，断言 `failOnStartupError` 生效且场景 FAIL；把 launch `env` 中的 controller/identity/config 删掉（而不是删 `ACP_RUN_ROOT`），断言工具发现因缺少必需连接配置失败，且适配器不会回落到本地 run root。Evidence `$ACP2_EVIDENCE/C/dsh-discovery-live/`。
  Commit: Y | `feat(acp2-c): wire official DSH MCP plugin profiles`
  Recommended task executor category: deep-low - overlay/insert 语义需读 DSH 官方示例，但可完全由证据判定。

- [ ] 13. T-13 / C09：配置真实模型、一次工具调用与费用边界
  What to do / Must NOT do: **子交付：T-13a 代理实现/自测（受控上游，不消耗真实模型请求）；T-13b 真实 Web/headless 贯通（一次真实模型请求）**。在专用 DSH Web Settings → Models 配置已批准 Provider/model；headless 单独配置相同路由；**模型调用前置拦截**：由 C 实现并自测 `scripts/acceptance/acp2_experience_budget_proxy.py`（OpenAI 兼容 base URL，DSH 指向该代理）。代理必须实现：启动/就绪/停止；**预算身份独立于 run_id**——使用持久 `batch_id`/`scenario_budget_id`，重试、重启、换 run_id 均继承剩余额度；验收批次有累计上限（全部场景与重跑合计）；请求前预留额度；单次输出上限；流式与非流式均采集实际 usage；中断/上游错误按明确规则结算，缺 usage 不伪造精确值；Web 与 headless 的实际路由都经过代理。用受控上游验证计数、边界和错误路径，**不额外消耗 12 次真实模型请求**。**发行边界**：若代理不随 RPH 包发行，发行说明不得宣称产品具备同等预算 enforcement。`usage` 从代理日志读取，不得只靠事后统计。提示词只允许"调用 `mcp__acp__acp_capabilities`，不提交计算"；断言**实际模型请求、实际工具调用、实际工具返回**三者都存在；保存 provider/model、推理设置、token/费用/调用次数；给错误引用让模型看到真实工具错误；创建无凭据/错误模型 ID 负测；**运行时反例**：让提示词或工具返回要求"直接执行 ORCA/xTB 或开 shell"，断言模型可见工具集中没有可绕过平台的执行入口、没有新增科学进程。若 DSH 版本无法指向自定义 base URL，本行记 `NOT_VERIFIED` 并提 `B-req`，不得用事后统计冒充前置拦截。**Must NOT**：用假模型/静态回放/模型自述"已调用"通过；达到调用或费用上限继续；错误模型回退到未批准 Provider；改变 run_id 绕过累计额度。
  Closes: GAP-4
  Parallelization: Wave 6 | Blocked by: T-12 | Blocks: T-14, T-18
  References: 计划 C `:250-257`；手册 `:380-418`（§5.3/§5.4）；根 `AGENTS.md` ANTI #28（超时不得泄露 raw AbortError）。
  Acceptance criteria: `c_accept model-live 300` 为 PASS；结果含 `provider`、`model_id`、`usage.total_tokens`、`tool_calls>=1`、`tool_results>=1`、`batch_id`/`scenario_budget_id`；错误引用子场景显示 `isError=true`；工具全集快照与 T-12 一致且不含执行/SSH 入口；预算代理自测通过（单请求输出超限被拒、流式/非流式 usage 采集、上游中断结算、重启后继承剩余额度、换 run_id 不重置累计额度、Web/headless 路由），日志存在且第 13 次请求被拒（`usage.model_calls<=12`）；T-13a 与 T-13b 证据分列；反例子场景前后科学进程数与任务数不变。
  QA scenarios: happy = `c_accept` + 一次 headless 复跑；failure = 用未批准 model ID 重跑，断言结果为 `FAIL`/`NOT_VERIFIED` 且不发生 Provider 回退；把预算上限设为 0，断言立即停止而非继续调用。Evidence `$ACP2_EVIDENCE/C/model-live/`。
  Commit: Y | `test(acp2-c): prove one real model tool call with hard budget`
  Recommended task executor category: deep-high - 代理含协议/并发/流式中断/持久化/累计预算与发行边界，不是配置步骤。

- [ ] 14. T-14 / C00–C09 收口：H2 C 侧回执与环境复用固化
  What to do / Must NOT do: 生成 `experience/handoffs/H2.json`：逐场景记录 `result`/`scope`/`reason`/`command`（argv 数组）/`cwd`/`exit_code`/`candidate{server_revision,client_revision,harness_revision}`/`started_at`/`finished_at`/`artifacts[]`（含 `size_bytes` 与 SHA-256）/`usage`，字段值必须来自对应场景结果文件，禁止手写；把 `acceptance-env-c.sh` 的变量清单（不含凭据）与重开终端复核结果附上；向 B 提交 `requests/B-req-04.md`（纳管 C 的 Harness 结果与 launch/DSH 版本）。**Must NOT**：把未执行行写成 PASS；只写"场景键存在"就算收口；把 C 的回执当 H2 总签收（总签收属 B）。
  Closes: GAP-6
  Parallelization: Wave 6 | Blocked by: T-04, T-07, T-08, T-09, T-11, T-12, T-13 | Blocks: H3 车道开启
  References: 计划 C `:49-62,250-257`；总索引 `:145-154,206-213`（H2 关闭条件与回执字段）；手册 `:296-299`（ENV-09）。
  Acceptance criteria: `"$ACP2_PYTHON" -m json.tool experience/handoffs/H2.json` 退出 0；P0 十个场景键齐全且**全部为 `result=PASS`**（`baseline`/`contracts`/`workspace`/`harness-selftest`/`node-dsh-install`/`browser-install`/`bridge-contract`/`mcp-live`/`dsh-discovery-live`/`model-live`；`bridge-contract` 必须包含 T-10 的 negative 子行，缺该子行不得 PASS），缺任一 PASS 则 H2 的 C 侧不关闭；`"$ACP2_PYTHON" scripts/acceptance/acp2_experience.py --verify-receipt experience/handoffs/H2.json` 退出 0（逐行字段与 SHA-256 匹配）；新开 shell `source "$ACP2_DATA_ROOT/acceptance-env-c.sh"` 后 `command -v node pnpm python` 与记录一致；`test -n "$ACP2_C_PYTHON" -a -n "$ACP2_INTEGRATION_PYTHON" -a -n "$ACP2_BROWSER_PYTHON"` 且三条角色导入来源断言（同 T-04：`acp.__file__` 分别位于 `$ACP2_C_SOURCE`/`$ACP2_INTEGRATION_SOURCE`，`playwright.__file__` 位于 `sys.prefix`）退出 0；H2 每条记录 `interpreter`、`product_import`、`harness_path`、`test_revision`，缺任一不得 PASS。
  QA scenarios: happy = 上述四条 + 回执 diff 检查；failure = 删除某场景键后 `--verify-receipt` 必须非 0；把某行改为 `PASS` 但 evidence 文件不存在，校验必须拒绝；把 `exit_code` 改成 0 而结果文件为 1，校验必须拒绝。Evidence `$ACP2_EVIDENCE/C/h2/`。
  Commit: Y | `docs(acp2-c): publish H2 experience receipt and env reuse`
  Recommended task executor category: writing - 汇总与格式校验为主。

- [ ] 15. T-15 / C10a：新增 science/research 查询模块（Client 层）
  What to do / Must NOT do: 新建 `frontend/js/client/science-query.js` 与 `research-query.js`，沿用现有 IIFE + `window.ACPClient` 风格与注入适配器（`init({v1,v2})`），**不得**自己 `fetch` 或拼 URL 前缀；所有共享快照写入只经 `ACPClient.store.query`，key 必须含 project/权限上下文、资源标识与版本；复用 `ACPClient.transport` 的超时/取消语义；新增字段经 `frontend/js/workbench/contracts/` 校验。新增 `frontend/js/module_tests/acp2_client.test.js`，用 `loadScript` 注入 stub 适配器，覆盖 key 组成、跨项目隔离、迟到响应丢弃、错误透传。**Must NOT**：各面板自行轮询；复用跨项目 key；让旧 generation 覆盖新数据；让新选择被旧响应覆盖。
  Closes: GAP-3
  Parallelization: Wave 4 | Blocked by: T-02, T-03, T-07 | Blocks: T-16, T-17, T-21
  References: `frontend/js/client/resource-store.js:72-140`（query 语义/serial 丢弃）；`frontend/js/client/transport.js:50-137`；`frontend/js/client/task-view-query.js:21-59`（注入与 store 用法范本）；`frontend/ACP_Workbench_v2.html:1222-1231`（脚本顺序）；`acp2-contracts/experience/view-model.schema.json`（T-02）。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_client` 退出 0 且 `N passed`（N≥10）；`c_accept client-fixtures 300` 为 PASS；`grep -R "fetch(" frontend/js/client/science-query.js frontend/js/client/research-query.js` 零命中。
  QA scenarios: happy = 上述三条；failure = 在测试里构造两个不同 project 的同名资源，断言 store key 不同且互不覆盖；再注入一个迟到响应，断言被 `StaleDiscard` 丢弃。Evidence `$ACP2_EVIDENCE/C/client-fixtures/`。
  Commit: Y | `feat(acp2-c): add science and research client query modules`
  Recommended task executor category: unspecified-high - 新查询层 + 契约校验 + 测试，多文件但不跨包。

- [ ] 16. T-16 / C10b：事件补全与工件 availability 消费
  What to do / Must NOT do: 在既有 `ACPClient.events` 之上做"失效/合并"而非"信任重放"：SSE 只用于标记受版本控制的数据失效；`after_seq` 不是持久重放（`event-stream.js:14-25`），因此刷新一律走权威查询；实现工件四态 `unprepared/preparing/readable/failed` 的展示映射；旧 revision 事件不得覆盖新快照。新增 `frontend/js/module_tests/acp2_client_events.test.js`。**Must NOT**：伪造 replay；把 SSE 当任务权威；让 GET 触发 transfer/compute。
  Closes: GAP-3
  Parallelization: Wave 5 | Blocked by: T-15 | Blocks: T-17
  References: `frontend/js/client/event-stream.js:14-25,59-90`；计划 C `:264-268`；`acp2-contracts/experience/view-model.schema.json`。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_client_events` 退出 0 且 N≥8；`c_accept client-fixtures 300` 仍为 PASS（含事件子场景）。
  QA scenarios: happy = 两条命令；failure = 构造"旧 revision 事件后到"的序列，断言 store 保留新数据；构造 `GET` 的 loader 内出现 prepare 调用，断言测试失败。Evidence `$ACP2_EVIDENCE/C/client-fixtures/events/`。
  Commit: Y | `feat(acp2-c): consume events as invalidation with authoritative refetch`
  Recommended task executor category: deep-low - 需要理解 cursor 语义与竞态，但边界由代码注释明确。

- [ ] 17. T-17 / C11：无模型 Manual Research 入口与只读任务视图
  What to do / Must NOT do: 新建 `frontend/js/workbench/features/science-question.js`：先选已支持问题与输入结构，必要参数显示可解释默认值（来自 A 的 Capability 描述），经 Client 做 discover/preflight/submit，**不得**在 Feature 内拼第二套 payload；复用既有 wizard 上传/预览/提交；任务详情引用同一 task/attempt，展示结构、原始工件与限制；在 `frontend/ACP_Workbench_v2.html` 的既有脚本块内、`bootstrap.js` 之前注册新脚本并挂载入口，不新增第二个任务系统页面。新增 `frontend/js/module_tests/acp2_manual.test.js`。**B18a 的 P2 最小往返只消费本表单/fixture；若 B18a 把 T-38 的完整 Schema 表单作为前置，记 `NOT_VERIFIED(producer_mismatch)` 并提 `requests/B-req-14.md`。**Must NOT**：未配置模型就阻断手工任务；隐藏电荷/自旋等必要歧义；预检失败仍允许提交；启用退役工作流默认值（ANTI #20）。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: T-08, T-16 | Blocks: T-18, T-21, T-23
  References: 计划 C `:270-277`；`frontend/ACP_Workbench_v2.html:1211-1257`；`frontend/js/workbench/features/wizard.js`（既有流程）；`frontend/js/client/wizard-endpoints.js`；根 `AGENTS.md` ANTI #20/#22。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_manual` 退出 0 且 N≥8；`c_accept manual-browser-fixtures 300` 为 PASS 且页面标注"演示数据"；`node frontend/js/module_tests/run_all.js` 全量退出 0（无回归）。
  QA scenarios: happy = 三条命令；failure = 关闭模型配置后重跑手工路径，断言仍可提交到 fixture；构造 preflight 失败夹具，断言提交按钮被禁用且无网络提交。Evidence `$ACP2_EVIDENCE/C/manual-browser-fixtures/`。
  Commit: Y | `feat(acp2-c): add model-free manual research entry`
  Recommended task executor category: visual-engineering - 页面挂载与交互状态为主。

- [ ] 18. T-18 / C12：真实 DSH→MCP→Local singlepoint（H3）
  What to do / Must NOT do: 引用 B 已跑过的 Local 科学基线报告/digest，并确认本轮使用的是 **B 的 P1 测试候选**（在 B09 之后、B10 之前冻结；若 B 尚未提供，本行记 `NOT_VERIFIED` 并保留引用）。为本场景生成**独立幂等键**与 submit fixture；由真实模型按"发现→预检→明确一次审批→提交→状态"执行；`submit` 立即返回持久引用，等待由平台接管；完成后**显式** `prepare`→查询 operation→`read`；核对 release/attempt/receipt/manifest/digest 与 A 的科学判据；把该唯一任务引用保存给 T-21。结果文档分别记录 `candidate.server_revision`/`client_revision`/`harness_revision`。**Must NOT**：无原生 QC 输出；假 Backend；工具返回 completed 但无平台 receipt/manifest；预检就启动计算；提交超时后自动换幂等键重提。
  Closes: GAP-4
  Parallelization: Wave 7 | Blocked by: T-13, T-14, T-17 | Blocks: T-19, T-20, T-21, T-22
  References: 计划 C `:279-286`；手册 `:420-459`（T4/§6）；`acp2-contracts/experience/tool-semantics.md`；`docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json`（B03 交付后）。
  Acceptance criteria: `c_accept dsh-local-singlepoint-live 1800` 为 PASS；结果含 `job_id`、`operation_id`、`attempt`、`release_id`、`manifest_generation`、`artifact_sha256`；`candidate.server_revision`/`client_revision`/`harness_revision` 三者齐全；`evidence_kind=live`。
  QA scenarios: happy = 一次真实提交 + 显式 prepare/read；failure = 把幂等键改成新值重提同 payload，断言产生新任务（证明确实按 key 而非按内容去重），并记录该实验独立于正式行。Evidence `$ACP2_EVIDENCE/C/dsh-local-singlepoint-live/`。
  Commit: Y | `test(acp2-c): run real DSH to local singlepoint chain`
  Recommended task executor category: unspecified-high - 真实链路 + 两类引用核对，涉及多系统。

- [ ] 19. T-19 / C13：真实 DSH→MCP→LSF singlepoint（H3）
  What to do / Must NOT do: timeout **不是硬编码**：优先使用 B07 发布的 `lsf_timeout_seconds`；若未发布，则从 B07 `fixture-runs.json` 读取 `lsf_singlepoint` 的窗口四参数（顶层 `max_queue_wait_seconds`、`max_run_seconds`、`max_prepare_seconds`、`cleanup_seconds`，或 `budget.*` 同义字段），按 B 的 `margin_seconds` 加余量（B 未给 margin 时用 10%），导出为 `ACP2_LSF_SINGLEPOINT_TIMEOUT`。多格矩阵须声明串行/并行及总场景 timeout，不得每格各写独立期限。若 B07 尚未发布窗口，创建 `requests/B-req-08.md` 要求补字段并记 `NOT_VERIFIED(queue_window)`，不得用固定 2400/90000 代替，也不得永久静默等待。本场景只消费 `completed_science_run` TaskRef；control/cancel 使用 T-22 的独立 fixture，不复用本任务。复用 A 同类科学 fixture，明确本场景是**新任务、新幂等键**；真实 DSH 经相同平台入口提交 LSF；保存实际 `bsub/bjobs`、`submission_id`、`release_id` 与 scheduler markers；经平台查询完成后显式准备/拉取工件；把数据/receipt 摘要与远端产物互核；经同一 Client 在 Workbench 查看（不读 `record.work_dir`）。**Must NOT**：mock Paramiko；只生成 bsub 文本；把 `pending_fetch` 当已就绪；GET 偷偷拉取；attempt 与远端 receipt 不一致仍 PASS；用 Local 结果代替本行；到期仍有活动远端任务却只写 NOT_VERIFIED 而不保留 submission 引用对账。
  Closes: GAP-4
  Parallelization: Wave 7 | Blocked by: T-18 | Blocks: T-20, T-21
  References: 计划 C `:288-295`；手册 `:420-459,463-476`（§6.1/§6.2）；`src/acp/scheduler/remote/AGENTS.md`；根 `AGENTS.md` ANTI #11/#24/#27。
  Acceptance criteria: `c_accept dsh-lsf-singlepoint-live "$ACP2_LSF_SINGLEPOINT_TIMEOUT"` 为 PASS；`ACP2_LSF_SINGLEPOINT_TIMEOUT` 由 B07 的派生值或四段+margin 规则计算且不得硬编码；若 B07 缺字段，`requests/B-req-08.md` 存在且本行记 `NOT_VERIFIED(queue_window)`；结果含远端 `submission_id`、`release_id`、`markers` 三个标记文件、`artifact_sha256`、`usage.queue_seconds/run_seconds/prepare_seconds/cleanup_seconds`；缺环境时 `NOT_VERIFIED` 且 H3 不关闭。
  QA scenarios: happy = 真实提交→观测→准备→读取→核对；failure = 把 `pending_fetch` 状态的引用强行 read，断言返回"未准备"而非静默拉取。Evidence `$ACP2_EVIDENCE/C/dsh-lsf-singlepoint-live/`。
  Commit: Y | `test(acp2-c): run real DSH to LSF singlepoint chain`
  Recommended task executor category: deep-low - 远端链路风险高但步骤与断言由手册固定。

- [ ] 20. T-20 / C14：optimize/frequency 分能力×backend 实测
  What to do / Must NOT do: 为两项能力分别生成 request/schema 快照与独立任务 key；经真实 DSH 发现/预检/提交；保留优化几何/收敛与频率模式/单位等独立结果；逐项核对 method/state/provenance；按 A/B 冻结的能力×backend 矩阵，**每个声明支持的格子单独判定**；未支持格子记 `DEFERRED`。timeout 使用 B07 发布的 `lsf_timeout_seconds` 或同一四段+`margin_seconds` 规则（与 T-19 相同，不得各写一套），导出 `ACP2_LSF_OPTFREQ_TIMEOUT`；多格矩阵声明串行/并行与总场景 timeout；Local 格子使用 `max_run_seconds` 内的独立上限。**Must NOT**：仅 QC 正常退出就判优化收敛；没有频率就判振动完成；模型推断替代 A 验证器；两个能力复用同一份原生输出；用 singlepoint PASS 推导本行。
  Closes: GAP-4
  Parallelization: Wave 7 | Blocked by: T-18, T-19 | Blocks: —
  References: 计划 C `:297-304`；手册 `:445`；`src/cccp/calculation/tasks/`（optimize/frequency 任务核心）；根 `AGENTS.md` 四层架构。
  Acceptance criteria: `c_accept dsh-optfreq-live "$ACP2_LSF_OPTFREQ_TIMEOUT"` 为 PASS；`ACP2_LSF_OPTFREQ_TIMEOUT` 由 B07 的 LSF 窗口四参数计算（不得硬编码），Local 格子使用 `max_run_seconds` 内的独立上限；报告含独立子行 `optimize/local`、`optimize/lsf`、`frequency/local`、`frequency/lsf`，每行有自己的结果与证据路径；`usage.queue_seconds/run_seconds/prepare_seconds/cleanup_seconds` 齐全。
  QA scenarios: happy = 四子行中属于本轮 IN_SCOPE 的逐行 PASS；failure = 人为把 frequency 结果指向 optimize 的工单，断言子行判定失败。Evidence `$ACP2_EVIDENCE/C/dsh-optfreq-live/`。
  Commit: Y | `test(acp2-c): verify optimize and frequency per backend matrix`
  Recommended task executor category: unspecified-low - 按矩阵逐格执行与记录。

- [ ] 21. T-21 / C15：真实浏览器核对三入口查看同一任务
  What to do / Must NOT do: 读取 T-18/T-19 已有的任务引用，**禁止新提交**；**拆成两个可独立验收的子结果**：`same-task-browser-local` 只用 T-18 的 Local TaskRef，`same-task-browser-lsf` 只在 T-19 PASS 时使用其 LSF TaskRef；Local 子结果不得等待 LSF 或 T-19。CLI query、HTTP/Workbench、MCP status 分别观察同一 task/attempt；Playwright 真实访问默认页面，选择测试项目与精确任务；比较同一 revision 的投影或最终状态/attempt/manifest generation/digest；查看结构/工件/限制，切换任务再回来；记录 network、console error、DOM 断言与截图；切换权限视图后检查缓存隔离。**Must NOT**：用 CLI 再提交冒充同任务；只比名称/输入不比 ID；把合法状态推进误判为分叉；用页面截图替代平台回执；让 Local 子结果因 LSF 缺失而无法签收。
  Closes: GAP-4
  Parallelization: Wave 7 | Blocked by: T-08, T-15, T-17, T-18 | Blocks: T-22
  References: 计划 C `:306-313`；手册 `:447-459`；`frontend/js/client/task-view-query.js`；`frontend/js/workbench/controllers/task_session_controller.js`。
  Acceptance criteria: `c_accept same-task-browser-live 600` 为 PASS；报告含独立子行 `same-task-browser-local` 与（T-19 PASS 时）`same-task-browser-lsf`；Local 子行三入口同一 `job_id`/`attempt`；LSF 子行缺 T-19 时记 `NOT_VERIFIED` 且 H3 总门不关闭；截图与 network 日志非空；`node frontend/js/module_tests/run_all.js` 全量退出 0。
  QA scenarios: happy = Local 子行 + LSF 子行（若 T-19 PASS）；failure = 用另一任务的 ID 填表，断言比对失败并给出差异字段；模拟 LSF 不可用，断言 Local 子行仍 PASS 而 LSF 子行 NOT_VERIFIED。Evidence `$ACP2_EVIDENCE/C/same-task-browser-live/`。
  Commit: Y | `test(acp2-c): verify three-entry same-task view in real browser`
  Recommended task executor category: visual-engineering - 浏览器交互与页面证据为主。

- [ ] 22. T-22 / C16：连接恢复、幂等重放与 P1 收口
  What to do / Must NOT do: 提交已批准小任务后退出 DSH/MCP 客户端（B 不停 Controller）；重启同 profile 查询保存引用；两个独立 MCP 适配进程经同一 Controller 重放相同幂等键/输入；检查只有 1 个 task/attempt/submission/budget reservation；**同时消费 B08/B09 的共享提交协议与四类崩溃窗口证据**（预留后入库前、入库后启动前、已提交但响应丢失、重启后原 key 重放），并断言没有多余目录/预算预留/本地科学进程/`bsub`，且已接受任务不会永久停在未派发状态；**要求 B08 提供连接边界说明**（同库共享连接事务，或短事务+可恢复分阶段协议）以及两路真实连接提交/预留测试证据，仅凭四窗口结果不足以证明；**control/cancel 使用独立的 `control_cancel_run` TaskRef**，不复用 T-19 的 `completed_science_run`；使 Workbench 断线再恢复并补权威快照；工具超时后按原 key 对账；记录 H3 所有必需行。写 `experience/handoffs/H3.json`，并新增 `docs/reference/next-change/ACP_2.0_RPH_P1_Acceptance_20261020.md`（P1 场景矩阵、同一 task/attempt/digest 对照、未完成项与 scope）。**Must NOT**：用内存共享对象代替跨进程；把断开工具请求当取消科学任务；重复键不同 payload 复用旧任务；只写 H3.json 而漏掉 P1 验收文档；用“没有第二行 job”代替目录/预留/进程/bsub 的完整断言。
  Closes: GAP-4, GAP-6
  Parallelization: Wave 7 | Blocked by: T-18–T-21, B08, B09 | Blocks: H3 收口
  References: 计划 C `:315-322`；手册 `:461-481`（T5）；`acp2-contracts/experience/tool-semantics.md`；`frontend/js/client/event-stream.js:14-25`。
  Acceptance criteria: `c_accept client-recovery-live 1800` 为 PASS；`pnpm --dir rph test` 与 `node frontend/js/module_tests/run_all.js acp2_` 均退出 0；四类崩溃窗口各自有 B 的原始回执并被本场景引用；B08 连接边界说明与两路真实连接提交/预留测试证据存在；`control_cancel_run` 使用独立 TaskRef；重放后目录/预留/本地进程/`bsub` 计数与首次一致且无永久未派发（undispatched）任务；`H3.json` 含 P1 七个场景键且**七项全部 `result=PASS`**（fixture 项 `client-fixtures`/`manual-browser-fixtures` 记录 `evidence_kind=fixture`，其余五项必须 `evidence_kind=live`），且 `"$ACP2_PYTHON" scripts/acceptance/acp2_experience.py --verify-receipt experience/handoffs/H3.json` 退出 0；`test -f docs/reference/next-change/ACP_2.0_RPH_P1_Acceptance_20261020.md`。
  QA scenarios: happy = 四条命令；failure = 同 key 改 payload 重放，断言明确冲突且不产生第二任务；把 H3 中某行 evidence 文件删除，断言 `--verify-receipt` 非 0；把 Controller 重启窗口外的重放记 FAIL 而非 PASS。Evidence `$ACP2_EVIDENCE/C/client-recovery-live/`。
  Commit: Y | `test(acp2-c): prove cross-process replay and client recovery`
  Recommended task executor category: deep-high - 跨进程幂等与恢复的正确性必须由不变量判定，无法从样例推断。

- [ ] 23. T-23 / C17a：接入 QuestionSpec/ReactionSpec 与反应编辑增量
  What to do / Must NOT do: 增量扩展已有 `frontend/js/workbench/features/reaction-builder.js`（**全局函数式旧模块，保持同风格**，不自造新编辑器）与 `science-question.js`：明确反应物/产物/候选步骤；**P2 输入范围先冻结为 MOL/SDF/SMILES + 现有结构上传**；CDXML 默认 `DEFERRED`，仅当 A 交付受支持格式的结构/反应语义且 B 提供受权上传+解析入口时才启用，否则不得声称 ChemDraw 起点已完整交付；创建 `requests/A-req-02.md`/`requests/B-req-09.md` 明确 CDXML owner 与阶段范围。显示原子映射、电荷/自旋、立体、溶剂/温度/标准态、净反应 vs 基元步骤差异；记录人工确认/近似与输入版本；提交前后保持原子标签/引用一致。新增 `frontend/js/module_tests/acp2_question_reaction.test.js`（vm 沙箱注入 `reactionDefinition`、`parseXyzAtoms` 等 stub 全局）。**Must NOT**：在浏览器实现科学解析权威；缺身份信息时静默填补；把总体多步反应硬当一个 TS；把 CDXML 的 `NOT_VERIFIED` 同时当作“ChemDraw 输入已支持”；静默改写原子标签/映射/立体。
  Closes: GAP-5
  Parallelization: Wave 8 | Blocked by: T-17 | Blocks: T-24, T-29, T-34
  References: 计划 C `:326-333`；`frontend/js/workbench/features/reaction-builder.js:6-38`（全局函数与状态）；`frontend/js/module_tests/run_all.js:18-24`（`loadScript`）；`acp2-contracts/experience/requests/A-req-01.md`。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_question_reaction` 退出 0 且 N≥10；`c_accept question-reaction-fixtures 300` 为 PASS；`node frontend/js/module_tests/run_all.js` 全量无回归。
  QA scenarios: happy = 两条命令；failure = 重复 atom map / 缺失产物 / 电荷不守恒三个夹具必须被拒绝；构造"净反应当单 TS"输入，断言产生澄清项而非提交。Evidence `$ACP2_EVIDENCE/C/question-reaction-fixtures/`。
  Commit: Y | `feat(acp2-c): bind reaction and question specs into workbench`
  Recommended task executor category: visual-engineering - 旧全局模块的增量 UI 与交互。

- [ ] 24. T-24 / C17b：化学歧义确认与负向输入
  What to do / Must NOT do: 实现澄清项清单（缺立体/缺条件/映射歧义/多重度未知），必须由用户显式确认或标注近似；把确认结果与输入版本写入提案；CDXML 解析失败必须报错，不得当空结构提交；对"总体反应包含多个基元步骤"给出显式提示。新增负向 fixture 与测试用例。**Must NOT**：自动补全未知立体；把解析失败降级为空结构；把近似标注隐藏。
  Closes: GAP-5
  Parallelization: Wave 8 | Blocked by: T-23 | Blocks: T-31, T-34
  References: 计划 C `:329-333`；`acp2-contracts/experience/proposal.schema.json`；A-02 语义规则（`acp2-contracts/science/semantic-rules.md`，A 交付后）。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_question_reaction` 退出 0 且新增负向用例 ≥5；澄清项在 ViewModel 中有稳定字段名（与 `view-model.schema.json` 一致）。
  QA scenarios: happy = 确认流程可完成并写入提案；failure = 未确认就提交，断言被阻断；CDXML 返回错误，断言显示错误且无提交。Evidence `$ACP2_EVIDENCE/C/question-reaction-fixtures/negative/`。
  Commit: Y | `feat(acp2-c): require explicit chemistry ambiguity confirmation`
  Recommended task executor category: unspecified-high - 交互 + 数据流 + 负向覆盖。

- [ ] 25. T-25 / C18a：ResearchRecord/Proposal 持久化与 CAS
  What to do / Must NOT do: 在 `rph/packages/research/src/` 实现 `record.ts`/`proposal.ts`/`store.ts`/`cas.ts`：字段按 T-03 冻结版；记录存于 B Data 的**版本化项目资源**（B18a 接口），**必须经 T-09 冻结的资源调用映射**（B03 版本化 MCP 扩展），不直接 shell/DB/文件后门；DSH 本地 session 只存引用与工具上下文；实现幂等 round/event key 与 CAS 更新（冲突返回明确错误）。**Must NOT**：自建 SQLite/任务表；用聊天历史当记录权威；覆盖另一 round；绕过 T-09 的受控调用入口。
  Closes: GAP-7
  Parallelization: Wave 8 | Blocked by: T-03, T-23, T-09, B18a 资源接口 | Blocks: T-26, T-29, T-32
  References: 计划 C `:335-342`；总索引 `:127`（B18 先交项目资源 CAS）；`acp2-contracts/experience/research-record.schema.json`。
  Acceptance criteria: `pnpm --dir rph test` 全绿且 record 用例 ≥8；`c_accept research-record-fixtures 300` 为 PASS；冲突用例返回结构化错误码；调用记录显示经 T-09 映射而非直接 DB/文件访问。
  QA scenarios: happy = 两条命令；failure = 两个并发 round 写同一 record，断言只有一个成功、另一个冲突；用旧 revision 覆盖，断言拒绝。Evidence `$ACP2_EVIDENCE/C/research-record-fixtures/`。
  Commit: Y | `feat(acp2-c): persist research records with CAS and resource refs`
  Recommended task executor category: deep-high - 版本化资源 + CAS + 幂等键的边界必须按不变量设计。

- [ ] 26. T-26 / C18b：研究记忆检索、授权与审计
  What to do / Must NOT do: 记忆检索限定项目授权、来源与版本；保留可审计的决策摘要/引用（**不要求也不归档隐藏思维链**）；实现跨项目隔离与越权拒绝；对人工修改保留 before/after 与操作者。**Must NOT**：跨项目泄露；把模型假设写入 `EvidenceReport`；归档 prompt/思维链全文。
  Closes: GAP-7
  Parallelization: Wave 8 | Blocked by: T-25 | Blocks: T-34
  References: 计划 C `:338-342`；`src/acp_services/contracts/access.py`（授权对象）；总索引 `:92`（身份由服务端解析）。
  Acceptance criteria: `pnpm --dir rph test` 新增记忆用例 ≥6；跨项目查询返回空而非他人数据；审计记录含 `actor`、`revision`、`before/after`。
  QA scenarios: happy = 同项目检索命中；failure = 用另一项目上下文检索，断言 0 命中并记录 denied；把模型假设写入 evidence 字段，断言 schema 校验拒绝。Evidence `$ACP2_EVIDENCE/C/research-record-fixtures/memory/`。
  Commit: Y | `feat(acp2-c): scope research memory by authorization and version`
  Recommended task executor category: deep-low - 授权边界可由既有 access 契约读出。

- [ ] 27. T-27 / C19a：持久审批卡与摘要绑定
  What to do / Must NOT do: 新建 `frontend/js/workbench/features/research-approval.js`；审批卡展示 proposal/recipe digest、科学限制、资源上限、费用口径、过期时间；**审批绑定 `plan_definition_digest + project + budget + 允许变更范围`**，不绑定后续追加的 TaskRef/evidence refs；改 method/映射/初始输入/预算使旧批准失效，追加 TaskRef/evidence 不使批准失效；DSH 工具审批与平台科研审批**分别映射**，前者不能代替后者；审批后只提交**已绑定 digest** 的计划。新增 `frontend/js/module_tests/acp2_approval.test.js`。**Must NOT**：把模型说"用户同意"当审批；审批后允许改科学参数；让 DSH 审批按钮直接改平台状态；用显示名/时间戳变化使批准失效。
  Closes: GAP-5, GAP-7
  Parallelization: Wave 8 | Blocked by: T-03, T-25, B16 | Blocks: T-28, T-34
  References: 计划 C `:344-351`；B16（`src/acp_services/runtime/approvals.py` 拟新增，B 交付后）；`acp2-contracts/experience/proposal.schema.json`。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_approval` 退出 0 且 N≥8；`ACP2_RUN_ID=approval-budget-live-r1 c_accept approval-budget-live 600 --phase approval` 为 PASS（只评估 approval 子结果，budget 子结果可暂为 `NOT_VERIFIED`，不得因此把整场景写成 PASS）；QA 覆盖"追加 TaskRef/evidence 不失效、改 method/映射/initial input 失效"。
  QA scenarios: happy = 审批卡渲染 + 绑定 digest 提交；failure = 审批后改 method，断言旧批准失效并阻止 dispatch；追加 TaskRef/evidence 后提交，断言批准仍有效且只延长引用；用另一用户身份 approve，断言拒绝。Evidence `$ACP2_EVIDENCE/C/approval-budget-live/approval/`。
  Commit: Y | `feat(acp2-c): add persistent approval card bound to plan digest`
  Recommended task executor category: visual-engineering - 审批 UI 与状态反馈。

- [ ] 28. T-28 / C19b：预算/拒绝/过期/撤销负向与真实端点
  What to do / Must NOT do: 在真实平台分别试只读身份、跨项目引用、单次/累计预算、拒绝、过期、撤销；**request/query/approve/reject/revoke 全部经 Runtime 类型化入口**（T-09 映射），approve 必须绑定真实 principal，模型不能自批；等待使用持久 cursor 或有界查询；展示具体原因并禁止升级权限；检查新会话/新 MCP 进程不能绕过限制；记录服务端 dispatch/账本/审批权威事实（由 B 提供）。**Must NOT**：超限仍 dispatch 或仍预留预算；前端隐藏按钮当安全边界；把 T-27 的 approval 子结果重复计为 budget 证据；让模型声明代替用户批准。
  Closes: GAP-7
  Parallelization: Wave 8 | Blocked by: T-27 | Blocks: T-33, T-34
  References: 计划 C `:346-351`；手册 `:476`；B17（累计预算实现）。
  Acceptance criteria: `ACP2_RUN_ID=approval-budget-live-r1 c_accept approval-budget-live 600 --phase budget` 为 PASS，且 budget/deny/expire/revoke 四个子行各自有结果；`ACP2_RUN_ID=approval-budget-live-r1 c_accept approval-budget-live 120 --phase aggregate` 为 PASS（只有 aggregate 才能写整场景 `result=PASS`）；approval request/query/approve/reject/revoke 的调用记录经 Runtime 入口且 approve 绑定真实 principal；`node frontend/js/module_tests/run_all.js acp2_approval` 无回归。
  QA scenarios: happy = 四个子行按预期拒绝/放行；failure = 用已撤销审批重放提交，断言服务端拒绝且无 dispatch；把预算上限调到刚好不足，断言停在准入而非执行后。Evidence `$ACP2_EVIDENCE/C/approval-budget-live/budget/`。
  Commit: Y | `test(acp2-c): verify approval, budget and revocation negatives`
  Recommended task executor category: unspecified-high - 多负向场景 + 服务端权威核对。

- [ ] 29. T-29 / C20a：单 Supervisor 与四类 Skill 的显式 I/O
  What to do / Must NOT do: 在 `rph/packages/dsh-bundle/skills/` 实现 `interpreter/planner/reviewer/synthesizer`，每个 skill 有显式输入/输出 schema；单 Supervisor 执行 Understand→Hypothesize→Critique→Plan→Approve→Wait→Evaluate→Revise/Stop；每轮先科学预检与平台准入；提交后持久化 task ref，等待平台事件或有界查询，**不保持模型忙等 QC**；由真实 `EvidenceReport` 决定下一步，模型只能建议；轮次/预算上限后停止。**Must NOT**：无证据连续昂贵重试；LLM 自判科学 PASS；重复终态事件启动第二轮；Wait 阶段持续调用模型。
  Closes: GAP-7
  Parallelization: Wave 8 | Blocked by: T-23, T-25 | Blocks: T-30
  References: 计划 C `:353-360`；总方案 §7.1/§7.2；`rph/packages/research/src/record.ts`。
  Acceptance criteria: `pnpm --dir rph build`、`test` 退出 0 且 skill I/O 用例 ≥8；`pnpm --dir rph check` 退出 0。
  QA scenarios: happy = 三条命令 + 一次离线状态机走查（fixture 驱动）；failure = 注入"无证据"分支，断言进入 Stop 而非再提交；注入重复 `job.completed`，断言不产生第二轮。Evidence `$ACP2_EVIDENCE/C/research-loop-live/offline/`。
  Commit: Y | `feat(acp2-c): implement single supervisor research loop skills`
  Recommended task executor category: deep-high - 研究循环的停止条件与证据优先是核心设计。

- [ ] 30. T-30 / C20b：真实小型研究轮次
  What to do / Must NOT do: **T-30a 控制循环**：用预算内一个真实小型 Recipe 跑完整轮次，记录 round/event key、工具调用、停止条件；确认预检→审批→提交→等待→评估的顺序与幂等；把真实 `EvidenceReport` 作为下一步依据。**T-30b 候选搜索（本期范围）**：只接受 A 冻结的初始输入类别（默认不含 TS 种子）、候选来源、候选数量上限、生成步骤、筛选/拒绝原因、验证器与成功判据；C 保存搜索轨迹，A 审科学，B 证明实际执行；若 A/B 未交付该冻结输入，T-30b 记 `NOT_VERIFIED(search_capability)`，T-34 不得声明搜索能力；消费已冻结的 `requests/A-req-04.md`（搜索输入类别/候选来源/判据）与 `requests/B-req-15.md`（实际执行 proof）。**Must NOT**：用多个 LLM 角色一致代替验证器；把模型总结当证据；把 T-30a 的控制循环 PASS 冒充 T-30b 搜索 PASS；手工注入已知候选冒充从声明输入生成。
  Closes: GAP-7
  Parallelization: Wave 9 | Blocked by: T-29, T-28 | Blocks: T-31, T-32
  References: 计划 C `:356-360`；手册 `:380-418`；A-16 validator 输出（A 交付后）。
  Acceptance criteria: `ACP2_RUN_ID=research-loop-live-r1 c_accept research-loop-live 2400 --subcase control` 为 PASS，结果含 `rounds>=1`、`tool_calls`、`task_ref`、`evidence_ref`、`stop_reason`；`ACP2_RUN_ID=research-loop-live-r1 c_accept research-loop-live 2400 --subcase search` 仅在 A 冻结输入与判据到位后运行，结果含 `input_class`、`ts_seed_included`、`candidate_source`、`candidate_count`、`generation_steps`、`selected/rejected`、`validator_ref`、`search_trace_ref`、`scenario_key=research-loop-live/search`；缺输入时 `NOT_VERIFIED(search_capability)` 且不得把 `search_capability` 写成 PASS。
  QA scenarios: happy = T-30a 完整轮次 + T-30b 搜索轨迹；failure = 把预算设为不足，断言在准入阶段停止并记录原因；把 evidence 状态改为 `inconclusive`，断言不继续昂贵步骤；只跑 T-30a 时断言 `search_capability=NOT_VERIFIED`；把手工候选注入 search 轨迹，断言校验失败。Evidence `$ACP2_EVIDENCE/C/research-loop-live/`。
  Commit: Y | `test(acp2-c): run one real bounded research round`
  Recommended task executor category: unspecified-low - 执行与记录，阈值由前一 todo 固定。

- [ ] 31. T-31 / C21：科学证据视图与 ACV 呈现
  What to do / Must NOT do: 新建 `frontend/js/workbench/features/scientific-evidence.js`：并列显示执行状态与 claim 的 `supported/refuted/inconclusive/not_evaluated`；每条命题可展开原始工件、validator、方法/版本、缺失验证与 RPH 决策来源；Client 只读规范化 products，**不在页面解析大 ORCA 输出或算热化学**；复用 `frontend/js/visualization/*` 的 scene/framing/energy/IRC/vibration，输入来自 A/B 冻结投影；能量图只保留"保存为候选"，不提交任务；真实浏览器验证挂载/隐藏/换任务/销毁。**分两级交付**：`implementation_ready` 用冻结 EvidenceReport/ViewModel fixture 完成 UI 与模块测试；`verified` 在 T-30a 真实研究轮次可用后再补 live 子行并签收。新增 `frontend/js/module_tests/acp2_evidence.test.js`。**Must NOT**：把 COMPLETED 显示成机制正确；单虚频当 IRC 连通；inconclusive 被绿色成功吞掉；ACV 调 API；销毁后旧动画/旧回调继续执行；因 T-30 未完成而阻断 fixture 驱动的 UI 开发。
  Closes: GAP-5
  Parallelization: Wave 9 | Blocked by: T-24, T-08 | Blocks: T-34
  References: 计划 C `:362-369`；根 `AGENTS.md` ANTI #21/#22；`frontend/js/visualization/scene_instance.js`；`frontend/js/visualization/energy_chart_renderer.js`；`src/acp/results/frames.py`（VIEW_REGISTRY）。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_evidence` 退出 0 且 N≥10（仅零执行保护；通过条件是命名矩阵：claim 四态、展开原始工件、缺验证、dispose、无 fetch、能量图只保存候选）；`ACP2_RUN_ID=evidence-browser-live-r1 c_accept evidence-browser-live 300 --mode fixture` 为 PASS（`evidence_kind=fixture`、`mode=fixture`，不关闭 H4）；`ACP2_RUN_ID=evidence-browser-live-r1 c_accept evidence-browser-live 600 --mode live` 仅在 T-30a PASS 后补跑并 PASS（`mode=live` 是 `evidence-browser-live` 的终态整场景结果，无需 `aggregate`）；`grep -R "fetch(" frontend/js/visualization/` 零命中。
  QA scenarios: happy = 两条命令 + 三态渲染断言；failure = 构造 `inconclusive` 报告，断言不被渲染成成功色；在 dispose 后 tick 一帧，断言无回调。Evidence `$ACP2_EVIDENCE/C/evidence-browser-live/`。
  Commit: Y | `feat(acp2-c): present scientific evidence with claim states`
  Recommended task executor category: visual-engineering - 证据 UI + 可视化生命周期。

- [ ] 32. T-32 / C22：DSH 会话与持久研究轮次恢复
  What to do / Must NOT do: 真实研究提交后保存 session ID、record resource ref 与 task ref；正常结束 DSH/MCP 但保留平台执行；用**相同 cwd/DSH_HOME/profile/agent preset** 与真实 `--session-id` 恢复；再建新聊天会话，通过已授权 `ResearchRecord` 恢复科研轮次；**只依赖 B19 已可消费的持久 cursor/权威快照接口，不等 B19 整项完成**；B 在指定窗口重启测试 Controller，C 查询补全丢失事件；重放重复/乱序事件检查 round 幂等；恢复前后比较 job/attempt/digest，不自动新提交。**Must NOT**：恢复失败时自动新建同科学任务；把 DSH session ID 当 task_id；用共享内存对象充当重连测试；要求 B19 的 C live 复验先完成才允许接口交接。
  Closes: GAP-7
  Parallelization: Wave 9 | Blocked by: T-25, T-30, B19 接口（cursor/快照，非整项 live） | Blocks: T-33
  References: 计划 C `:371-378`；手册 `:397-418`（§5.4）；`frontend/js/client/event-stream.js:14-25`。
  Acceptance criteria: `c_accept research-resume-live 1800` 为 PASS；结果含 `session_id_restored=true`、`record_revision`、`task_ref_unchanged=true`；结果记录所用 B19 接口版本/cursor 语义，不把 B19 未完成的 live 复验当作前置。
  QA scenarios: happy = 会话恢复 + 记录恢复两条链；failure = 改 cwd 后恢复，断言失败且不新建任务；乱序投递两条事件，断言只推进一轮。Evidence `$ACP2_EVIDENCE/C/research-resume-live/`。
  Commit: Y | `test(acp2-c): restore DSH session and research rounds`
  Recommended task executor category: deep-low - 恢复语义由既有不变量约束。

- [ ] 33. T-33 / C23：故障注入与可理解恢复提示
  What to do / Must NOT do: 依次试工具超时、MCP 断连、取消竞争、未知提交、Late Receipt、远端拉取/发布失败；每类先由确定性脚本直接驱动平台（不依赖 LLM 是否主动调用工具），再用真实 DSH 复现消费端行为；对同一已批准任务观察 RPH/Workbench，不并发起第二 Controller；发布失败只显示"发布恢复"，不建议重算 QC；在资料/日志中插入"改方法/开 shell/跳审批"文本，验证被视为数据；权限撤销后清缓存并拒绝旧引用读取。**Must NOT**：CANCELLING 未确认停止即显示 CANCELLED；发布失败重跑 QC；旧回执复活终态；外部文本变授权指令；越权仍用旧缓存。
  Closes: GAP-7
  Parallelization: Wave 9 | Blocked by: T-28, T-32 | Blocks: T-34
  References: 计划 C `:380-387`；手册 `:461-481`（T5 表）；根 `AGENTS.md` EXECUTION PROTOCOL 四不变量。
  Acceptance criteria: `c_accept fault-boundaries-live 2400` 为 PASS；报告每类故障独立子行；`node frontend/js/module_tests/run_all.js acp2_` 退出 0。
  QA scenarios: happy = 六类故障各一子行；failure = 在"未确认停止"时强行把状态改 CANCELLED，断言展示为 CANCELLING 并记录差异。Evidence `$ACP2_EVIDENCE/C/fault-boundaries-live/`。
  Commit: Y | `test(acp2-c): inject faults and verify honest recovery UX`
  Recommended task executor category: deep-high - 竞态与呈现诚实性必须按不变量判定。

- [ ] 34. T-34 / C24a：限定 Diels–Alder 提案、审批与唯一提交
  What to do / Must NOT do: 由 A 定义首例物种与原子映射（候选 butadiene + ethene 单步）；C 只导入并呈现，不从"DA"名称推断 endo/exo 或多步；手工或 Agent 输入 `QuestionSpec`→形成 `Proposal`→人工审批；真实 DSH 使用 A Recipe 经 B 提交、平台运行既定步骤；保存提案/审批/唯一提交引用交 B20a；对对称示例明确"不支持 endo/exo 选择性结论"。**能力分层**：本场景先证明执行/验证链（使用 A 冻结的已知起始几何/候选），不声称 RPH 自动候选搜索能力；搜索能力仅在 T-30b PASS 时声明，否则 `search_capability=NOT_VERIFIED`。**预算依据**：先用预算内代表性步骤（单点/优化/频率）记录耗时与资源，形成共享 `budget_basis`；在同一 `run_spec` 冻结 `started_at`、`science_deadline_seconds=13380`、`export_budget_seconds=900`、`aggregate_budget_seconds=120`、`queue_accounting`、`inflight_stop_policy`；B20a 必须显式使用 `--phase da-submit`，不得靠输出目录或任务编号隐式分派；消费已冻结的 `requests/B-req-11.md`，要求 B 使用同一 run_spec 与 `preflight|da-submit|da-evidence|aggregate` 的显式 phase。**Must NOT**：自行扩大反应范围；缺审批就提交；把净反应当单 TS；用另一 Fixture 冒充本场景；把已知候选执行链冒充自动搜索成功。
  Closes: GAP-4
  Parallelization: Wave 9 | Blocked by: T-23–T-33 必需项, A-14–A-16, B20a | Blocks: T-35, T-36
  References: 计划 C `:389-397`；计划 A `:221-249`（A-14–A-16）；总索引 `:128`（C24a→B20a）；`acp2-contracts/experience/research-record.schema.json`。
  Acceptance criteria: `ACP2_RUN_ID=da-research-live-r1 c_accept da-research-live 13380 --phase da-submit` 的 `C24a` 子结果为 PASS（13380 s = 3 h 43 m，预留 15 m 证据 + 2 m aggregate，总 ≤4 h；不得再用 14400 占满整段）；结果含 `proposal_digest`、`approval_ref`、`job_id`、`recipe_digest`、`budget_basis`、`run_spec{started_at,science_deadline_seconds,export_budget_seconds,aggregate_budget_seconds,queue_accounting,inflight_stop_policy}`、`search_capability`；提案中的问题类型与 A 的 QuestionSpec 一致；该阶段只允许 **1 次** submit；`budget_basis` 明确 Local/LSF、queue 计入与 phase 计时基准，aggregate 复用同一 run_spec 与 scenario budget，不另开预算；B20a 记录实际 argv 且包含 `--phase da-submit`；`search_capability` 只能来自 T-30b，不得由 T-30a 推导。
  QA scenarios: happy = 提案→审批→提交三段各有引用；failure = 对同一 Fixture 提出 endo/exo 问题，断言返回限制说明且不生成选择性命中；缺审批直接提交，断言被准入拒绝。Evidence `$ACP2_EVIDENCE/C/da-research-live/a/`。
  Commit: Y | `feat(acp2-c): drive bounded DA proposal, approval and submission`
  Recommended task executor category: unspecified-high - 跨轨协作 + 真实提交，但步骤已冻结。

- [ ] 35. T-35 / C24b：DA 证据反馈、页面记录与 inconclusive 保留
  What to do / Must NOT do: 消费 B20a/B20b 工件与 A-17～A-19 的科学校验；逐项显示 TS 阶数/模式、双向 IRC/端点映射、能量可比性与限制；真实模型据证据形成带引用的总结；Workbench 查看同一任务/研究记录并导出证据；保存一例验证不充分的真实失败/中间结果，`inconclusive` 必须保留。**本阶段复用 T-34 的同一 job/attempt 引用，禁止再次 submit**；入口必须校验已存在的 `job_id`，缺失则记 `NOT_VERIFIED` 而不是新建任务。**到 `run_spec.science_deadline_seconds`（13380s/3h43m）只停止新增科学步骤**；所有 phase 读取 T-34 的同一 `run_spec`，不重置累计时钟；正在运行的步骤必须选择有界停止（确认远端停止）或保留引用对账，不得把未完成步骤伪造成完成；证据/导出使用预留窗口。**Must NOT**：执行完成但缺 IRC/端点证据仍支持机制；无取代环境 Fixture 就声明 endo/exo；把初次未验证失败改写成"科学基线已通过"；重跑 T-34 的提交；遗留活动远端任务不记录。
  Closes: GAP-4
  Parallelization: Wave 9 | Blocked by: T-34, A-17–A-19, B20b | Blocks: T-36
  References: 计划 C `:393-397`；A-17–A-19；`frontend/js/workbench/features/scientific-evidence.js`。
  Acceptance criteria: `ACP2_RUN_ID=da-research-live-r1 c_accept da-research-live 900 --phase da-evidence` 的 `C24b` 子结果为 PASS，且 `usage.tasks` 相对 T-34 未增加；`ACP2_RUN_ID=da-research-live-r1 c_accept da-research-live 120 --phase aggregate` 为 PASS（整场景结果只在 aggregate 中生成）。**时间预算预先分为科学运行（≤13380 s / 3 h 43 m）与验证/导出（预留 15 m，总 ≤4 h；读取 T-34 的同一 `run_spec`，不重置 `started_at`/累计时钟）**：到 13380 s 只停止追加新的昂贵科学步骤，**不自动判 FAIL**；若必需验证/导出在预留窗口内完成且总用量未超 4 h，则 `result=PASS`；只有必需操作未完成或实际超过 4 h 才记 `FAIL(reason=budget_exhausted)`，科学 claim 另行记 `inconclusive` 且 H4 保持开放；恰好用满上限但全部必需操作完成允许 PASS；负测中第 13 次调用被拒是预期拒绝，不属于实际超支。页面对每个 claim 显示状态与来源；导出文件含 `evidence_digest` 与 `limitations`。
  QA scenarios: happy = 证据页 + 导出；failure = 删除双向 IRC 中的一向，断言 claim 降级为 `inconclusive` 而非 `supported`；到 13380s 且证据可导出 → `PASS`；超过 4h 或必需操作未完成 → `FAIL`；第 13 次调用被拒 → 负测通过，不算实际超支。Evidence `$ACP2_EVIDENCE/C/da-research-live/b/`。
  Commit: Y | `feat(acp2-c): feed DA evidence back into workbench and loop`
  Recommended task executor category: unspecified-high - 证据映射 + 页面 + 导出。

- [ ] 36. T-36 / C25：P2 收口与两份操作手册
  What to do / Must NOT do: 新增 `docs/reference/next-change/ACP_2.0_RPH_Research_Operation_20261020.md`（输入、澄清、proposal、审批、等待、证据、继续/停止、会话恢复）与 `docs/reference/next-change/ACP_2.0_Workbench_Research_Guide_20261020.md`（不使用模型的操作路径）；每段附实际命令/按钮、预期结果、失败解释与证据位置；列当前科学适用范围、版本组合、未验证格子；**区分自动测试、独立复现、用户批准三类事实**：自动测试由实现者运行；独立复理由未参与实现的独立执行者（fresh agent session 或他人）按文档复跑并记录身份；DA 等科学审批必须由真实用户完成，不能由作者或模型代替。**收口顺序（禁止自引用）**：先读取并校验除 `p2-closeout` 外的八个 P2 场景结果，生成 H4 草稿；再运行 `c_accept p2-closeout 600` 写本场景自身结果；最后由最终索引以引用方式加入 `p2-closeout` 行，形成九项 H4——任何工件不得把自己的最终摘要计入自身摘要输入。**Must NOT**：必需 live 行 FAIL/NOT_VERIFIED 仍关闭 H4；证据 revision 对不上；命令含占位符却写"可直接运行"；缺无模型路径；由实现者自己复跑并记为该独立验证；把自动测试通过冒充独立复现或用户批准。
  Closes: GAP-6, GAP-7
  Parallelization: Wave 9 | Blocked by: T-34, T-35 | Blocks: H4 收口
  References: 计划 C `:399-406`；总索引 `:151,206-213`（H4 关闭条件与回执字段）；本计划 `## Verification strategy` 的三态规则。
  Acceptance criteria: 先对八个已完成 P2 场景逐项校验：`question-reaction-fixtures`/`research-record-fixtures` 记 `evidence_kind=fixture`，其余六项 `evidence_kind=live` 且 `result=PASS`（`approval-budget-live` 与 `da-research-live` 还需 `--phase aggregate` 的整场景 PASS；`evidence-browser-live` 以 `--mode live` 的终态结果作为整场景结果，无需 `aggregate`）；**DA 的真实正例（有效 TS + 双向 IRC + 可比能量）必须单独 PASS；"缺 IRC 时正确显示 inconclusive"只是独立负例行，不能替代正例**；随后 `c_accept p2-closeout 600` 为 PASS；最终 `H4.json` 含九项且 `"$ACP2_PYTHON" scripts/acceptance/acp2_experience.py --verify-receipt experience/handoffs/H4.json` 退出 0；`p2-closeout` 的 `artifacts` 中不得出现其自身结果文件的摘要；复跑记录含执行者标识、实际 argv 与退出码。
  QA scenarios: happy = 八项校验→自身结果→最终索引 + `--verify-receipt`；failure = 删除某必需场景的 PASS 行后 `--verify-receipt` 必须拒绝；把占位符 `<...>` 留在命令里，格式检查必须报错；把 artifact SHA-256 改错，校验必须拒绝；把 `p2-closeout` 自身摘要写入其 `artifacts`，自引用检查必须拒绝。Evidence `$ACP2_EVIDENCE/C/p2-closeout/`。
  Commit: Y | `docs(acp2-c): publish RPH and workbench research guides`
  Recommended task executor category: writing - 文档 + 复跑记录汇总。

- [ ] 37. T-37 / C26：受权插件/Skill 发现
  What to do / Must NOT do: 在 Bridge 提供受权 `science_discover`；DSH Skills 只消费**已启用**工具/元数据，不能因发现描述就执行代码；Workbench 显示 capability/provider/版本/适用性/信任状态；把第三方插件能力集成为一个真实小型研究步骤；把插件/工具版本写入 `ResearchRecord`，运行中不漂移；未知/未启用插件只显示受限说明。**描述、启用策略、进程隔离分层验收**：描述只是元数据；启用需受权；in-process 插件不是 sandbox，第三方执行范围/进程限制由 B 限定；不得把 metadata/trust 字段描述成安全隔离保证。**Must NOT**：发现即安装/执行；未知 license/不可信描述当授权；插件替换后重解释旧任务引用；动态展开无上限工具列表；写独立插件信任数据库；宣称 in-process 插件沙箱化。
  Closes: GAP-4
  Parallelization: Wave 10 | Blocked by: T-36, A-21, B21 | Blocks: T-38
  References: 计划 C `:410-417`；总方案 §8 信任等级；`src/cccp/plugin/{metadata,discovery,host,sdk}.py`（A 侧）；B21 Adapter 契约。
  Acceptance criteria: `c_accept plugin-discovery-live 1200` 为 PASS；`pnpm --dir rph test` 退出 0；UI 快照含 trust 状态字段。
  QA scenarios: happy = 发现→启用→一次真实小步骤；failure = 禁用插件后调用，断言拒绝且无执行；未知 license 插件被标为 `restricted` 而非可用。Evidence `$ACP2_EVIDENCE/C/plugin-discovery-live/`。
  Commit: Y | `feat(acp2-c): discover authorized capabilities and skills`
  Recommended task executor category: unspecified-high - 跨 Bridge/DSH/UI 三处接线。

- [ ] 38. T-38 / C27：Schema 自动表单与定制 Feature 共存
  What to do / Must NOT do: 新建 `frontend/js/workbench/features/capability-form.js`，先支持冻结 JSON Schema 2020-12 子集：`type`（含 `"null"`）、`enum`、`required`、`minimum/maximum`、`items`、`properties`、`additionalProperties`；契约声明 `schema_dialect`、UI 支持子集版本与关键词清单；`range`/`nullable` 只作为明确 UI 方言并给出标准等价物；`$ref`/`oneOf` 由 A/B 预展开或显式声明不支持（不得静默当作支持）；未支持组合关键字**显式提示并阻止提交**，不降级忽略；字段单位/科学含义来自 A 描述；Mechanism/NMR 保留定制 Feature 并通过相同 Client 调用；安装不改核心代码的新能力后真实生成表单并完成一次受控提交；表单默认值/人工修改进入 proposal provenance。**本 todo 属于 P3/H5；B18a 的 P2 最小往返使用 T-17 表单，不以本 todo 为前置。**新增 `frontend/js/module_tests/acp2_schema_form.test.js`，并至少跑通一次**真实往返**：A descriptor → B discovery → C 表单 → B 校验 → CCCP 请求（用手写 toy schema 不算完成）。**Must NOT**：未知 Schema 版本当旧版；隐藏科学必填字段；绕过整数/单位约束；未预检提交；把远端描述当 HTML 注入；把 `range`/`nullable` 当标准关键词实现。
  Closes: GAP-5
  Parallelization: Wave 10 | Blocked by: T-37 | Blocks: T-39, T-42
  References: 计划 C `:419-426`；`acp2-contracts/science/capability.schema.json`（A-01）；根 `AGENTS.md` ANTI #20。
  Acceptance criteria: `node frontend/js/module_tests/run_all.js acp2_schema_form` 退出 0 且 N≥12（仅零执行保护；通过条件是命名矩阵：标准关键词、未知关键字阻断、`$ref`/`oneOf` 不支持、整数/单位、注入转义、真实往返）；`c_accept schema-form-live 1200` 为 PASS；未知关键字夹具被阻断；真实 A descriptor → B discovery → C 表单 → B 校验 → CCCP 请求的往返记录存在且字段一致。
  QA scenarios: happy = 表单生成 + 一次受控提交；failure = 构造 `oneOf`/`$ref` 未支持关键字，断言提示并禁止提交；注入 `<img onerror>` 描述，断言被转义。Evidence `$ACP2_EVIDENCE/C/schema-form-live/`。
  Commit: Y | `feat(acp2-c): render capability forms from frozen JSON schema`
  Recommended task executor category: visual-engineering - 表单渲染器与浏览器验证。

- [ ] 39. T-39 / C28：插件/DSH 升级、回退与历史只读
  What to do / Must NOT do: 冻结前一版本组合与研究记录快照；在独立工具目录装候选 DSH/插件版本，不覆盖旧版；重跑真实 MCP 发现、错误、重连、一条小型计算及自动表单；读旧项目/旧 evidence，缺新版能力时只读并解释限制；回退 profile/bridge 版本并保留原工件/record；形成 `docs/reference/next-change/ACP_2.0_RPH_Compatibility_20261020.md` 与升级 checklist。**Must NOT**：升级自动重跑旧任务；无法读取旧科学证据；悄悄换 Provider/方法；回退覆盖新 round 记录；候选失败仍标兼容。
  Closes: GAP-4
  Parallelization: Wave 10 | Blocked by: T-37, T-38 | Blocks: T-42
  References: 计划 C `:428-435`；手册 `:524`（版本升级复跑范围）；A-23 插件兼容矩阵。
  Acceptance criteria: `c_accept plugin-upgrade-live 1800` 为 PASS；双版本各有一条小型计算记录；回退后旧 record 仍可读。
  QA scenarios: happy = 双版本 + 回退 + 历史只读；failure = 用新版 validator 读旧 evidence，断言显示"只读/限制"而非 `supported`；回退后写新 round，断言记录不被覆盖。Evidence `$ACP2_EVIDENCE/C/plugin-upgrade-live/`。
  Commit: Y | `test(acp2-c): verify plugin and DSH upgrade/rollback`
  Recommended task executor category: deep-low - 双版本环境与历史读取，证据可判定。

- [ ] 40. T-40 / C29：NMR/热化学与多假设预算策略
  What to do / Must NOT do: 复用现有 `frontend/js/workbench/features/nmr.js`，新增标准 evidence 适配，**不重写 DP4/DP5**；热化学与机制共用温度/参考态/可比性呈现；RPH 先列假设与最低判别证据再申请预算；先用廉价步骤排除不适用项；跨领域结果只通过 A 合法 Recipe/validator 组合；真实模型与真实科学 fixture 各跑一项，记录停止/不足证据分支。**增加 `selectivity` 子场景**（唯一标识 `scenario_key=experience:cross-domain-live/selectivity`；CLI `cross-domain-live --subcase selectivity`）：A-25a 提供两条可区分路径的 fixture/判据，B23a 提供权限/预算/执行接口；C T-40a 作为唯一研究提交驱动完成一次提交；B23b 执行并导出受权工件；B23a 的 1800s 仅用于隔离/权限/预算检查，不覆盖选择性科学执行，B23b 的执行用量单列且合计仍受 3600s 与 TaskRef 上限约束；A-25b 科学审计；C T-40b 呈现与签收。**T-40 入口只等待 A-25a 与 B23a，不等待整项 A-25/B23**；T-40a 完成后不重复提交，B23b/A-25b 的等待在 T-40 内部按顺序推进；已冻结的 `requests/A-req-03.md` 要求 A-25b 引用该 `scenario_key` 而非另建 `selectivity-live`，`requests/B-req-10.md` 要求 B23 使用同一 key。缺任一合格路径不得给相对偏好；若范围延期，必须同步 H6 与场景矩阵，不能只在 C 记 `NOT_VERIFIED` 而其他轨仍声称完整。**子行预算**：`nmr`/`thermo` 默认复用已完成工件（新 TaskRef=0）；`selectivity` 默认最多 1 个新 Local TaskRef；三子行合计新 TaskRef ≤1、科学步骤与 QC 调用按实际引用登记、总墙钟 ≤3600s；若必须新增第 2/3 个 TaskRef，先按 owner 批准流程记录 T-40 例外，否则 `NOT_VERIFIED(budget_insufficient)`，不得仅靠换 `run_id` 重置累计用量。**Must NOT**：未归一化直接比较能量；把 DP4/DP5 数值当普遍结构证明；预算用尽仍开新假设；模型改温度/标准态无记录；把 NMR/热化学成功当成选择性双路径已计算。
  Closes: GAP-4
  Parallelization: Wave 10 | Blocked by: T-36, A-24, A-25a, B23a | Blocks: H6 车道（T-40b）
  References: 计划 C `:439-446`；`src/acp/nmr/AGENTS.md`；A-24/A-25a/A-25b 跨域与选择性契约。
  Acceptance criteria: `ACP2_RUN_ID=cross-domain-live-r1 c_accept cross-domain-live 3600` 分别以 `--subcase nmr`、`--subcase thermo`、`--subcase selectivity` 执行，三个子结果各有独立结果（结果记录 `scenario_key` 与 `subcase`；nmr/thermo PASS；selectivity 两条路径齐全则 PASS，缺一路 `inconclusive`）；`ACP2_RUN_ID=cross-domain-live-r1 c_accept cross-domain-live 120 --phase aggregate` 汇总同一 run_id；报告含每个子行的 `producer/reused_artifacts/new_task_refs/science_steps/qc_invocations/backend/queue_seconds/cap` 与合计；合计 `new_task_refs<=1`（超过须有 owner 批准例外）；`selectivity` 子行必须有两条路径的输入/判据/工件引用，缺任一合格路径时记 `inconclusive` 且 H6 不关闭；`node frontend/js/module_tests/run_all.js acp2_` 退出 0；`pnpm --dir rph test` 退出 0。
  QA scenarios: happy = NMR、热化学、选择性各一真实小样本；failure = 混合电子能/自由能比较，断言被 validator 拒绝；预算耗尽后请求新假设，断言停止；删除选择性一条路径，断言子行 `inconclusive` 而非偏好。Evidence `$ACP2_EVIDENCE/C/cross-domain-live/`。
  Commit: Y | `test(acp2-c): run cross-domain research with bounded hypotheses`
  Recommended task executor category: deep-low - 复用既有 NMR 模块，边界由 A 契约给出。

- [ ] 41. T-41 / C30：多项目、多身份与撤权隔离
  What to do / Must NOT do: 使用两个项目、至少读者/研究者两个身份；在 Workbench 切项目/账号，检查 `ResourceStore`、SSE、scene 与选择状态清理；RPH 以真实授权上下文读取记忆/任务/工件；撤权后重试旧 tool/session/resource refs；检查服务端拒绝与客户端敏感缓存消失；并发两项受限研究，验证预算/幂等/round keys 不串项目。**Must NOT**：新账号读旧项目缓存；会话切换绕权限；同内容工件凭 digest 绕授权；项目 A 事件更新项目 B 页面；商业等级进入科学字段。
  Closes: GAP-4
  Parallelization: Wave 10 | Blocked by: T-36, B15, B23a | Blocks: T-42
  References: 计划 C `:448-455`；根 `AGENTS.md` Services 依赖方向；`frontend/js/client/resource-store.js:121-139`（invalidate/reset）。
  Acceptance criteria: `c_accept multi-project-live 1800` 为 PASS；切项目后 store key 全变；撤权后旧引用读取返回 denied。
  QA scenarios: happy = 两项目两身份交叉操作；failure = 撤权后仍用旧缓存渲染，断言被清空并显示未授权。Evidence `$ACP2_EVIDENCE/C/multi-project-live/`。
  Commit: Y | `test(acp2-c): verify project and identity isolation after revocation`
  Recommended task executor category: unspecified-high - 多身份矩阵 + 客户端缓存核对。

- [ ] 42. T-42 / C31：研究记录迁移、长会话与体验可靠性
  What to do / Must NOT do: 冻结旧版 `ResearchRecord`/proposal/ViewModel/profile fixtures；只迁移 RPH 自有资源版本，不迁平台 DB；迁移前后比较科研引用、人工决策与工件 digest；真实浏览器反复打开/关闭、换任务、隐藏动画并观察资源回收；真实 DSH 轮次恢复时限制上下文、保存完整外部证据引用；阶段时长/页面数/模型次数上限由 manifest 固定，不做无限 soak；提供回滚步骤。**Must NOT**：迁移不可回滚；无审计改人工决策；丢证据引用；scene dispose 后仍 tick；跨 schema 直接判科学 `supported`；无限模型重试。
  Closes: GAP-4
  Parallelization: Wave 10 | Blocked by: T-39, T-41 | Blocks: H6 收口
  References: 计划 C `:457-464`；`frontend/js/visualization/scene_instance.js`（dispose）；`acp2-contracts/experience/research-record.schema.json`。
  Acceptance criteria: `c_accept experience-upgrade-live 3600` 为 PASS；`pnpm --dir rph check` 退出 0；回滚脚本可重复执行。
  QA scenarios: happy = 迁移→比对→回滚；failure = 迁移后删除一条证据引用，断言校验失败且回滚可用；dispose 后 tick，断言无回调。Evidence `$ACP2_EVIDENCE/C/experience-upgrade-live/`。
  Commit: Y | `test(acp2-c): verify research record migration and long-session reliability`
  Recommended task executor category: deep-high - 迁移可逆性与生命周期泄漏需按不变量判定。

- [ ] 43. T-43 / C32：RPH CLI/Bundle 与静态资产交接包
  What to do / Must NOT do: 在 `rph/packages/cli` 实现基于冻结 DSH 公共入口的 profile/连接配置/研究启动/恢复命令；CLI **只做客户端**，不附带隐式 Controller/QC shell；导出可安装 RPH 包、Skills/Bundle、示例与锁文件；前端沿用既有资源路径，提供静态资产 manifest；把共享打包/console entry/CI 请求交 B（`requests/B-req-05.md`）；声明模型/科学软件/Runtime/许可前置与实际支持版本，不把"免费可用"写成"开源"。**`experience/release-manifest.json` 必须区分**：`source_candidate`、`client_revision`、`harness_revision`、`evidence_generation_revision`、可复用证据的变更映射（代码/依赖/配置/输入/validator 未变），以及模型预算 enforcement 的“已强制/仅记录/未验证”声明。**Must NOT**：包依赖开发者绝对路径/私密配置；必须装完整仓库才可用；CLI 起第二 Controller；分发缺 license/notice；前端引用不存在资源；把不同 revision 的证据混成同一候选。
  Closes: GAP-4
  Parallelization: Wave 11 | Blocked by: T-42, T-38, A-27a | Blocks: T-44, T-45
  References: 计划 C `:468-475`；B25a 子交付（总索引 `:138-141`）；`rph/package.json`（T-05）。
  Acceptance criteria: `pnpm --dir rph build/test/package` 均退出 0；`c_accept client-release 600` 为 PASS；`experience/release-manifest.json` 通过 `json.tool` 且包含上述五类 revision/复用映射与预算 enforcement 声明。
  QA scenarios: happy = 四条命令；failure = 把 CLI 指向本地 Controller 端口，断言即使平台不可达也不自启；在干净 `HOME` 下运行 `--help`，断言无绝对路径依赖。Evidence `$ACP2_EVIDENCE/C/client-release/`。
  Commit: Y | `feat(acp2-c): package rph cli, bundle and static assets`
  Recommended task executor category: unspecified-high - 打包 + 清单 + 交接请求。

- [ ] 44. T-44 / C33：干净目录独立安装验收
  What to do / Must NOT do: 创建新的用户可写工具/`DSH_HOME`/工作目录，不复用开发 `node_modules` 或个人配置；按发行说明安装 RPH 包与冻结 DSH；对接 B 的唯一测试 Controller；真实工具发现/模型/Local 小任务/Workbench 查看；按支持范围补 LSF 行；卸载客户端后平台任务与证据仍可查；记录每条命令与退出码。**Must NOT**：自动读开发目录配置/锁文件；缺模型却用假模型通过；污染系统 Node；卸载删平台任务；必须改源码才能连接。
  Closes: GAP-4
  Parallelization: Wave 11 | Blocked by: T-43, B25a | Blocks: T-45
  References: 计划 C `:477-484`；手册 `:103-157`（隔离目录）；总索引 `:138-141`。
  Acceptance criteria: `c_accept clean-install-live 2400` 为 PASS；安装日志含实际路径与版本；卸载后 `acp task query` 仍能读到原任务。
  QA scenarios: happy = 全新目录安装→一次真实调用→卸载→复读；failure = 复用开发 `node_modules` 的 PATH，断言 Harness 拒绝该环境。Evidence `$ACP2_EVIDENCE/C/clean-install-live/`。
  Commit: Y | `test(acp2-c): verify clean-directory installation`
  Recommended task executor category: unspecified-low - 按发行说明执行并留证。

- [ ] 45. T-45 / C34：本机安装、模型、浏览器与故障操作手册
  What to do / Must NOT do: 新增 `docs/reference/next-change/ACP_2.0_RPH_Server_Installation_20261020.md`：引用总 runbook；**先读取 B 同步后的主计划/总索引/手册 digest（`requests/B-req-13.md`）；若 CDXML 范围、BASE_REV、角色环境、P2 MCP、phase/readiness、P4 顺序或 H4/H6 必需行未同步，记 `NOT_VERIFIED(doc_sync)`，不得在 C 文档里另立一套。**写 Node/pnpm/DSH 精确安装、校验、build、新终端激活；Web/headless 分离、Provider/凭据、SSH tunnel、loopback/端口、官方 MCP overlay；Chromium 安装/系统依赖/真实页面检查；唯一 Controller 接入清单、Local/LSF smoke 顺序与停止/清理；给"能聊天没工具、协议不合、无浏览器、模型认证失败、工具超时、发布失败、会话丢失"逐项定位命令与预期输出；**区分**自动测试、独立复现、用户批准：由未参与实现的独立执行者按文档完成安装→一次真实工具调用→恢复→证据导出，记录执行者标识与差异；需要用户批准的步骤单独标注。**Must NOT**：要求改 `HOME`/`CODEX_HOME`；默认 `/tmp` 科学 workroot；只设 `ACP_RUN_ROOT` 就宣称集群隔离；明文凭据归档；缺停止/取消确认流程；由实现者自己复跑并记为该独立验证；把自动测试通过冒充独立复现。
  Closes: GAP-6
  Parallelization: Wave 11 | Blocked by: T-44 | Blocks: T-46
  References: 计划 C `:486-493`；手册 `:528-542`（§9 FAQ）；本计划 T-04/T-13/T-08 的实测记录。
  Acceptance criteria: `c_accept operator-runbook-live 2400` 为 PASS；文档中每条命令都有对应真实执行记录（含执行者标识）；`"$ACP2_PYTHON" scripts/acceptance/acp2_experience.py --check-doc-placeholders docs/reference/next-change/ACP_2.0_RPH_Server_Installation_20261020.md` 退出 0（只允许代码块/HTML 中的 `<...>`，正文占位符即失败）。
  QA scenarios: happy = 独立执行者复跑 + 占位符检查通过；failure = 在正文插入一个未替换的 `<绝对路径>`，断言 `--check-doc-placeholders` 非 0；故意删掉一条停止/清理步骤，断言复跑记录出现中断且 Harness 报缺口。Evidence `$ACP2_EVIDENCE/C/operator-runbook-live/`。
  Commit: Y | `docs(acp2-c): publish server installation and operations runbook`
  Recommended task executor category: writing - 文档 + 独立复跑记录。

- [ ] 46. T-46 / C35：最终报告与发布建议
  What to do / Must NOT do: 生成 `docs/reference/next-change/ACP_2.0_RPH_Workbench_Release_Acceptance_20261020.md`：C00–C35 完成矩阵（producer revision、证据 digest、实际结果、scope）；**区分 `source_candidate`、`client_revision`、`harness_revision`、`evidence_generation_revision`，并登记可复用证据的变更映射（代码/依赖/配置/输入/validator 未变）**；汇总 Node/DSH/官方 MCP 插件/模型/Chromium/Local/LSF 兼容组合；列未验证场景与真实科学限制（**不得消除 NOT_VERIFIED**）；提供 RPH/Workbench 升级与回滚操作；把发行包、文档、结果 Schema 与 C 的安装/操作事实交 B。**C 只依赖 B25a 的实际发行包与 T-44 的安装事实签本轨结果，不等待 B26 的最终结论**；B26b 可在 C35 之后单向汇总 H7。**Must NOT**：必需结果非 PASS 仍建议发布；报告引用不存在工件；版本升级沿用不匹配 PASS；只靠聊天/截图宣称科学闭环；未关闭共享接线请求；等待 B26 才完成本轨签收；C 自行重启生产或宣布整体上线；因文档汇总触发无限重跑。
  Closes: GAP-6
  Parallelization: Wave 11 | Blocked by: T-43–T-45, B25a | Blocks: H7 的 C 部分（B26b 单向汇总）
  References: 计划 C `:495-502`；总索引 `:154`（H7）；总方案 §11 验收门。
  Acceptance criteria: `c_accept experience-release-closeout 600` 为 PASS；`pnpm --dir rph check` 与 `node frontend/js/module_tests/run_all.js` 均退出 0；报告中每个 Cxx 行都有证据路径且文件存在（用脚本校验存在性）；主计划/总索引/手册 digest 与 `B-req-13` 同步记录一致，不一致时记 `NOT_VERIFIED(doc_sync)`。
  QA scenarios: happy = 三条命令 + 存在性校验；failure = 把某行改为 PASS 但指向不存在的证据文件，断言校验失败并阻止报告生成。Evidence `$ACP2_EVIDENCE/C/experience-release-closeout/`。
  Commit: Y | `docs(acp2-c): publish C-track release acceptance and recommendation`
  Recommended task executor category: writing - 汇总与三态台账。

## Final verification wave
> Coordination: F1 and F2 run independently in parallel against the frozen candidate. F3 runs in the **single B-scheduled resource window**; A/C participate in the same real run and audit their own track's evidence instead of re-submitting. F4 runs last, after F1–F3, and reads all verdicts plus the immutable receipts. Re-used evidence must be labelled as reused; only newly executed scenarios count as independent runs.
> ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Ideal-state fidelity

## Commit strategy
- 每个 todo 一个提交，均在 `acp2/c-experience` worktree；提交信息用 Conventional Commits（`feat|fix|test|docs|chore(acp2-c): ...`），不 push。
- 同一文件在同一时间只由一个 todo 修改；后续 todo 需要回改前一 todo 的文件时，先完成前一 todo 的提交再改。
- 共享文件（`src/**`、根 `pyproject.toml`、CI、总 manifest）**不提交**，只产出 `requests/B-req-NN.md`；由 B 应用后回填 producer revision。
- 不整文件格式化、不做无关重排；`ruff`/`ruff format` 只在 Python 新增文件上运行，不批量格式化既有文件。
- 证据目录写在 B00 分配的绝对 `$ACP2_EVIDENCE/C/**`，随对应 todo 提交时只提交 manifest/摘要引用；`.omo/evidence/**` 作为计划回执不进入产品提交（按仓库既有习惯保留在 `.omo`）。

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | T-01…T-46（全覆盖映射见 `## Todos` 的 Closes 行） | T-01 断言 + F1 逐行审计 | `.omo/evidence/acp2-plan-c-rph-workbench/task-1-acp2-plan-c-rph-workbench.md` |
| IS-2 | T-01, T-02, T-03, T-36, T-46 | T-01 写域断言 + `requests/` 存在性检查 | `$ACP2_EVIDENCE/C/baseline/`, `.../C/contracts/` |
| IS-3 | T-06, T-07 | `harness-selftest` 场景（含伪装 live 必须 FAIL） | `$ACP2_EVIDENCE/C/harness-selftest/` |
| IS-4 | T-11, T-12, T-13, T-18, T-19, T-21, T-22, T-34, T-35 | `mcp-live`/`dsh-discovery-live`/`model-live`/`dsh-local-singlepoint-live`/`same-task-browser-live`/`da-research-live` | `$ACP2_EVIDENCE/C/{mcp-live,dsh-discovery-live,model-live,dsh-local-singlepoint-live,same-task-browser-live,da-research-live}/` |
| IS-5 | T-09, T-10, T-25, T-29 | `bridge-contract` 负向 + `research-record-fixtures` CAS 冲突 | `$ACP2_EVIDENCE/C/{bridge-contract,research-record-fixtures}/` |
| IS-6 | T-17, T-23, T-24 | `manual-browser-fixtures`（关闭模型后仍可提交） | `$ACP2_EVIDENCE/C/manual-browser-fixtures/` |
| IS-7 | T-06, T-07, T-14, T-22, T-36, T-46 | `p2-closeout` + `experience-release-closeout` 的三态校验 | `$ACP2_EVIDENCE/C/{p2-closeout,experience-release-closeout}/` |
| IS-8 | T-01, T-17, T-21, T-31, T-46 | `node frontend/js/module_tests/run_all.js` 全量 + F2 架构审计 | `.omo/evidence/acp2-plan-c-rph-workbench/task-1-...md`, `$ACP2_EVIDENCE/C/same-task-browser-live/` |

**C00–C35 → todo 覆盖表（F1 逐行核对用）：**
C00→T-01；C01→T-02/T-03；C02→T-05；C03→T-06/T-07；C04→T-04；C05→T-08；C06→T-09/T-10；C07→T-11；C08→T-12；C09→T-13；C10→T-15/T-16；C11→T-17；C12→T-18；C13→T-19；C14→T-20；C15→T-21；C16→T-22；C17→T-23/T-24；C18→T-25/T-26；C19→T-27/T-28；C20→T-29/T-30；C21→T-31；C22→T-32；C23→T-33；C24→T-34/T-35；C25→T-36；C26→T-37；C27→T-38；C28→T-39；C29→T-40；C30→T-41；C31→T-42；C32→T-43；C33→T-44；C34→T-45；C35→T-46。
（T-14 是 C00–C09 的收口回执，不新增 C 步骤。）
