# ACP 2.0 并行计划 C：RPH、Workbench 与本服务器体验验收

**日期**：2026-10-20  
**性质**：Agent C 可执行任务单；本文只安排工作，不表示功能、安装或联调已经完成。  
**总方案**：[ACP 2.0 四组分协同升级方案](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)  
**操作依据**：[MCP / DSH 本服务器联调与验收手册](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)

**2026-10-21 基线**：用户已确认 debug 完成；默认开发 revision 为 `3670790363c7a1959ad1dbe4b4297adec518952a`，报告内代码候选为 `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798`。按[总索引](ACP_2.0_Parallel_Execution_Index_20261020.md)消费实际范围和剩余接线，不把 debug 完成推定为真实科学/LSF/MCP 已验。

## 1. 目标、边界和开始条件

C 同时负责两个并列客户端：RPH 完成科研问题理解、提案、审批反馈和证据驱动研究；Workbench 在不启动 Agent、不配置模型时也能完成手工研究、观察任务和查看科学证据。二者均消费 A 的科学契约和 B 的平台公开用例。

本计划有 **36 个步骤：C00–C35**。P0–P2 细化到文件、操作、验证和交接；P3–P5 同样指定可交付产物和可关闭条件。按 debug 已完成规划，但开工必须记录其实际冻结 revision。debug 明确延期的 MCP 正式入口仍由 B 实现；不得把“debug 完成”解释成该入口已存在。

### 1.1 文件所有权

| C 可以修改 | 用途与限制 |
|---|---|
| `frontend/**` | 沿用 `js/workbench`、`js/client`、`js/visualization`；产品名 ACV 不触发目录改名 |
| **拟新增** `rph/**` | 独立 Node/pnpm workspace；C02 冻结布局后创建；不把 DSH fork 放入本项目 |
| **拟新增** `tests/test_acp2_experience_*.py` | Python 体验契约及 Harness 测试；遵循 `tests/AGENTS.md` |
| **拟新增** `scripts/acceptance/acp2_experience*.py` | 体验验收驱动、真实浏览器与 DSH 检查；不另造平台 Controller |
| **拟新增** `docs/reference/next-change/acp2-contracts/experience/**` | C 的消费契约、ViewModel、交接请求及版本说明 |
| 本文与 `docs/reference/next-change/ACP_2.0_RPH_*.md`、`ACP_2.0_Workbench_*.md` | 体验与发行操作文档 |

C 不修改 `src/cccp/**`、`src/acp_services/**`、`src/acp/**`、根 `pyproject.toml`、共享 CI、B 的总 manifest 或科学 Schema。需要公共路由、安装 extra、服务静态资源挂载、统一发行或 CI 时，在 `acp2-contracts/experience/requests/` 提交带正负示例的请求，由 B 单写；需要科学字段和验证器时向 A 提交请求。

### 1.2 已有代码要复用

- `frontend/js/client/resource-store.js`：共享资源快照唯一写入口；页面、RPH 面板不能再建立任务缓存权威。
- `frontend/js/client/{transport,task-view-query,job-detail-query,event-stream,job-commands}.js`：请求、命令、查询与事件；新增能力从这些入口增量接入。
- `frontend/js/workbench/features/reaction-builder.js`：已有反应物/产物、键变化和原子空间交互，增量扩展；不另造反应编辑器。
- `frontend/js/workbench/features/wizard/**`：已有上传、预览、表单、提交和编辑流程。
- `frontend/js/visualization/{scene_instance,mol3d_adapter,framing,energy_chart_renderer}.js`：复用有界场景、framing 和能量呈现，不让可视化层发 API 请求。
- `frontend/js/module_tests/run_all.js`：现有零依赖 Node 模块测试入口。新增模块测试命名 `acp2_*.test.js`，使 `node frontend/js/module_tests/run_all.js acp2_` 成为精确选择器；空选择器结果不算通过。
- `docs/reference/next-change/examples/acp_mcp_stdio_probe.py`：已有只读进程探针；不能把它当成真实 DSH 或科学计算证据。

### 1.3 不允许的替代实现

1. RPH 不持有第二份任务/attempt 数据库，不写平台 job status，不直接调用 ORCA/xTB，不通过 shell、SSH 或 DSH 通用执行工具绕过平台提交。
2. DSH 官方 `@deepseek-ai/dsh-mcp-client` 负责 MCP 传输；`rph-acp-bridge` 只映射科研语义、引用和错误。不能 fork DSH 核心或再写一套 MCP transport 换取首轮通过。
3. `ResearchRecord` 记录研究过程和任务引用；DSH Session 是聊天/工具上下文；二者均不是平台任务权威。
4. CCCP `EvidenceReport` 与 RPH 的假设/解释分别存放和展示。模型不能把自己的结论写成科学验证器的 PASS。
5. fixture 模式可用于开发，但本服务器真实握手、真实 DSH、真实模型、Local/LSF、浏览器阶段门必须提供实际执行证据。

## 2. A/B/C 交接契约和并行顺序

### 2.1 共同里程碑

| 门 | C 的交付 | C 的依赖 |
|---|---|---|
| H0：基线与边界 | C00 的源码/文件边界记录；不等待 Node 安装或 Harness | debug 冻结候选；B00 的基线清单 |
| H1：正负契约可消费 | C01 的 Schema、引用和错误 Fixture；C06/C10/C11 实现可随后交付 | A-01 最小 Capability / Task；B01/B03 工具/公开用例契约；不等待 H2 真实入口 |
| H2：P0 真实环境与 MCP/DSH | C04–C09 | B 已启动唯一隔离 Controller、真实 MCP 入口；环境许可/凭据 |
| H3：P1 Local/LSF 同任务 | C10–C16 | A 小型 singlepoint/optimize/frequency fixtures；B 真实 Local/LSF、数据准备/读取 |
| H4：P2 限定 DA 科学闭环 | C17–C25 | A Reaction/Question/Recipe/Evidence；B 持久审批、预算、事件及恢复 |
| H5：P3 插件扩展 | C26–C28 | A/B 插件发现、启用及兼容契约 |
| H6：P4 跨域可靠性 | C29–C31 | A 跨域科学契约；B 多项目授权、迁移和恢复 |
| H7：P5 发行 | C32–C35 | A/B 冻结发行候选、可独立安装的 Runtime |

**启动顺序**：C00 后可独立做 C04 工具链安装，并与 C01 的契约消费并行；C01+C04 后做 C02 workspace，随后 C03 Harness、C06 Bridge。C10/C11 的 Fixture 开发不等待完整 ReactionSpec 或 DA Recipe。P0/P1 使用现有 singlepoint 契约先打通。C07–C09 等 B 正式入口交付后实测；C12–C16 等 B 的科学运行窗口；C17 以后再接 P2 契约。某个真实运行条件不足时，保留该行 NOT_VERIFIED，同时继续没有依赖的前端/Bridge 工作。C00～02 中“C03 后用 Harness 汇总”只是证据补录，不是完成这些先行任务的反向前置。

### 2.2 最早必须收到的交付

| 发送者 → 接收者 | 交付内容 | 最迟消费步骤 |
|---|---|---|
| A → C | 最小 CapabilityDescriptor、现有 singlepoint 请求/结果 Schema、至少一个合法和一个非法 fixture、单位/电荷/自旋语义 | C01、C06；H1 |
| B → C | H1 的工具/launch Schema、拟 argv 参数与协议支持；H2 的真实 command/args/cwd/env 和唯一 Controller 接口 | C06 只依赖 H1 契约；C07 才依赖 H2 真实入口 |
| B → C | 任务/attempt/revision、工件 digest/generation、prepare operation、分页/错误/授权/幂等字段；总 manifest 引用 C 的消费契约 | C10；H1，真实执行最迟 H3 |
| A → C | 每项 P1 能力真实科学 fixture 与判据，不能由 C 从软件名称猜算法或输入格式 | C12–C14；H3 |
| A → C | ReactionSpec、QuestionSpec、ScientificRecipe、EvidenceReport、科学 provenance 与正负 fixtures；限定 DA 物种/原子映射/TS/频率/IRC 判据 | C17–C25；H4 |
| B → C | 持久 Proposal/审批关联、预算 quote/admit/reject、幂等与失联补全、授权访问 ResearchRecord 资源的服务接口 | C19–C25；H4 |
| C → A/B | 渲染所需字段缺口、精确复现输入、期望错误码、消费端回归证据；不得只发“接口不好用” | 每次阻塞发生时 |
| C → B | RPH/前端包、脚本、路由挂载和发行需求；C 不改共享安装配置和 CI | C02、C32、C35 |

每个交接请求使用独立文件 `experience/requests/{A或B}-{编号}.md`，包含：consumer step、owner、当前/期望 Schema revision、最小输入、预期成功输出、至少一个失败输出、兼容要求、证据路径。解决后记录 producer revision，不能直接改对方文件。

### 2.3 本机环境明确分工

| 工作 | 单写/执行 Owner | C 的动作 |
|---|---|---|
| 原生数据目录、Python venv、科学软件、LSF、数据库、Controller、MCP 正式进程 | B | 从 B manifest 取得路径/启动清单；消费和验证，不另起第二 Controller |
| Node/pnpm、冻结 DSH、独立 RPH workspace、DSH_HOME、Web/headless profiles | C | 按手册 §3.2 和 §5 实装、锁定、保存命令和摘要 |
| 模型 Provider、受限凭据、真实模型调用 | C | 先读取会话内已批准 Provider/预算；缺必要信息时只暂停依赖该凭据的场景，继续离线工作 |
| Playwright/Chromium 二进制和浏览器测试 | C | 使用 B 提供的测试 Python venv；系统依赖缺口提交 B，不能通过生产系统升级隐式解决 |
| 测试窗口和故障注入 | B 是唯一总操作者，C 驱动体验客户端 | C 不并发重启 Controller、不自行 kill B 进程；按窗口记录 task refs |

Node 20.x 的已知预检不能直接满足手册所查 DSH engines；以选定 revision 的实际 engines 为准。手册核对候选是 DSH `687f282e8625c6b8d11ff58211a90524be2e4317`、Node `22.19.0`、pnpm `11.28.5`，这只是待验证组合，不能提前写 PASS。DSH 的 `--profile acp` 表示 **Agent Client Protocol**，不是 AutoCalcPlatform；本计划用 `web` / `headless` 加官方 MCP 插件。

## 3. 拟新增目录、Harness 和证据规则

### 3.1 独立 RPH 包布局

以下均为**待 C02 创建**的布局，不表示仓库当前已有这些包：

```text
rph/
  package.json                 # 私有 workspace，定义 build/test/check/package scripts
  pnpm-workspace.yaml
  pnpm-lock.yaml
  tsconfig.json
  packages/
    bridge/                    # rph-acp-bridge：科研语义 → 官方工具/公开API
    research/                  # Proposal/ResearchRecord、轮次与证据消费
    dsh-bundle/                # 固定 DSH 公共插件接口，profiles/skills/overlay
    cli/                       # P5 用户入口；只启动客户端，不内嵌 Controller
  test/                        # Node 测试及契约消费测试
  fixtures/                    # 附 producer revision/digest 的消费fixture
  examples/                    # 配置模板、任务引用样例；无凭据
```

独立包的公开接口使用带 schema/version 的 JSON；不要让外部调用者依赖 DSH 的内部类。第三方 DSH 源码安装在 `$ACP2_TOOLS/deepseek-harness`，不是 `rph/` 的 fork。

### 3.2 先交付 Harness，再使用场景命令

**C03 必须先实现** `scripts/acceptance/acp2_experience.py` 及 `tests/test_acp2_experience_harness.py`。后文命令中的场景是拟新增接口，尚未交付时不能把命令存在当作可执行事实。

```text
python scripts/acceptance/acp2_experience.py
  --manifest <B冻结总manifest绝对路径>
  --scenario <本计划下表中的精确场景ID>
  --out <本次场景独立证据目录>
  --timeout <正整数秒>
  [--dry-run]
```

需要先冻结 C 的消费字段 Schema：`acp2-contracts/experience/manifest-extension.schema.json`。B 总 manifest 引用它，C 不擅自给 B 的文件添加字段。它至少描述：候选 revision、A/B 契约路径和 digest、源码/工具/数据/证据绝对路径、测试 Controller 引用、MCP launch 清单、DSH revision/launcher/profile、浏览器可执行路径、模型 Provider/model ID、受限凭据来源引用、fixture 清单、预算/任务数量/超时上限。凭据值不进入 manifest。

Harness 必须：

1. `--help` 不初始化模型/DSH、不连接平台、不提交任务；`--dry-run` 只校验输入并列出动作，报告 `NOT_VERIFIED`。二者自身成功退出 0（与 B Harness 一致），该 0 不表示业务场景 PASS。
2. 退出码 `0=PASS`、`1=实际断言失败`、`2=NOT_VERIFIED/未执行`、`3=用法错误`；已执行失败不能被异常捕获改成 NOT_VERIFIED。
3. fixture 场景标 `evidence_kind=fixture`；真实场景标 `evidence_kind=live`，真实场景检测到 mock transport/FakeBackend/假模型必须失败。
4. 每个场景保存脱敏 argv、cwd、配置摘要、开始/结束时间、退出码、实际任务/操作/会话引用、断言及工件摘要；零断言/零工具调用/零 browser page 都不能 PASS。
5. 严格限定 B manifest 的 Controller 和测试项目；不回落生产 `8765`、生产 `/var/lib/acp/runs` 或 debug 数据目录。
6. 不自行重启/停止 Controller。需要故障时向 B 的受控测试接口/操作者申请窗口，记录确认，再继续客户端检查。

执行人从 B 提供的环境文件加载变量，不给生产服务换 PATH；可在本轮终端定义以下函数，使每步命令一致：

```bash
export ACP2_EXPERIENCE_EVIDENCE="$ACP2_EVIDENCE/C"
mkdir -p "$ACP2_EXPERIENCE_EVIDENCE"
c_accept() {
  "$ACP2_TOOLS/acp-venv/bin/python" \
    "$ACP2_SOURCE/scripts/acceptance/acp2_experience.py" \
    --manifest "$ACP2_MANIFEST" --scenario "$1" \
    --out "$ACP2_EXPERIENCE_EVIDENCE/$1" --timeout "${2:-300}"
}
```

`ACP2_SOURCE/TOOLS/EVIDENCE/MANIFEST` 均从 B 冻结清单取得；`ACP2_TOOLS/acp-venv` 若采用其他名称，在 H0 一次性冻结实际绝对路径并更新函数，不能临时猜路径。`ACP2_SOURCE` 是集成候选源码，不是 DSH 工作目录。

### 3.3 场景枚举与实际证据

| 阶段 | 场景 ID（每个均须由 C03 登记） |
|---|---|
| P0 | `baseline`、`contracts`、`workspace`、`harness-selftest`、`node-dsh-install`、`browser-install`、`bridge-contract`、`mcp-live`、`dsh-discovery-live`、`model-live` |
| P1 | `client-fixtures`、`manual-browser-fixtures`、`dsh-local-singlepoint-live`、`dsh-lsf-singlepoint-live`、`dsh-optfreq-live`、`same-task-browser-live`、`client-recovery-live` |
| P2 | `question-reaction-fixtures`、`research-record-fixtures`、`approval-budget-live`、`research-loop-live`、`evidence-browser-live`、`research-resume-live`、`fault-boundaries-live`、`da-research-live`、`p2-closeout` |
| P3 | `plugin-discovery-live`、`schema-form-live`、`plugin-upgrade-live` |
| P4 | `cross-domain-live`、`multi-project-live`、`experience-upgrade-live` |
| P5 | `client-release`、`clean-install-live`、`operator-runbook-live`、`experience-release-closeout` |

每个任务的证据默认保存到 `$ACP2_EXPERIENCE_EVIDENCE/<场景ID>/`。报告使用手册的 `acp2.acceptance.v1`；`PASS/FAIL/NOT_VERIFIED` 与 `IN_SCOPE/DEFERRED/NOT_APPLICABLE` 分开记录。必需 live 行不是 PASS 时，里程碑不能关闭。大体积原始模型/科学输出保存到受控数据资源，报告只存授权引用与 digest；归档副本脱敏。对已有可信证据只验证 revision/digest 和影响范围，不因文档修改重复昂贵计算。

## 4. P0：环境、契约和真实 MCP/DSH 就绪（C00–C09）

### C00 固定基线和 C 文件边界

- **前置**：debug 提供冻结候选及报告；B 给出隔离源码、数据根、证据目录和总 manifest 草案。
- **动作**：①读取本计划、总方案、手册及 `tests/AGENTS.md`；②保存 `git rev-parse HEAD`、工作区脏文件列表和现有前端回归结果；③核对不在生产数据库上执行；④在 `experience/ownership.md` 登记 C 写域和 A/B 请求路径；⑤把继承的既有失败与新失败分别记来源，不改 debug 报告。
- **产物**：`experience/ownership.md`、基线快照和 C 开工记录。
- **验证**：`git status --short`；`git rev-parse HEAD`；`node frontend/js/module_tests/run_all.js`。C03 完成后运行 `c_accept baseline` 汇总真实命令结果。
- **失败断言**：候选 SHA 与 manifest 不同、数据根指向生产、C 计划需覆盖对方写域时拒绝开始修改；旧回归失败必须保留，不能重命名为新 feature 限制。
- **证据/交接**：`C/baseline/`；把基线 revision/边界确认发 B，科学 fixture 基线请求发 A。**完成 H0 的基线部分**。

### C01 冻结最小消费契约和错误示例

- **前置**：C00；A 的最小 Capability/现有 Task 正负 fixtures；B 的现有 singlepoint 工具及任务/工件引用草案。无需等待完整 ReactionSpec。
- **动作**：①创建 `experience/manifest-extension.schema.json`、`view-model.schema.json`、`tool-semantics.md`，并先交 `proposal.schema.json` / `research-record.schema.json` 的最小引用版，含研究/项目 ID、计划摘要、资源上限、审批/任务引用；P2 再版本化补全问题/反应/证据字段，不等待 C18 实现；②列明 raw MCP 工具名与 DSH 模型可见名称的映射；③把 `isError`、结构化结果、异步引用、generation、revision、prepare/read 边界写入消费合同；④为未知工具、非法请求、错误项目、失效引用分别保存预期失败 fixture；⑤发 producer 字段缺口请求并冻结 H1 版本。
- **产物**：上述 Schema/映射、`experience/fixtures/` 与 `experience/requests/`；科学 Schema 只引用 A 的路径/digest，不在 C 复制一套可编辑权威。
- **验证**：`python3.11 -m json.tool docs/reference/next-change/acp2-contracts/experience/manifest-extension.schema.json`；C03 后 `c_accept contracts` 必须校验所有 `$ref`、Schema版本和正负输入。
- **失败断言**：未知科学字段/版本不能被默默丢掉；负向请求不能被 Bridge 自动补成另一有效科学实验；raw MCP 端不能收到 `mcp__acp__` 前缀名称。
- **证据/交接**：`C/contracts/`；A/B 回答缺口后登记 producer revision；B 在总 manifest 引用消费 Schema。**H1 可先供 fixture 开发**。

### C02 创建独立 RPH workspace

- **前置**：C00、C01、C04 工具链安装；C 对 `rph/` 拟新增独立包布局确认。先用 pnpm 完成 workspace 自身构建/测试，C03 后的 Harness 汇总不阻塞本项交付。
- **动作**：①按 §3.1 创建 package/workspace/tsconfig 和锁文件；②把 `bridge/research/dsh-bundle/cli` 包的依赖方向写入 `rph/README.md`；③定义 `pnpm --dir rph build/test/check/package` 四个脚本；④bridge 仅依赖公共科学 JSON/平台客户端接口，research 不导入平台 store；⑤锁定包管理器和最小 Node engines；⑥将根安装/CI/发行所需改动提交 B，请 B 单写。
- **产物**：独立可构建包框架、包内 API、锁文件和 B 挂载请求。
- **验证**：`pnpm --dir rph install --frozen-lockfile`；`pnpm --dir rph build`；`pnpm --dir rph test`；`pnpm --dir rph check`；C03 后 `c_accept workspace`。
- **失败断言**：空测试集不能成功；静态检查发现 `child_process`/SSH/QC 二进制执行入口、平台任务 DB 驱动或 DSH 私有源码路径依赖即失败。CLI 仅允许启动冻结 DSH 客户端程序，不能借该例外执行科学软件。
- **证据/交接**：`C/workspace/`；向 B 提交 package/CI/静态资源挂载请求，不改根共享文件。

### C03 实现体验 Harness、结果格式和自检

- **前置**：C01–C02；B 确认总 manifest 与 C extension 对接方式。
- **动作**：①新增 `scripts/acceptance/acp2_experience.py`，实现 §3.2 CLI 和全部场景枚举；②未实现的已知场景返回 NOT_VERIFIED 而不是伪 PASS；③新增 `tests/test_acp2_experience_harness.py`；④实现凭据脱敏、manifest/digest 校验、timeout/数量上限和 append-only 场景结果；⑤为 PASS、断言失败、缺前置、错参数、零断言、dry-run 副作用、mock 冒充 live 建独立自检；⑥由 B 把 C 报告纳入集成汇总，不改 debug Harness 的枚举。
- **产物**：Harness、单元测试、`experience/harness-contract.md`、合法/非法汇总样例。
- **验证**：`python3.11 -m pytest tests/test_acp2_experience_harness.py -q`；`python3.11 scripts/acceptance/acp2_experience.py --help`；`c_accept harness-selftest`；再对每个枚举运行 `--dry-run`，确认无进程/模型/平台副作用。
- **失败断言**：执行失败不准转 NOT_VERIFIED；help/dry-run 不准写 PASS；未知枚举必须退出 3；真实场景遇见假模型/FakeBackend 必须拒绝 PASS。
- **证据/交接**：`C/harness-selftest/`；把实际 `--help` 与结果 Schema 发 B，供统一集成。

### C04 在本服务器安装隔离 Node/pnpm/DSH

- **前置**：C00 的基线/边界记录；B02 提供可写工具路径、原生数据路径及 Python 环境；网络/安装权限与现有授权一致。不等待 C02/C03；安装本身用官方命令完成，C03 交付后补做 `node-dsh-install` 场景汇总。
- **动作**：①按手册 §3.2 从官方来源下载冻结 Node 包并核对 SHASUMS，安装到 `$ACP2_TOOLS`；②本终端添加精确 Node/pnpm 路径，不替换系统版本；③克隆并 detach 冻结 DSH revision；④`pnpm install --frozen-lockfile`、build；⑤按官方源码启动约定定义 `dsh_acceptance`，工作目录设到独立 research workspace；⑥保存 Node/pnpm/DSH/官方 MCP 插件版本、可执行路径、锁文件摘要；⑦新终端加载受限环境文件并重验。
- **产物**：ENV-03、ENV-09 的实际就绪记录；`experience/dsh-compatibility.json`；独立 DSH_HOME。
- **验证**：`node --version`；`pnpm --version`；`git -C "$ACP2_DSH_SOURCE" rev-parse HEAD`；在 DSH 源码目录运行 `pnpm install --frozen-lockfile`、`pnpm run build`、`pnpm dsh --version`；`c_accept node-dsh-install 1200`。
- **失败断言**：Node 不满足 engines、锁文件改变、DSH revision 漂移或新终端仍解析系统 Node 时不得通过；“npm 上存在相似版本”不能代替已冻结源码/锁文件。
- **证据/交接**：`C/node-dsh-install/`；向 B 提交非敏感路径和版本，B 更新总 manifest。**本步骤是实际安装任务**。

### C05 安装 Chromium 并确认真实浏览器可运行

- **前置**：C03；B 提供测试 Python venv；C04 的受限环境路径已就绪。
- **动作**：①依手册 §3.4 设置独立 `PLAYWRIGHT_BROWSERS_PATH`；②用测试 venv 的 Playwright 安装 Chromium；③启动一次 headless Chromium，创建 page，访问本地静态空白测试页并截图；④确认用户/工作目录/缓存不落生产位置；⑤若缺系统库，把明确包名和报错提交 B，不自行广泛升级服务器。
- **产物**：ENV-07、浏览器版本/路径/启动日志、环境复用文件中的非敏感浏览器变量。
- **验证**：`"$ACP2_TOOLS/acp-venv/bin/python" -m playwright install chromium`；`c_accept browser-install 600`，场景至少断言 browser version、一个 page 和截图文件非空。
- **失败断言**：仅 `import playwright` 成功不算环境就绪；浏览器启动失败、page 没创建、截图为空必须 FAIL 或明确缺前置 NOT_VERIFIED，不能改用 DOM stub 标 live。
- **证据/交接**：`C/browser-install/`；给 B 浏览器路径和所需静态页挂载请求。

### C06 实现最小科研 Bridge 与确定性契约测试

- **前置**：C01–C04；A/B H1 fixtures。
- **动作**：①在 `rph/packages/bridge/src/` 实现 discover/validate/submit/status/evidence 的科研语义映射，底层调用 DSH 已注册官方 MCP 工具；②动态查 Capability，不把所有能力展开成顶层模型工具；③分离 raw 工具名与模型工具名；④工具失败保留结构化错误和 `isError`；⑤长任务只保存平台引用；⑥对大工件仅回引用/摘要，读取走 B prepare/read；⑦测试重复调用不改变输入科学参数。
- **产物**：Bridge 公共接口、`rph/test/bridge-contract.test.*`、C producer/consumer 契约快照。
- **验证**：`pnpm --dir rph build`；`pnpm --dir rph test`；`c_accept bridge-contract`。该行 evidence_kind 明确为 fixture，不关闭 H2。
- **失败断言**：把工具错误包装成成功、自动切换方法/电荷、自行运行量化 shell、read 隐式 prepare、遗漏 project/attempt/generation 时测试必须失败。
- **证据/交接**：`C/bridge-contract/`；向 B 回报工具 Schema/错误投影缺口，向 A 回报科学歧义字段缺口。

### C07 对 B 正式 MCP 进程做真实握手与无副作用检查

- **前置**：C03、C06；B 交付正式 launch JSON，已启动本轮唯一隔离 Controller，明确协议支持集合。B 未交付则此步 NOT_VERIFIED。
- **动作**：①校验 launch 里的 command/args/cwd/env 与测试实例一致；②记录 Controller PID/任务计数；③运行既有 stdlib 探针；④使用所冻结官方客户端运行默认 initialize/initialized/tools/list/capabilities；⑤记录实际协商版本及完整 allowlist 的 input/output Schema；⑥完成不支持版本负测；⑦关闭 MCP 子进程，核对 Controller 仍活着且任务数不变。
- **产物**：真实 stdio 协议记录、stdout/stderr 分流、官方客户端矩阵、Schema 快照。
- **验证**：`python3.11 docs/reference/next-change/examples/acp_mcp_stdio_probe.py --launch "$ACP2_EVIDENCE/mcp-launch.json" --out "$ACP2_EXPERIENCE_EVIDENCE/mcp-live/probe" --protocol-version 2025-06-18`；`c_accept mcp-live 300`。若 B 协商支持版本变化，探针参数按冻结 manifest 明确选择，不绕过 initialize。
- **失败断言**：stdout 混入日志、不支持协议被静默接受、握手新增科学任务、探测进程起第二 Controller、关闭 MCP 杀死科学任务均 FAIL。
- **证据/交接**：`C/mcp-live/`；协议问题交 B 修改入口并生成新候选；不在 C 包内修服务协议。

### C08 接入官方 DSH MCP 插件并验证无模型工具发现

- **前置**：C04、C06、C07；DSH 支持的公共 overlay/plugin 接口已按所选 revision 核对。
- **动作**：①按手册 §5.1 创建 Web/headless 专用 profile overlay，插件名 `@deepseek-ai/dsh-mcp-client`；②配置与 launch JSON 完全相同的 command/args/cwd/env；③使用 `insert` 创建 Cordis entry，避免误作 deep merge；④dump effective config 的脱敏副本；⑤启动 Web，只绑定 loopback；⑥读取官方客户端工具注册结果并验证八工具/冻结 allowlist；⑦强制坏 command 和不兼容协议，确认插件显式失败。
- **产物**：`rph/packages/dsh-bundle/profiles/` 模板、DSH Web/工具注册日志、兼容矩阵。
- **验证**：`dsh_acceptance --profile web --patch "$ACP2_EVIDENCE/acp-mcp.patch.yml" --dump-config`；按手册启动 Web；`c_accept dsh-discovery-live 300`。配置私密原件受限保管，报告只附脱敏副本。
- **失败断言**：DSH 能聊天/进程仍在即算兼容的判据必须被拒绝；没有实际工具列表、插件启动失败、重连起第二 Controller、子进程丢 cwd/env 时不得 PASS。
- **证据/交接**：`C/dsh-discovery-live/`；把实际协议/工具名称发 B；**此步和 C07 共同关闭 H2 的 MCP/DSH 传输部分**。

### C09 配置真实模型、一次工具调用和最小费用边界

- **前置**：C08；会话内已批准 Provider/model/真实请求预算；凭据由受限来源提供。缺凭据时继续 C10/C11 的离线开发，此 live 行保留 NOT_VERIFIED。
- **动作**：①在专用 DSH Web Settings → Models 配置 Provider/model；②给 headless 单独配置相同模型路由，不能假定 Web 托管凭据自动继承；③模型仅请求“调用 capabilities，不提交计算”；④断言实际模型请求、实际工具调用和实际工具返回均存在；⑤保存 provider/model、推理设置、token/费用/调用次数；⑥给错误引用，让模型看见真实工具错误；⑦创建无凭据/错误模型 ID 负测。
- **产物**：ENV-05 真实记录、Web/headless 模型配置模板、一次真实模型工具调用证据。
- **验证**：`dsh_acceptance --profile headless --patch "$ACP2_EVIDENCE/acp-mcp.patch.yml" --json '调用 mcp__acp__acp_capabilities；只报告实际返回，不提交计算。'`；`c_accept model-live 300`。
- **失败断言**：假模型、静态回放、仅自然语言说“已经调用”不能通过；达到调用次数/费用上限必须停止；错误模型不能回退另一未批准 Provider。
- **证据/交接**：`C/model-live/`；把非敏感模型/usage 证据交 B 汇总；**H2 可关闭，前提 C04–C09 所需 live 行均 PASS**。

## 5. P1：同一平台任务的研究/人工入口（C10–C16）

### C10 增量扩展 Client 查询、事件与工件消费

- **前置**：C01；B 任务、工件、prepare/read、错误/事件 fixtures；可在 H2 完成前开发。
- **动作**：①在 `frontend/js/client/` 增加 `science-query.js`、`research-query.js` 和必要命令适配；②所有共享快照写入经现有 `resource-store.js`，key 包含 project/权限上下文、资源标识和版本；③复用 transport 超时/取消语义；④新增字段通过 `workbench/contracts/` 校验；⑤SSE 仅失效/合并受版本控制的数据，不让旧事件覆盖新 revision；⑥当前 after_seq 不是持久 replay，B 未提供持久事件时用权威查询补全；⑦工件明确区分未准备/准备中/可读/失败。
- **产物**：Client 适配、ViewModel 校验、`frontend/js/module_tests/acp2_client.test.js`。
- **验证**：`node frontend/js/module_tests/run_all.js acp2_client`；`c_accept client-fixtures`。
- **失败断言**：面板各自 poll、跨项目 key 复用、旧 generation 覆盖、新选择被旧响应覆盖、GET 触发 transfer/compute 均失败。
- **证据/交接**：`C/client-fixtures/`；B 如缺 freshness/cursor/generation，提交精确交接请求，C 不伪造 replay。

### C11 添加无需模型的最小手工科学入口与只读任务视图

- **前置**：C10；A 最小 Capability + B 既有 singlepoint fixture；不依赖 C09 的模型凭据。
- **动作**：①在 `frontend/js/workbench/features/` 增加 `science-question.js` 的基础问题入口；②复用已有 wizard 的上传/预览/提交；③先选择已支持问题和输入结构，必要参数显示可解释默认值；④通过 client 发现/预检/提交，不在 Feature 内拼另一套 payload；⑤任务详情引用同一 task/attempt，提供结构/原始工件/限制；⑥关闭 Agent UI 和模型配置后仍可操作；⑦修改 `frontend/ACP_Workbench_v2.html` 的脚本/挂载点，不新增另一个任务系统页面。
- **产物**：Manual Research 最小入口、`acp2_manual.test.js`、可供浏览器 fixture 模式运行的页面。
- **验证**：`node frontend/js/module_tests/run_all.js acp2_manual`；`c_accept manual-browser-fixtures 300`。浏览器 fixture 页面标明演示数据；只证明交互合同。
- **失败断言**：未配置模型就阻断手工任务、隐藏电荷/自旋等必要歧义、预检失败仍可提交、启用退役工作流默认值均失败。
- **证据/交接**：`C/manual-browser-fixtures/`；给 A 看科学问题/默认值，给 B 提交所需公开路由缺口。

### C12 本服务器真实 DSH → MCP → Local singlepoint

- **前置**：C07–C11；A 提供小分子 XYZ、方法/基组与判据；B 冻结 Local 环境、准入上限、唯一 Controller 和测试项目。
- **动作**：①先引用 B 已跑过的 Local 科学基线报告/digest；②生成本场景独立幂等键和 submit fixture；③真实模型按发现→预检→明确一次审批→提交→状态执行；④submit 立即返回持久引用，等待由平台接管；⑤完成后显式 prepare、查询 operation、read；⑥核对 release/attempt/receipt/manifest/digest 和 A 科学判据；⑦将这个唯一任务引用保存给 C15，不能在其他入口重提来冒充同任务。
- **产物**：一个实际 Local 任务、真实模型工具事件、原生输出/科学验证引用。
- **验证**：`c_accept dsh-local-singlepoint-live 1800`；Harness 必须保存实际 argv、job_id/operation_id、attempt、状态和工件 digest。
- **失败断言**：无原生 QC 输出、假 Backend、工具返回 completed 但无平台 receipt/manifest、预检就启动计算、提交超时后自动换幂等键重提均 FAIL。
- **证据/交接**：`C/dsh-local-singlepoint-live/`；A 验科学结果，B 验生命周期，C 只归档可追溯工具/体验事实。**H3 Local 必需行**。

### C13 本服务器真实 DSH → MCP → LSF singlepoint

- **前置**：C12；B 提供实际测试节点、队列、独立远端目录、可用 release 和科学/SSH 环境。
- **动作**：①复用 A 同类科学 fixture，明确本场景是新任务、使用新幂等键；②真实 DSH 模型经相同平台入口提交 LSF；③保存实际 bsub/bjobs、submission_id、release_id 与 markers；④经平台查询完成后显式准备/拉取工件；⑤将数据/receipt 摘要与实际远端产物相互核对；⑥经同一 client 在 Workbench 查看该任务，不读取未准备的 `record.work_dir`。
- **产物**：一个实际 LSF 任务和可读工件、远端提交及拉取证据。
- **验证**：`c_accept dsh-lsf-singlepoint-live 2400`。真实 LSF 缺环境时返回 NOT_VERIFIED，不用 Local 模式代替本行。
- **失败断言**：mock Paramiko、只有生成 bsub 文本、把 pending_fetch 当已就绪、GET 偷偷拉取、task attempt 与远端 receipt 不一致均 FAIL。
- **证据/交接**：`C/dsh-lsf-singlepoint-live/`；B 提供远端权威记录，A 核验输出；**H3 LSF 必需行**。

### C14 对 P1 注册的 optimize/frequency 分别实际验证

- **前置**：C12–C13；A 对 optimize/frequency 分别提供小型 fixture、适用性与验证准则；B 启用对应受控执行通路。
- **动作**：①为两项能力分别生成 request/schema 快照和独立任务 key；②通过真实 DSH 发现/预检/提交；③保留优化几何/收敛和频率模式/单位等独立结果；④逐项核对 method/state/provenance；⑤按 A/B 冻结能力×backend 矩阵执行：P1 声明支持 Local/LSF 的格子都必须有真实任务证据；⑥任一未支持格子登记 DEFERRED，不把 singlepoint PASS 推导到其他任务。
- **产物**：每个必需格子单独结果；不可由一条总 PASS 掩盖缺项。
- **验证**：`c_accept dsh-optfreq-live 3600`；报告子行 `optimize/<backend>`、`frequency/<backend>` 分开判定。
- **失败断言**：仅 QC 正常退出就判优化收敛、没有频率就判振动完成、模型推断替代 A 验证器、两个能力复用同一份原生输出均 FAIL。
- **证据/交接**：`C/dsh-optfreq-live/`；A 定科学结论，B 定执行/范围，C 定工具与视图是否忠实消费。

### C15 真实浏览器核对三入口查看同一任务

- **前置**：C05、C10–C14；B 把既有 CLI query 的实际语法和隔离 Workbench URL 交付 C。
- **动作**：①读取 C12/C13 返回的既有任务引用，禁止新提交；②CLI query、HTTP/Workbench、MCP status 分别观察相同 task/attempt；③Playwright 真实访问默认页面，选择测试项目和精确任务；④比较同一 revision 投影或最终状态/attempt/manifest generation/digest；⑤查看结构/工件/限制，切换任务再回来；⑥记录浏览器 network、console error、DOM 断言与截图；⑦权限视图切换后检查缓存隔离。
- **产物**：同任务对照表、真实页面和网络证据、无 Agent 的手工观察证据。
- **验证**：`c_accept same-task-browser-live 600`；已有前端模块全量回归：`node frontend/js/module_tests/run_all.js`。
- **失败断言**：用 CLI 再提交新任务、仅比名称/输入不比 ID、不同 revision 的合法推进被误判分叉、DOM 显示已完成而 API 未完成、切换串台均 FAIL。
- **证据/交接**：`C/same-task-browser-live/`；向 B 提交 ID/状态/摘要对照，不用页面截图替代平台回执。

### C16 真实连接恢复、幂等重放和 P1 关闭

- **前置**：C12–C15；B 安排重连/重启窗口并提供权威任务计数/提交记录；先完成 cheap/deterministic 平台验证再做模型重放。
- **动作**：①提交已批准小任务后退出 DSH/MCP 客户端，B 不停 Controller；②重新启动同 profile，查询保存引用；③两个独立 MCP 适配进程经同一 Controller 重放相同幂等键/输入；④检查只一个 task/attempt/submission/budget reservation；⑤使 Workbench 断线再恢复，补权威快照；⑥实际工具超时后按原 key 对账；⑦记录 H3 所有必需行和未完成项。
- **产物**：跨进程恢复、重放及页面重订证据；`ACP_2.0_RPH_P1_Acceptance_20261020.md`。
- **验证**：`c_accept client-recovery-live 1800`；`pnpm --dir rph test`；`node frontend/js/module_tests/run_all.js acp2_`。
- **失败断言**：内存共享对象测试不能代替真实跨进程；断开工具请求不自动等于取消科学任务；重复键不同 payload 必须冲突，不能复用旧任务。
- **证据/交接**：`C/client-recovery-live/`；失败持久幂等/生命周期问题交 B，不在 C 引入本地任务 DB 绕过。**只有必需 live 子行全 PASS 才关闭 H3**。

## 6. P2：化学问题、研究轮次、科学证据与恢复（C17–C25）

### C17 接入 QuestionSpec / ReactionSpec 和化学歧义确认

- **前置**：C11；A-02～04 的 P2 Schema/正负 Fixture；B 公开静态预检/资源摄入接口。只等契约与接口，不等 H4 科学全链签收。
- **动作**：①增量扩展已有 `reaction-builder.js` 和 `science-question.js`，明确反应物/产物/候选步骤；②支持 A 承诺的输入格式，包括 CDXML 时调用现有/新公开解析服务，不在浏览器造科学解析权威；③显示原子映射、电荷/自旋、立体、溶剂/温度/标准态、净反应与基元步骤差异；④记录人工确认/近似及输入版本；⑤缺关键身份信息时产生澄清项，不能自动静默填补；⑥提交前后保持原子标签/引用一致。
- **产物**：问题/反应编辑 ViewModel、`acp2_question_reaction.test.js`、演示及负向 fixture。
- **验证**：`node frontend/js/module_tests/run_all.js acp2_question_reaction`；`c_accept question-reaction-fixtures`。
- **失败断言**：重复/缺失 atom mapping、未知电荷/多重度、把总体多步反应硬当一个 TS、CDXML 解析失败被当空结构提交均失败。
- **证据/交接**：`C/question-reaction-fixtures/`；科学合法性缺口交 A，解析/资源入口缺口交 B。

### C18 实现 Proposal / ResearchRecord 和可追溯记忆

- **前置**：C17；A-03/A-15 的 Recipe/Evidence 关联规范；B18 先交的 Data 资源版本写入/读取与 CAS/冲突接口。C01 已提供 Proposal/Record Schema，B16 可据此先做审批；本项再实现记录读写，不让两边互等完整功能。
- **动作**：①在 `rph/packages/research/src/` 实现 C01 冻结的 `ResearchRecord/Proposal`，字段包含 research/project ID、question/reaction revision、hypothesis、recipe digest、round、decision、approval ref、platform task refs、evidence refs、缺失验证、人工修改；②record 存于 B Data 的版本化项目资源；③DSH 本地 session 只存该 record 引用与工具上下文；④实现幂等 round/event key 和 CAS 更新；⑤记忆检索限定项目授权、来源和版本；⑥模型推理保留可审计的决策摘要/引用，不要求或归档隐藏思维链。
- **产物**：`experience/research-record.schema.json`、研究包、record migration 说明和 fixture 测试。
- **验证**：`pnpm --dir rph test`；`c_accept research-record-fixtures`。
- **失败断言**：只存在聊天历史而无持久研究引用、更新覆盖另一 round、人改过的 proposal 被旧事件覆盖、跨项目记忆泄露、把模型假设写入 CCCP EvidenceReport 均 FAIL。
- **证据/交接**：`C/research-record-fixtures/`；B Data 持久接口未交付时仅 fixture 开发，不自行搭 SQLite/任务表。

### C19 接入服务端持久审批与预算反馈

- **前置**：C18；B 已交付审批/quote/admit/reject/过期/撤销、预算预留/结算和权限错误的真实端点。
- **动作**：①在 Bridge 与 Workbench 增加审批卡，展示 proposal/recipe digest、科学限制、资源上限、费用口径和过期时间；②DSH 的工具审批与 B 科研执行审批分别映射，前者不能代替后者；③审批后只提交已绑定 digest 的计划；④未批/拒绝/过期/撤销/超预算时展示具体原因并禁止升级权限；⑤在真实平台分别试只读身份、跨项目引用、单次/累计预算；⑥检查新会话/新 MCP 进程不能绕过限制。
- **产物**：`frontend/js/workbench/features/research-approval.js`、Bridge 审批反馈、真实负向回执。
- **验证**：`c_accept approval-budget-live 600`；`node frontend/js/module_tests/run_all.js acp2_approval`。
- **失败断言**：模型说“用户同意”不能算 approval；审批后改科学参数必须失效；超限/拒绝必须没有 dispatch 与额外预算预留；DSH approval 按钮不能直接改平台状态。
- **证据/交接**：`C/approval-budget-live/`；由 B 提供权威 dispatch/账本/审批记录，C 只验证用户/模型可见反馈。

### C20 实现单 Supervisor 的研究循环与真实小型试验

- **前置**：C18–C19；A 已交付可验证小型 Recipe 和 EvidenceReport；B 执行/事件/审批用例可用。
- **动作**：①在 `rph/packages/dsh-bundle/skills/` 实现 interpreter/planner/reviewer/synthesizer 的明确输入输出；②单 Supervisor 执行 Understand→Hypothesize→Critique→Plan→Approve→Wait→Evaluate→Revise/Stop；③每轮先科学预检和平台准入；④提交后持久化 task ref，等待平台事件或有界查询，不保持模型忙等 QC；⑤由真实 EvidenceReport 决定下一步，模型只能建议；⑥执行一个预算内真实小型 Recipe，记录 round/event key、工具调用和停止条件。
- **产物**：Research Supervisor/Skills、轮次状态约束、一个真实小型研究记录。
- **验证**：`pnpm --dir rph test`；`c_accept research-loop-live 2400`。
- **失败断言**：没有证据就连续昂贵重试、LLM 自判科学 PASS、轮次/预算上限后仍提交、重复终态事件启动第二轮、Wait 阶段不断调用模型均 FAIL。
- **证据/交接**：`C/research-loop-live/`；科学判据由 A 验，调度/资源由 B 验；不能以多个 LLM 角色一致替代验证器。

### C21 Workbench 科学证据与标准 ACV 呈现

- **前置**：C17–C20；A 标准 EvidenceReport/provenance/帧产品；B 授权数据读取；C05 浏览器环境。
- **动作**：①新增 `workbench/features/scientific-evidence.js`，并列显示执行状态和 claim 的 supported/refuted/inconclusive/not_evaluated；②每个命题展开原始工件、validator、方法/版本、缺失验证与 RPH 决策来源；③Client 读规范化 products，不在页面解析大 ORCA 输出/算热化学；④复用 `visualization` scene/framing/energy/IRC/vibration，输入来自 A/B 冻结投影；⑤能量图保留只“保存候选”，不提交任务；⑥真实浏览器挂载/隐藏/换任务/销毁并检查动画和网络。
- **产物**：证据 Feature、标准视图适配、`acp2_evidence.test.js` 及真实浏览器断言。
- **验证**：`node frontend/js/module_tests/run_all.js acp2_evidence`；`c_accept evidence-browser-live 600`。
- **失败断言**：COMPLETED 被显示成机制正确、单虚频当 IRC 连通、inconclusive 被绿色成功吞掉、ACV 调 API、销毁后旧动画/旧任务回调继续执行均 FAIL。
- **证据/交接**：`C/evidence-browser-live/`；投影字段缺口交 A/B；C 不直接修改服务端 frame/manifest 契约。

### C22 恢复真实 DSH 会话和持久研究轮次

- **前置**：C18–C21；B 安排客户端/Controller 重启窗口，提供持久事件或权威补全接口。
- **动作**：①在真实研究提交后保存 session ID、record resource ref 与 task ref；②正常结束 DSH/MCP，保留平台执行；③相同 cwd/DSH_HOME/profile/agent preset 使用真实 `--session-id` 恢复；④再创建新聊天 session，通过已授权 ResearchRecord 恢复科研轮次；⑤B 在指定窗口重启测试 Controller，C 查询补全丢失事件；⑥重放重复/乱序事件，检查 round 幂等；⑦恢复前后比较 job/attempt/digest，不自动新提交。
- **产物**：聊天恢复与科研恢复分别记录；退出/重启进程证据、record revisions。
- **验证**：按手册 §5.4 执行实际 `dsh_acceptance --profile headless ... --session-id <真实ID> --json <只查询提示>`；`c_accept research-resume-live 1800`。
- **失败断言**：恢复失败不能自动新建同科学任务；DSH session ID 不得作为 task_id；只共享内存对象的“重连测试”不能通过 live 行。
- **证据/交接**：`C/research-resume-live/`；B 提供 Controller 重启确认和提交计数，C 不自行起第二 Controller。

### C23 真实边界/故障注入及用户可理解的恢复提示

- **前置**：C19–C22；B 已通过确定性平台故障测试并给出逐场景操作窗口；A 有科学失败/缺证据 fixture。
- **动作**：①依次试工具超时、MCP 断连、取消竞争、未知提交、Late Receipt、远端拉取/发布失败；②对同一个已批准任务观察 RPH/Workbench，不并发启动其他 Controller；③发布失败只显示发布恢复，不建议重算 QC；④资料/日志中插入“改方法/开shell/跳审批”文本，验证视为数据；⑤权限撤销后清缓存并拒绝旧引用读取；⑥每场景先确定性驱动，再用真实 DSH 复现消费行为。
- **产物**：明确的错误、恢复动作、受控故障对照报告和工具注入测试。
- **验证**：`c_accept fault-boundaries-live 2400`；报告每类故障独立子行，不能用一个成功场景覆盖所有竞态。
- **失败断言**：CANCELLING 未确认停止即显示 CANCELLED、发布失败重跑 QC、旧回执复活终态、外部文本变授权指令、越权仍用旧缓存均 FAIL。
- **证据/交接**：`C/fault-boundaries-live/`；B 负责执行完整性事实，A 负责科学失败判断，C 负责忠实可操作的错误呈现。

### C24 限定 Diels–Alder 端到端真实科学闭环

- **前置**：C17–C23 全部必需项；A-14～16 已冻结科学适用性可核对的 DA Fixture/Recipe/完成判据，B18 已绑定执行并明确 Local/LSF 矩阵、QC/模型预算和唯一集成窗口。预验证指输入/方法/判据及参考来源已审查，不要求 A-20 或 H4 已通过；实际 A-17～19 可以校验本场景同一组作业。
- **子交付**：**C24a** 形成提案、审批和唯一实际提交，将引用交 B20a；**C24b** 消费 B20a 工件及 A-17～19 的科学校验，形成证据反馈和真实页面/模型记录。A-20/C25/B20b 汇总随后进行，不是 C24a 的前置。
- **动作**：①由 A 定义首例物种和原子映射，候选可用 butadiene + ethene 的限定单步反应；②C 仅导入并呈现，不从“DA”名称推断 endo/exo 或多步机理；③手工或 Agent 输入 QuestionSpec→形成 Proposal→人工审批；④真实 DSH 使用 A Recipe 经 B 提交，平台运行既定构象/TS/频率/IRC/热化学/验证步骤；⑤消费 A EvidenceReport，逐项显示 TS 阶数、模式、双向 IRC/端点映射、能量可比性和限制；⑥真实模型据证据形成带引用的总结；⑦Workbench 查看同一任务/研究记录，导出证据；⑧保存一例验证不充分的真实失败/中间结果，必须保留 inconclusive。
- **产物**：真实成熟科学 fixture 的研究全链记录、可复跑输入/方法/原始输出/validator 引用、浏览器报告。
- **验证**：`c_accept da-research-live 14400`。Harness 可长时间执行，但操作人分阶段读取进度，不能把无上限等待留给模型；每个外部命令和轮次均有子超时/预算。
- **失败断言**：执行完成但缺 IRC/端点证据不能支持机制；无区分取代环境的 fixture 不能声明 endo/exo 选择性；初次真正未验证的计算失败不能被改写成“成熟科学基线已通过”。
- **证据/交接**：`C/da-research-live/`；A 签科学判据，B 签执行/预算/恢复，C 签真实模型/页面/研究记录；必须三类证据同时存在。若 A 尚无成熟 DA，先闭环 A 已成熟反应并明确 H4 DA 仍未关闭，不能偷换验收对象。

### C25 关闭 P2 并发布研究/操作说明

- **前置**：C24；A-20 科学事实与 B16～B20 的运行原始回执已提供。C 先签本轨部分，再交 B20 汇总 H4；不把 B20 总签收作为 C25 的前置。
- **动作**：①新增 `ACP_2.0_RPH_Research_Operation_20261020.md`，逐步写输入、澄清、proposal、审批、等待、证据、继续/停止、会话恢复；②新增 `ACP_2.0_Workbench_Research_Guide_20261020.md`，写不使用模型的操作路径；③逐段附实际命令/按钮、预期结果、失败解释和证据位置；④列当前科学适用范围、版本组合、未验证格子；⑤由一名未参与实现的操作者按文档复跑一个已批准小案例；⑥把 C 报告和 A/B 报告路径/digest 交 B 汇总。
- **产物**：研究操作手册、Workbench 手册、H4 C 验收清单。
- **验证**：`c_accept p2-closeout 600`；它只汇总可信原始证据，不自动重新执行科学计算；手册操作复跑记录必须真实存在。
- **失败断言**：必需 live 行 FAIL/NOT_VERIFIED、证据 revision 对不上、命令仍含占位符却写“可直接运行”、缺无模型路径都不得关闭 H4。
- **证据/交接**：`C/p2-closeout/`；B 单独判断集成主干是否可发布，C 不自行重启生产或宣布全部平台上线。

## 7. P3：插件、自动表单和兼容升级（C26–C28）

### C26 受权发现 Plugin/Skill，保持安装和启用分离

- **前置**：H4；A 科学插件 manifest/capability/recipe 发现，B 受权启用/禁用/信任决策，DSH 冻结公共插件接口。
- **动作**：①在 Bridge 提供受权 science_discover；②DSH Skills 只消费已启用工具/元数据，不能因发现描述就执行代码；③Workbench 显示 capability/provider/版本/适用性/信任状态；④把第三方插件能力集成成一个真实小型研究步骤；⑤记录插件/工具版本进 ResearchRecord，运行中不漂移；⑥未知/未启用插件仅显示受限说明。
- **产物**：插件发现 UI、Bridge/Skills 扩展和真实第三方小能力消费记录。
- **验证**：`c_accept plugin-discovery-live 1200`；`pnpm --dir rph test`。
- **失败断言**：发现即安装/执行、未知 license/不可信描述被当授权、插件替换后旧任务引用被重解释、动态展开无上限工具列表均 FAIL。
- **证据/交接**：`C/plugin-discovery-live/`；A/B 管发现/启用权威，C 不写独立插件信任数据库。**H5 部分**。

### C27 Schema 自动表单及化学专用 Feature 共存

- **前置**：C26；A 新 capability JSON Schema/约束/科学提示；B 对应预检与提交。
- **动作**：①新增 `workbench/features/capability-form.js`，先支持冻结 JSON Schema 子集的 object/array/enum/required/range/nullable；②未支持组合关键字显式提示并阻止错误提交，不降级忽略；③字段单位/科学含义来自 A 描述；④Mechanism/NMR 保留定制 Feature，通过相同 Client 调用；⑤安装不改核心代码的新能力后真实生成表单并完成一次受控提交；⑥表单默认值/人工修改进入 proposal provenance。
- **产物**：Schema 表单渲染器、支持子集文档、`acp2_schema_form.test.js`、新增能力真实浏览器操作。
- **验证**：`node frontend/js/module_tests/run_all.js acp2_schema_form`；`c_accept schema-form-live 1200`。
- **失败断言**：未知 Schema 版本被当旧版、科学必填字段隐藏、整数/单位约束绕过、未经预检提交、远端描述注入 HTML 都 FAIL。
- **证据/交接**：`C/schema-form-live/`；A 提供科学提示和契约，B 提供相同平台通路；C 不在浏览器实现科学验证器。

### C28 插件/DSH 升级、回退与历史只读

- **前置**：C26–C27；A/B 兼容范围与历史工件 reader；已通过 DSH 旧版本候选。
- **动作**：①冻结前一版本组合和研究记录快照；②在独立工具目录装候选 DSH/插件版本，不覆盖旧版；③重跑真实 MCP 发现、错误、重连、一条小型计算及自动表单；④读旧项目/旧 evidence，缺新版能力时只读并解释限制；⑤回退 profile/bridge 版本，保留原工件/record；⑥形成兼容矩阵和升级 checklist。
- **产物**：`ACP_2.0_RPH_Compatibility_20261020.md`、双版本验收、回退包及历史阅读证据。
- **验证**：`c_accept plugin-upgrade-live 1800`。
- **失败断言**：升级自动重跑旧任务、无法读取旧科学证据、悄悄换 Provider/方法、回退覆盖新 round 记录、候选失败仍标兼容均 FAIL。
- **证据/交接**：`C/plugin-upgrade-live/`；B 合并整体插件/发行结论；**C26–C28 必需行 PASS 后关闭 H5 的 C 部分**。

## 8. P4：跨领域、多项目与长期可靠性（C29–C31）

### C29 NMR/热化学与多假设预算策略

- **前置**：H5；A 提供 NMR/热化学能力与证据、至少两个可比较假设 fixture；B 有对应执行和预算策略。
- **动作**：①复用现有 `workbench/features/nmr.js`，新增标准 evidence 适配，不重写 DP4/DP5；②热化学和机制共用标准温度/参考态/可比性呈现；③RPH 先列假设和最低判别证据再申请预算；④先用已批准廉价步骤排除不适用项；⑤跨领域结果只通过 A 合法 Recipe/validator 组合；⑥真实模型和真实科学 fixture 各跑一项，记录停止/不足证据分支。
- **产物**：跨领域研究 Skills、NMR/热化学证据视图、多假设决策和使用说明。
- **验证**：`c_accept cross-domain-live 3600`；`node frontend/js/module_tests/run_all.js acp2_`。
- **失败断言**：未经归一化直接比较能量、把 DP4/DP5 数值当普遍结构证明、预算用尽仍开新假设、模型改变温度/标准态无记录均 FAIL。
- **证据/交接**：`C/cross-domain-live/`；A 决定科学可比性，B 决定资源准入，C 决定解释/交互忠实性。

### C30 多项目、多身份与撤权后的记忆/缓存隔离

- **前置**：C29；B 真实多身份/项目权限和审计，测试操作者取得受限身份。
- **动作**：①使用两个项目、至少读者/研究者两个身份；②在 Workbench 切项目/账号，检查 ResourceStore、SSE、scene 和选择状态清理；③RPH 以真实授权上下文读取记忆/任务/工件；④撤权后重试旧 tool/session/resource refs；⑤检查服务端拒绝和客户端敏感缓存消失；⑥并发两项受限研究，验证预算/幂等/round keys 不串项目。
- **产物**：权限与研究记忆隔离报告、真实浏览器/模型负向场景。
- **验证**：`c_accept multi-project-live 1800`。
- **失败断言**：新账号仍读旧项目缓存、会话切换绕权限、同内容工件凭 digest 绕授权、项目A事件更新项目B页面、把商业等级当科学结果字段均 FAIL。
- **证据/交接**：`C/multi-project-live/`；B 提供授权审计事实，C 不把前端隐藏按钮当安全边界。

### C31 研究记录迁移、长会话与体验可靠性

- **前置**：C28–C30；A/B 有版本兼容、迁移与回滚策略。
- **动作**：①冻结旧版 ResearchRecord、proposal、ViewModel 和 profile fixtures；②实现只迁移 RPH 自有资源版本，不迁平台 DB；③迁移前后比较科研引用、人工决策与工件 digest；④真实浏览器反复打开/关闭、换任务、隐藏动画并观察资源回收；⑤真实 DSH 轮次恢复时限制上下文、保存完整外部证据引用；⑥选定阶段时长/页面数量/模型次数上限，由 manifest 固定而非无限 soak。
- **产物**：RPH 资源迁移工具、历史只读 fallback、资源泄漏/恢复报告和回滚步骤。
- **验证**：`c_accept experience-upgrade-live 3600`；`pnpm --dir rph check`。
- **失败断言**：迁移不可回滚、无审计改人工决策、丢证据引用、scene dispose 后仍 tick、跨 schema 直接判科学 supported、长会话无限模型重试均 FAIL。
- **证据/交接**：`C/experience-upgrade-live/`；向 B 提交数据资源迁移需求；**C29–C31 必需行 PASS 后关闭 H6 的 C 部分**。

## 9. P5：独立发行、本机部署和操作手册（C32–C35）

### C32 构建 RPH CLI/Bundle 与 Workbench 发行交接包

- **前置**：H6；A/B 已冻结公共契约、发行规格和当前许可下可独立取得的 Local Runtime 方案。C 先交包/静态资产给 B25 构建，不等待 B25 的最终发行验收或 H7。
- **动作**：①在 `rph/packages/cli` 实现基于冻结 DSH 公共入口的 profile/连接配置/研究启动/恢复命令；②CLI 只做客户端，不能附带隐式 Controller/QC shell；③导出可安装 RPH 包、Skills/Bundle、示例和锁文件；④前端沿用既有资源路径，提供静态资产 manifest；⑤把共享打包/console entry/CI 请求交 B 单写；⑥声明模型/科学软件/Runtime/许可前置及实际支持版本，不把免费可用自动写成开源。
- **产物**：RPH 包、CLI 帮助、前端静态资产包、`experience/release-manifest.json` 和 B 发行接线请求。
- **验证**：`pnpm --dir rph build`；`pnpm --dir rph test`；`pnpm --dir rph package`；`c_accept client-release 600`。
- **失败断言**：包依赖开发者绝对路径/私密配置、必须安装完整本仓库才可用、CLI 起第二 Controller、分发缺 license/notice、前端引用不存在资源均 FAIL。
- **证据/交接**：`C/client-release/`；B 独占共享发行配置与正式发布操作，C 只提交可审查包。

### C33 本服务器干净目录独立安装验收

- **前置**：C32；B25a 给出实际安装包/摘要和独立 Runtime 测试实例；已批准模型/QC预算。不等待 B25b 包验证汇总。
- **动作**：①创建新的用户可写工具/DSH_HOME/工作目录，不能复用开发 node_modules 或个人配置；②按发行说明安装 RPH 包和冻结 DSH；③对接 B 的唯一测试 Controller；④真实工具发现/模型/Local 小任务/Workbench 查看；⑤按发行支持范围补 LSF 行；⑥卸载客户端后平台任务与证据仍可查；⑦记录安装和恢复的每条命令/退出码。
- **产物**：独立安装报告、依赖锁/来源摘要、真实 smoke 及卸载后数据可读证据。
- **验证**：`c_accept clean-install-live 2400`。
- **失败断言**：自动读开发目录配置/锁文件、缺模型却用假模型通过、安装污染系统 Node、卸载删平台任务、必须修改源码才能连接均 FAIL。
- **证据/交接**：`C/clean-install-live/`；依赖/共享安装修正交 B，C 重跑受影响场景。

### C34 完成本机配置、模型、浏览器与故障操作手册

- **前置**：C33；现有服务器 runbook 的操作命令已经由 A/B/C 实际执行。
- **动作**：①新增 `ACP_2.0_RPH_Server_Installation_20261020.md`，引用已有总 runbook，写出 Node/pnpm/DSH 精确安装、校验、build、新终端激活；②写 Web/headless 分离、Provider/凭据、SSH tunnel、loopback/端口与官方 MCP overlay；③写 Chromium 安装/系统依赖/真实页面检查；④写唯一 Controller 的接入清单、Local/LSF 科学 smoke 的操作顺序和停止/清理；⑤给“能聊天没工具、协议不合、无浏览器、模型认证失败、工具超时、发布失败、会话丢失”逐项定位命令/预期输出；⑥独立操作者按文档完成安装→一次真实工具调用→恢复→证据导出，记录差异。
- **产物**：RPH 本服务器安装手册、用户科研 guide、兼容矩阵、运维 FAQ；所有命令指向实际发行接口，不留不存在的默认入口。
- **验证**：`c_accept operator-runbook-live 2400`；Harness 检查独立操作者的命令/退出码/工具记录/浏览器证据存在且匹配候选版本，不以 Markdown 语法检查代替操作复跑。
- **失败断言**：文档要求改 HOME/CODEX_HOME、默认 `/tmp` 科学 workroot、只设 ACP_RUN_ROOT 就宣称集群配置隔离、明文凭据归档、没有停止/取消确认流程均失败。
- **证据/交接**：`C/operator-runbook-live/`；涉及总服务器 runbook 的共享修订先向 B/集成负责人提交精确补丁请求，C 不并改同一文件。

### C35 交付体验轨道最终报告和发布建议

- **前置**：C32–C34；A/B/C 对候选 SHA、契约版本、发行范围一致；必需场景证据齐全。
- **动作**：①生成 C00–C35 完成矩阵，逐项列 producer revision、证据 digest、实际结果和 scope；②汇总 Node/DSH/官方 MCP 插件/模型/Chromium/Local/LSF 兼容组合；③列未验证场景与真实科学限制，不能消除 NOT_VERIFIED；④提供 RPH/Workbench 版本升级与回滚操作；⑤把发行包、文档、结果 Schema 和 A/B 交接关闭记录交 B；⑥只有 B 的统一集成与发布流程批准后，进入正式发布，C 不擅自重启生产。
- **产物**：`ACP_2.0_RPH_Workbench_Release_Acceptance_20261020.md`、C 发行 manifest、可复跑场景清单。
- **验证**：`c_accept experience-release-closeout 600`；`pnpm --dir rph check`；`node frontend/js/module_tests/run_all.js`；只补跑因候选变化受影响的 live 场景，保留历史证据。
- **失败断言**：必需结果不是 PASS、报告引用不存在工件、版本升级沿用不匹配 PASS、只靠聊天/截图宣称科学闭环、未关闭共享接线请求都不能建议发布。
- **证据/交接**：`C/experience-release-closeout/`；B 单写最终总报告/共享发行配置。**满足全部必需行时关闭 H7 的 C 部分**。

## 10. Agent C 收工条件

1. C00–C35 各自都有可定位的产物、验证命令、实际执行状态和证据引用；尚未进入阶段的任务保持未完成，不提前声称交付。
2. H2 有本服务器真实官方 DSH/MCP 握手和模型工具调用；H3 有真实 Local/LSF 及相同 task/attempt/digest 的 CLI/MCP/Workbench 对照。
3. H4 有 A 验证的科学 fixture、B 权威审批/预算/恢复、C 真实研究/浏览器证据；失败/不足证据不能被模型解释成成功。
4. 本机 Node/pnpm/DSH、模型、Chromium、环境复用、启动、恢复、诊断、导出和清理操作均在文档中写明，并由实际命令记录支持。
5. C 没有复制任务状态机、科学验证器或 MCP transport；没有未交接的共享文件修改；B 收到可独立安装的发行包和可审查的最终证据。
