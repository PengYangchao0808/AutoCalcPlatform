# ACP 多用户拓扑：一人一 Linux 虚拟主机 + 一人一 DSH + 虚拟文件系统

日期：2026-10-31
性质：**目标架构与改造方案**，不是已实现记录。本文回答三个问题：RPH/DSH 在 ACP 中的拓扑位置、每用户虚拟主机与存储如何组织、现有模块要改什么。
**位置**：ACP 3.0 的**拓扑细节附件**；总纲见 [ACP 3.0 架构与共享契约](ACP_3.0_Architecture_20261031.md)，后端/前端执行见 [后端计划](ACP_3.0_Backend_Plan_20261031.md) / [前端计划](ACP_3.0_Frontend_Plan_20261031.md)。
上游：`ACP_Frontend_Target_Workbench_Plan_20261030.md`（前端目标形态）、`ACP_2.0_前端同步升级开发方案_20261029.md`、`ACP_Services_Identity_Support_Scope.md`（身份档位）。

---

## 0. 一句话结论

**ACP 从「单实例单用户 Web 应用」升级为「中央控制面 + 一人一台受管 Linux 主机 + 一人一个 DSH」**：

```
浏览器
  └─ ACP Controller（中央：登录/项目/任务/配额/审计/存储目录/插件市场）
        ├─ 用户 A 的 Linux 主机（容器或 microVM）
        │    ├─ dsh web（本机 loopback，独立 DSH_HOME / 模型凭据 / 插件）
        │    ├─ /home/a/acp/projects/<p>/tasks/<t>   ← acpfs 虚拟文件系统
        │    └─ host agent（文件解析、缓存、DSH 生命周期、健康检查）
        ├─ 用户 B 的 Linux 主机（同上，完全隔离）
        └─ 计算集群（LSF/Slurm）：任务输出落集群存储，按策略归档/清理
```

- **DSH 是每个用户的工作台运行时**（聊天、终端、文件、插件），不再由 ACP 自己实现终端/文件。
- **ACP 是控制面与科学后端**：身份、项目、任务、配额、存储目录、MCP 工具、化学面板插件。
- **数据不跟着人走**：数据在集群/对象存储；用户主机只有元数据与热缓存；acpfs 按需取用。

---

## 1. 拓扑与职责边界

| 层 | 组件 | 职责 | 不做什么 |
|---|---|---|---|
| 控制面 | ACP Controller（FastAPI + SQLite/Postgres） | 账户/会话、项目、任务目录、存储绑定、配额、审计、DSH 路由、插件市场索引 | 不跑用户 shell、不存科学大数据 |
| 用户主机 | 受管 Linux 沙箱（systemd-nspawn/LXC；hosted 可升 microVM） | 用户 UID、home、DSH 实例、acpfs 挂载、host agent、热缓存 | 不持有其他用户数据、不直连控制面数据库 |
| 工作台 | 每用户一个 `dsh web` | 聊天/Agent、终端（PTY）、文件（workspaceFiles）、插件面板 | 不实现 ACP 任务权威，不绕过 ACP 认证 |
| 计算 | LSF/Slurm/本地 | 真实计算、集群暂存、项目存储 | 不暴露给浏览器 |
| 存储 | 集群共享盘 / 对象存储 / 归档 | 任务产物、项目配额、冷归档 | 不做身份与目录权威（ACP 才是） |

### 1.1 为什么不是「共享主机 + 每人一个目录」

`acp_services/runtime/isolation.py` 明确规定：**共享 UID 的目录隔离是伪隔离**，不能提升隔离等级，也不能满足 `lab`/`hosted` 档位。因此每用户必须有独立的 OS 身份/命名空间；目录隔离只能作为开发环境的临时模式。

### 1.2 主机形态选择

| 形态 | 隔离 | 成本 | 适用 |
|---|---|---|---|
| systemd-nspawn / LXC 容器（**默认**） | 独立 UID/PID/mount/net/cgroup | 低（共享内核） | lab、单机多用户 |
| microVM（KVM/Firecracker） | 硬件级 | 中高 | hosted、不可信代码 |
| 每用户云主机 | 硬件级 | 高 | 跨机部署 |
| 共享主机 + 每用户 Unix 账号 | 仅 UID | 最低 | 仅开发/可信内网，**不得标 hosted** |

方案用 `HostProvider` 协议把四者统一；默认实现 nspawn，hosted 升 microVM，开发环境用 `ExistingHostProvider`（把当前单机 adopt 成一个 host）。

### 1.3 现状与差距（代码核对）

| 能力 | 现状 | 差距 |
|---|---|---|
| 身份/账户 | `identity_accounts` + `IdentityLifecycle` 已有账户/邀请/会话/撤销；`Principal`/`TenantScope`/`User` 契约存在；API-key 模式已接 HTTP | `User` 只是 DTO；无 `principal ↔ uid ↔ host` 记录；`deployment_tier` 是进程级环境变量，不能按用户 |
| 租户/项目 | 项目是唯一数据边界；`ProjectManager` 有 project_id/name/run_root | 项目表无 `tenant_id`/`owner_principal_id`；无法按用户列项目 |
| 开通 | `ProvisioningService` 只创建 namespace + 冻结 `StorageBinding`；**明确不做系统账户/进程/网络副作用**（有测试钉住） | 无主机创建/启动/停止/重置/销毁/健康；`ProvisioningIntent` 无 host/image/lifecycle 字段 |
| 隔离 | `IsolationLevel`/`TIER_REQUIREMENTS`/`IsolationGate` 已定义（拒绝不降级）；`cgroup.py` 只是 plan | `PrivilegedBroker`（创建 OS 身份）在 `src/` **无实现**；`OsIdentity` 只来自内存 map；hosted 档没有可执行隔离 |
| 配额/准入 | 每 job cores/mem/walltime + core-hours 配额 | `local_admission` 明示「非多租户」；无每用户主机资源包/磁盘配额/并发上限 |
| 存储 | 单进程一个 `run_root`，按项目分目录；`StorageBinding` 已有 tenant/project/endpoint/root/relative_path | 无 per-user root；远端缓存按 `job_id` 键、共享 `.remote_cache`；`ReadRootResolver` 按 job 解析，不接 namespace |
| 归档/清理 | `archive.py` 只做导出；`local_cleanup`/`remote/cleanup` 按 run_root/远端 retention | 无按用户/项目的策略引擎、恢复、证据保护 |
| 部署/开通 | `install_systemd.sh` 单机单实例；`acp init` 是机器级向导；`acp doctor` 无身份/隔离行 | 无 `acp onboard`、无每用户 unit、无 host agent |
| DSH | 只有本地/嵌入实验，未接生产 | 每用户 DSH 实例、模型凭据、插件市场、路由全部为 0 |

> 结论：**身份 ≈ 60%、主机生命周期 0%、隔离仅声明、虚拟 FS 0%、onboarding 0%**。这不是「补几个接口」，而是新增一层 Host 控制面；但现有 `StorageBinding`/`Namespace`/`IsolationGate`/`IdentityLifecycle` 可以直接复用，不需要重造。

### 1.4 RPH/DSH 在拓扑中的位置

```
用户 → ACP 门户（登录/会话）
     → 用户主机上的 DSH（profile = rph-research：Supervisor + 4 skills；MCP client）
     → MCP stdio → acp mcp serve → ACP Controller API
     → Services（身份/预算/任务/ResearchRecord/审计）
     → CCCP 任务核心 → 本地 / LSF
```

- **RPH 不是独立服务**：它是 DSH profile 里的 bundle + skills + bridge，随每个用户主机部署一份；`bridge` 仍然无传输，MCP 是唯一边界。
- **ResearchRecord/Proposal 的权威在 ACP Services**，不在 DSH 会话；换机/重连从 ACP 恢复，聊天记录只是交互历史。
- **终端/文件属于用户主机**（DSH 原生），**提交/查询只走 MCP**；ACP 面板是只读/配置面，不新增第二条提交路径。
- **版本**：RPH bundle、ACP 面板插件、DSH 本体三者一起灰度；`BRIDGE_EXTENSION_ID=acp.research.v1` 的契约版本随 ACP API 演进。

---

## 2. 每用户 DSH 的部署与路由

### 2.1 实例模型

- 每台用户主机一个 `dsh web`，`DSH_HOME=/home/<user>/.dsh`，workspace 根 `/home/<user>/acp`。
- 监听 **loopback 或 Unix socket**；端口随机、只对本机 host agent 开放。
- 模型配置写在该用户的 `$DSH_HOME/.credentials.yaml`（见 §5.3），**不进 ACP 数据库**。
- 插件：ACP 私有市场索引 + 固定版本；每用户主机独立安装（§6）。
- 升级：控制器按 `dsh_version` 分批滚动；升级前快照 `DSH_HOME`；失败回滚镜像/版本。

**可行性事实（已核实，安装版 0.2.1-rc.3 + npm 0.2.1-rc.5）**
- `@deepseek-ai/dsh` 已发布到 npm（`0.2.1-rc.5`，MIT，`bin: dsh`，Node 24 系），Linux 可直接 `npm i -g @deepseek-ai/dsh@<pin>`，不依赖 Electron。
- **一个进程 = 一个 `DSH_HOME`**：`resolveDshHome()` 优先级为配置 > `$DSH_HOME` > `~/.dsh`；home 下有 `profiles/{web,desktop}`、`sessions`、`storages`、`attachments`、`.credentials.yaml`、`.anonymous-user-id`。
- `dsh web` 默认 `host 127.0.0.1`、`port 3080`；`--port 0` 由 OS 选；`--host` 只接受本机具体地址，拒绝通配。
- **同一 home 的并发写入未验证安全**（除 `.credentials.yaml` 的 per-file 锁外没有 home 级锁），所以**一人一 home、绝不共享**；跨用户隔离 = OS 用户 + 独立 DSH_HOME + 独立端口。
- DSH **显式单用户**：home 里没有 tenant/account 映射，profile 是目录不是用户；多用户只能靠「一主机一 home」。
- 模型凭据可非交互预置：`$DSH_HOME/.credentials.yaml`（`version: 1` + `refs`/`records`，必须 `chmod 600`，否则启动报错）；profile 用 `--patch <yml>` 或 `profiles/<name>/cordis.patch.yml` 覆盖。
- 插件安装：`dsh plugin --profile web add <pkg> --registry=<url>`（参数原样转发 pnpm）；**没有** `search/list/config/login/profile` 子命令。插件市场是**可选实验 bundle**（`dsh-experimental-plugin-market-bundle`），默认 web profile 不挂载。

### 2.2 路由（浏览器只认 ACP）

```
GET /u/<tenant>/dsh/*  → ACP Controller 反代 → host agent → 127.0.0.1:<dsh_port>
```

- 浏览器先登录 ACP（会话 cookie）；反代校验 `tenant` 与会话主体一致，再注入该主机 DSH 的 launch token。
- token 按主机实例生成、只在控制器与 host agent 内存；不进前端、不落日志。
- `/u/<tenant>/panel/*` 用于 ACP 面板 iframe（任务、网络、结构），同样按租户校验。
- DSH 的 WebSocket（`/api/remote.mux`）由反代透传，逐连接校验租户。

### 2.3 DSH 到 ACP

- Agent 通过 **MCP stdio**（`acp mcp serve --controller <url> --token <per-user scoped key>`）提交/查询任务；密钥按租户作用域，不是全局 key。
- 面板通过 ACP HTTP API（同源反代，无 CORS）；凭据由反代注入，浏览器不持有。
- 任务权威、预算、审批、审计全部在 ACP；DSH 不存任务真值。

---

## 3. 虚拟文件系统（acpfs）

### 3.1 目录模型（用户可见）

```
/home/<user>/acp/
├── projects/<project_slug>/
│   ├── project.json                 # 只读投影（id/name/配额）
│   └── tasks/<task_slug>/
│       ├── input.xyz | input.json
│       ├── WORK/…                   # 运行期
│       ├── RESULT/…                 # 产物（manifest 为准）
│       └── .acp.json                # 绑定/可用性/归档状态（只读）
├── shared/                          # 跨项目只读引用
└── .acp/{cache,trash,restore}/      # 缓存/回收/恢复
```

- 路径不带 `job_id`/时间戳（沿用 ANTI #15）；`task_slug` 由 `<molecule>_<task>_<remark>` 生成。
- `.acp.json` 暴露：`storage_binding`、`availability`（hot/warm/cold/remote）、`digest`、`archive_state`、`quota`。
- 写入：只允许在任务目录的新文件/`WORK`；`RESULT` 以 manifest 为权威，禁止手改（可写 staging + commit）。

### 3.2 实现方式

| 方式 | 说明 | 取舍 |
|---|---|---|
| **acpfs（FUSE）** | host agent 实现 FUSE，路径解析走控制器，内容按需取 | 真正的「虚拟文件系统」；需 FUSE 依赖与故障处理 |
| 集群共享盘直挂 | Lustre/NFS/GPFS 挂到用户主机 | 性能最好；受网络与集群策略限制 |
| 同步/缓存 | rclone 类同步 + 热缓存 | 简单；一致性/陈旧风险 |

**建议**：acpfs 作为统一视图；对同集群共享盘支持 **passthrough**（直接 bind mount，acpfs 只做路径映射/权限）；对象存储/跨集群走按需拉取 + 缓存。FUSE 读失败必须显式返回 `EIO`/离线标记，不能返回空文件。

**实现选型**：v1 用 Python `pyfuse3`（asyncio，与控制器同语言，易维护）；若大文件/高并发实测不达标，再换 Go 单二进制。acpfs 只做「路径→绑定→内容」的解析与缓存，不做科学逻辑。

### 3.3 缓存与归档

- 热缓存：`~/.acp/cache/<digest>`，LRU + 每用户配额；pin 打开中的文件。
- 分层：`host cache（L0）→ 集群暂存（L1）→ 项目存储（L2）→ 归档（L3，对象存储/压缩包）`。
- 策略（按项目）：`keep_hot_days`、`archive_after_days`、`purge_after_days`、`keep_final`、`keep_evidence`；manifest/证据引用中的工件永不静默删除。
- 清理：控制器 sweeper + 集群侧 quota/scratch 清理；每次删除写审计与 tombstone；恢复走 `acp restore`。
- 现有 `acp_services/data/{storage_binding,namespaces,archive,transfer}.py` 是落点：扩展 `StorageBinding.endpoint` 为 `cluster:<name>`/`object:<bucket>`，`kind` 增 `host_cache|archive`；`archive.py` 加策略引擎与恢复。

---

## 4. 主机与 DSH 生命周期（控制器侧）

### 4.1 数据模型（新增表）

```
user_hosts(
  host_id, tenant_id, principal_id, provider, endpoint, status,
  namespace_id, run_root, home_root, dsh_home, dsh_port, dsh_version,
  image_version, plugin_set_version, quotas_json,
  created_at, updated_at, last_health_at
)
host_operations(operation_id, host_id, kind, state, error_code, payload, created_at)
```

- `principal_id` 指向 `identity_accounts`；`tenant_id` 与 `Principal.tenant_id` 一致；`namespace_id` 复用 `namespaces`（`NamespaceKind.PERSONAL_SPACE`）。
- 状态机：`requested → creating → provisioning_dsh → ready → degraded → stopping → stopped → destroyed`；
- 幂等：同一 `tenant_id` 只允许一个 active host；重复 provision 返回同一记录；
- 对账：控制器启动/定时 reconcile 未完成操作；失败显式 `failed`，不静默重试成第二个 host。

**副作用边界**：`ProvisioningService` 继续保持「不建系统账户/不 spawn/不联网」的既有契约（`tests/test_services_provisioning.py` 钉住）；创建 OS 身份、容器、unit 的副作用只放在**新的** `host_provisioning.py` + `os_identity.py` 里，并由 `PrivilegedBroker` 白名单操作实现（`isolation.py` 目前只有 Protocol，无实现）。

### 4.2 主机供应（HostProvider 协议）

```
provision(spec) / start / stop / destroy / health / exec / mount_storage / write_secret
```

- 端口：`SystemdNspawnProvider`、`KvmProvider`、`ExistingHostProvider`（开发/迁移）。
- 主机镜像：基础 OS（Debian/Ubuntu）+ **Node 24**（DSH 要求）+ `@deepseek-ai/dsh@<pin>` + FUSE3 + acpfs + host agent + 固定版本插件；镜像版本与 `dsh_version` 一起登记。
- nspawn 要点：`--private-users=pick`（独立 UID 段）、`--private-network` + veth/NAT、`/home/<user>/acp` 绑定挂载、cgroup v2 限制、只读基础镜像 + 可写 home。
- 主机内只跑三样东西：`dsh web`、acpfs、host agent（Unix socket）；不跑 ACP Controller。
- 资源：cgroup v2（CPU/mem/pids/io）+ 磁盘配额；hosted 档必须启用隔离能力，否则 `IsolationGate` 拒绝启用（沿用 `isolation.py` 的「拒绝，不降级」）。
- 迁移：现有单机用 `ExistingHostProvider` adopt；`run_root` 映射到 `/home/<user>/acp`；旧数据原地保留、只加绑定。

### 4.3 host agent（每台用户主机一个）

| 能力 | 接口（Unix socket，控制器持有主机密钥） | 说明 |
|---|---|---|
| 健康 | `GET /health` | DSH 端口/版本、acpfs 挂载、磁盘/内存、插件版本 |
| DSH | `POST /dsh/{start,stop,restart}`、`GET /dsh/token` | token 只回给控制器；进程以用户身份运行 |
| 文件 | `POST /fs/{stat,list,read,write}` | 只服务 `/home/<user>/acp`；路径穿越/符号链接校验 |
| 插件 | `POST /plugins/{install,remove,list}` | 只接受 ACP 私有索引的固定版本 |
| 配置 | `POST /config/model` | 写 `$DSH_HOME/.credentials.yaml` + profile，不返回明文 |
| 归档 | `POST /cache/{pin,evict}` | 与控制器 sweeper 配合 |

控制器到 host agent 的通道：SSH 隧道或 vsock；不用公网端口。host agent 用一次性注册 token + 主机密钥认证。

### 4.4 开通流程（onboarding，全部可非交互）

1. 管理员/自助创建账户（`lab`/`hosted`）→ `tenant_id`（复用 `identity_lifecycle`）；
2. `host_lifecycle.create_host`：`os_identity` 建 OS 用户/UID → 分配端口/DSH_HOME → 建 `/home/<uid>/acp`；
3. 主机内安装 Node 24 + `@deepseek-ai/dsh@<pin>`（npm，固定版本）；
4. 写 `$DSH_HOME/.credentials.yaml`（`version:1` + provider refs，`chmod 600`）与 profile patch（模型、插件、市场索引、workspace 根）；
5. `dsh plugin --profile web add <ACP bundle> --registry=<private>` 预装 ACP 面板插件（市场可选）；
6. 启动：`dsh web --host 127.0.0.1 --port 0 --no-open --public-url /u/<tenant>/dsh --patch <acp-profile.yml>`，token 交 host agent；
7. 建 DSH workspace（root = `/home/<uid>/acp`）+ acpfs 挂载；
8. `doctor`：DSH 健康、MCP 握手、模型 ping、acpfs 读写、配额；门户显示「打开我的工作台」。

> 注意：DSH 没有 `config/login/profile` CLI；模型与 profile 只能通过凭据 YAML + patch 文件写入，这正是上面第 4 步的方式。

---

## 5. 身份、密钥与插件市场

### 5.1 身份

- 复用现有 `lab`/`hosted` 档位、账户/会话/邀请/撤销（`identity_lifecycle.py`）；`tenant_id` = 用户，项目是租户内资源。
- 新 entry class：`portal`、`dsh_proxy`、`virtual_fs`、`host_admin`，全部 fail-closed（`entry_authz.py` 的未知类拒绝语义）。
- 管理员可列出主机、重置 DSH、调整配额、禁用账户（即时关闭 DSH 代理与 SSE）。

### 5.2 密钥与模型配置

- 模型 key 只存用户主机的 `$DSH_HOME/.credentials.yaml`（DSH 凭据存储），ACP 只记 `configured/ref` 元数据，不存明文。
- 该文件是 `version: 1` 的 YAML，顶层 `refs`（环境变量名→值）与 `records`（`<owner>/<id>` → `{kind, payload}`）；DSH 强制 owner-only，权限不是 600 会拒绝启动。
- **可非交互预置**：onboarding 先写这个 YAML（含 provider 记录），再启动 DSH；不需要 `dsh login/config`（这两个命令不存在）。
- 模型/插件/市场/workspace 根等通过 profile patch 写入：`--patch <file.yml>` 或 `profiles/<name>/cordis.patch.yml`；加载顺序是 bundle 层 → `cordis.patch.yml` → `--patch`，根 `cordis.yml` 永远不要手改。
- DSH launch token 每实例生成、内存持有；MCP 用 per-user scoped key。
- 主机 host agent 用一次性注册 token + mTLS/本机 socket，避免长期共享密钥。

### 5.3 DSH 插件市场（已核实）

DSH 的「插件市场」不是中心化商店，而是**用户配置的索引 URL 列表**（`dsh-plugin-market`）：

- 索引是 JSON（`dsh-plugin-market-index` 格式 1）：bundle、版本、`engines.dsh`、展示元数据、npm/git 安装参数；`marketInstallTarget` 生成精确的 `name@version` + registry 或 `git+url#commit`；
- 安装由 `pluginManager.installBundle` 用 pnpm 执行；**索引无签名**，安装是否成功以已安装 manifest 为准；
- 兼容性按 `engines.dsh` 判定；已装版本**不支持原地升级**，必须卸载再装；
- 默认 `indexes: []`，没有配置索引就完全不发请求；索引 URL 必须是 http(s)，拒绝重定向。

**ACP 的做法**：
- **市场是可选件**：默认 web profile 只挂 `dsh-base` + `dsh-web-app`，不挂市场；ACP 的 profile patch 要显式插入 `dsh-experimental-plugin-market-bundle`（或 `plugin-market` + `ui-plugin-market` 两行），并把 `indexes` 指向 ACP 私有索引；
- 控制器发布**私有索引** `https://<controller>/plugins/index.json`，只列 ACP 官方 bundle（`@acp/dsh-panels` 等）与固定版本/commit；
- onboarding 预装 ACP 必需插件（`dsh plugin --profile web add <pkg> --registry=<private>`），**市场只作为“用户可自行发现/安装”的入口**；管理员决定是否追加官方 npm 索引，非管理员不得添加任意索引；
- 安装/升级由控制器编排（按 `dsh_version` 选兼容版本），用户手动安装仅限 allowlist；
- 供应链：私有 registry 或固定 commit + 版本锁定；「索引无签名、安装执行 pnpm」是已知限制，不能把任意第三方索引当可信来源。

### 5.4 ACP 前端形态

- **DSH 是壳**：`/u/<tenant>/dsh/` 是用户主界面；ACP 提供插件与面板。
- ACP 面板以 **iframe 微前端**进入 DSH：`@acp/dsh-panels` 插件注册面板，内容指向 `/u/<tenant>/panel/{tasks,network,structure,config}`；复用现有 ACP 前端代码，不整体重写 React。
- Ketcher 作为 `structure` 面板的 iframe（vendor 版），3D 用 ACP 现有 viewer 页面。
- ACP 门户（Controller）只做登录、开通、主机状态、管理；经典页保留给管理员/过渡。
- 现有 `/next/` 三工作区在迁移完成后退役（数据与 API 不退役）。

### 5.5 APP 与网页端

- **网页端**：ACP 门户（登录/开通/状态）→ 跳 `/u/<tenant>/dsh/`；浏览器只与 ACP Controller 同源通信，DSH 面板与 ACP API 都经反代。
- **APP**：原生外壳（WebView）加载同一个 `/u/<tenant>/dsh/`；登录用 ACP OAuth/设备码或内嵌会话 cookie；不把 DSH token 放进 APP。
- **离线/弱网**：APP 只缓存 UI 壳与会话列表；文件走 acpfs 的服务端缓存，不在客户端做第二套存储。
- **一致性**：网页/APP/DSH 三端看到同一项目/任务，因为权威都在 ACP Controller；DSH 只是每用户的工作台运行时。

---

## 6. 模块改造清单（按仓库落点）

### 6.1 新增（只加真正缺失的对象）

| 文件 | 内容 |
|---|---|
| `src/acp_services/runtime/hosts.py` | `HostState`（REQUESTED/PROVISIONING/SCHEDULED/RUNNING/STOPPED/FAILED/DESTROYED）、`HostRecord(host_id, principal_id, tenant_id, uid, os_identity, root, state, revision, health)`、`HostRegistry`（SQLite，`principal_id` 唯一 = 一人一机）、`HostLifecyclePort` 协议；纯 stdlib + 注入 broker，保持 acp-free |
| `src/acp_services/runtime/host_lifecycle.py` | 6 个幂等用例：`create/start/stop/reset/destroy/health`，每次提交状态 + 一条审计，形状对齐 `ProvisioningService.provision` |
| `src/acp_services/workspace/os_identity.py` | **唯一特权缝**：实现 `isolation.py` 的 `PrivilegedBroker`（白名单 `create_os_identity`/`set_quota`/`start_sandbox`），返回 `OsIdentity(username, uid, gid)` 并持久化 principal→uid；其它模块不得 shell out |
| `src/acp_services/workspace/tenancy.py` | `users`/`tenant_members` 表 + principal↔tenant↔project join（`User` DTO 目前无持久化） |
| `src/acp_services/data/virtual_fs.py` | `VirtualFileSystem(namespace_id, principal_id, binding, quota, mount_mode, read_root)` + resolver；是 `NamespaceRecord`+`StorageBinding`+`StorageBudgetRequest` 的薄投影，不新增存储 |
| `src/acp_services/runtime/host_quota.py` | 把 core-hours（`QuotaService`）、`StorageBudgetRequest` 字节/inode、`AdmissionLimits` 并发绑到 `host_id`（`quota_budgets.scope_type="host"`） |
| `src/acp_services/runtime/dsh_host.py` | DSH 实例：profile patch、token、端口/Unix socket、健康、版本、插件集、模型配置写入 |
| `src/acp_services/runtime/host_providers/{nspawn,kvm,existing}.py` | 主机适配器（stdlib/subprocess，不 import acp） |
| `src/acp_services/data/fs_cache.py`、`data/retention.py` | 热缓存/LRU/pin；归档/清理策略与恢复（或扩展 `archive.py`） |
| `src/acp/api/{v2_hosts,dsh_proxy,v2_virtual_fs,portal}.py` | 管理员主机面、`/u/<tenant>/dsh/*` 反代、host agent 内部 FS、门户 |
| `scripts/acp_user_unit.sh.tmpl` | 每用户 systemd unit 模板（host root `/var/lib/acp/users/<uid>/runs`） |
| `dsh-plugins/acp-panels/`、`dsh-plugins/acp-profile/` | DSH client plugin（面板 iframe）+ profile patch |
| `tests/test_services_hosts.py`、`tests/test_services_virtual_fs.py` | 新护栏 |

### 6.2 修改（在既有对象上开洞）

| 文件 | 改造 |
|---|---|
| `src/acp_services/store/migrations.py` | 追加 `"024"`（`hosts` 表）与 `"025"`（`projects.tenant_id`/`owner_principal_id`、`identity_accounts.host_id`）；`_MIGRATIONS` 当前最后是 `"023"` |
| `src/acp_services/runtime/provisioning.py` | `ProvisioningIntent` 增加 `host_id`/`image`/`lifecycle`；`provision` 在 `ensure_namespace` **之前**调用 `HostLifecyclePort.ensure_host`，namespace root 用主机 root。保持「无系统账户副作用」：副作用由 host port→broker 做 |
| `src/acp_services/runtime/isolation.py` | `IsolationConfig.identity_map` 改为由 `os_identity` 支撑的 resolver（保留字段给测试）；`TIER_REQUIREMENTS`/`IsolationGate` 不动 |
| `src/acp_services/contracts/access.py` | `Principal` 增加 `host_id: str = ""`（frozen、additive）；`resolve_scope` 透传 |
| `src/acp_services/workspace/projects.py` | `_create_with_id` 增 `tenant_id`/`owner_principal_id`；`dir_leaf_for` 用 per-user 父目录（**用户段放 run_root，不改 storage relative_path**，避免动 24 个远端存储测试） |
| `src/acp_services/bootstrap.py::ServicesPorts` | 增加 `hosts` / `virtual_fs` 过渡端口（按现有 10 个端口的迁移台账纪律） |
| `src/acp/results/remote_structure_cache.py` + `src/acp/services_data_bootstrap.py::_ReadRootAdapter` | 缓存根改为 `<user_root>/.remote_cache/<job_id>`，经 `virtual_fs` 解析；attempt/purge 围栏不动 |
| `src/acp/scheduler/{local_cleanup,remote/cleanup}.py` | 按用户 root/主机阈值 sweep，不再 run_root 全局 |
| `src/acp/api/server.py` | `app.state` 挂 `host_registry`/`virtual_fs`；hosted 档按请求解析（当前 tier 是进程级） |
| `src/acp/cli.py` | 新增 `onboard`、`host {create,start,stop,reset,destroy,status}`；`doctor` 增加 identity/isolation/host 行（现在只有 run_root+可执行文件） |
| `scripts/install_systemd.sh` | `--per-user` 模式：按 host registry/accounts 渲染每用户 unit；单机路径保留为 local-single 分支 |

**不要新增**：第二个控制状态机（submit/pause/cancel 仍归 `JobManager`/`RuntimeCommands`）、第二个存储分配器（`StorageBindingAllocator` 唯一）、新的执行根（host 操作只经那一个 broker）。

### 6.3 DSH 插件与市场

| 路径 | 内容 |
|---|---|
| `dsh-plugins/acp-panels/` | DSH **bundle**：`package.json` 声明 `dsh.bundle.patch` → YAML 插入 host service + client UI；面板 iframe 指向 ACP `/u/<tenant>/panel/*` |
| `dsh-plugins/acp-profile/` | profile patch 模板：`dsh.profile.bundles`、市场索引、workspace 根、模型占位 |
| `scripts/plugin_market/build_index.py` | 生成格式 1 索引（名称/版本/`engines.dsh`/install target） |
| `scripts/host/build_image.sh` / `provision.sh` | 主机镜像（Node 24 + DSH pin + FUSE + acpfs + host agent）与供应脚本 |

**插件形态（已核实）**：ACP 插件必须是一个 npm 包，`package.json` 里用 `dsh.bundle.patch` 指到 YAML loader patch（插入 host service / client UI），安装后进入 `profiles/<name>/package.json` 的 `dsh.profile.bundles`；安装用 `dsh plugin --profile web add <pkg> --registry=<private>`（参数原样转发 pnpm，失败会回滚 package.json/lock/node_modules）。DSH **没有官方插件脚手架/模板**（templates/examples ABSENT），`dsh.bundle.patch` + 市场索引 JSON schema 就是事实契约，ACP 需要自己维护这个模板。

**市场的真实形态**：默认 web profile 不挂市场；市场是可选实验 bundle（`dsh-experimental-plugin-market-bundle`）插入的 `plugin-market` + `ui-plugin-market`，`indexes` 默认 `[]`，没有内置可搜索商店，只有官方 on-demand 白名单。ACP 私有索引是让用户「发现/安装 ACP 插件」的入口，不是通用插件商店。

### 6.4 护栏影响与迁移顺序

**会破的护栏（按优先级）**

1. `tests/test_services_provisioning.py::test_no_system_account_or_network_side_effects` —— **设计上必破**：拆成 (a) provisioning 只调注入端口，(b) `os_identity` 是唯一允许创建账户的模块（AST 断言 caller）。
2. `tests/test_services_isolation.py::test_capability_matrix_file_matches_code` + `tests/baseline/services-evidence/isolation_matrix.md` —— 新增 os-identity 行必须与代码同提交重生成；没有真实 broker 探针不得从 `configured` 改 `measured`。
3. `tests/test_acp_services_isolation.py` —— AST 禁 `acp_services → acp` 覆盖全部新模块；6 列台账新行必须有 `src/...py` 驻点。
4. `tests/test_acp2_runtime_multitenant.py` —— `test_default_deployment_remains_local_single` 必须保持绿（host 能力默认关闭，只有 tier 开启才生效）；404 非披露形状不能被 per-user root 改成 403。
5. `tests/test_services_runtime_usecases.py` —— 新 `host`/`onboard` CLI 必须走既有 use-case 入口，不能出现第二条提交/控制路径。
6. `tests/test_remote_storage_identity.py`（24 项）—— 只有把用户段塞进 `relative_path` 才会破；**方案选择用户段放 `run_root`**，这些测试不动。
7. `tests/test_acp_projects.py` —— 新列会改变返回字典的键集；`test_migration_creates_projects_table` 现在到 024/025。
8. `tests/test_services_worker_contract.py` —— worker 必须保持 acp-free/DB-free；主机注册表不能进 worker。
9. 文档同步三件套：`test_services_standalone_recipe.py`、`test_services_doc_sync.py`、`test_services_closure_record.py` —— 新能力先登记再谈 PASS。

**最省事的落地顺序**：迁移 024/025 → `os_identity` + host registry/lifecycle（默认关闭的端口）→ VFS 只读投影 → cache root/cleanup 切换 → CLI `onboard`/`host` → `install_systemd --per-user`。前 3 步只碰新测试；第 4 步才开始动既有套件。

### 6.5 工作量矩阵（单人粗估）

| 模块 | 动作 | 依赖 | 估时 |
|---|---|---|---|
| 迁移 024/025 + tenancy | 新增表/列 + join | 无 | 3–4 d |
| `os_identity` + broker | 新特权缝 + 审计 | 主机 provider | 4–6 d |
| `hosts`/`host_lifecycle` | 注册表 + 6 用例 + 对账 | os_identity | 5–7 d |
| `host_providers`（nspawn） | 镜像/启动/停止/健康 | 主机环境 | 5–8 d |
| `dsh_host` + onboarding 写入 | 凭据/ patch/ 插件/ 健康 | providers | 6–8 d |
| DSH 反代/门户 | `/u/<tenant>/dsh` + portal | dsh_host | 5–7 d |
| `virtual_fs` + acpfs | 解析/缓存/挂载/错误码 | 绑定/迁移 | 10–15 d |
| storage/archive/cleanup | 策略/恢复/配额 | VFS | 5–7 d |
| ACP DSH 插件 + 私有市场 | bundle + index + Ketcher 面板 | DSH 插件契约 | 8–12 d |
| 护栏/迁移/文档 | 拆分/重生成/台账 | 以上 | 4–6 d |

合计约 **55–80 人日**（不含 microVM 档与多 Controller）。

---

## 7. 分阶段与验收

| 期 | 内容 | 验收 | 估时 |
|---|---|---|---|
| **H0 拓扑 spike** | nspawn 两台主机 + 各自 DSH_HOME/端口 + `/u/<tenant>/dsh` 反代 + workspace + 终端/文件 + MCP 提交 + 一个最小 ACP bundle（`dsh.bundle.patch`） | 两用户互不可见；DSH 终端/文件可用；Agent 提交的 `xtb_optimize` 在 ACP 可见且不重复；bundle 可装可卸 | 3–4 周 |
| **H1 控制面** | 主机注册表/ProvisionUserHost/onboarding/门户/身份路由/配额 | 新建用户 10 分钟内拿到可用工作台；重置/禁用即时生效 | 3–4 周 |
| **H2 虚拟 FS 与存储** | acpfs MVP（list/stat/read/cache）+ 存储绑定 + 归档/清理 + 恢复 | 任务产物在用户主机可见；冷数据按需恢复；配额与审计正确 | 4–6 周 |
| **H3 ACP on DSH** | `@acp/dsh-panels` 插件 + 私有市场索引 + Ketcher 面板 + 门户切换 | 任务/网络/结构面板在 DSH 可用；旧 `/next/` 退役 | 4–6 周 |
| **H4 硬化** | microVM 档、备份/恢复、滚动升级、审计、容量 | hosted 档通过隔离评审；升级不丢会话/数据 | 持续 |

总计约 **3–5 个月**（单人；可并行则压缩）。H0 不通过（例如 nspawn 不可用/DSH 无法按用户隔离）则退回「共享主机 + 每用户 Unix 账号」的开发档，但**不得标 hosted**。

---

## 8. 风险与待确认

1. **主机形态**：nspawn/LXC 是否满足你们的隔离要求？hosted 是否必须 microVM/独立云主机？
2. **控制面规模**：单 Controller + SQLite 是否够？多 Controller 需要 Postgres/对象存储与一致性方案。
3. **acpfs 复杂度**：FUSE 的故障/性能/权限是长期成本；是否先用「共享盘直挂 + 缓存」过渡？
4. **集群网络**：用户主机能否直达 Lustre/NFS？不能则必须走 transfer/对象存储。
5. **DSH 版本/许可/运行时**：`@deepseek-ai/dsh` 是 npm 公开包（MIT，0.2.1-rc.5，Node 24 系），但升级节奏快、rc 版本多；必须锁版本 + 主机镜像同版本 + 升级前快照 `DSH_HOME`。许可与分发责任需法务确认。
6. **DSH 单用户 + 无 home 锁**：home 里没有 tenant/account，唯一隔离是 OS 用户 + DSH_HOME + 端口；**没有 home 级实例锁**，共享 home 并发写不受支持。host agent 必须保证「一 home 一实例」并检测双启动。
7. **插件供应链**：市场是可选项、索引无签名、安装执行 pnpm；DSH 没有官方插件脚手架（`dsh.bundle.patch` 是事实契约）。只允许私有索引/固定版本，并把插件包当代码审计。
8. **模型密钥**：key 在用户主机 `.credentials.yaml`，agent 以同 UID 运行、可读（DSH 文档明确）；这是「每用户主机」隔离的边界，需确认符合安全要求。
9. **Windows 开发环境**：nspawn/FUSE 仅 Linux；开发档用 `ExistingHostProvider` + 本地目录，需明确不是生产形态。
10. **成本**：每用户常驻 DSH + 主机内存/磁盘；是否按需启动（休眠/唤醒）？
11. **迁移与回退**：现有单机数据 adopt；`local-single` 默认必须保持可用；回退开关保留多久。
12. **DSH 升级兼容**：插件/市场/profile 都跟 DSH 版本绑定；升级可能导致会话/插件不可用，必须灰度与回滚。

---

## 9. 与既有方案的关系

- 取代 `ACP_Frontend_Target_Workbench_Plan_20261030.md` 的 T1/T2 二选一：**用户级 T2（DSH 为壳）**成为目标，ACP 面板以插件进入 DSH；T1 仅保留为开发/降级模式。
- 继续有效：UI-03 的任务/预检/提交契约、UI-04 的连通性图数据契约、Ketcher 2D 方案、`acp_services` 的分层与 identity 档位。
- 退役：`/next/` 三工作区壳（迁移完成后）、ACP 自建终端计划（DSH 终端替代）、`local-single` 作为生产形态。
