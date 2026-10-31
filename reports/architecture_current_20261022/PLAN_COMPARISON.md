# 对照 2026-10-16 架构调整方案的实现评估

审查日期：2026-10-22。对象为当前工作树，包括未提交修改。设计依据为 [ACP_Architecture_Adjustment_Plan_20261016.md](../../docs/ACP_Architecture_Adjustment_Plan_20261016.md)，重点采用其 §17 完成定义、§18—21 独立产品要求与 §35—36 里程碑。本文是对照评估，不另设实施计划，也不改写历史验收结果。

**总体判断：科学底座独立化和前端职责拆分已经产生可验证的成果；平台服务迁移完成了重要基础，但完整控制端、默认数据链路和独立 CLI 产品尚未收口。按方案自己的最终完成条件，整体仍未完成。**

目前最接近方案目标的是 CCCP；最需要继续打通的是 Services 与应用入口、浏览器之间的实际调用链。多用户、MCP、项目包、计量等已有模块，但不能据此宣布对应产品能力已经上线。也不能将可选的 Hosted、Slurm、对象存储等后期能力，反过来作为基础本地产品不可交付的理由。

## 1. 评估依据与证据范围

本次逐项核对主方案、当前源码、发行配置、默认入口、交接台账及既有验收证据。源码结构与 CCCP 独立安装的详细分析见 [当前架构报告](REPORT.md)。可复现核对脚本为 [compare_plan_evidence.py](evidence/compare_plan_evidence.py)，输出为 [plan-comparison-audit.json](evidence/plan-comparison-audit.json)。

证据必须区分三个时间与范围：

| 证据 | 可支持的判断 | 不能外推的判断 |
|---|---|---|
| 当前架构审查的 230 项定向测试、当前源码重建 CCCP wheel、仓库外真实 xTB | 当前科学包、依赖隔离、所选架构/worker/产品契约；CCCP 独立安装和 Python 调用 | 全部工作流的真实科学执行、完整 GUI、生产部署 |
| 10 月 22 日修复候选的 10082 passed / 11 skipped / 95 deselected；17 项浏览器、329 项 Node | 对应冻结候选在已记录环境中的回归结果 | 当前所有未提交修改均已通过完整回归；跳过的真实环境能力已通过 |
| 当前源码及默认入口核查 | 模块在哪里、谁调用、哪些默认启用、发行依赖是什么 | 实际部署服务已经更新到该源码 |

本次将既有修复候选的 1,574 个源码、前端、测试、脚本及发行相关文件摘要与当前文件比较，发现 **12 个登记文件已变化**：7 个源码/部署脚本、3 个测试及2个台账。其中包含 API 装配、CLI、init、Workspace boundary。前述 10082 项完整回归不能不加限定地盖到当前工作树上。当前架构审查登记的源码/打包输入则未发现摘要变化，其 230 项测试和重新构建 CCCP 的证据可以沿用。完整差异名单已保存在审计 JSON。

本次比较没有重新运行完整非慢测试、真实 LSF 或生产验证；也没有修改产品代码、主方案、历史台账或服务部署状态。

## 2. 九个目标模块现在落实到什么程度

方案要求的是三层、每层三个逻辑模块，不要求九个独立进程或九个独立 Python 包。现有科学四层执行栈与这个目标可以并存。

| 目标模块 | 当前落点与实际成果 | 对照评价 |
|---|---|---|
| Workbench | `frontend/js/workbench/`；任务/详情/候选控制器、业务 features、四段 wizard；默认 HTML 实际加载 | **主体拆分已落地**。兼容 UI 状态仍在；新平台能力的界面接线另行验收 |
| Client | `frontend/js/client/`；transport、共享资源状态、查询、命令、事件和轮询策略 | **基础实现已接入**。持久事件 A04 与 prepare/availability 消费未完成 |
| Visualization / ACV | `frontend/js/visualization/`；3Dmol adapter、scene instance、framing、图表；默认页已加载 | **主干拆分已落地**。正式浏览器与实例/图形验证已有证据；性能和全部部署环境另验 |
| Workspace | `acp_services/workspace/`；项目、身份、权限、密钥和部署边界模块；local-single 边界已接 API | **本地基础与模块已交付**。生产多用户身份、权限/配额全链和 Lab/Hosted 验收未完成 |
| Runtime | `acp_services/runtime/`；冻结执行、worker、回执、恢复、计划契约、资源模块；store/CAS 已内移 | **部分闭环已接入**。完整控制端仍借用 ACP 实现；普通提交默认新执行链仅 singlepoint |
| Data | `acp_services/data/`；持久 prepare、传输校验、读取、可用性、存储、归档/导入 | **后端基础较完整，用户链路未收口**。旧 GET 与新读取面并存；前端尚未消费新协议；项目包无完整默认入口 |
| Calculation | `cccp/calculation/tasks/` 的唯一任务实现；TaskRequest / Context / Result、batch | **核心独立性成立**。15 个任务实现模块可调用；ACP 的复杂工作流与新 worker 覆盖是另一维度 |
| Analysis | `cccp/analysis/`；NMR、PES、结构、采样、科学产品、有界数据与身份、可信插件样板 | **重要科学实现已归位**。旧路径兼容转发保留；实际软件/模型环境按各自证据声明 |
| Software | `cccp/software.py` + `cccp/backends/` + `cccp/qc/`；发现、能力声明、输入翻译、执行与解析 | **符合逻辑职责目标**。能力矩阵存在不支持/桩状态；不是每个软件的每种能力均可运行 |

前端实际装配见 [HTML](../../frontend/ACP_Workbench_v2.html:1222)。科学唯一实现、反向依赖、身份转发等有 [架构护栏](../../tests/test_architecture_invariants.py)、[CCCP 隔离](../../tests/test_cccp_isolation.py) 与 [Services 隔离](../../tests/test_acp_services_isolation.py) 支撑。

**这些变化有实际价值：** 科学算法可以脱离 ACP 平台复用；执行进程可以不安装 ACP 应用、不接触 jobs DB；前端网络与图形适配拥有明确入口；已有兼容调用者可以继续通过旧路径工作。尚无足够同样本性能对照，因此不宣称延迟、吞吐或维护成本下降了某个百分比。

## 3. 与目标仍有距离的五个关键点

### 3.1 独立 worker 已成立，完整 Services 控制端尚未独立

方案 §5、§9、§19 希望 Runtime 拥有完整任务流程。当前 [ServicesPorts](../../src/acp_services/bootstrap.py:36) 仍有 **10 个过渡端口**：tasks、task_views、control、planning、publication、catalog、remote_paths、remote_release、remote_submission、projects。

[RuntimeCommands](../../src/acp_services/runtime/commands.py:44) 的提交、暂停、取消、重跑等动作继续转给注入端口；[API lifespan](../../src/acp/api/server.py:285) 仍实例化 `acp.scheduler.manager.JobManager`。规划、发布、远端执行装配也仍有 ACP 实现。

这满足无反向 Python 导入的边界，但运行时仍依赖 ACP 提供控制能力。因此应分别表述为“科学 worker 已独立”和“完整平台控制端迁移尚未完成”。保留纯兼容 shim 不影响完成；这些仍承担实际控制职责的实现才是需要继续处理的部分。

### 3.2 8 个工作流科学闭包，不等于 8 个默认切换，更不等于 15 个全部切换

[执行能力表](../../src/acp_services/runtime/execution_chain.py:162) 登记 8 个 `integrated`：singlepoint、optimize、frequency、scan、irc、casscf、xtb_optimize、OrcaGradient；另外 7 个保留 legacy。

但 [DEFAULT_ENVELOPE_WORKFLOWS](../../src/acp_services/runtime/execution_chain.py:217) 当前只有 **`singlepoint`**。实际选择还要求冻结组存在、目标执行协议支持。其他工作流科学闭包、普通提交启用、远端支持必须分别判断。

因此主方案 §45.1 的“8/15 envelope 启用”容易造成误解，应按当前代码理解为 **8 个闭包登记，1 个普通提交默认启用，7 个明确 legacy**；其余已登记闭包仍待各自默认启用证据。15 个现役工作流可继续通过现有路径运行，并不意味着 15 个都已通过独立 Services worker。

CCCP 还有一个小而具体的公开入口不齐问题：`xtb_constrained_opt` 的任务模块可导入，但 `cccp.calculation` 根导出缺少对应 `run_*` 与类型；[通用 worker](../../src/acp_services/runtime/worker.py:344) 按根导出查找 runner。该工作流现在仍走 legacy，不能据此说它当前不可用；未来启用新链前需要补齐。复现和影响见 [架构报告](REPORT.md)。

### 3.3 Data 已有新能力，但默认读取链还没有全面满足只读快照目标

后端已有 availability/prepared 端点、持久准备、generation/身份校验、后台预取等实现。前端契约仍明确 [prepareAvailability: false、eventCursor: false](../../frontend/js/workbench/contracts/task_view_contract.js:123)，新协议未成为默认消费路径。

更直接的例子是旧读取辅助函数 [_job_read_root](../../src/acp/api/v1_routes.py:441)：远端终态任务进入该路径时仍同步调用 `cache.fetch_catalog(...)`。因此不能说“所有 GET 都已只读且不触发远端传输”。这是保留结果可见性的兼容路径，后续应在 prepare、后台重试和可用性展示形成完整替代链后再切换。

现有按任务 SSE 的序号/断线续读也不能等同于方案要求的 A04 持久事件协议。Client 需要消费真实服务的游标、重置、重连与失效规则，再按覆盖范围收掉对应轮询。两项交接仍明确为 `NOT_VERIFIED`，见 [交接台账](../../tests/baseline/services-evidence/handoff_registry.md)。

### 3.4 三个发行角色已经有了，但还没有对应三种完整产品形态

| 方案 §18—19 的产品形态 | 目前发行事实 | 判断 |
|---|---|---|
| 独立 CCCP 科学库 | `cccp` wheel/sdist，只含科学代码与资产；无 ACP/Services/Web；Python API 真实 xTB 验证 | **独立构建、安装和调用成立**；公共仓库发布状态未核实 |
| Services + CCCP 完整无界面 CLI | `acp-worker` 包含 Services，只有 `acp-worker` 入口；完整 `acp` 入口在 `acp-web` | **尚未形成目标中的独立 CLI 发行角色** |
| 完整浏览器 Workbench | `acp-web` 包含 ACP 应用与静态资源，依赖 CCCP/worker，并强制 Web/远端依赖 | 已有制品和安装后 HTTP/资源证据；完整产品与生产验收仍按范围进行 |

配置见 [packaging/README.md](../../packaging/README.md)、[worker 角色](../../packaging/roles/worker/pyproject.toml)、[Web 角色](../../packaging/roles/web/pyproject.toml) 和 [构建依赖](../../scripts/build_release.py:49)。

现有根组合发行的 `acp` CLI 本来可以在没有浏览器、没有运行中 Web 服务时使用，不能说“CLI 不能独立运行”。缺的是方案要求的**完整 CLI 独立发行边界**：安装它不应被迫带前端静态资源和 Web 角色。独立 worker 也不能代替带摄入、规划、控制、恢复、prepare、导出的完整 CLI 产品。

CCCP 不提供 `python -m cccp` 完全符合方案 §19.1；不属于缺陷。裸 `pip install cccp` 是否取得本项目的发行版本，本次没有验证，不作为推荐安装证明。

### 3.5 模块交付与产品接入之间仍有明确欠项

[交接台账](../../tests/baseline/services-evidence/handoff_registry.md) 保留六项未收口能力：账户/权限/配额生产接线、MCP 默认入口、项目包 CLI/API、计量生产接线、持久事件 A04、前端 prepare/availability 消费。

当前源码与此基本一致：MCP 有服务适配模块，未绑定默认 CLI/API 入口且无真实客户端联调证据；Data 归档/导入有构建实现，缺少完整用户入口；计量解耦有模块和测试，未成为 Runtime 完成/恢复/读取的生产闭环。已有任务 ZIP 下载或“归档”标记不等于方案 §32 的可迁移科研项目包。

Workspace 已有 local-single Origin/CSRF 和档位门，不能说访问边界完全没有实现；但 [OIDC_SUPPORTED](../../src/acp_services/workspace/identity_lifecycle.py:166) 仍为 false，多用户授权、撤销、配额竞争、进程/存储隔离的整体上线结论不成立。本地 cgroup 异常死亡恢复已有适配器，真实委派环境仍待验证。

这些缺口对不同里程碑的意义不同：M1 先收口本地科研流程，M2 再完成项目包与 MCP，M3 才验实验室多用户。收费与 Hosted 按需进入 M4，不能作为已交付的宣传能力，也无需为了 M1 先安装全部候选组件。

## 4. 严格套用 §17 的九项最终完成条件

| 条件 | 当前评价 | 主要依据或剩余门 |
|---|---|---|
| 1. 九模块 owner、入口、无循环、无需绕全局总线 | **部分满足** | 逻辑边界及反向导入门明确；完整控制端和过渡注入尚未收口 |
| 2. 科学唯一实现、CCCP 无平台反依赖、ACV 无网络/全量科学、页面无原生 3Dmol | **核心边界已有较强证据** | 科学迁移、独立安装、依赖护栏、前端适配已落地；不外推到所有插件/外部软件能力 |
| 3. 15 工作流、CLI、edit、attempt、CAS、恢复、发布和历史兼容 | **广泛回归有据，整体验收仍有缺口** | 现有路径与保护门保留；全部默认新链未交付；真实环境与完整版本兼容不能仅凭单测关闭 |
| 4. Data 只读、持久传输、发布/可用性和 worker 权限闭环 | **部分满足** | 后端核心有实现，前端未消费新协议；旧 GET 仍可同步取远端 catalog |
| 5. 本地/远端入口、release、依赖/模型与独立角色一致 | **本机部分已验，远端未闭环** | 三角色制品、资产与 xTB 有证据；完整 headless CLI 角色、真实 LSF 等仍需验收 |
| 6. 测试、真实科学、性能、备份、灰度回滚与维护齐备 | **未全部满足** | 固定候选回归与部分真实科学通过；性能同样本对照、实际部署/回滚等不足；当前有候选后修改 |
| 7. 独立产品与 Credit 分开验收 | **边界已区分，产品门未全部满足** | CCCP 独立性成立；PR-02 CLI 发行不完整；收费链未上线 |
| 8. 按档位验多用户、隔离、配额、API 和管理员能力 | **local-single 部分落地，Lab/Hosted 未验证** | 组件存在，不等于生产身份与隔离全链可用 |
| 9. Lab/Hosted 可复现部署、manifest、同输入科学与选型试点 | **尚不能判定完成** | 未见覆盖要求的完整双版本部署证据；候选组件只按实际采用范围验收 |

不能给这张表简单打“80%”之类总分：独立模块、默认调用者、科学环境与生产交付的权重不同，完成一个模块也不会自动关闭对应的端到端门。

## 5. 从 M0—M4 看，现在处在哪里

| 里程碑 | 当前判断 |
|---|---|
| M0 基线与主场景 | 已有丰富源码、测试和候选基线；固定科研场景的性能/资源预算仍需完整证据，不能只靠测试数量视为全部关闭 |
| M1 本地完整闭环 | 大量必需组件已交付：科学库、worker、前端模块、初始化、真实 xTB 和安装验证。**尚缺足以关闭整个 M1 的连续验收证据**：新用户 init → 真实科研任务 → GUI/CLI → ACV/报告，以及取消/重启/失败保留证据、独立 CLI 发行 |
| M2 可迁移与可调用 | 项目包和 MCP 等已有模块准备，但用户入口、项目往返与至少一个真实 MCP 客户端联调未闭环 |
| M3 实验室协作 | 访问、身份、资源和 LSF 基础存在；多用户/撤销/竞争/隔离/恢复与部署配方仍需真实验收 |
| M4 按需扩展 | 属后续按需求开放范围；不因尚未采用 Slurm、GPU、对象存储或 AiiDA 就判基础重构失败 |

当前合理的交付定位是：**已形成可独立使用的科学库和可运行的平台基础，正在补齐本地完整产品闭环；不能宣布整个三层平台及实验室/托管产品已经验收完成。**

## 6. 主方案中的哪些状态已经过时

主方案同时保留设计和多日实施记录，末尾的“当前”实际上对应各自记录日期，不能直接当作 10 月 22 日状态。

| 旧记录 | 本次核查后的正确理解 |
|---|---|
| §44 所引用的前端剩余收口状态 | 当前 scene instance 与 wizard 四段模块已由默认 HTML 加载；应以现有接线和新浏览器证据为准 |
| §45.1 “8/15 envelope 启用” | 科学闭包登记 8 个；普通提交默认新链只有 singlepoint。两种状态应分列 |
| §45.3 当时的 F-3 单项失败 | 后续修复与当前 worker/隔离测试已有通过证据；保留旧失败，不将它继续描述为当前未经修复的问题 |
| §45.4 独立 CCCP wheel 的 `gate OPEN` | 当前已经能构建、独立安装和真实调用；完整产品发行、默认安装切换与生产发布仍须另判 |
| §46 “尚未实施修复或重新验收” | 已有后续 debug、复核及 10 月 22 日修复/验收记录，这句话不再代表当前代码 |
| 早期四组合全 PASS | 后续更严格核查留下旧服务组合 FAIL，不能沿用较早全通过结论；也不能据此说新服务所有功能失败 |

四组合历史核查的明确结果为 F0/S0、F1/S0 FAIL，F0/S1、F1/S1 PASS，见 [后续四组合摘要](../architecture_debug_20261021/evidence/four-combinations/summary.json)。这是相应候选和受控范围的历史验收，当前比较没有重新运行四组合。

建议以后在主方案保留历史记录，再追加带源码身份的当前状态索引，分别列“模块已交付、默认入口已接入、真实环境已验证”。本文只提供核查结果，没有自动修改主方案或把 `NOT_VERIFIED` 改为 PASS。

## 7. 沿原方案继续收口的优先次序

1. **先关闭 M1 的实际用户链路。** 选一个明确支持的真实科研任务，从全新安装、`acp init`、提交、观察、可视化到证据报告连续验收，同时覆盖失败、取消和重启；GUI 与 CLI 消费同一科学结果。已有分项证据可以复用，缺的是连续链条。
2. **完成 PR-02 的独立 CLI 发行边界与 P4 控制端归属。** 明确哪些控制实现继续迁入 Services，哪些只是薄入口或长期 shim；保留 `acp` 命令兼容，避免创建第二个生命周期实现。
3. **完成 P2/P1 的默认数据消费。** 前端接 prepare/availability，验证重启重试、后台预取及旧 generation 拒绝，再逐资源移除 GET 隐式准备；A04 按真实协议独立接入。
4. **按证据逐工作流扩大默认 worker 集合。** 请求构建、科学闭包、完成/发布、恢复、远端支持分别达标后启用，不能仅扩大常量集合或用“8 integrated”替代行为验证。
5. **再按 M2/M3 收口项目包、MCP 与实验室协作。** 性能、真实 LSF、资源隔离、备份和发布回滚必须在对应支持环境执行；Hosted 与收费保持按需、单独验收。

评价当前重构应承认两件事实：CCCP 独立化、科学实现归位、前端职责拆分和部分执行契约已经落地；要把这些组件组合成方案承诺的完整产品，还需要接入工作和端到端证据。现阶段应把主要精力放到这些具体闭环上。
