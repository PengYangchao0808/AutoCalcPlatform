# Phase B — RPH 正式产品入口装配（F05）

实现与离线安装面已完成；**HTTP 集成/真实闭环未验收，不能据此关闭 F05 的全部生产证据或正式发布门**。本阶段模型测试全部为注入 fake transport/gateway；未调用真实模型、未提交真实 QC/LSF，未重启生产服务，未 git add/commit。

## 设计

- 复用 `acp.research.v1@1.0.0` 的 `acp_capabilities` 应用层协商。原 8 个基础工具及 10 个 P2 工具契约保留；绑定消费端口的服务在协商后额外列出 10 个 `*_v1` 工具，共 28 个。原客户端可以继续消费原面；RPH 产品工厂额外检查十个消费工具真实出现在 tools/list，缺失即拒绝。
- 新操作统一前缀 `acp_research_`、后缀 `_v1`：`model_plan`、`model_usage`、`model_statement`、`record_create`、`record_read`、`record_append`、`evidence_read`、`budget_read`、`checkpoint`、`readiness`。写操作要求 write；只读操作要求 read；模型 key 不作为工具参数。没有新增 dotted wire id，Services 中没有新增 `acp.*` 字面量；wire station 和隔离规则保持原样。
- MCP stdio 的 framing 实现无需第二套工具表：新增操作直接经现有 `mcp_stdio.py` → `McpServer` dispatch；新增字节流测试验证协商后的调用确实经过该路径。没有在 bridge 添加 HTTP/fetch/spawn。
- 安装后的 `main()` 默认分支在 CLI/bundle 组合根调用 `createProductParts`：`createModelPlanner` 经 Controller gateway；writer 使用平台 proposal / ResearchRecord CAS；读者使用平台证据投影与现有 admission/model ledgers；submitIntent 默认使用 session bridge ledger。工厂在规划前读取 Controller readiness，关键项失败时不调用模型或提交；测试注入 seam 保留；demo 仍为显式 fixture，继续拒绝真实 Controller。
- 首场景仅 `singlepoint` / `electronic.energy`，一轮、一个输入、identity atom mapping。方法、电荷/多重度、基组选择、输入字节 SHA、ncores、memory_gb、任务计数和 deadline 都来自确认配置与验证计划；不一致即拒绝。`basis:null` 只显式支持 r2SCAN-3c 的无独立基组选择，映射到不含 null 的 canonical execution plan。约束、多输入、替代项和 labelled/reference 电子态暂拒绝。没有新增 active 工作流。
- 模型仅产生 candidate；平台冻结后返回 science / execution / proposal 三个摘要。提交使用 execution digest，科学归属使用 science digest，不再混用。
- proposal 保存显式配置、稳定 session seed、验证计划和客户端 checkpoint。`resume` 读取 proposal/审批，验证执行/科学摘要：审批后复用已保存计划，不再调用模型；已有 checkpoint 时验证相同 attempt/release 后继续等待/证据/决策，不再 submit。`--task`/`--cursor` 与保存绑定不符会拒绝。原 Saga proposal 继续使用既有 Saga resume。恢复不重建另一套研究状态机，复用现有 Supervisor。
- `doctor` 保留旧 checks，新增 `readiness[]` 的 ok/warn/fail + hint、独立 `profile_paths` 和 `ready`。覆盖模型 gateway/凭据配置、身份/权限、本地 ORCA 可执行解析、Controller run_root 与客户端 profile 路径、singlepoint envelope。静态检查不宣称远端已验证；connect 只读检查不调用 provider/QC。诊断错误即使回显凭据也会被脱敏。

## 改动文件（仅本阶段）

既有用户修改、Phase A 文件及 `reports/acp2_function_audit_20261028/**` 均未写入；下表从本阶段开始时的工作区状态中排除既有修改。没有 Phase A 文件交集。

| 路径 | 改动 |
|---|---|
| `docs/reference/next-change/acp2-contracts/experience/rph-product-entry.v1.md` | 记录安装入口、显式科学配置、恢复、doctor 和证据边界。 |
| `docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json` | 登记可选消费工具面及产品客户端实际 tools/list 覆盖要求。 |
| `docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json` | 更新协商后的加性工具可见性，保持原十项 P2 承诺。 |
| `docs/reference/next-change/acp2-contracts/runtime/research-consumption.v1.json` | 冻结十个新消费操作的 HTTP 对应、权限、参数 Schema 和边界。 |
| `rph/examples/singlepoint.product.json` | 提供显式 H2 输入、方法/基组选择、资源和预算示例。 |
| `rph/packages/cli/src/compose.ts` | 默认调用产品工厂并接通产品恢复；保留 parts/transport 注入 seam。 |
| `rph/packages/cli/src/index.ts` | 帮助文本说明扩展后的科学输入文件。 |
| `rph/packages/dsh-bundle/src/compose.ts` | 将 profile 的 Controller credential 经 launcher 环境传递，避免工具参数/argv 泄密。 |
| `rph/packages/dsh-bundle/src/doctor.ts` | 增加五类三态 facts、独立 profile 路径检查、ready 和失败信息脱敏。 |
| `rph/packages/dsh-bundle/src/index.ts` | 公开产品工厂与恢复组合根。 |
| `rph/packages/dsh-bundle/src/product-parts.ts` | 装配模型 Planner、CAS writer、证据/预算 reader、严格 singlepoint 映射及恢复。 |
| `rph/packages/dsh-bundle/src/research-ports.ts` | 区分科学/执行摘要，接入恢复 adapter 并保留原端口 seam。 |
| `rph/scripts/install-surface.mjs` | 安装验证用临时文件捕获 bin 输出，并检查 spawn.error，避免受限管道产生假成功。 |
| `rph/test/m3-02-cli.test.ts` | 旧 18 工具假服务握手成功仍被判定产品消费面不完整。 |
| `rph/test/m3-02-harness.ts` | 为测试增加可注入的 MCP 工具回复。 |
| `rph/test/product-parts.test.ts` | fake gateway 下验证默认装配、审批、CAS、证据、跨调用恢复与凭据脱敏。 |
| `src/acp/api/v2_research.py` | 新增消费应用路由并复用单一投影；显式拒绝本地 reader 读取远端作业目录。 |
| `src/acp/services_mcp_bootstrap.py` | 为生产 MCP 组合根注入 ControllerConsumptionPort。 |
| `src/acp_services/entrypoints/controller_client.py` | 为闭合消费路由表复用已有 Controller JSON transport。 |
| `src/acp_services/entrypoints/mcp.py` | 注册/鉴权/协商后暴露消费工具并发布实际可用性。 |
| `src/acp_services/entrypoints/research_consumption.py` | 持有版本、工具 Schema、权限与字面量路由表；路径参数转义。 |
| `src/acp_services/runtime/research_consumption.py` | 在原 proposal CAS 上持久客户端状态并投影既有预算/模型账本。 |
| `tests/baseline/services-evidence/migration_ledger.md` | 补登记 F05 OPEN 行；所有既有状态保留，HTTP/真实链仍 NOT_VERIFIED。 |
| `tests/test_acp2_research_consumption.py` | 覆盖协商、stdio、新端口、真实 proposal CAS、预算与应用级证据/readiness 护栏。 |
| `tests/test_services_mcp.py` | 将静态派发表精确集合更新为 P1、原 P2 和新增消费工具的并集。 |

## 验证命令与结果

Node 使用本机已有 24.22.1、pnpm 11.28.5。登录 shell 默认 Node 20 不满足工作区要求，因此最终 Node 命令前统一设置：

```sh
export PATH=/home/xieningke/.nvm/versions/node/v24.22.1/bin:$PATH
```

未更改 HOME；打包/离线安装使用已有 Corepack 缓存和 `/tmp/phaseB/xdg-data`，不下载依赖。

| 命令/范围 | 结果 | 证据 |
|---|---|---|
| `pytest -q tests/test_acp2_research_consumption.py tests/test_acp_services_isolation.py tests/test_services_injection_contracts.py tests/test_services_worker_contract.py tests/test_services_mcp.py tests/test_acp2_mcp_research_handshake.py tests/test_acp2_p2_tools.py -k 'not http' --junitxml=/tmp/phaseB/pytest-unit-scope.xml` | PASS：244 passed，4 HTTP deselected；明确为可运行单元/契约范围，不是完整文件集成 PASS | `pytest-unit-scope.log/xml` |
| `pytest -q tests/test_acp2_thin_slice.py tests/test_acp2_evidence_projection.py tests/test_acp2_model_gateway.py -k 'not thin_slice_script' --junitxml=/tmp/phaseB/pytest-science-ports.xml` | PASS：63 passed，1 实际启动 Controller 的脚本 deselected | `pytest-science-ports.log/xml` |
| `pytest -q tests/test_services_doc_sync.py::test_migration_ledger_six_columns_and_status_tokens` | PASS：1 passed；未放宽任何台账断言 | `pytest-ledger.log/xml` |
| `pytest -vv tests/test_acp2_mcp_sdk_contract.py -m 'not contract'` | NOT_VERIFIED：该文件整体标 contract，0 selected / 7 deselected，exit 5，不能当 PASS | `pytest-sdk-structural.log/xml` |
| `pytest -q tests/test_acp2_mcp_sdk_contract.py::test_research_handshake_identity_matches_the_contract_and_the_bridge -m contract` | PASS：1 个纯静态契约用例；不是实际 SDK conformance PASS | `pytest-sdk-identity.log/xml` |
| `pnpm --dir rph build` | PASS | `rph-build.log` |
| `pnpm --dir rph test` | PASS：21 个测试文件，0 fail | `rph-test.log` |
| `node --test --test-isolation=none --test-reporter=tap rph/test/dist/*.test.js rph/test/*.test.mjs` | PASS：298 个实际子测试，0 fail / 0 skip；额外保留完整子测试明细 | `rph-test-no-ipc.log` |
| `pnpm --dir rph check` | PASS：tsc strict + 依赖方向、网络/进程/执行边界守护 | `rph-check.log` |
| `COREPACK_HOME=/home/xieningke/.cache/node/corepack XDG_DATA_HOME=/tmp/phaseB/xdg-data pnpm --dir rph package` | PASS：四个 tgz + checksum/lock/install surface；脚本内 throwaway 离线安装验证通过 | `package.log` |
| 复制完整 dist-packages 到新临时前缀后 `pnpm --dir /tmp/phaseB/installed-final install --offline --frozen-lockfile` | PASS：exit 0 | `install.log`, `install.exit` |
| `node /tmp/phaseB/installed-final/verify-install.mjs` | PASS：四包出树解析、版本、帮助、doctor；exit 0 | `install-verify.log`, `install-verify.exit` |
| 安装 bin `rph doctor --json` | PASS 冒烟：exit 0，static、ready=false，未检查的产品项为 warn | `doctor-static.json`, `.exit` |
| 安装 bin `rph research --help` | PASS：exit 0，显示显式科学配置要求 | `research-help.txt`, `.exit` |
| 安装 bin `rph doctor --connect --json`，不配置 Controller | PASS 预期失败路径：exit 1，ready=false、controller=fail，JSON 可解析，无崩溃 | `doctor-disconnected.json`, `.exit` |
| 修改 Python 文件 `ruff check` / `ruff format --check`、compileall、`git diff --check` | PASS | `ruff.log`, `ruff-format.log`, `compile.log`, `diff-check.log` |

### 环境受阻的集成检查（不是 PASS）

- `tests/test_acp2_runtime_mcp_backends.py` 全文件已尝试：9 ERROR，临时 uvicorn 无法绑定 `127.0.0.1:0`（沙箱端口限制）。见 `pytest-http-backends.log/xml`。
- `tests/test_acp2_runtime_mcp_process.py -m 'not contract'` 已尝试：19 ERROR，临时 Controller stub 绑定失败。见 `pytest-mcp-process.log/xml`。
- `timeout 45s pytest -vv -o faulthandler_timeout=15 tests/test_acp2_p2_tools.py tests/test_acp2_research_read_routes.py ...`：P2 非 HTTP 部分通过后停在首个 HTTP TestClient；45 秒超时。见 `pytest-http-research.log`。单独最小空 FastAPI 应用也在 `TestClient.__enter__()` 卡住，业务尚未运行；系统 Python 和 `/opt/acp/venv` 均复现。见 `readiness-debug.log`、`venv-testclient-probe.log`。
- 没有改 marker、放宽 Origin/身份/CAS、关闭护栏或把这些超时/ERROR 改为 PASS。新应用用例通过直接 Request 调用覆盖逻辑和 OpenAPI 注册；这不能替代 HTTP 投递证据。
- 初次 package 还暴露沙箱限制：同步子进程 pipe capture 返回 EPERM 而 status 可能为 0。安装 verifier 改为普通临时文件捕获，并新增 `result.error` 断言；全部校验保留。最终 package/独立安装已通过。

## 遗留风险与 Phase C

1. 需要在允许 loopback/正常 TestClient 的环境重跑上述 HTTP/MCP process 文件和真实 SDK 套件；当前不能给 HTTP 完整装配签 PASS。
2. 真实模型 → 单份计划 → 人工审批 → 真 singlepoint QC → 发布精确 evidence_report/ArtifactRef → 证据绑定/解释/记录，必须是一条轨迹。fake 模型/证据测试不冒充该证据。普通 singlepoint 的实际报告引用发布与读取仍需真实轨迹确认；缺引用时客户端明确停止，不用模型叙述补证据。
3. proposal checkpoint 是客户端恢复元数据：服务端检查当前 project/attempt，再执行 proposal revision CAS；它不是跨数据库原子 worker fence。实际 rerun/取消/进程死亡并发窗口、worker receipt/fencing、断连后相同幂等键不重复提交，需要 Phase C 实测。
4. resume 已用 fake transport 验证跨调用恢复、审批后无新模型调用、checkpoint 后无新 submit、foreign task/cursor/digest 拒绝。真实跨进程/Controller 重启和决策提交后断电重放仍未验证。
5. 本地 reader 显式拒绝远端作业工作目录；LSF 必须接入平台 read-root/cache 契约后单独验证，不能据本轮声称远端闭环。
6. 累计 token/core-hours 硬强制仍 DEFERRED。预算 reader 的未测量 wall/queue（以及缺失 token 用量）是明确标记的下界；不能作为已测量零值或硬上限。既有 per-attempt deadline 不替代这些累计约束。
7. 首 mapper 的严格范围见上文；xtb_optimize envelope 链、便携 skill、第二 harness、多 Agent 和开放式科研策略不属本阶段交付。三个非 Planner helper 仍是确定性实现。
8. 既有用户修改中的 README/现状说明没有覆盖写入，仍可能描述 Phase B 之前的状态。新契约和本报告记录本次增量；正式 evidence coverage / Candidate-Full 未提升。

`migration_ledger.md` 本次仅补登记 F05 `[OPEN]`，保留所有既有三态和历史行。代码、安装和 fake 测试完成不等于真实科学验收完成。
