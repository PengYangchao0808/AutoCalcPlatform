# acp-legacy-bug-remediation - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**Who this is for and what changes for them:** 平台研究员与操作员（命令行 + Web 界面）、读取计算结果的下游流程、以及依赖测试套件的开发者。修复后：重跑或编辑重算的任务把结果放回原位、任务之间绝不误杀彼此的进程、扫描命令开箱即用、测试如实反映软件配置、重跑后界面显示真实进度、成功计算显示真实能量、公开接口只列真实工作流、连续重置请求在预期时间内响应。

**What you'll get:** 九个历史遗留缺陷的根治（每个都带回归测试与可自动执行的验收证据），外加配套文档同步与一套防复发守护测试。

**Why this approach:** 按危害分波推进——先堵执行完整性漏洞（产物失踪、进程误杀），再解功能阻断（扫描不收敛、测试谎言），然后修观测与对外暴露面，最后补测试基建并调查唯一未复现的网络问题（复现优先，不盲改并发）。两个承重决策：产物目录判定从"白名单枚举"改为"白名单 + 调度器身份正向判定"双保险；演示用合成工作流仅从公开列表移除、内部受理保留，避免测试基座大面积坍塌。

**What it will NOT do:** 不迁移历史已错位的数据目录；不动远程计算主链路、不整体重构调度锁模型（仅在问题被复现证实后做最小修正）；不删除内部演示工作流本体。

**Effort:** Large
<!-- Effort is exactly ONE band, never hours/days. Quick = single edit, minutes of agent work; Short = one focused change, a few files; Medium = multi-file feature in one session; Large = several waves, one long session; XL = multi-session or architectural work. A written duration is rewritten to a band. -->
**Risk:** Medium - 触及调度状态语义与进程终止安全面；以双向回归矩阵、免二进制的端到端测试和逐波全量回归门缓解
**Decisions to sanity-check:** ① 合成工作流"fake"仅从公开列表/错误提示移除，内部提交仍受理；② 扫描逐点迭代上限默认提到 200（可配置，单点/优化默认不变）；③ "有界响应"取 60 秒/5 秒边界（工程默认，可调）；④ 历史已错位目录保持原样（只修前向）。

Your next move: 批准后由 worker 会话执行（或先跑高精度评审）。Full execution detail follows below.

---

> TL;DR (machine): Large | Medium risk | 9 legacy bugs 根治（4+1 实现波 13 todos + F1-F4 终验）：产物重定向、进程误杀、扫描迭代、门控同源、state 回放、能量键、公开契约、浏览器 fixture、HTTP 双发复现

## Scope
### Affected user and ideal state
<!-- From the draft ledger: who this touches and how they use the result, then one IS row per property with its reason, then one GAP row per difference with its reason. Every todo below closes a GAP row; ## Success criteria proves every IS row. -->
**Affected user:** ① 平台研究员/操作员（CLI `acp run ...` + Web Workbench + API：提交、重跑、编辑重算、查看结果与进度）；② 下游工作流/消费者（PESsearch/BatchOptimize/结果查看器/结构来源，直接读 `RESULT/`、`result_manifest.json`）；③ 开发者/CI（pytest 全量 + slow 套件、前端浏览器测试）。修复后：①重跑/编辑/暂停操作可信，②产物永远出现在承诺位置，③测试门控如实反映生产配置。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | （前瞻语义）simple 工作流（singlepoint/optimize/frequency/xtb_optimize/casscf/scan/tsmode）rerun 或 edit 后，产物写回**同一任务目录**的 `RESULT/`，下游立即可读；调度器任务目录不再新生 `<work_dir>_1/` 兄弟目录（本地与远端节点子进程走同一 `_resolve_output_dir` 代码路径） | 下游直接读 `RESULT/`；COMPLETED 必须意味着产物在承诺位置 |
| GAP-1 | `.structure_history` 被 rerun 保留但不在 `_SCHEDULER_MARKERS`，`_resolve_output_dir` 判定失败 → `ensure_unique_dir` 重定向 `<work_dir>_1/<name>`，任务仍标 COMPLETED | 产物"消失"但状态撒谎，下游读不到 |
| IS-2 | kill/rerun/暂停任务 X 绝不 signal 任何属于其他任务的进程；同名/前缀任务（`X` vs `X__02`）互不干扰；假阳性守卫不阻塞新任务启动 | 多任务并行时进程隔离是调度安全底线；误杀=数据丢失+算力浪费 |
| GAP-2 | `processctl.py:122` 裸子串 `needle in cmdline`：`/…/X` 匹配 `/…/X__02` 的 argv → rerun X 会 SIGTERM 运行中的 X__02（实测 -15）；`_guard_single_execution` 同源假阳性 | 实测已发生的跨任务误杀 |
| IS-3 | `acp run scan --input "CCO" --coordinate 3,4,1.0,3.0` 全点位收敛并产出轨迹；逐点几何迭代预算可调（默认 200）；失败时给出可行动错误 | 手册命令开箱通过是 README 承诺 |
| GAP-3 | `relaxed_scan` 不写 `%geom MaxIter`（ORCA 默认 ~50），无逐点迭代开关；首点 50 循环不收敛 → SHARK 级联失败 | 手册命令默认失败，用户无从下手 |
| IS-4 | 真实 QC 门控与测试体使用同一生产解析器（`cccp.config.load_config` + `cccp.software.resolve_executable`）：`~/.cccp.yaml` 配置即算可用；slow 套件全绿 | 测试必须如实反映生产可用性 |
| GAP-4 | 4 处测试体仍 `shutil.which`/`is_available` 裸名探测，keyword-smoke 自建硬编码门控 → 门控放开、body FAIL（实测 3 failed：crest/xtb/backends） | 配置了软件仍跑不过测试=测试谎言 |
| IS-5 | 事件/详情/stepper 只读**当前 attempt** 的 state；attempts 归档永不被发现/回放；重跑后不再出现陈旧 `stage.completed` | 重跑后 UI 必须显示真实进度 |
| GAP-5 | `find_workflow_state` 的 `rglob("state.json")` 无 attempts 过滤 → 归档 `WORK/00_RUNTIME/attempts/<N>/state.json` 被重放并污染 `seen` 集合 | 重跑后 UI 显示旧阶段事件，误导操作员 |
| IS-6 | 浏览器套件即使首个用例超时/失败，fixture 也必定清理 Playwright；独立复跑与整套复跑结果一致 | 前端测试基础设施可信 |
| GAP-6 | 至少 3 个套件裸 `sync_playwright().start()` 无 `finally: stop()`；首超时后泄漏 → 后续用例 "Sync API inside asyncio loop" 级联 | 前端测试不可信，阻塞前端工作 |
| IS-7 | `acp run singlepoint` 成功后摘要打印真实能量，四项 handler 共用统一 `energy` 键约定（`sp_energy` 兼容保留） | CLI 输出可信；成功计算不得显示 N/A |
| GAP-7 | SINGLEPOINT 分支只写 `sp_energy`，CLI 读 `metadata["energy"]` → `Energy: N/A` | 输出谎言 |
| IS-8 | 公开 API/错误串只暴露 14 个 active workflow；合成 `fake` 仅内部/测试受理 | 公开契约诚实 |
| GAP-8 | `jobs.py:159 return active + ("fake",)` → `GET /api/workflows` 与 6 处 `Supported:` 错误串泄露 fake | 合成测试工作流污染公开契约 |
| IS-9 | 连续两次 in-place-reset POST（rerun / edit-recalculate in_place）都在有界时间内拿到 HTTP 响应（A7：每 POST ≤60s；reset 中并发健康检查 ≤5s），无长连接/锁挂起；有网络级回归测试 | Web 操作可预期 |
| GAP-9 | urllib 客户端"无响应"（curl 正常）未复现；嫌疑：全局 `manager._lock` 跨慢操作持有、SSE 阻塞事件循环；无网络级测试 | 无法排除的真实用户报告 |

### Must have
- 9 条 bug 的根因修复、回归测试、守护测试与 QA 证据（含真实 ORCA scan 验收、slow 门控套件、浏览器套件、真实 HTTP 验收）。
- ANTI #11 审计收口：调度器根写文件全集与 `_SCHEDULER_MARKERS` 一致性守护（行为式 + 集合快照）。
- 相关文档同步：AGENTS.md（ANTI #11）、Job File Layout Spec、scan 文档/README、API 契约 compat 提示——仅限与修复直接冲突的句子。
- 每波收尾 wave gate：`pytest -m "not slow"` 全绿 + `ruff check src tests && ruff format --check src tests`。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不迁移/不修复历史已重定向的 `<work_dir>_1` 数据（A1；IS-1 为前瞻语义，历史目录不算 F4 失败）。
- 不改 remote LSF 的 bsub/bjobs 主链路；T3 **禁改 `monitor.py` 代码**（`_pkill_acp` 子串风险仅在 T13 文档化）。
- 不重构 scheduler 锁模型 —— **唯一例外**：BUG-9 复现证实的最小阻塞 seam（Q3A 例外条款：把该慢操作移出 `_lock` 全持锁段，或 SSE 阻塞读入线程池，取证据指向的最小者）。
- 不重命名/不删除 `fake` 工作流本体与 legacy demo 按钮路径（A2：scheduler/API 内部受理保留 fake）。
- 不新增 workflow/不扩展 CLI 功能面（`--geom-maxiter` 为既有选项在 scan 分支的复用，非新面）；不做与本 9 bug 无关的重构、格式化、依赖变更。
- runner 根写审计发现的 UNKNOWN 首跑触发行为 → 记为新发现行，不在 T1 内展开修复。
- 不修改 `.omo/` 之外的计划产物；不提交 `grad.engrad`、不提交 `tests/baseline/...e2e-task-root.txt` 的脏改动。
- 不跳过任何既有测试/门控（禁 xfail/skip 掩盖）。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: 实现+回归测试同 commit（tests-with-implementation）。可先行复现的 bug（BUG-1/2/5/7）允许 TDD 节奏（先红后绿）；BUG-3/4/6/9 测试与实现同体交付。
- Framework: pytest（slow 门控 `--run-slow`、`requires_orca` 门控走 conftest 生产解析器）+ Playwright headless（浏览器套件）+ 真实 ORCA 6.1.1（`~/.cccp.yaml` `executables.orca.path=/home/<user>/orca611/orca`；PATH 上 `/usr/bin/orca` 是 Python 脚本诱饵，禁用）。
- Wave gate（每波收尾）: `pytest -m "not slow"` 全绿 + `ruff check src tests && ruff format --check src tests`。
- F 波全量: `pytest -m "not slow"` + 目标性 `--run-slow`（scan 集成 / 门控套件 / 浏览器套件 / HTTP harness）+ 全量 slow 一次（IS-4 终证）。
- 免 QC 二进制策略: manager 层调度侧断言与 HTTP harness 可用 `fake`（A2 内部受理；注意边界——fake 为 runner 内进程工作流，**不经 `_resolve_output_dir`、不产 `result_manifest.json`**，禁止用 fake 断言产物/清单/输出目录行为）；IS-1 的下游可读证明（result_manifest 解析）与 IS-3 的收敛证明用真二进制 E2E（slow + requires_orca 门控，生产 ORCA）。
- Evidence: <attemptDir>/task-<N>-acp-legacy-bug-remediation.<ext> (attemptDir = currentAttemptDir from the ulw-loop status operation - on Senpi the eval SDK import, const { agentToolkit } = await import(\`\${env("OMO_AGENT_TOOLKIT_SDK_ROOT")}/sdk.js\`) then agentToolkit.status(), on Codex the omo-agent-toolkit CLI, .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave A（P1 执行完整性）**: T1 → T2（轻串行：T2 的 E2E 断言双保险合力，且同文件相邻区域）；T3 与之并行。
- **Wave B（P2 功能阻断）**: T4 → T5（真实验收依赖管线）；T6 与 T4 并行。
- **Wave C（观测与暴露面）**: T7、T8、T9 并行。
- **Wave D（测试基建与调查）**: T10、T11 并行；T12 在 T11 后。
- **Wave E（收尾）**: T13（依赖 T1/T9 的最终形态）。
- **Final verification wave**: F1-F4（全部 todo 完成后并行）。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | — | T2, T13 | T3 |
| T2 | T1 | F1-F4 | T3 |
| T3 | — | — | T1, T2 |
| T4 | — | T5 | T6 |
| T5 | T4 | F1-F4 | — |
| T6 | — | F1-F4 | T4 |
| T7 | — | F1-F4 | T8, T9 |
| T8 | — | F1-F4 | T7, T9 |
| T9 | — | T13 | T7, T8 |
| T10 | — | F1-F4 | T11 |
| T11 | — | T12 | T10 |
| T12 | T11 | F1-F4 | — |
| T13 | 1, 3, 4, 9 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. BUG-1(a) `.structure_history` 入册 `_SCHEDULER_MARKERS` + 调度器根写全集审计（ANTI #11 收口）
  What to do / Must NOT do: ① `src/acp/workflows/simple.py:248-266` `_SCHEDULER_MARKERS` 增加 `.structure_history`；② 审计 `src/acp/scheduler/runner.py` 在任务目录根写的文件，逐一"登记进集合或注明豁免理由"：`electronic_state.json`（runner.py:74-79）、`input.com`/`input.inp`（:211-216）——仅登记/文档化，**禁止**在审计中发现 UNKNOWN 首跑触发行为时展开修复（记为新发现行即可）；③ 新增守护测试 `tests/test_scheduler_markers_audit.py`（行为式，非氛围检查）：fixture 目录含全部 markers（含 `.structure_history`）→ `_resolve_output_dir` 复用 base；+ `_SCHEDULER_MARKERS` 集合字面快照断言（pin 全集，任何变更须显式更新快照并注明理由）。
  Closes: GAP-1（与 todo 2 双保险合力）
  Parallelization: Wave A | Blocked by: 无 | Blocks: 2, 13
  References (executor has NO interview context - be exhaustive): src/acp/workflows/simple.py:248-276；src/acp/results/structure_snapshots.py:14-17（无条件 mkdir `.structure_history`，:17）；src/acp/scheduler/manager.py:1019（preserve_outputs）/1028/1413（_reset_work_dir_in_place 显式保留）；src/acp/scheduler/runner.py:74-79/211-216（根写候选）；AGENTS.md ANTI #11（`_SCHEDULER_MARKERS` 必须列出调度器预建文件全集，漏列致产物重定向 `<work_dir>_1/`）；docs/ACP_Job_File_Layout_Spec.md:160（契约警告）；.omo/notepads/acp-execution-integrity-remediation/learnings.md:32（resume_source.json 同病先例，55262ddf 修复）
  Acceptance criteria (agent-executable): `pytest tests/test_scheduler_markers_audit.py tests/test_acp_workflows_simple.py tests/test_acp_job_edit.py -v` 全绿；grep 断言 `.structure_history` 出现在 `_SCHEDULER_MARKERS` 定义处
  QA scenarios (name the exact tool + invocation): happy — pytest 构造调度器目录（job.json+task.json+全部 markers+.structure_history）断言 `_resolve_output_dir` 返回 base；failure — 含未知额外文件且无调度器身份仍重定向（既有意图，tests/test_acp_workflows_simple.py:134-140 保持绿）。Evidence <attemptDir>/task-1-acp-legacy-bug-remediation.log
  Commit: Y | fix(scheduler): register .structure_history in _SCHEDULER_MARKERS and close ANTI-11 root-write audit
  Recommended task executor category: deep-low — 根写全集审计需跨 scheduler/workflow 模块枚举与分类，worker 从已列清单出发可自洽完成

- [x] 2. BUG-1(b) `_resolve_output_dir` 正向调度器身份复用 + 三层回归（workflow 层 / manager 层 / 真二进制 E2E）
  What to do / Must NOT do: ① `src/acp/workflows/simple.py:269-276` `_resolve_output_dir` 在 marker 子集判定失败前增加正向身份判定：base 含 `job.json`+`task.json` → 直接复用 base（先 grep `is_scheduler_task_dir` 定位既有谓词并复用——已知驻点 `src/acp/workflows/_helpers.py:22`——勿重造）；保留既有 CLI 语义（空目录/纯 markers 复用；未知文件且无身份 → `<safe_name>/` 子目录 + `ensure_unique_dir` `_1`）。② **workflow 层回归（免 QC 二进制，直测 `_resolve_output_dir`）**：三分支——(a) 调度器目录（job.json+task.json+全部 markers+`.structure_history`）→ 返回 base；(b) **身份分支正向用例**（job.json+task.json+未知额外文件、无完整 markers）→ 仍复用 base（此分支与 T1 的 markers 白名单互为双保险，必须有独立正向断言）；(c) 负向（无身份+未知文件 → 重定向，既有 :134-140 意图保持）。③ **manager 层（fake 可用，仅断言调度侧）**：fake 工作流走 manager rerun 与 edit 链路中**不经 EDIT_ACTIVE_WORKFLOWS 门的部分**（fake 不在该门内），断言：rerun 后无 `<work_dir>_1` 兄弟目录、`.structure_history` 保留、attempt 回执归档 `WORK/00_RUNTIME/attempts/<N>/`、fake 照常完成且 state.json 在任务根。**禁止**对 fake 断言 `result_manifest.json` 或 `_resolve_output_dir` 行为——fake 是 runner 内进程工作流（`_run_fake`），既不经 `_resolve_output_dir` 也不产 result_manifest。④ **真二进制 E2E（slow + requires_orca，生产 ORCA `~/.cccp.yaml` 解析）**：singlepoint（active，在 EDIT_ACTIVE_WORKFLOWS 内）经 manager 提交并完成后：原地 rerun 一轮 + edit-recalculate in_place 一轮，断言每轮后 `RESULT/result_manifest.json` 在**原任务目录**且可经 `acp.results.manifest` 解析（下游可读证明）、无 `<work_dir>_1` 兄弟、`.structure_history` 保留。⑤ 远端注记：远端节点子进程走同一 `_resolve_output_dir` 代码路径，验证仅本地执行。禁止改 preserve/reset 本体逻辑；禁止动 irc/BatchOptimize/PESsearch/Confsearch 等不受影响工作流；禁止迁移历史 `_1` 数据；禁止为凑 ③ 的 edit 腿把 fake 塞进 EDIT_ACTIVE_WORKFLOWS。
  Closes: GAP-1
  Parallelization: Wave A | Blocked by: 1 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): src/acp/workflows/simple.py:269-276；src/acp/core/utils.py:8-28（ensure_unique_dir）；src/acp/workflows/_helpers.py:22（is_scheduler_task_dir 谓词）/29（无 job.json/task.json 时追加 `/<safe_name>`）；src/acp/calculations/executor.py:784（RESULT 写入点）；src/acp/scheduler/manager.py:1019-1120/1400-1413（preserve/reset/RESULT 重建）；src/acp/scheduler/runner.py:559；7 个调用点：singlepoint:553 / casscf:578 / optimize:602 / xtb_optimize:626 / frequency:650 / cli.py:3273（scan）/ tsmode.py:158；fake 边界证据：src/acp/scheduler/runner.py:547-549/1818-1943（_run_fake 内进程、无 result_manifest）；edit 门：src/acp/scheduler/job_edit.py:67-84/131（EDIT_ACTIVE_WORKFLOWS=active 集，不含 fake）+ src/acp/api/v1_routes.py:5504-5513；仿照范例 tests/test_acp_job_edit.py:1314-1363
  Acceptance criteria (agent-executable): ② 三分支单测通过；③ manager 层断言通过（fake rerun 调度侧）；④ slow+requires_orca E2E 通过（singlepoint rerun+edit 双轮 manifest 可解析、无 `_1` 兄弟）；`pytest tests/test_acp_workflows_simple.py -v` 全绿
  QA scenarios (name the exact tool + invocation): happy — ②(a/b) 复用 + ④ singlepoint 双轮后 RESULT manifest 经 acp.results.manifest 解析成功；failure — ②(c) 非调度 CLI 目录未知文件仍走子目录（:134-140）；③ fake rerun 后无 _1 兄弟、.structure_history 保留。Evidence <attemptDir>/task-2-acp-legacy-bug-remediation.log
  Commit: Y | fix(workflows): positive scheduler-identity reuse in _resolve_output_dir + three-layer rerun/edit regression
  Recommended task executor category: unspecified-high — 跨 simple.py/manager/谓词复用 + 三层 E2E 组装，标准多文件特性

- [x] 3. BUG-2 processctl cmdline 边界安全匹配 + 守卫不阻塞
  What to do / Must NOT do: ① `src/acp/scheduler/processctl.py:122` 裸 `needle in cmdline` 改为词元/路径边界判定：needle 后必须紧跟 `/`、空白、引号、`)`、`,` 或行尾；needle 前不得为路径字符（`[A-Za-z0-9._-/]`）；cwd 探针（:119）保持不变。② 回归矩阵（单测）：`X` vs `X__02` **双向**、`--output=/p` 与 `--output /p`、`/p/sub`、`/p2`、`/p_2`、`/p,`、多参数含路径尾部的真实 argv 形态。③ runner 级场景（IS-2 第三子句）：同前缀兄弟进程存活 → `_guard_single_execution`（runner.py:840-847，经 `_live_task_pids`:815 / `_foreign_task_alive`:838）不阻塞新任务启动。④ 远端 `_pkill_acp`（monitor.py:314-349）子串风险仅记录转 todo 13 文档化。禁止改 monitor.py 代码；禁止改 pause/unpause/cancel 的 tracked-PID 路径；禁止重构 processctl API。
  Closes: GAP-2
  Parallelization: Wave A | Blocked by: 无 | Blocks: 无
  References (executor has NO interview context - be exhaustive): src/acp/scheduler/processctl.py:103-122（cwd 探针 :119 边界安全、cmdline 探针 :122 裸子串）/218-248（extract + extra_pids :238-241 同 matcher）；kill 调用点 manager.py:988 → :2655-2662、重启清扫 :5241；假阳性只读路径 runner.py:815/838/840-847；远端对照 monitor.py:295/362/387（数字 lsf_job_id 无此问题）；既有测试 tests/test_acp_scheduler_processctl.py:75-88（仅无关父目录用例）
  Acceptance criteria (agent-executable): `pytest tests/test_acp_scheduler_processctl.py -v` 全绿含新矩阵；双向负向断言（X 不匹配 X__02 的 cmdline、X__02 不匹配 X）；runner 级 guard 场景通过
  QA scenarios (name the exact tool + invocation): happy — X 匹配自身各 argv 形态；failure — X 不误匹配 X__02/X2/X_2（负向）；guard — 同前缀兄弟存活时新任务照常启动。Evidence <attemptDir>/task-3-acp-legacy-bug-remediation.log
  Commit: Y | fix(scheduler): boundary-safe process cmdline matching; guard no longer blocks same-prefix siblings
  Recommended task executor category: deep-low — 边界语义细微、双向矩阵与 runner 级验证需细致推理

- [x] 4. BUG-3(a) scan 逐点几何迭代预算管线贯通（默认 200）
  What to do / Must NOT do: ① `src/cccp/calculation/requests.py:604-643` `ScanOptions` 增 `geom_maxiter: int | None = None`（PEP 604、frozen dataclass 契约）。② `_scan_options_from_resources`（src/acp/calculations/legacy_adapters.py:620-677）从 resources 透传 `geom_maxiter`。③ `src/acp/cli.py` scan 分支：**复用**既有 `--geom-maxiter` 选项（cli.py:391-396 optimize 语义；不新增第二旋钮）注册到 scan 子命令（:421 附近），`_handle_scan` resources（:3282-3292）默认写入 `geom_maxiter=200`。④ 失败诊断仅限既有失败消息路径（orca.py:2288 → tasks/scan.py:213-226）：错误串附可行动提示（当前 MaxIter 值 + 建议 `--geom-maxiter` 调大）。⑤ 单测：`ScanOptions(geom_maxiter=200)` 经 orca.py:2207→2258 → translation.py:176-187 渲染出 `%geom` 块内 `MaxIter 200`；缺省 resources → 默认 200 进输入。⑥ 序列化/身份面核对：`geom_maxiter=None`（PES 等不传该键的路径）时 ScanOptions 序列化与 plan/step 身份摘要输出与现状一致——不破坏既有 checkpoint resume/恢复 fixtures。禁止改 `Calc_Hess`/`Trust`/`Recalc_Hess`（默认 0 保持）/`NoTRAH` 语义；禁止动 PES 传值路径（acp/calculations/pes/scan.py:743）；禁止改单点/优化默认迭代；禁止动 ErrorModel/errors.py。
  Closes: GAP-3（管线部分；真实收敛证明在 todo 5）
  Parallelization: Wave B | Blocked by: 无 | Blocks: 5
  References (executor has NO interview context - be exhaustive): src/cccp/calculation/requests.py:604-643（ScanOptions 无迭代字段）；src/acp/calculations/legacy_adapters.py:620-677（_scan_options_from_resources）；cli.py:391-396（--geom-maxiter 仅 optimize 注册）/421（scan 分支）/3282-3292（_handle_scan resources）/3335（CLI exit）；src/cccp/calculation/tasks/scan.py:213-226（失败链）/497-512（_scan_capability_kwargs 已透传 extras）；src/cccp/qc/interfaces/orca.py:1614-1631（_build_input_blocks）/1794-1797（失败判定）/2126（relaxed_scan）/2206-2258（geom_maxiter 已接受、渲染链）/2288（失败消息）；src/cccp/qc/translation.py:176-187（render_opt_geom_lines，MaxIter 门槛 `max_cycles is not None and max_cycles > 0`）；既有断言缺口：tests/test_cccp_task_scan.py:478、tests/test_cccp_orca_ts_extensions.py:220（仅断言 ScanTS/Scan 行）
  Acceptance criteria (agent-executable): 新单测通过（`MaxIter 200` 渲染断言 + 缺省默认 200 断言）；`acp run scan --help` 显示 `--geom-maxiter`；`pytest tests/test_cccp_task_scan.py -v` 全绿
  QA scenarios (name the exact tool + invocation): happy — CLI `--geom-maxiter 250` → ScanOptions 250 → 输入含 `MaxIter 250`；failure — 模拟 ORCA returncode≠0 时错误串含行动建议（当前值+调大建议）。Evidence <attemptDir>/task-4-acp-legacy-bug-remediation.log
  Commit: Y | feat(scan): per-point geometry iteration budget piped to ORCA %geom MaxIter (default 200)
  Recommended task executor category: unspecified-high — 三层管线（契约/适配/CLI）+ 渲染断言，标准多文件特性

- [x] 5. BUG-3(b) 真实 ORCA 手册命令验收（CCO）+ water 回归
  What to do / Must NOT do: 新增 slow + requires_orca 集成测试 `tests/test_scan_orca_manual_acceptance.py`（门控走 conftest 生产解析器 RealQCSnapshot，仿 tests/test_pes_orca_simulscan_integration.py:48-49）：`acp run scan --input "CCO" --coordinate 3,4,1.0,3.0`（默认 200）全点位收敛、`RESULT/trajectories/scan_trajectory.json` 帧数>0；water 对照回归不降级。运行预算自洽校准：CLI 默认 `--scan-points 21`（cli.py:439-443）+ `--method r2SCAN-3c`（cli.py:323/165），按 ~10min/点上界预估全程至多 ~3.5h——测试超时上限显式设 **240min**，实际逐点耗时记入证据 JSON（供后续回归预算校准）。验收证据（命令、轨迹摘要、逐点收敛表）写 evidence。环境注意：生产 ORCA 经 `~/.cccp.yaml` `executables.orca.path=/home/<user>/orca611/orca`（6.1.1）；PATH 上 `/usr/bin/orca` 为 40.3 Python 脚本诱饵，禁用。禁止 skip/xfail 掩盖；禁止为凑绿降断言；禁止动 requirements/pyproject。
  Closes: GAP-3（收敛证明）
  Parallelization: Wave B | Blocked by: 4 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): tests/test_pes_orca_simulscan_integration.py:48-49（唯一真实 ORCA scan 先例与门控范例）；tests/conftest.py:84-100（RealQCSnapshot：load_config + resolve_executable 一次构建）/403-414（requires_orca）；审计 JSON 记录 `scan_control.scan_default: failed / "ORCA relaxed scan failed"`（draft findings BUG-3 环境）；water 对照 PASS 记录
  Acceptance criteria (agent-executable): `pytest tests/test_scan_orca_manual_acceptance.py --run-slow -v` 通过（requires_orca 门控）；CCO 轨迹帧>0
  QA scenarios (name the exact tool + invocation): happy — CCO 手册命令全点收敛；failure/回归 — water 案例仍 PASS。Evidence <attemptDir>/task-5-acp-legacy-bug-remediation.json（轨迹摘要+收敛表）
  Commit: Y | test(scan): real-ORCA manual-command acceptance (CCO) + water regression
  Recommended task executor category: unspecified-low — 管线就位后的机械验收组装（运行真实命令+断言+证据落盘）

- [x] 6. BUG-4 QC 门控与测试体同源（四处偏差面 + smoke 门控 + 守护测试）
  What to do / Must NOT do: **四处**偏差测试体全部切 conftest 生产解析路径（real_qc_binary_path fixture / RealQCSnapshot，禁裸名 which）：① tests/test_qc_interfaces_crest.py:64；② tests/test_qc_interfaces_xtb.py:106；③ tests/test_qc_interfaces_orca.py:137（`shutil.which(str(interface.exe_path))` → 生产路径断言；exe_path 来自 sample_config 裸名是根因）；④ tests/test_acp_backends.py:570/573（`is_available()` + `_make_config` 裸名 → 注入解析结果或加载生产配置）。⑤ tests/test_orca_keyword_smoke.py:52/67-69 退役硬编码 `ACP_ORCA_SMOKE_BIN` 默认路径（改 conftest 生产 ORCA 路径）。⑥ docstring 同步：tests/test_cccp_real_qc_smoke.py:16-17、tests/test_acp_nmr_qc_smoke.py:10-11（仍写 shutil.which 的过期描述）。⑦ 守护测试：QC 二进制门控禁 `shutil.which` —— 范围限定 QC 接口/后端门控测试文件集（限定文件集 grep 或 AST 匹配，防误报无关用途）。禁止改 conftest 门控本体（已正确）；禁止删测试；本 todo 验收不含全量 slow 套件（F 波负责）。
  Closes: GAP-4
  Parallelization: Wave B | Blocked by: 无 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): tests/conftest.py:84-100（RealQCSnapshot 门控，a21beca 已切生产解析器）/403-414（HAS_*/requires_*）；四处偏差行号如上；正确范例 tests/test_conftest_real_qc_gating.py:142-150（real_qc_binary_path fixture）、tests/test_cccp_real_qc_smoke.py:81-92（load_config）；本机实测基线：门控全开时 crest/xtb/backends 3 failed、orca 因 `/usr/bin/orca` 诱饵侥幸通过
  Acceptance criteria (agent-executable): 配置就绪环境（~/.cccp.yaml）跑 `pytest tests/test_qc_interfaces_crest.py tests/test_qc_interfaces_xtb.py tests/test_qc_interfaces_orca.py tests/test_acp_backends.py tests/test_orca_keyword_smoke.py --run-slow -v` 从 3 failed → 全绿；守护测试通过
  QA scenarios (name the exact tool + invocation): happy — 门控与 body 同源全绿；failure — 无配置环境（隔离 HOME 模拟）上述文件全部 skip 而非 fail。Evidence <attemptDir>/task-6-acp-legacy-bug-remediation.log
  Commit: Y | fix(tests): unify QC gating with conftest production resolver (crest/xtb/orca/backends/keyword-smoke)
  Recommended task executor category: unspecified-low — 逐行号机械替换 + 守护测试

- [x] 7. BUG-5 state 发现隔离 attempts 归档
  What to do / Must NOT do: ① `src/acp/scheduler/runner.py:164-188` `find_workflow_state` 候选排除 `WORK/00_RUNTIME/attempts/**`（root 命中优先与 legacy `<mol_id>/state.json` 深度规则保持）。② 回归测试：仅播种归档 state（`WORK/00_RUNTIME/attempts/<N>/state.json`）→ poll 一轮 → 新 `events.jsonl` 无 `stage.completed`、`seen` 集合不含旧 stage。③ 同一排除谓词应用到**有界清单（8 个 file:line）**同类 rglob（逐处判定消费对象：消费 state.json/任务产物 → 复用谓词；否则记录豁免）：src/acp/scheduler/runner.py:2009/2013、src/acp/scheduler/structure_sources.py:732/787、src/acp/scheduler/files.py:108、src/acp/api/v1_routes.py:3484/4759、src/acp/results/structure_migration.py:34。禁止改 `_observe_state` 事件语义；禁止动 attempts 归档写入（manager.py:1400-1409 保留）。
  Closes: GAP-5
  Parallelization: Wave C | Blocked by: 无 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): src/acp/scheduler/runner.py:164-188（find_workflow_state，:176 rglob min((depth,-mtime)) 无 attempts 排除）/639（seen 每 attempt 新 seed）/1085-1141（_observe_state → :1132 stage.completed → :1141 写 events.jsonl）；归档写入 manager.py:1400-1409（_reset_work_dir_in_place receipts）/1468-1469（_archive_attempt_receipts）；部分守卫 manager.py:5345（_disk_shows_completed）/4995（_collect_result）；消费者 routes.py:375-454+522+552（SSE）、v1_routes.py:444/4584（detail）/5634、stage_tasks.py:525-560（stage 镜像）；8 个 file:line 同类 rglob 清单如上（含目录）
  Acceptance criteria (agent-executable): 回归测试通过；`pytest tests/ -k "workflow_state or observe" -v` 全绿；8 项清单处置记录在案（复用/豁免逐处标注于 evidence）
  QA scenarios (name the exact tool + invocation): happy — 归档 state 存在时新 attempt 无陈旧事件；failure — root state.json 仍被优先发现（正向保持断言）。Evidence <attemptDir>/task-7-acp-legacy-bug-remediation.log
  Commit: Y | fix(scheduler): exclude archived attempts from workflow state discovery
  Recommended task executor category: deep-low — rglob 语义与 7 处消费者判定需细致推理

- [x] 8. BUG-7 singlepoint 能量键统一
  What to do / Must NOT do: ① `src/acp/workflows/simple.py:491-492` SINGLEPOINT 分支补 `metadata["energy"] = result.energy`（保留 `sp_energy` 兼容并注释标注规范键为 energy）。② `src/acp/cli.py:3166` 读取改 `metadata.get("energy") or metadata.get("sp_energy")`（旧结果兼容）。③ 回归：扩展 `tests/test_acp_workflows_simple.py:659-706` 内的测试函数 `test_simple_cli_handlers_construct_reporter`（现传空 metadata 不断言 Energy 行）与同文件 :438/:596 断言，断言四 handler（singlepoint/optimize/xtb_optimize/casscf）摘要 Energy 行非 N/A（有值路径）。禁止删 `sp_energy`；禁止动 FREQUENCY（无能量键语义）；禁止改 casscf/optimize/xtb_optimize 写入侧（已一致）。
  Closes: GAP-7
  Parallelization: Wave C | Blocked by: 无 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): src/acp/workflows/simple.py:485-500（OPTIMIZE :486 / SINGLEPOINT :492 / CASSCF :500 写键现状）；src/acp/cli.py:2048（正确读 energy_hartree 先例）/3128（casscf 一致）/3166（singlepoint N/A 根因）/3204（optimize 一致）/3519（xtb_optimize 一致）；tests/test_acp_workflows_simple.py:438（断言 sp_energy）/596（断言 energy）/659-706（test_simple_cli_handlers_construct_reporter 测试函数，fake_run 传空 metadata）；历史定性 .omo/ulw-execute/ledger.jsonl:20（曾判 P3 cosmetic out-of-scope，本次按根治纳入）
  Acceptance criteria (agent-executable): 新断言通过（四 handler Energy 行含数值非 N/A）；`pytest tests/test_acp_workflows_simple.py -v` 全绿（含 :659-706 扩展）
  QA scenarios (name the exact tool + invocation): happy — singlepoint 成功后摘要打印真实能量；failure — 仅含 sp_energy 的旧 metadata 也能显示（兼容路径断言）。Evidence <attemptDir>/task-8-acp-legacy-bug-remediation.log
  Commit: Y | fix(cli): canonical energy key for singlepoint summary (sp_energy kept as compat)
  Recommended task executor category: unspecified-low — 小面积多点机械修改

- [x] 9. BUG-8 公开/内部工作流集合拆分（含 ImportError fallback 修正）
  What to do / Must NOT do: ① `src/acp/scheduler/jobs.py:124-162` 拆 `PUBLIC_WORKFLOWS`（= catalog active 14）与 `ALL_WORKFLOWS`（active + `("fake",)`）；**既有符号 `SUPPORTED_WORKFLOWS` 保留为内部受理集合（= ALL_WORKFLOWS 含 fake），15+ 既有消费者语义不变**，公开面一律改用新 `PUBLIC_WORKFLOWS`；**ImportError fallback 静态表同步修正**：现缺 `tsmode` 与 `casscf`（12 real + fake），补齐为与 catalog 一致的 14 active（公开/内部分别构造）。② 6 处暴露面**逐处分类** validation-vs-string-only 后切换：routes.py:258、v1_routes.py:2180/7198、v2_routes.py:602、manager.py:661/1265 —— 错误串一律公开集合；**受理校验保持内部集合**（A2：API/scheduler 提交 fake 仍 200；manager.py 两处若为 validation-then-accept 站点必须留在内部集合，防 20+ 测试基座与 legacy demo 塌方）。③ registry.py:148-158 公开列表过滤剔除 fake（内部注册 :32-37 保留）。④ 同步固定断言（**仅此清单，禁止越界改其它测试**）：tests/test_acp_api.py:212、tests/test_architecture_invariants.py:115、registration 测试 ×2 = tests/test_acp_orca_gradient_registration.py:46 + tests/test_acp_xtb_path_search_registration.py:48、tests/test_acp_catalog.py:705、tests/test_acp_workflow_task_matrix.py:309 —— 改为断言公开面=恰 14 active、内部受理仍含 fake。⑤ fake-still-accepted 回归：scheduler 路径提交 fake 成功 + legacy demo 流（frontend/ACP_Workbench.html:204/521 按钮路径）不被破坏。禁止删 fake 本体/legacy demo 按钮；禁止改前端（v2 已 visible/active 过滤）；禁止改 `/workflow-catalog`。
  Closes: GAP-8
  Parallelization: Wave C | Blocked by: 无 | Blocks: 13
  References (executor has NO interview context - be exhaustive): src/acp/scheduler/jobs.py:124-162（:153 fallback 缺 tsmode/casscf、:159 `active + ("fake",)`、:162 SUPPORTED_WORKFLOWS）；src/acp/workflows/registry.py:32-37（fake 注册）/148-158（过滤）；暴露面 routes.py:258、v1_routes.py:2180/7198、v2_routes.py:602、manager.py:661/1265；固定测试 id 清单如上；前端对照：v2 用 `/workflow-catalog`+visible/active 不受影响、legacy ACP_Workbench.html:204/521 demo 按钮、minimal:135 客户端过滤；docs/ACP_MethodMeta_Frontend_DevDoc.txt:197/2064-2081（fake 由来 R14/D5）
  Acceptance criteria (agent-executable): `pytest tests/test_acp_api.py tests/test_architecture_invariants.py tests/test_acp_catalog.py tests/test_acp_workflow_task_matrix.py -v` 全绿（新断言）；TestClient 断言 `GET /api/workflows` == 恰 14 active 无 fake；fake 提交仍 200
  QA scenarios (name the exact tool + invocation): happy — 公开列表恰 14、fake 内部受理存活（含 legacy demo 流）；failure — 非法 workflow "bogus" 被拒且错误串只列 14 active（无 fake）。Evidence <attemptDir>/task-9-acp-legacy-bug-remediation.log
  Commit: Y | fix(api): public workflow surface = 14 active; fake internal-only; repair stale ImportError fallback
  Recommended task executor category: unspecified-high — 多表面契约拆分 + 6 站点分类 + 测试面同步

- [x] 10. BUG-6 浏览器 fixture 必定清理（Playwright 生命周期）
  What to do / Must NOT do: 三个坏套件 fixture 改 try/finally（或 `with sync_playwright()`，照抄正确范例）：tests/test_frontend_viewer_framing_browser.py L148-222、tests/test_frontend_measure_edit_browser.py L242-303、tests/test_frontend_multiframe_playback_browser.py L177-262 —— teardown 失败也必须执行 `pw_ctx.stop()`（finally 链 close+stop，防 context.close() 抛错跳过 stop）；补外部路由拦截 `context.route("https://3Dmol.org/**", abort)`（仿 interaction.py:223；本地 vendor 已就位但拦截防御 CDN 回归）。**约束性验收 = 整套复跑一致性**（绑定标准，非模式符合性）：3 文件各自单跑 + 与 startup/interaction 合并同进程跑均绿；探针用例验证 setup 超时注入后无级联（临时注入 goto 超时，验后删除）。禁止改被测前端行为；禁止删用例；禁止改 server_url module fixture。
  Closes: GAP-6
  Parallelization: Wave D | Blocked by: 无 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): 三个坏 fixture：viewer_framing L148-222（start L157、未守卫 goto L179/initViewer L202-205、teardown L220-222）、measure_edit L242-303（start L250、goto L269、waits L283-297）、multiframe L177-262（start L184、goto L228、waits L242-256）；正确范例 tests/test_frontend_interaction.py:200-237（L223 路由拦截、L232-237 finally）、tests/test_frontend_startup_browser.py:74-81（with 语句）；泄漏机理（yield 前抛出 → teardown 永不执行；teardown 内 close 抛错 → stop 被跳过）；frontend/ACP_Workbench_v2.html:7（本地 vendor）；.github/workflows/ci.yml:73（同进程多文件）；.omo/notepads/cccp-correctness-hardening/learnings.md:448；全部 slow 标记需 `--run-slow`
  Acceptance criteria (agent-executable): `pytest tests/test_frontend_viewer_framing_browser.py --run-slow -v`（×3 文件逐个）+ `pytest tests/test_frontend_interaction.py tests/test_frontend_startup_browser.py tests/test_frontend_viewer_framing_browser.py tests/test_frontend_measure_edit_browser.py tests/test_frontend_multiframe_playback_browser.py --run-slow -v`（合并同进程）均绿（headless）
  QA scenarios (name the exact tool + invocation): happy — 合并复跑与单跑结果一致；failure — 注入首用例超时（临时 goto timeout 探针）后后续用例仍正常（无 "Sync API inside asyncio loop" 级联；验后删探针）。Evidence <attemptDir>/task-10-acp-legacy-bug-remediation.log
  Commit: Y | fix(tests): guaranteed Playwright teardown + external route interception in browser fixtures
  Recommended task executor category: visual-engineering — 前端浏览器测试基建修复

- [x] 11. BUG-9(a) 真实 HTTP 双发有界延迟 harness
  What to do / Must NOT do: 新增 harness 测试 `tests/test_api_inplace_reset_http.py`（slow 标记）：uvicorn 线程 + 临时 run_root 起真实 HTTP 服务（非 TestClient），urllib 客户端（CPython urllib 强制 Connection: close，对齐原始报告客户端语义）连发两 POST（`POST /api/v1/jobs/{id}/rerun` 与 `POST /api/v1/jobs/{id}/edit-recalculate` mode=in_place），断言具体边界（A7 默认）：每个 POST ≤60s 返回、reset 进行中并发 `GET /api/status` ≤5s 响应。观测点记录：两 POST 时延、并发健康检查时延（JSON 证据）。用 fake 工作流免 QC 二进制。禁止改生产并发/锁语义（todo 12 按证据分支）；禁止引入新依赖；禁止碰 SSE 端点行为。
  Closes: GAP-9（复现部分）
  Parallelization: Wave D | Blocked by: 无 | Blocks: 12
  References (executor has NO interview context - be exhaustive): src/acp/api/v1_routes.py:5048-5063（rerun 同步处理链）/5476（edit-recalculate in_place）；src/acp/scheduler/manager.py:912/1216（_lock RLock park 点）/936（后台执行分流）/1000（锁内远端 fetch_catalog）/1207-1247；SSE routes.py:375-454/522/552（async 生成器内阻塞读）；harness 先例：.omo/evidence/real-qc-gap-remediation/task-14-acceptance/api_acceptance.py（urllib POST /jobs）、tests/test_acp_job_edit.py:1194-1201 与 tests/test_acp_api_v1.py:781-785（TestClient 两连发，仅此先例）；客户端差异背景：urllib Connection: close vs curl keep-alive、uvicorn 默认单 worker
  Acceptance criteria (agent-executable): harness 稳定运行并产出复现/不复现结论 JSON（两 POST 时延 + 健康检查时延 + 结论字段）
  QA scenarios (name the exact tool + invocation): happy — 两 POST 有界返回（断言通过）；failure — 若观察到挂起：时延数据 + 服务端线程/锁状态采样落盘（供 todo 12）。Evidence <attemptDir>/task-11-acp-legacy-bug-remediation.json
  Commit: Y | test(api): real-HTTP double-POST bounded-latency harness for in-place reset
  Recommended task executor category: deep-low — 线程化服务 + urllib 客户端时序组装需细致处理

- [x] 12. BUG-9(b) 证据驱动处置（最小 seam 修复或负证据）
  What to do / Must NOT do: 依 todo 11 证据二选一：(a) **复现成功** → 仅修被证实的最小阻塞 seam（Q3A 例外条款授权范围：把该慢操作移出 `_lock` 全持锁段，或 SSE 阻塞文件读入线程池 `run_in_executor`，取证据指向的最小者），todo 11 harness 转常驻回归门；(b) **未复现** → 负证据报告：时延数据 + **未覆盖环境差集显式枚举**（代理/中间件/keep-alive/原始客户端环境等；防"此 harness 不复现 ≠ bug 不存在"）+ todo 11 harness 留守卫。结论与证据入 evidence + notepad。禁止预改并发；禁止锁模型整体重构（仅例外条款内最小 seam）；禁止盲改 SSE。
  Closes: GAP-9（处置部分）
  Parallelization: Wave D | Blocked by: 11 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): todo 11 证据（evidence JSON）；src/acp/scheduler/manager.py:912/1216（_lock RLock 语义）；src/acp/api/routes.py:522/552（SSE 阻塞读候选 seam）；原始报告背景（draft findings BUG-9：repo 外、无复现器无日志）
  Acceptance criteria (agent-executable): 分支结论落盘 evidence；(a) 修复后双发有界 + `pytest -m "not slow"` 全绿；(b) 负证据报告完整（时延数据 + 环境差集枚举）
  QA scenarios (name the exact tool + invocation): happy — (a) 修复后 harness 断言通过 / (b) 守卫 harness 绿。Evidence <attemptDir>/task-12-acp-legacy-bug-remediation.md
  Commit: Y | fix(api): unblock in-place reset double-POST (evidence-driven minimal seam) 或 test(api): negative-evidence guard for double-POST latency
  Recommended task executor category: deep-low — 由观测证据定分支与最小修复面，worker 从证据可自洽

- [x] 13. 文档同步收口
  What to do / Must NOT do: ① AGENTS.md ANTI #11：`.structure_history` 与根写全集口径（对齐 todo 1 实际集合 + 豁免注记）；② docs/ACP_Job_File_Layout_Spec.md:160 附近：目录复用语义更新（正向身份判定 + markers 全集）；③ scan 文档/README CLI 段：`--geom-maxiter`（scan 分支复用、默认 200）；④ BUG-8 公开面口径：`GET /api/workflows` 契约变化 compat 说明（对外脚本消费者一行提示，fake 从公开列表消失）+ `_pkill_acp`（monitor.py:314-349）子串风险文档化（todo 3 转来）；⑤ 远端注记：BUG-1 修复对远端节点子进程同代码路径生效、无新增运行时 import（requirements-node.txt 不变）。QA = agent 通读对照实际行为（非句子 grep）。禁止重写无关段落；禁止提交验收副产物脏文件（grad.engrad、tests/baseline/...e2e-task-root.txt）。
  Closes: GAP-1/3/8 的文档侧（支撑 IS-1/IS-3/IS-8）
  Parallelization: Wave E | Blocked by: 1, 3, 4, 9 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): AGENTS.md（ANTI #11、CONVENTIONS 调度上下文段）；docs/ACP_Job_File_Layout_Spec.md:160；README.md（CLI 选项 scan 段）；monitor.py:314-349（仅引用文档化，不改码）；frontend/ACP_Workbench.html:204/521（legacy demo 语境）
  Acceptance criteria (agent-executable): 4 处文档抽查与代码行为一致（agent 读对照：ANTI #11 文本 ↔ `_SCHEDULER_MARKERS` 实际内容；Layout Spec ↔ 复用语义；README ↔ scan 选项；compat 提示存在）
  QA scenarios (name the exact tool + invocation): happy — 抽查 4 处一致；无 failure 场景（文档交付）。Evidence <attemptDir>/task-13-acp-legacy-bug-remediation.md
  Commit: Y | docs: sync scheduler markers, scan iteration budget, public workflow surface
  Recommended task executor category: writing — 纯文档同步

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  逐 todo 对照本计划：What/Must-NOT/References/Acceptance 是否全部兑现；证据文件存在且内容匹配断言；无越界改动（diff 仅落在各 todo 声明的文件面）。
- [x] F2. Code quality review
  全量 diff 审查：PEP 604 新代码、无裸 `except Exception:`、常量无复制、`pathlib.Path`+`os.replace` 原子写、ruff/mypy 风格一致；`ruff check src tests && ruff format --check src tests` 干净。
- [x] F3. Real manual QA
  全量回归：`pytest -m "not slow"` 全绿；目标性 slow：`pytest tests/test_scan_orca_manual_acceptance.py tests/test_qc_interfaces_crest.py tests/test_qc_interfaces_xtb.py tests/test_qc_interfaces_orca.py tests/test_acp_backends.py tests/test_orca_keyword_smoke.py tests/test_frontend_viewer_framing_browser.py tests/test_frontend_measure_edit_browser.py tests/test_frontend_multiframe_playback_browser.py tests/test_api_inplace_reset_http.py --run-slow -v`；再全量 slow 一次（IS-4 终证）。证据落 <attemptDir>/task-F3-acp-legacy-bug-remediation.log。
- [x] F4. Ideal-state fidelity
  IS-1..IS-9 逐行对照交付行为 1:1（IS-1 按前瞻语义：历史 `<work_dir>_1` 目录不算失败）；任何 shortfall 变为新 `- [ ] N.` 行，绝不变为备注。

## Commit strategy
- 每个 todo 一个原子 commit（`fix/feat/test/docs(scope): ...`，A6），实现+测试同 commit。
- F 波发现的修复也走独立 commit（`fix(...): F-wave remediation <id>`）。
- 禁止提交：`grad.engrad`（未跟踪验收副产物）、`tests/baseline/refactor-evidence/e2e-task-root.txt` 的既有脏改动、`.omo/` 之外的任何计划产物。
- 每波收尾 wave gate（`pytest -m "not slow"` + ruff 全绿）通过才允许进入下一波并开始该波的 commit。

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 1, 2 | T2 三层验证：workflow 层（markers/身份分支正向/负向）+ manager 层（fake rerun 调度侧）+ 真二进制 E2E（singlepoint rerun+edit，RESULT manifest 可解析、无 `_1` 兄弟） | <attemptDir>/task-2-acp-legacy-bug-remediation.log |
| IS-2 | 3 | T3 双向矩阵（X vs X__02 互不匹配）+ runner 级 guard 场景（兄弟存活不阻塞新任务） | <attemptDir>/task-3-acp-legacy-bug-remediation.log |
| IS-3 | 4, 5 | T5 真实 ORCA 验收：CCO 手册命令全点收敛、轨迹帧>0；water 回归 PASS | <attemptDir>/task-5-acp-legacy-bug-remediation.json |
| IS-4 | 6, F3 | T6 门控套件全绿（配置就绪环境）+ F3 全量 slow 一次 | <attemptDir>/task-6-acp-legacy-bug-remediation.log；task-F3 log |
| IS-5 | 7 | T7 回归：归档 state 播种后 poll 一轮无陈旧 stage.completed | <attemptDir>/task-7-acp-legacy-bug-remediation.log |
| IS-6 | 10 | T10 整套复跑一致性 + 超时注入探针无级联 | <attemptDir>/task-10-acp-legacy-bug-remediation.log |
| IS-7 | 8 | T8 四 handler Energy 行非 N/A 断言 + 旧 sp_energy 兼容路径 | <attemptDir>/task-8-acp-legacy-bug-remediation.log |
| IS-8 | 9 | T9：GET /api/workflows == 恰 14 active；fake 内部受理存活（含 legacy demo 流）；错误串无 fake | <attemptDir>/task-9-acp-legacy-bug-remediation.log |
| IS-9 | 11, 12 | T11 harness：两 POST ≤60s + reset 中健康检查 ≤5s（或 T12 复现后修复 + 常驻回归门） | <attemptDir>/task-11-acp-legacy-bug-remediation.json；task-12 md |
