# node-selection-at-submission - Work Plan

## TL;DR (For humans)

**目标**: ACP 调度器支持"提交时选择计算节点" — 多异构远程节点按能力过滤（静态声明 + 探测推断混合），手动指定与自动默认（least-loaded + 同源亲缘）双模式，含前端选择器、节点选择持久化、并发竞态修正、per-node LSF 队列覆盖。任务涉及 scheduler 全链路（config/registry/manager/API/DB）+ 前端单页 + 文档，最终交付一段让"任何任务提交时都能看到能力过滤后的节点列表并选择或放行自动选择"的可运行行为。

**为什么这样设计**: 代码里 `JobSpec.target_node/execution_mode` 管道与 `NodeRegistry.select_remote(required=…)` stub 已存在（80% 管道就绪），真正缺的是能力模型 + 推导 + 过滤 + 前端选择器。15 项需求决策（D1–D15）已与用户逐项确认并写入设计稿；Metis + Oracle phase-1 已核查事实（修正了 nmr 软件依赖等错误）并锁定约束（NodeSpec 字段测试锁、migration 013 追加、亲和/竞态的服务端语义等）。

**它不会做什么**: 版本约束过滤；per-node walltime/extra_flags；LSF `-m` 宿主机 pin；节点注册 UI/自动发现；重写 `_probe_required_binaries`/`requires_binaries` 表（记录为后续技术债）；v0 legacy `/api/jobs` 节点字段；历史行回填。

**工作量**: 5 波 14 个实施 todo + 4 个终验任务（F1–F4）。核心风险在推导表正确性（W2 内锁测试）、锁定测试同波更新（T4/T5）、后端权威不漂移（T11/T12 前端只消费端点）。

## Scope

**In scope**（对应设计稿 v0.2 `.omo/drafts/node-selection-design.md`，执行时以该文件为规范；决策 D1–D15 均已确认）：

1. 节点能力声明：`RemoteNode.capabilities.{software,tags}` + `queue` 覆盖；软件枚举 = `_DOCTOR_SOFTWARE_SCRIPT` 的 7 名；未声明 → 混合三态（declared / probe-inferred / unknown 软件回退通用；D13: 标签恒不满足）。
2. 软件需求权威推导 `derive_required_software(spec)`（workflow/protocol/profile 固定表 + METHOD_SCHEMAS engine 映射）+ `local_satisfies` + `matches_capabilities` + `is_degraded`（单一实现）+ 错误码（英文 + code/missing_* 字段）。
3. 提交链路：创建时校验 400（未知/禁用/能力不满足 target_node）+ 调度后盾；auto 语义 D14（需求空→默认；本机满足→local；否则升级能力匹配远程）；affinity（continue 缺省回源 / rerun 捕获 / stage 链服务端复制）；continue 显式改选（D15）；预留计数竞态修正（锁 + 重启重建，软上限）。
4. per-node queue 覆盖 → BSUB 脚本（None 时字节一致）。
5. 持久化：migration 013（jobs.node_id/host，追加不重排，不回填）；tasks.node_id 真实节点名 + on-disk task.json；v1 模型顶层 node_id/host；v2 batch 字段透传。
6. API：`POST /api/v1/nodes/matching` 预览端点（含 local_satisfies、note）。
7. 前端选择器（三态下拉 + 标签 chips + 徽标 + 置灰原因 + i18n zh/en；载荷 target_node/node_tags；stage 预选；continue 控件）+ test_frontend_sync 新锁。
8. 文档：`docs/remote_execution_plan.txt` Phase 6 状态修正 + Phase 8 能力过滤章节；`config/defaults.yaml` cluster 注释示例。

**Out / Must NOT Have**: 版本约束；per-node walltime/extra_flags；LSF `-m` pin；GPU/资源实时调度（标签表达）；节点注册 UI/自动发现；重写 `_probe_required_binaries` 或 `WORKFLOW_CATALOG.requires_binaries`；v0 legacy `/api/jobs` 节点字段；历史行回填（读端回退）；服务端中文错误串；queue 进入匹配/过滤逻辑（仅提交属性）；除 T5/T4 明确列出的锁定测试外不得改动既有锁定测试语义。

**兼容红线**: 未声明 capabilities 的旧节点不因本次改动而无法接任务（不勾标签时软件需求回退通用）；`node.queue is None` 时 LSF 脚本输出与改动前字节一致；migration 追加在 `_MIGRATIONS` 末尾；spec_json 反序列化容忍缺键。

## Verification strategy

- **每 todo 自带 agent 可执行验收**: 单测/集成测试命令 + 证据路径（测试文件、断言内容、pytest 输出文件）。
- **锁定测试守卫**（改动时同 commit，禁止语义漂移）:
  - `tests/test_acp_execution_target.py::test_registry_nodespec_field_subtraction` — NodeSpec 字段新增与测试更新同 commit（T4）。
  - `tests/test_acp_execution_target.py:401-419`（`test_invalid_target_node_fails_fast_no_retry`）— 语义从 dispatch 失败改为创建时 400 + 保留调度后盾测试，同 commit（T5）。
  - `tests/test_remote_phase2.py`（derive_lsf_resources/generate_lsf_script）— queue=None 字节一致（T7）。
  - `tests/test_frontend_sync.py` — 新增选择器结构锁/i18n 键锁/无硬编码节点名（T12）。
  - `tests/test_f4_scope_audit.py` — migration/表结构审计（T8）。
- **能力推导正确性**: 种子行全部在测试 docstring 中带 file:line 引用（T3）；漂移锁测试（软件枚举 == 探测脚本名，T1）。
- **每波门禁**: `pytest -m "not slow"` 定向相关文件（具体命令见各 todo）+ 全量门禁仅在 T14。

## Execution strategy

5 波，编号 1–14 顺序执行；标注 [P] 的任务在单 worker 顺序执行时可与同波其他任务换序（无依赖），多 worker 时可并行：

- **W1 配置模型**: T1 → T2（T2 依赖 T1 的声明形状）
- **W2 能力核心**（单波内依赖: T3 → T4 → T5 → T6；T7 仅依赖 T1，[P]）:
  - T3 依赖 T1（capabilities dataclass 与枚举）
  - T4 依赖 T3（matches_capabilities）+ 需先完成 T1
  - T5 依赖 T3+T4；T6 依赖 T5（复用校验 helper）+ T4（affinity 参数）
- **W3 API/持久化**: T8 [P 可提前至 W1 后]; T9 依赖 T3（derive/matches）；T10 依赖 T5（校验 helper 泛化）
- **W4 前端**: T11 依赖 T9（matching 端点）+ T2（NodeStatusModel 字段）；T12 依赖 T11 + T6（continue 覆盖）+ T8（顶层 node_id/host 字段）
- **W5 收尾**: T13 → T14（全量回归）

依赖矩阵（先行 → 后继）: T1→{T2,T3,T4,T7}；T2→{T9,T11}；T3→{T4,T9}；T4→T5；T5→{T6,T10}；T8→T12；T9→T11；T6,T11→T12；全部→T14。

执行纪律：每 todo 完成即跑其验收命令并记录证据；测试与实现同 todo 同 commit；不引入新第三方依赖；遵循 AGENTS.md 反模式清单（不改 `__init__.py` 实现、不新增路径拼接、错误消息英文等）。

## Todos

### W1 — 配置模型

- [x] 1. RemoteNode 能力声明与 queue 覆盖解析 + 软件枚举漂移锁
  - **References**: `src/acp/scheduler/remote/config.py`（RemoteNode 30-134, RemoteExecutionConfig 137-257, from_config_dict 204-257）；`config/defaults.yaml` cluster 段 328-384（注释示例 351-363 需同步）；`src/acp/scheduler/remote/node_manager.py` `_DOCTOR_SOFTWARE_SCRIPT` 620-646（7 软件名）；`src/cccp/config.py` `_get_default_config()` cluster 块 440-454（内置默认需同步示例注释）。
  - **任务内容**: RemoteNode 增加 `queue: str | None = None` 与 `capabilities: NodeCapabilities | None = None`（frozen dataclass: `software: tuple[str, ...]`、`tags: tuple[str, ...]`）。`from_config_dict` 解析 `capabilities.software`（去重保序）与 `capabilities.tags`；软件名不在枚举 → `logger.warning` 并丢弃该值（不阻断）；`capabilities` 区块整体缺省 → None（generic 哨兵）。`queue` 缺省 → None（回落 `config.queue`）。`config/defaults.yaml` 与 `cccp/config.py` 内置默认的 cluster 注释示例同步新字段。
  - **Acceptance**:
    - `RemoteNode.from_config_dict({...capabilities:{software:["orca","orca","xtb"],tags:["gpu"]},queue:"bigmem"})` → capabilities.software==("orca","xtb")（去重）、queue=="bigmem"
    - 无 capabilities 键 → `capabilities is None`；software 含未知名 "foo" → 加载成功且该值被丢弃、日志含 warning
    - 漂移锁测试（新文件 `tests/test_node_capabilities.py`）: 声明枚举 == `_DOCTOR_SOFTWARE_SCRIPT` 内的软件名集合（从源码脚本文本提取），防止两处 drift
  - **QA happy**: `pytest tests/test_node_capabilities.py -v` 全绿（含 parse 用例）。
  - **QA failure**: 构造未知软件名与重复项用例断言丢弃/去重行为，缺失断言即红。
  - **Commit**: `feat(scheduler): declare per-node capabilities and queue override in remote config`

- [x] 2. NodeStatus 三态扩展与 NodeStatusModel/API 同步
  - **References**: `src/acp/scheduler/remote/node_manager.py`（NodeStatus 173-196, `_refresh_status` 504-557, `_software_for` 559-578）；`src/acp/api/v1_schemas.py`（NodeStatusModel 956-967, NodeListResponse 970-974）；`src/acp/api/v1_routes.py`（/nodes 端点 4164-4203）；`src/acp/scheduler/remote/config.py` T1 产物。
  - **任务内容**: NodeStatus 增加 `declared: dict | None`（{software, tags, queue}，来自静态声明）、`capability_state: "declared"|"probe-inferred"|"unknown"`、`declared_ok: bool | None`、`mismatch: list[str]`、`probe_note: str | None`。NodeManager 填充: 有声明 → declared 态 + declared_ok/mismatch（探测软件 ⊆ 声明？）；无声明且探测软件非空 → probe-inferred（软件集=探测集）；无声明且探测空/失败 → unknown（probe_note="capability unknown; treated as generic"）。NodeStatusModel 同步新字段（纯增量）。探测/状态 TTL 与缓存结构不变。
  - **Acceptance**: `GET /api/v1/nodes`（mock NodeManager 返回构造 NodeStatus）响应含 capability_state/declared_ok/mismatch/probe_note — **序列化断言新测试加入 `tests/test_acp_api.py`**（无既有 /nodes FastAPI 测试可循，属新建模式: 经 request.app.state 注入 mock NodeManager；函数名 test_v1_nodes_capability_state，含 "node" 确保 -k 命中）；三态构造单元测试（declared 不一致 → declared_ok=False + mismatch 列缺项；无声明+探测成功 → probe-inferred；无声明+探测空 → unknown + note）；既有 NodeStatusModel 字段不破坏。
  - **QA happy**: `pytest tests/test_acp_api.py tests/test_remote_phase1.py tests/test_acp_api_v1.py -k "node" -m "not slow"` 绿（test_acp_api_v1 中如无 /nodes 用例则为空收集可接受，新增用例以 test_v1_nodes* 命名放入 test_acp_api_v1.py 或 test_acp_api.py 其一）。
  - **QA failure**: 断言三态转换 helper（新增纯函数）输入探测空 dict → unknown，若实现漏分支即红。
  - **Commit**: `feat(scheduler): surface capability state on node status model`

### W2 — 能力核心

- [x] 3. capabilities.py: derive_required_software + matches_capabilities + local_satisfies + is_degraded + 错误码
  - **References**: 新模块 `src/acp/scheduler/capabilities.py`；`src/acp/catalog.py`（METHOD_SCHEMAS, WORKFLOW_CATALOG）；`src/acp/scheduler/jobs.py` JobSpec 483-526；`src/cccp/software.py`（resolve_executable）；`src/acp/scheduler/nodes.py`（错误类 ExecutionTargetError/ExecutionCapacityUnavailable, validate_execution_request 100-114）；工作流软件证据（种子行复核目标）: `src/acp/workflows/nmr.py` 6-16,437-445、`src/acp/calculations/primitives/thermochemistry.py` 10,73（run_shermo）、`src/acp/workflows/energy_shared.py` 382-500（censo-crest rank1/cumulative-99 → orca+shermo）、Confsearch protocols `src/acp/confsearch/protocols/*.py`、README 协议描述。
  - **任务内容**: 实现:
    - **JobSpec (jobs.py) 增加 `node_tags: list[str] = field(default_factory=list)`**（含 store.py spec_json 反序列化 .get() 容忍缺键，round-trip 测试在 T8 锁定）
    - `derive_required_software(spec) -> frozenset[str]`：workflow/protocol/profile 固定表 `WORKFLOW_SOFTWARE_REQUIREMENTS`（**先复核**：对表内每一行给出 file:line 证据并写入测试 docstring；种子行含 nmr→{crest,censo,orca}、BatchOptimize opt_freq_sp_thermo→+shermo、censo-crest rank1/cumulative-99→+orca+shermo、xtb-crest→{xtb,crest}、censo-crest→{xtb,crest,censo}、xtb-md→{xtb,molclus,isostat}(复核)、xtbmd-censo→{xtb,molclus,isostat,censo}(复核)） + method schema levels engine→软件映射（engine 字段名 wave-0 核实; 未知 engine 忽略+log）
    - `matches_capabilities(required_software, required_tags, node) -> MatchResult`（satisfies/missing_software/missing_tags/reasons）：declared 权威；probe-inferred 用探测集；unknown 软件回退满足；**标签: 仅 declared 节点的声明 tags 可满足，其余恒不满足 (D13)**；reasons 英文文案
    - `local_satisfies(required_software) -> bool`（cccp resolve_executable 逐一解析）
    - `is_degraded(node_status) -> bool`（running>=max 或 disk>=90，单一实现）
    - 错误码: 在 nodes.py 或 capabilities.py 定义 `NoCapableNodeError`，扩展 ExecutionTargetError 携带 `code` 与 `missing_software/missing_tags` 字段（英文消息）
  - **Acceptance**: 新测试 `tests/test_capabilities.py`: 推导行覆盖表内每个 workflow/protocol/profile 组合（断言精确 frozenset）；matches 四象限（declared 满足/缺软件/缺标签/unknown 软件满足+标签不满足）；local_satisfies mock resolve；is_degraded 三用例（满载/磁盘高/健康）；错误类字段断言。测试 docstring 含每行 file:line 证据。
  - **QA happy**: `pytest tests/test_capabilities.py -v` 绿。
  - **QA failure**: 任一行推导与真实代码复核不符 → 改表不改测试（表中每一行必须有引用，无引用行不得入表）。
  - **Commit**: `feat(scheduler): derive job software requirements and capability matching`

- [x] 4. select_remote/require 能力过滤 + 亲和参数 + 预留计数（锁/重启重建）+ NodeSpec 字段
  - **References**: `src/acp/scheduler/nodes.py`（NodeSpec 59-86, select_remote 178-208, require 158-171, _remote_running_jobs 217-227）；`src/acp/scheduler/manager.py`（registry 构造 208-214, _resolve_execution_target 1800-1812, _ensure_remote_capacity 1850-1866, STARTING retry loop 1560-1638, _try_recover_remote_job 区域）；`tests/test_acp_execution_target.py`（test_registry_nodespec_field_subtraction 85-89 断言无 tags/priority — **本 todo 同 commit 更新**，改为断言新增字段存在、仍无 priority）；`src/acp/scheduler/remote/config.py`（RemoteNode 能力, 供 registry 映射）。
  - **任务内容**: NodeSpec 增加能力承载字段（如 `capabilities` + `capability_state` 摘要或 declared dict，与测试同 commit）；`select_remote(required=frozenset, required_tags=frozenset, affinity_node=str|None)` 正式实现: 候选集 = 匹配集 ∧ 非 offline/degraded；亲和优先（满足候选即返回）→ least-loaded ratio（含预留数再比）→ YAML 序；匹配集空 → NoCapableNodeError；候选空但匹配非空 → ExecutionCapacityUnavailable。`require(name)` 增加能力硬校验（不满足 → ExecutionTargetError 带 code/missing）。**_resolve_execution_target auto 分支编排（D14）**: derived 空 → 跟随服务器默认；非空 ∧ local_satisfies → local；否则 select_remote(required=derived, required_tags=spec.node_tags, affinity_node=…)；提交解析后将 derive 结果写入 `result["required_software"]`（审计，设计 §1.3）。预留计数: NodeRegistry `reservations: dict[node_name, set[job_id]]`，变更全在 `JobManager._lock` 下；select 时 +1，终局（成功首轮 poll 观察到 running / 失败 / 取消）释放；**启动重建**：扫描 RUNNING/PENDING/PAUSED 行 `result["execution_target"]` 重建（镜像 _try_recover_remote_job）。
  - **Acceptance**: 过滤测试（声明缺 orca 被排除；unknown 软件回退被包含；标签不足被排除；affinity 满足时优先于更空载节点）；预留测试（并发 select 两次同节点 → 第二次计入预留不超 max；release 幂等；失败路径释放；**重启重建**：seed store 有 RUNNING 行 result.execution_target=comp-01 → 重建后 reservations 含该 job）；**dispatch auto 编排用例**（mock local_satisfies=True + 存在远程候选 → 走 local；derived 空 → 服务器默认；local_satisfies=False + 候选存在 → select_remote 被调用且 required 正确；提交后 result["required_software"] 存在）；`test_registry_nodespec_field_subtraction` 更新后绿。
  - **QA happy**: `pytest tests/test_acp_execution_target.py tests/test_capabilities.py -m "not slow"` 绿。
  - **QA failure**: 预留未在锁下变更或重建缺失 → 新增并发/重建用例红。
  - **Commit**: `feat(scheduler): capability-filtered node selection with affinity and in-flight reservations`

- [x] 5. 创建时校验 400（未知/禁用/能力不满足）+ 调度后盾 + 锁定测试语义更新
  - **References**: `src/acp/api/v1_routes.py`（create_job 1230-1276, JobSpec 构建 1257-1258）；`src/acp/api/v1_schemas.py`（V1JobCreateRequest 275-289, V1JobSpecModel 187-201）；`src/acp/scheduler/manager.py`（submit 468-542, _resolve_execution_target 1800-1812）；`tests/test_acp_execution_target.py`（401-419 test_invalid_target_node_fails_fast_no_retry — **语义更新在本 todo 同 commit**）；`src/acp/scheduler/nodes.py`（validate_execution_request）。
  - **任务内容**: 新增同步校验 helper（v1 create_job 与 v2 batch 共用，纯函数可单测）: target_node 非空时 → registry 存在且 enabled 且能力满足（derive+matches），失败 → 抛出 ExecutionTargetError(code=unknown_target_node/target_node_incapable, missing_*)，**HTTP 层按调用方翻译: v1 → 400, v2 batch → per-item failed[]**；auto + 需求非空 + 匹配集空（无远程节点可满足 + local 不满足）→ 抛出 NoCapableNodeError(code=no_capable_node)（同上翻译）。调度后盾保留（_resolve_execution_target 处仍校验，防创建后配置漂移；出现 NoCapableNodeError → 记事件 + 转 ExecutionCapacityUnavailable 语义防 STARTING-forever）。更新 401-419 测试: 断言创建即 400；新增调度后盾漂移用例（创建通过后改配置 → dispatch 转容量语义重试而非永久失败）。
  - **Acceptance**: POST /api/v1/jobs 带未知 target_node → 400 + code=unknown_target_node；带能力不足节点 → 400 + missing_software；auto 且无任何节点满足且 local 不满足 → 400 no_capable_node；创建通过（容量场景）→ 提交成功 STARTING；更新后的 401-419 断言创建 400；调度后盾漂移用例绿。**确定性要求**: no_capable_node 与 local 分支的测试必须 monkeypatch `capabilities.local_satisfies`（或 cccp resolve_executable）——CI/开发机可能装有 QC 软件导致分支翻转。
  - **QA happy**: `pytest tests/test_acp_execution_target.py -m "not slow"` 绿；手动 curl 三例（未知/能力不足/成功）证据。
  - **QA failure**: 去掉 helper 校验 → 401-419 新断言红（证明测试锁住行为）。
  - **Commit**: `feat(scheduler): validate node target at job creation (fail fast with error codes)`

- [x] 6. continue 显式改选 + rerun 亲和捕获 + stage 链服务端复制 + pop 语义保留 node
  - **References**: `src/acp/scheduler/manager.py`（continue_job pop 逻辑 1281-1282, rerun_job 594-695 及其 result 替换 663-666, clone_job 587-592）；`src/acp/api/v1_routes.py`（continue 端点区, IRC 复制 3487-3488, stage 创建点）；`src/acp/api/v1_schemas.py`（continue 请求模型）；`src/acp/scheduler/nodes.py`（校验 helper, T5 产物）。
  - **任务内容**: continue_job 增加可选 target_node 覆盖（API 请求体 + manager 签名），缺省 = 原 result.execution_target（回源）；覆盖值按 T5 同规则校验（400）。pop 逻辑改为只清 lsf_job_id/remote_dir/command_line，**保留 node/execution_target/execution_kind**。rerun_job: 替换 result 前捕获原 execution_target 写入新 job 亲和（spec 不动，auto 分支读取）。stage 链: 检查现有各 stage 创建路径（IRC 3487-3488 已复制），BatchOptimize 等 from-job 创建路径补服务端复制源 execution_mode/target_node（显式值才复制，auto 不落 spec）。亲和读取点: `_resolve_execution_target` auto 分支消费 affinity_node（T4 参数）。
  - **Acceptance**: POST continue 带 target_node=合法节点 → job continue 后回该节点；带未知 → 400；不带 → 回源节点（execution_target 断言）；continue 后 result 保留 node/execution_target 且无 lsf_job_id；rerun 后新 job 亲和源=原 execution_target（单测 mock）；stage 创建（BatchOptimize from-job 路径）spec 复制源显式 target_node；无显式目标时行为不变（auto）。
  - **QA happy**: `pytest tests/test_acp_job_operations.py -k "continue or rerun" -m "not slow"` 绿 — 新增 continue 覆盖/rerun 亲和用例以 test_continue_*/test_rerun_* 命名加入**该文件**（既有 continue/rerun 测试主场: test_acp_job_operations.py:721-1043 区域）。
  - **QA failure**: continue 后断言 result["node"] 仍存在 → 若被 pop 即红。
  - **Commit**: `feat(scheduler): affinity-aware continue/rerun with explicit node override`

- [x] 7. per-node queue 覆盖线程化（BSUB 字节一致）
  - **References**: `src/acp/scheduler/remote/script_gen.py`（LSFScriptSpec 84-119, build_lsf_script_spec ~508-587, generate_lsf_script 590-628, #BSUB -q 596）；`src/acp/scheduler/remote/runner.py`（_prepare_and_submit 738-835 内 build_lsf_script_spec 调用 797-808）；`tests/test_remote_phase2.py`（derive_lsf_resources/generate_lsf_script 锁定测试）。
  - **任务内容**: submit 路径解析 `queue = target.queue or config.queue`（runner 已有 target_node 作用域）传入 build_lsf_script_spec；script_gen 仅消费传入 queue，不改 generate 逻辑；`queue=None` 时与现状字节一致（测试锁）。
  - **Acceptance**: 构造 node.queue="bigmem" → 脚本含 `#BSUB -q bigmem`；node.queue=None → 含 config.queue；`node.queue is None` 且 config.queue 相同时输出与改动前测试 fixture 字节一致（test_remote_phase2 全绿）。
  - **QA happy**: `pytest tests/test_remote_phase2.py -m "not slow"` 绿。
  - **QA failure**: 改 script_gen 默认逻辑 → phase2 字节测试红（禁止改动 generate_lsf_script 输出默认）。
  - **Commit**: `feat(scheduler): honor per-node queue override in LSF script generation`

### W3 — API / 持久化

- [x] 8. migration 013（jobs.node_id/host）+ 写入 + tasks/task.json 真实节点名 + v1 模型提升
  - **References**: `src/acp/scheduler/migrations.py`（_MIGRATIONS 结构, tasks 表 142-166, 010/011 现状）；`src/acp/scheduler/store.py`（jobs 表 27-49, _record_to_row/_row_to_record 428-455）；`src/acp/scheduler/manager.py`（_record_execution_target 1814-1828, remote submit 后置 sync 调用点 530-534）；`src/acp/scheduler/tasks.py`（sync_from_job 199-231）；`src/acp/scheduler/remote/runner.py`（on-disk task.json 写入 462-479）；`src/acp/api/v1_schemas.py`（V1JobRecordModel 242-272）；`src/acp/api/v1_routes.py`（_record_to_v1_model 410-451）；`src/acp/api/v2_routes.py`（83 已 lift，对齐复核）；`tests/test_f4_scope_audit.py`。
  - **任务内容**: migration 追加 013: jobs +node_id TEXT +host TEXT（NULL，不回填，不重排 _MIGRATIONS）。_record_execution_target 后写 node_id=target.name/host；local → node_id="local"。tasks.sync_from_job: node_id 写真实节点名（result.node 或 execution_target；**缺省回退链: remote_job_id 存在 → "remote"，否则 "local"** — 使 test_acp_task_index.py::test_sync_from_job_remote_maps_sftp 保持绿；若移除回退链须同 commit 更新该测试并将其列入锁定清单），storage_mode 不变；remote submit 完成后补一次 sync（异步提交时序修正）。on-disk task.json 增加 node_id 字段（runner.py 写入点）。V1JobRecordModel +node_id/host 顶层；_record_to_v1_model 提升（列值 → result → spec.target_node → null 回退链）。v2_routes.py:83 复核一致。
  - **Acceptance**: 新 DB 迁移后列存在；历史行 NULL 且读端回退（seed 旧行无列值 + result.node="comp-01" → API 顶层 node_id="comp-01"）；mock 远程提交后 tasks.node_id == 真实节点名且 task.json 含 node_id；`tests/test_acp_task_index.py` 全绿（回退链语义保持）；`test_f4_scope_audit` 绿；spec_json round-trip（node_tags 缺键容忍）测试绿。
  - **QA happy**: `pytest tests/test_f4_scope_audit.py tests/test_acp_scheduler.py tests/test_remote_phase1.py -m "not slow"` 绿。
  - **QA failure**: 去掉读端回退 → 历史行断言红。
  - **Commit**: `feat(scheduler): persist chosen node on jobs/tasks and lift node fields to v1 API`

- [x] 9. POST /api/v1/nodes/matching 预览端点
  - **References**: `src/acp/api/v1_routes.py`（路由注册区, /nodes 现有端点 4164-4203 风格）；`src/acp/api/v1_schemas.py`（新请求/响应模型）；`src/acp/scheduler/capabilities.py`（T3 产物: derive/matches/is_degraded）；`src/acp/scheduler/manager.py` 或 router 侧对 registry/NodeManager 的访问（现 /nodes 端点如何取 NodeManager 照抄）；`src/acp/scheduler/remote/node_manager.py`（NodeStatus/缓存）。
  - **任务内容**: `POST /api/v1/nodes/matching`，请求 `{workflow, method: {...}, protocol?: str, node_tags: list[str]}`（node_tags 键必含可空）；响应 `{required_software, node_tags, local_satisfies, nodes: [...], note}`，nodes 每项含 name/host/status/running_jobs/max_jobs/disk_usage_pct/queue/**tags（声明 tags，D13；未声明 → [] — 前端 chips 唯一数据源）**/capability_state/declared_ok/mismatch/satisfies/missing_software/missing_tags/reasons + degraded 标记（is_degraded 单实现）；satisfies = 匹配集成员 ∧ status != offline；容量/degraded 不进 satisfies；零远程节点 → nodes=[] + note。复用 NodeManager 30s 缓存，不强制 SSH 刷新。错误: workflow 未知 → 400；derive 抛异常 → 500 记日志。
  - **Acceptance**: 构造多节点 mock → 响应 satisfies/missing 断言（declared 缺 crest → missing_software=["crest"]; unknown 节点软件满足；勾 gpu 标签未声明节点 satisfies=false）；local_satisfies 反映本机解析；零节点 → nodes=[] + note 非空；请求缺 node_tags 键 → 400（模型必填 — 注意 FastAPI pydantic 必填字段缺省返回 422，需显式校验或模型层处理以命中 400 断言）。
  - **QA happy**: `pytest tests/test_acp_api_v1.py -k matching -m "not slow"` 绿 + curl 证据。
  - **QA failure**: matches 改动后 preview 与提交路径不一致 → 断言响应与 derive/matches 直调结果相等（同实现保证）。
  - **Commit**: `feat(api): node matching preview endpoint for submission-time selection`

- [x] 10. v2 batch 字段透传（execution_mode/target_node/node_tags）
  - **References**: `src/acp/api/v2_routes.py`（_submit_batch_item 283-293, V2TaskBatchItem 定义处）；`src/acp/api/v2_schemas.py`（如有）；`src/acp/scheduler/jobs.py`（JobSpec 字段）；T5 校验 helper。
  - **任务内容**: V2TaskBatchItem + execution_mode/target_node/node_tags（纯增量，缺省 None/[]）；_submit_batch_item 构建 JobSpec 透传；创建时校验 helper 同 v1 应用（**同错误码与 missing_* 字段；v2 不返回整体 HTTP 400，校验失败的 item 进入 failed[]（per-item 200 契约），见 Acceptance**）。
  - **Acceptance**: v2 batch 提交带 target_node 合法 → 落 spec；带未知/能力不满足 → **不返回整体 HTTP 400**（保持 v2 create_task_batch per-item 200 契约，test_v2_batch_partial_failure 语义不变），该 item 进入 failed[] 且消息含 code=unknown_target_node/target_node_incapable + missing_* 文本；其余 items 正常创建；不带字段 → 行为与改动前一致（现有 v2 batch 测试全绿）。
  - **QA happy**: 现有 v2 相关测试 + 新增透传用例 `pytest tests/ -k "v2 or batch" -m "not slow"` 绿。
  - **QA failure**: 透传缺失断言（spec.target_node == 请求值）红。
  - **Commit**: `feat(api): pass node selection fields through v2 batch submission`

### W4 — 前端

- [x] 11. 前端节点选择器 UI（三态下拉 + 标签 chips + 徽标 + 置灰原因 + i18n + 零节点提示）
  - **References**: `frontend/ACP_Workbench_v2.html`（提交模态资源设置区 3844-3860, .node-select CSS 1699/1714 已定义未用, loadNodes 14550-14564, renderNodePanel 14566-14610, submitJobModal 18614, workflow/method 防抖变更点 16591-16789, i18n zh ~5158/en ~5957, docs/remote_execution_plan.txt Phase 6.4 规范 2335-2359）；`POST /api/v1/nodes/matching`（T9）。
  - **任务内容**: 资源区新增"计算节点"区块: 下拉 `自动选择(跟随服务器默认)` / `本地` / 每节点条目（name+状态灯+running/max+queue）; 数据流: workflow/method/profile/tags 变更防抖 → POST /nodes/matching → 渲染（**不复制推导逻辑**, 只消费响应）; 条目态: satisfies=false 或 offline → disabled + title 原因（缺软件 X/缺标签 Y/离线）；degraded → 可选 + 徽标"当前满载/磁盘告警"；capability_state 徽标（探测推断/能力未知按通用）；declared_ok=false → 黄色不一致徽标；选"本地"时节点下拉禁用（防矛盾组合）；零远程节点 → 仅 自动+本地 + note 文案。i18n zh/en（nodes.* 键）; 错误码映射表（D12: no_capable_node/target_node_incapable 等 code → 文案）。matching 响应 local_satisfies=false 且需求非空时自动选项副文案"将选择可用远程节点"。
  - **Acceptance**: 手动 QA（浏览器 devtools 断言 DOM）: 节点列表项与 matching 响应一致; 不满足节点 option.disabled=true 且 title 含原因; 默认选中"自动选择"; 选本地后节点下拉禁用; 空响应提示可见; zh/en 切换文案存在。新增 DOM 结构断言脚本（可被 T12 的 test_frontend_sync 引用或独立 test 读取 HTML 片段）。
  - **QA happy**: 起 serve + mock matching（或真实 /nodes）+ 打开 Workbench 检查（playwright/手动截图存档）。
  - **QA failure**: 移除 disabled 逻辑 → DOM 断言红（测试锁）。
  - **Commit**: `feat(frontend): node selector with capability filtering in submit wizard`

- [x] 12. 前端载荷 + stage 预选 + continue 改选控件 + test_frontend_sync 新锁
  - **References**: `frontend/ACP_Workbench_v2.html`（submitJobModal 18614, NMR 分支 18733-18762, mechanism 18861-18883, general 19027-19050, project_id/task_name 追加 19043-19044, stage 向导, detail 恢复区 continue 按钮, selectedJobIsRemote/lastRemoteNode 4360-4361,11563-11579,11956-12007, i18n）；`tests/test_frontend_sync.py`；T6（continue 覆盖后端）、T11（选择器）。
  - **任务内容**: 三个提交支路（NMR/mechanism/general）+ v2 batch（若前端使用）载荷加 target_node（选中节点时）/ node_tags（勾选时），自动选择省略两者; stage 向导显示源节点并预选（读源 job 显式 target_node 或 result 展示; 载荷仅发用户选择）; job detail 恢复区 continue 增加"继续到节点"选择（缺省回源, 空白=回源）→ POST continue 带 target_node; Node 展示改读顶层 node_id/host（T8 后字段）。test_frontend_sync.py 新锁: 提交模态含节点 select id; 无硬编码节点名; nodes.* i18n 键 zh/en 齐; 载荷构造函数含 target_node/node_tags 键（HTML 源码断言 + 现有 catalog 驱动模式同风格）。
  - **Acceptance**: test_frontend_sync 新断言绿; HTML 载荷函数源码含 target_node/node_tags; continue 控件存在且缺省不发覆盖字段; 无硬编码节点名字面量（"comp-01" 之类仅测试桩允许）。
  - **QA happy**: `pytest tests/test_frontend_sync.py -m "not slow"` 绿 + 手动提交流程（选节点 → job detail 显示该节点）。
  - **QA failure**: 删除载荷键 → 源码断言红。
  - **Commit**: `feat(frontend): submit payloads, stage preselect, and continue node override`

### W5 — 收尾

- [x] 13. 文档修订（remote_execution_plan Phase 6 状态 + Phase 8 能力过滤 + 技术债记录）
  - **References**: `docs/remote_execution_plan.txt`（Phase 6 2232-2361 标注"✓ 完成"需修正, Phase 7 后追加 Phase 8）; `config/defaults.yaml`（T1 已改注释示例, 复核）; 本计划 Scope/Key Decisions。
  - **任务内容**: Phase 6 状态改为真实状态（本次实现补齐）; 新增 Phase 8 章节: 能力声明模型（D8/D13）、推导表（指向 capabilities.py 与测试）、auto 语义（D14）、错误码契约（D12）、queue 非过滤轴说明、兼容策略; 记录技术债: WORKFLOW_CATALOG.requires_binaries 漂移待后续收敛; 同 host 多逻辑节点计数说明。
  - **Acceptance**: 文档包含上述小节且与实现一致（实现完成后编写, 防空写）; 文档内示例 YAML 与 T1 一致。
  - **QA happy**: 人工核对文档断言 + grep 关键术语存在。
  - **QA failure**: 文档无 Phase 8 或示例过期 → 检查清单红。
  - **Commit**: `docs: node selection capability model (plan phase 6 status + phase 8)`

- [x] 15. [发现缺口 T12] v1 create 透传 node_tags（V1JobCreateRequest/V1JobSpecModel + create_job → JobSpec）+ 测试
  - **任务内容**: v1 单任务提交目前静默丢弃 node_tags（V1JobCreateRequest 无该字段, pydantic extra=ignore）— 前端已发送（T12）, v2 batch 已透传（T10）, 唯 v1 缺失, 导致 v1 任务的标签约束 (D13/D14 required_tags) 不生效。修复: `src/acp/api/v1_schemas.py` V1JobSpecModel + V1JobCreateRequest 加 `node_tags: list[str] = []`; `src/acp/api/v1_routes.py` create_job 构建 JobSpec 时透传 `node_tags=req.node_tags`; 测试: POST /api/v1/jobs 带 node_tags → 落 spec; 不带 → 默认 []。回归 v1 create 相关测试 + test_frontend_sync 载荷锁仍绿。
  - **Commit**: `feat(api): thread node_tags through v1 job creation`

- [x] 14. 全量回归 + 修复
  - **References**: AGENTS.md CI 段（py3.10-3.12, `pytest -m "not slow"`）; 全量 `tests/`。
  - **任务内容**: `pytest -m "not slow"` 全量绿（含新增 test_node_capabilities/test_capabilities 与全部锁定测试）; 若有红 → 修复到绿（修复不得放宽断言）; 快速冒烟: `acp run serve` 起服 + /api/v1/nodes + matching 端点 curl 通（无真实节点配置下 nodes=[] + note 路径）。
  - **Acceptance**: `pytest -m "not slow" -x` 零失败退出码 0（记录输出路径）; compileall 通过。
  - **QA happy**: pytest 全绿 + 冒烟 curl 证据。
  - **QA failure**: 任一既有测试因本次改动变红 → 必须修复（除非该测试属 T4/T5 明确列出的语义更新项且已同 commit 更新）。
  - **Commit**: `test: full regression green for node selection feature`

## Final verification wave

（并行执行, 全部 APPROVE 才交付; 若任一 REJECT → 修复后重跑该任务）

- [x] F1. Plan compliance audit
  - 逐 todo 核对实现与计划一致性（文件、字段、行为、测试名）; 无遗漏 todo; 无 Scope OUT 项被实现（特别是: 版本过滤/extra_flags/-m pin/requires_binaries 重写/服务端中文错误/v0 legacy 字段）。
- [x] F2. Code quality review
  - ruff check src tests（或至少新增文件无 E/F/I 错误）; 类型注解/日志/路径规范符合 AGENTS.md; 无 `except Exception` 新引入（在新增/修改行内）; 错误消息英文 + code 字段; 无绕过 frames/布局等既有约定。
- [x] F3. Real manual QA
  - 起 `acp run serve`（可 mock 远程节点或临时配置测试节点）: 提交向导节点下拉渲染与置灰; 提交流程端到端（选节点/自动）; job detail 节点展示; continue 改选; 零节点配置路径。截图/日志存档。
- [x] F4. Scope fidelity
  - 与用户需求（D1–D15）逐条对照终态行为; 无用户未要求的附加行为改变（UI 其余部分回归目检）。

## Commit strategy

- 每个 todo 一个 commit（或一组语义相关 commit），消息前缀见各 todo Commit 行; W1→W5 顺序提交。
- T4 与 test_registry_nodespec_field_subtraction 更新、T5 与 401-419 测试更新必须**同 commit**（CI 红锁）。
- 前端（T11/T12）与后端（W1-W3）同仓库单页文件, 按 todo 顺序提交; 不拆 feature branch 要求（/start-work 默认分支或用户指定）。
- 每 commit 后跑对应 todo 验收命令再进入下一 todo。

## Success criteria

（特性级终态验收, 全部为真即特性完成）

1. 任一任务提交时，若指定 target_node: 未知/禁用/能力不满足 → **v1 创建即 HTTP 400**（code=unknown_target_node/target_node_incapable + missing_*）；**v2 batch 不返回整体 400，校验失败的 item 进入 failed[] 且消息含同 code（per-item 200 契约，T10）**；调度时同规则仍作后盾。
2. 未指定时 auto 行为: 推导需求空 → 服务器默认; 需求非空且本机满足 → local; 否则自动选到能力匹配远程节点（affinity 优先 → least-loaded）; 匹配集为空 → 创建即 400 no_capable_node（**v1；v2 相应 item 进入 failed[] 含同 code**）; 匹配非空但全满载/degraded/offline → 创建成功, 调度 60s 重试等容量。
3. 能力判定与设计 D8/D13 一致: declared 权威 / probe-inferred 探测集 / unknown 软件回退通用; 未声明节点恒不满足标签; queue 不参与匹配。
4. continue/rerun/stage 链默认回源节点（execution_target）, continue 支持显式改选; 预留计数并发安全且重启重建（软上限）。
5. per-node queue 覆盖生效, None 时 LSF 脚本输出与改动前字节一致。
6. jobs.node_id/host 列存在, 历史行读端回退; tasks.node_id 与 on-disk task.json 记录真实节点名; v1/v2 API 顶层暴露 node_id。
7. `/api/v1/nodes/matching` 返回 required_software/local_satisfies/逐节点 satisfies+missing+reasons, 与提交期判定同一实现。
8. 前端向导出现节点选择（自动/本地/节点列表 + 置灰原因 + 徽标 + 标签 chips）, 载荷携带选择, 无硬编码节点名, zh/en 齐; 零远程节点优雅降级。
9. `pytest -m "not slow"` 全绿（含全部新测试与更新后的锁定测试）; 文档 Phase 6 状态已修正并含 Phase 8。
