# ACP MCP 可用性修复计划（2026-10-29）

> **输入**：`acp_mcp_ux_review.md`（36 条编号项 + P5 汇总）、两轮外部专家咨询、一轮独立源码核查、一轮计划评审。
> **外部意见来源**（只是第二意见，不是证据）：
> - `[external: codex 0.163.0 / gpt-6-astra / reasoning=high / read-only]` 独立诊断（A–E）与计划评审（第 3 轮）。
> - `[external: codex 0.163.0 / gpt-6.1-sol / reasoning=high / read-only]` 收敛：修正前提、给出清单。
> - `[internal: DSH 子代理 / 只读源码逐条核查]` 36 条编号项的 `file:line` 判定表（未跑测试、未起服务）。
> **基线**：`HEAD=fd2087c7`，但工作区有 16 个已跟踪 `src` 文件修改（含 5 个 AGENTS.md）+ 3 个未跟踪 Python 文件，另有关联测试/契约/RPH 修改。**计划中的验收数字必须在隔离 worktree 上取得**，不能拿当前工作区当基线。
> **状态**：计划，未实施；下列验收全部是后续执行要求，本轮未跑测试、未起服务。

---

## 0. 结论

评审的核心判断成立：**执行层可靠，接口层不可自描述**。但 36 条里并非都同级：

- **真问题、需修**：S1（未知 method 键静默丢弃）、S2（`max_conformers` 映射到不存在的 flag）、S4（结果无方法身份）、S5（`conformers_kept` 语义错）、C2（preflight 缺语义校验）、C3（`cores` 只在一层被接受）、C6（两套 limits 常量）、C7（status 丢 workflow/进度）、D1/D2（无 workflow 输入契约）、D5（产物发现缺失）、D7（walltime 入口不可见）、D8（project_id 语义）、N1/N3（回包噪音与里程碑泄漏）。
- **需修正的表述**：
  - **C1** 服务端归因不成立：`_require_reference` 已支持 `{kind,id}` 对象（`mcp.py:2955`），真实对象可用；但"客户端把对象字符串化"仍是假设，关闭归因前必须拿到实际客户端链路证据。
  - **D6** 部分成立：拒绝发生在"`max_bytes` 请求超过 1 MiB"时，大 body 本身会截断并置 `truncated=true`；真问题是 (a) discovery 广告 8 MiB vs 存储真实上限 64 MiB，(b) MCP 无 offset 入口，2 MiB 产物无法一次读回，(c) 截断片段的 digest 仍指向完整工件。
  - **M3** 误判：`research_extension` 协商后工具可见（`mcp.py:2515-2526`），无需改语义；但要补"协商后客户端重新 `tools/list`"的验证，默认拒绝对。
  - **M6** 绑定已存在：submit 会重算摘要并拒绝不匹配的 fingerprint，不是"只进不出"。
  - **N2** 夸大：status 并不每次附带完整 admission/identity 信封。
  - **S3** 不是 P0：`budget` 触发 research launch 属于明确分类后拒绝执行，问题在描述误导，不在静默错误。
  - **数字更正**：`capability_descriptors` 实测 4 条（含 `vibration.compute`），不是 3 条；`resources` 默认 walltime 是 24 h，`864000`（10 天）是 ORCA 子进程等待，不是准入默认。
- **设计约束、不应"修掉"**：C9/M4（`explicit=true` 是分析护栏；`registered` ≠ `executable`）、N5（外层 status 与任务生命周期是两层语义）。

**如果只做五件事**（边际收益排序）：
1. 提交前语义校验，preflight 与 submit 共用；
2. 现有 capabilities 支持按 workflow 查输入契约与示例；
3. status 投影 + 产物发现 → prepare → read 闭环；
4. 资源解析 / admission / capabilities 声明对齐；
5. 方法溯源与构象计数。

---

## 1. 设计原则（本轮硬约束）

1. **fail-closed，但不误拒**：未知键必须报错；字段形状与枚举引用现有定义，嵌套优先复用消费者的纯解析；flag 映射只作一致性检查，**不能当公开 API 契约权威**（反例：`batch_roles` 存在时 scalar flags 提前返回、不再发出）。
2. **本轮不加第九个基础工具**：自描述通过现有 `acp_capabilities` 的可选 `workflow` 选择器实现，信息效果与 `describe_workflow` 等价。但"永远八个工具"不是产品原则——`list_jobs` 解决"跨会话找回任务"，selector 替代不了，单独排期（若跨会话可用性是目标，应早于 P4）。
3. **不暴露真实 `work_dir`**：路径 redact 保留，用受控 `ArtifactRef` 闭环。
4. **每个修复配可执行断言**：错误码 + 字段 + 形状；不靠修改既有固定断言换绿灯。
5. **每阶段独立提交、可单独回滚**：不动调度状态机、不迁移数据库、不做架构重构。

---

## 2. 计划清单

> 路径默认相对 `src/`；`tests/`、`docs/` 除外。首轮完整契约只覆盖 **Confsearch、BatchOptimize**，其余 13 个 active 工作流明确标注"schema 未完整提供"，且不得掩盖它们仍可能静默忽略参数。

### P0 基线与归因（0.5–1 人日）

- [ ] **P0-01 固定三层基线，隔离实施目录**
  改动点：独立 git worktree；记录基线清单（隔离动作直接执行，不需用户二选一）
  验收：HEAD=`fd2087c7`；完整 tracked diff 与未跟踪文件均有 SHA-256；原工作区前后一致；"既有补丁 / 本轮修改"可分别列出；干净 HEAD 与带补丁基线分别留证
  风险：漏掉关联契约或测试会制造假回归

- [ ] **P0-02 固定 C1 wire 回归，不预先改解析**
  改动点：`acp_services/entrypoints/mcp.py::_require_reference`；`controller_client.py::_task_id_of`；SDK harness
  验收：字符串 ID 与真正的 `{kind,id}` 对象命中相同 URL、返回同一任务；JSON 文本字符串单列，不冒充对象；判定根因前不预设"客户端字符串化"
  风险：直调 handler 或另起服务都覆盖不到真实客户端转换

- [ ] **P0-03 核验既有 C5 补丁及依赖闭包**
  改动点：`acp/api/routes.py::_backend_capability_summary/get_backends`；`cccp/backends/capabilities.py::backend_status`
  验收：PATH 缺失、配置 executable 存在时两条 discovery 路径都报可用；去掉配置后不可用；补丁前后结果分别记录（C5 是唯一必须活体探测的项）
  风险：源码通过不证明运行中的服务已加载补丁

### P1 先堵静默错误（3–4 人日）

> P1-01～03 是一个完整修复：校验器未接入没有收益，先删映射又会恢复静默忽略。可分提交，但中间态不可当作可发布成果。

- [ ] **P1-01 定义两工作流的纯提交校验**
  改动点：拟增 `acp/api/submission_contract.py`；API 层只组织纯校验与结构化错误
  范围：输入存在性、method 顶层未知键、容器类型、已知会吞字段的分支/优先级（如 `batch_roles + optimization_method`）；嵌套优先调用或抽取现有消费者的纯解析校验，不在 API 再抄一份科学规则
  验收：合法 `flat/levels/batch_roles/profile_id` fixture 全通过；拼错键、非法枚举、错误嵌套类型、已知吞键组合返回结构化 `field/rule` 或明确告警；输入对象深比较不变
  风险：把 flag 表当契约权威会误拒历史合法请求

- [ ] **P1-02 preflight 与 submit 接入同一纯检查**
  改动点：`acp/api/v1_routes.py::create_job/_preflight_projection`
  验收：有权限的两入口对空输入、未知 method 键返回相同 `422 input_invalid` + `params.field/rule`；任务数、prepare、reservation、预算记录均不增加；fingerprint 既有测试保持绿
  风险：不能移动或绕过现有鉴权、审批边界

- [ ] **P1-03 删除 Confsearch 的错误 `max_conformers` 映射**
  改动点：`acp/scheduler/jobs.py::_CONFSEARCH_SCALAR_FLAGS`
  验收：该键在提交前被拒；Confsearch/BatchOptimize/NMR 的本地与远端完整 argv 都能被 `cli.py::build_parser` 解析；NMR 保留该 flag
  风险：只删映射而不接 P1-02，会退回静默忽略

- [ ] **P1-04 对齐 `cores` 别名与实际执行资源**
  改动点：`acp/scheduler/resources.py::resolve_job_resources`；P1 资源纯检查
  验收：分别传 `nproc/ncores/cores=4`，admission 与 argv 均为 4；冲突别名、bool、小数核数提交前拒绝；`request-field-equivalence` 摘要向量不变
  风险：执行归一化不能回写原始摘要材料

- [ ] **P1-05 用已装配的 admission 实例发布容量限制**
  改动点：`acp/api/routes.py::_capabilities_payload`
  验收：环境覆盖核数/时长后，discovery 与 admission snapshot 相等；默认时长单列；无 admission 时明确"未强制"；不改实际配额
  风险：把默认类实例当运行配置会继续误报

### P2 自描述（1.5–2 人日）

- [ ] **P2-01 提供按 workflow 查询的 Controller 契约投影**
  改动点：`acp/api/routes.py::get_capabilities/post_capabilities/_capabilities_payload`；P1 契约定义；`application-api.v1.json`
  验收：GET/POST selector 等价；两工作流各两个示例通过 P1 校验；其他 active 工作流标明 schema 不完整；未知 ID 返回 `input_invalid` + allowed + 大小写建议
  风险：UI schema 不能直接冒充提交 schema

- [ ] **P2-02 贯通 MCP capabilities selector**
  改动点：`acp_services/entrypoints/mcp.py::_CAPABILITIES_SCHEMA/CapabilityPort/_tool_capabilities`；`acp/services_mcp_bootstrap.py::_ControllerCapabilities`；`controller_client.py::capabilities`
  验收：真实 SDK 查询得到所选契约；无参形状与八工具集合不变；selector 查询不扰动已协商状态；协商后重新 `tools/list` 可见研究工具；旧 Controller 拒绝 selector 时错误明确且不降级
  风险：只改 tools/list 会出现"声明支持、实际丢字段"

- [ ] **P2-03 修正文案与发现说明**
  改动点：`mcp.py::_SUBMIT_PROPERTIES/_PREFLIGHT_SCHEMA/_PREPARE_SCHEMA`；P2 契约投影
  验收：preflight key 描述为可选、submit 仍必填；示例可校验；`screen`/纯 xTB 限制可见；budget-only 保持既有审批错误；错误码与状态词表对应现有枚举；**`resources.walltime_s/walltime` 的入口、单位、默认值（24 h）以及"admission 上限 ≠ 执行终止"写进描述**
  风险：不得混淆 launch budget 与 proposal budget，不得删除 gating

### P3 闭环与计数（3–5 人日）

> P3-01/02/03/06/07 不依赖 P2，基线固定后即可开工；P3-04/05 也可在 P0 后启动，最终 SDK 闭环再汇合。

- [ ] **P3-01 修正构象计数并保留精修选择数（原 P4-01，提前）**
  改动点：`acp/confsearch/engine.py::_finalize`；现有 i18n 消费者
  验收：两个 entries + `screen` 得 kept=2、refinement_selected=0；`rank1` 分别为 2/1；更新有解释的 progress 断言
  风险：旧指标含义变化须同步消费者

- [ ] **P3-02 补 status 的 workflow、进度与本地 live_status**
  改动点：`acp/api/v1_routes.py::get_job`；`acp_services/entrypoints/status_projection.py::project_task_status`
  验收：仅含 `spec.workflow` 的 fixture 返回正确 workflow；本地已有指标可见；远端冷缓存返回 unknown；fetch/enqueue 调用数为零
  风险：不能靠切到旧 summary 路由引入不受控路径读取

- [ ] **P3-03 保留 prepare operation 的完整资源引用**
  改动点：`status_projection.py::project_operation_status`
  验收：prepare 直接回包与 operation status 的 `artifact_ref/digest/generation` 相同；未完成操作不伪造完整引用
  风险：fallback 引用不能标成"已验证可读"

- [ ] **P3-04 新增有界、只读的产物描述构造器**
  改动点：拟增 `acp/api/artifact_descriptors.py`；复用 manifest、`PrepareOperationService.list_for_job`、`build_read_root_resolver`
  验收：manifest 产品转换为合法 prepare 参数，路径补 `RESULT/` 前缀；size 未知为 null；只取当前 attempt；prepared 必须有有效操作与 store 元数据；越界路径拒绝、冷远端零 fetch
  风险：旧 `ArtifactRegistry` 无 attempt，不能作为当前引用的自动兜底

- [ ] **P3-05 描述列表接入 DTO 与 status**
  改动点：`acp/api/v1_schemas.py::V1JobRecordModel`；`v1_routes.py::get_job`；`status_projection.py::project_task_status`
  验收：列表穿过 `response_model` 后仍存在；上限与截断状态可断言；跨项目查询保持既有 404；无绝对路径、无目录扫描
  风险：轮询成本必须受元数据字节数与条目数约束

- [ ] **P3-06 修正 artifact 正文的实际 encoding**
  改动点：`acp_services/data/artifacts.py::ArtifactRead.to_dict`
  验收：非 UTF-8 与"截断切断多字节字符"两种 fixture 都能按返回的 encoding 解回返回片段；`truncated` 保持真实
  风险：部分客户端可能依赖旧的错误标签

- [ ] **P3-07 正文脱敏与摘要作用域分三级验收**
  改动点：`mcp.py::_tool_read_artifact` 的受控序列化检查
  验收（三级，不得混为一谈）：
  ① `truncated=false` 且未脱敏 → body SHA-256 == 引用 digest；
  ② `truncated=false` 但脱敏改变内容 → `unsupported_capability` + `params.rule=artifact_body_redacted`，不成功返回错误字节；
  ③ `truncated=true` → **不要求**片段 hash == 完整工件 digest；返回明确 digest 作用域（指向完整工件），如需验证片段另加 fragment digest
  风险：把"截断"误判为"内容损坏"会拒掉合法读取

- [ ] **P3-08 建立完整 SDK 闭环 fixture**
  改动点：`tests/test_acp2_mcp_sdk_contract.py` 与现有 MCP fixture
  验收：`提交 → 完成 → status 发现 → prepare → operation status → read` 全程不手填路径/摘要；错 digest、旧 generation 返回 `revision_conflict`；冷读返回 `resource_not_ready`；既有 MCP 门非空通过
  风险：fixture 科学派发通过不能写成真实 QC PASS

### P4 科学可解释性（2–4 人日）

- [ ] **P4-01 为 CENSO 结果携带成功执行证据**
  改动点：`cccp/qc/interfaces/censo.py::refine_ensemble`；`cccp/calculation/tasks/censo_refine.py`；workflow→`ProtocolOutcome` 传递处
  验收：fixture 关联实际 argv、rc/template、成功输出、最终 part JSON/XYZ 的相对引用与 digest；缺输出或 part 冲突不能标 verified
  风险：request provenance 不能直接升级为执行事实

- [ ] **P4-02 在 final_report 写入分角色方法身份**
  改动点：`acp/confsearch/report.py::write_final_report/_conformer_block`
  验收：按 `source_conf_id` 对齐 geometry/energy/thermal 证据；argv 温度覆盖正确；混合 refined/unrefined 不共用方法声明；证据缺失写 unknown；旧报告仍可读
  风险：复合方法、热校正与实际程序不能被一个 func/basis 简化误述

---

## 3. 本轮不做

- 其余 13 个 active 工作流的完整提交 schema（不宣称已全覆盖）。
- 第九个基础工具；`log-tail / batch-submit / cancel-all / delete / cleanup`。
- `list_jobs`：本轮不做，但**值得独立排期**（跨会话找回任务 selector 替代不了）；若跨会话可用性是目标，应早于 P4。
- 暴露真实 `work_dir`；猜测性解析 JSON 文本 reference；自动改写 workflow 大小写。
- 拆 research submit；budget-only 自动降级；删除 `explicit`/gating/兼容字段。
- 大工件完整分页读取；全协议方法溯源；历史报告回填。
- 调度状态机、数据库迁移、架构重构、生产重启及真实模型/QC/LSF 提交。

---

## 4. 顺序与工作量

| 阶段 | 目标 | 依赖 | 预估人日 | 出口条件 |
|---|---|---|---:|---|
| P0 | 可追溯基线，收窄 C1/C5 | 无 | 0.5–1 | 三层差异可重建；C1/C5 有独立结果 |
| P1 | 两工作流提交前拒绝无效请求，资源一致 | P0 | 3–4 | 语义错误零入队；摘要、argv、admission 断言通过 |
| P2 | agent 可发现并构造合法请求 | P1 契约 | 1.5–2 | SDK selector、示例、八工具与协商门通过 |
| P3 | 计数、状态与产物取回闭环 | P0；P3-01/02/03/06/07 不等 P2 | 3–5 | SDK 闭环、三级摘要、零 fetch 与隔离断言通过 |
| P4 | CENSO 方法可核验 | 计数已由 P3-01 完成；报告依赖执行证据 | 2–4 | 逐构象证据 fixture 通过 |

合计 **11–17 人日**，是**乐观预算、不是承诺**；P0 之后按消费者清单重估。最可能低估 **P3**（attempt 判定、prepare/store 一致性、远端冷缓存、DTO 过滤、编码、脱敏同时交叉），其次 P1（合法组合齐全 + 本地/远端/NMR argv）。

**如果只有 3 天**：P0 + P1-01～03（最小校验接入 + 错误 flag 删除）+ 针对性回归。**不**同时承诺完整嵌套契约与 C1 实际客户端归因。

---

## 5. 验收门

1. 新增 `tests/test_acp2_mcp_ux_contract.py`：一个 ID 至少一条断言（错误码 / 字段 / 形状）。
2. 既有门保持绿：`tests/test_acp2_mcp_sdk_contract.py`、`tests/test_services_mcp.py`、`tests/test_acp2_preflight_fingerprint.py`、`tests/test_acp_confsearch_progress.py`。
3. **fixture 闭环**（P3 后）：提交 → 完成 → status 发现 → prepare → read，不手填路径/摘要；摘要三级验收见 P3-07。
4. **真实科学验收**（单独排期，本轮不做）：正确请求跑通真实 QC。不要把 fixture 派发写成真实 PASS。
5. 证据三态：PASS / FAIL / NOT_VERIFIED；外部专家意见只记为 `source: codex`，不得升格为 PASS。

---

## 6. 待拍板

- [x] **worktree 隔离**：直接执行（P0-01）。真正要确认的是"哪些既有补丁及依赖进入实施基线"，干净 HEAD 与带补丁基线分别留证。
- [ ] **C1**：先定原则——关闭归因前必须拿到实际客户端链路证据；不预设根因，也不阻塞其余修复。
- [ ] **P1 范围**：建议接受"只覆盖 Confsearch + BatchOptimize"，但必须公布校验覆盖范围，且"schema 不完整"不得掩盖其余工作流仍可能静默忽略参数。
- [ ] **投入上限**：11–17 人日是乐观区间；要拍板的是预算上限与 3 天交付边界，不是认可一个未经拆解的数字。
