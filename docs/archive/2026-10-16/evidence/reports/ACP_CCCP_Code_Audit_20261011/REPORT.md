# ACP / CCCP 无效代码与技术债审计报告

审计日期：2026-10-11。源码基线：`27d2e2b3f6508c96d2d5e04e4e1ff6d8a37359ea`。审计对象：`src/acp`、`src/cccp`；辅查相关测试、CLI、API、配置、CI、项目知识库及前端调用点。

## 1. 主要结论

当前最应优先处理的是科学输入和执行参数的完整性。已复现的问题包括：远程命令遗漏电子态配置；GJF 和 ORCA 输入摄入丢失电荷、多重度；任务层接受截断或含 NaN 的 XYZ；非均匀扫描 λ 被按均匀进度计算；错误的远程配置被转换为本地执行配置。这五类问题列为 P1。它们比格式、命名和单纯的文件长度更值得先修。

代码维护方面，主要负担是大型模块承担过多职责、同一参数在多条执行路径中手工转换、共享算法存在两个维护位置，以及已退出生产链的功能仍由旧测试维持。找到 10 个在仓库源码、测试、脚本和前端中没有实际引用的私有函数，共 127 行；另有 PES 自动推荐、退役工作流命令分支、旧结果写入辅助函数等待收口代码。这里的“没有引用”限定为仓库内可检索的调用关系，不能证明外部使用者不存在。

四层架构的主要迁移护栏当前通过。不能把 `acp.calculations.primitives`、`acp.backends` 的转发文件、Confsearch 仍使用的旧引擎、历史结果读取器，以及明确保留的 CCCP 公开 API 统称为可删除死代码。当前问题更接近“迁移后的参数与语义没有完全收口”，而非计算任务实现再次出现两个根。

本报告记录 **13 项行为问题、6 项冗余与遗留代码发现、10 项结构和维护问题**，并单列文档、测试缺口和验证边界。19 项隔离探针提供可运行证据；相关定向测试 **328 项通过、1 项排除**。全量非慢速测试未完成：当前环境中 TestClient 进入时停滞，独立的空 FastAPI 应用也出现同样现象，不能据此断言 ACP 的调度器发生死锁。

### 优先修复的五项

| 编号 | 问题 | 已复现结果 | 主要影响 |
|---|---|---|---|
| B01 | 远程执行遗漏电子态配置 | 同一 JobSpec：本地有 `--spin-config`，远程没有 | 指定的电子态、初猜或自旋策略未按原始请求传到 CLI |
| B02 | 输入摄入丢失电荷、多重度 | 标准 GJF / ORCA 输入的 `1 2` 被读成 `0 1` | 后续没有显式覆盖时，计算可能采用错误电子数和自旋 |
| B03 | 任务几何加载不够严格 | 声明 3 原子、实际 2 原子仍接受；NaN 仍接受 | 错误几何进入计算核心，失败位置延后或计算对象改变 |
| B05 | 非均匀 λ 没有驱动约束值 | λ 为 `0, 0.2, 1` 时，中间目标为 2.0 而非 1.4 | 路径记录与实际扫描约束不一致 |
| B10 | 错误远程配置退化为本地模式 | `execution_mode="remot"` 得到 `local` | 配置错误改变执行目标，日志警告不能替代阻止错误执行 |

### 判定方法与范围

全量扫描覆盖两个包的全部 Python 文件；人工审查集中在扫描发现的热点、输入输出、任务参数、恢复身份、调度状态和远程执行链。本报告不声称逐行人工审阅了全部 17.1 万行，也不声称验证了真实 ORCA、xTB、CREST、CENSO 或 LSF 执行。探针只使用临时文件、临时 SQLite、参数构建、配置替身和无害 shell 命令。

P1 表示应优先修复、可能改变现行计算语义或执行目标；P2 表示条件性行为缺陷、边界校验缺口或显著维护风险；P3 表示清理、兼容性收口或低优先级遗留问题。本次没有足够证据把任何发现定为已发生的生产 P0 事故。

“已复现”指直接调用当前实现得到确定结果；“静态确认”指源码和调用条件可以支持结论；“风险推断”指后续影响尚未通过完整工作流或并发测试验证。各项的影响范围应按这个区别理解。

## 2. 全量扫描结果

### 2.1 规模与复杂度

| 指标 | ACP | CCCP | 合计 |
|---|---:|---:|---:|
| Python 文件 | 266 | 86 | 352 |
| 物理行数 | 134,692 | 36,328 | 171,020 |
| 函数 / 方法 / 嵌套函数定义 | 3,671 | 1,015 | 4,686 |
| 捕获 Exception / BaseException 的处理块 | 297 | 33 | 330 |

物理行包含空行和注释，不能等同于有效逻辑行。共有 35 个文件至少 1,000 行，194 个函数至少 100 行，216 个函数的 AST 分支评分至少 20。分支评分是本报告脚本的筛选指标，不是经过标准工具认证的圈复杂度；它不累加内嵌函数的分支。

| 热点文件 | 行数 | 审计判断 |
|---|---:|---|
| `acp/api/v1_routes.py` | 7,312 | 224 个函数，接口、编排、投影、文件读取和业务处理混合 |
| `acp/scheduler/manager.py` | 5,758 | 179 个函数，生命周期、远程提交、目录操作、清理、索引等集中 |
| `acp/catalog.py` | 5,368 | 大量声明式 schema / catalog 数据，长度不能直接视为坏代码 |
| `acp/cli.py` | 4,030 | 参数声明与手工路由、参数解释混合 |
| `acp/workflows/nmr.py` | 4,012 | 科学流程、人口选择、诊断、修订和发布耦合 |
| `cccp/qc/interfaces/orca.py` | 3,812 | 多类任务的输入渲染、调用和解析集中 |
| `acp/scheduler/remote/runner.py` | 2,578 | 远程存储、版本、提交、轮询和收据处理集中 |
| `acp/scheduler/structure_source_store.py` | 2,406 | 组织存储、检索和事务逻辑较重 |
| `acp/scheduler/runner.py` | 2,103 | 本地执行、参数转换、结果读取、进度观察混合 |
| `acp/calculations/executor.py` | 1,968 | 核心执行与恢复驻点，拆分必须保持身份和发布契约 |

| 函数 | 起始行 | 函数行数 | AST 分支评分 |
|---|---:|---:|---:|
| `JobRunner._build_cmd` | `scheduler/runner.py:1221` | 295 | 115 |
| `ORCAInterface._build_input_blocks` | `qc/interfaces/orca.py:1375` | 399 | 105 |
| `CalculationPlanExecutor.execute` | `calculations/executor.py:741` | 558 | 103 |
| `build_s2_energy_graph` | `results/energy_graph.py:168` | 323 | 90 |
| `normalize_and_validate_method_config` | `catalog.py:4838` | 241 | 71 |
| `select_path_seeds` | `calculations/pes/path_selection.py:804` | 396 | 65 |
| `run_xtbmd_censo_energy` | `workflows/xtbmd_censo_energy.py:1267` | 916 | 61 |
| `run_nmr_analysis` | `workflows/nmr.py:3230` | 534 | 58 |
| `run_pes_scan` | `calculations/pes/scan.py:263` | 269 | 58 |
| `run_conformer_energy` | `workflows/energy.py:127` | 660 | 54 |

长函数的风险取决于它混合了哪些职责。较长的 argparse 声明或 catalog 数据，通常比同长度的状态转换、恢复和科学执行函数更易审查；不建议用统一行数阈值机械拆分。

### 2.2 静态质量与架构护栏

| 检查 | 结果 | 解释 |
|---|---|---|
| AST 解析 | 352 文件可解析 | 未发现 Python 语法错误 |
| 同一直接作用域重复定义 | 0 | 没有发现这一类后定义覆盖前定义 |
| 直接终止语句后的不可达语句 | 0 | 只检查同一语句列表，不能排除条件性不可达分支 |
| `ruff check src/acp src/cccp` | 676 项，退出码 1 | 大多数是旧语法、空白和行宽问题 |
| `ruff format --check` | 91 个文件需格式化；261 个已符合 | 不等于 91 个行为缺陷 |
| mypy | 577 项错误，涉及 127 文件 | 类型契约噪声较高，不能把错误数量直接当成运行时 bug 数 |
| `architecture-remediation` grep gates | 10 项通过，0 阻断、0 allowlist 项 | 当前四层依赖和关键唯一驻点护栏通过 |
| 定向非慢速测试 | 328 passed，1 deselected | 排除一个 TestClient 环境停滞用例 |

Ruff 主要分布：UP045 150、UP006 132、W293 126、E501 125、UP035 40、I001 28、N806 23、F401 18；另有 F841 2 项。未出现 F821、F822、F811 这类未定义名称、错误导出或重定义告警。F401 也不能无差别自动删除：例如 `acp/backends/orca.py:12` 的私有解析器导入承担旧路径兼容职责。

异常处理统计为 318 个 `except Exception`、12 个 `except BaseException`，没有无类型的裸 `except:`；其中 11 个处理块只有 `pass`。捕获范围宽和静默吞错是两个不同问题，不能把 330 处全部描述为静默吞错。

详细数据见 [AST 清单](evidence/ast_inventory.json)、[Ruff 明细](evidence/ruff_src.json)、[格式检查](evidence/ruff_format.txt)、[mypy 输出](evidence/mypy.txt) 和 [架构护栏输出](evidence/architecture_gates.txt)。

## 3. 行为问题：应先于大规模清理处理

### B01 — 远程命令遗漏电子态配置【P1；已复现；现行路径】

位置：[本地 runner](src/acp/scheduler/runner.py:66)、同文件 `:1403`；[远程命令构建](src/acp/scheduler/remote/script_gen.py:148)；`acp/catalog.py:5226` 的通用 method-level 转换。

本地 simple 工作流从 level 中提取 `electronic_state`，写 `electronic_state.json` 并传 `--spin-config`。远程路径主要走 `method_levels_to_cli_flags`，没有对应的电子态提取和 sidecar 传递。用同一个 singlepoint JobSpec 构建两个命令，本地标志和 sidecar 均存在，远程命令没有该标志。

**证据键：** `remote_electronic_state_loss`。探针没有运行 QC，也没有比较计算能量；已确认的是请求载荷丢失。单独传 `--multiplicity` 无法表达完整的初猜、参考态、状态策略等配置。

**建议：** 用一个共享的 JobSpec → 执行请求 / argv 转换实现，分别处理本地和远程工件路径。远程 sidecar 必须进入预提交上传和参数身份链；若暂不支持，应在提交前拒绝该配置。验收应比较两端的有效计算参数和电子态载荷，而非只比较命令中包含几个普通选项。

### B02 — GJF 与 ORCA 输入摄入丢失电荷、多重度【P1；已复现；现行路径】

位置：[GJF 底层读取](src/cccp/utils/file_io.py:90)、`cccp/io/input_handler.py:312`；[ACP GJF 摄入](src/acp/intake/parsers.py:630)、同文件 `:694` 的 ORCA 摄入。

标准 GJF 的结构是 route、空行、标题、空行、电荷与多重度、坐标。ACP 实现把第一处空行后第一个非空行当成电荷行，实际读到标题，再回退到 `0, 1`。CCCP 实现用带点号的特殊 token 方式提取状态，未正确识别标准的 `1 2`。输入 `He`、电荷 +1、多重度 2 时，三个入口都得到 `0, 1`。

ORCA 摄入遇到 `* xyz 1 2` 时只切换到坐标区，没有读取 header 中的两个数字，同样得到 `0, 1`。坐标通常仍可读入，因此用户不一定能从“导入成功”看出电子态已经丢失。

**证据键：** `standard_gjf_charge_multiplicity_loss`、`standard_orca_intake_charge_multiplicity_loss`。影响是导入信息错误；若用户后续明确覆盖 charge / multiplicity，覆盖可能修正状态，不能断言每次导入都必然产生错误计算。

**建议：** 将结构和电子态解析结果统一为明确契约，按格式的段落 / header 规则解析；缺失、格式错误与真实的 `0, 1` 必须区分。补齐带标题、带 Link0、正负电荷、开壳层及 ORCA `* xyz` 的测试。现有仅用中性单重态的样例不足以发现此问题。支持 GJF 导入不意味着恢复退役 Gaussian 计算入口。

### B03 — 任务层接受截断和非有限坐标的 XYZ【P1；已复现；现行路径】

位置：[read_xyz](src/cccp/utils/file_io.py:20)、[load_geometry](src/cccp/calculation/_common.py:95)。

`read_xyz` 按文件头截取若干行，短行被跳过，但不检查实际解析原子数是否等于声明数。`load_geometry` 随后检查数组形状与符号长度，没有验证原文件的数量完整性。头部声明 3 个原子、实际只有 2 行坐标，任务加载器正常返回 `(2, 3)` 数组。含 `nan` 的坐标也通过，`isfinite` 结果为 false。

**证据键：** `truncated_xyz_accepted_by_task_loader`、`nonfinite_geometry_accepted_by_task_loader`。这不是只存在于退役模块的缺陷；该 helper 是当前 CCCP 计算任务的公共输入基础。部分 ACP 文本摄入或特定工作流已有额外校验，不能替代任务层对路径输入和直接调用的保护。

**建议：** 在底层 reader 保留声明数量并严格校验，每行必须包含合法原子与有限三维坐标；在任务入口再次保证非空、形状正确、符号数量一致和坐标有限。失败应在调用 backend 前返回明确输入错误，避免改成“少读一些原子也继续算”。

### B04 — XYZ 写入截断，多帧读取不验证原子身份【P2；已复现；现行基础工具】

位置：`cccp/utils/file_io.py:63`、`:247`。

`write_xyz` 的头部按 `len(symbols)` 写入，正文用普通 `zip(symbols, coordinates)`。两个符号配一行坐标时，输出声称有 2 个原子，实际只有 1 行，没有抛错。

`read_xyz_multiframe` 只保存第一帧符号。第一帧为 C/H、第二帧为 N/H、原子数相同时，返回 C/H 作为所有帧的符号表，将后续坐标拼接。除了元素变化，同元素原子重排仍需要上游映射契约约束；仅比较符号无法解决所有映射问题。

**证据键：** `xyz_writer_silently_truncates`、`multiframe_element_identity_loss`。多帧 reader 被实际轨迹 / 构象代码使用，但本次没有证明正常外部软件产物会出现元素变化。

**建议：** writer 在写文件前验证尺寸；多帧 reader 校验每帧原子数和符号顺序，并在不一致时报告帧号。若产品需要跨组成帧，应使用每帧独立身份的结果结构，而非复用第一帧符号。

### B05 — 非均匀 λ 被记录，但目标约束仍按均匀步长计算【P1；已复现；现行扫描契约】

位置：[ReactionCoordinatePlan](src/cccp/qc/interfaces/constraints.py:187)、同文件 `:238`、`:250`；`cccp/qc/interfaces/orca.py:2506` 的同步扫描消费。

构造器接受严格递增、首尾为 0 和 1 的非均匀 `lambda_values`。但 `frame_constraints` 和 `coordinate_targets` 都用 `index / (points - 1)`。距离从 1.0 到 3.0、λ 为 `(0.0, 0.2, 1.0)` 时，中间目标应为 1.4，实际为 2.0。ORCA 消费端又能将该帧 progress 记录为 0.2，形成进度与约束语义不一致。

**证据键：** `nonuniform_lambda_targets_use_uniform_progress`。触发条件是非均匀 λ 且坐标未用显式 `values` 指定网格；已有显式 values 的路径应分别验证，不应一概宣称所有 scan 网格有误。

**建议：** 定义唯一的 `progress_at(index)`，目标、约束和记录统一消费。若契约只允许均匀 λ，应拒绝非均匀输入并移除误导性的可接受状态。修改 QC 接口时须按项目 F4 amendment 护栏登记。

### B06 — 覆盖扫描点数会丢失完整计划字段【P2；已复现；现行兼容入口】

位置：[build_scan_plan](src/cccp/calculation/tasks/scan.py:245)，特别是 `:267` 的重建分支。

raw plan 的 points 与 `options.points` 不同时，仅用 coordinates、points、coupling、start_from 重建对象。原计划中的 `lambda_values`、`reference_geometries`、`fixed_endpoints` 和 `xtb_scc_max_iterations` 没有转入新对象。探针将 3 点非均匀计划覆盖为 4 点，结果 λ 直接变为空，而不是明确报告网格长度冲突。

**证据键：** `plan_point_override_drops_full_fidelity_fields`；其余字段丢失由构造参数静态确认。原 docstring 声称保持 full-fidelity，现有同名测试也未覆盖这些扩展字段。

**建议：** 不兼容的点数覆盖应拒绝或按显式策略重采样；可保留字段必须保留，不能仅靠 `replace` 把不兼容长度继续塞入新对象。验收同时检查参考几何、固定端点和 SCC 限制。

### B07 — 扫描原子索引只校验上界【P2；已复现；CCCP 公共调用】

位置：`cccp/calculation/requests.py:534`；`cccp/calculation/tasks/scan.py:287`、`:312`。

1-based atoms `(0, 1)` 被转换为 0-based `(-1, 0)`；`validate_atom_indices` 仅检查 `atom >= atom_count`，因此通过。负数进入 Python 几何索引可能指向末尾原子，进入 backend 文本也可能产生非法约束。

**证据键：** `negative_scan_index_passes_task_validation`。已确认直接 CCCP 请求可触发；未验证每个 UI / CLI 表面都允许该输入。现有扫描测试覆盖超大正索引，没有覆盖零 / 负索引。

**建议：** request 校验源索引基准，任务层再执行 `0 <= atom < atom_count`；按坐标种类验证需要的原子数及是否允许重复原子。

### B08 — CASSCF 的本地与远程执行能力没有统一登记【P2；builder 缺口已复现】

位置：`acp/scheduler/runner.py:1420`；`acp/scheduler/remote/script_gen.py:128`；`acp/scheduler/manager.py:4479` 与 `scheduler/nodes.py` 的节点解析。

CASSCF 是 active 工作流，本地 builder 支持，远程 builder 的允许集合中没有它。直接构建命令得到 `No remote subprocess mapping for workflow: casscf`。节点选择主要匹配软件要求，没有与远程 builder 共享同一工作流执行能力表。

**证据键：** `remote_casscf_builder_gap`。远程 builder 不支持是确定事实；普通 API 提交是否在更早的具体配置环节被拦截，需要补端到端用例，不能把 late failure 当成已完整复现。

**建议：** 在工作流 registry 中登记 local / remote 能力，提交校验、节点选择和 builder 都消费该事实。CASSCF 可补实现或明确限本地。XtbPathSearch / OrcaGradient 的已规定本地专用范围应保持，不能为了对齐列表擅自新增远程支持。

### B09 — Boltzmann helper 未校验物理温度【P2；已复现；现行共享 helper】

位置：[boltzmann_weights](src/acp/confsearch/shared/boltzmann.py:13)、同文件 `:58`。

传 `-298.15 K`、两个构象相差 1 kcal/mol，两个 missing 策略都接受输入，得到高能构象约 0.844 的权重。零温还会在倒数处失败。部分上游模型可能已有温度限制，但 helper 的公开契约本身没有这项保障。

**证据键：** `negative_temperature_accepted_zero`、`negative_temperature_accepted_none`。这里只确认 helper 行为，不能推定正常向导默认温度有误。

**建议：** 所有共享 Boltzmann 实现统一验证 `isfinite(T) and T > 0`，并保持明确的缺失能量政策。补物理无效温度和极端能量差的数值稳定性用例。

### B10 — 配置校验拒绝 typo，服务启动却回退本地执行【P1；已复现；现行服务配置】

位置：[server._load_remote_config](src/acp/api/server.py:50)；`acp/scheduler/remote/config.py:347`。

RemoteExecutionConfig 对非法 execution_mode 抛 ValueError，意图是避免拼写错误改变执行位置。server 捕获所有 Exception，返回 `execution_mode="local"`。配置为 `remot` 的隔离探针确实得到 local。该代码会记录 warning，问题是依然继续采用另一种执行模式，而不是完全没有可观察性。

**证据键：** `invalid_remote_config_becomes_local`。已证明配置解析结果变化；没有真实提交作业来证明本机消耗了资源。

**建议：** 区分“未配置可选远程功能”与“明确配置但内容无效”。后者应使启动或远程功能就绪检查失败；如果允许 fallback，应由明确配置决定，并在 API 能力状态中体现原因。已有 `test_load_remote_config_degrades_on_exception` 测的是当前回退行为，不能作为执行位置正确的证明。

### B11 — 旧 CandidateSet 的能量单位混用【P2；已复现；遗留公开 API】

位置：[CandidateSet](src/cccp/core/candidates.py:132)、`:191`；与当前 `acp/confsearch/shared/boltzmann.py` 对照。

candidate.energy 的契约是 Hartree，旧权重函数却直接除以 kcal/(mol·K) 的 kB；能量窗口也直接拿 Hartree 差与 window_kcal 比较。两个构象相差 1 kcal/mol 时，旧权重约为 `(0.50067, 0.49933)`，正确的 Hartree-aware 权重约为 `(0.84394, 0.15606)`。0.5 kcal/mol 窗口错误地保留两个构象。

**证据键：** `legacy_candidate_energy_unit_mismatch`。该模块明确 DEPRECATED，未找到现行 ACP 生产链调用，不能据此说当前 Confsearch 的所有权重都算错。旧测试使用 0、1、2 这类无单位示例并主要检查排序 / 归一化，容易掩盖这个错误。

**建议：** 保留公开导入路径，统一单位换算和共用实现；若按遗留兼容策略不修，则明确支持限制和错误行为，不能仍暗示该 API 的科学语义可靠。清理优先级低于现行链路。

### B12 — 遗留 JobStore API 可复活终态、留下孤儿索引【P2；已复现；未发现现行 manager 调用】

位置：[JobStore.update](src/acp/scheduler/store.py:139)、[JobStore.delete](src/acp/scheduler/store.py:724)。

`update` 无 CAS 守卫整行写 status / spec / result，且不变更 revision。临时 DB 中先用正确 CAS 将任务置 COMPLETED，再写回旧 QUEUED 记录，任务变回 QUEUED，revision 仍为 1。`delete` 直接删 jobs；同步过 TaskIndex 后调用该方法，job 已不存在而 tasks 行仍存在。

**证据键：** `legacy_store_update_revives_terminal`、`legacy_store_delete_leaves_task_row`。搜索没有发现 JobManager 生产链继续调用这两个 JobStore 方法。其他组件的 `.store.update` 可能属于 StageTaskStore 等对象，不能按同名方法误判。

**建议：** 收口或禁用不安全公共入口；需要兼容时通过 CAS 或 `purge_cascade` 实现，而非留下“只要别调用就安全”的接口。旧测试准备数据也应使用专门 helper。`update` 中“保留到 manager 迁移完成”的注释已经与迁移收口状态不一致。

### B13 — 旧 LSF adapter 的 shell / argv 组合不会执行重定向【P3；语义复现；遗留 API】

位置：`cccp/qc/cluster/__init__.py:300`、`:326`。

`subprocess.run(['bsub', '<', script], shell=True)` 在 POSIX 中把列表后两项作为 shell 位置参数，而不是输入重定向。用无害命令复刻相同 argv 形状，stdin 没有得到文件内容。这意味着该旧 adapter 的提交写法不能按作者意图向 bsub 送入脚本。

**证据键：** `legacy_lsf_shell_list_does_not_redirect`。未运行 bsub；该层明确 deprecated，现行 ACP 远程 runner 使用另一条执行链，不能把这项结论套到现行 LSF 提交。

**建议：** 按遗留支持策略修为明确 stdin 或明确 shell 字符串，补替身进程验收；不要为了这一处旧 API 重建第二个远程调度实现。

## 4. 无效、退役和仅由旧测试维持的代码

### D01 — 十个私有辅助函数在仓库内没有引用【P3；静态确认】

AST 初筛得到 24 个无 name-load 的私有候选、共 276 行。再用 `rg` 核对源码、测试、scripts、frontend，排除转发、测试使用和同名误命中后，下面 10 个是较明确的清理候选。统计只计函数体所在物理行，不包含关联 imports / data，因此不是完整删代码收益估计。

| 位置 | 函数 | 行数 | 判断 |
|---|---|---:|---|
| `acp/api/v1_routes.py:409` | `_enrich_from_state_json` | 3 | 旧包装；现行消费 `_enrich_job_snapshot` |
| `acp/calculations/pes/path_selection.py:665` | `_reactant_reference_frame` | 5 | 当前选点实现未调用 |
| 同文件 `:715` | `_resolve_basin_candidate` | 36 | 旧 basin 解析分支辅助函数 |
| 同文件 `:753` | `_resolve_shoulder_candidate` | 49 | 旧 shoulder 解析分支辅助函数 |
| `acp/intake/parsers.py:21` | `_reset_asset_counter` | 3 | 无调用的计数器重置 helper |
| `acp/scheduler/jobs.py:812` | `_select_flag` | 7 | 无调用的参数翻译 helper |
| `acp/scheduler/runner.py:195` | `_opt_text` | 6 | 无调用的文本转换 helper |
| `acp/scheduler/structure_sources.py:181` | `_select_rank1_conformer` | 6 | 无调用的旧结果选择 wrapper |
| `acp/workflows/pes_search.py:75` | `_load_reaction_definition` | 10 | 无调用的读取 helper |
| `cccp/calculation/contracts.py:377` | `_enum_value` | 2 | 无调用的枚举辅助转换 |
| **合计** | **10 个** | **127** | 仓库内零引用，不宣称外部绝对零使用 |

建议做小范围删除，复查关联导出、文档和 imports；不应借此整体删除 `path_selection.py`。`getattr`、字符串注册或外部私有导入无法被普通静态扫描完全排除，清理前仍应查注册表。

### D02 — PES 生产流程已改为人工选点，自动推荐链仍由旧测试维持【P2；静态确认】

位置：`acp/calculations/pes/scan.py:263`、`:485`、`:514`；旧链自 `:1351` 的 `_recommend_candidates` 开始，经 `_recommend_bond_candidates`、`_select_distance_seeds`、`_recommend_coordinate_candidates` 等函数。

`run_pes_scan` 明确生成空的自动推荐 TS / INT 集合，标记 `manual_only`。`_recommend_candidates` 的仓库调用来自 `tests/test_pes_constraint_gate.py`，生产 run 路径没有调用它。旧推荐逻辑仍占据显著代码和测试空间，文件开头及 policy 描述容易让人误以为它仍决定生产候选。

这里证明的是推荐链退出了现行 run 路径，不是所有路径分析、轨迹查看和质量诊断都无用。`select_path_seeds` 等公开 / re-exported API 和其独立测试也不能只凭退役入口就删除。

**建议：** 明确是否继续支持离线分析 API；支持则隔离并标注范围，不支持则连同旧私有推荐测试收口。生产测试应验证 manual-only、帧候选保存与读取契约。不要恢复 energy viewer 直接提交 BatchOptimize 的退役行为。

### D03 — 本地和远程 builder 仍有退役工作流提交分支【P3；静态确认】

位置：`acp/scheduler/runner.py:1221`、`:1360`、`:1390`；`acp/scheduler/remote/script_gen.py:128` 及相应 elif；`acp/scheduler/manager.py:818` 的提交允许集合。

builder 仍列举 / 构建 `ensemble`、`energy`、`xtbmd_censo_energy` 的 CLI run 命令。现行 public workflows 不允许这些 id，CLI 也拦截它们。对有效的新任务，这些分支不会成为可运行路径；即使直接调用内部 builder，得到的也是被 CLI 拒绝的命令。`wf != "mech-chain"` 的条件对当前 builder 接受的有效工作流恒成立。

**建议：** 收口退役 CLI 命令构建分支，保留历史记录展示和结果读取。`workflows/ensemble.py`、`energy.py`、`xtbmd_censo_energy.py` 的内部科学引擎仍供 Confsearch 使用，不能与这些提交分支一并删除。

### D04 — 结构来源列表存在条件不可达分支，并调用已不存在的方法【P3；静态确认】

位置：`acp/scheduler/structure_sources.py:429`、`:458`；`acp/scheduler/store.py:574`。

`list_recent` 取 `list_recent_terminal`，后者 SQL 只返回 COMPLETED / FAILED / CANCELLED。循环先处理非 COMPLETED 的 terminal 项，随后又判断“非 COMPLETED”并调用 `_discover_terminal_frame_candidates`，但类中没有这个方法。按真实 JobStore 的返回契约，该分支不可达，因而当前缺少方法未必触发运行时异常；使用更宽松替身或今后改变 store 契约时可能触发。

mypy 能发现缺失方法，却不能直接证明生产用户已经遇到 AttributeError。建议删除这个旧分支或恢复一个明确的非终态查询契约和完整实现，避免保留被前序条件掩盖的坏调用。

### D05 — 明确 deprecated 的公开库面处于保留但难以保证可用的状态【P3；支持范围问题】

对象包括 `cccp/core/candidates.py`、`state_manager.py`、`cccp/pipeline/executor.py`、`cccp/qc/cluster` 以及部分旧资源解析 API。模块说明已有“无 ACP 生产调用、外部用户不确定、无移除计划”的证据和支持约定。

`PipelineExecutor.execute_final_opt_sp` 仍委托给外部传入 engine 的 `_run_shared_dft_handoff`，被包装的旧 engine 已移除；`execute_handoff` 又直接用候选能量差与 `small_gap_kcal` 比较，显示与 B11 同类的单位边界缺口。传入特制外部 engine 仍可能满足 duck type，因此不能把整个类描述为绝对不可执行。

**建议：** 为 retained API 建最小支持矩阵：仅保留 import、保留历史读取、保证计算行为，三者应分开。对承诺计算行为的 API 补少量契约测试；对其余 API 明确 deprecated 范围。项目已明确保留这些导出，本次不建议未经兼容决策删除整个包。

### D06 — 旧 helper 的测试通过，不等于现行发布路径被验证【P3；静态确认】

位置：`acp/workflows/simple.py:211`、`:216`、`:221`、`:234` 的四个旧文件 writer，合计 33 行；`acp/cli.py:83` 的 `_parse_levels`。

这些 helper 在仓库中主要 / 仅被旧测试调用，现行 simple 运行使用 CalculationPlanExecutor 与任务发布契约；现行 CLI 另有 `_parse_levels_json`。继续维护旧 helper 的测试会增加“测试很多，但实际入口边界缺失”的错觉。

**建议：** 查明旧导入是否是承诺兼容面；是则留薄转发和兼容测试，否则删除 helper 并把重要断言移到现行入口。不要把 `ProgressReporter._overall_progress` 等明确被测试检查的内部 hook 因生产 call-count 为零而直接删除。

## 5. 结构和维护问题

### C01 — 同一任务参数经过两个大型命令转换器【P2；确定的结构问题】

位置：`scheduler/runner.py:1221` 与 `scheduler/remote/script_gen.py:148`。

两端都承担 workflow 判断、method levels 翻译、资源、电子态、扫描 / IRC、配置路径等转换。它们已有不同的支持集合和参数处理，B01、B08 是实际差异，D03 又显示同样的退役分支在两端保留。单纯要求每次新增参数“记得两边一起改”难以持续保证完整性。

建议先建立不依赖进程或 SSH 的统一执行请求编译器：输入是 JobSpec 和工件定位规则，输出 argv、需要准备的 sidecar、有效参数摘要。local / remote runner 只处理环境和工件传输。不要把 subprocess 放进 backends，也不要把 CCCP 与 ACP 依赖方向倒置。

### C02 — v1 路由文件过大，v2 又依赖其私有实现【P2；静态确认】

位置：`api/v1_routes.py`；`api/v2_routes.py:26`、`:151`、`:209`、`:220`。

v1 的 224 个函数混合了任务提交、输入快照、结构和轨迹投影、项目管理、PES 人工审核、NMR 处理、文件下载、节点与软件配置等职责。v2 直接引用 v1 的 `_apply_auto_tag_rules_on_submit`、`_enrich_job_snapshot`、`_record_to_v1_model`，同时复制 `_is_remote_job` / `_job_read_root`。复制理由的注释仍是迁移期“v1 文件有用户未提交改动”，不是长期设计依据。

当前两个 `_job_read_root` 都使用 manager-owned RemoteStructureCache，并处理 terminal catalog fetch；本报告没有发现它们已经绕过该契约。风险是未来修补只改一边，或 v1 内部改名影响 v2。

建议先提取共享投影、远程读根和提交服务，再按领域拆 APIRouter；保持 URL、response schema 和 `_job_read_root` 语义。按 API 版本命名的路由模块不应成为另一版本的业务服务实现根。

### C03 — JobManager 聚合太多职责，关闭过程有无界等待点【P2；结构确认，运行风险待专项验证】

位置：`scheduler/manager.py`；其中 `move_job:997`、`_persist_submit_intent:3402`、远程提交 `:4370`、`_stop_background_work:3005`、`shutdown:3051`。

5,758 行 manager 同时拥有生命周期 CAS、容量预留、目录映射、项目移动 / 重命名、远程版本绑定、提交租约、对账、结果预取、清理和索引同步。一个错误处理分支可能同时涉及 DB、目录和远程收据，审查边界很大。

关闭时 background thread 的 `join()` 没有 timeout；外层 pass-limit 限制重查次数，不限制单次 join 的等待时间。若工作线程卡在无法被 stop event 中断的操作，关闭可能长期等待。本次 TestClient 的停滞发生在框架 portal 进入处，空应用也复现，不能用那个现象证明这里已经发生了死锁。

建议按提交 / 对账 / terminal 副作用 / 目录组织拆协作者，由 manager 保留协调入口和明确锁边界。关闭专项演练应包括慢 SSH、卡住的 fetch、停止中提交；保持“线程停止后再释放 run_root 所有权”，不要靠提前释放锁制造第二个 manager 同时执行的窗口。

### C04 — 科学执行与结果发布集中在超长函数【P2；复杂度与职责确认】

重点是 `CalculationPlanExecutor.execute` 558 行、ORCA 输入块渲染 399 行、`run_xtbmd_censo_energy` 916 行、`run_conformer_energy` 660 行、`run_nmr_analysis` 534 行。这些函数仍在实际链路中，不是仅因旧名称就可删除的遗留文件。

Executor 同时处理计算身份、checkpoint、依赖、恢复、执行、结果有效性和发布。科学协议函数也集中处理筛选、异常恢复、外部调用、热化学和输出。典型问题是局部改动需要理解整个函数，错误和成功路径共享大量可变状态。

建议按纯参数解析、身份 / 恢复决定、任务调用、结果验收和发布分离。保持每一步的显式结果类型和失败政策。拆分时必须继续满足：旧结果自证身份；上游失败阻断必需步骤；发布失败仅重试发布；诊断结果不当作正常可复用结果。减少行数不能成为删除这些守卫的理由。

### C05 — 共享 helper 半迁移，科学算法仍有完全相同的维护副本【P2；AST 与生产调用确认】

`confsearch/shared/helpers.py` 声称 helper 已搬迁、`energy_shared.py` 可以删除，但 `workflows/energy_shared.py` 仍约 1,600 行，并被 NMR、ensemble、energy、xtbmd-censo 活跃调用。两处 `_base_route_extras` 的去除 docstring 后 AST 完全相同；`resolve_crest_ewin` 也相同，`resolve_levels` 则具有较大的重复语义范围。

更有价值的去重是 `confsearch/sampling.py:191` 的 `equilibration_cutoff` 与 `workflows/xtbmd_censo_energy.py:241` 的 `_equilibration_cutoff`：42 / 40 行函数的正文 AST 完全相同，两个位置都有生产调用。这会使一个科学判据的修正需要同步两份实现。

另外，原子写 JSON / text、`_format_bytes` 等辅助函数存在多份相同正文。本次找到 10 组至少 12 行的 AST 正文完全重复，但其中包括兼容结果转换器和独立 adapter 的配置初始化，不能把 10 组全部当成重复科学实现。

建议优先统一平衡区间判据、level / route 翻译和原子写入工具，公开旧路径留转发。不要在尚有活跃调用时按 helpers.py 的注释直接删 `energy_shared.py`；也不要把 XTBInterface 并回 CREST 破坏既定接口分离。

### C06 — 科学常量和 Boltzmann 语义没有单点权威【P2；静态确认，B11 为实际后果】

`HARTREE_TO_KCAL` 有五处赋值定义：

| 文件 | 行 | 值 |
|---|---:|---|
| `cccp/utils/constants.py` | 6 | 627.5094740631 |
| `acp/core/models.py` | 19 | 627.5094740631 |
| `acp/confsearch/shared/boltzmann.py` | 8 | 627.5094740631 |
| `acp/results/energy_graph.py` | 24 | 627.5094740631 |
| `acp/calculations/pes/path_analysis.py` | 20 | 627.509 |

气体常数 / Kelvin-to-Hartree 还以命名常量和内联数值散落在 CandidateSet、NMR、energy_shared、xtbmd-censo、ensemble_thermo、CENSO 接口等位置。精度差异本身很小，本次没有证据表明 627.509 的截断已造成实质科学结论错误。更重要的是单位与温度、缺失能量和筛选政策也分散实现，B11 已证明单位边界可以出错。

建议跨包允许的底层 constants 成为数值单点；相同语义的权重计算统一，共享数值核与各产品的筛选政策保持清晰分离。统一时保留现行 missing 政策和 NMR 的人口证据，不要把所有输出都强行改成一种未定义的 fallback。

### C07 — 异常兜底和 DB / 文件镜像写入缺乏可修复性【P2；静态确认，部分并发影响待验证】

330 个广泛捕获中，manager 49、v1_routes 37、remote runner 20；其余分布在化学嵌入、PES 映射、NMR、远程清理和存储等模块。顶层边界为了记录失败、释放资源或处理终态，广泛捕获后重新抛出可以合理存在；真正应审查的是用默认值 / pass 把不变量破坏隐藏的地方。

例如 `scheduler/tasks.py:794` 在 DB 名称修改已 commit 后写 task.json，`:806` 捕获所有 Exception 后 pass，调用者仍拿到成功结果，磁盘镜像没有日志或重试凭据。`store.py:769`、`:784`、`:800` 的项目 / 路径 / 名称方法直接更新 spec_json，没有 attempt / revision CAS 守卫；manager 的组织操作又跨越文件移动、DB 和索引。这是具体的一致性审查点，不等于证明所有生命周期 CAS 已失效。

建议将“DB 是权威、文件是可重建镜像”作为明确政策；镜像失败记录待重建项并幂等重试。对会覆盖 spec 的组织操作补条件写和并发演练，验证与 rerun / edit 的竞争。无需把所有异常都向用户弹窗，也不应把科学输入错误转换成成功的空结果。

### C08 — 导入图耦合较重，app 工厂仍有导入期 I/O【P2；静态和隔离复现】

静态导入图有 7 个多模块强连通组，最大组 32 个模块，集中在 scheduler / remote / results / workflow registry。该图包含包 re-export、函数内 lazy import 和 TYPE_CHECKING；强连通不能直接证明运行期循环导入错误，当前源码可以正常导入探针依赖。

`api/server.py:278` 的 `app = create_app()` 在导入时解析配置并于 `:186` 创建 run_root。隔离探针确认新临时目录在 import 后存在。当前 JobManager 已在 lifespan `:199` 中创建，没有证据支持“导入 server 就启动调度线程”的旧说法。

建议减少包根 eager re-export 对重型模块的传播；共享业务对象由较小模块提供。配置副作用尽量移到明确启动阶段；若保留 module-level app，工厂应尽量只定义路由和生命周期。对 CLI、库调用和 TestClient 都建立可控的配置注入。

### C09 — 质量门被推迟，工具版本和规则不能形成稳定增量约束【P2；静态确认】

`.github/workflows/ci.yml:81` 的 lint / format 仍注释。pre-commit 的 Ruff 固定为 0.4.0，而 dev extra 允许 `ruff>=0.4.0`，本次环境是 0.15.22。mypy 同样只有最低版本，本次使用 2.3.1。工具和依赖升级可能改变计数，因此本报告的 676 / 577 不应与历史 CI 注释中的数字直接相减来判断回归。

大多数 Ruff 债务可批量格式化或语法现代化，但需要对兼容 re-export 和外部 API 的 F401 特别审查。全库一次自动修复会淹没行为变更，也可能误删兼容导入。

建议先统一工具版本，再按模块维护可收缩基线或启用只针对新增问题的检查；格式化独立提交，科学行为修复单独审查。恢复门禁时保持现有完整 extras、架构唯一驻点、QC amendment 和恢复 fixture 检查，不应以关掉规则达到绿色。

### C10 — 类型契约噪声掩盖真正的结构残留【P2；mypy 与人工复核】

mypy 的 577 项中，arg-type 111、union-attr 72、assignment 64、attr-defined 50，另有返回类型、未注解调用和显式 re-export 等问题。部分来自第三方 RDKit 的类型可见性、动态 from_dict 工厂、shim 导出，以及声明过宽的 object / Any，而不是运行时缺少方法。

人工复核发现：`workflows/nmr.py:1140` 的 populations.append 告警与此前分支把同名变量推断为 tuple 有关；那个 tuple 分支提前 return，后面实际创建的是 list，不能凭告警断言每次 NMR 都会崩溃。另一方面 D04 的缺失方法是实际结构残留。`manager.py:4410` 的 `_sub_id(job_id)` fallback 少传 attempt，真走该表达式会 TypeError，但正常 `_persist_submit_intent` 总会写 submit_owner，所以不能直接升级为正常远程提交必现故障。

建议先给任务结果、backend 返回值、组织投影和请求编译定义具体 Protocol / DTO；薄转发显式声明 re-export。类型收敛应让不可能状态和真正的参数遗漏更容易被发现，而非批量添加 ignore。配置写着 strict、同时关闭 disallow_untyped_defs，不等于所有关键边界都已经严格建模。

## 6. 文档与测试结论

### G01 — 知识库和迁移说明存在过时描述

Root 知识库说 YAML protocols 节不可达，但 `cccp/core/protocols.py:145` 的 resolve_protocol_spec 明确把 `config['protocols'][protocol]` 作为优先于 built-in defaults 的来源。这里存在文档与代码冲突，本次没有改任一方；新增协议仍应遵守用户给定的 built-in 落点规则，但分析现状时不能据“不可达”删除当前会读取的配置。

API 知识库对文件规模、模块数量和 import 时 JobManager 的描述也落后于当前实现；旧架构报告和迁移台账属于历史审计材料，不能把迁移前的待办当成当前架构失败。helpers.py 的“energy_shared 可以删除”、JobStore.update 的“保留到迁移完成”则容易直接导致错误清理决策。

建议统一标记历史文档的基线与适用时间，更新实际调用入口、配置优先级和 retained API 的支持范围。不要为让文档显得一致而改变现行科学参数优先级。

### G02 — 现有测试覆盖了结构护栏，但缺少跨路径和物理边界断言

328 项定向测试通过，同时行为探针复现了上述缺陷。两者并不冲突：测试很好地验证了架构唯一驻点、恢复身份和一些典型任务，但并未穷尽输入状态或两端参数等价性。

| 已发现缺口 | 当前测试容易遗漏的原因 | 应补的验收 |
|---|---|---|
| GJF / ORCA charge、multiplicity | 样例以中性单重态为主 | 标准格式中 `+1, 2`、负电荷、多重度的完整保存 |
| XYZ 截断、NaN、多帧组成 | 主要使用格式正常的 fixture | 数量不符、坏行、非有限值、元素 / 顺序不符都拒绝 |
| local / remote electronic state | 单独测 CLI 与本地 payload | 相同 JobSpec 在两端编译出同一有效电子态与方法 |
| raw scan plan | full-fidelity 测试没有扩展网格字段 | λ、参考几何、固定端点、SCC 参数与点数冲突政策 |
| scan 原子索引 | 覆盖大正索引越界 | 1-based 零 / 负值与 0-based 负值拒绝 |
| CandidateSet 单位 | 检查排序和权重和，而非物理值 | 1 kcal/mol 差值的参考权重和 0.5 kcal/mol 窗口 |
| 配置执行位置 | 回退测试明确期待 graceful degradation | typo 拒绝与缺省可选配置分别处理 |
| 遗留测试与现行入口 | 直接调用旧私有 helper | manual PES、现行发布 / result_manifest 的入口验收 |

建议只补能阻止实际缺陷回归的行为测试，不为删去几个无调用私有函数逐一写镜像测试。

### G03 — 验证环境限制和没有完成的检查

全量 `pytest -m 'not slow' tests/` 和最初的定向组合长时间停滞，审计主动中断，退出码为 130。定向组合的已完成用例顺序指向下一个 API 用例；全量运行的简略日志不足以确定当时的具体用例。随后单独运行 `test_legacy_api_mechanism_returns_410`，75 秒超时，faulthandler 显示主线程等待 Starlette / AnyIO portal，事件循环线程停在 selector；未出现 ACP manager 代码栈。

再以完全不导入 ACP 的空 FastAPI 应用做对照，同样在 TestClient.__enter__ 停滞并 15 秒超时。这支持“本环境 / 依赖组合的框架进入问题”判断，但尚未定位到某个包版本或系统限制。没有以更高权限重跑，也没有改变依赖或服务配置。

排除该 API 用例后，相同相关组合在 51.46 秒内结束，328 passed / 1 deselected。完整 suite、浏览器、真实 QC 二进制、真实 SSH / LSF、完整任务提交和并发故障演练仍为 NOT_VERIFIED。没有把超时、中断或缺少真实环境算成测试通过。

## 7. 整改顺序与验收边界

### 第一批：计算输入和请求语义

建议将 B01、B02、B03、B05、B10 分成可独立审查的行为修复，每个先固定一个失败样例，再修实现并运行相关测试。B06、B07 与 B05 共享 scan 契约，可在同一领域连续处理，但不要把整个扫描层重写后才验收。

这一批的完成标准是：无效输入在 backend 前被拒绝；合法 charge / multiplicity 与电子态完整到达执行请求；计划中的 λ 和真正约束一致；配置错误不能未经显式策略改变执行位置。修复 QC 接口时登记 F4 amendment，保持唯一计算实现根。

### 第二批：消除两端差异和不安全遗留入口

统一 local / remote 请求编译，建立 workflow 执行能力表，处理 B08。收口 JobStore.update / delete 的危险接口以及 B11、B13 的 legacy 支持政策。先做公共兼容面清单，不能用“没有生产调用”替代外部兼容决策。

这一批的完成标准是：参数新增只有一个语义转换位置；不支持的执行节点在提交前被明确拒绝；状态写不能绕过 CAS；清理走级联；retained 科学 API 的单位与最小行为承诺有证据。

### 第三批：清理退出生产链的代码和共享副本

先处理 D01 的 127 行私有候选和 D04 的不可达分支，再明确 D02 / D06 的支持范围，去掉失效的退役 CLI 构建分支。将 C05 的平衡区间判据、level helper 与 C06 的常量逐项收成一个实现，保留必要薄转发。

验收不是“代码删了多少”，而是现行调用链更清楚、旧测试不再暗示退役生产行为、重复科学算法只改一处、历史结果和兼容路径仍可按承诺读取。不能重新开放 retired workflow，也不能放宽架构 grep gates。

### 第四批：模块职责和质量门

在上述行为修复形成稳定边界后拆 API 和 manager，逐步收敛 executor / NMR / ORCA 的长函数。独立进行格式化和类型契约修复，统一工具版本，再恢复增量质量门。

目录组织与 DB / 文件镜像专项测试应覆盖 rename / move 与 rerun、edit 的竞争，关闭演练覆盖慢远程 I/O。必须维持 attempt 的目录与版本身份、控制意图 CAS、身份有效的恢复、上游前置条件这四个执行不变量。

### 不建议的清理方式

- 按文件名 legacy / retired、包根无 incoming import 或没有直接调用，一次删整个目录。
- 为消除重复文件，把 ACP 的任务实现重新写回 primitives，或让 CCCP 导入 ACP。
- 先全库 `ruff --fix` 再混入科学行为变化，使兼容和行为变化难以复核。
- 用 catch-all、默认状态或空结果“保持流程继续”来遮住输入和身份错误。
- 为削减长函数删掉 CAS、恢复身份、发布重试或依赖阻断分支。

## 8. 证据、复现与交付说明

### 8.1 证据索引

| 文件 | 内容 |
|---|---|
| [audit_inventory.py](audit_inventory.py) | 只读 AST 清单、分支评分、重复函数正文、静态导入图脚本 |
| [reproduce_findings.py](reproduce_findings.py) | 19 项隔离行为探针；使用临时文件和临时 DB，不启动 QC / SSH |
| [ast_inventory.json](evidence/ast_inventory.json) | 两包全量统计、各函数位置、候选和重复组 |
| [reproductions.json](evidence/reproductions.json) | 行为探针结构化输出；以第 3 节的证据键检索 |
| [reproduction_run.txt](evidence/reproduction_run.txt) | 探针运行输出，包含配置回退 warning |
| [ruff_src.json](evidence/ruff_src.json) | 当前源码 Ruff 告警明细 |
| [ruff_format.txt](evidence/ruff_format.txt) | 当前源码格式检查 |
| [mypy.txt](evidence/mypy.txt) | 类型检查完整输出 |
| [architecture_gates.txt](evidence/architecture_gates.txt) | 架构整改 suite 的实际运行结果 |
| [verified_pytest.txt](evidence/verified_pytest.txt) / [XML](evidence/verified_pytest.xml) | 328 passed / 1 deselected 的完整定向验证 |
| [pytest.txt](evidence/pytest.txt) / [focused_pytest.txt](evidence/focused_pytest.txt) | 中断的全量 / 初次定向运行，不能视为完成 |
| [testclient_diagnostic.txt](evidence/testclient_diagnostic.txt) | 单独 ACP API 用例的超时栈 |
| [testclient_baseline.txt](evidence/testclient_baseline.txt) | 空 FastAPI 对照的超时栈 |
| [audit_run.json](evidence/audit_run.json) | 环境版本、检查命令、退出码及最终验证状态 |

### 8.2 复现命令

以下命令从仓库根目录运行，使用当前已有依赖。当前 shell 的默认 `python` 为 3.9，不符合项目 Python 3.10+ 要求；本次明确使用 Python 3.11.13。探针输出中的随机 `/tmp` 路径是隔离工件位置，并非要复用的任务目录。

```bash
/usr/bin/python3.11 docs/archive/2026-10-16/evidence/reports/ACP_CCCP_Code_Audit_20261011/audit_inventory.py
/usr/bin/python3.11 docs/archive/2026-10-16/evidence/reports/ACP_CCCP_Code_Audit_20261011/reproduce_findings.py
ruff check src/acp src/cccp --output-format json --no-cache
ruff format --check src/acp src/cccp --no-cache
mypy src/acp src/cccp --cache-dir /tmp/acp-code-audit-mypy-20261011 --no-incremental --show-error-codes
/usr/bin/python3.11 scripts/check_grep_gates.py --suite architecture-remediation
```

已完成的定向验证：

```bash
ACP_RUN_ROOT=/tmp/acp-code-audit-unit-20261011 timeout 180s pytest -m 'not slow' \
  tests/test_architecture_invariants.py tests/test_grep_gates_script.py \
  tests/test_recovery_identity.py tests/test_recovery_cross_version.py \
  tests/test_recovery_fixtures_smoke.py tests/test_cccp_task_scan.py \
  tests/test_acp_electronic_state.py tests/test_acp_confsearch_boltzmann.py \
  tests/test_remote_phase3.py -k 'not test_legacy_api_mechanism_returns_410' \
  --disable-warnings -q
```

探针脚本输出的是缺陷现状，退出 0 表示探针执行完毕，**不是问题已经修好或所有行为正确**。正常整改后，部分探针可能不再得到当前值，应改成对应的行为验收测试。AST 结果也只是筛选依据，不能自动驱动代码删除。

本次交付只新增报告、审计脚本与证据文件，没有修改 ACP / CCCP 生产源码，没有删代码、修改运行任务、重启服务或发布变更。
