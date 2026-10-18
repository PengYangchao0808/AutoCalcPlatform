# ACP Services First-Slice Contracts — 首片契约冻结（B00-3 / todo 3）

**Status:** FROZEN first slice（字段命名由 owner=B 定义，各路不得另起 DTO）。
**Created:** 2026-10-18，基线 HEAD `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9`。
**Contract version:** `1`（本文件头部版本号即交付物版本；样例集 `sample_version=1` 与之绑定冻结）。
**Owner:** B 路（`src/acp_services/**` 单写者）。消费者：A（前端）、C（科学）。
**Samples:** `tests/baseline/services-evidence/contract_samples/`（正常/空/部分/损坏/旧版本，逐版本冻结）。
**Checker:** `tests/test_services_first_slice_contracts.py`（解析每条样例并按契约语义分类，非仅 JSON 合法性）。
**Authority for dependency direction:** `tests/baseline/services-evidence/public_allowlist.md`。

本文件冻结 X2 §P0 要求的**最小可用部分**；后续扩展以「版本 + 能力标记」追加，不推翻已冻结字段。字段命名由对应 owner 定义，消费者只按本表消费，不另建并行 DTO。

---

## 0. 版本与分类策略（所有类别共用）

### 0.1 版本字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `contract_version` | int | 是 | 本契约大类版本；首片=1。消费者按此判「旧版本」并走迁移映射。 |
| `schema_version` | int | 条件 | 载荷自身版本（科学产品/事件/发行沿用各自 schema 号）。 |
| `sample_version` | int | 仅样例 | 样例集版本，与 contract_version 同步冻结。 |

### 0.2 分类 token（每条样例必须归入其一）

| token | 含义 |
|---|---|
| `accepted` | 满足本契约全部必填字段与语义（含「空」= 合法空集合、无错误）。 |
| `accepted_partial` | 语义允许的部分完成：显式 `partial` 标记或可选字段缺失，但必填身份/键齐备。 |
| `accepted_with_migration` | 旧版本：必填字段以 legacy 名出现，经映射后可接受；消费者须按迁移规则转换。 |
| `rejected` | 违反契约语义（枚举非法、必填缺失、类型错误、身份/修订冲突）。**注意：合法 JSON 不等于 accepted。** |

### 0.3 错误分类（deny / unavailable / stale / conflict / invalid）

| 语义 | 触发 | 消费者动作 |
|---|---|---|
| `deny` | 无授权（无匹配 grant 或 scope 不含 action） | 拒绝，不回退匿名 |
| `unavailable` | 资源未准备（availability=absent/unavailable）或准备失败 retryable | 提示不可用/可重试，不返回伪造数据 |
| `stale` | 资源 ready 但投影未跟上（freshness=stale） | 显示基础事实 + 明确 stale，不隐藏 |
| `conflict` | `expected_revision` 与当前 revision 不符 | 409，不静默覆盖 |
| `invalid` | 载荷违反契约 | 拒绝，记录契约版本与失败字段 |

### 0.4 通用规则

- **空（empty）**：合法空集合（`products=[]`、`events=[]`、无 grant 且 decision=deny）是 `accepted`，不是错误。
- **部分（partial）**：显式 `complete=false` 或 `partial=true` 或可选资产缺失是 `accepted_partial`；不得当作全量。
- **损坏（corrupt）**：结构上是合法 JSON，但违反语义（如枚举外值、必填缺失、sha 长度错）→ `rejected`。
- **旧版本（old-version）**：legacy 字段名/低 schema_version → `accepted_with_migration`，须给出映射。
- **追加规则**：字段**只增不删**；删除或改语义须提升 `contract_version` 并在本文件「变更记录」说明。

---

## 1. Access 与配置边界（X2 交付物 1）

### 1.1 Principal

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `principal_id` | str | 是 | 稳定主体标识（issuer+subject 映射后固定；不随会话变）。 |
| `kind` | enum | 是 | `user` 或 `service` 或 `anonymous`。 |
| `display_name` | str | 否 | 展示名，不用于鉴权。 |
| `tenant_id` | str | 是 | 租户/项目边界（local-single 档为默认租户）。 |
| `roles` | list[str] | 是（可空） | 角色集合；空表示无角色（合法）。 |
| `deployment_tier` | enum | 是 | 见 1.5。 |

### 1.2 AccessScope

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `scope_id` | str | 是 | 授权范围标识。 |
| `tenant_id` | str | 是 | 所属租户；与 Principal.tenant_id 一致才生效。 |
| `resource_kinds` | list[str] | 是（可空） | 覆盖的资源类（project/task/data/event）。 |
| `action` | enum | 是 | `read` 或 `write` 或 `control` 或 `admin`。 |
| `project_ids` | list[str] | 是（可空） | 限定项目；空 = 租户内全部（local-single）。 |

### 1.3 ResourceGrant

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `grant_id` | str | 是 | 授权标识（幂等键基础）。 |
| `principal_id` | str | 是 | 被授权主体。 |
| `scope_id` | str | 是 | 引用的 AccessScope。 |
| `resource_id` | str | 是 | 目标资源（project id / task id / data resource id）。 |
| `actions` | list[str] | 是 | 允许动作子集（⊆ scope.action 扩展）。 |
| `granted_at` | str(ISO-8601) | 是 | 授权时间。 |
| `expires_at` | str(ISO-8601) 或 null | 是 | null = 无期限（合法）；过期即失效。 |
| `basis_revision` | int | 是 | 授权所依据的 owner revision。 |

### 1.4 AccessDecision

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `decision_id` | str | 是 | 判定标识。 |
| `effect` | enum | 是 | `allow` 或 `deny`（仅两值；非法值 rejected）。 |
| `principal_id` | str | 是 | 判定主体。 |
| `scope_id` | str | 是 | 判定范围。 |
| `reason_code` | enum | 是 | `granted` / `no_grant` / `expired` / `scope_mismatch` / `revoked`。 |
| `decided_at` | str(ISO-8601) | 是 | 判定时间。 |
| `available_actions` | list[str] | 条件 | effect=allow 时给出可执行动作；缺失为 partial。 |

### 1.5 deployment_tier 与 execution_mode 的区分（不得混用）

| 概念 | 取值 | 语义 |
|---|---|---|
| `deployment_tier` | `local-single` 或 `lab` 或 `hosted` | 部署档位：授权/隔离/账户能力边界。 |
| `execution_mode` | `local` 或 `lsf` 或 `slurm` 或 `podman` | 执行模式：作业实际在哪跑。 |

同一 deployment_tier 可有多种 execution_mode；公网（hosted）在缺认证/隔离时**拒绝启用**且不得由请求降档。授权失败一律 `deny`，不回退匿名或更宽 scope。

### 1.6 样例语义

| sample_kind | 期望 | 判据 |
|---|---|---|
| normal | accepted | effect=allow，grant 与 scope 匹配，available_actions 齐备 |
| empty | accepted | roles=[]、project_ids=[]、无 grant，decision=deny/no_grant |
| partial | accepted_partial | effect=allow 但 available_actions 缺失；grant.expires_at=null 合法 |
| corrupt | rejected | effect 取枚举外值，或 grant 缺 principal_id，或 basis_revision 为负 |
| old-version | accepted_with_migration | legacy 字段名（如 `user_id`/`allowed`）映射到 Principal/AccessDecision |

---

## 2. Data prepare/read（X2 交付物 2）

### 2.1 DataResourcePort 操作

| 操作 | 输入 | 输出 | 幂等键 |
|---|---|---|---|
| `prepare` | resource_id, resource_kind, idempotency_key, expected_revision, requested_by | PrepareOperation | `idempotency_key`（同键去重，返回既有 operation） |
| `get_operation` | operation_id | PrepareOperation | 只读 |
| `cancel_operation` | operation_id, expected_revision | PrepareOperation | operation_id |
| `read` | resource_id, expected_revision, range | 只读产物句柄 | 只读（零 SSH/SFTP、零 enqueue） |

`read` 不得触发远端抓取或写入；建 operation 只能经显式 `prepare`（`acp_services → acp` 禁令同源：读取面不反向驱动旧实现）。

### 2.2 PrepareOperation 字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `operation_id` | str | 是 | 操作标识（稳定；重启后可查）。 |
| `idempotency_key` | str | 是 | 去重键：同键不重复准备。 |
| `resource_id` | str | 是 | 目标资源。 |
| `resource_kind` | enum | 是 | `input` / `artifact` / `manifest` / `source` / `candidate` / `transfer` / `cache`。 |
| `state` | enum | 是 | `pending` / `running` / `ready` / `failed` / `cancelled`。 |
| `availability` | enum | 是 | `absent` / `preparing` / `ready` / `unavailable`。 |
| `freshness` | enum | 是 | `current` / `stale` / `unknown`。 |
| `basis_revision` | int | 是 | 投影所依据的 owner revision。 |
| `expected_revision` | int | 是 | 调用方期望的资源修订；不符 → `conflict`。 |
| `error` | object 或 null | 是 | `{code, retryable, message}`；无错为 null。 |
| `bytes` | int 或 null | 否 | 已就绪大小时给出。 |
| `sha256` | str 或 null | 否 | 新工件摘要；历史无摘要见迁移规则。 |
| `observed_at` | str(ISO-8601) | 是 | 诊断时间；**不作为跨进程/跨节点严格新旧判定**。 |

### 2.3 availability 与 freshness 与 basis_revision 的语义区分（评审 P1-8）

| 维度 | 回答的问题 | 取值 | 不表示 |
|---|---|---|---|
| `availability` | 资源本体**是否已准备可读** | absent / preparing / ready / unavailable | 不表示投影新旧 |
| `freshness` | **投影是否跟上了权威状态** | current / stale / unknown | 不表示资源不可读 |
| `basis_revision` | 投影**依据哪个 owner revision** | int | 不表示时间新旧；不同 owner revision 不互相替代 |

组合语义：

| 组合 | 判定 | 消费者动作 |
|---|---|---|
| availability=ready + freshness=current | 完整可用 | 正常显示 |
| availability=ready + freshness=stale | 资源可读但投影旧 | 显示 + 标 stale（不得隐藏） |
| availability=absent + 无投影 | 无投影但 jobs 基础事实可见 | 显示基础事实，提示 prepare |
| 旧 attempt 投影迟到 | freshness=stale（basis_revision 落后） | 丢弃迟到投影，不覆盖新事实 |
| 时钟偏移但 basis_revision 正确 | 以 revision 为准 | 不据 observed_at 判新旧 |

**科学 partial/failed/unavailable** 继续按第 4 节科学产品契约保留，不得用 Data 的 availability 表达科学完成度。

### 2.4 样例语义

| sample_kind | 期望 | 判据 |
|---|---|---|
| normal | accepted | ready + current + expected_revision 相符 |
| empty | accepted | pending + absent + unknown，无 error |
| partial | accepted_partial | availability=ready 但 freshness=stale |
| corrupt | rejected | availability 取 `stale`（该值属 freshness），或 state 枚举外，或 sha256 非 64 hex |
| old-version | accepted_with_migration | legacy `op_id`/`avail`（含旧 `stale` 值）映射到 operation_id/freshness |

---

## 3. 事件流（X2 交付物 3）

### 3.1 EventEnvelope 字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `event_id` | str | 是 | 稳定事件 ID（去重键）。 |
| `seq` | int | 是 | 每 owner 内单调递增序号（重放顺序）。 |
| `schema_version` | int | 是 | 事件 schema 号。 |
| `owner_type` | enum | 是 | `job` / `task` / `project` / `data`。 |
| `owner_id` | str | 是 | owner 标识（job/task/project/resource id）。 |
| `scope` | enum | 是 | `task` / `project` / `data`（覆盖范围）。 |
| `event_type` | str | 是 | 事实类型（如 state.changed / data.available）。 |
| `cursor` | str | 是 | 游标（`after_seq` 兼容：与 seq 对应）。 |
| `emitted_at` | str(ISO-8601) | 是 | 发出时间（诊断用）。 |
| `payload` | object | 是（可空） | 事实载荷。 |
| `dedup_key` | str | 是 | 重放去重键。 |
| `coverage` | enum | 是 | `task` / `project` / `data`（与 scope 一致时才可消费）。 |

### 3.2 语义规则

- **cursor 单调**：同一 owner 内 `seq`/`cursor` 单调递增；回退视为 corrupt。
- **过期重置**：cursor 超出保留窗口 → 消费者须重置并全量重读，不得静默跳事件。
- **权限撤销**：撤销后事件流不再投递该 scope 事件；已投递历史不回滚。
- **覆盖范围**：日志/任务进度/摘要/Data availability 分属不同 scope，消费者不得混用。
- **兼容**：旧 `after_seq` JSONL 为兼容出口；重放须按 `dedup_key` 去重，不重复追加同一事实。
- 事件**不得取代** CAS 状态；状态权威在 store，事件是可重放投影。

### 3.3 样例语义

| sample_kind | 期望 | 判据 |
|---|---|---|
| normal | accepted | seq/cursor 单调，owner/scope 齐备 |
| empty | accepted | `events=[]`（合法空流） |
| partial | accepted_partial | payload 缺可选扩展字段，身份/seq 齐备 |
| corrupt | rejected | seq 回退、缺 owner_id、effect/scope 枚举外 |
| old-version | accepted_with_migration | 仅 `after_seq` 无 schema_version 的 legacy 记录 → 迁移补 owner/scope |

---

## 4. 科学输入输出与帧 adapter（X2 交付物 4）

### 4.1 复用既有契约（不另起 DTO）

| 概念 | 权威驻点 | 映射约定 |
|---|---|---|
| 输入意图 | `src/cccp/calculation/requests.py:1762`（TaskRequest）；TaskKind/MethodSpec/StructureInput/TaskResources | worker 只执行冻结 TaskRequest；平台身份字段（workflow/profile/candidate_id/state-sweep）**禁止**出现在此 |
| 运行上下文 | `src/cccp/calculation/context.py:41`（TaskContext） | **不序列化**；由执行侧在进程内构造 |
| 科学结果 | `src/cccp/calculation/results.py:1387`（TaskResult）；`complete=false` 表示 partial | 平台发布只消费 typed payload，不重解析 QC 文本 |
| 展示清单 | `src/acp/storage/manifest.py:88`（ResultManifest v2；Product{id,label,path,kind,metadata}） | 产品 path 相对 `RESULT/` |
| 帧视图 | `src/acp/results/frames.py:175`（VIEW_REGISTRY）；`TrajectoryFrame.to_node()`（:64）、`TrajectoryAnnotation.to_annotation()`（:125） | 所有视图投影必须经 to_node/to_annotation；新视图先注册 ViewSpec |

### 4.2 单位与身份约定

- 坐标 Å、能量 Hartree、频率 cm⁻¹、熵 au（与 TaskResult docstring 一致）。
- 原子身份：1-based 或 0-based 由 `atom_index_base` 显式记录；帧/模式/点身份随 product metadata（node_id、frame_number、scan_step、display_label）。
- 来源摘要：产品 metadata 记录来源摘要与选择来源，供平台溯源（科学来源由 C 提供）。

### 4.3 partial/unavailable/旧产品映射

| 情形 | 语义 | 消费者动作 |
|---|---|---|
| `complete=false` | 科学部分完成，有效子项保留 | 显示部分 + 标 partial；不得当作全量 |
| 科学 unavailable | 无科学产品 | 显示可用基础事实，不伪造 |
| 未知 product.kind | 回退 `file`（ResultManifest.from_dict 行为） | 按 file 展示，标未知种类 |
| 旧 manifest（version<2） | 经迁移映射到 v2 | accepted_with_migration，保留原产品 |

### 4.4 样例语义

| sample_kind | 期望 | 判据（由真实 reader 消费） |
|---|---|---|
| normal | accepted | ResultManifest.from_dict 解析成功且 products 齐备；frame 经 TrajectoryFrame 形状校验 |
| empty | accepted | products=[]，frame 最小字段 |
| partial | accepted_partial | status=partial 或 complete=false |
| corrupt | rejected | products 非 list 或 version 缺/非 int |
| old-version | accepted_with_migration | version=1 旧 manifest → 迁移到 v2 |

消费者测试：`tests/test_services_first_slice_contracts.py::test_science_samples_consumed_by_real_reader` 用 `acp.storage.manifest.ResultManifest` 与 `acp.results.frames` 实际消费 normal/old-version 科学样例。

---

## 5. 执行身份与科学闭包（X2 交付物 5）

### 5.1 ExecutionEnvelope 字段白名单

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `envelope_version` | int | 是 | 信封版本；旧版本走迁移。 |
| `job_id` | str | 是 | 平台任务身份。 |
| `attempt` | int | 是 | 尝试序号（≥1）。 |
| `submission_id` | str | 是 | 提交身份（`sub_` + 摘要）。 |
| `release_id` | str | 是 | 执行版本（内容寻址发行）。 |
| `owner` | str | 是 | 执行 owner 标识（fencing 基础）。 |
| `fencing_token` | str | 是 | 防旧持有者写入。 |
| `plan_digest` | str | 是 | 冻结 plan 摘要。 |
| `plan_identity` | str | 是 | 两层身份（plan_identity/step_identity）。 |
| `data_bindings` | list[StorageBinding] | 是（可空） | 数据绑定与摘要。 |
| `resource_budget` | object | 是 | 资源预算（可能部分）。 |
| `scientific_closure` | object | 是 | `{task_kind, payload_schema, result_schema, algorithm_version}`。 |
| `task_context` | **禁止** | 否 | TaskContext **不得**序列化进信封。 |

平台 UI 身份（浏览器 tab、前端会话 id、展示名）**禁止**进入信封；仅 job/attempt/submission/release + owner/fencing 进入。

### 5.2 ExecutionReceipt 字段白名单

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `receipt_version` | int | 是 | 回执版本。 |
| `submission_id` | str | 是 | 对应提交身份。 |
| `job_id` | str | 是 | 任务身份。 |
| `attempt` | int | 是 | 尝试序号。 |
| `state` | enum | 是 | `completed` / `failed` / `cancelled` / `blocked`。 |
| `result_digest` | str | 条件 | 科学结果摘要；空产出时可为空串（合法空）。 |
| `errors` | list[str] | 是（可空） | 错误集合；空合法。 |
| `written_at` | str(ISO-8601) | 是 | 回执时间。 |

### 5.3 科学闭包规则

- worker 只执行**冻结 plan**；不自增 attempt、不直连 jobs DB。
- 每工作流依赖/release/插件摘要随 `scientific_closure` 冻结；未满足切片保持旧兼容路径但不声称独立。
- 旧 attempt/submission/release 回执**拒绝**；未知提交须对账，不重复派发。

### 5.4 样例语义

| sample_kind | 期望 | 判据 |
|---|---|---|
| normal | accepted | 信封+回执字段齐备，attempt≥1，无 task_context |
| empty | accepted | 回执 errors=[]、result_digest=""（空产出合法） |
| partial | accepted_partial | resource_budget 或 data_bindings 标注部分 |
| corrupt | rejected | 信封含 `task_context`，或缺 attempt，或 attempt<1 |
| old-version | accepted_with_migration | envelope_version=0 legacy 字段名 → 迁移 |

---

## 6. 发行与资产（X2 交付物 6）

### 6.1 ReleaseManifest 字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `release_id` | str | 是 | 内容寻址发行 ID（摘要派生）。 |
| `manifest_version` | int | 是 | 清单版本。 |
| `git_commit` | str | 是 | 源码提交。 |
| `clean` | bool | 是 | 是否干净工作区。 |
| `files` | list[object] | 是（可空） | `{path, sha256, size}`。 |
| `dependency_lock` | str | 是 | 依赖锁定摘要。 |
| `asset_manifest` | list[object] | 是（可空） | 资产（模型/notice）清单。 |
| `notices` | list[str] | 是（可空） | 许可/notice。 |
| `wheel_sha256` | str 或 null | 是 | 最终 wheel 摘要；未构建为 null（partial）。 |
| `legacy_import_window` | object 或 null | 是 | 旧导入兼容窗口声明。 |

### 6.2 资产/构建规则

- Web/IAM/账单不进科学最小安装；静态资源发行缺口单独验证。
- 已应用提交、构建配置、发行文件归属、依赖与资产清单、旧导入窗口、最终 wheel 摘要均须冻结。
- 科学/QC 门修订由 C 提供证据；B 只集成共享打包文件。

### 6.3 样例语义

| sample_kind | 期望 | 判据 |
|---|---|---|
| normal | accepted | files 齐备、wheel_sha256 有效、lock 齐备 |
| empty | accepted | files=[]（合法空占位）、notices=[] |
| partial | accepted_partial | wheel_sha256=null（未构建）或 asset_manifest 部分 |
| corrupt | rejected | sha256 非 64 hex、files 非 list、release_id 与摘要不符 |
| old-version | accepted_with_migration | manifest_version=0 legacy 字段 → 迁移 |

---

## 7. X2 六项交付物覆盖矩阵

| X2 交付物 | 章节 | 样例组 | 消费者 | 最小冻结字段 |
|---|---|---|---|---|
| Access 与配置边界 | §1 | access | A/C | Principal/AccessScope/ResourceGrant/Decision；deployment_tier 与 execution_mode |
| Data prepare/read | §2 | data | A | prepare/get_operation/cancel_operation/read；idempotency_key；expected_revision；availability/freshness/basis_revision |
| 事件流 | §3 | events | A | owner/scope/cursor/重放/过期重置/权限撤销/覆盖范围 |
| 科学产品与帧 adapter | §4 | science | B 写 adapter，A 显示，C 供 schema | TaskRequest/TaskResult/ResultManifest/VIEW_REGISTRY；to_node/to_annotation；partial/unavailable/旧产品映射 |
| 执行协议与科学闭包 | §5 | execution | worker（B/C） | Envelope/Receipt 字段白名单；StorageBinding；scientific_closure；TaskContext 不序列化 |
| 发行与资产 | §6 | release | B/C | 构建配置/发行文件归属/依赖与资产清单/旧导入窗口/wheel 摘要 |

## 8. 样例总表（每条样例的期望分类）

| contract | sample_kind | 期望分类 | 关键判据 |
|---|---|---|---|
| access | normal | accepted | 授权匹配，actions 齐备 |
| access | empty | accepted | 空角色/空项目，deny/no_grant |
| access | partial | accepted_partial | available_actions 缺失 |
| access | corrupt | rejected | effect 枚举外 |
| access | old-version | accepted_with_migration | legacy user_id 映射 |
| data | normal | accepted | ready+current+revision 相符 |
| data | empty | accepted | pending+absent |
| data | partial | accepted_partial | ready 但 stale |
| data | corrupt | rejected | availability=stale（串维度） |
| data | old-version | accepted_with_migration | legacy op_id 映射 |
| events | normal | accepted | 单调 seq |
| events | empty | accepted | 空流 |
| events | partial | accepted_partial | payload 可选缺 |
| events | corrupt | rejected | seq 回退 |
| events | old-version | accepted_with_migration | 仅 after_seq |
| science | normal | accepted | manifest+frame 齐备 |
| science | empty | accepted | products=[] |
| science | partial | accepted_partial | status=partial |
| science | corrupt | rejected | products 非 list |
| science | old-version | accepted_with_migration | version=1 |
| execution | normal | accepted | 信封+回执齐备 |
| execution | empty | accepted | errors=[]、空 digest |
| execution | partial | accepted_partial | budget 部分 |
| execution | corrupt | rejected | 含 task_context |
| execution | old-version | accepted_with_migration | envelope_version=0 |
| release | normal | accepted | files+wheel 齐备 |
| release | empty | accepted | files=[] |
| release | partial | accepted_partial | wheel=null |
| release | corrupt | rejected | sha 长度错 |
| release | old-version | accepted_with_migration | manifest_version=0 |

## 9. 追加与冻结规则

- 首片四类（Access/Data/科学输入输出/执行身份）+ 事件流 + 发行 = 六交付物，字段集在本版本冻结。
- **只增不删**：新增能力以新版本追加字段与样例；删字段或改语义须提升 `contract_version` 并在下方记录。
- 消费者对提供方发布的**同一组 fixture** 做契约测试；只检查「样例是合法 JSON」不算完成（损坏样例是合法 JSON 但必须 `rejected`）。
- 字段命名由 owner 定义，各路不得另起一份 DTO。

## 变更记录

| 日期 | contract_version | 变更 | 提交 |
|---|---|---|---|
| 2026-10-18 | 1 | 首片冻结（四类 + 事件流 + 发行） | n/a |

---

## 10. 执行信封/回执 v1/v2 schema 字段表（B06-1 / todo 30）

**Status:** APPEND-ONLY extension of §5（不删不改已冻结字段；§5.1/§5.2 仍是权威白名单）。
**Module:** `src/acp_services/contracts/envelope.py`（stdlib-only；**不得** import `acp`，见 `public_allowlist.md`）。
**Checker:** `tests/test_services_envelope.py`（消费 `execution_*` 样例 + 负例）。
**Samples:** 复用 §5 已冻结的 `tests/baseline/services-evidence/contract_samples/execution_*.json`，**不重写**；本节的「v1 隐式 legacy / v2 显式」即样例中的 `envelope_version` `0` / `1`。

### 10.1 版本别名映射（消除计划散文 ↔ 冻结 wire 名）

| 计划散文（B06-1） | wire 字段（§5.1，样例） | 语义 | 解码动作 |
|---|---|---|---|
| v1 隐式 legacy | `envelope_version = 0`（或字段缺失） | 无显式信封的旧载荷 | `migrate_legacy_envelope()` 折叠别名 + 升版 + 补默认 |
| v2 显式 envelope | `envelope_version = 1` | 当前显式信封 | 直接严格解码 |

`Receipt` 同构：`receipt_version` `0`=legacy、`1`=current。旧版仅接受 camelCase/`schema_version` 别名折叠（见 §10.6），不静默丢字段。

### 10.2 ExecutionEnvelope 字段（§5.1 冻结 + B06-1 追加）

| 字段（envelope） | 类型 | 必填 | 冻结/追加 | 语义（真实驻点） |
|---|---|---|---|---|
| `envelope_version` | int | 是 | §5.1 冻结 | 信封版本；`schema_version` 为其属性别名。 |
| `job_id` | str | 是 | §5.1 冻结 | 平台任务身份（仅 id，非展示名）。 |
| `attempt` | int | 是 | §5.1 冻结 | 尝试序号（≥1，调度器唯一递增）。 |
| `submission_id` | str | 是 | §5.1 冻结 | `sub_`+sha256(`job_id:attempt`)[:16]（`remote/submission.py:56-64`）；`expected_submission_id()` 等价重算。 |
| `release_id` | str | 是 | §5.1 冻结 | 内容寻址执行版本。 |
| `owner` | str | 是 | §5.1 冻结 | 执行 owner（fencing 基础）。 |
| `fencing_token` | str | 是 | §5.1 冻结 | 防旧持有者写入。 |
| `plan_digest` | str | 是 | §5.1 冻结 | 冻结 plan 摘要。 |
| `plan_identity` | str | 是 | §5.1 冻结 | plan 层身份（`identity.py:366`）。 |
| `data_bindings` | list[StorageBinding] | 是（可空） | §5.1 冻结 | 数据绑定与工件摘要。 |
| `resource_budget` | object | 是 | §5.1 冻结 | 资源预算（见 §10.4）。 |
| `scientific_closure` | object | 是 | §5.1 冻结 | 科学闭包（见 §10.5）。 |
| `release_digest` | str | 否 | B06-1 追加 | release manifest 摘要（`release.py:109-155`）。 |
| `step_identities` | list[str] | 否 | B06-1 追加 | step 层身份快照（`identity.py:374-385`）。 |
| `protocol` | enum 或 null | 否 | B06-1 追加 | 所选执行协议，**按 attempt 冻结**（`local`/`lsf`/`slurm`/`podman`）。 |
| `entrypoint` | enum | 否 | B06-1 追加 | 入口白名单：`worker.execute`/`worker.bootstrap`/`worker.resume`/`worker.reconcile`。 |
| `required_capabilities` | list[str] | 否 | B06-1 追加 | 所需能力（`TaskKind` 词表，`requests.py:54`）。 |
| `task_context` | **禁止** | — | §5.1 冻结 | TaskContext 不得序列化（见 §10.7）。 |

平台 UI 身份（task custom_name、organization 标签、浏览器 tab、展示名）**禁止**进入信封。

### 10.3 StorageBinding 字段（B06-1 定义）

| 字段（binding） | 类型 | 必填 | 语义 |
|---|---|---|---|
| `binding_id` | str | 是 | 稳定绑定 ID（Data 唯一分配；显示名变更不重分配）。 |
| `tenant_id` | str | 否 | 稳定租户身份。 |
| `project_id` | str | 否 | 稳定项目身份。 |
| `endpoint` | str | 否 | 存储端点（如节点/执行后端）。 |
| `root` | str | 否 | 受控根。 |
| `relative_path` | str | 否 | 完整相对路径。 |
| `mapping_version` | int | 否 | 映射版本。 |
| `artifact_digests` | map[str,str] | 否 | 相对工件路径 → sha256（工件摘要）。 |

### 10.4 ResourceBudget 字段（复用 resolve_job_resources 语义）

| 字段（budget） | 类型 | 必填 | 语义 |
|---|---|---|---|
| `nproc` | int 或 null | 否 | 核数（规范名；别名 `cores`/`ncores` 折叠）。 |
| `mem` | str 或 null | 否 | 归一化带后缀内存（`resolve_job_resources` 输出名）。 |
| `mem_mb` | int 或 null | 否 | 整数 MB（别名 `memory_gb` → ×1024 折叠）。 |
| `queue` | str 或 null | 否 | LSF 队列。 |
| `walltime_s` | int 或 null | 否 | 时长预算（秒）。 |
| `partial` | bool | 否 | 部分预算标记（§5.4 partial 语义）。 |
| `extra` | object | 否 | 其余调度元数据（如 `batch_id`/`parallelism`）透传。 |

> 复用 `resolve_job_resources`（`src/acp/scheduler/resources.py:14-41`）的**字段名与语义**，但因 `acp_services → acp` 禁令**不 import** 该模块。

### 10.5 ScientificClosure 字段

| 字段（closure） | 类型 | 必填 | 语义 |
|---|---|---|---|
| `task_kind` | str | 是 | 冻结 TaskRequest 的任务种类。 |
| `payload_schema` | int | 否 | 载荷 schema 号。 |
| `result_schema` | int | 否 | 结果 schema 号。 |
| `algorithm_version` | str | 否 | 算法版本。 |
| `dependency_digests` | map[str,str] | 否 | 工作流依赖/插件摘要。 |

### 10.6 ExecutionReceipt 与 ExecutionError 字段（§5.2 冻结 + B06-1 追加）

| 字段（receipt） | 类型 | 必填 | 冻结/追加 | 语义 |
|---|---|---|---|---|
| `receipt_version` | int | 是 | §5.2 冻结 | 回执版本；`schema_version` 为其属性别名。 |
| `job_id` | str | 是 | §5.2 冻结 | 任务身份。 |
| `attempt` | int | 是 | §5.2 冻结 | 尝试序号（≥1）。 |
| `submission_id` | str | 是 | §5.2 冻结 | 对应提交身份。 |
| `state` | enum | 是 | §5.2 冻结 | `completed`/`failed`/`cancelled`/`blocked`。 |
| `result_digest` | str | 条件 | §5.2 冻结 | 科学结果摘要；空产出为 `""`（合法空）。 |
| `errors` | list[str 或 object] | 是（可空） | §5.2 冻结 + 追加 | 空合法；`str` 保持原样，`{code,message,retryable}` 为 B06-1 追加的 typed 错误（`list[str]` 仍是合法子集）。 |
| `written_at` | str(ISO-8601) | 是 | §5.2 冻结 | 回执时间。 |
| `release_id` | str | 否 | B06-1 追加 | 执行版本。 |
| `release_digest` | str | 否 | B06-1 追加 | release 摘要。 |
| `owner` | str | 否 | B06-1 追加 | 执行 owner。 |
| `fencing_token` | str | 否 | B06-1 追加 | fencing。 |
| `plan_identity` | str | 否 | B06-1 追加 | plan 层身份。 |
| `step_identities` | list[str] | 否 | B06-1 追加 | step 层身份。 |
| `data_bindings` | list[StorageBinding] | 否 | B06-1 追加 | 数据绑定。 |
| `evidence_digests` | map[str,str] | 否 | B06-1 追加 | 证据/工件摘要。 |
| `task_context` | **禁止** | — | §5.2 冻结 | TaskContext 不得序列化。 |

`ExecutionError` = `{code: str, message: str, retryable: bool}`。

`migrate_legacy_receipt()` 折叠 camelCase/`schema_version` 别名并升版。

### 10.7 硬语义（负例）

- **TaskContext 不序列化**：`task_context`/`context`/`progress`/`cancelled`/`config`/`workdir`/`backend`/`capability_extras` 等运行时字段出现即 `TaskContextSerializationError`；值为 callable/lock/泛型 dataclass/TaskContext 实例同样拒绝（`ensure_no_runtime_state`）。
- **协议按 attempt 冻结**：`select_protocol()` 同一 attempt 只可调用一次；resume 重选 → `AttemptProtocolError`；新 attempt 经 `advance_attempt(next=self.attempt+1)` 才可重选。
- **入口/能力不支持明确失败**：`entrypoint ∉` 白名单 或 `required_capabilities ⊄` 词表 → `UnsupportedCapabilityError`（不被静默降级）。
- **未知字段拒绝**：信封/回执出现契约外字段 → `UnknownFieldError`（含 UI 身份字段）。
- **未来版本拒绝**：`envelope_version`/`receipt_version` 高于当前 → `EnvelopeVersionError`。

### 10.8 变更记录（追加）

| 日期 | contract_version | 变更 | 提交 |
|---|---|---|---|
| 2026-10-18 | 1 | B06-1 追加 ExecutionEnvelope/Receipt v1/v2 字段表（§10）；§5 白名单未改，仅追加 `release_digest`/`step_identities`/`protocol`/`entrypoint`/`required_capabilities`（信封）与 release/owner/fencing/plan/step/binding/evidence（回执）；`errors` 由 `list[str]` 扩展为 `list[str 或 typed]`（`str` 子集保留） | 待提交 |
