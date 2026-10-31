# ACP2 功能审查与新开发基线

审查日期：2026-10-28—29。基线：`main` / `fd2087c71df9695edbc798b74e595ee487b198f8`，与前次 `b32f22bb` 的 Git 树一致；本轮工作区修改为文档及审查证据。环境与原始状态见 [baseline.json](evidence/baseline.json)。

## 1. 审查结论

ACP2 的科学底座、任务平台、研究运行时和 MCP 已有大量实现与回归证据；目前不能把它定义为已完成的自主科研产品，也不能把本轮审查记作全绿。

最先处理的事项是 Workbench 可操作性、测试隔离和构建断言缺陷，其次是 RPH 正式入口装配与同一条真实模型＋真实 QC＋原生证据＋后续决策链。完整 DA 配方和多 Agent 不宜作为首个新开发批次。

本轮全面盘点功能表面并运行广泛回归，**并非对每个工作流、科学软件、硬件和集群组合都重做真实验收**。未重启生产服务，未新增真实模型调用、QC 或 LSF 科学提交。真实运行结论逐项引用历史回执；未执行或缺少依赖的项目保持 NOT_VERIFIED。详细开发建议见[修订路线图第 6 节](<../../docs/RPH_Agent_Three_Phase_Roadmap_20261027(1).md#6-审查后的开发建议2026-10-29>)。

## 2. 本轮验证结果

| 验证层 | 结果 | 证据与解释 |
|---|---|---|
| 全量非慢 Python 回归，SDK 专项另跑 | **12125 PASS / 1 FAIL / 26 SKIP**；另有 104 deselected | [nonslow.xml](evidence/nonslow.xml)；失败为离线构建测试的参数断言，见 F02；跳过不计 PASS |
| 真实 DSH SDK / MCP contract | **7 PASS / 0 SKIP** | [mcp-sdk.xml](evidence/mcp-sdk.xml)；真实 SDK/进程与隔离 Controller，科学派发为测试场景，不验证真实模型/QC |
| 5 个前端浏览器模块 | **26 PASS / 18 FAIL**，44 项 | [browser.xml](evidence/browser.xml)；真实 Chromium、临时服务，未使用生产服务 |
| 其中受 Origin 影响的 10 项定向复验 | **10 PASS**，54.68 秒 | [browser-isolated-origin.xml](evidence/browser-isolated-origin.xml)；仅设 `ACP_PUBLIC_ORIGIN=` 隔离主机部署配置，无产品代码改动；原始 18 FAIL 保留 |
| Workbench 独立布局诊断 | **FAIL：新建项目按钮被遮挡** | [DOM 诊断](evidence/workbench-overlap.json)、[截图](evidence/workbench-overlap.png)；与 Origin 无关 |
| 前端 Node 模块 | **479 PASS / 0 FAIL** | [日志](evidence/frontend_modules.log)；模块测试不替代真实 DOM 可操作性 |
| RPH build/test/check | **PASS**，20 个编译后测试文件 | [测试](evidence/rph_test.log)、[边界与类型](evidence/rph_check.log) |
| RPH package + 离线安装表面 | **PASS** | [日志](evidence/rph-package-unrestricted.log)；4 个 tgz / 12 个安装面条目；不证明默认研究组件已装配 |
| compileall / CCCP isolation | **PASS** | [编译](evidence/compile.log)、[隔离](evidence/cccp_isolation.log) |
| architecture-remediation grep gates | **PASS**，10 gates / 0 blocking / 0 stale allowlist | [日志](evidence/grep-gates-suite.log) |
| ACP2 契约 / coverage 验证 | **VALID** | [契约](evidence/contracts.log)、[覆盖](evidence/coverage.log)；VALID 表示记录结构有效，仍有 5 gaps OPEN |
| 覆盖行引用的原始回执摘要 | **108 / 108 匹配** | [完整性核对](evidence/evidence-integrity.json)；不等于所有能力通过 |
| 冻结 BOM 当前 URI 可取回性 | **FAIL**，31 个路径均不存在于本机 | [walker 日志](evidence/bom_walk.log)、[逐项核对](evidence/evidence-integrity.json)；不能推断历史制品从未存在或摘要错误 |
| Ruff check / format | **FAIL**，1064 条诊断 / 189 文件需格式化 | [check](evidence/ruff_check.log)、[format](evidence/ruff_format.log)；存量质量债，不能按条数等同功能缺陷 |
| 文档守护与已生成的 RPH 安装面专项复验 | **41 PASS** | [doc-and-install-checks.xml](evidence/doc-and-install-checks.xml)；补充文档与当前生成物验证，不重写全量套件原始跳过结果 |

10 项 Origin 复验不能把原 44 项整组改记 PASS。剩余 8 个原始浏览器失败中，7 个包含面板/顶部区域拦截操作，另 1 个为主查看器未找到 canvas/container；后者根因仍待定位。未修复、未重跑整组，不宣称剩余问题已关闭。

非慢套件的 26 个跳过包含外部发行制品/历史 worktree、当时尚未生成的 RPH 制品、SSH 凭据与集成门、qml/openbabel、本机缺少的真实 Bruker 数据等。后来 RPH 打包成功仅补充安装面证据，不覆盖其他跳过项。各个跳过理由及失败名称见 [test-results.json](evidence/test-results.json)。

运行过程中的两个非产品失败保留原记录：grep gates 首次缺少必需的 `--suite` 参数，补正后通过；RPH package 首次受沙箱内 pnpm store 访问限制，获准在沙箱外复跑后通过。不得把首次失败日志删除或当成最终产品失败。

## 3. 功能覆盖与实现边界

机器盘点含 534 个 `test_*.py`、30 个 catalog 条目、执行能力表、API 路由、MCP 工具、执行限制与科学能力注册，见 [inventory.json](evidence/inventory.json)。以下是对功能族的审查；“有实现/回归”不等于每项已真机验证。

| 功能族 | 当前能力与证据 | 边界 / 未闭合项 |
|---|---|---|
| 科学计算 | 15 个 active 工作流，CCCP 任务核心为唯一执行根；下表逐项区分入口 | 普通 workflow、科学 capability、bounded step 是不同注册表，不能把某表缺项解释为整个计算功能不存在 |
| NMR | 科学实现迁入 `cccp.analysis.nmr`，ACP 保留修订/评审，DP4/DP5/FCHL 等进入回归 | qml/openbabel 和真实仪器数据层有跳过；未重做真实 NMR 全链 |
| 证据/溯源/反应/选择性 | 契约语料、验证器、科学产品、命题状态、方法/能量可比性与引用均有实现和回归 | 模型叙述不能提升为证据；支持一个能量子命题不能提升整个机理 |
| 科学插件 | metadata-only discovery、disabled-by-default、授权启用、distribution digest、冻结描述符、pure_analysis 与表单有实现 | F02 为回归缺陷；F03 为人手编辑数值缺陷；worker-hosted COMPUTE 仍 DEFERRED |
| 任务/执行 | 调度、pause/unpause/continue/rerun/cancel、CAS、发布与 attempt 身份、fencing、cgroup、恢复有覆盖 | 默认 envelope 仅 `singlepoint` + `BoundedRecipe`；不能将全部 integrated 条目都称为默认冻结链 |
| 研究运行时 | Proposal/ResearchRecord CAS、ResearchSession、writer、Saga、提交意图、事件游标、snapshot/resume、EvidenceView/决策校验 | 平台能力存在不等于 RPH 正式 CLI 已完整消费；需同链故障恢复验收 |
| 审批/身份/预算 | principal/project/proposal 作用域、plan digest、审批撤销、reservation/usage、幂等与限额有覆盖 | 生产模型凭据轮换/授权 M3-03 未闭合；累计 core-hours/token 硬强制 DEFERRED |
| walltime/queue deadline | 当前代码已声明 enforced；历史 M4-03 有真实终止、排队、暂停/恢复和重启回执 | 不能拿通用 LSF deadline 通过替代 DA Local/LSF 专属要求 |
| 数据/存储 | ArtifactRef、prepare/read、generation/digest、访问边界、远端缓存、namespace/传输/归档与存储配额有实现及回归 | 未对每个远程存储后端、跨版本数据集和大文件负载重新实测 |
| CLI/API/MCP | 六个顶层 CLI；API 24 模块、13 个挂载 router；MCP stdio 8 基础 + 协商后 10 研究工具 | research API 包含写入/Saga/证据，非旧文档所称全只读；未实现 MCP Streamable HTTP |
| RPH | 四包、Supervisor、skill 接口、Planner gateway、端口组合、恢复/readiness/CLI 框架 | 默认 bin 的产品组件不完整；其余三个 helper 默认确定性；便携 skill 与第二 harness 无交付证据；无多 Agent 交付 |
| Workbench | 项目/队列/结构/轨迹/编辑、研究问题、审批/预算/科学能力表单，模块与部分浏览器通过 | F01/F03 可操作性缺陷；查看器一项失败根因待定位；字段交付不等于所有结果已有面板 |
| 发行/安装/CI | 四 Python 发行设计，RPH 离线打包本轮通过，M5-03 独立操作者第二轮历史 PASS；非空前端/RPH/MCP 门已接 CI | 当前冻结 BOM URI 无法取回；未重新执行四发行干净环境全组合或生产发布；lint 门仍有存量债 |
| 兼容面 | 14 retired + 1 planned，identity forwarder、legacy 只读、恢复 fixture 保留 | 不复活 retired workflow；不为消除缺口破坏单向依赖、回执或科学证据护栏 |

### 3.1 工作流矩阵

`integrated` 指科学 closure 表，不等于默认启用 envelope；remote 是代码声明支持，不代表本轮重新做了远端真机验收。完整值见 inventory。`BoundedRecipe` 是内部额外执行入口，非第 16 个普通 CLI 工作流。

| 工作流 | closure | 默认 envelope | 声明 remote |
|---|---|---|---|
| singlepoint | integrated | 是 | 是 |
| optimize | integrated | 否 | 是 |
| frequency | integrated | 否 | 是 |
| scan | integrated | 否 | 是 |
| irc | integrated | 否 | 是 |
| tsmode | legacy | 否 | 是 |
| casscf | integrated | 否 | 否 |
| xtb_optimize | integrated | 否 | 是 |
| nmr | legacy | 否 | 是 |
| Confsearch | legacy | 否 | 是 |
| PESsearch | legacy | 否 | 是 |
| BatchOptimize | legacy | 否 | 是 |
| XtbPathSearch | legacy | 否 | 否 |
| OrcaGradient | integrated | 否 | 否 |
| XtbConstrainedOpt | legacy | 否 | 否 |

bounded DAG 支持 singlepoint/optimize/frequency/irc 四种步骤。科学插件 capability registry 的内置描述符当前为 electronic.energy、geometry.optimize、vibration.analyze、vibration.compute；其他 family 未在该注册表中开放，不影响既有 workflow/catalog 的独立存在。

### 3.2 历史真实证据的有效范围

- [M4-02](../acp2_remediation_20261026/m4-02/summary.json)：真模型三次调用，但模型分支派发 `fake`；真 singlepoint/optimize/frequency/xtb_optimize 在另一科学分支；不能拼接为同一自主研究链。Chromium/Edge 历史通过也不能覆盖本轮页面失败。
- [M4-03](../acp2_remediation_20261026/m4-03/README.md)：15 行真实 LSF/控制/恢复/约束证据；该环境使用 localhost SSH/Openlava，不代表任意生产集群。
- [DA route-a](../acp2_remediation_20261026/m4-04-route-a/REPORT.md)及[16 核复跑](../acp2_remediation_20261026/m4-04-route-a-16core/REPORT.md)：执行 PASS、端点关联/连通性命题 refuted；方法偏差有记录。九步多个 attempt 不等于完整单 TaskRef 配方；Local receipt/计时与 DA-ENV-LSF 仍未闭合。
- [M5-03 独立复验](../acp2_remediation_20261026/m5-03/m503-operator-receipt-round2/RESULTS.md)：第二轮安装 PASS。当前 URI 不在本机与历史成功可以同时成立，应重新核对发行物保存与取回路径。

## 4. 发现与处理优先级

这里的 F 编号是本次审查编号，不改写原 G/H 门。

| 编号 | 判断与影响 | 依据 | 后续处理及完成标准 |
|---|---|---|---|
| F01 | **已复现产品缺陷**：科研面板遮挡项目/队列操作；影响基础使用 | 1280×800 下科研面板高度约 1630，按钮中心命中 `sq-no-project-note`；trial click 被拒绝；截图与原浏览器失败 | 修复主布局/面板滚动和尺寸约束；不同视口可点击新建项目、分页、队列控制；查看器缺失不阻断 shell；整组浏览器复验 |
| F02 | **回归断言缺陷**：离线插件测试错误要求两种构建器都有 `--no-index` | `tests/test_acp2_science_plugin_production.py:683`；helper 的 build 分支使用 `python -m build --no-isolation`，pip 分支才有 `--no-index`；fixture backend 为 stdlib 且无构建依赖 | 分支验证真实离线约束并覆盖两种工具链，保留 metadata-only/无依赖下载要求；不卸载 build 或跳过用例制造绿灯 |
| F03 | **代码及历史实测确认的产品缺陷**：整数/数值字段经 DOM 编辑变成字符串，被 schema 拒绝 | `frontend/js/workbench/features/capability-form.js:610`；历史 M4-05-F1、[form.json](../acp2_remediation_20261026/m4-05/form.json) | 按 schema 转换数值并区分空值/非法输入；用真实 change 事件验证；保持后端强类型拒绝，不能只保留默认值回避操作 |
| F04 | **环境隔离缺陷已定位**：临时端口测试继承部署 public_origin，合法测试请求被拒绝 | server `_resolve_public_origin`；10 项仅设置 `ACP_PUBLIC_ORIGIN=` 即全通过 | 在测试夹具显式隔离部署配置，保留独立的跨域攻击拒绝测试；不关闭产品跨域保护；原始失败证据保留 |
| F05 | **产品装配缺口**：默认 `rph research` 缺正式 parts，readiness 拒绝 | `rph/packages/cli/src/main.ts` / `compose.ts::defaultParts`；当前说明 §2.3 | 装配模型、持久 writer、证据、预算、提交映射；安装后的 bin 无测试注入即可运行；MCP 消费能力有版本协商 |
| F06 | **同链验收缺口**：真模型与真 QC 尚未在同一研究中连通 | M4-02 分支；各 helper/证据端口实现 | 一条真实轨迹记录规划、审批、QC、原生证据与后续决定；断线/重启不重复提交，取消/预算停止真实生效 |
| F07 | **执行覆盖与配方缺口**：普通 workflow 默认 attempt 链范围有限；完整 DA recipe 缺 step kind | execution_chain/step_definition 与 G15 | 按近期场景逐个推广，冻结/回执/恢复/发布一起验收；跨 TaskRef 路线可独立实现，但不能据此关闭单 TaskRef 的 G15 |
| F08 | **发行可取回性未通过**：当前冻结 BOM 的 31 个本机 URI 全缺 | walker + evidence-integrity | 核实保存位置/恢复候选制品，重新 verify 并对账；重新构建当前源码不是自动恢复旧 digest |
| F09 | **未定位浏览器失败**：主查看器首次加载未找到 canvas/container | browser.xml 中 `test_main_viewer_first_load_centers_model` | 隔离复现、检查 fixture/资源/布局，定位后修复复验；暂不归为已确认渲染引擎缺陷 |
| F10 | **存量质量债**：lint/format 未通过 | 1064 诊断、189 格式文件 | 按触及模块收敛并建立增量基线；单独安排存量治理，不能以批量格式化掩盖功能审查，也不要求全部清零才验证第一条研究链 |

F02 中扫描/启用等前置断言已通过，但该用例最终仍 FAIL。无 `--no-index` 不能直接证明发生网络下载；同样，本轮没有网络封锁回执，不能把断言分析提升为已验证绝对离线。

## 5. 新开发前置条件与正式门

建议先建立能复现的开发基线：修 F01/F03，修 F02/F04 的测试问题，定位 F09，并重跑受影响套件；同时明确 F08 的发行物取回策略。设计讨论和文档工作可继续；第一阶段功能交付前须消除其路径上的阻塞。累计资源硬上限、CDXML、worker COMPUTE 等 DEFERRED 项不是此轮全部实现的前提，但产品不能宣称拥有这些能力。

随后只推进一个正式 RPH 入口和一个小型真实场景，逐项纳入执行链与证据链。既有能力复用，不重建任务数据库、审批体系、预算或分布式调度。

[正式 coverage](../../docs/reference/next-change/acp2-contracts/release/evidence-coverage.current.json)仍有 G05/G10/G13/G15/G18、H3/H4/H7 等 OPEN，M3-03 OPEN，`candidate_full.allowed=false`。原回执 108 项摘要一致不关闭任何未满足的门。阶段性交付可以有明确受限范围，完整候选发布仍按原门逐项验收；不能用新路线图重命名旧缺口达到放行。

## 6. 可复现性与文档更新

命令、JUnit 结果及跳过/失败详情见 [test-results.json](evidence/test-results.json)、[checks.json](evidence/checks.json)和[追加验证说明](evidence/followup-checks.json)。布局诊断脚本见 [workbench-overlap-probe.py](evidence/workbench-overlap-probe.py)。这些测试应在临时服务与测试目录运行；真实模型/QC/LSF 的专属验收另外记录成本、输入、软件/模型版本与范围。

修改文档的本地链接检查见 [doc-links.json](evidence/doc-links.json)；README 中两项缺失历史 HTML 的链接已改成现存文档入口并注明缺失，初次检查记录保留为 [doc-links-initial.json](evidence/doc-links-initial.json)。证据文件摘要清单见 [SHA256SUMS](evidence/SHA256SUMS)。

本轮更新根及相关嵌套 `AGENTS.md` 的源码基线、目录/入口、研究 API 写入、MCP 工具范围、默认执行链和证据边界；根 README 保留详细 MCP 指南，并补充本报告入口。历史回执、冻结契约/JSON/BOM 未被改写，代码缺陷没有在文档任务中顺手修复。
