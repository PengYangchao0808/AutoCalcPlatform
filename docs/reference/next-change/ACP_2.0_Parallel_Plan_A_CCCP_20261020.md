# ACP 2.0 并行计划 A：CCCP 科学契约、能力与证据

**日期**：2026-10-20  
**性质**：交给科学 Agent 独立执行的逐步计划；本文不表示新增接口、测试文件或真实科学结论已经实现。  
**上位方案**：[四组分协同方案](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)。  
**服务器操作依据**：[MCP / DSH 本服务器联调手册](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)。  
**执行约定**：本批 debug 按完成后的冻结基线规划；启动时填实际 freeze SHA。A 不重做 debug，也不根据旧报告推断当前代码仍有同一缺陷。

**2026-10-21 基线**：用户确认 debug 完成，默认开工 revision 为 `3670790363c7a1959ad1dbe4b4297adec518952a`；其报告内代码候选 `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798` 单独登记。[总索引](ACP_2.0_Parallel_Execution_Index_20261020.md)规定实际范围、文件 Owner 与阶段依赖。

## 1. 交付范围、写入边界与执行顺序

A 是所有新增科学类型、科学语义、能力声明、科学判据与科学 Fixture 的唯一 Owner。B 负责平台执行和本机环境；C 负责 RPH / Workbench。CCCP 可由 Python 独立调用，不依赖 ACP Services、账户、浏览器或 DSH。

**现有基线必须复用**：`src/cccp/calculation/{requests,context,results,contracts}.py` 已提供 `TaskRequest / TaskContext / TaskResult`；任务核心在 `calculation/tasks/`；`plugin/{metadata,discovery,host,sdk}.py` 已有插件发现、启用和调用边界；`analysis/` 已有结构、PES、频率、热化学、NMR 和有界科学产品。新增 Capability / Recipe 是科学描述与现有 API 的适配，不能成为第二执行站。

| A 可直接写 | A 不可直接写；交给 Owner 的方式 |
|---|---|
| `src/cccp/**`，但不改版本常量或发行共享配置 | `src/acp/**`、`src/acp_services/**`、`frontend/**`、RPH 目录：交 B / C 提供接口请求和测试 Fixture |
| `tests/test_acp2_science_*.py` | 旧测试文件、`tests/conftest.py`、共享测试配置：A 提供 patch，B 审查并应用 |
| `tests/fixtures/acp2/science/**`；拟新增 `scripts/acceptance/acp2_science.py` | `tests/baseline/**`、旧恢复 Fixture、模型资产和 notices：保留；发现兼容缺口记请求，不覆写历史证据 |
| 本计划；`docs/reference/next-change/acp2-contracts/science/**`；科学专题说明 | 根 AGENTS、主方案、统一索引、共同 ADR、CI、`pyproject.toml`、`requirements*.txt`、四处版本号、默认 YAML：B 单写者；写 `science/requests/*.md` 或 `.patch` 交付 |
| `$ACP2_EVIDENCE/A/A-NN/**` 的运行证据 | 生产 `acp.service`、其数据库、运行目录、全局 Node/Python 和 SSH 配置：按 B 的隔离环境使用；不自行改动 |

所有新增生产路径在本计划中标记为“拟新增”；执行前先搜索冻结基线，若已有等价模块则扩展原实现，并在 `science/path-map.md` 记录最终落点。禁止先按新名字复制一套实现。`__init__.py` 只导出；不在 `src/cccp` 增设 CLI / `__main__.py`。QC 接口层如必须修改，先向 B 交 F4 amendment 请求，原护栏不放宽。

**顺序**：P0 为 A-00～08；P1 为 A-09～13；P2 为 A-14～20；P3 为 A-21～23；P4 为 A-24～26；P5 为 A-27～29。任务前置按编号列出；没有前置的独立动作可以提前做，但不能提前把阶段记 PASS。共 30 项。

### 1.1 共享交接点

| 交接点 | A 必须提供 / 消费 |
|---|---|
| H0 基线 | 消费 B 发布的 debug freeze SHA、实际源码位置、共享文件 Owner、证据根；A 输出兼容基线 |
| H1 契约 | A-01 先发布基础能力/Task binding/错误正负 Fixture；A-02～03 增量发布反应与证据草案；B / C 不等待完整反应模型即可并行消费 |
| H2 P0 | A-08 关闭基础能力/Task binding 静态门；反应/Recipe 预研不阻塞该门；消费 B 的环境清单与 C 的真实 MCP / DSH 握手证据；A 不把协议握手当科学验证 |
| H3 P1 | A-13 提供 Local / LSF 小科学结果和独立分析；B / C 提供同一任务的三入口对照 |
| H4 P2 | A-20 提供限定 DA 的 TS / 频率 / 双向 IRC / 可比性证据；B 提供审批、预算和恢复；C 提供研究记录和界面 |
| H5 P3 | A-23 提供第三方科学插件无需改核心的实证；B / C 完成平台、动态发现及表单消费 |
| H6 P4 | A-26 提供 NMR / 热化学与选择性 Fixture 的一致性及科学回归；B / C 提供多项目和体验证据 |
| H7 P5 | A-29 提供独立安装的科学 API / SDK / 插件文档和证据；B 形成发行物，C 提供使用入口 |

### 1.2 每项任务的记录与命令约定

1. B 提供 `$ACP2_SOURCE`（冻结的测试源码）、`$ACP2_PYTHON`（隔离解释器）、`$ACP2_EVIDENCE`（本轮证据根）。A 在命令前进入 `cd "$ACP2_SOURCE"`；环境变量只含路径与无密钥标识。
2. 每项证据固定在 `$ACP2_EVIDENCE/A/A-NN/`，至少含 `result.json`、真实执行 argv、退出码、日志和涉及输入/输出 SHA-256。`result.json` 记录 freeze/integration SHA、契约版本、前置、断言和 `PASS / FAIL / NOT_VERIFIED`；另记 scope。必需场景跳过不能关闭阶段门。
3. 下文新 `tests/test_acp2_science_*.py`、科学 Probe 和 Schema 均是**待该任务交付**的接口；命令只有在文件已交付后才可执行。缺文件返回缺口，不能以空集合或 `--collect-only` 代替通过。
4. 纯 Schema / 单元测试可以并发；本机环境安装、服务启动、真实 QC 与集群作业由 B 统一排程。先在 B 的 Fixture 登记表申请 `fixture_digest + recipe_digest + environment_digest + scenario`，取得唯一 run key。A、B、C 对同一昂贵场景读取同一组受控任务和回执；独立性测试需要额外运行时单独登记。
5. 开跑前填写实际的 `cpu_cores / memory_mb / per_step_timeout / total_walltime / max_attempts / max_candidates`；采用有限候选和逐步预算，禁止不限量 TS 搜索。小分子 singlepoint 优先，之后 optimize / frequency，最后限定 DA。扩展预算须有新的审批记录，不能在超时后静默放宽。
6. 科学测试保留既有 `--run-slow --run-integration` 门控；先由 B 配好 `CONFSEARCH_<SOFTWARE>_PATH` 等真实软件路径，再运行。无二进制、许可或排队窗口记录 `NOT_VERIFIED` 并继续不依赖它的任务；实际必需验收行继续开放。
7. 不执行 `git reset`、清理他人改动或切换共享工作区。若采用三工作树，由 B 发布集成 revision；A 不把自己的旧虚拟环境误当成集成源码。消费新契约时核对实际导入文件和 schema digest。

本文 `science/*.md`、`science/examples/**`、`science/requests/**`、`science/handoff-H*.json` 均是 `docs/reference/next-change/acp2-contracts/science/` 下的相对路径；执行证据一律写 `$ACP2_EVIDENCE/A/A-NN/`。统一证据根采用总索引的 `reports/acp2_implementation/<candidate-label>/`。

### 1.3 A 必须先交付的科学 Harness

拟新增 `scripts/acceptance/acp2_science.py` 由 A 单写，遵守 B01 发布的 `acp2.acceptance.v1` 证据格式；不是生产 CLI，也不接管平台作业。先支持 `--help / --dry-run` 与下列接口，再运行真实场景。`--dry-run` 仅核对路径和预算，不能产生 PASS。

```text
python scripts/acceptance/acp2_science.py
  --scenario <installed-singlepoint|validate-artifacts|validate-da-ts|validate-da-irc|validate-da-report|installed-examples>
  --fixture <fixture-manifest.json>
  --out <evidence-dir>
  --timeout <seconds>
  [--run-spec <run-spec.json>]
  [--input-manifest <authorized-artifacts.json>]
  [--dry-run]
```

- `installed-singlepoint` 通过 CCCP 公共 API 真运行，必须给 run-spec；其中固定解释器/源码或 wheel 摘要、可执行软件/版本、独立 native work-root、core/memory/time/candidate/attempt 上限和批准的 run key。没有这些字段拒绝运行。
- 其余 `validate-*` 只消费 B 显式 prepare/export 的授权工件清单。`input-manifest` 的 schema 由 B Owner 冻结，至少含任务/attempt/receipt 绑定、相对引用、实际本地 read-root、artifact schema/generation、SHA-256。A 不直接读中央 jobs DB、SSH 或生产任务路径。
- 科学 Fixture manifest 固定 schema/版本、输入文件/摘要、科学参数、预先通过判据、允许缺失项与范围；修改判据产生新版本。没有预先判据的输出不作为通过参考值。
- 实际场景返回码 `0=PASS, 1=FAIL, 2=NOT_VERIFIED, 3=usage`，同时落结构化回执。`--help/--dry-run` 自身成功退出 0，但场景未执行时不能写 PASS。已执行科学检查失败为 FAIL；缺软件/授权工件为 NOT_VERIFIED；缺必需参数是 usage。不得吞异常输出空成功。
- A 将 DA/optfreq 的实际执行需求写入 `science/requests/runtime-scenarios.md`，包含科学 TaskRequest DAG、输入摘要、检查点与预算。B 在自己的 Harness 登记最终场景 ID 和 argv 后，A/C 再使用；不得假定后文举例场景名已存在。

## 2. P0：早期契约交接与科学适配基线

### A-00：锁定科学现状与 debug 基线

- **前置**：H0。只接受实际提交 SHA；“预计完成”不充当 revision。
- **步骤**：①读 root、`src/cccp/AGENTS.md`、受影响子目录 AGENTS 和 `tests/AGENTS.md`。②记录 `git rev-parse HEAD`、`git status --short` 和三轨写入边界。③列出 15 active workflows、15 `TaskKind`，分别说明两张表的映射，不能假设名称一一相等。④核对现有任务 API、插件、科学产品、软件解析器和兼容转发。⑤只记录与本计划有关的真实缺口，不重开已完成 debug。
- **产物**：`science/baseline.md`、`science/path-map.md`、兼容矩阵，含真实 freeze SHA。
- **验证**：`git rev-parse HEAD`；`"$ACP2_PYTHON" -m pytest tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_recovery_fixtures_smoke.py -q`。
- **失败断言**：CCCP 导入 ACP、兼容 shim identity 改变、现有恢复 Fixture 失效或基线 SHA 不符，均不得进入科学迁移；交 B 处理集成，不绕过护栏。
- **证据**：`$ACP2_EVIDENCE/A/A-00/`。
- **交接**：向 B / C 发布现有能力与“未实现”清单；消费接口以实际源码为准。

### A-01：先交最小 Capability / Task 科学协议

- **前置**：A-00；可与 B 的环境配置、C 的 Fixture 页面同时开始。
- **步骤**：①在 `docs/reference/next-change/acp2-contracts/science/` 写 `capability.schema.json`、`task-binding.schema.json`、`errors.schema.json`。②冻结科学字段：`schema / schema_version / capability_id / implementation_id / input_schema / output_schema / preconditions / completion_checks / limitations / compatibility / provenance_requirements / execution_semantics`。③`TaskRequest / Context / Result` 引用既有类型与序列化；`TaskContext` 不进 JSON。④提供 `electronic.energy`→singlepoint、`geometry.optimize`→optimize、`vibration.compute`→frequency 的样例与错误码；`vibration.compute` 明确启动频率计算，`vibration.analyze` 则只读已有 Hessian/频率产物、不启动程序，两者输入前置与 side effect 不同。⑤定义“可描述”“已安装”“可用”“已科学验证”分开返回，发现操作不执行 QC 或插件加载。⑥交基础三任务各自有效/无效 Fixture，立即发布 `science/handoff-H1.json` 的最小版本，让 B/C 先行；完整反应类型不阻塞此交接。
- **产物**：上述 Schema；`science/examples/capability-energy.json`、`science/examples/task-binding-singlepoint.json`；`tests/fixtures/acp2/science/contracts/capability_{valid,unknown_version,duplicate_id,unsupported_provider}.json`（四个实际文件，不用花括号作运行路径）。
- **验证**：先交付 `tests/test_acp2_science_schema.py`，再运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_schema.py -k capability -q`；Schema 验证器使用现有依赖，若新增依赖先交 B 请求。
- **失败断言**：未知主版本、重复能力 ID、未知实现不能被静默修正；描述查询和 `vibration.analyze` 不得启动子进程；不能把分析输入不足静默变成重新计算。
- **证据**：`$ACP2_EVIDENCE/A/A-01/`。
- **交接**：立即向 B 提供 discover/preflight 响应字段，向 C 提供表单与动态工具元数据；不等后续科学执行完成。

### A-02：先交 ReactionSpec / QuestionSpec 的可消费 Schema

- **前置**：A-01 的 Schema 版本规则。
- **步骤**：①交 `reaction.schema.json`、`question.schema.json`，固定结构引用、物种、原子映射、键变化、电荷、自旋、立体、条件与输入不确定性。②`QuestionSpec` 固定问题类型、假设引用、需要的证据类型和科学比较条件，不含 LLM 角色、费用、job status。③定义原子 ID 在两端稳定、每端 map 唯一、元素与计量守恒；允许明确声明的未知立体/条件，不能偷偷补全。④提供有效简单反应与 duplicate-map、missing-product、charge-mismatch、ambiguous-stereo 的独立 JSON。⑤在约束说明里区分“无法解释输入”与“可表示但该 Recipe 不适用”。
- **产物**：科学 Schema、`science/examples/reaction-da.json` / `question-activation.json`；`tests/fixtures/acp2/science/contracts/reaction/` 正负 Fixture；`science/semantic-rules.md`。
- **验证**：交付 `tests/test_acp2_science_reaction_contract.py` 后运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_reaction_contract.py -q`。
- **失败断言**：重复 map、守恒不符拒绝；未知立体不能伪装为明确构型；净反应不得自动声称“只有一个基元步骤”。
- **证据**：`$ACP2_EVIDENCE/A/A-02/`。
- **交接**：B 校验/存储时保留 schema 和原始歧义；C 用 Fixture 做编辑/确认交互，不另定义 ReactionSpec。

### A-03：增量交 EvidenceReport / Provenance / 引用语义

- **前置**：A-01～02。
- **步骤**：①交 `evidence.schema.json`、`provenance.schema.json`、`recipe.schema.json` 的最小版本。②`EvidenceReport` 每项 claim 固定 `supported / refuted / inconclusive / not_evaluated`、validator ID/version、source refs、缺失检查与限制；执行终态不映射为 claim supported。③对接现有 `contracts.py::ArtifactRef / Provenance`；科学引用只用相对来源、内容摘要、单位和科学角色，平台 project/attempt/generation 在 B 的绑定层保留，存在时不可丢失。④提供 model/method/basis/software/plugin/temperature/standard-state/reference-state 派生关系。⑤交 completed-but-inconclusive、missing-IRC、corrupt-digest、partial-output 和历史 schema 的样例。⑥写 `science/handoff-H1.json`（A-01 先交基础版本，本项增量更新自己的文件版本），列文件摘要、字段 Owner、正负 Fixture、兼容范围；交 B 合并到总 manifest。
- **产物**：上述 3 个 Schema、`science/evidence-semantics.md`、`tests/fixtures/acp2/science/contracts/evidence/**`、H1 清单。最小 Recipe 已含 DAG/required/diagnostic-only/限制字段，后续只能版本化补充。
- **验证**：交付 `tests/test_acp2_science_evidence_contract.py` 后运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_schema.py tests/test_acp2_science_evidence_contract.py -q`。
- **失败断言**：缺科学来源或摘要不匹配不能 supported；LLM 文字不能冒充 validator 来源；未知 evidence 状态必须拒绝。
- **证据**：`$ACP2_EVIDENCE/A/A-03/`。
- **交接**：A-01 的 H1 最小包允许 B / C 提前开工；本项增量使 ResearchRecord/证据视图继续并行开发。B / C 仅序列化消费科学类型；契约变更先提版本与迁移，禁止各自修同名字段。

### A-04：实现科学公共类型与前后向读取

- **前置**：A-02、A-03 的科学 Schema 及 H1 基础版本规则；不以完整实现作为 H1 的前置。
- **步骤**：①将 A-02～03 科学类型落到拟新增 `src/cccp/analysis/research/{reaction,question,evidence,provenance}.py`；若冻结基线已有等价类型则适配并记录路径。②使用不可变 dataclass、显式 `to_dict/from_dict` 和版本检查。③复用 `StructureArtifact`、既有电子态/方法定义，避免另建不相容的原子/自旋模型。④历史读取采用可辨识 schema adapter；没有来源/字段的数据保留 unknown，不填假默认。⑤`__init__.py` 仅导出，导入纯类型不能载入 QC 程序。
- **产物**：上述拟新增生产模块和科学合同 Fixture 的完整 round-trip；类型映射文档。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_reaction_contract.py tests/test_acp2_science_evidence_contract.py tests/test_cccp_isolation.py -q`。
- **失败断言**：纯类型导入触发任务/软件执行；历史缺失字段被填成“已验证”；序列化前后 map 或单位变化，均失败。
- **证据**：`$ACP2_EVIDENCE/A/A-04/`。
- **交接**：B 直接调用 A 的校验器；C 可使用 Schema 生成客户端类型，但不再实现科学判定逻辑。

### A-05：实现元数据 Capability Registry

- **前置**：A-01；只登记基础三能力，不等待 A-04 的完整研究类型。
- **步骤**：①在拟新增 `src/cccp/calculation/capabilities/{contracts,registry,builtin}.py` 定义科学描述及只读查询。②先登记能量/优化/频率计算三能力；只读 `vibration.analyze` 复用 Analysis，对已有产物计算，不调用 frequency Task；其他家族仅声明未支持，不能因候选目录存在就开放。③分离 Capability 与 Implementation；明确 provider/method/state/version 适用条件。④复用 `calculation/selection.py` 和 `cccp.software.resolve_executable`，不另造软件解析器。⑤发现仅读元数据；安装检查和科学验证证据按来源独立投影。
- **产物**：能力 Registry、可排序的稳定 JSON catalog、`tests/test_acp2_science_registry.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_registry.py -q`。
- **失败断言**：同能力可有多个显式实现，不能按安装顺序任意选；冲突实现 ID、未知 Provider 和不兼容科学状态必须失败；catalog 查询不得 spawn/import 第三方代码。
- **证据**：`$ACP2_EVIDENCE/A/A-05/`。
- **交接**：B 用公开 registry 支撑 capability/preflight；C 用同一 catalog，不能靠软件名猜能力。

### A-06：把能力调用绑定到现有 Task API

- **前置**：A-05。
- **步骤**：①在拟新增 `capabilities/task_binding.py` 将 capability input 映射成既有 `TaskRequest`；只做类型转换与科学前置校验。②执行只委托现有 `run_singlepoint / run_optimize / run_frequency` 及其唯一 `tasks/` 实现。③保持旧 TaskRequest/TaskResult 版本和 payload 类型约束；新增字段使用版本化兼容策略。④写 spy 断言每条调用只到达一个既有任务实现，preflight 不执行。⑤输出整张 15 TaskKind 兼容矩阵，未注册能力不影响旧工作流。
- **产物**：拟新增科学适配模块、`tests/test_acp2_science_task_binding.py`、映射表。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_task_binding.py tests/test_cccp_task_singlepoint.py tests/test_cccp_task_optimize.py tests/test_cccp_task_frequency.py tests/test_architecture_invariants.py -q`。
- **失败断言**：一个能力请求触发两次 QC；preflight 产生工作产物；旧 payload 被新字段破坏；不支持能力偷偷降级成别的操作。
- **证据**：`$ACP2_EVIDENCE/A/A-06/`。
- **交接**：B 只接收科学 binding 输出后冻结为平台 Envelope；C 不直接调用 backend/tasks 内部函数。

### A-07：实现 Recipe DAG、规范化与科学身份

- **前置**：A-03、A-06；与 B 确认 plan/step identity 边界。
- **步骤**：①在拟新增 `src/cccp/calculation/recipes/{contracts,validation,compile}.py` 定义 ScientificRecipe：capability step、typed refs、edges、required、diagnostic_only、validators、适用范围和有限备选。②编译结果是科学 TaskRequest DAG；调度/持久化/平台重试仍由 B 完成。③固定 canonical JSON 和 schema digest，科学身份覆盖实际方法、结构内容、映射/角色、依赖摘要、provider/plugin 版本与比较条件；不含 job_id 或显示名。④复用已有 identity 逻辑可用部分，明确与 `plan_identity / step_identity` 的接合，不新增另一恢复真相。⑤测试环、悬空依赖、失败上游、诊断步被误当必需结果。
- **产物**：拟新增 Recipe 模块、`science/identity-boundary.md`、`tests/test_acp2_science_recipe.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_recipe.py tests/test_recovery_fixtures_smoke.py -q`。
- **失败断言**：原子映射/标准态/方法变更而身份不变；必需上游 blocked 却可完成；Recipe 中出现队列/账户/中央 job status，均失败。
- **证据**：`$ACP2_EVIDENCE/A/A-07/`。
- **交接**：B 接收科学 DAG 与 identity components，生成自己的 Envelope/Receipt；C 显示科学步骤和限制，不将 DAG 当第二调度器。

### A-08：关闭 P0 科学门，发布可消费版本

- **前置**：A-01、A-05～06；可引用 B02/B04～B06 环境/入口与 C07～C09 握手回执，不等待 B07 的 H2 总签收。A-02～04/07 是前置科学预研，可在环境等待期继续，不阻塞基础 P0/P1 门。
- **步骤**：①核对 H1 基础能力/Task Schema 与实现 round-trip、一致排序和负向样例；研究类型/Recipe 另列未完成进度。②补兼容读取说明和“未支持能力”清单。③将增加依赖、F4 amendment、共享版本/CI 请求放入 `science/requests/`，等 B 应用后验证集成 revision。④输出 `science/handoff-H2.json`，分别列科学静态 PASS、真实 QC 未运行、MCP/DSH 外部证据引用。⑤B / C 消费契约摘要后锁定该版，新增语义走下一版本。
- **产物**：P0 科学报告、契约版本冻结、SDK 示例草案。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_schema.py tests/test_acp2_science_registry.py tests/test_acp2_science_task_binding.py tests/test_architecture_invariants.py tests/test_cccp_isolation.py -q`。反应/Recipe/Evidence 的额外测试在对应任务实现后另记预研证据，不加入本门必需命令，避免等待尚未交付的测试文件。
- **失败断言**：Schema 校验成功但实际实现不接受同一 Fixture；消费端版本不同；缺握手证据却总门 PASS。
- **证据**：`$ACP2_EVIDENCE/A/A-08/`。
- **交接**：B 合并总 H2；C 以冻结数据做基础视图与工具桥接。后续真实计算不阻塞 C 的 Fixture 开发。

## 3. P1：真实基础科学任务及三入口一致

### A-09：冻结小分子 singlepoint 的科学 Fixture

- **前置**：A-01、A-06。静态 Fixture 在 H2 前即可交给 B07/C12；真实计算 A-10 仍等待 A-08 与实际环境。
- **步骤**：①选小型闭壳层分子，交原始 XYZ、电荷/多重度和原子顺序；方法/基组选择本机实际支持的一组，不依赖未安装 Provider。②交 `tests/fixtures/acp2/science/singlepoint/{input.xyz,request.json,expected-science.json,fixture-manifest.json}`。③expected 中分开解析正确、正常终止、能量存在/单位正确和允许的数值容差；未做实算前不填虚构参考能量。④提供非法电子数、缺结构、错误方法的 Fixture。⑤向 B 登记 Local 与 LSF run key，规定只做一次实际提交、多入口读同一引用。
- **产物**：可执行的真实 Fixture；另交 `science/requests/runtime-fixture-singlepoint.md` 描述 B 入口所需字段，不把 TaskRequest JSON 冒充 MCP submit body。
- **验证**：先交付 `tests/test_acp2_science_fixtures.py`，运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_fixtures.py -k singlepoint -q`。
- **失败断言**：改变 XYZ 内容后摘要未变；科学检查缺能量仍通过；非法自旋被默认纠正。
- **证据**：`$ACP2_EVIDENCE/A/A-09/`。
- **交接**：B 生成真实入口 submit JSON 与受控执行；C 用同一 Fixture 触发 MCP/DSH 流程，保留返回任务引用。

### A-10：本服务器执行独立 CCCP singlepoint

- **前置**：A-08、A-09；B 已发布隔离 Python、软件路径、预算和登记 run key。
- **步骤**：①先交科学测试 Harness `scripts/acceptance/acp2_science.py`（拟新增），只通过 CCCP 公共 API 调用既有 task，绝不操作中央 jobs DB。②提供 §1.3 的 `installed-singlepoint` 场景；执行前检查环境/预算，work-root 必须在 run-spec 显式给出。③在本服务器以单独运行目录执行；记录原始输出、TaskRequest/Result 和软件版本。④独立解析原生输出比对规范化能量/单位；不把 mock 或引用旧输出计新运行。⑤若依赖缺失返回 2 / NOT_VERIFIED。
- **产物**：拟新增测试 Harness、独立科学 API 的实际成功/失败回执。
- **验证**：文件先交付后运行 `"$ACP2_PYTHON" scripts/acceptance/acp2_science.py --help`；B 填好独立 work-root 与预算的 run-spec 后，运行 `"$ACP2_PYTHON" scripts/acceptance/acp2_science.py --scenario installed-singlepoint --fixture tests/fixtures/acp2/science/singlepoint/fixture-manifest.json --run-spec "$ACP2_EVIDENCE/A/A-10/run-spec.json" --out "$ACP2_EVIDENCE/A/A-10/probe" --timeout 600`。超时值须与批准预算一致，变更写入真实 argv。
- **失败断言**：没有 ACP Services 或 jobs DB 就不能执行；从环境加载了另一源码版本；规范化能量和原始输出不符。
- **证据**：`$ACP2_EVIDENCE/A/A-10/`。
- **交接**：B 得到独立 API 基线和原始输出格式；C 获得只读 Fixture。此独立性运行不能冒充 MCP 的运行。

### A-11：实测 optimize / frequency，并验证静态点语义

- **前置**：A-10；B 另登记这两个真实场景和预算。
- **步骤**：①交 `tests/fixtures/acp2/science/optfreq/` Fixture：初始结构、优化请求、频率请求、来源引用和科学检查；实际方法与参考条件固定。②优先由 B 平台执行同一优化→频率链，A 从发布工件做科学校验；需要独立 API 证明时登记额外小运行。③确认频率针对优化后同一结构、同一方法与电子态，记录优化收敛与全部模式。④输出低频阈值/数值容差作为 Fixture 的预先约定，不能把阈值改到刚好通过。⑤交未收敛、缺 Hessian、错误结构来源、虚频存在的负样例。
- **产物**：真实 optimize / frequency 的科学结果、`tests/test_acp2_science_stationary_point.py`；拟新增确定性 validator 放 `analysis/research/validators.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_stationary_point.py -q`；实际计算统一由 C14 的 `dsh-optfreq-live` 场景在 B 登记的同一窗口驱动；A 用 §1.3 `validate-artifacts` 检查 B 准备的授权清单，保存实际 argv 和任务引用，不新增未登记的 B 场景名。
- **失败断言**：优化未收敛仍称 minimum；frequency 读取了原始结构却沿用优化结构身份；singlepoint 通过被当作 frequency 实测通过。
- **证据**：`$ACP2_EVIDENCE/A/A-11/`。
- **交接**：B 校验依赖与工件身份；C 展示收敛、频率和限制，不能从任务完成直接推断稳定。

### A-12：核对真实 Local / LSF 的科学结果与出处

- **前置**：A-09～11；B 的 Local / LSF Harness、发布链与 C 的 MCP 调用可用。
- **步骤**：①先复用 debug 交付的 `scripts/acceptance/real_environment.py --help`，核对 `m1-singlepoint / lsf-singlepoint` 真实接口。②由 B 按手册 §6 跑基线，随后 C 经 MCP/DSH 发起受控科学场景；A 不再重复同输入核对。③分别核验原始输出、实际方法/软件版本、TaskResult、工件 SHA-256 和 Provenance。④同环境同输入按预先容差比对；Local/LSF 方法或版本不同则明确不能直接断言数值相等。⑤把科学数据差异与平台拉取/发布错误分开归因。
- **产物**：Local / LSF 科学核验表、绑定真实 job/attempt/receipt 的来源索引；job 信息属于平台 binding，不塞入科学身份。
- **验证**：`"$ACP2_PYTHON" scripts/acceptance/real_environment.py --help`；交付测试后运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_artifact_validation.py -q`；真实 argv 与运行证据取 B10/B11 和 C12/C13 的同一场景记录。
- **失败断言**：只生成 bsub 命令而没有远端输出；发布缓存对应旧 attempt；digest 不符；不同环境强行数值一致，均失败。
- **证据**：`$ACP2_EVIDENCE/A/A-12/`。
- **交接**：B 修真实发布/来源问题；C 显示真实任务和限制。LSF 未配置时行状态 NOT_VERIFIED，H3 不关闭。

### A-13：关闭 P1 科学与三入口一致门

- **前置**：A-10～12，B10～B13 的运行事实及 C15 的同任务对照可用；不等待 B14/C16 的 H3 总签收。
- **步骤**：①核对 CLI / HTTP-Workbench / MCP-DSH 读取的是首次 submit 返回的同一任务与 attempt。②检查三入口指向同一 TaskResult/科学产品 schema、manifest generation/digest 和科学校验结果；完整 EvidenceReport 已交付时附引用，未交付不阻塞 P1。③对 completed-but-missing-energy、旧 attempt、旧 generation 做负向验证。④确认旧 15 工作流可运行契约保留。⑤发布 `science/handoff-H3.json`，附实际科学能力的 Local/LSF 状态；没有实测的组合不标 verified。
- **产物**：P1 科学验收与跨入口来源矩阵。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_artifact_validation.py tests/test_acp2_science_task_binding.py tests/test_architecture_invariants.py -q`；真实跨入口证据引用 B 的 `three-entry` 与 C15 的 `same-task-browser-live`，不用单元测试替代。
- **失败断言**：三个入口各自提交得不同 ID 却记一致；同一 ID 读取不同 attempt 的结果；三入口共享同一个错误自然语言结论也不能通过科学门。
- **证据**：`$ACP2_EVIDENCE/A/A-13/`。
- **交接**：向 B / C 签发 H3 科学部分；开放 P2，保持已通过小计算 Fixture 固定。

## 4. P2：限定 Diels–Alder 的可验证科学闭环

### A-14：先冻结首个反应范围与 ADR-008 输入

- **前置**：A-02～04、H3；若 H3 真实排队未完成，可先做静态定义，不能提交 DA 作业。
- **步骤**：①首个候选限定为气相、中性、闭壳层、单基元 butadiene + ethene → cyclohexene；明确单构象/采样边界。若最终选择别的简单 DA，须在运行前由 ADR-008 固定完整结构，不沿用本候选结论。②交精确带映射反应、反应物/产物结构、键变化及原子顺序，不只写名字。③列允许问题：此候选路径是否连通指定端点、该方法/条件下的活化能；**该对称示例不支持 endo/exo 选择性结论**。④声明不能排除其他路径/完整机理、不能代表实验速率或普遍精度。⑤固定方法/基组/溶剂/温度/标准态、最大候选和资源预算；判据在真实计算前冻结。
- **产物**：`tests/fixtures/acp2/science/diels_alder/v1/{reaction.json,question.json,reactants.xyz,product.xyz,fixture-manifest.json,acceptance.json}`；`science/requests/ADR-008-input.md` 交 B 写共同 ADR。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_fixtures.py -k diels_alder -q`，先扩展该测试验证精确映射、守恒与不支持问题。
- **失败断言**：未定义 map 或参考态就开算；对该 Fixture 提出 endo/exo 时返回貌似完整选择性 Recipe；缺预算仍执行。
- **证据**：`$ACP2_EVIDENCE/A/A-14/`。
- **交接**：B 冻结科学参数摘要及批准预算；C 在向导/Agent 中明确范围，遇到超范围问题返回限制。

### A-15：把有限 DA Recipe 编译成现有科学任务

- **前置**：A-07、A-14。
- **步骤**：①交 `diels_alder/v1/recipe.json`：有限结构准备/构象、候选路径、TS 优化、频率、双向 IRC、端点检查、能量与热化学比较，逐步标 required。②复用既有 Confsearch/PES/scan/tsmode/optimize/IRC 等可用任务，按实际能力选择有限路线；不新建命名反应专属引擎。③检查 TS mode 的原生打印序号与 provenance，不按数组位置猜模式。④任何候选生成/映射不能确定时停止并产生缺口；不能暗中转成不受限搜索。⑤编译到 typed TaskRequest DAG，交 B 执行；记录任务参数/依赖工件摘要。
- **产物**：科学 Recipe 和可重放编译 Fixture；`tests/test_acp2_science_da_recipe.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_da_recipe.py tests/test_acp2_science_recipe.py tests/test_cccp_task_irc.py -q`。
- **失败断言**：频率或 IRC 被设成可省略却仍允许“连通证实”；失败上游下仍发下游；Recipe 含平台队列/账户状态。
- **证据**：`$ACP2_EVIDENCE/A/A-15/`。
- **交接**：B 将 DAG 冻结为 Envelope，C 的 Proposal 只引用该版 Recipe 和用户批准的参数。

### A-16：实现确定性 TS / IRC / 可比性验证器

- **前置**：A-11、A-15。
- **步骤**：①扩展 `analysis/research/validators.py`（拟新增于 A-11），复用现有频率、PES atom_mapping、tsmode 验证。②分别判断优化收敛、一阶鞍点的虚频及模式、IRC 双向实际完成、端点几何/键拓扑对应、电子态/方法/参考态一致。③单虚频仅证明局部鞍点候选，不自动证明反应连通；IRC 两端反向允许交换但必须以映射证明相同目标反应。④区分无检验 `not_evaluated`、证据缺失/不确定 `inconclusive`、实际矛盾 `refuted`。⑤给真实输出快照与构造负样例分别标来源，不能把构造数据当实测。
- **产物**：validator ID/version 注册、`tests/test_acp2_science_da_validators.py`；wrong-mode、one-sided-IRC、wrong-endpoint、wrong-charge、incomparable-energy Fixture。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_da_validators.py tests/test_acp2_science_stationary_point.py tests/test_cccp_task_frequency.py tests/test_cccp_task_irc.py -q`。
- **失败断言**：单虚频直接 supported；只有一个 IRC 方向也完整支持；原子换序导致错误连通；不同方法能量相减未拒绝。
- **证据**：`$ACP2_EVIDENCE/A/A-16/`。
- **交接**：B 发布原始和标准化产物；C 仅呈现确定性判据，不在前端或 Agent 复制计算公式。

### A-17：跑真实 TS 候选、优化与频率

- **前置**：A-14～16、B16～B18 的审批/配额/科学绑定和实际环境；统一登记 DA run key。可由 C24 发起同一组受控作业后按阶段验证，不等 A-20/B20/C25 的 H4 总签收，也不要求另跑整套 DA。
- **步骤**：①先验证反应物/产物几何与小步预算，再由 B 执行冻结 Recipe 的候选与 TS 优化/频率。②保留所有候选的选择/舍弃依据和有限搜索范围。③A 从真实产物验证收敛、虚频数/模式与成键方向。④未找到合格 TS 时发布 inconclusive 与原始失败，不用示例 TS 替换本次结果。⑤只在预算内按已批准备选重试；新增策略生成新 Recipe/proposal 版本。
- **产物**：真实候选、TS、频率产品和阶段 EvidenceReport。
- **验证**：实际作业引用 B20 的 `da-runtime` 与 C24 的 `da-research-live`；执行 `"$ACP2_PYTHON" scripts/acceptance/acp2_science.py --scenario validate-da-ts --fixture tests/fixtures/acp2/science/diels_alder/v1/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-17/validation" --timeout 300`；授权工件清单由 B12/B18 填实际绝对路径，保存同一 job/attempt/receipt，不另起 TS 作业。
- **失败断言**：候选耗尽仍称成功；错误模式或多个显著虚频仍进入“已证明 TS”；取消任务产物被收养为完成。
- **证据**：`$ACP2_EVIDENCE/A/A-17/`。
- **交接**：B 继续可信依赖到 IRC；C 显示“TS 候选成立/未证连通”等分阶段结论。

### A-18：跑真实双向 IRC 与端点归属

- **前置**：A-17 的当前身份合格 TS；B 冻结双向运行与预算。
- **步骤**：①由 B 依 TS 工件摘要执行正/反向 IRC，保留原生步骤、终止原因和最终结构。②按冻结 Recipe 做必要的端点优化；若未做，明确端点仍可能不是极小值。③A 使用稳定原子映射和键变化核对两个目标端点，不以可视化“看起来像”作证。④核对 IRC 与 TS 同方法/状态，跨 attempt 来源可追溯。⑤只有单向成功、途径其他反应物、路径过短或映射歧义都保留不确定性。
- **产物**：真实 ReactionPath、端点判据和源工件图；可供 C 的 IRC/结构视图 Fixture。
- **验证**：沿用 B20/C24 的同一 DA 作业，执行 `"$ACP2_PYTHON" scripts/acceptance/acp2_science.py --scenario validate-da-irc --fixture tests/fixtures/acp2/science/diels_alder/v1/fixture-manifest.json --input-manifest "$ACP2_ARTIFACT_MANIFEST" --out "$ACP2_EVIDENCE/A/A-18/validation" --timeout 300`；固定反例另运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_da_validators.py -k endpoint -q`。
- **失败断言**：一个方向失败却支持双向连通；端点只按文件名识别；同一个源结构被错误当成两个端点。
- **证据**：`$ACP2_EVIDENCE/A/A-18/`。
- **交接**：B 发布路径/端点摘要；C 绑定真实帧与结果引用，使用既有帧契约和 visualization 路径。

### A-19：形成热化学、活化能与最终 EvidenceReport

- **前置**：A-18；必要热化学步骤已实际执行或明确标未完成。
- **步骤**：①复用现有 thermo normalize、Shermo adapter、ensemble thermo；不复制常量/标准态校正公式。②核对反应物、TS、产物方法、电子态、温度、标准态、参考态与组分计量，区分电子能垒和自由能垒。③根据实际完成的步骤逐条生成 claim；缺热化学仅给电子能垒，不补造 ΔG‡。④交 `EvidenceReport + ScientificProvenance`，保持执行事实与科学命题状态分离。⑤记录构象/采样覆盖限制、数值容差、模型适用边界和所有失败候选。
- **产物**：真实标准化能量/热化学表、报告 JSON、来源图、`tests/test_acp2_science_energy_comparability.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_energy_comparability.py tests/test_cccp_task_thermochemistry.py -q`；另用 A-16 validator 校验当前真实报告与原生工件。
- **失败断言**：混合电子能与自由能；不同标准态/方法直接相减；漏一个反应物组分；没有构象搜索却声称全局最低路径。
- **证据**：`$ACP2_EVIDENCE/A/A-19/`。
- **交接**：B Data 发布不可变报告和出处；C Research Loop 根据 supported/refuted/inconclusive 决定继续或停止，模型推断另记 ResearchRecord。

### A-20：故障、科学反例与 H4 总验收

- **前置**：A-17～19；B16～B19 的审批/预算/恢复和 C24 的实际工具/页面记录可用。不等待 B20/C25 的最终 H4 签收，A 先交自己的科学报告供其汇总。
- **步骤**：①用固定产物负样例检查缺频率、错误端点、坏摘要、旧 provider 版本、标准态不一。②真实故障由 B 在指定检查点注入：worker 中断、发布失败、恢复、late receipt；A 只核查能否复用身份与完成事实均有效的科学结果。③检查发布重试没有重新启动昂贵 QC；完整科学结果缺发布只能重试发布。④必需科学步 blocked、审批拒绝或预算拒绝不能产出完成科学链。⑤C 对同一场景展示缺失证据并生成受限制总结；A 审核没有夸大。⑥输出 H4 逐项三态，不能要求计算必然发现正确 TS 才如实记录结果，也不能以负例健壮性替代必需真实正例。
- **产物**：`science/handoff-H4.json`、限定 DA 科学验收报告、失败分类与可重放命令。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_da_validators.py tests/test_acp2_science_energy_comparability.py tests/test_acp2_science_recipe.py tests/test_recovery_fixtures_smoke.py -q`；真实故障引用 B 的 `control-recovery` 和 `approval-budget`，在 Fixture 中分别登记 publication/late-receipt/budget-denied 子例，B01 冻结子例 Schema 后执行。
- **失败断言**：恢复复用无当前身份产物；缺必需科学证据却 supported；三轨任一必需行 NOT_VERIFIED 而总 H4 PASS。
- **证据**：`$ACP2_EVIDENCE/A/A-20/`。
- **交接**：B 关闭执行门，C 关闭实际用户旅程；A 只签发限定 Fixture 的科学结论，不能推广为全部 DA 机理求解能力。

## 5. P3：第三方科学插件与受控能力扩展

### A-21：扩展既有插件 Manifest 与 Registry 适配

- **前置**：H4；A-05 的 metadata registry 和既有 PluginHost/sdk 基线。
- **步骤**：①复用 `plugin/{metadata,discovery,host,sdk}.py`，在已有 API/schema 兼容规则上增量表达 capability、recipe、validator、科学版本范围、许可、摘要、Fixture 与 trust。②发现仅元数据、启用需明确授权；科学验证级别不能仅靠插件自称。③科学执行保持 `cccp.calculation` 唯一驻点，纯分析插件不可 spawn。④固定运行所用插件 distribution/version/digest；升级不改变已冻结任务。⑤为字段改变提供兼容 adapter 和旧插件负向测试。
- **产物**：插件扩展与 `science/requests/ADR-007-input.md`；`tests/test_acp2_science_plugin_registry.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py tests/test_cccp_plugin_pilot.py tests/test_cccp_plugin_sdk.py tests/test_architecture_invariants.py -q`。
- **失败断言**：发现导入并执行插件；未知 License 或越权覆盖内建能力被自动启用；升级导致旧任务透明换实现。
- **证据**：`$ACP2_EVIDENCE/A/A-21/`。
- **交接**：B 应用启用/信任/环境冻结控制；C 动态发现只显示服务端允许的能力。

### A-22：交一个独立发行的第三方科学插件 Fixture

- **前置**：A-21。
- **步骤**：①在 `tests/fixtures/acp2/science/external_plugin/` 交独立 distribution 的最小只读科学 validator 或数据 converter；自带 pyproject、版本、License、metadata 和固定原生输入。②通过已有 entry-point 候选发现接入，不把实现复制进核心。③在隔离 venv 安装该 Fixture distribution，先证明禁用时不加载，再启用调用。④用真实固定输出验证新能力与错误输入，记录由 Generic/Typed 到 Validated 所需的证据，不自动升级信任。⑤向 B 交外部测试包的构建/依赖请求，不改根 pyproject。
- **产物**：外部插件 Fixture wheel/source、版本摘要、契约验证及卸载/重新安装步骤。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py -k external_distribution -q`，该测试先按规范交付独立 build/install 环境；实际 pip/build argv 由 B 固定并留证。
- **失败断言**：必须改核心源码才能发现新 capability；禁用插件仍执行；插件依赖不可用被误报科学结果为空成功。
- **证据**：`$ACP2_EVIDENCE/A/A-22/`。
- **交接**：B 在平台登记并受控启用；C 无代码改动从公开 Schema 发现并使用该能力。

### A-23：插件冲突、升级和历史只读验收 H5

- **前置**：A-22；B / C 的插件发现与动态表单就绪。
- **步骤**：①测试同名 capability、同 implementation ID、API 不兼容、缺依赖、未知许可、校验数据损坏。②保留 v1 产物后安装 v2，当前新任务使用明确新版，v1 原始/标准化工件仍可只读，不要求重跑。③确认插件写权限/进程限制由 B 执行，A 不宣称进程内 PluginHost 是安全沙箱。④冻结 Plugin Metadata→Capability→Recipe→Provenance 完整关联。⑤发布 H5 科学证据和开发者接入步骤。
- **产物**：`science/handoff-H5.json`、插件兼容矩阵与回滚说明。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_plugin_registry.py tests/test_cccp_plugin_pilot.py tests/test_cccp_plugin_sdk.py tests/test_packaging_assets.py -q`；动态 UI / MCP 使用记录引用 B / C 的同一外部包版本。
- **失败断言**：卸载插件就打不开历史科学报告；冲突被安装顺序静默决定；不可信代码被自动执行。
- **证据**：`$ACP2_EVIDENCE/A/A-23/`。
- **交接**：B / C 合并 H5，确认“不改核心新增能力”是真的外部发行测试，不是向 core 添加另一个 if 分支。

## 6. P4：跨领域、选择性与科学可靠性

### A-24：将 NMR / 热化学能力接到相同科学协议

- **前置**：H5；保留既有 NMR 模型资产/notice 和兼容路径。
- **步骤**：①选择现有一个 NMR shield/比较能力和一个 ensemble thermo 能力，建立 typed CapabilityDescriptor 与 Task binding，不另建 NMR 引擎。②补 NMR 的分子/原子映射、实验谱来源、溶剂/参考/缩放条件及模型资产版本；热化学补温度/标准态/组分权重。③把确定性校验转成 EvidenceReport，缺实验参考或覆盖不足时报告 limitation/inconclusive。④交正常、缺失、部分、坏数据 Fixture，供 Mechanism/NMR 独立界面同合同消费。
- **产物**：扩展科学能力、`tests/test_acp2_science_cross_domain.py`、`science/cross-domain-compatibility.md`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_cross_domain.py tests/test_cccp_task_nmr_shielding.py tests/test_cccp_task_thermochemistry.py tests/test_packaging_assets.py -q`。
- **失败断言**：NMR 原子顺序变化仍沿用旧归属；缺模型资产仍给完整置信结论；热化学权重未归一化却无错误。
- **证据**：`$ACP2_EVIDENCE/A/A-24/`。
- **交接**：B 发布相同 Artifact/Evidence 契约；C Mechanism/NMR 使用相同 client data，不混用不同领域的 claim。

### A-25：增加真正可区分立体选择性的反应 Fixture

- **前置**：A-24 与 H4 限定 DA 已实测；另开 Fixture/预算，不修改 v1 定义。
- **步骤**：①选端点立体可区分、适用现有方法与资源预算的简单 DA 或其他限定选择性案例；在开算前冻结结构、原子映射、endo/exo 定义与可比较路径。②明确同一科学条件下比较的构象/TS 覆盖与缺失路径，不能把最低的两个单点叫完整选择性预测。③按已有 Recipe/任务执行有限多假设；没有合格两侧证据不能输出选择性支持。④科学外推/实验定量预测如无验证 Fixture，保留未评估。⑤交实际计算证据，不能用 A-14 的对称示例冒充。
- **产物**：`tests/fixtures/acp2/science/selectivity/v1/**`、限定选择性 EvidenceReport、`tests/test_acp2_science_selectivity.py`。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_selectivity.py tests/test_acp2_science_energy_comparability.py -q`；B 登记并排程实际作业，C29 的 `cross-domain-live` 用包含该选择性 Fixture 的研究计划触发并返回同一任务引用，A 消费科学工件，不额外发明 B 场景名。
- **失败断言**：缺一个假设路径却给二者相对优劣；不一致标准态下排序；原子映射无法区分立体仍输出 endo/exo。
- **证据**：`$ACP2_EVIDENCE/A/A-25/`。
- **交接**：B 执行受控预算；C RPH 真实多假设选择与停止策略有科学数据依据。

### A-26：跨领域回归、版本兼容与 H6

- **前置**：A-24～25；B 多项目/多用户可靠性测试已提供相同工件的受权读取证据。
- **步骤**：①在 singlepoint、optfreq、DA、NMR、thermo 上核对单位、映射、引用、schema 升级和派生身份。②测试旧 scientific schema 只读、新版不支持字段显式拒绝，以及模型/插件摘要变更后的保守恢复。③真实重跑只针对已变化科学路径，不无理由重复所有昂贵计算；固定原生回归数据与真实运行证据分开标记。④A 科学输出不含账户等级/项目权限差异，B 的授权可影响可访问性但不能改科学算法结果。⑤发布 H6 能力适用性/验证覆盖矩阵。
- **产物**：`science/handoff-H6.json`、跨域版本矩阵、科学回归预算清单。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_cross_domain.py tests/test_acp2_science_selectivity.py tests/test_acp2_science_evidence_contract.py tests/test_recovery_fixtures_smoke.py tests/test_cccp_isolation.py -q`。
- **失败断言**：账户付费等级进入科学 identity；历史报告被新版 validator 静默改为支持；只读没有源数据却伪装重新验证。
- **证据**：`$ACP2_EVIDENCE/A/A-26/`。
- **交接**：B / C 合并 H6，逐项区分科学语义不一致与访问拒绝/数据未准备。

## 7. P5：独立科学发行、SDK 和用户可复现

### A-27：独立安装的科学依赖与 wheel 验证

- **前置**：H6；B 明确发行角色、版本和打包规格。先交规格供 B25 构建；拿到实际候选 wheel 后再完成安装复验，不等待 B25/H7 总签收。
- **子交付**：**A-27a** 完成依赖/资产/打包请求并交 B25a；**A-27b** 收到 B25a 的实际 wheel 后执行干净安装。B25a 不以 A-27b 为前置，A-27b 不等待 B25b 总验收。
- **步骤**：①审计 CCCP 公共导入的依赖，确保独立 wheel 不导入 `acp/acp_services/DSH`。②向 B 交 `science/requests/packaging.md` 与精确 patch，包括 package include、运行依赖、科学资产/License、Python 支持范围；不自行拆根包或改四处版本。③B 构建冻结 wheel 后，在没有源码路径/可编辑安装的干净环境安装。④从仓库外 cwd 跑纯导入、Fixture 解析和小型独立科学 Probe，校验模块 `__file__` 来源。⑤没有软件时能导入/发现，真正执行时报 typed unavailable，而不是 import 崩溃。
- **产物**：独立科学包安装证据、依赖闭包和 wheel/资产摘要；`tests/test_acp2_science_installed_package.py`。
- **验证**：先交该测试，再运行 `"$ACP2_PYTHON" -m pytest tests/test_acp2_science_installed_package.py tests/test_packaging_assets.py -q`；真正发行验证使用 B 创建的干净 venv 对 wheel 重跑 Probe，并记录实际 argv。
- **失败断言**：wheel 依赖源码树才能运行；偷偷导入 Services；NOTICE/model 文件漏包；无 ORCA 导致纯科学类型无法 import。
- **证据**：`$ACP2_EVIDENCE/A/A-27/`。
- **交接**：B 独立发布科学发行角色；C 文档只能引用实际发行能力，不将开发环境 editable install 当第三方安装成功。

### A-28：交可逐条操作的科学 SDK / 插件文档

- **前置**：A-27；所有命令对应真实冻结版本。
- **步骤**：①在 `science/user-guide.md` 写安装、无软件类型/发现、配置软件、单点/优化/频率、读取报告、单位/映射、失败解释、资源上限与清理。②交 `science/examples/` 可运行 Python：public API 单点、Recipe 编译、报告读取和外部插件接入；不使用 `python -m cccp`。③将独立科学使用与通过 ACR 的受控科研使用分开说明，引用本机手册 ENV-06 和实际软件路径配置方法。④补 API/schema 兼容、license/验证范围、贡献 Fixture、插件信任及只读历史结果规则。⑤用干净安装环境顺序执行每个命令，文档保留预期输出/常见失败，不凭示例文字宣称真实成功。
- **产物**：科学 SDK 操作手册、可执行示例、插件开发指南和版本支持矩阵。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_installed_package.py -k documented_examples -q`；需要 QC 的示例按真实软件/预算单独登记执行，缺环境为 NOT_VERIFIED。
- **失败断言**：文档引用未发行模块、占位路径被当可复制命令、需要额外未声明依赖、示例输出科学结论超出 Fixture 范围。
- **证据**：`$ACP2_EVIDENCE/A/A-28/`。
- **交接**：B 将准确安装步骤接入 Lab Standalone/Server 文档；C 接入用户操作旅程和科学限制说明。

### A-29：最终科学发布门 H7 与交付审计

- **前置**：A-27～28；B25 的实际构建产物和 C33/C34 的真实安装/操作证据。不等待 B26 的 H7 总签收，A 先交科学签收供 B26 汇总。
- **步骤**：①核对 H0～H6 真实 revision/schema/provider/plugin/Fixture 摘要及每项范围，更新科学支持矩阵。②运行必要科学回归和独立 wheel Probe；仅对本轮改变或未消除疑虑的真实路径重跑。③检查代码无反向导入、唯一执行站保持、15 active workflows/兼容 shim/历史 reader/模型资产保留。④确认所有实际步骤有运行命令、原生证据与 PASS/FAIL/NOT_VERIFIED；必需项未验证不可发布“全链路可用”。⑤向 B 交最终文档/依赖/CI patch 请求并核对集成 SHA 后签发 H7。
- **产物**：`science/handoff-H7.json`、最终科学发行说明、能力/版本/限制矩阵、重放索引。
- **验证**：`"$ACP2_PYTHON" -m pytest tests/test_acp2_science_schema.py tests/test_acp2_science_registry.py tests/test_acp2_science_task_binding.py tests/test_acp2_science_recipe.py tests/test_acp2_science_da_validators.py tests/test_acp2_science_cross_domain.py tests/test_acp2_science_installed_package.py tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_recovery_fixtures_smoke.py tests/test_packaging_assets.py -q`。B 在集成 revision 执行完整 CI；A 不重复抢占完整测试资源。
- **失败断言**：没有独立安装证据却宣称 CCCP 独立发行；缺原始结果的“科学支持”；真实软件跳过被算 PASS；公共依赖请求未集成导致可复现安装失败。
- **证据**：`$ACP2_EVIDENCE/A/A-29/`。
- **交接**：B 汇总最终发行门，C 以冻结 SDK/Schema/真实报告完成第三方科研旅程。A 不负责生产服务重启或公开发布。

## 8. 依赖等待、失败处理与复用规则

- 等 B 配环境/排队：A 可完成 Schema、兼容适配、固定输出 validator、插件元数据和文档；每 1 个交接点给 B / C 更新版本清单，不在共享文件自行补接线。
- 等 C 模型/界面：A 可用真实任务的已发布科学产物独立验证；缺真实 DSH/界面的行保持 NOT_VERIFIED，不能用离线 Fixture 替代产品验收。
- H1 后变更字段：先写变更原因、兼容级别、Schema/Fixture diff 和迁移方法，由 B 更新总 manifest，C 更新消费；原版本保留可读。不能先改字段再让其他 Agent 猜。
- 科学搜索失败：如实输出已有证据与缺失验证；真实反例可验证系统的诚实性，但不能替代里程碑要求的真实支持正例。不要扩大反应范围或补填参考能量以关闭门。
- 复用实际作业：必须同时满足当前科学身份、原生产物完成事实、来源/摘要有效及平台 attempt/授权要求；A 不决定 CAS、中央终态或发布重试。发布失败时先交 B 重发，不能再调用 QC。
- 本计划精确规定步骤和产物；实际耗时由首次环境/Provider/Fixture 兼容结果决定。原 debug 约 5 小时不构成本轨完成承诺。
