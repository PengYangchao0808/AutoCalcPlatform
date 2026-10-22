# acp-parallel-plan-3-cccp - Work Plan

## TL;DR (For humans)
**Who this is for and what changes for them:** 科学路（C 路）执行者与平台三方：完成后全部科学算法与只读解析唯一驻留 CCCP 科学底座，旧平台路径保留兼容转发，平台与前端通过稳定契约消费同一份科学数据，CCCP 可独立安装使用。

**What you'll get:** 一份可直接执行的 56 步迁移计划（含 2026-10-18 两轮三路评审修订）+ 4 项终验：从科学闭包清点、首个阻塞闭包迁移、版本化科学产品契约，到采样/路径/TS/NMR 科学归并、有界大数据产品、常量与配置归并、独立可安装的 CCCP 包与一个可信插件样板；每一步带写权标注、可执行验收命令与证据路径。

**Why this approach:** 先清点再迁移（闭包台账决定全部范围）；单写者纪律（混合文件只出补丁，平台侧应用）；证据三态（真实软件缺环境记"未验证"，绝不用模拟冒充满分）；不同精度常量和黄金基线本次只记录、不合并、不改动。

**What it will NOT do:** 不新建第二套执行根/调度体系；不改变科学数值政策与黄金基线；不放宽任何现有护栏；不代替平台侧（B）与前端侧（A）的接入、打包集成与验收工作。

**Effort:** XL
**Risk:** Medium-High - 跨模块大规模归并 + 跨路交接；主要风险是范围漂移、证据不足与过度声明。
**Decisions I made for you:** 覆盖全部 C00–C10（近细远专）；首个迁移目标由清点结果按既定程序自动选定（默认候选序 Confsearch→NMR→PES/batch/tsmode）；插件样板默认取一个只读解析器；跨路动作只交付前置工件；按三路评审修订：基础类型/identity 前移、nmr/enumerate 补齐、按工作流交付闭包、隔离检查扩展到迁移范围、出树预验 + 合并版本复验；R2 修订：TMS 资产统一入口先于 T25（T55）、首批构建规格/滚动打包早期子项（T56）、产品实装闭包、关键交接物产生时点入库、T14/T35/T54 依赖修正与 F 入场范围调整；R4 修订：T54=发布前候选安装复验、T13 状态二分、T51 前置收口；R5 修订：T40a/T40b 范围拆分与里程碑验收范围规则。

Your next move: 高精度双评审（momus + 独立评审）已按你的要求运行；通过后可在独立会话执行 `$ulw-execute acp-parallel-plan-3-cccp`（可选 `--worktree <绝对路径>` / `--make-pr` / `--ship`）。

> TL;DR (machine): XL；风险 Medium-High；执行 Plan_3（C00–C10）全路线：闭包台账 → 首片科学闭包迁移与产品契约 → 摄入/解析、采样/PES/TS、NMR 归并 → 有界大数据、常量/配置归并、独立发行、插件样板 → 终验收口。56 个实现 todo（两轮三路评审修订：52–54 基础/identity/合并复验；55–56 资产前移与滚动打包）+ F1–F4 终验；全程按 C 路写权（混合文件产补丁交 B）、X1–X6 共同约束与 tri-state 证据纪律执行。

## Scope

### Affected user and ideal state

**Affected user:** 路线 C 执行者/科学负责人（主用户——按本计划零访谈执行迁移）；路线 B（Services——消费闭包清单/handoff/打包改动）；路线 A（Frontend——消费产品样例/映射/显示限制）；平台研究者（最终用户——数值/单位/身份不变、CCCP 可独立使用）；未来 CCCP 使用者与插件作者（可安装科学包 + SDK）。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | 路线 C 获得决策完备、可直接执行的计划：每片含写权标注、精确符号/路径、可执行验收与证据路径 | 执行者无访谈上下文；C00–C10 跨多会话，依赖/回退必须可追踪 |
| IS-2 | 路线 B 能"不猜科学规则"开发 worker adapter：逐工作流闭包清单 + handoff 包 + 补丁集齐备 | Plan_3 C01 退出门原文；B06 切换前置 |
| IS-3 | 被选工作流科学闭包脱离旧 acp 实现：唯一实现、数值/类型/异常语义保持、cccp 无反向依赖 | 四层架构唯一驻点；B06 独立 worker 前置（P4-03） |
| IS-4 | 同一科学产品可由 Python/CLI/前端消费：版本化契约、有效参数/单位/atom_uid/frame/point/原生模式/拓扑来源、typed unavailable | C02 退出门；A04/B frames adapter 前置 |
| IS-5 | 结构摄入/ORCA 只读解析/振动产品行为冻结：黄金字段/单位/原子/模式映射等价，解析不启动程序 | C03 退出门；旧任务兼容由 B 接入验证 |
| IS-6 | 采样/PES/IRC/TS 数值/排序/关键点/证据按既有容差通过；retired 引擎仅保留必要兼容面 | C04 退出门；筛选边界不得迁移中改变 |
| IS-7 | NMR 科学路径独立可用：fixture 处理、合法资产加载、等价 JSON/XLSX/PNG 报告；DP5q/ShiftPredictor 隔离不被破坏 | C05 退出门；无人值守导出与隔离是硬约束 |
| IS-8 | 大数据产品有界且可复核：原始点映射/顺序/关键点保留，派生身份含输入摘要+算法+范围+分辨率+schema | C06 退出门；不得把显示抽样当全量科学结果 |
| IS-9 | 唯一科学事实来源：常量/类型/errors/units/spec/config/software 单点定义；不同精度项独立决策 | C07 退出门；ANTI #9 |
| IS-10 | CCCP 有真实独立安装证据：无 acp 环境安装 wheel 并运行科学 fixture/产品读取/模型与报告样例；许可/可获得性声明 | C08 退出门（PR-01）；"源码 import 成功"不算证据 |
| IS-11 | 一个可信插件可在不重写核心任务/状态机情况下使用；固定版本结果与失败语义可验证 | C09 退出门；M1 仅需一个样板 |
| IS-12 | 全程证据纪律：模块验证/接入/真实功能分列；真实软件按 PASS/FAIL/NOT_VERIFIED；护栏不放宽、黄金值不覆盖 | C10 + 索引 §7；防重复"42.7"式过度声明教训 |

| GAP | Difference from IS | Reason (why it matters) | Closed by |
| --- | --- | --- | --- |
| GAP-1 | Confsearch 4 协议仍委托 retired acp 引擎；NMR 分析完全驻留 acp；PES/batch/tsmode 科学在 acp | B06 无法在无旧 acp 实现环境执行被选闭环 | C00/C01 + C04/C05 |
| GAP-2 | 无闭包台账/三分类/数值基线冻结点 | 迁移范围不可判定，B 无法开工 | C00 |
| GAP-3 | C02 未启动：无版本化产品契约、无 B adapter 字段映射、无 A 显示需求清单 | 平台与前端无法消费新科学产品 | C02 |
| GAP-4 | 摄入/解析/振动产品驻留 acp（chem/intake/io/orca_parser/frequencies） | 科学摄入与只读解析未归 Analysis/Software | C03 |
| GAP-5 | 采样/PES/tsmode 科学在 acp；IRC 已 shim 完成；retired 引擎活跃科学闭包未迁 | 数值/排序/证据无独立驻点 | C04 |
| GAP-6 | NMR 25 模块 ~19k 行 + ~62.5MiB 模型在 acp.nmr；models.py vs models/ 命名风险 | NMR 不可独立、资产随包验证缺失 | C05 |
| GAP-7 | 无分块/抽样/多分辨率产品与派生身份 | 大数据不可有界消费 | C06 |
| GAP-8 | HARTREE/R 常量多处重复（含一处截断精度）；cluster 平台配置与科学配置同文件；find_executable 遗留 | 唯一事实来源/依赖方向缺失 | C07 |
| GAP-9 | 单 pyproject 同时发现 acp/cccp；无独立 wheel/资产/离线证据 | PR-01 不可验证 | C08 |
| GAP-10 | 无插件发现/启用/加载边界与 SDK/示例 | M1 插件样板缺失 | C09 |
| GAP-11 | 历史 PASS 不代表当前 revision；C 路证据体系未建立 | 过度声明风险（索引 §3 已记录此类事故） | C10 + 每片 |
| GAP-12 | 对 B/A 的交接物（闭包清单、adapter 映射、打包改动、release 字段）未产出 | 跨路依赖不可开工 | C00/C01/C02/C08/C09 |

### Must have

- C00：15 工作流闭包台账（静态/懒加载/TYPE_CHECKING/registry 字符串/动态调用者）；三分类（已独立/仍需旧 helper/纯平台编排）；数值/单位/参考态/原子/帧/模式身份与完成语义冻结；写权/交接登记；handoff 包格式；基线 revision 与依赖版本记录。
- C01：首片科学闭包迁移（唯一实现 → cccp；旧 acp 路径同函数/同类转发；阻断反向依赖；无 acp 源码环境证明）；retired 引擎活跃闭包处置（不恢复退役 CLI）。
- C02：版本化科学产品契约（复用 typed TaskPayload/Results）——结构/轨迹、曲线/散点、振动、派生产品；有效参数/单位/参考系/atom_uid/frame/point/native mode/拓扑来源/版本与证据；typed unavailable 与 failed/unprepared 分离；B 的 frames/VIEW_REGISTRY adapter 字段映射 + fixture 集；A 的显示需求与二面角决策。
- C03：`acp/chem`、`acp/intake`（parsers/models）、`acp/io` 科学部分、`results/orca_parser.py`、`frequencies.py` 归并；parser 无启动副作用；旧公开导入同类身份与异常保持。
- C04：`confsearch/{sampling,sampling_models,selection,shared/*}` 科学部分；PES `{atom_mapping,bond_changes,path_analysis,path_selection,candidates,validation,atom_selection}`；tsmode `{mode_mapping,source,validation}`；重复实现合（equilibration 等）；固定排序/权重/弧长/RMSD/导数行为。
- C05：NMR 25 模块科学归并；平台耦合点解耦（catalog/identity/Structure）；模型资产 + 许可/notice 迁移；DP5q 隔离保持；无人值守 JSON/XLSX/PNG 导出保持。
- C06：分块/抽样/多分辨率有界产品；原始点映射/顺序/关键点保留；派生身份（输入摘要+算法+参数+范围+分辨率+schema）；性能实测。
- C07：无 I/O 科学 types/errors/units/spec 归并（旧路径同类转发）；同值/同精度常量单点化；不同精度常量决策记录；科学 config 与平台配置分离；software 归并；qc/cluster 审计；科学异常窄化。
- C08：最小科学包/依赖/资产/许可清单 + 精确打包改动；wheel 构建 + 无 acp 环境安装 + 科学样例运行；许可/可获得性声明；Python API/示例/兼容政策/支持矩阵。
- C09：一个最小能力样板（只读 parser）；发现元数据/启用/加载边界；API/schema 版本/依赖/能力/许可/冲突/unavailable；版本摘要供 B；SDK/示例/契约测试；QCEngine/cclib/QCElemental 对照评估。
- C10：每片复用现有测试入口 + 真实科学边界；静态/懒加载/字符串入口检查；旧/新输出对比；tri-state 记录；撤旧 + 公开转发保留；B/A 交付。
- HANDOFF：B/A 交接包集合（逐工作流闭包清单、补丁、字段映射与 fixture、打包变更请求、release 字段、显示限制）。
- 三路评审修订（C1–C9/X1–X6）：基础类型与 identity 前移；nmr/enumerate 与公开导出面覆盖；资产/loader/打包同批原子切换；按工作流交付闭包；隔离检查扩展到迁移范围；出树预验 + 合并版本复验；共同执行约束与 tracked 交接。
- 第二轮评审修订（R2 P0-2/P0-3/P1-9/P1-10/P1-5）：TMS 资产统一入口先于首次消费（T55）；首批构建规格/滚动打包子项（T56）；产品生成/B adapter/A 消费实装闭包；关键交接物产生时点入库（T13/T39/T42）；T14/T35/T54 依赖修正与 F 入场范围调整；T10a 最小交付与 T24 按工作流 readiness。
- 第四轮复核修订（R4 §3.1/§3.3）：T54 定为候选版本发布前安装复验（发布前终验消费其结果；生产发布与发布后核对归 B-T76/F5）；T13 交付状态二分（状态 A 契约可消费 / 状态 B 跨路端到端）与 B28a/B28b 接收分工；T51 在 T54 后汇总且不提前关闭跨路未决项；Wave 4b 概览同步 T56。
- 第五轮复核修订（R5）：T40 拆 T40a（本次范围安装预验，不等待完整 T39）/T40b（完整发行预验）；T54 等待对应范围 40a/40b；T51/F1–F4 按本次 IN_SCOPE 任务 + 必要依赖 + T54 做里程碑验收（全路线关闭才检查全部；DEFERRED 不绕过必需上游）。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- 不做第二执行根/DAG/调度器；Analysis 不启动科学程序、不反调任务；不新增 `python -m cccp`。
- 不引入 cccp→acp 任何形式依赖（静态/懒加载/TYPE_CHECKING/字符串/subprocess 旧 CLI 绕路）。
- 不直接改 B 共享文件（`pyproject.toml`、`requirements-node.txt`、根 README/AGENTS、主方案、全局架构门）——只产精确变更请求；不整文件搬迁混合文件。
- 不放宽/绕过护栏（4 个 grep pin、F4 scope、架构不变量、scheduler markers、恢复 fixture）；不删审计材料。
- 不合并不同精度常量、不更新黄金值/容差遮差异、不改变筛选边界作为迁移副作用。
- 不复活 retired CLI/写交互；不重建退役工作流；不为退役 id 增加 registry 条目。
- 不替换默认算法；不激活 DP5q/ShiftPredictor；不删除无人值守报告依赖（matplotlib/openpyxl）。
- 不写未经测量的性能收益；不把显示抽样当全量科学结果；不把 mock/接口测试当真实软件验证。
- 不创建新里程碑/命名/编号体系；不改 15 active 工作流集合、edit coverage、frames/manifest 契约（平台侧行为）。
- 不在本计划内实施 B06 切换、frames adapter、打包集成、AGENTS 更新（仅交付其前置工件）。

## Verification strategy

> Zero human intervention - all verification is agent-executed.

- Test decision: tests-after（迁移片：既有断言先冻结，迁移后全绿 + 新增隔离/等价断言；新能力片：最小真实边界测试）。框架：pytest（仓库既有入口），不新增测试框架。
- 每 todo 验收命令模板：
  - 定向：`pytest <切片相关测试文件> -q`
  - 护栏（每波收口必跑）：`pytest tests/test_architecture_invariants.py tests/test_cccp_isolation.py tests/test_f4_scope_audit.py tests/test_grep_gates_script.py tests/test_scheduler_markers_audit.py -q`
  - 隔离（每迁移片必跑）：`python scripts/verify_cccp_isolation.py`
  - 全量（波次收口）：`pytest -m "not slow" -q` + `python -m compileall src/cccp src/acp`（按影响范围）
- 真实软件：ORCA/xTB/CREST/CENSO/ISOSTAT/Shermo/LSF 按既有 marker（`requires_*`、`--run-slow --run-integration`）；未预置环境一律记 NOT_VERIFIED，禁止记 PASS。
- 证据：`.omo/evidence/acp-parallel-plan-3-cccp/task-<N>-acp-parallel-plan-3-cccp.<ext>`（ulw-loop 执行时使用当前 attemptDir；每条附命令原文与原始输出）。
- 负向对照：迁移片至少 1 个注入式负例（如临时注入 `import acp` 到 cccp 模块 → `tests/test_cccp_isolation.py` 必须 FAIL → 回滚），证明护栏有效。
- 写权验证：混合文件改动只以补丁形式产出；补丁用 `git apply --check` + 隔离 worktree 应用 + 定向测试（记录后撤销），不以改工作区代替。
- 分支/隔离：每片独立 worktree/分支；不共享可写任务库/任务目录（主方案 §37.5）。
- 每片合入门（评审 C1/C4）：每个可合入切片在无旧 acp 环境可导入并调用本片新公开 API；旧入口/异常类型/序列化/身份输出等价；禁止"迁一半再允许反向 import"；不以最终全迁完作为恢复可用条件。
- 隔离能力（评审 C8）：静态依赖/动态入口扫描 + 公开 API 导入 + 至少一个成功科学 fixture 路径 + 错误路径 + 旧/新对象与输出对照 + 无 ACP 安装检查；workflow→task/helper 映射逐条登记；禁止以计数相等作为门。
- 扫描语义（评审 C8）：只认真实 import/importlib/registry 动态目标/CLI 绕路；文档与历史 provenance 中的 `acp.*` 字符串不算反向依赖（例：`src/acp/calculations/pes/atom_mapping.py:588` 的 `generated_by=acp.calculations.pes.atom_mapping` 是输出内容，修改需单独兼容评估，不为 grep 清零而改）。
- 注入式负例只在临时副本/隔离 worktree 运行并在失败后清理；不污染其他路线正在消费的工作树；全局护栏修改仍由 B 按登记同步（等效或更强）。
- 证据可交付（评审 X5）：契约、写权登记、样例、交接状态、验收摘要必须受版本控制（位置与 B-T3 联合裁定），不以"证据路径存在"作为唯一验收；大日志留稳定工件位置并登记 URI/摘要/产生提交/环境/命令/退出码；补丁记录 provider 基线与 consumer 应用提交。
- 环境隔离（评审 X6）：每路固定自己的 worktree、venv、run_root、配置、端口与临时输出；集成另用一套环境并记录模块实际来源；真实部署集中在通过终验的集成 release 上执行。
- 范围状态（评审 X4）：条件项使用 `IN_SCOPE / DEFERRED / NOT_APPLICABLE` 独立记录；证据仍只用 `PASS / FAIL / NOT_VERIFIED`；延期不是通过。

## Execution strategy

### Parallel execution waves

> 目标每波 5–9 个 todo。C10 为贯穿程序（每个切片 QA 中执行），Wave 5 仅做最终收口。

- **Wave 0（C00 基准，todo 1–6）**：1 先行；2/4 并行；3/5/6 依赖 2；为 Wave 1 提供台账/登记/冻结。
- **Wave 1（首片与契约，todo 7–15）**：三车道并行——C01（7→8→9→10；T10a 最小交付在 3/5/48 就绪后先行，T10b 随 8/9 滚动）、C02（11→12→13→14，可与 8/9 并行）、C07-s1（15 独立）；T48 的隔离检查能力先于 T8 建立（见 todo 48）。
- **Wave 2a（C03 + 基础/资产/打包前移，todo 16–19、52、53、55、56）**：56（首批构建规格）先出；52/53（基础类型与 identity 前移）先于消费方（17/28/29）；55（资产统一入口与同批切换，含 B40a 就绪/回执两态）先于 25；16 先行，17/18/19 基于 16 目标包位置。
- **Wave 2b（C04，todo 20–24）**：20→21→22；23 与 20 并行；24 依赖 6（处置决定）。
- **Wave 2c（C05，todo 25–30）**：25→26（含资产/loader/打包同批）→27→28（依赖 53）串行批处理；29/30 依赖 28。
- **Wave 3（C06+C07，todo 31–38）**：31→32→33；34–38 与 C06 并行（35 依赖 5 登记）。
- **Wave 4a（C08，todo 39–43、54）**：39 依赖 30/33/38 的资产与清单；**40a（本次范围安装预验）不等待 39**、40b（完整发行预验）←39；41 依赖 40；42 依赖 30/39；43 可并行；54（候选版本发布前安装复验）等待对应范围 40a/40b 与 B 应用回执（跨路）后执行。
- **Wave 4b（C09，todo 44–47）**：44 依赖 18/56（首批构建规格，不等完整发行）；45/46 递进；47 可与 45/46 并行。
- **Wave 5（C10 收口，todo 48–51）**：48 自 Wave 1 前置建立后随片扩展；49/50 可并行；51 最后。

### 共同执行约束（三路共享，评审 §6.3）

1. 实施以共同基线和唯一写权登记为准；所有跨路输入均记录契约版本、提供提交、样例摘要和应用回执。
2. 一个可合入切片必须同时具备唯一实现、旧入口兼容、消费者调整、依赖/资产/护栏同步；未完成的跨路补丁不能算该切片已集成。
3. 临时端口注入可以支持迁移，但不证明独立运行；独立性用无旧实现环境和实际安装产物验证。
4. 新查询不创建 prepare；旧接口的过渡行为明确登记；切换前必须验证冻结旧客户端。
5. 任务状态、事件、工件发布、预算预留的各个持久化边界都有崩溃与重放验收。
6. `PASS / FAIL / NOT_VERIFIED` 不互相替代；范围延期单独记录；实际合并版本重新验证，不能拼接不同提交的通过记录。
7. 三路各用独立环境和数据；真实部署集中在通过终验的集成 release 上执行。
8. 每个里程碑声明实际支持范围；未进入范围的能力保持开放，不阻塞已满足要求的独立交付，也不计作完成。

### Dependency matrix

> 权威依赖只维护 `Depends on` 一列（逐行自洽、无环，且与各 todo 头 `Blocked by` 一致）；Blocks/波次由它推导；"（参考）"为非阻塞关系。已按评审 §6.2 核对未知任务 ID、环、提前消费、同文件同时写、提供者有交付但无接收任务。

| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 2–6 | — |
| 2 | 1 | 3,5,6,7 | 4 |
| 3 | 2 | 7,24 | 4,5 |
| 4 | 1 | 7,8,9,11,15,16–33（数值门） | 2,3,5 |
| 5 | 2 | 7–10,16–47（登记门） | 4 |
| 6 | 2 | 24 | 3,5 |
| 7 | 2,3,4,5 | 8,9 | 11–15 |
| 8 | 4,5,7,48 | 9 | 11–15 |
| 9 | 4,5,7,8 | 10（参考） | 11–15 |
| 10 | 3,5,48 | B06 切换（跨路） | 7–9,11–15 |
| 11 | 4,5 | 12,13,14、32（参考） | 8,9 |
| 12 | 11 | 13,14 | 8,9 |
| 13 | 11,12（完整交付） | B28b、A04（跨路） | 14 |
| 14 | 11,12 | A04（跨路） | 13 |
| 15 | 4,5 | 34 | 8–14 |
| 16 | 4,5 | 17,18,19 | 20,25 |
| 17 | 16,52 | — | 18,19 |
| 18 | 16 | 44 | 17,19 |
| 19 | 16,18 | 33 | 17 |
| 20 | 4,5 | 21,22 | 23,25 |
| 21 | 20 | 22 | 23,25 |
| 22 | 20,21 | 33（参考） | 23 |
| 23 | 4,5 | 25（参考） | 20–22 |
| 24 | 3,6,20,21 | 仅相关工作流 readiness（非任务级） | 22,23 |
| 25 | 4,5,55 | 26 | 16–23 |
| 26 | 25,55 | 27 | — |
| 27 | 26 | 28 | — |
| 28 | 27,53,55 | 29,30 | — |
| 29 | 28 | 30 | — |
| 30 | 26,28,29 | 39,42 | — |
| 31 | 4,19 | 32 | 34–38 |
| 32 | 31 | 33 | 34–38 |
| 33 | 32 | 39 | 34–38 |
| 34 | 15 | 35（参考） | 31–33 |
| 35 | 5,52 | 36（参考） | 31–34 |
| 36 | 34、B-T36–T39（同批，跨路） | — | 35,37 |
| 37 | 5 | — | 36,38 |
| 38 | 4 | — | 36,37 |
| 39 | 30,33,38 | 40b、42 | — |
| 40 | 40a←56+本次范围所选闭包；40b←39 | 41、54（按子项） | 43 |
| 41 | 40 | — | 43 |
| 42 | 30,39 | B07（跨路） | 41,43 |
| 43 | 39 | — | 40–42 |
| 44 | 18,56 | 45,46 | 47 |
| 45 | 44 | 46 | 47 |
| 46 | 45 | B06/B07（跨路） | 47 |
| 47 | 44 | — | 45,46 |
| 48 | 1,4,5 | 8（能力门）、16–30（随片扩展） | 49,50 |
| 49 | 8–30 各片 | — | 48,50 |
| 50 | 8–47 | — | 48,49 |
| 51 | IN_SCOPE 任务+必要依赖+T54（里程碑）；全路线=全部 | — | — |
| 52 | 2,5 | 17,35、28/29（参考） | 16,20,25 |
| 53 | 2,5 | 28、（跨路）B-T27 | 16,52 |
| 54 | 40a/40b（按声明范围）、B 应用回执（跨路）、候选源码/构建输入冻结（跨路） | B release、发布前终验（F 波消费其证据） | — |
| 55 | 4,5,56 | 25,26,28,30 | 16–19,52,53 |
| 56 | 2,5 | 55,44 | 52,53 |

## Todos

> 每个 todo = 一个原子切片（实现 + 测试同一 todo）。写权标注：`C直写` = `src/cccp/**` + 已登记移交文件 + 科学测试/fixture；`B补丁` = 产补丁交 B 应用（并入 handoff 包）；`B请求` = 精确变更请求交 B。证据默认 `.omo/evidence/acp-parallel-plan-3-cccp/task-<N>-acp-parallel-plan-3-cccp.<ext>`（ulw 执行时用 attemptDir）。

- [x] 1. C00-s1 基准、证据骨架与切片模板
  What to do: 记录基线 revision `f410ac9dae035eb424c40c599692500fdea457cc`、工作区 dirty/untracked 归属清单、Python/依赖/科学软件/解析器版本与配置来源；创建 `.omo/evidence/acp-parallel-plan-3-cccp/` 及 `handoffs/`；写入切片模板（§12.1 八步 + §17 台账字段 + 写权三态 + tri-state 证据栏）与 handoff 包模板（索引 §5 十要素）；写明本计划从属声明（沿用主方案 v1.12 与索引编号，不新建里程碑）。
  Must NOT do: 不修改任何产品代码；不清理用户未提交工作；不把未测指标写成结论。
  Closes: GAP-2（部分）、GAP-11（部分）
  Parallelization: Wave 0 | Blocked by: — | Blocks: 2–6
  References: `docs/reference/next-change/ACP_Architecture_Progress_and_Parallel_Index_20261018.md` §1/§5/§7；`docs/ACP_Architecture_Adjustment_Plan_20261016.md` §12.1/§17；`AGENTS.md`（COMMANDS、SYSTEMD SERVICE）。
  Acceptance (agent-executable): `.omo/evidence/acp-parallel-plan-3-cccp/c00-baseline.md` 与 `templates/slice-template.md`、`templates/handoff-package.md` 存在；baseline 含 revision、dirty 清单（或"clean"）、版本表（至少 python/deps/orca/xtb/crest/censo/shermo）、命令原文。
  QA: happy = 生成三件套并抽查 3 个字段有据可查（命令或来源）；failure = 工作区 dirty 时记录归属不清理，证据头部标 `dirty_worktree` 风险（示例：若 `git status` 非空）。
  Commit: N | none（证据仅在 .omo）
  Recommended task executor category: quick

- [x] 2. C00-s2 15 工作流闭包台账
  What to do: 按 15 个 active 工作流逐一产出：CCCP 入口（cccp.calculation 任务/契约/backend）、静态/懒加载/TYPE_CHECKING/registry 字符串/动态调用者引用清单、科学 helper、平台 I/O、模型资产、科学/解析器版本；用统一配方逐工作流核验（`grep -rn "acp\." src/cccp`、`grep -rn "from acp\|import acp" src/cccp`、各 workflow 模块 import 图、`tests/test_cccp_isolation.py` 探针）；产出 `.omo/evidence/acp-parallel-plan-3-cccp/c00-closure-ledger.md`（每工作流一节 + 引用 file:line）。同步生成**符号依赖图**（运行时/延迟导入/TYPE_CHECKING/动态注册/资产边），标出科学基础类型（Structure 族）与 identity fingerprint 纯算法部分的唯一目标候选（评审 C1）。
  Must NOT do: 不迁移任何代码；不把猜测当结论（推断须标 `inferred`）；不遗漏 retired 引擎被 active 路径复用项。
  Closes: GAP-2、GAP-1（证据面）
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 3,5,6,7 | Can parallelize with: 4
  References: `src/acp/cli.py:4129-4149`（分发表）；`src/acp/workflows/*`；`src/cccp/calculation/tasks/`；`src/acp/confsearch/protocols/__init__.py:31-35`；`src/acp/workflows/nmr.py:131,712,715,786,869`；`tests/test_cccp_isolation.py:504`；`scripts/verify_cccp_isolation.py`。
  Acceptance: ledger 覆盖全部 15 工作流；每工作流含入口/引用/helper/平台 I/O/资产/版本六栏；对 `cccp→acp` 引用给出零命中证据或逐条 file:line；脚本断言：15 个 `## <workflow>` 小节数 = 15；依赖图覆盖已证消费边（`src/acp/io/structures.py:20`、`src/acp/results/frequencies.py:8`、`src/acp/confsearch/selection.py:12`、`result_helpers.py:10`、NMR protocol/reference_validation）并给出唯一目标裁定。
  QA: happy = 逐工作流核验命令全部回填；failure = 对任一工作流漏列动态引用时，用注入式反例（在 cccp 模块临时注入 acp 引用）验证 grep 配方可捕获并记录。
  Commit: N | none（本地台账；如团队需要入库由 B 决定）
  Recommended task executor category: deep-low

- [x] 3. C00-s3 三分类与首片选择程序（B06 输入）
  What to do: 把 15 工作流分为"当前 task 已独立 / 仍需旧 acp 科学 helper / 纯平台编排"三类（以 todo 2 台账为据）；产出 M1 最小闭包候选与确定性选择程序：① simple 系（singlepoint/optimize/frequency/scan/irc/casscf/xtb_optimize/XtbPathSearch/OrcaGradient/XtbConstrainedOpt）先提交隔离证据（已有 cccp 任务核则直接跑 `python scripts/verify_cccp_isolation.py` + 定向测试）；② 首片迁移目标 = 真实阻塞且依赖最少者，默认候选序 Confsearch（优先 xtb-crest 等无 ORCA 依赖组件）→ NMR 分析层 → PES/batch/tsmode 残片；③ 同权时记录裁决理由。产出 `c00-closure-classification.md` 与 `c00-first-slice-decision.md`。
  Must NOT do: 不硬编码未经台账支持的结论；不把 not-ready 工作流误标 ready；不以首片代表全覆盖。
  Closes: GAP-1、GAP-2、GAP-12（部分）
  Parallelization: Wave 0 | Blocked by: 2 | Blocks: 7,24 | Can parallelize with: 4,5
  References: `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C00/C01 节；`docs/reference/next-change/ACP_Parallel_Plan_2_Services_20261018.md:88-98`（B06）；todo 2 台账。
  Acceptance: 分类表 15 行齐全且每行有 file:line 证据；首片决定含候选序、选择结果、否决理由；simple 系逐项给"已独立/阻塞"判定与验证命令。
  QA: happy = 按程序复跑一遍得到同一首片结论（幂等）；failure = 若台账出现两个同权候选 → 记录 tie-breaker 并明确记录，不静默任选。
  Commit: N | none
  Recommended task executor category: deep-high

- [x] 4. C00-s4 数值/身份/完成语义冻结
  What to do: 固定科学 fixture/容差/单位/参考态/原子-帧-模式身份与完成/部分/不可用语义；登记现有黄金源（`tests/baseline/cccp_calculation_goldens/`、`tests/baseline/nmr/fchl_golden*.json`、`tests/baseline/recovery_fixtures/`、`tests/fixtures/**`）；对未 pin 的黄金值补登记清单（NMR 内联常数、IRC TS 能量 -151.532087、hess water/TS 频率、dihedral 回归值、PES propylene E2E、`path_analysis.py:20` 的 627.509）；产出 `c00-numeric-baseline.md`（含每项来源 file:line、容差、禁止变更标记）。
  Must NOT do: 不更新/覆盖任何黄金值或容差；不合并不同精度常量；不把未 pin 值当已冻结。
  Closes: GAP-8（登记面）、GAP-11（部分）
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 7,8,9,11,15,16–33（数值门） | Can parallelize with: 2,3,5
  References: `tests/baseline/cccp_calculation_goldens/manifest.json`（source commit f1c49a14）；`tests/baseline/nmr/fchl_golden_tolerances.json`；`tests/baseline/recovery_fixtures/README.md`；`src/acp/calculations/pes/path_analysis.py:20`；`src/acp/workflows/nmr.py:1088,1952`；`tests/test_acp_irc_trajectory.py`、`tests/test_hess_file_real_fixtures.py`、`tests/test_cccp_geometry_tools.py`、`tests/test_pes_e2e_propylene.py`。
  Acceptance: numeric-baseline 表逐项含 `path:line`、当前值/容差、来源测试；未 pin 清单 ≥5 项；每项标注"迁移期禁止变更"。
  QA: happy = 抽取 3 个黄金值在测试源码中可定位且与登记一致；failure = 发现未登记的新常数时补登记（示例 grep 配方：`grep -rn "627\.\|0\.001987" src/acp src/cccp`），记录补登项。
  Commit: N | none
  Recommended task executor category: deep-low

- [x] 5. C00-s5 写权与交接登记（handoff registry）
  What to do: 与 B 登记每文件/每符号的写权：`C直写`（`src/cccp/**`、登记移交的 acp 科学专属文件、科学测试/fixture）、`B补丁`（混合文件：workflows/catalog/executor/results 等）、`B请求`（pyproject/requirements-node/AGENTS/全局门）；列出公开导出、同类身份、旧路径转发、删除与回退条件；定稿补丁程序（`git apply --check` → 隔离 worktree 应用 → 定向测试 → 记录 → 撤销）；产出与 B-T3 联合的**受版本控制**登记表（tracked 位置与 B-T3 裁定；HN-01…编号约定），每条含 `owner / 可修改范围 / provider / consumer / base_commit / prerequisites / acceptance / applied_commit`（评审 X1）；补丁生命周期：生成=已交付，接收分支应用并通过测试=已集成，且记录 provider 基线与 consumer 应用提交（评审 X5）。
  Must NOT do: 不自行假定混合文件直改；不跳过登记先改文件；不整文件搬迁交接。
  Closes: GAP-12、GAP-2（部分）
  Parallelization: Wave 0 | Blocked by: 2 | Blocks: 7–10,16–47（登记门） | Can parallelize with: 4
  References: 索引 §5 单写者表；主方案 §12.1/§37.2；`docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` 开头写权段。
  Acceptance: registry 覆盖 todo 2 台账中出现的全部 acp 侧文件（逐文件/逐符号）；每行含写权三态之一 + 依据 + 删除/回退条件 + provider/consumer；含登记版本号与 tracked 路径；补丁状态可区分已交付/已集成；补丁程序含具体命令序列。
  QA: happy = 任取 3 个待迁移文件均可在 registry 找到写权裁决；failure = 出现台账有而 registry 无的文件 → 补登记并记录（不允许未登记即迁移）。
  Commit: Y | `docs(plan3): add versioned write-registry and handoff registry (tracked)`
  Recommended task executor category: deep-low

- [x] 6. C00-s6 retired 引擎复用处幂
  What to do: 对 `ensemble.py`、`energy.py`、`energy_shared.py`、`ensemble_thermo.py`、`xtbmd_md.py`、`xtbmd_censo_energy.py` 按消费方（Confsearch 4 协议、nmr.py）逐符号判定：活跃科学闭包迁移目标 / 保留必要入口 / 死代码候选；核定与 C01/C04 的分工（哪些在本轮迁、哪些留待后续）；产出 `c00-retired-disposition.md`。
  Must NOT do: 不恢复退役 CLI；不删除仍有调用者的入口；不把"retired 文件"整文件当死代码。
  Closes: GAP-5（决策面）
  Parallelization: Wave 0 | Blocked by: 2 | Blocks: 24 | Can parallelize with: 3,5
  References: `src/acp/confsearch/protocols/{xtb_crest.py:26,censo_crest.py:39,65,xtb_md.py:34-39,xtbmd_censo.py:31}`；`src/acp/workflows/nmr.py:131,712,715,786,869`；`src/acp/workflows/energy_shared.py`；`src/acp/workflows/xtbmd_censo_energy.py:242`（重复 equilibration）。
  Acceptance: 六文件逐符号表（symbol → 消费方 file:line → 处置三类 → 目标位置或保留理由）；处置为"迁移"的符号列表可直接被 todo 24 消费。
  QA: happy = 抽取 2 个符号可在符号表与消费方源码双向对上；failure = 若发现未列消费方（新 grep 命中）→ 补录并重判（示例配方：`grep -rn "energy_shared\|ensemble_thermo\|run_conformer_energy" src/acp`）。
  Commit: N | none
  Recommended task executor category: deep-low

- [x] 7. C01-s1 首片选择与切片规格
  What to do: 消费 todo 3 的选择程序产出首片规格：目标工作流、待迁符号清单（file:line）、目标 cccp 位置（按"科学局部过程→cccp.calculation；纯算法/归一化→cccp.analysis；软件输入/parser→Software 域"规则）、旧路径转发形式、依赖阻断点、测试与证据清单；产出 `c01-first-slice-spec.md`；如按程序判定 simple 系已独立，则本 todo 提交其同等隔离证据并把 T8/T9 对本次首批范围标为 `NOT_APPLICABLE`（范围状态，不删除整路后续科学迁移）；8/9 按 todo 3 ② 候选序（Confsearch→NMR→PES）取得首片规格；首片符号唯一登记（每个符号只对应一个实施任务，评审 C4）。
  Must NOT do: 不选择台账未列符号；不改变任何现有行为；不把多工作流打包成一片；不把 T8/T9 的"不适用"读作删除后续迁移。
  Closes: GAP-1（首片）、GAP-2
  Parallelization: Wave 1 | Blocked by: 2,3,4,5 | Blocks: 8,9 | Can parallelize with: 11–15
  References: `c00-closure-ledger.md`、`c00-first-slice-decision.md`、`c00-write-registry.md`；主方案 §6/§12.1；`src/cccp/calculation/` 现有结构。
  Acceptance: 规格含符号清单（≥1 且逐条 file:line）、目标位置、转发形式、验收命令；简单系分支的隔离证据含命令与输出；T8/T9 的 `NOT_APPLICABLE` 记录显式（若适用）。
  QA: happy = 规格中每个符号可在源文件定位；failure = 若首片符号牵涉未登记文件 → 停下补登记（不迁移），记录。
  Commit: N | none
  Recommended task executor category: deep-high

- [~] 8. C01-s2 首片迁移实施（唯一实现 + 旧路径转发） — NOT_APPLICABLE（本次首批范围；T7 verdict B：xtb-crest 闭包符号唯一归属 T20/T21/T24，simple 系 8/10 已独立，证据 c01-first-slice-spec.md §7 + t7-isolation-evidence.txt；非"完成"，全路线后续科学迁移保留）
  What to do: 按规格执行：提取唯一实现到 cccp 目标位置（逐符号，含类型/异常语义保持）；在旧 acp 路径（C直写文件）改为 re-export 同函数/同类或兼容转换；阻断 cccp 对 acp/Services 的静态/懒加载/TYPE_CHECKING/字符串反向依赖（禁止 subprocess 旧 CLI 绕路）；对固定输入比较原/新结果（fixture/黄金值，按既有容差）；补/跑定向测试。
  Must NOT do: 不复制两份实现；不改数值政策/容差；不整文件搬迁；不在混合文件直改（用 todo 5 的补丁程序）。
  Closes: GAP-1、GAP-3（部分）
  Parallelization: Wave 1 | Blocked by: 4,5,7,48 | Blocks: 9,10 | Can parallelize with: 11–15
  References: 首片规格；`tests/test_cccp_isolation.py`；`scripts/verify_cccp_isolation.py`；`tests/test_architecture_invariants.py:471/538/559`；相关黄金值清单（todo 4）。
  Acceptance: `python scripts/verify_cccp_isolation.py` 通过；`pytest <该切片定向测试> -q` 全绿；`pytest tests/test_cccp_isolation.py tests/test_architecture_invariants.py -q` 全绿；旧路径导入同对象（`python -c "import acp.X as m; import cccp.Y as n; assert m.f is n.f"` 类断言按符号执行）；负例：注入 `import acp` 到新 cccp 模块 → 隔离测试 FAIL → 回滚；使用 T48 扩展后的隔离能力；本片在无旧 acp 环境可导入并调用新公开 API 且旧入口/异常/序列化/身份等价（评审 C1/C8）。
  QA: happy = 上述命令全绿 + 原/新输出按容差一致证据；failure = 数值偏离 → 停止并回退，记录差异清单（不得改黄金值）。
  Commit: Y | `refactor(cccp): relocate <slice> into <target> with acp re-export shims`
  Recommended task executor category: deep-high

- [~] 9. C01-s3 次片迁移实施（第二组符号） — NOT_APPLICABLE（本次首批范围；同 T7 verdict B：次片默认 NMR 分析层符号唯一归属 T25–T30；全路线保留）
  What to do: 按 todo 3 程序选择第二组真实阻塞符号（默认：若首片为 Confsearch 协议闭包 → 次片为 NMR 分析层首组；反之亦然），执行与 todo 8 相同的迁移配方与验证（含 simple 系之外的第一组 NMR 或 PES 证据）；每个符号只登记一个实施任务；后续任务消费首片提交并处理剩余符号，不重复迁移（评审 C4）。
  Must NOT do: 同 todo 8 的 Must NOT；不与首片同一文件并行改（避免冲突）。
  Closes: GAP-1、GAP-5（部分）
  Parallelization: Wave 1 | Blocked by: 4,5,7,8 | Blocks: 10 | Can parallelize with: 11–15
  References: 同 todo 8 + `src/acp/nmr/*` 或 Confsearch 协议模块（按选择）。
  Acceptance: 同 todo 8 的命令集（对次片定向）；次片规格文档先行（一段式，含符号清单）。
  QA: 同 todo 8。
  Commit: Y | `refactor(cccp): relocate <second-slice> with acp re-export shims`
  Recommended task executor category: deep-high

- [x] 10. C01-s4 B06 handoff 合成（T10a 最小交付 + T10b 滚动汇总）
  What to do: **T10a 最小交付**：对已判独立工作流（含 simple 系）在分类（T3）、登记（T5）、隔离证据能力（T48）齐全后**立即交付**，不等 T8/T9；**T10b 滚动汇总**：随 T8/T9/T24 等输出增量更新（参考关系）。交付内容（索引 §5 十要素）：按工作流分别交付 + 四状态表（task 核已独立 / 科学 helper 已独立 / 平台编排待 B / 安装发行已验证，评审 C4）；逐工作流 ready/blocked、补丁或登记记录（已交付/已集成，含 provider 基线与 consumer 应用提交）、隔离证据、release/依赖需求；**每行标明该闭包对应的旧实现注入点与 worker 独立装配需求**（供 B-T72 移除注入、B-T31 独立装配，评审 R2-P0-4）；产出 tracked `handoffs/HN-01-b06-closure.md`（位置与 B-T3 裁定）+ 补丁文件；标注 blocked-by-B 与未决记录方式。
  Must NOT do: 不代替 B 应用补丁；不声称 B 侧已集成；不把 blocked 项标为完成；不把单一工作流的 blocked 扩展为整路 blocked；不以 T8/T9 未完成阻塞已独立工作流的最小交付（T10a）；不把跨路"移除注入/worker 装配"标记为 C 侧完成。
  Closes: GAP-12、GAP-3（部分）
  Parallelization: Wave 1 | Blocked by: 3,5,48 | Blocks: B06 切换（跨路） | Can parallelize with: 8,9,11–15
  References: 索引 §5；`ACP_Parallel_Plan_2_Services_20261018.md:88-98`；`c00-write-registry.md`；todo 5 补丁程序；评审 C4/X1/R2-P0-4。
  Acceptance: T10a 在 3/5/48 就绪后可独立完成（逐工作流交付记录）；handoff 含全部十要素字段 + 四状态表 + 旧实现注入点/worker 装配需求列；补丁 `git apply --check` 通过或在隔离 worktree 应用后定向测试通过（记录后撤销）；"已集成"只在 B 应用并通过测试后记录；blocked-by-B 显式。
  QA: happy = handoff 字段完整性检查 + 补丁试应用证据；failure = 补丁不 apply → 修正补丁直至 apply 干净，记录修订。
  Commit: Y | `docs(plan3): b06 per-workflow closure handoff (tracked)`
  Recommended task executor category: unspecified-low

- [x] 11. C02-s1 产品契约骨架 + 首产品（结构/轨迹）
  What to do: 复用现有 typed TaskPayload/Results（不另造平行 TaskRequest/取消回调），定义版本化产品契约骨架：schema 版本、产品身份、series/point 稳定 ID、x/y/单位、参考系、缺失原因、来源映射；首产品 = 结构/轨迹（structure/frame ID、atom_uid、元素、坐标单位、拓扑来源、帧顺序/块索引）；typed "unavailable/failed/not-prepared" 与正常数据分离；产出契约文档 + schema 模块（cccp 侧）+ fixtures（normal/empty/partial/corrupt/old）。每类产品须定义：真正生成它的函数、生成时机、稳定身份、原始输入摘要、schema reader/writer、旧版本转换、部分失败与缺失原因（评审 C5）。
  Must NOT do: 不把平台 job/account/price 放进科学身份；不静默删选项/改方法；不缺字段时用零值伪装。
  Closes: GAP-3、GAP-4（部分）
  Parallelization: Wave 1 | Blocked by: 4,5 | Blocks: 12,13,14,32 | Can parallelize with: 8,9
  References: 主方案 §7.3；`docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C02；`src/cccp/calculation/results.py`（现有 payload 类型）；`src/acp/results/frames.py`（VIEW_REGISTRY 现行契约，兼容目标）；`docs/reference/current/ACP_Energy_Trajectory_Viewer_DevDoc.md`（帧契约）。
  Acceptance: schema 模块含版本常量与序列化；≥4 个 fixture 文件覆盖 normal/empty/partial/corrupt(+old)；校验脚本/测试：happy round-trip + empty/partial 不伪造 + corrupt 明确失败（`pytest tests/<新契约测试> -q`）；契约描述含生成函数/时机/旧版转换路径。
  QA: happy = round-trip 与字段断言全绿；failure = 构造缺 atom_uid 的 partial fixture → 必须显式报 unavailable/缺失原因而非补零。
  Commit: Y | `feat(cccp): add versioned science product contracts (structure/trajectory)`
  Recommended task executor category: deep-high

- [x] 12. C02-s2 曲线/散点 + 振动 + 派生产品契约
  What to do: 扩展契约：曲线/散点（x/y/单位/参考系/缺失原因/来源映射）、振动（原生模式编号、频率单位、原子顺序/身份、位移向量与归一化约定）、派生产品（来源映射/版本）；保留原始 I/O 与类型化程序专属参数；不支持明确失败/unavailable；产出 fixtures 与测试；每类产品同 T11 的生成函数/生成时机/稳定身份/输入摘要/reader-writer/旧版转换/失败原因清单（评审 C5）。
  Must NOT do: 不丢软件专属选项；不重编号原生模式；不把显示样式放进科学 JSON。
  Closes: GAP-3
  Parallelization: Wave 1 | Blocked by: 11 | Blocks: 13,14 | Can parallelize with: 8,9
  References: 主方案 §7.3 表；`src/acp/results/orca_parser.py`（频率/模式解析现状）；`tests/test_acp_frequency_modes.py`；`src/cccp/calculation/frequency_parse.py`。
  Acceptance: 三类产品各有 schema + ≥2 fixtures（normal/partial）；测试覆盖单位/模式编号/缺失原因断言；定向 pytest 全绿。
  QA: happy = 模式编号与源文件一致（对照 `tests/fixtures/structure_viewer/orca_freq_modes_full.txt`）；failure = 输入缺频率段 → typed unavailable（非空成功）。
  Commit: Y | `feat(cccp): extend product contracts for curves/vibrations/derived products`
  Recommended task executor category: deep-high

- [x] 13. C02-s3 B adapter 字段映射与产品实装闭包（frames/VIEW_REGISTRY）
  What to do: 产出 B 写 adapter 所需的字段映射表（科学产品 → `TrajectoryFrame.to_node()/TrajectoryAnnotation.to_annotation()` 兼容层）与正常/空/部分/损坏/旧版本 fixture 集；**为本次声明支持的每类产品建立一行实装闭包**：真实生成入口 → C 产品 writer/reader → B Data 登记/读取 → B frames/annotation adapter → A 消费与选择映射 → 验收任务（评审 P1-9）；handoff 中**指明 B 侧接收任务为 B-T28a/B-T28b 拆分**（B28a 既有发布/markers 入口收敛；B28b 科学产品→frames/annotation 适配、旧结果 reader/转换与发布/读取接线，消费本契约/fixture 与对应生产入口；评审 R4 §3.3）与消费契约测试（同一 fixture 组，评审 X2）；明确 CCCP 不 import `acp.results`、平台 manifest 登记不由 CCCP 写；完整曲线/振动字段依赖 T12 收口（若提前交付，明确只交结构子集并分版本补充，评审 C5）。**交付状态二分（评审 R4 §3.3）：状态 A「契约可消费」= 映射表/fixture/实装闭包表就绪即判本 todo 完成，B 可立即开始 B28b，不得等待状态 B；状态 B「跨路端到端验收完成」= 同一 fixture 贯穿 C 生成→B 发布/投影→A 消费的联合状态，待 B/A 接入后成立、单独记录，不作为本 todo 完成条件。**
  Must NOT do: 不写平台 frames adapter 实现（B 的写权）；不改 `VIEW_REGISTRY`；不绕过 to_node()/to_annotation() 契约；旧结果用兼容 reader/转换，不复制科学算法；新结果不得只有示例 JSON 而无生产调用链；不把状态 B 当作状态 A 的前置（避免互相等待）。
  Closes: GAP-3、GAP-12
  Parallelization: Wave 1 | Blocked by: 11,12（完整交付） | Blocks: B28b、A04（跨路） | Can parallelize with: 14
  References: `src/acp/results/frames.py`（VIEW_REGISTRY + to_node/to_annotation）；主方案 §7.3 末段；ANTI #21；A 路清单 A04；评审 P1-9、R4 §3.3。
  Acceptance: 状态 A（本 todo 完成条件）：映射表逐字段（源字段→目标字段→单位/身份规则）+ 实装闭包表逐产品（含 B28a/B28b 分工与 A 条件任务引用、C 侧生产入口归属任务 T18/T19/T20/T22 等）+ fixture 五态齐全（每个附期望映射输出）+ cccp 侧零 `acp.results` import；状态 B（跨路联合，单独记录，不阻碍开工）：同一固定输入经 C 生产入口→B 发布/投影→A/Python 消费逐项一致（单位/atom_uid/frame/point/native mode/缺失原因/块索引）；仅 schema 测试不能关闭状态 B；C 侧生产入口接线证据随对应任务（T19/T20/T22 等）提交。
  QA: happy = 用映射表核对 1 个真实帧样例 + 实装闭包表字段完整性；failure = 对同一 fixture 新旧投影不一致 → 记录差异并修订映射（不放松 to_node 契约）。
  Commit: Y | `docs(plan3): frames-adapter mapping and product realization closure (tracked at production time)`
  Recommended task executor category: deep-low

- [x] 14. C02-s4 A 显示需求与二面角决策
  What to do: 产出 A 所需：单位/选择映射/缺帧/数据预算（有界载荷）；科学数字与样式分离；二面角等科学约定先出决策与黄金样例（登记既有差异，统一另立数值切片）；改变既有行为须单列数值切片，不在本 todo 执行；结构子集可随 T11 先行，曲线/振动显示字段的完整交付以 T12 契约为前置（评审 P1-5：T12 纳入完整交付硬依赖）。
  Must NOT do: 不在 JS/前端数值重算；不统一二面角差异当去重；不承诺未定显示能力。
  Closes: GAP-3、GAP-12
  Parallelization: Wave 1 | Blocked by: 11,12 | Blocks: A04（跨路） | Can parallelize with: 13
  References: `docs/reference/next-change/ACP_Frontend_FE10_Dihedral_Verification_20261016.md`；A 路清单 A04；主方案 §4.3；评审 P1-5。
  Acceptance: 文档含单位表/选择映射/缺帧语义/数据预算与二面角决策记录；黄金样例 ≥1（含既有差异标注）。
  QA: happy = 用 C02-s1 fixture 派生一份显示载荷并核对单位/身份；failure = 发现需要科学约定变更 → 只登记为独立切片，不在本 todo 改行为。
  Commit: Y | `docs(plan3): display constraints and dihedral decision (tracked)`
  Recommended task executor category: writing

- [x] 15. C07-s1 同值常量单点化（低风险）
  What to do: 按 write-registry 处置：`HARTREE_TO_KCAL` 同值项（`acp/core/models.py:19`、`acp/results/energy_graph.py:24`、`acp/confsearch/shared/boltzmann.py:8`——随 C04 迁移自然收敛）；`R` 同精度项判定；以 `src/cccp/utils/constants.py` 为唯一定义并保留旧导出名（re-export 同值对象）；不改动不同精度项（`627.509`、R 变体、`cccp/core/candidates.py:148,180`、`nmr.py:1088,1952`）——仅登记待 todo 34 决策。
  Must NOT do: 不合并 627.509；不改任何数值；不重命名公开导出；不更新黄金值。
  Closes: GAP-8（部分）
  Parallelization: Wave 1 | Blocked by: 4,5 | Blocks: 34 | Can parallelize with: 8–14
  References: `src/cccp/utils/constants.py:6,13`；todo 4 数值基线；ANTI #9；`src/acp/core/models.py:19`、`src/acp/results/energy_graph.py:24`、`src/acp/confsearch/shared/boltzmann.py:8,10`。
  Acceptance: `python -c "from cccp.utils.constants import HARTREE_TO_KCAL as a; from acp.core.models import HARTREE_TO_KCAL as b; assert a==b"` 类断言按处置执行；未处置项在 `c07-precision-ledger.md` 清单中（627.509 等 ≥4 项）；定向测试全绿。
  QA: happy = 同值断言 + 全量 `pytest -m "not slow"` 中相关子集全绿；failure = 发现隐藏同值副本 → 补登记/收编（示例：`grep -rn "627.509" src/acp src/cccp` 只剩截断项与其他两处历史值）。
  Commit: Y | `refactor(constants): single-source same-precision constants with legacy re-exports`
  Recommended task executor category: unspecified-low

- [x] 16. C03-s1 结构摄入科学归并（chem/intake → cccp/analysis/structure）
  What to do: 逐符号迁移 `acp/chem/{embedding,composition}`、`acp/intake/{models,parsers}` 科学部分到 `src/cccp/analysis/structure/`（新包，按 cccp `__init__` 规范不写实现进 `__init__.py`）；保留 6 格式解析语义（xyz/sdf/mol/gjf/orca-inp/smiles）、坏行/多帧行为；旧路径在登记文件内改为同函数/同类转发；`intake/storage.py`（平台）不动；发布面 `__all__` 全量。
  Must NOT do: 不迁移 `intake/storage.py`/平台路径逻辑；不用零/空成功遮蔽解析失败；不改变公开函数签名。
  Closes: GAP-4
  Parallelization: Wave 2a | Blocked by: 4,5 | Blocks: 17,18,19 | Can parallelize with: 20,25
  References: `src/acp/chem/embedding.py`（517L）、`src/acp/chem/composition.py`（cccp re-export shim）、`src/acp/intake/parsers.py`（856L）、`src/acp/intake/models.py`；`tests/test_acp_intake*.py`、`tests/test_acp_chem_composition.py`；`cccp/io/input_handler.py`（避免双实现，注意与 `parse_smiles` 去重记录）。
  Acceptance: `python scripts/verify_cccp_isolation.py` 通过；`pytest tests/test_acp_intake.py tests/test_acp_intake_detect_format.py tests/test_acp_intake_qc_output.py tests/test_acp_chem_composition.py tests/test_cccp_isolation.py -q` 全绿；旧导入同对象断言（≥3 个符号）；`grep -rn "from acp" src/cccp/analysis` 零命中。
  QA: happy = 上述命令全绿 + 解析输出与迁移前逐字节/字段一致证据；failure = 坏行输入行为不一致 → 回退该符号并记录（不得放宽断言）。
  Commit: Y | `refactor(cccp): move structure intake science into cccp.analysis.structure`
  Recommended task executor category: deep-high

- [x] 17. C03-s2 io/structures 科学部分与依赖方向修正
  What to do: 审计 `src/acp/io/structures.py`：`StructureReader/Writer` 薄转发保持（或改为 cccp 直连）、`capture_nmr_topology` 等科学部分迁 `cccp/analysis/structure/`；处理 `acp.core.models.Structure` 耦合（Structure 族由 todo 52 前移后本片消费，不再延后到 todo 35）；旧公开导入同类身份与异常保持；产出符号审计表。
  Must NOT do: 不重复迁移 Structure 族（todo 52 的范围）；不改变读/写格式行为；不新增第二套 IO。
  Closes: GAP-4（部分）
  Parallelization: Wave 2a | Blocked by: 16,52 | Blocks: — | Can parallelize with: 18,19
  References: `src/acp/io/structures.py:20,275,440,220`；`tests/test_acp_io.py`；`cccp/io/input_handler.py`。
  Acceptance: 审计表逐符号（保留转发/迁移/待 52/35 裁决）；`pytest tests/test_acp_io.py tests/test_cccp_isolation.py -q` 全绿；`capture_nmr_topology` 类行为对照（迁移前后同输出）；消费 todo 52 前移的 Structure 族（零反向依赖）。
  QA: happy = 读写 round-trip 与拓扑捕获前后一致；failure = 若发现新的 acp.core 耦合点 → 登记入 35 清单并保持本片不变更。
  Commit: Y | `refactor(cccp): relocate NMR topology capture and IO science symbols`
  Recommended task executor category: deep-low

- [x] 18. C03-s3 ORCA 只读解析归并
  What to do: 将 `src/acp/results/orca_parser.py` 的只读解析（`OrcaCalculation`/`OrcaOutputParser`、能量/SCF/轨道/RMS 梯度等 + 频率字段委托）迁入 cccp（建议 `src/cccp/analysis/orca_parser.py`，或按 Software 域放 `cccp/qc/` 就近——以 todo 5 registry 裁决为准）；删除本地 fallback mirror（cccp 为唯一实现）；保持"解析不启动程序"；`frequencies.py` 依赖随迁/改指；旧路径转发。
  Must NOT do: 不启动任何程序；不改变模式编号/字段语义；不保留双实现（fallback mirror 必须去除并留测试证据）。
  Closes: GAP-4
  Parallelization: Wave 2a | Blocked by: 16 | Blocks: 44 | Can parallelize with: 17,19
  References: `src/acp/results/orca_parser.py:50`（委托 `cccp.calculation.frequency_parse`）；`tests/test_acp_frequency_modes.py::TestLocalFallbackMirror`；`tests/test_qc_interfaces_orca.py`；`tests/fixtures/orca_optfreq_real_sections.txt`。
  Acceptance: 迁移后 `pytest tests/test_acp_frequency_modes.py tests/test_qc_interfaces_orca.py tests/test_cccp_isolation.py -q` 全绿；fallback mirror 已删（grep 证明唯一实现）；解析输出与迁移前一致（固定 fixture 对比）。
  QA: happy = 真实 ORCA fixture 段解析一致 + 零 acp import；failure = 删除 fallback 后模拟 cccp-missing 场景 → 明确 ImportError/typed 失败（不得静默降级）。真实 ORCA 未预置环境记 NOT_VERIFIED。
  Commit: Y | `refactor(cccp): single-source ORCA read-only result parsing`
  Recommended task executor category: deep-high

- [x] 19. C03-s4 振动产品归并
  What to do: 将 `src/acp/results/frequencies.py`（`build_normal_modes_product`/`build_frequency_report`）迁至 cccp 分析/产品域（按 registry 裁决）；字段/单位/原生模式编号/坏模式跳过语义保持不变；旧路径转发；为 todo 33 的 B/A 产品交付准备样例。（与 T18 为硬依赖：OrcaCalculation 类型先行，评审 C1。）
  Must NOT do: 不改模式身份/编号规则；不把显示装配混入科学 JSON；不放松坏模式跳过断言。
  Closes: GAP-4（部分）
  Parallelization: Wave 2a | Blocked by: 16,18 | Blocks: 33 | Can parallelize with: 17
  References: `src/acp/results/frequencies.py:13,114`；`tests/test_acp_frequency_modes.py`；`tests/fixtures/structure_viewer/orca_freq_modes_full.txt`。
  Acceptance: 产品 JSON 与迁移前逐字段一致（固定 fixture）；定向 pytest 全绿；旧导入同对象；产品文件由真实解析路径生成（生产调用链证据，评审 P1-9；非示例 JSON）。
  QA: happy = golden fixture 对比一致；failure = 坏模式输入 → 跳过且记录原因（不空成功）。
  Commit: Y | `refactor(cccp): relocate vibration product builders into cccp`
  Recommended task executor category: deep-low

- [x] 20. C04-s1 采样核心迁移（sampling/deduplication）
  What to do: 迁移 `acp/confsearch/sampling.py`、`sampling_models.py` 科学部分（`parse_traj_frames`/`equilibration_cutoff`/`assign_basins`/`mds_2d`/`_compute_saturation`/`compute_sampling_history` + 数据模型/序列化）与 `shared/deduplication.py`（`plain_rmsd`/`dedup_by_rmsd`）到 `src/cccp/analysis/sampling/`；路径绑定函数（写/读 `RESULT/confsearch/sampling_history.json` 的 `write/load_sampling_history`）留在 acp 或做薄包装（按 registry）；`sampling_history_v1` schema 逐字节保持；confsearch `contracts.py` 中被 selection/result_helpers 依赖的科学结果类型（`ConformerEntry`/`ProtocolOutcome`/`REFINEMENT_POLICIES`）先行归属裁定（同批唯一目标，评审 C1）；消费方（`api/v1_routes.py:3780,4448`、`results/sampling_graph.py:17`、`results/energy_graph.py:72`、`results/frame_candidate_geometry.py:126`、`results/structure_viewer.py:1789`）改为旧路径转发（B补丁，若混合）。
  Must NOT do: 不改变采样顺序/盆/饱和度数值语义；不合并 `clustering.py::_aligned_rmsd`（Kabsch，语义不同）；不改 schema。
  Closes: GAP-5
  Parallelization: Wave 2b | Blocked by: 4,5 | Blocks: 21,22 | Can parallelize with: 23,25
  References: `src/acp/confsearch/sampling.py`（509L）、`sampling_models.py`（353L）、`shared/deduplication.py`（65L）；`tests/test_acp_confsearch_sampling.py`；`cccp/calculation/tasks/clustering.py`（对照，勿合并）。
  Acceptance: `pytest tests/test_acp_confsearch_sampling.py tests/test_acp_sampling_graph.py tests/test_cccp_isolation.py -q` 全绿；`sampling_history_v1` 序列化 diff 为空（固定输入）；旧导入同对象；契约类型归属裁定记录。
  QA: happy = schema diff 空 + 数值逐项一致（既有容差）；failure = 序列化键序/字段漂移 → 修正至 byte 级一致，记录。
  Commit: Y | `refactor(cccp): relocate confsearch sampling science into cccp.analysis.sampling`
  Recommended task executor category: deep-high

- [x] 21. C04-s2 Boltzmann/选择迁移
  What to do: 迁移 `shared/boltzmann.py`（`boltzmann_weights`/`relative_energies_kcal`，常量原值）、`selection.py`（`select_for_refinement`/`threshold_for_policy`）、`result_helpers.py` 科学部分（`sorted_records`/`build_entries`/权重表逻辑）；统一 `shared/helpers.py::select_cumulative_boltzmann` 与 `selection.py` 的重复选择逻辑（保留行为差异判定记录）；`profiles/平台批量/人工 review` 留 B；常量按 todo 4/15/34 政策处理；本片先消费 T20 的契约归属裁定（selection/result_helpers 的 contracts 依赖，评审 C1）。
  Must NOT do: 不合并不同精度 R/常量；不改 cumulative 阈值语义（0.99/0.90 差异保留）；不迁平台 profiles。
  Closes: GAP-5、GAP-8（部分）
  Parallelization: Wave 2b | Blocked by: 20 | Blocks: 22 | Can parallelize with: 23,25
  References: `src/acp/confsearch/shared/boltzmann.py:8,10`、`selection.py:17,54`、`result_helpers.py:70,122,196`、`shared/helpers.py`（`select_cumulative_boltzmann`）；`tests/test_acp_confsearch_boltzmann.py`、`test_acp_confsearch_provenance.py`（627.509474/0.8442 黄金值）。
  Acceptance: 定向 pytest 全绿（含 provenance 黄金值）；排序/权重对固定输入逐项一致；重复逻辑合并处有行为对照证据。
  QA: happy = 黄金值断言全绿；failure = 阈值边界样例（0.99/0.90 各自）验证行为保持。
  Commit: Y | `refactor(cccp): relocate Boltzmann/selection science into cccp.analysis`
  Recommended task executor category: deep-high

- [x] 22. C04-s3 PES 科学迁移
  What to do: 迁移 `acp/calculations/pes/{atom_mapping,bond_changes,path_analysis,path_selection,candidates,validation,atom_selection}` 到 `src/cccp/analysis/pes/`（这些模块已依赖 `cccp.utils`，迁移主要是位置+导入调整）；`path_analysis.py:20` 的 627.509 截断值原样保留（登记，勿"修正"）；`scan.py` 规划/编排部分留 B（科学候选/推荐函数按 registry 裁决逐符号）；解析器配置/常量不新造。
  Must NOT do: 不改路径弧长/RMSD/导数/候选排序语义；不合并 627.509；不建第二 DAG。
  Closes: GAP-5
  Parallelization: Wave 2b | Blocked by: 20,21 | Blocks: 33 | Can parallelize with: 23
  References: `src/acp/calculations/pes/*`（atom_mapping 1275L、path_selection 1277L、path_analysis 651L 等）；`tests/test_pes_search.py`、`test_pes_atom_selection.py`、`test_pes_constraint_gate.py`、`test_acp_pes_review.py`；`results/pes_profile.py:200`、`results/structure_viewer.py:1855`（消费方）。
  Acceptance: 定向 PES pytest 全绿；路径/候选输出对固定 fixture 一致；627.509 留存证据（grep）；路径档案/候选由真实扫描/路径数据生成（生产调用链证据，评审 P1-9）。
  QA: happy = 等价对比通过；failure = 关键点排序漂移 → 回退并记录（不得改黄金值）。真实 ORCA PES 记 NOT_VERIFIED（未预置环境）。
  Commit: Y | `refactor(cccp): relocate PES path/candidate science into cccp.analysis.pes`
  Recommended task executor category: deep-high

- [x] 23. C04-s4 tsmode 科学迁移
  What to do: 迁移 `tsmode/{mode_mapping.py,source.py 科学符号,validation.py}` + `contracts.py` 的 identity helpers（`geometry_hash`/`compute_target_mode_id`/`optimized_structure_digest`/`validate_coordinate_array`/`validate_mode_completeness`/`sha256_file`）到 cccp 分析域；`load_bundle_from_files` 等含 I/O 边界的符号按 registry 裁决（纯解析入 cccp，路径策略留 acp）；`engine.py` 编排留 B；保持三 ID 模式映射与 launch gate 语义。
  Must NOT do: 不迁移 `engine.py` 编排；不改模式映射/门控阈值；不改变 bundle 校验语义。
  Closes: GAP-5（部分）
  Parallelization: Wave 2b | Blocked by: 4,5 | Blocks: 25（参考） | Can parallelize with: 20–22
  References: `src/acp/calculations/tsmode/{mode_mapping.py:71,98,242,source.py:589,validation.py:24,49,85,contracts.py:733}`；`tests/test_acp_tsmode_mapping.py`、`test_acp_tsmode_source.py`、`test_acp_tsmode_orca_inputs.py`、`test_acp_tsmode_engine.py`；`tests/fixtures/qc/orca61/ts_opt.hess`。
  Acceptance: 定向 tsmode pytest 全绿；映射/门控输出对 fixture 一致；coupled acp 依赖点清单化（engine 保留项）。
  QA: happy = 映射与 gate 行为对照通过；failure = 版本不匹配样例 → typed 失败（不静默替换模式）。
  Commit: Y | `refactor(cccp): relocate TS mode mapping science into cccp.analysis.tsmode`
  Recommended task executor category: deep-high

- [x] 24. C04-s5 retired 引擎闭包实施
  What to do: 按 todo 6 处置表执行（前置：T20/T21 采样/Boltzmann 先行，评审 C4）：把仍被 Confsearch 协议/NMR 复用的活跃科学闭包（Boltzmann/selection handoff、ensemble 组装、thermo 聚合等）从 `ensemble.py`/`energy.py`/`energy_shared.py`/`ensemble_thermo.py`/`xtbmd_*` 迁入 cccp（目标：calculation 科学局部过程或 analysis）；保留必要入口供旧路径转发；合并 `xtbmd_censo_energy.py:242` 的 equilibration 重复实现；产出逐符号完成清单（仅相关工作流的 readiness 被本片阻塞，其他已独立工作流不等待）。
  Must NOT do: 不恢复退役 CLI；不删仍有调用者入口；不整文件搬迁；不改变 CENSO/ISOSTAT 科学语义。
  Closes: GAP-1、GAP-5
  Parallelization: Wave 2b | Blocked by: 3,6,20,21 | Blocks: 仅相关工作流 readiness（非任务级） | Can parallelize with: 22,23
  References: todo 6 处置表；`src/acp/confsearch/protocols/*`；`src/acp/workflows/nmr.py:786,869`；`src/acp/workflows/energy_shared.py`（1576L）；`tests/test_acp_workflows_ensemble.py`、`test_acp_workflows_energy.py`、`test_acp_workflows_xtbmd_censo_energy.py`、`test_acp_censo_p5_acceptance.py`。
  Acceptance: 处置表中"迁移"符号全部落位（逐条证据）；定向 workflow pytest 全绿；旧路径转发同对象；equilibration 单一实现（grep 证明）。
  QA: happy = 上述断言全绿；failure = 一个符号在两个调用方行为不一致 → 停止并记录差异，不改语义强行合并。真实 CENSO/ISOSTAT 记 NOT_VERIFIED（缺环境时）。
  Commit: Y | `refactor(cccp): migrate retired-engine active science closures`
  Recommended task executor category: deep-high

- [x] 25. C05-s1 NMR 核心批 1（models/structure_map/io/equivalence/averaging/enumerate）
  What to do: 先建立 `acp.nmr` 公开符号清单（`__init__.__all__:42`、动态 registry、TYPE_CHECKING、子模块导入）并逐项映射（迁移/保留/拆分）到各批次；迁移 `acp/nmr/{models,structure_map,io,equivalence,averaging,enumerate}.py` 纯科学到 `src/cccp/analysis/nmr/`（新包）；`models.py` 的 `NmrReport`/`NmrConfig`/TMS 查表随迁（报告写手另见 29；TMS 读取经 T55 统一入口，见 `_load_tms_table:36,45`）；本片消费 T55 的资产统一入口（不依赖模块旁 `models/`）；`models.py` 对 `atomic_diagnostics` 的 TYPE_CHECKING 引用目标改写；保持 `REPORT_SCHEMA_VERSION=2` 与全部字段/序列化；`enumerate.py` 保留 stereocenters/seed/max_isomers/对映体去重/无拓扑输入拒绝语义（评审 C2）；新 `__init__` 只能导出、不得混入实现或意外激活可选算法；旧路径转发（登记文件直写；nmr 为登记移交的唯一候选）。
  Must NOT do: 不改字段名/默认值/序列化；不迁平台耦合（method_config/analysis_revision 另办）；不拆 `models/` 资产（T55 统一处理）；不漏枚举语义；不以"25 模块"计数代替覆盖检查。
  Closes: GAP-6
  Parallelization: Wave 2c | Blocked by: 4,5,55 | Blocks: 26 | Can parallelize with: 16–23
  References: `src/acp/nmr/{models.py:2197L,structure_map.py,io.py,equivalence.py,averaging.py,enumerate.py}`；`src/acp/nmr/__init__.py:42`；`src/acp/workflows/nmr.py:72,406`；`tests/test_acp_nmr_structure_map.py`、`test_acp_nmr_io.py`、`test_acp_nmr_equivalence.py`、`test_acp_nmr_averaging.py`、`test_acp_nmr_signal_group.py`、`test_acp_nmr_topology.py`、`test_acp_nmr_enumerate.py`。
  Acceptance: 定向 NMR pytest 全绿（含 `test_acp_nmr_enumerate.py`，枚举可在无 ACP 安装中运行）；建模类/解析函数旧导入同对象（含 `NmrReport` 序列化 byte 级一致）；`grep -rn "from acp" src/cccp/analysis/nmr` 零命中；公开符号清单逐项有处置；冷启动进程（清缓存）取得相同 TMS 数值（不得被迁移前缓存掩盖，评审 P0-2）。
  QA: happy = 全绿 + 序列化 diff 空；failure = 旧导入类身份断言失败（isinstance）→ 修正 re-export 至同对象，记录。
  Commit: Y | `refactor(cccp): relocate NMR core science batch 1 into cccp.analysis.nmr`
  Recommended task executor category: deep-high

- [x] 26. C05-s2 NMR 批 2（scaling/assignment/iterative_assignment/probability/error_model/fchl）
  What to do: 迁移 `scaling.py`、`assignment.py`、`iterative_assignment.py`、`probability.py`、`error_model.py`、`fchl.py`；资产与全部 reader 路径切换已由 T55 同批完成（本片不再搬资产，仅验证 `error_model.py:41`/`fchl.py:81` 经统一入口解析）；保持 typed probability/OOD/unavailable 语义（缺模型≠0、不可比不成功）；`iterative_assignment` 的 `PeakConstraint` 消费方（proton_processor）随 27 处理；旧路径转发。
  Must NOT do: 不把 unavailable 填零/改成功；不改 DP5 权重/误差模型数值；不激活 ShiftPredictor/DP5q；不重建第二资产位置或第二定位入口。
  Closes: GAP-6
  Parallelization: Wave 2c | Blocked by: 25,55 | Blocks: 27 | Can parallelize with: —
  References: `src/acp/nmr/{scaling.py,assignment.py,iterative_assignment.py:1343L,probability.py,error_model.py:958L,fchl.py:1526L}`；资产 `src/acp/nmr/models/`；`tests/test_acp_nmr_probability.py`、`test_acp_nmr_probability_state.py`、`test_acp_nmr_scaling.py`、`test_acp_nmr_assignment.py`、`test_acp_nmr_iterative_assignment.py`、`test_acp_nmr_fchl*.py`、`test_acp_nmr_evidence_gate.py`。
  Acceptance: 定向 pytest 全绿（含 188.452125/32.1243166667 等内联黄金值）；typed 状态断言（probability=None 场景）不回归；资产 8/8 摘要一致；**源码环境与 wheel 安装各验证**资产清单/DP5/FCHL 可用性/模型加载/数值输出（wheel 侧证据随 B40a 集成回执，不得早于）；模型齐全环境继续得原结果（模型缺失 typed unavailable 仅作负例）；FCHL 已知不可用组合按原基线保持（评审 C3/P0-2）。
  QA: happy = 全绿 + 缺模型/不可比样例 typed 语义保持；failure = 资源缺失场景 → typed unavailable（不零值）。
  Commit: Y | `refactor(cccp): relocate NMR probability/error-model/FCHL batch 2`
  Recommended task executor category: deep-high

- [x] 27. C05-s3 NMR 批 3（spectra/carbon_processor/proton_processor/spectra_registry）
  What to do: 迁移 `spectra.py`、`carbon_processor.py`、`proton_processor.py`、`spectra_registry.py`（含 registry `importlib` 动态加载路径改写）；保持四层谱模型、处理质量门、2D `ser` 拒绝、多实验选择语义；Bruker fixture 消费不变；旧路径转发。
  Must NOT do: 不放松质量门/坏峰处理；不改 processor 注册语义；不删除 nmrglue 可选依赖降级路径。
  Closes: GAP-6
  Parallelization: Wave 2c | Blocked by: 26 | Blocks: 28 | Can parallelize with: —
  References: `src/acp/nmr/{spectra.py:1324L,carbon_processor.py:1768L,proton_processor.py:1499L,spectra_registry.py}`；`tests/test_acp_nmr_spectra*.py`、`test_acp_nmr_carbon_processor.py`、`test_acp_nmr_proton_processor.py`；`tests/fixtures/nmr/**`、`tests/fixtures/bruker_real_group_delay/`。
  Acceptance: 定向 pytest 全绿；fixture 处理输出一致；动态加载在 cccp 包内解析（探针子进程验证）。
  QA: happy = fixture 质量评估结果一致；failure = 缺 nmrglue 环境 → typed 降级（记录，不得崩）。
  Commit: Y | `refactor(cccp): relocate NMR spectra processing batch 3`
  Recommended task executor category: deep-high

- [x] 28. C05-s4 NMR 批 4（protocol/reference_validation/atomic_diagnostics + identity 解耦）
  What to do: 迁移 `protocol.py`、`reference_validation.py`、`atomic_diagnostics.py`；解除平台耦合：`identity_fingerprint`（现 `acp.calculations.identity`）消费 **T53 下沉的同一实现**（同算法、同输出；不复制第二套算法，评审 C1）并按需保留 acp 侧 re-export；`reference_validation.py` 路径语义已由 T55 统一（`asset_hashes:673`/`NOTICE` 引用经统一入口；本片迁移并验证）；保持六段指纹/参考门（typed，不称校准）/原子诊断语义。
  Must NOT do: 不改指纹算法/门控结论；不把参考门升级为校准声明；不引入 acp 依赖；不复制 fingerprint 算法。
  Closes: GAP-6
  Parallelization: Wave 2c | Blocked by: 27,53,55 | Blocks: 29,30 | Can parallelize with: —
  References: `src/acp/nmr/{protocol.py:46,reference_validation.py:37,673,atomic_diagnostics.py}`；`src/acp/calculations/identity.py`；`tests/test_acp_nmr_protocol_spec.py`、`test_acp_nmr_reference_validation.py`、`test_acp_nmr_atomic_diagnostics.py`。
  Acceptance: 定向 pytest 全绿；指纹输出对固定输入等价（旧/新 hash 一致）；零 acp import；路径字符串已迁移（grep）。
  QA: happy = 指纹等价 + 全绿；failure = 指纹算法偏差 → 回退并保持 acp 侧实现（记录待决，不改门控）。
  Commit: Y | `refactor(cccp): relocate NMR protocol/diagnostics batch 4 with identity decoupling`
  Recommended task executor category: deep-high

- [x] 29. C05-s5 报告/导出保持 + analysis_revision/method_config 拆分
  What to do: 明确 `report.py` 无人值守导出的 **CCCP 目标位置、旧路径转发与依赖清单**（write-registry 裁定，评审 C2）；保持 JSON/XLSX/PNG 输出（`write_all_reports` 调用链 `workflows/nmr.py:3670,3940`）与 `NmrReport.as_dict` 载荷边界（展示装配与科学内容分离点记录）；`analysis_revision.py` 拆分：纯峰编辑/证据哈希入 cccp，修订记录/平台路径 (`acp.core.models.Structure` 耦合) 留 acp（或注入）；`method_config.py` 平台目录耦合（`acp.catalog`）留 acp，科学默认/校验符号按 registry 裁决；`dp5q_stub.py`/`shift_predictor.py` 保持隔离并随核心迁位（路径更新）。
  Must NOT do: 不删 matplotlib/openpyxl 依赖；不把修订平台身份迁走；不破坏 DP5q 隔离（默认链不得引用）。
  Closes: GAP-6
  Parallelization: Wave 2c | Blocked by: 28 | Blocks: 30 | Can parallelize with: —
  References: `src/acp/nmr/{report.py:356L,analysis_revision.py:744L,method_config.py:671L,shift_predictor.py,dp5q_stub.py}`；`src/acp/workflows/nmr.py:3670,3940`；`tests/test_acp_nmr_report_schema.py`、`test_acp_nmr_report_paths.py`、`test_acp_nmr_analysis_revision.py`、`test_acp_nmr_method_config.py`、`test_acp_nmr_dp5q_isolation.py`、`test_acp_nmr_shift_predictor.py`。
  Acceptance: 报告三件套生成通过（json/xlsx/png 存在且 schema v2）；DP5q 隔离测试全绿（含默认链不带 stub 断言）；拆分两侧符号清单化。
  QA: happy = 报告样例全绿 + 隔离全绿；failure = 缺 openpyxl/matplotlib 场景 → 明确失败/降级（不得静默跳过 XLSX）。
  Commit: Y | `refactor(cccp): preserve NMR report chain; split revision/method-config boundaries`
  Recommended task executor category: deep-high

- [x] 30. C05-s6 资产与打包闭环最终审计（最终状态）
  What to do: 资产统一迁移与全部 reader 切换已由 T55 完成（本片不做迁移、不构成任何前序任务的提交前置）：审计最终状态——单一资产位置（默认 `src/cccp/analysis/nmr/model_assets/`，无 `__init__.py`）无第二份内容；`models`/TMS、`error_model`、`fchl`、`reference_validation`、`dp5q_stub` 全部经 T55 统一入口解析（dp5q_stub 缺失参数状态按基线保持）；各已合入节点资产摘要不出现"非空→None"退化；汇总资产清单（文件/大小/哈希/许可）供 todo 39。
  Must NOT do: 不改资产内容/校验和；不在 acp.nmr 保留第二份；不把 LFS/在线下载当替代；不把本审计表述为 T26 的合入前置（评审 P0-2）。
  Closes: GAP-6、GAP-9（部分）
  Parallelization: Wave 2c | Blocked by: 26,28,29 | Blocks: 39,42 | Can parallelize with: —
  References: `src/acp/nmr/models/`（atomic_reps.gz 22MB、frag_reps.gz 18MB、*.p、tms_references.txt、LICENSE-DP5、NOTICE.md）；`tests/test_packaging_assets.py`；`pyproject.toml:85-86`；评审 P0-2。
  Acceptance: `sha256sum` 8/8 与迁移前一致；无第二份资产（grep/路径证明）；摘要无"非空→None"（逐节点对照记录）；源码与 wheel 两个环境复验；资产清单文档含文件/大小/哈希/许可；B 变更请求文本完成。
  QA: happy = 8 文件哈希一致 + 加载探针 + 无双份；failure = 任一摘要退化或出现第二份 → FAIL 并回链 T55 修正。
  Commit: Y | `docs(plan3): NMR asset and packaging closure final audit (tracked)`
  Recommended task executor category: deep-low

- [x] 31. C06-s1 样本选择与基线测量
  What to do: 选择固定真实规模样本（优先现有 fixtures/goldens；不足时生成固定规模合成数据），覆盖轨迹/能量与谱数据两类；测量全量读取/内存/处理基线（命令、机器、数据规模、耗时、峰值内存）；产出 `c06-sample-baseline.md`；指标按方法分列（无损分块可重构全序列；抽样产品只证选中点↔原始点映射与关键点保留）；首次全量科学分析与后续有界读取分开报告（评审 C5）。
  Must NOT do: 不写未经测量的收益比例；不用超大真实数据集当性能证据（可控固定样本优先）；不修改产品代码。
  Closes: GAP-7（部分）
  Parallelization: Wave 3 | Blocked by: 4,19 | Blocks: 32 | Can parallelize with: 34–38
  References: `tests/baseline/cccp_calculation_goldens/`、`tests/fixtures/irc/`、`tests/fixtures/nmr/`；`docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C06。
  Acceptance: 基线表含样本路径/规模/命令/耗时/内存（可复跑）；含生成/读取两阶段指标定义与首次全量 vs 有界读取分列；未知项标"未测"。
  QA: happy = 同命令复跑偏差记录在容差内；failure = 样本不可复现（外部依赖）→ 替换为合成固定样本并记录。
  Commit: N | none
  Recommended task executor category: unspecified-low

- [x] 32. C06-s2 有界产品实现（分块/抽样/多分辨率）
  What to do: 在 cccp 分析域实现分块/抽样/多分辨率产品：保留原始点映射/顺序/科学关键点保留信息；派生身份包含输入摘要 + 算法/版本/参数/范围/分辨率/schema（不得只用 job_id）；显式标注"显示抽样 ≠ 全量科学结果"；与 C02 契约对齐（复用 schema 骨架）；记录生成与读取的峰值内存/时间随规模变化（不只看最终块大小，评审 C5）。
  Must NOT do: 不在 ACR/ACV 重算聚类/降维；不丢关键点；不新增第二条算法链。
  Closes: GAP-7
  Parallelization: Wave 3 | Blocked by: 31 | Blocks: 33 | Can parallelize with: 34–38
  References: 主方案 §7.3 分块/抽样行；`src/acp/confsearch/sampling.py`（参考）；C02 契约（todo 11/12）。
  Acceptance: 产品块有界 + 生成/读取峰值内存与时间-规模曲线记录；无损分块可重构全序列、抽样仅证映射与关键点保留（分列断言）；派生身份对同输入稳定、异参数区分；生成走真实生产入口（同 T13 实装闭包表，评审 P1-9）；定向测试全绿。
  QA: happy = 同数据块化后重构全序列（映射往返）一致；failure = 关键点（最低能量/鞍点）在抽样中丢失 → 必须保留或显式标注，记录。
  Commit: Y | `feat(cccp): bounded chunked/subsampled products with provenance identity`
  Recommended task executor category: deep-high

- [x] 33. C06-s3 大数据交付与 handoff
  What to do: 产出已生成产品/块索引与数据预算说明交 B（Data 只做字节/元数据查询）；给 A 的有限产品消费说明；登记 fixture（正常/空/部分/损坏）；并入 handoffs；交接物为版本化交付物（含版本/提供提交/样例摘要/期望消费结果），B/A 对同一 fixture 组做消费契约测试（仅检查样例合法 JSON 不算完成，评审 X2）。
  Must NOT do: 不让 Data 做科学判据；不承诺未经测的分块尺寸调优；不把块索引当科学结果。
  Closes: GAP-7、GAP-12
  Parallelization: Wave 3 | Blocked by: 32 | Blocks: 39 | Can parallelize with: 34–38
  References: todo 32 产物；`handoffs/` 约定；B 路 B02/B03 消费面。
  Acceptance: handoff 含块索引 schema/示例/预算；fixture 齐全；A 说明含单位/选择/缺帧。
  QA: happy = 用块索引复读样本块成功；failure = 索引与块不一致 → 修正索引生成并重测。
  Commit: Y | `docs(plan3): c06 bounded-product handoff (tracked)`
  Recommended task executor category: unspecified-low

- [x] 34. C07-s2 不同精度常量决策记录（不执行变更）
  What to do: 对 `HARTREE_TO_KCAL=627.509`（`path_analysis.py:20` 截断）、R 变体（`cccp/core/candidates.py:148,180`、`nmr.py:1088,1952` 内联、`constants.py:13`）逐项记录历史值/单位/容差与阈值影响（哪些筛选边界依赖它）；产出一份独立"科学变更提案"（统一影响面、黄金值影响、验收方式）供后续切片，不在本 todo 执行任何数值变更。
  Must NOT do: 不改任何常量值；不更新黄金值遮差异；不合并进迁移切片。
  Closes: GAP-8（决策面）
  Parallelization: Wave 3 | Blocked by: 15 | Blocks: 35（参考） | Can parallelize with: 31–33
  References: `src/acp/calculations/pes/path_analysis.py:20`；`src/cccp/core/candidates.py:148,180`；`src/acp/workflows/nmr.py:1088,1952`；`src/cccp/utils/constants.py:13`；todo 4 数值基线。
  Acceptance: 提案含逐项对照（历史值/差异量级/影响测试清单/变更后预期 diff）；签署为"独立科学变更切片，等待批准"。
  QA: happy = 用差异量级工具（python 计算 627.509 vs 627.5094740631 的相对差）记录到提案；failure = 影响面不可判定 → 标注"未评估项"并阻塞变更提案（不强行给结论）。
  Commit: N | none
  Recommended task executor category: deep-low

- [x] 35. C07-s3 C0 类型归并（科学对象 → cccp）
  What to do: Structure 族已由 T52 前移；本片完成其余无 I/O 科学类型与常量的归并收口（目标 `src/cccp/core/` 或 `src/cccp/analysis/` 按 registry 裁决），核验全部消费者无反向依赖；`acp.core.models` 保留同名 re-export 同一类（isinstance/序列化/延迟导入保持）；`JobSpec`/平台状态/路径/ownership 留 acp；对混合文件按 registry 直改或产补丁（B）；本片消费并核验 T52 产物（显式前置，评审 P1-5）。
  Must NOT do: 不迁平台类型；不删除旧导入名；不改序列化格式；不引发循环导入（cccp 不得 import acp）；不重复迁移 Structure 族（T52 已迁）。
  Closes: GAP-8
  Parallelization: Wave 3 | Blocked by: 5,52 | Blocks: 36（参考） | Can parallelize with: 31–34
  References: `src/acp/core/models.py`（含 `HARTREE_TO_KCAL:19`）；`src/cccp/core/candidates.py`（兼容桥现状）；`tests/test_acp_*.py` 中 Structure 消费面；主方案 §9.1 core 行。
  Acceptance: `python -c "from acp.core.models import Structure as a; from <cccp target> import Structure as b; assert a is b"`；定向测试（含序列化）全绿；`grep -rn "from acp" src/cccp` 零命中。
  QA: happy = 身份断言 + 全量 `pytest -m "not slow"` 子集全绿；failure = 循环导入或旧路径断言失败 → 回退并以补丁方式改消费方，记录。
  Commit: Y | `refactor(cccp): relocate no-I/O science types into cccp with legacy re-exports`
  Recommended task executor category: deep-high

- [x] 36. C07-s4 config 域分离（科学 vs 平台）
  What to do: 先冻结三层：CCCP 科学配置解析 / Services 平台配置解析 / 旧完整配置兼容装配（评审 C6）；将 `src/cccp/config.py` 的 `cluster` 平台节（`execution_mode/type/queue/walltime/extra_flags/nodes/local/local_retention`）与科学节分离；明确每个公开入口返回字段与旧 CCCP 入口的过渡契约；兼容字段仅限被动透传、CCCP 不得反向加载 Services 平台实现、最终装配由上层完成；新权威平台默认值/YAML/init/RemoteNode 校验与 B 在同一集成批更新；协议仍走 Python 配置入口（不生成 YAML protocols）。
  Must NOT do: 不改配置合并结果（同一输入输出等价）；不新增 YAML protocols；不破坏 cccp 6 源合并顺序。
  Closes: GAP-8
  Parallelization: Wave 3 | Blocked by: 34、B-T36–T39（同批，跨路） | Blocks: — | Can parallelize with: 35,37
  References: `src/cccp/config.py`（`_get_default_config()`）；`src/acp/core/config.py`；`config/defaults.yaml`（参考性）；ANTI #3/#4；`src/acp/scheduler/remote/config.py`；评审 C6。
  Acceptance: 覆盖原六源优先级、未知字段、环境覆盖、本地无 remote extra、远端节点与旧配置文件；同输入 `load_config` 输出等价（快照 diff 为空）；新科学最小安装不要求 Services；旧完整配置入口等价声明；三层边界文档化；与 B 的同一集成批绑定具体提交与测试记录（评审 C6 批次待绑定）。
  QA: happy = 配置快照等价 + 相关 pytest 全绿；failure = merge 顺序破坏 → 修正至 diff 空，记录。
  Commit: Y | `refactor(cccp): split platform config domain from science config with equivalence proof`
  Recommended task executor category: deep-high

- [x] 37. C07-s5 software 归并
  What to do: 审计 `src/cccp/utils/resource_utils.py:143 find_executable` / `:173 resolve_executable_config` 的公开返回形状/调用者（含测试与动态引用）；无公开/动态/历史使用则删除（保留迁移证据）；有使用则做行为兼容 adapter 后再收口；确认 `cccp.software` 单一 resolver 地位；不以 `software/` 新目录覆盖现有文件。
  Must NOT do: 不删除仍有调用者的入口；不改变 `resolve_executable` 行为；不新建 `software/` 目录。
  Closes: GAP-8
  Parallelization: Wave 3 | Blocked by: 5 | Blocks: — | Can parallelize with: 36,38
  References: `src/cccp/software.py`（`resolve_executable:229` 等）；`src/cccp/utils/resource_utils.py:143,173`；`src/cccp/utils/__init__.py`（re-export）；`tests/test_cccp_software.py`。
  Acceptance: 调用者审计表（每调用者 file:line + 处置）；删除后或 adapter 后相关 pytest 全绿；`grep -rn "find_executable" src` 收口记录。
  QA: happy = 审计完整 + 全绿；failure = 动态引用发现（字符串/配置）→ 保留并加 adapter，记录。
  Commit: Y | `refactor(cccp): retire legacy executable discovery with audit evidence`
  Recommended task executor category: deep-low

- [x] 38. C07-s6 qc/cluster 调用者审计 + 科学异常窄化
  What to do: 审计 `cccp/qc/cluster/*` 平台机制的真实调用者（哪些走它、哪些走 Services 新路径），审完才交 B 接管（不直接删）；对科学模块的宽泛捕获做窄化：以具体异常 + 结构化失败替代 `except Exception`（保留边界兜底的隔离职责与日志）；新增/改动 QC 界面提供 F4 amendment/等效门及原生输入输出 fixture。
  Must NOT do: 不直接删除 qc/cluster；不新增裸 `except Exception:`；不放宽兜底日志/关联身份；异常窄化/第三方采用不与位置迁移合并提交，独立行为基线（评审 C9）。
  Closes: GAP-8（部分）、GAP-11（部分）
  Parallelization: Wave 3 | Blocked by: 4 | Blocks: — | Can parallelize with: 36,37
  References: `src/cccp/qc/cluster/__init__.py`（JobStatus/factory）；`tests/test_f4_scope_audit.py`；ANTI #8；根 AGENTS（QC 层改动登记）。
  Acceptance: 调用者表 + 移交/保留结论；窄化清单（file:line → 替代异常类型）；F4 amendment 文本（若触 QC 界面）；定向测试全绿。
  QA: happy = 窄化后原失败场景仍结构化失败（对照测试）；failure = 窄化导致漏捕 → 回退该处并记录（不得以宽泛捕获掩盖）。
  Commit: Y | `refactor(cccp): narrow science exceptions; audit qc/cluster callers`
  Recommended task executor category: deep-low

- [x] 39. C08-s1b 完整发行清单与最终变更请求（C39b）
  What to do: 完整发行清单：包发现（`cccp*`）、依赖（core deps + 科学必需 extras）、资产（`model_assets/*`）、许可/notice（LICENSE、LICENSE-DP5、NOTICE.md）、Python 版本；给出精确 `pyproject.toml`/构建配置改动文本与 node 依赖同步点，交 B 应用（B请求）；不依赖私有在线算法；冻结独立 CCCP 发行配置、Services/CLI 发行归属与兼容 ACP 包的文件所有权（避免多 wheel 重复安装同一 Python 文件）；滚动打包与首批构建规格已由 T56/T55 交付（本片只做完整发行层，评审 P0-3/X4/C8）。
  Must NOT do: 不直接改 pyproject（B 请求）；不让 Web/IAM/账单依赖进入科学最小包；不声明未验证的资产随包；不重复 T56 的滚动子项。
  Closes: GAP-9、GAP-12
  Parallelization: Wave 4a | Blocked by: 30,33,38 | Blocks: 40,42 | Can parallelize with: —
  References: `pyproject.toml:74-76,85-86`；`requirements-node.txt`；`tests/test_packaging_assets.py`；主方案 §18/§28.3；todo 30 资产清单；评审 P0-3。
  Acceptance: 清单逐项含文件/大小/哈希/来源/许可；变更请求含可粘贴 diff 文本与校验步骤（wheel namelist 断言 `atomic_reps.gz` 等出现）；含多 wheel 文件归属裁定与"首批范围验收 vs 完整发行验收"分列。
  QA: happy = 变更文本在临时构建目录实测可构建（记录）；failure = 依赖缺失漏列 → 以干净环境 `pip check`/导入探针补全。
  Commit: Y | `docs(plan3): full-release build spec and change requests (tracked)`
  Recommended task executor category: deep-low

- [x] 40. C08-s2 安装预验（T40a 本次范围 / T40b 完整发行）
  What to do: **T40a 本次范围安装预验**：消费本批构建规格（T56）、已完成的所选科学闭包与依赖清单（按本次声明 `IN_SCOPE` 范围），用出树（out-of-tree）构建法实测：把 `src/cccp` + 独立打包配置在临时目录构建 wheel；在无 acp/Services/前端/Web/账户/账单且离线依赖已准备的环境安装（venv + `--no-index` 预置 wheelhouse 或已装依赖）；运行本次范围内的公开科学 fixture/产品读取/模型与报告样例；记录命令与输出——**不无条件等待完整 T39**（评审 R5）。**T40b 完整发行安装预验**：保留 T39 与完整 NMR/大数据等前置，对完整发行清单做同等预验。出树预验保留；最终候选复验由 T54 按对应范围执行，形成"C 预验→B 应用回执→C 复验"链条（评审 C7/R4）。
  Must NOT do: 不以"源码 import 成功"替代 wheel/资产验证；不在缺少依赖时视为通过；不污染主仓（临时目录/venv）；不用 T40b 的完整前置阻塞 T40a；不把 T40a 当作完整发行验收。
  Closes: GAP-9
  Parallelization: Wave 4a（分两阶段） | Blocked by: T40a←56 + 本次范围所选闭包；T40b←39 | Blocks: 41、54（按对应子项） | Can parallelize with: 43
  References: `tests/test_packaging_assets.py:15-18`（wheel 检查步骤）；`requirements-node.txt`；T55/T56（T40a）、todo 30/32 样例（T40b）；`scripts/verify_cccp_isolation.py`（隔离理念）；评审 C7/R5。
  Acceptance: T40a：按本次声明范围的 wheel namelist 与资产清单一致、干净环境安装成功且 `find_spec("acp") is None`、范围内科学样例跑通、命令与输出入证据；调度场景核对：仅选一个已独立 simple 工作流（NMR/大数据未选）时本预验可达，T30/T33 保持未完成且不被宣称交付（评审 R5）。T40b：完整资产（≥8 文件）与完整样例集通过。
  QA: happy = 上述断言；failure = 缺资产/缺依赖 → 修正清单（按范围回 39/56）并重跑；真实科学程序未预置记 NOT_VERIFIED。
  Commit: N | none（构建在临时目录；如需 CI 收敛交 B）
  Recommended task executor category: deep-high

- [x] 41. C08-s3 许可/可获得性声明
  What to do: 产出科学软件许可与可获得性独立声明：CCCP 自有代码 MIT（保留项目 LICENSE）；模型资产（DP5/FCHL）许可证与来源（LICENSE-DP5/NOTICE.md）随包；不可分发/外部软件（ORCA/xTB/CREST/CENSO/ISOSTAT/Shermo）给 provider/availability 说明；不用私有在线算法补缺。
  Must NOT do: 不伪称已随包分发不可分发项；不改变既有许可；不遗漏 notice。
  Closes: GAP-9、GAP-12
  Parallelization: Wave 4a | Blocked by: 40 | Blocks: — | Can parallelize with: 43
  References: `LICENSE`；`src/acp/nmr/models/LICENSE-DP5`、`NOTICE.md`；主方案 §18.1/§28.3。
  Acceptance: 声明文档三节（自有/资产/外部）齐全；每项含许可与来源或"外部提供"标注。
  QA: happy = 用 wheel 内资产对照声明（无漏项）；failure = 发现无许可资产 → 标记 blocked 并入 39 清单修正。
  Commit: Y | `docs(plan3): science license and availability statement (tracked)`
  Recommended task executor category: writing

- [x] 42. C08-s4 节点依赖同步请求 + release/worker 对齐
  What to do: 汇总 C03–C09 产生的全部新增运行时依赖（凡 `acp run`/节点运行需 import 的），产出 `requirements-node.txt` 同步请求（B请求）与 pyproject extras 对应；与 B06/B07 对齐 release、worker 科学依赖、模型/notice；不新增 `python -m cccp`；应用结果纳入 T54 候选版本发布前安装复验。
  Must NOT do: 不直接改 requirements-node（B请求）；不夹带 Web/IAM 依赖。
  Closes: GAP-9、GAP-12
  Parallelization: Wave 4a | Blocked by: 30,39 | Blocks: B07（跨路） | Can parallelize with: 41,43
  References: `requirements-node.txt`；`tests/test_packaging_assets.py`（同步守护）；`ACP_Parallel_Plan_2_Services_20261018.md:100-112`；根 AGENTS（节点依赖纪律）。
  Acceptance: 请求含 diff 文本与同步点；逐依赖说明用途与最小性。
  QA: happy = 对照已迁移模块 import 面（AST/grep）无遗漏；failure = 发现遗漏依赖 → 补请求并复跑打包样例。
  Commit: Y | `docs(plan3): node dependency sync requests (tracked)`
  Recommended task executor category: unspecified-low

- [x] 43. C08-s5 Python API/示例/兼容政策/支持矩阵
  What to do: 提供 Python API 文档（公开入口清单）、科学示例脚本/notebook 级样例（离线可跑）、兼容/弃用政策（旧路径转发窗口、删除条件）、可复现支持矩阵（Python/OS/科学软件/模型组合的 PASS/FAIL/NOT_VERIFIED 标注）。
  Must NOT do: 不承诺未验证的软件/模型组合；不把示例依赖浏览器/平台。
  Closes: GAP-9、GAP-10（准备）
  Parallelization: Wave 4a | Blocked by: 39 | Blocks: — | Can parallelize with: 40–42
  References: `src/cccp/**` 公开导出面；主方案 §18/§27.4；todo 40 证据。
  Acceptance: 示例至少 2 个可离线执行（命令+输出证据）；支持矩阵含未验证标记；兼容政策含删除条件。
  QA: happy = 示例在隔离环境跑通；failure = 示例依赖未宿主机软件 → 标注为需外部软件并给 NOT_VERIFIED 说明。
  Commit: Y | `docs(plan3): python api examples and support matrix (tracked)`
  Recommended task executor category: writing

- [x] 44. C09-s1 插件样板冻结（只读 parser）
  What to do: 选一个最小能力样板（默认只读 parser：`cccp.calculation.frequency_parse` 或具备明确 Analysis seam 的等价项）；**元数据字段尽早冻结**（首批无外部插件也必须有明确空集合/内置版本身份，评审 X4）；冻结发现元数据/启用/加载边界：名称/发行与版本、API/schema 兼容范围、科学软件版本、能力、资源要求、许可证、依赖、错误语义；发现不自动安装/执行 entry point；名称冲突拒绝；内置 task 仍是唯一执行驻点；按首批需要提前（不等完整 NMR/大数据发行，评审 P1-3；打包侧使用 T56 首批构建规格）。
  Must NOT do: 不把全部科学代码插件化；不引入任意 shell/import 分派；不让发现流程执行未启用插件。
  Closes: GAP-10
  Parallelization: Wave 4b | Blocked by: 18,56 | Blocks: 45,46 | Can parallelize with: 47
  References: 主方案 §31.1；`src/cccp/calculation/frequency_parse.py`；`docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C09。
  Acceptance: 样板规格文档（元数据/启用/加载/冲突/缺失/unavailable 边界）+ 最小实现骨架（可导入、默认关闭）；`pytest` 相关定向测试全绿。
  QA: happy = 启用样板可解析样例输入且与内置一致；failure = 未启用/冲突名场景 → 拒绝且明确错误（不得覆盖内置）。
  Commit: Y | `feat(cccp): trusted plugin pilot for read-only parser with frozen boundaries`
  Recommended task executor category: deep-high

- [x] 45. C09-s2 SDK + 最小示例 + 契约测试
  What to do: 提供插件 SDK/最小示例/契约检查，覆盖：版本不符、缺依赖、重复能力、部分输出、单位/映射、取消/超时、回退；版本/摘要锁定（供 release/step identity/产品身份）；与内置任务入口复用 TaskRequest/Result/Context。
  Must NOT do: 不产生第二任务链；不用测试替身冒充真实解析；不跳过取消/超时边界。
  Closes: GAP-10
  Parallelization: Wave 4b | Blocked by: 44 | Blocks: 46 | Can parallelize with: 47
  References: todo 44 规格；`src/cccp/calculation/{requests,results,context}.py`；主方案 §31.1 验收清单。
  Acceptance: 契约测试套件覆盖全部 7 类边界（逐类断言）；示例插件在测试中加载并通过；SDK 文档含弃用政策。
  QA: happy = 套件全绿；failure = 版本不符场景 → typed 失败与回退指引（不静默降级）。
  Commit: Y | `feat(cccp): plugin SDK, example and contract test suite`
  Recommended task executor category: deep-high

- [x] 46. C09-s3 版本摘要字段供 B
  What to do: 为 B 的 release/step identity/产品身份提供插件与 parser 版本摘要字段规范（锁定值与产物入身份）；并入 handoffs（HN-0n）；**版本摘要 schema 与字段形状必须在 B 冻结执行身份并发布之前明确**（不得发布后首次决定，评审 C7/P1-3）；首批 API/配置/插件能力对应同一 release。
  Must NOT do: 不把插件身份混入科学结果内容身份之外用途；不承诺未定 API。
  Closes: GAP-10、GAP-12
  Parallelization: Wave 4b | Blocked by: 45 | Blocks: B06/B07（跨路） | Can parallelize with: 47
  References: todo 44/45；主方案 §7.2/§31.1；B 路 B06/B07；评审 C7。
  Acceptance: 字段规范 + 示例值；B 可直接照接（字段名/types/来源）；字段冻结时点早于 B 执行身份发布。
  QA: happy = 用样例插件生成摘要并核对；failure = 字段歧义 → 修订规范并复跑示例。
  Commit: Y | `docs(plan3): plugin/parser version digest fields handoff (tracked)`
  Recommended task executor category: unspecified-low

- [x] 47. C09-s4 QCEngine/cclib/QCElemental 对照评估
  What to do: 仅按实际候选做单程序 fixture 对照（能力/原生版本/字段/模式原子映射/失败部分输出/取消预算/科学容差）与许可/依赖评估；记录结论（可采用/不可采用/未决）；未通过不接管现有科学路径、不产生第二任务链；本评估为本次里程碑非必选，结论与行为基线独立（不与位置迁移合并，评审 C9）。
  Must NOT do: 不集成未通过候选；不因候选存在改变主路径；不引用宣传材料当证据。
  Closes: GAP-10（评估面）
  Parallelization: Wave 4b | Blocked by: 44 | Blocks: — | Can parallelize with: 45,46
  References: 主方案 §28.1 R5/§27.4；`docs/reference/next-change/ACP_Open_Source_Reuse_Assessment_20261016.md`；单程序 fixture（`tests/fixtures/qc/orca61/` 等）。
  Acceptance: 评估表（候选×维度×结论）+ 许可/依赖记录；未通过项明确"不接管"。
  QA: happy = 至少一个候选完成单程序对照（记录命令/差异）；failure = 依赖不可得 → 记 NOT_VERIFIED 并保留评估为未决。
  Commit: N | none
  Recommended task executor category: deep-low

- [x] 48. C10-s1 隔离/依赖/唯一性检查能力（首片前建立，随片扩展）
  What to do: 在首个迁移片（T8）前建立、随切片扩展的检查能力（评审 C8）：静态依赖/动态入口扫描（真实 import、importlib/registry 目标、CLI 绕路；不用文档/历史 provenance 字符串判反向依赖）、公开 API 导入、至少一个成功科学 fixture 路径、错误路径、旧/新对象与输出对照、无 ACP 安装检查；复用/扩展 `tests/test_architecture_invariants.py`、`scripts/check_grep_gates.py`、`scripts/verify_cccp_isolation.py`（现仅固定 14 个 task 核，需覆盖新 `cccp.analysis.*` 与 workflow→task/helper 映射）；禁止以计数相等作为门；新公开实现不放 `__init__.py`；产出可复用检查脚本（或清单）与每片运行记录。
  Must NOT do: 不修改现有护栏断言（放宽须经 B 登记并保持等效或更强）；不以文件存在/计数相等当唯一性证据；注入式负例仅在临时副本/隔离 worktree 运行并清理，不污染其他路线工作树；不把检查写成人类步骤（必须命令化）。
  Closes: GAP-11
  Parallelization: Wave 1 前置（先于 T8 建立）→ 每片扩展 → Wave 5 收口 | Blocked by: 1,4,5 | Blocks: 8（能力门）、16–30（随片扩展） | Can parallelize with: 49,50
  References: `tests/test_architecture_invariants.py`；`scripts/check_grep_gates.py`；`scripts/verify_cccp_isolation.py:63`（EXPECTED_TASKS 14 核）；`tests/test_cccp_isolation.py`；todo 1 slice 模板。
  Acceptance: 能力在 T8 前可用；脚本/清单可对全仓跑通；输出含 15 工作流入口面与 cccp 侧零 acp 引用断言 + 新 cccp.analysis 模块的隔离探针；对历史片可复跑；workflow→task/helper 映射登记在案。
  QA: happy = 全仓通过 + 注入负例被捕获；failure = 发现漏检入口（如字符串引用）→ 扩展检查并记录。
  Commit: Y | `test(cccp): add per-slice dependency and uniqueness check harness`
  Recommended task executor category: deep-low

- [x] 49. C10-s2 旧/新输出对比程序（PASS/FAIL/NOT_VERIFIED）
  What to do: 对相同输入/参数/软件/算法版本比较旧/新及实际 Python/CLI/Web/MCP 科学输出（本次范围：Python/CLI + fixture；Web/MCP 记 NOT_VERIFIED）；接口模拟和纯输入生成只算各自范围；ORCA/xTB/CREST/LSF 真实能力按配置/marker 记录三态；产出对比报告与逐项证据；在联合验收中负责同一 release 下科学等价判定（B 组织组合验收，评审 X3）。
  Must NOT do: 不把模拟当真实运行；不写"通过"但无命令输出；不改黄金值。
  Closes: GAP-11、IS-12 证明
  Parallelization: Wave 5 | Blocked by: 8–30 各片 | Blocks: — | Can parallelize with: 48,50
  References: todo 4 数值基线；各切片 QA；`tests/conftest.py` 三态规则（NOT_VERIFIED）。
  Acceptance: 对比报告逐项含输入/命令/旧新输出/判定（三态）；真实软件项如实标注。
  QA: happy = 至少 3 个领域（NMR/PES/simple）完成对比；failure = 输出偏离超容差 → 列为 FAIL 并回链对应切片修复。
  Commit: N | none（报告入 tracked 验收摘要，见 T51）
  Recommended task executor category: deep-low

- [x] 50. C10-s3 撤旧实现与兼容面收口
  What to do: 对已迁能力按删除条件撤旧实现（保留必要公开转发、历史 reader、模型/vendor/许可、恢复 fixture）；删除前核对公开导出、动态注册、持久格式、恢复 fixture；**若发现遗漏调用者：恢复兼容 shim/修正调用路径，或回退整个切片——不得在 CCCP 已有唯一实现后再恢复一份 ACP 实现**（评审 C9）；同类身份、错误类型、原生模式编号与既有科学 hash 需有证据；受影响异常/兼容/依赖与文档台账同步（经 B 请求/补丁）；更新 handoff registry 状态。
  Must NOT do: 不删有调用者/公开/动态使用的实现；不删审计材料/fixture；不整目录删除 compat；不新建第二份 ACP 实现。
  Closes: GAP-11、GAP-12
  Parallelization: Wave 5 | Blocked by: 8–47 | Blocks: — | Can parallelize with: 48,49
  References: 主方案 §17/§29.2；`src/acp/compat/legacy/*`（只读保留）；todo 5 registry；评审 C9。
  Acceptance: 撤旧清单（符号→证据→保留转发）逐条；删除后全量定向测试全绿；registry 状态更新。
  QA: happy = 全绿 + 无孤儿引用（grep）；failure = 发现遗漏调用者 → 恢复兼容 shim 或回退切片（不新建 ACP 实现），记录。
  Commit: Y | `refactor(acp): retire migrated implementations with verified forwarding shims`
  Recommended task executor category: deep-low

- [x] 51. C10-s4 最终证据与交接汇总
  What to do: 汇总可合入切片清单、源码唯一性/数值/隔离/资产证据索引、旧调用替换记录；向 B 交付（闭包/补丁/打包/字段）与向 A 交付（产品样例/映射/限制）的完成清单；关闭 handoff registry 与台账状态机（待核验→…→关闭）；产出 `c10-final-evidence-index.md`；验收摘要与状态必须入库（tracked）；补丁状态区分已交付/已集成；范围状态 `IN_SCOPE/DEFERRED/NOT_APPLICABLE` 与证据三态分列（评审 X4/X5）；**在 T54 之后汇总本次范围证据（评审 R4 §3.1）；仅汇总已可用证据，仍等待跨路应用/集成的项目显式保持未关闭（不得提前关闭）；本汇总不构成 B 发布的额外等待项——发布前终验消费 T54，生产部署与冒烟归 B-T76/F5；里程碑验收按本次 `IN_SCOPE` 任务及其必要依赖、T54 执行；全路线最终关闭才检查全部任务；未选能力保留 DEFERRED，必需上游不能用 DEFERRED 绕过（评审 R5）。**
  Must NOT do: 不把 blocked-by-B 项标为完成；不替 B/A 记录其验收；不删未决清单。
  Closes: GAP-11、GAP-12
  Parallelization: Wave 5 | Blocked by: 本次 IN_SCOPE 任务 + 必要依赖 + T54（里程碑）；全路线最终关闭时=全部 | Blocks: — | Can parallelize with: —
  References: todo 10/13/14/33/39/42/46 的 handoff；主方案 §17 状态机；索引 §7 联合关闭条件。
  Acceptance: 索引覆盖全部 todo 证据路径（逐 todo 一行）；handoff 状态逐项（完成/blocked-by-B/blocked-by-A）；未决项显式；跨路未决项保持 open（不提前关闭）；tracked 摘要含补丁已交付/已集成与范围状态。
  QA: happy = 交叉核对证据路径全部存在；failure = 缺失证据 → 回链该 todo 补齐（不补记"完成"）。
  Commit: Y | `docs(plan3): final evidence index and acceptance summary (tracked)`
  Recommended task executor category: unspecified-low



- [x] 52. C07-s3a 科学基础类型前移（Structure 族）
  What to do: 将 `acp/core/models.py` 中前序切片实际消费的无 I/O 科学基础类型（`Structure`/`StructureRecord`/`StructureEnsemble` 及所需科学常量）前移至 cccp（目标由 todo 2 依赖图 + todo 5 registry 裁定，默认 `cccp/core/` 或 `cccp/analysis/structure/`）；`acp.core.models` 保留同名 re-export 同一对象（isinstance/序列化/延迟导入保持）；`JobSpec`/平台状态/路径/ownership 留 acp；混合文件按登记直改或产补丁（B）；等待常量精度决策（T34）不是本片前置（评审 C1）。
  Must NOT do: 不迁平台类型；不删除旧导入名；不改序列化；不引入 cccp→acp 依赖；不等待 T34/T35。
  Closes: GAP-8
  Parallelization: Wave 2a | Blocked by: 2,5 | Blocks: 17,28,29,35 | Can parallelize with: 16,20,25
  References: `src/acp/core/models.py`（`Structure` 等，含 `HARTREE_TO_KCAL:19`）；消费边 `src/acp/io/structures.py:20`、`src/acp/nmr/analysis_revision.py:41`；评审 §6.2 C-T17/T21/T28 行。
  Acceptance: `python -c "from acp.core.models import Structure as a; from <cccp target> import Structure as b; assert a is b"`；`grep -rn "from acp" src/cccp/core src/cccp/analysis` 零命中；定向测试（含序列化）全绿。
  QA: happy = 身份断言 + 序列化等价 + 全绿；failure = 循环导入/身份断言失败 → 回退并以补丁方式改消费方，记录。
  Commit: Y | `refactor(cccp): forward-move no-I/O science base types with acp re-exports`
  Recommended task executor category: deep-high

- [x] 53. C07-s3b identity fingerprint 纯算法下沉
  What to do: 将 `acp/calculations/identity.py` 的纯算法 fingerprint 部分下沉 cccp（唯一目标由依赖图/todo 5 registry 裁定，默认 `cccp/analysis/identity.py`）；ACP 侧保留同对象/同输出兼容入口（旧 plan/step identity 不变）；T28 不得复制第二套算法；产出可消费交付物供 B-T27 验证（版本 + 提供提交 + 样例摘要，评审 C1/§6.2）。
  Must NOT do: 不在 T28 复制算法；不改输出（hash 值/schema/字段顺序）；不反向加载平台实现。
  Closes: GAP-8、GAP-12（部分）
  Parallelization: Wave 2a | Blocked by: 2,5 | Blocks: 28、（跨路）B-T27 | Can parallelize with: 16,52
  References: `src/acp/calculations/identity.py`；消费边 `src/acp/nmr/protocol.py:46`、`reference_validation.py:37`、`analysis_revision.py:40`；评审 C1。
  Acceptance: 老/新 fingerprint 对固定输入输出一致（字符级）；`python -c` 同类断言；交付物含版本+提供提交+样例摘要；旧 plan/step identity 回归全绿。
  QA: happy = 输出一致 + 全绿；failure = 任一样例输出漂移 → 停止并保持 ACP 实现（标记待决，不改门控）。
  Commit: Y | `refactor(cccp): sink identity fingerprint pure algorithm with compat entry`
  Recommended task executor category: deep-high

- [x] 54. C08-s6 候选版本发布前安装复验（B 应用回执后）
  What to do: **定位为候选版本发布前安装复验（评审 R4 §3.1）**：B 的"应用回执"指补丁已进入集成候选提交，**不代表已部署生产**；**本复验等待对应范围的安装预验（T40a 本次范围 / T40b 完整发行，按本次声明范围选择）**；在本次候选源码/构建输入冻结、回执齐备后，于仓库之外启动验收环境（排除 editable `.pth`、源码 cwd、PYTHONPATH 泄漏），确认 ACP/Services 不可发现；对候选提交重建 wheel 并复验：资产清单/摘要、DP5/FCHL 可用性、模型加载、数值输出、运行样例；**样例与运行要求按本次声明范围执行——若发布 M1 子集则验证该子集的实际安装产物，未纳入的运行能力（如 NMR 运行）不阻塞、按范围状态显式；随包资产完整性按实际发行清单核对**；记录候选提交、构建配置、安装产物摘要与运行环境（发布使用的产物须与本复验产物可核对）；首批 API/配置/插件能力对应同一 release（无插件也有明确版本集合，评审 C7/X4）；**影响产物的后续修改使原复验失效，须修复候选后重跑（评审 P1-5/R4）。**
  Must NOT do: 不以出树预验替代合并复验；不拼接不同提交的通过记录；不用 `find_spec`/文件数量当验收；不把 B 应用回执当生产发布；不以发布后核对替代本复验；不在 B 冻结执行身份后才首次决定字段形状（T46 覆盖）。
  Closes: GAP-9、GAP-12
  Parallelization: Wave 4a（发布前） | Blocked by: 40a/40b（按声明范围）、B 应用回执（跨路）、候选源码/构建输入冻结（跨路） | Blocks: B release、发布前终验（F1–F4 消费本结果） | Can parallelize with: —
  References: todo 39/40；`tests/test_packaging_assets.py:15-18`；评审 C7/§6.2 C-T40 行、R4 §3.1；X2"发行与资产"交付物。
  Acceptance: 干净环境安装成功、`find_spec("acp") is None`、资产摘要与实际发行清单一致、声明范围内科学样例运行记录（含命令与退出码）；候选提交/构建配置/产物摘要/环境关联记录且发布产物可核对。
  QA: happy = 全部断言 + 样例运行；failure = 缺资产/依赖泄漏（acp 可发现）→ FAIL 并修复候选后重跑；真实科学程序缺环境记 NOT_VERIFIED。
  Commit: N | none（记录并入 T51 tracked 验收摘要）
  Recommended task executor category: deep-high

- [x] 55. C05-s0 NMR 资产统一入口与同批切换（先于 T25）
  What to do: 建立 CCCP 内**唯一资产定位入口**（默认 `src/cccp/analysis/nmr/assets.py`：`asset_dir()`/`resolve_asset(name)`，不依赖各模块自身 `__file__` 推导）；将 `src/acp/nmr/models/` 8 文件（含 tms_references.txt/LICENSE-DP5/NOTICE.md）迁至单一位置（默认 `src/cccp/analysis/nmr/model_assets/`，无 `__init__.py`）；**同一批**切换全部已有 reader 的路径语义：`acp/nmr/models.py::_load_tms_table`（:36,45 读取 `models/tms_references.txt`，缺失将返回 `(None, None)`，:83/:91）、`error_model.py:41`、`fchl.py:81`、`reference_validation.py:82-83,673`（asset_hashes 经统一入口）、`dp5q_stub.py:110-111`（缺失参数状态按基线保持）；尚未迁移的 ACP reader 调用 CCCP 定位入口（不复制第二份资产、不从 CCCP 反查旧 ACP）；打包侧：`package-data`/打包测试补丁与 T56 首批构建规格组成同一集成批（补丁就绪与 B 应用回执两态分列，评审 P0-2/P0-3）；测试须清除进程内 TMS 缓存影响（不得被迁移前缓存掩盖路径缺失）。
  Must NOT do: 不产生第二份资产或第二定位入口；不改资产内容/校验和；不从 CCCP 反向查询旧 ACP；不把 dp5q_stub 缺失参数当回归修复。
  Closes: GAP-6（资产面）、GAP-9（部分）
  Parallelization: Wave 2a | Blocked by: 4,5,56 | Blocks: 25,26,28,30 | Can parallelize with: 16–19,52,53
  References: `src/acp/nmr/models.py:36,45,83,91`；`src/acp/nmr/reference_validation.py:82-83,673`；`src/acp/nmr/dp5q_stub.py:110-111`；`tests/test_acp_nmr_*`、`tests/test_packaging_assets.py`；评审 P0-2。
  Acceptance: 冷启动进程（清缓存）取得与迁移前逐值一致的 TMS 数值；8/8 资产摘要一致且非空；各 reader 经统一入口解析（探针子进程）；源码与（B40a 后）wheel 证据；无第二份资产（grep/路径证明）。
  QA: happy = 冷启动 TMS 对照 + 8/8 摘要 + reader 探针；failure = 复现 `(None, None)` 或摘要非空→None → FAIL 并修正定位入口后复测。
  Commit: Y | `refactor(cccp): unify NMR asset location and switch all readers in one batch`
  Recommended task executor category: deep-high

- [x] 56. C08-s0 首批构建与资产变更规格（C39a）
  What to do: 产出**首批构建/资产变更规格**（评审 P0-3 C39a）：精确构建与 package-data 补丁文本、资产摘要（8 文件）、验证命令、node 依赖早期增量；前置 = C00 登记（T2/T5）与本批科学变更规格（T55 范围）；交付到登记的 tracked 路径（产生时点入仓，不等 T51）；供 B40a 应用并作为 T44 插件样板的打包侧输入。
  Must NOT do: 不等待完整 C08/NMR/大数据；不要求 B 在科学源码未备时先改 package-data（补丁就绪与集成应用分列）；不夹带完整发行层内容（T39 另做）。
  Closes: GAP-9（部分）、GAP-12
  Parallelization: Wave 2a | Blocked by: 2,5 | Blocks: 55,44 | Can parallelize with: 52,53
  References: `pyproject.toml:74-76,85-86`；`requirements-node.txt`；`tests/test_packaging_assets.py`；评审 P0-3/§6.2 C-T40 行。
  Acceptance: 规格含补丁文本、摘要、验证命令、早期依赖增量；tracked 提交；可被 B 直接照接；临时构建目录实测通过（记录）。
  QA: happy = 临时构建/namelist 校验；failure = 补丁不可应用 → 修正文本直至 `git apply --check` 通过。
  Commit: Y | `docs(plan3): first-batch build spec and package-data patch request (tracked)`
  Recommended task executor category: deep-low

- [x] 57. C07-f4a 同值常量单点化补齐（F4 发现新增行）：将 `src/cccp/analysis/confsearch/boltzmann.py` 的 `HARTREE_TO_KCAL` 改为从 `cccp.utils.constants` 导入（值不变、`is` 同对象）；核对 cccp 内其它同值副本一并收编（不同精度项一律不动——T34 政策）；修正 `c07-precision-proposal.md` 中"已单点化"误述；定向测试 + 护栏全绿；Commit Y | `refactor(cccp): single-source confsearch Boltzmann constant`（完成于 71c36da；F4 复验 APPROVE）

## Final verification wave

> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
> 入场范围（评审 R4 §3.1 + R5）：里程碑验收检查本次 `IN_SCOPE` 任务及其必要依赖与 T54（候选版本发布前安装复验；B 应用回执为补丁进入集成候选的回执、不代表生产发布；T54 等待对应范围的 T40a/40b）；F1–F4 为发布前终验并**必须消费 T54 结果**，通过后才允许发布；全路线最终关闭才检查全部任务；未选能力保留 DEFERRED、必需上游不能用 DEFERRED 绕过；实际发布与发布后核对由 B-T76/F5 负责（跨路）；发布失败进入修复候选与复验，不得倒签。

- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Ideal-state fidelity

## Commit strategy

- 每 todo 产出一个可合入的切片提交系列（若该 todo 有代码/测试变更）：纯搬运（位置变化）与行为变化（shim/针脚/常量/调用切换）分提交，不混夹；整文件搬迁禁止。
- 提交信息格式：`<type>(<scope>): <summary>`，示例 `refactor(cccp): relocate confsearch sampling into cccp.analysis.sampling`、`test(cccp): add isolation guards for migrated NMR modules`、`chore(plan3): record C00 closure ledger`（仅当仓库存放台账时）。
- 不提交：审计生成物、`.omo/evidence/` 本地证据、任务运行目录；不提交护栏文件的放宽改动；不提交未通过定向测试的切片。
- 每片保留回退点：旧路径转发与旧 fixture 可读性在删除条件满足前不删除；回退按主方案 §15.2 触发条件执行。
- 共享文件（pyproject/requirements-node/AGENTS/全局门）永远以 B 请求/补丁形式流转，不直接提交。

## Success criteria

> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.

| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 全部 todo（本计划文件）+ 1 | 结构自检（todo 计数/格式）+ F1 | `.omo/plans/acp-parallel-plan-3-cccp.md` |
| IS-2 | 3,5,7,10 | B06 handoff 完整性检查（索引 §5 十要素） | task-10-acp-parallel-plan-3-cccp-b06-closure.md |
| IS-3 | 8,9,20–30 | `verify_cccp_isolation.py` + 定向全绿 + 注入负例 | task-8/9/…-acp-parallel-plan-3-cccp.log |
| IS-4 | 11–14 | schema 校验 + 正常/空/部分/损坏/旧 fixture | task-11..14-acp-parallel-plan-3-cccp.json |
| IS-5 | 16–19 | 黄金字段/单位/原子/模式映射等价断言 | task-16..19-acp-parallel-plan-3-cccp.log |
| IS-6 | 20–24 | 排序/权重/弧长/RMSD/导数等价 + 既有测试全绿 | task-20..24-acp-parallel-plan-3-cccp.log |
| IS-7 | 25–30、55 | NMR fixtures 全绿 + 资产统一入口/同批切换 + 报告三件套 + DP5q 隔离 | task-25..30/55-acp-parallel-plan-3-cccp.log |
| IS-8 | 31–33 | 同数据实测 + 映射往返/关键点断言 | task-31..33-acp-parallel-plan-3-cccp.json |
| IS-9 | 15,34–38,52,53 | 单点定义/同类身份断言 + 常量对照记录 | task-15/34..38/52/53-acp-parallel-plan-3-cccp.md |
| IS-10 | 39–43、54（发布前安装复验） | 干净环境 wheel 安装（出树预验 + 候选版本发布前安装复验，结果供发布前终验）；发布与发布后核对由 B-T76/F5 负责 | task-40/task-54（候选提交、构建配置、产物摘要、运行环境） |
| IS-11 | 44–47 | 插件契约套件（7 类边界） | task-45-acp-parallel-plan-3-cccp.log |
| IS-12 | 每 todo QA + 48–51 | 证据索引齐全 + tri-state 记录 + 护栏全绿 | task-48..51-acp-parallel-plan-3-cccp.md |


