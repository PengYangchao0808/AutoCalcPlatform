# ACP 2.0 验收缺陷报告

- 报告编号：`ACP2-ACC-DEF-20261025`
- 验收对象：`/ACPServer/AutoCalcPlatform`，HEAD `ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（parent `f6dd3c928b4f11dc8c3782c4b15c7130cc856a0b`）
- 验收日期：2026-10-25
- 验收范围：ACP 2.0 四组分（CCCP / ACP CLI-ACR / RPH / Workbench）合并候选及最终验收整改
- 最终判定：**NOT APPROVED（完整 ACP2 不通过）**
- 整改代码面判定：**PASS_WITH_CONDITIONS**
- 生产环境：未重启 `acp.service`，未触碰 `/var/lib/acp/runs`
- 工作树状态：`reports/acp2_implementation/bb45a46/integration/h7-index.json` 与 `integration/summary.json` 有未提交修改；`reports/acp2_final_acceptance_20261024/` 等验收证据为 untracked；存在无关未跟踪文件（`lab.pid`、`sp.*`）

> 本报告只记录事实、证据与关闭条件；不把任何 `NOT_VERIFIED` 改写为 `PASS`，也不替代 owner 的 scope 决策。

---

## 1. 结论摘要

### 1.1 总体结论

本次整改（`ec4a0b3`）的主要代码缺陷已关闭：

- B1 的 7 条只读 science/research 路由已注册，真实 `create_app()` 探针 7/7 通过（200/400/404 符合契约）；
- M1–M5、m1、m5、R8 N1/N2/N3、R9-1/2/3、R1–R4/R6 均有代码、测试或契约证据；
- 定向复跑 304 项整改用例、54 项 experience harness、28 项 CLI full loop、444 项前端 Node、80 项 RPH、boundaries、tsc 全部通过；
- 仓库既有全量回归证据为 11,344 passed / 0 failed / 11 skipped（JUnit tests=11,355）；
- HEAD `ec4a0b3` 的四角色安装（cccp / acp-worker / acp-web / root acp）通过，MCP `2025-06-18` + 8 工具复验，四处版本 `0.3.0` 一致。

但是，**完整 ACP2 的设计验收门仍未满足**：

- P0/P1/P2/P4/P5 阶段门未闭合；H0/H5 PASS，H1–H4/H6/H7 OPEN；
- B2 owner A/B scope 决策未签收；
- 真实 DSH 握手、真实模型调用、当前候选 LSF、DA TS/IRC/EvidenceReport、H6 selectivity/readability/real-DA 均无证据；
- C33/C34/C35、static-assets manifest、独立 operator 缺失；
- Workbench 面板级 live 与 artifact-read UI 消费者未验证；
- RPH CLI 仍为 `0.0.0`、help-only、无 `bin`、无 `dist-packages`；
- B1-3 的“真实 backend 无拦截闭环”仍只用 `workflow:"fake"` + SMILES，未覆盖真实 XYZ/charge/method/resources。

### 1.2 缺陷统计

| 严重度 | 数量 | 状态 |
|---|---:|---|
| P0 发布阻断 | 10 | 全部 OPEN / NOT_VERIFIED |
| P1 门禁阻断 | 6 | 5 NOT_VERIFIED，1 PARTIAL |
| P2 证据完整性 | 7 | 全部 OPEN |
| P3 文档/测试卫生 | 2 | 全部 OPEN |
| **合计** | **25** | — |

### 1.3 可签署范围与不可签署范围

**可以签署：**

- B1 只读 science/research HTTP API；
- M1–M5、m1/m5、N1–N3、R1–R4/R6 的代码整改；
- CCCP 静态/构造科学层；
- 本地运行时、前端模块、RPH 模块套件；
- HEAD r3 四角色安装包（16 PASS / 3 expected NOT_VERIFIED）；
- 版本 `0.3.0` 与 MCP `2025-06-18` + 8 工具。

**不可以签署：**

- 完整 ACP2 的 P0/P1/P2/P4/P5；
- H1–H4/H6/H7；
- 真实 DSH/model/LSF/DA 科学链；
- C33/C34/C35、static assets、独立 operator；
- Workbench 新科研面板 live、RPH CLI 客户端发行；
- B2 owner scope 决策。

---

## 2. 严重度与状态定义

| 级别 | 定义 | 放行影响 |
|---|---|---|
| **P0** | 发布阻断。没有它就不能签署完整 ACP2 或对应阶段门。 | 必须修复，或由 owner 明确签署 `DEFERRED(scope)` 并逐行分类。 |
| **P1** | 门禁阻断。功能可能存在，但当前候选缺少文档要求的 live/真实输入证明。 | 完整范围必须关闭；缩范围时必须列为 deferred 并注明实现/安装/运行。 |
| **P2** | 证据完整性。代码可能正确，但证据 stale、untracked、uncommitted、标签错误或互相矛盾。 | 影响可审计性与复现，不能作为正式验收依据。 |
| **P3** | 文档/测试卫生。不改变运行时行为，但削弱证明质量或误导读者。 | 发布前修正。 |

状态：

- **OPEN**：未完成或未满足；
- **NOT_VERIFIED**：证据缺失或不属于当前候选；
- **PARTIAL**：部分子项通过，部分未验证；
- **CLOSED**：已由代码/测试/证据满足（列入第 8 节“已验证关闭项”，不计入缺陷）。

---

## 3. 缺陷总览

> 路径约定：除特别标注外，仓库根为 `/ACPServer/AutoCalcPlatform`；外部科学证据根为 `/ACPServer/acp2-evidence/a-science-20261022/`。

| ID | 严重度 | 状态 | 标题 | 关联门/需求 | 主要证据 |
|---|---|---|---|---|---|
| ACP2-DEF-001 | P0 | OPEN | B2 owner scope 决策/签收缺失 | 主方案 §11.1；整改计划 §4 / L385-404 | `REPORT.md:91-108`、`:294-301` |
| ACP2-DEF-002 | P0 | NOT_VERIFIED | H1 契约可消费门未闭合 | H1 / P0 | `receipts/h1-r2.json:74-76` |
| ACP2-DEF-003 | P0 | NOT_VERIFIED | H2 真实 MCP/DSH/模型握手门未闭合 | H2 / P0 | `experience/handoffs/H2.json:101-113` |
| ACP2-DEF-004 | P0 | NOT_VERIFIED | H3 Local/LSF 同一任务闭环未闭合 | H3 / P1 | `receipts/h3.json:74-76`、`B11/live-gap.json` |
| ACP2-DEF-005 | P0 | NOT_VERIFIED | H4 限定 Diels–Alder 科学闭环未闭合 | H4 / P2 | `B20c/h4-closeout.json:110-119` |
| ACP2-DEF-006 | P0 | NOT_VERIFIED | H6 跨领域/可靠性门未闭合 | H6 / P4 | `B24b/h6-receipt.json:161,202-245` |
| ACP2-DEF-007 | P0 | NOT_VERIFIED | H7 发行/独立安装门未闭合 | H7 / P5 | `science/handoff-H7.json:497-527` |
| ACP2-DEF-008 | P0 | NOT_VERIFIED | Workbench 科学/研究面板未 live 验证；artifact-read 无生产消费者 | B1-8 / §11 门 2、7 | `REPORT.md §1.2.4`；`event-invalidation.js:358` |
| ACP2-DEF-009 | P0 | OPEN | RPH CLI 发行实现缺口 | C32 / T-43 / H7 | `rph/packages/cli/package.json:3-7` |
| ACP2-DEF-010 | P0 | NOT_VERIFIED | C static assets manifest 缺失，无 `/experience` 挂载 | m3 / C32 / H7 | `manifest-absence.json`、`B25/static-assets-status.json` |
| ACP2-DEF-011 | P1 | NOT_VERIFIED | B1-3 真实 backend 无拦截闭环未证明 | B1-3 / §11 门 2、3 | `tests/test_acp2_api_science_routes.py:675-681` |
| ACP2-DEF-012 | P1 | NOT_VERIFIED | B08/B11/B13 当前 HEAD 验收未重跑 | H3 / §11 门 4 | `B08/concurrency.json`、`B13/exit-semantics.json` |
| ACP2-DEF-013 | P1 | PARTIAL | C19 budget live 未验证（approval live 已 PASS） | H4 / C19 | `approval-budget-live/.../budget/...json:66` |
| ACP2-DEF-014 | P1 | NOT_VERIFIED | C10/C11/C17/C18/C21 证据早于 HEAD，未重跑 | C10–C21 / H3/H4 | `H2.json:123-127`；`ec4a0b3` 前端改动 |
| ACP2-DEF-015 | P1 | NOT_VERIFIED | C26–C31 插件/Schema/升级/跨域/隔离未验证 | C26–C31 / H5/H6 | C 证据目录缺失；`capability-form.js` 不存在 |
| ACP2-DEF-016 | P1 | NOT_VERIFIED | m4 Python 3.10/3.12 矩阵未执行 | m4 / 跨版本 | `m4-matrix-r2.log` |
| ACP2-DEF-017 | P2 | OPEN | `summary.json` 顶层仍为旧候选/旧判定 | 验收记录一致性 | `summary.json` 顶层 |
| ACP2-DEF-018 | P2 | OPEN | 整改探针 candidate 标签 stale | 证据标签 | `probe-frontend-contracts-r2.json` |
| ACP2-DEF-019 | P2 | OPEN | r3 发行/安装证据 untracked；h7-index/summary 未提交 | 证据可审计性 | `git status --short` |
| ACP2-DEF-020 | P2 | OPEN | 三套发行制品并存；版本清单指向非 HEAD 祖先构建 | 发行可追溯性 | `release/`、`release-fix5/`、`release-r3/` |
| ACP2-DEF-021 | P2 | OPEN | h7-index/manifest stale；science 契约未登记总 manifest | 契约/索引一致性 | `h7-index.json:15-18,157,161,246`；`manifest.json:633-637` |
| ACP2-DEF-022 | P2 | OPEN | A 证据根不符合文档；A 计划 untracked 且 post-R6 | 证据根/计划治理 | `/ACPServer/acp2-evidence/...`；`.omo/plans/acp2-plan-a-cccp-science.md` |
| ACP2-DEF-023 | P2 | OPEN | 全量回归证据未绑定 commit | 证据可复现性 | `nonslow-r2.log` 04:16 vs commit 04:19 |
| ACP2-DEF-024 | P3 | OPEN | `REPORT.md §1.2.2` 对 m1 的描述与代码不符 | 文档准确性 | `REPORT.md:74`；`controller_client.py:680-699` |
| ACP2-DEF-025 | P3 | OPEN | 测试二次挂载 science/research router，重复 Operation ID | 测试卫生 | `tests/test_acp2_v2_science_routes.py:45-53` |

---

## 4. P0 发布阻断缺陷

### ACP2-DEF-001 — B2 owner scope 决策/签收缺失

- **严重度/状态**：P0 / OPEN
- **需求**：主方案 §11.1 要求必需行未执行则阶段门保持未通过；整改计划 §4（`L385-404`、`L536`、`L702`）要求 owner 在“方案 A：补齐 live 证据”与“方案 B：逐行 `DEFERRED(scope)`”之间作出选择并签收。
- **证据**：
  - `reports/acp2_final_acceptance_20261024/REPORT.md:91-108`：owner A/B 决策请求 3 次未获回应，signoff pending；
  - `REPORT.md:294-301`：H1–H4/H6/H7 保持 OPEN/NOT_VERIFIED；
  - `summary.json`：顶层 `verdict: NOT_APPROVED`。
- **现象**：整改代码已完成，但没有任何 owner 签署的 scope 决策或 release note 列明实际 PASS 集合。
- **影响**：即使代码正确，完整 ACP2 不能合法放行；缩范围发布也没有被批准的边界。
- **根因**：流程/owner 决策缺失，不是代码缺陷。
- **关闭条件**：
  - 方案 A：补齐第 5 节所有 live 证据并刷新 H 门；或
  - 方案 B：owner 逐行签署 `DEFERRED(scope)`，注明延期的是 **implementation / installation / live execution**，release note 列出实际 PASS 集合，且不得改写 `NOT_VERIFIED`。
- **验证**：签署文件 + 更新后的 `h7-index.json`/`H7-receipt.md`/`summary.json`。

### ACP2-DEF-002 — H1 契约可消费门未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P0/H1 要求 A/C 契约样例可被消费、H1 回执与集成签收完成。
- **证据**：
  - `reports/acp2_implementation/bb45a46/integration/receipts/h1-r2.json:74-76`：`a_science_contract_fixture_absent;c_authored_sample_absent`；
  - `h7-index.json:15-18`：H1 OPEN/NOT_VERIFIED；
  - `REPORT.md:295`：C authored sample 未定位到，H1 集成/签收未完成。
- **现象**：A 的科学契约语料已到达当前树，但 H1 回执仍保留旧的 “A fixture absent”，且 C-authored sample 仍缺失。
- **影响**：三轨契约消费链未闭环；后续 H3/H4/H7 的契约前置不成立。
- **根因**：H1 回执未随 A 语料到达而刷新；C 侧缺少 authored sample。
- **关闭条件**：C 提供 authored sample；重跑 H1 验证并刷新 `h1-r2.json`/索引；H1 集成签收。
- **验证**：新的 H1 receipt + C-authored sample + H1 signoff。

### ACP2-DEF-003 — H2 真实 MCP/DSH/模型握手门未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P0/H2 要求真实 MCP 进程、官方 DSH 握手/工具发现、真实模型调用；不能用安装存在代替当前候选通过。
- **证据**：
  - `docs/reference/next-change/acp2-contracts/experience/handoffs/H2.json:101-113`：
    - `mcp-live=NOT_VERIFIED(controller_unavailable)`
    - `dsh-discovery-live=NOT_VERIFIED(controller_unavailable)`
    - `model-live=NOT_VERIFIED(no_model_credential|controller_unavailable; 127.0.0.1:18765 refused)`
  - `H2.json:825-827`：3/10 P0 场景未 PASS，H2 C side stays open；
  - `integration/receipts/h2.json:74-76`：`c_dsh_handshake_absent;lsf_prerequisite_missing`。
- **现象**：MCP 自身在 HEAD r3 包中通过（`2025-06-18` + 8 工具），但 DSH 官方握手、工具发现、真实模型调用均未执行。
- **影响**：四组分协同的 P0 入口未验证；RPH/Workbench 的真实链路不能成立。
- **根因**：隔离 Controller/模型凭据/DSH 环境未就绪；不是 MCP 8 工具本身失败。
- **关闭条件**：在 `ec4a0b3` 上重跑 `mcp-live`、`dsh-discovery-live`、`model-live` 并全部 PASS；H2 由 B 汇总签收。
- **验证**：H2 新回执 + 真实 DSH tool discovery 快照 + 模型工具调用记录。

### ACP2-DEF-004 — H3 Local/LSF 同一任务闭环未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P1/H3 要求真实 Local 与 LSF 提交/观测/控制/拉取，CLI/MCP/Workbench 查看同一 job/attempt/release/manifest digest；C12–C16 必须提供 live 证据。
- **证据**：
  - `integration/receipts/h3.json:74-76`：`c_dsh_browser_absent;a_scientific_audit_absent`；
  - `B/B14b/h3-receipt.json`：H3 OPEN；
  - `B/B11/live-gap.json`：记录候选 `10eb71e4` 的 LSF `NOT_VERIFIED`；
  - 真实 LSF job 1765 PASS 仅属于旧候选 `6ec01ec`（`B/B26/H7-receipt.md`）；
  - C 的 `dsh-local-singlepoint-live`、`dsh-lsf-singlepoint-live`、`dsh-optfreq-live`、`same-task-browser-live`、`client-recovery-live` 目录均不存在。
- **现象**：CLI/HTTP 本地同任务有 PASS；LSF 与 C Workbench 行未在当前 HEAD 证据中闭环。
- **影响**：P1 执行闭环与“三入口等价”不能签署。
- **根因**：C live 场景未执行；当前候选 LSF 未重跑；A 科学审计缺失。
- **关闭条件**：当前 HEAD 上跑通 Local + LSF；C12–C16 全部有 live receipt；三入口引用同一任务。
- **验证**：job/attempt/release/manifest digest 四元组一致的 CLI/HTTP/Workbench 记录 + LSF bsub/bjobs/控制/拉取回执。

### ACP2-DEF-005 — H4 限定 Diels–Alder 科学闭环未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P2/H4 要求真实 DA Recipe、有效 TS（单虚频 + 映射模式）、双向 IRC、端点连通性、可比能量、独立 search 行、EvidenceReport；A-20/C25/B20a-exec/search 必需。
- **证据**：
  - `B/B20c/h4-closeout.json:110-119`：`h4_status=OPEN`、`mechanism_proven=false`、`B20a-exec NOT_VERIFIED(c24a_absent)`、`B20a-search NOT_VERIFIED(search_not_declared)`；
  - `integration/receipts/h4.json:74-76`：A-20/C25/B20a-exec/search 缺失；
  - A-20 在仓库内无 `reports/acp2_implementation/bb45a46/A/` 目录（外部 evidence 根虽有结果，但不在文档约定根）。
- **现象**：只有构造的 validators/负例；没有真实 TS/频率/IRC/能量正例。
- **影响**：P2 科学闭环不成立；不能把“构造 PASS”当作科学完成。
- **根因**：A-20、C25、B20a-exec/search 未交付；真实 QC 未执行。
- **关闭条件**：真实 DA TS/freq/双向 IRC/能量/EvidenceReport 正例；A-20 与 C25 签收；search 行单独有证据。
- **验证**：原生输出 + release/attempt/receipt + EvidenceReport claim 状态 + search 证据。

### ACP2-DEF-006 — H6 跨领域/可靠性门未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P4/H6 要求 NMR/热化学复用、多项目/多用户隔离、selectivity、A/C readability、real-DA、DSH live 等。
- **证据**：
  - `B/B24b/h6-receipt.json:161`：`h6_status=OPEN`；
  - `:202-245`：open rows 含 `lsf_prerequisite_missing`、`c_dsh_h6_absent`、`c_readability_absent`、`a_readability_absent`、`c_t40a_absent`、`c24a_absent`；
  - S0 执行 FAIL 被如实保留（`h6-receipt.json:141,148`）；
  - `integration/receipts/h6.json:74-76`。
- **现象**：NMR/thermo 只有静态/构造绑定；selectivity、readability、real-DA 缺失。
- **影响**：P4 不可签署；跨领域可复现性不能宣称。
- **根因**：C T-40a/T-40b、A readability、DSH、real-DA 证据缺失。
- **关闭条件**：补齐 open rows；H6 回执 `h6_status=CLOSED`；S0 FAIL 仍以 FAIL 记录并说明 scope。
- **验证**：新的 H6 receipt + 各 open row 的真实回执。

### ACP2-DEF-007 — H7 发行/独立安装门未闭合

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：P5/H7 要求 A-29 signoff、B25a release candidate、C33/C34/C35、independent operator、C static assets、真实安装/操作手册。
- **证据**：
  - `docs/reference/next-change/acp2-contracts/science/handoff-H7.json:497-527`：`release_readiness.ready=false`；blocking requirements：B25a、H3、H4、H6；
  - `science/handoff-H7.json`：`signed_by: A`、`platform_release:false`、`candidate_b25a:NOT_VERIFIED`；
  - `package-install-r3/a-c-evidence.json`：C33/C34 `present:false`、`NOT_VERIFIED`；
  - `static-assets-r2/manifest-absence.json`：HEAD 全 ref 搜索无 `static-assets.manifest.json`，无 `/experience` mount；
  - `B/B26/H7-receipt.md:29-36`：H7 OPEN；A-29/C35/independent operator/C33/C34/DSH 缺失。
- **现象**：HEAD r3 四角色安装 PASS，但 H7 的独立科学签收、C 交付物与独立复现缺失。
- **影响**：不能宣称完整 ACP2 P5 完成；只能签“本地安装包 + 静态科学层”。
- **根因**：A-29 自签但 release_readiness=false；C33/C34/C35/static assets 未交付；独立 operator 未执行；B25a 缺失。
- **关闭条件**：A-29 `ready=true`；C33/C34/C35/static assets/independent operator 齐备；B25a release candidate 被 A 接受并签收；H7 汇总 PASS。
- **验证**：H7 新回执 + `release_readiness.ready=true` + 独立 operator 记录。

### ACP2-DEF-008 — Workbench 科学/研究面板未 live 验证；artifact-read 无生产消费者

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：B1-8/B1-9 要求面板级 live 接线，或显式 `NOT_VERIFIED/DEFERRED(scope)`；直接客户端调用不能扩大为整个 Workbench 通过。
- **证据**：
  - `REPORT.md §1.2.4`：面板级 live 接线（research-approval / scientific-evidence 自动挂载）为 `NOT_VERIFIED`；
  - `frontend/js/workbench/compat/api.js:42`：只 `init` 了 `eventInvalidation`；
  - `frontend/js/client/event-invalidation.js:358`：`readArtifact` 存在，但生产 UI 无调用点（仅模块测试）；
  - `frontend/js/workbench/features/science-question.js`、`scientific-evidence.js` 的面板接线未由 live 浏览器证据覆盖。
- **现象**：后端路由已注册，前端模块测试通过，但没有真实浏览器/面板级端到端记录。
- **影响**：Workbench 新科研流程不能作为 ACP2 完成面宣称；§11 门 2、7 不能 PASS。
- **根因**：C 侧面板 live 未执行；artifact-read 消费者未接线。
- **关闭条件**：真实浏览器完成 capabilities → preflight → submit → status/view → artifact read 的面板级证据；或 owner 明确将该面板列入 `DEFERRED(scope)` 并分类。
- **验证**：浏览器 trace / 截图 / 网络日志 + 面板状态记录。

### ACP2-DEF-009 — RPH CLI 发行实现缺口

- **严重度/状态**：P0 / OPEN
- **需求**：C32/T-43/P5 要求 RPH CLI/Bundle 可安装、可启动、可交付静态资产；80 项 RPH 测试不能替代客户端发行验收。
- **证据**：
  - `rph/package.json:3-9`：`version 0.0.0`、`private:true`、`packageManager pnpm@11.28.5`、Node `^22.19.0 || >=24.0.0`；
  - `rph/packages/cli/package.json:3-7`：`version 0.0.0`、`private:true`、`main ./dist/index.js`、**无 `bin` 字段**、描述为 help/usage only；
  - `rph/packages/cli/src/index.ts:4,16,90-118`：help/version/profile only，不启动 Controller、不执行 science；
  - `rph/dist-packages/` 不存在；无 `experience/release-manifest.json`。
- **现象**：RPH 模块测试 80 项通过，但 CLI 仍是开发骨架。
- **影响**：P5 的 RPH 客户端发行不成立；若声称“完整 ACP2”，这是实现缺口而非环境缺失。
- **根因**：C/T-43 未完成。
- **关闭条件**：实现 `bin` 入口、打包 `dist-packages`、发布 `experience/release-manifest.json` 与 static assets；或 owner 明确 `DEFERRED(scope, implementation gap)`。
- **验证**：`pnpm package` 产物 + 干净安装 + CLI 启动/查询记录。

### ACP2-DEF-010 — C static assets manifest 缺失，无 `/experience` 挂载

- **严重度/状态**：P0 / NOT_VERIFIED
- **需求**：m3/C32/H7 要求 C 发布 `experience/static-assets.manifest.json`，合入新冻结候选、重建 Web/RPH、验证安装包和真实 `/experience/<release_id>/` URL。
- **证据**：
  - `reports/acp2_implementation/bb45a46/B/B25/static-assets-status.json:2-5`：`NOT_VERIFIED(c_static_assets_absent)`；
  - `reports/acp2_final_acceptance_20261024/evidence/static-assets-r2/manifest-absence.json`：candidate `ec4a0b3`，全 ref 搜索无 manifest，无 `/experience` mount；
  - `package-install-r3`：`b25.static_assets_mount=NOT_VERIFIED`。
- **现象**：没有静态资产发布清单，也没有挂载路由。
- **影响**：C 的前端发行不完整；H7 不能闭合；不能把“能上传文件”写成 C 静态资产已交付。
- **根因**：C 未发布 manifest/资产；B 没有可合并对象。
- **关闭条件**：C 发布 manifest + 资产；新候选重建；安装包内验证 digest；真实 URL 可访问。
- **验证**：manifest + 安装包内文件 + `/experience/<release_id>/` HTTP 200 + digest 匹配。

---

## 5. P1 门禁阻断缺陷

### ACP2-DEF-011 — B1-3 真实 backend 无拦截闭环未证明

- **严重度/状态**：P1 / NOT_VERIFIED
- **需求**：B1-3 要求“真实 backend、无 route interception、真实 XYZ/charge/method/resources”的 preflight → submit → status/view 闭环，且预检与提交 plan digest 相同。
- **证据**：
  - `tests/test_acp2_api_science_routes.py:675-681`：闭环使用 `workflow:"fake"`、`input.source:"CCO"`、`method.protocol:"ext"`；
  - `tests/test_acp2_preflight_fingerprint.py:35-42`：同样使用 `workflow:"fake"` + SMILES；
  - 前端并发测试使用受控 adapter/fixture。
- **现象**：规范化请求、fingerprint、stale 拒绝、乱序预检逻辑已被证明；真实输入链路没有被证明。
- **影响**：无法确认真实 XYZ/resources 下 preflight 与 submit 的 identity/digest 一致；§11 门 3 仍 NOT_VERIFIED。
- **根因**：测试选择 `fake` workflow 以避免真实执行，但没有补真实 normalized request 的 API 级闭环。
- **关闭条件**：用真实 XYZ、charge/multiplicity、method/basis/solvent、resources 跑通 preflight → submit → status/view，断言同一 job/digest，且无 route interception。
- **验证**：新增/修改测试 + 运行记录。

### ACP2-DEF-012 — B08/B11/B13 当前 HEAD 验收未重跑

- **严重度/状态**：P1 / NOT_VERIFIED
- **需求**：H3/T5 要求当前候选的幂等提交、LSF 控制/取消、发布恢复、断线/重启证据。
- **证据**：
  - B08 双进程/重启证据在 `ede698bb`（`B/B08/concurrency.json`、`restart-replay.json`）；
  - B11 记录 `10eb71e4` 的 LSF `NOT_VERIFIED`（`B/B11/live-gap.json`）；
  - B13 记录 `10eb71e4` 的 local 5 行 PASS、LSF control NOT_VERIFIED（`B/B13/exit-semantics.json`）；
  - 真实 LSF job 1765 PASS 仅属于 `6ec01ec`（`B/B26/H7-receipt.md`）；
  - HEAD 只有 B08 的 pytest 绿（`nonslow-r2.xml`），没有 B08 双进程 acceptance 场景重跑。
- **现象**：执行完整性在历史候选有证据，在 `ec4a0b3` 只有局部测试。
- **影响**：§11 门 4 不能 PASS；不能把测试绿当作跨进程/跨集群验收。
- **根因**：整改后未重跑 B08/B11/B13 acceptance 场景。
- **关闭条件**：在 `ec4a0b3` 上重跑 B08 submit-replay、B11 LSF、B13 cancel/restart/publication；保留旧回执。
- **验证**：新的 scenario receipts + job/submission/release 记录。

### ACP2-DEF-013 — C19 budget live 未验证（approval live 已 PASS）

- **严重度/状态**：P1 / PARTIAL
- **需求**：C19 要求 approval/budget live：批准、拒绝、过期、撤销、预算预留/结算。
- **证据**：
  - approval live PASS：`C/approval-budget-live/.../approval/...json:108`；
  - budget `NOT_VERIFIED(b16_handoff)`：`C/approval-budget-live/approval-budget-live-r1/budget/...json:66`；
  - HEAD 已有 `src/acp/api/v2_budgets.py`（commit `7f0fa7e`），但 C 证据未重跑。
- **现象**：审批 live 通过，预算未验证。
- **影响**：P2 的持久审批/预算只完成一半；H4 仍 OPEN。
- **根因**：C 证据生成早于 budget 端点落地；未重跑。
- **关闭条件**：在 HEAD 上跑 budget/deny/expire/revoke live；刷新 C19 回执。
- **验证**：budget live receipt + 预留/结算/拒绝记录。

### ACP2-DEF-014 — C10/C11/C17/C18/C21 证据早于 HEAD，未重跑

- **严重度/状态**：P1 / NOT_VERIFIED
- **需求**：C10/C11/C17/C18/C21 需要当前候选的 fixture/live 证据；HEAD 修改了同一批前端文件。
- **证据**：
  - C 回执 client revision `51a7164`/`39d4aeb`（`H2.json:123-127`）；
  - HEAD `ec4a0b3` 修改 `frontend/js/client/science-query.js`（+53）、`science-question.js`（+180）、`event-invalidation.js`（+26）、`scientific-evidence.js`（+12）、manual fixture（+734）；
  - 无 C10/C11/C17/C18/C21 在 `ec4a0b3` 的重跑记录。
- **现象**：fixture PASS 是旧前端候选的结果，不能直接覆盖 HEAD。
- **影响**：C 侧体验证据与当前候选不一致；H3/H4 的 C 行不可归因。
- **根因**：整改后未重跑 C 场景。
- **关闭条件**：在 `ec4a0b3` 上重跑 C10/C11/C17/C18/C21，或提供逐文件 applicability mapping 并由 C 签收。
- **验证**：新的 C receipts + HEAD revision 记录。

### ACP2-DEF-015 — C26–C31 插件/Schema/升级/跨域/隔离未验证

- **严重度/状态**：P1 / NOT_VERIFIED
- **需求**：C26–C31 覆盖插件发现、Schema 自动表单、升级/回退、NMR/热化学、多项目隔离、体验升级。
- **证据**：
  - C26–C31 无 live 证据目录；
  - C27 `frontend/js/workbench/features/capability-form.js` 不存在；`acp2_schema_form.test.js` 不存在；
  - C29 `cross-domain-live/` 目录为空；
  - C30/C31 目录不存在；
  - H5 PASS 来自 B/A 插件证据，C26–C28 均为 `[~]`，不能归因 C。
- **现象**：H5 的整体 PASS 不能证明 C 侧插件体验；H6 的 cross-domain 证据缺失。
- **影响**：P3/P4 的 C 侧不可签署。
- **根因**：C26–C31 未实现/未执行。
- **关闭条件**：补齐 C26–C31 实现与证据；H5/H6 的 C 行单独签收。
- **验证**：C 目录 receipts + 前端模块测试 + live 记录。

### ACP2-DEF-016 — m4 Python 3.10/3.12 矩阵未执行

- **严重度/状态**：P1 / NOT_VERIFIED
- **需求**：m4 要求 Python 3.10/3.12 矩阵与 ruff/mypy；若本机仅 3.11，必须诚实报告，不得写 PASS。
- **证据**：`m4-matrix-r2.log` 仅 3.11；3.10/3.12 缺失；ruff 为存量 advisory；新增 API 模块 mypy 0。
- **现象**：报告已诚实标注“本机仅 3.11”，但没有跨解释器矩阵。
- **影响**：跨版本支持未验证；CI 矩阵结论不能引用。
- **关闭条件**：在 3.10/3.12 解释器或 CI 上运行 compileall/pytest/定向套件。
- **验证**：3.10/3.12 日志 + exit code。

---

## 6. P2 证据完整性缺陷

### ACP2-DEF-017 — `summary.json` 顶层仍为旧候选/旧判定

- **严重度/状态**：P2 / OPEN
- **证据**：`reports/acp2_final_acceptance_20261024/summary.json` 顶层 `candidate_revision=f6dd3c9`、`verdict=NOT_APPROVED`；`ec4a0b3` 只在 `remediation` 附加块。
- **影响**：最终验收摘要没有按 HEAD 重新生成；读者可能误以为整改未发生，或误把附加块当作新总评。
- **关闭条件**：重新生成顶层为 `ec4a0b3` 的 summary，并保留历史 verdict 字段。

### ACP2-DEF-018 — 整改探针 candidate 标签 stale

- **严重度/状态**：P2 / OPEN
- **证据**：`evidence/frontend/probe-frontend-contracts-r2.json` 记录 `"candidate":"f6dd3c9..."`，但模板包含整改后的 v2 路由；生成时间 2026-10-25 01:16。
- **影响**：证据标签与实际代码不一致，审计无法仅凭该文件证明整改候选。
- **关闭条件**：用 `--candidate ec4a0b3` 重跑并保留旧文件。

### ACP2-DEF-019 — r3 发行/安装证据 untracked；h7-index/summary 未提交

- **严重度/状态**：P2 / OPEN
- **证据**：`git status --short` 显示 `reports/acp2_final_acceptance_20261024/` untracked；`h7-index.json`、`integration/summary.json` 修改未提交；`B25` 多数 release tarball untracked。
- **影响**：HEAD 的安装 PASS 证据不在版本控制中；工作树与 HEAD 不一致。
- **关闭条件**：提交或冻结 r3 发行/安装证据；提交 h7-index/summary；明确不提交的临时文件。

### ACP2-DEF-020 — 三套发行制品并存；版本清单指向非 HEAD 祖先构建

- **严重度/状态**：P2 / OPEN
- **证据**：
  - `release/` 源 `a84c4266...`；`git merge-base --is-ancestor a84c426 HEAD` 为 **false**；
  - `release-fix5/` 源 `6ec01ec9...`；
  - `release-r3/` 源 `ec4a0b3`（HEAD）；
  - `ACP_2.0_Release_Version_List_20261023.md:20-26` 的哈希只匹配 `release/`。
- **影响**：版本清单与当前候选脱节；三个制品代的哈希不同，发布时可能误用旧包。
- **关闭条件**：为 `ec4a0b3` 生成新的 release version list，标明旧套件历史；发布说明只引用 HEAD 套件。

### ACP2-DEF-021 — h7-index/manifest stale；science 契约未登记总 manifest

- **严重度/状态**：P2 / OPEN
- **证据**：
  - `h7-index.json:15-18,157,161` 仍写 A-29 absent，但 `docs/.../science/handoff-H7.json` 已 tracked；
  - `:246` 仍写 A science dependency fixture absent，但 `science/cross-track-dependencies.json` 已 tracked；
  - `manifest.json:633-637` `D-B03-02 a_dependency_fixture_absent` 与事实矛盾；
  - `manifest.json.contracts[]` 不含 `acp2-contracts/science/*`，A-03/B03 的合并要求未完成。
- **影响**：索引/契约台账与当前树矛盾；A 契约不可被总 manifest 消费。
- **关闭条件**：刷新 h7-index；删除/修正 stale absent 字段；把 14 个 science 契约登记入总 manifest；官方 validator 仍通过。

### ACP2-DEF-022 — A 证据根不符合文档；A 计划 untracked 且 post-R6

- **严重度/状态**：P2 / OPEN
- **证据**：
  - A 证据在 `/ACPServer/acp2-evidence/a-science-20261022/A/`；仓库无 `reports/acp2_implementation/bb45a46/A/`；
  - `.omo/plans/acp2-plan-a-cccp-science.md` untracked；当前 sha `4637947a`，R6 审查快照 `7473ab3d`；51/51 `[x]` 为审查后编辑。
- **影响**：证据根不符合计划；索引仍报 A-20/A-29 absent；计划状态不可审计。
- **关闭条件**：按文档迁移/链接证据，或正式修订证据根契约；提交 A 计划并记录 R6 后的变更。

### ACP2-DEF-023 — 全量回归证据未绑定 commit

- **严重度/状态**：P2 / OPEN
- **证据**：`evidence/nonslow-r2.log` 生成于 04:16，整改 commit `ec4a0b3` 为 04:19；XML 无 candidate 字段。
- **影响**：不能仅凭证据文件证明它对应 `ec4a0b3`；需要工作树/命令记录辅助。
- **关闭条件**：重跑并写入 candidate/tree；或补充运行命令、环境、工作树指纹。

---

## 7. P3 文档/测试卫生缺陷

### ACP2-DEF-024 — `REPORT.md §1.2.2` 对 m1 的描述与代码不符

- **严重度/状态**：P3 / OPEN
- **证据**：
  - `REPORT.md:74` 称 `POST /api/v1/recover` 注册；
  - 实际 `controller_client.py:680-699` 抛 `UnsupportedError`；`application-api.v1.json:2073-2085` 标 `not_delivered`、`transport_binding:none`。
- **影响**：m1 实际上按允许的 unsupported 分支 CLOSED，但报告句子会误导读者以为 HTTP 路由存在。
- **关闭条件**：把报告改为“保留签名并显式 unsupported/not_delivered；未注册 HTTP 路由”。

### ACP2-DEF-025 — 测试二次挂载 science/research router，重复 Operation ID

- **严重度/状态**：P3 / OPEN
- **证据**：
  - `tests/test_acp2_v2_science_routes.py:45-53` 在 `create_app()` 已挂载 `v2_science.router` 后又 `include_router`；
  - `tests/test_acp2_v1_science_status.py:50-55`、`tests/test_acp2_research_read_routes.py:107-111` 同样二次挂载；
  - 测试出现重复 OpenAPI Operation ID 警告；
  - 真实 `create_app()` 探针无重复，产品路由正常。
- **影响**：测试没有真正证明 `server.py` 的挂载；重复 Operation ID 会污染 OpenAPI。
- **关闭条件**：删除测试中的二次 `include_router`，直接用真实 app；断言 OpenAPI 无重复 operationId。

---

## 8. 已验证关闭项（不计入缺陷）

| 项目 | 状态 | 证据 |
|---|---|---|
| B1-0..B1-2、B1-4..B1-7 路由/读模型 | CLOSED | `tests/test_acp2_api_science_routes.py` 等 8 套件 151 passed；真实探针 7/7；304 项定向复跑 |
| B1-3 预检/提交闭环 | PWC | 规范化请求/fingerprint/stale/并发逻辑已闭合；真实 XYZ/resources 闭环 = DEF-011 |
| B1-8/B1-9 | PWC | fixtureMode/inputRevision/Abort/tickToken/digest/eventInvalidation.init 已落地；panel live 未验证（DEF-008） |
| M1 MCP 幂等 | CLOSED | 双进程重放、bare-digest 403、审批正例 |
| M2 fixture 分类 | CLOSED | experience b18 fixture + `initial_inputs` allowlist |
| M3 `paths.py` 登记 | CLOSED | `test_f4_scope_audit.py` |
| M4 `tickToken` | CLOSED | `scientific-evidence.js:785-800` |
| M5a/M5b | CLOSED | origin hermeticity + fake-module token pairs |
| m1 recover | CLOSED（unsupported 分支） | `test_acp2_controller_client_recover.py`；契约同步 |
| m5 metadata | CLOSED | `metadata-version-r2.log`；0.3.0 ×4 |
| R8 N1/N2/N3 | CLOSED | `verified_release_facts`、mixed-cache、in-flight preflight、key collision 测试 |
| R9-1/2/3 | CLOSED | local sidecar、verified-half、client_request_digest |
| R1–R4/R6 | CLOSED / PWC | R1 真实 backend 闭环 = DEF-011；R2/R3/R4/R6 CLOSED |
| 全量回归（仓库证据） | PASS | `nonslow-r2.xml` 11,344 passed / 0 failed / 11 skipped |
| 前端 Node | PASS | `node-all-r2.log` 444 passed |
| RPH | PASS | 80 tests、boundaries PASS、tsc exit 0 |
| HEAD r3 四角色安装 | PASS | 16 PASS / 3 expected NOT_VERIFIED；四个 venv；MCP 8 工具；Lab 真实 H2/STO-3G |
| 版本 | PASS | `0.3.0` ×4 |
| 契约 validator | PASS | `validate_acp2_contracts.py` → VALID |

---

## 9. 门禁追溯矩阵

### 9.1 P0–P5

| 阶段 | 门 | 结果 | 缺陷 |
|---|---|---|---|
| P0 | H0 / H1 / H2 | H0 PASS；H1/H2 NOT_VERIFIED | DEF-002、DEF-003 |
| P1 | H3 | NOT_VERIFIED | DEF-004、DEF-012、DEF-014 |
| P2 | H4 | NOT_VERIFIED | DEF-005、DEF-013、DEF-015 |
| P3 | H5 | PASS（A/B 归因）；C26–C28 未验证 | DEF-015 |
| P4 | H6 | NOT_VERIFIED | DEF-006、DEF-015 |
| P5 | H7 | NOT_VERIFIED | DEF-007、DEF-009、DEF-010 |

### 9.2 H0–H7

| 门 | 状态 | 主要缺陷 |
|---|---|---|
| H0 | CLOSED / PASS | — |
| H1 | OPEN / NOT_VERIFIED | DEF-002、DEF-021 |
| H2 | OPEN / NOT_VERIFIED | DEF-003 |
| H3 | OPEN / NOT_VERIFIED | DEF-004、DEF-012、DEF-014 |
| H4 | OPEN / NOT_VERIFIED | DEF-005、DEF-013 |
| H5 | CLOSED / PASS | C 侧 DEF-015 |
| H6 | OPEN / NOT_VERIFIED | DEF-006、DEF-015 |
| H7 | OPEN / NOT_VERIFIED | DEF-007、DEF-009、DEF-010 |

### 9.3 四组分 §11 八项验收门

| # | 门 | 结果 | 说明 |
|---|---|---|---|
| 1 | 模块独立 | PASS | CCCP 独立 wheel；15 任务无 ACP 泄漏 |
| 2 | 三入口等价 | NOT_VERIFIED | CLI/HTTP 同任务 PASS；Workbench/DSH 缺失（DEF-004、DEF-008） |
| 3 | 科学契约 | NOT_VERIFIED | 构造 validator PASS；真实 DA TS/IRC 缺失（DEF-005、DEF-011） |
| 4 | 执行完整性 | NOT_VERIFIED | B19 本地 PASS；当前 HEAD LSF/B08 acceptance 未重跑（DEF-012） |
| 5 | 数据与授权 | PARTIAL | B15/B23 历史 PASS；生产多用户/预算 live 未验证（DEF-013、DEF-015） |
| 6 | 插件生态 | PASS | H5；C 侧未归因（DEF-015） |
| 7 | 新手体验 | PARTIAL / NOT_VERIFIED | 路由已修；面板 live/静态资产缺失（DEF-008、DEF-009、DEF-010） |
| 8 | 科学可复现性 | PARTIAL | 本地 provenance PASS；DA/H6 未验证（DEF-005、DEF-006、DEF-007） |

---

## 10. 放行/关闭标准

完整 ACP2 放行必须同时满足：

1. **DEF-001**：owner 签署方案 A 或 B；方案 B 逐行分类 implementation/install/live，release note 列出实际 PASS 集合。
2. **DEF-002..DEF-010**：全部关闭，或由 owner 在方案 B 中明确 `DEFERRED(scope)`；不得把 `NOT_VERIFIED` 改写为 PASS。
3. **DEF-011..DEF-016**：完整范围必须关闭；缩范围必须在 release note 中逐行注明。
4. **DEF-017..DEF-023**：证据/索引/契约刷新并以 `ec4a0b3` 为 candidate 提交或冻结。
5. **DEF-024..DEF-025**：发布前修正。
6. 重新生成 `summary.json` 顶层为 `ec4a0b3`；刷新 `h7-index.json`/`H7-receipt.md`；提交 r3 发行/安装证据；登记 science 契约；修正 release version list。
7. 最终验收报告明确列出：实际 PASS 集合、`DEFERRED(scope)` 行、`NOT_VERIFIED` 行、以及不可签署的范围。

---

## 11. 复现与证据命令

```bash
# 1) 定向整改套件（本次独立复跑 304 passed）
ACP_RUN_ROOT=/tmp/acp-accept-targeted ACP_PUBLIC_ORIGIN='' PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m pytest -q --no-header -p no:cacheprovider \
  tests/test_acp2_api_science_routes.py \
  tests/test_acp2_v2_science_routes.py \
  tests/test_acp2_v1_science_status.py \
  tests/test_acp2_research_read_routes.py \
  tests/test_acp2_preflight_fingerprint.py \
  tests/test_acp2_science_projection.py \
  tests/test_acp2_controller_client_recover.py \
  tests/test_acp2_research_resource_key.py \
  tests/test_acp2_science_h6_gate.py \
  tests/test_f4_scope_audit.py \
  tests/test_frontend_sync_viewer_framing.py \
  tests/test_services_local_boundary.py \
  tests/test_acp2_runtime_persistent_idempotency.py

# 2) 路由探针（当前 HEAD）
ACP_PUBLIC_ORIGIN='' PYTHONDONTWRITEBYTECODE=1 \
python3.11 reports/acp2_integration_20261024/probe_frontend_contracts.py \
  --output /tmp/probe-ec4a0b3.json \
  --candidate ec4a0b39b26645139c01aa9578fbedd0c3db23a2

# 3) 契约校验
PYTHONDONTWRITEBYTECODE=1 python3.11 scripts/acceptance/validate_acp2_contracts.py

# 4) 前端 / RPH
node frontend/js/module_tests/run_all.js
cd rph && /ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node scripts/run-tests.mjs
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node scripts/check-boundaries.mjs
node_modules/.bin/tsc -b

# 5) 全量回归（仓库既有证据）
# reports/acp2_final_acceptance_20261024/evidence/nonslow-r2.xml
# 11,344 passed / 0 failed / 11 skipped
```

---

## 12. 限制与未执行项

1. 本报告未重启生产 `acp.service`，未触碰 `/var/lib/acp/runs`，未执行真实 DSH/模型/LSF/QC 计算。
2. 本次独立全量回归启动后在 15%（0 失败）处因耗时终止；全量结论采用仓库既有 `nonslow-r2.xml` 证据，并以 304 项关键整改用例独立复跑补强。
3. 未对 A 外部证据根做全量重算；H7 artifact/fixture/plugin digest 已由独立复核逐项哈希验证。
4. 工作树存在未提交修改与 untracked 证据；本报告不修改这些文件，仅记录其审计影响。
5. 本报告不构成 owner 签收，也不改变任何 H 门或 `NOT_VERIFIED` 状态。
