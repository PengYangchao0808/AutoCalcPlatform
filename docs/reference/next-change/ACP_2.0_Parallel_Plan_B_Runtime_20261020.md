# ACP 2.0 并行计划 B：平台执行、MCP、服务器环境与集成发行

**日期**：2026-10-20  
**执行者**：Agent B；本文件是待执行计划，任务复选框不表示已经交付。  
**总方案**：[四组分协同方案](../../ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)；操作依据：[本服务器验收手册](ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md)。  
**任务数**：B00—B26，共 27 项；P0：B00—B07，P1：B08—B14，P2：B15—B20，P3：B21—B22，P4：B23—B24，P5：B25—B26。

**2026-10-21 基线**：用户已确认 debug 完成。默认开发起点为 `3670790363c7a1959ad1dbe4b4297adec518952a`，代码候选 `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798`、最终 Harness 和报告版本分别登记。依[总索引](ACP_2.0_Parallel_Execution_Index_20261020.md)管理 H0～H7；原报告中的真实科学/LSF 与 G4 打包缺口由本计划接续，不改原结论。

## 1. 执行边界与并行纪律

本轮从 `.omo/plans/acp-architecture-acceptance-remediation.md` **完成后形成的完整 freeze SHA** 起步。历史执行链 debug 不在本轮重新设计。B00 只登记交付 SHA、已修问题和剩余范围；发现回归由原 owner 修复后生成新候选，不借机重写 Controller。MCP 默认进程入口、身份/配额应用接线等明确延期项目由本计划交付。

B 独占 `src/acp_services/**`、`src/acp/cli.py`、`src/acp/services*_bootstrap.py`、`src/acp/api/**`、`src/acp/scheduler/**` 的平台变更与平台测试。保留现有 `JobManager`/Controller、Store CAS、冻结 attempt、receipt、publication 的唯一实现权威；MCP、CLI、HTTP 都调用同一应用用例。`src/acp_services/**` 禁止导入 `acp`；需要旧管理器的实现由 `acp` 侧组合根显式注入。worker 不打开中央 jobs DB，也不提升为第二任务管理器。

共享文件由 B 单写：`pyproject.toml`、`requirements-node.txt`、CI、四处版本、根及共享 AGENTS、统一发行/安装脚本、Services 台账、总契约 manifest。A/C 只能发带理由、依赖分类、精确 diff 及验证要求的 patch request，B 审阅后合入。B 不编辑 `src/cccp/**` 科学类型/算法，不编辑 C 的 RPH/Workbench 功能实现；通过契约与 request 协作。

本机环境职责：B 配 Python、数据根、ACP 配置、科学二进制、SSH/LSF、测试 Controller；C 配 Node/pnpm/DSH、模型、Chromium。任何测试 DB 任一时刻只有一个 Controller。DSH/SDK/探针的 stdio 子进程只作入口适配，不启动或争抢 Controller，不执行启动时的任务提交。HTTP 在 P0 指现有平台 REST；首期 MCP 运输为 stdio。Streamable HTTP MCP 必须作为独立运输任务及真实客户端验收行，不能把 REST 或 SSE 命名为已经验证的 HTTP MCP。

## 2. 先交付的契约和交接点

契约根统一为 `docs/reference/next-change/acp2-contracts/`。本节列出的新增路径均为**拟新增交付物**，执行者先创建、校验并冻结后再消费，不能据路径推断文件已经存在。

| 契约 | B 交付路径 | 最早接收者及用途 |
|---|---|---|
| 基线和路径 owner | `manifest.json`、`runtime/baseline-lock.json` | H0：A/C 固定 debug SHA、文件归属及未实现范围 |
| 应用请求/引用/错误/事件 | `runtime/application-api.v1.json`、`runtime/samples/**` | H1：A 科学到平台映射；C 固定 Fixture 及 Client/RPH 开发 |
| 实际进程启动与身份 | `runtime/mcp-launch.schema.json`、本轮证据 `mcp-launch.json` | H1 给格式；H2 给真实 argv/cwd/env/controller URL，C 可启动发现 |
| 接口与科学版本绑定 | `runtime/science-binding.v1.json` | H1 先绑定现有 singlepoint；H3 接 A 增量 Registry，不等待所有 Recipe |
| 验收 Harness | `runtime/acceptance-harness.v1.md`、`runtime/acceptance-result.schema.json` | H1：A/C 可先写驱动及消费端；场景 ID/退出码冻结 |
| 任务控制/工件/幂等/预算 | `runtime/control-and-data.v1.json`、`runtime/admission-approval.v1.json` | H1 基础字段；H4 增量审批/资源冻结字段 |
| 环境与共享作业登记 | 证据 `environment/b-runtime.json`、`fixture-runs.json` | H2 环境就绪；H3 起 A/C 使用已登记任务引用，避免重复昂贵计算 |

H0—H7 分别是：debug 基线；可消费的版本契约与正负 Fixture；P0 真实 MCP/DSH 握手发现及环境；P1 Local/LSF 三入口同任务；P2 限定 Diels–Alder 科学闭环/审批预算恢复；P3 插件；P4 跨域可靠性；P5 发行。阶段门由总索引约束，B 无权将 C/A 未验收项代签为通过。

### 2.1 每个任务统一执行规则

- 先读该路径适用的 AGENTS；新实现用原有职责根，未有实现才用本文件标明的拟新增路径。保留兼容 shim、旧 reader、15 active workflows 与历史 Fixture。
- 证据根遵循总索引 `reports/acp2_implementation/<candidate-label>/{A,B,C,integration}/`。本文简写 `E=reports/acp2_implementation/<candidate-label>/B`，下文 `E/Bxx/` 是 B 的任务目录；跨轨汇总放同候选 `integration/`。记录真实 argv、退出码、冻结 SHA、环境/配置摘要、断言和原始日志位置，凭据不归档。运行数据另用明确的原生文件系统绝对路径，不指向生产数据。
- 新 Harness 拟为 `scripts/acceptance/acp2_runtime.py`；下文该命令均须先在 B01 交付、核对 `--help` 后运行。它的 `--controller` 表示连接已有测试 Controller，默认**绝不自建** Controller。现有 debug Harness 如确实交付则复用，其枚举和含义保持冻结。
- 测试结果三态 `PASS/FAIL/NOT_VERIFIED`，范围 `IN_SCOPE/DEFERRED/NOT_APPLICABLE` 分列；已运行断言失败只能记 FAIL。退出码 0 为 PASS，1 为 FAIL，2 为缺环境未验证，3 为用法错误；`--help/--dry-run/--check-only` 的 0 只表示自身成功，不计真实场景 PASS。
- 每个任务交付一个可审阅变更集和一个 receipt；receipt 至少含任务 ID、输入契约版本、代码 SHA、修改清单、测试/实测证据、失败项、下一方可直接消费的路径。不要把“已交付字段”写成“前端已接入”。

### 2.2 验证命令的输入约定

执行下文命令前由 B02/B04 填实际值：`ACP2_EVIDENCE` 指总证据根 `reports/acp2_implementation/<candidate-label>` 的绝对路径，`ACP2_CONTROLLER` 指本轮唯一测试 Controller URL。`ACP2_LAUNCH`、`ACP2_SINGLEPOINT_FIXTURE`、`ACP2_LSF_FIXTURE`、`ACP2_CONTROL_FIXTURE`、`ACP2_APPROVAL_FIXTURE`、`ACP2_DA_FIXTURE`、`ACP2_PLUGIN_FIXTURE`、`ACP2_MULTI_FIXTURE`、`ACP2_MIGRATION_FIXTURE`、`ACP2_PACKAGE_FIXTURE` 分别指已校验、冻结的实际 JSON 绝对路径，按对应任务交付，不得空填。Harness 输出均用 `$ACP2_EVIDENCE/B/Bxx/`，总汇总由 B 放 `$ACP2_EVIDENCE/integration/`。路径和变量存在不证明场景已通过。

### 2.3 每个任务的最低可执行验证命令

下表是各任务“验证”字段的可执行补充；须与本任务的断言、科学/客户端独立验收一起执行。`acp2_runtime.py`、`test_acp2_runtime_*.py` 及其场景为**拟新增，先按对应任务交付**；命令不意味着当前已存在。现有测试路径已经本次规划时检查存在。新 Harness 的 `--fixture` 内容由每任务先冻结；缺实际能力时返回 NOT_VERIFIED，不以空实现通过。命令从冻结候选仓库根、B 的 venv 执行。

| 任务 | 验证命令或实际交付后执行的命令 |
|---|---|
| B00 | `git show --no-patch "$ACP2_BASE_REV"`，其中变量为实际 debug 完整 SHA |
| B01 | `python scripts/acceptance/acp2_runtime.py --help`；`pytest tests/test_acp2_runtime_acceptance_harness.py -q` |
| B02 | `python -c 'import acp, cccp, acp_services; print(acp.__file__, cccp.__file__, acp_services.__file__, sep="\n")'`；`acp doctor` |
| B03 | `pytest tests/test_services_runtime_usecases.py tests/test_services_machine_output.py tests/test_services_submission_fidelity.py -q` |
| B04 | `python scripts/acceptance/acp2_runtime.py --scenario entry-readonly --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --out "$ACP2_EVIDENCE/B/B04" --timeout 120` |
| B05 | `python docs/reference/next-change/examples/acp_mcp_stdio_probe.py --launch "$ACP2_LAUNCH" --out "$ACP2_EVIDENCE/B/B05/stdio" --timeout 30`；`pytest tests/test_acp2_runtime_mcp_process.py -q` |
| B06 | `python scripts/acceptance/acp2_runtime.py --scenario mcp-stdio --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --out "$ACP2_EVIDENCE/B/B06" --timeout 120` |
| B07 | `python scripts/acceptance/acp2_runtime.py --scenario env-runtime --controller "$ACP2_CONTROLLER" --out "$ACP2_EVIDENCE/B/B07" --timeout 180` |
| B08 | `python scripts/acceptance/acp2_runtime.py --scenario submit-replay --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_SINGLEPOINT_FIXTURE" --out "$ACP2_EVIDENCE/B/B08" --timeout 600` |
| B09 | `pytest tests/test_services_admission.py tests/test_services_quota.py tests/test_services_entry_authz.py -q`；真实拒绝 Fixture 作为 B08/B17 场景必需行 |
| B10 | `python scripts/acceptance/acp2_runtime.py --scenario local-singlepoint --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_SINGLEPOINT_FIXTURE" --out "$ACP2_EVIDENCE/B/B10" --timeout 1800` |
| B11 | `python scripts/acceptance/acp2_runtime.py --scenario lsf-singlepoint --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_LSF_FIXTURE" --out "$ACP2_EVIDENCE/B/B11" --timeout 1800` |
| B12 | `python scripts/acceptance/acp2_runtime.py --scenario artifact --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_CONTROL_FIXTURE" --out "$ACP2_EVIDENCE/B/B12" --timeout 600` |
| B13 | `python scripts/acceptance/acp2_runtime.py --scenario control-recovery --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_CONTROL_FIXTURE" --out "$ACP2_EVIDENCE/B/B13" --timeout 1800` |
| B14 | `pytest tests/test_acp_services_isolation.py tests/test_architecture_invariants.py tests/test_services_doc_sync.py -q`；真实 H3 矩阵按 B10/B11/B13 命令与 C 驱动逐项签收 |
| B15 | `python scripts/acceptance/acp2_runtime.py --scenario multitenant --controller "$ACP2_CONTROLLER" --fixture "$ACP2_MULTI_FIXTURE" --out "$ACP2_EVIDENCE/B/B15" --timeout 600` |
| B16 | `pytest tests/test_acp2_runtime_approvals.py -q`；B17 的真实重启审批分支必跑 |
| B17 | `python scripts/acceptance/acp2_runtime.py --scenario approval-budget --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_APPROVAL_FIXTURE" --out "$ACP2_EVIDENCE/B/B17" --timeout 600` |
| B18 | `pytest tests/test_services_envelope.py tests/test_services_worker_contract.py tests/test_services_submission_fidelity.py -q`；B20 的契约/预检分支先于 QC |
| B19 | `python scripts/acceptance/acp2_runtime.py --scenario control-recovery --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_CONTROL_FIXTURE" --out "$ACP2_EVIDENCE/B/B19" --timeout 1800`，Fixture 含研究事件/游标恢复案例 |
| B20 | `python scripts/acceptance/acp2_runtime.py --scenario da-runtime --controller "$ACP2_CONTROLLER" --launch "$ACP2_LAUNCH" --fixture "$ACP2_DA_FIXTURE" --out "$ACP2_EVIDENCE/B/B20" --timeout "$ACP2_DA_TIMEOUT"`，超时按批准预算冻结 |
| B21 | `python scripts/acceptance/acp2_runtime.py --scenario infra-plugin --controller "$ACP2_CONTROLLER" --fixture "$ACP2_PLUGIN_FIXTURE" --out "$ACP2_EVIDENCE/B/B21" --timeout 1800` |
| B22 | `python scripts/acceptance/acp2_runtime.py --scenario infra-plugin --controller "$ACP2_CONTROLLER" --fixture "$ACP2_PLUGIN_FIXTURE" --out "$ACP2_EVIDENCE/B/B22" --timeout 1800`，Fixture 增加升级/回滚分支 |
| B23 | `python scripts/acceptance/acp2_runtime.py --scenario multitenant --controller "$ACP2_CONTROLLER" --fixture "$ACP2_MULTI_FIXTURE" --out "$ACP2_EVIDENCE/B/B23" --timeout 1800` |
| B24 | `python scripts/acceptance/acp2_runtime.py --scenario migration --fixture "$ACP2_MIGRATION_FIXTURE" --out "$ACP2_EVIDENCE/B/B24" --timeout 1800`，迁移专用 DB 不共享活动 Controller |
| B25 | `python scripts/acceptance/acp2_runtime.py --scenario package-install --fixture "$ACP2_PACKAGE_FIXTURE" --out "$ACP2_EVIDENCE/B/B25" --timeout 1800`，新环境 Controller 仍单实例 |
| B26 | `python scripts/acceptance/acp2_runtime.py --check-only "$ACP2_EVIDENCE/integration/summary.json"`；实际独立复现另按 B25/C 驱动和最终手册执行 |

## 3. P0：基线、环境、应用契约与真实启动

### B00 冻结起点和唯一 owner（H0）

**前置**：debug 已完成；收到其最终候选完整 SHA 和验收索引。

1. 读取 root、`src/acp_services/AGENTS.md`、`src/acp/api/AGENTS.md`、`src/acp/scheduler/AGENTS.md`、测试 AGENTS；记录 `git rev-parse HEAD` 和工作区修改，不覆盖他人变更。
2. 将 debug freeze SHA、部署 SHA、测试 Harness SHA 分别写入 `runtime/baseline-lock.json`；列出 debug 交付的唯一执行链驻点及 MCP/身份等延期项。移动中的当前 HEAD 不能替代完整 SHA。
3. 登记本文件 §1 的路径 owner、测试资源上限、各轨工作分支/候选；建立只读起点与独立集成分支，不重置主工作区。
4. 向 A/C 发 H0 receipt，后续契约修改只能升版本或增兼容字段。

**产物**：`runtime/baseline-lock.json`、总 `manifest.json` 初稿、共享文件 request 模板。**验证**：`git show --no-patch <freeze-sha>`；`git diff --check`；manifest 中各 SHA 可解引用。**失败断言**：缺完整 SHA、使用 dirty checkout 作冻结科学候选、两个轨占用同一共享文件均阻断 H0。**证据**：`E/B00/`。**交接**：A/C 立即从该基线启动独立领域工作；B 后续任务不等待 A 完成 P2 科学类型。

### B01 先交付验收协议和可执行 Harness 骨架

**前置**：B00。

1. 创建 `runtime/acceptance-harness.v1.md` 与结果 JSON Schema，根 schema 标识固定为 `acp2.acceptance.v1`；明确场景、输入 Fixture、owner、真实进程/网络/科学要求、前置、超时和清理。协议/模块测试与真实验收分列。
2. 创建拟新增 `scripts/acceptance/acp2_runtime.py` 和 `tests/test_acp2_runtime_acceptance_harness.py`。最先实现 `--help`、`--check-only`、`--dry-run`；真实操作未交付时返回 2 和具体缺口，不能返回伪造 PASS。
3. 冻结命令形态：`python scripts/acceptance/acp2_runtime.py --scenario <id> --controller <loopback-url> --launch <json> --fixture <json> --out <dir> --timeout <sec>`；具体场景无需的参数可省，其 required 集合在 `--help` 列明。
4. 场景依次登记 `env-runtime`、`entry-readonly`、`mcp-stdio`、`submit-replay`、`local-singlepoint`、`lsf-singlepoint`、`three-entry`、`artifact`、`control-recovery`、`approval-budget`、`da-runtime`、`infra-plugin`、`multitenant`、`migration`、`package-install`；DSH/浏览器由 C 的驱动通过总结果 Schema 接入。
5. 实现预检、总超时、PID/任务引用登记及 cleanup 回执；重用 debug real-environment 报告时记录来源哈希，不向其冻结 enum 添加 MCP/DSH 场景。

**产物**：Harness CLI/结果契约与可验证骨架。**验证**：`python scripts/acceptance/acp2_runtime.py --help`；`pytest tests/test_acp2_runtime_acceptance_harness.py -q`。**失败断言**：缺 Controller、mock 科学输出、缺环境、超时后未停止活动任务不能得到 PASS；dry-run 不创建 DB/进程/作业。**证据**：`E/B01/`。**交接**：A 提供科学 Fixture/预期，C 据统一 Schema 实现真实客户端驱动。

### B02 配置 Python、数据根和 ACP 测试配置

**前置**：B00；允许使用的绝对工具/源码/原生数据/证据路径已登记。

1. 实行 runbook ENV-01、02、04、09：核对 Python 3.10+、git/curl/tar/xz/SSH、磁盘 inode、权限、端口；建立隔离 worktree、Python venv，保存实际版本和包锁/来源。
2. 从冻结源码安装 `.[dev,api,remote]`；MCP 独立客户端测试依赖与生产依赖分清，B 接 C 的 SDK/pnpm request 后统一处理。打印三包 `__file__`，确认不继承旧 checkout 的 `PYTHONPATH`。这是沿用现有发行的早期环境，不要求先完成 P5 分包、完整 NMR 安装或开源政策决定。
3. 单独建 Controller cwd/config；使用现有 `acp init --config` 创建专用配置，审阅多源合并后有效科学软件/cluster/workroot。仅设 `ACP_RUN_ROOT` 不能屏蔽用户全局配置。
4. 建非敏感 `acceptance-env.sh`、受限凭据注入约定和本轮最大核数/内存/磁盘/模型及作业数量预算；新 shell 重启后复核导入和路径。
5. 所需原生文件系统路径不可写或依赖安装失败时先记录原因；需管理员权限或网络审批按会话规则处理，不换成生产目录绕过。

**产物**：`E/environment/b-runtime.json`、Python freeze/导入路径、脱敏有效配置摘要、环境复用脚本。**验证**：runbook §3.0—3.1 命令、`acp doctor`、新 shell 导入冒烟；`env-runtime` 场景。**失败断言**：导入非冻结源码、指向生产 DB/远端目录、慢 FS 当科学运行根、重新开 shell 丢配置均使环境行失败。**证据**：`E/B02/`。**交接**：向 C 提供 venv/数据/配置/证据路径，不代装 Node/DSH。

### B03 冻结同一应用用例和科学任务绑定（H1）

**前置**：B00；现有 singlepoint Task API 可读。A 尚未交付新 Recipe 不阻塞本任务。

1. 审核 `runtime/commands.py`、`contracts/injection.py`、`acp/services_bootstrap.py`、CLI `acp task` 和 HTTP 提交/控制/查询路径，画出每入口到同一管理器/Store 的调用表。
2. 在 `runtime/application-api.v1.json` 冻结 capabilities、preflight、submit、status、cancel、prepare、read、analyze 请求和结构化响应；引用包含可验证的 task/attempt、release、artifact generation/digest；错误区分重试、授权、参数、冲突和未准备。
3. 优先复用现有 machine output、Workspace/Runtime/Data 公共对象；确需新增平台 DTO 时放 `src/acp_services/contracts/`，冻结 dataclass，不拷贝 A 的科学 Schema。
4. 创建 `runtime/science-binding.v1.json`：现有 singlepoint 先映射到当前 TaskRequest/plan；A 新 Capability/Recipe 通过 schema ID/digest/version 引用，不让平台重写方法/电荷/自旋等科学默认或判断。
5. 在 H1 交 `runtime/mcp-launch.schema.json`、未填写真实路径的模板、拟新增 argv/cwd/env/Controller 约定，以及从现有模块提取的八工具 Schema/协议版本/无副作用约束；这是格式契约，不是 H2 可执行入口。写请求成功、未知字段、缺权限、冲突、工件未准备和科学不支持的正负样本；交 C 据样本开发界面与 RPH，输出可校验而非自由文本约定。

**产物**：应用契约、正负 Fixture、现有 singlepoint 绑定。**验证**：既有 `tests/test_services_runtime_usecases.py`、`test_services_machine_output.py`、`test_services_submission_fidelity.py`；新增契约样本校验纳入 B01。**失败断言**：入口各建 Submit 逻辑、平台偷偷改科学参数、响应伪造 completed/claim、无版本科学对象均拒绝。**证据**：`E/B03/`。**交接**：H1 给 A/C；A 扩科学字段时只升级引用映射。

### B04 交付测试 Controller 启停及进程拓扑

**前置**：B02、B03。

1. 复用 debug 完成后的服务组合根，仅为隔离测试提供明确的 cwd/config/runroot/端口启动清单；HTTP 服务在 loopback，例如 18765，生产 8765 不参与。
2. 通过当前 `acp run serve --help` 核实参数后按 runbook 启动；探测 health/只读任务列表；记录 Controller PID、启动时间、DB、runroot、release 和端口。
3. 冻结适配器连接方式。若现有 HTTP 不足承载同一应用用例，拟新增 `src/acp_services/entrypoints/controller_client.py` 与 `acp` 侧路由装配；client 只通过服务边界调用，不能导入管理器并启动线程。
4. 给 CLI/stdio/SDK 一个明确的 Controller target；如需新增 `acp task --controller` 或其他参数，先在 `runtime/application-api.v1.json` 冻结拼写，再实现及展示 `--help`，不把拟新增参数当现有功能。
5. 第二个 Controller 指向同 DB 必须在产生工作线程/提交前拒绝。正常关闭适配器不得关闭 Controller 或中断已提交计算。

**产物**：真实启动清单、连接配置及 PID/DB 拓扑。**验证**：`entry-readonly` 场景连续启动/退出两个适配器；查 Controller PID/任务数不变；既有隔离/锁守护。**失败断言**：import/discovery 启动 scheduler、适配器占管理锁、退出适配器杀死任务、同 DB 双 Controller 一律 FAIL。**证据**：`E/B04/`。**交接**：C 获一个可用 Controller，不自行重启或并启；A 通过 B 登记需要的作业。

### B05 交付真正可启动的 stdio MCP 及协议兼容

**前置**：B03、B04；C 提供冻结 DSH 客户端/SDK 协议能力信息。

1. 保留 `src/acp_services/entrypoints/mcp.py` 的八工具和类型化端口；拟新增 `src/acp_services/entrypoints/mcp_stdio.py` 负责逐行 UTF-8 JSON-RPC 读取/分发/写出。stdout 只有协议帧，日志用 stderr；EOF/信号正常释放本进程资源。
2. B 在 `src/acp/cli.py` 增加并冻结真正 CLI 启动项，建议形态 `acp mcp serve --transport stdio --controller <url>`；具体最终 argv 必须经 `--help` 验证并写实际 `mcp-launch.json`，不得依赖上述建议拼写已存在。
3. `acp` 侧组合根接入同一 Controller 应用客户端；Services 侧只接显式 Protocol/依赖。`initialize/tools/list/capabilities` 不产生科学任务、存储准备或付费操作。
4. 当前模块锁定 `2025-06-18`；与 C 实测 DSH 提供版本。保留旧支持版本或按 MCP 协商规则显式返回双方支持版本；双方无交集必须清晰失败，不忽略客户端版本。
5. 补进程级测试：真实子进程 framing、连续独立请求、畸形帧、unsupported method/version、长引用结果、stderr/stdout 分流、客户端断线。按冻结 MCP 版本明确拒绝未支持的 JSON-RPC batch，不为通过测试新增隐式 batch 支持。模块测试继续保留，不能代替独立客户端真实连接。

**产物**：MCP 默认启动入口、八工具 schemas、真实 launch JSON、协议矩阵/ADR-006 接口部分。**验证**：现有 `tests/test_services_mcp.py`；拟新增 `tests/test_acp2_runtime_mcp_process.py`；runbook 只读 `acp_mcp_stdio_probe.py`；真实官方客户端验收由 C 运行。**失败断言**：握手不兼容仍列 PASS、stdout 混日志、探测启动第二 Controller 或增加任务均 FAIL。**证据**：`E/B05/`。**交接**：C 用同一 launch 完成 DSH 官方插件握手；A 获能力 schema 引用。

### B06 MCP/HTTP/CLI 接真实应用，消除连接内状态权威

**前置**：B03—B05。

1. 实现 `McpBackends` 端口至 Controller 的真实适配；HTTP 与 CLI 的同一动作复用相同 RuntimeCommands/Data/Workspace 入口，不重新拼执行链。
2. preflight 委托 A 的静态科学检查或既有 Task 校验；返回 quote/问题清单但不提交、prepare、预留运行 CPU。
3. submit 返回持久 TaskRef；status/read 不更新任务状态、不触发远端传输；prepare/analyze 显式且授权；结构化 isError 与现有 machine output 一致。
4. 当前 MCP 进程内 `IdempotencyRegistry/WorkspaceBudget` 不能作跨进程权威。P1 前由 B08/B09 接 Controller 持久事务状态；进程内对象只缓存或测试适配，生产入口不允许绕过 Controller。
5. 以无科学进程的 read-only 场景先交付真实端到端，后续任务再开启小计算；CLI 原直接 `acp run` 的兼容科学运行不强制注册新平台任务。

**产物**：真实后端绑定、入口到用例的 single-owner 表、错误一致性样本。**验证**：`entry-readonly`、`mcp-stdio`；现有 usecase/entry authz/read-root 测试。**失败断言**：工具返回 fake 任务、GET 触发远程拉取、长任务堵住 stdio 等待完成、三个入口产生不同参数均失败。**证据**：`E/B06/`。**交接**：C 可完成 H2 发现和 P1 行为开发；A 接契约端到端预检样本。

### B07 准备真实科学环境并关闭 P0（H2）

**前置**：B02、B04—B06；C 交 ENV-03/05/08 及真实 DSH 握手日志；A 给小型 XYZ 和支持条件。

1. 明确 Local 科学软件路径/版本、依赖/许可证、核数/内存上限；按 runbook ENV-06 运行 `acp doctor`，登记缺口，禁止 fake binary 当 QC 实测。
2. 审核测试 LSF 节点、SSH host identity/凭据、队列、远端 Python/软件、release root 和独立工作目录。对指定测试节点执行 doctor/轻量只读版本命令，不先投大作业。
3. 建 `fixture-runs.json`，字段含 fixture/input digest、backend、request owner、预算、task/attempt/release、状态、证据 owner、哪些消费方可复用。任何昂贵 Fixture 由 B 原子登记后提交一次。
4. 汇总 B/C 环境表与 C 的真实 MCP 客户端握手；每个必需行有原始日志及结果，P0 缺项不能关闭。可向下一阶段交付 Fixture 开发，不冒称 P0 通过。

**产物**：本机/LSF 环境摘要、科学执行预算、共享作业登记与 H2 receipt。**验证**：`env-runtime`；C 的官方 SDK/DSH 真实握手与探针结果；版本/路径重新激活校验。**失败断言**：Node/DSH 可运行却 MCP 不兼容、缺真实 LSF、环境变量存在但二进制不可执行不能记就绪 PASS。**证据**：`E/B07/`。**交接**：A 仅负责科学预期，C 驱动模型/浏览器；B 为下阶段唯一 Controller 操作者。

## 4. P1：持久平台语义和 Local/LSF 三入口闭环

### B08 将幂等提交和未知结果对账放到 Controller

**前置**：B06；应用契约 v1。

1. 在 `acp_services/store/migrations.py` 增量迁移持久请求记录（如确需，拟新增 `runtime/idempotency.py`）；复用当前 DB/事务机制，不让 MCP 自建第二 jobs DB。
2. key 至少关联 verified principal/project/action/idempotency key；保存规范请求 digest、处理状态、TaskRef/operation ref。相同 key+payload 复用，same key+different payload 返回明确冲突，权限范围变化重新授权。
3. 将请求占位、唯一约束、持久任务创建与引用绑定放在可恢复事务/明确 outbox 边界；服务断在提交前/后/响应前均能查询原结果，未知远程提交沿既有 submission_id 对账。
4. 同时用两个独立 MCP 子进程并发重放，退出它们后再启动；Controller 重启保留 DB，再重放同 key。观测实际本地启动/远程 bsub 次数。

**产物**：持久幂等实现、迁移、冲突/恢复契约。**验证**：`submit-replay`；拟新增 `tests/test_acp2_runtime_persistent_idempotency.py`；现有 CAS/submission 守护。**失败断言**：跨进程重放新增任务、同 key 不同载荷被收养、重启后重复 bsub 均 FAIL；无外部 exactly-once 保证时先对账而非宣称魔法幂等。**证据**：`E/B08/`。**交接**：C 固定重放/恢复提示词与 session 用同 key；A 不做科学结果级别的第二任务去重。

### B09 强制本地身份边界与单次资源准入

**前置**：B03、B08；P1 仍是明确受限的 local-single scope。

1. 接 `workspace/entry_authz.py`、`identity_mapping.py`、`runtime/admission.py` 的现有实现；身份源由 Controller 认证取得，工具 arguments 或 `_meta` 中 project/principal/role 不构成认证或授权。
2. 将 task/cores/memory/walltime/storage 上限在持久提交/dispatch 边界检查；相同幂等请求不重复 reservation；失败/取消按现有 ledger 释放一次。
3. 将 MCP 进程内 budget 从最终准入路径剥离；工具级 hints 不能覆盖服务端 grant；不要把无真实 hard limit 的环境标注为 quota enforced。
4. 为 P2 多用户铺可兼容字段，本阶段不把 local stdio 环境变量当成安全的远程 API Key 多租户系统。

**产物**：真实单次准入接线、本地身份范围和拒绝错误样本。**验证**：现有 admission/quota/entry authz/API key 模块测试；`approval-budget` 的 P1 子集与真实请求拒绝。**失败断言**：超核数/超任务/越项目请求开始 QC、重放双预留、拒绝后产生工作目录/提交均 FAIL。**证据**：`E/B09/`。**交接**：C 界面展示服务端拒绝/预算，不在 RPH 自封授权；A 科学参数无商业等级字段。

### B10 真实 Local singlepoint 基线与三入口引用

**前置**：B07—B09；A 的已冻结小分子 XYZ/方法支持与科学预期；C 客户端可调用。

1. 先核实 debug `scripts/acceptance/real_environment.py --help`，复用其 m1-singlepoint 作独立环境基线；该脚本自启动的临时 Controller 使用独立 DB，不连共享 H3 Controller，不与其同时抢资源。
2. 在共享作业登记新行，用真实 MCP（C 驱动）提交固定 Fixture 一次；B 记 TaskRef 后只查询，不再执行 `acp run singlepoint` 作为同 ID 核对。
3. 核对真实 ORCA 输出、TaskResult/Receipt、attempt/release、完成条件和 manifest；A 判断科学输出/单位有效，B 判断平台执行和工件完整。
4. C 从 Workbench，B 从 CLI/HTTP 读取相同任务；复用 task/attempt、plan identity、manifest digest，不只对比显示名和 completed 文本。

**产物**：单个共享真实任务及同 ID 结果样本。**验证**：`local-singlepoint`、`three-entry`；debug harness 报告另附哈希，不混同任务。**失败断言**：科学进程未运行、CLI 另建任务、三入口 digest/attempt 不一致、COMPLETED 但必需产物缺失均 FAIL。**证据**：`E/B10/`。**交接**：A 签科学部分，C 签真实工具与页面，B 汇总 H3 Local 行。

### B11 真实 LSF 执行、控制、对账和结果归集

**前置**：B07—B10；隔离远端目录及可用测试队列。

1. 先用 debug `lsf-singlepoint` Harness 核对其自身已交付语义；如其自起服务则用独立资源窗口，与共享测试 Controller 区分。
2. 对共享 Controller 的远端任务登记 fixture/backend/request，真实提交前核对 release_id、scheduler markers、submission_id 和确认路径；交 C 发唯一 MCP 提交。
3. 保存实际 SSH/bsub/bjobs 原始命令、退出码、远端执行环境和 receipt；对指定测试作业实施 bstop/bresume/取消或专门分支 Fixture，并确认观测状态。
4. 执行断线与提交未知对账；只按既有恢复逻辑收养同 submission/attempt，不新建“兜底提交”。终态后显式 prepare/拉取并校验摘要和 generation。
5. 核对 remote read-root 与 cache-only GET、旧 attempt 拒绝、远端取消确认；每项独立断言，任务最终完成不能掩盖中间失败。

**产物**：真实 LSF 完整操作/控制/归集证据。**验证**：`lsf-singlepoint`、`control-recovery` 的 LSF 子集；debug harness 原始报告。**失败断言**：只有生成命令无真实执行、缺 bsub 回执、未知提交直接重投、未确认停止先 CANCELLED、直接读本地 record.work_dir 均 FAIL。**证据**：`E/B11/`。**交接**：C/CLI/浏览器复用该 TaskRef；A 审最终科学产物；缺 LSF 是 NOT_VERIFIED，H3 不关闭。

### B12 工件、prepare/read 和证据引用完整性

**前置**：B10/B11 至少已有真实产物，A EvidenceReport 草案可后接。

1. 复用 `data/operations.py`、`resource_read.py`、`read_root.py`、`transfer.py`；冻结 ArtifactRef 相对路径、schema/version、digest、generation、大小、availability 与权限投影。
2. MCP/HTTP 的 prepare 返回持久 operation ref；read 严格 cache-only，不隐式拉取或启动科学分析。analyze 仅显式调用 A 已登记分析能力。
3. 测试未 prepare、损坏摘要、旧 generation、越界路径、权限撤销、远端缓存缺失；工件缺失明确返回，不生成替代空结果。
4. 科学 EvidenceReport 不由平台解释为 claim supported；平台只存储/发布/授权其 schema 和来源引用，真实科学判断留给 A。

**产物**：真实工件引用、准备/读取错误矩阵及权限证据。**验证**：`artifact`；现有 read-root/prepare/transfer tests；三入口同摘要。**失败断言**：GET 出网拉取、相同引用映到另一 generation、路径遍历、损坏工件仍被读作有效、MCP 把日志当 EvidenceReport 均 FAIL。**证据**：`E/B12/`。**交接**：C 通过 ResourceStore 和 RPH 读引用；A 交科学 manifest 需求 request，B 不复制 parser。

### B13 基础取消、断线、重启及发布恢复

**前置**：B08—B12；B01 已有可控故障点与清理。

1. 分别故障注入 MCP 进程 EOF、DSH 断连、测试 Controller 重启，保留同 DB/config/release；重新握手只恢复客户端，任务由唯一 Controller 继续管理。
2. 使用专用廉价任务检查 pause/unpause/cancel 竞争、late/旧 attempt receipt；保持现有 CAS、确认停止、终态不复活语义，不改 debug 已冻结规则。
3. 在真实 QC 结束后制造 publication 失败，保存科学产物/receipt 和计数；解除故障仅重试发布，证明科学启动或 bsub 次数未增加。
4. 每次失败都记录任务引用和远端状态；超时后平台 cancel 并对账，不以杀客户端进程当成取消计算。

**产物**：P1 故障矩阵与无重复计算证据。**验证**：`control-recovery`；现有 recovery/receipt/CAS tests；C 自动重连日志。**失败断言**：适配器重连生成任务、旧 receipt 改终态、publication 失败重跑 QC、cancel 返回即视为已停止均 FAIL。**证据**：`E/B13/`。**交接**：C session/页面恢复引用，A 检查科学结果没有被假恢复；B 将新增回归发原执行链 owner 定点返修。

### B14 P1 冻结集成与能量/优化/频率覆盖（H3）

**前置**：B08—B13；A 交注册的 singlepoint/optimize/frequency；C 交真实模型、页面/CLI 同任务证据。

1. B 单操作者按 A→B→C 兼容依赖顺序合入候选；共享文件由 B 应用 request，生成新完整 SHA 和环境/契约 digest。禁止在运行候选上边改边验。
2. 每个已宣称的 P1 capability 至少有对应真实 Fixture 和科学预期；单点完成不能外推优化/频率完成。Local/LSF 核心链分别实跑，具体能力×backend 覆盖表逐行列出范围，未跑组合不声称支持已验。
3. 先执行架构/隔离和各轨单元回归，再由 B 启唯一 Controller，C 驱动真实 DSH/浏览器，A 消费产物审核科学断言。
4. 汇总 T0—T5、历史结果和 15 active workflows 兼容守护；必需项 FAIL 或 NOT_VERIFIED 不关闭 H3。

**产物**：P1 候选锁、可复跑证据索引、H3 receipt。**验证**：§7 基础回归、实际 Local/LSF/DSH/browser 场景；结果 Schema 校验。**失败断言**：只跑 mock、用一个案例冒充全能力、不同 SHA 的证据拼成同候选、未消费 request 均拒绝冻结。**证据**：`E/B14/`。**交接**：给 A/C 一个冻结集成 SHA；P2 从此增量推进，不撤销现有兼容面。

## 5. P2：完整审批、累计预算、科研闭环和恢复

### B15 多用户授权与可撤销访问的真实接线

**前置**：H3；现有 Workspace identity/key/grant 模块。

1. 复用 `workspace/identity_lifecycle.py`、`keys.py`、`entry_authz.py` 和 `contracts/access.py`；建立测试用户/项目/角色与真实 HTTP authentication-to-principal 接线，默认配置保持明确 local-single。
2. 每次 submit/control/read/prepare/事件订阅重新做资源授权；以 Controller 查到的任务/project scope 为准，用户输入 scope 不自证。
3. 明确 stdio 代理身份的绑定与权限上限；多用户环境不共享可任意提权的 MCP launch。远程身份 Provider 未实现则 capabilities 明示不支持。
4. 实测另项目 TaskRef、API Key scopes、撤销密钥/项目访问后继续读取与事件订阅，报告不泄露任务是否存在等敏感差异。

**产物**：真实认证授权链和撤销矩阵。**验证**：`multitenant` 的授权子集；既有 identity/API key/events authz tests；真实网络请求。**失败断言**：伪造 workspace_id 提权、撤销后读缓存/收事件、MCP 请求自带角色生效均 FAIL。**证据**：`E/B15/`。**交接**：C 在界面/RPH处理 denied/expired，不缓存授权判断；A 无账户依赖。

### B16 持久 Proposal 审批与冻结计划授权

**前置**：B15；A-14/A-15 的科学计划 digest，C01 的 ResearchProposal/approval Schema。只等契约，不等 C18/C19 的实际客户端实现或 H4 总签收。

1. 拟新增 `src/acp_services/runtime/approvals.py` 与必要 store 迁移；保留现有 action gating，审批记录引用 proposal digest、科学计划 digest、预算、项目、审批人、过期时间及 revision。
2. 明确 draft/approved/rejected/expired/revoked 的持久行为；审批与 task execution 是不同事实，不加第二任务状态机或新 JobStatus，不挪用 `WAITING_REVIEW`，不改 `resume()` 的 review-only 语义。审批/预算有独立持久记录，用户 approve 入口通过当前认证 principal。
3. 提交前检查批准的 immutable digest；模型/Recipe/输入/预算变化使旧批准失效。断线/服务重启后查询同审批，不由聊天回复“同意”替代服务端批准。
4. 将 request/query/approve/reject 的错误与事件升入版本契约，C 提供界面，B 执行授权；科学合理性由 A 校验，不让审批自动使无效科学计划合法。

**产物**：持久审批 API/迁移、计划绑定、正负 Fixture。**验证**：`approval-budget` 的 approval 分支；拟新增 `tests/test_acp2_runtime_approvals.py`；真实重启后重复 approve/submit。**失败断言**：未批准/过期/换计划/另一用户批准仍 dispatch、模型一句自然语言绕过批准均 FAIL。**证据**：`E/B16/`。**交接**：C RPH/Workbench仅引用批准记录，A Recipe只描述科学步骤与证据。

### B17 累计预算、预留、可信用量与结算

**前置**：B09、B16；B15 多用户范围冻结。

1. 扩充现有 `admission.py`、`quota.py`、`usage.py`、`metering_policy.py` 接线，使按项目/研究轮次预算、queued reservation、dispatch 复验可在 DB 重启后恢复。
2. 资源 quote 输入引用 A 计划和 C 研究预算；服务端决定有效上限，批准只授权相应范围。模型 token 预算与科学 CPU/存储预算分项，外部 LLM费用若由 DSH管理只记可信来源，不伪造统一计费。
3. 用 receipt/可信运行证据结算，重复收据/重复事件不能重复扣费；失败/取消/发布等待各定义何时释放，发布失败不重复收计算费用。
4. 并发两个请求争抢最后额度，跨 Controller 重启重复结算；商业/账户字段不进入科学 TaskRequest 或算法结果。

**产物**：持久研究预算与用量账、明确 hard/soft/advisory 支持声明。**验证**：`approval-budget` 的 budget 分支；现有 admission/quota/usage/metering tests；真实端到端拒绝。**失败断言**：超额可先 dispatch 后补拒绝、重放双扣、重启额度重置、余额改变科学算法均 FAIL。**证据**：`E/B17/`。**交接**：C展示服务端 quote/remaining；A 只给科学成本属性，不处理付费策略。

### B18 接 A 的科学 Recipe 与证据发布，不复制科学判断

**前置**：A-02～04、A-15/A-16 已交 ReactionSpec/QuestionSpec/Recipe/EvidenceReport 的类型、DAG 与验证器；B16/B17；C01 的研究记录 Schema。不等待 A-17～20 的真实 DA 结果或 H4 总签收。

1. 在 `runtime/science-binding.v1.json` 增量登记 A 的 schema/version/digest；通过公开 Task/Recipe 接口冻结到现有 plan/envelope，解析纯科学 DAG，不实现第二 Recipe 执行器。
2. 在现有允许的工作流/Task dispatch 上接 TS/频率/IRC，按每工作流证据逐个启用 frozen worker；legacy 仍需保留时明示，不偷改全15个为统一新链。
3. 将 EvidenceReport、原生文件、科学来源/限制注册为 ArtifactManifest，保留支持/反驳/不确定/未评估的原始状态；task completed 只表示执行完成。
4. 将 plan identity、software/plugin release、输入/原子映射摘要与 worker receipt/result 串联。科学前置条件拒绝必须发生在昂贵提交之前。
5. 在既有 Data 项目资源边界接版本化 JSON 资源的 create/read/CAS update，保存 C 所拥有的 Proposal/ResearchRecord；冻结 resource ID、schema、revision、摘要、授权、冲突与撤权语义。Data 不实现研究决策，不另建任务表。此接口先交 C18，使 C18 的记录实现不必等待 C20/C24 的真实研究完成。

**产物**：DA Recipe 平台绑定和证据可读引用。**验证**：`da-runtime` 的无计算契约/预检分支；现有 envelope/worker/lineage/step requirements 守护。**失败断言**：平台代A宣称 TS 连通、缺必需 upstream 仍 completed、输入映射不符收养历史结果均 FAIL。**证据**：`E/B18/`。**交接**：A 签科学原文，C消费证据视图和研究决策，B只保证执行与引用。

### B19 跨研究会话的事件、等待与恢复

**前置**：B08、B12、B16—B18；C01 已交 ResearchRecord 版本契约，C18 可与本任务并行实现。事件/恢复接口先交付，C22 的真实恢复记录随后联合验证，不倒置为本接口的前置。

1. 复用 `runtime/events.py`、`outbox.py`、`projections.py`、`recovery.py`；冻结 event ID、cursor、task/attempt、revision、project scope 与 payload version。持久事件重放和当前快照共同恢复，不靠临时 WebSocket 消息。
2. B 维护 task/approval/budget 权威，C 独立存 ResearchRecord 的问题/提案/轮次及任务引用；两边不双写同一业务状态。
3. 冻结 cursor 过期、重放/乱序/重复、授权撤销、RPH 退出后任务完成的行为；消费者须幂等，服务端 projection 不受 stale event 覆盖。
4. 与 C 做真实 DSH 终止→Controller继续运行→新会话恢复；额外做 Controller重启与消息重复，对账后只产生一次后续批准/提交。

**产物**：事件/快照恢复契约与跨进程证据。**验证**：`control-recovery` 的 research 分支；现有 events/order/authz tests；C 的真实 session/headless 日志。**失败断言**：聊天 Session 即项目权威、游标丢失导致重复 submit、旧事件恢复终态、断线保持昂贵 LLM空转等待均 FAIL。**证据**：`E/B19/`。**交接**：C 消费事件与引用，A 结果证据只读。

### B20 限定 Diels–Alder 真实闭环与 P2 冻结（H4）

**前置**：B15—B19；A-14～16 的 DA 契约/判据及 C20～23 的可用客户端，Fixture/预算冻结。运行可与 A-17～20、C24 联合分步完成；拿到 A-20 与 C25 的本轨签收后再关闭总 H4，不要求先有完整 H4 报告才启动场景。

**子交付**：**B20a** 接 C24a 的唯一提交，运行并提供逐步 receipt/授权工件，供 A-17～19/C24b 消费；**B20b** 收到 A-20/C25 后汇总 H4。C25 只依赖 B20a 的原始事实，不等待 B20b。

1. A 提供限定输入、合理方法、TS/虚频/IRC/端点映射/能量可比性预期；B/C 共同确定可支付的任务数量和超时，先登记不完整科学输入的拒绝案例。
2. B启动唯一冻结测试 Controller，C从真实DSH提出/请求审批/提交，按登记执行DA小切片；同一任务由Workbench/CLI观察。任务长于推理窗口时暂停研究循环。
3. A审科学EvidenceReport；B审worker/release/attempt/manifest/预算；C审证据反馈/ResearchRecord。科学证据不足允许inconclusive，不能把不支持的命题改为supported过门。
4. 用独立低成本案例覆盖拒绝批准、预算超限、发布失败、DSH/Controller恢复；不要求每次故障都重跑整个DA昂贵链。
5. B按 §6 集成规程冻结P2；记录“真实科学链已执行”与“命题得到支持”两个不同断言。

**产物**：H4候选、DA科学/执行/研究三方签收与复跑步骤。**验证**：`da-runtime`、`approval-budget`、`control-recovery`；C真实模型和浏览器；A真实科学验收。**失败断言**：只有脚本生成计划无真实QC、单虚频冒充IRC连通、未审批/超预算继续、证据不足却声称机制正确均FAIL。**证据**：`E/B20/`。**交接**：给A/C冻结SHA和可复用研究记录；科学不足由A/C按缺口补证，不由B改标签。

## 6. P3—P5：具体扩展与交付

### B21 基础设施 Adapter 契约、发现与隔离（P3）

**前置**：H4；既有 Local/LSF稳定且科学Plugin契约由A交付。

1. 将现有 backend/storage/transfer/identity 能力以平台 AdapterDescriptor 描述；拟新增 `src/acp_services/contracts/adapters.py`。字段含ID、版本、支持schema、环境要求、资源限制与验证Fixture；不放科学parser。
2. 发现只读元数据，安装/启用需授权；运行任务冻结版本和摘要。保留现有Local/LSF适配，不为插件目录重写控制器。
3. 拟新增 `src/acp_services/runtime/adapter_registry.py` 与必要组合根，将验证过的独立插件接入；至少一个外部安装的基础设施适配器在不改核心代码下运行。若宣称Slurm支持，必须在真实Slurm环境执行提交/取消/恢复，缺环境列未验证，不借LSF结果代签。
4. 检查错误版本/缺环境/能力冲突/元数据触发执行；旧任务仍以冻结Adapter读回。

**产物**：平台Adapter规范、外部插件样例、兼容矩阵。**验证**：`infra-plugin`；实际插件安装/发现/启用/任务操作；既有Local/LSF回归。**失败断言**：发现插件执行任意入口、未知版本仍自动替换、科学逻辑混入平台Adapter均FAIL。**证据**：`E/B21/`。**交接**：A/C使用各自registry，按版本引用平台支持条件。

### B22 插件升级、回滚与P3集成（H5）

**前置**：B21、A第三方能力Recipe、C动态表单/Skill发现。

1. 为A/C的依赖request应用共享打包/entry_points变更，审运行时依赖是否需同步节点requirements；插件trust不因可导入就提升。
2. 对同一旧任务：冻结插件v1→安装v2→只读历史工件→新任务v2；验证撤销/删除依赖时的明确错误和可用只读路径。
3. 运行“第三方新增能力，核心源码不改”的真实案例；科学Fixture由A定义，C动态发现并调用，B只做权限/准入/固定版本/执行。
4. B冻结H5总manifest，保留各插件notice/license/来源/摘要与版本回滚步骤。

**产物**：P3候选、插件验证/升级/回滚矩阵。**验证**：`infra-plugin`及A/C真实插件E2E；wheel安装检查。**失败断言**：插件v2覆盖运行中任务v1、卸载导致旧项目完全不可读、未知License标作已许可均FAIL。**证据**：`E/B22/`。**交接**：A/C签自己插件范围；未验证新Provider/Slurm不列PASS。

### B23 多项目多用户并发和跨领域持久一致性（P4）

**前置**：H5；A交NMR/热化学能力Fixture、C交相应Feature。

1. 设计两用户两项目并发矩阵：同名任务/结构、幂等key、共享节点额度、跨项目读/取消/prepare、权限撤销与并行事件。
2. 在预算内执行跨领域小Fixture，检查统一TaskRef/artifact/provenance/预算语义；不在平台重新实现NMR或热化学解析。
3. 比较Local/远端缓存读、批量列表/详情/事件、重复订阅的资源和DB开销；只报同机器/样本/配置下的实测数，未测指标置null。
4. 验证租户/项目隔离和强制存储/资源边界；hosted尚无可执行硬限额时明确能力限制，不把lab配置写成hosted已验收。

**产物**：多用户跨领域一致性/隔离/资源报告。**验证**：`multitenant`；A/C的NMR/thermo真实场景；现有query budget/isolation/event守护。**失败断言**：同名/同key串项目、跨域科学单位丢失、用户撤权仍收工件、资源超额静默越过均FAIL。**证据**：`E/B23/`。**交接**：A签科学可比性，C签不同Feature只消费同一平台事实。

### B24 迁移、备份恢复和可靠性冻结（H6）

**前置**：B23；实际迁移版本、备份来源和恢复目标均为测试数据。

1. 从冻结旧DB/manifest/recovery Fixture迁移至候选；保存迁移前备份、schema version、数据计数和工件摘要，不改历史权威证据。
2. 演练中断迁移、DB恢复、失联worker、旧/重复receipt、publication pending、cursor过期；保证任务身份/控制意图/幂等/审批/账本恢复一致。
3. 跑debug完成后保留的F0/S0四组合兼容Harness及现有历史reader；根据P4改动补受影响组合，不用当前旧路由冒充旧实现。
4. 写恢复与回滚操作手册、明确可逆/不可逆步骤和最旧支持版本；B冻结H6，A/C检自己的schema/视图/研究记录可读。

**产物**：P4候选、备份恢复/迁移/可靠性矩阵。**验证**：`migration`、四组合Harness、existing backup/restore/recovery tests；真实故障进程。**失败断言**：丢审批/预算/幂等记录、终态复活、损坏旧工件被静默跳过、用删除DB代替恢复均FAIL。**证据**：`E/B24/`。**交接**：给A/C可用迁移Fixture与恢复引用，缺证据留NOT_VERIFIED。

### B25 独立安装包、节点包与发行文档（P5）

**前置**：H6；ADR-005的发行/开源边界、A-27 先交的打包规格与 C32 的 RPH/静态资产已交付。先构建实际候选供 A-27/28、C33/34 安装验证，不等待 A-29/C35 的最终签收才构建。B先提交具体可审查政策/许可决定稿；未确认不得替项目变更许可证或宣称完整开源。既有许可下的本地安装/兼容验证仍可先行，不倒置为P1依赖。

**子交付**：**B25a** 消费 A-27a/C32 后构建实际包并发布摘要/锁；**B25b** 收到 A-27b/A-28 与 C33/C34 的安装/操作证据后汇总包验证。A/C 的安装不等待 B25b，也不以最终 H7 为前置。

1. B应用A/C已审request到pyproject/extras、节点requirements、console scripts、CI；新增依赖按科学节点/控制端/DSH/浏览器分类，不能把DSH/浏览器拖进CCCP独立安装。
2. 在全新venv/干净数据根构建并安装wheel；验证CCCP独立科学调用、Standalone CLI、本服务器Lab服务、MCP入口；保留现有acp命令和兼容imports。
3. 检查wheel/资产/notice/license、样例配置、schema、SDK与文档随发行可获取；若A科学样例无需ACR，应确实可在不启Controller下运行。
4. 更新统一安装/升级/LSF节点/真实MCP/DSH/审批预算/恢复手册、三轨receipt、版本清单和Services六列表；无上线授权不动生产service，生成可审发布候选。

**产物**：Standalone/Lab发布包、依赖锁/来源、运行指南和节点安装清单。**验证**：`python -m build`（先装冻结构建工具）；`package-install`；干净环境真实握手/小计算；四处version一致性。**失败断言**：依赖旧checkout才可import、漏资产、节点缺新增import、版本只改一处、安装CCCP强制要DSH均FAIL。**证据**：`E/B25/`。**交接**：A/C各自独立安装验证，B保存用户从零复现步骤。

### B26 外部复现、最终冻结和交付（H7）

**前置**：B25、A/C阶段签收；范围内所有required行有结果。

1. 按用户视角由干净shell/目录执行文档，不使用作者临时环境；独立操作者可由另一轨完成，C驱动真实DSH/浏览器，A审科学预期，B只管理集成服务/权限/资源。
2. 验收第三方插件安装、新手手工入口、Agent研究入口、历史只读、Local/LSF、授权/预算和断线恢复；每项引用真实运行报告，禁止用新文档声明代替运行证据。
3. 汇总H0—H7完整SHA、schema digest、依赖版本、已测矩阵、NOT_VERIFIED/DEFERRED、复跑命令和清理情况；A/C未签范围不得代签。
4. 产出最终发行候选/变更说明/升级回滚路径；任何产品部署由用户此前明确授权及发布门决定，本计划自动执行不包含重启生产acp.service。

**产物**：H7总交付、第三方复现报告、完整文档和可审发布候选。**验证**：契约/报告Schema校验、干净安装实测、阶段required行全PASS、无遗留未对账活动作业。**失败断言**：外部复现依赖未记载手工操作、最终SHA与证据不符、FAIL改成NOT_VERIFIED、仍有失控测试任务均阻断交付。**证据**：`E/B26/`。**交接**：向用户与A/C给一份能直接复跑的最终索引，生产发布作为独立有授权的操作。

## 7. 集成波次、失败返修与资源清理

### 7.1 B 作为唯一集成操作者

1. **H0/H1并行**：A做科学schema，B先用现有singlepoint稳定平台入口，C用正负Fixture做RPH/Workbench；任何轨不等全栈完成才开始自己的工作。
2. **H2早联调**：先合B的启动契约/入口和C的冻结DSH配置，只做真实握手发现；不等Recipe/DA。C先启动DSH/SDK适配进程，B检查Controller/DB/任务计数，无计算副作用后才放行。
3. **H3平台闭环**：A小Fixture/Registry→B绑定/幂等/准入→C真实工具和视图；B在合并request后生成冻结候选。B启单Controller，C驱动同TaskRef；真实QC作业都写共享登记表。
4. **H4科学闭环**：A的DA契约及预期→B审批/预算/证据/恢复→C研究循环/证据视图；每轨签自己断言，长任务由Controller持久管理。
5. **H5/H6/H7扩展与发布**：每波先契约兼容，再包安装，再真实场景；不得一次合所有未经验证的大改后尝试整体验收。

候选记录包含 `candidate_id`、完整git SHA、A/B/C变更集SHA、debug基线SHA、契约manifest digest、环境lock digest。应用修复必须生成新候选；旧PASS仍归旧候选，只有确认未受影响的证据可通过显式映射复用。停止当前实测→取证→按owner返修→合新SHA→重跑受影响场景及关键握手/引用链；不在正在验收的worktree热改代码。

### 7.2 失败返回正确 owner

| 失败 | 返修 owner | B 的动作 |
|---|---|---|
| 科学schema/单位/电荷自旋/TS/IRC/Recipe预期 | A | 保留任务工件，提供输入digest/release/receipt，禁止改科学结论 |
| DSH profile/模型/Tool Bridge/ResearchRecord/浏览器/视图 | C | 保持同Controller/TaskRef，交协议/HTTP日志，禁止并开第二Controller |
| MCP进程/平台应用/权限准入/幂等/工件/恢复 | B | 修所属代码、升级契约兼容说明并重验负向断言 |
| debug已经修好的执行链再次回归 | 对应原实现owner；B协调 | 给最小重现和失败CAS/receipt证据，定点修复，不新造执行器 |
| 共享依赖/CI/版本/发行/Services台账 | B | 应用A/C明确request，记录谁请求、为何、验证结果 |

### 7.3 超时、断网与正常退出

每个真实场景先设总超时、最大任务数、单任务资源和模型用量上限；Harness记录自己创建的PID和TaskRef/远端submission_id。超时不是自动通过，也不是把所有进程粗暴pkill：先保存日志→用平台取消测试active任务→确认Local进程组/LSF终态或明确pending reconciliation→导出原生文件/manifest/digest→退出C的DSH/MCP进程→由B停止测试Controller。网络断开无法确认远端停止时保留存储和对账记录，场景失败或未验证按事实区分，不删库或释放“已确定停止”的额度。清理只针对本轮拥有的测试资源，不碰生产service、数据根和其他轨作业。

### 7.4 最低回归与证据签收

在读过测试AGENTS且环境就绪后，B按改动选择既有守护；必需基础组如下，新增真实进程测试另列。它们通过只证明模块/架构，不取代H2/H3/H4实测：

```bash
pytest tests/test_acp_services_isolation.py tests/test_architecture_invariants.py -q
pytest tests/test_services_mcp.py tests/test_services_runtime_usecases.py -q
pytest tests/test_services_worker_contract.py tests/test_services_cas_fidelity.py -q
pytest tests/test_services_doc_sync.py tests/test_services_closure_record.py -q
```

合并候选按CI要求执行 `pytest -m "not slow"`；真实Local/LSF/模型/浏览器按冻结Harness单独执行，慢测按其显式门控开启。只修文档或共享元数据不重复跑昂贵科学链；新代码/依赖/科学配置/入口协议改变时重跑受影响真实场景。服务台账保持六列，结果token正确，NOT_VERIFIED不可改写为PASS。

**B 最终交付的判断**：同一冻结源码/依赖/配置组合确实可在本服务器安装并运行；MCP/HTTP/CLI使用唯一Controller；Local/LSF科学作业、持久幂等、审批预算、工件和恢复有真实证据；A/C可只凭契约和文档复现，且全部已宣称支持范围可逐行追溯。

## B00 sync (2026-10-22)

**Baseline reading rule.** `3670790363c7a1959ad1dbe4b4297adec518952a` is the historical debug delivery revision; `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798` is the historical code candidate. Neither is the start baseline. The start baseline is `final_debug_candidate = sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`, a frozen source overlay (not a Git commit); it is materialized by applying `docs/reference/next-change/acp2-contracts/runtime/final-debug-patch/` in `apply-order.md` order to a clean `096cdff` worktree. The current dirty checkout is never the baseline. B02 commits the applied bundle and records the resulting full SHA in `runtime/baseline-lock.json`. The candidate digest is inherited from `reports/architecture_repair_20261022/REPORT.md` with `verification: partial`; all 1,906 per-file SHA-256 values were independently verified (`1906/1906`).

**Path ownership.** A owns `src/cccp/**` and science contracts under `acp2-contracts/science/**`; B owns `src/acp/**`, `src/acp_services/**`, `packaging/**`, `acp2-contracts/runtime/**`, the contract manifest and the runtime acceptance tooling; C owns `frontend/**`, `rph/**` and `acp2-contracts/experience/**`. Shared files (the list is authoritative in `manifest.json.shared_files`) are B-single-write: `pyproject.toml`, `requirements-node.txt`, CI, the four version locations, root/nested AGENTS, `scripts/install_systemd.sh`, the Services ledgers and the contract manifest. Old lane names map as A: frontend→science, B: services→runtime, C: cccp→experience.

**Common enums.** `phase`: `main|approval|budget|da-preflight|da-submit|da-evidence|fixture|live|aggregate` (`preflight` is a read alias for `da-preflight`). `readiness`: `contract_ready|implementation_ready|integration_ready|verified` (`deployed` is a read-only alias for `integration_ready`). `delivery_status`: `planned|blocked`. `result`: `PASS|FAIL|NOT_VERIFIED`. `scope`: `IN_SCOPE|DEFERRED|NOT_APPLICABLE`. `evidence_kind`: `fixture|live|aggregate`.

**P2 entry choice.** The P2 client path is a versioned MCP extension, transport `stdio JSON-RPC 2.0 over loopback HTTP Controller`, MCP protocol `2025-06-18`; the restricted HTTP client path is not chosen. The authoritative record is `docs/reference/next-change/acp2-contracts/manifest.json` (`p2_entry`), with the request/operation table frozen in `runtime/application-api.v1.json` (B03).

**Manifest and validation.** `docs/reference/next-change/acp2-contracts/manifest.json` (schema `acp2.contracts.manifest.v1`) is the H0 contract index; `scripts/acceptance/validate_acp2_contracts.py` exits 0 on it. Actual lint baseline: 1,058 diagnostics across 129 files (ruff 0.15.22); the plan's earlier 1,065/133 figure is superseded and recorded as drift.


## B03 sync (2026-10-22)

**Application command surface.** H1 freezes one command per entry in `runtime/application-api.v1.json` (schema `acp2.runtime.application-api.v1`): `capabilities`, `health`, `preflight`, `submit`, `status`, `cancel`, `prepare`, `read`, `analyze`, plus `control` (pause/unpause/continue/rerun/edit) and `recover`. `control` and `recover` are added because those entries already exist; leaving them outside the frozen surface would hide a second Submit/Cancel path. `runtime/entry-call-table.v1.md` renders the audited map. Audit verdict is **PARTIAL**, not PASS: `GAP-B03-01` (CLI `task query` and the four `GET /api/v1/jobs*` reads hit the manager/store directly), `GAP-B03-02` (`acp task export` reads `RESULT/result_manifest.json` directly), `GAP-B03-03` (all eight MCP tools call injected port adapters and the submit path keeps an in-connection idempotency dict), `GAP-B03-04` (no entry accepts a Controller target; `ControllerClient` is B04a). Owners: B04a/B05/B06/B10a/B12.

**Digests.** Four fields, never reused. `plan_digest` is the submit authorization value: the server refuses to execute a plan whose freshly computed digest differs from the submitted one (`plan_digest_mismatch`). `proposal_digest` is the canonical Proposal/ResearchRecord semantic projection and excludes `approval_ref`/`status`/`updated_at`/`revision`/`etag`. `contract_digest` is the digest of the delivered contract set and `health` returns it with `candidate_revision` and `contract_version` for entry-equivalence checks — it is neither artifact content nor `plan_digest`. An artifact `digest` is content bytes pinned with `generation`. The canonicalization rules (sorted keys, UTF-8, no floats, decimal-only quantities, frozen unit table, set semantics) and byte-exact vectors live in `runtime/canonical-digest-vectors.v1.json`; **B16 implements the single `src/acp_services/runtime/canonical_digest.py`**, B18 imports it, and B03 deliberately ships no runtime implementation.

**Phase and readiness.** All eleven B03 contract entries are `delivered` in the manifest at `readiness: contract_ready`; implementation acceptance stays `planned` until the owning H2/H3 gates (B04a/B05/B06/B10a/B12/B13). The manifest's `delivery_status` keeps its frozen `planned|blocked` meaning (acceptance delivery); contract publication is tracked by `readiness` + `digest_or_pending` + `contract_delivery`. `contract_version` stays `1.0.0` because nothing already delivered changed on the wire; only planned contracts became delivered, so `contract_digest` moved instead.

**P2 entry and the research-operation table.** The P2 client path is the **versioned MCP extension** (`acp.research.v1`, namespaced tools like `acp_research_proposal_create_v1`), negotiated in `initialize` and refused with `unsupported_capability` when not negotiated. The eight-tool P1 allowlist is unchanged; no restricted HTTP client is added. `runtime/p2-research-operations.v1.json` freezes the five R3-02 rows with tool name/version, ControllerClient method, application use case, authorization rule, error class and binding owner: **B16 owns approval tools (P2-R2/R3/R4), B18 owns Proposal/ResearchRecord resource and form-handoff binding (P2-R1/R5)**. A data interface alone is not a live DSH tool. The approval tool requires a server-verified human approver; a payload-claimed approver is refused.

**Schema and unknown fields.** `schema_dialect` is JSON Schema 2020-12 with an explicit UI keyword subset (local-only `$ref`, discriminator-based `oneOf`, inclusive `minimum`/`maximum`, union-type nullability; `allOf`/`anyOf`/`multipleOf`/exclusive bounds unsupported). Unknown-field policy: new execution requests reject unknown envelope fields; historical reads preserve them without granting execution/recovery eligibility; unknown major versions execute nothing; extra metadata only under namespaced `extensions`. A real A descriptor → B discovery → C form → B validation → CCCP round trip is **scheduled for B18 and not claimed**: a hand-written toy form does not satisfy it.

**CDXML scope.** CDXML is **deferred**, not implemented and not silently unsupported-by-accident. `runtime/fallback-deferral.json` records the A/B/C scope change, the reserved (not frozen) `structure.parse` endpoint, and the backfill conditions. An unsupported format returns `invalid_parameter/input_invalid/unsupported_structure_format` and **never** creates an empty structure or an empty task. P1 formats are xyz/gjf/com/inp/sdf/mol/smiles.

**Dependency ledger.** `runtime/dependencies.json` now merges C's `experience/handoffs/dependencies-c.json` (`sha256` recorded, five rows kept in the C-consumes-B direction, never inverted) and records **A's `science/cross-track-dependencies.json` as `NOT_VERIFIED / blocked_fixture`** with the exact expected path — no A row is fabricated. Artifacts carry version/digest/readiness, relations are explicit AND/OR, and the validator rejects missing producers, dangling references, cycles and impossible stage order. Refresh at B07, B14b and every later stage freeze.

**Samples.** `runtime/samples/**` holds 55 positive/negative samples with a hashed index (`runtime/samples/manifest.json`); every frozen contract has at least one negative sample. B01's acceptance corpus stays under `samples/acceptance/**` and is indexed by B01's `cases.json`, deliberately excluded from the B03 index.
