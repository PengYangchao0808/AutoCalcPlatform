# ACP MCP Round 5 执行缺口实施计划

日期：2026-10-31  
状态：待实施  
只读核实基线：`8589ddd3fc16a58e0cf312b19f1a0a6b9fe2e992`

本文替代原草案的 PR 划分、实施顺序和验收要求。正文中的行号对应上述 revision；实施时以函数名定位，并记录最终验收 revision。

本次仅完成源码与现有 fixture 核实，未修改文件、运行测试、启动服务或提交真实计算。下文命令是实施后的验收命令，不是已获得的 PASS 回执。

## 1. 交付目标与范围

本批交付以下行为：

1. PESsearch/scan 的坐标定义只有一个有效来源；冲突请求在任何提交副作用前拒绝。
2. IRC 不再接受实际不生效的显式 `step`；多帧 XYZ 不得经首帧读取转化为正式端点。
3. preflight 与 submit 共用静态解析、规范化和校验结果；无法核实的来源明确报告，不能给出无条件可执行结论。
4. PES 候选发布具备真实来源、授权、事务性 revision、持久幂等和恢复能力；Batch 消费相同的已提交版本。
5. MCP 提供实际可达的查询、授权发布接口，在平台授权内完成“选点→发布→Batch”。
6. Batch 单结构限制、推荐禁用原因和有效容量可发现；状态与产物准备不再用含混字段误导客户端。

本批不做：

- 多结构 scheduler BatchOptimize 任务。
- 自动推荐算法启用、矩阵反应位点算法替代实现。
- 猜测 ACP `step` 与任何 ORCA 输入关键字的映射。
- 将轨迹末帧自动升级为收敛端点或已验证最低点。
- 新建任务数据库、第二候选权威或第二科学执行层。
- 用本批测试关闭现有完整产品、真实科学、安装或 Local/LSF 发布门。

范围限定：首批新增自动发布接口针对 **PESsearch**。普通 `scan` 的通用候选发布仍按现有 `frame_candidates` 权威处理；未接入本批接口前，discovery 必须明确“不支持通过该 PES 接口发布 scan 候选”。

## 2. 核实结果与优先级

| 项目 | 当前源码事实 | 实施决定 |
|---|---|---|
| 坐标覆盖 | `src/acp/calculations/pes/contracts.py:472` 解析两个字段，`:519` 优先返回 `coordinates`；`src/acp/calculations/pes/scan.py:330` 执行列表首坐标，`:484` 却用 `req.coordinate.n_points` 判完成 | **P0**，先消除双重权威 |
| scan 别名覆盖 | `_submission_scan_coordinates` 实际在 `src/acp/scheduler/jobs.py:373`，与 `scan_method_flags` 的优先级选择相同 | **P0**，同批覆盖 input/method 和别名冲突 |
| preflight 假阳性 | `v1_routes.py:9311` 已调用通用校验；PES 专用必填与语义校验仍主要在 `_prepare_bond_scan_input`（`:1708`） | **P1**，抽取共用只读解析层；P0 先接入坐标拒绝 |
| IRC step | CLI `src/acp/cli.py:235` 接受；内部 `irc/source.py:43` 默认注入；`orca.py:3500` 未消费 | **P0**，新执行显式拒绝，去掉内部默认注入 |
| IRC 首帧冒充 | `orca.py:1227` 使用通用 `read_xyz`；`file_io.py:54` 只读首帧；`orca.py:3567` 将其放入 `final_geometries`；任务层 `irc.py:447` 可重新物化为端点 | **P0**，不等待真实投诉样本 |
| IRC failed | `tasks/irc.py:253` 先发现，再按方向完成状态过滤；`:279` 才报无端点 | **P1**，区分发现失败、未收敛和关联不成立 |
| review revision | `review.py:506` 读 revision 后，`:519`、`:520`、`:555` 分别写结构、manifest、review | **P1**，需发布组提交与持久恢复，不能只加锁或单文件原子替换 |
| review attempt | `review.py:541` 将 selection revision 写入 `attempt` | 新记录分离 `source_attempt` 与 `selection_revision`；旧字段不重解释 |
| 推荐为空 | `scan.py:482`、`outputs.py:94` 主动置空；`engine.py:301` 也置空 | **P1**，报告 disabled；算法启用另立项目 |
| Batch 单结构 | `runner.py:1547` 固定 `single_flat`；`batch/engine.py:598` 检查一个 item | **P1**，补解析后的基数检查与拆分示例 |
| 并发限制 | `manager.py:448` 的 `max_running` 明确 unused；`:5381` 实际使用 `registry.local.max_jobs` | **P1**，公布实际限制；不把历史 running 数量直接判为调度缺陷 |
| prepare 初始 absent | `operations.py:1197` 创建 operation，`:1208` 为 QUEUED，`:1217` 为 ABSENT | **P2**，分离准备状态与文件可用性，增加受约束的本地快速路径 |

另有本批必须处置的条件性无效参数：

- native scan 忽略 `retry_count`、`retry_strategy`、`failure_policy`、`reuse_previous_geometry`，见 `orca.py:2317`。
- PES driver/optimizer 有重复的重试次数与迭代次数字段，实际执行主要取 optimizer，见 `pes/scan.py:744`、`:754`。
- PES `strategy` 经校验后未驱动不同执行分支，见 `workflows/pes_search.py:140`。
- `IrcOptions.initial_hessian` 被转发为同名 kwargs，当前 ORCA IRC 接口没有消费，见 `tasks/irc.py:614`、`backends/orca.py:329`、`orca.py:3500`。

上述发现必须进入参数处置表和行为测试，不能只补说明文字。

## 3. PR 总览、依赖和执行顺序

将原六个 PR 调整为九个，主要拆开发布事务、自动授权与体验改动，避免一个 PR 同时承担持久化迁移、权限开放和工具上线。

| PR | 优先级 | 交付内容 | 硬依赖 |
|---|---|---|---|
| PR-01 | P0 | 坐标规范化与冲突拒绝 | 无 |
| PR-02 | P0 | IRC 无效参数止损与首帧防护 | 无 |
| PR-03 | P1 | 共享只读校验、有效参数回执、消费者守护 | PR-01、PR-02 |
| PR-04 | P1 | IRC 逐方向证据与输出诊断 | PR-02；真实扩展依赖 sample |
| PR-05 | P1 | PES 发布事务、幂等、恢复基础 | 无；不开放 Agent 发布 |
| PR-06 | P1 | 受限自动授权、真实来源与 Batch 绑定 | PR-05 |
| PR-07 | P1 | Controller/MCP 查询和发布闭环 | PR-03、PR-06 |
| PR-08 | P1 | Batch 基数、推荐解释、有效容量与示例 | PR-03；最终 Agent 示例依赖 PR-07 |
| PR-09 | P2 | 状态 locator、live_status、prepare 快速路径 | 无 |

建议执行顺序：

1. **立即实施 PR-01、PR-02，可并行开发；合并顺序 PR-01→PR-02。**
2. 两项 P0 通过后，推进 PR-03；PR-05 可并行开发。
3. PR-04 可独立推进诊断与现有 fixture 回归；真实输出格式扩展等待 sample，不阻塞其他 PR。
4. PR-05→PR-06→PR-07 串行完成发布闭环。
5. PR-08 的 Batch、推荐和容量工作可在 PR-03 后与 PR-06 并行；最终闭环示例在 PR-07 后验证。
6. PR-09 可独立开发、合并，优先级低于 P0 和发布闭环。

“可并行”指实施任务之间的依赖关系，不授权启动额外 Agent、生产服务或真实计算。

### 3.1 每个阶段的完成定义

| 阶段 | Definition of Done |
|---|---|
| P0 止损完成 | 原坐标冲突与显式 IRC step 在新执行前拒绝；多帧首帧不能成为正式端点；正常省略 step 的 IRC 路径不受默认值误伤 |
| 共享校验完成 | 同请求、同来源快照的静态错误在 preflight/submit 一致；零副作用断言通过；参数处置表逐行有行为测试 |
| 发布基础完成 | 并发、崩溃、丢回包、远端部分写回均可对账；未提交发布组不能被消费 |
| 自动发布完成 | 平台验证授权；Agent 来源真实；Batch 固定消费来源 attempt/revision/digest；重试不重复扣限额 |
| MCP 闭环完成 | 真实 SDK 经实际 Controller/stdio 调用完成查询→发布→Batch preflight/submit；不以直接调用 Python writer 代替 |
| 体验完成 | 限制和未知状态机器可读；示例通过真实校验链；prepare 快速路径没有绕过身份与发布校验 |
| 真实投诉关闭 | 有对应原始 sample 或另行授权的真实运行证据，能够解释投诉中的具体失败；fixture PASS 不替代此项 |

## 4. 共用实施与验收规则

### 4.1 新提交与历史数据

- 历史请求、回执和 review 保持可读，不批量重写。
- 新提交、编辑重算、rerun 产生的新执行必须遵守新校验。
- 历史 checkpoint 的读取不因新校验失败；真正恢复执行前重新核对执行契约。无法安全兼容时明确拒绝恢复，要求显式重算。
- 不将历史 `step=0.1` 回执改写为“已执行步长”，不将历史 review 的 `attempt` 改解释为科学执行 attempt。
- 默认值缺省与用户显式提供必须可区分；不能先填默认值，再将所有请求判为显式无效参数。

### 4.2 副作用边界

静态拒绝必须发生在下列动作之前：

- 结构快照或任务目录写入；
- prepare operation 创建、远端 fetch；
- 审批消费、预算预留、幂等执行 claim 创建；
- job 创建、任务派发或 QC 子进程启动。

身份与访问检查可以先执行，但不得借校验创建执行事实。

### 4.3 验收命令约定

各 PR 下的命令从仓库根目录执行。新增测试文件均由对应 PR 创建。

普通回归默认使用：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" <测试文件>
```

测试必须在 `tmp_path` 下构造独立 Controller、数据库和任务树，隔离部署环境变量。真实 QC、模型、LSF 场景另行授权、另留证据，不包含在这些命令中。

每个 PR 留存：

- revision、命令、退出码、实际收集/执行/跳过数量；
- fixture 来源、构造方式和摘要；
- 关键行为断言与回滚验证结果；
- `PASS / FAIL / NOT_VERIFIED`，以及尚未覆盖的范围。

不得将空选择、跳过或纯 mock 测试写成对应真实链路 PASS。

### 4.4 独立合并与回滚约定

每个 PR 必须在其依赖已合并的基线上独立通过验收，不依赖后续 PR 补齐正常路径。

跨 PR 新能力通过显式、可选端口和版本能力发现连接。依赖未装配、被关闭或回滚时：

- 返回明确的 capability unavailable；
- 不回退到旧的不安全 writer；
- 不将 Agent 请求降级为 manual；
- 不影响无关工作流启动。

持久化迁移采用增量、向后可读方式。回滚不删除新记录、不降级 schema、不重写来源。已提交 Batch 使用其冻结输入继续运行。

这里的独立回滚包括“关闭该 PR 新能力并回退其实现，其他 PR 安全报告依赖不可用”；不要求依赖它的新能力继续可用。各 PR 必须验证这一降级行为。

## 5. PR-01：坐标规范化与冲突拒绝

### 5.1 目标与不做什么

目标：首先阻断坐标静默覆盖，并使执行坐标、主坐标和完成点数来自同一规范化结果。

不做推荐算法、自动发布、全工作流校验框架或新的扫描算法。

### 5.2 固定语义

PESsearch：

- 仅 `coordinate`：规范化为单元素有序坐标列表。
- 仅非空 `coordinates`：允许，主坐标为第一项。
- 两者同时提供：规范化后的 `coordinate` 必须与 `coordinates[0]` 完整一致。
- 两者不一致：返回 `422 input_invalid`，`rule=conflicting_fields`，列出双方字段路径。
- 不自动合并、不排序、不按“列表包含该坐标”放行。
- 比较 kind、原子顺序、合法规范单位、start/end、点数和显式 values。
- 不将显式 values 与缺省线性插值擅自认定为等价。
- 空列表、空对象、非法单位、非有限数、非整数原子索引及点数不能通过默认填充掩盖。
- 多坐标继续使用现有同步语义、点数限制和科学校验。

普通 scan：

- 保留现有字符串/对象 wire 语法。
- 汇总 `input`、`method` 中实际支持的坐标别名；重复定义只有规范化等价时才接受。
- 点数的顶层和 level 别名同样拒绝冲突。
- 不能让校验读到一个点数，而 argv 使用另一个。

### 5.3 改动清单

| 位置 | 改动 |
|---|---|
| `src/acp/calculations/pes/contracts.py:141`，`ScanCoordinate` | 严格类型、有限数和按 kind 的单位规范化；修正距离/角度单位别名不能交叉接受 |
| 同文件 `:472`，`PesScanRequest.from_dict`；`:519`，`scan_coordinates` | 建立唯一坐标规范化结果；兼容字段仅作为一致投影 |
| 同文件 `:753`、`:906` | 复用单坐标和同步坐标校验，避免第二套科学规则 |
| `src/acp/calculations/pes/scan.py:330`、`:484` | 执行、横轴和完成点数统一消费规范化结果；验证移到计算目录写入前 |
| `src/acp/scheduler/jobs.py:240`、`:373`、`:399`、`:471` | `scan_method_flags`、坐标/点数解析和校验共用解析结果 |
| `src/acp/api/v1_schemas.py:1555`，`BondLengthScanJobInput` | 支持 coordinates-only，但要求至少一个有效坐标来源 |
| `src/acp/api/submission_contract.py:839` | 在共用入口调用 PES/scan 静态坐标校验 |
| `src/acp/api/v1_routes.py:1708`、`:2664`、`:9259` | prepare、submit、preflight 使用同一规范化结果；不得各自选择优先级 |
| `src/acp/api/v1_routes.py:6142`；`src/acp/api/v2_routes.py:1042` | 编辑重算、v2 批量入口保留同样拒绝语义 |
| `src/acp/api/workflow_contracts.py:410`、`:476`；`src/acp/api/guidance_pack.py` | 更新坐标关系、错误和 coordinates-only 示例 |
| 新增 `tests/test_acp_round5_coordinate_contract.py` | 跨入口行为回归 |

若需要新增纯函数模块，放在 `src/acp/calculations/pes/normalization.py`；不放进 Services，不反向导入 ACP。

### 5.4 验收与 fixture

Fixture 来源：

- `tests/test_acp_submission_contract_first_batch.py::_pes_input`、`_scan_input`。
- 新增构造的十原子 XYZ 和坐标对象，复现 `[9,4]` 与 `[9,0]` 冲突；标记为根据投诉重建的请求，不冒称原始请求。
- 现有 PES 多坐标测试中的有效同步扫描输入。
- Local/remote argv 只构建和解析，不执行 QC。

必须通过：

1. 单字段、双字段一致、双字段冲突、coordinates-only、合法多坐标五组。
2. 改变原子顺序、单位、范围、点数或 values 任一项，不能静默覆盖。
3. preflight/submit 的冲突 `code/field/rule` 一致。
4. 拒绝时目录、数据库、prepare、预算与派发 spy 均无变化。
5. 正常请求的执行计划、生成输入和 profile 中的坐标、点数一致。
6. Local/remote argv 消费相同规范化结果。
7. 历史冲突记录可读；新 rerun 不复现静默覆盖。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp_round5_coordinate_contract.py tests/test_acp_submission_contract_first_batch.py tests/test_pes_search.py tests/test_scan_workflow.py
```

### 5.5 依赖、风险与回滚

无前置依赖。

主要风险是默认值与显式字段混淆、普通 scan 和 PES wire 被错误统一，以及历史请求被自动修正。

回滚时保留数据原貌；若必须退回旧实现，先关闭受影响扫描的新执行入口。不得在已知静默覆盖仍可达时恢复真实扫描。PR-03 等后续能力缺少规范化端口时应明确不可用。

**DoD：原投诉冲突请求不能进入队列；正常请求只有一份有效坐标和点数。**

## 6. PR-02：IRC 参数止损与首帧防护

### 6.1 目标与不做什么

目标：

- 拒绝新执行中的显式 `step`，包括顶层、level、CLI、typed options 和 capability extras。
- 清除平台自身默认注入的 step。
- 阻断多帧 XYZ 经 `final_geometries` 冒充正式端点。
- 同批拒绝 ORCA IRC 不支持的显式 `initial_hessian` 选项。

不新增步长控制、不扩展真实输出文件名识别、不修改 JobStatus。

### 6.2 改动清单

| 位置 | 改动 |
|---|---|
| `src/acp/api/submission_contract.py:237` | 对 `method.step`、`method.levels.irc.step` 返回明确 unsupported 错误；不只删除 allowlist 后报泛化 unknown_field |
| `src/acp/api/workflow_contracts.py:838` | step 从可执行参数改为明确拒绝项，给出省略该字段的修复路径 |
| `src/acp/catalog.py:750`、`:2480`、`:2494` | IRC schema/profile 不再默认生成 step；历史描述保留兼容解释 |
| `src/acp/cli.py:235`、`:5040` | 能区分显式 `--step` 与省略；显式值在执行前失败 |
| `src/acp/calculations/irc/source.py:43` | `VerifiedTsSource.method_payload` 停止注入默认 step |
| `src/acp/workflows/irc.py:47`、`:118`、`:236` | 清理资源和 checkpoint 的新执行 step 语义；旧 checkpoint 有版本化读取处理 |
| `src/acp/scheduler/runner.py:1697`；`remote/script_gen.py:473` | 两条 runner 不再生成默认 `--step`；非法历史 spec 在新执行前得到明确错误 |
| `src/cccp/calculation/requests.py:669`；`tasks/irc.py:591` | 保留历史反序列化；ORCA 执行边界拒绝显式 step/initial_hessian |
| `src/cccp/qc/interfaces/orca.py:3470` | 直接调用接口也在 mkdir、写 inp、复制 Hessian、启动进程前拒绝这两个未支持项 |
| `src/cccp/qc/interfaces/orca.py:1227`、`:3567`；`tasks/irc.py:414` | 端点读取必须验证恰好一帧、完整原子数/顺序、有限坐标；拒绝的文件不得从 final_geometries 回流 |
| 新增 `src/cccp/io/xyz_validation.py` | 将严格单帧验证放在共享低层，供 interface/task 使用；不改变通用 `read_xyz` 的历史首帧行为 |
| 新增 `tests/test_cccp_irc_boundary_contract.py` | 参数拒绝及首帧绕过反例 |

继续保留有效的 TS Hessian 文件交接链；不能把“不支持 initial_hessian 字符串控制”扩大为“禁用 hess_file”。

### 6.3 验收与 fixture

Fixture 来源：

- `tests/test_cccp_task_irc.py` 的 backend/result fixture。
- `tests/fixtures/irc/h2o2_IRC_F_trj.xyz`、`h2o2_IRC_F.xyz`。
- 构造的截断、多帧、NaN、原子顺序错误文件。
- `tests/fixtures/qc/orca61/ts_opt_bundle.json` 等现有 TS 来源 fixture。

必须通过：

1. 显式 `step=0.1` 也拒绝，不能仅拒绝非默认值。
2. 顶层、level、CLI、`IrcOptions.step`、extras 各入口均覆盖。
3. 完全省略 step 的正常请求可走到生成 ORCA 输入；没有平台补出的 step。
4. `maxpoints` 非默认值仍到达 `%irc` 的实际迭代限制输入。
5. 显式 `initial_hessian` 在 ORCA IRC 执行前拒绝；现有 `hess_file` 正常交接。
6. `_read_endpoint_geometry` 拒绝多帧；完整 interface→task 路径不能将其首帧物化为 `irc_f.xyz`。
7. 合法单帧端点继续通过；独立 typed 几何仍需数量、顺序和有限数验证。
8. 不改变全仓通用 XYZ 首帧读取语义。
9. 旧回执可读，新执行不会将旧 noop 字段宣称为实际步长。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_cccp_irc_boundary_contract.py tests/test_cccp_task_irc.py tests/test_irc_ts_source.py tests/test_qc_interfaces_orca.py tests/test_irc.py
```

### 6.4 依赖、风险与回滚

无前置依赖，可与 PR-01 并行。

风险是内部默认 step 导致正常请求全部被拒绝，以及修改 checkpoint fingerprint 后错误复用历史结果。新执行和历史读取必须分离处理。

回滚不得通过重新接受 step 或首帧端点恢复服务；必要时关闭受影响 IRC 新执行入口。端点解析扩展 PR-04 回滚不能撤销本 PR 防护。

**DoD：不存在已知“接受 step/initial_hessian 却忽略”的 ORCA IRC 新执行路径；多帧首帧不能成为正式端点。**

## 7. PR-03：共享只读校验、有效参数回执与消费者守护

### 7.1 目标与不做什么

目标：preflight、submit、编辑重算共用只读“解析→规范化→校验”结果，并把本批无效参数全部变成可执行的处置规则。

不将有副作用的 `_prepare_*` 整体搬进 preflight；不做通用 Python 数据流分析；不宣称全部 15 个工作流已完成消费者审计。

首批覆盖 PESsearch、scan、irc、tsmode，并为 PR-08 的 Batch 解析后基数检查提供同一入口。

### 7.2 改动清单

- 新增 `src/acp/calculations/submission_validation.py`：只读编排入口，返回规范化输入、有效方法、来源验证范围和未验证项。科学规则仍调用已有 owner。
- `src/acp/api/submission_contract.py:839`：保留通用字段契约；接入明确的 workflow 专用只读校验。
- `src/acp/api/v1_routes.py:1708`：拆分纯解析与来源物化；`_prepare_bond_scan_input` 消费已校验结果。
- `v1_routes.py:2664`、`:6142`、`:9259`：统一校验顺序；静态校验先于 snapshot、approval 消费和任务创建。
- `v1_routes.py:9053`，`_preflight_source_facts`：复用现有 IRC/tsmode 来源检查，增加来源版本、检查范围与未验证原因。
- `src/acp_services/entrypoints/application_params.py`、`controller_client.py:608`、`src/acp/services_mcp_bootstrap.py`：贯通新字段与版本，防止 DTO/MCP 投影丢失。
- `src/acp/calculations/pes/contracts.py`、`scan.py:346`、`:744`：统一执行模式推导、重复参数处理和实际参数回执。
- `src/acp/workflows/pes_search.py:62`、`:140`：处理 strategy 仅校验未执行的问题。
- 新增 `src/acp/api/parameter_effects.py`：从既有契约枚举字段，登记处置、适用模式、最终消费者和行为测试；不得另建允许键权威。
- 新增 `tests/test_acp_round5_preflight_parity.py`、`tests/test_acp_parameter_effects.py`。

### 7.3 preflight 返回语义

已知静态错误统一返回结构化错误，不能只放 advisory。

来源尚未就绪、不能在授权的 cache-only 读取中核实：

- 不触发 fetch、prepare 或快照。
- 返回稳定原因，如 `source_not_verified`、`needs_prepare`。
- 既有布尔 `feasible` 不得为无条件 true。
- 版本化响应增加 `validated_scope`、`unverified_checks`、契约版本及来源快照标识。
- 已有 v1 冻结响应通过兼容投影表达失败原因；新字段若超出冻结结构，须协商新版，不静默破坏旧客户端。

有效参数回执至少包含：

- 规范化坐标与主坐标、点数；
- 实际优化方法和单点方法；
- `native_scan` / `pointwise`；
- 参数别名、条件不适用项、拒绝项；
- 来源 attempt/digest 和校验契约版本。

回执的 requested/effective/executed 三个层次分别表示请求、规范化计划、实际执行事实。执行失败时不能预先填 executed。

改变摘要规则必须版本化；不得重算历史幂等记录并将其误判为新意图。

### 7.4 参数处置表

“消费”包含规范化别名与元数据消费，但 discovery 必须说明它是否影响科学执行。`legacy_noop` 仅用于兼容读取或明确公布的不适用缺省项，不能用来豁免用户显式科学控制。

| 参数/条件 | 处置 | 必须验证的行为 |
|---|---|---|
| PES `coordinate`、`coordinates`、`protocol.coordinate` 镜像 | 消费，规范化别名；冲突拒绝 | 执行、横轴和完成点数相同；镜像不覆盖主定义 |
| scan 的 input/method 坐标别名和点数别名 | 消费；不等价重复定义拒绝 | Local/remote argv 与校验结果一致 |
| scan 对象中当前无法传递的额外科学字段 | 拒绝 | 不能接收后在字符串 argv 化时丢弃；支持字段需有最终输入探针 |
| IRC `method.step`、`levels.irc.step`、CLI/typed/extras step | 新执行拒绝；历史记录 `legacy_noop` | 省略正常；显式默认值和非默认值均提前失败 |
| ORCA `IrcOptions.initial_hessian`、同名 extras | 拒绝 | 不到达进程；不影响合法 `hess_file` |
| IRC `maxpoints` 及 level 镜像 | 消费；冲突拒绝 | 非默认值进入实际 ORCA 输入 |
| PES `scan_driver.retry_count` | 消费为 optimizer retry_count 的兼容别名 | 仅 driver 提供时生效；两处显式冲突拒绝 |
| PES `scan_driver.max_iterations` | 消费为 optimizer max_iterations 的兼容别名 | 仅 driver 提供不被 optimizer 默认值覆盖；冲突拒绝 |
| native scan 中显式 retry_count/retry_strategy/failure_policy/reuse_previous_geometry | 拒绝 | 不承诺逐点重试；不为满足参数偷偷改成 pointwise |
| native scan 中平台缺省的上述逐点设置 | 明示条件不适用，按兼容 `legacy_noop` 投影 | 不向后端继续传无效 kwargs；回执标记不适用 |
| pointwise 中 retry_count、reuse_previous_geometry | 消费 | 注入失败后实际重试次数及使用的几何符合配置 |
| pointwise 中 failure_policy 与 retry_strategy | 消费；相互覆盖的显式冲突拒绝 | `retry_previous/retry_original` 不静默顶掉另一项显式策略 |
| pointwise 中 ScanTS 控制；非 ScanTS 下显式 full_scan 控制 | 不支持的组合拒绝 | 不能接受后在 pointwise/普通 relaxed scan 中消失；缺省值只标不适用 |
| `protocol.execution_mode` | 新请求拒绝，作为服务端输出字段 | 客户端不能伪造实际执行模式 |
| PES `strategy=reverse_peb/direct_ts` | 新执行拒绝；历史读取保留 | 不再表现为已实现的独立策略 |
| PES `strategy=guided_scan` | 兼容名称 `legacy_noop`，明确不切换算法 | omission 与显式值执行一致，discovery 如实说明 |
| direct bond scan 分支的 manifest 专用 `select/coordinate_plan` | 拒绝 | 不能因为其他分支有消费者就允许本分支静默忽略 |
| `input.scan_request` / `method.scan_request` 与展开字段 | 消费为同一规范请求；冲突拒绝 | 任一承载方式不覆盖另一份不同请求 |
| `schema_id/profile_id` 等编辑或描述元数据 | 消费为 metadata-only | 不宣称其在该入口自动选择实际方法；真正 profile 扩展另按已有消费者验证 |
| `pes.scan_selection`、旧 `step2.scan.selection` 推荐阈值 | 当前禁用推荐时明确 `legacy_noop` | 不改变扫描计算；不能报告“算法运行后无候选” |
| `input.selection.kind/atoms/path_plan` | 按已有语义消费 | 不因推荐禁用而整块删掉：它仍参与功能原子和路径校验 |
| `max_running` / `ACP_MAX_RUNNING` / serve 对应兼容参数 | `legacy_noop`，PR-08 负责发现与告警 | 不宣称它限制本地并发；有效值来自 `registry.local.max_jobs` |

表内所有 wire 别名必须在消费者登记中展开为准确字段路径。实现过程中发现额外无效项时，提交前补入同一表及测试；不得以“待完善守护”放行已知静默无效参数。

### 7.5 验收与 fixture

Fixture 来源：

- PR-01/02 构造请求。
- `tests/test_acp_mcp_ux_p1_contract.py` 的隔离 API fixture。
- `tests/test_irc_ts_source.py` 和已有 tsmode 来源 fixture。
- 现有 ORCA 输入生成测试，替换 `_run_orca` 为受控测试边界，检查真实生成输入。
- pointwise 重试使用构造失败序列，不运行真实 QC。

必须通过：

1. PES 缺 coordinate/coordinates、非法 protocol、来源类型缺字段等静态错误，preflight/submit 一致。
2. 校验不修改传入对象；prepare 消费规范化结果后不再另做优先级选择。
3. 冷远端来源明确未验证，无网络和写入。
4. 同一来源快照下，静态错误 `code/field/rule` 相同；不要求不同时间点容量和授权结果恒定。
5. 参数表每行绑定具体测试；改变非默认值必须产生最终可观察效果，或明确拒绝/noop。
6. 只检查 `kwargs`、源码字符串或消费者函数名的测试不能算科学参数生效验收。
7. 契约新增字段却未登记消费者处置时，CI 失败。
8. 未覆盖 workflow 明确保持 partial，不能自动获得“完整消费者覆盖”。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp_round5_preflight_parity.py tests/test_acp_parameter_effects.py tests/test_acp_mcp_ux_p1_contract.py tests/test_acp_submission_contract_first_batch.py tests/test_services_submission_fidelity.py
```

### 7.6 依赖、风险与回滚

依赖 PR-01、PR-02。

风险是共用解析改变 fingerprint、默认 profile 或历史迁移行为。回滚保留 P0 拒绝规则；新版校验能力关闭时，客户端不得将缺失的 validated_scope 解释为全部通过。

**DoD：本批已知无效参数逐项关闭，preflight 不再遗漏可静态判断的 submit 条件。**

## 8. PR-04：IRC 逐方向证据与端点诊断

### 8.1 目标与不做什么

目标：将“没有发现端点”“发现但未收敛”“仅有轨迹”“有效端点但关联不成立”分别呈现，并保存可读产物。

不新增 JobStatus，不以减少 FAILED 数量为验收目标。

### 8.2 改动清单

| 位置 | 改动 |
|---|---|
| `src/cccp/qc/interfaces/orca_ts.py:181`、`:217`、`:280`、`:784` | 轨迹/端点发现与方向证据解析；只有真实 fixture 支持的格式才扩展 |
| `src/cccp/qc/interfaces/orca.py:3563` | 分离进程成功、路径存在、端点候选和方向完成事实 |
| `src/cccp/calculation/tasks/irc.py:253`、`:362`、`:414`、`:522` | 保留过滤前诊断；ORCA 缺收敛证据时不能仅因 success+文件存在判收敛 |
| `src/cccp/calculation/results.py:494`，`IrcDirectionResult` | 增量承载端点来源、收敛状态、原因和 last-observed 引用 |
| `src/acp/calculations/irc/contracts.py:52`；`src/acp/workflows/irc.py` | 科学结果与工作流状态分离，失败路径也保留诊断与轨迹引用 |
| `src/acp/results/irc_projection.py`、`frames.py` | 保持方向/帧身份，通过现有帧契约投影 |
| 新增 `tests/test_cccp_irc_endpoint_evidence.py` | 覆盖发现、收敛、端点与轨迹的组合 |

逐方向结果至少表达：

- `endpoint_discovery`：found / missing / invalid；
- `convergence`：converged / not_converged / unknown；
- 来源文件、方向、帧身份与证据位置；
- 正式 endpoint 与 `last_observed_geometry` 的区别；
- 工作流执行结果与科学关联 verdict 的区别。

双向请求只有一侧完成时，整体完整性不得为 true。若保留既有“有一侧端点则可返回部分成功”映射，必须同时明确 `complete=false` 和未完成方向，不得只给笼统 completed。

### 8.3 验收与 fixture

已有 fixture：

- `tests/fixtures/irc/orca_irc_h2o2.out`。
- 同目录 F/B/Full 轨迹和单帧文件。
- 日志第 1188、1206 行明确达到最大迭代数；它是**未收敛负例**。
- `tests/fixtures/acp2/science/diels_alder/validators/irc-*.json` 用于科学关联判定，不能冒充原始 ORCA 输出。

必须通过：

1. H₂O₂ fixture 有轨迹和单帧文件，但不能判双向收敛。
2. 缺日志/缺方向证据报告 unknown，不用全局 success 补出收敛。
3. 轨迹末帧可读取为 last-observed，不能进入正式 endpoint 产品。
4. F/B 轨迹分别处理，不能用 Full 的最后一帧代表两端。
5. 一侧收敛、一侧上限停止，逐方向结果和整体完整性一致。
6. 有效端点关联失败交给科学验证表达，不报告为端点解析失败。
7. 失败时轨迹和诊断仍可通过 manifest/prepare/read 找回。
8. PR-02 首帧反例继续通过。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_cccp_irc_endpoint_evidence.py tests/test_cccp_task_irc.py tests/test_acp_irc_trajectory.py tests/test_acp_irc_projection.py tests/test_irc.py
```

### 8.4 sample 依赖

**需要用户提供 sample**，才能关闭“60 帧双向轨迹却 failed”这一具体投诉，以及扩展缺失的真实 ORCA 输出格式。

最小样本：

1. 该次运行原始 `.inp` 和完整 `.out`，保留版本、方向、收敛/停止与正常/异常终止段。
2. 原始文件名与相对目录清单。
3. 日志引用的所有 F/B/Full 轨迹及 endpoint XYZ，保留帧顺序、注释和能量。
4. ACP 对应 attempt 的错误日志、IRC 结果/轨迹 JSON、manifest。
5. 请求方向、maxpoints、源 TS 结构与原子顺序。
6. 若要验证 Hessian 交接，再提供对应 `.hess` 或可核对的来源 bundle 与摘要。

此外，需要至少一份**真实双向收敛**输出作为正例；若投诉样本不收敛，不能把它改造成正例。单方向收敛组合可先用明确标记的构造 fixture 验证逻辑。

缺 sample 时，诊断和现有负例可合并；投诉复现与未支持格式保持 `NOT_VERIFIED`，PR 不得声称已修复该具体运行。

### 8.5 风险与回滚

依赖 PR-02。风险是对日志措辞过度匹配，以及全局收敛信息污染另一个方向。

新增诊断字段采用兼容读取。回滚解析扩展时保留原始输出、诊断产物及 PR-02 防护；未知格式恢复为 unknown，不能恢复为默认收敛。

**DoD：每个方向都有可解释证据；真实投诉关闭状态与代码交付状态分开记录。**

## 9. PR-05：PES 发布事务、持久幂等与恢复基础

### 9.1 目标与不做什么

目标：先修现有人工发布路径的完整性，为后续 Agent 发布提供可复用基础。

本 PR 不开放 Agent 发布、不新增 MCP 写工具、不把字符串 `agent` 加入 loader allowlist。

### 9.2 改动清单

- `src/acp/calculations/pes/review.py:459`，`save_pes_review`；`:600`，`restore_pes_review`：保留科学校验和唯一物化链，改为受发布事务编排调用。
- `review.py:506`、`:519`、`:520`、`:555`：将读 revision、结构物化、manifest 和 review 分别写入的流程改为可恢复的发布组。
- `src/acp/api/v1_routes.py:4077`、`:4016`：保存和 restore 走同一用例。
- `v1_routes.py:3917`，`_remote_review_write_back`：替换“部分失败后重试即可”的假定，返回可查询的发布意图与恢复状态。
- 新增 `src/acp_services/runtime/selection_publication.py`：发布用例、注入端口、幂等和恢复编排，不导入 ACP。
- 新增 `src/acp/calculations/pes/publication.py`：ACP 侧适配器，调用唯一 review writer。
- `src/acp_services/runtime/idempotency.py:254`、`store/store.py:1087`：复用现有 claim/lease 机制，扩展非 job 发布结果的持久完成与重放。当前 `finalize_idempotent_job` 不能直接冒充发布操作完成器。
- `src/acp_services/store/migrations.py`：必要的增量迁移；基线最后 job migration 为 027，实施时核对最新编号，不预占。
- `src/acp/results/manifest.py`、`structure_policy.py`、`src/acp/calculations/batch/loaders.py:92`、结构来源索引读取：统一验证发布组已提交及引用摘要。
- `src/acp/results/remote_structure_cache.py` 与现有 remote fetcher：补发布组暂存、提交检查和失败恢复所需端口，继续复用现有 SSH/SFTP 基础设施。
- 新增 `tests/test_services_pes_publication_transactions.py`。

### 9.3 发布契约

必须实现以下不变量：

1. selection revision 与 source execution attempt 分开。
2. 并发相同 expected revision 只能有一个提交者。
3. 幂等作用域绑定服务端 principal/project、动作和 key。
4. 同键同请求返回原提交结果；同键异请求冲突；处理中返回原 operation。
5. 对已提交幂等意图的重放先对账，不因当前 revision 已增长而制造第二次发布。
6. 结构及发布组引用不可在提交后原地改写；新选择生成新 revision。
7. review/manifest/结构必须属于同一提交组，读取者不能观察到可消费的混合版本。
8. 旧 writer、restore 和新 writer 不能绕过同一串行化与 fencing 边界。
9. CAS 提交前再次核对 source attempt、profile/geometry digest。
10. restore 生成新 revision，不将 revision 倒退。

持久发布意图记录属于现有 Services store；候选内容仍以 PES review/manifest 为权威。不得再建立一份可独立编辑的候选状态库。

远端采用同样的发布组完整性规则。仅上传 review 文件最后一步不足以证明事务完整；reader 必须核对提交组及所有引用摘要。无法核对的部分写回状态保持 pending/reconcile，不暴露为已发布候选。

### 9.4 验收与 fixture

Fixture 来源：

- `tests/test_acp_pes_review.py`。
- `tests/test_acp_api_pes_review.py::_make_pes_task`、`_make_failed_pes_task`、`_RemoteTreeFetcher`。
- `tests/fixtures/acp2/platform/reference-case/pes/`。
- 同一临时数据库的独立连接/进程用于并发，不能只测试同线程顺序调用。

必须通过：

1. 两个请求同时发布 revision N：恰好一个 N+1，另一个 409。
2. 相同 key 重试、回包丢失、进程重启后重放不增加 revision。
3. 相同 key 不同候选、来源摘要或角色冲突。
4. 分别在结构写入后、manifest 暂存后、提交前、提交后回包前注入故障。
5. 每个故障点 reader 只能返回完整旧版本、完整新版本或明确未就绪。
6. 远端每个上传边界失败后，同键恢复不产生新的选择版本。
7. 旧 lease owner 不能在新 owner 接管后提交。
8. source rerun 与发布竞争时，旧 attempt 发布失败。
9. restore 走同样事务，历史 backup 保留。
10. 关闭新发布能力后，无关工作流继续工作，发布明确不可用。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_services_pes_publication_transactions.py tests/test_acp_pes_review.py tests/test_acp_api_pes_review.py tests/test_acp_services_isolation.py
```

### 9.5 依赖、风险与回滚

无功能前置依赖，可与 PR-03 并行。

风险集中在数据库与文件系统跨介质提交、远端可见性及遗漏旧读取路径。合并前必须列出所有 review/manifest 候选消费者并逐一验证。

回滚先关闭 PES 发布/restore 写入口，停止接受新发布意图，保留恢复查询和已提交产物。不得删除 migration、未完成意图或把部分写回伪装为成功。PR-06/07 发现事务端口不可用时必须拒绝新发布。

**DoD：人工发布已具备事务性可见性和持久恢复；Agent 发布仍未开放。**

## 10. PR-06：受限自动授权、来源记录与 Batch 消费绑定

### 10.1 目标与不做什么

目标：平台验证范围授权后，Agent 无逐点人工确认地发布 PES 初猜；Batch 合法消费该版本并保留完整来源。

不允许 Agent 自授权，不把已有 plan-digest approval 当作任意计划通行证，不将“已发布”解释为“已验证 TS”。

### 10.2 改动清单

| 位置 | 改动 |
|---|---|
| 新增 `src/acp_services/contracts/selection.py` | 版本化 selection request/result、来源与授权引用契约 |
| 新增 `src/acp_services/runtime/selection_authorization.py` | 受限自动授权的验证及逐动作准入 |
| `src/acp_services/runtime/approval_policy.py:201`、`:236`；`approvals.py:917` | 复用身份/审批边界，明确范围授权到具体发布/Batch 意图的绑定 |
| `src/acp_services/data/research_resources.py:474`、`:577` | 在现有持久资源机制中登记、CAS 更新授权记录；必要 schema 扩展显式迁移 |
| `src/acp_services/runtime/research_budget.py` 及现有预算账本 | 持久候选/任务/重试上限；重启和重试不清零 |
| PR-05 publication service 与 ACP adapter | 发布前验证授权，提交时重检适用版本及剩余额度 |
| `src/acp/calculations/pes/review.py:529`、`:541` | selector、actor、授权依据、source_attempt、selection_revision 分离 |
| `src/acp/calculations/batch/loaders.py:92`；`batch/models.py` | 新来源需完整证明，不能只放宽字符串 allowlist；保留 item lineage |
| `src/acp/api/v1_routes.py:1859`、`:1906` | 来源解析不能在生成 inline XYZ 时丢弃可执行身份，只留下事件快照 |
| `src/acp/scheduler/runner.py` 的 Batch 输入物化；`calculations/batch/engine.py` | 将来源绑定纳入实际冻结输入/执行身份，执行时不重新解析“最新选择” |
| `src/acp/api/v1_schemas.py`、`submission_contract.py` | 新来源/授权字段显式版本化 |
| 新增 `tests/test_acp_authorized_pes_selection.py` | 授权、来源、冻结与消费链 |

授权记录最少包含：

- owner、principal/project、policy revision；
- 有效期、撤销状态；
- 允许 source task/attempt 或明确限定的来源范围；
- 允许动作、候选角色、workflow、方法和资源范围；
- 最大候选数、Batch 任务数及补算/重试次数；
- 每次实际动作的 digest 与授权决定引用。

授权管理通过既有具备审批权限的主体或管理员配置入口完成；Agent 使用凭据不得创建、扩大或延长自己的授权。

### 10.3 验收与 fixture

Fixture 来源：

- PR-05 PES 发布 fixture。
- 现有 HTTP/MCP identity、approval、budget fixture。
- `tests/test_batch_optimize.py` 的任务构造与 fake 科学执行边界。
- source rerun 和不同 revision 使用独立构造版本。

必须通过：

1. 合法授权内发布→Batch，无逐点人工确认。
2. `selector=agent`、actor 为服务端身份；请求伪造 manual/actor 不生效。
3. 跨项目、错误 principal、过期、撤销、越界方法/角色/资源均拒绝。
4. source attempt/profile/geometry 任一变化，旧选择请求拒绝。
5. 过期或不匹配的 approval 不能通过更换计划复用。
6. 同键重试不重复扣候选/任务额度；重启后额度保留。
7. Agent manifest 缺授权回执、提交组或摘要时，loader 拒绝。
8. 发布 revision N 后，Batch 固定消费 N；之后发布 N+1 不改变已提交 Batch。
9. 新提交引用旧 source attempt 时拒绝；已冻结历史 Batch 仍可恢复。
10. 旧 manual/manual_frame 保持历史语义，不被重写为 Agent。
11. 关闭自动发布能力后，人工路径和已有冻结 Batch 不受影响。
12. 普通 scan 请求该 PES 专用发布服务时明确不支持。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp_authorized_pes_selection.py tests/test_batch_optimize.py tests/test_acp2_runtime_mcp_identity.py tests/test_acp_services_isolation.py
```

### 10.4 依赖、风险与回滚

依赖 PR-05。

风险是范围授权过宽、额度重复消费，以及 provenance 仅保存到事件却未绑定实际执行输入。

回滚关闭自动授权发布能力，保留新来源记录和读取能力；不降级成 manual。已冻结 Batch 继续使用冻结输入，尚未提交的 Agent 候选暂停新增消费。PR-07 查询继续可用，写操作报告 capability unavailable。

**DoD：Agent 可在真实授权范围内发布，Batch 消费同一已提交版本，任何来源伪造或旧版本替换均被拒绝。**

## 11. PR-07：Controller/MCP 查询与发布闭环

### 11.1 目标与不做什么

目标：通过已有 harness 可发现、可调用、可恢复的工具完成发布闭环。

建议新增两个工具：

- `acp_get_pes_review`：只读查询当前状态、历史版本摘要及指定幂等意图。
- `acp_publish_pes_review`：授权发布选择。

名称属于本 PR 新增契约，当前仓库尚不可调用。首版不增加 MCP restore 动作；已有 HTTP restore 保持可用并走 PR-05 事务。

### 11.2 改动清单

- `src/acp_services/entrypoints/mcp.py:1877`，`McpBackends`：新增可选 selection 端口。
- 同文件 `:2560`，`visible_tool_names`：仅在版本协商成功且后端装配后暴露。
- 同文件工具 schema/handler：读写分开，写工具要求稳定幂等键、预期 revision 和来源绑定。
- `src/acp_services/entrypoints/controller_client.py:470`、`:608` 所在客户端：新增对应请求/结果 DTO 与调用。
- `src/acp/services_mcp_bootstrap.py:435`，`build_mcp_runtime`：显式装配，不在 Services 导入 ACP。
- `src/acp/api/v1_routes.py:3966`、`:4077` 或新增 `src/acp/api/v1_selection.py`：新增版本化的 cache-only 查询与授权发布入口。
- `src/acp/api/routes.py:459`：capability 返回连接实际支持的选择操作、schema 和恢复方式。
- `src/acp/api/guidance_pack.py`、`workflow_contracts.py`、README MCP 指南：更新可执行工具链。
- 新增 `tests/test_acp2_mcp_pes_selection.py`；扩展 `tests/test_acp2_mcp_sdk_contract.py`。
- Services 新公开面同步登记 `migration_ledger.md`、`public_shim_inventory.md` 等适用台账。

不得直接包装旧 GET review 作为只读工具：当前远端 review 读取可能执行 fetch。新 MCP 查询必须使用 cache-only 服务；资料不足时返回 prepare 指引。

### 11.3 验收与 fixture

Fixture 来源：

- PR-05/06 的持久发布和授权 fixture。
- `tests/_acp2_mcp_live_harness.py`。
- 真实 MCP SDK + 隔离 Controller/stdio 进程。
- 科学计算使用明确标注的 fake dispatch；该结果只证明工具闭环。

必须通过：

1. 未协商、未装配、已关闭三种情况下工具不可伪装可用。
2. tools/list 根据实际能力变化；不固定断言总工具数。
3. 查询不写库、不 prepare、不远端 fetch。
4. 查询返回 source task/attempt、revision、摘要、候选及能力状态。
5. 发布成功后工具返回实际提交版本和可用于 Batch 的稳定引用。
6. 丢回包后查询原幂等意图，返回相同 publication/revision。
7. MCP 错误保留 `code/field/rule`、revision 冲突和授权原因。
8. `acp_capabilities → tools/list → 查询 → 发布 → Batch preflight → submit → status` 经真实 SDK 贯通。
9. 已发布候选只能得到初猜状态；频率/IRC 等验证未执行时明确 not_evaluated。
10. PR-06 或 PR-05 关闭时，写工具停止可用；不得转用旧 manual 路径。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp2_mcp_pes_selection.py tests/test_acp2_runtime_mcp_backends.py tests/test_acp2_runtime_mcp_process.py tests/test_acp_guidance_pack.py
```

真实 SDK 契约验收另跑：

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m contract tests/test_acp2_mcp_sdk_contract.py
```

沿用现有 conformance 的非空收集与 skip 统计规则。SDK/DSH 环境缺失记 `NOT_VERIFIED`，不能以 handler 单测代替。

### 11.4 依赖、风险与回滚

依赖 PR-03、PR-06。

风险是新字段被 MCP 投影丢弃、旧连接仍缓存工具列表、查询误触发远端准备。

回滚关闭 selection extension 并撤回工具可见性，保留 Controller 发布记录。客户端重新发现后应明确报告当前连接不可执行，不提供绕过授权的 inline/manual 替代路径。

**DoD：真实 SDK 能执行并恢复合法发布→Batch 链，工具能力与指南完全一致。**

## 12. PR-08：Batch 基数、推荐说明与有效容量

### 12.1 目标与不做什么

目标：让 Agent 在提交前知道“一任务一结构”、推荐为何为空，以及实际并发限制。

不修改 Batch single_flat 布局，不启用推荐算法，不把 `max_running` 自动映射为新的并发控制。

### 12.2 改动清单

- `src/acp/api/submission_contract.py:827`：保留 inline items 提前检查。
- `src/acp/api/v1_routes.py:1859`，`_resolve_batch_structures_input`；`src/acp/calculations/batch/loaders.py:163`、`:195`：解析来源后、写快照前校验实际选中的结构基数。
- `src/acp/api/workflow_contracts.py` 的 Batch 描述：增加机器可读限制，例如每 scheduler task 恰好一个选中结构，并明确适用 single_flat。
- `src/acp/calculations/pes/scan.py:482`、`engine.py:301`、`outputs.py:94`、`:126`：将推荐状态与候选数组分离，当前明确 disabled 及稳定原因码。
- `src/acp/api/guidance_pack.py`：两个候选拆成两个 Batch 请求的示例；input_artifact/source 引用示例。
- `src/acp/api/routes.py:270`，`_capability_limits`；`src/acp/scheduler/manager.py:5368`、`:5379`：公布 manager 实际本地 slot 限制与计数口径。
- `src/acp/api/server.py:362`、`src/acp/cli.py:5196`：将 max_running/环境变量标明 legacy_noop，启动诊断明确指出有效配置位置。
- 新增 `tests/test_acp_round5_discovery_contracts.py`。

推荐状态当前使用 `status=disabled`、`reason_code=manual_only_policy` 表达原生推荐算法禁用。PR-06 上线后应同时区分：

- 原生算法推荐仍禁用；
- 获授权 Agent 可以从帧表提出并发布初猜。

不能继续用一条“全流程 manual-only”描述否定已开放的授权 Agent 发布。

### 12.3 验收与 fixture

Fixture 来源：

- `tests/test_batch_optimize.py`。
- PR-05/06 的两个已发布候选。
- 构造多帧 XYZ、一个 manifest 含两个结构、selected_ids 选一个/两个的 fixture。
- `/tmp/da_validation/pes_profile.json` 可用于结构参考；当前核实它有 15 帧，不能直接称为“frame 15 的 27 kcal/mol 投诉原件”。转存前记录来源与摘要。
- 现有 admission/capability fixture，不启动实际作业。

必须通过：

1. 两个 inline items、单 item 引用多帧、manifest 展开后两个选中结构均在 submit 副作用前拒绝。
2. 一个 manifest 明确选择一个结构可通过。
3. 错误返回 limit、实际数量及可执行拆分指引，不自动提交。
4. 拆成两任务的示例各自通过 preflight；候选来源 revision/role 不丢失。
5. 空推荐数组同时给 disabled 原因，不能输出“未找到峰”。
6. 有明显峰的构造曲线在算法禁用时仍报告 disabled，而非伪造候选。
7. `registry.local.max_jobs=1` 时 discovery 报 1；仅改变 legacy max_running 不改变有效容量。
8. 本地容量与远端容量分别表达；不能将全局 running 数与本地 slot 数直接比较。
9. `task_id/job_id` 文档说明兼容别名，推荐保存带 attempt 的 TaskRef。
10. 幂等示例一次意图生成一次 key，重试复用；两个有意重复计算使用不同 key。服务端不随机补 key、不按内容哈希合并独立意图。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp_round5_discovery_contracts.py tests/test_batch_optimize.py tests/test_acp_mcp_ux_p1_contract.py tests/test_acp_guidance_pack.py tests/test_acp_api_guidance_routes.py
```

### 12.4 依赖、风险与回滚

依赖 PR-03；最终 Agent 示例依赖 PR-07。

风险是多来源解析触发写入、把展开前数量当实际数量，以及把旧 max_running 默认值重新变成强制限制。

回滚发现字段时保留现有 single_flat 执行门和已实现的提交拒绝；推荐状态缺失应解释为 unknown，不能解释为算法运行后无候选。不得通过删除 engine 单结构检查实现兼容。

**DoD：单结构限制、推荐禁用和真实容量均在 discovery/preflight 中可见，示例可直接通过校验。**

## 13. PR-09：状态 locator、live_status 与 prepare 本地快速路径

### 13.1 目标与不做什么

目标：提供安全的相对任务定位、可解释的缺失阶段信息，并让已经可验证就绪的本地产物在 prepare 调用中直接返回 ready。

不暴露绝对服务器路径，不通过状态 GET 启动 fetch，不修改调度生命周期来迎合展示。

### 13.2 改动清单

- `src/acp_services/entrypoints/status_projection.py:180`、`:214`：增量投影 storage locator；保留旧字段和脱敏规则。
- `src/acp/api/job_status_view.py:112`，`build_live_status`；对应 `v1_schemas.py` DTO：补 observation time、来源和未知原因。
- `src/acp/api/v1_routes.py:9505`，`prepare_artifact`：使用现有 Data 准备/发布机制执行有界本地快速检查。
- `src/acp_services/data/operations.py:1100`、`:1310`、`:1405`：若新增按指定 operation 执行的快速路径，保持相同 lease、epoch、完成 CAS 和重放规则。
- `src/acp/services_data_bootstrap.py` 的 Data executor、read-only/cache-only reader 装配：复用验证与发布检查，不另造 `exists()` 快捷判定。
- `src/acp/services_mcp_bootstrap.py:313` 和 Controller DTO：贯通 operation 状态与资源可用性。
- 新增 `tests/test_acp_round5_artifact_status.py`。

locator 至少区分：

- 存储类型/绑定；
- 受授权的相对路径；
- source attempt；
- 当前连接是否能够定位。

远端路径、Controller cache 路径不能冒充本地 run-root 相对路径。无法安全投影时返回明确 unknown 原因。

### 13.3 验收与 fixture

Fixture 来源：

- `tests/test_acp2_status_projection_verified_facts.py`。
- `tests/test_acp_mcp_ux_p3_status.py`、`tests/test_acp_mcp_ux_p3_artifact.py`。
- `tests/test_services_prepare_operations.py`。
- 构造 local/remote/cache、软链接越界、旧 attempt、缺 body 的资源状态。

必须通过：

1. 本地 locator 可相对定位，wire 中不出现绝对 run_root。
2. 远端/cache 明确标识；越界和不授权路径不泄露。
3. 有 metrics 无 current_stage 时，stage_label 可为空，但有稳定 unknown reason；不能猜阶段。
4. observation time 不得每次查询伪造为新鲜观测。
5. 已发布且身份、digest、generation、授权均匹配的本地产物，prepare 可同步 ready。
6. 只有文件 exists、旧 attempt、撤销授权、digest 错误、缺发布 body 时不能 ready。
7. 快速路径与 worker 竞争时只有一个有效完成者，旧 lease 不能覆盖新结果。
8. 不满足快速路径时仍返回原 operation 的 pending；不重复创建操作。
9. `QUEUED + ABSENT` 明确表示准备可用性尚未建立，不等于已证明原始文件不存在。
10. status/read 查询无额外准备或远端抓取。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q -m "not slow and not integration and not contract" tests/test_acp_round5_artifact_status.py tests/test_services_prepare_operations.py tests/test_acp_mcp_ux_p3_status.py tests/test_acp_mcp_ux_p3_artifact.py tests/test_acp2_status_projection_verified_facts.py
```

### 13.4 依赖、风险与回滚

无功能前置依赖，可独立合并。

主要风险是 prepare 快速路径绕过 fencing 或权限，及 locator 泄露部署路径。先合并正确性测试，再开放快速路径。

回滚关闭快速路径恢复异步准备；已有 operation 和已发布资源保持有效。locator/live_status 新字段为增量字段，可停止输出，客户端回退到现有 directory_name/unknown 状态。

**DoD：定位与状态事实可解释，本地 ready 有完整验证依据，回滚异步模式不丢操作身份。**

## 14. 批次最终验收与发布边界

### 14.1 必须保留的三条独立验收链

| 验收链 | 内容 | 不可替代的范围 |
|---|---|---|
| MCP 基础易用性 | 发现、校验纠错、提交、状态、prepare/read、找回 | 不证明科学结果正确 |
| 授权初猜到 Batch | 来源、授权、发布事务、MCP 可达、Batch 冻结与恢复 | fake QC 只证明编排，不证明驻点 |
| IRC 科学证据 | 输出发现、方向收敛、端点可信性、科学关联 | JSON validator fixture 不替代真实 ORCA 输出 |

### 14.2 批次必须执行的负例

- 坐标冲突、点数冲突、单位不匹配。
- 显式 IRC step、initial_hessian。
- 多帧首帧经中间转换成为端点。
- preflight 缺字段却给 feasible。
- 冷远端来源触发隐式 fetch。
- native scan 接受逐点重试控制。
- strategy 名称被解释为实际执行了对应算法。
- Agent 伪造 manual、actor 或授权。
- source rerun、profile 改变、旧 selection revision。
- 同键异内容、丢回包、过期 lease、并发 revision。
- 远端部分写回被 Batch 消费。
- 单个引用展开为多个 Batch 结构。
- 推荐 disabled 被表述为没有峰。
- 有轨迹被表述为双向收敛。
- 仅有本地文件就被 prepare 判 ready。

### 14.3 文档与发行同步

各 PR 修改对外语义时，同批更新：

- workflow contract、catalog/default、guidance 和有效示例；
- 输入、错误、产物、恢复契约；
- 指南版本及契约摘要，不能仅增加版本号；
- 适用 Services 公共面台账；
- 安装后读取指南和 discovery 的检查。

PR-07 完成后，计划文档只引用实际权威内容包和版本，不保留另一份独立 prompt 正文。

最终证据保存到新增目录：

`reports/acp2_mcp_round5_20261031/`

按 PR、revision 和验收层分开记录。不得将不同 revision 的模块 PASS 拼成同一条科研闭环 PASS。

### 14.4 本批结束条件

本批工程实施完成，需要：

1. PR-01/02 的 P0 防护全部通过。
2. 参数处置表逐项有消费、拒绝或明确 legacy_noop 的行为证据。
3. preflight/submit 静态 parity 与零副作用通过。
4. 发布事务、授权、来源绑定和真实 SDK 工具链通过。
5. Batch、推荐、容量和状态的说明与实际执行一致。
6. 各 PR 的关闭能力、兼容读取和回滚验证通过。
7. 缺 sample、未运行真实 QC/模型/LSF、未完成安装验收的范围保持 `NOT_VERIFIED`。

“60 帧 IRC 失败”具体投诉只有取得原始 sample 并完成对应诊断后才能关闭；它不应阻塞 P0 防护上线，也不能被其他 fixture 的 PASS 自动关闭。
EXIT:0
