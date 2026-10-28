# 并行计划三：CCCP 科学闭包、Analysis 与独立发行

日期：2026-10-18。负责人：科学路 C。共同基线、状态、写权与依赖见[并行索引](ACP_Architecture_Progress_and_Parallel_Index_20261018.md)。可与 A/B 同时启动，不等待新页面、账户系统或托管组件。

## 1. 目标与实现边界

沿用现有 `cccp.calculation` 的 TaskRequest/Result/Context 和唯一 `tasks/`，归并仍在 acp 的科学算法与只读解析，交付 Analysis、稳定科学产品、Software 能力与可独立使用的科学发行。当前四层任务根已存在，不能再新建第二执行根或将旧 shim 当待重写任务。

Software 保留现有 backends/qc/software.py 物理驻点：backend 无 subprocess、不导 tasks，parser 只读；Analysis 不启动科学程序、不反调任务。平台账户、配额/Credit、LSF/SSH 控制、manifest 发布与恢复调度留在 Services。

默认写权为 `src/cccp/**`、登记移交的 acp 科学专属文件、科学专属测试/fixture 和本清单。混合模块、shared packaging、F4/grep/全局架构门由 B 应用本路补丁及证据，先登记再改迁位规则。

## 2. 可立即执行的清单

### C00：科学符号、资产与独立 worker 闭包清点

关联：P0-02/05、CQ-01、P4-03；无新服务前置。

- [ ] 按十五 active 工作流列出 CCCP 入口、静态/懒加载/TYPE_CHECKING/registry/字符串引用、科学 helper、平台 I/O、模型资产、科学/解析器版本和动态调用者。
- [ ] 将“当前 task 已独立”“仍需旧 acp 科学 helper”“纯平台编排”分开，为 B06 提供逐工作流阻塞闭包，优先选择 M1 必需的最小闭包。
- [ ] 固定现有科学 fixture、容差、单位/参考态、原子/帧/模式身份、完成/部分/不可用语义；保留跨版本恢复与 DP5q 隔离证据。
- [ ] 与 B 登记混合文件的符号迁移/接线写权，列公开导出、同类身份、旧路径转发、删除与回退条件。

退出门：首片科学依赖与数值基线明确，B 可在不猜科学规则的情况下开发 worker adapter；其余闭包继续清点。

### C01：优先迁移独立 worker 必需的科学闭包

关联：P4-03、CQ-06；B06 的明确前置，按工作流逐片交付。

- [ ] 从 C00 选择真实阻塞项；已有 simple 等闭包若已满足则提交隔离证据，不为迁移数量重复搬代码。
- [ ] 将科学局部过程归 Calculation、纯算法/归一化归 Analysis、软件输入/parser 归 Software；平台 plan/recovery/发布与 job/attempt 仍由 B 处理。
- [ ] 提取唯一实现，旧 acp 路径只重导出同函数/同类或兼容转换；阻断 cccp 对 acp/Services 的静态/动态反向依赖，不借子进程调用旧 CLI 绕过。
- [ ] retired 的 ensemble/energy_shared 等若被 Confsearch/NMR 内部复用，迁其活跃科学闭包并保留必要入口，不恢复退役 CLI。
- [ ] 对固定输入比较原/新结果与完成证据，在无 acp 源码的环境证明闭包；将接线补丁、依赖与 release 需求交 B。

退出门：被选工作流的科学闭包已脱离旧平台实现，单一任务根/数值/类型/异常语义保持；其余未完成工作流明确记录，不能以首片代表全覆盖。

### C02：科学产品、公开 Analysis 与适配契约

关联：P5-01、§7.3/32.2/33.3；可与 C01 并行定义首个产品。

- [ ] 复用现有 typed TaskPayload/Results，补结构/轨迹、曲线/散点、振动和派生产品的版本化契约，不另造平行 TaskRequest/取消回调。
- [ ] 明确有效参数、单位/参考系、atom_uid/frame/point/native mode、拓扑来源、软件/parser/算法版本与原始证据；缺失、不适用、解析失败、尚未准备分开。
- [ ] 原始 I/O 和类型化程序专属参数保留；不支持明确失败/unavailable，不静默删选项或改方法/基组。
- [ ] 给 B 科学→平台 frames/VIEW_REGISTRY adapter 的字段映射和正常/空/部分/损坏/旧版本 fixture；平台 manifest 登记由 Data 写，CCCP 不 import acp.results。
- [ ] 给 A 显示所需单位/选择映射/缺帧/数据预算，科学数字与样式分开；二面角等科学约定先出决策/黄金样例，改变既有行为另立数值切片。

退出门：同一科学产品可由 Python/CLI/前端消费，科学内容身份无平台 job/account/price，错误与来源明确可检查。

### C03：结构摄入、ORCA 只读解析与振动产品

关联：P5-02、P5-04 部分；依赖 C02 对应字段。

- [ ] 逐符号审计 `acp/chem`、`intake/parsers`、`io`、`results/orca_parser.py`、`frequencies.py`、结构科学 helper；上传暂存/平台路径/manifest 查询留给 B。
- [ ] 科学摄入/原子映射/只读 parser 归 Analysis/Software，保留软件版本差异、坏行/多帧、部分输出和原生模式编号，不凭位置重新编号。
- [ ] 公开无启动程序副作用的解析能力，保留旧 parser/结构公开导入的同类身份与异常；解析失败不用零/空成功遮蔽。
- [ ] 向 B 提供旧结果投影兼容 adapter 所需产品，向 A 提供固定载荷；不因 GET 需要数据而在 Data 内重新放解析器。

退出门：黄金输出字段/单位/原子/模式映射等价，parser 路径不启动程序；旧任务读取兼容由 B 接入验证。

### C04：采样、筛选、Boltzmann 与 PES/IRC/TS 算法

关联：P5-02、CQ-02/06；依赖 C00/C02 对应片。

- [ ] 逐项迁 `confsearch/sampling.py`、sampling models/selection/shared 科学部分，以及 PES atom mapping/bond changes/path/topology、IRC/TS 纯科学验证。
- [ ] profiles/平台批量计划/人工 review 身份与发布顺序留 B；科学内部复合过程经 Calculation，不在 Analysis 创建第二 DAG/调度器。
- [ ] 固定采样顺序、盆/饱和度、权重、能量参考、路径弧长/RMSD/导数、上游失败与 TS/IRC 证据行为；保留专用工作流本地限制。
- [ ] 常量精度不同的路径暂保持原值并在 C07 决策；同精度项才可按等价重构合并，不能迁移中改变筛选边界。
- [ ] 向 B 交可组合科学入口/旧路径转发和固定结果，不在 Services/JS 留第二算法。

退出门：同 fixture 数值/排序/关键点/证据按既有容差通过，旧内部引擎只保留必要兼容面。

### C05：NMR 科学与模型/无人值守报告

关联：P5-02、PR-01 资产、CQ-07；按 NMR 嵌套 AGENTS 分片。

- [ ] 拆分 DP4/DP5、FCHL/模型、谱处理、归属/缩放/平均、protocol/reference gates、结构映射和科学诊断；任务/操作/analysis revision 的平台身份与登记留 B。
- [ ] 保留 typed probability/support/OOD/unavailable，不能把缺模型/不可比/证据不足填零或改成功；DP5q/ShiftPredictor 研究面维持隔离且不替换默认算法。
- [ ] 迁模型数据/加载路径/许可证与 notice，避免 `models.py` 和资产 `models/` 同名产生错误包结构；旧公开导入保持同类身份/序列化兼容。
- [ ] 保留无人值守 JSON/XLSX/PNG 导出与依赖；不因为浏览器 Visualization 已有而删 matplotlib/openpyxl 或降低报告能力。
- [ ] 拆迁科学报告内容与平台路径/注册，提交逐片 fixture、模型缺失/可选依赖/导出和回退证据。

退出门：独立科学路径可处理支持的 NMR fixture、加载合法资产并产生等价报告；不将方法迁移测试描述为临床/普适准确性验证。

### C06：有界科学大数据产品

关联：P5-03、§7.3；依赖已选 C02/C03/C04/C05 产品，不等待全迁移。

- [ ] 选择固定真实规模的轨迹/能量或谱数据样本，测量全量读取/内存与处理基线。
- [ ] 在 Analysis 实现分块/抽样/多分辨率，保留原始点映射、顺序、科学关键点及算法/范围/参数/分辨率/schema 来源。
- [ ] 派生身份包含输入摘要与算法等全部相关维度，不只用 job_id；不可将显示抽样当全量科学结果。
- [ ] 向 B 交已生成产品/块索引，Data 只做字节/元数据查询；A 只消费有限产品，不重算聚类/降维或科学判据。

退出门：产品块有界、映射/关键点可复核，性能用同样本实测，不写未经测量的收益比例。

## 3. 科学基础、发行与扩展

### C07：C0 类型/常量、配置域与 Software 质量归并

关联：P5-04、CQ-02/05/07。

- [ ] 归并无 I/O 的科学 types/errors/units/spec，旧路径重导出同一个类；保持 isinstance、异常捕获、序列化和延迟导入。
- [ ] 相同值/精度常量统一单点定义并保留旧导出；不同精度 HARTREE/R 等记录历史值、单位、容差/阈值影响，单列科学变更，不更新黄金值遮差异。
- [ ] 科学 config/protocol 与平台远程/部署配置分离，保留 merge/优先级与旧兼容装配；built-in/defaults 同步由 B 集成，协议仍走既定 Python 配置入口。
- [ ] 软件发现复用 `cccp.software`，旧 find_executable 等先审公开返回形状/fallback_paths，再兼容或删除；不以 `software/` 新目录覆盖已有文件。
- [ ] QC 变化由 C 提供 F4 amendment/等效门及原生输入输出 fixture，B 写共享护栏；qc/cluster 平台机制审完调用者才交 B 接管，不直接删。
- [ ] 窄化科学内部异常，保留真正边界兜底的身份/日志；禁止失败变空成功或新增裸 `except Exception:`。

退出门：唯一科学事实来源和正确依赖方向，等价项有行为证据，数值政策变更独立可审查。

### C08：CCCP 独立科学发行与角色安装

关联：PR-01、P6-01、P4-06 配套；按实际支持能力分片，完整范围需对应科学闭包完成。

- [ ] 向 B 提供最小科学包/依赖/资产/许可清单与精确打包改动；单个 pyproject 同时发现 acp/cccp 不能当独立发行证据。
- [ ] 实际构建/检查 wheel，在无 acp/Services/前端/Web/账户/账单及离线所需依赖已准备的环境安装，运行公开科学 fixture/产品读取/模型与报告样例。
- [ ] 科学软件许可与可获得性独立声明，无法分发的资产给 provider/availability，不依赖私有在线算法补缺口。
- [ ] 与 B06/B07 对齐 release、worker 科学依赖、模型与 notice，新增运行时依赖同步节点清单；不新增 `python -m cccp`。
- [ ] 提供 Python API/科学示例、兼容/弃用政策、可复现支持矩阵；未验证软件/模型组合明确标记。

退出门：CCCP 有真实独立安装与科学能力证据；Services＋CCCP CLI 是否完整由 B 另验，不以源码 import 成功替代 wheel/资产验证。

### C09：可信插件样板与科学组件试点

关联：§31.1、OS-09、M1/M4；首期只交一个样板，不强制所有能力插件化。

- [ ] 在 Software engine/只读 parser 或明确 Analysis seam 选一个最小能力样板，复用内置任务入口和 TaskRequest/Result/Context。
- [ ] 冻结发现元数据/启用/加载边界、API/schema 版本、依赖/能力/资源/许可、冲突/缺失/unavailable；发现不自动安装或执行所有 entry point。
- [ ] 锁定插件/parser 版本摘要，提供 B 的 release/step identity/产品身份所需字段；内置任务层仍唯一执行驻点。
- [ ] 交插件 SDK/最小示例/契约检查，覆盖版本不符、缺依赖、重复能力、部分输出、单位/映射、取消/超时与回退。
- [ ] QCEngine/cclib/QCElemental 仅按实际候选做单程序 fixture 对照与许可/依赖评估；未通过不接管现有科学路径，不产生第二任务链。

退出门：一个可信插件可在不重写核心任务/状态机的情况下使用，固定版本结果与失败语义可验证；Hosted 安装仍由受控运维管理。

### C10：科学/架构验收、清理与证据交付

关联：P7、CQ-08、M1—M4 联合门。

- [ ] 每片复用现有 cccp task/contract/isolation、NMR、PES/IRC/采样/解析、packaging 与 recovery 测试，按影响补真实科学边界，保留黄金容差和恢复 fixtures。
- [ ] 检查静态/懒加载/TYPE_CHECKING/字符串入口依赖、backends→tasks 禁止、tasks 唯一实现与 shim 零业务逻辑；新公开实现不放 `__init__.py`。
- [ ] 对相同输入/参数/软件/算法版本比较旧/新及实际 Python/CLI/Web/MCP 科学输出；接口模拟和纯输入生成只算各自范围，不冒充真实软件执行。
- [ ] ORCA/xTB/CREST/LSF 等真实能力按配置/marker 记录 PASS/FAIL/NOT_VERIFIED；解析真实 fixture 不等于当场真实运行。
- [ ] 已迁能力撤旧实现，保留必要公开转发、历史 reader、模型/vendor/许可；受影响异常/兼容/依赖与文档台账同步。
- [ ] 向 B 交付可合入切片、源码唯一性/数值/隔离/资产证据与旧调用替换，向 A 交付稳定产品样例/映射/限制。

退出门：当前发布支持范围内科学唯一实现、数值/证据与独立安装通过；科学完成不等于浏览器/租户/收费/生产完成。

## 4. 并行依赖与节奏

C00/C01 优先消除 B06 的真实阻塞；C02 的首个产品可同时定约。C03—C05 按已冻结独立符号组交错推进，避免一次整包搬迁；C06 随选定产品加入。C07 低风险等价项穿插，C08 对已承诺支持范围验证实际发行，C09 一个样板满足 M1，其余扩展随 M4。C10 随每片执行，不拖至最终大清理。

B 在科学闭包就绪前可完成自己的 Port/协议/仓储/用例重构；A 在科学产品升级前可完成 S0 场景封装。C 不为等待 B 新包而反向 import acp，也不为等待 A 新视图而复制显示或平台发布逻辑。

本次文档拆分只核查源码与历史证据；清单测试、构建与真实科学验证均留待实施片执行，未在本轮运行。
