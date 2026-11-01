# Round-8 修复计划：外部 MCP Agent 第三轮反馈（全研究链）+ 方法选择 USER CORRECTION

- 基线：`main` = `d6c993bc`（Round-7 合并 PR #55），工作区仅一个未跟踪的 `.scratch/`（本轮**不碰**）。
- 本轮为**只读规划**：未改代码、未跑真实 QC、未重启服务、未调用外部模型。允许的写操作只有本文件。
- 基线核查方式：源码阅读 + `/opt/acp/venv/bin/python` 导入纯函数测量 payload 体积 + 跑 1 个非慢测试（`tests/test_acp_guidance_pack.py` 18 passed）。
- 术语沿用 Round-7：P0 = 让整条研究链不可用或让 agent 拿到「预检可行但运行必挂」的死路；P1 = 契约/闭环缺口，本批修；P2 = 体验与可观测性。

---

## 总览

本轮外部反馈 15 条里，真正会**把研究链走成死路**的只有 3 条，恰好都落在「预检说可行 / 文档说能构造 → 运行时挂」这一类，全部升为 P0：(1) `input.items` 少了 `source_type: "batch_structures"` 时预检 `feasible:true`、运行时报一句不指名字段的通用错误（`runner.py:1601`）；(2) `topic.artifact_locating` 明文说 status 描述符自带 `generation/digest/size` 五元组，实测描述符只有 5 个字段、`size_bytes` 恒为 `null`，而 `acp_read_artifact` 的 schema **强制**五元组（`mcp.py:1074-1085`），唯一合法来源 `acp_prepare_artifact` 在指南里没有可操作的调用说明——文档本身就是错的那条「第二条路」；(3) IRC 契约示例给出的 `ts_source: {"schema":"irc_ts_source_v1"}` 空壳是**运行时陷阱**：服务端会自行从 `source_job_id + source_product_id` 派生 provenance，一旦你手写了这个空壳，`manager._verified_irc_spec` 的 locked-field 等值检查会把 `None != 值` 判成「IRC source evidence changed since the previous submission」（`manager.py:1408-1430`），而 `analyze_irc_submission` 完全不看这三个字段（`submission_validation.py:355-398`），所以预检也可行。第 4 条 P0 是用户直接下达的 **USER CORRECTION**：优化默认/最优选择改为 r2SCAN-3c（绝大多数体系），**不再推荐 wB97M-V 做几何优化**；wB97M-V 只保留为单点默认——这条覆盖 round-6/7 指南正文（`guidance_pack.py:708-721, 943-946`）。P1 覆盖：状态词表缺 `starting/pending/cancelling` 且 counts 在过滤后计算（`v1_routes.py:3180`）、Batch 顶层 mirror 在缺 role 时静默失效（`submission_contract.py:862-900` 只覆盖 SP mirror）、TS 失败分类只回 `[unknown]`（`orca.py:111-145`）、`result_manifest` 缺能量/Gibbs（thermo 只在 Shermo 步登记，`engine.py:1711-1751`）、目录名复用与「两个真相源」、local-single 仍被打码。P2 是 Hessian 重复计算（需要科学裁决，默认不动）、上下文体积（合同/信封的**有界投影**）、以及 Round-7 已经修好的 `task.find_transition_state` 摘要一致性回归守护。**本轮范围** = A–E 五个互不重叠的写批（见「批次拆分」），全部是「不放宽任何护栏」的加法或收紧密封。**需要外部样本/裁决**：`20261101_173534_008` 与 `20261101_183311_022` 的 `task.json`/`job.json`/`state.json`、job 024 的状态调用原文（到底传的是 job_id 还是 operation_id）、三次 `[unknown]` 的 `WORK/**/*.out` 尾巴、以及「Hessian 复用策略」「Batch source_type 严格度」两个科学/契约决定（见末节）。

---

## 逐条计划

### 一.1 BatchOptimize：`input.items` 无 `source_type` 时预检可行、运行时通用报错（P0）

**复现性/根因（VERIFIED）**
- 契约：`WORKFLOW_CONTRACTS["BatchOptimize"].required_input_any = ("items","from_artifact","items_file","input","source")`（`src/acp/api/submission_contract.py:453`）；`_BATCH_INPUT_PROPERTIES["source_type"]` 只有 `enum:["batch_structures"]`，没有一句「有 items 就必须带它」（`src/acp/api/workflow_contracts.py:103`）。注意：契约**示例里确实有** `"source_type": "batch_structures"`（`workflow_contracts.py:417`，round-7 前也在），所以「示例也没写」这一子命题 **NOT_REPRODUCED**；真正缺的是「`items` 与 `source_type` 的配对是硬要求」这一点没写进 `required_any`/constraints。
- 预检：`analysis_for("BatchOptimize", ...)` → `analyze_batch_submission` 只解析 **method**，`input_data` 仅用于 `_batch_item_roles`（读 `items[].role/tag/xyz` 的 TAG），**从不判断来源是否可被物化**（`src/acp/calculations/submission_validation.py:624-758`）。因此 `feasible:true / reasons:[] / advisories:[]` 完全符合当前实现。
- 提交边界：`_validate_input` 只做 `required_input_any` 的「存在性」检查（`submission_contract.py:890-918`），`batch_structures_only` 只拒绝 `len(items)>1`（`:927-937`）。
- 运行时：`materialize_job_input` 只在 `source_type == "batch_structures"` 时展开 items（`src/acp/scheduler/runner.py:504-506`），否则落到 `_materialize_single_input`（找 `source/input/smiles`）→ 返回 `None`；派发时 `runner.py:1585-1601` 走到 `else: raise ValueError("BatchOptimize job requires a batch artifact, items file, or materialized structure")`——错误里没有 `input.source_type`、没有「你给了 items」、没有可执行的修法。
- 附带证据：`remote/script_gen.py:216` 与 `remote/runner.py:1730` 同样以 `source_type` 为唯一开关，远端同构。

**修复方案（最小、收紧密封，不新增第二解析路径）**
1. `src/acp/api/submission_contract.py::_validate_input`（Batch 分支）：当 `payload.get("items")` 存在且非空时，要求 `source_type == "batch_structures"`，否则抛 `SubmissionContractError(rule="missing_source_type", field_name="input.source_type", details={"items_count": n, "hint": "..."})`。**不**在运行时猜「有 items 就当 batch」——那会让同一字段有两种含义。
2. `src/acp/api/submission_contract.py::_validate_method` 的 Batch mirror 段：把「缺 role 的顶层 mirror」拒绝规则从 `_SP_ROLE_MIRRORS` 扩到 `_WORKBENCH_FLAT_MIRRORS`（见 二.4），共用同一 `missing_role_mirror`。
3. `src/acp/calculations/submission_validation.py::analyze_batch_submission`：新增 `input_source` 投影 —— 判定 `source_type/items/from_artifact/items_file` 四种载体哪一种是生效来源，并在 `validated_scope` 里加 `"input_source"`；若 `items` 存在但 `source_type` 缺失，直接抛 `SubmissionAnalysisError(rule="missing_source_type")`（与 submit 同一个 rule，保持 preflight/submit 同码）。
4. `src/acp/scheduler/runner.py::_command_for`（Batch 分支）与 `remote/script_gen.py`：把 `ValueError` 文案升级为结构化提示（保留原句可 grep）：`BatchOptimize input items were not materialized: set input.source_type="batch_structures" (requires items) or provide from_artifact/items_file`。派发前先 `if inp.get("items") and not source_type: raise ValueError(<同上>)`，让失败点从「材料化之后」前移到「能指名字段」。
5. `src/acp/api/workflow_contracts.py::_batch_block`：`gaps` 增一句「items 必须与 source_type=batch_structures 同时出现」；`input.required_any` 保持（它是 union），但在 `properties.source_type.description` 写明条件必填；示例保持现状（已正确）。

**测试**
- 既有：`tests/test_acp_submission_contract_first_batch.py`、`tests/test_acp_round7_preflight_batch.py`（preflight/submit parity + 零副作用）、`tests/test_batch_optimize.py`。
- 新增 `tests/test_acp_round8_batch_input_source.py`：
  1. `items` 无 `source_type` → submit 422 `missing_source_type`，**preflight 同样 422**（同 rule/field），`would_create_task=False`；
  2. `items + source_type` → 通过，且 `submission_analysis.effective.input_source.mode == "batch_structures"`；
  3. `items` 空 + `from_artifact` → 通过（不能误伤 artifact 路径）；
  4. runner `_command_for` 单测：缺失时报错文案含 `source_type`，且 **不** spawn 任何进程（mock 断言）。
- 回归守护：preflight 仍零副作用（沿用 round-7 fixture，断言 store/task 目录未变）。

**优先级** P0。**风险/回滚**：会把「以前能提交、运行几秒后失败」的 payload 变成 422——这是本轮意图（create-now-fail-later → fail-fast）；回滚只需撤掉 `_validate_input` 的这一条与 analyzer 的同名检查，互不牵连。**依赖**：无（批次 A 独立）。

---

### 一.2 `acp_read_artifact` 的 ArtifactRef 构造不出来 / 指南自相矛盾（P0）

**复现性/根因（VERIFIED，且是文档错误而非代码缺失）**
- `acp_read_artifact` 的 `artifact_ref` **强制**五元组：`required: ["task_id","attempt","artifact_id","generation","digest"]`（`src/acp_services/entrypoints/mcp.py:1074-1085`）；`_validate_artifact_refs` → `normalize_artifact_ref`（`:1627-1651`），缺字段即 typed error。
- 描述符由 manifest 投影，**结构上不包含** generation/digest：`src/acp/api/artifact_descriptors.py:107-120` 只输出 `resource_id/relative_path/kind/label/size_bytes(None)/prepared(False)/attempt`；模块 docstring 明确写「never invents them」（`:1-10`）。
- `ArtifactRef` 的 `generation/digest/size_bytes` 只能由 prepare 操作提交（`src/acp_services/data/artifacts.py:247-295`、`operation_to_artifact_ref:1102-1135`）；prepare 是唯一的 `generation` 来源（`record.resource_generation`）。
- **指南写错了**：`topic.artifact_locating` body 第 1 条说「status 的返回携带 artifacts 描述符（artifact_id、generation、digest、size 等）——五元组全部来自该描述符」（`src/acp/api/guidance_pack.py:744-747`），与代码冲突；同时第 4 条又说「本地已终态且描述符可用时可直接 read」（`:779-781`），这条指令在 schema 上无法执行。这解释了外部 agent「按文档永远凑不出合法 ArtifactRef」。
- 另有两处次生摩擦：(a) `size_bytes` 恒 `null`，但文件大小可以安全读取；(b) MCP read 对 body 做 `redact(decoded) != decoded` 拒读（`mcp.py:3548-3553`），含绝对路径/token 形状的结果文件会被 `artifact_body_redacted` 拒——这也是「只能 cat 文件」的一部分推力（**NOT_VERIFIED** 是否命中该 agent 的具体文件）。

**修复方案**
1. **改正指南为唯一真话源**（批次 C）：`topic.artifact_locating` 改为：
   `status.artifacts` = **描述符**（`resource_id`、`relative_path`、`kind`、`label`、`size_bytes`、`prepared`、`attempt`），**不是** ArtifactRef；
   读取路径 = `acp_prepare_artifact({job_id|task_ref, purpose:"artifact", artifact_id})` → 回包 `artifact_ref`（含 `generation/digest/size_bytes`）→ `acp_read_artifact({artifact_ref, max_bytes?, encoding?})`；
   同一条目里给出**一个可复制的最小 payload**和「read 永不触发 prepare，prepare 永不启动科学计算」；删掉「描述符可直接 read」。
2. `src/acp/api/artifact_descriptors.py::describe_job_artifacts`：`size_bytes` 用 `RESULT/<relative>` 的 `stat().st_size` 填（路径已经过 `validate_artifact_relative_path`，越界即 None；异常 → None，绝不发明）；新增 `"prepare"` 小对象 `{"tool":"acp_prepare_artifact","arguments":{"job_id":..., "purpose":"artifact","artifact_id":"<resource_id>"}}`，让描述符**自己指向下一步**；`prepared` 仍为 `False`（描述符代表未 prepare）。
3. `src/acp/api/v1_routes.py` 的 `_with_artifact_descriptors`/`get_job`：把 `artifacts_unknown_reason` 的取值补一个 `"no_manifest"`（当前只有 remote 的 `"remote_cache_only"`），让「空 artifacts」可区分「没产物」与「读不到」。
4. （与 三.7 同批）local-single 下不再对 `work_dir/directory_name/name/idempotency_key` 打码，见 三.7；这对本条的「能不能定位」是同一体验问题。
5. `mcp.py` prepare 的 schema 描述里加一句「descriptor.resource_id → artifact_id」的映射（`_PREPARE_SCHEMA.description`，`:1200-1215` 附近），避免再出现「resource_id vs artifact_id」歧义。

**测试**
- 既有：`tests/test_acp2_mcp_guidance_tool.py`、`tests/test_acp_mcp_guidance_index.py`、`tests/test_acp_artifacts.py`、`tests/test_acp2_runtime_artifact.py`、`tests/test_acp_mcp_ux_p3_artifact.py`。
- 新增 `tests/test_acp_round8_artifact_prepare_chain.py`：
  1. 造一个带 `RESULT/result_manifest.json` 的假 task_dir → `describe_job_artifacts` 的每个描述符都有 `prepare.arguments.artifact_id == resource_id`，`size_bytes` 与磁盘一致，`prepared is False`；
  2. 用描述符 → prepare → read 全链（mock Store/Operation）：prepare 回包里的 `artifact_ref` 正好是 read schema 接受的最小五元组；**断言 describe 本身不产生任何 operation/prepare**（零副作用）；
  3. 路径越界（`../`）、Windows 分隔符、未知 resource_id → 描述符被跳过（不发明条目）；
  4. 指南文本断言：body 不再出现「描述符…generation」（防回归的字符串守护，放在 `tests/test_acp_guidance_pack.py`）。
- 守护：`topic.artifact_locating` 的 `size_bytes <= MAX_GUIDE_BYTES`（现有测试已覆盖全部 guide）。

**优先级** P0。**风险/回滚**：`size_bytes` 由 `null` → 数值是**加法**（旧客户端按 null 处理）；`prepare` 子对象是加法；指南改写会改变 `version` 与 digest（缓存失效是预期）。回滚 = 单 commit 还原 `artifact_descriptors.py` + 指南段落。**依赖**：与 三.7（redaction）共享「local-single 可见性」结论，但文件不重叠（批次 B vs 批次 B？见批次拆分：两者都在批次 B 的 `mcp.py/machine_output.py` 面 → 合并到同一批，避免跨批语义漂移）。

---

### 一.3 IRC `ts_source` 是黑盒、空壳预检通过但运行必挂（P0）

**复现性/根因（VERIFIED，且比 agent 猜的更糟：它不是「大概率炸」，是**确定**炸）**
- 契约约束只说「runner requires the verified ts_source provenance (irc_ts_source_v1)」（`src/acp/api/workflow_contracts.py:750`），示例就是空壳 `{"schema":"irc_ts_source_v1"}`（`:841`）；`ts_source` 的 property 描述只有一句「Resolved provenance」（`:859-862`）。`workflow.irc` 指南只写「带 ts_provenance」（`guidance_pack.py:876`），全文没有字段表。
- 真值：`ts_source` **不需要客户端构造**。`manager._verified_irc_spec` 用 `source_job_id + source_product_id` 去 `resolve_verified_ts_source` 派生完整 provenance（`src/acp/scheduler/manager.py:1391-1410`），并把它写回 spec（`:1485`）。客户端提供的 `ts_source` **只**用于「来源是否变化」的等值检查：`previous.get(key) != current[key]`，`locked_fields` 十个（`manager.py:1408-1430`）。
- 因此空壳 `{"schema": ...}` → `previous.get("job_id") = None != current["job_id"]` → `ValueError("IRC source evidence changed since the previous submission")`。错误语义还误导人（以为来源变了，其实是没填）。
- 预检：`analyze_irc_submission` 只查 `method.step`/`initial_hessian`/`maxpoints`，**完全不动** `ts_source/source_job_id/source_product_id`（`submission_validation.py:355-398`）——所以空壳 `feasible:true`，与第 1 条同型。
- 附带：CLI 的 `--ts-provenance`（`src/acp/cli.py:221`）是**人工路径**，与 MCP 派生路径不是一回事；指南没有区分，这也是「黑盒」的一部分。

**修复方案**
1. **契约示例改对**（批次 A）：`workflow_contracts.py::_irc_block` 的 example 删除 `ts_source`，只保留 `source_job_id + source_product_id + directions`（+ 可选 `input_role`）；constraints 增一条「`ts_source` 由平台派生并固定；不要手写；提供时只做等值校验，部分字段会被拒绝」。
2. **提交边界拒绝半成品**（批次 A，`submission_contract.py::_validate_input` 的 irc 分支或新 `_validate_irc_source`）：`ts_source` 出现且缺少任一 `locked_fields` → 422 `rule="irc_ts_source_partial"`，`params.hint` 直接给出「删除 input.ts_source，平台会从 source_job_id/source_product_id 派生」。完整 `ts_source`（十字段齐全）仍接受（保持幂等重放/CLI 兼容），由 manager 做等值检查。
3. **预检投影来源可解析性**（批次 A）：`analyze_irc_submission` 增加只读 `source_reference` 投影：`{"source_job_id": ..., "source_product_id": ..., "authored_ts_source": "absent"|"complete"|"partial", "resolution": "runtime_derived"}`；`validated_scope` 加 `"source_reference"`，并加一条 advisory（不是 fail）：真实 TS 校验在提交时由 `resolve_verified_ts_source` 完成（需要源作业 completed + 恰一个虚频 + Batch TS item 证据）。**不**在 analyzer 里读 store（保持纯函数与 preflight 零副作用）。
4. **manager 错误信息区分两种失败**（批次 D）：`previous` 非空但**不是**完整 provenance → 单独抛 `ValueError("input.ts_source is partial; omit it — the platform derives provenance from source_job_id/source_product_id")`；只有完整 provenance 不等值才用现有「changed」文案。
5. **指南补全 `workflow.irc`**（批次 C）：写清「三件套 = `source_job_id`（BatchOptimize/tsmode 的 completed 作业）+ `source_product_id`（registry 里的结构产品，Batch 的 TS 产品形如 `batch_<item>`，路径含 `__TAG_TS__`）+ `directions`」；`ts_source` 由平台派生；CLI `--ts-provenance` 是人工/历史路径，MCP 不要用。

**测试**
- 既有：`tests/test_acp_irc_directions.py`、`tests/test_acp_irc_live_metrics.py`、`tests/test_acp2_science_da_irc_scenario.py`、`tests/test_cccp_irc_boundary_contract.py`。
- 新增 `tests/test_acp_round8_irc_source_payload.py`：
  1. 空壳 `ts_source` → submit 422 `irc_ts_source_partial`；preflight 同 rule；`would_create_task=False`；
  2. 省略 `ts_source` + 合法 `source_job_id/product_id` → 契约通过，`analysis.effective.source_reference.resolution == "runtime_derived"`；
  3. 完整 provenance 但改一个 `geometry_sha256` → 仍由 manager 报「changed」（不得被新规则吞掉）；
  4. manager 单测：partial 与 mismatch 两条路径的错误文案不同且都可 grep（`partial` vs `changed`）。
- 回归：`tests/test_acp_round5_preflight_parity.py`（preflight/submit 同码）。

**优先级** P0。**风险/回滚**：拒绝 partial 会让「手工拼了半份 provenance」的旧客户端从运行期失败变成 422（意图内）；完整 provenance 与 omit 两条路径不变。回滚 = 撤 `_validate_irc_source` 与 manager 的分支文案。**依赖**：与 一.1 共享 `submission_contract`/`submission_validation`（同批 A）。

---

### 二.1 同一字段名三种形状 + `source_type` 枚举两套（P1，文档表 + 最小一致性）

**复现性/根因（VERIFIED）**
- PESsearch 的 `input.source` 是对象（`_PES_INPUT_PROPERTIES`，`workflow_contracts.py` 内 `_pes_block` 使用的 `source:"object"` 分支）；simple 系列用 `input.source_type`（字符串枚举 `smiles|xyz_text|structure_asset`，`workflow_contracts.py:82-84`）+ `xyz_text`/`input`；Batch 的 `source_type` 枚举只有 `batch_structures`（`:103`）。
- 运行时各工作流自己的 `_require_source_type`/`materialize_job_input` 只认自己那套（`runner.py:504` 起），**没有**全局统一解析器。
- 这是三套并存的**真实**契约，不是笔误；强行全局统一会同时动 15 个工作流的提交路径和远程脚本生成，风险远超收益。

**修复方案（只做「可发现」，不做「大统一」）**
1. `src/acp/api/workflow_contracts.py`：新增一份**静态表** `SOURCE_SHAPE_BY_WORKFLOW`（workflow → {carrier: `source_object`|`source_type+text`|`batch_structures`|`*_request`|`source_job_id`, enum, notes}），在 `_capability_index`/`describe_workflow` 的 `guide_ref` 旁挂 `input.source_shape`；每个 workflow 的 `input` 块加 `"source_shape": "<key>"`。表是**投影**，不参与校验（单一权威仍是各 workflow 的 contract/runtime）。
2. 枚举统一为**两份声明 + 一处交叉校验**：`SIMPLE_SOURCE_TYPES = {"smiles","xyz_text","structure_asset"}`、`BATCH_SOURCE_TYPES = {"batch_structures"}` 放在 `submission_contract.py`，供 `_CONFSEARCH_INPUT_PROPERTIES`/simple 块引用；再加一个测试断言「任何 `_*_INPUT_PROPERTIES.source_type.enum` 必须等于这两个集合之一」——防止未来再漂出第四种拼写。
3. 指南（批次 C）：在 `topic.inputs_electronic_state` 里加一张 5 行表（PES / scan / Batch / simple / tsmode），明确「字段名不是同一个概念，按表写」。

**测试**：新增 `tests/test_acp_round8_source_shape_table.py`（表全覆盖 15 个 active、枚举断言、`additional_properties:false` 与表一致）；既有 `tests/test_acp_api_v1.py`、`tests/test_acp_submission_contract_first_batch.py`。**优先级** P1。**风险/回滚**：纯加法（多一个字段/表）；回滚 = 删表与字段。**依赖**：与 一.1、二.4 同批（A）。

---

### 二.2 `task_state` 存在未登记值 `starting`（P1）

**复现性/根因（VERIFIED）**
- 发布词表只有 7 个：`queued/running/paused/waiting_review/failed/cancelled/completed`（`src/acp/api/workflow_contracts.py:45-57`）。
- 真实枚举有 10 个：`QUEUED/STARTING/PENDING/RUNNING/PAUSED/CANCELLING/WAITING_REVIEW/CANCELLED/COMPLETED/FAILED`（`src/acp_services/store/records.py:35-57`），`is_active` 含 STARTING/PENDING/CANCELLING（`:59-71`）。
- 过滤是**精确等值**：`store.list(status=...)` → `WHERE status=?`（`acp_services/store/store.py:722-724`）；v1 路由在内存里 `record.status.value == status`（`v1_routes.py:3160`、`:1575`）。所以 `status=running` 查不到当时是 `starting` 的作业，`jobs:[]` 正确但**词表外**。
- 另叠加一个真 bug：`counts` 在**过滤后**统计 —— `_counts_for_records([item["record"] for item in enriched])`（`v1_routes.py:3180`，project 路由 `:1580` 同），过滤结果为空 → `{"counts": {}, "jobs": []}`。这正是 agent 看到的形状。
- `acp_list_jobs` 只是转发 `status` 字符串（`mcp.py:3603-3644`），不做枚举校验，所以词表外的值静默返回空。

**修复方案**
1. `workflow_contracts._STATUS_VOCABULARY.task_state` 扩为 10 个真实值，并加 `"filter_semantics": "exact match; use status_group for a family"` 与 `"groups": {"active":[...], "terminal":[...], "running_now":["starting","pending","running"]}`。
2. `v1_routes.py`：新增 `status_group` query（`active|terminal|running_now`），与 `status` 互斥（同时给 → 422 `input_invalid`）；`counts` 改为**过滤前**的项目/全局计数（新增 `_scoped_counts(store, project_id)`，用 `store.counts()` 或 `list_enriched` 前的快照；保证 10 个 key 恒定存在）。
3. `mcp.py::_tool_list_jobs`：`status` 做词表校验（非法 → `input_invalid` + `allowed`）；新增可选 `status_group`；返回体里回 `counts`（未过滤）与 `filter` 回显。
4. 指南（批次 C）：`topic.artifact_locating` 或新 `topic.task_locator` 写「状态是精确匹配；`starting/pending` 也是活动态」。

**测试**：既有 `tests/test_acp2_mcp_list_jobs_tool.py`（转发/摘要/字段）、`tests/test_acp_job_status_view.py`；新增 `tests/test_acp_round8_task_state_vocab.py`：10 值齐全；`status=starting` 命中；`status_group=active` 命中 starting+queued+pending；非法 status 422；空结果时 `counts` 仍为 10 个 key（非 `{}`）。**优先级** P1。**风险/回滚**：counts 语义从「过滤后」改「过滤前」是**行为变更**，需在 PR 描述标注（旧客户端若把 counts 当 filtered total 会看到更大数）；回滚 = 单点还原 counts 调用。**依赖**：无（批次 B）。

---

### 二.3 顶层摘要宣传的链路走不通（publish 恒被拒 / `pes_recommendations` disabled）（P1，Round-7 已大半修复，本轮只补一致性与显式性）

**复现性/根因（VERIFIED / 部分已被 round-7 修）**
- `task.find_transition_state` 摘要**在 `d6c993bc` 已改**为「（有授权才 publish，否则 inline role:"ts" 一等路径直达）」（`guidance_pack.py:791-793`），正文 chain 第 3 步与 decisions 也一致（`:820-846`），不再是「summary 说必须 publish、正文说 publish 恒被拒」的矛盾。外部投诉对应的是 round-7 之前的快照 —— **STALE**，但需要守护。
- `pes_recommendations` 仍恒 `disabled/manual_only_policy`：`src/acp/calculations/pes/outputs.py:118-145`（`status:"disabled"`, `reason_code:"manual_only_policy"`，reason 文本「native algorithmic recommendations are disabled; authorized ...」）。
- 纯 MCP 会话的 publish 恒被拒是**产品决定**（无预铸有界授权），不是 bug。

**修复方案（只做显式化与一致性守护，不新增自动授权）**
1. 契约层：`workflow.PESsearch` 的 gaps/说明里写明 `pes_recommendations.status="disabled"` + `reason_code="manual_only_policy"` 是**预期**，`ts:[]` 时走「读曲线 + 人工/内联选点」；避免 agent 把 disabled 当故障重试。
2. 指南：`orchestration.pes_to_batch` 与 `task.find_transition_state` 的 summary 保持一致（一个 `_SUMMARY` 常量被两处引用，或加测试断言子串）；`workflow.PESsearch` 增加「自动发布不存在，publish 需要 operator 预铸授权」。
3. 守护测试：`tests/test_acp_guidance_pack.py` 增加「摘要不得声称 publish 是必经步骤」的断言（grep `必经`/`必须发布`）；`tests/test_acp_round5_preflight_parity.py` 不变。

**测试**：`tests/test_acp_guidance_pack.py`（新增 2 条字符串守护）、`tests/test_acp_api_guidance_routes.py`。**优先级** P1（文档一致性），**风险/回滚**：纯文本；**依赖**：批次 C。

---

### 二.4 Batch 示例的顶层 `optimization_method` 与 mirror 规则冲突 / 只交 TS 时能不能写顶层（P1）

**复现性/根因（VERIFIED，且是**静默失效**）**
- 示例里同时有 `batch_roles` 与顶层 `optimization_method`（`workflow_contracts.py:415-437`），约束却只说「顶层 single_point_* 只能镜像 batch_roles.int」（`_BATCH_CONSTRAINTS`，round-7 改写）。
- 代码：`_flat_mirror_source` 把 `optimization_*` 映射到 **int** role（`submission_contract.py:381-401`）；但「缺 role 就拒绝」的检查**只覆盖 `_SP_ROLE_MIRRORS`**（`:862-900`）；`_WORKBENCH_FLAT_MIRRORS`（含 `optimization_method/optimization_basis/temperature/...`）在缺 `int` role 时走进下面的值等值循环，`role_value` 不是 Mapping → `continue`（`:901-920`）→ **顶层值被静默忽略**。`BatchMethodOptions` 在 `batch_roles` 存在时不读扁平字段，与 API 行为一致（`options.py` 早期返回）。
- 结论：只交 `ts`（不交 `int`）时写顶层 `optimization_method` **不报错、也不生效**——「赌一把不写」恰好是唯一安全写法，但这不该靠赌。

**修复方案**
1. `submission_contract.py`：把「缺 role 的 mirror」拒绝扩到 `_WORKBENCH_FLAT_MIRRORS` 全部键（`temperature/pressure/scale_factor` 只在**非默认值**时算 mirror，复用 `_is_workbench_default`），rule 统一 `missing_role_mirror`，`details.role` 指到 `batch_roles.<role>.<field>`；消息给出两种正确写法。
2. 同时保留「值不等 → `mirror_conflict`」的现有分支（`:920-940` 附近），不合并两者。
3. 契约文本：`_BATCH_CONSTRAINTS` 把「top-level single_point_* 只能镜像 int」扩写成「所有顶层 `optimization_*`/`single_point_*`/温度压力缩放都只是 int role 的 Workbench 镜像；缺 int role/字段会被 422 拒绝，不会被静默忽略」。
4. 示例：保留现有 `optimization_method`（int role 在），但**加第二个示例**「TS-only：不写任何顶层 mirror，只写 `batch_roles.ts`」——直接把「赌不赌」变成可照抄。

**测试**：新增 `tests/test_acp_round8_batch_mirror_missing_role.py`：`ts`-only + 顶层 `optimization_method` → 422 `missing_role_mirror`；`ts`-only + 顶层 `temperature=298.15`（默认）→ 通过；`int`+`ts` + 顶层与 int 一致 → 通过；顶层与 int 不一致 → `mirror_conflict`；`effective.roles.*.optimization_method` 与期望一致。**优先级** P1。**风险/回滚**：从「静默」变「422」是收紧（意图内）；回滚 = 撤 `_WORKBENCH_FLAT_MIRRORS` 的缺 role 分支。**依赖**：批次 A（与 一.1 同文件）。

---

### 二.5 `levels` 语义只有类型检查；`recalc_hess: 5` 与 `"auto"` 的区别 API 层不可知（P1 + 部分 P2）

**复现性/根因（VERIFIED）**
- 契约自认：`submission_contract.py:21`（模块级「Nested `levels` content is type-checked only」）、`workflow_contracts.py:643`（Batch gaps「level values are type-checked only」）；Batch 的 `levels` 表 40+ key 由 `_level_table` 从 catalog 生成（`:328-333`）。
- 但有一类语义**在 API 层是可判定的**：`opt_recalc_hess`/`opt_initial_hessian` 的 `"auto"` 走元素分级的纯函数解析（`src/cccp/qc/hessian_policy.py:191-236` → `default_recalc_hess_for_symbols(symbols)`），`5` 是显式间隔。TS 默认 `recalc_hess=5`（`options.py:59-70`），INT 默认 `"auto"`（`:74-80`）。
- 因此「5 vs auto 有什么区别」**不该**是不可知：只要给出元素组成，就能投影出 `auto → N` 与 `reason: auto|explicit_interval|explicit_off`。

**修复方案**
1. （P1，批次 A）`analyze_batch_submission` 的 `effective.roles.<role>` 增加 `hessian_resolution: {"initial": {...}, "recalc": {"requested": "auto"|5|null, "resolved_interval": N|null, "reason": "...", "source": "..."}}` —— 复用 `cccp.qc.hessian_policy` 纯函数，符号从 `items[].xyz` 解析（只读、零副作用）；无符号（无 items）时 `resolved_interval:null` + `reason:"symbols_required"`。
2. （P2）`workflow_contracts._batch_block`：`method.levels_note` = 「level values 只做类型/枚举检查；语义归 CLI/consumer；`opt_initial_hessian`/`opt_recalc_hess` 的有效值在 preflight 的 `effective.roles.*.hessian_resolution` 里给出」；`_level_table` 保持原样（**不**伪造每个 key 的语义）。
3. （P2，批次 C）指南 `workflow.BatchOptimize`：加一句「`levels` 是透传区；要看生效值读 preflight 的 `effective`」。

**测试**：新增 `tests/test_acp_round8_batch_hessian_effective.py`：`ts` 默认 → `recalc.requested=5, resolved=5, reason=explicit_interval`；`int` 默认 → `requested=auto, resolved=<元素规则值>, reason=auto`；无 items → `reason=symbols_required` 且不抛错。既有 `tests/test_acp_round7_batch_roles_sp.py`、`tests/test_protocol_levels.py`。**优先级** P1（hessian_resolution）/ P2（文本）。**风险/回滚**：`effective` 加法；不改变执行。回滚 = 删投影。**依赖**：批次 A。

---

### 三.1 目录名被复用，同类目两个作业一个残留 `state.json`（P1）

**复现性/根因（部分 VERIFIED，具体因果 NOT_VERIFIED）**
- 命名：物理目录 = `<molecule>_<task>_<remark>`，由 `JobSpec.task_dir_name()` 生成（`acp_services/store/records.py:118-133`），冲突时 `__NN` 后缀（`manager._dedupe_task_dir`，`manager.py:3530-3544`）。
- 分配是「先解析候选 → `mkdir(exist_ok=False)` → `FileExistsError` 再 `__NN`」（`manager.py:3475-3495`）。**只要目录在，就不可能复用同一 leaf** —— 所以「两个作业同 leaf」意味着**分配那一刻旧目录已经不在**，或旧作业记录指向的路径被别的东西重建。
- 清理路径中确有 `shutil.rmtree`：`manager.py:1228-1230`（adoption 失败：staging + final_dir 都删）、`:2512`、`:2648`（purge/delete）。`manager.py:2051-2153` 的「归档 `.exit_code`/`state.json`/`run.lock`」说明 `state.json` 是有意保留/归档的 sidecar。
- 结论：最可能是「失败的 008 在 adoption/失败清理里删了目录（或只删了部分），DB 行仍保留该 `work_dir`；随后 022 在**已释放**的 leaf 上合法分配」，于是历史 `job.json` 说 008 在 `__10`、022 也在 `__10`，而残留 `state.json` 是 008 的 sidecar。**需要 008/022 的 `task.json`/`job.json`/`state.json` 才能定案**（见末节）。另一可能：`move_job` 的 `_resolve_work_dir` 在目标已存在时抛错，不会覆盖，可排除。

**修复方案（两条不变量 + 一条可观测性，皆不改变正常路径）**
1. **不删非空目录**：`manager.py:1228-1230` 改为「只有本流程创建且目录为空时才 rmtree」；非空 → 保留 + `logger.error` + 事件 `task.dir_adoption_failed_leftover`（绝不再 `ignore_errors=True` 全删）。
2. **分配时校验归属**：`_allocate_work_dir` 成功后若 `<dir>/task.json` 已存在且 `task_id != job_id` → 释放并 `__NN` 自增（`task.json` 是 `_SCHEDULER_MARKERS` 成员，ANTI #11 已守护，勿新增 marker 文件）。
3. **状态回包可交叉验证**：`v1_routes` 的 job model 增加 `directory_owner_task_id`（来自 `<work_dir>/task.json`，读不到则 `null`）；当它 != `record.id` 时回 `directory_conflict: true` 而不是静默。
4. 指南（批次 C）：`topic.artifact_locating` 第 2 条改「目录名**不是**唯一标识，可能被后续作业复用；一律用 `task_id`（= `task.json.task_id`）交叉验证」。

**测试**：新增 `tests/test_acp_round8_task_dir_reuse.py`：预置「空目录」「只有 state.json 的目录」「含他人 task.json 的目录」三种 → 分别为 复用/保留+自增/自增；adoption 失败路径不得删除含文件的目录（mock `shutil.rmtree` 断言未调用或仅空目录）。**优先级** P1。**风险/回滚**：`__NN` 自增会让目录名多一个后缀（不做反查的客户端不受影响）；不删非空目录可能留下垃圾目录（可接受，胜过丢证据）。**依赖**：批次 D。

---

### 三.2 两个真相源：job 024 文件系统有结果、`acp_status` 回 `resource_not_ready`（P1）

**复现性/根因（机制 VERIFIED，触发条件 NOT_VERIFIED）**
- `acp_status` → `_tool_status` → `self._backends.status.get(reference)`（`mcp.py:3393-3405`），Controller 侧 `status()` 打 `GET /api/v1/jobs/{id}`（`controller_client.py:560-571`）；路由 `manager.get(job_id)` → `store.get()` **只查 DB**（`manager.py:1496-1497`、`store.py:647-650`），无 run_root 兜底。
- 找不到 → 404 → `controller_client._HTTP_ERRORS[404] = ("resource_not_ready", NotPreparedError)`（`controller_client.py:362`）；MCP 再把 `None` 包装成 `RESOURCE_NOT_READY("operation/job reference not found")`（`mcp.py:3399-3404`）。**把「这个 id 我根本不认识」误分类成「还没准备好、可重试」**，并且 `retryable=True`。
- 两种最可能的触发：(a) 调用用的是 **operation_id** 而非 job_id；(b) DB 行缺失/不在当前项目作用域（例如另一实例的 run_root、迁移后的孤儿目录）。**两者都需要原始调用串才能区分**。

**修复方案**
1. 错误分类（批次 B）：`mcp.py::_tool_status` 把「记录不存在」映射为 `ErrorCode.NOT_FOUND`（若无该枚举则新增，语义「unknown reference」，`retryable=False`），消息写明「未知 reference；用 acp_list_jobs 或 task.json.task_id 核对；job_id ≠ operation_id」。`controller_client` 的 404 映射改为「若 detail 含 `Job not found` → NotFoundError，否则保留 NotPreparedError」——**不**放宽任何访问检查。
2. 诊断（批次 B）：路由 404 的 detail 增加只读探测：该 id 形状合法时，检查 `<run_root>/<project>/<dir>/task.json` 是否存在同名 `task_id`；存在 → `{"code":"job_row_missing","run_root_task_found":true,"hint":"..."}`；不存在 → `run_root_task_found:false`。**只读、不 adopt、不建第二真相源**。
3. 指南（批次 C）：`topic.artifact_locating` 第 3 条补「job_id（`YYYYMMDD_HHMMSS_NNN_<name>`）与 operation_id 不是同一个 id；status 只认 job_id」。

**测试**：新增 `tests/test_acp_round8_status_reference.py`：未知 id → `NOT_FOUND`（不可重试、消息含 `acp_list_jobs` 提示）；已知 id → 正常；404 detail 两种形状的前缀一致。既有 `tests/test_acp2_status_projection_verified_facts.py`。**优先级** P1。**风险/回滚**：错误码从 `RESOURCE_NOT_READY` 变 `NOT_FOUND` 是 breaking-ish（客户端若靠 code 重试会停）——需写进 PR；回滚 = 还原映射。**依赖**：批次 B（与二.2/一.2 同文件面）。

---

### 三.3 TS 失败只回 `"ORCA transition-state optimization failed [unknown]"`（P1）

**复现性/根因（VERIFIED）**
- 唯一文案：`src/cccp/qc/interfaces/orca.py:3682`，`failure_class = classify_orca_failure(output_file)`。
- `classify_orca_failure`（`:111-145`）只匹配 4 类固定 pattern：memory（`cannot allocate` 等）、geometry（`THE OPTIMIZATION HAS NOT CONVERGED`/`OPTIMIZATION DID NOT CONVERGE`/`GEOMETRY OPTIMIZATION DID NOT CONVERGE`）、SCF（`SCF NOT CONVERGED` 等）、以及 `"scf iterations" and not "scf converged"`；其余一律 `unknown`。ORCA 的实际 TS 失败常见写法（`The optimization did not converge`、`Hessian is not positive definite`、`error termination`、`aborting the optimization`、`CONVERGENCE FAILURE`）**不在表内** → 三次 rescue 全 `unknown` 完全可解释。
- 就算分类成功，消息里也**没有**超循环数/虚频个数/SCF 轮数——agent 要求的是「说出是哪一类」。

**修复方案**
1. `cccp/qc/interfaces/orca.py`：新增 `_failure_details(output_text) -> dict`（tail 8 万字符内）：`{"reason_lines": [<=5 条, 去路径], "scf_cycles": n|null, "opt_cycles": n|null, "imaginary_count": n|null, "terminal": "error_termination"|"normal"|...}`；`classify_orca_failure` 的 pattern 表补齐（大小写折叠后的「did not converge」「not converged」「error termination」「positive definite」「too many iterations」），并新增 `"hessian_not_positive_definite"`/`"scf_not_converged"`/`"opt_maxiter"` 分类；`unknown` 仍保留（诚实）。
2. `TsOptResult.error_message` 改为 `f"ORCA transition-state optimization failed [{failure_class}]: {summary}"`，`summary` 由 `_failure_details` 生成（≤200 字符，**不含**绝对路径，redaction 交给上层）；同时把 `metadata["failure_details"]` 带上完整结构，供 `step_result.json`/`live_status` 透出。
3. `src/acp/calculations/batch/engine.py` 的 rescue 记录已存 `rescue_failure_type`/`rescue_actions`（`:1177-1191`），补 `rescue_failure_details`（同上 dict），让 manifest/provenance 能回答「三次 rescue 为什么都没救回来」。
4. 指南（批次 C）：`topic.partial_failure_recovery` 加「失败分类 + `failure_details` 在哪读；`unknown` 不是平台故障」。

**测试**：新增 `tests/test_cccp_orca_ts_failure_details.py`（fixture：三类真实 ORCA 尾巴字符串 + 一个真 `unknown`）：断言分类、`imaginary_count`、`error_message` 含分类与摘要、不含 `/home/`、metadata 结构；更新 `tests/test_cccp_orca*.py` 中依赖旧文案的断言（grep `failed \[`）。**优先级** P1。**风险/回滚**：错误文案变化会影响 grep 它的脚本/测试（列在 PR）；分类只增不减。**依赖**：批次 E（`engine.py` 与 三.4 同文件）。

---

### 三.4 结果提取靠正则爬 ORCA 原文：manifest 只给频率、`step_result` 只有一个 energy（P1）

**复现性/根因（VERIFIED：Round-7 已加 thermo 产品，但只覆盖「Shermo 热化学步」）**
- `result_manifest` 的 `ProductKind` 有 `ENERGY_REPORT`/`THERMO_REPORT`（`src/acp/storage/manifest.py:21-38`）。
- `frequency` 工作流：`simple.py:567` 在 freq 步后调 `_publish_frequency_thermochemistry`（`:606-655`），成功则登记 `THERMO_REPORT`（`results/thermochemistry.py:445-490`）——**已修**。
- BatchOptimize：只有 `build_batch_thermochemistry`（`results/thermochemistry.py:231-269`，读 Shermo `*.sum`），且**只在 `opt_freq_sp_thermo` profile 的热化学步**登记（`engine.py:1711-1751`）。若 profile 是 `opt_freq_sp`（外部 agent 的典型用法），freq 步的 ORCA 输出里其实已有 `Electronic energy / ZPE / Total Enthalpy / Final Gibbs free energy`，但平台**不登记任何 thermo 产品** → agent 只能 grep `WORK/04_FREQ/freq.out`。这精确解释了「manifest 只给频率、不给 Gibbs」。
- 另：`step_result.json` 的 energy 字段来自 step 结果投影（`engine.py:489, 937-952, 1252`），不含 Gibbs/ZPE；`structure` 产品的 metadata 只挂了 `frequency`（`frequency_status`），没有 energy。
- 也要注意：**旧作业**（round-7 之前）本来就没有 thermo 产品，不会回溯生成——指南必须说清「没有产品 ≠ 没算」。

**修复方案**
1. （批次 E）`src/acp/calculations/batch/engine.py` 发布段：当 **freq 步成功**且 Shermo 产品不存在时，尝试 `parse_orca_thermochemistry(<freq step log>)` → `build_frequency_thermochemistry(...)` → `publish_thermochemistry(product_id=f"thermochemistry_{item_id}")`；**优先**已有 Shermo 产品（不覆盖），两者都无则**不登记**（不造假值）。同一逻辑抽到 `results/thermochemistry.py::publish_if_available(...)`，`simple.py` 复用它（现在是内联实现），避免两份。
2. （批次 E）`structure` 产品 metadata 增 `energy_hartree`（freq/SP 步的电子能，来源注明 `step`）与 `thermochemistry_product_id`（存在时）；`step_result.json` 的 freq/SP 步增加 `gibbs_hartree`/`enthalpy_hartree`/`zpe_hartree`（**只写实际解析到的**，缺失即省略）。
3. （批次 C）`topic.artifact_locating` 的「结果字段地图」补：Batch 逐 item 的 `RESULT/thermochemistry/<item>.json` 在产品存在时才可读；旧作业/无 Shermo 的 freq 步现在也会产出；`FINAL SINGLE POINT ENERGY` 多处匹配的陷阱（取 step 对应 log + 产品优先）。
4. **不改** `step_result.json` 的既有 `energy` 字段语义（兼容），只加字段。

**测试**：既有 `tests/test_acp_round7_thermochemistry.py`（420 行，覆盖频率产品/无值不造假）；新增 `tests/test_acp_round8_batch_thermo_from_freq.py`：用真实 ORCA freq 尾巴 fixture（含 `Final Gibbs free energy`）跑 `opt_freq_sp` → 断言 `THERMO_REPORT` 产品 + `gibbs_hartree` 与解析值一致 + `structure.metadata.energy_hartree`；Shermo 存在时不覆盖；解析失败时无产品且 `status` 不伪装。**优先级** P1。**风险/回滚**：多出产品条目会让旧客户端看到更多 `artifacts`（加法）；解析用现成函数，不新增科学判断。回滚 = 单点关闭发布调用。**依赖**：批次 E（与 三.3 同文件）。

---

### 三.5 Hessian 算两遍（TS 优化 `Calc_Hess` 66 位移 + freq 步再算）（P2，需科学裁决）

**复现性/根因（VERIFIED）**
- Batch 的 TS 优化步显式 `resources["calculate_frequencies"] = False`（`engine.py:1425-1430`），即 **FREQ 是独立 profile 步**；TS 默认 `opt_initial_hessian="calculate"`、`opt_recalc_hess=5`（`options.py:59-70`）→ 优化开头算一次数值 Hessian（CPSCF 29 min 的来源）。
- 独立 FREQ 步走 `OrcaInterface.frequency()`，内部 `self._write_input(..., "freq", ...)` + `_run_orca`，**没有**任何 Hessian 复用参数（`src/cccp/qc/interfaces/orca.py:3112-3205`），必然再算一遍 `NumFreq`（66 位移）。
- 存在的能力：TS 优化支持 `initial_hessian="read"` + `hess_file` → staging 为 `<name>.hess` 并写 `InHessName`（`orca.py:3535-3616`、`_copy_irc_hessian:1393`）；但 FREQ 侧没有对应的读入路径。ORCA 端能否「读 Hessian 做频率」**NOT_VERIFIED**（不能在计划里假装有）。

**修复方案（默认不动；只加可观测 + 一个显式、严格门控的可选策略）**
1. （批次 E）`live_status`/`step_result` 增 `hessian_recomputed: true` 与 `hessian_source: "opt_initial"|"freq_numfreq"`，让重复成本**可见**（不改变行为）。
2. （需用户裁决后才实现）可选 `method/batch_roles.<role>.freq_hessian_policy = "recompute"（默认）| "reuse_opt_hessian"`，复用条件**全部**满足才允许：①几何 digest 与优化终点完全一致（`geometry_sha256`）；②method/basis/溶剂/网格/`opt_convergence` 与 freq 完全一致；③ORCA 读入 Hessian 的机制已在真实 ORCA 上验证（当前 NOT_VERIFIED）；④只在同一次 attempt 内复用（不许跨作业）。
3. 不满足任一 → 回落 `recompute` 并在 `effective` 里写明 `reuse_blocked_reason`。
4. 本轮**只做 1**；2/3 进「需要用户确认」（科学决定 + 需要 ORCA 实测）。

**测试**：`tests/test_acp_round8_hessian_visibility.py`（只测 1：字段存在、默认 `hessian_recomputed=true`）；不写 2/3 的测试直到裁决。**优先级** P2。**风险/回滚**：1 是纯观测。**依赖**：批次 E。

---

### 三.6 上下文被契约吃掉（每次 7–9 KB 信封，80% 样板）（P2）

**复现性/根因（VERIFIED，含实测字节数）**
- 实测（本机 import，无副作用）：`describe_workflow("BatchOptimize")` = **9208 B**，其中 `method` 5343 B（`levels` 40+ key + properties）、`input` 1036 B、`resources` 860 B、`examples` 487 B、`constraints` 608 B；`PESsearch` = 7769 B（`input` 4350 B，因 coordinates 说明）；`irc` = 4230 B；`singlepoint` = 3854 B。
- workflow-scoped capability 响应又把这些包在 `workflows/backends/tools/extensions/workflow_contract` 里（`src/acp/api/routes.py:655-664`）；full payload 再叠 `research_tools`/`identity`/`execution_limits` 等（`:703-749`）。
- 指南侧本身不臃肿：全部 27 条共 46 KB，单条最大 `task.find_transition_state` 5347 B、`topic.artifact_locating` 3314 B，均在 `MAX_GUIDE_BYTES=8192` 内；`read_guide` 已支持 `section` 与 `max_bytes`（`guidance_pack.py:1099-1160`）——问题是**没人告诉 agent 可以按 section 读**。
- preflight 回显是另一处真浪费：`submission_analysis["requested"] = {"input": dict(raw_input), ...}`（`src/acp/api/v1_routes.py:10104-10107`）把整份 xyz/items 原样回吐。

**修复方案**
1. （批次 B）`v1_routes._preflight_projection`：`requested` 改为有界投影 —— 结构载体字段（`xyz_text`/`items[].xyz`/`source`（XYZ 文本）/`input`）替换为 `{"omitted":true,"sha256":...,"atoms":N,"bytes":M}`；其余标量原样；总大小硬上限（如 2 KB）并在超限时置 `"truncated":true`。**不**影响 `plan_digest`（digest 仍基于完整 payload，`_plan_digest(payload)` 在投影前计算）。
2. （批次 B）`routes.py` workflow-scoped 分支：加 `contract_sections` query（`input,method,method.levels,resources,examples,constraints,gaps`），默认返回**核心**（`workflow/input/method.properties/resources/constraints/gaps`，不含 40-key `levels`）；`workflow_contract` 里加 `"sections_available"` 与 `"next":{"tool":"acp_capabilities","arguments":{...}}`。响应头部加 `contract_digest`（对完整 block 的 sha256），便于缓存与「内容没变」判断。
3. （批次 B）`routes.py` workflow-scoped 分支：`workflows` 只保留 id 列表（现在已是 196 B，保持），`backends` 保留（270 B），`tools`/`extensions` 在 workflow-scoped 响应中省略（handshake 已经给过），并在 `next` 里指出如何取。
4. （批次 C）指南中统一写「大对象按 section 读：`acp_get_guidance(guide_id, section=...)`；contract 用 `contract_sections`」。

**测试**：新增 `tests/test_acp_round8_envelope_budget.py`：Batch workflow-scoped 核心响应 **≤ 5 KB**（当前 9.2 KB）；`contract_sections=method.levels` 单独可取；`preflight` 回显 ≤ 2 KB 且 `plan_digest` 与完整 payload 一致（关键：digest 不因投影变化）；未知 section → 422。既有 `tests/test_acp_api_guidance_routes.py`、`tests/test_acp_mcp_guidance_index.py`。**优先级** P2（但 preflight 回显是 P1 级浪费，可单独提前——本计划把它留在 B 批内一次做完）。**风险/回滚**：默认核心视图会改变现有响应内容（缺 `levels`）——因此必须**保留** `contract_sections="full"` 等价路径并在 PR 标注；回滚 = 默认改回 full。**依赖**：批次 B（`v1_routes.py`）+ C（指南文案）。

---

### 三.7 红色剪刀：local-single 也打码 `work_dir`/`idempotency_key`/`name`（P1）

**复现性/根因（VERIFIED）**
- `machine_output.redact()` 无条件：先 `_redact_secrets`（含 `_OPAQUE_RE` 对长 token → `<redacted>`），再 `_ABSOLUTE_PATH_RE` → `<redacted-path>`（`src/acp_services/entrypoints/machine_output.py:470-478`、`:324`）；`redact_value` 对**每个字符串值**递归执行（`:481-512`），没有身份模式参数。
- `MachineResult` 序列化时统一 redact（模块 docstring `:14-19`），因此 MCP 响应对 local-single 也打码；`_PUBLIC_IDENTIFIER_RE` 只豁免 job_id 形状与命名空间 token（`:371-401`），不豁免 `work_dir` 普通路径、`idempotency_key`（任意字符串）、以及某些长 `name`。
- 服务端**知道**模式：`identity_mode`（`IdentityMode.LOCAL_SINGLE`）已在 capabilities 里发布（`routes.py:745-752`），MCP server 也有 `configured_tier`（`mcp.py:2202/2697`）——只是 redaction 没用它。

**修复方案**
1. `machine_output.py`：新增 `redact_value(value, *, reveal_paths: bool = False, reveal_fields: frozenset[str] = frozenset())`（**默认行为不变**）；`redact()` 不变。字段名在 `reveal_fields` 且是声明过的本地字段时：路径类不替换、`<redacted>` 的 opaque token 还原（**只**在 `reveal_paths=True` 时）。
2. `mcp.py`：在**唯一**响应出口构造 `reveal = (configured_tier == LOCAL_SINGLE)`，`reveal_fields = {"work_dir","directory_name","name","task_name","molecule_name","idempotency_key","spec_ref","source_ref"}`；把 `reveal` 传给 `MachineResult.to_dict(...)`/序列化。**绝不**在 `MachineResult` 里默认开启，也不改 HTTP 路由（它们的 redaction 语义另行评审）。
3. 安全边界写死在代码注释 + 测试：`reveal_paths=True` 只在 `LOCAL_SINGLE`（单用户、同机、无远程租户）成立；`API_KEY`/多租户模式**永远** redacted。
4. 指南（批次 C）：写明「local-single 的 MCP 响应会给出 work_dir/name/idempotency_key 原文；API-key 模式仍打码」。

**测试**：新增 `tests/test_acp_round8_local_redaction.py`：`LOCAL_SINGLE` → 三字段原文；`API_KEY` → 仍 `<redacted>`/`<redacted-path>`；默认 `redact()` 行为逐字节不变（黄金样本）；`reveal_fields` 外的字段仍打码（例如 `api_key` 字段在 local-single 下也不还原）。既有 `tests/test_acp_mcp_ux_p3_artifact.py`（base64/redacted body 策略）、`tests/test_services_machine_output.py`。**优先级** P1。**风险/回滚**：这是**局部降级安全**，必须在 PR 描述里显式写「仅 local-single、仅声明字段、仅 MCP 出口」；回滚 = 不传 `reveal`。**依赖**：批次 B（与 一.2 同出口）。

---

### 四 用户自认的锅（不改平台，最多给指南提示）（P2）

**根因（无需代码核查）**：①exo/endo 凭几何直觉标反，靠 InChIKey 撞 PubChem 同义词表才发现；数值巧合（GFN2 0.76 vs 文献 0.70）强化了错误自信。②DLPNO 能量被怀疑解析错（FINAL SP −193.720 比 r2SCAN-3c 的 −194.045 高 0.32 Ha），实为跨方法比总能绝对值无意义（DFT 总能系统性偏低）。

**修复方案（仅指南）**：`topic.method_energy_comparability` 加两句「跨方法/跨级别只比相对值与能差；绝对总能偏移不构成解析错误证据」「report 时必须同时给方法/基组/零点/标准态」；`topic.stationary_point_verification` 或 `topic.inputs_electronic_state` 加「命名类判断（exo/endo、E/Z、R/S）在计算前用独立标识符（InChIKey/CAS/文献）钉死；几何直觉不是证据」。不新增自动命名工具，不实现 InChIKey 查询。

**测试**：`tests/test_acp_guidance_pack.py` 加子串守护（`InChIKey`、`绝对总能`）；无代码行为变化。**优先级** P2。**依赖**：批次 C。

---

### 五 真心好用的（必须保留，禁止回退）+ 方法选择 USER CORRECTION

**五的保留项（回归护栏，不写新功能）**
- `preflight.submission_analysis.orca_summary`（Batch 每 role 的 ORCA 关键字投影，`submission_validation.py` + `effective_config.build_orca_summary`）：本轮**不许**在 三.6 的「核心视图」里把它删掉——它是信息密度最高的字段，`contract_sections` 只裁剪**契约**，preflight 的 `effective` 不裁。
- `method_fingerprint`（40+ 参数进 manifest）、`ts_validation:"passed"`、`reaction_progress`（0.397/0.402）、`live_status` stage 粒度、幂等键语义：新增测试断言这些字段在 round-8 修改后**仍存在**（`tests/test_acp_round8_regression_kept_fields.py`），只增不改。
- `orca_summary` 建议推广到其它 workflow（agent 的建议）：列为 P2 待办，不在本轮做（避免与 三.6 的裁剪冲突）。

**USER CORRECTION（必须在本轮落地）**：优化 = r2SCAN-3c 默认/最优（绝大多数体系）；**不推荐 wB97M-V 做几何优化**；单点默认 wB97M-V **保留**。
- 现状冲突点（VERIFIED）：
  - `topic.method_selection` 摘要「小体系 wB97M-V」（`guidance_pack.py:708-709`）与正文「小体系：允许上 wB97M-V + def2-TZVP」（`:716`）——**必须删/改**。
  - `workflow.optimize` 正文「优化方法按体系大小选档（小体系允许 wB97M-V…）」（`:387`）——**必须改**。
  - `task.barrier_high_accuracy`「端点优化…小体系允许 wB97M-V」（`:943`）——**必须改**。
  - 需保留：「默认单点：wB97M-V + def2-TZVP(D)」（`:720`、`:946`）、`wB97X-D4` 不并列的表述。
- 目标文案（单一改写，四个位置引用同一句，避免再漂移）：
  「几何优化默认 r2SCAN-3c（复合方法，自带基组，不填 basis）——对绝大多数体系它就是最优选择；不做 wB97M-V 几何优化的推荐档。仅当用户明确要求或已有历史记录需要复现时，才在优化上用 wB97M-V，并记录理由与成本。单点仍默认 wB97M-V + def2-TZVP(D)。」
- 附带：`workflow_contracts.py:376-377`（Confsearch 示例 levels 里 `dft_opt/refinement_sp` 用 wB97M-V）属于**示例**，改成 `dft_opt: r2SCAN-3c`、`refinement_sp: wB97M-V`（保留单点默认），避免「示例教人用 wB97M-V 优化」。
- **不改** `catalog.py` 里的方法预设/模板（`:2200`、`:2626` 等，含 `dft_opt: wB97M-V`）：那是**生产默认与冻结 fingerprint**，改动会变更既有工作流语义与历史可比性 → 列进「需要用户确认」（K3）。
- 版本：`PACK_VERSION` 1.4.0 → 1.5.0；`topic.method_selection` 1.3.0 → 1.4.0；`workflow.optimize` 1.1.0 → 1.2.0；`task.barrier_high_accuracy` 1.2.0 → 1.3.0；`topic.artifact_locating` 1.3.0 → 1.4.0；`workflow.irc` 1.1.0 → 1.2.0；`workflow.BatchOptimize` 1.3.0 → 1.4.0。
- 测试：`tests/test_acp_guidance_pack.py` 新增断言：`wB97M-V` 在 `method_selection` 中只出现在「单点」上下文（字符串层面：不得出现「小体系允许 wB97M-V」）；`r2SCAN-3c` 出现在优化默认句；单点默认句仍在。**优先级** P0（用户指令；纯文本，零运行时风险）。**依赖**：批次 C 独占 `guidance_pack.py`。

---

## 批次拆分（5 批，文件不重叠）

| 批次 | 主题 | 独占文件 | 覆盖条目 |
|---|---|---|---|
| **A** 契约与预检 | 提交边界、只读分析器、契约投影 | `src/acp/api/submission_contract.py`、`src/acp/calculations/submission_validation.py`、`src/acp/api/workflow_contracts.py` | 一.1、一.3、二.1、二.4、二.5(1)、二.2 的词表部分 |
| **B** 状态/产物/信封投影 | status、descriptor、capabilities、MCP 出口、redaction | `src/acp/api/v1_routes.py`、`src/acp/api/artifact_descriptors.py`、`src/acp/api/routes.py`、`src/acp_services/entrypoints/mcp.py`、`src/acp_services/entrypoints/machine_output.py`、`src/acp_services/entrypoints/controller_client.py` | 一.2、二.2、三.2、三.6、三.7 |
| **C** 指南内容包（含 USER CORRECTION） | 全部 guide 文本与版本 | `src/acp/api/guidance_pack.py`（+ `tests/test_acp_guidance_pack.py` 等测试） | 五 的方法纠正、一.2/一.3 的指南、二.1 表、二.3、三.1/三.2/三.4/三.5/三.6/三.7 的文案、四 |
| **D** 调度运行时 | 目录分配/清理、IRC 派生错误、Batch 派发错误 | `src/acp/scheduler/runner.py`、`src/acp/scheduler/manager.py`、`src/acp/scheduler/remote/script_gen.py` | 三.1、一.3(4)、一.1(4) |
| **E** 结果与失败可读性 | thermochemistry 覆盖、失败分类、Hessian 可见性 | `src/acp/calculations/batch/engine.py`、`src/acp/workflows/simple.py`、`src/acp/results/thermochemistry.py`、`src/cccp/qc/interfaces/orca.py` | 三.3、三.4、三.5(1) |

**依赖与顺序**：A 与 C 可并行（A 定 rule 名/字段名，C 只写文本；若 A 改 rule 名，C 的最后一遍文案对账即可）；B 依赖 A 的 `status_group`/错误码命名（但文件不重叠，可先写 A 的常量再落 B）；D 依赖 A 的 rule 名与 一.1 的错误文案；E 独立（只碰 cccp + engine/results），但 `engine.py` 的 `rescue_failure_details` 字段名需与 三.3 的 `failure_details` 对齐 → 同批 E 内自洽。**C 批必须在 A/B/D/E 的 rule 名与字段名定稿后合并**（只改文本，成本最低）。

**测试归属**：每批自带新增测试文件，文件名见逐条；新增测试一律归入 `pytest -m "not slow"` 可跑集合（无真实 QC/ORCA）。**跨批唯一共享测试**：`tests/test_acp_round8_regression_kept_fields.py`（五 的保留项）由 C 批落，其它批只读取断言。

---

## 验收清单

**A. 契约/预检**
1. `pytest tests/test_acp_round8_batch_input_source.py tests/test_acp_round8_irc_source_payload.py tests/test_acp_round8_batch_mirror_missing_role.py tests/test_acp_round8_source_shape_table.py tests/test_acp_round5_preflight_parity.py tests/test_acp_round7_preflight_batch.py -q` → 全 PASS。
2. `pytest tests/test_acp_submission_contract_first_batch.py tests/test_acp_round7_batch_roles_sp.py tests/test_batch_optimize.py -q` → 无回归。
3. 同 payload 的 preflight 与 submit 必须同 `code/field/rule`（新增测试显式断言 `set` 相等），且 preflight 后 `would_create_task=False`、store 行数不变、无新 task 目录（零副作用）。

**B. 状态/产物/信封**
4. `pytest tests/test_acp_round8_artifact_prepare_chain.py tests/test_acp_round8_task_state_vocab.py tests/test_acp_round8_status_reference.py tests/test_acp_round8_envelope_budget.py tests/test_acp_round8_local_redaction.py tests/test_acp_mcp_ux_p3_artifact.py -q`。
5. 实测体积：`python -c "..."` 断言 Batch workflow-scoped 核心 ≤ 5 KB、`preflight` 回显 ≤ 2 KB（测试内断言即可，不需外部脚本）。
6. `acp_read_artifact` 全链：描述符 → prepare → read，**且** describe 路径无 operation/无 prepare（零副作用断言）。

**C. 指南**
7. `pytest tests/test_acp_guidance_pack.py tests/test_acp_api_guidance_routes.py tests/test_acp_mcp_guidance_index.py tests/test_acp2_mcp_guidance_tool.py -q`。
8. `python -c "from acp.api.guidance_pack import audit_pack; print(audit_pack())"` → `VALID`（现有 validator）；每条 `size_bytes <= 8192`；index/pack 版本为 1.5.0。
9. 文本守护：`grep -n "小体系允许 wB97M-V" src/acp/api/guidance_pack.py` → **无输出**；`grep -n "r2SCAN-3c" ...method_selection...` 存在；`grep -n "描述符.*generation" src/acp/api/guidance_pack.py` → 无输出；`grep -n "irc_ts_source_v1" src/acp/api/workflow_contracts.py` → 只剩约束/描述，不再作为示例必填。
10. `python scripts/check_docs_links.py` → PASS（若指南改动引用了新文档）。

**D. 运行时**
11. `pytest tests/test_acp_round8_task_dir_reuse.py tests/test_acp_irc_directions.py tests/test_acp2_science_da_irc_scenario.py -q`。
12. 目录复用不变量：预置「含他人 task.json」目录 → 新作业分配 `__NN`，旧目录内容字节不变；adoption 失败路径 **不** 删除含文件目录（mock 断言）。
13. IRC：partial `ts_source` 在 submit 422；完整 provenance 变更仍由 manager 拒绝（两条错误文案可 grep 区分）。

**E. 结果/失败**
14. `pytest tests/test_cccp_orca_ts_failure_details.py tests/test_acp_round8_batch_thermo_from_freq.py tests/test_acp_round7_thermochemistry.py tests/test_acp_round8_hessian_visibility.py -q`。
15. `pytest tests/test_cccp_route_basis_invariant.py tests/test_cccp_orca_executable_probe.py -q`（cccp 邻面不回归）。
16. 失败文案：`failed [<class>]: <summary>` 且 `summary` 不含 `/home/`；`metadata.failure_details` 结构稳定。

**全局护栏（每批都必须满足）**
- **不放宽**：不新增 `allow_unverified_*` 旁路；不改 `ReleaseIdentity.assert_current`；不改 `resolve_verified_ts_source` 的十项证据；不把「缺 role 的 mirror」自动编译进 role；不在 runtime 猜测 batch 语义。
- **preflight 零副作用**：新分析器只读；不得 import/store 写入；不得触发任何 prepare/QC。
- **不建第二状态库**：run_root 只做只读诊断探测，不 adopt、不写 `task.json`、不新增 marker（ANTI #11 守护 `_SCHEDULER_MARKERS`）。
- **层隔离**：`cccp/**` 不得 import `acp`（`pytest tests/test_cccp_isolation.py tests/test_architecture_invariants.py -q`）；`src/acp_services/**` 不得 import `acp`（`pytest tests/test_acp_services_isolation.py -q`）；`acp → acp_services` 单向不变。
- **无新未跟踪副本**：不新增「state.json 的副本」到别处；不把 `.scratch/` 纳入提交。
- **科学门**：本轮不跑真实 QC、不重启生产服务、不调用外部模型；`release/evidence-coverage.current.json` 的 OPEN 项不因本轮文本/投影改动被关。

---

## 需要用户确认 / 需要外部样本

**需要外部 agent 的原始件（决定 NOT_VERIFIED 项）**
1. **job 008 / job 022**：`task.json`、`job.json`、`state.json` 的原文（以及两个目录的 `ls -la`）。用来确认「目录复用」到底是 adoption 失败清理、purge 后重建、还是 DB 行与目录脱钩——目前三种假设都写在 三.1 里，只有原件能排除。
2. **job 024 的状态调用**：`acp_status` 的 `reference` 原文（job_id 还是 operation_id）、调用时间、以及该作业的 `task.json.task_id`。决定 三.2 是「id 用错」还是「DB 行缺失」。
3. **三次 `[unknown]` 的 ORCA 尾巴**：`WORK/` 下对应的 `*.out`（至少最后 200 行 ×3，可脱敏）。用来验证 三.3 的 pattern 补齐是否覆盖真实写法，以及是否 `Hessian is not positive definite`。
4. **`opt_freq_sp` 作业的 `WORK/04_FREQ/freq.out` + `RESULT/result_manifest.json`**：确认 三.4 的「freq 里已有 Gibbs 但未登记」假设，以及是否有第三处 `FINAL SINGLE POINT ENERGY` 匹配。
5. **提交 payload 原文**（一.1）：确认是否真的没有 `source_type`，以及契约示例是否被读到（如果是旧版本客户端的缓存，另说）。

**需要用户裁决的科学/契约决定**
- **K1（Hessian 复用）**：是否允许 `freq_hessian_policy="reuse_opt_hessian"`？若允许，需要先在真实 ORCA 上验证「读 Hessian 做频率」的输入写法；否则本轮只保留 三.5(1) 的可观测性。
- **K2（Batch `source_type` 严格度）**：确认「有 items 必须有 source_type（422）」而非「运行时自动推断」——本计划选前者（不新增第二解析路径）。
- **K3（方法预设是否同步纠正）**：`catalog.py` 里仍有把 wB97M-V 当优化方法的预设/模板（`:2200`、`:2626` 等）。本轮只改**指南与契约示例**，不动 catalog 默认（会变更指纹与历史可比性）。是否需要下一轮把优化默认统一到 r2SCAN-3c？
- **K4（local-single 可见性）**：确认 三.7 的「仅 local-single、仅声明字段、仅 MCP 出口」降级可接受（HTTP 路由与 API-key 模式保持打码）。
- **K5（counts 语义）**：确认 `counts` 从「过滤后」改为「过滤前（10 键恒定）」是可接受的 breaking-ish 变更。
- **K6（`acp_status` 未知 id 的错误码）**：确认 `RESOURCE_NOT_READY → NOT_FOUND`（不可重试）的语义变更可接受。

**本轮明确不做**
- 不做 `catalog.py` 的方法默认迁移；不做矩阵反应位点识别；不做自动发布授权；不重构三套 source 形状为全局统一解析器；不做 Hessian 复用（等 K1）；不跑真实 QC/不重启服务/不关闭任何发布门。
