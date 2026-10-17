# ACP 三层模块边界、统一接口与调用规范

v1.5 战略增补见[完整方案第 30—35 节](ACP_Architecture_Adjustment_Plan_20261016.md#30-产品定位与-aiida-借鉴边界)和[产品战略与差异化](ACP_Product_Strategy_and_Differentiation_20261016.md)：本地完整科研闭环优先，Lab 分 Standalone/Server 配置，Hosted 按需后置；保持独立运行内核，借鉴 AiiDA，引入受控插件、项目迁移与 MCP。新增能力均为待实施目标，v1.4 的设计补全与质量门保留。

架构设计 v1.4 已补齐预留恢复、租户存储映射、身份迁移和独立核时配额依赖；代码质量清理按[主方案第 29 节](ACP_Architecture_Adjustment_Plan_20261016.md#29-本轮代码质量与冗余清理交付门)及[源码审计台账](ACP_Architecture_Code_Quality_Audit_20261016.md)执行。当前为设计与静态审计结果，未实施运行代码清理。

实施总入口：[《ACP 完整架构调整方案》](ACP_Architecture_Adjustment_Plan_20261016.md)。工作包、阶段依赖、验收与回滚统一按该方案维护；本文保留架构/模块解释，不再形成独立实施顺序。

日期：2026-10-16。状态：**目标模块规范，未实施代码迁移**。承接[三层架构主设计](ACP_Three_Tier_Architecture_20261016.md)，落实已确认的前端 ACV/3Dmol、后端 CCCP 科学大数据处理方向。

本轮模块粒度已收敛为[《三层高内聚模块设计》](ACP_Three_Tier_Cohesive_Modules_20261016.md)：每层三个主要模块。本文的入口、查询、事件、适配等条目是内部组件职责，不再分别维护为独立业务模块；模块归并与调用方向以新稿为准。

产品化接口增补见完整方案第 18—21 节：CLI/Agent 进入同一 ACR 用例，Workspace 提供 Credit 账户/预留/结算公开能力，Runtime 提供受控资源用量与预算准入；CCCP/ACV 无账户计费依赖。

双版本与组件归位按完整方案第 26—28 节：实验室版 ACP Lab、线上多用户版 ACP Hosted 共用本架构，开源组件作为既有 owner 的内部实现/adapter；身份、调度、存储、绘图及科学适配各有唯一归属，不新增业务层。具体候选须 PoC/版本/社区功能与许可核验后才冻结。

## 1. 组织原则与当前状态

目前源码仍为 `frontend/`、`src/acp/`、`src/cccp/`；下列模块是现有职责的归并目标，不能视为新包已经存在或依赖已经清理完成。本文不建立新的科学执行根、不删除兼容 shim、不改变任务与磁盘协议。

**职责模块回答“谁拥有能力与数据”，内部子层回答“代码允许怎样依赖”，运行角色回答“谁在哪个进程执行”。** 不为每个职责复制一套 controller/worker/仓储，也不要求每个模块单独成为服务。

减少调用采用五条规则：

1. 同一模块内部步骤直接调用内部函数，不为每个函数造接口；跨模块只用明确公开能力。
2. 兄弟业务模块不任意串联；跨域流程由上级 process/use case 协调，共享纯规则放基础模块。
3. 数据有唯一权威写入者；其他模块取得只读快照、结果或命令回执，不能直接改其文件/表/私有状态。
4. 查询、命令、事件、科学执行分开。GET 不隐式运行计算/拉取远程文件，事件不替代事务和必要的同步顺序。
5. 每个边界使用提供方拥有的契约。统一的是协议、命名、错误与版本规则，不是把所有方法塞入一个全局 API/manager。

## 2. 第一层：三个前端模块内的五类组件

| 编号/内部组件 | 当前主要驻点 | 拥有的职责与状态 | 公开接口（目标示意） | 可调用 |
|---|---|---|---|---|
| F1 页面壳 `shell` | 主 HTML 的页面布局/导航、CSS | 导航、容器、面板布局、装配；不持有科学/任务权威数据 | mount/unmount、页面路由与组件挂载 | F2；app bootstrap 负责依赖装配 |
| F2 功能用例 `features` | 主 HTML 与 job_editor/task_input_workspace/structure_source_picker 等混合逻辑 | 任务选择、提交表单、候选保存、错误呈现、跨面板选择；有界 UI 状态 | actions/view-model；workspace 用例连接不同功能 | F3、F5 的公开 API；不直调 F4 |
| F3 数据客户端 `data` | 各面板现有请求/缓存/刷新逻辑的归并目标 | 资源副本、请求合并、版本比较、订阅、轮询策略、操作回执 | query、command、subscribe、prepare；资源只读快照 | F4；不操作 DOM/图形 |
| F4 通信 `transport` | api/apiRemote、fetch/EventSource 包装 | HTTP/SSE、序列化、超时/取消、网络错误分类 | request/openChanges/close | 浏览器网络 API、服务端生成契约；不反调 features |
| F5 可视化 `ACV` | SVG 图函数、structure/vibration viewer、3Dmol 使用处 | 二维/三维渲染、相机、图例、动画、临时交互与本地图片导出 | create/setData/setSelection/setStyle/resize/export/destroy；交互回调 | 自己的 renderer/适配器；无网络/业务模块调用 |

F3 内部的 ResourceStore 是共享资源缓存唯一写入口；QueryCoordinator 取数，ChangeCoordinator 合并事件，RefreshPolicy 控制刷新，CommandClient 管理命令回执。它们共用同一 store，不再给每个面板各建轮询和任务实体。

F5 内部：`contracts/specs/models → renderers → library adapters` 是职责示意；具体静态依赖由较高的 renderer 依赖底层契约、纯模型和 adapter。二维图与三维图是并列渲染能力，不相互调用；共用纯主题/选择/身份工具。只有 3Dmol adapter 操作 `$3Dmol` 和底层 viewer 句柄。

ACV 只保存自己实例的相机/动画/hover 等状态。选帧后通过回调给 F2，F2 调 F3 补齐数据，再 setData 给 ACV。ACV 不调用另一个面板、不读取全局 selectedJob、不直接保存候选、不在图内提交计算。F2 之间的联合动作由 workspace 用例协调，不能导入对方私有变量。

**静态资源呈现顺序**：ACP 提供容器，feature 将已加载的数据传给 ACV，ACV 在容器中渲染。不是 ACV 生成一套 HTML 应用再让 ACP 二次解析。

## 3. 第二层：ACR 三个高内聚模块

| 主要模块 | 内部组件归并 | 唯一权威 | 允许跨模块调用 |
|---|---|---|---|
| workspace 工作空间管理 | 项目、成员/权限、分子分组、工作空间配置、自己的查询/事件 | 项目组织与授权信息 | 不调用 runtime/data 业务实现 |
| runtime 任务运行管理 | 命令、planning、controller、plan_runner、恢复/完成处理、任务名称/标签/编辑、任务查询/投影/事件 | job 生命周期、attempt、计划与提交记录、任务元数据 | workspace 的授权接口；data 的输入/发布/工件接口；CCCP 公开科学入口 |
| data 数据资源管理 | 输入资产、publication、manifest、transfers、cache、结构来源/候选、数据查询/可用性事件 | 工件、缓存、传输操作与数据资源组织 | workspace 的授权接口；不反调 runtime，不启动科学任务 |

此前九项能力不删除，而是作为三个模块的内部组件；EntryPoints 与 bootstrap 是薄装配，queries/sync 各归自己的模块，跨模块共享日志只是基础设施。完整归并表见最新高内聚设计第 4 节。

模块内部保留 R1 入口 → R2 应用 → R3 领域/Ports、R4 实现 Ports 的依赖纪律。纯内部步骤可直接调用，无须为每个函数建立 Port；对外通过稳定类型化接口，禁止跨模块访问私有文件/表/变量。

### 3.1 执行模块内部只保留两种角色

- **controller**：拥有准入、提交身份、lease、外部观测对账和 CAS，负责确认进程/LSF 停止；接受用户意图但不能被旧观测覆盖。
- **plan_runner**：拥有步骤顺序、依赖、恢复、科学任务调用和回执生成；作为 worker 部署到本地或远端，不直接连接平台 jobs 数据库。

两者共享纯领域规则与协议，不共享可任意改写的内存状态。controller 派发版本化 ExecutionEnvelope，worker 返回 ExecutionReceipt。它们是同一执行模块的角色边界，无须各复制一套 workflow/registry/repository。

### 3.2 不把基础设施当成新的业务调度中心

`ports/`、`domain/`、`contracts/`、`adapters/`、`bootstrap/` 是中段模块的实现边界，不是各自能发起完整业务的“超级模块”。

| Adapter 能力 | 允许做 | 禁止做 |
|---|---|---|
| repository/UnitOfWork | 事务、CAS、序列化、记录操作/事件 | 擅自把观测解释为完成、跨模块盲改表 |
| local/LSF execution | 启动 worker、bsub/bjobs、暂停/停止、返回执行事实 | 编写科学输入、决定业务 profile、绕过 controller 置终态 |
| SSH/SFTP/storage | 连接、传输、校验、原子替换/已发布对象读取 | 从 getter 发起传输、偷偷重跑计算 |
| CCCP science adapter | 平台/科学契约转换，注入 TaskContext，调用公开科学 API | 调用 backends/QC 私有入口、定义另一套科学任务 |

LSF adapter 可依赖低层 SSH transport，SFTP adapter 也可使用低层连接；SSH 不能反向依赖 LSF 或 manager。连接池与目录分配都由明确 owner 装配，不在每个路由中各自创建实例。

### 3.3 模块内流程与跨模块发布顺序

FinalizeExecution 是 runtime 内部用例，规划、控制与回执直接在模块内协作；输入准备与工件发布通过 data 公开接口。data 内 transfer/publication/availability 组成完整内部流程，不反调 runtime。具体终态与 `result_publication` 顺序沿用现行协议，不能根据组件名称重排。

需要同一数据库原子的更新由外层 UnitOfWork 提交；文件/远程操作使用持久阶段和幂等恢复，不能假装受 DB 事务覆盖。终态副作用仍在终态 CAS 成功后执行。事件/投影可通过 outbox/持久变更记录解耦，但不为每个内部函数引入消息总线。

## 4. 第三层：三个科学模块内部的组件与执行子层

| 编号/内部组件 | 当前主要驻点 | 职责与写入权 | 公开性/可调用 |
|---|---|---|---|
| S0 科学基础 `foundation/contracts` | calculation/contracts/errors、utils/constants 等待归并定义 | 科学类型、错误、单位、身份/纯校验；无 I/O | 下层公共契约；无反向执行依赖；旧路径保持同类重导出 |
| S1 公开边界 `calculation public` | calculation 的公开导出、requests/results/context/progress | 请求验证、任务/分析能力说明、稳定科学入口；无平台记录 | 外部唯一科学门面；现有 run_* + TaskRequest/TaskResult/TaskContext；分析/摄入 API 按需增量公开 |
| S2 科学任务 `calculation/tasks` | 现有唯一 tasks 根、受限 batch | 单科学任务内部步骤、完成证据、取消/超时、授权工作区和科学回执 | 由 S1 调用；可用 S3/S4/S5，禁止平台编排和第二 tasks 根 |
| S3 科学摄入与结构 `chemistry/io` | cccp/io、acp/chem/intake/io 的科学部分 | 科学格式、结构解析/embedding、拓扑/原子身份及科学几何校验 | C3 科学能力；任务/公开入口调用，不存平台上传权限或项目标签 |
| S4 科学分析与数据产品 `analysis/products` | sampling/selection、PES/IRC/TS 科学 helper、NMR、原始结果派生中的科学部分 | 大数据解析编排、数值分析、抽样/分辨率产品、规范化点/结构/模式数据；授权科学产物写入 | C3 纯算法及数据产品服务；需要启动计算时交 S2 明确安排，不能隐式调用 S5/S6 启动程序 |
| S5 能力适配 `backends` | cccp/backends | 优化、频率、采样等能力选择与翻译；不直接 subprocess | C3 能力分支；调用 S6，不导入 S2 tasks |
| S6 软件接口 `qc/interfaces` 等 | interfaces、程序特定输入/解析与 Shermo 适配 | 统一科学参数到原生输入、执行适配、原生输出到科学结果 | C4；执行方法调 S7；只读解析可供 S4 使用，但解析路径不能启动程序 |
| S7 进程机制 `process runtime` | interfaces/runners 中待收敛的纯进程部分 | 启动、等待、日志、退出码、超时、局部控制 | C5；依赖标准库与基础控制契约；无 LSF/项目/工作流/方法策略 |

S3/S4/S5 同属 C3 的并列能力，不代表新增三个执行层。S2 可以编排它们，算法间确需共享时提取下级纯算法，不互相调用完整 NMR/PES 工作流。S0 只是基础，不是所有业务都要经过的执行步骤。

软件接口内部的纯 parser 与执行方法必须可区分，不能因为 S4 允许解析就让纯分析能启动程序。S4 使用与归一化科学相关的 parser，ACR/ACV 不得直接消费原始 ORCA 日志再复制解析。

规范化不丢弃软件特性：统一公共语义，软件专属能力用类型化选项和 capability 声明，原始文件保留。不支持/缺失/失败/未准备分别表达，不静默忽略参数或把空值填零。

## 5. 统一接口族

下列为接口族，不强制每族只有一个类或 HTTP URL；具体方法保持有类型、可发现、可校验，避免 `call(name, arbitrary_dict)` 任意分派。

| 接口族/拥有者 | 使用方 | 输入 → 输出 | 基本约束 |
|---|---|---|---|
| Catalog / ACR | ACP features、嵌入式客户端 | 工作流/资源能力查询 → 版本化目录与 schema | 科学能力从 CCCP 公共描述转换；工作流目录与科学任务 registry 分开 |
| Query / ACR | ACP F3 | 有类型查询、游标、筛选 → 快照/页/可用性 | 授权、只读、本地索引；无远程操作、原始科学解析 |
| Command / ACR | ACP F3 | 类型化意图、幂等键、适用 revision → OperationReceipt | 接受不等于完成；同键不同内容冲突；无任意路径/函数名 |
| Artifact / ACR | ACP F3、应用用例 | 工件引用/prepare 意图/窗口 → 可用性、操作或已授权数据块 | prepare 是命令，read 是查询；摘要/attempt/generation 验证；大数据数值处理交 CCCP |
| Changes / ACR | ACP F3 | 作用域、cursor → 已提交变更/重放 | 至少一次可去重；过期游标可快照重置；事件不写客户端新权威 |
| Execution / ACR | controller、plan worker | ExecutionEnvelope → 进度观测、ExecutionReceipt | job/attempt/submission/release/token 是平台 envelope 字段，不塞科学 options |
| Science / CCCP | ACR science adapter、独立 Python 用户 | 现有 TaskRequest + TaskContext → TaskResult/科学工件 | 不另建科学任务协议；新分析能力经 C1 公开；TaskContext 不序列化跨网 |
| Visualization / ACV | ACP feature | 科学数据/兼容投影 + 显示参数 → 挂载实例/交互/本地导出 | 不发网络请求、不执行科学分析；底层 3Dmol 对页面不可见 |

版本与错误按边界区分：产品 API、执行 envelope、科学结果、视图输入和磁盘 manifest 各有 schema version；release_id 不替代协议版本。错误至少区分请求/权限/版本冲突、未准备/暂不可达、科学未收敛/解析失败、取消/超时和渲染不可用。TaskResult 的科学状态不直接当作 JobStatus，必须由执行与完成规则显式映射。

## 6. 科学数据到 ACV 的契约

| 数据产品 | 必需语义 | 不应包含 |
|---|---|---|
| 序列/散点 | schema、产品身份/摘要、series_id/point_id、坐标与单位、参考系、缺失值、来源实体映射 | DOM ID、颜色、像素、JS 函数 |
| 分子/轨迹 | structure/frame ID、atom_uid、元素、坐标单位、已知键/拓扑来源、帧顺序/块索引 | 平台权限、3Dmol 实例、服务器凭据 |
| 振动 | 原生模式编号、频率单位、对应原子身份/顺序、位移与归一化约定 | 仅凭数组下标猜测模式、由前端产生的正式 TS 判据 |
| 大数据显示产品 | 范围/分辨率/抽样与算法版本、关键点保留、原点 ID、块摘要 | 无来源追溯的截断数组、全量数据假象 |

CCCP 定义科学契约；ACR 可添加平台工件引用、授权读取信息和兼容投影 envelope，但不改科学语义；ACP 数据层只做规范化缓存；ACV 做有限数据的显示装配。当前帧视图仍经 `acp.results.frames.to_node/to_annotation`，新科学产品通过受限适配接入，不能让 CCCP 导入 ACP 契约。

正式科学量、科学映射、全量聚类/降维/抽样一处实现于 CCCP。ACV 可保留轻量轴映射、像素裁剪、临时测量和几何草稿；这些不能作为正式判据或回写科学结果。需要更新科学分析时显式发分析命令，而不是图形函数暗中重算。

## 7. 允许调用矩阵与三条业务链

### 7.1 关键边界白名单

| 调用方 | 允许调用 | 明确禁止 |
|---|---|---|
| ACP 页面壳 | feature 的公开动作与展示组件 | fetch、$3Dmol、任务权威写入 |
| ACP feature | data、ACV | 其他 feature 私有状态、transport 直连、原始 QC 解析 |
| ACP data | transport、纯缓存/版本规则 | DOM、3Dmol、科学计算 |
| ACV | 自身 renderer/adapter 与纯显示工具 | transport、ACR/CCCP 执行、任务提交、私有页面全局变量 |
| ACR 入口 | 应用 use case/query | DB/SSH、CCCP 私有接口 |
| ACR process | 明确列出的子用例、共享 UoW | 无边界地访问所有模块内部或成为第二调度器 |
| ACR 查询 | 只读仓储/索引 | transfer.prepare、执行器、原始日志解析 |
| ACR plan worker | ScientificEngine Port、步骤/工件受限存储 | jobs DB、浏览器、平台 attempt 自增 |
| ACR science adapter | CCCP 公开 API | QC/backend 执行入口、平台参数混入科学身份 |
| CCCP tasks | 科学结构/算法/能力、C0 | ACR/ACP、项目状态、LSF 策略 |
| CCCP backend | 软件接口、科学基础 | task 模块、平台编排、直接 subprocess |
| CCCP 纯分析 | 纯算法、受限读写、只读 parser | 隐式启动软件/计算、前端图形库 |

bootstrap 可以创建并注入需要的对象，但不能写业务流程。架构检查对 bootstrap 的豁免只限装配，不能允许其成为任意绕过边界的出口。

### 7.2 提交执行链

```text
页面 → feature → data.command → transport → ACR 入口
→ SubmitCalculation（组织/权限、planning、持久操作）
→ controller 准入/派发 → plan worker
→ Science adapter → CCCP 公开任务 → 科学能力 → 软件接口 → 进程
→ 科学结果 → worker 回执 → controller 验证 → FinalizeExecution
→ 按现行语义 CAS/发布/持久后续工作 → 投影与通知
```

### 7.3 结果展示链

```text
页面 → feature → data.query → ACR query → 已准备产品快照
→ data 的只读显示数据 → feature → ACV → SVG/3Dmol → 页面容器
```

缺数据时另发 `prepare` 命令，由 transfer worker 处理；缺科学派生产物时显式安排 CCCP 分析。两者都不是 query 的隐藏副作用。

### 7.4 交互与刷新链

```text
ACV 选点/取帧回调 → feature → data.query → ACR 数据块 → setData
ACV 保存候选意图 → feature → data.command → ACR data 候选用例
已提交状态/工件变更 → ACR sync → transport SSE → data 合并 → feature → ACV 更新
```

回调与事件表示控制/数据返回，不是反向 import。SSE 不能替代候选保存回执，ACV 选点也不能直接变成服务端科学事实。

## 8. 唯一写入者与生命周期

| 数据 | 唯一权威写入边界 |
|---|---|
| job 生命周期/attempt | ACR execution 的 CAS 服务；命令受理的控制意图同样经既有守卫 |
| 项目/任务名称/标签/人工候选 | ACR organization；对应 revision 独立 |
| 科学请求/产物语义 | CCCP 定义，科学任务写授权工作区；ACR 保存冻结意图与平台关联 |
| 平台 manifest/发布登记 | ACR publication |
| 传输状态/缓存字节 | ACR transfers；临时完成后校验 generation 才可发布 |
| 投影/事件 | ACR sync/事务变更记录；均不得复活/覆写 jobs 权威事实 |
| 浏览器共享缓存 | ACP data 的 ResourceStore |
| 相机/动画/临时标注 | ACV 实例；正式保存经 ACR 命令 |

暂停、取消、原地重跑、租约和旧进程写入隔离继续按主设计与执行完整性四不变量。当前自动 NMR 报告仍保持无浏览器可生成 JSON/XLSX/PNG；旧导出器属于受控兼容能力，不成为第二 ACV 服务。

## 9. 迁移和边界验收

每个模块建立公开符号清单、依赖白名单、唯一写入者清单、输入输出 fixture、允许的基础设施能力和兼容入口。使用“唯一实现/旧调用切换/恢复与历史兼容通过”判完成，不能只检查目录名称。

优先切片：前端统一数据入口及二维 ACV 样板；3Dmol 调用和生命周期归并；ACR query/prepare 分离配套持久传输；执行入口与规划/发布边界收敛；最后逐项迁入 CCCP 的科学算法与规范化产品。保持已有实施阶段依赖，不在本规范中要求一次性拆包。

检查至少包括：页面无直接网络/3Dmol；ACV 无网络/全量科学分析；查询无远程拉取；业务用例无直连 SQL/SSH/进程；CCCP 无平台反向依赖；backend 无 tasks 导入；科学实现与常量唯一；TaskRequest/TaskResult/帧投影/磁盘格式兼容；CAS、恢复、发布重试与旧 attempt 迟到拒绝通过。现有架构门不关闭，新禁边仅对已切换范围启用明确 allowlist 并逐项收紧。

后续新增功能优先回答三个问题：**谁拥有这份数据？应该调用哪一个公开能力？是否需要上级用例协调？** 若只能通过访问私有状态或跨多个模块追调用链实现，应先调整职责，而不是再增加一层包装。
