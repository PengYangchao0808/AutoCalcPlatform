# ACP 成熟开源组件复用评估

v1.5 战略增补见[完整方案第 30—35 节](ACP_Architecture_Adjustment_Plan_20261016.md#30-产品定位与-aiida-借鉴边界)和[产品战略与差异化](ACP_Product_Strategy_and_Differentiation_20261016.md)：本地完整科研闭环优先，Lab 分 Standalone/Server 配置，Hosted 按需后置；保持独立运行内核，借鉴 AiiDA，引入受控插件、项目迁移与 MCP。新增能力均为待实施目标，v1.4 的设计补全与质量门保留。

架构设计 v1.4 已补齐预留恢复、租户存储映射、身份迁移和独立核时配额依赖；代码质量清理按[主方案第 29 节](ACP_Architecture_Adjustment_Plan_20261016.md#29-本轮代码质量与冗余清理交付门)及[源码审计台账](ACP_Architecture_Code_Quality_Audit_20261016.md)执行。当前为设计与静态审计结果，未实施运行代码清理。

日期：2026-10-16。性质：基于项目代码和本轮官方资料核验的**候选选型建议**，不是已安装/已验证清单，不改变主方案的已确认模块边界。没有部署组件、迁移数据库或替换科学实现。

两版研究路线已正式纳入[完整调整方案第 26—28 节](ACP_Architecture_Adjustment_Plan_20261016.md#26-双版本产品部署与研究路线)：ACP Lab 与 ACP Hosted 共用内核，组件在现有九模块归位，并以 OS-01—OS-12 工作包做 PoC/冻结选型。路线已确认，具体新增组件仍是候选；本评估提供资料依据，不另设一套实施顺序。

## 1. 结论与采用原则

账户认证、密码/加密原语、权限引擎、HTTPS、数据库、资源控制、图形、科学基础与解析都有可复用组件。完整用户研究流程、科学身份与完成证据、attempt/CAS/恢复、工件发布/租户授权、核时预留结算仍需 ACP 集成并拥有业务语义。

**成熟开源服务可以取代基础能力实现，不能直接取代整个 ACR 或 CCCP。** 采用顺序按开发收益、额外服务数量、现有兼容和运维可恢复性评估，而不是按组件数量。库可以嵌入；独立服务需额外维护升级、秘密、备份与健康检查。

现有 pyproject 已声明 FastAPI/Uvicorn、Paramiko、NumPy/SciPy/RDKit/Matplotlib/openpyxl，NMR 另有可选依赖；前端已有本地 3Dmol。优先复用这些能力，不为使用开源而重写现有入口或换框架。

## 2. 分模块候选矩阵

“覆盖高”只针对该行基础能力，不表示整个对应 ACP 模块已可外包或开箱即用。

| ACP 位置/能力 | 候选及官方依据 | 可省下的实现 | 仍由 ACP 负责 | 建议与部署增量 |
|---|---|---|---|---|
| Workspace：完整身份服务 | [Keycloak](https://www.keycloak.org/docs/latest/server_admin/)；提供用户、身份协议、角色/组和认证管理 | 注册/登录/恢复认证、MFA/SSO、身份管理界面等基础能力，覆盖高 | 科研项目归属、等级/配额/Credit、个人计算 API Key 策略、对象授权与身份映射 | 线上/机构 SSO 优先候选；新增独立身份服务及其数据维护 |
| Workspace：嵌入式认证基础 | [Authlib](https://authlib.org/)、[pwdlib](https://github.com/frankie567/pwdlib) | OAuth/OIDC 协议与密码哈希库，协议/原语覆盖高 | 账户注册、会话、找回、限流、撤销、审计和本地账户 UI，仍须实现完整用例 | 轻量自部署候选，无需独立 IAM；Authlib 本身不是完整用户管理服务 |
| Workspace：权限决策 | [PyCasbin](https://github.com/pycasbin/casbin) | RBAC/ABAC 等策略求值 | 定义 user/project/artifact/action 模型、对象关系、执行每个入口检查、策略变更一致性 | 可嵌入；简单固定角色可先用窄策略接口，复杂时再接，避免维护两份权限事实 |
| ACR：元数据/CAS/账本 | [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)及事务能力 | 数据库基础、事务与可选行级防御 | 业务 CAS、幂等键、账本、schema/迁移/权限上下文；连接池不能残留上一租户上下文 | 多用户/多角色部署优先；单用户继续 SQLite；不能只换连接字符串 |
| ACR：HTTPS 入口 | [Caddy](https://caddyserver.com/docs/automatic-https) | 自动 HTTPS/证书管理与标准入口能力 | 域名/端口/信任/转发配置、API 授权、worker 安全 | 优先；独立入口服务，复用已有合格入口也可；内网证书信任条件仍需配置 |
| Runtime：集群调度/核时来源 | [Slurm](https://slurm.schedmd.com/overview.html)、[Accounting](https://slurm.schedmd.com/accounting.html) | 节点资源调度、队列、用户/账户限制和核时记录 | ACP job/attempt/提交身份、科学计划与恢复、计价/预留/账本 | 无现有调度器的集群可选；已有 LSF 保留，不强制替换；Slurm 适配尚需开发 |
| Runtime：本机进程资源/隔离基础 | [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)、[Podman](https://docs.podman.io/en/latest/markdown/podman-run.1.html) | OS 资源控制和容器运行原语 | 用户/OS 身份映射、最小挂载、可信启动、停止确认、科学软件兼容 | 按 Linux 部署档位接入；cgroup 不是文件访问隔离，rootless 限制需测宿主能力 |
| Data：分布式对象存储 | [SeaweedFS 社区仓库](https://github.com/seaweedfs/seaweedfs) | 分布式字节存储、S3 接口等存储引擎能力 | 工件身份/manifest、租户授权、配额预留、generation、原子可见发布、引用/回收 | 容量或多节点确有需求后候选；首期用受权本地/现有集群盘；社区与企业功能分开核验 |
| Workspace/Runtime：通用计费 | [Lago 仓库](https://github.com/getlago/lago)、[钱包说明](https://getlago.com/docs/guide/wallet-and-prepaid-credits/overview) | 用量计费、价格/账单等通用能力 | 科学核时证据、实时准入/并发预留、平台故障政策、重复 attempt 与收费关系 | 商业运营阶段评估；不是首期必装或完整免费实时 Credit 闭环 |
| ACV：三维显示 | [3Dmol.js](https://3dmol.org/) | 分子三维 renderer，覆盖高；当前已使用 | 统一 ACV API、atom/frame/mode 映射、相机/动画与销毁 | 立即复用现有版本并封装，不建第二三维引擎，不顺带升级未经验证版本 |
| ACV：二维图 | [Apache ECharts](https://echarts.apache.org/handbook/en/concepts/dataset/) | 系列图、数据映射、图形交互基础 | 科学产品到显示选项映射、点/帧身份、缺失值、科学单位、选择联动 | 候选样板；现有 SVG 若满足需求可继续；不能因此把科学分析迁到 JS |
| CCCP：跨软件数据模型 | [QCElemental](https://github.com/MolSSI/QCElemental) | 分子/科学模型与 QCSchema 生态基础 | 适配现有 TaskRequest/Result、特有方法、原子身份、版本与平台解耦 | 先做兼容性试点，作为可选 adapter；不直接替换既有科学契约 |
| CCCP：科学程序执行/I/O | [QCEngine](https://github.com/MolSSI/QCEngine) | 已支持程序的执行与 QCSchema 输入输出归一化 | 逐程序/版本覆盖、CCCP 特有能力、取消/预算/完成证据、唯一执行驻点 | 可按 backend 试点；CCCP task 调受控 adapter，不绕任务层或形成第二执行链 |
| CCCP：输出解析 | [cclib 属性覆盖表](https://cclib.github.io/data.html)、[支持说明](https://cclib.github.io/) | 多程序已支持属性的读取 | ORCA 等实际版本/模式序号/向量/失败输出验证，现有协议兼容 | 只读 parser adapter 候选，先对照真实 fixtures；不能宣称所有软件/字段全覆盖 |
| ACR 运维：监控 | [Prometheus](https://prometheus.io/docs/introduction/overview/) | 指标采集/查询/告警基础 | 指标含义、租户/科学信息保护、SLO 和告警处置 | 线上/集群推荐，可选服务；不是科学回执或账本权威 |
| ACR 运维：备份 | [restic](https://restic.readthedocs.io/en/stable/100_references.html) | 文件备份/恢复工具基础 | DB 一致快照、工件/账本/密钥版本关联、远端在途对账 | 可选；不能直接复制运行中数据库文件就宣称一致备份 |

上表“优先/候选”是结合 ACP 当前架构的建议，不是对所有部署的通用性能排名。未验证各候选锁定版本与本项目 Python 3.10/3.11/3.12、OS、软件协议的兼容性，实施前必须做切片 PoC。

## 3. 三个最容易高估的“完整替代”

### 3.1 身份服务不等于 ACP 工作空间管理

Keycloak 可显著减少认证实现，登录页/身份管理也可复用；Workspace 仍拥有项目、成员作用范围、等级/配额与 Credit。外部身份以 issuer+subject 等稳定映射关联本地 owner，不复制/同步两份密码。

Keycloak 管理界面不等于 ACP 的节点、存储、任务与 Credit 管理界面；这些仍通过 ACP ACR 管理接口实现。PyCasbin 是权限库，不提供用户注册、进程隔离或计费系统；PostgreSQL RLS 是防御手段，不能替代正确的应用授权，owner/BYPASSRLS 等例外须考虑。

### 3.2 通用计费不等于可用的科学 Credit 准入

Lago 是有价值的通用计费候选，但其当前文档明确将 **Real time wallet balance** 标为 Premium 功能。不能仅因仓库开源就承诺免费取得实时余额、原子预留及全部企业能力。仓库显示 AGPL-3.0，采用前锁定版本/组件与适用发行方式；本评估不代替具体许可证审查。[官方钱包说明](https://getlago.com/docs/guide/wallet-and-prepaid-credits/overview)、[仓库](https://github.com/getlago/lago)。

ACP 必须拥有核时口径、可信 usage、预留/结算关联与重复执行政策。首期可在 PostgreSQL 上实现窄而严格的不可变额度账本/预留，不从头造订阅/发票/税务平台；以后需要通用商业账单再接 Lago 等，并预先确定唯一余额权威，不能两个钱包都扣钱。

### 3.3 HPC 平台不等于科学工作台

[Open OnDemand](https://www.openondemand.org/)是可复用的 HPC 用户门户，[其 Slurm 接入文档](https://osc.github.io/ood-documentation/latest/installation/resource-manager/slurm.html)展示了资源管理器集成。机构已有该门户时，可评估把 ACP 作为科学应用接入，复用机构账户/入口能力。

它不直接提供 ACP 科学任务契约、规范化数据/ACV 或 Credit 闭环。若用户只需要 ACP Web/CLI，不应同时再部署一套门户去复制已有工作台。Slurm 管物理调度，ACR 管研究任务、恢复与使用量关联，不能让两者分别成为同一 job 的权威状态机。

## 4. 已纳入计划的两版研究组合

### 4.1 轻量实验室版

```text
现有 ACP/ACR/CCCP
  + FastAPI（已有）
  + 嵌入式成熟密码/身份协议库，按需接权限库
  + PostgreSQL（真实多用户/账本场景；本地单用户可继续 SQLite）
  + 本地/已有集群存储与 OS 配额
  + 现有 LSF 或受控 Local 执行
  + Caddy（对外 HTTPS，已有入口可复用）
  + ACV 内现有 3Dmol；二维先保留可用 SVG
```

这档减少常驻服务，但注册/会话/撤销等用户用例仍需实现，不能把“几个认证库”说成零开发。如果更重视立即获得完整账户、找回与 MFA/SSO，可直接选 Keycloak，接受多一个身份服务的运维；不必同时维护自建密码登录和外部登录两套完整体系。

资源限制和用户工作区隔离是上线前置，不能因服务少而省略。支付关闭时仍保留配额、核时与审计，不强制安装计费平台/分布式对象存储/容器编排系统。

### 4.2 线上多用户/机构版

```text
Caddy → ACP/ACR
          ├─ IdentityProvider adapter → Keycloak（或机构已有 OIDC）
          ├─ Repository adapter → PostgreSQL
          ├─ Execution adapter → 现有 LSF / 新增经验证的 Slurm
          ├─ Isolation adapter → OS 身份/受控 Podman 等
          ├─ ArtifactStore adapter → 本地/集群盘，按需 SeaweedFS
          └─ Billing adapter → 既有 ACR 预留账本，按需通用计费服务
```

均挂在现有 ACR 三模块的 adapter/Port 下，不新增一圈业务层。Keycloak 不是科学/节点管理员，Slurm 不是 Credit 钱包，SeaweedFS 不是研究工件目录，Lago 不是科学完成判据。外部服务故障各自有明确不可用/待恢复语义，不悄悄退回无权限/无配额。

## 5. 仍应由 ACP/CCCP 保持的核心

- 科学任务请求/结果和唯一任务实现；输入角色、原子/帧/模式身份、NMR/PES/恢复证据与 typed unavailable。
- 平台 attempt、目录映射、release/submission、CAS 控制意图与停止确认、跨版本恢复和发布重试。
- 研究工件/候选/manifest、租户/项目权限、generation、引用与原地 rerun 的生命周期。
- 科学用量到核时/Credit 的可解释映射、准入/预留/结算与平台失败补偿；可复用数据库/计费引擎，但不把证据责任外包。
- Workbench 与 ACV 的研究交互、稳定科学图形映射及 CLI/Web/Agent 的统一语义。

## 6. 选型推进顺序与验证

1. **保持现有基础**：FastAPI、Paramiko、科学库、3Dmol 先收边界，避免引入开源项目反而重写现有代码。
2. **先账户和元数据**：小型本地认证或 Keycloak/机构 OIDC 二选一主路径；明确项目身份映射、个人 API、PostgreSQL 迁移和对象授权。
3. **先复用现有集群**：已有 LSF 不迁；新机构需要开源调度再做 Slurm adapter PoC，覆盖取消/暂停/用量/重启对账。
4. **存储和计费按需增加服务**：本地/集群盘先行，容量驱动对象存储；先完成科学核时与账户预留证据，再验证第三方账单接口。
5. **科学标准化独立试点**：选一个已支持任务验证 QCElemental/QCEngine/cclib 的字段、版本、数值/单位和完成证据；不批量替换 parser/backend。

每项 PoC 记录锁定版本、社区/商业功能边界、许可/资产、Python/OS 兼容、增加的服务和资源、维护方、恢复/备份、外部服务不可用行为、需要保留的 ACP 代码与回退办法。没有上述证据不列为冻结选型，更不把“服务启动成功”算作多用户/科学/收费验收。

**当前建议：先复用成熟基础库与已有集群；完整身份服务按部署规模引入，分布式存储/计费平台按需求后置；把开发投入集中到科学可复现性、任务恢复和研究工件/Credit 一致性。**
