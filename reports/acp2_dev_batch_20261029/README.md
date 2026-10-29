# ACP/RPH 开发批次 2026-10-29：基线修复 + RPH 正式装配 + 小分子真实闭环

执行日期：2026-10-29。源码基线：`main` / `fd2087c7`（与审查基线一致；工作区含审查文档改动）。
范围依据：[RPH 三阶段路线图 §6](../../docs/RPH_Agent_Three_Phase_Roadmap_20261027(1).md) 与 [功能审查报告](../acp2_function_audit_20261028/REPORT.md) F01–F10。

分工与证据口径：
- 工程实施：codex CLI（`gpt-6.1-sol`，reasoning effort=high，经 `sudo -n /usr/local/sbin/zoog-codex exec -s workspace-write`）；其叙述性交接见 phaseA/codex-engineering-note.md，**属工程笔记，不构成验收证据**。
- 复核与机器证据：DSH Lead（宿主侧）实际运行并保存 JUnit/日志于本目录；本 README 只引用这些机器结果。
- 不重启生产服务；Phase A 无真实模型/QC/LSF 提交；浏览器测试用临时服务；无 git commit（工作区改动待评审）。

## Phase A：基线修复（完成）

| 编号 | 处理 | 机器证据（Lead 宿主侧运行） | 状态 |
|---|---|---|---|
| F01 面板遮挡 | 布局约束重排：shell 定高 grid、队列不可压缩、科研面板限高自滚动（frontend/css/workbench/{layout,queue-detail,features}.css） | browser-rerun.xml 整组 48/48；workbench-overlap.json 探针 `new_project_click: actionable` | PASS |
| F03 数值编辑 | DOM 边界按 schema 转换 number/integer，空值与 badInput 分流，后端强类型保持（capability-form.js + ui_subset.js + 模块测试） | browser-rerun.xml 48/48；frontend-modules-final.log 488 PASS | PASS |
| F02 插件断言 | 按 build/pip 工具链分支断言（test_acp2_science_plugin_production.py + _acp2_external_plugin_harness.py） | plugin-host.xml 25 passed（含原 8 个 uvicorn ERROR 用例在宿主全过） | PASS |
| F04 Origin 隔离 | 五个临时服务改用显式隔离夹具（conftest.py 等），产品跨域保护与攻击拒绝测试保留 | browser-rerun.xml（ACP_PUBLIC_ORIGIN= 隔离下 48/48）；unit-affected-host.xml 内 local_boundary 91 passed | PASS |
| F09 查看器首载 | 根因定位：3Dmol resize() 销毁并重建 canvas/WebGL 上下文；ResizeObserver 自触发风暴在 SwiftShader 下 CONTEXT_LOST_WEBGL，第三次重建失败后 canvas 永久缺失。修复：framing.js observer 尺寸未变跳过；scene_instance.js resize 包装同尺寸 no-op；molecule-view.js 捕获 webglcontextlost 显示降级占位（i18n zh/en 新增 viewer.render_failed） | f09-fix-alone.xml 1 passed；browser-rerun.xml 48/48；frontend-modules-final.log 488 PASS（新增 2 个 F09 回归用例） | PASS |
| F08 制品取回 | 只读全盘定位：原 31 URI 仍 0 存在；16/31 找到 SHA-256 匹配历史副本（/ACPServer/acp2-runs/remediation/ 下 m5-01-install 与 m4-05），15/31 未找到匹配副本；未重建、未改 BOM | bom-location.{log,json}（codex 运行的只读扫描，脚本随附） | 定位完成；原 URI 可取回性仍 FAIL（需制品保存方确认外部存储），如实保留 |

受影响非慢单元：unit-affected-host.xml（local_boundary + ui_subset + viewer framing sync）91 passed。
原始审查证据 reports/acp2_function_audit_20261028/** 未修改；原 18 FAIL 记录保留。

## Phase B：RPH 正式装配（完成，机器证据由 Lead 宿主侧复跑）

实施：codex（gpt-6.1-sol, high）。设计要点：`acp.research.v1@1.0.0` 协商后**加性**暴露 10 个消费工具（`acp_research_{model_plan,model_usage,model_statement,record_create,record_read,record_append,evidence_read,budget_read,checkpoint,readiness}_v1`；基础 8 + P2 10 契约不变）；CLI 默认 `createProductParts`（模型 Planner 经 Controller gateway / proposal CAS writer / 证据投影 reader / 预算 reader / 严格 singlepoint mapper / session ledger submitIntent）；resume 复用已存计划与 checkpoint（审批后不再调用模型、checkpoint 命中后不再 submit）；doctor 三态 readiness + 凭据脱敏；4 个契约语料文件同步；migration ledger 补登记 F05 OPEN（未放宽）。

| 验证 | 结果 | 证据（phaseB/） |
|---|---|---|
| 隔离/注入/worker/MCP 派发 + 消费契约（含 HTTP 用例） | 209 passed | host-guards.xml |
| MCP backends 全文件（uvicorn/HTTP） | 9 passed | host-mcp-backends.xml |
| MCP process 非 contract | 19 passed | host-mcp-process.xml |
| P2 工具/研究读路由/thin slice/证据投影/模型网关 | 98 passed, 1 skipped | host-http-science.xml |
| 真实 DSH SDK MCP conformance（contract） | 7 passed | host-sdk-contract.xml |
| rph build / test（21 文件）/ check（边界守护） | 全 PASS | host-rph-*.log |
| rph package（4 tgz）→ 临时前缀离线安装 → verify-install | 全 PASS；安装 bin `rph doctor --json` exit 0（ready=false 静态预期）、`rph research --help` exit 0 | install.log / verify.log / doctor-static.json / research-help.txt |

codex 沙箱内已跑单元/契约（244+63）与其工程笔记见 phaseB/codex-engineering-note.md 及同目录日志；其沙箱无法绑定的 HTTP 用例已由上表 Lead 复跑覆盖。F05 的真实模型→真实 QC 同链验收归 Phase C；台账 F05 行保持 OPEN 直至该链通过。

## Phase C：小分子真实闭环（C1 部分完成；证据绑定遗留一个平台缺陷）

同一条真实轨迹已打通至真实 QC 与幂等恢复；证据视图绑定被一个平台侧身份解析不一致缺陷阻断（有回执）。全部经安装后的 `rph` 产品入口执行；模型凭据经 `~/.acp/model_key` 注入，不落日志/证据。

### C1 已验证（PASS，机器证据 phaseC/）

| 环节 | 结果 | 证据 |
|---|---|---|
| 自然语言 → 真实模型计划 | deepseek-chat 经网关（预算预扣/回执/settle），计划通过严格 schema+科学映射校验 | step1-research.log（stop 在审批点，model_requests_used=1）；run_root/model-gateway/receipt-*.json |
| 提案 + 审批请求 + 操作者落锤 | proposal CAS 持久化；local-single 策略下操作者经 API approve（expected_revision CAS） | step1/step3 日志与 approvals 记录 |
| 平台预检 → 真实 QC | preflight digest 匹配；**ORCA r2SCAN-3c 水单点完成**：`FINAL SINGLE POINT ENERGY -76.418906909978` Ha，result_manifest 含 energy_report 产品与完整 digest 链 | controller.log（POST /api/v1/jobs 201）；run_root/…/RESULT/result_manifest.json |
| 完整 TaskRef + 幂等恢复 | submit 经 acp_status 对账得 attempt=1/release；resume[1]/[2] **submits=0**（checkpoint 复用，不重复提交）；重连后无新模型调用 | step3-resume-{0,1,2}.log |
| 预算与账目 | 模型 1 次调用；planning_budget 上限执行；resume 不追加调用 | summary.json |

修复清单（本轮为打通真实链修的实际缺陷，均已带回归或真实运行验证）：planner 系统提示词与输出 schema 契约对齐（+测试）；部署指令 parameters 精确化；plan.budget 由「回显剩余」改为「声明自身需要」（原为结构性死锁）；restore 适配线上 proposal 裸文档形状 + snapshot 取权威 revision；approve 查询补 project 作用域；执行绑定改规范 JSON 比较；提交输入适配 `{source_type, source, xyz_text}` 平台契约；submit TaskRef 经 acp_status 有界对账 + digest 裸 hex 规范化；完成态经 prepare_artifact 生成摘要绑定的原生证据引用。

### C1 已收口（2026-10-29 第二轮）✓

证据报告生产面已交付：`acp_services/runtime/evidence_production.py`（A 线 `assemble_evidence_report`/`verify_claim_sources` 组装 + 摘要绑定，确定性字节）+ `POST /api/v2/research/evidence/prepare` + MCP `acp_research_evidence_prepare_v1`（协商后共 11 个消费工具）+ rph wait 适配器接线。真实同链最终结果：`status=completed stop_reason=accepted`，研究记录 `decision=accept`，`evidence_refs=[RESULT/evidence_report.json, RESULT/energy/singlepoint_energy.json]`（两条 digest 绑定引用），模型 1 次调用、submits 1→0→0（不重复提交）。复验：310 Python（含 6 个生产模块新测试）+ 真实 SDK conformance 7/7 + rph 300/300 + boundary check。证据见 phaseC/closure/。

（历史定位记录，已修复）

第一轮定位的身份不一致（v1 路由 from_server 回退 entry_class=http vs gateway=controller）**已修复**：`src/acp/api/v1_routes.py` 回退统一为 CONTROLLER，配 `tests/test_acp2_command_identity_consistency.py`（2 用例）钉住；同轮修复 `read_attempt_evidence` 绑定丢失 `release_digest`、`TaskRef.from_task_binding` 对 receipt 裸 hex 的规范化、投影内 receipt↔TaskRef digest 的 scheme 不敏感比较（受影响套件 162+141 全绿）。

修复后 evidence/read 推进到**报告语义层**：引用的必须是 `schema_version:1` 的 EvidenceReport 文档（science_binding 冻结词汇表），而非原生科学结果 JSON。合法产地是 A 线 `cccp.analysis.research.assemble_evidence_report`（Subject/ExecutionFacts/ClaimDraft + 逐域验证器，含 reject_model_conclusion_as_evidence），当前**没有**把已完成 envelope 任务的原生产物组装为证据报告的平台操作——`register_evidence_report`（science_binding:783）存在但未暴露任何 HTTP/MCP 面。下一批首项：设计并交付该「证据报告生产面」（A 线词汇表所有，Services 暴露最小版本化操作），随后 C1 即可收口（视图→引用解释→record CAS）。在此之前客户端按设计停在 `evidence_binding_required`，不编造解释。

### 故障演练（PASS，phaseC/drills/）

三项演练均走真实链（安装 bin + 真 Controller + 真 ORCA），断言来自平台 API 与 rph 输出：

| 演练 | 结果 | 证据 |
|---|---|---|
| 断线重连：等待中 SIGKILL 客户端 → 新进程 resume | 全项目仍**恰 1 个 job**，重启客户端 **submits=0**，规范停在 wait_pending | drills/disconnect/ |
| 取消：RUNNING 中经 API cancel | HTTP 200 → 终态 **CANCELLED**，客户端如实观察到 | drills/cancel/ |
| 超时：6s 墙钟 vs ~10s 任务 | 终态 FAILED，`[WALLTIME_EXCEEDED] the attempt was terminated at its wall deadline` | drills/timeout/ |

注：一次早期演练中重启 resume 曾停在 budget_cap（任务回执已计入 used 后轮次门的顺序问题），不重复提交的不变量始终成立；该项与证据报告生产面一并列入下一批。

## C2：`xtb_optimize` envelope 推广 — 完成（证据优先）

1. **预演**（仅进程内放宽集合，跑未修改的 runner 真冻结路径）：`attempt.frozen` → real xTB worker（`normal termination of xtb`）→ `execution_receipt.json` completed → 发布产物 `RESULT/energy/optimize_energy.json` + `publication_state`；`attempt_choice.entry=envelope`。证据：phaseC2/c2-facts.json。
2. **永久推广**：`DEFAULT_ENVELOPE_WORKFLOWS` = `{singlepoint, xtb_optimize} ∪ RECIPE_WORKFLOWS`；`reports/architecture_remediation_20261020/support-matrix.md` 第 8 行按证据门更新（PASS 格均带证据链接，remote 保持 NOT_VERIFIED）。
3. **同链真实闭环**（安装 bin + 真 Controller + 真 xTB）：`status=completed stop_reason=accepted`；job `xtb_optimize` completed；manifest 产品 `file/energy_report/structure`；记录 `decision=accept` 引用 `evidence_report` + `computed_quantity`（`RESULT/energy/optimize_energy.json`，优化能量 **-5.070544 Ha**）。证据：phaseC2/ 与 phaseC2/attempt/。
4. 回归：chain-switch / enablement-matrix / bounded-recipe / architecture invariants / fault matrix **157 passed**；rph 300/300。

### 下一批

正式台账对账与 G/H 门复核；可选继续推广其他集成工作流（optimize/frequency/scan/irc 各自证据优先），或多 Agent 前置于第二 harness 迁移验收。
