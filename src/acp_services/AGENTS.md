# acp_services/ — Platform Services Layer

> **2026-10-29 historical audit; 2026-10-30 scoped update:** [functional audit](../../reports/acp2_function_audit_20261028/REPORT.md) and [status reconciliation](../../docs/reference/current/ACP_RPH_Status_20261028.md). Research writer/service/Saga, step-definition/dispatch/worker, fenced checkpoints, model gateway and deadline enforcement are present. Deadline/queue termination is enforced in current code; historical NOT_VERIFIED notes are not its current declaration. DA science closure, installed RPH real-chain acceptance and current BOM retrievability must be checked separately; default product assembly now exists in code. Never infer an executed feature from a module name or reset a frozen evidence status.

## MCP 消费与自动执行兼容约定（2026-10-30）

先读[统一计划](../../docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md) §3/5/7/8。指南、范围授权和自动发布是待实现交付；当前代码能力须逐项核对。

- MCP 可见性由 `mcp.py::visible_tool_names` 和实际后端装配决定：8 基础、协商后 10 研究、装配 consumption 时另有当前 11 消费操作。不要把历史 18 工具断言用于所有新会话；新增 wire/schema 有显式协商和兼容检查。
- Services 只经注入端口调用 ACP 编排/候选服务，禁止反向导入；Controller 为唯一任务事实权威，worker/harness 不写第二任务库。
- 全自动允许在平台验证的范围内连续发布和提交，无需每步人工确认；Agent 不能自授权限。现有逐 plan_digest approval 不可当成任意计划通行证；范围授权到逐计划决定的扩展必须显式实现、绑定和留审计。
- 自动发布必须保存真实 selector/actor、授权依据、source attempt/工件版本与 selection revision。旧 manual 来源不改写；PES/scan 的 writer/adapter 与 Batch loader 同批兼容，不以单独放宽 allowlist 实现全自动。
- 新候选/补算/重试上限服务端持久化；恢复不能清零。累计 token/core-hours 硬限制仍 DEFERRED。来源变更重新绑定，回包丢失先查原幂等记录；继续使用 canonical_digest 唯一实现。
- 产物读取保留完整正文内容检查、编码与摘要作用域；只读状态/发现不得隐式远端 fetch。Prompt 不替代身份、预算、科学证据或缺失的 MCP 消费接口。

## OVERVIEW
Platform service layer for ACP: workspace/access boundary, Data prepare/read, Runtime use cases, persistent store/schema, contracts, and thin entrypoints. Dependency direction is **one-way**: `acp → acp_services`; `src/acp_services/**` must never import `acp` (including lazy imports, `TYPE_CHECKING`, `importlib`, `__import__`, string `getattr`). **128 `.py`** (121 implementation modules + 7 `__init__.py`); Python 3.10+; PEP 604 annotations. The ACP 2.0 capability families (research budget/approval/events, adapters, attempt chain, science binding/step DAG, MCP entrypoints) are implemented natively here.

**Historical slice status (2026-10-26):** legacy module/contract slices plus the ACP 2.0 capability slices are delivered and covered by guard families — `test_acp_services_isolation.py`, `test_services_worker_contract.py` and `test_services_{attempt_recovery,cgroup_attempt_recovery,standalone_recipe,packaging_roles,release_prep,worker_bootstrap,doc_sync,closure_record}.py`. The cited closure record predates the M4 real model/QC/LSF/browser receipts and M5-03 independent install. Read the current audit for their separate scope; production and full-release sign-off remain unverified. `acp` old import paths are retained as identity forwarders — never re-add an implementation on the `acp` side.

## STRUCTURE
```
acp_services/                             # 128 .py
├── bootstrap.py          # ServicesPorts frozen port bundle (10 fields; "port convergence" phase; each port has a migration/removal task)
├── worker_bootstrap.py   # self-sufficient worker composition root (`python -m acp_services.worker_bootstrap`; also the `acp-worker` console script; zero acp import / zero jobs DB)
├── events.py             # pure re-export shim → contracts.events (frozen EventEnvelope/EventCursor/EventScope allowlist)
├── workspace/            # 10 modules: projects, admin, entry_authz, identity_lifecycle, identity_mapping, keys, boundary,
│                         #   http_identity (B15 HTTP credential→principal), server_identity (B09 authoritative principal/project),
│                         #   command_identity (M0-04 one frozen CommandIdentity for HTTP/CLI/admission + idempotency scope)
├── data/                 # 13 modules: contracts, operations (prepare), artifacts (B12 artifact lifecycle + ArtifactRef), transfer,
│                         #   resource_read, read_root, storage_binding, namespaces, archive, import_, budget (storage reservation),
│                         #   research_resources (B18 Proposal/ResearchRecord CAS), helpers
├── runtime/              # 70 implementation modules + __init__.py: commands, execution_chain, worker, receipts, recovery, plan_contracts, step_result,
│                         #   step_requirements, admission, quota, usage, usage_units, metering_policy, metering_decoupling,
│                         #   freshness, projections, events, outbox, provisioning, workflow_enablement, isolation,
│                         #   lab_capabilities, lineage, helpers, method_flags, research_budget (B17), research_events (B19),
│                         #   research_session (M1-02 planning claim / submit intent / crash-point recovery),
│                         #   approvals (B16), canonical_digest, attempt_recovery (B13), cgroup, attempt_freeze,
│                         #   attempt_completion (B07-A), execution_right + receipt_identity + errors (D02), step_dag (B18b),
│                         #   science_binding (B18a), capability_registry (B22), adapter_registry + adapter_bootstrap (B21),
│                         #   analysis_capabilities (B06/B12), idempotency (B08), local_admission (B09),
│                         #   usage_authority (M0-07 producer capability authority), research_session (M1-02 identity/claim/receipt/intent/crash-matrix),
│                         #   evidence_projection (M1-03 唯一平台投影/TaskRef 归一/ResearchDecision), science_inputs + digest_domains + submit_policy +
│                         #   execution_limits + planning_reservation (M1-04 a/b/c)
├── store/                # 8 modules: store (JobStore CAS), migrations (26, latest id 027), record (v2 TaskRecord §9.1), records, layout,
│                         #   naming, legacy_method, molecule_groups
├── contracts/            # 8 modules: access, envelope, events, config_plan, config_validation, action_gating, injection,
│                         #   adapters (B21 infra-adapter contract)
└── entrypoints/          # 10 .py: diagnostics, machine_output, mcp (9 base incl. acp_get_guidance + 10 negotiated research tools), mcp_stdio,
                          #   application_params + status_projection (B06), controller_client (B04a),
                          #   research_port + wire_identifiers (M1-01c 唯一经审查 wire-id 常量驻点；ANTI #29 例外范围)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Port bundle / transitional assembly | `bootstrap.py::ServicesPorts` | Still the assembly root but **narrowed**: docstring marks the "port convergence" (端口收敛) phase; 10 fields remain — `tasks`, `task_views`, `control`, `planning`, `publication`, `catalog`, `remote_paths`, `remote_release`, `remote_submission`, `projects`. `JobStorePort`/`MigrationsPort`/`StepRequirementPort` are no longer fields (moved in place). ACP 2.0 capabilities are native, not injected ports. ACP-side consumer bootstraps (6): `acp/services_{,completion_,data_,events_,mcp_,publication_}bootstrap.py` |
| Worker composition root | `worker_bootstrap.py` | Builds from frozen envelope/plan/binding/artifacts only; writes one receipt; never touches jobs DB. Composes `FrozenWorker` + Services-native `step_requirements` + `cccp.calculation.run_*`. Also the `acp-worker` console-script target (`acp_services.worker_bootstrap:main`, `packaging/roles/worker/pyproject.toml`) |
| Store / CAS | `store/store.py`, `store/record.py` | `JobStore` (incl. `claim_idempotency`), `JobStateConflictError`, `CursorSnapshot`; `record.py` = v2 server-side `TaskRecord` (§9.1). Implementation root of `acp.scheduler.store` forwarder |
| Schema migrations | `store/migrations.py` | `migrate`, `get_schema_version`; **26 jobs migrations, latest id `027`** (`022` idempotency ledger, `024` identity versioning, `025` usage capabilities, `027` admission v2 indexes; ids are not contiguous). Implementation root of `acp.scheduler.migrations` forwarder |
| Plan/step contracts | `runtime/plan_contracts.py`, `runtime/step_result.py`, `runtime/step_requirements.py` | Identity-forwarded from `acp.calculations.{contracts,step_result,step_requirements}` |
| Runtime use cases | `runtime/commands.py` | submit/pause/unpause/cancel/continue/rerun/edit/purge + CLI completion registration via injected ports |
| Execution entry selection | `runtime/execution_chain.py` | Eight catalog workflows have integrated scientific closure, seven retain legacy closure; this is not default entry activation. Default envelope = singlepoint + internal BoundedRecipe only; all other ordinary submissions still use their registered legacy path |
| Data prepare/read | `data/operations.py`, `data/artifacts.py`, `data/transfer.py`, `data/resource_read.py`, `data/read_root.py` | Persistent prepare operations, verified transfer, cache-only reads; `artifacts.py` (B12) = explicit artifact prepare/read lifecycle + frozen `ArtifactRef(job_id, attempt, resource_id, generation, digest)` reusing `PrepareOperationService` rows (no second state machine); old GET remains compat |
| Research resources (Data) | `data/research_resources.py` | B18 versioned JSON `Proposal`/`ResearchRecord` CAS store in the Data boundary; opaque JSON — no decision semantics, no task table |
| Storage binding / namespace | `data/storage_binding.py`, `data/namespaces.py` | Single allocation authority; atomic idempotent namespace |
| Storage budget | `data/budget.py` | B02-7 reservation for bytes/inodes/scratch/upload amplification — **distinct** from `runtime/research_budget.py` |
| Events / outbox | `runtime/events.py`, `runtime/outbox.py`, `events.py` | Persistent event protocol; per-task JSONL reuse; `events.py` = pure re-export shim of the frozen `EventEnvelope`/`EventCursor`/`EventScope` allowlist (implementation in `contracts/events.py`) |
| Research events + cursor | `runtime/research_events.py` | B19 cross-session events + opaque time-bounded cursor (expiry = explicit snapshot reset, never a silent gap) + write-once terminal projection + detach/claim/action ledger |
| Research session / submit intent | `runtime/research_session.py` | M1-02: `ResearchIdentity.allocate` (identity before the first plan), `planning_claim` (D-IDENT-04 `plan|...`) + `model_receipt_id` (D-IDENT-05 `modelrcpt|...`) reusing `canonical_digest`, opaque `SubmitIntent` (D-IDENT-07) with `supersede_approval_for_plan_change` (old approval revoked via CAS, new round/revision/approval intent, no execution fact), and the frozen `CRASH_POINT_RULES` + `recover_research_session` over the B19 coordinator (never `JobManager.resume()`) |
| Research budget | `runtime/research_budget.py`, `runtime/usage_units.py`, `runtime/local_admission.py` | B17 persistent cumulative budget vocabulary (`BudgetReservation/Decision/Settlement/Release/Projection`); identity = project + `proposal_ref`, survives retry/restart and is server-derived (client cannot pick the scope); ledger = `admission.ReservationLedger`; `usage_units.py` = dependency-free counters shared by usage/budget/admission; `local_admission.py` (B09) = local-single admission composition over the same ledger |
| Approvals | `runtime/approvals.py` | B16 Proposal/ResearchRecord approvals bound to the frozen plan via `plan_digest` + `proposal_digest`; `draft → pending → approved / rejected → revoked / expired`; one optimistic CAS on `expected_revision`; never writes execution facts |
| Canonical digest | `runtime/canonical_digest.py` | The ONE canonical-digest implementation for the frozen B03 rules/vectors; B18 imports it and never re-implements |
| Attempt chain | `runtime/attempt_freeze.py`, `runtime/attempt_completion.py`, `runtime/execution_right.py`, `runtime/receipt_identity.py`, `runtime/errors.py` | `attempt_freeze` produces `WORK/00_RUNTIME/frozen/` + `frozen_group.json` sha256 manifest + persistent execution claim; `attempt_completion` (B07-A) = controller-side receipt gate → terminal CAS → publication handoff (`AttemptSnapshot`); `execution_right` (D02) = exclusive `(job, attempt)` receipt-write right fenced at the worker seam; `receipt_identity` = single receipt↔envelope identity predicate; `errors.py` = neutral `WorkerError` base that breaks the receipts↔worker cycle |
| Local attempt recovery | `runtime/attempt_recovery.py`, `runtime/cgroup.py` | B13 cgroup v2 delegated-root kernel reconciliation for accidental local worker death; refuses without boundary proof; process containment, **not** a hostile-code sandbox. `cgroup.py` = dependency-free cgroup v2 resource plans shared by isolation + recovery |
| Science binding / step DAG | `runtime/science_binding.py`, `runtime/step_dag.py` | B18a reads published recipe/evidence schemas by id+digest and converts C's P2 minimal form → `cccp.calculation.requests.TaskRequest` (never guesses science defaults); B18b = one platform task with bounded persistent science steps, single loop, science injected from `cccp.calculation.run_*` |
| Science capability admission | `runtime/capability_registry.py` | B22 third-party science admission: discover `acp2.science.capability` v1, allow-list trust, freeze snapshot per attempt |
| Infra adapters | `runtime/adapter_registry.py`, `runtime/adapter_bootstrap.py`, `contracts/adapters.py` | B21 registry is the ONLY infra-adapter authority (discover/authorize/freeze-per-task); never imports `acp`, never executes on discover. `adapter_bootstrap` composes from `ACP_ADAPTER_PLUGIN_ROOT`/trusted-plugin env (no Controller/subprocess). `contracts/adapters.py` = frozen inert `AdapterDescriptor`, explicitly NOT a science plugin |
| Analysis capabilities | `runtime/analysis_capabilities.py` | B06/B12 registry-gated explicit `POST analyze`; unregistered capability → `unsupported_capability` |
| Idempotency | `runtime/idempotency.py` | B08 persistent submission-idempotency types; ledger is `JobStore.claim_idempotency` (migration `022`, identity versioning `024`). M0-04: the scope id is built by `workspace/command_identity.CommandIdentity` (verified principal), `IdempotencyContext.principal_id`/`key_id` carry the attribution, and pre-M0-04 rows keep `identity_version=1` with a `legacy-*` `migration_state` (unattributable rows are a typed conflict, never a replay). **M0-06**: `REQUEST_DIGEST_VERSION="2"` over the canonical effective submit request; `DIGEST_FIELD_TABLES` freezes each rule's field table, `canonical_request_digest_for_version`/`verify_request_digest` verify a pre-bump row against its own table, and `JobStore.claim_idempotency(request_payload=…)` replays on equal digest bytes / reconciles on an unverifiable difference (never "tampered", never a blind resubmit) |
| Metering decoupling | `runtime/metering_decoupling.py` | B10-4: accounting never blocks science / science completion never gates usage |
| Access / identity | `workspace/{entry_authz,identity_lifecycle,identity_mapping,keys,boundary,http_identity,server_identity,command_identity}.py` | Default is still `local-single`, but multi-user HTTP authz **exists server-side and is opt-in** (`http_identity.IDENTITY_MODE_ENV = "ACP_IDENTITY_MODE"`): `http_identity.py` (B15) binds credential→principal via `ApiKeyManager` verify → `IdentityLifecycle` status → `EntryAuthorizer` project scope ∩ key action ceiling. `server_identity.py` (B09) supplies the authoritative principal/project for runtime entries (client `tenant_id` ignored; wraps local identity + `LocalPrincipal`). `command_identity.py` (M0-04) is the single frozen per-command identity projection (`from_verified_http`/`from_server`/`from_local` + `idempotency_scope`); `IdempotencyContext.principal_id`/`key_id` and `LocalAdmission.request_for_spec`/`admit_job` carry it, and `LOCAL_PRINCIPAL_ID` is legal only on the explicit local-operator path. OIDC remains contract-only: `OIDC_SUPPORTED = False`, no network login/redirect/token validation |
| Admin | `workspace/admin.py` | Per-entry re-authorization + audit + restricted broker |
| Admission / quota / usage | `runtime/{admission,quota,usage,metering_policy}.py` | Module and actual wiring have M4/M5 scoped receipts; current release/DA/credential gaps stay explicit. **M0-07 (G26)**: `admission.py::record_usage_once` is the single settlement wiring point (server-resolved producer capability via `runtime/usage_authority.py`, historical attempt receipts verified against the attempt's immutable binding — booking only, never state advancement; usage + budget settle in one transaction, idempotent by `receipt_id`; admission schema 026; identity DB migration 025 adds `api_keys.usage_capabilities`) |
| Contracts | `contracts/{access,envelope,events,config_plan,config_validation,action_gating,injection,adapters}.py` | Frozen data classes; no `acp.*` in public signatures |
| Entrypoints | `entrypoints/{diagnostics,machine_output,mcp,mcp_stdio,application_params,status_projection,controller_client}.py` | Thin. `mcp_stdio.py` (B05) = real NDJSON JSON-RPC 2.0 framing (protocol stays in `mcp.py`), bound via `acp mcp serve` → `acp.services_mcp_bootstrap`; `application_params.py` (B06) = single param normaliser for MCP/HTTP/controller (stdlib-only); **M0-06** adds the exhaustive field policy (`SUBMIT_/PREFLIGHT_/PREPARE_FIELD_POLICY`, `FIELD_POLICY`, empty `LEGACY_REQUEST_FIELD_ALLOWLIST`, `validate_request_fields`, `canonical_submit_request`, `normalize_node_tags`) — one authority for field meaning/entry/rejection (`unknown_field` → 422, `unsupported_server_path`); `status_projection.py` (B06) = one task/operation status projection for CLI+HTTP+MCP; `controller_client.py` (B04a) = loopback Controller client (stdlib urllib only, no `acp`, no jobs DB) |
| MCP tool allowlist | `entrypoints/mcp.py::TOOL_ALLOWLIST` | 9 base tools — `acp_capabilities`, `acp_preflight`, `acp_submit`, `acp_status`, `acp_cancel`, `acp_prepare_artifact`, `acp_read_artifact`, `acp_analyze`, `acp_get_guidance`（2026-10-30 指南批，只读，→ `GET /api/v1/guidance/{guide_id}`）— each 1:1 with a Controller HTTP command; RESEARCH_TOOL_NAMES adds 10 proposal/approval/wait/snapshot/resume tools after acp.research.v1 negotiation; `research_consumption.py::CONSUMPTION_TOOL_NAMES` adds consumption operations when its backend is composed |
| ACP-side API consumers | `acp/api/{v2_research,v2_approvals,v2_budgets}.py`, `acp/api/v1_routes.py` | `v2_research` → `data/research_resources.ResearchResourceStore`; `v2_approvals` → `runtime/approvals` (+ `canonical_digest`); `v2_budgets` → admission ledger budget projection/evidence/settlement (`runtime/research_budget` vocabulary); POST analyze → `runtime/analysis_capabilities` |
| Method flags | `runtime/method_flags.py` | Implementation root of `acp.catalog.method_levels_to_cli_flags` |

## CONVENTIONS
- **One-way dependency**: never import `acp` from this package (AST guard `tests/test_acp_services_isolation.py`; worker guard `tests/test_services_worker_contract.py`).
- **Single implementation root**: `acp` old paths must be identity forwarders (`is` same object). Do not re-add implementations or business logic there.
- **Frozen contracts first**: `contracts/**` and `runtime/plan_contracts.py` are `@dataclass(frozen=True)`; extend additively, never fork a second DTO.
- **One canonical digest**: `runtime/canonical_digest.py` is the only implementation of the frozen B03 rules/vectors — import it, never re-implement it or re-derive a private variant.
- **Server-fixed research-budget identity**: `project + proposal_ref`, derived from the B16 approval; retry/restart/replay inherits the remaining batch budget, and a client can never choose the scope.
- **Approvals are intent, not execution**: `runtime/approvals.py` never writes a job row, never adds a `JobStatus`, never reuses `WAITING_REVIEW`; after submit it may record a `TaskRef` as evidence only.
- **Adapter discovery never executes**: `adapter_registry` is the only infrastructure-adapter authority — no import/exec of entrypoints on discover/install/enable, no Controller start; `contracts/adapters.py` is infrastructure-only. Science admission is a different authority (`runtime/capability_registry.py`).
- **Exclusive receipt right**: exactly one worker per frozen `(job, attempt)` may write the authoritative receipt (`runtime/execution_right.py`); receipt↔envelope identity only via `runtime/receipt_identity.py`.
- **Explicit injection**: `ServicesPorts` is a frozen bundle, no global mutable singleton, no dynamic lookup.
- **No service restart from docs**: deployment is generated by `scripts/install_systemd.sh`; actual release/restart is todo 76 (gated).
- **Evidence**: update `tests/baseline/services-evidence/migration_ledger.md` (6 columns, status token in the exit-condition cell) and `public_shim_inventory.md` when adding/retaining a public surface; the directory holds 20 files (also `public_allowlist.md`, `single_writer_map.md`, `lineage_field_map.md`, `isolation_matrix.md`, `packaging_rollout_receipts.md`, `wheel_namelist_manifest.json`).

## ANTI-PATTERNS
- **`acp_services → acp` import** — hard fail, including lazy/`TYPE_CHECKING`/string reflection.
- **Second state machine** — control intent stays with `JobManager`; Runtime use cases delegate through ports exactly once.
- **DB access from worker** — `worker_bootstrap` must run with an empty `ACP_RUN_ROOT` and create no DB file.
- **Mock as real** — `PASS` at module level is not real-science/browser/LSF/production evidence; keep `NOT_VERIFIED` explicit.
- **Deleting compat surfaces for line count** — primitives/backends/compat legacy/recovery fixtures/model assets/vendor/bootstraps are retained by design.
- **Rewriting `NOT_VERIFIED` to `PASS`** or documenting “field delivered” as “frontend integrated” — forbidden.

## INSTALLED ROLE (B25b, 2026-10-23)
- `acp-worker` installs only `acp_services/**` + `cccp`; `python -m acp_services.worker_bootstrap` and the `acp-worker` script are the standalone worker entries. Do not install Web/DSH/browser into a worker or CCCP node.
- The installed wheel keeps `acp_services` independent of `acp`; the root `acp` distribution composes it. MCP stdio is served by `acp mcp serve` through `src/acp/services_mcp_bootstrap.py`, not by importing `acp` from this package.
- Clean-install evidence: `reports/acp2_implementation/bb45a46/B/B25/` (`installed-probes.json`, `assets-audit.json`).
