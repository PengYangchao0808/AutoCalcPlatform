# ACP Services 组件 Manifest（现有 / 候选 / 试点 / 批准）

**日期**：2026-10-18 · **性质**：台账文档，不改变任何依赖、配置或代码。
**来源**：主方案 §27（九模块归属与基础设施边界）、§28.3（选型台账与冻结规则）、[开源复用评估](ACP_Open_Source_Reuse_Assessment_20261016.md) §2/§6。
**消费方**：并行计划二 todo 62—65（B12 逐组件门）以本 manifest 为组件基线；状态只能经固定版本 PoC 通过后按 §28.3 规则升级。

## 0. 状态语义与硬性约束

| 状态 token | 含义 | 当前数量 |
|---|---|---|
| `existing` | 已在代码/依赖清单中实际使用（有安装版本或 vendored 文件证据） | 12 |
| `candidate` | 评估文档列名的候选，**候选 ≠ 已批准 ≠ 已安装**；未写入任何依赖文件、未安装、未验证 | 16 |
| `pilot` | 固定版本 PoC 进行中（对应门控 todo 已开工且有样例证据） | 0 |
| `approved` | 固定版本 PoC 通过，并在同一切片更新 packaging/角色依赖/配置/护栏后（§28.3） | **0** |

- **批准规则**：`candidate → pilot → approved` 单向升级；`approved` 前不得进入 `pyproject.toml` / `requirements-node.txt`，不得宣称"已验证"。本轮**没有任何组件处于 pilot/approved**。
- **不新增默认依赖**：本 manifest 不引入任何新安装依赖。验证命令与结果（零命中，exit 1）见
  `.omo/evidence/acp-parallel-plan-2-services/task-5-acp-parallel-plan-2-services.md`：
  `grep -Ei "pwdlib|keycloak|seaweedfs|slurm|\blago\b" pyproject.toml requirements-node.txt` → 无命中。
- **列约定（机器可解析）**：一张主表、一行一组件、列序固定；
  `状态` ∈ {existing, candidate, pilot, approved}；`模块/边界` ∈ §27.1 九模块（Workbench/Client/ACV/Workspace/Runtime/Data/Calculation/Analysis/Software）或 §27.2 基础设施类别（§27.2-入口 / §27.2-数据库 / §27.2-SSH / §27-监控 / §27.2-备份）；
  `候选` 行 `证据` 列一律 `None`；`existing` 行证据列必须指向仓内文件或安装元数据记录。
- **版本口径**：`existing` 的"版本"为 2026-10-18 在本机 `python3.11 -m pip show` 观测的安装版本（环境证据，非发布承诺）；`candidate` 的"版本"为 `未锁定`（PoC 时锁定）。行为级验证一律 `NOT_VERIFIED`（本任务未运行任何组件级验证）。

## 1. 组件主表

| # | 组件 | 类型 | 版本 | 许可 | 模块/边界 | 角色 | 秘密处理 | 故障语义 | 回退 | 状态 | 门控 | 证据 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | FastAPI | 库 | 0.141.1（约束 >=0.115） | MIT（License-Expression，安装元数据） | §27.2-入口 | Web API 薄协议适配（acp/api）；不含科研/权限/计费业务分支 | 当前无认证秘密（API 无 auth 中间件，trusted-network 现状）；秘密注入未实现 | 服务启动失败 → `acp run serve` 不可用；本地 CLI/科学流程不依赖它 | CLI 全链路可用；不降级匿名写入口 | existing | — | `pyproject.toml:42-46`；安装元数据（证据文件 §A） |
| 2 | Uvicorn | 库 | 0.52.4（约束 >=0.30） | BSD-3-Clause（安装元数据） | §27.2-入口 | ASGI server，承载 FastAPI | 无秘密 | 同上，进程退出 → Web 不可达，任务状态不受影响 | 同上 | existing | — | `pyproject.toml:42-46`；安装元数据（证据文件 §A） |
| 3 | python-multipart | 库 | 0.0.32（约束 >=0.0.9） | Apache-2.0（安装分类器） | §27.2-入口 | FastAPI 表单/上传解析 | 接收上传体（任务输入文件），非凭据 | 上传端点报错；不影响已有任务 | 无上传替代入口（未实现） | existing | — | `pyproject.toml:42-46`；安装元数据（证据文件 §A） |
| 4 | Paramiko | 库 | 5.0.0（约束 >=3.0） | LGPL-2.1（安装元数据） | §27.2-SSH | 远程 SSH/SFTP 连接（Runtime 执行命令 + Data 字节传输共用低层连接，不复制连接池） | 接收 SSH 私钥路径/口令或密码，来源 `cccp` 配置（`~/.cccp.yaml`/config 源）；凭据不写日志/回执（现有约定） | 连接不可达 → 远程任务 FAILED/blocked 于提交或拉取阶段，有 submission 对账语义 | 本地 Local 执行路径始终可用 | existing | — | `pyproject.toml:47-49`；安装元数据（证据文件 §A） |
| 5 | 3Dmol.js | 前端（vendored） | 2.5.5（`frontend/js/vendor/3Dmol-2.5.5.min.js`） | BSD-3-Clause（`frontend/js/vendor/3Dmol-LICENSE`） | ACV | 分子三维 renderer；3Dmol 独占适配入口（scheduleViewerFraming/frame3DViewer） | 无秘密；不加载账户数据 | viewer 初始化失败 → 结构查看降级/报错呈现，不影响任务权威状态 | 无第二三维引擎（刻意不建）；2D 用现有 SVG | existing | — | `frontend/js/vendor/README.md`（sha512 锁定）、`frontend/js/vendor/3Dmol-LICENSE` |
| 6 | NumPy | 库 | 2.4.6（约束 >=2.1.0） | BSD-3-Clause 及捆绑件复合许可（安装 License-Expression） | Analysis | 科学数值基础 | 无秘密 | import/运算失败 → 科学工作流不可用（核心依赖） | 无（必需依赖） | existing | — | `pyproject.toml:25`；`requirements-node.txt:39` |
| 7 | RDKit | 库 | 2026.3.6（约束 >=2022.09.1） | BSD-3-Clause | Analysis | 化学信息学（embedding/composition/指纹） | 无秘密 | 同上 | 无（必需依赖） | existing | — | `pyproject.toml:26`；`requirements-node.txt:40` |
| 8 | SciPy | 库 | 1.17.1（约束 >=1.10） | BSD（安装分类器） | Analysis | 回归/统计（DP4 等） | 无秘密 | NMR 定量路径不可用；其他工作流不受影响 | 无（节点必需，见 requirements-node 注释） | existing | — | `pyproject.toml:27`；`requirements-node.txt:41` |
| 9 | Matplotlib | 库 | 3.11.1（约束 >=3.7） | matplotlib License（PSF-based，安装分类器） | Analysis | 报告绑图 | 无秘密 | XLSX 报告缺绑图；计算结果不受影响 | 无（节点必需） | existing | — | `pyproject.toml:28`；`requirements-node.txt:42` |
| 10 | openpyxl | 库 | 3.1.5（约束 >=3.1） | MIT | Analysis | XLSX 报告写入（core 依赖，非 extra） | 无秘密 | 报告写入失败显式报错（不允许静默跳过） | 无（必需依赖） | existing | — | `pyproject.toml:30-37`；`requirements-node.txt:43` |
| 11 | PyYAML | 库 | 6.0.3（约束 >=6.0） | MIT | Software | 配置文件解析（YAML 配置源） | 配置文件可能含路径/节点设置；秘密明文不在 YAML 建议实践内（未强制，NOT_VERIFIED） | 配置加载失败 → 启动/任务参数解析错误 | 内置默认 `_get_default_config()` 兜底默认值 | existing | — | `pyproject.toml:27`；`requirements-node.txt:41` |
| 12 | nmrglue | 库（nmr extra） | 0.12（约束 >=0.10） | New BSD（安装元数据） | Analysis | Bruker 原始数据读取（远程节点也安装） | 无秘密 | `--bruker` 路径不可用；`--spectrum` 路径不受影响 | `--spectrum` 输入模式 | existing | — | `pyproject.toml:50-52`；`requirements-node.txt:45` |
| 13 | pwdlib | 库 | 未锁定 | 未确认 | Workspace | 密码哈希原语（本地身份候选组件） | 目标：仅经 Workspace 秘密组件持有密码哈希参数；当前无代码 → 未实现 | 未实现 | 未实现（候选未接线） | candidate | todo 52 / 73（OS-03 身份 PoC） | None |
| 14 | Authlib | 库 | 未锁定 | 未确认 | Workspace | OAuth/OIDC 协议库（本地身份/机构 OIDC 候选） | 目标：client secret 经 env/配置引用，不落日志；当前未实现 | 目标：OIDC provider 不可用 → 登录明确失败，**不退回匿名/默认管理员**（§27.4） | 未实现 | candidate | todo 52 / 73（OS-03） | None |
| 15 | PyCasbin | 库 | 未锁定 | 未确认 | Workspace | 权限策略求值引擎（AccessPolicy adapter 候选） | 无秘密（策略数据非秘密）；当前未实现 | 目标：拒绝未知作用域；策略版本/撤销一致；不能两引擎各存成员事实（§27.4） | 现有窄规则接口（候选未接线时保持现状） | candidate | todo 52（授权 PoC） | None |
| 16 | Keycloak | 服务 | 未锁定 | 未确认 | Workspace | IdentityProvider 候选（Hosted/机构 OIDC 优先路径） | 目标：admin/client secret 经部署秘密管理注入；ACP 不复制密码；当前未实现 | 目标：身份服务不可用 → 认证明确失败，不退回匿名；业务数据不损坏 | LocalIdentity（本地身份主路径）保持唯一主身份事实来源之一，切换须 ADR | candidate | todo 73（OS-03 身份迁移 PoC） | None |
| 17 | PostgreSQL | 服务 | 未锁定 | 未确认 | §27.2-数据库 | Repository/CAS/账本存储候选；各模块拥有自己的表写权，bootstrap 管连接 | 目标：DSN/口令经 env/部署配置引用；连接池租户上下文不串用；当前未实现 | 目标：连接失败 → 明确不可用/待恢复，不静默降级无配额；迁移可演练回退 | 现状 SQLite（单 owner）继续可用，不因候选被移除 | candidate | todo 62（OS-04） | None |
| 18 | Caddy | 服务 | 未锁定 | 未确认 | §27.2-入口 | 自动 HTTPS/证书管理入口（薄协议层，不含业务分支） | 证书/私钥由 Caddy 管理；ACP 侧无新增秘密面；当前未实现 | 反代后端不可达 → 5xx；不影响科学任务状态 | 现有直连入口（loopback 绑定）保持 | candidate | todo 68（OS-11 运维组件） | None |
| 19 | Slurm | 服务/调度器 | 未锁定 | 未确认 | Runtime | ExecutionBackend 候选（无现有调度器的集群可选；LSF 保留不替换） | 目标：提交凭据经集群既有机制；ACP 不持有额外秘密；当前未实现 | 目标：提交不确定先按 submission 对账，不盲目重提；协议/能力不兼容拒绝派发（§27.4） | 现有 Local/LSF 路径 | candidate | todo 64（OS-06） | None |
| 20 | Podman | 工具/运行时 | 未锁定 | 未确认 | Runtime | ExecutionIsolation 候选（cgroup v2/rootless 容器隔离机制） | 无秘密（挂载最小化）；当前未实现 | 目标：不支持隔离级别 → 拒绝该部署档位；停止/退出可确认，非仅容器命令返回成功 | OS 身份/资源组隔离；不支持则不启用 Hosted 档位 | candidate | todo 64（OS-06） | None |
| 21 | SeaweedFS | 服务 | 未锁定 | 未确认 | Data | ArtifactStore 候选（S3 接口对象存储；容量驱动，非首期） | 目标：access/secret key 经 env/配置引用；租户授权在 ACP 侧；当前未实现 | 目标：存储不可达 → 明确不可用；临时写入/摘要/generation 后发布（§27.4） | 本地/集群文件系统 + quota（首期方案） | candidate | todo 63（OS-07） | None |
| 22 | Apache ECharts | 前端库 | 未锁定 | 未确认 | ACV | 二维图候选（系列图/数据映射；现有 SVG 若满足可继续） | 无秘密；不接账户数据 | 渲染失败 → 视图报错；科学产品 schema 不变 | 现有 SVG 渲染路径 | candidate | 并行计划一 OS-08（二维样板对比） | None |
| 23 | QCElemental | 库 | 未锁定 | 未确认 | Analysis | 科学模型/字段标准化候选（可选 adapter；不直接替换既有科学契约） | 无秘密；不加载账户/账单 SDK | 目标：缺字段/不支持版本 → typed unavailable，不默默换执行路径（§27.4） | 现有 TaskRequest/Result 契约 | candidate | 并行计划三 OS-09（CCCP PoC） | None |
| 24 | QCEngine | 库 | 未锁定 | 未确认 | Software | 科学程序执行/I/O adapter 候选（受控 backend adapter，不绕任务层） | 无秘密 | 目标：取消无效/版本不支持 → 明确不可用，不形成第二执行链 | 现有 cccp backends/qc 接口 | candidate | 并行计划三 OS-09 | None |
| 25 | cclib | 库 | 未锁定 | 未确认 | Software | 只读输出 parser 候选（先对照真实 fixtures） | 无秘密 | 目标：字段缺失/失败输出 → 显式缺失，不宣称全覆盖 | 现有 orca_parser 等既有解析 | candidate | 并行计划三 OS-09 | None |
| 26 | Lago | 服务 | 未锁定 | AGPL-3.0（仓库；评估 §3.2；Real time wallet balance 为 Premium 付费功能） | Workspace | 通用计费 BillingProvider 候选（仅经 Workspace adapter；首期 ACR 自有窄账本基线） | 目标：API key 经部署秘密管理；当前未实现 | 目标：账单服务故障不重复扣费、不改科学结果；不能内外同时扣费（§27.3） | ACR 窄账本/预留（首期基线，不依赖 Lago） | candidate | todo 61（OS-10，条件门：价格政策批准后） | None |
| 27 | Prometheus | 服务 | 未锁定 | 未确认 | §27-监控 | 指标采集/查询候选；最小必要指标，业务标签受控 | 无业务秘密；指标不得泄露租户/科学数据；当前未实现 | 采集失败不影响任务执行；**收费证据不依赖监控采样** | 无监控仍可运行（可选组件） | candidate | todo 68（OS-11） | None |
| 28 | restic | 工具 | 未锁定 | 未确认 | §27.2-备份 | 备份/恢复工具候选；输入是一致 DB 快照 + 关联工件/密钥版本 | 接收 repo 密码/云凭据（部署秘密）；当前未实现 | 工具不自动保证跨存储一致性；恢复后须先对账在途执行再开放派发 | 现状：todo 4 的关联备份/恢复演练方案（非 restic） | candidate | todo 68（OS-11；依赖 todo 4 演练） | None |

`许可=未确认` 表示仓内权威资料未锁定该候选许可证，PoC 时须按 §28.3 记录上游官方许可与社区/付费边界；不在此猜测。
`python-multipart`/`zipstream-ng` 等 api extra 支撑库仅列已安装且服务链路直接使用的组件；dev/browser extra（pytest/ruff/mypy/playwright/httpx 等）为开发工具，不进服务组件面，此处不逐项登记。

## 2. §27.1 九模块归属映射

| §27.1 模块 | existing 组件 | candidate 组件 | ACP 保留职责（摘要，详见主方案 §27.1） |
|---|---|---|---|
| Workbench | （现有 HTML/JS/CSS 页面本身） | — | 研究交互、错误呈现、跨面板协调；provider 登录 UI 不替代科研管理板块 |
| Client | 现有浏览器 HTTP/SSE、api/apiRemote 包装 | — | ResourceStore、幂等/事件/版本/共享取消 |
| ACV | 3Dmol.js 2.5.5、现有 SVG | ECharts | 统一 API、原子/帧/模式映射、相机/动画/销毁 |
| Workspace | —（身份组件均为候选） | pwdlib、Authlib、PyCasbin、Keycloak、PostgreSQL（仓储）、Lago（BillingProvider） | 身份稳定映射、项目/成员、授权、个人 Key、预留与账本；一个主身份事实来源 |
| Runtime | Paramiko/SSH（+既有 Local/LSF 适配器） | Slurm、Podman、PostgreSQL（CAS 仓储） | 任务/attempt/plan/release/submission、停止确认、科学回执/恢复、预算证据 |
| Data | 本地/集群文件系统 + quota、Paramiko/SFTP | SeaweedFS、PostgreSQL（资源元数据） | 工件/来源/候选、manifest、租户授权、generation、传输/配额对账 |
| Calculation | CCCP TaskRequest/Result/Context 与 tasks（现有实现；科学基础库 numpy/rdkit/scipy/matplotlib/openpyxl 支撑） | — | 单科学过程、完成证据、取消/超时、预算内步骤 |
| Analysis | NumPy、SciPy、RDKit、nmrglue、Matplotlib、openpyxl | QCElemental | 全量数据处理、NMR/PES/采样算法、规范化产品、不确定性与来源 |
| Software | 现有 backends/QC 接口与软件发现、PyYAML（配置） | QCEngine、cclib | 原生 I/O、程序特性/版本、线程预算/日志/取消、科学解析证据 |

基础库不因归属表被移为另一个可导业务层；归属只确定 owner（主方案 §27.1 尾注）。

## 3. §27.2 基础设施边界映射

| §27.2 基础设施 | manifest 组件（状态） | 管理边界 | 使用方式与限制（摘要） |
|---|---|---|---|
| FastAPI/Uvicorn（已有）、Caddy（候选） | FastAPI existing、Uvicorn existing、python-multipart existing、Caddy candidate | ACP entrypoints/bootstrap 与部署脚本 | 薄协议适配/HTTPS，不含科研/权限/计费业务分支 |
| PostgreSQL | PostgreSQL candidate | 各模块拥有自己的 repository/表写权，bootstrap 管连接资源 | 一个实例多 owner，无跨模块盲写 |
| Paramiko/SSH 低层连接（已有） | Paramiko existing | 受限基础设施；Runtime 执行命令、Data 字节传输 | 不复制连接池；bsub/kill 执行策略不进 Data/通用连接器 |
| cryptography 等成熟安全库 | （暂无直接依赖行；Paramiko 传递依赖持有） | Workspace 秘密组件与 Data 安全存储 adapter 按用途使用 | 无手写密码协议；CCCP 无平台密钥与账户依赖 |
| Prometheus（候选） | Prometheus candidate | ACR 运维/metrics adapter | 最小必要指标；不作为核时扣费或科学回执权威 |
| restic（候选） | restic candidate | 部署/备份恢复组件 | 输入为一致 DB 快照和关联工件/密钥版本 |
| Open OnDemand（可选集成） | 不入本轮 manifest 主表（未评估版本/许可） | 机构入口 adapter/部署方案 | 非 ACP 必装门户；不产生第二套任务/账户权威 |

同一服务被多个模块使用不改变依赖图（主方案 §27.2 尾注）。

## 4. 门控消费说明（todo 62—65 及相关）

| 后续 todo | 依赖的 manifest 行 | 进入条件（未过不开工） |
|---|---|---|
| todo 62（PostgreSQL） | #17 | 实际需求确认 + 固定版本 PoC；通过后才可 `approved` 并更新 packaging |
| todo 63（对象存储/SeaweedFS） | #21 | 容量/多节点实际需求 + PoC（摘要/generation/权限/硬配额/故障恢复） |
| todo 64（Slurm/Podman/GPU） | #19、#20 | 逐 adapter 实证；不支持则拒绝、不静默降级 |
| todo 65（Hosted 组合门） | #16、#17、#19、#20、#21 | 仅启用完整隔离+可信用量+运维+预算门的组合 |
| todo 61（Lago，条件门） | #26 | 价格政策批准 + B10 通过后才试点；社区/付费边界已标注 Premium |
| todo 68（Caddy/Prometheus/restic） | #18、#27、#28 | 按采用范围集成验证；收费证据不依赖监控 |
| todo 52/73（身份） | #13—#16 | 本地 vs Keycloak/OIDC 二选一主路径 PoC |

升级到 `approved` 的同一切片义务（§28.3）：锁定版本、更新 packaging（`pyproject.toml`，`acp run` 运行时依赖同步 `requirements-node.txt`）、角色依赖、配置、等效护栏；Web/IAM/账单组件不得误入 CCCP 最小科学发行。

## 5. 事实边界声明

- 本文件所有 `existing` 行仅证明"已声明/已安装/vendored"，**不构成行为级验证**（NOT_VERIFIED）：未在本任务中启动服务、发起 SSH、渲染分子或跑科学 fixture。
- 所有 `candidate` 行**未安装、未配置、未测试**；`证据=None` 是刻意值，PoC 证据落位后再改状态与证据列。
- 许可列中 `未确认` 项在 PoC 冻结前必须补上游官方依据；Lago 行的 AGPL-3.0 与 Premium 边界来自评估文档 §3.2 的已核验记录，不等于采用决定。
