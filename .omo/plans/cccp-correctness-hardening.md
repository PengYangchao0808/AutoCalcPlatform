# cccp-correctness-hardening - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 一次性消除"参数描述不对就把整批计算悄悄做废"的隐患：扫描点选 GFN 方法时不再出现基组、色散等不适用的参数，溶剂模型行为与 xTB 一致；已知的网格与二面角缺陷修复并加回归；同时立起一个统一的关键字登记表，以后新增参数或方法族不会再出现同类漏配。

**Why this approach:** 真正"一劳永逸"的落点不是逐个打补丁，而是把"参数适用性 + 关键字翻译"收敛到 cccp 的单一事实源（统一注册表），再由它同时驱动后端输入生成、目录数据与网页字段门控——本次的网格透传、GFN 剥离不全、GBSA 非法、溶剂名分叉都是同一根因的不同表现。

**What it will NOT do:** 不新建通用的字段可见性管线；不重写后端接口架构；不改与本次无关的科研默认值与工作流编排（唯一例外：非法默认网格 UltraFine 改为空值，属本案修复范围）；不做静默回退或把自由文本大小写折叠；承诺范围限定为本文列明的方法 × 参数 × 入口（"一劳永逸/零错误"为方向性表述，不构成对未列组合的保证）。

**Effort:** Large
**Risk:** Medium - 触及 QC 核心输入构建 + 目录/校验/前端，但以"真实 ORCA 慢速矩阵 + 端到端重跑"作为安全网。

**Decisions to sanity-check:** ①GFN 溶剂仅 {none, ALPB}（GBSA 移出 ORCA）②门控复用 METHOD_META 机制 ③统一关键字注册表 ④legacy 网格值近似映射+告警 ⑤枚举未知值 fail-fast、自由文本透传 ⑥全谱测试（含真实 ORCA 与端到端重跑）⑦实现解析 = `resolve_implementation(method, engine)`（方法×引擎共同决定）⑧入口行为契约（新建=严格；未改动=迁移；改动字段=严格）⑨能力/依赖/策略三分（探针只更新前两列，不改写策略）。

Your next move: 计划文件已写入；随后自动执行双通道高精度评审（momus + Oracle）并汇报结果。执行请在独立 worker 会话启动（如 `$start-work cccp-correctness-hardening`）。

---

> TL;DR (machine): Large/Medium - fix dihedral + grid bugs, add a unified cccp keyword registry, gate GFN-inapplicable parameters across cccp/catalog/frontend, verified by a real-ORCA smoke matrix and a propylene PESsearch E2E re-run.

## Scope
### Affected user and ideal state
**Affected user:** 计算化学研究者（CLI 与 Workbench 两条使用路径）。今天他们可能在 GFN 方法的扫描/PES 任务里带着不适用的基组/色散/网格/溶剂参数提交，QC 输入非法时整批计算悄悄作废且难以察觉；修复后，任何入口都不会再产生方法不适用的参数，非法组合在提交/运行期被明确拦截或迁移告警，关键行为有真机证据背书。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | 含二面角的扫描/PES 任务在全角度域（含 >180° 目标）量测与约束判定正确，不再因内部相位偏差整批丢帧 | 角错位会静默把整批帧判废（F1 实测 90/90） |
| IS-2 | 无论从哪个入口（CLI/API/重算），都不会把方法不支持的参数写进 QC 输入；未知枚举值立即给出可行动报错而不是悄悄透传 | 静默透传/不一致映射正是本案根因（F2/F8） |
| IS-3 | 选择 GFN 方法时不再出现基组/色散/网格/RI/aux 或非法溶剂关键字；ORCA 路径溶剂行为为 ALPB-only | 非法输入会让 QC 拒绝或结果不可用（F7/F9） |
| IS-4 | 独立 xTB 计算使用规范溶剂名，别名正确解析，非法名附带合法清单报错 | 非法名原样透传导致运行失败（F10） |
| IS-5 | 网页端选择 GFN 后不适用字段隐藏/锁定，单点网格为合法选项，历史值迁移有提示，非法组合在提交/运行期被拦截，告警在界面可见 | 用户无法从界面判断参数是否适用（F4/F5/F19） |
| IS-6 | 关键行为有真机证据：GFN/网格关键字经真实 ORCA 6.1.1 冒烟矩阵验证，propylene PESsearch 端到端重跑成功（90/90 SP、候选非空） | 单元测试不能证明 QC 真正可用（F11/F12/F21） |
| IS-7 | 代码库保持可回归：离线测试全绿、无新增 lint/类型违规（相对基线）、旧行为锁定测试同步更新 | 防止修复引入新回归（F16；AGENTS 反模式 #15） |
| IS-8 | 文档与实现一致（GFN 规范 §2.1、溶剂选项说明） | 避免规范与实现再次偏离（F17） |
| GAP-1 | `calculate_dihedral` 恒有 ±180° 相位偏移；PES 残差未做角度环绕 → 历史 90 帧全部 off-constraint | 闭于 T1/T21 |
| GAP-2 | 关键字映射分散在 5-6 张表且行为不一（透传/丢弃/报错并存）；GFN-FF 漏检 | 闭于 T2/T3/T4/T5/T22 |
| GAP-3 | GFN 参数剥离不对称（NMR 强填 6-311G(d)、backend 泄漏 def2-TZVPP、ts/irc 无检查）；GBSA 非法；SMD/CPCM 静默改写 | 闭于 T6/T7/T8/T10 |
| GAP-4 | xTB 二进制溶剂名无映射（dichloromethane 等非法名原样透传），零单测 | 闭于 T9 |
| GAP-5 | 前端无方法族门控；single_point copy-scan-level 传染；grid 默认 UltraFine 非法；告警无展示通道 | 闭于 T11-T16/T23/T24/T25 |
| GAP-6 | 缺真机验收（3 个点名 GFN 测试不存在、无冒烟矩阵、E2E 未重跑） | 闭于 T17/T18/T22 |
| GAP-7 | lint/format 门禁被推迟（预存 847 ruff / 115 format / 386 mypy），旧行为锁定测试未更新，缺基线对比约定 | 闭于 T19/T20/T26 |
| GAP-8 | 文档与实现偏离（`docs/ACP_Scan_OptTS_GFN_DevPlan.md` §2.1 与当前行为不符） | 闭于 T19 |

### Must have
1. cccp 单一关键字注册表（方法族 × 实现（ORCA 原生 / ORCA 外部 xTB / 独立 xTB）× 参数：合法值/别名/适用性/未知值策略）；全部 `!` 行装配点（8 处）经统一渲染器，无旁路。
2. cccp 几何修复：`calculate_dihedral` 恢复标准 IUPAC 约定 + 回归测试。
3. cccp GFN 语义全入口一致：basis/dispersion/grid/RI/aux 统一剥离；ORCA 路径溶剂仅 `ALPB(solvent)`、GBSA 移出、取消静默改写；NMR/TS/IRC 对齐；GFN-FF 正确识别。
4. cccp xTB 二进制溶剂名表（含 ch2cl2/chcl3/thf/dmso 等规范名）与别名解析。
5. acp：METHOD_META 增加 GFN 条目 + 家族化选项解析 + 最小 grid/SCF 不可用规则；scan 溶剂选项家族化（GFN→{none,ALPB}）；single_point grid 改 `DefGrid1/2/3`（默认空）；校验层补齐 GFN 与 single_point purpose；提交/运行期校验（非仅 advisory /validate-method）。
6. 前端：方法族驱动的字段隐藏/锁定 + copy-scan-level 防传染。
7. 测试：纯函数 TDD + 集成 tests-after + 真实 ORCA 6.1.1 slow 冒烟矩阵（含 outcome 分类学：语法错误/缺依赖/计算失败/有效完成）+ propylene PESsearch 端到端重跑 + 浏览器交互验收（T25）；更新锁定旧行为的测试；文档同步至 `docs/ACP_Scan_OptTS_GFN_DevPlan.md` §2.1。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不新建通用 depends_on/visibility 管线（Q2 决策：复用 METHOD_META 机制）。
- 不重写 `QCInterfaceBase`/后端架构；唯一的例外是"8 处 `!` 行装配收敛到统一渲染器"（T5，含 orca_ts 纯函数）——这是本计划明确许可的唯一结构性变更；不引入 Gaussian；不改与本案无关的科研默认值（唯一例外：非法默认网格 `UltraFine`→`""`，属本计划范围）。
- 注册表范围边界：仅 ORCA `!` route + 关键字映射 + 溶剂名；CENSO rcfile/preset 注入模板（`censo.py:655` 类 route 字符串）、CREST/Molclus CLI 组装不在注册表范围（仅消费 `xtb_solvent`）。
- 不做静默回退、静默改写、自由文本 case-fold（AGENTS 反模式 #33）。
- 不改工作流编排（scan→SP 顺序、默认协议名 `orca_relaxed_scan_xtb_gfn2_sp_b973c_v1`）；不做 UI 视觉重构。
- 不执行生产数据迁移/清理（仅验收性重跑 propylene_PESsearch）。

### Entry behavior contract（严格提交 / 历史迁移 / 底层兼容 / 子进程）
> 以字段为单位区分"用户显式输入"与"历史继承值"；两类值处理不同，执行者不得自行猜测。

| 入口 | 上下文 | 行为 |
| --- | --- | --- |
| 新建 API / 向导 / 普通 CLI 提交 | strict | 用户显式非法组合一律拒绝（API 4xx / CLI 非零 + 明确错误）；内部默认值（如 backend 默认 basis）不得被误判为用户输入（应剥离，至多告警） |
| 历史重跑 / 编辑重算的**未改动字段** | migration | 规范化 + 告警：GFN 下 basis/dispersion/RI 清除；CPCM/SMD→ALPB（ORCA+GFN）；legacy grid 别名映射；不因历史值非法而拒绝 |
| 编辑重算的**本次显式改动字段** | strict | 按"新建"规则严格校验：新改入的非法值**不迁移**、直接拒绝/阻断；改动后与历史同值的按继承处理 |
| 直接调用 cccp（库层） | 默认 strict | 注册表未知枚举 fail-fast；GFN 参数剥离为显式行为并附 warning；迁移上下文必须由调用方显式传入（默认不迁移） |
| 调度器启动子进程 | 显式传递 | 提交/重算时完成校验与规范化，最终配置写入 job spec / scan_config；子进程按最终配置执行；fingerprint 校验"预览 == 最终运行" |

- 迁移字段白名单（仅这些、"未改动"时适用）：`basis`/`dispersion`/`ri`/`aux`（GFN 清除）、`solvent_model`（GFN+ORCA 时 CPCM/SMD→ALPB）、`grid`（legacy 别名）；其余字段在迁移上下文同样严格校验。
- 告警通道：API 响应（validate-method / edit preview）、CLI stderr、任务记录/events（由 T14/T24 落地）。

### Capability vs dependency vs policy（能力 / 依赖 / 策略三分）
> 三个维度分开记录、分开修改；探针（T22/T17）只更新前两列，策略变更必须显式（用户决策）。

| 维度 | 含义 | 记录处 | 更新方式 |
| --- | --- | --- | --- |
| 软件能力 | ORCA/xTB 是否支持该关键字/组合（官方文档 + 实测） | T22 JSON（附文档引用） | 探针实测可修正（需证据） |
| 本机依赖 | 参数文件/二进制等本机部署完整度 | T22 参数文件清单 | 探针实测可修正 |
| 平台策略 | ACP 本轮开放/关闭哪些组合（Q1/Q8/Q9 等） | D8/D15/D16 与本表 | 仅显式决策变更；探针不得自动改写 |

- 本轮已定策略（不变）：ORCA 路径 GFN 溶剂仅 {none, ALPB}；GFN0-xTB 为 xTB-二进制专属、ORCA 路径关闭；GFN+NMR 默认拒绝（除非显式决策开放）。
- 已知记录：ORCA 6.1 外部接口表已列 GFN0-xTB 与 GFN-FF（能力=支持）；本机缺 `param_gfn0-xtb.txt` 属依赖缺口而非"不支持"；GFN-FF 本机实测完成、GFN2-xTB NMR rc=0 但屏蔽解析为空（能力缺口，待产物级证据）。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: 纯函数 **TDD**（`calculate_dihedral`、关键字注册表、溶剂映射表）；集成 **tests-after**；框架 **pytest**（默认 `-m "not slow"`，真实二进制用 `--run-slow`）。
- 测试随行为同提交：任何改变行为的 todo 必须同步更新其锁定测试（禁止把测试更新集中到 Wave 5）；CI 在每波结束时全绿。
- 引用规范：todo 行号仅作导航，执行前以符号名（函数/常量）在 HEAD 复核；行号漂移以符号为准。
- 反模式 #33 契约：注册表每个枚举值必须通过 emit→parse 往返（任意大小写）+ catalog 规范化 + API 三层 choke point 测试。
- 真实 ORCA：本机 `/home/xieningke/orca611/orca`（6.1.1）；二进制缺失时 skip（沿用仓库 real-binary 约定）。
- Evidence: `.omo/evidence/task-<N>-cccp-correctness-hardening.<ext>`（本会话无 ulw-loop，统一用 `.omo/evidence/`）。
- 质量基线：首次修改前由 T26 采集（工具版本 + 全量诊断输出），T20 以 rule/site 级全量 diff 对比（counts 不足以证明"无新增"）。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1 — cccp 基座（T26, T1-T5, T21-T22）**：质量基线采集（首次修改前，T26）、几何修复、残差环绕、注册表建立、ORCA 早期探针、家族识别、映射迁移、统一渲染器。
- **Wave 2 — cccp GFN/溶剂（T6-T10）**：GFN 剥离集中化、溶剂语义、NMR、xTB 溶剂名、后端默认基组泄漏。
- **Wave 3 — acp 数据/校验（T11-T14）**：METHOD_META GFN、家族化选项、single_point grid、校验与提交期拦截。
- **Wave 4 — 前端（T15-T16, T23-T24）**：方法族联动隐藏/锁定、copy-scan-level 防传染、序列化防线/横幅/迁移提示/硬编码审计、校验告警链路（4 个 todo；同一 HTML 文件内串行 T16 → T23 → T24）。
- **Wave 5 — 验证（T17-T20, T25）**：真实 ORCA 冒烟矩阵、E2E 重跑、旧测试/文档同步、质量门、浏览器交互验收。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 几何修复 | T26 | T17,T19,T21 | T2 |
| T2 注册表 | T22,T26 | T3,T4,T5,T6,T9,T11,T12 | T1 |
| T3 家族识别 | T2 | T6,T10 | T4 |
| T4 映射迁移 | T2,T22 | T5 | T3 |
| T5 统一渲染器 | T2,T4 | T6,T7,T8,T10 | — |
| T6 GFN 剥离集中化 | T2,T3,T5 | T7,T8,T10,T11 | T9 |
| T7 溶剂语义 | T5,T6,T22 | T8,T11,T17 | T9 |
| T8 NMR GFN | T5,T6,T7 | T19 | T9 |
| T9 xTB 溶剂名 | T2 | T19 | T6,T7,T8 |
| T10 后端默认基组 | T3,T5,T6 | T11,T17 | T9 |
| T11 METHOD_META GFN | T2,T6,T7,T10 | T12,T13,T15 | — |
| T12 scan 溶剂/single_point grid | T2,T11 | T13,T15 | — |
| T13 校验层 | T11,T12,T22 | T14,T17 | T15 |
| T14 提交/运行期校验 | T13 | T18,T19,T23,T24 | T15,T16 |
| T15 前端门控 | T11,T12 | T16 | T13,T14 |
| T16 copy 防传染 | T15 | T23 | T14 |
| T17 ORCA 冒烟矩阵 | T1,T5,T6,T7,T10,T13 | T18,T20 | T19 |
| T18 E2E 重跑 | T14,T17,T21 | T20 | T19 |
| T19 旧测试/文档同步 | T1,T8,T9,T14,T16,T21,T23,T24 | T20 | T17,T18 |
| T20 质量门 | T17,T18,T19,T25,T26 | — | — |
| T21 残差环绕 | T1 | T18 | T22 |
| T22 ORCA 探针 | T26 | T2,T4,T7,T13 | T1 |
| T23 前端硬化（序列化/横幅/审计） | T14,T15,T16 | T24 | — |
| T24 告警链路 | T14,T23 | T19,T25 | — |
| T25 浏览器交互验收 | T15,T16,T23,T24 | T20 | T19 |
| T26 质量基线 | — | T1,T2,T20,T22 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. cccp: correct `GeometryUtils.calculate_dihedral` to the standard IUPAC convention + regression tests
  What to do: change the first bond vector from `coords[atom_j] - coords[atom_i]` to `coords[atom_i] - coords[atom_j]` (keep the v/w projection + arctan2 structure); add `tests/test_cccp_geometry_tools.py` asserting cis=0, trans=±180, ±30/±150 and a ≤1e-9 cross-check vs an independent reference (RDKit `rdMolTransforms.GetDihedralDeg`, not a copy of the same formula); NOTE: the pre-fix function is a **180° PHASE OFFSET** (measured = standard + 180°), NOT a sign flip — every measured dihedral shifts by 180° after the fix (Oracle rr-cccp-02 #5); the two src callers need no change for the shift itself, but the PES residual math DOES need angular wrapping — see T21 (do not skip it). [user review item 5] State permutation semantics correctly: FULL reversal (l,k,j,i) PRESERVES the angle; swapping the two endpoint atoms while keeping the j–k axis (l,j,k,i) NEGATES it — pin whichever permutation the test uses and compare with a PERIODIC difference (mod 360) so +180° and −180° never count as a mismatch.
  Must NOT do: touch `calculate_distance`/`calculate_angle`; change call-site atom ordering; alter `ScanFrame` fields.
  Parallelization: Wave 1 | Blocked by: T26 | Blocks: T17,T19,T21
  References: src/cccp/utils/geometry_tools.py:72-102 (`b1` at :89, arctan2 at :99-102); src/acp/calculations/pes/scan.py:793-801; src/acp/calculations/pes/scan_snapshot.py:68-88; zero existing tests (grep `calculate_dihedral` in tests/ = no hits)
  Acceptance criteria: `pytest tests/test_cccp_geometry_tools.py tests/test_pes_search.py tests/test_pes_constraint_gate.py -q` all pass; new tests assert `abs(fn - reference) < 1e-9` over ±30/±150/cis/trans cases. NOTE: T1+T21 together form the geometry fix — after both land, re-run this acceptance plus the historical 90-frame check before declaring geometry done (do not accept T1 alone as the fix; user review 2026-09 execution adjustment #3).
  QA scenarios: happy - synthetic planar 4-atom torsions in the new test file (`pytest -q`); failure - full reversal preserves the angle while endpoint-swap negates it, both compared modulo 360 (asserted in-test); the historical 90-frame RDKit re-measure (90/90 within the 1.0° tolerance using periodic residuals; max ≈1e-4°, user review item 5) is recorded as supporting evidence. Evidence `.omo/evidence/task-1-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): use standard dihedral convention in GeometryUtils.calculate_dihedral
  Recommended task executor category: deep-low - geometry-convention math with periodic-comparison tests; single source file + focused test file

- [x] 2. cccp: create the unified keyword registry module (method family × parameter) + unit tests
  What to do: add `src/cccp/qc/keyword_registry.py` exposing: `method_family(method)` (conventional_dft / composite_3c / gfn / gfnff / unknown) and **`resolve_implementation(method, *, engine)`** — the SAME method resolves differently by engine: `("GFN2-xTB", "orca") → "orca_external_xtb"`, `("GFN2-xTB", "xtb") → "xtb_binary"`, `("Native-GFN2-xTB", "orca") → "orca_native"`; invalid (method, engine) combos raise a typed error whose message names method + engine + legal implementations (user review 2026-09 item 1: `GFN2-xTB`/`GFN-FF`/`GFN0-xTB` are ORCA's EXTERNAL xTB-interface keywords, `Native-GFN2-xTB` etc. are the NATIVE implementation, standalone xTB binary is a third path; this effort keeps the existing external-interface path — native is modeled but out of execution scope; applicability rules are keyed by (family, implementation)). Per-domain tables (`opt_level`, `scf_convergence`, `scf_strategy`, `grid` incl. legacy aliases SG1→DefGrid1, Fine→DefGrid2, UltraFine→DefGrid3, SuperFine→DefGrid3, `dispersion`) **including an explicit `normal → None` no-op entry for every catalog-declared enum value** (Oracle rr-cccp-04 #2: `normal` is catalog-legit and handled today only as a silent no-op; naive fail-fast breaks the T18 E2E whose config carries `scan_optimizer.convergence: normal`); per-family × implementation applicability rules (ORCA external xTB: no basis/dispersion/grid/RI/aux; solvent models {none,ALPB} under ORCA is PLATFORM POLICY, explicitly distinguished from software capability); one `resolve(domain, value, *, family, implementation)` API (implementation comes from `resolve_implementation(method, engine)`) returning `(canonical_or_none, warning_or_none)` and raising a typed error for unknown enum values (Q5) while passing free-form values through unchanged. Enum lookup is case-insensitive (canonicalize to the registry spelling; anti-pattern #33). T2 is BLOCKED BY T22: run the probe first and finalize the GFN applicability tables only after its verdicts are recorded (Oracle rr-cccp-02 #1).
  Must NOT do: import `acp`; perform I/O or route-line assembly; duplicate ORCA/xTB-specific rendering.
  Parallelization: Wave 1 | Blocked by: T22,T26 | Blocks: T3,T4,T5,T6,T9,T11,T12
  References: src/cccp/qc/interfaces/orca.py:429-477; src/cccp/qc/interfaces/orca_ts.py:31-36; src/cccp/utils/solvent_map.py:4-34; AGENTS.md anti-pattern #33 (free-form never folded); ORCA facts in draft F11-F12.
  Acceptance criteria: `pytest tests/test_cccp_keyword_registry.py -q` passes; asserts GFN forbids basis/dispersion/grid and allows only {none,ALPB}; asserts EVERY catalog-declared enum value (incl. `normal`) resolves (no-op where applicable) — not only that bogus values raise (Oracle rr-cccp-04 #2); `resolve_implementation` covers GFN2-xTB (orca→orca_external_xtb, xtb→xtb_binary) and Native-GFN2-xTB (orca→orca_native), and invalid combos raise with method+engine+legal list; unknown enum raises with field+legal-values in the message; free-form (`def2-TZVP`, `DMSO`) passes unchanged; enum lookup is case-insensitive (`defgrid3` → `DefGrid3`); this suite also loads the T22 probe JSON and programmatically asserts every verdict maps to a registry/applicability decision (acceptance moved here from T22 because the registry exists only after T2 — user review 2026-09; Oracle rr-cccp-03 #1 applies here).
  QA scenarios: happy - matrix parametrization over families × domains (`pytest -q`); failure - unknown enum value raises `KeywordValueError` (asserted). Evidence `.omo/evidence/task-2-cccp-correctness-hardening.txt`
  Commit: Y | feat(cccp): add unified keyword registry as the single source of truth
  Recommended task executor category: deep-high - new cross-package registry API that acp consumes; the contract shape is the keystone

- [x] 3. cccp: make method-family detection registry-driven (covers GFN-FF and GFN0-xTB)
  What to do: replace the suffix heuristic inside `_is_orca_gfn_xtb_method` with `method_family(method)` from the registry. NOTE (Oracle rr-cccp-03 #5): HEAD's heuristic already classifies `GFN0-xTB` but MISSES `GFN-FF`; keep both as regression assertions while remaining backward-compatible for `GFN2-xTB`/`GFN1-xTB` (any case/spacing). Also expose `resolve_implementation(method, *, engine)` from the registry so downstream consumers (T7/T9/T11/T13/T22) can distinguish ORCA-external-xTB vs ORCA-native vs standalone-xTB (user review 2026-09 item 1).
  Must NOT do: match arbitrary `GFN*` strings outside the registry table.
  Parallelization: Wave 1 | Blocked by: T2 | Blocks: T6,T10
  References: src/cccp/qc/interfaces/orca.py:422-426 (current `endswith("-XTB")` gap); src/acp/catalog.py:1231-1248 (GFN-FF offered); src/acp/calculations/levels.py:41 (GFN_METHODS omits GFN0-xTB).
  Acceptance criteria: `pytest tests/test_cccp_keyword_registry.py -q` includes `GFN-FF`/`GFN0-xTB` classified as GFN (family `gfn`/`gfnff`); `resolve_implementation` distinguishes GFN2-xTB (orca→orca_external_xtb, xtb→xtb_binary) vs Native-GFN2-xTB (orca→orca_native) and rejects invalid combos; `pytest tests/test_acp_pes_dft_scan.py -q` still passes.
  QA scenarios: happy - classification table test; failure - a non-GFN method (`B97-3c`, `r2SCAN-3c`) must return non-GFN. Evidence `.omo/evidence/task-3-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): classify GFN-FF/GFN0-xTB through the keyword registry
  Recommended task executor category: quick - swap one heuristic for a registry call; single file

- [x] 4. cccp: migrate all ORCA/TS keyword maps into the registry and delete duplicates
  What to do: move `_OPT_LEVEL_MAP`, `_SCF_CONVERGENCE_MAP`, `_SCF_STRATEGY_MAP`, `_GRID_KEYWORD_MAP`, `_DISPERSION_KEYWORD_MAP` (orca.py) and `_OPT_LEVEL_KEYWORDS` (orca_ts.py) onto registry tables; delete the duplicate map; make all lookups go through `resolve()` so unknown-value behavior is uniform.
  Must NOT do: leave a compatibility shim that silently drops unknown values (today `_OPT_LEVEL_MAP` drops, `orca_ts` raises - pick the registry rule: fail-fast).
  Parallelization: Wave 1 | Blocked by: T2,T22 | Blocks: T5
  References: src/cccp/qc/interfaces/orca.py:429-466,1312-1363; src/cccp/qc/interfaces/orca_ts.py:31-36,352-361; draft findings F2.
  Acceptance criteria: `pytest tests/test_cccp_keyword_registry.py tests/test_keyword_parser_parity.py -q`; grep asserts the deleted map names no longer exist outside the registry; every catalog-declared enum value (incl. `normal` for opt_level/scf_convergence/scf_strategy) resolves to its documented no-op/keyword and only bogus values raise (Oracle rr-cccp-04 #2).
  QA scenarios: happy - every catalog value resolves canonically; failure - a bogus opt level raises (not dropped). Evidence `.omo/evidence/task-4-cccp-correctness-hardening.txt`
  Commit: Y | refactor(cccp): route ORCA/TS keyword maps through the registry
  Recommended task executor category: unspecified-low - mechanical re-pointing of existing maps onto the registry across two files

- [x] 5. cccp: funnel all 8 `!`-simple-input assembly points through one renderer (applicability + fail-fast)
  What to do: add a single route-renderer that consumes the registry (family applicability + alias/unknown policy) and use it at all identified assembly points: orca.py:1434, 1436, 2792-2793 (casscf), 3346/3351/3353 (NMR), orca_ts.py:363 (ts_opt_route), 459 (irc_route). Remove per-site ad-hoc token handling (e.g. `ts_opt_route` grid/scf verbatim appends at orca_ts.py:344-347).
  Must NOT do: keep a raw bypass that writes user grid/dispersion values verbatim; change free-form `route_extras` passthrough semantics.
  Parallelization: Wave 1 | Blocked by: T2,T4 | Blocks: T6,T7,T8,T10
  References: assembly points from cccp audit (orca.py:1434,1436,2792,3346-3353; orca_ts.py:363,459); current passthrough bug orca.py:1348,1358 and orca_ts.py:344-345; draft F2.
  Acceptance criteria: `pytest tests/test_orca_basis_block.py tests/test_cccp_orca_ts_extensions.py tests/test_acp_pes_dft_scan.py -q`; a unit test feeding `grid="UltraFine"` yields `DefGrid3` + warning (never `UltraFine` on the `!` line).
  QA scenarios: happy - generated inputs contain only legal tokens; failure - bogus grid raises before subprocess. Evidence `.omo/evidence/task-5-cccp-correctness-hardening.txt`
  Commit: Y | refactor(cccp): single route renderer for every simple-input line
  Recommended task executor category: deep-low - structural pivot (8 assembly sites into one renderer); evidence leaves one right answer

- [x] 21. acp/cccp: wrap angular constraint residuals to [-180,180) in PES frame extraction
  What to do: in `_extract_frames`, the residual `value - target` is NOT angle-wrapped (scan.py:688); after the T1 fix, targets >180° (the historical scan runs 1°→359°) yield residual ≈ ±360° and the candidate gate drops those frames. Wrap residuals for `angle`/`dihedral` kinds to [-180,180) (e.g. `((v - t + 180) % 360) - 180`) before storing `constraint_residuals` and before the tolerance check (scan.py:98-105, target interpolation scan.py:736-745); distances stay unwrapped. Add a regression test with a 359° target asserting residual ≈ 0 and `constraint_residual_ok=True` after the fix.
  Must NOT do: touch `scan_snapshot.py` (measurement-only, no residuals); change `_measure_scan_coordinate` semantics; alter the 1.0° dihedral tolerance without the user.
  Parallelization: Wave 1 | Blocked by: T1 | Blocks: T18
  References: src/acp/calculations/pes/scan.py:680-693 (residual), :98-105 (tolerances), :348-379 (candidate gate), :736-745 (target interpolation); Oracle rr-cccp-01 finding B1.
  Acceptance criteria: `pytest tests/test_pes_constraint_gate.py -q` + new tests: target=359.0°, measured=-1.0° ⇒ residual≈0 ⇒ gate passes; target=200° case likewise. T1+T21 jointly constitute the geometry fix (see T1 acceptance) — accept only after both pass.
  QA scenarios: happy - a 90-frame profile has zero invalid constraint frames after the fix; failure - the raw unwrapped ±360° residual must not appear (asserted). Evidence `.omo/evidence/task-21-cccp-correctness-hardening.txt`
  Commit: Y | fix(pes): wrap angular constraint residuals to [-180,180)
  Recommended task executor category: quick - one wrap expression + regression test in a single file

- [x] 22. cccp: early empirical ORCA probe to freeze registry/applicability defaults (Wave-1 gate)
  What to do: before finalizing the registry applicability tables (T2) and rejection lists (T4/T7/T13), run a minimal real-ORCA probe (`/home/xieningke/orca611/orca`, tiny 3-atom input, seconds each) for: `! GFN2-xTB`, `! Native-GFN2-xTB` (implementation contrast — user review item 1), `! GFN2-xTB ALPB(water)`, `! GFN2-xTB GBSA(water)`, `! GFN2-xTB def2-SVP`, `! GFN2-xTB D4`, `! GFN2-xTB DefGrid3`, `! GFN2-xTB TightSCF`, `! GFN-FF`, `! GFN0-xTB`, `! GFN2-xTB NMR`, `! DEFGRID3`, `! UltraFine`. [user review item 2] Classify EVERY case as exactly one of: syntax error / missing dependency / calculation failure / valid completion — a return code of 0 is NOT sufficient (user probes: GFN0-xTB exits 0 after aborting due to a missing `param_gfn0-xtb.txt`; GFN2-xTB NMR exits 0 but yields no parsed shielding). Record the ORCA binary path+sha256, the `otool_xtb` version, and the parameter-file inventory. Also record per case the three dimensions: software capability (documented + observed), local dependency state, and observed outcome class — capability/dependency updates the record, NOT platform policy (see Scope; ORCA 6.1's external-interface table lists GFN0-xTB and GFN-FF — do not infer "unsupported" from a missing local parameter file). Write results as MACHINE-READABLE JSON (`case → keyword, outcome class, first error line, energy/shielding artifact`) for the consumer assertion in T2 (Oracle rr-cccp-04 #5); adjust T2/T4/T7/T13 capability records to the verdicts.
  Must NOT do: require network; run anything heavier than single points; skip the recording.
  Parallelization: Wave 1 | Blocked by: T26 | Blocks: T2,T4,T7,T13 (verdict consumption) | parallel with T1
  References: prior empirical grid evidence (UltraFine/Grid5 rejected, DEFGRID3 accepted); ORCA manual §3.5.3 native xTB; Oracle rr-cccp-01 M2/M3; user review item 2 probes (six small ORCA runs — re-run rather than trusting the ephemeral temp path); draft F11/F12.
  Acceptance criteria: JSON evidence covers all 13 cases with outcome CLASS (not just accept/reject), first error line, and artifact evidence (energy for SP; parsed shielding tensors for NMR); the ORCA binary + otool_xtb version + parameter-file inventory + capability/dependency annotations are recorded. The registry-consistency assertion is NOT part of T22 (the registry does not exist yet when T22 runs — acceptance split per user review 2026-09 execution adjustment #2): it moves to T2's acceptance, which loads this JSON and asserts each verdict maps to a registry/applicability decision programmatically (Oracle rr-cccp-03 #1 then applies to T2).
  QA scenarios: happy - probe completes <5 min with the table recorded; failure - ORCA binary missing ⇒ record skip reason and mark dependent todos blocked for the user. Evidence `.omo/evidence/task-22-cccp-correctness-hardening.txt`
  Commit: Y | test(cccp): empirical ORCA keyword probe gating registry defaults
  Recommended task executor category: unspecified-high - empirical probe protocol with machine-readable outcome evidence

- [x] 6. cccp: centralize GFN parameter stripping across all entry points
  What to do: extend the renderer (T5) + `_build_input_blocks` so basis/dispersion/grid/RI/aux (and any `%basis` block) are stripped for GFN family methods everywhere. NOTE: `ts_opt_route`/`irc_route` bypass `_build_input_blocks` entirely (orca.py:3095-3104, 3243-3248) — they are covered through T5's renderer, do NOT claim a `_build_input_blocks`-only fix; preserve the existing grid/dispersion strip (orca.py:1345-1363); delete the per-call-site basis blanking inside `relaxed_scan` / `_run_synchronous_relaxed_scan` (symbols, lines drift).
  Must NOT do: strip for conventional/composite DFT; block or alter `route_extras` free text (only enumerated params are governed).
  Parallelization: Wave 2 | Blocked by: T2,T3,T5 | Blocks: T7,T8,T10,T11 | same-file serialization: T6 → T7 → T8
  References: src/cccp/qc/interfaces/orca.py:1345-1363,1409-1422,1436,2052-2062,2234-2248,2550,1827-1828,2660-2661,3079,3225; acp/backends/orca.py:36-38; draft F7/F8.
  Acceptance criteria: `pytest tests/test_acp_pes_dft_scan.py -q` plus new parametrized tests asserting no basis token or `%basis` block appears for GFN across all entry points.
  QA scenarios: happy - GFN inputs contain `! GFN2-xTB [Opt|SP|Freq]` only; failure - a library-level explicit `basis=` with GFN is stripped with a warning (asserted), while strict entries (API/CLI/new submissions) reject the same combination upstream via T13/T14 (entry-behavior contract, see Scope). Evidence `.omo/evidence/task-6-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): strip DFT-only parameters for GFN methods in every entry point
  Recommended task executor category: deep-low - multi-entry behavior unification inside orca.py; spec is precise

- [x] 7. cccp: unify GFN solvent semantics (ALPB-only under ORCA; remove GBSA and silent coercion)
  What to do: make the ORCA renderer emit `ALPB(solvent)` for GFN when the model is ALPB, ignore/deny `none`, and **reject GBSA under ORCA** (not an ORCA keyword); delete the silent `if normalized_model not in {"ALPB","GBSA"}: = "ALPB"` coercion in `_orca_scan_route_settings:870-875` and the `%cpcm`-for-GFN path; align `transition_state_opt`/`irc` (`orca_ts.py:348-350,456-458`) and NMR with the same rule; keep ALPB/GBSA valid for the standalone xTB binary. T22's probe verdicts update the CAPABILITY/DEPENDENCY records only; rules are keyed by `resolve_implementation(method, engine)` (user review 2026-09 item 1): ORCA external xTB vs ORCA native may differ; "ALPB-only under ORCA" is our PLATFORM POLICY, not a claim about software capability — record the three-way distinction (capability/dependency/policy, see Scope) in code comments and docs; probe results never auto-rewrite policy.
  Must NOT do: silently rewrite a user model; emit GBSA/CPCM/SMD for ORCA GFN (decision Q1: only {none,ALPB}).
  Parallelization: Wave 2 | Blocked by: T5,T6,T22 | Blocks: T8,T11,T17 | same-file serialization: T6 → T7 → T8
  References: src/cccp/qc/interfaces/orca.py:855-876,1532-1539; src/cccp/qc/interfaces/orca_ts.py:348-350,456-458; draft F9/F11 (ORCA docs: native xTB ALPB; GBSA not an ORCA keyword).
  Acceptance criteria: `pytest tests/test_cccp_orca_ts_extensions.py tests/test_acp_pes_dft_scan.py -q`; new tests assert `ALPB(water)` for GFN+ALPB, `none` emits nothing, and GBSA under ORCA raises a clear error.
  QA scenarios: happy - route line contains `ALPB(Water)`; failure - `solvent_model="gbsa"` with GFN raises `KeywordValueError`. Evidence `.omo/evidence/task-7-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): ALPB-only GFN solvation under ORCA, reject GBSA
  Recommended task executor category: deep-low - solvent-rule unification keyed by implementation; one subsystem, several sites

- [x] 8. cccp: stop force-filling a DFT basis for GFN NMR inputs
  What to do: remove the `if not _basis: _basis = "6-311G(d)"` fill and route NMR through the registry/renderer. [Q9] default policy = reject GFN+NMR at validation with a clear message ("NMR 仅支持 DFT/复合方法"), while keeping a reserved switch that allows `! GFN2-xTB NMR` if T17's real-ORCA test proves support (default stays reject); implement the switch as a module-level constant; T17 records the artifact-level capability, and opening it remains an explicit policy decision (with test/doc updates when decided) — a probe verdict never auto-flips policy; if the switch ever flips to allow, GFN NMR solvent must route through ALPB (never CPCM/SMD) — implement that in the switch's code path (Oracle rr-cccp-03 #7). [user review item 2] The switch stays CLOSED until an artifact-level acceptance shows the existing `NmrShieldingParser` returns NON-EMPTY shielding tensors for the target atoms — "ORCA exited 0" or "syntax accepted" is NOT sufficient (user probes: `! GFN2-xTB NMR` completes but the parser yields an empty dict). Keep DFT NMR behavior unchanged.
  Must NOT do: change NMR behavior for DFT methods; emit a DFT basis for GFN NMR; silently allow GFN+NMR without the T17 evidence.
  Parallelization: Wave 2 | Blocked by: T5,T6,T7 | Blocks: T19 | same-file serialization: T6 → T7 → T8
  References: src/cccp/qc/interfaces/orca.py:3313-3371 (force-fill :3337-3338; route :3346; solvent :3351-3353); draft F7.
  Acceptance criteria: `pytest tests/test_acp_workflows_nmr*.py -q`; new unit test asserts no `6-311G(d)` for GFN and ALPB route when solvent set.
  QA scenarios: happy - GFN NMR input has no basis; failure - DFT NMR still emits its basis unchanged. Evidence `.omo/evidence/task-8-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): no implicit DFT basis for GFN NMR input
  Recommended task executor category: quick - remove one default and reroute; single file

- [x] 9. cccp: fix xTB-binary solvent-name mapping (canonical names + alias resolution)
  What to do: split into TWO layers (user review item 4): (a) name alias resolution (`SOLVENT_ALIASES`-style: `dichloromethane`/`dcm`→`ch2cl2`, `chloroform`→`chcl3`, `meacn`→`acetonitrile`, keeping case/whitespace folding) and (b) combination legality validated as implementation × solvent model × solvent — ALPB and GBSA have DIFFERENT official solvent sets, GFN0 has no ALPB parameterization. Use the complete official lists; the draft list omitted documented solvents (aniline, benzaldehyde, phenol, woctanol, …). Unknown names raise with the legal list in the message (not raw passthrough); apply to xtb.py/xtb_path.py/crest.py/molclus.py/xtb_thermo.py. The validation entry point must receive the FINAL EFFECTIVE method, implementation (`resolve_implementation`) and solvent model — including call-time overrides of initialization defaults — never the initialization defaults alone (user review 2026-09 item 1).
  Must NOT do: change ORCA `orca_smd_solvent` (long names already valid for ORCA ALPB/SMD per Table 3.24, case-insensitive).
  Parallelization: Wave 2 | Blocked by: T2 | Blocks: T19
  References: src/cccp/utils/solvent_map.py:4-34; src/cccp/qc/interfaces/xtb.py:103-110; xtb_path.py:104-110; crest.py:84-85; molclus.py:63-64; xtb_thermo.py:171-172; xTB docs canonical strings (draft F10/F11).
  Acceptance criteria: new `pytest tests/test_cccp_solvent_map.py -q` asserts the mappings above, that an unknown name raises with the legal list, and combination legality per model AND method: ALPB set = {acetone, acetonitrile, aniline, benzaldehyde, benzene, ch2cl2, chcl3, cs2, dioxane, dmf, dmso, ether, ethylacetate, furane, hexadecane, hexane, methanol, nitromethane, octanol, woctanol, phenol, toluene, thf, water}; GBSA set = {acetone, acetonitrile, benzene (GFN1-only), ch2cl2, chcl3, cs2, dmf (GFN2-only), dmso, ether, h2o, methanol, n-hexane (GFN2-only), thf, toluene}; GFN0+ALPB rejected; assert GFN1+GBSA(benzene) legal, GFN2+GBSA(benzene) rejected, GFN2+GBSA(dmf) legal, GFN1+GBSA(dmf) rejected (Oracle rr-cccp-05 #4); a slow-gated real-xTB case exercises at least 3 solvents across GFN1/GFN2 (user review item 4); `pytest tests/test_qc_interfaces_crest.py tests/test_cccp_xtb_path.py -q` pass; a call-time override that differs from the initialization default is validated/rendered by the OVERRIDE (asserted).
  QA scenarios: happy - `--alpb ch2cl2` emitted for `dichloromethane`; failure - `solvent="unobtainium"` raises. Evidence `.omo/evidence/task-9-cccp-correctness-hardening.txt`
  Commit: Y | fix(cccp): canonical xTB solvent-name mapping
  Recommended task executor category: unspecified-low - data tables plus alias/legality assertions in one module

- [x] 10. cccp/acp: stop leaking the backend's default DFT basis onto GFN methods
  What to do: ensure `ORCABackend.__init__` does not `setdefault("basis", ...)` for GFN methods (or that the renderer ignores it), so `single_point`/`optimize` through the backend cannot inject `def2-TZVPP` into a GFN route.
  Must NOT do: change defaults for DFT methods.
  Parallelization: Wave 2 | Blocked by: T3,T5,T6 | Blocks: T11,T17
  References: src/acp/backends/orca.py:27-43 (defaults at :36-38); src/cccp/qc/interfaces/orca.py:1436.
  Acceptance criteria: `pytest tests/test_acp_backends.py tests/test_acp_backends_batch.py -q`; new test: backend-level GFN single_point emits no basis token.
  QA scenarios: happy - backend GFN SP input has no basis; failure - backend DFT SP still gets `def2-TZVPP`. Evidence `.omo/evidence/task-10-cccp-correctness-hardening.txt`
  Commit: Y | fix(acp): don't leak default DFT basis into GFN backend calls
  Recommended task executor category: quick - backend default guard; single file + test

- [x] 11. acp: add GFN entries to METHOD_META + family-aware option resolution + minimal grid/SCF rule
  What to do: add GFN method entries to `METHOD_META` (family per the registry: `gfn` for GFN2/GFN1 and `gfnff` for GFN-FF — ONE spelling shared with `docs/ACP_Scan_OptTS_GFN_DevPlan.md` §A2.1 and its `isGfn` check; `implementation` via `resolve_implementation(method, engine)`; **`basis: ()` for ALL GFN entries** so `_derive_functional_options_map` yields `[]` and T19's `test_functional_options_map_gfn_basis_empty` passes — `[""]` would contradict both the dev doc and that test (Oracle rr-cccp-04 #1); `builtin_dispersion` GFN2→D4 / GFN1→D3 / GFN-FF→builtin; **`ri_support: "composite"`** — the EXISTING key/values consumers read (`supports_ri` does not exist; Oracle rr-cccp-02 #2), matching dev doc §A2.1; `solvent_models: ["none","ALPB"]`); make `_resolve_field_options` consult the family so `scan_optimizer_basis`/`_dispersion` resolve to the locked/empty set; add one minimal "field unavailable for family" rule covering `grid`/`scf_*`. [Oracle M1] Do NOT re-encode a second authority: **derive** family/applicability/solvent-models/allowed-grid from the cccp registry (acp→cccp import direction is safe; cccp must not import acp at import time) and keep only presentation metadata in METHOD_META; add a derivation consistency test.
  Must NOT do: build a generic depends_on pipeline (Q2); change conventional-DFT/3c option sets.
  Parallelization: Wave 3 | Blocked by: T2,T6,T7,T10 | Blocks: T12,T13,T15
  References: src/acp/catalog.py:468-607,628,4412-4478,4790-4997; registry module (T2); draft F4/F5/F10/F13.
  Acceptance criteria: `pytest tests/test_acp_catalog.py tests/test_acp_pes_dft_scan.py -q`; new asserts: GFN resolves basis `[]` (empty/locked — consistent with dev doc §A2.1 and the T19-named test), dispersion locked, RI `none`, solvent models `["none","ALPB"]`, METHOD_META GFN `ri_support == "composite"`, and `canonical_level` clears GFN basis/dispersion/RI; the registry↔METHOD_META consistency test passes for family AND implementation.
  QA scenarios: happy - catalog payload shows locked/empty GFN fields; failure - alphabetical bogus family raises in the consistency test. Evidence `.omo/evidence/task-11-cccp-correctness-hardening.txt`
  Commit: Y | feat(acp): method-family-aware field gating via METHOD_META
  Recommended task executor category: deep-low - catalog resolution changes plus registry-derivation consistency test

- [x] 12. acp: family-aware scan solvent options + ORCA-native single_point grid
  What to do: give `scan_optimizer_solvent_model` family-aware options (GFN → `["none","ALPB"]`; DFT unchanged) and change the shared `grid` field to ORCA-native `["DefGrid1","DefGrid2","DefGrid3"]` with default `""`; route legacy aliases through the registry with an explicit migration warning (Q4); update pes_scan profile defaults accordingly, AND sweep every other `"grid"` default in `src/acp/catalog.py` (verified sites: 2256, 2301, 2346, 2447, 2718, 2731, 3131, 3679, 3752, 3934, 4065 plus the field default at :777) — assert zero residual `UltraFine` defaults (Momus rr-cccp-01 P1). [Oracle rr-cccp-05 #2] ALSO migrate `option_labels_zh` (catalog.py:772-775): replace the SG1/Fine/UltraFine/SuperFine keys with DefGrid1/2/3 zh labels — the frontend resolves zh labels BY OPTION VALUE (frontend:27371-27373), so leaving legacy keys yields unlocalized labels AND makes the "no UltraFine anywhere" assertion unsatisfiable.
  Must NOT do: keep `UltraFine` as a default anywhere; silently drop legacy values.
  Parallelization: Wave 3 | Blocked by: T2,T11 | Blocks: T13,T15
  References: src/acp/catalog.py:765-778 (grid),1269-1303 (scan solvent/grid),3105-3119 (profile defaults); registry alias table (T2); draft F3/F12.
  Acceptance criteria: `pytest tests/test_acp_catalog.py tests/test_acp_pes_dft_scan.py -q`; asserts GFN scan solvent options `["none","ALPB"]`, grid default `""`, `UltraFine` maps to `DefGrid3` with warning when supplied, AND a programmatic assertion scoped to the catalog `grid` field's `options`/`default`/`option_labels_zh` keys finds no legacy names (the registry alias table is intentionally allowed to retain them — scope the assertion accordingly; Oracle rr-cccp-05 #2); grep as secondary check.
  QA scenarios: happy - catalog payload reflects new options; failure - unknown grid value rejected. Evidence `.omo/evidence/task-12-cccp-correctness-hardening.txt`
  Commit: Y | feat(acp): ORCA-native grid options and family-aware scan solvation
  Recommended task executor category: quick - catalog data/default/label edits in one file + sweep assertion

- [x] 13. acp: complete GFN + single_point validation in levels
  What to do: [Q7 分层策略] historical/recompute path: `canonical_level` keeps clearing basis/dispersion/RI for GFN but now emits a warning, and the CPCM/SMD→ALPB migration is explicit + warned (no silent rewrite); new-submission path: `validate_level_for_purpose` rejects explicit basis/dispersion/RI and non-{none,ALPB} solvent models for GFN; add a `single_point` purpose and pass the PES single-point level through `canonical_level`/`validate` (today it is never validated). [user review item 3] Make the two contexts EXPLICIT with a defined order: (1) preserve the raw config and detect user-EXPLICIT conflicting fields; (2) strict submission → reject, migration/historical → normalized config + warnings; (3) validate the FINAL config uniformly before execution. Both job creation and edit-recalculate share `_prepare_bond_scan_input` — pass the context explicitly instead of composing existing functions (clear-then-validate would miss new submissions; strict-first would reject historical recalc). GFN0-xTB membership per Q8: POLICY = xTB-binary-only — the ORCA path rejects it (registry classifies it in the GFN family); ORCA 6.1's external-interface table lists GFN0-xTB (capability = supported), and the local missing `param_gfn0-xtb.txt` is a DEPENDENCY gap — neither alters the policy (capability/dependency/policy split, see Scope); record both in the rejection message/docs. GFN-FF on the ORCA path: capability documented (external-interface table; user probe completed), so the catalog offer at 1233/1236 stands and GFN family rules apply (T3/T6/T7); T22 records the dependency status. The scan_optimizer option set is unchanged, and `_METHOD_ALIASES` `"gfn0": "GFN-FF"` is corrected to `"GFN0-xTB"` with a test. Entry-context semantics follow the Scope entry-behavior contract (unchanged fields migrate; user-changed fields strict).
  Must NOT do: loosen composite-3c validation; break legacy default profiles; hard-reject historical specs on the recompute path (warn + canonicalize instead).
  Parallelization: Wave 3 | Blocked by: T11,T12,T22 | Blocks: T14,T17
  References: src/acp/calculations/levels.py:41,144-218,240-302; src/acp/calculations/pes/contracts.py:330-369,786-811; src/acp/calculations/pes/scan.py:489-505; draft F6.
  Acceptance criteria: `pytest tests/test_acp_pes_dft_scan.py tests/test_protocol_levels.py -q`; new asserts: GFN+basis raises; GFN+ALPB passes; single_point level is validated.
  QA scenarios: happy - valid GFN+ALPB passes; failure - GFN+`smd` raises with a clear message. Evidence `.omo/evidence/task-13-cccp-correctness-hardening.txt`
  Commit: Y | fix(acp): validate GFN and single-point levels
  Recommended task executor category: deep-low - strict-vs-migration validation semantics across levels/contracts

- [x] 14. acp: enforce method validation at submit/run time (not only the advisory endpoint)
  What to do: ensure PES job creation/runner path runs `normalize_and_validate_method_config` (or the level validators) and fails fast with actionable errors, so invalid GFN combinations cannot reach cccp even if the wizard validated them earlier (submission currently does not re-run validation). New-submission semantics per Q7: hard reject; the recompute/historical lane stays warn+canonicalize (T13); field-level strict/migration semantics follow the Scope entry-behavior contract (用户改动字段=严格；未改动字段=迁移). [user review item 3] Warnings must surface beyond `/validate-method`: the edit-recalculate preview, CLI output, and the job record/events must carry them — logger-only is insufficient (the rerun and direct-CLI paths bypass the endpoint).
  Must NOT do: change BatchOptimize keyword canonicalization behavior.
  Parallelization: Wave 3 | Blocked by: T13 | Blocks: T18,T19,T23,T24
  References: src/acp/api/v1_routes.py:5927-5941 (advisory only), 1704-1734 (batch-only canonicalizer); src/acp/calculations/pes/scan.py:211 (runtime validate_scan_protocol); draft F13/F14.
  Acceptance criteria: `pytest tests/test_acp_api_batch_keyword_case.py tests/test_acp_job_edit.py -q` + new API test asserting an invalid GFN method config is rejected at submit with the path that reaches the runner; warning-channel tests: the edit-recalculate PREVIEW response carries a `warnings` list for migrated configs, the CLI path prints warnings to stderr, and the job record/events persist a warning entry (Oracle rr-cccp-05 #3 — naming the channels in prose is not enough).
  QA scenarios: happy - valid submission accepted; failure - invalid GFN config returns 4xx/raises. Evidence `.omo/evidence/task-14-cccp-correctness-hardening.txt`
  Commit: Y | fix(acp): validate method configs on the submit/run path
  Recommended task executor category: unspecified-high - submit/run-path wiring + warning channels across API/CLI/job records

- [x] 15. frontend: method-family-driven hiding/locking in the wizard + single_point grid
  What to do: extend `CatalogUtils`/`buildFieldRow` so fields flagged unavailable for the selected method family render hidden or locked (basis locked to built-in, dispersion built-in badge, RI locked `none`, grid/SCF hidden), driven entirely by the catalog payload; wire the `single_point` level's grid options/default; ensure the method-change handler re-derives defaults from METHOD_META for GFN AND clears/locks inapplicable fields (F19(b), frontend:27564-27611); make `CatalogUtils.getFieldOptions` resolve method-family-scoped options (F19(c), frontend:22898-23000).
  Must NOT do: hardcode field lists in the frontend; add a generic depends_on engine.
  Parallelization: Wave 4 | Blocked by: T11,T12 | Blocks: T16
  References: frontend/ACP_Workbench_v2.html:22846-22901,26172-26194,27038-27066,27253-27378,27564-27611,29249-29325; draft F13/F14.
  Acceptance criteria: `pytest tests/test_frontend_sync.py -q` (updated contracts) + new sync assertions that GFN fields are hidden/locked and grid options are DefGrid*.
  QA scenarios: happy - switching `scan_optimizer_method` to GFN2-xTB hides/locks the invalid fields; failure - switching back to B97-3c restores them. Evidence `.omo/evidence/task-15-cccp-correctness-hardening.txt`
  Commit: Y | feat(frontend): family-aware field gating in the method wizard
  Recommended task executor category: visual-engineering - wizard field gating is frontend/UI work

- [x] 16. frontend: prevent copy-scan-level from propagating GFN-invalid values
  What to do: the copy action maps `scan_optimizer_method`→`functional` and copies the method too (frontend:27148-27156); therefore reconcile ALL copied fields against the COPIED method's family — not the pre-copy target — so copying a GFN scan level yields a GFN single-point level with basis/dispersion/RI cleared, and assert the final serialized payload (user review item 6; removes the earlier ambiguity).
  Must NOT do: remove the copy feature.
  Parallelization: Wave 4 | Blocked by: T15 | Blocks: T23 | same-file serialization: T16 → T23 → T24
  References: frontend/ACP_Workbench_v2.html:27131-27168; draft F15.
  Acceptance criteria: `pytest tests/test_frontend_sync.py -q` with new assertions on the copy field map / family guard.
  QA scenarios: happy - copy from GFN scan level clears basis/dispersion on an ORCA DFT SP level; failure - valid shared fields still copy. Evidence `.omo/evidence/task-16-cccp-correctness-hardening.txt`
  Commit: Y | fix(frontend): guard copy-scan-level against family-inapplicable fields
  Recommended task executor category: visual-engineering - copy-behavior guard in the workbench UI

- [x] 23. frontend: serialization defense, catalog-driven banner, hydration hint, hardcode audit
  What to do: fold F19(a,d,f,g): (a) `buildPESProtocolFromMethod` must null basis/dispersion/RI for GFN like it does for composite methods (29249-29325, 29262-29267) so invalid values never reach the job payload (note: partly redundant once T11 sets `ri_support: "composite"` — keep as defense-in-depth, Oracle rr-cccp-02 #7); (d) replace the hardcoded `_xtbMethods` banner list (28183) with catalog/registry-driven family info; (f) show the legacy grid migration warning when hydrating drafts (UltraFine→DefGrid3); (g) audit remaining hardcoded method/field lists (`_scopedFieldBase` 27357, supports_custom allowlist 27374, copy map 27148-27156, `"GFN2-xTB"` literals 5644/29262/29441/31120/31233/31682) and make them catalog-driven, leaving literals only in historical-compat branches.
  Must NOT do: build a generic depends_on engine; let frontend-side checks substitute for backend validation; touch non-PES wizards beyond the audit list.
  Parallelization: Wave 4 | Blocked by: T14,T15,T16 | Blocks: T24 | same-file serialization: T16 → T23 → T24
  References: frontend/ACP_Workbench_v2.html lines above; draft F19/F20 (user-added scope).
  Acceptance criteria: extended `pytest tests/test_frontend_sync.py -q` (F20) asserting GFN serialization nulls, banner data-driven markers, hydration-hint markers, and audit sentinels.
  QA scenarios: happy - a GFN config serializes without basis/dispersion; failure - a legacy UltraFine draft shows the migration hint. Evidence `.omo/evidence/task-23-cccp-correctness-hardening.txt`
  Commit: Y | feat(frontend): serialization defense, data-driven banner, migration hint
  Recommended task executor category: visual-engineering - banner/serialization/hydration UI hardening

- [x] 24. acp+frontend: plumb method-validation warnings to the user
  What to do: `ValidateMethodResponse.warnings` ALREADY exists (src/acp/api/v1_schemas.py:1034) and `doValidate`'s catch already returns `warnings: []` (frontend:29050) — the gap is that `normalize_and_validate_method_config` returns only `(normalized, errors)` (catalog.py:4822) and the route never populates the field (v1_routes.py:5937). Implement: extend the normalizer (or add `collect_method_warnings`) to surface Q7 migration/canonicalize warnings and legacy alias-mapping warnings; populate `ValidateMethodResponse.warnings` at the validate-method route; render warnings in `doValidate` + the wizard summary (frontend:29020-29055).
  Must NOT do: re-add a duplicate `warnings` field; surface warnings only via logger.
  Parallelization: Wave 4 | Blocked by: T14,T23 | Blocks: T19,T25 | same-file serialization: after T23
  References: src/acp/api/v1_schemas.py:1030-1034; src/acp/api/v1_routes.py:5927-5941; src/acp/catalog.py:4822; frontend/ACP_Workbench_v2.html:29020-29055; Oracle rr-cccp-02 #3.
  Acceptance criteria: `pytest tests/test_acp_api_batch_keyword_case.py -q` + new API test asserting a migrated legacy config returns non-empty `warnings`; `pytest tests/test_frontend_sync.py -q` asserts the render path.
  QA scenarios: happy - legacy UltraFine config returns a warning string rendered in the modal; failure - a clean config returns `warnings == []` and renders nothing. Evidence `.omo/evidence/task-24-cccp-correctness-hardening.txt`
  Commit: Y | feat(acp): surface method-validation warnings end to end
  Recommended task executor category: unspecified-high - cross-stack: normalizer, route, and frontend warning render

- [x] 25. frontend: browser interaction acceptance (Playwright, skip-if-unavailable)
  What to do: [user review item 6] add REAL interaction verification beyond the static `test_frontend_sync.py` markers: serve the workbench locally (`acp run serve` on a scratch port) and drive it with Playwright (record a skip reason ONLY when, AFTER attempting `python -m pip install -e '.[browser]'` (plus `python -m playwright install chromium` when needed), the `playwright` module is still not importable OR no browser binary exists — T20 enforces the attempt and the CI wiring added below keeps the flows non-vacuous; a Chromium build may already be cached under ~/.cache/ms-playwright): (1) switching `scan_optimizer_method` DFT→GFN→DFT hides/restores basis/dispersion/RI/grid/SCF; (2) the copy-scan-level result produces the expected FINAL serialized payload (assert captured JSON, not just UI state); (3) a legacy draft with `grid=UltraFine` shows the migration hint; (4) an invalid GFN combination is rejected by the backend on submit; (5) validation warnings are visible in the preview/summary. Wire `tests/test_frontend_interaction.py` into the existing browser-test step of `.github/workflows/ci.yml` (lines 52-61: installs `.[browser]` + `playwright install chromium` on the 3.11 leg and runs the existing `tests/test_frontend_*_browser.py` files) so the flows run permanently in CI (Oracle rr-cccp-06 #2). Evidence: screenshots + captured payload JSON.
  Must NOT do: replace or weaken the static contract tests in `tests/test_frontend_sync.py` (they remain the regression net); require external network; skip when `playwright` and a browser ARE available (that skip must FAIL T20 — Oracle rr-cccp-05 #1).
  Parallelization: Wave 5 | Blocked by: T15,T16,T23,T24 | Blocks: T20
  References: frontend/ACP_Workbench_v2.html:27131-27168,27564-27611,29020-29055,29249-29325; tests/test_frontend_sync.py:11080-11205; pyproject.toml:59-61 (`browser` extra); .github/workflows/ci.yml:52-61 (browser step to extend); user review item 6.
  Acceptance criteria: `pytest tests/test_frontend_interaction.py --run-slow -q` passes with all 5 flows asserted; evidence includes payload JSON + screenshots; the CI browser step includes this file (Oracle rr-cccp-06 #2); skips ONLY when the `.[browser]` install attempt still leaves playwright unimportable or no browser binary exists — when both are available a skip is a FAILURE (enforced by T20).
  QA scenarios: happy - full 5-flow run against the local server; failure - injecting a wrong expected payload makes the payload assertion fail. Evidence `.omo/evidence/task-25-cccp-correctness-hardening.txt`
  Commit: Y | test(frontend): browser interaction acceptance for method-family gating
  Recommended task executor category: visual-engineering - Playwright browser-interaction acceptance over the workbench UI

- [x] 17. tests: real ORCA 6.1.1 smoke matrix (slow-gated) for GFN and grid keywords
  What to do: add a `--run-slow` gated test (skips when ORCA missing) that runs the real binary on generated inputs and records accept/reject for: grid simple keywords (`DEFGRID1/2/3` accepted; `UltraFine/Grid5/SG1/Fine/SuperFine` rejected); GFN with basis/dispersion/grid/`CPCM`/`SMD`/`ALPB`/`GBSA`; GFN-FF and GFN0-xTB verdicts under ORCA (`! GFN-FF`, `! GFN0-xTB` — capability per ORCA 6.1's external-interface table (lists both) vs the native-keyword table; record capability/dependency empirically and never infer "unsupported" from a missing local parameter file; platform policy remains as decided (T13)); the Q9 arbiter case `! GFN2-xTB NMR`; and that the fixed renderer's GFN output parses. [user review item 2] Record each case's OUTCOME CLASS (syntax error / missing dependency / calculation failure / valid completion) with first error line; SP requires normal termination + a VALID ENERGY; the NMR case requires actually parsed shielding tensors from `NmrShieldingParser` (non-empty) — exit code 0 alone is NEVER a pass. Record ORCA binary path+sha256, `otool_xtb` version, and parameter files. Persist a results table as evidence. If any verdict contradicts a registry/applicability default, reopen T2/T4/T7/T12 and re-run this matrix after the fix.
  Must NOT do: run in the default `-m "not slow"` lane; depend on network.
  Parallelization: Wave 5 | Blocked by: T1,T5,T6,T7,T10,T13 | Blocks: T18,T20
  References: `/home/xieningke/orca611/orca`; prior empirical grid matrix evidence; draft F11/F12; tests/conftest.py slow gating convention.
  Acceptance criteria: `pytest tests/test_orca_keyword_smoke.py --run-slow -q` passes (or skips if ORCA absent); evidence file records the ORCA binary path + sha256, each case's accept/reject, and T20 confirms the matrix actually executed (not skipped) whenever the binary exists (Oracle M4); contract suite additionally covers (a) anti-pattern #33 emit→parse round-trip for every registry enum in any casing, (b) registry ↔ acp METHOD_META / family consistency lock (both directions).
  QA scenarios: happy - all legal inputs parse and run; failure - each illegal input produces the documented INPUT ERROR. Evidence `.omo/evidence/task-17-cccp-correctness-hardening.txt`
  Commit: Y | test(cccp): slow-gated real-ORCA keyword smoke matrix
  Recommended task executor category: unspecified-high - slow-gated real-binary integration suite with outcome taxonomy

- [x] 18. tests: end-to-end re-run of propylene PESsearch acceptance
  What to do: ground the baseline first: historical job `/var/lib/acp/runs/uncategorized/propylene_PESsearch` — pre-assert and RECORD both the 90-frame count AND the buggy baseline (`single_point_status=="failed"` ×90, `constraint_residual_ok==false` ×90) from `RESULT/pes_search/pes_profile.json`; this is the "before" evidence (Oracle rr-cccp-01 B2). Then re-run the same scan-config (copy of the job's `scan_config.json`: propylene xyz_text, dihedral atoms 4-0-1-2, 1°→359°, 90 points) with the fixed code via the local CLI (`acp run PESsearch --mode bond_length_scan --scan-config <copy> --output <scratch> --nproc 8 --mem 16GB`), slow-gated, timebox ≤2h; NOTE the raw config bypasses catalog normalization, so the cccp renderer (T5/T2 alias, reached transitively via T17) must apply the legacy grid alias (`UltraFine`→`DefGrid3`) — assert the generated SP input no longer contains `UltraFine`; assert 90/90 SP success, zero off-constraint frames, non-empty candidates; capture profile+logs+ORCA binary path/sha256.
  Must NOT do: touch/purge the historical job dir; use the remote LSF lane; widen the timebox without user approval.
  Parallelization: Wave 5 | Blocked by: T14,T17,T21 | Blocks: T20
  References: historical job `/var/lib/acp/runs/uncategorized/propylene_PESsearch` (comparison baseline); `acp run PESsearch` CLI; draft F1/F3.
  Acceptance criteria: `pytest tests/test_pes_e2e_propylene.py --run-slow -q` asserting SP success 90/90 and candidate count > 0 (the CLI run is supporting evidence only, never a substitute; Oracle rr-cccp-03 #8); the generated `sp_0000.inp` must not contain `UltraFine`; evidence records binary path+sha256 and the before/after deltas.
  QA scenarios: happy - profile shows zero off-constraint frames; failure - any SP failure or zero candidates fails the test. Evidence `.omo/evidence/task-18-cccp-correctness-hardening.txt`
  Commit: Y | test(pes): end-to-end propylene PESsearch acceptance
  Recommended task executor category: unspecified-high - long real-run acceptance with before/after deltas

- [x] 19. tests+docs: update tests that lock old behavior and align docs
  What to do: update the assertions that intentionally locked the old permissive behavior once the new behavior lands (test_acp_pes_dft_scan.py:145-207/242-273/648-720; test_acp_catalog.py:134-195; test_frontend_sync.py:11080-11205; test_pes_constraint_gate.py for the T21 residual wrapping); add the three dev-plan-named GFN tests that never existed (`test_functional_options_map_gfn_basis_empty`, `test_orca_input_blocks_gfn_no_basis`, `test_orca_input_blocks_gfn_alpb_solvent`); update `docs/ACP_Scan_OptTS_GFN_DevPlan.md` §2.1 and `docs/ACP_PES_DFT_Scan_Extension_Plan.md` solvent option notes to match implementation. If T17's `! GFN2-xTB NMR` verdict shows artifact-level support (non-empty shielding), record the capability; opening the T8 switch remains an explicit policy decision (update docs/tests when decided — capability/dependency/policy split, see Scope).
  Must NOT do: weaken coverage or delete tests without replacement.
  Parallelization: Wave 5 | Blocked by: T1,T8,T9,T14,T16,T21,T23,T24 | Blocks: T20
  References: listed test files/lines; docs listed; draft F16/F17.
  Acceptance criteria: `pytest -m "not slow" -q` green; grep confirms the three named GFN tests exist; doc diff reflects the implemented option sets.
  QA scenarios: happy - full offline suite green; failure - old locking assertions now fail meaningfully. Evidence `.omo/evidence/task-19-cccp-correctness-hardening.txt`
  Commit: Y | test/docs: align suites and dev docs with hardened behavior
  Recommended task executor category: unspecified-high - multi-suite behavior-lock updates + docs alignment

- [x] 20. quality gates: full suite + ruff + mypy
  What to do: use the PRE-CHANGE baseline captured by T26 (raw full-diagnostic outputs + tool versions under `.omo/evidence/task-26-cccp-correctness-hardening/`; must have been captured before the first code modification — user review 2026-09 execution adjustment #1); run the same gates on the final tree and fix every NEW violation this branch introduced: `pytest -m "not slow" -q`, `ruff check src tests`, `ruff format --check src tests`, and the configured type check; ensure no new suppressions were introduced to hide failures; explicitly run `pytest tests/test_orca_keyword_smoke.py tests/test_pes_e2e_propylene.py --run-slow -q` (or assert the evidence files carry no skip marker) so the T17 smoke matrix and T18 E2E are PROVEN executed (not skipped) whenever `/home/xieningke/orca611/orca` exists and the historical baseline dir is present (Oracle M4 + rr-cccp-02/03 #3); also verify T25's browser-acceptance evidence: before accepting a skip, attempt `python -m pip install -e '.[browser]'` (plus `python -m playwright install chromium` when needed) so a skip is reserved for a genuinely unavailable dependency; if playwright is importable AND a browser binary exists after that attempt, a T25 skip FAILS T20 (Oracle rr-cccp-05 #1 + rr-cccp-06 #2).
  Must NOT do: lower lint standards, add blanket `# type: ignore`, or expand scope into fixing the pre-existing ruff/mypy backlog (deferred hygiene wave per AGENTS anti-pattern #15).
  Parallelization: Wave 5 | Blocked by: T17,T18,T19,T25,T26 | Blocks: —
  References: T26 baseline evidence (`.omo/evidence/task-26-cccp-correctness-hardening/`); pyproject/CI gates + `[tool.mypy]` config; AGENTS.md COMMANDS section + anti-pattern #15; `.github/workflows/ci.yml:3-9` (deferred lint gates) and `:52-61` (browser step for T25); draft F16; Oracle rr-cccp-06 #1/#2; user review 2026-09 (full-diagnostic diff).
  Acceptance criteria: `pytest -m "not slow" -q` exits 0; `ruff check src tests` / `ruff format --check src tests` / the configured type check show NO NEW violations vs the T26 baseline (full rule/site-level diff archived, not counts-only; pre-existing findings documented and unchanged; for these three static gates exit 0 is NOT required — Oracle rr-cccp-06 #1 + user review 2026-09); baseline + final raw outputs recorded in the evidence files; the explicit `--run-slow` pair executed with recorded non-skip results; T25 executed with evidence unless the `.[browser]` install attempt failed (Oracle rr-cccp-05 #1 + rr-cccp-06 #2); bump the 4 version sites per AGENTS anti-pattern #2 if this ships as a behavior release; no new lint/type suppressions.
  QA scenarios: happy - clean gates relative to the recorded baseline (offline suite green, no NEW static violations); failure - any new violation fixed before completion. Evidence `.omo/evidence/task-20-cccp-correctness-hardening.txt`
  Commit: Y | chore: pass pytest/ruff/mypy gates
  Recommended task executor category: unspecified-high - baseline-vs-final gates across suites, static checks, and the slow pair

- [x] 26. ops: capture the pre-change quality baseline + tool inventory BEFORE the first modification
  What to do: run FIRST — before T1/T2/T22 make any change. Record tool versions (`python --version`, `ruff --version`, `mypy --version` or the installed binary path, `pytest --version`) and the THREE gate commands with FULL diagnostics (not just counts): `ruff check src tests` (raw full output), `ruff format --check src tests` (raw full output), the configured type check (`mypy src --cache-dir=<tmp>`, or the installed `mypy` binary when `python -m mypy` is unavailable — raw full output), plus `pytest -m "not slow" -q` result and `git rev-parse HEAD`; save everything under `.omo/evidence/task-26-cccp-correctness-hardening/` (raw text per command + a `summary.md` with counts). These files are the T20 comparison baseline: T20 diffs FULL rule/site-level diagnostics — totals alone cannot prove "no new violations" (user review 2026-09 execution adjustment #1).
  Must NOT do: modify any src/tests file; install/upgrade tools; skip raw outputs (counts-only is unacceptable).
  Parallelization: Wave 1 | Blocked by: — | Blocks: T1,T2,T22,T20 | run FIRST
  References: AGENTS.md anti-pattern #15 + `.github/workflows/ci.yml:3-9` (deferred gates); `mypy` at `~/.local/bin/mypy` / `[tool.mypy]` at pyproject.toml:93; T20 (diff consumer).
  Acceptance criteria: `.omo/evidence/task-26-cccp-correctness-hardening/` contains verbatim tool versions + full outputs for all four commands + `git rev-parse HEAD`; a machine-diffable raw copy per diagnostic exists and summary counts match `wc -l`/`grep -c` on the raw outputs; captured before the first code modification.
  QA scenarios: happy - baseline captured before any edit; failure - a tool is missing ⇒ record the reason + fallback used, and mark that T20 check as "documented-baseline-unavailable" instead of a silent skip. Evidence `.omo/evidence/task-26-cccp-correctness-hardening/`
  Commit: N | (evidence-only; no commit)
  Recommended task executor category: quick - mechanical baseline capture of toolchain diagnostics into fixed evidence files

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Ideal-state fidelity

## Commit strategy
- One atomic commit per todo (T1-T26; T26 is evidence-only — no commit), using the conventional-commit lines given in each todo; no mixed-concern commits.
- Commit order follows the dependency matrix (Wave 1 → Wave 5); every commit must leave the offline suite (`pytest -m "not slow"`) green.
- Do not amend/squash pre-existing history; the worker owns its branch/worktree.
- No "final cleanup" commit that hides failures; final sign-off only after F1-F4 approve.

## Success criteria
> One row per IS row (IS-1…IS-8 are defined in `### Affected user and ideal state`). The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.

| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 (dihedral convention + wrapped angular residuals) | T1, T21 | T1: synthetic cis/trans/±30°/±150° vs RDKit reference ≤1e-9 + periodic permutation checks; T21: 359°/200° targets ⇒ residual≈0 and constraint gate passes; historical 90-frame re-measure 90/90 within 1.0° | `.omo/evidence/task-1-cccp-correctness-hardening.txt`, `.omo/evidence/task-21-cccp-correctness-hardening.txt` |
| IS-2 (ONE registry; all 8 `!`-line assembly points; fail-fast enums; free text unchanged; method×engine resolution) | T2, T3, T4, T5, T22 | T2: registry matrix + unknown-enum raise + `normal` no-op + case-insensitivity + `resolve_implementation`（method×engine）+ probe-JSON consistency; T3: GFN-FF/GFN0-xTB classified; T4: every catalog value resolves, deleted maps gone; T5: illegal grid raises before subprocess | `.omo/evidence/task-{2,3,4,5,22}-cccp-correctness-hardening.txt` |
| IS-3 (GFN produces no inapplicable tokens anywhere; ORCA solvent ALPB-only; NMR gated by T17 verdict) | T6, T7, T8, T10 | T6: no basis/`%basis` for GFN across all entry points (library strips with warning; strict entries reject — entry-behavior contract); T7: `ALPB(water)` emitted / GBSA raises; T8: no `6-311G(d)` for GFN (DFT unchanged); T10: backend GFN SP emits no basis token | `.omo/evidence/task-{6,7,8,10}-cccp-correctness-hardening.txt` |
| IS-4 (xTB canonical solvent names + alias resolution) | T9 | T9: alias mapping asserts + unknown-name raise with legal list + ALPB/GBSA × method legality matrix + slow-gated real-xTB cross-check | `.omo/evidence/task-9-cccp-correctness-hardening.txt` |
| IS-5 (acp/frontend family gating end-to-end; submit/run-time rejection; warnings surfaced; browser acceptance) | T11, T12, T13, T14, T15, T16, T23, T24, T25 | T11-T12: catalog payload locked fields + no legacy grid names; T13: GFN+basis raises / GFN+ALPB passes / single_point validated + entry behavior contract（未改动=迁移、改动=严格）; T14: submit rejection + warning channels (preview/CLI/job record); T15-T16: hide/restore + copy clears; T23: serialization/banner/hint markers; T24: warnings rendered; T25: 5 Playwright flows (or justified skip; CI-wired) | `.omo/evidence/task-{11,12,13,14,15,16,23,24,25}-cccp-correctness-hardening.txt` |
| IS-6 (real-binary acceptance: ORCA smoke matrix + propylene E2E) | T17, T18, T22, T25 | T17: smoke matrix outcome taxonomy (rc=0 alone never a pass; skip only when binary absent; T20 proves executed); T18: 90/90 SP success, zero off-constraint frames, candidates>0, generated `sp_0000.inp` free of `UltraFine`, before/after deltas recorded | `.omo/evidence/task-{17,18,22,25}-cccp-correctness-hardening.txt` |
| IS-7 (regression stability + quality gates vs recorded baseline) | T19, T20, T26 | T19: `pytest -m "not slow" -q` green + the three named GFN tests exist + doc diff; T26: pre-change baseline captured (tool versions + full diagnostics); T20: full rule/site-level diff vs T26 shows no NEW ruff/format/type violations, explicit `--run-slow` pair executed, T25 install attempt enforced | `.omo/evidence/task-19-cccp-correctness-hardening.txt`, `.omo/evidence/task-20-cccp-correctness-hardening.txt`, `.omo/evidence/task-26-cccp-correctness-hardening/` |
| IS-8 (docs match implementation) | T19 | T19: doc diff reviewed against implemented option sets (`docs/ACP_Scan_OptTS_GFN_DevPlan.md` §2.1 + solvent-option notes) | `.omo/evidence/task-19-cccp-correctness-hardening.txt` |
