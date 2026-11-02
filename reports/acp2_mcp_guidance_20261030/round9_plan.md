# Round-9 修复计划：外部 MCP Agent 全研究链反馈（M1–M15）

- 基线：`main` = `2e70583c`（Round-8 合并 PR #56），工作区仅两个未跟踪项（`.scratch/`、`ACP_REPO_AUDIT_20261102.md`），本轮**均不碰**。
- 本轮为**只读规划**：未改代码/测试、未跑真实 QC、未重启服务、未调用外部模型、未提交。允许的写操作只有本文件。
- 核查方式：源码阅读 + `grep` + 少量直接读函数体；未执行 depends-on-real-data 的复现（见各条 `NOT_VERIFIED`）。
- 术语：P0 = 官方 TS 链路被直接阻断（拿不到科学数据或走不通关键边）；P1 = 验证/闭环缺口，本批修；P2 = 契约自洽性（一次调用即可复现的接口矛盾）；P3 = 遥测与错误信息质量。
- 沿用 Round-8 纪律：**不放宽护栏**（preflight 零副作用、不建第二状态库、`cccp` 不 import `acp`、`acp_services` 不 import `acp`、cccp/acp_services 隔离），并且**不得回退 Round-8 的 USER CORRECTION**（几何优化默认 r2SCAN-3c、不推荐 wB97M-V；单点默认 wB97M-V 保留）。

---

## 总览

15 条反馈里 5 条是**科学数据拿不到**（M1/M2/M4/M5 及其连带的 M3），构成 P0；3 条是 P1 闭环（M4 能力视图、M3 发布路径、M5 逐 item 能量；M5 与 M2 同属「Batch 只能靠再补作业」的成本问题）；5 条 P2 是「服务端自己给的模板/字段/广告调不通」；3 条 P3 是遥测与错误文案。**P0 全部集中在两个面**：工件读出口（M1）与 PES/Batch 产物面（M2/M5），而 M1 是其余一切的前置——PES 曲线、虚频、ZPE/G 都在被整篇拒绝的 JSON 里。

| 优先级 | 条目 | 一句话 | 一句话根因（已核） |
|---|---|---|---|
| P0 | M1 | 工件正文整篇拒读，与平台其它面「打码」不一致 | 两层整篇校验：`data/artifacts.py:922 _assert_body_content_safe`（截断前、二进制直接拒）+ `entrypoints/mcp.py:3642 _verify_artifact_body`（解码后整篇拒）；`max_bytes` 不参与策略 |
| P0 | M2 | PES 无 manifest/逐帧工件，Batch 接不上 | `pes/outputs.py:158-175` 只登记 `pes_profile` + `pes_recommendations`；帧文件其实已落盘（`pes/scan.py:890-920` → `scan_frames/frame_%03d.xyz`）但从不登记 |
| P0 | M4b | `vibration.analyze` 名义可用、实际必失败 | `services_analysis_bootstrap.py` 只认 `format="orca-output"` 并用 `OrcaOutputParser` 解析；normal_modes JSON 解析出 0 频率 → `cccp/calculation/capabilities/task_binding.py:586-601` preflight 不满足 → 409 |
| P0 | M5 | Batch 无逐 item 能量产物 | `calculations/batch/engine.py` 不写 energy 产品；optimize 独有 `executor.py:1978-2014` 写 `energy/<step>.json` |
| P1 | M3 | 纯 MCP 会话无发布路径 | 发布要求服务端预铸 `authorization_id`；local-single 无管理员授权即 typed 拒绝 |
| P1 | M4a | 能力视图 summary/full 自相矛盾 | `mcp.py:3244` 用**新建的空** `AnalysisCapabilityRegistry()` 投影 → summary 恒 `unavailable`；detail=full 走 Controller 回包（真实 registry）→ `executable:true` |
| P2 | M6 | 服务端给的 prepare 模板调不通 | `artifact_descriptors.py` 的 `prepare.arguments` 只有 `{job_id,purpose,artifact_id}`；路由/执行侧实际还需要 `relative_path`（+`media_type`） |
| P2 | M7 | prepare 报错不可诊断 | `data/operations.py:1796` 的 `_refuse("invalid_parameter")` 不带 `params.field/rule`，且把「manifest 未就绪」竞态与参数错合并成同一码 |
| P2 | M8 | `contract_sections` 广告了没接线 | 契约 `next` 指向它，但 `mcp.py:3218-3232` 的 `_port_accepts` 在 Controller 适配器上判定不支持 → `capability_not_wired`；Controller 侧 capability 路由不接受该参数 |
| P2 | M9 | `acp_cancel` 拒绝 `reason` | `_CANCEL_SCHEMA`（`mcp.py:1218-1229`）根本没有 `reason` 属性，服务端 allowed 字段也没有 → 工具面/契约面互相矛盾 |
| P2 | M10 | operation 引用被二次序列化 | `mcp.py:3936 _reference_from` 把上游 `reference/id` 的 **Python dict repr 字符串**原样当成 id |
| P2 | M11 | 能力视图互斥项并存 | `api/routes.py:299` 同时发布 `max_running={status:"legacy_noop"}` 与 `effective_config.registry.local.max_jobs`（`api/capacity.py:168-184`） |
| P2 | M12 | 单点能量产物 `method: ""` | `executor.py:1993-2002`：`method = plan.profile or ""`，再用 `_extract_method_from_resources(resources)`（`executor.py:486-489`）覆盖；singlepoint 两步都拿不到 → 空串（optimize 因 `plan.profile` 非空才有值） |
| P3 | M13 | `live_status` 不 live | 写入侧 `progress._write` 每次 force 都会刷新 `updated_at`（`progress.py:320`），读取侧 `job_status_view.build_live_status:132` 直接取它——**冻结点不在这一对**，需真样本定位（见该条） |
| P3 | M14 | 失败信息被截到 ~200 字符 | 未定位到截断点（`cccp/.../irc.py` 内无 `[:200]`）；候选：job error 投影/`metadata.failure_details` 投影/`OrcaOutputParser` 摘要 |
| P3 | M15 | 描述符 `prepared` 恒 false | `api/artifact_descriptors.py:136` 硬编码 `"prepared": False` |

**本轮范围** = A–F 六个互不重叠的写批（见「批次拆分」），全部是加法或收紧密封，不放宽任何护栏。**需要外部样本/裁决**：M1 掩码 vs 结构化投影的**策略与 digest 语义**、M3 自签授权策略、M6「服务端派生 `relative_path` vs 要求客户端给」、M8「接线 vs 撤广告」、M13/M14/M12 的真实 `state.json`/失败回包/单点作业目录、M4b 的科学充分性（A 线签署）、M2 帧产物上界。

---

## 逐条计划

### M1｜工件正文整篇拒读（P0）

**复现性/根因（VERIFIED，双点）**
1. `src/acp_services/data/artifacts.py:917-935 _assert_body_content_safe`：在**任何截断之前**对**整个** body 跑 `machine_output.redact`，`redact(text) != text` 即 `ArtifactBodyRefusedError(rule="artifact_body_redacted")`；非 UTF-8 直接 `artifact_body_unverifiable`。注释明说「截断切片会藏住跨界路径，所以策略必须跑在整篇」。这解释了「`max_bytes=1200` 同样被拒」。
2. `src/acp_services/entrypoints/mcp.py:3642-3697 _verify_artifact_body`：解码后再次 `redact(decoded) != decoded` → `McpToolError(UNSUPPORTED_CAPABILITY, rule="artifact_body_redacted")`，并且在 `truncated` 时**提前 return**（只跳过 digest 校验，不跳过内容策略）。
3. `machine_output.redact`（`machine_output.py:535-544`）= 绝对路径正则 + secret 正则；`redact_value` 已支持 `reveal_paths/reveal_fields`（local-single 例外，`:546-600`），但工件正文面没有接入任何「掩码后返回」的模式。
4. `digest` 是**真实 body** 的 sha256（`resolve_artifact_read` 第 7 步与 `_verify_artifact_body` 的 digest 比对）；因此「掩码后返回」必须显式声明 scope，不能延续 `digest == 返回体` 的暗示。

**修复方案（最小、不建第二存储、不降低完整性）**
- 在 `artifacts.py` 引入**显式的 body 呈现策略**（一个枚举 + 一个纯函数），并把策略参数从 `resolve_artifact_read` 一路传到 MCP：
  - `body_policy="refuse"`（默认，现状，保持所有既存拒绝语义与测试）；
  - `body_policy="mask"`：先对**已通过 digest 校验的真实 bytes** 做 `redact`，返回 `body`（掩码文本）+ `masked: true` + `masking_policy: "redact-v1"` + `digest`（真实 body 的摘要，**保留**）+ `masked_digest: "sha256:<sha256(masked)>"` + `digest_scope: "artifact"`。**绝不**声称返回体 digest 等于 `digest`；错误信息与文档都这么写。
  - 二进制（非 UTF-8）：`mask` 仍拒绝，但用**可重试语义**之外的新码 `artifact_body_binary_unsupported`（与 `unverifiable` 区分：前者「策略不支持二进制」，后者「无法校验」），并在 `params.allowed_formats` 指向结构化投影（见下）。
- `resolve_artifact_read` 的 `max_bytes` 切片必须在**掩码之后**、且掩码永远在整篇上做（保持现有「策略在整篇」的注释结论），切片只影响返回长度；把这段顺序写进 docstring 并加注释，避免以后有人把策略挪到切片后。
- 新增**结构化投影**（同一 read 表面，`projection="paths_stripped"`）：对 JSON body，返回 `body` 为原 JSON 但每个字符串值经 `redact_value` 处理（键名不变），并附 `projection: "paths_stripped"` + `masked_digest`。这是 M1 观察②里「不可读的全含路径字段」的正解，也给 M2 之前的 PES 曲线提供出口。
- MCP 侧：`_verify_artifact_body` 改为「校验真实 body 的 digest → 按 `body_policy` 渲染」，删除「策略未过则整篇拒」的默认分支；`_READ_SCHEMA` 增补 `body_policy`（enum，默认 `refuse`）与 `projection`（enum，默认 `none`），两者互斥校验（`mask` + `projection` 同时给 → `input_invalid/rule=mutually_exclusive`）。
- **不新增**任何路径/密钥白名单；`redact` 仍是唯一策略实现（ANTI：常量/策略单点）。

**测试**
- 既有必须全绿且语义不变：`tests/test_acp_mcp_ux_p3_artifact.py`（6 处 `artifact_body_redacted` 断言）、`tests/test_acp_artifacts.py`、`tests/test_acp2_runtime_artifact.py`。
- 新增 `tests/test_acp_round9_artifact_masking.py`：
  1. 含绝对路径的 JSON：`refuse` → `artifact_body_redacted`；`mask` → 200，`masked is True`，`digest` 等于真实 body 摘要、`masked_digest` 等于返回文本摘要，且文本不含 `/ACPServer/`；
  2. `max_bytes=1200` + `mask`：整篇策略生效，返回 1200 字节、`truncated true`、`digest_scope="artifact"`；
  3. `projection="paths_stripped"`：合法 JSON，数值字段（频率/能量/ZPE）逐字段相等，无路径字符串；
  4. 二进制 body：`refuse`→`unverifiable`，`mask`→`binary_unsupported` 且 `params` 指向投影；
  5. `mask` 结果确定性：连续两次调用字节相同（守护「可重放」）。
- 守护：`tests/test_acp2_mcp_sdk_contract.py -m contract`；新增断言「掩码模式下 error 文案不含 `digest` 已验证」的反向检查，防止未来误标。

**优先级** P0（M2/M4b 的读数前置）。**风险/回滚**：默认仍是 `refuse`，故旧客户端零变化；新增字段是加法。回滚 = 撤 `body_policy` 分支与两个 schema 属性，`artifacts.py` 的 `_assert_body_content_safe` 原地不动即回到现状。**依赖**：M2 的产物登记、M4b 的输入读取都建议在 M1 之后复验；M1 与 M15 共享「描述符 → prepare → read」链，但文件不重叠（M15 在 `artifact_descriptors.py`）。

---

### M2｜PES 与 BatchOptimize 端点对不上（P0）

**复现性/根因（VERIFIED）**
- `src/acp/calculations/pes/outputs.py:158-175`（`persist_pes_outputs`）只 `add_product` 两项：`pes_profile`（`ProductKind.PES_PROFILE`）、`pes_recommendations`（`REPORT`）；manifest 由 `ResultManifest.read/write` 落在 `RESULT/result_manifest.json`（第 173 行）。
- 帧**确实存在**：`src/acp/calculations/pes/scan.py:890-920` 把每帧写到 `scan_dir/"scan_frames"/f"frame_{index:03d}.xyz"`（`write_xyz`），`_extract_frames` 返回的 `ScanFrame` 里有 target/actual/residual/acceptance。
- 但 `outputs.py` 从不遍历 `frames` 去登记 structure 产品；`pes_profile` 只把 `frames` 内嵌在 JSON 里（`:112`）。因此：
  - `pes_ts_frame_005` 这类身份只活在（M1 修复前不可读的）JSON 里；
  - Batch 的 `input.from_artifact` 需要 `RESULT/result_manifest.json`——manifest 存在，但**没有可寻址帧**，所以即使指过去也无 item 可取；
  - 契约自述 `partial_scope: "mode=bond_length_scan direct branch only; the manifest path is not published"` 与实现（已写 manifest）也不一致，属于同一处文档/实现漂移。
- Batch 侧期望：`batch/loaders.load_items_from_result_manifest`（`src/acp/calculations/batch/loaders.py`）按 manifest 的 `structure` 产品 + `metadata.role/tag/frame_index` 装配 item——本轮需在 C 批里核对其字段（**PARTIALLY_VERIFIED**：函数存在，字段契约待逐行确认）。

**修复方案**
1. `pes/outputs.py::persist_pes_outputs`：在写 manifest 前遍历 `frames`，为**每个**存在 `frame_%03d.xyz` 的帧登记 `kind=STRUCTURE`、`id=f"frame_{index:03d}"`、`path=f"{scan_dir_rel}/scan_frames/frame_{index:03d}.xyz"`、`metadata={"frame_index": index, "role": "scan_frame", "target": <主坐标 target>, "residual": <主坐标 residual>, "accepted": <bool>, "profile_point": <energy>}`。`id` 用帧号（稳定、与 agent 现有 `pes_ts_frame_005` 习惯一致）。
2. 同时把 `pes_profile` 从「唯一真相源」降级为「审计/曲线」产品：曲线数值仍以 profile 为准，**帧身份**以 structure 产品为准（不复制结构数据，只登记已落盘的 xyz —— 不建第二存储、不二次写文件）。
3. `metadata` 里加 `selection_source: "scan"`，与 review 发布的手选帧（`metadata.selection_source="agent"`/`"manual"`）区分；`batch/loaders` 只认显式 `role`，保持既有过滤（本轮不改 loader 语义，只在 C 批核对）。
4. 上界策略（防 manifest 爆炸）：帧数 > `PES_MAX_PUBLISHED_FRAMES`（默认 200）时只登记「局部极值帧 + 每个扫描窗口的边界帧」，并在 `metadata.frames_truncated=true` + `pes_profile.frames_count` 原始值保留；常量单点定义在 `pes/outputs.py` 顶部。
5. 契约文本：`WorkflowContract` 的 PES `partial_scope` 改为「manifest 已发布；逐帧 structure 产品已登记；`bond_length_scan` 直接分支之外仍非 partial」。

**测试**
- 既有：`tests/test_pes_search*.py`、`tests/test_acp2_science_pes_*.py`、`tests/test_pes_review*.py`（按实际文件名在实现批首跑确认）。
- 新增 `tests/test_acp_round9_pes_frame_products.py`：
  1. 用 3 点假 scan 结果跑 `persist_pes_outputs` → manifest 含 2 + 3 个产品，`frame_000/001/002` 路径存在且 `metadata.frame_index` 递增；
  2. `load_items_from_result_manifest` 只取 `kind=structure` 且 `role` 匹配的项（不把 `pes_profile` 当结构）；
  3. 帧数超上界 → 截断标志为真且被登记的帧仍是合法路径；
  4. 帧文件缺失（模拟崩溃）→ 跳过该帧，不发明条目。
- 回滚守护：manifest 产品数增加不得改变 `MCP/status` 的 `artifacts` 摘要字段形状（描述符已按 kind 分组）。

**优先级** P0。**风险/回滚**：manifest 变大（受上界约束）；旧消费者若假设「PES 节只有两项」需核对（`results/` 与前端 PES 视图）。回滚 = 撤 `persist_pes_outputs` 的帧循环与上界常量。**依赖**：M1 落地后才能「读到 profile 曲线」；M5 与 M2 共享「Batch 输入来自上游 manifest」的方向，但文件不同（`batch/engine.py` vs `pes/outputs.py`）。

---

### M3｜纯 MCP 会话没有发布路径（P1）

**复现性/根因（PARTIALLY_VERIFIED）**
- 发布面是 `POST /api/v1/jobs/{id}/pes/review` + `acp_publish_pes_review`；`acp_publish_pes_review` 必须带 `authorization_id`，服务端在 `v1_routes._authorize_agent_selection`（名称按线索）里校验预铸授权；`_selection_autopublish_enabled` + 环境变量 `ACP_SELECTION_AUTOPUBLISH` 已存在（线索点名），但**本轮未逐行确认** local-single 下的默认值与拒绝路径。
- 指南自承「纯 MCP 会话走 inline 直达」——即文档承认这条边不可用，等于 P1 的「名义能力」。后果：review revision / selector 审计链在 MCP 侧缺失，Batch 引用没有上游证据绑定。

**修复方案（最小自签 + 记录，不放宽多租户）**
1. **local-single 自签**：新增显式自签模式，仅当 `permission_scope == "local-single"` **且** `ACP_SELECTION_AUTOPUBLISH=1`（沿用既有变量，不新增第二开关）时，`publish` 允许不带 `authorization_id`；服务端**自己铸**一条记录：
   - `authorization_id`（服务器生成）、`origin: "agent-self"`、`policy: "local-single-autopublish"`、`granted_by: "server:local-single"`、绑定 `job_id/attempt/selection_digest/revision`、`expires_at`（短 TTL）、`tenant/project` 取自服务端身份（不取请求体）；
   - 写入**已有**的 selection/authorization 存储（不建第二库、不建第二 writer），并落一条审计事件（`selector=agent`、`basis`、`evidence_refs`）。
   - 拒绝路径保持：非 local-single、或开关关闭、或 identity 不能证明 local-single → 现有 typed 拒绝，文案补 `reason_code`（`authorization_required` vs `autopublish_disabled`）。
2. **inline 也要能带证据绑定**：`BatchOptimize.input.items[]` 增可选 `source_job_id` + `source_frame_id`（或 `source_product_id`），仅用于 provenance 记录与「上游证据绑定」，**不**改变材料化路径（结构仍由 inline xyz 提供）；`analyze_batch_submission` 仅做形状校验，避免又出现「预检可行、运行必挂」的镜像问题。
3. 指南：删掉「纯 MCP 走 inline 直达」的免责句，改为「local-single 自签路径 + 何时需要管理员授权」，并在 `topic.artifact_locating`/PES 段给一个可复制的最小 publish payload。

**测试**
- 新增 `tests/test_acp_round9_agent_self_publish.py`：
  1. local-single + 开关开 → 无 `authorization_id` 的 publish 成功，返回的 `authorization_id` 含 `origin="agent-self"`，且 selection revision 递增；
  2. 多租户 scope → 仍 typed 拒绝（`authorization_required`），**不**生成任何记录（断言零写入）；
  3. 开关关 → 拒绝，`reason_code="autopublish_disabled"`；
  4. inline items 带 `source_job_id/frame_id` → provenance 里有绑定，且材料化结果与不带时 byte-identical（证明没走第二路径）；
  5. 重复 publish（同 selection digest）→ 幂等/显式 revision_conflict，不产生两条授权。
- 守护：`tests/test_acp2_runtime_approvals.py`（审批不写执行事实）与 `tests/test_acp2_research_*` 不得被触动。

**优先级** P1。**风险/回滚**：这是**授权面**变更，风险最高的一条；回滚 = 关掉环境变量即完全回到现状（代码保留但默认 `0`），因此实现必须让「开关关」等价于今天的拒绝语义。**依赖**：M2（要有可引用的帧身份才有意义）；决策项 D2（用户确认自签策略）。

---

### M4｜频率分析通道（P1，两处）

**M4a｜能力视图 summary/full 自相矛盾（VERIFIED）**
- `src/acp_services/entrypoints/mcp.py:3238-3258`：无论哪种 detail，都先构造 `analysis_rows = list(capability_projection(AnalysisCapabilityRegistry()))`——**新建空 registry**，注释明确「fallback 是显式空表，不是进程全局默认」。于是：
  - `detail=summary` 且 Controller 回包未带 `analysis_capabilities` → `setdefault` 写入 `{count:2, detail:…}`（只有 count，没有状态）；
  - `detail=full` 且 Controller 回包**带**了真实投影（注入的 registry → `executable:true, owner:"A"`）→ `setdefault` 不覆盖 → 看起来正常。
  - 客户端把 summary 的 `unavailable` 与 full 的 `executable` 并排看到，即反馈的「自相矛盾」。
- 另外 `analysis_capabilities.capability_projection` 的 `_registered_entry` 对「注册但无 handler」给 `state="registered"`（`analysis_capabilities.py:186-196`），与 `vibration.compute` 的 `registered:true, executable:false` 一致——**这一半是对的**，不是 bug。

**修复（A 批，仅 mcp 侧）**：删除「用空 registry 兜底」的构造，改为：
- Controller 回包带 `analysis_capabilities` → 原样转发（两种 detail 同一内容，summary 只做**字段裁剪**：保留 `analysis_id/state/registered/executable`，去掉 `metadata` 与长描述）；
- 回包不带 → 返回 `{"status": "unknown", "reason": "controller_projection_missing", "detail_tool": …}`，**绝不**伪造 `unavailable`（这是本轮的核心纠正：未知 ≠ 未实现）。
- 契约文本与 `next` 同步（F 批）。

**M4b｜analyze 只吃 ORCA 原文（VERIFIED）**
- `src/acp/services_analysis_bootstrap.py:36-40`（`_SUPPORTED_INPUT_FORMATS = ("orca-output",)`）、`:137-160`（`primary.body.decode → _analyse_text`）、`:210-240`（`OrcaOutputParser().parse_text` → `bind_capability(...).preflight()`）；对 `normal_modes_v1` JSON 解析出 `frequencies=0, mode_vectors=0` → `cccp/calculation/capabilities/task_binding.py:586-601` 的 `frequency_products_available` 不满足 → 该 handler 抛 409 `invalid_scientific_input`（MCP 侧映射为 `resource_not_ready`，即反馈文案）。
- 也就是说：**平台从不缺频率数据**（`RESULT/frequencies/item_001__normal_modes.json` 存在，`ProductKind.FREQUENCY_MODES` 也已登记），缺的是「从登记产品构造 analysis_input」的适配层。

**修复（C 批）**
1. `services_analysis_bootstrap.py` 新增 `format="frequency-modes-v1"` 分支：把已 prepare 的 `normal_modes_v1` body 解析为 `frequencies` + `mode_vectors`（+ `symbols/geometry` 若存在），构造与 `OrcaOutputParser` 输出**同形**的最小 `calculation` 对象（只填 `build_frequency_report` 实际读取的字段），再走同一个 `bind_capability(...)`；
2. 若 cccp 已有对应的纯解析器/规范化器（`cccp/analysis/frequency_products.py` 或 `cccp/analysis/orca_parser.py` 的 `from_normal_modes` 一类），**优先复用**，禁止在 acp 里复制科学解析（ANTI #17）；
3. `parameters.format` 的 enum 由 handler 自述（`_SUPPORTED_INPUT_FORMATS`）投影，不手写第二份；
4. 失败码分流：缺产品 → `resource_not_ready`（可重试，`retry_after_ms`）；有产品但科学不满足（不是恰一个虚频）→ `invalid_scientific_input`（不可重试，`checks[]` 原样给出）。这正是「TS 停在 candidate」需要的两条不同语义。
5. `vibration.compute` 保持 `registered:true, executable:false`（它是 compute，必须提交任务，不能在 analyze 里跑）。

**测试**
- 新增 `tests/test_acp_round9_vibration_analyze_products.py`：1) 用真实形状的 `normal_modes_v1` fixture（脱敏，来自 M1 修复后可读的样本或现有测试 fixture）→ `executable` handler 返回 report，`report.imaginary_count >= 0`、频率单调；2) 缺频率 → `resource_not_ready` + `retryable`；3) 两个虚频 → `invalid_scientific_input` + checks 指名；4) `vibration.compute` 仍拒绝；5) 处理器不启动任何 subprocess/task（mock 断言）。
- 新增 `tests/test_acp_round9_capability_parity.py`：同一 registry 下 `detail=summary` 与 `detail=full` 的 `analysis_id → (state, registered, executable)` 完全一致；Controller 缺投影时 summary 返回 `status:"unknown"` 而非 `unavailable`。

**优先级** P1（M4b 使「TS 只能是 candidate」永久化，实际接近 P0；但科学裁决需 A 线，故列 P1）。**风险/回滚**：新 format 是加法；旧 `orca-output` 路径不动。回滚 = 删 `_SUPPORTED_INPUT_FORMATS` 的新成员与分支。**依赖**：M1（工件要读得到）；A 批（能力视图）与 D 批（Controller 侧投影）需同 rule；样本/科学签署见 D7。

---

### M5｜BatchOptimize 缺 per-item 能量产物（P0/P1）

**复现性/根因（VERIFIED）**
- optimize 单步路径：`src/acp/calculations/executor.py:1978-2014` 在 `state.result.energy is not None` 时原子写 `energy/{product_id}.json`（schema `acp_energy_product_v1`，含 `energy/energy_hartree/unit/method/source`）并 `manifest.add_product(kind=ENERGY_REPORT)`。
- Batch 路径：`src/acp/calculations/batch/engine.py` 组装 per-item 结果产品（structure/frequency_modes/thermochemistry），**没有** energy 产品（反馈说指南自述「Batch 只存逐 item energy_hartree」，位置在 manifest 里——而 manifest 恰好是 M1 拒读的那类文件，双重不可达）。
- 结论：这不是「数据没算」，是「算完的能量只活在 manifest/内存并回里」。成本：每做一次能垒要再补 2 个 singlepoint 作业。

**修复方案（C 批）**
1. 抽出**唯一**能量产品写出函数（新模块 `src/acp/results/energy_product.py`，或放进 `results/` 现有 writer 模块）：`write_energy_product(result_dir, *, product_id, step_kind, energy_hartree, method, label, source="scientific_result.json", diagnostic) -> (rel_path, Product)`；`executor.py` 改为调用它（行为不变），`batch/engine.py` 在每 item 结算处调用同一函数（`product_id = f"{item_id}_{profile or step_kind}"`）。
2. Batch 的 `method` 必须写入真实方法标识：优先 per-item 解析出的 level/method，其次 job 级 method；**若都拿不到，写 `method: null` + `method_unknown_reason`，绝不写 `""`**（与 M12 同一常量/同一 helper）。
3. manifest 里 batch item 的 energy 产品加 `metadata={"item_id", "profile", "method_source"}`，让 `load_items_from_result_manifest` 与下游（能垒拼装）按 item 关联；不新增文件格式、不改 ProductKind（`ENERGY_REPORT` 已存在）。
4. 指南（F 批）：把「Batch 只存逐 item energy_hartree」改成「Batch 每 item 发 `energy/<item>_<profile>.json`，`kind=energy_report`，字段与 optimize `step_0_optimize.json` 完全一致」。

**测试**
- 新增 `tests/test_acp_round9_batch_energy_products.py`：1) 2 item batch 的 result manifest 有 2 个 `energy_report`，字段集合与 optimize 产物**逐键相等**（防 schema 漂移）；2) `method` 非空且等于提供的 level（缺失时为 `null` + reason，不为空串）；3) 原子写（临时文件 + replace）在异常时不留半文件；4) 不含绝对路径（`redact` 幂等）。
- 回归：`tests/test_batch_optimize*.py`、`tests/test_acp2_science_batch_*.py` 全绿。

**优先级** P0（阻断了「拿能垒」这条链，且直接烧作业预算）。**风险/回滚**：多写 N 个小 JSON（体积可忽略）；旧消费者若按「batch 无 energy 产品」分支需核对。回滚 = 撤 engine 的调用点，`energy_product.py` 可保留（executor 已复用）。**依赖**：与 M12 共享 method 解析 helper（E 批负责该 helper，C 批先以局部函数实现并在 E 批收敛——需在 PR 描述里标注为**临时重复**，或把 helper 放 B/C 的公共位置；推荐：helper 落在 `results/energy_product.py`，E 批改 `executor.py` 复用它，两批文件不重叠）。

---

### M6｜prepare 模板调不通（P2）

**复现性/根因（VERIFIED 一半）**
- 描述符的 `prepare.arguments` 在 `src/acp/api/artifact_descriptors.py`（Round-8 新增的 `prepare` 子对象）里只含 `{job_id, purpose, artifact_id}`；`artifact_id` 由 `resource_id` 映射而来（模块 docstring 已写明）。
- 服务端真实要求：`src/acp_services/entrypoints/application_params.py:953-1030 normalize_prepare` 对 `purpose="artifact"` **只强制** `task_ref|job_id` + `artifact_id`；`relative_path` 是可选（`:991-993`）。但执行侧会用它：`v1_routes.prepare` 在 `purpose=="artifact"` 分支里若 `relative_path` 非空才 `validate_artifact_relative_path`（`v1_routes.py:10763-10775`），随后构造 `artifact_payload["relative_path"]`（`:10840` 附近）——**空路径会让下游（ArtifactStore/executor）以 `invalid_parameter` 拒绝**。
- 因此「模板必错」成立，但根因在**分工**：服务端本可以按 `artifact_id` 从 manifest 反查 `relative_path`（manifest 是唯一权威），却把它留成「客户端必须自己知道」。`media_type` 同理（可从后缀推）。

**修复方案（B 批）**
1. **服务端派生（推荐）**：`v1_routes.prepare`（`purpose=="artifact"`）在 `relative_path` 为空时，按 `artifact_id` 从该 job/attempt 的 `RESULT/result_manifest.json` 查产品路径；查不到 → `input_invalid` + `params={"field":"artifact_id","rule":"not_in_manifest"}`（可诊断）。派生路径必须**同样**过 `validate_artifact_relative_path`（越界拒绝），不新增信任边界。
2. **描述符补全模板**：`prepare.arguments` 增加 `relative_path`（来自 manifest 产品）与 `media_type`（`application/json` / `chemical/x-xyz` 等简单映射），并注明「可省略，服务端会从 manifest 派生」。
3. 契约/文档：`_PREPARE_SCHEMA.description`（`mcp.py:1237-1250`，A 批文件）与 `v1_schemas` 的 `V1PrepareRequest` 保持「required = {purpose} (+artifact_id when purpose=artifact)」，把 `relative_path/media_type` 标 `optional, server-derived`。

**测试**：新增 `tests/test_acp_round9_prepare_template.py`：1) 只用 `{job_id,purpose,artifact_id}` prepare 成功（server 派生路径，digest 正确）；2) 未知 `artifact_id` → `input_invalid/not_in_manifest`；3) 客户端给的 `relative_path` 与 manifest 不一致 → `revision_conflict`（或明确以客户端为准并记证据——**取前者**，避免两个真相源）；4) 模板里的每个字段都被 `normalize_prepare` 接受（schema 闭环，防 M6 复发）。

**优先级** P2。**风险/回滚**：派生路径是「服务端权威」方向的收紧；回滚 = 恢复「必须给 relative_path」。**依赖**：M7（错误参数化）同一批。

---

### M7｜prepare 报错不可诊断（P2）

**复现性/根因（VERIFIED 一半）**
- `src/acp_services/data/operations.py:1796` 附近直接把 `"invalid_parameter"` 当作拒绝码/消息返回，没有 `params.field`、没有 `rule`；`data/artifacts.py:738,750` 也有 `_refuse("invalid_parameter")`（两处）。
- 「manifest 未就绪」这种**可重试竞态**与「参数错」被压成同一个码，客户端无法区分——这是最伤 agent 的部分（它会重试错方向或放弃）。
- `ApplicationParamError`（`application_params.py`）本身是带 `params` 的（`_command_error(400, exc.code, exc.message, params=...)` 在 v1_routes:10721 已透传），所以问题主要在 `operations/artifacts` 的 `_refuse` 与 manifest 竞态分支。

**修复方案（B 批）**
1. `operations.py` 的 `_refuse` 增必填 `field`/`rule`（或 `reason`），并在 JSON 回包里带 `params={"field":…, "rule":…, "hint":…}`；对「manifest/artifact 尚未发布」类竞态使用独立码 `resource_not_ready` + `retryable: true` + `retry_after_ms`（与 MCP 现有语义一致），不叫 `invalid_parameter`。
2. `data/artifacts.py:738,750` 的 `_refuse("invalid_parameter")` 同样参数化（哪一字段、哪一规则）。
3. 统一文案风格：`message` 一句人话 + `params` 机读；**保持** `code` 取值词表不变（不新增 code 除非必要；`resource_not_ready` 已存在）。
4. 加一个「错误码 → (字段, 规则, 可重试)」的投影表（单点定义，供 HTTP/MCP/CLI 复用），避免三处各写一遍。

**测试**：新增 `tests/test_acp_round9_prepare_diagnostics.py`：1) 参数错 → `input_invalid` + `params.field`/`params.rule` 非空；2) manifest 未就绪 → `resource_not_ready` + `retryable`（且**不**是 `invalid_parameter`）；3) 两种错误在 MCP/HTTP 两入口的 code/params 一致（entry parity）；4) 现有 `tests/test_acp2_request_field_equivalence.py` 驱动的 `field_policy_gaps()` 无新增 gap。

**优先级** P2。**风险/回滚**：改变错误码/文案属外部语义，需在 PR 描述列出 before/after；回滚 = 恢复短码（但保留 `params`，不回退诊断性）。**依赖**：M6 同批；M1 的 read 错误码同风格（A 批）。

---

### M8｜`contract_sections` 广告了没接线（P2）

**复现性/根因（VERIFIED）**
- MCP 侧：`mcp.py:3178-3234` 先规范化 `contract_sections`（枚举/互斥都做了），再 `_port_accepts(self._backends.capabilities, "contract_sections", method="capabilities")`；stdio Controller 适配器 `_ControllerCapabilities.capabilities(self, *, workflow, detail)`（`src/acp/services_mcp_bootstrap.py:170-187`）**没有** `contract_sections` 参数 → 判定不支持 → `unsupported_capability / capability_not_wired`。
- Controller 侧：`controller_client.capabilities(..., contract_sections=...)` **已经**能发（`controller_client.py:517,552-556`），缺的是「Controller 的 capability 路由接受并投影 sections」。而契约块 `next` 却写着 `acp_capabilities(workflow=…, contract_sections="method.levels")`——文档比实现超前，正是「广告了没接线」。

**修复方案（两选一，需决策 D4；推荐 1）**
1. **接线**（A 批 + D 批）：适配器加 `contract_sections` 形参并**仅**在收到时透传（保持「默认不改变回包」）；Controller capability 路由增加 `contract_sections` 查询参数，投影到**已存在**的契约 projection（若 `workflow_contracts`/`contract_projection` 已支持分节，直接复用；若无，则只支持 `method.levels` 一节并显式声明其余 unsupported）。**不新增**任何契约内容来源。
2. **撤广告**（纯文档）：从契约 `next` 与指南删掉 `contract_sections`，保留 typed 拒绝；成本最低、零风险，但 agent 少一条省 token 的路。

**测试**：`tests/test_acp2_mcp_guidance_tool.py` 增：1) 接线后 `contract_sections="method.levels"` 返回该节且 `data["contract_sections"]` 回显；2) 未接线时错误**必须**带 `params.rule="capability_not_wired"` 且文案指向「不要用该参数」（现在文案已对，补 `retryable:false` 与 `hint`）；3) 若选 2，则契约文本不含该参数（字符串守护）。

**优先级** P2。**风险/回滚**：接线是加法；撤广告会改契约版本/digest（缓存失效是预期）。**依赖**：A 批（`mcp.py`）+ D 批（`v1_routes.py`）；决策 D4。

---

### M9｜`acp_cancel` 不接受 `reason`（P2）

**复现性/根因（VERIFIED 一半）**
- `mcp.py:1218-1229 _CANCEL_SCHEMA` 的 `properties` 只有 `reference`、`expected_revision`，`additionalProperties: False` —— 所以「MCP 工具 schema 有 reason」**不是**来自 tools/list，而可能来自 harness 提示/指南（本轮未定位到该文案，`grep acp_cancel` 只命中 3 处，均无 `reason`）。**NOT_VERIFIED**：需要用户提供触发调用时的 `tools/list` 回包或提示原文。
- 服务端 `unknown_field 'reason'` 说明**确有**一条路径把 `reason` 当合法字段发出去（可能来自 agent 的手写 payload 被 controller 允许列表拒绝）。Controller 的 cancel 允许字段里没有它。

**修复方案（A 批 schema + D 批 route，二选一；推荐「收下但不参与控制」）**
1. `_CANCEL_SCHEMA` 增 `"reason": {"type":"string","maxLength":200}`，并在 `_tool_cancel` 里作为**审计注释**转发（`note`），**绝不**进入控制路径（不改变 cancel 语义、不改变幂等键）；Controller cancel 路由接受 `reason` 并存进事件 metadata（`cancelled_by_intent` 之外的新字段），拒绝时不得据此改变行为。
2. 或：保持 schema 不变，把任何提示文案里的 `reason` 删掉，并在 `unknown_field` 错误里加 `params.allowed`（现在可能没有）——这更「不增面」，但 agent 失去表达意图的位置。
- 采用 1 时，必须加「`reason` 不参与控制」的守护测试。

**测试**：新增 `tests/test_acp_round9_cancel_reason.py`：1) 带 `reason` 的 cancel 成功，回包 `ack` 形状与不带时**逐键相等**（除新增 `note`）；2) 超长 `reason` → `input_invalid/max_length`；3) 不带 `reason` 仍成功；4) 未知字段仍报 `unknown_field` 且 `params.allowed` 列出全部合法字段。

**优先级** P2。**风险/回滚**：加法；回滚 = 去掉 property。**依赖**：决策 D5；与 D 批（route）同一 rule。

---

### M10｜operation 引用被二次序列化（P2）

**复现性/根因（VERIFIED）**
- `src/acp_services/entrypoints/mcp.py:3936-3960 _reference_from(record, kind)`：若 `record` 是 Mapping，按 `_ID_KEYS = ("reference","job_id","jobId","id","operation_id","operationId","task_id")` 取第一个非空；若该值是 Mapping → merge 并 `setdefault("kind")`；**否则 `str(value)`**。
- 当上游把 `{"kind":"operation","id":"op_…"}` 以**字符串**形式放进 `reference`（Python `dict.__repr__`，可见 `'` 引号），`_reference_from` 产出 `{"kind":"operation","id":"{'kind': 'operation', 'id': 'op_…'}"}`——正是反馈的回包。原样回传 `acp_cancel`/`acp_status` 必失败（id 不是合法 operation id）。
- 谁先把 dict 变成 str：适配器/客户端回包投影（`services_mcp_bootstrap` 的 prepare/status 适配器）或 `_cancel_acknowledgement`（`mcp.py:408`）构造 reference 时的拼接。**PARTIALLY_VERIFIED**：需在 A 批用一条最小 in-process 探针（构造 `{"reference": "{'kind': 'operation', 'id': 'op_1'}"}` 调 `_reference_from`）确认字符串来源，再决定修在产出点还是消费点。

**修复方案（A 批）**
1. **消费点兜底**（必须）：`_reference_from` 增加「字符串看起来是 dict repr 或 JSON」的解析：`ast.literal_eval` 仅对 `{`/`[` 开头的字符串、限长 512、异常回退为原字符串；解析出 Mapping 则 merge。**不用** `eval`，不用 `json.loads` 兜底 repr（引号不同）。
2. **产出点收敛**（优先）：在适配器侧的 reference 构造处，把 `record.operation_ref` 原样传 Mapping，不 `str()`；`_cancel_acknowledgement` 用同一 helper。加一个「reference 必须是 `{kind,id}` 且 id 匹配 `^op_[A-Za-z0-9_-]+$|^[0-9]{8}_[0-9]{6}_[0-9]{3}_[A-Za-z]+$`」的校验（白名单式，失败 → `internal`/`unexpected_reference_shape`，不静默）。
3. 同时核对 `v1_routes._operation_response`（已正确：`operation_ref = {"kind":"operation","id": record.operation_id}`，`v1_routes.py:10502-10520`），确认 HTTP 侧不是源头。

**测试**：新增 `tests/test_acp_round9_reference_wire.py`：1) `_reference_from` 对 dict repr / JSON 字符串 / 正常 Mapping / 纯字符串四种输入都产出 `{"kind","id"}` 且 `id` 无 `{`；2) prepare→cancel 回传闭环（mock 端口）；3) `reason`-类无关字段不污染；4) 回包 `reference` 的 `id` 通过白名单校验。

**优先级** P2（但会直接造成「operation 引用无法二次使用」，若 M1 修好后 agent 频繁 prepare，就会踩得很痛——可考虑提前到 P1）。**风险/回滚**：解析是防御性加分；回滚 = 去掉兜底分支。**依赖**：与 M6/M7 同属「prepare 面」，但文件不同（A 批 `mcp.py`，B 批 `v1_routes/operations`）。

---

### M11｜能力视图互斥项并存（P2）

**复现性/根因（VERIFIED）**
- `src/acp/api/routes.py:299-306`：`published["max_running"] = {"status": "legacy_noop", ...}`（文案说明「legacy max_running / ACP_MAX_RUNNING 不再限制本地并发」）；
- `src/acp/api/capacity.py:168-184`：`concurrency["max_running"] = effective_slots` + `max_running_source = "registry.local.max_jobs"`（必要时回退 `manager.max_running`）。
- 两处都发布「max_running」，一个说 noop、一个给数值 → 客户端无法判断哪个是权威；同时 `effective_config` 又给 registry 值。这是「常量/权威单点」被破坏的典型。
- 次要：`routes.py:912-919` 还会投影 `max_running_source`，三处并存。

**修复方案（D 批）**
1. 只保留**一个**权威块：`published["concurrency"] = {effective_slots, slots_source, queue_capacity?, …}`；把 legacy 事实降级为**子键**：`published["concurrency"]["legacy"] = {"max_running_env_ignored": bool, "replaced_by": "registry.local.max_jobs", "note": …}`，删除顶层 `published["max_running"]`。
2. `routes.py:912-919` 的 `max_running_source` 投影同步到 `concurrency.slots_source`（不保留第二处）。
3. 契约投影（`capability_forms`/`science_projection` 若有引用）同步；前端若读 `max_running` 需同批改为读 `concurrency`（**本轮不改前端**：先双写一个版本周期？——不行，双写就是两权威。改为：保留 `max_running` 作为 `concurrency` 的**只读镜像**，内容是数值 + `deprecated:true`，并在下一轮删）。
   - 落地选择：`concurrency` 为权威；`max_running` 保留为**镜像字段**（数值 + `deprecated` + `replaced_by`），并加测试禁止「noop 状态」。

**测试**：`tests/test_acp_round9_capability_limits.py`：1) 顶层不再有 `{"status":"legacy_noop"}`；2) `concurrency.effective_slots` 与 `registry.local.max_jobs` 相等；3) `max_running`（若保留）数值等于 `effective_slots` 且 `deprecated:true`；4) `detail=summary` 与 `full` 的 limits 投影一致（与 M4a 同守护）。

**优先级** P2。**风险/回滚**：改变公开回包形状（前端/脚本可能读旧键）→ 故保留镜像一个周期；回滚 = 恢复旧键。**依赖**：M4a 的 summary/full 一致性同一原则，但文件不重叠（mcp 批 vs api 批）。

---

### M12｜能量产物缺方法标识（P2）

**复现性/根因（VERIFIED）**
- `src/acp/calculations/executor.py:1993-2002`：`method = plan.profile or ""`；若 `state.index < len(plan.steps)` 则 `method = _extract_method_from_resources(_step_resources(_normalise_step(plan.steps[state.index])), method)`。
- `_extract_method_from_resources`（`:486-489`）只读 `resources["method"]`；singlepoint 的 method 落在 job spec / request（`workflows/simple.py:474 spec["method"] = request.method`、`:448-451` 的默认值解析），不保证进入 step resources → 两次都空 → 写 `"method": ""`。
- optimize 之所以有值：`plan.profile` 非空（r2SCAN-3c）。这与 Round-8 的 USER CORRECTION 一致（优化默认 r2SCAN-3c；单点默认 wB97M-V），**修 M12 时不得改变默认方法的选择**。

**修复方案（E 批）**
1. 统一方法解析 helper（与 M5 共用一个，落在 `src/acp/results/energy_product.py` 或 `acp/calculations/method_identity.py`）：`resolve_energy_method(step_resources, plan, job_spec, item_level) -> (method: str | None, source: str)`，顺序：per-item level → step resources → plan.profile → job spec/method → None。
2. 写出时 `method` 非空则字符串；为空则 `null` + `"method_unknown_reason": "no_level_in_plan_or_spec"`，**禁止** `""`（空串是「有值但为空」的伪事实，也是本条的 bug 本体）。
3. 可选：写 `method_source`（`"step_resources"`/`"plan_profile"`/`"job_spec"`），让 provenance 可审计（呼应 `provenance_requirements.method_identity: required`）。
4. 加守护测试：`acp_energy_product_v1` 的 `method` 必须满足 `method is None or (isinstance(method,str) and method.strip())`（schema 级校验函数单点）。

**测试**：`tests/test_acp_round9_energy_method_identity.py`：1) singlepoint 产物 `method == "wB97M-V"`（默认单点），`method_source == "job_spec"`；2) optimize 产物仍为 `r2SCAN-3c`（**Round-8 纠正守护**）；3) 缺 level → `null` + reason，绝不为 `""`；4) 两工作流产物 schema 键集合相等（防再次漂移）。

**优先级** P2（但直连科学可审计性，可提前到 P1）。**风险/回滚**：`method` 从 `""` 变成真实值属修正；回滚 = 恢复 `plan.profile or ""`（不建议）。**依赖**：M5（共用 helper），E 批文件不重叠（`executor.py` vs `batch/engine.py`）。

---

### M13｜`live_status` 不 live（P3）

**复现性/根因（PARTIALLY_VERIFIED）**
- 写入侧：`src/acp/calculations/progress.py:197-219 set_live_metrics` 每次 `_write(force=True)`；`_write`（`:286-330`）每次都刷新 `"updated_at": _iso_now()`（`:320`）。所以**只要 metrics 被更新，`updated_at` 就会变**。
- 读取侧：`src/acp/api/job_status_view.py:112-145 build_live_status` 取 `_first_text(state_data, ("updated_at","last_observed_at","observed_at"))` —— 读的就是那个 `updated_at`。
- 因此冻结不可能来自这一对本身。候选（**NOT_VERIFIED**，需要真样本）：
  1. **metrics 更新不够频繁**：optimize 的 live metrics 只在 `primitives/optimize.py:109 update_live_metrics` 的触发点（可能是每个 ORCA 步解析后、或仅起点/终点）；batch 只在 `batch/engine.py:1116 set_live_metrics`（可能是**item 边界**）→ 用户看到 Step 6 vs 实际 Step 10 的落差；
  2. **读取用了缓存/远端快照**：`v1_routes._enrich_from_state_json`（`:836-851`）走 `find_workflow_state`，`snapshot_version` 是 `state.json` mtime epoch；远端作业可能读 `pending_fetch` 缓存；
  3. **`job …_020/…_022` 是 batch 子项**：子项 reporter 与父 job 的 state.json 不是同一份，父 job 的 `current_stage` 冻结在 item 起始。
- 反馈的「observed_at 长期冻结」+「step 落后 4–5 步」更像 (1) 或 (3)。

**修复方案（E 批，先诊断后修）**
1. 诊断（不改行为）：`build_live_status` 增 `observed_at_age_s`（服务端 `utcnow - updated_at`）与 `source: "state_json"|"remote_cache"`（`source` 已存在，补枚举值），让「冻结」可自证。
2. 修 (1)：把「Step N」类 metric 的写入从**item/阶段边界**提到**每次 ORCA 步解析后**（`primitives/optimize.py` 的 parse 回调），并**限流**（例如 ≥2s 或 ≥1 步变化才写，避免 IO 抖动）——限流常量单点定义，且限流不得让 `observed_at` 落后于最后一次真实观测（写 `observed_at` 用观测时间，而非写入时间）。
3. 修 (3)：batch 子项的 live metrics 必须带 `batch_item` 上下文（`progress.update_live_metrics` 已保留 context，`:219-224`），并在父 job 的 `current_stage` 之外给 `batch_item_index/total`（`_write` 已在 `:323-325` 写）；补一个「父 job step 跟随当前 item」的投影。
4. 若根因是远端缓存：`_enrich_from_state_json` 的 remote 分支显式 `observed_at` + `cache_age_s` + `stale: true`，**不**假装 live。

**测试**：新增 `tests/test_acp_round9_live_status_freshness.py`：1) 写入两条 metrics（间隔模拟）→ `observed_at` 单调递增；2) 陈旧 state（`updated_at` 早于 10s）→ `observed_at_age_s > 0` 且 `stale` 标志正确；3) batch 子项 metrics 更新 → 父投影 `batch_item_index` 变化；4) 限流下 `observed_at` 仍取**观测时间**（用 monkeypatch clock）。

**优先级** P3（不阻断链路，但直接损害 agent 的等待策略；升 P2 的理由：它会诱导 agent 误判「卡住」并取消作业）。**风险/回滚**：只加字段；限流若写成「丢观测」会引入新 bug——故必须 `observed_at` = 观测时刻。**依赖**：需要 `20261101_173534_008` / `20261101_183311_022`（或反馈里的 `_020`/`_022`）的 `state.json` + 同一时刻 status 回包（D6）。

---

### M14｜失败信息不足以定位（P3）

**复现性/根因（NOT_VERIFIED）**
- 反馈的截断串 `… Inspect the run log for the endpoint file fo…`（~200 字符，尾部省略号）在 `src/cccp/calculation/tasks/irc.py:300-317` 是**完整句子**的一部分，该文件内**没有** `[:200]` 或等价截断（已 grep）。
- 候选截断点（需样本定位，按可能性排序）：
  1. job error 投影（`scheduler/manager.py` / `runner.py` 把 `TaskResult.message` 落成 job `error` 时截断）；
  2. `metadata.failure_details` 投影（`cccp/qc/interfaces/orca.py:369-416 _failure_details` 只取 tail + `reason_lines` 上限）；
  3. `machine_output` 的 message 摘要（未见 `[:200]`，但可能有 `textwrap`/`ellipsis` 形式的 helper）。
- 另：反馈指出「log 工件本身也可能落入 M1」——即 `WORK/**/*.out` 若要当工件发布，同样会被整篇拒绝（M1 修好后可读）。

**修复方案（F 批）**
1. 截断必须**显式**：任何对 `message`/`error` 的截断都要带 `truncated: true` + `full_message_ref`（工件 id）或 `full_length`，禁止无标记截断。
2. 保持 `failure_details` 为**机器可读**主渠道（`reason_lines` 全量行 + `scf_cycles/opt_cycles/imaginary_count` + `endpoint_file` 提示），并把「去哪看」写成稳定指针（`log_artifact: "RESULT/…/…out"` + `log_tail_lines` 前 40 行），而不是让 agent 去 grep。
3. IRC 文案补 `failure_class`（枚举：`endpoint_not_found` / `no_hessian_handoff` / `scf_failed` / …），与 Round-8 的 `[unknown]` 分类修复同一词表（避免又出现只回 `[unknown]`）。
4. `WORK/**` 日志若要发布为工件：只发布**尾巴**（`log_tail.txt`，有界），并登记 `kind=file`（不新增 kind）。

**测试**：新增 `tests/test_acp_round9_failure_diagnostics.py`：1) 长文案 → `truncated`/`full_length` 成对出现；2) `failure_details.reason_lines` 行数与原 tail 一致（不被二次截断）；3) IRC 四类失败各自映射到 `failure_class`，无 `unknown` 兜底；4) 日志工件位于 `RESULT/` 且过 M1 的 `redact` 幂等检查。

**优先级** P3。**风险/回滚**：文案/字段变化；回滚 = 撤新字段。**依赖**：D6（真实失败回包 + `WORK/**` 尾巴）；M1（日志可读）。

---

### M15｜状态描述符的 `prepared` 从不翻转（P3）

**复现性/根因（VERIFIED）**
- `src/acp/api/artifact_descriptors.py:136` 直接写 `"prepared": False`（模块 docstring 说明「描述符不是 ArtifactRef，`prepared` 保持 False」——这是 Round-8 的**设计选择**，不是疏漏；但反馈指出它让客户端无法判断缓存状态）。
- `prepare` 成功后状态只活在 operation 记录（`PrepareOperationService.find(...)`）与 ArtifactStore 里；status 描述符从不查它。

**修复方案（B 批）**
1. `describe_job_artifacts` 增加**只读**查询：`service.find(job_id, resource_id, attempt, permission_scope)`（不创建、不 expire、不 prepare），投影：
   - 命中 `SUCCEEDED` 且 body 可用 → `prepared: true`、`prepared_generation`、`prepared_state`、`prepared_at`；
   - 命中未成功 → `prepared: false` + `prepared_state`（`pending/failed/…`）；
   - 无记录 → `prepared: false` + `prepared_known: false`（与「查过、没有」区分）；
   - service 不可用 → `prepared: null` + `prepared_unknown_reason`（绝不默认 false 冒充事实）。
2. **零副作用**：该查询禁止调用 `expire_succeeded_for_resource`（那是 prepare 路由的行为），并在测试里断言 operation 表/ArtifactStore 前后无变化。
3. `prepare.arguments` 与 `prepared` 的一致性：`prepared:true` 时描述符同时给 `generation/digest`（从 operation 读，**不发明**）；`prepared:false` 时仍只给 prepare 模板。

**测试**：新增 `tests/test_acp_round9_artifact_prepared_flag.py`：1) 未 prepare → `prepared:false, prepared_known:false`；2) prepare 后 → `prepared:true` 且 `generation/digest` 与 prepare 回包一致；3) 失败 prepare → `prepared:false` + `prepared_state`；4) describe 前后 operation 行数不变（零副作用）；5) service 缺失 → `null` + reason。

**优先级** P3（但直接决定 agent 是否重复 prepare，成本可观）。**风险/回滚**：加字段 + 只读查询；回滚 = 恒 false。**依赖**：M1（同一 read 链）、M6（描述符 prepare 模板）。

---

## 批次拆分

6 批，两波。**同一波内文件不重叠**；跨波复用文件（`v1_routes.py`）时必须在上一波合并后再开。

### 波 1（可并行）

| 批 | 条目 | 拥有文件（唯一写者） | 说明 |
|---|---|---|---|
| **A（P0）** | M1、M10、M4a-MCP 侧、M8-MCP 侧、M9-schema | `src/acp_services/entrypoints/mcp.py`、`src/acp_services/data/artifacts.py`、`src/acp_services/entrypoints/machine_output.py`、`src/acp/services_mcp_bootstrap.py` | MCP wire 面单点；`body_policy`/`masked_digest`/reference 解析/能力投影/取消 schema 都在这里。**P0 最关键**：M1 解锁其余一切读数。 |
| **B（P0/P2）** | M6、M7、M15 | `src/acp/api/artifact_descriptors.py`、`src/acp_services/entrypoints/application_params.py`、`src/acp_services/data/operations.py`、`src/acp/api/v1_schemas.py` | prepare 契约与描述符面；不碰 `v1_routes.py`（派生逻辑以 helper 形式放 `artifact_descriptors.py`/`operations.py`，由 D 批在 route 里调用）。 |
| **C（P0/P1）** | M2、M4b、M5 | `src/acp/calculations/pes/outputs.py`、`src/acp/workflows/pes_search.py`、`src/acp/calculations/batch/engine.py`、`src/acp/services_analysis_bootstrap.py`、新增 `src/acp/results/energy_product.py` | 科学产物面；**不碰 cccp**（M4b 若确需 cccp 解析器，先在 C 批内以 acp 适配层实现，把「复用而非复制」的评审留到 PR 描述）。 |

### 波 2（波 1 合并后）

| 批 | 条目 | 拥有文件 | 说明 |
|---|---|---|---|
| **D（P1/P2）** | M3、M8-Controller、M9-route、M11 | `src/acp/api/v1_routes.py`、`src/acp/api/routes.py`、`src/acp/api/capacity.py` | API/授权/limits 面；`v1_routes.py` 全波唯一写者。 |
| **E（P2/P3）** | M12、M13 | `src/acp/calculations/executor.py`、`src/acp/workflows/simple.py`、`src/acp/calculations/progress.py`、`src/acp/api/job_status_view.py` | 遥测与能量方法标识；复用 C 批的 `energy_product.py`（只 import，不改它）。 |
| **F（P3/文档）** | M14、指南与契约同步 | `src/cccp/qc/interfaces/orca.py`、`src/cccp/calculation/tasks/irc.py`、`src/acp/api/guidance_pack.py`、`docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md`、`reports/acp2_mcp_guidance_20261030/*` | 只读 CSV/文档题；**cccp 文件仅 F 批可改**（保持 cccp 侧单写者）。 |

**顺序建议**：A →（C 与 B 并行）→ D → E → F。理由：M1 是所有读数的前置；M2/M5 决定 agent 能否拿到 TS 证据；D 的授权面风险最高，放在 M1/M6 稳定之后；F 的文档必须在 A–E 的实现语义定稿后再写，避免又出现「文档比实现超前」（M8 的教训）。

**显式风险提示**：A 与 B 都涉及 prepare 链，若并行开发，先约定「`artifact_id` → `relative_path` 派生」的唯一实现位置（B），A 只消费；否则会出现两份派生逻辑（两个真相源）。

---

## 验收清单

### 必备命令与预期

```bash
cd /ACPServer/AutoCalcPlatform
# 1) 本轮新增/守护测试（实现批执行）
/opt/acp/venv/bin/python -m pytest -q \
  tests/test_acp_round9_artifact_masking.py \
  tests/test_acp_round9_prepare_template.py \
  tests/test_acp_round9_prepare_diagnostics.py \
  tests/test_acp_round9_reference_wire.py \
  tests/test_acp_round9_pes_frame_products.py \
  tests/test_acp_round9_vibration_analyze_products.py \
  tests/test_acp_round9_capability_parity.py \
  tests/test_acp_round9_batch_energy_products.py \
  tests/test_acp_round9_agent_self_publish.py \
  tests/test_acp_round9_capability_limits.py \
  tests/test_acp_round9_energy_method_identity.py \
  tests/test_acp_round9_live_status_freshness.py \
  tests/test_acp_round9_failure_diagnostics.py \
  tests/test_acp_round9_artifact_prepared_flag.py
# 预期：全 PASS；任何 skip 必须在 PR 描述逐条说明原因（三态证据规则）

# 2) 既有面回归（不得改变旧语义）
/opt/acp/venv/bin/python -m pytest -q \
  tests/test_acp_mcp_ux_p3_artifact.py tests/test_acp_artifacts.py \
  tests/test_acp2_runtime_artifact.py tests/test_acp2_mcp_guidance_tool.py \
  tests/test_acp_guidance_pack.py tests/test_acp_mcp_guidance_index.py \
  tests/test_acp2_request_field_equivalence.py
# 预期：全 PASS（M1 的 6 处 artifact_body_redacted 断言在默认 refuse 下必须原样通过）

# 3) 架构/隔离硬门（护栏不放宽）
/opt/acp/venv/bin/python -m pytest -q \
  tests/test_architecture_invariants.py tests/test_acp_services_isolation.py \
  tests/test_cccp_isolation.py tests/test_services_injection_contracts.py
# 预期：PASS；src/cccp/** 与 src/acp_services/** 的 import 图零新增反向边

# 4) 契约（MCP SDK 真实门）
/opt/acp/venv/bin/python -m pytest -q tests/test_acp2_mcp_sdk_contract.py -m contract
# 预期：PASS 且 JUnit 计数非空（空选择 = FAIL，沿用 round-8 的防静默门）

# 5) 静态检查
/opt/acp/venv/bin/ruff check src tests && /opt/acp/venv/bin/ruff format --check src tests
# 预期：clean
```

### 护栏检查（逐项可断言）

| 护栏 | 断言方式 | 预期 |
|---|---|---|
| **digest 完整性** | `test_acp_round9_artifact_masking`：`mask` 回包的 `digest` == 真实 body sha256；`masked_digest` == 返回文本 sha256；两者不等时 `masked:true` 必为真 | 不存在「返回体 digest 冒充真实 digest」的路径 |
| **preflight 零副作用** | 现有 round-7 fixture + 新增 `test_acp_round9_prepare_diagnostics`（错误路径）断言：调用前后 `operations` 行数、`RESULT/` mtime、`task_dir` 列表一致 | 无新增写入 |
| **不建第二 store** | `git diff --stat` 中不得出现新 `*.sqlite`/`*_store.py`/新 JSON 状态文件；`grep -rn "sqlite3.connect" src/acp_services/data/` 不新增 | 复用 Operations/ArtifactStore |
| **cccp/acp_services 隔离** | 上述第 3 组测试 + `scripts/verify_cccp_isolation.py` | PASS |
| **Round-8 方法纠正不回退** | `test_acp_round9_energy_method_identity`：optimize → `r2SCAN-3c`；单点默认 → `wB97M-V`；`guidance_pack` 文本不含「wB97M-V 用于几何优化」的推荐 | 两条都成立 |
| **掩码不泄漏** | `test_acp_round9_artifact_masking`：`mask` 结果不含 `"/ACPServer"`、不含 `"sk-"`/`"Bearer "` 形态 | 零命中 |
| **授权不放宽** | `test_acp_round9_agent_self_publish`：非 local-single 或开关关 → 拒绝且**零写入** | 严格 |
| **文档不超前** | F 批后 `grep -rn "contract_sections" docs/` 仅出现在已接线（或已明确标注 unsupported）的位置 | 无「广告了没接线」 |

### 证据产物

- 每批 PR 描述附：`git diff --stat`、新增测试名与首个 PASS 输出、`pytest -q` 汇总行、被改行为的 before/after 回包片段（脱敏）。
- 统一落 `reports/acp2_mcp_guidance_20261030/round9_evidence/`（`round7_evidence/` 的同构目录），**不**改 `reports/acp2_implementation/` 历史证据。

---

## 需要用户确认/样本

| # | 决策/样本 | 为什么必须由用户定 |
|---|---|---|
| **D1** | **M1 的呈现策略**：`mask`（打码后返回，保留真实 digest + `masked_digest`）vs `projection`（只给数值、剥路径）vs 两者都做（默认 `refuse` 不变）。digest 语义如何对外表述（「digest 是对存储体，不是对返回体」）。 | 这是**产品/安全语义**决定：它决定平台对「工件正文可含路径」的公开承诺，也决定 agent 能否直接拿到 PES 曲线。若选 projection-only，PES 曲线的结构（frame 数组）仍需专用投影，成本更高。 |
| **D2** | **M3 自签授权**：local-single 下「agent 自签 + 记录」是否可接受；是否复用 `ACP_SELECTION_AUTOPUBLISH`；审计字段（`origin/policy/granted_by/expires_at`）与 TTL；inline 是否允许携带 `source_job_id/frame_id`。 | 授权面变更，涉及「谁为一次 TS 候选发布负责」。多租户不得被削弱，必须显式裁决。 |
| **D3** | **M6 分工**：`relative_path` 由服务端从 manifest 派生（推荐）vs 保持客户端必填（则模板必须补该字段并进 `required`）。 | 改变契约必填集；影响所有既有客户端。 |
| **D4** | **M8**：接线 `contract_sections`（需 Controller 有可复用的契约分节投影）vs 撤广告。 | 一个是开发量、一个是能力缩水；需产品判断。 |
| **D5** | **M9**：`reason` 收下但不参与控制（推荐）vs 从所有提示中删除。 | 影响审计可读性 vs 接口面最小化。 |
| **D6** | **真实样本**：① `_020`/`_022`（或反馈原 id）的 `WORK/**/state.json` + 同一时刻 `acp_status` 回包；② IRC 失败的完整 job error + `metadata.failure_details` + `WORK/**/*.out` 尾巴；③ singlepoint 作业目录（`RESULT/energy/*.json` + `task.json` 的 method 字段）；④ prepare 失败原文（含 `params`）与 `descriptor.prepare.arguments` 原文；⑤ 触发 `acp_cancel reason` 的 `tools/list` 或提示原文。 | M13/M14/M12/M6/M7/M9 的根因里有 `NOT_VERIFIED`，没有样本只能做「防御性加字段」，可能修不到真正的冻结/截断点。 |
| **D7** | **M4b 科学充分性（A 线签署）**：`normal_modes_v1` 是否足以支撑「恰一个虚频 + 模式方向」的判定（符号约定、质量加权、虚频判据、是否需要 hessian 而非仅 modes）。 | 这是科学判定，不是接口问题；不能由工程侧拍板。 |
| **D8** | **M2 帧上界**：`PES_MAX_PUBLISHED_FRAMES`（默认 200）与「只发极值/边界帧」的降级策略是否可接受。 | 影响 manifest 体积与 agent 的可选帧范围；也影响 PES 曲线的可复算性。 |

---

## 附：本轮不做（明确排除）

- 不做 Hessian 复用策略（Round-8 已列为需科学裁决，仍未定）。
- 不新增 MCP 工具、不改 10/18/29 工具计数语义（`acp_cancel` 的 `reason` 是属性，不是新工具）。
- 不改前端（`frontend/`）；`max_running` 的兼容镜像由后端保证一个周期。
- 不碰 `.scratch/`、`ACP_REPO_AUDIT_20261102.md`、`reports/acp2_implementation/` 历史证据、任何真实 QC/LSF/模型调用。
- 不重启 `acp.service`；不跑 `scripts/acceptance/` 全量（属发布流程）。
