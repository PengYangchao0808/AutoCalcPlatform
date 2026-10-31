# ACP 2.0 执行计划独立评估（库状态 · 合理性 · 可执行性）

**日期**：2026-10-26
**对象**：`EXECUTION_PLAN.md`（992 行，SHA-256 `6959389a…c2ff`）、`plan-graph.json`、`GAP_REPORT.md`、`VERIFICATION_R3.md`、`EXECUTION_PLAN_REVIEW.md`
**基线**：工作树 HEAD `0d739b2`（`feat/frontend-plan1-closure`）
**方法**：逐条回到源码符号、契约文件、CI/打包配置与真实运行环境核对；本次未修改业务代码，未启动生产服务，未调用付费模型/QC/LSF。所有断言给 `path::symbol` 或命令，行号会漂移，只作定位参考。

**总评：B（有条件可执行）。** 方向与证据治理显著优于仓库中已有的历史计划，可以据此开工；但必须先做 5 项前置修正，且在当前这台机器上“完成 G01–G26”不可达，天花板是 `Candidate-Contracts` + Local ORCA 子集。

| 维度 | 评级 | 一句话 |
|---|---|---|
| 事实基线准确性 | **A−** | 约 95% 断言可核对成立；4 处细节与 1 处继承自 KB 的错误 |
| 差距覆盖完整性 | **A−** | G01–G26 每项至少一个任务 + 关闭门；无孤儿任务 |
| 依赖结构与关键路径 | **B** | DAG 无环、19/6 可复算；但图把“开工依赖”和“关闭依赖”混在一张表里 |
| 任务粒度与规模 | **B−** | 子检查点设计好；9 XL + 22 L，无工期/人力/日历模型；M0-09 的规模与其决策权不匹配 |
| 验收与证据治理 | **A** | 全仓最强部分：三态证据、逐场景 required_observations、mutation 门、BOM walker |
| 可执行性（当前盘面） | **C+** | 环境缺 LSF/xtb/crest；基线未推送；B 串行链 6 任务；但 python3.11、Chromium、ORCA、Node22/pnpm 已具备 |
| 内部一致性与可维护性 | **B−** | 32→39 任务迁移痕迹、计数漂移、手工 plan-graph 被当成 M0-01 交付物 |

---

## 1. 库状态核对（计划断言 vs 实测）

### 1.1 基线与仓库拓扑

| 断言 | 结果 | 证据 |
|---|---|---|
| `ec4a0b3..0d739b2` 仅 `.gitignore` 差异 | **成立** | `git diff --stat ec4a0b3 0d739b2` → `.gitignore | 12 +` |
| 当前工作树有用户修改与未跟踪证据 | **成立，且更严重** | 30 个脏路径：14 修改 + 16 未跟踪；`AGENTS.md` 等知识库 12 份文件未提交；`tests/baseline/refactor-evidence/e2e-task-root.txt` 被改成 `/tmp/pytest-of-xieningke/...` |
| 39 个任务 | **成立** | `plan-graph.json` 39 节点；M0 13 + M1 6 + M2 5 + M3 6 + M4 6 + M5 3 |
| 关键路径 19 任务 / 6 XL | **算术成立** | 重新计算最长链与权重最长链，两者均为该 19 任务序列；默认边 109、外部变更 112 = 109 + M0-13 的 2 条 + M1-01→M0-13 |
| 15 active workflows、四层架构、Services 单向依赖 | **成立** | `acp.catalog.WORKFLOW_CATALOG`：30 条 = 15 active + 14 retired + 1 planned |
| 基线可作为“持久 ref/tag” | **不成立（新增）** | `git ls-remote origin` 上 `feat/frontend-plan1-closure` 指向 `d0fcf70…`，该对象本地不存在；本地 `0d739b2` 不在任何远程分支上。M0-01/M5-02/M5-03 的“persistent ref”要求当前没有起点 |
| 干净集成分支可从基线建立 | **部分不成立（新增）** | 已有 5 个 worktree：`acp2-development/{A,B,C,integration}`，其中 integration 在 `77ac1de`、B 为 detached HEAD，与 `0d739b2` 互不为祖先；直接复用会把两套历史混在一起 |

### 1.2 计划事实断言逐条核对（仅列判定有偏差者）

| 计划位置 | 计划表述 | 实测 | 影响 |
|---|---|---|---|
| M0-05、R09、§18 | 旧 MCP 派生键是 `op_` 前缀，需重算旧 `op_` 派生键并兼容 | **错**：`mcp.py::derive_idempotency_key`（864）生成 `auto_` 前缀；`op_` 是 `services_mcp_bootstrap.py:226` 的 operation 引用前缀 | 迁移测试会针对不存在的键命名空间；“老客户端原 key 重试不产生第二任务”的负例永远为真 |
| M0-04 | `ReservationPort` 有“两个实现” | **错**：仅 `_ControllerReservationPort`（`local_admission.py:285`）；`request_for_spec`/`admit_job` 是 `LocalAdmission` 的两个入口，不是两个端口实现 | 装配清单与改动面估计偏差 |
| M0-04 | `local_admission` `quota_enforced=True` 属 admission-only | **成立**（`snapshot()`:697，`HARD_LIMIT_ENFORCED`:160，模块内无 kill/terminate） | 拆分 enforced 语义的判断正确 |
| M2-04a | 现有 adapter“只产出 `{"result": digest}`” | **不准**：`step_dag.py` 同时产出 `output_digests={"result": digest}`（1094-1096）与完整 `publish_payload`（1098） | “声明产物映射”要比计划描述的更细，不是简单替换一个 dict |
| M2-04d / §0.3 | `acp/calculations/primitives/` 为纯转发 | **不成立**：约 2,068 行 ACP 侧逻辑（`frequency.py` 的 `normal_modes.json` 发布半部等）；`test_unique_primitive_definitions` 只钉住“任务核心定义唯一” | 发布职责从 acp 迁到 Services 是真实重构，含兼容面；计划需给出 seam 与回滚面 |
| M3-05 | 需在 `compat/api.js` 中“生产 init/load” | **一半已存在**：`compat/api.js:41` 已调用 `ACPClient.research.init({v2: apiV2})`；缺的是 load 与 commands | 任务描述偏大，验收点应改为 load/commands/审批回写 |
| §0.1 | 规划会话复跑 Recipe/science-binding、MCP/多用户/持久幂等定向测试均通过 | **与 §19.4 冲突表面成立**：审计证据里确有 `targeted-pytest-unrestricted.xml` 168 passed、`science-runtime-pytest.xml` 131 passed、RPH 80、前端 444；而 §19.4 说的是**另一次**会话（未安装本仓包）无法运行 | 两处都对，但文中未标明会话差异，读者会认为自相矛盾 |
| G19 计数 | 224 份 summary、43 个 candidate_revision | 当前盘面 **215 / 44** | 历史计数本身会漂移；计划已要求“不跨树互认”，但正文仍引用固定数字 |
| 历史发行制品 | “制品或相对路径不能在当前盘面复核” | **部分过期**：`reports/acp2_implementation/bb45a46/B/B25/` 下有 16 个 whl/tar.gz，共 356.6 MB，可取回 | 结论（缺统一 BOM）仍成立，但“不可取回”表述应收窄 |

其余核对成立（抽样）：8 个基础工具 + 研究扩展无条件 `1.1.0/negotiated=true`（`mcp.py:940/1061-1064`）、`chr(97)` 混淆（157）、`p2` 扩展权威 `1.0.0` 而 `application-api.mcp_extensions=1.1.0`、`routes.py:301 capability_descriptors=None`、`vibration.analyze` 全仓零注册、`analysis_capabilities.py:106` 空默认 registry、`step_dag::DispatchFn` 单参而 `cccp_compute_dispatch` 双参、`BudgetCaps = Σqc + len(steps)`（788）、`optimize::max_rescue=2`、`execution_right` 仅 acquire/verify + `AlwaysHeld`、`receipts::verify_receipt` 对旧 attempt 返回 `stale_attempt`、`v2_budgets` 收客户端自报 `trusted_source`、`claim_idempotency` 跨 scope 守卫、`migrations` 23 条、CI 的 RPH job 不跑 `pnpm package`、无 `run_all.js` job、pre-commit ruff `v0.4.0`、`pyproject` 无 `--strict-markers` 且无 `contract/nightly/pre_release` 标记、`docs/.../release/` 目录与 6 个新脚本全部不存在。

### 1.3 计划未覆盖、但影响执行的库状态

1. **环境已存在可用底座**：`python3.11.13` + 可编辑安装（`acp 0.3.0`）、numpy/rdkit/fastapi/pydantic/nmrglue 齐备、全仓 `11450` 个测试可收集；Chromium 在 `~/.cache/ms-playwright`；`/ACPServer/acp2-tools` 有 Node 22.19.0 与 pnpm 11.28.5；`orca 40.3` 可执行。这些应当在 M0-10 里记为 PASS 而不是待办。
2. **环境缺口是硬缺口**：无 `bsub/bjobs/bkill`、无 `LSF_ENVDIR`；`xtb/crest/isostat/molclus/Multiwfn` 全部缺失；默认 `node` 仍是 20.19（低于 22.19）；`ACP2_TOOLS` 为空（树在 `/ACPServer/acp2-tools`）。默认 PATH 下计划 §11 的 RPH/前端命令都跑不起来。
3. **release 角色制品在盘**（356.6 MB），M4-06a 的“上一已发布制品 → 当前候选”升级对已具备一半条件。
4. **DSH 外部树已就位**，`@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` 依赖 `@modelcontextprotocol/client@2.2.0`，与计划引用一致。

---

## 2. 计划合理性

### 2.1 覆盖与分层：成立

- G01–G26 全部在 §10 有“关闭任务 + 最低证据”行；反向检查 39 个任务全部至少出现在一条 G 行，无孤儿任务。
- 13 个 S0 项都落在 M0（入口安全）、M1（契约）与 M4（真实证据）上，未出现“低优先级任务先做”的倒挂。
- “部分完成不得关闭整项”写成了显式规则（§10 末），这对 G12/G15/G17/G09 这类易被单测“点亮”的项是关键约束。
- 阶段图被明确声明为“验收扇入而非开工屏障”，M2-02/M2-05/M3-06 可提前，避免了全阶段串行。

### 2.2 依赖结构与关键路径：算术正确，但语义混用

- DAG 无环、无悬空引用；最长链与计划公布的 19 任务一致，6 个 XL 与手工计算一致；外部变更路径 20/6 也自洽。
- **问题 1（语义）**：图的边同时承担“开工前置”和“关闭前置”。例如 M4-06 依赖 M4-03/M4-05、M5-01 依赖全部 M4，是为了“整项关闭”而加的边，但正文说 M4-06a 可在 M4-02/03/05 之前并行、M5-01 可在 M4-01 后启动。结果是**公布的 19 任务既不是最早开工关键路径，也不是真正的最晚关闭路径**，只是“按整项关闭串起来的一条链”。需要给边加 `kind: start|close` 或输出两张图，否则“唯一机器权威”会被不同读者按不同语义使用。
- **问题 2（治理）**：`build_acp2_plan_graph.py` 不存在，`plan-graph.json` 却在审计目录里已存在且未跟踪，还缺 M0-01 自己要求的 `unknown_refs`/`cycles` 字段。等于 M0-01 的一项交付物被“预先满足”为手工文件，漂移无门可查。
- **问题 3（串行链）**：`M0-01→04→05→06→11→12→M1-01→M1-06` 这 8 个任务里 6 个是 B 域。若 B 只有一人（计划 §2.2 自己承认这种可能），这就是一条 5–9 周的单线程链，期间 A/C 只能等契约。

### 2.3 任务粒度与规模：拆得动，但没算过账

- 优点：M0-02、M1-01、M1-04 的 a/b/c，M2-04 的 a–d，M4-06 的 a/b，以及 M1-06 的早期纵切，都是正确的“可独立验收”切法；§2.4 把中间任务 DoD 降到三条，避免每次都要发行级回执。
- 缺点 1：**没有数量化**。全文没有 eng-day、人数、日历、周五窗口，也没有“按此计划最小团队是多少”。39 个任务里 9 个 XL、22 个 L，按 XL 8–15 人日、L 3–6、M 1.5–3、S 0.5–1 估：
  - 总量 **≈150–290 人日**（约 7–14 人月）；
  - 关键链 **≈80–150 人日**（3 条车道并行时约 3–5 个月，单人则 6–12 个月）；
  - 到 `Candidate-Contracts`（M1-06）约 **25–45 人日**。
  这些是本评估给出的量级，不是计划给出的；计划应自己给一个可被挑战的版本。
- 缺点 2：**M0-09 规模与决策权不匹配**。该任务实际决定“研究产品线是否存在”（官方扩展路径 / 外部改 DSH / scope-change），只给 M，且失败分支才引出 L 的 M0-13。按现有证据（见 §3.3），最可能的落点是“外部变更或应用级握手”，应升为 L 并把 M0-13 的预算提前计入。
- 缺点 3：**覆盖率行爆炸未处理**。39 任务 + 24 个子检查点 ≈ 63 行，加上 G 行与 H 行映射，若无 schema 级校验，很容易退化成人工台账。计划已要求 validator，但应在 M0-01 就把子检查点建模进 `evidence-coverage.schema.json`。

### 2.4 证据治理：本计划最强的地方

- `fixture|live|aggregate` 冻结枚举 + 逐场景 `required_observations(present|absent)`：直接修掉“零模型调用即非 live”的错误规则，同时对预算拒绝这类合法零调用负例留出空间。
- “`NOT_VERIFIED` 不得改写成 PASS”“`已交付字段` 不得写成 `前端已接入`”与现有 ANTI #32 对齐。
- mutation 门（删测试/移除工具/改 required 字段/只改单侧版本必须失败）是对 G20 根因（两边各自绿）的正面回应，比“再加一个集成测试”有效。
- BOM walker + `reuse` 算法（exact/reused/rerun_required）+ “历史 summary 不改字节，用 supersedes 引用”：解决 215 份 summary、44 个 candidate_revision 的治理问题，且不篡改历史。
- 反例覆盖是“按根因”而不是“按文件”：DAG 的四个 TaskResult 真值、诊断步不释放下游、回执丢失后不得重新获得额度、审批摘要与冻结体摘要分别失效——这些正是最容易在实现时被“合理简化”的地方。

### 2.5 技术裁决 D01–D10：方向正确，两处需要更硬的边界

- D02“身份先于缓存”是全局最关键的排序，避免了把“同键跨用户重放”修成另一种冲突。
- D09 把 `core_hours`、token、`bsub` 计数默认降为 `recorded-only`，是对现状的诚实描述，不是讨价还价。
- D06 把 8 类摘要分开并指派唯一实现（平台 `canonical_digest` / 幂等 `canonical_request_digest` / CCCP step 身份），能直接消灭“伪摘要”这一类历史缺陷。
- **需收紧 1**：D05 允许在 `entrypoints/mcp.py` 为 wire-id 字符串开隔离扫描例外。方向可接受，但更稳的做法是把 wire-id 常量集中到一个显式常量模块 + 精确 allowlist，并让扫描器区分“import/反射”与“字面量”，而不是按位置豁免。
- **需收紧 2**：D01 删缓存后，`prepare`/`read`/`analyze` 的幂等语义分别定义得不错，但**没有给 Controller 侧新增调用量一个容量基线以外的验收门**（M1-06 只记录 p50/p95）。这对“删缓存后平台是否被打爆”足够，但应在 M4-01 的故障矩阵里加一条“重复 prepare/read 不对平台产生写放大”的断言。

---

## 3. 可执行性

### 3.1 环境就绪矩阵（= M0-10 的预演结果）

| 项 | 当前 | 对计划的影响 |
|---|---|---|
| Python 3.11 + 依赖 + 可编辑安装 | **PASS** | 11,450 测试可收集；`python3.11 -m pytest tests/test_acp_services_isolation.py` 23 passed |
| 默认 `python3` = 3.9 | 需显式 `python3.11` | §11 命令全部写 3.11，OK；但脚本里若有 `python3` 会踩坑 |
| Node 22.19 / pnpm 11.28.5 | **存在但不在 PATH**（`/ACPServer/acp2-tools`） | RPH/前端 CI 等价命令必须先设 PATH；`ACP2_TOOLS` 为空 |
| Chromium / Playwright | **PASS**（`~/.cache/ms-playwright`） | M4-02 浏览器行可执行 |
| ORCA | **PASS**（`orca 40.3`） | M4-02 的 Local singlepoint/opt/freq 可执行 |
| xtb / crest / isostat / molclus | **缺失** | Confsearch/xtb 系工作流、部分跨领域行不可执行 |
| LSF（bsub/bjobs） | **缺失** | M4-03 全部行、G13/G18/G23/G24/G25 的远程关闭门不可达 |
| 模型凭据 / 第二操作者 / 干净机器 | **未见登记** | M4-02、M5-03 需外部条件；M0-10 必须当场给出 FAIL/NOT_VERIFIED 决策 |
| 网络（npm/PyPI） | **可用** | M0-02 新增 `@modelcontextprotocol/client@2.2.0` 可行 |

**结论**：在当前机器上，计划可达 `Candidate-Contracts`，可达 `Candidate-P2` 的 Local+浏览器子集（ORCA 路线），**不可达 `Candidate-Full`**。这不是计划缺陷——计划自己规定缺环境即 `NOT_VERIFIED`——但默认目标“完成 G01–G26 整改”需要现在就改判，而不是等到 M4。

### 3.2 外部依赖：DSH 扩展协商大概率走“外部变更/应用级握手”

实测 `/ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/src/connection.ts:261-262`：

```ts
capabilities: {},
versionNegotiation: { mode: 'auto' },
```

`capabilities` 是**硬编码空对象**，插件没有配置项可注入 `extensions`。这意味着计划 M0-09 的三条候选路径里，“官方能力配置”分支在现有 DSH 版本上基本关闭；现实落点是：

1. 应用级握手（计划允许，但要在 M1-01/M3-01 重写“negotiated 来源”，并在契约里明确 initialize 不宣称扩展已协商）；
2. 或修改外部 DSH（M0-13），进入 BOM 与安装手册。

因此 M0-09 应默认按“路径 1 或 2”准备，而不是把它当“验证一下就走官方路径”的小任务。这个结论同时解释了为什么 G02 的 `negotiated=true` 是“无条件假协商”：客户端根本没有能力表达扩展。

### 3.3 人力与工期的现实约束

- 计划的分轨（B1–B4、A、C-RPH、C-Web、INT、REL）在**人数 ≥4** 时成立；在 1–2 人时，§2.2 自己也承认退化为单线程，此时 19 任务链就是 6–12 个月。
- AI 可以压缩“写代码/写测试/写文档”的时间，但压缩不了：真实 DSH/SDK 行为确认、SQLite 迁移对历史行的影响、fencing/并发正确性、真实 QC/LSF/模型授权、第二操作者与干净机器。**日历由这些外部项决定**，因此“三个月完成全量”只在环境全部就绪且 3 条以上车道并行时成立。
- 计划没有给“如果只有 1 个 B 执行者”的降级路线（除了 §2.2 的注释）。建议显式写成两种执行形态：`Team`（4–6 人，3–5 个月）/`Solo`（1–2 人，只做 M0 + M1-06 + Local P2，其余 DEFERRED）。

### 3.4 计划自身的三个执行风险（按概率×影响排序）

1. **M0-09/DSH**（高概率触发外部变更）：若选 scope-change，M2-01 的 P2 工具、M3-01/03/04 的 Agent 研究线、M4-02 的模型轮次全部作废或缩窄。计划已给分支，但没给“选 scope-change 后的最小可交付”清单。
2. **环境天花板**（已发生）：LSF/xtb 缺失使 M4-03 与部分 G13/G18 行永久 `NOT_VERIFIED`。需要在 M0 决策，而不是 M4 发现。
3. **B 单点 + 契约先行**（结构性）：`M0-04→05→06→11→12→M1-01` 是 6 个 B 任务的硬串行，任何返工都会整体右移；A/C 在此期间只能准备反例。

---

## 4. 必须修正的项

### P1 — 冻结前必须处理

1. **`op_` → `auto_` 迁移口径错误**（M0-05、R09、§18）。按实际代码把旧键命名空间盘点清楚：`auto_` 派生幂等键（`mcp.py::derive_idempotency_key`）、`op_` operation 引用（`services_mcp_bootstrap.py:226`）、`scope_id` 折叠键（`resolve_scope_id`）。迁移与负例必须用**旧代码真实产生的行**，否则“老客户端原 key 重试”测试会空转。
2. **plan-graph 语义与归属**。给边加 `kind`（开工/关闭）并输出两张图；补 `unknown_refs`/`cycles`/`xl_count`；实现 `build_acp2_plan_graph.py` 后再生成；删除或明确标注现存手工 `plan-graph.json` 为“评估用估算”，避免 M0-01 被未跟踪文件“提前完成”。
3. **基线持久化**。本地 `0d739b2` 与远程 `feat/frontend-plan1-closure`（`d0fcf70`）已分叉，且远程 tip 本地不存在。M0-01 必须先与 origin 对账、推送并打 tag，否则 M5-02/M5-03 的“可取回/独立复验”从第一天就不成立。
4. **工作树证据污染**。`tests/baseline/refactor-evidence/e2e-task-root.txt` 被改成个人 pytest 临时目录——这正是计划要禁止的“历史证据被改写”。开工前还原；`AGENTS.md` 的 12 份知识库改动要显式决定“提交到集成分支”还是“丢弃”，否则执行期引用的 KB 与基线不一致。
5. **环境决策前置**。把 §3.1 的矩阵在 M0-10 之前就变成决定：LSF/xtb 是“获取”还是“DEFERRED”；若 DEFERRED，则同步缩窄 §0.2 的 IN_SCOPE 与发行声明，并把 M4-03 标为 `NOT_APPLICABLE/NOT_VERIFIED`，不要留到最后才发现 Full 不可达。

### P2 — 应在对应任务开工前修正

6. **事实细节修正**：`ReservationPort 两个实现`（实为一个）；adapter 产物是 `output_digests` + `publish_payload` 两份；`compat/api.js` 已有 `research.init`；`acp/calculations/primitives/` 不是纯转发（约 2,068 行 ACP 侧逻辑，含 `normal_modes.json` 发布半部）——M2-04d 必须写明发布职责的 seam 与兼容回滚；`ServicesPorts` 没有 `Entrypoints` 符号。
7. **`check-boundaries.mjs` 缺 `fetch` 规则**：计划反复强调“bridge 禁 fetch/HTTP/spawn”，实际 gate 只拦 `child_process`/`ssh2`/`node:net`。应在 M3-02 加 `fetch`/`undici`/`http` 规则并附负例，否则该不变量只是文字。
8. **`validate_acp2_contracts.py` 不做目录完整性检查**：新契约文件只要不注册就完全不可见。M0-01 应加“目录 glob ↔ manifest 注册”的双向检查，否则新加的 `release/scope.json` 自身就可能漏登记。
9. **测试成本预算**：当前 11,450 个测试、481 个文件；计划要在其上再加跨端 `contract` 套件与真实进程/浏览器场景。§11 只有选择器没有时间预算。建议 M0-02 记录普通矩阵与 `contract` job 的 wall-clock，超过阈值（例如 20 min）就拆 job。
10. **v1 `extra=forbid` 的爆炸半径**：全仓只有 1 处 `V1JobCreateRequest(` 构造，说明请求主要是 JSON dict，真正的破坏面在 frontend/RPH/acceptance/文档示例。M0-11 的“客户端清单”应做成机械扫描（键名 vs schema），而不是人工列举。
11. **覆盖行建模**：把 39 任务 + 24 子检查点都写进 `evidence-coverage.schema.json`，否则“每个子检查点独立 coverage 退出”无法校验。

### P3 — 宜修不必阻塞

12. 计数漂移（224/43 → 实测 215/44）与 §0.1/§19.4 的会话差异，建议加一句“来自不同会话/时点”的限定。
13. `chr(97)` 去混淆时优先“集中常量 + 精确 allowlist”，不要按行号或函数位置豁免。
14. `M4-06`/`M5-01` 的关闭依赖与正文的“可提前并行”应在一处显式说明，避免读者把关闭链当开工链排期。

---

## 5. 建议的首周动作（按顺序）

1. **修正 M0-05/R09 的键命名空间**（1 小时），并据此改迁移测试与 `legacy` 负例。
2. **把 `plan-graph` 拆成 start/close 两张图并实现生成器**（0.5–1 天），重新生成、提交，删除手工版本。
3. **做环境裁决**（0.5 天）：LSF/xtb 获取或 DEFERRED；`ACP2_TOOLS`/nvm/pnpm 写入 profile；把结果写进 `environment-readiness.json`。
4. **基线卫生**（0.5 天）：还原 `e2e-task-root.txt`；决定 `AGENTS.md` 提交；与 origin 对账后推送并打 `acp2-baseline-20261026` tag。
5. **M0-09 预研时间盒（2 天）**：直接读 DSH `connection.ts` 与 `@modelcontextprotocol/client@2.2.0`，验证 `capabilities.extensions` 是否可注入；大概率结论是“应用级握手为主、DSH 变更条件化”，据此重估 M0-09/M0-13 规模与 M1-01/M3-01 的 negotiated 来源。
6. 之后再按 §14 的开工顺序启动 M0-01→M0-04。

---

## 6. 附：本次核对用到的关键命令与证据

```bash
# 基线/拓扑
git diff --stat ec4a0b3 0d739b2            # 仅 .gitignore
git ls-remote --heads origin               # remote tip d0fcf70（≠ 本地 0d739b2）
git worktree list                          # acp2-development/{A,B,C,integration} 均已分叉

# 关键路径复算（读 plan-graph.json，DAG 校验 + 最长链 + 权重链）
python3 - <<'PY' … graphlib.TopologicalSorter + 递归最长链 … PY

# 环境
python3.11 -c "import acp,cccp;print(acp.__version__)"   # 0.3.0
python3.11 -m pytest tests/test_acp_services_isolation.py -q   # 23 passed
python3.11 -m pytest tests/ --collect-only -q | tail -1         # 11450 tests
orca --version                                                   # 40.3
ls /ACPServer/acp2-tools/{node-v22.19.0-linux-x64,deepseek-harness}
sed -n '255,265p' /ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/src/connection.ts

# 源码锚点（抽样）
src/acp_services/entrypoints/mcp.py:157,864,940,1061-1064
src/acp_services/runtime/{step_dag.py:202,529,752,788,1074,1094-1098, execution_right.py:97,115, receipts.py:74,97}
src/acp_services/runtime/local_admission.py:285,429,458,697
src/acp_services/api/../v2_budgets.py:46,99,105
docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json:16,59,77,97,117,134
docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json:2
tests/test_acp2_runtime_contracts.py:821,847,874
rph/packages/bridge/src/index.ts:1127-1130,1277-1298
rph/scripts/check-boundaries.mjs:23-43
.github/workflows/ci.yml:42-59,104-134
```

**限制**：本次未运行完整 pytest、未跑 RPH/前端套件、未执行真实 QC/LSF/模型、未构建发行制品；上述“可执行/PASS”判断基于安装状态与单点验证，不等于候选验收证据。
