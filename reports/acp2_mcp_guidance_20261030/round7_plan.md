# Round-7 修复计划：外部 MCP Agent 第二轮反馈（8 类）

- 基线：`main` = 合并 PR #54 `1ee34970`；工作区有**未提交的 guidance 改动**（`src/acp/api/guidance_pack.py`、`src/acp/api/workflow_contracts.py`、`src/acp/catalog.py` + 两个测试版本 pin）。本计划**不重写**这些改动，只在其上做增量。
- 本轮为**只读规划**：未改代码、未跑真实 QC、未重启服务。
- 术语：P0 = 会静默产生错误科学语义或让整条工作流不可用、必须在下一轮真实计算前止损；P1 = 契约/闭环缺口，本批修复；P2 = 体验与可观测性。

## 总览

外部的 8 类反馈中，**#1（singlepoint 冻结 release 永久 stale）、#2（batch_roles 静默吞掉单点级别）、#3（双轨道基组被 ORCA 后者覆盖）** 是本轮 P0：三者都满足「作业正常结束 + 科学语义错误/工作流不可用」，且都不需要放宽任何护栏就能修。#4（热化学不可读）与 #6（preflight 假阳性）、#7d（DLPNO 能量标签）为 P1；#5（PES 自动发布死路）本轮只做**显式化**（typed error + 指南写明替代路径），不新增自动授权。#7 的其余条目为 P2。本轮范围 = A—E 五个互不重叠的写批；`#8` 是用户自认、`#9` 明确不改。**NOT_VERIFIED**：#3 中第二个基组 token（`ma-def2-SVP`）的确切来源、#7a 约束文本进入平台的具体字段、#1 中 `57054d2…` 冻结版本的构建时间——都需要外部 agent 的原始 `.inp`/`.out`/`job.json`/`live_status` 才能定案（见末节）。热化学产物的 **kind 命名**（复用 `thermo_report` 还是新增 `thermochemistry`）与 **DLPNO 应报告哪个能量** 是需要用户裁决的科学/契约决定。

---

## 逐条计划

### #1 singlepoint 全部 0.4s 失败：`local release is stale`

**复现性/根因（VERIFIED，代码可确认）**
- 报错文本唯一来源：`src/acp/calculations/attempt_freeze.py:190-200`（`ReleaseIdentity.assert_current`）。全部 freeze builder 在入口调用它：同文件 `:900 / :1033 / :1098 / :1170 / :1280` → 只要传进来的 `LocalRelease` 与**当前**源码树 digest 不一致就 fail-closed。
- 只有 `DEFAULT_ENVELOPE_WORKFLOWS` 的普通提交会走冻结：`src/acp_services/runtime/execution_chain.py:279` = `{"singlepoint", "xtb_optimize"} | RECIPE_WORKFLOWS`；`src/acp/scheduler/runner.py:1339-1373` 只对这些工作流建 frozen group。**BatchOptimize 不在集合里**，走 legacy 路径，所以同一服务里正常——与 agent 观察一致。
- release 对象被 **runner 进程内缓存**：`src/acp/scheduler/runner.py:1363-1371`（`self._default_release` 首次构建后不再刷新；远程同构 `src/acp/scheduler/remote/runner.py:1408-1423`）。长驻 `acp.service` 在源码树发生变化后（合并 PR、编辑未提交文件）仍复用旧 `LocalRelease`，下一次 singlepoint 提交必在 `assert_current()` 处失败，且**每次重试都同错**——agent 的 4 次重试现象完全吻合。
- 错误信息只给「build a new release」（管理员动作），系统里**没有** agent 可用的重建入口；`build_local_release` 本身是幂等的（`releases/<id>` 已存在则校验复用，不一致则 typed refusal，见 `attempt_freeze.py:501-600`）。
- **NOT_VERIFIED**：`57054d2…` 这个冻结版本的实际构建时刻/是否来自更早的 commit；以及服务是否在源码变更后未重启（需要服务 uptime + `releases/` 目录清单）。
- 目录约定差异（`<job_dir>/sp.out` vs `WORK/05_SP/sp.out`）：属于**布局/文档**问题，不是本条的失败原因（见 #7f）。

**修复方案（最小、不放宽护栏）**
1. `src/acp/scheduler/runner.py::_ensure_default_frozen_attempt`（与 `remote/runner.py` 对应段）：在 `build_freeze_spec(...)` 外捕获 `StaleReleaseError`，**清空** `self._default_release`，调用 `build_local_release()` 重新从活源码树物化/校验一个新的不可变 release，然后**只重试一次** freeze。绝不修改旧 release、绝不跳过 digest、绝不用 `ACP_ALLOW_UNVERSIONED`（`attempt_freeze.py:487-498`）兜底。
2. `attempt_freeze.py` 内新增显式接缝（如 `refresh_local_release(project_root, run_root)`），让 manager 侧也能在同一 attempt 之外失效缓存；不改 `ReleaseIdentity` 的哈希语义。
3. 错误信息加结构化 `retry_hint`（`params`）：说明「下一次提交将自动重建」与「已有 attempt 不受影响」——**在 1 落地后**才可这样写。
4. 不在本批改 on-disk 布局；singlepoint 的产物路径以 manifest 为准（见 #4/#7f）。

**测试**
- 既有：`tests/test_acp_attempt_freeze.py`（release identity/stale 已覆盖 `assert_current`）、`tests/test_services_chain_switch.py`（envelope/legacy 切换）、`tests/test_acp2_*freeze*`。
- 新增：`tests/test_acp_release_refresh.py`
  1. 构建 release → 改一个源文件（fixture 内临时树）→ 新提交：**成功**，且 `releases/` 下出现**新** release id，旧目录字节未变。
  2. 改源文件后**同一 attempt** 重放：行为不变（沿用已记录 choice，不重冻结）。
  3. 人为破坏 release 目录成员（篡改一个文件）：仍然 typed refusal（不得被 refresh 掩盖）。
  4. `ACP_ALLOW_UNVERSIONED` 不在默认路径生效（断言未设置时 release 为 versioned 且 digest 非空）。

**优先级** P0（工作流整体不可用）。**风险/回滚**：只影响「新 attempt 的冻结时点选哪份源码」；已冻结 attempt 继续执行自己的 release。回滚 = 撤掉 retry 包装，行为回到现状。**依赖**：无（独立批次 A）。

---

### #2 batch_roles 静默吞掉单点级别

**复现性/根因（VERIFIED 两处叠加）**
1. **`batch_roles` 存在时，顶层扁平镜像只在「对应 role 存在」时才做冲突检查**：`src/acp/api/submission_contract.py:318-336`（`_WORKBENCH_FLAT_MIRRORS` 含 `single_point_method/basis`，映射到 `int.sp_*`，见 `:380-400`），`：755-800` 的冲突循环 `role_value = roles.get(role_id)` → `roles` 里只有 `ts` 时 `None` → `continue`。于是顶层 `single_point_method` 既不被拒绝（在 mirror 白名单里）也不被检查，**完全惰性**。这解释了第 1 次：`effective_config.json.single_point_method == ""`。
2. **单点级别在解析时只有全局字段，且只从 `int` role 取**：`src/acp/calculations/batch/options.py:334-370`（`_build_opts_from_batch_roles` 只读 `int_raw.sp_method/sp_basis`，`ts_raw.sp_*` 被丢弃），`BatchMethodOptions.single_point_method/basis` 是**全局单值**（`:470-472` 附近），`for_step(StepKind.SINGLEPOINT, is_transition_state)` 同样全局取值（`:540-560`），`step_options()` 的 SINGLEPOINT 分支（`:640-650`）用的是 `getattr(self, f"{prefix}sp_method")`——这两个属性在 dataclass 上**不存在**，永远回落到全局值。这解释了第 2 次：int 的 SP 生效（因为写进了 int role），ts 的 SP 仍是角色方法（r2SCAN-3c）。
3. 第 3 次纯平铺能对，是因为 `from_method_dict` 在无 `batch_roles` 时走 `migrate_legacy_method_dict`（`:302-320`），把扁平字段完整迁移成两个 role——旧路径反而信息完整。

**修复方案**
1. **把 SP 级别变成 per-role**：在 `RoleMethodConfig`/`_build_role_config_from_opts`/`_build_opts_from_batch_roles` 增加 `sp_method`/`sp_basis` 的 role 解析（`int`、`ts` 各一份），dataclass 增加 `minimum_sp_method/basis`、`transition_state_sp_method/basis`（保留全局 `single_point_method/basis` 作为两 role 的兜底，兼容旧 payload）。`for_step(SINGLEPOINT, is_ts)`、`resolve_for_step`、`step_options` 的 SINGLEPOINT 分支统一走 per-role 解析；`cache_key` 同步纳入新字段（`options.py:_role_config_to_dict` / `cache_key`）。
2. **镜像规则收紧**（不放宽，只拒绝）：`submission_contract.py` 在 `batch_roles` 存在且某顶层 mirror 非默认、但其目标 role **缺失**时，抛 `SubmissionContractError(rule="mirror_without_role")`，消息直接给出两种正确写法（写进 `batch_roles.int.sp_method`，或删掉顶层字段）。**不**把扁平值自动编译进缺失 role（那会造出第二条解析路径）。
3. `src/acp/calculations/batch/effective_config.py::build_batch_effective_config` 输出 **per-role** requested/effective（含 sp_method/sp_basis 与最终 `!` 行），让 `effective_config.json` 能自证；`acp` 侧 `submission_validation`（见 #6）复用同一投影，不复制规则。
4. 指南侧（`workflow_contracts.py` 的 batch_roles 说明，属 keep 基线）本轮只补一句「role 内写 `sp_method/sp_basis`；顶层仅 Workbench 镜像，缺 role 会被拒」，由批次 E 以增量 patch 落地，不重写既有未提交内容。

**测试**
- 既有：Batch method options 单测（`tests/test_acp_batch_*` / `test_protocol_levels.py`）、`tests/test_acp_submission_contract*`（若有同名，按 `_WORKBENCH_FLAT_MIRRORS` grep 定位）。
- 新增 `tests/test_acp2_batch_roles_sp.py`：四组 payload（纯平铺 / `ts` role + 顶层 SP / 两 role 各自 `sp_method` / 顶层与 role 冲突）→ 断言 `BatchMethodOptions` 与 **生成的两条 `!` 行**（int 用 DLPNO、ts 用 r2SCAN-3c 或反之）；断言第 2 组现在被 `mirror_without_role` 拒绝；断言 `effective_config.json` 的 per-role 字段。
- 新增 render 级回归：int/ts 各跑一次 `step_options`，防止 `getattr` 回落再次出现。

**优先级** P0。**风险/回滚**：新增字段是加法；收紧镜像会让**当前会被静默忽略**的 payload 变成 422——这是意图内的行为变更，需在 PR 描述里列为 breaking-ish。回滚 = 回退 per-role 解析与 mirror 规则两个 commit（互相独立）。**依赖**：与 #3、#6 共享「Batch effective-config 投影」，由批次 B 定义、批次 D 消费。

---

### #3 自定义基组被 `ma-def2-SVP` 顶掉

**复现性/根因（渲染层 VERIFIED；来源 NOT_VERIFIED）**
- 渲染器把 `method`、`basis`、`route_extras` 拼进同一条 `!` 行，**没有任何「轨道基组只能有一个」的不变量**：`src/cccp/qc/interfaces/orca.py:1383`（`_build_input_blocks`，`_method/_basis/_route_extras` 汇合处）与 `:1539-1590`（aux 提取逻辑，只识别 `/<J|C>` 模式，不识别第二个轨道基组）。`src/cccp/qc/interfaces/route_render.py:106-120` 的 `RouteKeyword` 只治理枚举参数，不管基组 token。
- `src/cccp/qc/method_meta.py:47-59` 的 aux 表本身是**对的**（`def2-TZVP → aux_j=def2/J, aux_c=def2-TZVP/C`），且 `AUX_J_BASIS_DEFAULT="AutoAux"`（`:107-108`）。因此 `ma-def2-SVP` **不是**本仓库的 aux 默认；它是作为**第二个轨道基组**（很可能是 profile/模板的默认 `optimization_basis`）与用户方法串里的 `def2-TZVP` 一起被发出。
- ORCA 语义：同一 `!` 行多个轨道基组时**后者生效**。158 vs 246 函数量、性质正常、零警告——与描述一致。
- **NOT_VERIFIED**：`ma-def2-SVP` 的确切注入点（profile 默认？job 模板？API 默认？）。仓库内 `ma-def2-SVP` 只出现在 `method_meta` 表、docs、tests 与 reports（`grep -rn "ma-def2-SVP" --exclude-dir=.git .`），**不是**代码默认值 → 需要 agent 的 `job.json`/`optimize.inp` 与 `effective_config.json` 才能定位。
- **可独立验证的核心**：`method` 字符串自带基组（`"wB97X-D4 def2-TZVP"`）时，平台仍会追加独立的 `basis` 字段 → 双基组。这是渲染层契约缺陷，与第二个 token 的来源无关。

**修复方案**
1. 在 `cccp/qc/interfaces/route_render.py` 增加**唯一权威**的 route token 分类器（orbital basis / aux basis / keyword），基组名来自 `method_meta.BASIS_CATALOG`（含 `ma-*`、`def2-*`、`cc-*`），aux 用现有 `_aux_basis_pattern`。
2. `_build_input_blocks`（`orca.py:1383`）调用该分类器做**构造期不变量**：有效 route 上**恰好一个**轨道基组 token。规则：
   - method 串已含基组且 `basis` 为空 → 用 method 内基组（不重复发）；
   - `basis` 与之相同 → 去重；
   - `basis` 与之不同 → **typed error**（`rule=basis_conflict`，消息列出两个 token 与实际生效值），由 #6 的预检在提交前报出。
3. aux 字段只接受 `def2/J`、`def2-*/C`、`AutoAux` 形态；把普通轨道基组塞进 `aux_j_basis/aux_c_basis` 在 `resolved_spec.clamp_calculation_fields` 处拒绝（当前 `resolved_spec.py` 已有 clamp 语义，需确认是否覆盖用户显式 aux，`NOT_VERIFIED`）。
4. `effective_config.json`/`provenance` 记录 **effective orbital basis**（不只 requested），便于事后对账。
5. 不改变默认：没有用户基组时行为与今天完全一致（默认 profile 仍生效）。

**测试**
- 新增 `tests/test_cccp_orca_route_basis.py`：`method="wB97X-D4"` + `basis="def2-TZVP"` → 单基组；`method="wB97X-D4 def2-TZVP"` + `basis=""` → 单基组；`method="wB97X-D4 def2-TZVP"` + `basis="ma-def2-SVP"` → `ValueError/typed error`；composite（`r2SCAN-3c`）路径不变（`ri_support=="composite"` 分支清空 aux，见 `orca.py:1539-1580`）。
- 新增 fixture 断言：对渲染文本 `startswith("!")` 的行做 base-token 计数 == 1；`def2/J` 只作为 aux 出现。
- 既有：`tests/test_acp2_*orca*` / `tests/fixtures/qc/orca61/*`（README 里有真实 route 行可比对）。

**优先级** P0（静默错误科学级别）。**风险/回滚**：拒绝此前被接受但会覆盖基组的 payload；legacy 作业只读不受影响。回滚 = 移除构造期不变量（保留分类器与测试）。**依赖**：与 #6 共享「有效 route 预测」；批次 C 提供纯函数，批次 D 在 preflight 复用。

---

### #4 MCP 读不到热化学（ZPE/H/G/S）

**复现性/根因（VERIFIED）**
- `acp_status` 的 `artifacts` 由 `describe_job_artifacts` 从 manifest 产品投影：`src/acp/api/artifact_descriptors.py:52-108`（只读 `RESULT/result_manifest.json`，每项给 `resource_id/relative_path/kind/label`）；字段出现在 `src/acp_services/entrypoints/status_projection.py:250-252`。
- `ProductKind.THERMO_REPORT` 已存在（`src/acp/storage/manifest.py:33`），但**全仓库没有任何写入点**（`grep thermochemistry/THERMO_REPORT` 只有 enum + `acp/results/thermochemistry.py`）。`thermo.json` 由 simple 工作流写出（`src/acp/workflows/simple.py:234-249`），`build_thermo_report`（`src/acp/results/thermochemistry.py:18-43`）是**视图投影**，不进 manifest → MCP 看不到。
- `frequency` 产品只有 `normal_modes.json`，无热化学数值；agent 只能去共享文件系统 grep `Final Gibbs free energy`。

**修复方案（增量，不改 ProductKind 枚举）**
1. 在频率/单点发布路径登记产品：`Product(id="thermochemistry", kind=ProductKind.THERMO_REPORT, path="thermochemistry.json", metadata={...})`，内容用 `build_thermo_report` 同一投影 + 明确单位（Hartree）与来源（`freq.out` 的 `Final Gibbs free energy` / ZPE / H / S / 电子能）。
2. 写入点：simple 工作流（`src/acp/workflows/simple.py` 的发布段）与 Batch item 发布（`src/acp/calculations/batch/engine.py` 的 singlepoint/freq 结果落盘处，05_SP/FREQ 同级）。**只在 `thermo.json` 存在时登记**——没有频率就没有热化学，宁缺不假零。
3. `effective/requested`：产品 metadata 里带 method/basis/temperature/pressure/scale_factor，便于判断 DLPNO 与 (T) 修正是否同源。
4. MCP 读取路径无需改协议：`acp_prepare_artifact` + `acp_read_artifact` 读 JSON 即可；若用户确认要 `kind: "thermochemistry"` 作为**别名**，只在 `artifact_descriptors` 增加 `kind` 映射（不改 manifest enum）。**这是待裁决项（见末节 f）**。
5. 指南（批次 E 增量）写明「ΔG 走 thermochemistry 产品，不要 grep `freq.out`」。

**测试**
- 既有：`tests/test_acp_storage_manifest*`、`tests/test_acp_results*`（按文件名 grep `manifest`/`thermochemistry` 定位）。
- 新增 `tests/test_acp2_thermo_artifact.py`：频率作业 fixture → manifest 含 thermo_report 产品、MCP `describe_job_artifacts` 出现 `kind=="thermo_report"`、`acp_read_artifact` 返回的 `gibbs_hartree` 与 fixture 的 ORCA 行一致（容差 1e-6）；无 `thermo.json` 时**不**出现该产品。

**优先级** P1。**风险/回滚**：纯增量产品，无读取方破坏。回滚 = 移除注册点。**依赖**：与 #2/#6 的 effective 投影无关；依赖批次 D 独占 `executor.py`/`engine.py` 的发布段。

---

### #5 推荐的 TS 链路（PES → publish → Batch）在纯 MCP 会话是死路

**复现性/根因（VERIFIED）**
- `acp_publish_pes_review` 是研究/协商工具，schema 必填 `authorization_id`（`src/acp_services/entrypoints/mcp.py:1264-1295`），读写分类 `selection_publish`（`:416-440`）。
- round-6 已把发布固定为 `selector="agent"` 并新增**管理员**铸造入口 `POST /api/v1/selection-authorizations`（见 `reports/acp2_mcp_guidance_20261030/round6_pr_description.md`）：纯 MCP 会话里没有 operator，永远拿不到授权 → 403，且指南没有前置声明。
- inline item + `role:"ts"` 支路可用，但指南把它写在「注意」里，像兜底。

**修复方案（显式化，不新增授权）**
1. `acp_get_pes_review` 回包增加 `publishable: false` + `reason: "authorization_required"` + `next_actions`（`operator_mints_authorization` / `inline_role_ts`），让 agent **试之前**就知道。
2. `acp_publish_pes_review` 在无/未知 `authorization_id` 时返回既有 403 语义的 typed 包，`params` 带同样的 `next_actions`；**不**自动铸造、**不**把 `selector` 放宽成 manual（round-6 的护栏保持）。
3. `acp_capabilities`（或 guidance 的能力段）明确：PES 自动发布 = **需要管理员有界授权**；纯 agent 会话的推荐 TS 路径 = `PESsearch` 选点后 `BatchOptimize` inline item + `role:"ts"`（写成**支持路径**，不是脚注）。
4. 指南同步（增量 patch，`guidance_pack.py`，keep 基线上加）。

**测试**
- 新增 MCP 合同测试：纯会话 `get_pes_review` 返回 `publishable=false`；`publish` 无授权 → 403 + `next_actions`；有授权（probe）仍能发布（round-6 用例保持绿灯）。
- 指南快照测试（`tests/test_acp_guidance_pack.py`，注意该文件有未提交 pin，保持不动，只加断言）。

**优先级** P1（闭环可用性）。**风险/回滚**：回包新增字段是加法；不触授权策略。回滚 = 移除 `publishable/next_actions`。**依赖**：与批次 E 的 guidance 增量同 owner；与 #6 无关。

---

### #6 preflight 形同虚设（只做 schema 校验）

**复现性/根因（VERIFIED）**
- `_preflight_projection`（`src/acp/api/v1_routes.py:9913-10080`）确实调用 `validate_workflow_submission`（契约层）与 `analysis_for`；但 `src/acp/calculations/submission_validation.py:440-447` 的 `_ANALYZERS` **只有** `PESsearch/scan/irc/tsmode` → `BatchOptimize`、`singlepoint` 的 `submission_analysis` 恒为 `null`。
- 契约层看不到语义：`batch_roles` 缺 role 的镜像（#2）、双基组（#3）都不在 `submission_contract.py` 的检查范围内。
- 指南承诺「读取错误与生效配置」，但 preflight 从不返回 effective 配置（只有 `submission_analysis.requested`）。

**修复方案**
1. 新增 `analyze_batch_submission`（纯函数、零 I/O）：调用 `BatchMethodOptions.from_method_dict` 这一**唯一权威**，产出 per-role requested/effective + 预测的 int/ts `!` 行（复用批次 C 的纯分类器），并对 agent 的三种写法给出 `advisories`。
2. 新增 `analyze_singlepoint_submission`：method/basis/aux 冲突、`AUTOAUX` 与自定义基组的相互作用、以及**只读**的 release 陈旧检查（比较 `local_release_identity` 与最新 `releases/*` manifest 的 digest，只读，不构建）。
3. 在既有 `submission_analysis` 结构内返回 effective（键集不变，不新增顶层响应字段）；`unverified_checks` 照旧列出无法静态判定的项（如远端可用性）。
4. 预检保持零副作用：不创建 release、不写快照、不建任务、不预留资源（现有契约，测试必须守护）。

**测试**
- 新增 `tests/test_acp2_preflight_batch_semantics.py`：以 agent 的 #2/#3 payload 为 fixture，断言 `feasible=false` + `reasons[*].rule`（`mirror_without_role` / `basis_conflict`）；修复后同一 payload 在 submit 得到**相同** `code/field/rule`（parity）。
- 零副作用测试：preflight 调用前后 `run_root` 文件树/mtime 不变、无 job 行、无 release 目录新增。
- 既有：`tests/test_acp_api_preflight*`、`tests/test_acp2_preflight*`。

**优先级** P1。**风险/回滚**：预检新增拒绝会暴露既有坏 payload（意图内）；若发现误报可单独回退 analyzer。**依赖**：**强依赖 #2 的 options 解析与 #3 的 route 分类器**；批次 D 必须等 B/C 的纯函数落地后再接。

---

### #7 次级坑

**7a xTB 约束语法被忽略但作业正常结束（P1，「正常结束 + 结果是错的」）**
- 根因（VERIFIED 部分）：类型化约束渲染本身是对的（`src/cccp/qc/interfaces/xtb_scan.py:31-69`：`distance: i, j, value` + 块级 `force constant=`），但**用户原始 `.inp` 文本路径**（`path_inp_text`/`extra_args`，见 `src/acp/workflows/xtb_path.py:210`、`src/acp/calculations/legacy_adapters.py:1133-1180`）不校验 `$constrain` 行；xTB 对参数不足的行只告警并继续，任务按 exit code 判 COMPLETED。
- 修复：① 提交时解析 `$constrain` 行（`distance/angle/dihedral` 的原子数、目标值数值、第 4 token 必须是数值或改用 `force constant=` 行），非法 → `input_invalid`；② 运行后扫描 stdout/stderr 的 `not enough arguments to constrain` / `$constrain` 缺失回显 → typed 失败 `constraints_not_honored`，不产出 COMPLETED。**NOT_VERIFIED**：该 payload 究竟走 `XtbConstrainedOpt`（typed）还是 `XtbPathSearch` 的 `path_inp_text`（raw）——需 agent 的 job.json。

**7b idempotency_key 拒绝括号（P2）**
- 根因：`src/acp_services/entrypoints/application_params.py:105` `^[A-Za-z0-9._:-]{8,128}$`。
- 修复：字符集扩到 `[A-Za-z0-9._:()+-]`；若该 key 被用作存储/文件名（需 grep 使用点），则**保持 wire 宽松、存储侧哈希**，不要放宽路径安全。测试：覆盖 `...:sp-ccsd(t)-ts:...` 被接受且重放幂等。

**7c `/usr/bin/orca` 是 GNOME 屏幕阅读器（P2）**
- 根因：`src/cccp/software.py:229-252`（`resolve_executable`）只按 PATH 找名字，任何 `orca` 都接受。
- 修复：对 `name=="orca"` 做探针（`--version` 输出含 `Program Version`/`ORCA`，且不是 `pyatspi`/`Orca Screen Reader`）；`acp doctor` 与首次执行前给出可操作错误。**不**改解析优先级、不引入新配置项。

**7d DLPNO 三个能量混淆（P1，科学回执）**
- 根因：解析器/回执没有把 `Electronic energy`、`E(0)`、`E(TOT)`、带 `(T)` 的最终值区分命名；`E(0)` 是 SCF 参考，`E(TOT)` 是 CCSD，`E(TOT)+E(T)` 才是 CCSD(T)。
- 修复：`src/cccp/qc/interfaces/orca.py` 的能量提取返回命名结构（`scf_reference/e0/e_tot/e_ccsd_t/final`）+ `energy_label`；manifest/provenance 与指南统一；**绝不**把 `E(0)` 标成 CCSD(T)。**待用户裁决**：对外"该分子能量"默认展示哪个字段（见末节 a）。

**7e acp_cancel 回包 ~50KB（P2）**
- 根因：`src/acp_services/entrypoints/mcp.py:3328-3350` 把 `_ControllerCancel` 返回的整条 job 记录原样透出（`src/acp/services_mcp_bootstrap.py:301-312` → `controller_client.py:636-660` 的 `POST /jobs/{id}/cancel` 全量回包）。
- 修复：`_tool_cancel` 投影为有界摘要（`reference/state/already_terminal/cancel_requested/attempt/revision`），保留 `detail="full"` 显式取全量；两次 `already_terminal: true` 的现有语义保留。

**7f 目录布局不统一（P2，文档+manifest 解决）**
- 不改磁盘布局（历史读取方 risk）；以 manifest 产品路径为唯一权威（`describe_job_artifacts` 已按 `RESULT/` 相对路径发布），指南给出两套布局的对照表。singlepoint 若尚无 `sp.out` 产品登记，在本批补一个（与 #4 同批，`RESULT/` 相对路径）。

**#8** 用户自认（覆盖几何、Kabsch、未先小规模验证）：平台侧**不改**；仅在指南加「先小体系验证再放量」的一句话提醒。
**#9** 明确不用改：保持现状，回归测试守护（`effective_config` requested/effective、`frequency.ts_validation`、`normal_modes.json` 位移、`acp_list_jobs`、幂等键防重复计费）。

---

## 批次拆分（5 个互不重叠的写批；文件不交叠）

> **keep 基线（所有批次都不得重写）**：`src/acp/api/guidance_pack.py`、`src/acp/api/workflow_contracts.py`、`src/acp/catalog.py`、`tests/test_acp_api_guidance_routes.py`、`tests/test_acp_guidance_pack.py` 的当前未提交改动。批次 E 对 guidance 的修改必须是**其上的增量 patch**，且 E 是这几个文件的**唯一** owner。

| 批次 | 范围 | 文件（写） | 交付 |
|---|---|---|---|
| **A — 冻结/release** | #1 | `src/acp/scheduler/runner.py`、`src/acp/scheduler/remote/runner.py`、`src/acp/calculations/attempt_freeze.py`（错误面 + refresh 接缝）、`tests/test_acp_release_refresh.py`(+既有 freeze/chain 测试) | 源码变更后 singlepoint 自动重建 release 并冻结；digest 校验不放松 |
| **B — Batch 语义** | #2 | `src/acp/calculations/batch/options.py`、`src/acp/calculations/batch/effective_config.py`、`src/acp/api/submission_contract.py`、`tests/test_acp2_batch_roles_sp.py`(+既有 batch/contract 测试) | per-role SP；`mirror_without_role` 拒绝；effective per-role |
| **C — QC 接口** | #3 + #7a/#7c/#7d | `src/cccp/qc/interfaces/orca.py`、`src/cccp/qc/interfaces/route_render.py`、`src/cccp/qc/interfaces/xtb_scan.py`(+`xtb.py` 校验/回执)、`src/cccp/qc/method_meta.py`(只加分类 helper)、`src/cccp/qc/resolved_spec.py`、`src/cccp/software.py`、`src/cccp/calculation/tasks/xtb_constrained_opt.py`、新测试 | 单轨道基组不变量；约束失败即失败；orca 探针；能量命名 |
| **D — 结果/预检** | #4 + #6（+ #7f 产品登记） | `src/acp/workflows/simple.py`、`src/acp/calculations/batch/engine.py`(仅发布段)、`src/acp/results/thermochemistry.py`、`src/acp/calculations/submission_validation.py`、`src/acp/api/v1_routes.py`(仅 preflight 段)、新测试 | thermochemistry 产品；Batch/singlepoint 语义预检；零副作用 |
| **E — MCP/指南/小刺** | #5 + #7b + #7e + guidance 增量 | `src/acp_services/entrypoints/mcp.py`、`src/acp_services/entrypoints/application_params.py`、`src/acp/api/guidance_pack.py`(keep 基线上增量)、配套测试 | PES 死路显式化；cancel 有界回包；幂等键括号 |

顺序：**A 与 C 可并行**；B 先于 D（D 消费 B/C 的纯函数）；E 最后（指南要引用 B/C/D 的最终语义）。`batch/engine.py` 只归 D，`v1_routes.py` 只归 D。

---

## 验收清单（验收 agent 必跑）

**命令**
```bash
# 定向
pytest tests/test_acp_release_refresh.py tests/test_acp_attempt_freeze.py tests/test_services_chain_switch.py -q
pytest tests/test_acp2_batch_roles_sp.py tests/test_acp_submission_contract.py -q
pytest tests/test_cccp_orca_route_basis.py tests/test_cccp_xtb_constraints.py -q
pytest tests/test_acp2_thermo_artifact.py tests/test_acp2_preflight_batch_semantics.py -q
pytest tests/test_acp2_mcp_sdk_contract.py -m contract -q
# 架构/隔离/门
pytest tests/test_architecture_invariants.py tests/test_acp_services_isolation.py tests/test_cccp_isolation.py tests/test_services_injection_contracts.py -q
python scripts/verify_cccp_isolation.py && python scripts/check_grep_gates.py
ruff check src tests && ruff format --check src tests
# 全量本地
pytest tests/ -m "not slow" -q
```
**期望**：全绿；新增测试在修复前**失败**、修复后通过（先提交"红"证据）；`tests/test_acp2_mcp_sdk_contract.py -m contract` 计数非空、无新 skip。

**科学边界（不得放宽）**
1. **#1**：release digest 校验仍然每次执行；stale release 不会被 adopt/篡改；新 release 目录 id 变化；人为破坏 release 成员仍 typed refusal；`ACP_ALLOW_UNVERSIONED=1` 不在默认路径。
2. **#3**：无用户基组时默认行为**逐字节不变**（对比 fixture 的 `!` 行）；`ma-def2-SVP` 只在显式请求时出现；任何"静默替换基组"的路径必须改为显式拒绝。
3. **#6**：preflight 调用前后 run_root 无新文件/job/release/预留；静态错误的 `code/field/rule` 在 preflight 与 submit 一致。
4. **#4**：thermochemistry 产品只在 `thermo.json` 存在时登记；数值与 ORCA `Final Gibbs free energy` 对得上；不写假零。
5. **#2**：生成的 int/ts `!` 行与请求的 role 一致；`effective_config.json` 的 requested/effective 可区分。
6. **架构**：`src/acp_services/**` 零 `acp` import（含懒加载/TYPE_CHECKING/字符串反射）；MCP 投影仍走 Controller/现有 store，**不新增第二状态库**；cancel/PES 改动不写新 store。
7. **不重启服务、不跑真实 QC、不提交/推送**；证据写 `reports/acp2_mcp_guidance_20261030/round7_evidence/`。

---

## 需要用户确认 / 样本

**需要外部 agent 的原始材料（只读分析用）**
1. 4 个失败 singlepoint 作业的 `job.json`、`live_status`、`<job_dir>/` 列表、`releases/` 目录清单（确认 #1 的冻结时点与服务 uptime）。
2. 3 次 batch_roles 尝试的完整 payload + 生成 `optimize.inp`/`sp.inp` + `effective_config.json`（#2）。
3. `wB97X-D4 def2-TZVP` 作业的 `job.json`、`effective_config.json`、`optimize.inp`（#3：定位 `ma-def2-SVP` 来源）。
4. xTB 约束作业的 `job.json` + `xtb.inp`/`xtb.out`/`xtb.err`（#7a：确认走的是 `path_inp_text` 还是 typed 约束）。
5. 取消两次的 `acp_cancel` 原始回包（#7e 体积基线）。

**需要用户裁决的科学/契约决定**
- (a) DLPNO 对外「分子能量」默认报告哪个：`E(TOT)`、`E(TOT)+E(T)`、还是 `FINAL SINGLE POINT ENERGY`？是否强制把 `E(0)` 标注为 SCF 参考、禁止单独作为结果？
- (b) 非复合泛函 + 自定义基组做优化：接受平台自动补 aux（当前行为，存在覆盖风险）／拒绝并要求显式 aux／只在 `ri_support=="user"` 时补？**#3 的修复方向取决于此**。
- (c) 顶层扁平镜像缺 role：拒绝（本计划建议）还是自动编译进该 role？
- (d) singlepoint stale：允许自动重建（本计划建议，仅重建不改校验）还是要求管理员显式操作？
- (e) per-role SP 是否本轮进 schema（#2 的建议）——若认为破坏面太大，可退化为「ts role 的 sp_method 生效 + 顶层镜像缺 role 拒绝」两步走。
- (f) 热化学产物 kind：复用 `thermo_report`（不改 enum）还是新增 `thermochemistry`（manifest schema 变更，需版本化）？

**NOT_VERIFIED 汇总**：#3 第二个基组的注入点、#7a 的请求字段、#1 冻结版本构建时刻、`resolved_spec` 对显式 aux 的现有 clamp 覆盖范围、`/usr/bin/orca` 在本机 `acp doctor` 的实际输出。
