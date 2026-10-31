# ACP 3.0 后端实现计划（ACP Control Plane / CCCP / RPH）

日期：2026-10-31
配套：[ACP 3.0 架构与共享契约](ACP_3.0_Architecture_20261031.md) · [前端兼容计划](ACP_3.0_Frontend_Plan_20261031.md) · [拓扑细节](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)
范围：**后端**（Workspace/Runtime/Data/Agent Host/RPH/CCCP）。前端只依赖本文件的 §4 契约。

---

## 0. 原则与不做的事

- 新增的是**运行设施与适配器**，不是第二个 Core：任务/Attempt/Artifact/事件/预算的唯一权威仍在现有 Services。
- **复用不重造**：事务性 outbox、恢复语义、StorageBinding/Namespace、ArtifactRegistry、身份/会话/撤销、远端 LSF 链全部保留。
- 语言策略（见 §9-D1）：**Stage 0–2 以现有 Python 栈实现，契约保持语言中立**；Rust 只作为后续可替换实现（Workstation Controller/Artifact Catalog 优先），不在本轮引入双栈。
- `acp_services` 不得 import `acp`；`cccp` 不得 import `acp`；旧路径保持 identity forwarder。

---

## 1. 基线（已核对）

| 能力 | 现状 | 落点 |
|---|---|---|
| 身份 | `identity_accounts`/`identity_sessions`、邀请/撤销、`lab`/`hosted` 档位可用；API-key 已接 HTTP | `workspace/identity_lifecycle.py`、`workspace/http_identity.py`、`contracts/access.py` |
| 租户 | `Principal.tenant_id` 存在；项目表**无** tenant/owner | `contracts/access.py`、`workspace/projects.py` |
| 开通 | 只建 namespace + `StorageBinding`；**明确不建系统账户/不 spawn** | `runtime/provisioning.py`（`ProvisioningService.provision`） |
| 隔离 | `IsolationLevel`/`TIER_REQUIREMENTS`/`IsolationGate` 已定义；`PrivilegedBroker` **无实现**；cgroup 仅 plan | `runtime/isolation.py`、`runtime/cgroup.py` |
| 存储 | 单 `run_root` + 项目叶/任务叶；`StorageBinding` 含 tenant/project/endpoint/root/relative_path | `core/paths.py`、`data/storage_binding.py`、`data/namespaces.py` |
| 远端缓存 | `.remote_cache/<job_id>`，共享 UID | `results/remote_structure_cache.py`、`services_data_bootstrap._ReadRootAdapter` |
| 事件 | **事务性 outbox 已存在**：同事务写事件、至少一次、稳定 dedup、游标重放 | `runtime/outbox.py`、`runtime/events.py` |
| 恢复 | `RuntimeRecovery` + `step_result`/`resume_source` 已有 | `runtime/recovery.py` |
| 工件 | `ArtifactRegistry`（SQLite，本地）已有；无跨集群位置目录 | `scheduler/artifacts.py` |
| RPH | `research`/`bridge`/`dsh-bundle`/`cli` 四包已存在，依赖方向冻结 | `rph/packages/*`、`rph/README.md` |
| 迁移 | `_MIGRATIONS` 最后为 `"023"` | `acp_services/store/migrations.py` |

---

## 2. 工作包（按依赖排序）

### WP0 契约与迁移
- 迁移 **024**：`workstations`（`workstation_id PK, account_id UNIQUE, tenant_id, provider, endpoint, state, revision, home_root, dsh_home, dsh_port, dsh_version, image_version, plugin_set_version, quotas_json, health_json, created_at, updated_at`）+ `workstation_operations`（幂等/对账）。
- 迁移 **025**：`projects.tenant_id`、`projects.owner_principal_id`；`identity_accounts.workstation_id`。
- 迁移 **026**：`research_runs`（`research_run_id, account_id, project_id, dsh_session_id, state, last_consumed_event_id, created_at, updated_at`）+ `research_run_tasks(research_run_id, task_id, relation)`。
- 迁移 **027**：`artifact_locations(artifact_id, backend, cluster_id, locator, state, digest, size_bytes, updated_at)`；`artifacts` 以现有 registry 为基础扩展，不复制 manifest 字段。
- 契约版本：`ACP_API_VERSION=3`；事件 schema `acp.event.v1`。

### WP1 Workstation Manager
| 文件 | 动作 |
|---|---|
| `runtime/hosts.py`（新） | `HostState`、`HostRecord`、`HostRegistry`、`HostLifecyclePort`；`account_id` 唯一 = 一人一机 |
| `runtime/host_lifecycle.py`（新） | 幂等用例 `create/start/stop/suspend/resume/reset/destroy/health/backup`；状态提交 + 审计；对账 |
| `workspace/os_identity.py`（新） | 唯一特权缝：实现 `PrivilegedBroker`（白名单 `create_os_identity`/`set_quota`/`start_sandbox`），持久化 principal→uid |
| `runtime/host_providers/{nspawn,kvm,existing}.py`（新） | 主机适配器；`ExistingHostProvider` 用于单机 adopt |
| `runtime/host_quota.py`（新） | core-hours + storage bytes/inodes + 并发绑到 workstation |
| `runtime/provisioning.py` | `ProvisioningIntent` 增 workstation/state；`provision` 先 `ensure_workstation` 再 `ensure_namespace`（副作用由 broker 承担，原「无账户副作用」测试拆分） |
| `runtime/isolation.py` | `identity_map` 改由 os_identity resolver；档位门保持「拒绝不降级」 |

**休眠语义**：`suspend` 只停 DSH/终端/工作站进程，**不调用任何任务取消**；任务状态由 Runtime 持续管理。

**存储分区**：
- **Persistent Home**（`home_root`，备份）：`DSH_HOME`、Skills、研究记录引用、用户代码、项目必要文件。
- **Runtime Scratch**（`/home/acpuser/acp/scratch` 或 tmpfs，不备份）：终端进程、预览缓存、运行时中间数据、可重建缓存。
- 工作站状态机：`unprovisioned → creating → starting → running → idle → suspended → stopping → stopped → destroying → destroyed`，旁路 `degraded/failed`；每次迁移写 `workstation_operations` 与审计；`suspend` 不触发任务取消。

### WP2 Agent Host（DSH 生命周期与会话路由）
| 文件 | 动作 |
|---|---|
| `runtime/dsh_host.py`（新） | 安装/启动/停止/健康/token；`DSH_HOME`、端口、profile、插件集、模型配置写入；**一 home 一实例**检测 |
| `runtime/session_router.py`（新） | `account_id`→workstation→DSH 地址；`/u/{account}/dsh/*` 反代与 WebSocket 透传；token 只在服务端 |
| `runtime/recovery_bridge.py`（新） | Outbox 事件 → `research_runs` 游标 → 唤醒 DSH/RPH；幂等（`last_consumed_event_id`） |
| `api/v3_workstations.py`、`api/dsh_proxy.py`、`api/portal.py`（新） | 管理面、反代、门户/开通向导 |
| `cli.py` | `acp onboard`、`acp workstation`、`acp dsh`；`doctor` 增 workstation/DSH/MCP/模型行 |
| `scripts/acp_user_unit.sh.tmpl`、`install_systemd.sh --per-user` | 每用户 unit；单机路径保留为 local-single |

**开通步骤**（全部非交互）：账户 → 工作站 + OS 用户 → Node 24 + `@deepseek-ai/dsh@<pin>` → `$DSH_HOME/.credentials.yaml`（`chmod 600`）+ profile patch → `dsh plugin --profile rph add <bundle> --registry=<private>` → `dsh web --host 127.0.0.1 --port 0 --no-open --public-url /u/<account>/dsh --patch <acp-profile.yml>` → workspace 根 + ACP-VFS → doctor。

### WP3 Artifact Catalog 与 Storage Lifecycle
- `data/artifacts.py`：`ArtifactRecord` 与 `result_manifest` v2 product kinds 对齐，**不新建第二份科学元数据**。

```json
{
  "artifact_id": "art_0027",
  "owner_id": "user_001",
  "project_id": "P001",
  "task_id": "T002",
  "attempt": 1,
  "logical_path": "tasks/T002/results/ts_final.xyz",
  "content_sha256": "…",
  "size_bytes": 18240,
  "media_type": "chemical/x-xyz",
  "access": "read-only",
  "lifecycle": "online|archived|restoring|unavailable",
  "source": {"manifest": "RESULT/result_manifest.json", "product_id": "ts_final"},
  "locations": [
    {"backend": "cluster-ssh", "cluster_id": "hpc_a", "path": "/scratch/job_1024/ts_final.xyz", "state": "online"},
    {"backend": "object-store", "object_key": "users/user_001/art_0027", "state": "archived"}
  ],
  "created_at": "…"
}
```

- `data/retention.py`：策略引擎（`keep_hot_days/archive_after_days/purge_after_days/keep_final/keep_evidence`）；任务终态触发「发布 → 校验 → 归档」标准流程；证据/manifest 引用保护。
- `data/archive.py`/`transfer.py`：归档包 + 恢复；`locations` 状态 `online|archived|unavailable`；恢复进热缓存。
- `data/storage_binding.py`：`endpoint` 扩展 `cluster:<id>`/`object:<bucket>`；`kind` 增 `artifact|archive|host_cache`；用户段放 `run_root`，不改 `relative_path`。
- `scheduler/{local_cleanup,remote/cleanup}.py`：按用户/项目策略 sweep，不再全局 run_root。

### WP4 ACP-VFS
**阶段 1（逻辑 API）**：`data/virtual_fs.py` + `api/v3_fs.py`
- `list/stat/read/fetch/pin/archive/restore`；返回 `artifact_id + availability + last_location`。
- 解析：`account → workstation → namespace → binding → artifact`；读失败返回**明确错误码**（`unavailable/archived/remote_offline`），不返回空文件。
- Materialization：显式 `fetch` 后才有真实 POSIX 路径；`pin` 保热。

**阶段 2（FUSE，可选）**：`acpfs`（Python `pyfuse3` 起步）
- 只读挂载 `results/`；输入与用户文件可写；运行中日志走流式 tail。
- 权限：`/home/acpuser/acp` 内；路径穿越/符号链接校验；挂载失败降级为逻辑 API。
- 共享盘直挂作为 passthrough 快路径，acpfs 只做映射/权限。

### WP5 ResearchRun 与长任务恢复
- `runtime/research_runs.py`：CRUD、状态机、事件游标、与 task 关联。
- 提交幂等：`research_run_id + request_digest` 作为幂等键；重复提交返回同一 task。
- 唤醒：Outbox 事件 → `recovery_bridge` → Agent Host `wake(research_run_id)`；DSH 空闲/重启不影响。
- RPH 研究状态存 ACP（`research_state`），不依赖 DSH 会话历史。

### WP6 集群 Adapter
- 现有 LSF 链保留；新增 `SlurmAdapter`/`PBSAdapter`（SSH + 命令，或 `slurmrestd` 可选）。
- 统一接口：submit/status/cancel/logs/artifacts/scratch-cleanup；每集群独立权限/队列/网络探测。
- 产物：集群 scratch → Artifact Catalog（在线）→ 归档；**HPC scratch 不是长期归档**，任务完成后由 ACR 主动归档，不等用户登录。

### WP7 RPH 四包
| 包 | 现状 | 改造 |
|---|---|---|
| `rph/packages/research` → `research-core` | 已有，Harness 无关 | 明确不依赖 DSH 类型；研究状态/证据评价/计划 |
| `rph/packages/bridge` → `acp-bridge` | 已有，无传输 | 类型化 MCP/SDK 接口；`acp.research.v1` 契约 |
| `rph/packages/dsh-bundle` → `rph-dsh-bundle` | 已有 profiles/skills | 拆出 skills 为可移植文档；profile 名 `rph`；声明 `engines.dsh` |
| `rph/packages/cli` | 已有 | 保留为开发/运维工具，不启动 Controller/科学程序 |
| `rph-skills`（新或子包） | skills 在 dsh-bundle 内 | 独立版本、可被其他 Harness 加载 |

### WP8 安全与身份
- Gateway 先认证再路由；DSH 端口只对内部网络；launch token 不落地前端/日志。
- 每用户 ACP API key 作用域（MCP 用）；插件安装审批/版本锁/回滚；凭证 `chmod 600`。
- 新 entry class：`workstation_admin`、`dsh_proxy`、`virtual_fs`、`research_run`；未知类 fail-closed。

---

## 3. 数据模型（新增/修改摘要）

```sql
-- 024
workstations(workstation_id PK, account_id UNIQUE, tenant_id, provider, endpoint,
             state, revision, home_root, dsh_home, dsh_port, dsh_version,
             image_version, plugin_set_version, quotas_json, health_json,
             created_at, updated_at)
workstation_operations(operation_id PK, workstation_id, kind, state,
                       error_code, payload_json, created_at, updated_at)
-- 025
ALTER TABLE projects ADD tenant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD owner_principal_id TEXT NOT NULL DEFAULT '';
ALTER TABLE identity_accounts ADD workstation_id TEXT NOT NULL DEFAULT '';
-- 026
research_runs(research_run_id PK, account_id, project_id, dsh_session_id,
              state, last_consumed_event_id, research_state_json,
              created_at, updated_at)
research_run_tasks(research_run_id, task_id, relation, PRIMARY KEY(research_run_id, task_id))
-- 027
artifact_locations(artifact_id, backend, cluster_id, locator, state,
                   digest, size_bytes, updated_at,
                   PRIMARY KEY(artifact_id, backend, cluster_id))
```

不变量：`account_id ↔ workstation_id` 1:1（active）；`research_run_id` 属于一个 project；`task_id` 属于一个 project；`artifact_id` 属于一个 task/attempt（或 project 级）；事件游标单调递增。

---

## 4. 对外 API / 事件（前端只依赖这里）

见主文档 §3.3/§3.4。后端补充：
- 所有列表分页 `limit/cursor`；错误统一 `{code, message, params}`。
- SSE `/api/v3/events/stream?after_event_id=`；断线用游标续传，不重放已消费事件。
- `/api/v3/artifacts/{id}/read` 支持 `Range`；归档时返回 `202 + restore_operation_id`，恢复完成后事件通知。
- `/u/{account}/dsh/*` 反代必须校验 account 与登录主体；WebSocket 逐连接校验。

---

## 5. 关键流程

1. **开通**：账户 → 工作站（OS 用户/容器）→ DSH 安装/配置 → 插件 → ACP-VFS → 默认项目 → doctor。
2. **提交**：RPH/手动 → MCP/API（幂等键）→ Runtime 创建 task/attempt → 集群 Adapter → Outbox 事件。
3. **完成**：集群产物 → `result_manifest` v2 → Artifact Catalog（online）→ 归档（标准流程）→ 事件 → 唤醒 ResearchRun。
4. **读取**：Web/终端/DSH → ACP-VFS 解析 → 热缓存/远端流式/归档恢复；不可用给明确错误。
5. **休眠/恢复**：工作站 suspend 不停任务；resume 后 DSH 从 `research_state` + 事件游标继续。
6. **失败/重试**：attempt 隔离；幂等键防重复；归档失败重试发布，不重算科学。

---

## 6. 迁移与兼容

- **默认仍是 local-single**：新能力由 `ACP_DEPLOYMENT_TIER`/feature flag 开启；`test_default_deployment_remains_local_single` 必须保持绿。
- **单机 adopt**：`ExistingHostProvider` 把当前 `run_root` 映射为 `/home/<uid>/acp`；旧项目/任务原地保留，只加绑定与 owner 列。
- **旧 API**：`/api/v1|v2` 保留一个迁移周期；前端新面用 `/api/v3`。
- **旧入口**：经典页/`/next/` 保留回退开关，切换只改入口，不回滚数据。

---

## 7. 测试与护栏（按破坏优先级）

1. `tests/test_services_provisioning.py::test_no_system_account_or_network_side_effects` —— 拆成「provisioning 只调端口」+「os_identity 唯一创建账户」。
2. `tests/test_services_isolation.py::test_capability_matrix_file_matches_code` + `isolation_matrix.md` —— 新行同提交重生成；无真实探针不得 `measured`。
3. `tests/test_acp_services_isolation.py` —— AST 禁 `acp_services→acp` 覆盖全部新模块；6 列台账。
4. `tests/test_acp2_runtime_multitenant.py` —— local-single 默认、404 非披露。
5. `tests/test_services_runtime_usecases.py` —— 新 CLI 必须走既有 use-case 入口。
6. `tests/test_remote_storage_identity.py` —— 用户段放 run_root，路径测试不动。
7. `tests/test_acp_projects.py` —— 新列/迁移号。
8. `tests/test_services_worker_contract.py` —— worker 保持 acp-free/DB-free。
9. 文档同步三件套 + 新 `ACP_Services_Workstation.md` 登记后再谈 PASS。

新增：`test_services_workstations.py`、`test_services_research_runs.py`、`test_services_artifact_catalog.py`、`test_services_vfs.py`、`test_services_agent_host.py`、`test_services_event_wakeup.py`。

---

## 8. 阶段与工作量（与主文档 Stage 0–4 对齐）

| 阶段 | 后端交付 | 估时 |
|---|---|---|
| Stage 0 | WP0 契约/迁移、WP7 RPH 包重排、DSH pin/ profile 加载 | 2–3 周 |
| Stage 1 | WP1 工作站（本地/容器）、WP2 Agent Host、单用户闭环 | 4–6 周 |
| Stage 2 | WP3 Catalog/Lifecycle、WP4 VFS 逻辑 API、WP6 第二集群 | 6–8 周 |
| Stage 3 | WP5 ResearchRun/Outbox 唤醒、恢复闭环 | 4–6 周 |
| Stage 4 | 多用户/按需工作站/Gateway、VFS FUSE、备份/升级 | 6–10 周 |

合计约 **5–7 个月**（单人；可并行压缩）。Stage 0–1 不依赖前端完成，先用 CLI/MCP 验收。

---

## 9. 决策与风险

- **D1 语言**：Stage 0–2 Python（复用现有 247+ 测试与 Services 边界）；Rust 作为后续可替换实现（Workstation Controller/Artifact Catalog 优先），契约 HTTP/JSON 语言中立。**不建议现在双栈**。
- **D2 DSH 版本**：pin 0.2.1-rc.x；升级灰度；`rph` profile 与 `engines.dsh` 锁定。
- **D3 插件审批**：只允许 ACP 私有索引 + 固定版本；插件包当代码审计；DSH 无官方脚手架，`dsh.bundle.patch` 自维护。
- **D4 模型凭据**：存用户工作站，agent 同 UID 可读；需安全确认。
- **D5 集群**：共享盘直挂 vs 全量 transfer；按集群能力选择，VFS 统一视图。
- **D6 备份**：工作站 Home/DSH_HOME 备份；任务数据由 Artifact Catalog 归档，不依赖工作站快照。
