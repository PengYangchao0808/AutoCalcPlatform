# B08-5 研究溯源边 — 平台字段映射与 C02 diff 记录

| 字段 | 值 |
|---|---|
| 任务 | Plan-2 todo 46（B08-5）：Runtime/Data 只读证据投影 |
| owner / provider / consumer | B（Services） / C（C02 科学派生字段） / A06、69（下游） |
| tracked 位置 | `tests/baseline/services-evidence/lineage_field_map.md`（B 侧权威；`.omo/` 被 gitignore） |
| C02 通道 | `tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md` §2.4（`derived` → B-T46 研究溯源边投影 + A 候选选择 metadata） |
| C02 契约 | `src/cccp/calculation/products/contracts.py::DerivedPayload`（T12，`PRODUCT_SCHEMA_VERSION=1`） |
| 平台实现 | `src/acp_services/runtime/lineage.py`（`C02_FIELD_MAP` / `C02_FIELD_MAP_STATUS` / `add_c02_derived`） |
| status | `delivered`（provider 侧契约已交付；**未 integrated**） |
| cross_lane_e2e | **NOT_VERIFIED**（HN-02 §9.3 状态 B 未关闭；见 §4） |

---

## 1. 五类边（本任务冻结）

`src/acp_services/runtime/lineage.py::EdgeType`：

| 边 | 值 | 含义 | 平台来源（只读） |
|---|---|---|---|
| 使用 | `uses` | 任务消费输入 / 上游工件 | `spec.input` 的来源引用（`input.source_job_id` / `input.source.asset_id` / `input.from.source_job_id`） |
| 产生 | `produces` | 任务产出结果产品 | `RESULT/result_manifest.json` 的 product 条目（id/label/path/kind） |
| 派生 | `derives` | 从其它产品派生（消费 C02） | C02 `DerivedPayload` 信封（本文件 §2） |
| 人工选择或修订 | `revision` | 人工选择 / 修订（版本化，不覆盖原结果） | `RevisionLedger`（append-only） |
| 复用 | `reuses` | 复用既有结果（resume/continue/rerun 同目录） | attempt / resume 来源（`resume_source.json` 语义，平台侧） |

## 2. 字段映射表（C02 source field → 平台 lineage target field）

> 逐字段**原样**（verbatim）；不做单位换算、不重编号、不发明语义。目标边 = `LineageEdge(edge_type=derives)`。

| # | C02 源字段（file:line） | 平台目标字段 | 规则 |
|---|---|---|---|
| 1 | `ScienceProduct.identity`（contracts.py:1327+） | `evidence.identity` | 逐字携带（`product_kind`/`generator`/`algorithm`/`algorithm_version`/`input_digest`/`params_digest`）；`object_ref` 取 `identity.input_digest` |
| 2 | `DerivedPayload.sources[]`（contracts.py:1244） | `evidence.sources` + `mappings[].{source_kind,artifact,native_index}` | 每源一条 `IdentityMapping`；`native_index` 原样保留（不重编号）；`source_kind` 逐字保留，**不**归类为 atom/frame/mode 语义（`kind=None`） |
| 3 | `DerivedPayload.source_versions[]`（contracts.py:1245） | `evidence.source_versions` | 逐字，与 `sources` 一一对应（可复现性） |
| 4 | `DerivedPayload.parameters[]`（contracts.py:1246） | `evidence.parameters` | 逐字（`ProductParameter` name/value/unit/source） |
| 5 | `DerivedPayload.raw_io[]`（contracts.py:1247） | `evidence.raw_io` | 逐字（`ProductIODatum` name/role/value/source） |
| 6 | `DerivedPayload.reference_frame`（contracts.py:1248） | `evidence.reference_frame` | 逐字（axes/origin/energy_zero） |
| 7 | `ProductKind.DERIVED` | `edge_type = "derives"` | 唯一映射为 `derives` 的科学产品 kind |
| — | `ScienceProduct.state` / `complete` / `errors` / `missing_reason` | `evidence.{state,complete,errors,missing_reason}` | 逐字（缺失以 reason 表达，不补零、不伪装成功） |

代码常量：`src/acp_services/runtime/lineage.py::C02_FIELD_MAP`（逐条 `FieldMapEntry`，`verbatim=True`）。

## 3. 与 C02 映射的 diff 记录

| 关注点 | C02 侧（HN-02） | 平台侧（本文件 / `lineage.py`） | diff / 说明 |
|---|---|---|---|
| 目标形状 | 科学产品 → `TrajectoryFrame.to_node()` / `TrajectoryAnnotation.to_annotation()`（帧/标注） | 科学产品 → `LineageEdge(derives)`（溯源边 + `evidence`） | 两者消费**同一** `DerivedPayload`；HN-02 §2.4 明示 `derived` 实装闭包行指向 **B-T46**（本任务），故平台侧承载溯源边而非帧字段 |
| `derived` 产品 | §2.4 标 **N/A** 至 node/annotation（帧契约不承载溯源） | 映射为 `derives` 边 + 全字段 verbatim | 一致：派生信封不进帧契约，改由本平台边投影承载；未放松 `to_node()` 契约（ANTI #21） |
| 候选选择 metadata | §2.2/§4：`candidate_id`/`active`/`saved`/`recommended_type`/`selection_source`/`confidence`/`reason`（人工 review） | `EdgeType.REVISION` 边 + `RevisionLedger`（版本化；`payload` 逐字） | 平台只记录人工选择/修订的**版本化边与 payload**，不回写权威科学结果，不重算权威判据 |
| `native_index` | §2.1/§2.2：源编号空间，**永不重编号** | `IdentityMapping.native_index` 逐字保留 | 逐项一致 |
| 单位 | §1 单位表（`angstrom`/`hartree` 等契约字面值） | 不承载单位（边为溯源，不复制数值） | 平台不复制科学数值，故无单位换算风险 |

## 4. 验证状态（tri-state）

| 项 | 状态 | 依据 |
|---|---|---|
| 五类边 + 版本化修订 + 原结果不覆盖 | PASS | `pytest tests/test_services_evidence_projection.py -q` |
| C02 字段逐条 verbatim 保真（native_index 不重编号） | PASS | 本文件 §2 + 单元断言 |
| C02 provider 契约可消费 | PASS（provider 侧） | HN-02 状态 A「契约可消费」`delivered` |
| 跨路端到端（C 生产入口 → B 投影 → A 消费，逐项一致） | **NOT_VERIFIED** | HN-02 §9.3 状态 B `pending`；B/A 接入前不宣称 |
| 真实 QC 运行 | NOT_APPLICABLE | 纯数据投影，无子进程 |

> `C02_FIELD_MAP_STATUS["cross_lane_e2e"] == "NOT_VERIFIED"` 为该状态的机器可读出口；任何消费方不得读作已验证。
