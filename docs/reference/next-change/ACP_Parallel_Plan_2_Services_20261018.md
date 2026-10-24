# 并行计划二：Services 平台服务与交付

日期：2026-10-18。负责人：服务路 B，同时承担共享文件的集成写入。共同规则见[并行索引](ACP_Architecture_Progress_and_Parallel_Index_20261018.md)。本清单与 A 前端、C 科学可同时启动；独立 worker、真实多用户与收费保留明确退出门。

## 1. 目标与范围

实现 Workspace/Runtime/Data 的单向职责：`Runtime → Data → Workspace`，Runtime 可直接授权；查询/事件/仓储归各 owner。entrypoints/bootstrap/连接池只作受限支撑，不新增业务层或全局总 manager。

复用现有项目/任务/来源存储、CAS、目录/attempt、恢复身份、发布顺序和终态预取。新目标包为 `src/acp_services/`，旧 `src/acp` 保留同实现兼容入口；不创建已废止名称 `acp_runtime` 作为另一实现根。

默认写权：平台源码、CLI/init/config/部署、平台 API/协议、共享打包和集成文件。C 登记接管的科学专属文件归 C；混合 workflows/catalog/results 文件由 B 应用 C 的符号迁移补丁。所有切片保留十五 active、edit coverage、frames/manifest 和四项执行不变量。

## 2. 立即启动：owner、数据与观察

### B00：共同基线、契约、护栏和组件台账

关联：P0-01—05、CQ-01、OS-01、IN-01、M0。

- [ ] 固定 revision/dirty、Python/依赖/科学软件/解析器版本、配置来源、角色安装、公开/动态入口和现有测试证据；分别接收 A00/C00 地图。
- [ ] 固定两个主科研场景的输入/方法/机器/成功定义，记录请求/延迟/I/O/大数据规模、安装步骤和恢复基线；未测不填收益。
- [ ] 建逐符号迁移台账和公开 allowlist，冻结首片 Access/Data/科学输入输出及执行身份契约，登记共享文件单写者。
- [ ] 建关联 DB/任务/manifest/密钥版本的备份恢复样例和在途执行对账方案；恢复后先对账外部作业再开放派发。
- [ ] 组件 manifest 区分现有/候选/试点/批准，记录版本/许可/角色/秘密/故障/回退；不把所有候选加入 requirements。

退出门：首片契约可供 A/C 开发，基线证据可重现，现有 AST/grep/F4/恢复门保留；不要求全仓符号清点结束才启动 B01/B02 首片。

### B01：Workspace 访问边界与身份模型

关联：P4-02、MU-01/02/04/08/13、OS-03；先 local-single，Lab/Hosted 逐档扩展。

- [ ] 建明确本地 principal 和公开授权接口，原地接管项目/成员/分组/settings 的 owner；任务名称/标签仍由 Runtime，结构来源组织仍由 Data，revision 不混写。
- [ ] 将 user、tenant/project、role、tier、ResourceGrant、计费主体分开；所有敏感用例从验证身份获取范围，不信任客户端 tenant 字段。
- [ ] 实现适用档位注册/邀请/会话/禁用/API Key/首次管理员，复用成熟库；Key 摘要、范围/期限/撤销与凭据引用不进入科学载荷/日志。
- [ ] LocalIdentity/OIDC 采用固定主体 ID 与 issuer＋subject 映射，先做关联迁移预览、冲突/撤销/恢复/回退；不按邮箱或同名 role 自动合并/升权。
- [ ] local-single 验证 loopback/Origin/CSRF 等边界；公网档位缺认证/隔离时拒绝启用，不由请求自行降档。

退出门：本地模式不要求外部 IAM；多人模式授权/撤销可验证。Workspace 不反调 Data/Runtime，注册不暗中 SSH 或创建系统账户。

### B02：DataResourcePort、存储绑定和持久 prepare

关联：P2-01—05、MU-07/08、OS-07 基础、CQ-04；先复用现有本地/SFTP能力。

- [ ] 接管输入/工件/manifest/来源/候选/传输/缓存唯一写入口，提供类型化 DataResourcePort；保持 frame_candidates 权威、source UID、expected revision、旧 URL/相对路径。
- [ ] 将现有目录分配机制接到唯一 StorageBinding authority，Runtime 只冻结/消费；保留人类可读目录、远端映射、markers 和 rerun/continue 的同目录/回执归档。
- [ ] 把进程内预取提升为持久 operation：身份/幂等键、状态、租约、重启恢复、重试、失败原因、同资源去重、取消与可用性提交。
- [ ] 临时下载→内容摘要/大小/身份/attempt/generation 校验→缓存发布→可用性记录；拒绝旧 attempt、部分文件、权限撤销和迟到传输污染。
- [ ] 统一读取根，后台终态预取与启动恢复不依赖浏览器 `fetch=1`；只读新查询无 SSH/SFTP/QC 解析，大文件/Range/块查询有界。
- [ ] 先交持久 prepare、后台恢复及旧 Client 的显式兼容路径/版本迁移证据，再逐资源切 cache-only。不能删除当前 GET 获取行为导致 F0＋S1 不可用；过渡兼容面登记期限/调用者，未切完不宣称全量 cache-only。
- [ ] 逐入口验证下载/日志/Range/候选/来源/缓存/事件授权；存储预留包含字节/inode/scratch/上传/临时放大，并与实际硬限制对账。
- [ ] 多租户路径扩展先修订布局/AGENTS/等效门；覆盖同名、共享/独立根、旧映射接纳、重命名、软链接/越界、并发分配，禁止截末两层回退。

退出门：Data 不改 jobs、不运行科学解析或反向触发 Runtime；新只读查询与持久准备闭环真实通过，旧结果与旧客户端仍可用。

### B03：Runtime 查询投影与任务摘要

关联：P1-03、CQ-04。

- [ ] 复用 task-view/tasks/index 与现有摘要，接管后台投影更新；查询只读已经准备好的快照，去除同步扫描/解析回退。
- [ ] jobs 保持权威，无投影可显示基础事实；freshness/observed_at、availability 和独立 revision 明确，不伪造旧服务版本。
- [ ] 固定列表/详情请求预算，不能列表刷新逐行拉详情；按授权项目/过滤/分页范围构造查询。

退出门：query 不推进状态/传输/科学处理，旧 payload 和恢复矩阵兼容，A 可独立接入。

### B04：已提交事实的持久事件协议

关联：P1-04、P6-02 的事件部分。

- [ ] 复用每任务 JSONL/after_seq 事实，定义 owner、scope、schema、cursor、去重与过期重置语义；保存旧任务流兼容入口。
- [ ] 将事件与状态持久化顺序/幂等重放衔接，不能由日志或 SSE 取代 CAS，也不能进程内通知后丢失事实。
- [ ] 实现授权复用流及快照重置，区分日志、任务进度、摘要和 Data availability 的覆盖范围，交 A04 刷新策略消费。

退出门：重启/重复/乱序/断线/撤销可验证；事件描述已提交事实，不产生第二任务状态机。

## 3. Runtime、worker 与本地产品

### B05：原地归并命令、规划、恢复与完成处理

关联：P4-01、CQ-02/04/06。

- [ ] 从现有 manager/workflows/calculations 提取 Runtime 用例与规则，submit/pause/unpause/cancel/continue/rerun/edit 保持同一执行 owner。
- [ ] 保持 revision/status/attempt CAS、控制意图优先、停止确认、PAUSED 资源语义、终态持久化后副作用与幂等重试。
- [ ] 复用 plan/step identity、完成证据、resume_source 与 recovery fixtures；发布失败只重试发布，上游 blocked 阻断依赖者，诊断继续不作正常结果收养。
- [ ] 通过 Data 公共入口发布/写 markers/读工件，保持既有 result_publication 与 completed 时序；retired 内部算法待 C 提取后保留必要 shim。
- [ ] 按等价证据归并平台时间/原子写等纯 helper，禁止建立万能 utils 或跨 owner 私表访问。

退出门：一个状态机、一个科学任务根，旧 CLI/API/recovery/目录/编辑行为等价；必要跨 owner 事务通过窄操作/UoW。

### B06：ExecutionEnvelope/Receipt 与执行入口整链切换

关联：P4-03/04/05；契约/生成器重构可提前，独立切换依赖 C01 对应闭包通过。

- [ ] 定义 schema/兼容版本、job/attempt/submission/release、owner/fencing、冻结 plan、Data 绑定/摘要、资源预算；TaskContext 不序列化。
- [ ] worker 只执行冻结 plan/依赖/CCCP 调用并写回执，不直连 jobs DB、不自增 attempt；controller 校验资格后推进状态。
- [ ] 将本地 runner、远端 script_gen、catalog 参数转换、release/节点依赖/软件发现/脚本字符串入口一并切换，禁止新 Services 动态转调旧 `acp.cli` 绕隔离。
- [ ] 保留 bsub 前提交意图、scheduler markers、目录/release 身份、未知提交对账和旧在途 release 接管；旧 attempt/submission/release 回执拒绝。
- [ ] 根据 C01 闭包逐工作流启用，未满足的切片保持旧兼容路径但不声称整个 worker 已独立；最终覆盖十五 active 和既有本地专用限制。

退出门：真实隔离环境不依赖旧 acp 实现即可执行所支持闭环，本地/远端相关入口与 release 一致；无双派发。

### B07：完整 CLI、独立角色、acp init 与配置应用

关联：P4-06、P6-01、PR-02、OS-02、MU-11、IN-01/02/03/05；完整独立发行依赖 B06/C08。

- [ ] 保留 `acp run`/`python -m acp` 与旧参数，CLI/Web 共用 Services 用例；无前端/Web 服务/在线账本可完成本地摄入、计划、执行、控制、恢复和导出。
- [ ] 复用现有 init：统一使用方式/连接服务、部署档位、组件/能力、配置/secret 引用、计划预览/应用和 doctor；不要另建第二向导或 defaults。
- [ ] 提取无 Paramiko 的节点配置纯校验，交互/非交互共用 schema；本地无 remote extra 可用，无 TTY 不等待输入，未知字段和环境覆盖保留。
- [ ] 配置计划带版本/摘要，应用检查并发修改、原子文件/受限备份；跨系统操作持久分阶段和幂等恢复，明确部分完成和回退，不能承诺全原子回滚。
- [ ] 配置、安装依赖、远端 bootstrap、系统服务/身份/DB 迁移分开，具体外部动作在计划中审查授权；有在途任务/预留时阻止不兼容模式切换。
- [ ] 与 C08 联合调整 wheel/角色依赖、模型/notice 和节点依赖；Web/IAM/账单不进入科学最小安装，静态资源发行缺口单独验证。
- [ ] 交付可复现 Standalone 配方与 capability 报告，installed/configured/enabled/verified 分开；未验 Hosted 不展示可生产启用。

退出门：干净环境经 init→doctor→首个 GUI/CLI 科研闭环；缺外部科学软件时 typed unavailable。无浏览器/支付/外部服务的完整本地 CLI 验证通过。

### B08：机器接口、MCP、项目包与研究证据

关联：PR-03/04、§32/33、M2；可先固定契约，不等待全部 P5。

- [ ] 统一 JSON 输出、退出码、错误 code/params/retryable/诊断引用；dry-run/estimate 不启动科学程序，不消费核时。
- [ ] MCP 为同一用例的薄入口，锁定协议/客户端矩阵、工具 allowlist、身份/预算/幂等；长任务返回 operation/job，不暴露任意 shell 或让连接寿命决定任务存活。
- [ ] Data 提供 archive schema/一致快照/逐文件摘要、大小/相对路径、完整/产品/引用包；排除凭据与可直接用的主机路径。
- [ ] 导入限额暂存、校验路径/链接/压缩放大/schema/摘要，再原子登记新平台 ID/StorageBinding；幂等、保留科学来源映射，不收养外部活跃任务/lease/计费，不自动执行。
- [ ] Runtime/Data 形成输入/产生/派生/选择修订/复用的只读证据投影，科学派生字段由 C02 提供，人工修订不覆盖原结果。
- [ ] 结构化日志与诊断导出先落地，OpenTelemetry 可选；业务身份与 trace 分开，秘密/敏感内容脱敏，中英不改变方法枚举/科学 hash。

退出门：本地项目往返完整、重复/损坏/中断可处理，至少一个 MCP 客户端联调，GUI/CLI/MCP 复用同一科学与错误语义。

## 4. 实验室与按需托管

### B09：Lab Server 开通、资源隔离和管理员能力

关联：MU-03/05/06/09、OS-06、IN-04、M3；依赖适用 B01/B02/B05/B06。

- [ ] Runtime 消费 Workspace 开通意图，幂等调用 Data.ensure_namespace 并提交环境状态，失败可对账。
- [ ] 版本化用户/项目/Key 授权共同约束核数/内存/并发/排队/时长/节点/队列；派发前原子预留/重验，PAUSED 保留实际占用。
- [ ] 验证 Local OS/容器/资源组和 LSF 身份映射、最小权限与停止范围；不以 OMP_NUM_THREADS、同 UID 分目录或登录页代替隔离。
- [ ] 管理用例/审计区分账户等级与角色，普通 Key 无管理权；broker 如需要只提供受限操作，禁止任意 root shell。
- [ ] 用已验证 DB/HTTPS/LSF/存储组合提供 Lab init/doctor；先复用已有集群，Slurm 不阻塞 Lab 发布。

退出门：双用户越权/撤销/资源竞争/取消/重启、存储硬限制与个人 API 实证通过，A06 可接对应界面。

### B10：共同核时与 quota-only 闭环

关联：PR-05a/06、MU-10a、OS-06；独立于真实价格、Credit、支付。

- [ ] 冻结可审计计量口径、周期/时区/舍入、暂停/失败/重试与未知用量政策；未决政策保留显式配置/待确认，禁止隐藏默认政策。
- [ ] 本地/LSF 可信采集，UsageRecord 绑定 attempt/submission/资源/证据/政策；父子资源不重复计量，缺证据待对账。
- [ ] usage-only 明确管理员选择；quota-only 实现用户/项目共同核时预留/准入/核销、跨周期分段/恢复和耗尽停止政策。
- [ ] 采集/对账与科学完成/结果读取解耦；可信用量不是 progress、浏览器计时或监控采样。

退出门：配额并发/多 Key/未知提交/重放/跨周期可恢复，无价格/账本服务也可交付 Lab。

### B11：按需 Credit、托管预算与账单

关联：PR-05b/07—09、MU-10b、OS-10；条件后置，依赖 B10 和公开批准的价格政策。

- [ ] Workspace 采用定点最小单位、账户事务、稳定预留键/请求摘要、追加账本、结算/释放/冲正与按键查询；没有第二余额权威。
- [ ] Runtime 持久意图→原子预留→持久关联→派发→证据核对→幂等结算；响应丢失沿原键对账，提交未知或仅租约过期不提前释放。
- [ ] 验证并发预留、超预算追加、取消竞争、重复/迟到回执、平台故障重试、purge 保留证据和账本恢复；科学结果不受余额锁定。
- [ ] Web/CLI/Agent 使用同一账户/预算/账单；价格、暂停收费、失败补偿未确认不自行制定生产规则。
- [ ] Lago/支付仅在确有需求时经 Workspace adapter 试点，明确许可/社区边界；支付开关不决定共同授权/配额是否启用。

退出门：账务恢复/准入门通过后才可开放付费托管；未通过可保留明确不收费模式，不能宣传已可靠结算。

### B12：条件扩展数据库、存储与执行角色

关联：P6-02/03、OS-04/05/07、IN-06、§31.2、M4。

- [ ] 按实际需求验证 PostgreSQL owner 仓储、迁移、事务 CAS、lease/fencing/重复回执；SQLite 阶段保持单 owner，不用网络盘多写。
- [ ] 对象存储采用上传/完整性确认/登记发布与引用/保留/回收，不假定 POSIX rename 或 ETag 即摘要；与旧目录/绑定兼容。
- [ ] Slurm/Podman/GPU 等逐 adapter 实证，区分请求和实际分配，支持情况明确；不支持拒绝、不静默降级，GPU 价格另批政策。
- [ ] Hosted/distributed 配方只启用有完整隔离、可信用量、运维与所选模式预算门的组合；缺依赖不降级匿名/无配额。

退出门：选用组件有锁定 manifest、公开 adapter、故障/恢复/回退与支持清单，无需求的候选保持未启用。

### B13：集成、故障恢复与运维交付

关联：P6-04/P7、PR-10、MU-11/12、OS-11/12、CQ-07/08。

- [ ] 每片先定向契约/行为/恢复测试，再按范围执行既定非慢与适用 compileall；共享门变更接受 A/C 证据，不删除护栏换通过。
- [ ] 故障覆盖未知提交、重复/乱序回执、失联/崩溃、磁盘满、传输中断、旧 worker 仍写、权限撤销、预算竞争和恢复后外部作业对账。
- [ ] 验证 DB/科学原件/工件/密钥/适用账本的一致备份恢复；HTTPS/秘密/观测/备份组件只按采用范围集成。
- [ ] 与 A05/C10 分列模块/接入/真实功能，以及浏览器/科学软件/LSF/生产 PASS/FAIL/NOT_VERIFIED；同输入科学一致性不靠前端模拟证明。
- [ ] 保留旧导入 shim、历史 reader、模型/vendor/notice、恢复 fixture；逐项关闭台账并同步契约/AGENTS/部署/手册。
- [ ] 形成匹配 release/静态资源与在途任务兼容窗口；实际代码发布后按项目规范重启服务，unit 修改驻点仍为安装脚本。

退出门：对应 M1/M2/M3/M4 的实际支持范围通过，不把候选研究、local 测试或配方生成当真实托管上线。

## 5. 并行依赖与交付节奏

可立即并行准备 B00/B01 的本地边界、B02 本地 Data/持久操作、B03/B04 观察契约与 B05 现有用例归并；B07 的 init 纯计划也无需等待独立发行。B06 协议/适配可提前做，切换需 C01；完整 CLI 发行需 B06/C08。B08 随 M2，B09/B10 随 M3；B11/B12 保持条件门。

向 A 交付真实 API/错误/事件/prepare 样例与支持矩阵；向 C 交付独立 worker 的逐工作流阻塞闭包、科学接口需求和平台身份边界。双方可以按冻结契约独立开发，最终接入由本路写入对应平台文件。共享打包/架构门/主方案由本路单写，不能让三路同时改全局配置。

本路负责组织联合验收，不以它替代 A 的浏览器或 C 的科学黄金证据。现有测试入口按范围复用 scheduler/remote/execution-integrity/recovery/job-edit/manifest/init/packaging 系列；本次只拆文档，未执行测试或部署。
