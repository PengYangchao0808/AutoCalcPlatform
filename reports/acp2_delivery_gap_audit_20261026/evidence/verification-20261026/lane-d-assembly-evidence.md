# Lane D 验证 — DAG 生产装配 / live harness / 证据索引 / 测试有效性

- 验证对象：`ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（HEAD，2026-10-26 工作树）
- 验证范围：`GAP_REPORT.md` §G15 / §G18 / §G19 / §G20
- 方法：只读源码检索、git 历史与对象可达性、既有回执/JUnit/日志复核、单文件 `pytest --co` 收集；未跑全量测试
- 约束遵守：未修改业务代码/报告/既有证据，未 commit，未重启服务，未提交 QC/LSF，未调用模型 API
- 工具说明：环境未安装 `rg`，等价使用 `grep -rn`；未改变检索语义

## 结论总览

| 项 | 结论 | 一句话 |
|---|---|---|
| G15 Recipe DAG 未进生产执行链 | **确认** | `BoundedStepDagExecutor` 仅存在于库、测试与静态验收脚本；生产 `FrozenWorker`/`CccpTaskExecutor` 只执行一个 `TaskRequest`，且 DAG 的 CCCP 适配器 `cccp_compute_dispatch` 零调用点 |
| G18 live harness 固定缺口分支 | **确认（含两点校正）** | 204–223 行确为硬编码 `False` 的未实现 live 分支，不是动态探测；但它是计划 C 明确 `[~]` blocked 的 T-30 的占位，不是"漏排期"；另发现该 receipt 把离线运行标成 `evidence_kind=live` |
| G19 签收对象与证据索引不统一 | **确认** | 下表列出 8 个职责不同的 SHA；`abec2bc` 已不被任何 ref 包含；`h7-index`/`summary` 的差异仅为未提交的元数据刷新，且 `h1-r2.json` 本身 untracked |
| G20 测试不验证互相消费 | **确认** | 客户端用 recording/fixture client 自建 18 工具表，服务端自测 8 工具与自定义协商；两侧都绿，真实 SDK/参数/证据格式仍失败；跨语言"生产者真实输出 → 消费者真实实现"门不存在 |

---

## G15：Recipe DAG 实现没有进入生产任务执行链

### 验证结论

**确认。** 报告的核心事实成立，且比报告描述的更彻底：不仅 `BoundedStepDagExecutor` 没有生产构造点，连它为 CCCP 提供的唯一计算适配器 `cccp_compute_dispatch` 也没有任何调用点。

### 报告原声明不准确处

1. 报告写"本轮未找到 Controller/worker 的生产构造与选择入口"。实际上生产构造/选择入口是存在的：
   - `src/acp_services/worker_bootstrap.py:786-793` 构造 `FrozenWorker`；
   - `src/acp_services/runtime/execution_chain.py:240-264` 选择 `envelope` vs `legacy`。
   准确表述应是：**生产入口存在，但它只构造/选择一个单步 `FrozenWorker`；不存在构造 `BoundedStepDagExecutor` 或把 Recipe 编译成 DAG 的生产入口。**
2. 报告只检索了 `BoundedStepDagExecutor` 的名字。补充事实：`step_dag.py:1074` 的 `cccp_compute_dispatch` 在 `src/`、`scripts/`、`tests/` 中只有定义，没有调用；连验收脚本也用静态模拟 dispatch。
3. 报告称"科学 Recipe/TaskBinding 与局部 DAG 测试通过"是准确的；但应注明这些测试直接 `new BoundedStepDagExecutor(...)`（`tests/test_acp2_runtime_science_binding.py:291-305`），属于纯库级证明。

### 证据

**E15-1 定义与唯一实现位置**

- `src/acp_services/runtime/step_dag.py:761`：`class BoundedStepDagExecutor`
- `src/acp_services/runtime/step_dag.py:69`：`EXECUTION_LOOP_OWNER = "acp_services.runtime.step_dag.BoundedStepDagExecutor"`
- `src/acp_services/runtime/step_dag.py:1026-1066`：`recover(...)`
- `src/acp_services/runtime/step_dag.py:1074-1099`：`cccp_compute_dispatch(...)`（唯一的 CCCP 计算适配器）

**E15-2 非测试调用点只有验收脚本**

```text
$ grep -rn "BoundedStepDagExecutor" src scripts tests --include=*.py   # 摘录
src/acp_services/runtime/step_dag.py:761:class BoundedStepDagExecutor:
src/acp_services/runtime/step_dag.py:1037:    ) -> BoundedStepDagExecutor:
tests/test_acp2_runtime_science_binding.py:297-305   # 直接构造
scripts/acceptance/acp2_runtime.py:7920-7929          # 直接构造
scripts/acceptance/acp2_runtime.py:9210               # recover
```

- `tests/test_acp2_runtime_science_binding.py:291-305`：测试 helper 直接构造执行器。
- `scripts/acceptance/acp2_runtime.py:7919-7929`：验收脚本的 `_b18_executor(...)` 直接构造执行器；其 dispatch 是 `_b18_static_dispatch`（`scripts/acceptance/acp2_runtime.py:7880-7903`），publish 是 `_b18_static_publish`（同文件 7906-7917），均为 **STATIC/SIMULATED**，不调用 `cccp_compute_dispatch`。

**E15-3 生产 worker 只执行一个 TaskRequest，不跑 DAG**

- `src/acp_services/worker_bootstrap.py:786-793`：

```python
worker = FrozenWorker(
    executor=executor if executor is not None else CccpTaskExecutor(),
    step_requirements=ServicesStepRequirements(),
    plan_verifier=FrozenPlanVerifier(),
)
```

- `src/acp_services/runtime/worker.py:313-350`：`CccpTaskExecutor.execute()` 只做一件事——把 `plan.payload` 解成单个 `TaskRequest`，`getattr(calculation, f"run_{request.task.value}")` 后调用一次。
- `src/acp_services/runtime/worker.py:1338-1424`：`FrozenWorker.execute()` 是"exactly one frozen attempt"；`validate()` 里的 `for step in plan.steps`（1366-1374）只做前置条件校验，`execute()` 不按 `plan.steps` 迭代。执行路径之外，`plan.steps` 仅在 `frozen_plan_to_dict`（worker.py:194/211）和 `plan_contracts.py:305` 的校验/序列化中被读取。
- 因此 `FrozenPlan.steps` 即便有多项，生产执行仍只运行 `plan.payload` 的单个 `TaskRequest`；没有 step 状态机、readiness、publish 重试或 step 恢复。

**E15-4 生产选择逻辑只选 envelope/legacy，不选 DAG**

- `src/acp_services/runtime/execution_chain.py:217`：`DEFAULT_ENVELOPE_WORKFLOWS = frozenset({"singlepoint"})`
- `src/acp_services/runtime/execution_chain.py:240-264`：`select_execution_entry()` 只在 `envelope` / `legacy` 之间选择，依据是 workflow closure、remote support、frozen group 是否存在；没有 `step_dag` 分支。
- `src/acp_services/runtime/commands.py:60-79`：`RuntimeCommands.submit()` 直接转发到 `submission.submit(...)` 端口，不做 Recipe→DAG 编译。
- `src/acp_services/runtime/capability_registry.py:934-979`：`run_frozen()` 自称"the only execution seam"，最终 `science_binding.build_task_request(form, projection)` + 单个 `run_<task_kind>`（978-979），仍是单任务。补充：`run_frozen` 目前也只有验收脚本与测试调用（`scripts/acceptance/acp2_runtime.py:12670`、`tests/test_acp2_runtime_adapter_upgrade.py:453`），并非生产装配点；这里用它证明"即便走该 seam 也不是 DAG"，不是声称它已在生产链上。

**E15-5 科学绑定只产出单请求，不产出 DAG**

- `src/acp_services/runtime/science_binding.py:556`：`build_task_request(...)` 返回单个 `TaskRequest`。
- `src/acp_services/runtime/science_binding.py` 中没有任何 `BoundedStepDag`/`step_dag` 引用（`grep -n "step_dag\|BoundedStepDag" src/acp_services/runtime/science_binding.py` 无输出）。
- `src/acp_services/runtime/step_dag.py` 反而 import 了 `science_binding.document_digest`（37 行），方向是"DAG 依赖绑定"，不是"绑定生产 DAG"。

**E15-6 DAG 的 CCCP 计算适配器零调用点**

```text
$ grep -rn "cccp_compute_dispatch" src scripts tests --include=*.py
src/acp_services/runtime/step_dag.py:63:    "cccp_compute_dispatch",
src/acp_services/runtime/step_dag.py:1074:def cccp_compute_dispatch(
```

没有任何模块调用它；验收脚本使用自己的静态 dispatch，测试也使用 `_dispatch` 替身。也就是说，即使手工构造 DAG，也从来没有一条装配好的"真实 cccp 计算 + 真实发布"链路被验收过。

**E15-7 引入提交与计划口径**

- `git log --follow -- src/acp_services/runtime/step_dag.py` 只有一条：
  `938e894 2026-10-23 16:12:08 +0800 feat(runtime): bind science recipes and evidence`
  该提交同时新增 `step_dag.py`(1099 行)、`science_binding.py`(915 行)、`research_resources.py`(583 行) 和 B18 证据/验收脚本改动。
- `.omo/evidence/task-20-acp2-parallel-plan-b-runtime.md:34-71` 明确把交付拆成：
  - `### B18b — one platform task, persistent bounded steps`（库实现）
  - `### B18c — STATIC/SIMULATED three-step proof`（`real_qc=false`）
  - `### Not claimed: No real DA chain, no real QC...`（131-135 行）
- `reports/acp2_implementation/bb45a46/B/B18/B18a-to-C18.json:7`：
  `"c18_does_not_need": "B18b execution wiring or B18c recovery evidence"`
  这句本身承认存在一个尚未交付的 "B18b execution wiring"。
- 计划 `.omo/plans/acp2-parallel-plan-b-runtime.md:368` 要求 B18 拆成 **B18a contract conversion / B18b execution wiring / B18c recovery/publication verification**；同文件 `:367` 把任务 20 标成 `[x]`。
- B20 的 da-submit（第一次真实提交机会）为 `NOT_VERIFIED(c24a_absent)`：
  `reports/acp2_implementation/bb45a46/B/B20/B20-report.md:3`
  `reports/acp2_implementation/bb45a46/B/B20/da-runtime-da-submit-20261023T091047Z-1425776-5bf82a03.json` → `result=NOT_VERIFIED`, `reason=c24a_absent`, `submission_attempted=false`。
  因此 B20a-exec 从未运行，也就没有任何后续验收能暴露"生产链没有接 DAG"。

### 根因链

**技术根因**

1. 生产执行层是"单 attempt = 单 `FrozenWorker` = 单 `TaskRequest`"（`worker.py:313-350`, `1338-1424`）。它没有任何 step 循环，也不读取 `plan.steps` 作为执行序列。
2. 生产入口 `RuntimeCommands.submit` 直接转发端口；所有现有执行 seam（`capability_registry.run_frozen`、`science_binding.build_task_request`）都只处理单请求，且 `run_frozen` 本身也只有验收/测试调用（`acp2_runtime.py:12670`、`test_acp2_runtime_adapter_upgrade.py:453`）。
3. `BoundedStepDagExecutor` 与生产层之间缺少"编译 + 构造 + dispatch 注入"三件事；唯一准备好的注入点 `cccp_compute_dispatch`（`step_dag.py:1074`）没有调用者。
4. `execution_chain.py` 的 dispatcher 只解决 envelope/legacy 选择（240-264），不解决多 step DAG 选择。

**设计/流程根因**

1. B18 明确采用"库 + 静态验收先行"的分割：B18a 交 C18、B18b 做库、B18c 用 STATIC/SIMULATED 三 step 证明（`.omo/evidence/task-20-...:34-71`）。这解释了"为什么库先出现"，但不构成生产接线。
2. "生产接线后补"的 B18b execution wiring 在任务台账里被 `[x] 20`（计划 `:367`）覆盖：验收条件只要求 `da-preflight` 无计算分支 + 静态/模拟三 step，不要求生产路径到达 DAG。于是缺件在计划层不可见。
3. B20a-exec 因 C24a 缺失被阻塞（`B20-report.md:3`），真实 DA 提交从未发生，DAG 的生产接线失去了最后一个会被强制验证的关口。

**结论**：不是"单文件遗漏"，而是**"库+验收先行"按计划执行、"生产接线后补"从未补上，且任务台账以静态验收把缺口标成完成**。

---

## G18：真实纵向证据不足，部分 live harness 没有执行实现

### 验证结论

**确认（附两点校正）。** `scripts/acceptance/acp2_experience_scenario_research_loop_live.py:203-223` 确实在跑完离线证明后硬编码 `model_delivered=False / controller_delivered=False` 并返回 `upstream_not_delivered`；这不是动态探测。

### 报告原声明不准确处

1. 结论中的"implementation missing"分类是对的，但不应把本项写成"未排期"：计划 C 的 T-30（真实小型研究轮次）**已排期但显式 blocked**（`[~]`），T-29 的验收标准本身只要求离线状态机走查。因此更准确的是"排期后一直 blocked，未在审计前解除"。
2. 报告没有提到一个具体证据分类问题：该离线运行的 receipt 被写成 `"evidence_kind": "live"`，而同一目录的 `control-report.json` 是 `"evidence_kind": "offline_fixture"`，且 `live_model_calls=0`。结果虽是诚实的 `NOT_VERIFIED`，但 `live` 标签会污染证据分类。
3. 报告称"只补环境变量再跑相同脚本不会验证真实研究循环"完全正确：`_control()` 从未读取任何 Controller/model 环境变量，只读 `ACP2_C_SOURCE`/`ACP2_EVIDENCE`。

### 证据

**E18-1 代码是固定未实现分支，不是探测**

- `scripts/acceptance/acp2_experience_scenario_research_loop_live.py:5-6`（模块 docstring）：
  `no live model and no Controller are delivered yet, so the product row is NOT_VERIFIED(upstream_not_delivered)`
- 同文件 `:19-20`：`The body never starts a model, DSH, Controller, QC or browser process.`
- 同文件 `:203-223`：

```python
report = {
    "schema": "acp2.experience.research-loop.control.v1",
    "evidence_kind": "offline_fixture",
    ...
    "live": {
        "model_delivered": False,
        "controller_delivered": False,
        "result": "NOT_VERIFIED(upstream_not_delivered)",
    },
    "live_model_calls": 0,
}
...
return _not_verified(
    context,
    "upstream_not_delivered:live_model_and_controller_absent",
)
```

- 同文件 `:270-282`：只注册 `control` / `search` 两个 subcase，没有 live 执行分支。
- `grep -n "controller\|model\|http\|fetch"` 在该文件中只命中 docstring、`models.ts` 文件名，以及 `model_delivered`/`controller_delivered`/`live_model_calls` 这些字面量；没有 Controller URL、模型客户端或 HTTP 调用。**没有动态探测。**

**E18-2 实际回执状态**

- `reports/acp2_implementation/bb45a46/C/research-loop-live/research-loop-live-r1/main/control/experience_research-loop-live-experience_control-research-loop-live-r1.json`：
  `result=NOT_VERIFIED`, `reason=upstream_not_delivered:live_model_and_controller_absent`, `evidence_kind=live`
- 同目录 `control-report.json`：`evidence_kind=offline_fixture`, `live_model_calls=0`, `live.model_delivered=false`, `live.controller_delivered=false`
- 该运行确有离线证明：`research_loop.offline_proof_all_passed` PASS、`cases=7`、30 条断言（log 中 `offline_proof` 显示 `all_passed: true, assertions: 30, cases: [...]`）。

**E18-3 集成回执 H0–H7**

`reports/acp2_implementation/bb45a46/integration/h7-index.json` 的 8 行（按 H0→H7 顺序，title 行见 228/253/280/308/331/367/394/423）：

| 行 | result | gate | 证据行 |
|---|---|---|---|
| H0 Baseline / path ownership / contract manifest | PASS | CLOSED | `h7-index.json:202` |
| H1 Consumable minimal contracts | NOT_VERIFIED | OPEN | `h7-index.json:247` |
| H2 P0 real MCP process + DSH default negotiation | NOT_VERIFIED | OPEN | `h7-index.json:274` |
| H3 P1 Local/LSF closed loop | NOT_VERIFIED | OPEN | `h7-index.json:302` |
| H4 Limited DA science closed loop | NOT_VERIFIED | OPEN | `h7-index.json:325` |
| H5 Third-party plugin extension | PASS | CLOSED | `h7-index.json:349` |
| H6 Cross-domain reliability / isolation / recovery | NOT_VERIFIED | OPEN | `h7-index.json:388` |
| H7 Release and independent installation | NOT_VERIFIED | OPEN | `h7-index.json:417` |

- 8 个回执的 `candidate_revision` 全部是 `abec2bc4f1d302827248d36171973df5bc1bc4dd`（`reports/acp2_implementation/bb45a46/integration/receipts/h0..h7.json`，h1 另有 h1-r2）。
- H3 的 receipt 与 index 原因不一致：receipt 是 `lsf_prerequisite_missing;c_dsh_browser_absent;a_scientific_audit_absent`，index 的 H3 行省略了 `lsf_prerequisite_missing`（`grep -n '"result"' h7-index.json` + Python 提取）。result 未变，但说明索引与回执不是同一份事实的机械投影。

**E18-4 未提交修改与 untracked 材料**

```text
$ git status --porcelain reports/acp2_implementation/bb45a46/integration/
 M reports/acp2_implementation/bb45a46/integration/h7-index.json
 M reports/acp2_implementation/bb45a46/integration/summary.json
?? reports/acp2_implementation/bb45a46/integration/receipts/h1-r2.json

$ git diff --numstat .../h7-index.json .../summary.json
9  5  .../h7-index.json
2  1  .../summary.json
```

- diff 内容仅为元数据刷新：`harness_revision` 旧→新、`contract.manifest_digest` 旧→新、`evidence_revision` 旧→新、`runs` 的 `h1.json`→`h1-r2.json`，并加入 `refresh_note`；**没有任何行结果变化**（`git diff` 全文已核对）。
- `h1-r2.json` 本身是 `??` untracked，但被 tracked 的 `summary.json` 的 `runs` 引用（`summary.json:29-37`）。
- `reports/acp2_final_acceptance_20261024/` 整目录 untracked：`git ls-files ... | wc -l` = 0。

**E18-5 计划口径与引入提交**

- `git log --follow -- scripts/acceptance/acp2_experience_scenario_research_loop_live.py` 只有 `f413bf8 2026-10-23 feat(acp2-c): implement single supervisor research loop skills`；`git blame -L 203,223` 全部指向 f413bf8。
- `.omo/plans/acp2-plan-c-rph-workbench.md:472-478`：T-29 标 `[x]`，验收条件是 `pnpm --dir rph build`、`test` 通过且 skill I/O ≥8，QA 是"三条命令 + 一次离线状态机走查"；没有要求 live model/Controller。
- 同文件 `:480-487`：T-30 标 `[~]`，标题即 `blocked by T-28 + live model/Controller + A-req-04/B-req-15`；T-30a 才要求真实小型 Recipe 的完整轮次。
- 因此：离线分支是 T-29 的交付物，live 分支属于未解除阻塞的 T-30；harness 按 B01 规则"未实现场景返回 2 + 具名缺项，禁止假 PASS"写出占位，行为本身是合规的。

### 根因链

**技术根因**

1. `_control()` 没有 live 分支：不读取 Controller URL、不创建模型客户端、不装配 Supervisor 到平台端口，只调用 `rph/scripts/research-loop-offline-proof.mjs`（`acp2_experience_scenario_research_loop_live.py:133-145`）。
2. 因此 `model_delivered/controller_delivered` 只能是字面量 `False`；即便补上环境变量，代码路径也不会改变。
3. receipt 的 `evidence_kind` 由外层 harness 的 phase→kind 规则决定为 `live`（`scripts/acceptance/acp2_experience.py:639-645`：`experience:control` 不属于 fixture phase，故判 live），与内层 `control-report` 的 `offline_fixture` 不一致；这属于证据标签缺陷，但不是假 PASS。

**设计/流程根因**

1. 计划把 T-29（离线状态机）与 T-30（真实 live 轮次）拆开；T-29 以离线验收即可 `[x]`，T-30 一直 `[~]` blocked。
2. B20、H4/H7 等同类 live 路径也受 C24a/A-20 等外部缺件阻塞（H4/H7 同样 `NOT_VERIFIED`），所以"先交离线证明、live 后补"是全局交付策略，不是该脚本独有的疏漏。
3. 缺少"blocked 任务的占位必须与 evidence_kind 一致"的校验：`control-report.json` 是 offline_fixture，外层 receipt 却是 live。

**结论**：报告"不是动态探测而是未实现 live 分支"的判断正确；根因应写成"T-30 排期但 blocked、未解除，harness 按规则写 NOT_VERIFIED 占位"，并补一条 `evidence_kind=live` 的标签缺陷。

---

## G19：签收对象与证据索引不统一

### 验证结论

**确认。** 签收/候选/发布/落地材料中至少出现 8 个不同 SHA（见 E19-1 表），且其中一个已不可从任何 ref 到达；`h7-index`/`summary` 的修改是未提交的元数据刷新，`h1-r2.json` 本身未跟踪。

### 报告原声明不准确处 / 需补强处

1. 报告写"最终验收目录 summary 顶层仍是 f6dd3c9"正确，但应补一句：同一文件的 `remediation.landed_commit` 已是 `ec4a0b3`（`summary.json:124-125`）。所以这不是"整个目录不知道新 revision"，而是**顶层单一字段没有随 remediation 更新**，机器读者取顶层会读错。
2. 报告未提到 `integration/candidate.json:5` 还有第三个 SHA `77ac1de1...`（`acp2/integration-p1` 分支），以及 `release-candidate.json` 的 `source_candidate` 同时记录 `revision=6ec01ec`、`base_release_source_revision=abec2bc`、`landed_head=4937907`。这让"多个 summary"问题比报告描述更严重。
3. 报告写"`h7-index/summary` 存在用户已有未提交修改"正确；但需说明 diff 是元数据刷新（digest/harness_revision/h1-r2），不是结果改写，以免读者误以为有人改了 PASS/NOT_VERIFIED。
4. 报告未指出：`abec2bc` 已不被任何 ref 包含（dangling），因此"冻结在 abec2bc 的不可变回执"无法从任一分支/提交图复现；`h1-r2.json` 也未跟踪。

### 证据

**E19-1 SHA 清单（全部经 `git cat-file -t` 确认为 commit）**

| 角色 | 文件:行 | SHA |
|---|---|---|
| 当前 HEAD | `git rev-parse HEAD` | `ec4a0b39b26645139c01aa9578fbedd0c3db23a2` |
| 集成回执候选 | `reports/acp2_implementation/bb45a46/integration/summary.json:2` | `abec2bc4f1d302827248d36171973df5bc1bc4dd` |
| release-fix5 source | `integration/summary.json:24`；`integration/release-candidate.json` `source_candidate.revision` | `6ec01ec96fa81b89bec8ff321c38e3db2dc154d2` |
| 集成候选（另一份） | `integration/candidate.json:5` | `77ac1de1af6a5db6b57f4f904a859305ff73042c` |
| h7-index landed head | `integration/h7-index.json:429` | `493790709830eac4ce347b5fb81414050f7a1e21` |
| 最终验收 summary 顶层 | `reports/acp2_final_acceptance_20261024/summary.json:4` | `f6dd3c928b4f11dc8c3782c4b15c7130cc856a0b` |
| 最终验收 remediation | 同文件 `:124-125` | `ec4a0b39b26645139c01aa9578fbedd0c3db23a2` |
| r3 package-install | `reports/acp2_final_acceptance_20261024/evidence/package-install-r3/summary.json:2`；`.../package-install-main-20261024T204122Z-3210803-3076c61a.json` | `ec4a0b39b26645139c01aa9578fbedd0c3db23a2` |
| r3 release manifest | `reports/acp2_final_acceptance_20261024/release-r3/release-manifest.json` `source` | `ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（tree `5815b19d...`，与 HEAD tree 一致） |
| B18 证据候选 | `reports/acp2_implementation/bb45a46/B/B18/summary.json:2` | `10eb71e4bc1811054c9d19ae4fd71d974e027f8a` |
| B20 证据候选 | `reports/acp2_implementation/bb45a46/B/B20/summary.json:2` | `bc59e04758450321747068e679ca3a8d0efc5ad7` |

补充统计：`reports/**` 下 `summary.json` + `*-main-*.json` 共 224 个文件带 `candidate_revision`，**43 个不同值**。这直接说明"多个 summary 不能共同充当一个完整签收事实"。

**E19-2 可达性**

```text
$ git cat-file -t ec4a0b3...  -> commit
$ git cat-file -t abec2bc...  -> commit
$ git cat-file -t 6ec01ec...  -> commit
$ git cat-file -t f6dd3c9     -> commit
$ git for-each-ref --contains abec2bc4f1d302827248d36171973df5bc1bc4dd --format='%(refname)'
(空)
$ git merge-base --is-ancestor abec2bc... HEAD; echo $?
1
$ git describe --all --contains abec2bc...
fatal: cannot describe 'abec2bc...'
```

- `abec2bc`：**不被任何 ref 包含**，也不是 HEAD 祖先；只在对象库中存在。
- `6ec01ec`：是 HEAD 祖先（`git rev-list --count 6ec01ec..HEAD` = 104），同时被 `refs/heads/acp2/integrated-20261024`、`refs/heads/feat/frontend-plan1-closure`、`refs/stash` 包含。
- `77ac1de`：`refs/heads/acp2/integration-p1`，不是 HEAD 祖先。
- `f6dd3c9`：HEAD 的父提交（`git log -1 --format='%h %p' ec4a0b3` → `ec4a0b3 f6dd3c9 ...`）。
- `4937907`：HEAD 祖先。

**E19-3 最终验收 summary 的内部矛盾**

- `reports/acp2_final_acceptance_20261024/summary.json:4` `candidate_revision=f6dd3c9`、`:5` `candidate_tree=2e21cf63...`。
- 同文件 `:124-125` `remediation.landed_commit=ec4a0b3`、`parent_revision=f6dd3c9`。
- 同目录 r3 证据是 `ec4a0b3` + tree `5815b19d...`（`release-r3/release-manifest.json` 的 `source`），与 HEAD tree 一致；r3 package-install 结果 `PASS`（19 条断言）。
  → 目录里"实际跑过的证据"比顶层 `candidate_revision` 新一个 remediation。

**E19-4 未提交/未跟踪**

```text
$ git status --short
 M reports/acp2_implementation/bb45a46/integration/h7-index.json
 M reports/acp2_implementation/bb45a46/integration/summary.json
 M tests/baseline/refactor-evidence/e2e-task-root.txt
?? reports/acp2_final_acceptance_20261024/
?? reports/acp2_integration_20261024/
?? reports/acp2_acceptance_20261024/
?? reports/acp2_delivery_gap_audit_20261026/
...
```

- `git diff --numstat`：`h7-index.json 9/5`、`summary.json 2/1`；diff 只改元数据与 `h1.json`→`h1-r2.json`，无 result 变化。
- `git ls-files reports/acp2_final_acceptance_20261024 | wc -l` = `0`：整个最终验收目录 untracked。
- `reports/acp2_implementation/bb45a46/integration/receipts/h1-r2.json` 是 `??`，却被 `summary.json:29-37` 的 `runs` 引用。若只提交 `summary.json` 而不 `git add h1-r2.json`，索引会指向不存在文件。

**E19-5 外部根路径**

- `integration/h7-index.json:3`：A 证据来源 `/ACPServer/acp2-development/A git rev-parse HEAD`
- `integration/h7-index.json:65`：C 证据来源 `/ACPServer/acp2-development/C ...`
- `integration/h7-index.json:261-263`：C DSH 证据 `external_ref=/ACPServer/acp2-development/evidence/C/...`，状态 `NOT_VERIFIED(external_evidence_not_in_repo)`
- `reports/acp2_implementation/bb45a46/B/B18/B18a-to-C18.json:28` 起：9 处 `/ACPServer/acp2-development/A/...` schema 路径
- `reports/acp2_final_acceptance_20261024/evidence/package-install-r3/fixture.json:5-17`：`/ACPServer/acp2-evidence/a-science-20261022/...` 与 `/ACPServer/acp2-development/evidence/C/...`
  → r3 的 PASS 依赖仓库外材料；仓库内只能看到 fixture 路径，不能独立复核这些 A/C 证据。

### 根因链

**技术根因**

1. 每轮验收各自写自己的 `summary.json`，且 schema 只有一个顶层 `candidate_revision`；没有"receipt candidate / release source / remediation head / landed head / BOM"分字段的聚合对象。
2. 最终验收 summary 的 remediation 只写在嵌套 `remediation` 块（`:124`），没有回写顶层；`r3` 证据与顶层 revision 因此不一致。
3. 集成 summary 为保持历史不可变，故意把 `candidate_revision` 冻结在 `abec2bc`（`:2-3`），另以 `release_candidate.source_revision=6ec01ec` 指向 fix5（`:24`）；`release-candidate.json` 又同时记录 base/revision/landed_head 三个 SHA。这是"一份文档内表达多轮"的模型缺陷，不是单纯笔误。
4. `h7-index`/`summary` 的元数据刷新停留在工作树（`M`），`h1-r2.json` 未跟踪，导致"审计读到的索引"无法由 HEAD 复现。

**设计/流程根因**

1. 没有单一签收 owner 维护一个当前候选 BOM（source revision/tree、各制品 digest、配置/依赖锁、各验证实际运行对象）。每个任务只对自己那轮的 `summary` 负责。
2. "历史证据不可改写"是正确的，但没有配套"当前索引 + 覆盖表"；旧的 A 证据还放在仓库外，进一步使单一索引不可闭合。
3. 最终验收在 `f6dd3c9` 出一版 `NOT_APPROVED`，随后 `ec4a0b3` 做 remediation 并跑 r3；新证据没有驱动 summary 顶层 revision 更新，说明验收产物不是由"最后一次实际运行"生成，而是手工维护的。
4. 任务收口以"目录里有一份 summary"作为证据，而不是"summary 的 revision/tree 与所引 receipt 的 revision/tree 一致"，因此 43 个 candidate 值可以长期共存。

**结论**：不是"有人篡改历史 SHA"；是**多轮验收各自留档 + 缺少单一 BOM/覆盖表 + 顶层字段未随 remediation 更新 + 部分证据 untracked/仓库外**，导致没有一个文件能单独回答"这一版到底跑了什么"。

---

## G20：测试验证各模块内部一致，却没有验证双方真的能互相消费

### 验证结论

**确认。** 报告列举的四类测试特征全部可核实；并且找到了比报告更强的证据：客户端测试用自有常量断言"18 工具"，服务端测试用自有常量断言"8 工具"，两者同时通过。

### 报告原声明不准确处 / 需补强处

1. 报告写"168 项原有定向测试与 80 项 RPH 测试通过"正确，但应注明证据链：普通 `targeted-pytest.log` 只有 1 行截断输出（`wc -l` = 1，且含 `EEEE`），168 全绿来自 `targeted-pytest-unrestricted.xml`；80 RPH 来自 `rph-tests-no-isolation.log`（普通 runner 只显示 5 个文件级结果）。报告 §2.3 已说明，但 G20 正文没有重复这个限定。
2. 报告写"服务端 MCP 测试证明基础 8 工具和自定义协商规则"正确；应补充：`tests/test_services_mcp.py` 用进程内 `_Fakes`，`tests/test_acp2_runtime_mcp_process.py` 用手写 stdio 客户端，二者都不是 DSH SDK。
3. 报告写"前端审批测试检验本地卡片规则"正确且可加强：测试标题就是 `approval module owns no network call`（`frontend/js/module_tests/acp2_approval.test.js:418-422`），并断言模块无 `fetch`/`XMLHttpRequest`；`:579-613` 的"real ACPClient.research"也只是注入 `v2(path)` stub，不写平台。
4. 报告未提到 18 vs 8 与扩展版本 1.1.0 vs 1.0.0 这两个"双方各自绿、合起来红"的实例；它们比抽象描述更能证明"没有互相消费门"。

### 证据

**E20-1 RPH Bridge 测试使用 recording/fixture client**

- `rph/test/bridge-contract.test.ts:41-64`：

```ts
interface RecordingClient extends PlatformClient { readonly calls: RecordedCall[]; }
function recordingClient(handler?: ResultHandler): RecordingClient { ... }
```

- 该文件 29 个 `test(...)`，全部以 `recordingClient(...)` 构造 `Bridge`（`grep -n recordingClient` 命中 20+ 处），从不启动 stdio/HTTP transport。
- `rph/test/research-loop.test.ts:95-118`：`report()` 手工构造 RPH 形状的 `EvidenceReport`（`schema`, `project_id`, `research_id`, `round`, `task_ref`, `status`, `decision`, `claims`, `evidence_refs`, `terminal_event_key`）。
- A 的 EvidenceReport schema 要求完全不同：`docs/reference/next-change/acp2-contracts/science/evidence.schema.json` 的 `required = [schema_version, report_id, subject, execution, claims, limitations]`。
- `reports/acp2_delivery_gap_audit_20261026/evidence/node-probes.json` 的 `science_report_rejected_by_rph` 记录了 **20 项**结构问题（`$.execution`/`$.limitations`/`$.schema_version`/`$.subject` 为 additional_property，`$.schema`/`$.project_id`/`$.research_id`/`$.round`/`$.task_ref`/`$.status`/`$.decision` 为 required，`claims[0]` 的 `limitations`/`missing_checks`/`scope`/`source_refs`/`validator` 等也为 additional_property）。
  → 两侧各自按自己的 schema 建 fixture；真实 A 报告被 RPH 拒绝，而 RPH 80 项全绿（含 `research-loop.test.ts` 的 15 项）完全覆盖不到。

**E20-2 服务端 MCP 测试：8 工具 + 自定义协商，不跑 DSH SDK**

- `tests/test_services_mcp.py:279-284`：

```python
def test_tools_list_is_the_frozen_allowlist() -> None:
    tools = _call(_server(), "tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == set(mcp.TOOL_ALLOWLIST)
    assert names == set(mcp.McpTool.ALL)
```

- `src/acp_services/entrypoints/mcp.py:130`：`MCP_PROTOCOL_VERSION = "2025-06-18"`；`:136` 只支持该版本。
- `src/acp_services/entrypoints/mcp.py:703-728`：`_negotiate_protocol_version()` 的自定义规则——如果 `requested` 不在支持集，则**只有客户端额外声明 `protocolVersions` 列表**且其中有共同版本时才降级，否则抛 `unsupported_protocol_version`。
- `tests/test_acp2_runtime_mcp_process.py:29-41`：`PROTOCOL_VERSION="2025-06-18"`、`EXPECTED_TOOLS` 恰为 8 个；`:441-458` `test_no_common_version_fails_and_blocks_tools` 显式断言这种"无共同版本即 -32602"的行为——**测试把自定义规则固化了**。
- 该文件不 import 任何 `@modelcontextprotocol`/Python MCP SDK；`grep -rn "modelcontextprotocol" tests/` 无命中。它用手写 `_McpProcess`（`tests/test_acp2_runtime_mcp_process.py:236-265`）发 JSON-RPC，且 `handshake()` 默认只发 `protocolVersion`，需要时才加 `protocolVersions`。
- 真实 DSH SDK 探针：`reports/acp2_delivery_gap_audit_20261026/evidence/sdk-probe.json`

```json
{"sdk_client": ".../@modelcontextprotocol/sdk@1.31.0/.../client/index.js",
 "connected": false,
 "code": -32602,
 "message": "MCP error -32602: unsupported protocol version '2025-11-25'",
 "data": {"code":"unsupported_protocol_version","supported":["2025-06-18"],
          "requested":"2025-11-25","client_versions":[]}}
```

  → SDK 默认只发 `protocolVersion=2025-11-25`，不发明非标准 `protocolVersions`；服务端因自定义规则拒绝。测试只覆盖"符合自定义规则"的客户端。

**E20-3 8 vs 18 的双方自证**

- 客户端：`rph/test/bridge-contract.test.ts:183-200`

```ts
test("p2 operations all map to tools in the committed 18-tool allowlist", ...) {
  const p2Tools = new Set<string>();
  ...
  assert.equal(P1_TOOL_NAMES.length, 8);
  assert.equal(p2Tools.size, 10);
  assert.equal(allowlist.size, 18);
}
```

- 服务端：`tests/test_services_mcp.py:279-284` 断言 `McpTool.ALL`（8 个）；`src/acp_services/entrypoints/mcp.py:268` `TOOL_ALLOWLIST = frozenset(McpTool.ALL)`。
- B 的 P2 契约确有 10 个 `acp_research_*_v1` 工具（`docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`，`grep '"name": "acp_'` 得到 10 个），合计 18；但服务端 `tools/list` 只返回 8 个，且无 P2 工具。
  → 客户端测试用"自己算出的 18"通过；服务端测试用"自己的 8"通过；**没有任何测试把客户端的 18 表与服务端的实际 `tools/list` 对照**。

**E20-4 扩展版本 1.1.0 vs 1.0.0**

- 服务端 initialize 返回 `acp.research.v1` version `"1.1.0"`（`src/acp_services/entrypoints/mcp.py:1063`；B 契约 `application-api.v1.json` 的 `mcp_extensions.acp.research.v1.version` 也是 `1.1.0`）。
- RPH Bridge 只接受 `1.0.0`：`rph/packages/bridge/src/index.ts:398` `BRIDGE_EXTENSION_VERSION = "1.0.0"`；`rph/test/bridge-contract.test.ts:225` 的 `p2 negotiation rejects a mismatched extension version` 只测自己常量。
- `node-probes.json` 的 `server_extension_compatibility`：`accepted=false`, `rule=extension_version_mismatch`, `expected_version=1.0.0`, `observed_version=1.1.0`。
- 另：B 自己的 `p2-research-operations.v1.json` 写 `extension_version: "1.0.0"`，而 `application-api.v1.json` 写 `1.1.0`，B 内部也不一致。

**E20-5 MCP backends 测试的 fixture 替代**

- `tests/test_acp2_runtime_mcp_backends.py:50-76`：

```python
monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
...
manager = app.state.job_manager
manager._start_submission_thread = lambda *args, **kwargs: None
```

- 这两行替代了能力/资源准入检查；关闭提交线程后，`acp_submit` 只验证"返回 durable ref"，不验证真实执行。测试自身 docstring 也承认是"real FastAPI Controller ... 不是 `_Fakes`"（`tests/test_acp2_runtime_mcp_backends.py:1-22`），但 backend 仍是被替身的。
- 9 个该文件测试确实在 `targeted-pytest-unrestricted.xml` 中，全部通过（无 failure/error）。

**E20-6 前端审批测试不触达平台**

- `frontend/js/module_tests/acp2_approval.test.js:418-422`：

```js
await test("approval module owns no network call", () => {
  const bareCall = /(^|[^A-Za-z0-9_$])fetch\s*\(/;
  assert(!bareCall.test(source), "no direct fetch call in the approval module");
  assert(source.indexOf("XMLHttpRequest") === -1, ...);
});
```

- `:579-613`：加载真实 `research-approval.js`，但 `sandbox.ACPClient.research.init({ v2: (path) => ... })` 只做 fixture 读取；没有任何 `POST`/`RuntimeCommands`/approval 服务调用。
- `reports/acp2_delivery_gap_audit_20261026/evidence/node-probes.json` 的 `frontend_approval_without_platform`：无 transport/persistence 也返回 `status=approved`, `submit_result.submitted=true`, `dispatch_ref=dsp-2`, `approval_ref=apr-1`。
  → 测试和探针共同证明：前端审批是本地卡片状态机，不证明平台写入；这正是"模块内一致"而非"跨层消费"。

**E20-7 测试数量与存在性抽样**

```text
$ python3 - <<'PY'  # 解析 JUnit
targeted-pytest-unrestricted.xml : tests=168 failures=0 errors=0 skipped=0
science-runtime-pytest.xml       : tests=131 failures=0 errors=0 skipped=0
PY
```

抽样 id（均存在于 XML 且通过）：

- `tests.test_services_mcp::test_tools_list_is_the_frozen_allowlist`
- `tests.test_services_mcp::test_initialize_rejects_other_protocol_version`
- `tests.test_acp2_runtime_mcp_process::test_no_common_version_fails_and_blocks_tools`
- `tests.test_acp2_runtime_mcp_process::test_protocol_negotiation_matrix`
- `tests.test_acp2_runtime_mcp_backends::test_submit_returns_before_completion`
- `tests.test_acp2_runtime_mcp_backends::test_analyze_requires_explicit_and_is_registry_gated`
- `tests.test_acp2_runtime_science_binding::test_dag_is_bounded_and_irc_uses_existing_task_core`
- `tests.test_acp2_runtime_science_binding::test_restart_recovers_only_unfinished_steps`

`targeted` 的 class 分布：`test_services_mcp` 39、`test_acp2_runtime_mcp_process` 17、`test_acp2_runtime_mcp_backends` 9、`test_acp2_runtime_approvals` 13、`test_acp2_runtime_budget` 17、`test_acp2_api_science_routes` 18、`test_acp2_research_read_routes` 12、`test_acp_services_isolation` 23、`test_architecture_invariants` 20。

本机只读收集复核（未执行）：

```text
$ PYTHONPATH=src python3.11 -m pytest tests/test_acp2_runtime_science_binding.py \
    tests/test_acp2_runtime_mcp_backends.py --co -q
34 tests collected in 0.30s        # 25 + 9，与 XML 一致
```

RPH：

```text
$ grep -n "# tests\|# pass" reports/.../rph-tests-no-isolation.log
# tests 80
# pass 80
```

前端：`frontend-tests-unrestricted.log` 末尾 `444 passed, 0 failed`；`# acp2_approval` 段 21 个 `ok`（含 `approval module owns no network call`、`approval card uses the real ACPClient.research namespace`）。

**E20-8 没有跨语言生产者→消费者测试**

- `grep -rln "modelcontextprotocol" tests/` 无命中；`tests/` 中没有任何文件 import/spawn `rph/packages/bridge`。
- `tests/test_acp2_experience_harness.py` 覆盖的是 scenario CLI 语义（help/dry-run/exit code/redaction），不启动 RPH Bridge 或真实 MCP SDK。
- 真正发现问题的 `sdk-probe`/`node-probes` 是审计脚本产物（`reports/acp2_delivery_gap_audit_20261026/evidence/`），不是 pytest/CI 门。

### 根因链

**技术根因**

1. 两侧 fixture 自产自销：
   - 客户端 `P1_TOOL_NAMES`/`P2_OPERATION_MAP`（`rph/packages/bridge/src/index.ts:240`, `:503`）由 RPH 自己定义，测试直接用它算 18；
   - 服务端 `McpTool.ALL`/`TOOL_ALLOWLIST`（`mcp.py:211-268`）由服务端自己定义，测试直接断言 8；
   - 没有共享的、由生产者生成的 `tools/list` 快照或 JSON-RPC trace 作为测试输入。
2. 协商规则同理：服务端 `_negotiate_protocol_version`（`mcp.py:703-728`）是非标准扩展；测试按该规则构造 `protocolVersions`，因此测不到 SDK 默认客户端（`sdk-probe.json`）。
3. EvidenceReport：A 的 schema 与 RPH 的 `EvidenceReport` 类型是两套字段（`evidence.schema.json` vs `rph/test/research-loop.test.ts:95-118`）；两端测试各自造符合本端 schema 的对象，20 项结构冲突只能在真实交界处暴露（`node-probes.json`）。
4. 审批：前端模块的所有测试都在同一 JS 沙箱内注入 `ACPClient.research.v2` stub，并显式断言"no network call"，从设计上就没有平台写入路径。
5. `test_acp2_runtime_mcp_backends.py` 虽然是真 HTTP，但通过 monkeypatch 关掉能力准入与提交线程（`:55-56, :75`），使"真 HTTP"只覆盖路由/协议层，不覆盖执行层。

**设计/流程根因**

1. 计划 B 明确"contract-first + TDD for pure modules; tests-after for wiring"（`.omo/plans/acp2-parallel-plan-b-runtime.md:90`）。这天然把 wiring 的跨端验证留到"以后"，但"以后"没有独立门。
2. 各轨以"本模块契约测试通过"作为任务完成条件；没有一条验收条件要求"消费者用生产者的真实输出跑一次"。
3. CI（`.github/workflows/ci.yml:67,98-128`）分别跑 `pytest -m "not slow"`、可选前端浏览器测试、以及 `rph` 的 `pnpm test/check`（rph lock 存在时）；但没有任何 job 把 RPH/MCP SDK 与真实 Python 服务组合起来跑，因此 299 Python + 80 RPH + 444 前端全绿与真实 SDK/P2/证据失败可以长期共存。
4. 共享 fixture 不是"权威生产者生成 + 版本/digest"：B18a 虽有 `a-roundtrip.json`/`p2-minimal-form.json`，但它们是转换样例，不是"服务端真实 `tools/list`/RPH 真实 builder 的字节级快照"。

**结论**：报告"按模块契约各自建测"的判断成立；根因是**缺少由生产者在真实运行中生成、消费者真实实现消费的共享 fixture 与 CI 门**，而不是测试数量不足。

---

## 对报告的修正建议

1. **G15 措辞**：把"本轮未找到 Controller/worker 的生产构造与选择入口"改为"生产 worker 构造（`worker_bootstrap.assemble_worker:786`）与 entry 选择（`execution_chain.select_execution_entry:240`）存在，但只构造/选择单步 `FrozenWorker`，没有 DAG 的编译/构造/注入入口"。补充 `cccp_compute_dispatch` 零调用点、`plan.steps` 只用于前置校验（`worker.py:1366-1374`）两条硬证据。
2. **G15 根因**：明确区分 B18a（契约）已交、B18c（STATIC/SIMULATED）已交、**B18b execution wiring 未交**；引用计划 `:368` 的三段拆分与 `B18a-to-C18.json:7` 的自认"不需要 B18b wiring"，并指出任务 20 的 `[x]` 只按 da-preflight/静态标准判定。
3. **G18 分类**：把"未排期/未实现"改成"T-30 已排期但 `[~]` blocked（计划 C `:480`），T-29 的验收本身只要求离线状态机（`:472-478`）；harness 按 B01 规则写具名 NOT_VERIFIED 占位"。保留"只补环境变量不会验证真实循环"的判断。
4. **G18 补证**：增加 `evidence_kind` 不一致——外层 receipt 是 `live`、内层 `control-report.json` 是 `offline_fixture`、`live_model_calls=0`。这是一个可以立即修的标签缺陷，不应留到环境阶段。
5. **G19 加一张 SHA 角色表**（候选/发布/落地/HEAD/证据各是谁），并注明：
   - `abec2bc` 已无任何 ref 包含（不可从分支复现），`h1-r2.json` untracked；
   - `integration/candidate.json:5` 还有 `77ac1de`；
   - `release-candidate.json` 的 `source_candidate` 同时含 `revision=6ec01ec`、`base=abec2bc`、`landed_head=4937907`；
   - 最终 summary 顶层 `f6dd3c9` 与嵌套 `remediation.landed_commit=ec4a0b3` 并存，r3 证据是 `ec4a0b3`/tree `5815b19d`。
6. **G19 措辞**：说明 `h7-index`/`summary` 的 `git diff` 只是元数据刷新（digest/harness_revision/h1→h1-r2），没有 PASS/NOT_VERIFIED 变化；避免"未提交修改"被误读成结果被改写。
7. **G20 补两个具体矛盾**：客户端自算 18 工具（`bridge-contract.test.ts:183-200`）vs 服务端 8 工具（`test_services_mcp.py:279-284`）；扩展版本服务端 1.1.0（`mcp.py:1063`）vs RPH 1.0.0（`bridge/src/index.ts:398`）。这两个例子比"模块各自建测"更能说明跨端门缺失。
8. **G20 测试计数限定**：在 G20 正文注明 168 来自 `targeted-pytest-unrestricted.xml`（普通 log 截断/含 `EEEE`），80 来自 `rph-tests-no-isolation.log`（普通 runner 只显示 5 个文件级结果），444 来自 `frontend-tests-unrestricted.log`。
9. **总体建议**：保留报告"不要把测试数量当交付覆盖率"的结论；新增一条关闭条件——"任一跨语言边界必须有生产者真实输出 → 消费者真实实现的 CI 门；`sdk-probe`/`node-probes` 这类探针要么进 pytest/CI，要么在其结果上显式标注 NOT_VERIFIED，不能只作为一次性审计产物"。

## 附：本次只读复核用到的关键命令

```bash
git rev-parse HEAD
git status --short
git for-each-ref --contains <sha> --format='%(refname)'
git merge-base --is-ancestor <sha> HEAD; echo $?
git cat-file -t <sha>
git log --follow --oneline -- <path>
git blame -L 203,223 --date=short -- scripts/acceptance/acp2_experience_scenario_research_loop_live.py
grep -rn "BoundedStepDagExecutor" src scripts tests --include=*.py
grep -rn "cccp_compute_dispatch" src scripts tests --include=*.py
grep -rn "modelcontextprotocol" tests/
PYTHONPATH=src python3.11 -m pytest tests/test_acp2_runtime_science_binding.py \
  tests/test_acp2_runtime_mcp_backends.py --co -q
python3 - <<'PY'  # JUnit 统计（168/131，0 fail/0 error）
PY
```

未执行：全量 pytest、任何 QC/LSF 提交、模型调用、服务重启、git commit。
