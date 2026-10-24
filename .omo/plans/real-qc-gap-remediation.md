# real-qc-gap-remediation - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**Who this is for and what changes for them:** 面向用真实量子化学软件跑计算的研究人员，以及依赖平台结果做展示和归档的下游功能。今天"扫描、找过渡态、核磁预测"三类任务要么直接报错、要么悄悄给出不可信的"已完成"；这次修复后，它们在真实软件环境下能跑通，且"已完成"三个字恢复可信——真的收敛了才算完成。

**What you'll get:** 十四项修复与验收：扫描支持直接输入分子式（SMILES）、默认做普通扫描、需要找过渡态时才显式开启；过渡态模式搜索能读懂真实软件输出的海森矩阵文件；带溶剂的核磁预测全链路跑通；CASSCF 计算只在真正收敛时报告完成、失败不再被误收；梯度计算的超时设置不再崩溃；每份新计算的能量结果都落成可读取的正式文件；操作手册与软件实际行为重新对齐；测试体系改为与生产同一套软件探测逻辑，并固化真实格式样本防止回归。

**Why this approach:** 每个缺陷都先被逐行核实过（不是照单转抄），修复顺序遵循"先建可靠的回归证据，再修科学正确性，最后对齐文档并整体验收"——因为此前七千多项模拟测试全绿却掩盖了真实环境的三个崩溃，说明必须先让真实格式成为测试契约。所有关键抉择（扫描的过渡态开关默认关闭、无配置时采样阶段用 ALPB 溶剂、能量文件的位置与格式、线性分子的振动数目判定）都沿用了原计划的推荐并与代码现状对齐过。

**What it will NOT do:** 不新增工作流或科学计算库依赖；不改动远程集群提交行为；不宣称核磁归属概率（DP4/DP5）的校准准确性；不回写历史任务的旧结果文件；缺软件环境的项目只如实记"未验证"，不冒充通过。

**Effort:** XL
<!-- Effort is exactly ONE band, never hours/days. Quick = single edit, minutes of agent work; Short = one focused change, a few files; Medium = multi-file feature in one session; Large = several waves, one long session; XL = multi-session or architectural work. A written duration is rewritten to a band. -->
**Risk:** Medium - 科学完成状态/身份语义变更触及恢复复用路径，靠"缺陷回归先行 + 真实计算验收矩阵"双门控收敛
**Decisions to sanity-check:** ① 扫描默认不做过渡态搜索（新开关显式开启）；② 指定溶剂但未配置采样模型时，采样阶段默认用 ALPB；③ 能量正式产物统一为 RESULT 下 energy/<步骤>.json（产物标识不变）；④ 线性分子外部自由度用 SVD 秩判定（非线性 3N−6 / 线性 3N−5）。

Your next move: 直接 `/ulw-execute real-qc-gap-remediation` 开工，或先要求一次高精度评审。 Full execution detail follows below.

---

> TL;DR (machine): XL | Medium risk | 14 implementation todos in 4 waves + F1-F4 final verification — restores real-QC end-to-end for scan/tsmode/NMR/gradient/CASSCF, trustworthy completion gating, readable energy products, docs alignment, and a production-faithful real-test foundation (translates docs/ACP_Real_QC_Gap_Remediation_Plan.md T01-T13).

## Scope
### Affected user and ideal state
<!-- From the draft ledger: who this touches and how they use the result, then one IS row per property with its reason, then one GAP row per difference with its reason. Every todo below closes a GAP row; ## Success criteria proves every IS row. -->
**Affected user:** U1 计算化学研究人员（CLI/Workbench 终端用户）——今天 scan/tsmode/NMR 三工作流在真实 QC 下崩溃或结果不可信，CASSCF 完成状态不可信；修复后四者可信端到端。U2 下游消费方（查看器/API/RemoteStructureCache）——今天 energy product 空 path 读取降级；修复后新产物可读。U3 平台开发者——今天合成测试全绿掩盖真实缺陷、真实用例因 PATH/YAML 差异 skip；修复后真实格式 fixture 入仓 + 生产同源门控。U4 恢复/调度子系统——今天可错误收养 CASSCF 误判回执；修复后科学门控阻断、身份变化强制重算。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | `acp run scan --input "CCO" --coordinate …` 真实端到端完成：SMILES 物化为可追溯 XYZ，逐帧松弛扫描 | 手册/README 承诺的入口必须可用 |
| IS-2 | 普通 scan 默认只扫描；显式 `--scants` 才发 TS 搜索；语义进序列化/身份/恢复/edit coverage/远程 argv | ORCA ScanTS 要求最高能量点不在端点，默认开使单调扫描必然失败 |
| IS-3 | tsmode 消费未改写的真实 ORCA 6.1.1 `.out/.hess/.xyz`，InHess 原始字节交接 OptTS 完成；线性分子保留 3N−5 | 真实文件即契约；平铺重写丢 `$act_energy` 等节 |
| IS-4 | NMR 带溶剂：CREST/xTB 只收 alpb/gbsa/none；CENSO DFT/GIAO 各用有效模型；模型变化使科学阶段缓存失效 | 溶剂模型按引擎合法域分发 |
| IS-5 | CASSCF 仅在真实 CAS 收敛事实+产物齐备时 completed；纯 SCF/未收敛/截断不伪装完成；恢复同规则重判 | 科学完成状态可信是平台底线 |
| IS-6 | OrcaGradient 冻结 payload 带 timeout_seconds + timeout:null 正常；超时传到执行层；调用者配置零污染 | 冻结契约 + 健壮缺省 |
| IS-7 | 五类（singlepoint/optimize/frequency/xtb_optimize/casscf）新 energy product 均可 resolve/read；发布失败仅重试发布 | 下游读取契约 + 执行完整性不变量 2/3 |
| IS-8 | 手册/README/CLI help/示例一致；真实测试门控与生产 resolver 同源 | 使用契约不漂移 |
| GAP-1 | SMILES 在 `_check_input` 被拒（`Unsupported input format ''`） | 手册承诺入口不可用（D2a） |
| GAP-2 | ScanOptions 无 use_scants，接口默认 True → 单调扫描 0 帧 | scan 科学语义错误（D2b） |
| GAP-3 | 真实 `.hess` 解析崩溃 + tsmode 不可用 + 线性外部子空间 6 列应为 5 | 一个核心能力整体不可用（D1） |
| GAP-4 | NMR 溶剂 SMD 泄漏 xTB/CREST → exit 1；共享引擎同型默认 | 带溶剂 NMR 全断（D3） |
| GAP-5 | CASSCF 解析假阴/假阳 + 任务层 success≠CAS 收敛 | 科学状态不可信（D5，P1） |
| GAP-6 | gradient timeout=None TypeError + 浅拷贝污染 | 冻结 payload 场景断（D4） |
| GAP-7 | energy product `path=""` 硬编码 | 下游读不到能量（D6） |
| GAP-8 | 手册与 CLI 语义漂移；测试门控 shutil.which 与生产不同源 | 契约漂移 + 假信心（D7/D8） |

### Must have
- 源文档 T01–T13 全部行为修复与验收（§5 十用例真实矩阵、§6 自动化门四类关闭事实）。
- 缺陷回归先行：改实现前 xfail(strict) 回归证明缺陷存在；修复 todo 移除标记转绿。
- 真实格式 fixture 固化入仓（含来源/sha256/方法/版本 README），无二进制 CI 可跑解析契约测试。
- scan 新参数全投影面：CLI/catalog schema/runner argv/job_edit/remote argv/identity。
- 每个行为修改与回归测试同批提交；T13 服务重启加载修复版并留档。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不新增工作流、不重建 retired 工作流、不在 acp 侧新增计算实现体（四层架构/ANTI #17；`test_unique_primitive_definitions` 守护）。
- 不新增科学库依赖、不复制溶剂/单位映射（复用 `cccp/utils/solvent_map.py`）。
- 不放宽既有守护：`test_f4_scope_audit` 断言（改动 QC 接口层须登记 amendment）、grep pins、架构不变量、edit-coverage audit。
- 不做远程 LSF 提交修复步骤（仅验证 argv 投影/路径缓存/remote tests）。
- 不宣称 DP4/DP5 校准准确性（NMR 验收只证链路与产物）。
- 不全局改写存量 manifest 空 path；历史读取兼容保留。
- 不 bump 版本号（非发布）；新代码 PEP 604 + 无裸 `except Exception:`。
- 缺二进制的验收项记 NOT_VERIFIED（= 验收未完成、阻断计划关闭，非通过态；验收机二进制齐备故必需用例须全 PASS），不得伪造通过；历史证据不得标为当前 HEAD 实测。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD（缺陷回归先行：task 2 以 xfail(strict) 落缺陷回归 → 对应修复 todo 移除标记转 PASS；每个行为修改与测试同批提交）+ framework pytest（`pytest -m "not slow"` 为全量门；真实二进制用例经 task 1 的生产同源门控，无二进制自动 NOT_VERIFIED）
- Evidence: `.omo/evidence/real-qc-gap-remediation/`（task-<N>-*.md/json + task-14-acceptance/matrix.json）（attemptDir = currentAttemptDir from the ulw-loop status operation - on Senpi the eval SDK import, const { agentToolkit } = await import(\`\${env("OMO_AGENT_TOOLKIT_SDK_ROOT")}/sdk.js\`) then agentToolkit.status(), on Codex the omo-agent-toolkit CLI, .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/）

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1（测试地基 + 快赢，3 todos）**: 1 (T01 conftest 门控) ∥ 2 (T02 fixtures+回归) ∥ 3 (T03 gradient timeout)
- **Wave 2（解析器/输入类，5 todos）**: 4 (T04 hess 解析) ∥ **6→7（串行：共改 cli.py scan 区并共享测试文件）** ∥ 8 (T08a NMR 溶剂) ∥ 10 (T09 CASSCF 解析；软协调：与 4 共 `test_f4_scope_audit.py`、与 7 共 `orca.py` 不相邻区段)
- **Wave 3（链路/门控/发布类，5 todos）**: 5 (T05 tsmode 链路) ∥ 9 (T08b 共享引擎) ∥ **11→12（串行：共改 executor.py 且 publish-retry 语义耦合）** ∥ 13 (T12 文档，待 12 落地后写 (f) 项)
- **Wave 4（final implementation wave，1 todo）**: 14 (T13 集成验收)
- **Final verification wave**: F1–F4（并行，全部 APPROVE 才算完成）

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 T01 | — | 14 | 2, 3 |
| 2 T02 | — | 4, 5, 10, 11 | 1, 3 |
| 3 T03 | — | 12(软), 13, 14 | 1, 2 |
| 4 T04 | 2 | 5 | 6, 7, 8, 10 |
| 5 T05 | 2, 4 | 14 | 9, 11, 12, 13 |
| 6 T06 | —（软：7 串行在后） | 13, 14 | 4, 8, 10 |
| 7 T07 | —（软：6 串行在前） | 13, 14 | 4, 8, 10（另与 10 协调 orca.py） |
| 8 T08a | — | 9, 14 | 4, 6, 7, 10 |
| 9 T08b | 8 | 14 | 5, 11, 12, 13 |
| 10 T09 | 2 | 11 | 4, 6, 7, 8 |
| 11 T10 | 2, 10（软：12 串行在后） | 14 | 5, 9, 13 |
| 12 T11 | 3, 11（均软：3 timeout 先行；11 同文件串行在前） | 13, 14 | 5, 9, 13 |
| 13 T12 | 3, 6, 7, 12(软) | 14 | 5, 9, 11 |
| 14 T13 | 1–13 | F1–F4 | —（终波） |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. T01/D8 — 统一真实测试的二进制解析与执行配置（conftest 生产同源门控）
  What to do / Must NOT do: 在 `tests/conftest.py` 为真实 QC 测试建立独立配置 fixture：门控与实际启动使用**同一份**经 `cccp.config.load_config` + `cccp.software.resolve_executable` 解析出的绝对路径快照。**优先级必须照抄生产实现（探针+代码实证：`config.py:95,162`）**：内置默认 < `~/.cccp.yaml` < `./cccp.yaml` < `--config` 文件 < **CONFSEARCH_\* 环境变量（覆盖显式 YAML）** < CLI 参数。**生命周期**：在 session 级 fixture 中解析一次并持久化为有效配置快照（落盘临时 JSON 或模块级缓存），marker 判定、测试体启动、路径断言全部复用该快照——不得在 collection 判定后再于测试内重新解析（autouse `_clean_env_vars`（conftest.py:286-288）可能已清理环境变量导致两次解析结果分叉）。受控 PATH 无二进制但临时用户配置含有效绝对路径 → 真实 marker 打开且实际启动该路径；无任何来源 → 明确 NOT_VERIFIED 跳过（skip reason 打印解析来源）。collection 阶段不执行 QC、不做长时间版本探测（版本证据在真实任务启动时记录）。Must NOT: 不改普通单测的隔离 `sample_config` fixture 语义；不删除既有 marker 注册；不让用户 YAML 存在改变 mock 测试行为；不得写"显式配置优先于环境变量"之类的错误优先级。
  Closes: GAP-8（测试门控部分）
  Parallelization: Wave 1 | Blocked by: none | Blocks: 14（及所有真实二进制验收）
  References (executor has NO interview context - be exhaustive): `tests/conftest.py:18-28`（_ALL_CONFSEARCH_ENV_VARS）、`:43-50`（现行 shutil.which 门控 `_resolve_executable_path` + `shutil.which(...)`）、`:286-288`（_clean_env_vars autouse）、`:307`（sample_config）、`:362-368`（real_qc/slow marker 分派）、`:372-383`（marker 注册）；`src/cccp/software.py`（resolve_executable / discover_all — 生产唯一 resolver）；`src/cccp/config.py`（load_config 6 源合并）；`docs/ACP_Real_QC_Gap_Remediation_Plan.md` T01；`docs/reports/ACP_Real_QC_Gap_Audit_20261006.json` "D8" 节（PATH≠configured≠production 对照表：orca /usr/bin/orca vs /home/<user>/orca611/orca；censo /home/<user>/.local/bin/censo vs /home/<user>/anaconda3/bin/censo）；`tests/test_cccp_software.py`（既有 resolver 测试）；`/tmp/acp_mt/FULL_TEST_REPORT.md` §2 D8（slow 运行 CREST/xTB skip 现象）
  Acceptance criteria (agent-executable): 新增 `tests/test_conftest_real_qc_gating.py`：(a) monkeypatch 受控 PATH（无二进制）+ 临时用户 YAML 配置含 ORCA/CREST 有效绝对路径 → 门控函数返回开启且解析路径==配置路径；(b) 无 PATH 无配置 → 返回关闭且 skip reason 含 NOT_VERIFIED 与解析来源；(c) `CONFSEARCH_ORCA_PATH` **环境变量覆盖**显式 YAML 配置（照抄生产顺序——非相反）；(d) **启动路径==门控路径**：带一个真实 marker 用例（或 mock 启动钩子）断言实际启动的二进制路径与快照中门控判定所用路径逐字相等。全部为纯 Python 断言，无需真实二进制。`pytest tests/test_conftest_real_qc_gating.py tests/test_cccp_software.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=上述 (a)(c) 断言通过；failure=(b) NOT_VERIFIED 路径断言。Evidence: `.omo/evidence/real-qc-gap-remediation/task-1-gating.md`
  Commit: Y | test(conftest): unify real-QC binary gating with production resolver (D8/T01)
  Recommended task executor category: unspecified-high — 测试基础设施跨 conftest/软件解析/配置三层，需对齐生产语义但无算法难度

- [x] 2. T02 — 固化真实格式 fixture 与缺陷回归（先行失败）
  What to do / Must NOT do: 把 `/tmp` 证据固化为仓库 fixture（**前置风险：/tmp 易失——若执行时 /tmp 已被清理且 sha256 对不上，来源不可再生，须立即报告而非伪造**；条件允许时先复制到持久暂存区再开始）：`tests/fixtures/qc/orca61/` 下新增 (a) `water_freq.hess`（源 `/tmp/acp_mt/freq/water/WORK/04_FREQ/freq.hess`，sha256=3d4459d8…c42b）、(b) `ts_freq.hess`（源 `/tmp/acp_mt/tsmode_src/freq.hess`，sha256=351947b1…5a0）、(c) `casscf_water.out`（源 `/tmp/acp_mt/casscf/water/WORK/08_CASSCF/casscf.out`，sha256=d1a07e81…d95）、(d) `scf_without_casscf.out`（源 `/tmp/acp_gap_review/scf_without_casscf.out`）、(e) **完整 tsmode 源 bundle**：`ts_opt.out`/`ts_opt.hess`/`ts_opt.xyz`（源 `/tmp/acp_mt/ts_opt_m0/`，真实 TS −1122.09 cm⁻¹；**注意：该目录内 `freq.hess` 与 (b) 同 sha 351947b1（同一文件，README 注明去重即可）；`ts_opt.hess` 是另一独立文件（sha 7dd26657…），必须单独固化、不得与 freq.hess 混同**）+ bundle 快照 JSON——消除 todo 5 真实验收与 todo 14 终验对 `/tmp` 的依赖；每文件字节不改写，同目录 `README.md` 记录来源路径、方法（r2SCAN-3c 等）、几何、ORCA 版本（6.1.1）、sha256、捕获日期。新增缺陷回归测试（预期值取自审计 JSON 的独立解码结果，不用被测解析器自身输出）。**xfail 纪律（探针实证修订）：只给当前确实失败的断言加 `@pytest.mark.xfail(strict=True, reason="<缺陷>—见 T04/T09")`**——即 D1 水/TS 真实格式解析（当前 HessFileError 失败）、D5 真实 CAS 正例（当前 converged=false/空占据失败）、D5 纯 SCF 假阳性反转例（断言应 converged=false 当前返回 true 失败）；**截断负例当前已返回 converged=false（探针确认）→ 正常运行不加标记**，否则 strict XPASS 反而炸第一波。修复落地时移除对应标记（strict XPASS 自动报警残留）。Must NOT: 不修改生产代码；不删旧合成 fixture（继续用于畸形输入/兼容测试）；不提交大体积 gbw/波函数文件（仅 .hess/.out/.xyz/.json）；不得一刀切全标 xfail。
  Closes: GAP-3、GAP-5 的回归基座（配合 4/10 收口）
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4、5、10、11
  References (executor has NO interview context - be exhaustive): 审计 `docs/reports/ACP_Real_QC_Gap_Audit_20261006.json` "D1"（printed vs computed 频率、sha256）、"D5"（casscf_energy=-75.976220169701、natural_occupations 预期 [1.99733,0.00267]、markers `---- THE CAS-SCF ENERGY   HAS CONVERGED ----` / `---- THE CAS-SCF GRADIENT HAS CONVERGED ----`）、"evidence_hashes"；`/tmp/acp_gap_review/`（76 文件清单：scan_default 失败对照、scan_relaxed 3 帧成功对照、audit.py 探针）；既有测试基线 `tests/test_acp_tsmode_source.py`、`tests/test_acp_electronic_state.py::test_casscf_output_parser`（38 passed 证据在审计 "focused_tests"）；fixture 现状 `tests/fixtures/`（无任何 .hess；仅 irc/*.out/xyz、input/*.xyz）
  Acceptance criteria (agent-executable): `pytest tests/test_hess_file_real_fixtures.py tests/test_casscf_real_fixtures.py -q` → **xfail 组如实 XFAIL（D1 两例 + D5 真实正例 + SCF 假阳反转例）、截断负例正常 PASS**（证明缺陷存在且未误标）；`git status` 确认 fixture 二进制入库（含 ts_opt bundle 全件：out/hess/xyz/快照 JSON + README）且 sha256 与 README 记录一致（`sha256sum` 断言写进测试）。
  QA scenarios (name the exact tool + invocation): happy=fixture sha256 校验 + xfail 如实标记；failure=人为篡改 fixture 一字节 → sha256 测试失败。Evidence: `.omo/evidence/real-qc-gap-remediation/task-2-fixtures.md`
  Commit: Y | test(fixtures): freeze real ORCA 6.1.1 hess/CASSCF artifacts with defect regressions (T02)
  Recommended task executor category: unspecified-low — 文件固化+期望值已全在审计 JSON，机械但需细心

- [x] 3. T03/D4 — 修复 OrcaGradient nullable timeout 与共享配置副作用
  What to do / Must NOT do: `run_orca_gradient` 中将缺省/null 的 `optimization_control`/`timeout` 规范化为可写映射；将被修改的配置层（optimization_control、timeout、executables.orca、resources）深拷贝后再写，杜绝浅拷贝污染调用者；非映射的非法值抛可识别的 `OrcaGradientInputError`；`timeout_seconds` 同时进入 typed `TaskResources.timeout_s` 与 ORCA 实际执行配置（二者一致）；冻结 `pes2ts_orca_gradient_request_v1` 格式不变。测试必须捕获执行参数（mock task 层断言 TaskResources.timeout_s 与传入 cfg），不能只断言不抛 TypeError。Must NOT: 不改 `_validate_gradient_request` 冻结契约；不动 pes2ts_xtb_path 侧。
  Closes: GAP-6
  Parallelization: Wave 1 | Blocked by: none | Blocks: 12(软)、13、14
  References (executor has NO interview context - be exhaustive): `src/acp/workflows/orca_gradient.py:493`（_validate_gradient_request）、`:502-508`（cfg 浅拷贝 + 嵌套写）、`:509-514`（timeout None → TypeError 点）、`:520-526`（run_orca_gradient_task 调用）；复现日志 `/tmp/acp_gap_review/gradient_timeout.log`（TypeError: 'NoneType' object does not support item assignment）；`src/cccp/calculation/requests.py`（TaskResources.timeout_s 字段）；`tests/test_acp_workflows_orca_gradient.py`、`tests/test_acp_legacy_adapters.py`；审计 JSON "cli_reproductions.D4"
  Acceptance criteria (agent-executable): 新增/扩展 pytest 参数化三组：missing timeout / timeout=null / timeout=mapping（含其他字段保留断言）+ caller config 逐键 deep-equal 不变断言 + TaskResources.timeout_s==600 断言；`pytest tests/test_acp_workflows_orca_gradient.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=timeout=null payload → 正常执行路径 + 超时参数传递断言；failure=timeout=42（非映射）→ OrcaGradientInputError 且 caller 配置未变。Evidence: `.omo/evidence/real-qc-gap-remediation/task-3-gradient-timeout.md`
  Commit: Y | fix(orca-gradient): normalize nullable timeout, isolate config layers, propagate timeout (D4/T03)
  Recommended task executor category: unspecified-low — 单函数边界清晰，含传播断言

- [x] 4. T04/D1 — 按真实 ORCA 格式重写 Hessian/几何/频率解析器
  What to do / Must NOT do: 重写 `parse_orca_hess_file` 的 `$hessian` 解析：按列头（每块起始的列索引行）与行前缀索引填充矩阵（行首 token 为行号，其后为该行从块首列起的值；行号/列头是整数、不进矩阵）；处理末尾不足一个块宽的列。**校验现状澄清（探索实证）**：dimension==3N（:240-243）、有限性（validate_finite :129-138）、对称性（:251-254）**已存在**且对称门正是当前拒绝真实文件的报错点——本 todo 新增的是索引范围越界/重复/缺失校验与列头/行号解码、尾块处理（现完全无索引解析）。`$atoms` 行按 `symbol mass x y z`（Bohr）读取坐标（现状 :195-198 静默丢弃 x y z——非失败，是丢弃）；保留旧 `$coords` 合成格式支持；两节并存时交叉校验一致；**完全无几何来源时抛 HessFileError，不得返回全零坐标**（现状 :206 无 else 分支静默零）。`$vibrational_frequencies` 按 count + index/value 对解码（count 与行号不进频率列表；实证当前注入 `[6.0, 0.0, 0.0, 1.0, …]`），保留 6 个零模式。公共返回契约 `HessFileData` 字段不变。注意：`vibrational_frequencies` 现无生产消费者（tsmode 频率取自 .out 的 parse_ts_frequency_map）——修复为契约正确性，非解阻。Must NOT: 禁止"读够 dimension² 个 token 忽略余下数据"；不删 `sections` 审计字段；不引入新依赖。
  Closes: GAP-3（解析部分）
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 5（软：与 10 同批更新 test_f4_scope_audit.py 的 amendment 声明——两处 QC 接口改动协调串行或合并同一声明）
  References (executor has NO interview context - be exhaustive): `src/cccp/qc/interfaces/hess_file.py:47-48`（正则）、`:81-97`（_read_tokens）、`:141-274`（parse_orca_hess_file 全体：:206 全零坐标、:244 平铺读取、:249-254 对称校验、:256-263 频率）；真实文件形态证据：审计 "D1" 三例（dimension=9、printed_frequencies 含 6 零模式、current_parser 报非对称）；task2 固化的 `tests/fixtures/qc/orca61/{water_freq,ts_freq}.hess`；`tests/test_hess_file_real_fixtures.py`（task2 的 xfail 用例——本 todo 移除 xfail 标记使其通过）；畸形输入负例沿用旧合成 fixture；**架构守护**：`tests/test_f4_scope_audit.py`（QC 接口层改动须登记 amendment——同批更新声明与断言）
  Acceptance criteria (agent-executable): `pytest tests/test_hess_file_real_fixtures.py tests/test_acp_tsmode_source.py -q` 全通过（xfail 移除后转 PASS）；新增边界测试**正负例分开**——**正例（合法格式必须成功解析）**：D 指数 token、块宽 5（含末尾不足整块宽的 3 列尾块）各一例解析成功且数值正确；**负例（抛 HessFileError 带明确信息）**：缺行、重复索引、越界索引、截断（块中断缺条目）、非有限值各一例；`python scripts/check_grep_gates.py --suite architecture-remediation` 通过（裸调用 exit 2——argparse 必带 --suite/--gate；与 `tests/test_grep_gates_script.py:630-634` 的规范调用一致）。
  QA scenarios (name the exact tool + invocation): happy=水 fixture 解析 → 坐标/质量与 README 记录一致 + 独立解码频率（1638.587/3865.289/3987.466 cm⁻¹ ±0.01）；failure=截断 $hessian（删末行）→ HessFileError（非静默零填充）。Evidence: `.omo/evidence/real-qc-gap-remediation/task-4-hess-parser.md`
  Commit: Y | fix(qc): parse real ORCA .hess block format, $atoms geometry, indexed frequencies (D1/T04)
  Recommended task executor category: deep-low — 真实格式解析算法，可由 fixture+代码自行 settle

- [x] 5. T05/D1 — 打通 bundle→原始 Hessian→OptTS 并修正线性外部子空间
  What to do / Must NOT do: (a) 确认/补齐 tsmode 引擎将 staging 的 `source.hess` **原始字节**交给 `transition_state_opt` 的 InHess 交接（`InHess Read` + `InHessName`）；供 Python 分析的矩阵**禁止**重新平铺写回 ORCA 输入；未解析节（`$act_energy` 等）不得丢失。(b) `build_external_subspace` 以 SVD 秩判定替代 QR 列范数筛选：对 6×dim 质量加权基矩阵做 SVD，奇异值 < 相对容差（1e-8 × s_max）判退化丢弃；非线性保留 6（3N−6 振动）、线性保留 5（3N−5）；`compute_hessian_modes` 的零模态剔除计数相应正确。(c) 复核 Bohr→Å（含既有 unit 自动判别）、原子顺序、几何 RMSD 门（0.05 Å）、选定频率索引与模态对应。Must NOT: 不放宽 `_GEOMETRY_RMSD_TOLERANCE_A` 与无 Hessian 拒绝行为；不动 `MAPPING_VERSION`（eigenvalue_rank_v1）。
  Closes: GAP-3（链路部分）
  Parallelization: Wave 3 | Blocked by: 2、4 | Blocks: 14
  References (executor has NO interview context - be exhaustive): `src/acp/calculations/tsmode/source.py:109-141`（build_external_subspace——QR 全正交 Q 列范数恒 1 的缺陷点 :136-140）、`:144-215`（compute_hessian_modes：投影/零模态剔除 :184-198）、`:296-479`（load_bundle_from_files：:336-338 HessFileError 转译、:370-380 unit 自动判别、:423-440 频率交叉校验）、`:505-560`（snapshot_bundle_files——:523-525 已按原始字节 staging，勿改语义）；**.hess 再解析消费点**：`src/acp/calculations/tsmode/engine.py:149,509`、`src/acp/calculations/tsmode/mode_mapping.py:157-167`（解析矩阵仅用于 in-process 交叉校验/投影，不回写 ORCA）；**原始字节 InHess 交接已存在的先例链**：`src/cccp/qc/interfaces/orca.py:1092-1104`（_copy_irc_hessian raw copy2）、`:3160,3209`（staged_hessian + InHess 接线）、`tests/test_acp_tsmode_orca_inputs.py:18-32`（InHess 断言先例）；`src/cccp/qc/interfaces/orca.py:3050-3096`（transition_state_opt）；`src/cccp/qc/interfaces/orca_ts.py:412-434`（InHess 关键字发射）；测试：`tests/test_acp_tsmode_{source,mapping,engine,orca_inputs}.py`、`tests/test_acp_api_tsmode.py`；真实 TS 证据 `/tmp/acp_mt/ts_opt_m0/`（ts_opt.xyz，−1122.09 cm⁻¹）与审计 D1_linear_edge（expected 5 / actual 6）。**注意**：`tests/tsmode_synthetic.py:30-33,52` 的合成 fixture 用同一个有缺陷的 build_external_subspace 生成（自洽掩盖）——新增线性测试必须直接构造受控线性几何（如线性 HCN 坐标），不得经 tsmode_synthetic 辅助；既有 `test_acp_tsmode_source.py:192` 只对非线性断言 n_zero_modes_removed==6（保持有效）
  Acceptance criteria (agent-executable): (a) 新增线性三原子（如受控线性 HCN 几何）单测：`build_external_subspace` 返回 5 列、水返回 6 列；(b) `pytest tests/test_acp_tsmode_source.py tests/test_acp_tsmode_orca_inputs.py -q` 通过；(c) 真实验收（有 ORCA 时）：使用 **todo 2 固化入库的 `tests/fixtures/qc/orca61/ts_opt_*` 源 bundle**（不依赖 /tmp）构建 bundle 并 `acp run tsmode --source-bundle <固化 bundle> --source-mode-index 0`（经 14 的隔离数据根）完成 OptTS 且 InHess 读取 staging 字节（输入文件 sha256 断言）；无 ORCA 记 NOT_VERIFIED（按 todo 14 语义：阻断关闭而非通过态）。
  QA scenarios (name the exact tool + invocation): happy=线性 5 列/非线性 6 列 + 水 fixture 频率 vs 独立解码 ±0.01 cm⁻¹；failure=几何不匹配（扰动 0.5 Å）→ SOURCE_GEOMETRY_MISMATCH 拒绝。Evidence: `.omo/evidence/real-qc-gap-remediation/task-5-tsmode-chain.md`
  Commit: Y | fix(tsmode): raw-bytes InHess handoff + SVD rank external subspace (linear 3N-5) (D1/T05)
  Recommended task executor category: deep-low — 数值秩判定 + 链路交接，可由代码+fixture settle

- [x] 6. T06/D2a — scan 的 SMILES 输入物化
  What to do / Must NOT do: `_handle_scan`（cli.py:3211）与 `_check_input`（simple.py:41）协作改为：非文件输入不再被后缀校验拒绝，先经 `StructureReader.read`（已支持 SMILES 字符串）物化为**可追溯 XYZ**（落盘于任务 WORK 区，来源记录含 SMILES 串、嵌入参数/seed、原子数、charge/multiplicity），再交给既有 CalculationRequest/TaskRequest；来源/身份记录进入 plan_identity 材料（防恢复时使用不同嵌入几何）。不存在的文件报 FileNotFoundError、非法 SMILES 报可识别 ValueError，均在启动 QC 前失败；保留有效文件输入（.xyz/.gjf/.com/.inp）与 0-based 原子编号语义。scheduler 提交路径（runner argv 构建的 `acp run scan` 子进程）自动获得相同语义，不单独实现。Must NOT: 不删除 `_check_input`（文件校验仍需）；不把 `CCO` 直接当 Path 传入下游；不在 scan 内新写一个 SMILES 解析器（复用 acp.io/acp.chem 既有入口）。
  Closes: GAP-1
  Parallelization: Wave 2 | Blocked by: none（软：与 7 共改 cli.py scan 参数区并共享 test_scan_workflow/test_acp_workflows_simple——建议 6 先落 CLI 区或同分支串行提交） | Blocks: 13、14
  References (executor has NO interview context - be exhaustive): `src/acp/cli.py:3211-3222`（_handle_scan → _check_input 调用链）、`:3926`（dispatch 表）；`src/acp/workflows/simple.py:38-50`（_SUPPORTED_EXTENSIONS/_check_input）、`:53-64`（_read_input → StructureReader）、`:247`（run_scan 内 input 使用）；`src/acp/io/structures.py:244-313`（StructureReader.read：:256 is_file 判别、:272 非文件 → SMILES 拓扑捕获、:282-313 read 主入口）；SMILES 物化先例 `src/acp/workflows/nmr.py:3254`（embed_smiles stage）、`src/acp/chem/embedding.py`（enumerate_embeddings）、`src/acp/intake/parsers.py:802-837`（RDKit ETKDG 嵌入）；身份材料 `src/acp/calculations/identity.py`；复现日志 `/tmp/acp_gap_review/scan_smiles.log`（Unsupported input format ''）；scheduler argv `src/acp/scheduler/runner.py:1401,1420`；测试 `tests/test_acp_workflows_simple.py`、`tests/test_scan_workflow.py`
  Acceptance criteria (agent-executable): pytest：(a) `CCO` + `--coordinate 0,1,1.0,1.4` 进入任务执行（mock task 断言收到物化 XYZ 路径与来源记录）；(b) 不存在文件 → FileNotFoundError；(c) `CCOXX!!` → ValueError 含 SMILES；(d) XYZ/GJF/COM/INP 文件输入回归；(e) 原子越界 coordinate 在 QC 启动前失败。真实验收（有 ORCA，随 14）：`acp run scan --input "CCO" --coordinate 0,1,1.5,1.7 --scan-points 3 --output <隔离目录>` 完成 3/3 帧（**必须显式 `--scan-points 3`——CLI 默认 21 点，示例不显式则真实验收成本失控**）。
  QA scenarios (name the exact tool + invocation): happy=SMILES 物化+来源记录断言；failure=非法 SMILES/原子越界前置失败断言。Evidence: `.omo/evidence/real-qc-gap-remediation/task-6-scan-smiles.md`
  Commit: Y | feat(scan): materialize SMILES input to traceable XYZ with provenance (D2a/T06)
  Recommended task executor category: unspecified-high — 跨 cli/workflow/io/身份多文件特性，模式已有先例

- [x] 7. T07/D2b — ScanOptions.use_scants 默认关 + 全投影面透传
  What to do / Must NOT do: typed `ScanOptions`（requests.py:605 起）新增 `use_scants: bool = False`，进 `from_dict` 严格布尔解析与序列化 `to_dict`（cccp 序列化方法名即 from_dict/to_dict，无 from_payload/to_payload）；`tasks/scan.py` 总是把有效布尔传 backend → `cccp/qc/interfaces/orca.py` 接口（接口默认 True 不动——由任务层显式传入覆盖）；`legacy_adapters.py` scan 适配投影；CLI 新增 `--scants`（默认关）；catalog scan 工作流 schema 复用既有 `scan_use_scants` 参数定义（catalog.py:1114-1121，default False）；scheduler runner 与远程 argv **必须经共享入口 `scheduler/jobs.py::scan_method_flags`（:248 定义；本地 `runner.py:1421` 与远程 `remote/script_gen.py:282` 均已调用它）统一投影**——在该函数一处补 `--scants`，禁止本地/远程分别拼参数；job_edit（EDIT_ACTIVE_WORKFLOWS 已含 scan，job_edit.py:67-72）参数 diff/preview 覆盖该参数；**有效 use_scants 进入 plan/step 身份**（identity.py）——旧缓存缺字段不得当作新语义普通 scan 完成结果收养（恢复保守重算）。对同步多坐标/显式网格等 ScanTS 无法兑现的分支：显式提前报错，不静默忽略。`ScanMode.RELAXED` 语义不变（ScanTS 是附加行为，不与 rigid/relaxed 混用）。Must NOT: 不改 PESsearch 侧既有 use_scants 管线（pes/contracts.py:205 已默认 False）；不加 per-参数 type= 包装器（大小写折叠 choke point 规则不涉及）；不新增 retired 工作流。
  Closes: GAP-2
  Parallelization: Wave 2 | Blocked by: none（软：与 6 共改 cli.py scan 参数区并共享 test_scan_workflow/test_acp_workflows_simple——同分支串行提交或 6 先行；另与 10 同文件 orca.py 但区段不相邻——:848-862/:2050 vs :720-830，协调提交即可） | Blocks: 13、14
  References (executor has NO interview context - be exhaustive): `src/cccp/calculation/requests.py:553`（ScanMode）、`:605-642`（ScanOptions 类 + from_dict/to_dict——新字段落位处）、`src/cccp/calculation/selection.py:231-235`（mode 消费）；`src/cccp/calculation/tasks/scan.py`；`src/cccp/backends/orca.py`（relaxed_scan 未透传 use_scants——FULL_TEST_REPORT D2 定位）；`src/cccp/qc/interfaces/orca.py:848-862`（_orca_scan_route_settings：use_scants→extras.append("ScanTS")）、`:2050`（接口默认 True）、`:2144-2155`；`src/acp/calculations/legacy_adapters.py`；`src/acp/cli.py`（scan 参数区，_handle_scan 邻域）；`src/acp/catalog.py:1114-1121`（scan_use_scants 定义——schema 复用源）；`src/acp/scheduler/jobs.py:248`（scan_method_flags 共享入口——本地/远程 argv 单点）、`src/acp/scheduler/runner.py:1421`、`src/acp/scheduler/remote/script_gen.py:282,137`（两消费方）；`src/acp/scheduler/job_edit.py:67-72,123-131`；`src/acp/calculations/identity.py`；正负对照证据：审计 "scan_control"（default failed 0 帧 vs relaxed completed 3/3）+ `/tmp/acp_gap_review/scan_{default,relaxed}/orca_relaxed_scan.inp`；测试：`tests/test_cccp_task_scan.py`、`tests/test_scan_workflow.py`、`tests/test_acp_workflows_simple.py`、`tests/test_acp_legacy_adapters.py`、`tests/test_recovery_identity.py`、`tests/test_acp_job_edit.py`
  Acceptance criteria (agent-executable): pytest 断言：(a) 默认构造 ScanOptions.use_scants is False 且序列化含该键；(b) 默认路径生成的 ORCA 输入无 "ScanTS" 字样、`--scants` 有；(c) 身份 fingerprint(use_scants=True) != fingerprint(use_scants=False)；(d) 旧 payload 缺字段 → 恢复判定不收养（保守重算路径触发）；(e) 多坐标 + --scants → 显式错误；(f) runner/remote argv 投影含 --scants 当且仅当开启；(g) job_edit preview diff 含该参数。`pytest tests/test_cccp_task_scan.py tests/test_scan_workflow.py tests/test_acp_legacy_adapters.py tests/test_recovery_identity.py tests/test_acp_job_edit.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=默认无 ScanTS + 身份随开关变化；failure=单调扫描端点最高能量 + --scants（真实 ORCA，随 14）→ 失败不 completed、失败帧保留索引。Evidence: `.omo/evidence/real-qc-gap-remediation/task-7-scants.md`
  Commit: Y | feat(scan): ScanOptions.use_scants default-off with full projection surface (D2b/T07)
  Recommended task executor category: unspecified-high — 跨 7 个投影面的参数线程化，纪律性强

- [x] 8. T08a/D3 — NMR 工作流分阶段溶剂模型解析
  What to do / Must NOT do: `nmr.py` 构象生成段把溶剂名与各阶段模型分开解析：sampling 段（CREST/xTB）只接收 `alpb/gbsa/none`（`LEGAL_SOLVENT_MODELS`，solvent_map.py:235）。**配置契约（探针实证修订——合并配置已默认注入 `theory.preoptimization.solvent_model=none`，"未配置"与"显式气相"在旧键上不可区分）**：新增**专用配置键 `nmr.sampling_solvent_model`**，且内置 `_get_default_config()` **不得为它注入任何默认值**（absence 即信号；对 ANTI #3 的满足方式是"不添加默认值 + 在 defaults.yaml 注释说明该键有意无默认"，而非注入 "none"）。三态语义：(i) 键缺省 + 有溶剂名 → sampling 默认 **ALPB**（回执记 `defaulted: true`）；(ii) 键显式 `"none"` → sampling 气相（不向 CREST/xTB 传溶剂，即使有溶剂名）；(iii) 键显式 `alpb/gbsa` → 照用。旧键 `theory.preoptimization.solvent_model`/`censo.solvent_model` 保留原语义、不迁移不改读。CENSO DFT 段用其有效模型（smd/cpcm 等，censo.py:316-323 合法）；GIAO 段维持 `--solvent-model` 现有语义（仅控 GIAO，独立于新键）。**范围澄清（探索实证）**：`none→smd` 兜底缺陷不止 `--solvent` 触发——`resolve_nmr_method` 在未指定溶剂且模型≠none 时默认 chloroform（method_config.py:125,576-578），故默认 `acp run nmr` 同样失败；修复必须覆盖该默认路径。有效参数回执同步记录每段实际模型与 defaulted 标记；未知模型在对应阶段严格失败（SolventValueError → conformer_search.py:343-351 failed TaskResult → nmr.py:698-704 exit 1 的既有失败链保持）。Must NOT: 不把 DFT SMD 与 sampling ALPB 描述为等价转换（不同模型）；不改 `--solvent-model` 现有 help 语义（GIAO 专用）；不复制 solvent_map（复用 `cccp/utils/solvent_map.py` 唯一校验器）；不给新键注入内置默认值；NMR 验收不宣称 DP4/DP5 校准准确性。
  Closes: GAP-4（NMR 段）
  Parallelization: Wave 2 | Blocked by: none | Blocks: 9、14
  References (executor has NO interview context - be exhaustive): `src/acp/workflows/nmr.py:640-704`（_run_conformer_generation：:674-678 兜底缺陷点、:698-704 失败出口）、`:726-731`（CrestBackend(solvent_model=...) 构造）、`:786`（capability_extras 下传 CENSO）、`:3405-3412`（WorkflowResult failed）；`src/acp/workflows/energy_shared.py:1429-1448`（resolve_solvent_config——共享解析器，旧 sampling 模型配置面=theory.preoptimization.solvent_model + censo.solvent_model）；`src/cccp/config.py:229-233,257-260`（旧键内置默认 'none'——"未配置 vs 显式气相"不可区分的根源；新键 nmr.sampling_solvent_model 不得进入默认表）；`src/acp/nmr/method_config.py:125,568-582`（_DEFAULT_SOLVENT=chloroform 默认路径——不带 --solvent 也触发）；`src/cccp/utils/solvent_map.py:235,299-306`（LEGAL_SOLVENT_MODELS + _normalize_model raise——唯一校验器）；`src/cccp/qc/interfaces/crest.py:61,78-88,146`（构造不校验、arg 构建时校验）与 `src/cccp/qc/interfaces/xtb.py:82-83,103-113,161`；`src/cccp/qc/interfaces/censo.py:311-323,630`（CENSO sm=<model> rcfile，SMD 合法）；`src/cccp/calculation/tasks/conformer_search.py:57,343-351`（SolventValueError→failed TaskResult）；`src/acp/cli.py:2468-2481`（--solvent/--solvent-model 定义）、`:3685-3711`（CLI→resolver→analysis 接线）、`:3758-3760`（exit 1）；复现日志 `/tmp/acp_gap_review/nmr_solvent_repro.log`；测试盲区原因（新测试须破除）：`tests/test_acp_workflows_nmr.py:603,651` 现传 `solvent=None` 且 mock `run_conformer_search`（真实 arg 构建器从未运行）——新回归必须带溶剂且断言 `CrestBackend` kwargs 或直接调 `_solvent_args`；守护基线 `tests/test_cccp_solvent_map.py:302-305`；气相对照 `tests/test_acp_nmr_qc_smoke.py:637,670,727`（真实链但无溶剂）
  Acceptance criteria (agent-executable): 参数化 mock pytest **三态分测**：(a) 有溶剂名 + 新键缺省 → CREST/xTB 收到 alpb+chloroform且回执 defaulted=true（捕获 capability_extras/CrestBackend kwargs 断言）；(b) 有溶剂名 + 新键显式 "none" → CREST/xTB **不收溶剂**（气相被尊重，默认 ALPB 不生效）；(c) 新键显式 gbsa → 照用；(d) GIAO `--solvent-model smd/cpcm/none` 三例独立不受新键影响；(e) 完全无溶剂全链不发溶剂；(f) 新键未知值（如 mdm）→ 明确错误。`pytest tests/test_acp_workflows_nmr.py tests/test_acp_nmr_method_config.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=chloroform→alpb(sampling)+smd(GIAO) 分发断言；failure=未知模型严格失败断言。Evidence: `.omo/evidence/real-qc-gap-remediation/task-8a-nmr-solvent.md`
  Commit: Y | fix(nmr): stage-resolved solvent models for sampling/DFT/GIAO (D3/T08a)
  Recommended task executor category: unspecified-high — 多阶段语义拆分，mock 可全验证

- [x] 9. T08b/D3 — 共享 Confsearch 协议引擎溶剂覆盖 + fingerprint 失效
  What to do / Must NOT do: 覆盖 Confsearch 共享消费路径中"为 CENSO 与 CREST/xTB 同时默认 SMD"的同型兜底（共 **4 处**：`ensemble.py:277-283`、`energy.py:256-257`、`xtbmd_censo_energy.py:1454-1455`，及 todo 8 已修的 nmr.py:677-678）：CREST/xTB 段只收合法模型（与 8 同规则：显式配置优先，缺省 ALPB）；Confsearch 协议可达路径全部覆盖（xtb_crest 协议直接经 `run_ensemble_generation(solvent=...)`；censo-crest screen 经 ensemble、rank1/cumulative 经 energy——`acp run Confsearch --solvent X` 当前同样崩溃）。身份/fingerprint 现状澄清（探索实证）：GIAO checkpoint 指纹**已含** solvent_model（nmr.py:1251-1269，test_acp_nmr_resume.py:194-220 已守护——勿重复添加）；**缺口在协议指纹不含各科学阶段有效溶剂模型**——本 todo 把 sampling 段有效溶剂模型加入 `NmrProtocolSpec.SamplingSegment`（nmr/protocol.py:80-89，以符号定位），**新字段默认值为 `None`（"历史未记录"哨兵）而非 `"none"`**：`from_dict` 对缺失字段保持 None、fingerprint 区分三态 `None`（历史未记录，不臆断为气相）≠ `"none"`（显式气相）≠ 具体模型值；**新运行总是记录实际有效模型**（不留 None）。同时给 **GeometrySegment 与 PopulationEnergySegment（protocol.py:96-103）补各自有效溶剂模型字段（同 None 哨兵语义）**——否则仅改 CENSO DFT 模型不改变协议指纹。构象生成阶段本身无缓存（每次重跑，无陈旧风险）。不能沿用旧协议的校准声明。Must NOT: 不删除/重构退役引擎文件结构（Confsearch 协议懒加载复用，workflows/AGENTS.md 明令勿删）；既有气相 4 协议测试不得回归；不改 GIAO 指纹已覆盖部分；**不得把缺失模型默认成 "none"（把历史"未记录"伪造成"气相事实"）**。
  Closes: GAP-4（共享引擎段）
  Parallelization: Wave 3 | Blocked by: 8 | Blocks: 14
  References (executor has NO interview context - be exhaustive): 四处兜底点：`src/acp/workflows/ensemble.py:275-283`（log "defaulting to SMD for CENSO and CREST/xTB."）→ CREST `:330-331` + CENSO `:365-366`；`src/acp/workflows/energy.py:252-257` → CREST `:309`；`src/acp/workflows/xtbmd_censo_energy.py:1450-1455` → xTB-MD `:1531` + xTB opt `:1602`；Confsearch 消费点：`src/acp/confsearch/protocols/xtb_crest.py:26,37`、`src/acp/confsearch/protocols/censo_crest.py:39-50`（screen→ensemble）与 `:66-81`（rank1/cumulative→energy）；身份：`src/acp/nmr/protocol.py:80-89`（SamplingSegment 无溶剂）、`:96-103`（GeometrySegment/PopulationEnergySegment——本 todo 补模型字段处）、`:114-117`（ShieldingSegment.solvent_model 已有）、`:191-209`（from_dict——缺失字段须保持 None 哨兵）、`:211-213`（fingerprint()——需区分 None/"none"/模型值三态）、消费 `src/acp/workflows/nmr.py:3132-3143`；GIAO 已覆盖证据：`src/acp/workflows/nmr.py:1251-1269` + `tests/test_acp_nmr_resume.py:194-220`；`src/acp/calculations/identity.py`（identity_fingerprint）；`src/acp/cli.py:531`（Confsearch --solvent 存在、无 --solvent-model）；测试：`tests/test_acp_nmr_protocol_spec.py`、Confsearch/ensemble 溶剂既有测试、`tests/test_cccp_solvent_map.py`
  Acceptance criteria (agent-executable): pytest：(a) 带溶剂 Confsearch 共享路径——CREST 断言不收 SMD、CENSO 指定 DFT 模型保留；(b) **fingerprint 三态互异**：fingerprint(sampling=None) != fingerprint("none") != fingerprint("alpb") != fingerprint("gbsa")；且 **geometry/population 段模型变化也改变 fingerprint**（仅改 DFT 模型 ≠ 不变指纹）；(c) 旧持久化 spec（无新字段）经 from_dict 往返保持 None（不臆断为 "none"）；(d) 新运行产物总含实际有效模型（无 None）；(e) 气相 4 协议既有测试全绿。`pytest tests/test_acp_nmr_protocol_spec.py -k solvent -q` + Confsearch 定向通过。
  QA scenarios (name the exact tool + invocation): happy=共享路径 alpb/smd 分发 + 身份随模型变化；failure=改 sampling 模型后恢复尝试收养旧阶段 → 被拒并重算。Evidence: `.omo/evidence/real-qc-gap-remediation/task-8b-shared-solvent.md`
  Commit: Y | fix(confsearch): staged solvent defaults on shared engines + model-aware identity (D3/T08b)
  Recommended task executor category: deep-low — 退役引擎复用面的可达性推理是核心难点

- [x] 10. T09/D5 — CASSCF 版本兼容解析修复
  What to do / Must NOT do: `parse_casscf_output` 及 helpers：(a) 收敛判定接受 ORCA 6.1.1 `THE CAS-SCF ENERGY   HAS CONVERGED` / `THE CAS-SCF GRADIENT HAS CONVERGED`（可变空白），保留旧 `ORBITAL OPTIMIZATION HAS CONVERGED`；**普通 `THE SCF HAS CONVERGED` 单独不能证明 CAS 收敛**（从 or 布尔中移除）；只有 energy 标记、缺最终 CAS 结果/完整收敛事实的日志不判 converged。(b) 占据数：读末次有效 `N(occ)=v1 v2 …` 或明确的 active natural occupation 区段，绑定 active space（不把 inactive/virtual 的全部 OCC 当 active 占据）。(c) ROOT：兼容真实 `CASSCF RESULTS` 区段头（现仅认 `CAS-SCF RESULTS`）+ 各 ROOT 行。(d) 能量区分：CAS 能量、NEVPT2 修正、相关能量分开（不把最终其他方法能量自动当 CAS 能量——现 :821-823 抓任意末次 FINAL SINGLE POINT ENERGY 需限定绑定 CAS 区段）。覆盖多作业块、截断日志、纯前置 SCF 反例。Must NOT: 不改返回 dict 的键结构（consumers 兼容）；不放宽"截断即未收敛"。
  Closes: GAP-5（解析部分）
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 11
  References (executor has NO interview context - be exhaustive): `src/cccp/qc/interfaces/orca.py:720-737`（正则与常量：:721-723 N[d] 格式、:727 旧标记、:734 CAS-SCF RESULTS 头——真实 6.1.1 为无连字符 `CASSCF RESULTS`）、`:740-795`（_parse_natural_occupations / _parse_nevpt2_roots / _parse_casscf_root_energies）、`:798-830`（parse_casscf_output：:821-823 能量抓取——任意末次 FINAL SINGLE POINT ENERGY、:824-826 收敛 or 布尔——两处缺陷点）；消费链：`orca.py:2766`（casscf() 入口）、`:2913-2960`（parser→QCResult.converged :2957 + metadata :2949；:2914-2919 报告能量偏好 NEVPT2 相关能——保留）、`src/cccp/calculation/tasks/casscf.py:247-276`（向任务 metadata 传播，无门控——门控在 todo 11）；fixtures `tests/fixtures/qc/orca61/{casscf_water,scf_without_casscf}.out` + task2 的 xfail 用例（本 todo 移除标记）；真实 marker 全文见审计 "D5.actual_markers"（**两行独立标记**，非复合字符串）；期望值：casscf_energy=-75.976220169701、占据 [1.99733, 0.00267]、converged=true；**旧格式兼容双守护**：`tests/test_acp_electronic_state.py::test_casscf_output_parser`（:297-308，内联合成 CASSCF_NEVPT2_OUTPUT :76-103，无收敛标记——勿断言其 converged）与 `tests/test_cccp_calculation_equivalence.py:753-761` + `tests/baseline/cccp_calculation_goldens/casscf_nevpt2.json`（golden 含旧标记 `ORBITAL OPTIMIZATION HAS CONVERGED`/`N[ i] =`/`CAS-SCF RESULTS`、converged:true——**必须继续通过**）；ORCA 6.1 手册措辞参考 `docs/ACP_Real_QC_Gap_Remediation_Plan.md` §7；`tests/test_cccp_task_casscf.py:129,207,231`（payload 级占据，不涉 parser）；**架构守护**：`tests/test_f4_scope_audit.py:122,1498-1504`（该文件既有断言——amendment 同批更新）
  Acceptance criteria (agent-executable): `pytest tests/test_casscf_real_fixtures.py tests/test_acp_electronic_state.py tests/test_cccp_task_casscf.py -q` 通过：真实水 CAS → converged=true + 占据≈[1.99733,0.00267] + ROOT 0 能量 == -75.976220169701；纯 SCF/截断/未收敛负例 → converged=false 且无 CAS 能量伪装；旧格式合成正例回归通过。
  QA scenarios (name the exact tool + invocation): happy=6.1.1 真实 fixture 三断言；failure=scf_without_casscf.out → converged=false（防假阳性）。Evidence: `.omo/evidence/real-qc-gap-remediation/task-10-casscf-parser.md`
  Commit: Y | fix(qc): ORCA 6.1.1 CASSCF convergence/occupations/roots parsing (D5/T09)
  Recommended task executor category: deep-low — 版本措辞兼容 + 区段绑定逻辑，fixture 齐备

- [x] 11. T10/D5 — 任务完成与恢复复用的 CASSCF 科学门控
  What to do / Must NOT do: `cccp/calculation/tasks/casscf.py` 完成判定：不以 `success=True`+能量存在判 completed；显式 CAS 收敛事实 + 所请求必要产物齐备才 completed；backend success 但 CAS 未收敛 → failed（保留诊断日志/能量证据），依赖步骤 `blocked`/`upstream_failed` 阻断。**恢复复用必须覆盖两条入口且调用同一科学完成校验（探针实证修订）**：(i) `step_result.json` 收养路径（checkpoint resume 的结果复用判定）；(ii) `scientific_result.json` 发布重试路径（executor 恢复时可直接从 WORK 层科学记录恢复 completed 结果、**绕过任务执行**——该路径同样必须先过 CAS 收敛校验）。两入口共用同一个 validator（收敛事实 + 身份/摘要有效 + 所请求产物齐备）；仅当原始日志完整且摘要/身份有效时按新解析规则重判，缺证据保守重算；读取过程不得改写历史任务数据。单纯发布重试不重复执行 QC（与 12 的 publish-only retry 协同）。Must NOT: 不在 executor 层重写科学判定（门在任务层与复用检查处——executor 只调用 validator）；不复活任何终态失败结果。
  Closes: GAP-5（门控部分）
  Parallelization: Wave 3 | Blocked by: 2、10 | Blocks: 14（软：与 12 同文件 executor.py 且 publish-only retry 语义耦合——12 的"零重算"断言依赖本 todo 的重试路径，建议 11 先行落地）
  References (executor has NO interview context - be exhaustive): `src/cccp/calculation/tasks/casscf.py`；`src/cccp/calculation/_common.py`（electronic-state 门控驻点）；结果契约 `src/cccp/calculation/results.py`；恢复链 `src/acp/calculations/executor.py`（checkpoint resume/复用判定）+ `src/acp/calculations/checkpoint.py`；测试 `tests/test_cccp_task_casscf.py`、`tests/test_calculation_executor.py`、`tests/test_recovery_fixtures_smoke.py`、`tests/test_recovery_cross_version.py`（跨版本恢复 fixtures 固定于 `tests/baseline/recovery_fixtures/`——新增 converged=false-completed 反例 fixture 落同目录规范）；root AGENTS.md EXECUTION PROTOCOL 不变量 3/4（身份有效+完成事实完整才复用；必需步骤 blocked 整体不判 completed）
  Acceptance criteria (agent-executable): pytest：(a) 真 CAS 收敛 fixture → completed；(b) backend success + CAS 未收敛（mock parser converged=false）→ failed + 依赖 blocked + 整体不 completed；**(c) 两条恢复入口各有旧误判负例**——(c1) `step_result.json` 收养：converged=false 的 completed 回执 → 拒绝收养、保守重算；(c2) `scientific_result.json` 发布重试：WORK 层科学记录存在但 CAS 未收敛 → **不得恢复 completed**（过同一 validator 后转重算），且重算前 QC 调用计数只反映该次必要执行；(d) 正常（真收敛）publish-only 重试路径 QC 调用计数不增。`pytest tests/test_cccp_task_casscf.py tests/test_calculation_executor.py tests/test_recovery_fixtures_smoke.py tests/test_recovery_cross_version.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=真实收敛 completed；failure=未收敛不 completed + 恢复拒绝 + 发布重试零重算三断言。Evidence: `.omo/evidence/real-qc-gap-remediation/task-11-casscf-gating.md`
  Commit: Y | fix(casscf): task-level CAS convergence gating + conservative recovery re-judgment (D5/T10)
  Recommended task executor category: deep-low — 跨任务层/恢复链的科学状态机推理

- [x] 12. T11/D6 — 共享 executor 能量产物真实文件
  What to do / Must NOT do: `executor.py::_write_result_manifest` energy 分支（:1841-1849）改为：从既有科学结果（state.result.energy）发布 `RESULT/energy/<product_id>.json`（product_id 即 :1730 的 `step_{index}_{kind.value}`，确定性位置 id；文件名如 `energy/step_0_singlepoint.json`，id 仍为 `{product_id}_energy`）——原子写（os.replace，遵守项目原子写规范），内容含 energy、`"unit": "hartree"`、方法/步骤 id、来源引用；**metadata 补 `energy_hartree` 键**（结构查看器现只读 `metadata["energy_hartree"]`（structure_viewer.py:1093,1163），当前 executor 写空 metadata 使该契约不可满足——补键即接通既有消费方）；product.path=`energy/<product_id>.json`（相对 RESULT）、product id 与 kind（`energy_report`）不变（recovery fixtures 断言 id——`test_recovery_fixtures_smoke.py:541` 只查 id 不查 path，改 path 零破坏）。**相邻缺陷同批修**：:1821-1827 artifact 循环中非 OPTIMIZE 步（如 SINGLEPOINT 的 `WORK/05_SP/sp.out`）被标 kind=ENERGY_REPORT 且 path 为 task 根相对——与 OPTIMIZE 先例一致改标 `ProductKind.FILE`（不复制进 RESULT），消除"WORK 路径+能量种类"混标。`scientific_result.json`（WORK 层，executor.py:192-220）保留为恢复证据。覆盖五类 step kind；发布中断 → resume 只补发布（QC 调用计数不增）。旧 manifest（含空 path）读取兼容保留（reader 无路径解析：results/manifest.py:23-42；viewer/remote cache 均不解引用能量 path——零下游破坏）。**冻结的 recovery 基线 fixture（v2_checkpoint_mixed 含 path:""）不得再生**（fixture 固化规则）；历史 FILE 类 task-relative 路径口径不改写。Must NOT: 不全局回填历史 manifest；不借本次把所有历史空 path 全局禁止（blocked 注记本就不是能量文件）。
  Closes: GAP-7
  Parallelization: Wave 3 | Blocked by: 3（timeout 语义先行，避免同文件区冲突等待） | Blocks: 13、14
  References (executor has NO interview context - be exhaustive): `src/acp/calculations/executor.py:1691-1851`（_write_result_manifest 全体：:1841-1849 现硬编码 path="" 缺陷点、:1730 product_id 构造、:1710-1723 blocked path=""（非能量，不动）、:1768-1775 normal_modes 复制进 RESULT 先例、:1821-1839 artifact 循环混标点、:522 step kind→ProductKind 映射）、`:192-220`（_step_scientific_record——scientific_result.json）、`:1067,1456`（load_scientific_result 恢复消费）；**in-tree 正确先例**：`src/acp/workflows/orca_gradient.py:49`（ENERGY_PRODUCT_RELATIVE_PATH="RESULT/energy/energy.json"——常量含 RESULT 前缀，add_product 时用其相对段）+ `:443-448`（path+metadata 模式——照此实现）；读取方：`src/acp/results/manifest.py:23-42`（无路径解析）、`src/acp/results/structure_viewer.py:1064-1065,1088,1091-1095,1160-1165,1474`（只读 metadata.energy_hartree + 空 path 守卫）、`src/acp/results/remote_structure_cache.py:451-453,476-484`（只取 frequency_modes/structure，跳空 path）；写入器 `src/acp/storage/manifest.py:43,98-116,139-150`（path 契约="相对 RESULT"、按 id 去重、原子写）；发布中断恢复 fixture `tests/baseline/recovery_fixtures/v2_publish_interrupted/`（不再生）；审计 "D6"（五类 empty_energy_products 名单）；`/tmp/acp_mt/sp/water/RESULT/result_manifest.json`（实样）；测试基线盲区：`tests/test_calculation_executor.py:268-274`（只断言 len(products)>0）、`tests/test_storage_manifest_kinds.py:41-46,76-81`（测试自供非空 path——绕过 writer）、`tests/test_acp_structure_viewer.py:1300-1303,1358-1361,1423-1425,2936-2939`（把 path:"" 当期望输入——本 todo 后可补非空用例）、`tests/test_recovery_fixtures_smoke.py:151-153,497-499,525-527`（只断言 id）
  Acceptance criteria (agent-executable): pytest：(a) 五类 step kind 各跑一次 mock plan → 每个 energy product path 非空、resolve 后文件存在、JSON 值==scientific_result 能量、unit==hartree；(b) product id 与改动前一致（快照断言）；(c) 发布中断→resume：energy 文件补齐且 mock QC 计数不变；(d) 含空 path 的旧 manifest fixture 读取不抛错。`pytest tests/test_calculation_executor.py tests/test_storage_manifest_kinds.py -q` 通过。
  QA scenarios (name the exact tool + invocation): happy=五类产物可读且值一致；failure=发布中断后 resume 只补发布（QC 计数断言）。Evidence: `.omo/evidence/real-qc-gap-remediation/task-11b-energy-products.md`
  Commit: Y | fix(executor): publish real RESULT/energy files for energy products (D6/T11)
  Recommended task executor category: unspecified-high — 发布层特性 + 恢复语义，边界清晰

- [x] 13. T12/D7 — 手册与使用契约对齐
  What to do / Must NOT do: 按修复后行为更新：`docs/ACP_Function_Test_Manual.md`、`README.md`、CLI help、相关 DevDoc 与 AGENTS 命令示例。(a) **IRC provenance（必填互斥组，cli.py:178-180）**：文件路径用 `--ts-provenance prov.json`、调度器内联 JSON 才用 `--ts-provenance-json`——二者必选其一（实证：`acp run irc --input … --input-role …` 缺组报 `error: one of the arguments --ts-provenance --ts-provenance-json is required`）；手册示例缺失/误用此处即 D7 漂移；(b) IRC 示例**同时**补 `--input-role transition_state`（可选参数但手册漏标，与必填 provenance 组是**并列**要求、非替代）；tsmode 无 provenance 参数（仅 `--source-bundle`），勿把 provenance 写进 tsmode 示例；(c) 移除 runnable 示例中被 ORCA 忽略的 `--step`（保留兼容 warning 说明并写明未生效）；(d) Confsearch 按协议/refinement-policy 描述实际阶段（`xtb-crest screen` 不标 DFT 精修——selection.py:36 screen 返回 []）；(e) scan 默认无 ScanTS + `--scants` 显式说明；(f) RESULT 相对科学产物 vs 历史 FILE task-relative 路径口径；(g) 真实测试检测方式与 Python/二进制版本记录（3.11.13 vs 原报告 3.13.9 差异入验收记录）；(h) 清理"所有链路均无自动化覆盖"等与 `tests/test_acp_nmr_qc_smoke.py` 不符的绝对表述。验收以 PASS/FAIL/NOT_VERIFIED 记录覆盖。Must NOT: 不为文档目的重复昂贵 QC 运行；不改已冻结契约的语义描述；示例命令必须真实可执行（用隔离测试目录或 fixture 验证）。
  Closes: GAP-8（文档部分）
  Parallelization: Wave 3 | Blocked by: 3、6、7、12（软：文档 (f) 项描述 todo 12 定型的 RESULT 相对路径口径，须以其落地为准） | Blocks: 14
  References (executor has NO interview context - be exhaustive): `docs/ACP_Function_Test_Manual.md` §3.4/§3.10（FULL_TEST_REPORT D7 指认）；`README.md`（scan/nmr/tsmode 示例区）；`src/acp/cli.py` help 文案（scan/tsmode/irc 参数区）；`docs/ACP_Simple_Workflows_DevDoc.html`、`docs/ACP_TSMode_Optimization_Implementation_Plan.md`、root/嵌套 AGENTS.md 命令块；`src/acp/confsearch/selection.py:36`；`tests/test_acp_nmr_qc_smoke.py`（真实链路既有覆盖证据）；`/tmp/acp_mt/FULL_TEST_REPORT.md` §2 D7（全部漂移项清单）
  Acceptance criteria (agent-executable): 脚本化检查：(a) 手册+README 中 `--ts-provenance-json` 仅出现在内联语义处、文件示例均 `--ts-provenance`；(b) IRC 示例**可运行性**：均携带必填 provenance 组之一**并**含 `--input-role transition_state`（缺组示例 = FAIL）；(c) runnable scan 示例无 `--step`（irc 同理仅 warning 注记处提及）；(d) scan 默认描述含"不启用 ScanTS"字样；写一个 `tests/test_docs_contract.py`（grep 断言）固化以上；关键示例命令在隔离目录实跑（无需 QC 的 --help 级验证全量 + 有二进制时抽 3 条真实）。
  QA scenarios (name the exact tool + invocation): happy=test_docs_contract.py 全绿 + `acp run scan --help`/`acp run irc --help` 输出与文档一致性以**脚本化对照断言**落 evidence（help 文本 grep/diff 断言，零人工步骤，符合本计划零人工验证策略）；failure=文档与 help 不一致项逐条列出为 FAIL。Evidence: `.omo/evidence/real-qc-gap-remediation/task-13-docs.md`
  Commit: Y | docs(manual): align manual/README/help with fixed scan/tsmode/IRC/solvent semantics (D7/T12)
  Recommended task executor category: writing — 文档对齐为主 + 一个 grep 契约测试

- [x] 14. T13 — 集成验收与交付（真实计算矩阵 + 服务重启 + 记录）
  What to do / Must NOT do: 依赖 1–13 全部完成。(a) 定向测试：源文档 §6 分 defect 测试文件清单全绿；(b) 真实矩阵（§5 十用例，隔离数据根 `ACP_RUN_ROOT=<tmp>/acp_gap_accept`，限 nproc≤4，每进程超时，保留输入/输出/退出码/版本/工件摘要）：普通 scan（水 3 点无 ScanTS 3/3）、SMILES scan（CCO 3 点）、显式 ScanTS 负例（端点最高能量）+ 正例（额外 TS 搜索证据）、tsmode（真实 TS bundle，InHess sha256 校验）、NMR 乙醇 chloroform censo-zero（再 censo-light）、Confsearch 溶剂回归（CREST 不收 SMD）、OrcaGradient（timeout_seconds=600 + timeout=null）、CASSCF（真收敛 + 受控缺收敛负例及恢复拒绝）、energy 发布（五类 path 可读 + publish-only retry 零重算）、API（修复版服务新任务 scan/NMR/CAS 提交+detail/files/viewer 读取）；(c) 全量门：`pytest -m "not slow"`（CI 同款，`pip install -e '.[dev,api,remote]'` 已装）+ py3.10/3.11/3.12 compileall（如可行）+ `ruff check src tests` + `python scripts/check_grep_gates.py --suite architecture-remediation`（必带 --suite，裸调用 exit 2）+ 架构门三件套（test_architecture_invariants / test_f4_scope_audit / test_grep_gates_script）；(d) 服务重启：`sudo systemctl restart acp`（root AGENTS SYSTEMD 规则），记录服务加载版本（`acp doctor` / git rev）；仅对本轮新建 API 任务验收，既有 failed 作业（如 20261005_213230_002_Confsearch）保留原状不碰；(e) 产出：14 工作流矩阵（验收级别：科学门槛/产物门槛/未变路径沿用原始报告标注），修复总结 + 未验证项 + 精确版本清单。**关闭语义（修订）**：十用例中 scan×3/tsmode/NMR/Confsearch 回归/OrcaGradient/CASSCF/energy/API 全部为必需用例，在验收机（本机二进制齐备——审计 D8 生产路径表）**必须全部 PASS 才可关闭计划**；任一用例因缺软件 NOT_VERIFIED = **验收未完成、阻断计划关闭**（不是通过态——不得重演"全绿但未验证"）；仅"未重测的未变路径"可沿用原始报告标注。Must NOT: 缺二进制不得伪造通过（NOT_VERIFIED 阻断关闭而非放行）；不得把历史证据标为当前 HEAD 实测；不做远程 LSF 提交；无超时裸跑长任务。
  Closes: GAP-1..GAP-8（终验收口）
  Parallelization: Wave 4（final implementation wave） | Blocked by: 1,2,3,4,5,6,7,8,9,10,11,12,13 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): 源文档 `docs/ACP_Real_QC_Gap_Remediation_Plan.md` §5（十用例输入规模与必须验证项）、§6（自动化门清单）；root `AGENTS.md` COMMANDS（pytest 门）、SYSTEMD SERVICE（restart 规则）、EXECUTION PROTOCOL（验收不变量）；`docs/reports/ACP_Real_QC_Gap_Audit_20261006.json` D8（二进制生产路径表——真实矩阵用生产解析路径）；`scripts/check_grep_gates.py`；真实输入源：`/tmp/acp_mt/ts_opt_m0/`（真实 TS）、`/tmp/acp_mt/sp|opt|freq|xtb|casscf/water/`（energy 发布对照）、`/tmp/acp_gap_review/scan_relaxed/`（scan 期望帧：distances 1.05/1.15/1.25 Å、energies −76.41475646/−76.39847304/−76.3775457）；运行根隔离规则 root AGENTS ANTI #19（run_root 原生文件系统）
  Acceptance criteria (agent-executable): `.omo/evidence/real-qc-gap-remediation/task-14-acceptance/` 下落 matrix.json（十用例 × {status: PASS/FAIL/NOT_VERIFIED, evidence_paths[], versions}）+ full-suite.log + service-restart.log；**全部必需用例（scan×3、tsmode、NMR、Confsearch 回归、OrcaGradient、CASSCF、energy、API）必须 PASS——本机二进制齐备，无 NOT_VERIFIED 通行证；任一 NOT_VERIFIED 或 FAIL → 计划保持未关闭并回退对应 todo**；全量 `-m "not slow"` 0 failed。
  QA scenarios (name the exact tool + invocation): happy=十用例必需项**全 PASS**（NOT_VERIFIED=验收未完成，不入 happy）+ 全量绿；failure=任一 FAIL 或 NOT_VERIFIED → 矩阵标红 + 计划保持未关闭 + 回退项登记（不静默）。Evidence: `.omo/evidence/real-qc-gap-remediation/task-14-acceptance/matrix.json`
  Commit: Y | test(acceptance): real-QC integration matrix + service restart + delivery record (T13)
  Recommended task executor category: unspecified-high — 长流程编排与证据管理，无算法难点但步骤繁多

- [x] 15. (Discovered during Wave-1 prep, 2026-10-06) — 冻结 CREST 电荷旗标修正回归（--chrg）
  What to do / Must NOT do: 工作区预存未提交修正：`src/cccp/qc/interfaces/crest.py` 两处 `-charge` → `--chrg`（实证：`strings /home/<user>/crest/crest` 只含 `-chrg`/`--chrg`/`-charges`，不含 `-charge`；原写法使真实 CREST 调用传错旗标——同类"真实环境断点"，与本计划 GAP 类缺陷同源）。本 todo 不重做实现，只冻结证据：(a) 在既有 `tests/test_qc_interfaces_crest.py` 增加命令构建回归，断言电荷值所配旗标在合法集合 {`-chrg`,`--chrg`} 内且参数中无 `-charge` 形 token（覆盖两处构建点）；(b) 运行 CREST 界面定向测试确认无回归。Must NOT: 不改 CREST 其他旗标/流程语义；不删除该预存修正；不新增依赖；不碰溶剂相关逻辑（todo 8/9 处理）。
  Closes: —（W1 预备期发现的独立真实-QC 断点，不属 GAP-1..8 行）
  Parallelization: Wave 1（独立；crest.py 后续 todo 8/9 以本提交为基线）| Blocked by: none | Blocks: 8（软：同文件）
  References: `src/cccp/qc/interfaces/crest.py:136-140,282-286`（两处参数构建）；`tests/test_qc_interfaces_crest.py`（既有测试）；`strings /home/<user>/crest/crest | grep chrg`（实证依据）。
  Acceptance criteria (agent-executable): `pytest tests/test_qc_interfaces_crest.py -q` 通过且新增断言生效。
  QA scenarios (name the exact tool + invocation): happy=新增旗标断言通过；failure=临时把旗标改回 `-charge` → 新增测试失败（演示后还原，不提交该演示）。
  Commit: Y | fix(crest): use --chrg charge flag accepted by CREST CLI (discovered W1)
  Recommended task executor category: quick — 单文件测试补丁，实现修正已在工作区

- [x] 16. (Discovered during T13 acceptance, 2026-10-07) — ORCA 6.1.1 默认 NMR 泛函别名（mPW1PW91 → mPW1PW）
  What to do / Must NOT do: 真实 ORCA 6.1.1 探针实证（`/tmp/opencode/probe_func/a.out`）：默认 Goodman 泛函 `mPW1PW91` 被拒（exit 4, `UNRECOGNIZED OR DUPLICATED KEYWORD(S) IN SIMPLE INPUT LINE`）；同一 ORCA 接受 `mPW1PW`（exit 0, `****ORCA TERMINATED NORMALLY****`）——ORCA 对同一 modified-PW 1-参数泛函的命名。当前 `acp run nmr` 默认在 ORCA 6.1.1 上 GIAO 阶段不可达（矩阵发现项 D-NMR-ORCA61-FUNCTIONAL）。修复：在 **ORCA 输入发射层**为 `mPW1PW91` 增加别名映射 → 发射 `mPW1PW`（大小写不敏感），并在执行回执/metadata 中同时记录请求名（`mPW1PW91`）与执行关键字（`mPW1PW`）——**不得静默**。Goodman 误差模型 level 校验（`src/acp/nmr/error_model.py:49,84`）与 `src/acp/nmr/models/tms_references.txt` 校准表保持不变。Must NOT: 不改 DP4/DP5 概率/校准代码与声明；不动其他引擎（Gaussian 等）命名；不改 METHOD_META 其他泛函；不把别名扩散为全局方法重命名（仅 ORCA 发射层 + 回执记录）。
  Closes: 矩阵发现项 D-NMR-ORCA61-FUNCTIONAL（IS-4 "NMR 全链路" 默认路径收口）
  Parallelization: 独立（T13 后）| Blocked by: none | Blocks: F1-F4（evidence 完整性）
  References: `src/acp/nmr/error_model.py:49,60,84`；`src/acp/nmr/models/tms_references.txt`（不改）；`src/acp/nmr/method_config.py`；ORCA 输入发射链（`src/cccp/qc/interfaces/orca.py` NMR 输入构建 / `src/cccp/qc/translation.py`，`_build_nmr_input_lines`）；`.omo/evidence/real-qc-gap-remediation/task-14-acceptance/matrix.json` `discovered_defects[0]`；探针 `/tmp/opencode/probe_func/{a,b}.out`。
  Acceptance criteria (agent-executable): (a) 单测：Goodman 默认 level 配置下发射的 ORCA 输入含 `mPW1PW` 且不含 `mPW1PW91`；回执/metadata 记录 requested=`mPW1PW91` + executed=`mPW1PW`；(b) error_model 对 Goodman level 的校验保持原样（现有测试全绿）；(c) 真实验收：`acp run nmr --input "CCO" --solvent chloroform`（默认 method，censo-zero，隔离 run root，超时受限）全链路完成——GIAO 阶段产生谱图产物且无 ORCA 关键字错误；更新 `task-14-acceptance/matrix.json` case5/新增 5b 为默认 level PASS + 报告更新。`pytest tests/test_acp_nmr_*.py -q` 全绿（非 slow）。
  QA scenarios (name the exact tool + invocation): happy=默认 level GIAO 输入含 mPW1PW 且真实运行完成；failure=移除别名→单测失败 + 真实输入被 ORCA 拒绝（复现 exit 4）。
  Commit: Y | fix(nmr): emit ORCA-native mPW1PW alias for Goodman mPW1PW91 default (discovered T13)
  Recommended task executor category: deep-low — 发射层映射 + 回执记录 + 真实冒烟

- [x] 17. (Discovered during F3/F4 audit, 2026-10-07) — simple 工作流示例与行为对齐（singlepoint SMILES 漂移）
  What to do / Must NOT do: F3 实证：`acp run singlepoint --input "CCO"` 报 `ValueError: Unsupported input format ''`（`simple.py::_check_input` 只接受 .xyz/.gjf/.com/.inp；CLI help 明示 "Input structure file (XYZ, GJF, COM, ORCA .inp)"），但 README 快速开始与 AGENTS COMMANDS 仍以 SMILES 作 singlepoint 示例——T13 "示例命令必须真实可执行" 条款下的遗漏，属 IS-8（手册/README/CLI help/示例一致）缺口。修复方向：文档纠正 `simple` 系列示例为文件输入（与 CLI help/实际行为一致），并在 `tests/test_docs_contract.py` 增补"simple 系列示例输入必须匹配 `_SUPPORTED_EXTENSIONS` 能力声明"的脚本化断言。Must NOT: 不改 simple 运行时行为；不删 `_check_input`；不碰 scan/irc 已修路径；若产品意图为 simple 支持 SMILES，另立决策行（按 T06 模式扩展，范围外）。
  Closes: IS-8 示例一致性缺口（F4 发现）
  Parallelization: 独立 | Blocked by: none | Blocks: F1-F4 最终 verdict
  References: `README.md:314-315`；`AGENTS.md:153`；`src/acp/workflows/simple.py:53-62`（`_check_input`）；`acp run singlepoint --help`（file-only）；`tests/test_docs_contract.py`；F3 实证记录（本会话 `/tmp/opencode/f3/*`）。
  Acceptance criteria (agent-executable): README/AGENTS 中 simple 系列（singlepoint/optimize/frequency/xtb_optimize）示例输入与 `_SUPPORTED_EXTENSIONS` 一致（grep 断言加入 `tests/test_docs_contract.py`）；`pytest tests/test_docs_contract.py -q` 全绿；`acp run singlepoint --input <文件>` 语义不变。
  QA scenarios (name the exact tool + invocation): happy=脚本化断言 + 抽样实跑文件示例；failure=simple 示例中残留 SMILES → 新断言失败。
  Commit: Y | docs(readme): align simple-workflow examples with file-only CLI input (discovered F3)
  Recommended task executor category: quick — 文档两行 + 契约断言

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Ideal-state fidelity

## Commit strategy
- 每个 todo 恰好一个 commit（行为修改 + 对应回归测试同批；类型见各 todo Commit 行，conventional commits：`fix(...)`/`feat(...)`/`test(...)`/`docs(...)`）。
- 顺序遵循波次与依赖矩阵；task 2 的 fixture 二进制与测试同 commit 入库（`tests/fixtures/qc/orca61/` + README + sha256 断言）。
- pre-commit（ruff --fix + ruff-format）逐 commit 生效；禁提交 e2e-task-root.txt 类审计痕迹（tests/AGENTS 规则）。
- 涉及 QC 接口层（task 4/10）的 commit 必须同批带 `test_f4_scope_audit.py` amendment 更新。
- 全部 commit 落在 main 或任务专属 worktree 分支（由 `/ulw-execute` 启动参数决定）；T13 验收记录（matrix.json 等）落 `.omo/evidence/real-qc-gap-remediation/`——**该目录不入 git**；报告性结论另以一份 `docs/reports/ACP_Real_QC_Gap_Remediation_Acceptance_<date>.md` 随最终 commit 入库。

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 6 | task-6 happy：SMILES 物化+来源记录断言 + 真实 CCO 扫描 3/3 帧（task 14 矩阵 SMILES scan 行） | `.omo/evidence/real-qc-gap-remediation/task-6-scan-smiles.md`; `task-14-acceptance/matrix.json` |
| IS-2 | 7 | task-7 happy：默认无 ScanTS + 身份随开关变化；task 14 矩阵 ScanTS 负例（端点最高能量不 completed） | `task-7-scants.md`; `task-14-acceptance/matrix.json` |
| IS-3 | 4, 5 | task-4 happy：水 fixture 解析与独立解码一致；task-5 happy：线性 5 列/非线性 6 列；task 14 tsmode 行（InHess sha256 + 收敛 + 虚频目标重叠） | `task-4-hess-parser.md`; `task-5-tsmode-chain.md`; `task-14-acceptance/matrix.json` |
| IS-4 | 8, 9 | task-8a happy：chloroform→alpb(sampling)+smd(GIAO) 分发；task-8b happy：共享路径 CREST 不收 SMD + fingerprint 随模型变；task 14 NMR 行（乙醇 censo-zero 全链） | `task-8a-nmr-solvent.md`; `task-8b-shared-solvent.md`; `task-14-acceptance/matrix.json` |
| IS-5 | 10, 11 | task-10 happy：真实 CAS converged=true + 占据 [1.99733,0.00267]；task-11 failure：未收敛不 completed + 恢复拒绝 + 发布重试零重算 | `task-10-casscf-parser.md`; `task-11-casscf-gating.md` |
| IS-6 | 3 | task-3 happy：timeout=null 正常 + TaskResources.timeout_s==600 + caller 配置不变 | `task-3-gradient-timeout.md`; `task-14-acceptance/matrix.json`（OrcaGradient 行） |
| IS-7 | 12 | task-12 happy：五类 energy path 可读值一致；failure：发布中断 resume 零重算 | `task-11b-energy-products.md`; `task-14-acceptance/matrix.json`（energy 发布行） |
| IS-8 | 1, 13 | task-1 happy：门控与生产同源三断言；task-13：docs 契约测试全绿 + help 一致 | `task-1-gating.md`; `task-13-docs.md` |
