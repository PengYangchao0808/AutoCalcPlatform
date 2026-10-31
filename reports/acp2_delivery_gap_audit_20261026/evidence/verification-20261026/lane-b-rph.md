# Lane B — RPH TypeScript 包验证（G05 / G06 / G07 / G08 / G09 / G10 / G17-RPH）

- 验证对象：`/ACPServer/AutoCalcPlatform` @ `ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（2026-10-26 工作树）
- 验证方式：只读源码与既有证据；未修改业务代码、未 commit、未重启服务、未提交 QC、未调用模型 API、未重新 build、未重跑 RPH 测试。
- 复用的既有证据：`evidence/node-probes.json`、`evidence/rph-tests-no-isolation.log`（80/80 pass）、`evidence/rph-build.log`、`rph/packages/*/dist` 编译产物。
- 为确认两个模型的实际接受/拒绝行为，额外执行了两次只读探针：
  1. 用 `/ACPServer/acp2-development/venvs/c/bin/python`（3.11.13）`sys.path.insert(0,'src')` 后调用 `cccp.analysis.research.evidence.EvidenceReport.from_dict(...)`；
  2. 用指定 Node `v22.19.0` 运行已编译的 `rph/packages/cli/dist/index.js --version/--help`。
  两者都不启动服务、不执行科学计算。

## 总结表

| 项 | 结论 | 一句话 |
|---|---|---|
| G05 | 确认 | `runSupervisor` 已导出但无任何产品调用/组合根；这是计划内的 T-29 离线交付 + T-30/T-43 未完成，不是误删；`check-boundaries` 的全局 spawn 禁令会让 T-43 按现计划实现即失败。 |
| G06 | 确认 | 两个 profile 是逐字节复制 B05 验收 launch descriptor 的手写 overlay，CLI 只有 help/version/profile，无生成/校验/doctor。 |
| G07 | 确认（表述需修正） | 不是“两套”而是至少四套 evidence 形状；`science_binding.py:724` 是 B 侧独立实现的 A 形状 validator，不 import CCCP 类；C 侧另有一个 `research/memory.ts` 报告模型。 |
| G08 | 确认 | Supervisor 只做结构校验；`report.project_id/research_id/round/task_ref` 从未被读取，`event.task_ref`/`terminal_event_key` 也未绑定；从未设计 verify/attest 端口。 |
| G09 | 确认 | 单次调用内存状态、单次 wait、固定 `cursor:null`/60s、丢弃返回 cursor；持久化端口只写不读；`SubmitRequest` 甚至没有幂等键。 |
| G10 | 确认 | 默认 planner 硬编码单步 `geometry_optimization`/`r2SCAN-3c`/0/1；`PlannerInput` 没有 charge/spin/constraints 类型化通道；默认 digest 是 round 补零。 |
| G17（RPH） | 确认（措辞需修正） | CLI 可执行但只打印 help/version/profile；“不能启动”应指不能启动研究会话。四个包都是 `private: true`、`0.0.0`、无 `bin`，RPH 不在 Python 发行 BOM 中。 |

---

## G05：DSH–RPH–Supervisor 缺少生产组合根

### 验证结论：**确认**

报告的核心事实成立：RPH 有 Supervisor 状态机与四个纯函数 skill，但没有任何产品装配点把 DSH 客户端、模型适配器、Bridge、ResearchStore、Supervisor 端口接起来。

### 报告原声明不准确处

1. **调用点“只在”两个文件不精确。** 除 `rph/test/research-loop.test.ts` 与 `rph/scripts/research-loop-offline-proof.mjs` 外，本报告自己的审计探针 `reports/acp2_delivery_gap_audit_20261026/probe_node.mjs:22` 也调用了 `runSupervisor`；`rph/test/dist/research-loop.test.js` 是编译副本。结论不受影响，但“只在”应改为“产品代码中没有任何调用点；调用仅存在于测试、离线证明脚本与审计探针”。
2. **`runSupervisor` 并非“未导出”。** `rph/packages/dsh-bundle/src/index.ts:66` 通过 `export * from "../skills/index.js"` 已把它作为包公开面导出（`skills/index.ts:12`）。缺的是调用/装配，不是导出。
3. **报告把 spawn 禁令写成主因的倾向需要收回。** spawn 禁令是一个真实的、会在 T-43 落地时立刻撞上的技术冲突（见根因链），但它不是“当前没有组合根”的原因。当前没有组合根的直接原因是 T-30/T-43 尚未实施，且 T-29 的验收目标本来就是离线证明。

### 证据

**1) dsh-bundle 只描述 profile，不装配 Supervisor**

- `rph/packages/dsh-bundle/src/index.ts:9` `DSH_MCP_CLIENT_PLUGIN`
- `:12-13` `DSH_PROFILE_WEB/HEADLESS`；`:24-26` profile 名单；`:30-37` `BundleProfile` 只有 `name/plugin/bridgeOperations/localOnly`；`:46-60` `bundleProfile()` 只返回冻结描述；`:66` 仅 re-export skills。
- 全文件没有 `SupervisorPorts`、`PlatformClient` 实现、`ResearchStore` 构造或 `runSupervisor` 调用。
- `rph/README.md:30-37` 的命令只有 `install/build/test/check/package`，没有 `start/run/serve`；`rph/package.json:10-15` 的 scripts 同样是 `build/test/check/package`。

**2) 两个 profile 只加载官方 MCP 插件**

- `rph/packages/dsh-bundle/profiles/headless.cordis.yml:17-19` 只 `insert` `@deepseek-ai/dsh-mcp-client`；`:52-60` 禁用 shell/job 类工具。
- `rph/packages/dsh-bundle/profiles/web.cordis.yml:22-24` 同样只 insert MCP client；`:59-80` 额外加载 `dsh-persona`、`dsh-time-context`、`dsh-tool-todo`；`:84-95` 禁用 shell-bearing presets 并设默认 preset。没有 Supervisor/ResearchStore/Bridge 条目。

**3) 全仓调用点**

`grep -rn "runSupervisor"`（排除 node_modules）命中：
- 定义：`rph/packages/dsh-bundle/skills/supervisor.ts:366`
- 测试：`rph/test/research-loop.test.ts:396,426,453,479,491,501,536,560,569,582,600`
- 离线证明：`rph/scripts/research-loop-offline-proof.mjs:251,278,303,337,355,382,410`
- 审计探针：`reports/acp2_delivery_gap_audit_20261026/probe_node.mjs:22`
- 编译产物：`rph/packages/dsh-bundle/dist/skills/supervisor.js`、`rph/test/dist/research-loop.test.js`
- 产品路径（`rph/packages/{cli,dsh-bundle}/src`、`frontend/`、`scripts/acceptance/acp2_experience*.py`）**零调用**。

**4) Bridge 只有可注入接口**

- `rph/packages/bridge/src/index.ts:4-8` 注释明确：“A concrete platform client is injected by the caller (CLI or DSH plugin) in a later task.”
- `:111-116` 仅 `PlatformClient.callTool` 接口。
- 产品代码无实现者；唯一实现是测试夹具 `rph/test/research-fixture-client.ts:113`（`FixturePlatformClient implements ResearchPlatformClient`）。`grep "implements PlatformClient"` 产品目录零命中。

**5) CLI 是显式占位**

- `rph/packages/cli/src/index.ts:1-7`：“At this stage the CLI only parses arguments and prints help/usage… launching the frozen DSH client is a later task.”
- `:16` `CLI_VERSION = "0.0.0"`；`:83-97` 静态 usage；`:104-127` 只处理 help/version/profile。
- `rph/packages/cli/package.json:2-6`：`"private": true`、`"version": "0.0.0"`、description 写“help/usage only until it launches the frozen client”；全文件无 `bin`。

**6) 计划与证据证明这是分阶段交付，不是偶然遗漏**

- `.omo/plans/acp2-plan-c-rph-workbench.md:472-479`：T-29 `[x]`，验收就是“一次离线状态机走查（fixture 驱动）”。
- `:482-488`：T-30/C20b `[~]`，“blocked by T-28 + live model/Controller + A-req-04/B-req-15”。
- `:502-510`：T-32/C22 `[~]`，“blocked by T-30a + B19”。
- `:612-619`：T-43/C32 `[~]`，任务内容正是“在 `rph/packages/cli` 实现…profile/连接配置/研究启动/恢复命令”。
- T-36 至 T-46（P2 收口至最终报告）全部 `[~]`（`:542-647`），形成阻塞级联。
- `.omo/evidence/acp2-plan-c-rph-workbench/task-29-acp2-plan-c-rph-workbench.md:7-11`：“Mode: **offline / fixture only**. No live model and no Controller are delivered for this row… The offline Supervisor proof is the deliverable.”
- 同文件 `:63-65`：control/search/aggregate 都是 `NOT_VERIFIED`，不是 PASS。
- `scripts/acceptance/acp2_experience_scenario_research_loop_live.py:5-13` 文档写明“no live model and no Controller are delivered yet”；`:208-222` 直接把 `model_delivered=False / controller_delivered=False` 写进报告，并返回 `upstream_not_delivered`（不是动态探测失败）。

**7) 验收环境中唯一可用的“启动器”在仓库外，且启动的是 DSH 源码而非产品**

- `/ACPServer/acp2-development/data/acceptance-env-c.sh:361-405` `dsh_acceptance()`：`cd "$ACP2_DSH_WORKSPACE"`，用 `node --import "$ACP2_DSH_LOADER" "$ACP2_DSH_SOURCE/apps/cli/src/bin.ts" --profile ...` 启动冻结 DSH 源码。它不启动 Supervisor，也不来自 RPH 包，且依赖 tsx loader 与 DSH checkout。
- 这解释了“profile 有人用、Supervisor 没人用”的现状：真实验收走的是 DSH 直接调用 MCP 工具，绕开了 RPH 状态机。

**8) spawn 禁令是真实的未来冲突**

- `rph/scripts/check-boundaries.mjs:30-38` 的 `EXECUTION_RULES` 对 `child_process`、`.spawn(`、`.exec(`、`.execFile(`、`ssh2`、DSH 私有路径、平台 store import 全部报错。
- `rph/README.md:20,22` 的冻结依赖表明确写：`@rph/bridge` must not “spawn processes”，`@rph/cli` must not “start a Controller, spawn processes, or execute scientific software”。这与 T-43 要求的“研究启动”直接冲突。
- `:94-112` 对 `PACKAGES = ["research","bridge","dsh-bundle","cli"]` 的 `src` 与 `skills` 目录逐文件执行上述规则，**没有为 cli 开任何例外**。
- 因此 T-43 若真的在 `rph/packages/cli/src` 里 `import { spawn } from "node:child_process"` 启动冻结 DSH，`pnpm --dir rph check` 会失败；`scripts/` 不被扫描，但 `cli/package.json:15` 的 `files: ["dist"]` 意味着放 scripts 里不会成为可安装入口。
- 计划 T-05（`:233`）只禁“在 cli 里启动 Controller 或执行科学软件”，并未明确允许/禁止启动 DSH 客户端；T-43（`:612`）要求“研究启动”，但截至 HEAD 未见对应 gate 修订。

### 根因链

1. **技术根因**：RPH 侧只有纯函数与可注入接口；`@rph/dsh-bundle` 不装配 `SupervisorPorts`，`@rph/bridge` 不实现 `PlatformClient`，`@rph/cli` 只打印 help；DSH profile 只把 MCP 工具直接暴露给模型，没有注册 RPH skill/Supervisor。
2. **设计根因**：C 轨把“可运行的 Supervisor 状态机”和“产品组合根”拆成两个阶段。T-29 明确以离线/夹具证明为交付；真实模型与 Controller 属于 T-30，发布 CLI 属于 T-43。这不是“代码写漏了”，而是验收标准本身就把组合根排除了。
3. **流程根因**：T-30→T-32→T-36→…→T-43→T-46 形成全链 `[~]`；T-43 还依赖 T-42/T-38 与 A-27a。`rph/` 最后一次提交是 2026-10-23 07:15 的 `f413bf8`，之后没有任何装配提交。
4. **信息摘要（引入提交）**：
   - `450f2cb` 2026-10-22 21:07:39 `feat(acp2-c): scaffold standalone rph pnpm workspace`：建立四包骨架、help-only CLI、`check-boundaries.mjs` 的全局 no-spawn/no-private 门。
   - `5e01758` 2026-10-22 22:45:17 `feat(acp2-c): add research-semantics bridge over official MCP tools`：只加 `PlatformClient` 接口与操作矩阵，注释写明客户端“in a later task”。
   - `06d9ffe` 2026-10-23 04:09:18 `feat(acp2-c): wire official DSH MCP plugin profiles`：只接官方 MCP 插件。
   - `f413bf8` 2026-10-23 07:15:44 `feat(acp2-c): implement single supervisor research loop skills`：实现 Supervisor 与 skills，未加组合根；提交信息与 T-29“offline”一致。
   - 没有后续 `feat(acp2-c): assemble rph product` 类提交。
5. **综合判断**：**分阶段交付（库+测试先行，组合根后补），不是遗漏**；但“后补”从未发生。spawn 禁令不是当前缺失的原因，而是 T-43 落地前必须修订的阻塞条件：不仅 `check-boundaries.mjs` 会拒绝 `child_process`，`rph/README.md:22` 还把“spawn processes”写进 `@rph/cli` 的永久 must-not 表；T-43 与这两处需要一并修订，否则实现与文档/gate 直接冲突。

---

## G06：DSH profile 是本机验收配置，未形成可交付配置系统

### 验证结论：**确认**

两个 profile 的 `command/args/cwd/env` 是逐字节复制 B 的验收 launch descriptor，包含本机 venv、源码 cwd、固定 loopback 端口、验收 run_root 与 `ACP_CONFIG`；CLI 没有生成/验证这些值的命令。

### 报告原声明不准确处

1. **“profile 也没有完整用户化身份接入说明”偏重。** 仓库内 `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:326-356` 确实给了带占位符的 overlay 示例与 `dsh_acceptance --patch` 用法，并提醒替换绝对路径；缺的是**产品化参数与校验**，不是完全没有文档。准确说法应是：“只有验收手册级占位示例，没有产品配置系统/身份接入说明”。
2. **锁定版本有据可查这点报告已写对。** `docs/reference/next-change/acp2-contracts/experience/mcp-handshake.md:285-310` 记录了 profile SHA-256 与 B05 descriptor SHA-256；我复核 `sha256sum` 与文档一致（web `ca9294b0…`，headless `2d814146…`，B05 `ca807cc4…`）。

### 证据

**1) 硬编码路径逐项在场**

- headless：`profiles/headless.cordis.yml:23` `command: /ACPServer/acp2-development/venvs/b/bin/python`；`:35` `cwd: /ACPServer/AutoCalcPlatform/src`；`:37` `ACP_RUN_ROOT: /ACPServer/acp2-development/data/b/run-root`；`:41` `ACP_CONFIG: /ACPServer/acp2-development/data/b/controller-config/cccp.yaml`；`:42` `PATH: /ACPServer/acp2-development/venvs/b/bin:...`；`:32` `--controller http://127.0.0.1:18765`。
- web：`profiles/web.cordis.yml:28` 同一 python；`:40` 同一源码 cwd；`:42` 同一 run_root；`:46` 同一 ACP_CONFIG；`:47` 同一 PATH；`:37` 同一端口。
- 两份文件头注释均写：“command/args/cwd/env below are byte-for-byte the B05 launch descriptor (sha256 ca807cc4…)” (`headless:9-10`、`web:11-13`)。

**2) 该 descriptor 本身是验收环境**

- `reports/acp2_implementation/bb45a46/B/B05/mcp-launch.json`：`command` 是 `venvs/b/bin/python`，`cwd` 是 `/ACPServer/AutoCalcPlatform/src`，`env.ACP_RUN_ROOT` 是 `data/b/run-root`。
- `mcp-handshake.md:302-310` 明确写：“command/args/cwd/env are byte-equal to B05 mcp-launch.json” / “were compared field-by-field … equal”；`:285-290` 说明两个 profile 是“C-owned, shipped under the bundle”的验收 overlay。

**3) CLI 没有生成/验证命令**

- `rph/packages/cli/src/index.ts:46-80` 只解析 `--help/--version/--profile`；`:116-126` 的 `main()` 只输出 help/version/profile 名；`:120-122` 的 `--profile` 仅回显名字，不检查路径、端口、握手或权限。
- `rph/packages/cli/package.json:19-20` 仅依赖 `@rph/dsh-bundle`，没有配置生成/doctor 依赖。
- 全仓 `grep "cordis.yml"` 未发现任何产品侧生成器；只有验收脚本在运行时写**模型路由** overlay（`scripts/acceptance/acp2_experience_scenario_model_live.py:563-580`、`scripts/acceptance/acp2_experience_budget_proxy.py:2597-2620`），并不生成 ACP MCP launch 配置。

**4) 身份不在 profile 中，由服务端解析**

- `src/acp_services/entrypoints/mcp.py:15-18`：“Identity is server-enforced. The client may only *hint* a project id; tenant/tier/principal are derived from the verified local principal…”。因此 profile 不含凭据是合理设计；但这也意味着报告所说“项目身份、审批权和预算的服务端约束”不能靠 profile 达成，需要 T-41/B15 等未完成项。

### 根因链

1. **技术根因**：profile 把 B05 验收 descriptor 内联为 YAML 常量；没有变量、模板、环境分层或 `acp doctor` 类校验；CLI 只做无副作用的参数回显。
2. **设计根因**：T-12 的验收目标是“真实 DSH 发现真实 ACP MCP 工具”，因此要求 profile 与 B05 隔离环境逐字节一致（`mcp-handshake.md:302-310`）。这天然产生“本机验收配置”，而不是可发布配置。
3. **流程根因**：参数化模板、profile/连接配置/研究启动命令属于 T-43（`:612-619`），而 T-43 位于 `[~]` 级联末端（依赖 T-42/T-38 + A-27a）。没有独立的“配置生成/校验”todo 被提前交付。
4. **信息摘要**：
   - `06d9ffe` 2026-10-23 04:09:18 `feat(acp2-c): wire official DSH MCP plugin profiles`：一次提交新增 95 行 web profile、61 行 headless profile，内容直接取自 B05；提交说明只覆盖“wire profiles”，没有模板化。
   - `450f2cb` 建立的 help-only CLI 至今未扩展。
5. **结论**：报告的事实成立；“未形成可交付配置系统”的根因是**验收 profile 与发布 profile 没有分离**，且发布侧 T-43 未实施。

---

## G07：EvidenceReport 模型不一致，没有定义可信转换

### 验证结论：**确认（核心结论成立；数量与技术细节需修正）**

同一份 `tests/fixtures/acp2/science/contracts/evidence/supported_valid.json` 被 CCCP 接受、被 RPH 拒绝；两边字段语义不同，且没有任何 adapter。

### 报告原声明不准确处

1. **不是“两套”，至少是四套。**
   - CCCP：`src/cccp/analysis/research/evidence.py:1030` `EvidenceReport`。
   - RPH dsh-bundle：`rph/packages/dsh-bundle/skills/models.ts:80` `EvidenceReport`（`acp2.evidence.report.v1`，带 `decision`）。
   - RPH research：`rph/packages/research/src/memory.ts:482` 另一个 `EvidenceReport`（`report_id/project_id/claims`，claim 带 `validator_id`，无 `schema_version/subject/execution`）。
   - B 运行时：`src/acp_services/runtime/science_binding.py:724` `validate_evidence_report`，独立实现 A 形状校验。
   所以“两套”只在“CCCP vs RPH dsh-bundle”这个对比上成立，全仓实际有四个互不复用的 evidence 合同/校验器。
2. **行号有偏移。** `models.ts` 的 `EvidenceReport` 接口在 `:80-92`，`:72` 是 `EvidenceClaim.statement`；`supervisor.ts` 的报告选择在 `:639-647`，不是 `:594`（`:594` 是 `persistTaskRef` 的 catch 结束附近）。
3. **`science_binding.py:724` 不是“接受 CCCP 模型”的类型级实现。** 它不 import `cccp.analysis.research.evidence.EvidenceReport`，而是按 A 的 wire shape 重新实现结构校验（`schema_version==1`、`subject`、`execution.terminal_state`、`claims`、`limitations`）；`register_evidence_report` (`:797-850`) 负责把 A 报告原样发布到 B12 store，但 `grep` 显示 `src/` 内没有生产调用者。准确说法是“B 侧按 A 的合同校验，不是导入 CCCP 类型”。
4. **RPH 内部也没有统一。** `research/memory.ts:596` 的 `validateEvidenceReport` 与 `dsh-bundle/skills/models.ts:244` 同名但不同合同；两者都被各自测试覆盖（`rph/test/research-memory.test.ts:289`、`rph/test/research-loop.test.ts:598`），却没有互相转换。

### 证据

**1) CCCP 形状（执行事实与 claim 支持度分离）**

- `src/cccp/analysis/research/evidence.py:1030-1039`：`report_id, subject, execution, claims, limitations, provenance_digest, schema_version`。
- `:192-198` `_REPORT_KEYS` 只允许上述字段，未知字段被 `_reject_unknown` 拒绝（`:319`、`:1085`）。
- `:723-736` `Claim` 带 `claim_id/statement/status/scope/source_refs/missing_checks/validator/limitations`。
- `:490-527` `EvidenceValidator`：`kind` 必须是 `deterministic|software|human`；注释 `:505-506` “model/LLM text is never a validator”。
- `:798-835` `Claim.from_dict` 要求 `source_refs`，`supported/refuted` 无来源会被拒（`:777`）。
- 没有 `decision` 字段；执行终态在 `execution.terminal_state` 中，claim 支持度单独表达。

**2) RPH 形状（round 决策混入报告）**

- `rph/packages/dsh-bundle/skills/models.ts:80-92`：`schema/report_id/project_id/research_id/round/task_ref/status/decision/claims/evidence_refs/terminal_event_key`。
- `:199-241` `EVIDENCE_REPORT_SPEC` 明确要求 `decision ∈ {accept,revise,stop}`、`status ∈ {complete,partial,missing,failed}`；`claims[].evidence_refs` 仅是字符串数组（`:211`）。
- `:248-263` `blockingClaimStatus`：`refuted`/`inconclusive` 阻断；`decideFromReport`（`supervisor.ts:346-363`）直接用 `report.decision` 决定 accept/revise/stop。

**3) 同 fixture 的双向结果（既有证据 + 本次只读复核）**

- 既有探针 `evidence/node-probes.json.science_report_rejected_by_rph`：RPH 报 `$.schema required`、`$.project_id required`、`$.research_id required`、`$.round required`、`$.task_ref required`、`$.decision required`，并把 `$.schema_version/$.subject/$.execution/$.limitations` 判为 `additional_property`；claim 侧把 `scope/source_refs/validator/missing_checks/limitations` 判为 `additional_property`，要求 `evidence_refs/reason`。
- 本次用 C venv 3.11.13 直接调用 CCCP：
  `EvidenceReport.from_dict(supported_valid.json)` → 成功，`report_id=report-supported-valid`，claim 状态 `['supported']`，limitations `['lim-single-conformer']`。
- 结论：不是 JSON 外壳转换，字段语义与必填集合都不同。

**4) B 侧按 A 形状绑定**

- `src/acp_services/runtime/science_binding.py:674-680` 注释：“EvidenceReport registration (storage is B12; interpretation is A)”；`:686-688` 明确“claim_statuses preserves A's vocabulary verbatim. There is no claim_supported field by design”。
- `:724-765` 校验 `schema_version==1`、`subject.subject_id`、`execution.terminal_state`、`claims[].status`；`:756-762` 要求 `supported/refuted` 必须有 `source_refs`。
- `:797-850` `register_evidence_report` 原样序列化 A 报告并发布；**没有** C 的 `decision/round/task_ref` 映射。

**5) 跨轨信息隔离的书面记录**

- `docs/reference/next-change/acp2-contracts/experience/requests/A-req-01.md:22-24`：“No committed A science schema is visible from the C worktree. The T-02/T-03 baseline records `NOT_VERIFIED(a_science_handoff)`; C references the recipe by digest only.”
- 同文件 `:119` 继续要求保持 `NOT_VERIFIED(a_science_handoff)` 直到 A 交付。
- `docs/reference/next-change/acp2-contracts/science/evidence-semantics.md:3-8`：Owner A，Consumers B 和 C；“execution facts kept separate from claim status”。
- C 计划 `.omo/plans/acp2-plan-c-rph-workbench.md:36`（IS-5）只说“`ResearchRecord` 与 `EvidenceReport` 分存”，没有定义 A→C 的 evidence adapter；T-29（`:473`）要求“由真实 EvidenceReport 决定下一步”，但 C 侧把 round/decision 直接做进了自己的报告模型。

### 根因链

1. **技术根因**：两套模型字段不相交（A 有 `subject/execution/limitations/validator/source_refs`，C 有 `round/decision/task_ref/evidence_refs`），且没有转换函数/适配器；C 也不消费 A 的 schema。
2. **设计根因**：A 把 EvidenceReport 定义为“执行事实 + claim 支持度”的科学证据；C 把它定义为“某一研究轮次的控制决策输入”。两个同名对象服务于不同层，C 没有把研究决策拆成独立的 `ResearchDecision` 投影。
3. **流程根因**：C 在独立 worktree 中开发，看不到 A 的 science schema（A-req-01 明文记录），因此自行发明了 `acp2.evidence.report.v1`；跨轨没有“EvidenceReport adapter”任务，B 后来按 A 形状实现 `science_binding`，也没有补 C 适配。
4. **信息摘要（引入提交）**：
   - A：`2503f39` 2026-10-22 20:33:33 `docs(cccp): add ACP2 evidence, provenance and recipe schemas`（`evidence.schema.json` 等）；`243d2b7` 2026-10-23 07:42:39 `feat(cccp): assemble ACP2 Diels-Alder evidence report`；`0659eca` 2026-10-23 11:39:31 `feat(cccp): add evidence and provenance science types`（`EvidenceReport` 类）。
   - C：`f413bf8` 2026-10-23 07:15:44 `feat(acp2-c): implement single supervisor research loop skills`（`models.ts` 引入 `acp2.evidence.report.v1`，比 CCCP 类型提交早约 4 小时，比 A 的 schema 文档晚约 11 小时）。
   - B：`938e894` 2026-10-23 16:12:08 `feat(runtime): bind science recipes and evidence`（按 A 形状校验并发布）。
5. **补充**：RPH 的 `research/memory.ts`（`ca85029` 2026-10-23 07:08:36 `feat(acp2-c): scope research memory by authorization and version`）又定义了第三种报告校验（含 `validator_id`，禁止模型来源），说明 RPH 内部也没有共享单一报告合同。

---

## G08：Supervisor 接受其他项目、任务和 attempt 的报告

### 验证结论：**确认**

Supervisor 从 wait 结果里选第一份 `report !== null` 的终态事件（随后才做结构校验），从不核对它是否属于本轮提交的 TaskRef/project/research/round，也不核对来源摘要；`event.task_ref` 与 `report.terminal_event_key` 都未被使用。

### 报告原声明不准确处

1. **行号偏移。** 选择逻辑实际在 `rph/packages/dsh-bundle/skills/supervisor.ts:639-647`；wait 在 `:599-606`；无终态退出在 `:626-633`。报告写的 `:594-660` 覆盖了这段区域，但不是精确的“选择逻辑”起点。
2. **“检查结构”应更准确。** 它只调用 `validateEvidenceReport`（`models.ts:244-246`）验证 required/enum/type/additionalProperties；`TaskRef` 只校验 `attempt>=1`（`models.ts:114`），不校验 project/job/release 的任何绑定关系。
3. **“当前模块没有验证来源内容的端口”应加一句“从未设计过”。** `git show f413bf8:.../supervisor.ts` 中也没有 `verify/attest/source` 端口；不是“预留未实现”，而是端口集合从一开始就没有这一职责。

### 证据

**1) 报告选择与决策路径**

- `supervisor.ts:599-606`：每轮一次 `ports.wait({project_id, round, task_ref: submitted.task_ref, cursor: null, max_events: 200, max_wait_ms: 60_000})`。
- `:626-633`：`terminalEvents = fresh.filter(TERMINAL_EVENT_TYPES.has(type))`；空则 `wait_timeout`/`duplicate_terminal`。
- `:639`：`const rawReport = terminalEvents.find((event) => event.report !== null)?.report ?? null;` —— 只要事件里带报告，无论其身份。
- `:640-647`：只做 `validateEvidenceReport`；失败 `invalid_report`，空 `no_evidence`。
- `:648-656`：`persistReport`（可选）后 `reports.push(report); lastReport = report`。
- `:683-689`：`decideFromReport(report)` 直接看 `report.decision` 与 claim 状态。

**2) 身份字段从未被读取**

`grep -n "report\.project_id\|report\.research_id\|report\.round\|report\.task_ref\|report\.terminal_event_key\|report\.report_id\|report\.status"` 在 `supervisor.ts` 只命中 `:474` 的 `report.report_id`（用于 `prior_hypotheses` 列表）。也就是说：
- `report.project_id`、`report.research_id`、`report.round`、`report.task_ref`、`report.terminal_event_key` **一次都没读**；
- `event.task_ref` 除类型定义外也未被读取；wait 只把自己提交的 `task_ref` 发出去，不比对返回事件的 `task_ref`。

**3) 报告模型把 evidence 引用降级为自由字符串**

- `models.ts:74` `EvidenceClaim.evidence_refs: readonly string[]`
- `models.ts:90` `EvidenceReport.evidence_refs: readonly string[]`
- `:211` `stringArray({unique:true})`，无 digest/artifact 路径/角色约束。
- `:108-117` `TASK_REF_SPEC` 的 `project_id/job_id/release_id` 都只是任意非空字符串。

**4) 端口集合没有验证职责**

- `supervisor.ts:194-202`：`preflight/admission/approve/submit/wait` + 可选 `persistTaskRef/persistReport`，无 verify/attest/source-content 端口。
- `git show f413bf8:rph/packages/dsh-bundle/skills/supervisor.ts | grep -n "verify\|attest\|source"` 无命中。

**5) 现有测试矩阵没有身份负例**

`rph/test/research-loop.test.ts` 的 11 个 supervisor 用例（`:394-603`；该文件共 15 个 test，另 4 个是 skill schema）覆盖 happy path、no-evidence、duplicate、round cap、preflight/admission、refuted/inconclusive、wait-no-model、budget cap、invalid report；`grep "other-project|foreign"` 零命中。因此跨身份报告从未被测过，直到本轮探针才发现。

**6) 复现证据**

- `evidence/node-probes.json.foreign_report`：`stop_reason=accepted`、`status=completed`、`submits=1`；probe 把 event/report 的 project/research/job/attempt/release 全部替换（`probe_node.mjs:25,39`）。
- `evidence/node-probes.json.partial_report`：`status=partial` 仍 `accepted`（`probe_node.mjs:40`）——`reportHasEvidence`（`models.ts:267-272`）只把 `missing/failed` 当无证据，`partial` 不算阻断。

### 根因链

1. **技术根因**：报告选择函数以“terminal event 的第一份非空 report”作为事实，缺少四类绑定检查（project/research/round、TaskRef 的 job/attempt/release、terminal_event_key/cursor、plan/evidence 摘要）。`validateEvidenceReport` 只验证形状。
2. **设计根因**：Supervisor 把 `wait` 端口视为“已由平台验证过的可信来源”，因此没有 verify/attest 端口；报告模型又把 `decision` 当作可直接消费的控制字段，却没有对应来源证明。`supervisor.ts:16-17` 的不变量“由真实 EvidenceReport 决定”中的“真实”没有实现为可检查条件。
3. **流程根因**：T-29 验收只列 stop 条件（offline proof 的 7 cases/30 assertions），没有“错误身份必须拒绝”的负例；验收脚本也不构造外来报告。该漏洞因此通过了 `[x]`。
4. **信息摘要**：`f413bf8` 2026-10-23 07:15:44 `feat(acp2-c): implement single supervisor research loop skills` 一次性引入 Supervisor、Models 与测试文件（15 个 test，其中 11 个 supervisor 用例），端口集合与测试矩阵同时定型，之后未修改。没有引入/修正 verify 端口的提交。

---

## G09：长任务等待与研究恢复没有持久闭环

### 验证结论：**确认**

`runSupervisor` 是单次调用内的内存状态机：每轮只 wait 一次、固定 `cursor:null`、固定 60s、丢弃返回 cursor；无 checkpoint 读取入口；持久化端口只写不读；`SubmitRequest` 甚至没有幂等键。

### 报告原声明不准确处

1. **行号偏移。** 每次调用新建 `used/seenEvents/taskRefs` 实际在 `supervisor.ts:371-380`（`:366` 是函数声明）；wait 在 `:599-606`；无终态退出在 `:626-633`。
2. **阻塞原因的描述需要修正。** 报告未提 B19；计划把 T-32 标成 “blocked by T-30a + B19 — persistent cursor/snapshot not delivered”，但 `reports/acp2_implementation/bb45a46/B/B19/summary.json` 显示 B19a 合同已交付（`runtime/events-recovery.v1.json`，17 个 fixture 测试通过）、B19b PASS（C DSH 行单独 `NOT_VERIFIED`，有模拟行 `research-recovery.json`，见 `dsh-not-verified.json`）。因此“B19 未交付”不是当前 C 侧无恢复入口的主因；主因是 C 的 T-32 没实现、T-30a 没有 live 轮次可恢复。
3. **报告说“没有读取 checkpoint/resume 入口”正确，但可补充**：`SupervisorInput`（`:90-103`）只有 `prior_evidence` 一个跨调用输入；`used`/`seenEvents`/`taskRefs`/`round` 都无法回灌。

### 证据

**1) 内存状态与单次 wait**

- `supervisor.ts:371-380`：`const phases=[]; const used=zeroBudget(); const taskRefs=[]; const reports=[]; const seenEvents=new Set(); let submits=0; ... let round=0;` —— 全部在函数体内新建。
- `:599-606`：`waitInvocations += 1; const waited = await ports.wait({... cursor: null, max_events: 200, max_wait_ms: 60_000 })`。
- `grep "waited\."` 只命中 `:611` 的 `waited.events` 和 `:623` 的 `waited.events.length`；**`waited.cursor` 从未被读取**（`WaitResult.cursor` 定义在 `:191`）。
- `:626-633`：没有终态事件立即返回 `wait_timeout`（或 `duplicate_terminal`），不会用返回的 next cursor 继续等待。
- 探针 `evidence/node-probes.json.first_wait_empty`：`stop_reason=wait_timeout`，`wait_requests` 长度 1，返回 `cursor-next` 但未被使用（`probe_node.mjs:20,41`）。

**2) 持久化端口只写不读**

- `supervisor.ts:200-201`：`persistTaskRef?(ref): Promise<void>`、`persistReport?(report): Promise<void>` 都是可选写端口。
- `:588-594`、`:648-654`：调用前只判断 `!== undefined`，没有读取/恢复 API。
- `:90-103` `SupervisorInput` 只有 `prior_evidence` 可带入上一轮报告，不包含 used budget、seen event keys、task refs 或 round。

**3) 提交合同没有幂等键**

- `supervisor.ts:159-165` `SubmitRequest`：`project_id/round/plan_definition_digest/approval_ref/plan`，**没有 `idempotencyKey`**。
- `rph/packages/bridge/src/index.ts:62-63` 的 `OPERATION_FIELDS.submit` 要求 `project + input + idempotencyKey`；Supervisor 的提交端口无法直接满足 Bridge 的提交合同，也没有从 `plan_definition_digest` 派生 key 的约定。
- 因此报告建议的“提交意图/幂等键先持久化，再提交与绑定 TaskRef”在当前端口设计下无处落地。

**4) 没有恢复入口**

- `runSupervisor` 的唯一输入是 `SupervisorInput` + `SupervisorPorts` + `SupervisorSkills`；没有 `resume(state)`、`loadCheckpoint()` 或 `prior_used` 参数。
- `grep "TODO|FIXME|checkpoint|resume" rph/packages/dsh-bundle/skills/*.ts`：无 TODO/FIXME；`resume` 只出现在 bridge 的 P2 操作名（`bridge/src/index.ts:413,447,610-615`），不是 Supervisor 实现。
- `.omo/plans/acp2-plan-c-rph-workbench.md:502-510`：T-32（DSH 会话与持久研究轮次恢复）仍是 `[~]`。

**5) 真实恢复合同的现状（B 侧）**

- `reports/acp2_implementation/bb45a46/B/B19/summary.json`：`b19a.fixture_level_recovery_without_dsh=true`、`fixture_tests_passed=17`、handoff `runtime/B19a-to-C.json`；`b19b` 的 `new_session_resumed_from_cursor=true`、`duplicate_submit_same_task=true`，但 `dsh-not-verified.json` 记录真实 C DSH 行 `NOT_VERIFIED(c_dsh_absent)`，使用 `simulated=true` 行。说明 cursor/恢复合同已有，C 侧未接。

### 根因链

1. **技术根因**：Supervisor 没有持久状态对象；wait 结果 cursor 被丢弃；可选持久化端口只写；提交合同无幂等键；没有 checkpoint 读取入口。
2. **设计根因**：T-29 把 Supervisor 设计为“bounded single wait + 内存预算”的纯状态机（`supervisor.ts:9-23` 的 invariants 明确“no persistence backdoor”）；持久恢复被整体划给 T-32，而 T-29 的验收只证明离线停止条件，不证明跨进程恢复。
3. **流程根因**：T-32 依赖 T-30a（live 轮次）与 B19；T-30 本身 `[~]`，因此没有真实轮次可恢复。B19a 合同已交付，但 C 侧没有消费提交。
4. **信息摘要**：`f413bf8` 2026-10-23 07:15:44 `feat(acp2-c): implement single supervisor research loop skills` 引入该设计；`rph/` 此后无提交。T-32 从未开始。

---

## G10：默认 Planner 的科学参数与摘要是离线占位策略

### 验证结论：**确认**

默认 planner 对所有输入输出同一个单步几何优化计划、固定 `r2SCAN-3c/identity/charge=0/multiplicity=1`；默认 supervisor skills 直接绑定这些 deterministic runner；未提供 `plan_definition_digest` 时用 round 数字补零生成摘要。

### 报告原声明不准确处

1. **行号偏移。** `roundDigest` 在 `supervisor.ts:278-280`，调用在 `:485`；报告写的 `:281` 是 `roundDigest` 与 `addPhase` 之间的空行。
2. **“constraints 明确包含 charge=1/multiplicity=2”需要更准确。** probe 的 `constraints` 确实含这两项（`probe_node.mjs:11`），但 Supervisor 只把 constraints 传给 interpreter；interpreter 把它放进 `assumptions`（`interpreter.ts:114`），而 planner 完全不读 `assumptions`/`constraints`（`grep` 在 `planner.ts` 只命中输出里的 `charge/multiplicity`）。所以不是“planner 忽略了 constraints 文本”，而是**合同里根本没有把电荷/自旋/约束作为类型化字段传给 planner**。
3. **“默认 Planner 的科学参数与摘要是离线占位策略”成立，但需说明这是 T-29 的明确设计。** `.omo/evidence/acp2-plan-c-rph-workbench/task-29-acp2-plan-c-rph-workbench.md:7-11` 写“offline / fixture only… The offline Supervisor proof is the deliverable”，`planner.ts:168` 注释也写“Deterministic offline planner; a live model may replace it only via validation”。

### 证据

**1) 硬编码计划**

- `planner.ts:169-226` `runPlanner`：只校验输入、clamp budget、回显字段。
- `:197-219` `science`：
  - `:201` `capability: "geometry_optimization"`
  - `:202` `version: "1.0.0"`
  - `:203` `parameters: { max_cycles: 100 }`
  - `:214` `method: "r2SCAN-3c"`
  - `:215` `mapping: "identity"`
  - `:216` `charge: 0`
  - `:217` `multiplicity: 1`
  - `:218` `allowed_substitutions: []`
- 无任何分支读取 `input.hypothesis`/`normalized_question`/`critique_findings` 来改变科学参数；`hypothesis` 只被回显，`critique_findings` 只进 `notes`。

**2) Planner 合同没有电荷/自旋通道**

- `planner.ts:26-45` `PlannerInput` 字段：`project_id/round/hypothesis/normalized_question/recipe_digest/plan_definition_digest/initial_input/critique_findings/evidence/remaining_budget`。
- 没有 `charge`、`multiplicity`、`constraints`、`electronic_state` 或 `question_spec` 字段；`initial_input` 只有 `input_id/sha256/format/role`。
- `interpreter.ts:97-117` 把 `constraints` 复制到 `assumptions`（`:114`）；`supervisor.ts:468-472` 只把 `normalized_question/scope/assumptions` 传给 synthesizer，plan 阶段（`:515-529`）不传 assumptions。
- 因此即使换成一个真实的模型 planner，当前 I/O 合同也没有权威字段可填电荷/自旋；只能从自由文本猜。

**3) 默认 skills 绑定 deterministic runner**

- `supervisor.ts:113-118`：`DEFAULT_SUPERVISOR_SKILLS = { interpret: runInterpreter, synthesize: runSynthesizer, review: runReviewer, plan: runPlanner }`。
- `:369` `runSupervisor` 默认参数就是 `DEFAULT_SUPERVISOR_SKILLS`。
- 全仓 `grep "adapter|live model|model adapter"` 只命中 `planner.ts:168` 的注释；没有生产 adapter 模块。

**4) 摘要不是计划内容摘要**

- `supervisor.ts:278-280`：
  ```ts
  function roundDigest(round: number): string {
    return `sha256:${round.toString(16).padStart(64, "0").slice(-64)}`;
  }
  ```
- `:485`：`const digest = (input.plan_definition_digest ?? roundDigest)(round);`
- `planner.ts:196`：`plan_definition_digest: input.plan_definition_digest`（原样复制，不计算内容）。
- `planner.ts:4-8` 注释说明“this package does not re-implement the frozen canonical digest (that lives in `@rph/research` and the platform)”。
- 探针 `evidence/node-probes.json.baseline.plan`：首轮 `plan_definition_digest` 为 `sha256:000…000`，science 为 `r2SCAN-3c/identity/0/1`；输入 constraints 是 `charge=1,multiplicity=2`（`probe_node.mjs:11`）。

### 根因链

1. **技术根因**：`runPlanner` 是无分支的常量生成器；`PlannerInput` 无电荷/自旋/约束字段；`plan_definition_digest` 由调用方传入，缺省时退化为 round 编号；默认 skills 直接绑定该生成器。
2. **设计根因**：C 轨把“研究规划器”拆成 T-29（确定性离线 planner，用于证明状态机）与 T-30（真实模型/Controller 轮次）。`SupervisorSkills` 接口是预留的替换缝，但没有实现“生产 adapter 缺失即拒绝启动”：默认值静默落到 fixture planner。
3. **流程根因**：T-30/C20b 仍是 `[~]`，其前置（live model、Controller、A-req-04/B-req-15）未齐；没有任务专门实现“模型 planner adapter + 电荷/自旋合同”。
4. **信息摘要**：`f413bf8` 2026-10-23 07:15:44 一次性引入 deterministic planner 与默认绑定；`.omo/evidence/.../task-29...md:7-11` 明确这是 offline 交付。没有后续 adapter 提交。

---

## G17（RPH 部分）：CLI 无 bin、0.0.0、不能启动研究

### 验证结论：**确认（“不能启动”需限定为不能启动研究会话）**

`@rph/cli` 没有 `bin` 字段、版本 `0.0.0`、`private: true`，只能打印 help/version/profile；四个 RPH 包全部如此。它不是可安装为 `rph` 命令、可启动研究的产品入口。

### 报告原声明不准确处

1. **“不能启动”容易被读成二进制无法执行。** 我用指定 Node 实际执行了编译产物：
   - `node rph/packages/cli/dist/index.js --version` → `rph 0.0.0`，退出 0；
   - `node rph/packages/cli/dist/index.js --help` → 打印 usage，末行 “This build only prints help and never starts a Controller or executes science.”
   准确说法：**CLI 可执行，但只输出 help/version/profile，不能启动研究、DSH 会话或任何平台连接。**
2. **不只是 CLI 是 0.0.0。** `@rph/research`、`@rph/bridge`、`@rph/dsh-bundle`、`@rph/cli` 四个 `package.json` 都是 `version: 0.0.0`、`private: true`、无 `bin`；根 `rph/package.json` 也是 `0.0.0` + `private: true`。
3. **报告说“RPH CLI 无 bin”正确，但它不是唯一的发行缺口**：`rph/dist-packages/` 不存在（`.gitignore` 忽略），`experience/release-manifest.json` 与 `experience/static-assets.manifest.json` 在仓库与 `/ACPServer/acp2-development/C` 都不存在，Python 的 r3 release manifest 只列 `cccp/acp-web/acp-worker` 三个 Python 角色，不含 RPH。

### 证据

**1) CLI 源码与包元数据**

- `rph/packages/cli/src/index.ts:1-7`：help-only 注释；`:16` `CLI_VERSION = "0.0.0"`；`:83-97` usage；`:104-127` `main()` 只处理 help/version/profile；`:129-132` 直接执行 shim。
- `rph/packages/cli/package.json:2-6`：`"name": "@rph/cli"`, `"version": "0.0.0"`, `"private": true`, description “RPH user entry point (help/usage only until it launches the frozen client)”；`:7-14` `main/types/exports` 指向 `dist`；**无 `bin` 字段**。
- 运行验证（只读）：`/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node rph/packages/cli/dist/index.js --version` 输出 `rph 0.0.0`；`--help` 输出 usage 与 “This build only prints help…”。

**2) 四个包均为 0.0.0/private**

- `rph/packages/research/package.json`、`bridge/package.json`、`dsh-bundle/package.json`、`cli/package.json`：均为 `version: 0.0.0`、`private: true`、无 `bin`（逐一读取确认）。
- `rph/package.json:3-4`：`version: "0.0.0"`, `private: true`。

**3) 没有发行产物/清单**

- `ls rph/dist-packages` → 不存在；`rph/.gitignore` 忽略 `dist-packages/` 与 `*.tgz`。
- `find . -name release-manifest.json` 只命中历史 `reports/acp2_*/...`，没有 `experience/release-manifest.json`；`find . -name static-assets.manifest.json` 零命中。
- `reports/acp2_final_acceptance_20261024/release-r3/release-manifest.json` 的 `roles` 只有 Python 角色；`versions` 是 `pyproject.toml/src/acp/__init__.py/src/cccp/...=0.3.0`；`source` 指向 `ec4a0b3`。RPH 不在 BOM 中。
- `/ACPServer/acp2-development/C/experience/` 不存在（C worktree HEAD `39d4aeb`；`rph/` 源码与本次工作树一致，仅 build 产物差异）。

**4) 计划状态**

- `.omo/plans/acp2-plan-c-rph-workbench.md:612-619` T-43 `[~]`；`:622-630` T-44 `[~]`；`:632-640` T-45 `[~]`；`:642-650` T-46 `[~]`。
- T-43 的任务正文明确要“实现基于冻结 DSH 公共入口的 profile/连接配置/研究启动/恢复命令；导出可安装 RPH 包…提供静态资产 manifest”；这些都没有交付（无 `bin`、无 `dist-packages`、无 `experience` 清单）。

### 根因链

1. **技术根因**：CLI/四包仍是 scaffold 占位（版本/隐私/无 bin/help-only）；没有构建与发布产物；没有 release manifest/static assets manifest。
2. **设计根因**：RPH 发行被安排在 C32–C35（T-43–T-46），依赖 T-42/T-38 与 A-27a；在依赖未满足前，CLI 被有意保持为无副作用 help-only（`cli/src/index.ts:1-7`）。
3. **流程根因**：T-36 以后全部 `[~]`，P5 发行收口未完成；B25 的 Python 三角色/BOM 没有把 RPH 纳入，因此“Python 分包已有成果”不能外推到 RPH。
4. **信息摘要**：`450f2cb` 2026-10-22 21:07:39 scaffold 即写入 help-only CLI 与 `private: true/0.0.0`；`f413bf8` 2026-10-23 07:15:44 只加 skills，不改 CLI；之后无 RPH 发行提交。

---

## 对报告的修正建议

1. **G05**
   - 把“全仓调用点只在 test 与 offline-proof”改为“产品代码零调用；调用仅存在于 test、offline-proof 与审计探针 `probe_node.mjs`”。
   - 明确 `runSupervisor` 已由 `dsh-bundle/src/index.ts:66` 导出，缺的是装配不是导出。
   - 根因建议写成：“T-29 明确 offline（`task-29` evidence `:7-11`），T-30/T-43 仍 `[~]`，因此组合根按计划尚未交付；`check-boundaries.mjs:30-38,94-112` 的全局 child_process 禁令与 `rph/README.md:22` 的 `@rph/cli` must-not “spawn processes” 是 T-43 落地前必须解除/例外化的阻塞，不是当前缺失的原因。”
   - 建议补一句：验收环境中的启动器 `dsh_acceptance` 位于 `/ACPServer/acp2-development/data/acceptance-env-c.sh:361-405`，启动 DSH 源码而非 RPH Supervisor，不构成产品组合根。
2. **G06**
   - 建议写明 profile 与 B05 的关系：“`command/args/cwd/env` 逐字节等于 `B/B05/mcp-launch.json`（`mcp-handshake.md:302-310`，sha256 `ca807cc4…`），profile 本身的 sha256 为 `ca9294b0…`/`2d814146…`（`mcp-handshake.md:289-290`）。”
   - “没有完整用户化身份接入说明”改为“只有 runbook §5.1 的占位示例（`ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:326-356`），没有产品化参数/校验/doctor；MCP 身份由 `mcp.py:15-18` 服务端解析，profile 不承载身份。”
3. **G07**
   - 把“两套 EvidenceReport”改为“至少四套 evidence 形状”：CCCP `evidence.py:1030`、RPH `skills/models.ts:80`、RPH `research/memory.ts:482`、B `science_binding.py:724`。
   - 行号修正：`models.ts:80-92`（非 `:72`）、报告选择 `supervisor.ts:639-647`（非 `:594`）。
   - 把“`science_binding.py:724` 接受的是 CCCP 模型”改为“B 侧按 A 的 wire shape 独立校验（不 import CCCP 类型）；`register_evidence_report` 无生产调用者”。
   - 建议补入 A-req-01 `:22-24` 的“C worktree 看不到 A science schema / `NOT_VERIFIED(a_science_handoff)`”作为跨轨根因证据。
4. **G08**
   - 精确行号：wait `:599-606`、终态筛选 `:626-633`、报告选择 `:639-647`、决策 `:683-689`。
   - 补充三条更硬的证据：`report.project_id/research_id/round/task_ref` 在 supervisor 中从未被读取；`event.task_ref` 未被比对；`report.terminal_event_key` 未与本轮 event key 绑定。
   - 明确“没有 verify/attest 端口”是**从未设计**（`git show f413bf8` 无 verify/source），不是“预留未实现”。
   - 补充测试缺口：11 个 supervisor 用例无跨项目/外来报告负例，所以该漏洞通过了 T-29 `[x]`。
5. **G09**
   - 行号修正：状态初始化 `:371-380`、wait `:599-606`、timeout `:626-633`。
   - 补充“`SubmitRequest` 没有幂等键”（`:159-165`），而 Bridge submit 要求 `idempotencyKey`（`bridge/src/index.ts:62-63`），因此当前端口合同无法做到“失联不重复提交”。
   - 修正阻塞描述：B19a 的 cursor/recovery 合同已交付（`reports/acp2_implementation/bb45a46/B/B19/summary.json`、`runtime/B19a-to-C.json`），B19b 仅 C DSH 行为 `NOT_VERIFIED`；C 侧无恢复入口的主因是 T-32 未实现 + T-30a 无 live 轮次，而不是“B19 完全没有交付”。
6. **G10**
   - 行号修正：`roundDigest` `:278-280`，调用 `:485`。
   - 补充“PlannerInput 没有 charge/multiplicity/constraints 字段”（`planner.ts:26-45`），interpreter 把 constraints 放到 assumptions（`interpreter.ts:114`），但 planner 不读 assumptions；因此这是合同缺口，不只是默认值。
   - 补充“默认 skills 静默落到 fixture planner”（`supervisor.ts:113-118,369`），生产 adapter 缺失时不会拒绝启动。
7. **G17**
   - 把“RPH CLI 无 bin、版本 0.0.0、不能启动”改为“CLI 可执行但只打印 help/version/profile，不能启动研究；`@rph/cli` 及另外三个包均 `private:true`、`0.0.0`、无 `bin`”。
   - 补一句 RPH 不在 Python 发行 BOM：`reports/acp2_final_acceptance_20261024/release-r3/release-manifest.json` 的 `roles` 只有 `cccp/acp-web/acp-worker`，`experience/release-manifest.json`/`static-assets.manifest.json` 均不存在。
8. **通用**
   - 本报告多处 RPH 行号相对 HEAD 有 3–10 行偏移（`supervisor.ts` 704 行、`models.ts` 272 行），建议统一按接口/函数符号引用（如 `supervisor.ts::runSupervisor`）并附行为描述，避免版本漂移后失效。
   - 建议在 G07/G08 的关闭条件中明确要求“同一 fixture 必须由权威生产者生成并携带 producer revision/digest”，否则仅靠单元测试无法防止再次出现“结构合法但身份错误”的接受。
