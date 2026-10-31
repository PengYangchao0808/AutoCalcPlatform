# ADR-003 — TaskRef 归一、digest-bound 证据投影与独立 ResearchDecision

- **状态**：Accepted（2026-10-27，M1-03 契约冻结）
- **Owner**：A（科学 schema/validator）+ B（唯一平台投影/TaskRef 归一）+ C-RPH（唯一 `EvidenceView`/`ResearchDecision` 类型与归属 adapter）
- **来源任务**：`reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md` M1-03（G07/G08）；前置 M0-02、M1-01、M1-02
- **契约沿革**：消费 `docs/reference/next-change/acp2-contracts/runtime/research-session.v1.md`（`task_binding`/`evidence_ref`/`decision` 字段已冻结）与 `runtime/application-api.v1.json::references.task_ref`；本文不新增第二个研究身份、不重定义 `evidence_ref`、不改 `ResearchRecord` 已冻结字段
- **代码驻点**：
  - A：`src/cccp/analysis/research/evidence.py`（`EvidenceReport`/`Claim`/`ClaimStatus`/`verify_claim_sources`/`reject_model_conclusion_as_evidence`）
  - B：`src/acp_services/runtime/evidence_projection.py`（新增，唯一平台投影 + TaskRef 归一 + 决策策略）；`src/acp_services/runtime/science_binding.py`（B18 注册检查被投影复用）；`src/acp_services/runtime/worker.py::task_result_content_digest`（`task_result.json` 唯一 canonicalization 的公开 seam）
  - C-RPH：`rph/packages/research/src/evidence.ts`（唯一 `TaskRef`/`EvidenceView`/`ResearchDecision`/`DecisionRecord`）；`rph/packages/research/src/memory.ts`（只持久化引用/决策，不再持有平行报告模型）；`rph/packages/bridge/src/index.ts`（`attributeTaskRef`/`attributeEvidenceView`：解析 + 归属，无第四份模型）
- **证据**：
  - `tests/test_acp2_evidence_projection.py`（35 例：字段/映射表、全部负例、单解析路径守护、样本双端回放）
  - `rph/test/evidence.test.ts`（11 例：同一 fixture 集的 TS 侧回放；`memory.ts` 无第二报告模型）
  - `rph/scripts/check-boundaries.mjs`（新增 `duplicate_evidence_model` 与 bridge 重声明规则）
  - 样本：`docs/reference/next-change/acp2-contracts/runtime/samples/evidence-projection/`（正/负例 23 个）+ `tests/fixtures/acp2/science/evidence_projection/`（A 侧科学报告正/负例）

## 1. 背景（G07/G08）

1. **TaskRef 二义**：平台 `application-api.v1.json` 的 `task_ref` 是
   `{task_id, attempt, release:{release_id, release_digest}}`（`job_id` 为 legacy 别名），
   而 M1-02 的 `research-session.v1` task binding 与 C 的
   `ResearchRecord.task_refs` 都是扁平 `{project_id, job_id, attempt, release_id}`
   —— **没有 release digest**。旧实现（`research_session._binding_from_projection`）
   在缺失 `attempt`/`release` 时直接放弃绑定或退回默认值，导致“同一 attempt”不可证。
2. **投影多点**：B18 的 `science_binding.validate_evidence_report` 自带一套 claim 词汇/
   结构校验，G07 要求“CCCP 原件 → 唯一 adapter → RPH”。两套解释器就是两个事实源。
3. **“有返回值 = 通过”**：worker 已冻结 `qualification` 真值表（`complete`/
   `partial_result`/`incomplete_result`/`task_failed`），但消费侧没有把
   worker qualification、step outcome、publication status 合成唯一
   `{complete, partial, incomplete, failed}`，也没有把
   `inconclusive`/`not_evaluated`/blocking missing check 与 `accept` 解耦。
4. **第四份报告模型**：`rph/packages/research/src/memory.ts` 自带
   `EvidenceReport`/`validateEvidenceReport`，与 `dsh-bundle/skills/models.ts` 的
   skill 信封、A 的 `evidence.py`、B 的注册检查并存——消费方可以各拿一份“真相”。

## 2. 决策

### D1 — TaskRef 显式归一，`attempt`/`release` 缺失即拒绝

| 概念 | 平台（B） | RPH（C） |
| --- | --- | --- |
| 归一类型 | `evidence_projection.TaskRef` | `research.evidence.TaskRef` |
| 字段 | `project_id, task_id, job_id(=task_id), attempt, release_id, release_digest` | 同构：`project_id, task_id, job_id, attempt, release{release_id, release_digest}` |
| 解析入口 | `parse_task_ref(payload, project_id="")` / `TaskRef.from_dict` | `parseTaskRef(value, projectId="")` |
| 记录绑定 → TaskRef | `TaskRef.from_task_binding(binding, release_digest=...)` | `taskRefFromRecordRef(ref, releaseDigest)` |

冻结规则（两端逐条一致，`samples/evidence-projection/task-ref*.json` 双端回放）：

| 情形 | 结果 |
| --- | --- |
| `attempt` 缺失 | `task_ref_missing_attempt`（**永不**默认 1） |
| `attempt` 为 `bool`/非整数/`< 1` | `task_ref_missing_attempt` / `task_ref_invalid_attempt` |
| `release` 缺失或 `release_id` 空 | `task_ref_missing_release`（**永不**默认空） |
| `release_digest` 缺失 | `task_ref_missing_release_digest` |
| `release_digest` 非 `sha256:<64 hex>` | `task_ref_invalid_release_digest` |
| `task_id` 与 `job_id` 不一致 | `task_ref_alias_conflict` |
| 记录/绑定（无 digest）单独当 TaskRef 用 | 拒绝（`taskRefFromRecordRef`/`from_task_binding` 必须显式传 digest） |

`release` 接受冻结的嵌套对象（`release_id`/`release_digest`，或 `id`/`digest`）
与 M1-02 binding 的扁平 `release_id`/`release_digest`；输出一律为嵌套冻结形状。

### D2 — B 侧唯一投影实现，只消费 digest-bound 事实

`acp_services.runtime.evidence_projection.project_evidence(...)` 是平台唯一的
“attempt → 可消费证据”入口，`acp_services/**` 不 import `acp`：

1. **报告**：调用方必须给出 **报告字节** 与声明的 `report_digest`；`sha256(bytes)`
   必须相等（`report_digest_mismatch`）。传入已解析 mapping = `report_not_digest_bound`。
2. **单一解析路径**：`parse_evidence_report` 先走冻结的 B18 注册检查
   （`science_binding.validate_evidence_report`，claim 词汇/结构），再走 A 的封闭
   schema parser（`cccp.analysis.research.evidence.EvidenceReport.from_dict`），
   两层都不通过即 `invalid_science_report`。平台内**没有**第三个 evidence parser
   （`test_no_other_platform_module_parses_an_evidence_report` 守护）。
3. **来源 digest**：A 的 `verify_claim_sources(report, read_root)` 逐条校验
   claim source（`evidence_digest_mismatch`/`evidence_source_unavailable`/`evidence_path_refused`）。
4. **task_result**：`schema == acp_task_result_v1`，`content_digest` 必须由
   `worker.task_result_content_digest` 重算相等（`task_result_not_digest_bound`）。
5. **receipt**：`job_id`/`attempt`/`release_id`/`release_digest` 必须等于 TaskRef
   （`foreign_job`/`foreign_attempt`/`foreign_release`/`foreign_release_digest`），
   且 `evidence_digests["task_result_digest"]` 必须指向该 task_result
   （`receipt_not_digest_bound`）——**存在返回值不是 accepted**。
6. **归属**：`task_ref.project_id` 必须等于服务端 `ResearchIdentity.project_id`
   （`foreign_project`）；`verify_attribution` 再拒绝 `foreign_research`/`foreign_round`/
   `foreign_task_ref`。报告的 `execution.terminal_state` 必须与 receipt 状态一致
   （`blocked`→`failed`），否则 `execution_state_conflict`。
7. **模型文字**：`reject_model_conclusion_as_evidence` 在解析前执行；A 的
   `Claim`/`EvidenceValidator` 也只接受 `check.`/`software.`/`human.` 命名空间。

投影只携带引用/状态，不携带报告正文：`EvidenceView.report.digest` 是唯一正文入口，
`projection_digest` 覆盖整个视图（`EvidenceView.from_dict` 重算并拒绝
`projection_digest_mismatch`）。`evidence_status` 是执行完整性，永远不等于
`claim_status`；投影里没有 `claim_supported` 字段。

### D3 — 冻结 `{complete, partial, incomplete, failed}` 映射

| 维度 | 输入 | 映射 |
| --- | --- | --- |
| worker qualification | `complete` / `partial_result` / `incomplete_result` / `task_failed` | `complete` / `partial` / `incomplete` / `failed` |
| step outcome（`StepStatus`） | `completed`；`failed`/`blocked`/`upstream_failed`/`cancelled`；`pending`/`ready`/`running` | `complete`；`failed`；`incomplete` |
| publication（`PublishStatus`） | `published` / `not_published` / `failed` | `complete` / `incomplete` / `failed` |
| receipt 状态 | `completed` / `failed` / `cancelled` / `blocked` | `complete` / `failed` / `failed` / `failed` |

合成规则：**worst-of**（`failed > incomplete > partial > complete`），未知取值即
`unknown_qualification`/`unknown_step_status`/`unknown_publish_status`，永不升级。
未提供的维度不参与合成（缺失不是 0 分，也不是满分）。

### D4 — 决策策略：只有 `accept` 有前置条件

决策词汇 = `research-session.v1` 冻结的 `accept`/`revise`/`reject`/`defer`/`escalate`
（`DECISION_VALUES` 单一来源），与任何 task/job 状态不相交；独立类型 =
`ResearchDecision`（B 记录）/ `DecisionRecord`（C 信封），不复用 Record 的
`decision` 字段语义。`accept` 前置条件按冻结顺序：

| # | code | 条件 |
| --- | --- | --- |
| 1 | `execution_not_completed` | 报告执行 `terminal_state == completed` |
| 2 | `evidence_not_complete` | `evidence_status == complete`（`partial` 也不行） |
| 3 | `blocking_missing_check` | 无 blocking missing check（比 claim 状态更具体，先报） |
| 4 | `claim_status_not_supported` | `overall_claim_status == supported`（`refuted`/`inconclusive`/`not_evaluated` 显式拒绝） |
| 5 | `evidence_ref_required` | 至少一条 digest-bound `evidence_ref`（任何决策都需要） |

`revise`/`reject`/`defer`/`escalate` 始终可用——它们正是 `partial`/`incomplete`/
`refuted`/`inconclusive` 的诚实答案。决策信封**封闭**：字段集固定，理由只有
派生 `rationale_codes`（`evidence_partial`、`claim_inconclusive`、
`blocking_missing_check`…），没有 `reason`/`summary` 之类自由文本字段——模型文字
无法成为决策理由（`decision_field_not_allowed`）。

### D5 — C-RPH：唯一 `EvidenceView`/`ResearchDecision` 类型，bridge 只做归属

- `rph/packages/research/src/evidence.ts` 定义唯一 `TaskRef`/`EvidenceView`/
  `ResearchDecision`/`DecisionRecord` 与已冻结的 `RESEARCH_DECISIONS`（从
  `record.ts` 迁入，`record.ts` 只消费），并保留 `EvidenceReport` 消费模型
  （同一模块，不产生第二份字段模型）。
- `record.ts::RecordTaskRef` 明确**不是** TaskRef：记录引用无 release digest，
  只能经 `taskRefFromRecordRef(ref, releaseDigest)` 显式升级。
- `rph/packages/bridge/src/index.ts` 只做 `attributeTaskRef` /
  `attributeEvidenceView`（解析 + `foreign_project`/`foreign_research`/`foreign_round`
  归属），不声明证据类型、不实现决策策略。
- `research/memory.ts` 只持久化投影引用与决策；其平行 `EvidenceReport` validator
  已删除，`FORBIDDEN_ARCHIVE_FIELDS` 移到 `evidence.ts` 单点定义。

### D6 — 静态守护

| 守护 | 位置 | 规则 |
| --- | --- | --- |
| 第四份报告模型 | `rph/scripts/check-boundaries.mjs::duplicate_evidence_model` | `packages/*/src` 中声明 claim 词汇/report interface/`validateEvidenceReport` 的模块只允许 `research/src/evidence.ts` 与显式声明的 legacy skill 信封 |
| bridge 只做 adapter | `check-boundaries.mjs::{bridge_evidence_model,bridge_evidence_policy}` | bridge 禁止 `export interface EvidenceView/TaskRef/ResearchDecision/DecisionRecord` 或 `validateEvidenceView`/`buildDecisionRecord`/`parseTaskRef` |
| 单解析路径 | `tests/test_acp2_evidence_projection.py` | monkeypatch 观察注册检查 → A parser 的调用顺序；扫描 `src/acp_services`/`src/acp` 只允许投影模块引用 `EvidenceReport.from_dict` |
| 双端向量一致 | `tests/test_acp2_evidence_projection.py::test_rph_fixture_copies_match_the_contract_samples` | `rph/fixtures/evidence-projection/` 与契约样本字节一致（含 provenance.json digests） |
| 投影不可篡改 | `EvidenceView.from_dict` + TS `validateDecisionRecord` | `projection_digest` / `foreign_projection` |

## 3. 三 Owner 分工

| 面 | Owner | 唯一实现 |
| --- | --- | --- |
| 科学 schema、claim 词汇与 evidence floor、claim source digest 校验、模型文字拒绝 | **A** | `cccp/analysis/research/evidence.py` |
| TaskRef 归一、try 平台投影、状态映射、决策策略、digest 绑定 | **B** | `acp_services/runtime/evidence_projection.py`（+ `worker.task_result_content_digest`） |
| `EvidenceView`/`ResearchDecision` 类型、归属 adapter、研究记忆持久化 | **C-RPH** | `rph/packages/research/src/evidence.ts`、`rph/packages/bridge/src/index.ts`、`memory.ts` |
| 冻结契约与说明 | **A+B+C-RPH** | 本 ADR + `runtime/samples/evidence-projection/` |

B 永不发明科学默认（方法/电荷/自旋/阈值），A 永不定义平台归属或执行身份，
C-RPH 永不自造 claim 状态或把执行完成当成 supporting evidence。

## 4. 样本与 fixture

| 文件 | 类型 | 断言 |
| --- | --- | --- |
| `runtime/samples/evidence-projection/task-ref.positive.json` | 正 | 嵌套 release 归一 |
| `.../task-ref-flat-release.positive.json` | 正 | M1-02 扁平 binding 形状归一 |
| `.../task-ref-missing-attempt.negative.json` | 负 | `task_ref_missing_attempt` |
| `.../task-ref-missing-release.negative.json` | 负 | `task_ref_missing_release` |
| `.../evidence-view.positive.json` | 正 | 完整投影（digest/状态/引用） |
| `.../evidence-view-foreign-research.negative.json` | 负 | `foreign_research` |
| `.../evidence-view-foreign-round.negative.json` | 负 | `foreign_round` |
| `.../platform-attribution.positive.json` | 正 | project 归属一致 |
| `.../platform-attribution-foreign-project.negative.json` | 负 | `foreign_project` |
| `.../research-decision-accept.positive.json` | 正 | `accept` + rationale codes |
| `.../research-decision-revise-inconclusive.positive.json` | 正 | `inconclusive` 只能 revise |
| `.../research-decision-{partial,incomplete}-accept.negative.json` | 负 | `evidence_not_complete` |
| `.../research-decision-{inconclusive,refuted,not-evaluated}-accept.negative.json` | 负 | `claim_status_not_supported` |
| `.../research-decision-blocking-missing-check.negative.json` | 负 | `blocking_missing_check` |
| `.../research-decision-{foreign-project,foreign-research,foreign-round,foreign-release}.negative.json` | 负 | 四种 foreign 归属 |
| `.../research-decision-no-evidence.negative.json` | 负 | `evidence_ref_required` |
| `.../research-decision-model-text.negative.json` | 负 | `decision_field_not_allowed` |
| `tests/fixtures/acp2/science/evidence_projection/report.supported.positive.json` | 正 | A 报告（validator + digest-bound source + provenance） |
| `.../report.completed-inconclusive.positive.json` | 正 | 执行完成但整体 `inconclusive` |
| `.../report.digest-mismatch.negative.json` | 负 | `evidence_digest_mismatch` |
| `.../report.model-text.negative.json` | 负 | `invalid_science_report`（模型占 validator 槽） |

`rph/fixtures/evidence-projection/` 是上述样本的**逐字节副本**（`provenance.json`
记录 source + sha256），由 Python 守护测试防 drift；RPH 测试直接回放同一批向量。

## 5. 兼容与变更规则

- 本文对 `research-session.v1` 是 **additive**：不改 `evidence_ref`/`task_binding`/
  `decision` 字段，只冻结它们的消费形状与归一规则。
- 新增字段（例如 step 明细）是新契约版本；删除/重命名字段需要迁移说明与
  C 消费方确认。
- `release_id`/`release_digest` 的扁平形状保留为**读入**兼容；写出永远是嵌套冻结形状。
- 样本/契约在 `runtime/samples/manifest.json` 的注册与计数由 orchestrator 在
  M1 汇总时完成（本任务只新增文件，不改已注册内容）。

## 6. 遗留（不在 M1-03 关闭）

1. `rph/packages/dsh-bundle/skills/models.ts` 仍声明 model-facing skill 信封
   （`acp2.evidence.report.v1`，`status` 含 `missing`，`decision` 用
   `accept|revise|stop`）。它不持久化、无 digest 语义，已被静态守护显式登记为
   legacy；与唯一 `EvidenceView` 的收敛属 M3-04（Supervisor）范围，不在本任务
   改写已冻结的 skill 契约。
2. B18 `register_evidence_report` 仍使用较宽松的注册检查（其 fixture 的
   `supported`/`inconclusive` claim 不满足 A 的严格 evidence floor）。投影路径
   已强制严格解析；把注册路径也切到严格 parser 需要更新 A 的 B18 fixture，
   留给 A/orchestrator 决定。
3. C-Web 尚未消费 `EvidenceView`（M1-06/M3-05 引入真实响应消费者）。
4. 真实科学/模型/LSF 调用未验证；本 ADR 的全部证据为 STATIC fixture。
