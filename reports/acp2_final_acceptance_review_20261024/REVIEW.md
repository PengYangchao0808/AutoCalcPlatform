# ACP2 最终验收与整改计划独立复核

复核日期：2026-10-24。对象：`.omo/plans/acp2-final-acceptance-remediation.md` Revision 6、`reports/acp2_final_acceptance_20261024/REPORT.md` 与 `summary.json`。代码候选：`f6dd3c928b4f11dc8c3782c4b15c7130cc856a0b`。本次仅审查和临时目录探针，不修改产品、原报告或原计划。

## 判断

**完整 ACP2 不批准的结论成立；验收报告存在事实口径和范围错误。整改方向合理，但 R6 仍需修改后再作为实施基线。**

已核对原始 JUnit：11,161 passed、6 failed、11 skipped；前端 438、RPH 80 的结果与日志一致。CCCP 独立安装、真实 ORCA 与发行安装的通过项有证据。科学/研究前端请求的路由缺失成立，显式 fixture 模式、只读投影、跨项目隔离、保留旧证据、精确登记审计站点等修复原则正确。

没有重跑整套回归；本次复用了同一候选的原始 XML，并新增了三个小型独立探针，结果见 `probe-results.json`。

## 实施前必须修改

### R1 / P1：预检与实际提交不是同一个计算请求

- 计划位置：B1-3（99–107 行）、B1-8。
- 代码：`frontend/js/workbench/features/science-question.js:575` 的 `preflightPlan()` 没有结构 `source`，方法为平铺 `method/solvent`，不带提交资源；`:638` 的 `jobBodySpec()` 则包含实际 XYZ、`method.levels`、basis、资源。
- 计划只把预检交给 v1，没有修复两份载荷的差异，也没有要求真实提交闭环。按相同用户输入构造两种现有载荷后，调用生产 `_plan_digest()` 得到不同摘要（探针已保存）。因此“预检通过”不能证明即将提交的科学输入通过，不能用该预检摘要绑定实际提案或批准。
- 修改：从一个规范化请求构造预检和提交，保证结构、charge/multiplicity、method/basis、solvent、resources 一致；修改这些字段后使旧预检失效。补无网络拦截的正向用例：输入 → 预检 → 实际提交 → 同一任务状态/视图。调用真实后端的客户端 read 探针不足以证明表单可用。

### R2 / P1：provenance 策略会继续阻断普通 optimize/frequency，并绕开远程读根

- 计划位置：B1-0 的人工 release fixture；B1-5 第 140 行。
- 代码：`acp_services/runtime/execution_chain.py:217` 的默认 envelope 工作流只有 singlepoint；`acp/scheduler/runner.py:1336` 对其他普通工作流直接跳过默认 freeze。`attempt_release_facts()` 仅查 frozen envelope 和 execution receipt。
- 计划同时公布 singlepoint/optimize/frequency 为真实可用问题，又要求缺少 release_digest 一律 409。普通 legacy optimize/frequency 没有这些文件时，路由虽注册，任务视图仍不可用；给 completed fixture 人工写 release_digest 会掩盖这个缺口。该结论是依据当前默认执行路径推导的设计风险，尚未实现的新路由不能算已复现运行时故障。
- 此外，计划明确 current 使用 `record.work_dir` 取 release facts。远端应从已解析的受控 cache root 读取，不能再绕回本地 work_dir；这是项目 ANTI #27 的约束。
- 修改：先定义视图投影生产者身份与执行 release 身份的区别及真实来源；为无 envelope 的合法结果确定兼容策略或明确限制范围，不编造 release。以真实普通 optimize/frequency 输出、远端缓存 fixture、缺失 release 的历史结果测试该策略。读取 provenance 也统一使用 resolver 返回的 attempt root。

### R3 / P1：claims 不能直接采信 prepare 请求 payload

- 计划位置：B1-5 第 136 行。
- 代码：`v1_routes.py:8762` 在入队前把请求的 `evidence_report` 放入 payload；`PrepareOperationService` 的 payload 不等于发布完成事实。
- 独立探针已证实：`state=queued`、`availability=absent`、`sha256=null` 的行也可包含 `status=supported` 的 claim。R6 只写从 prepare payload 投影，未规定完成状态、工件摘要及授权的核验条件。照此实施可能把排队、失败或过期请求当成已发布科学报告。
- 修改：从经过当前授权、attempt/generation/digest 校验的已发布 EvidenceReport 获取 claims；或至少限定完成且 READY 的操作并核对其发布体。queued/running/failed/canceled/expired、缺 body、摘要错误、撤权的行不得贡献科学 claims。补这些负例；GET 仍然不触发 prepare。

### R4 / P2：research resource key 缺少资源种类

- 计划位置：B1-7 第 183–185 行，`resource_id = project_id + ':' + document_id`。
- 同项目 proposal_id 与 research_id 可以同名。两类文档按该规则生成同一个 key，而 store 的资源 key 是共享命名空间。独立探针先创建 proposal `project:same`，再创建 record `project:same`，实际抛出 `ResearchResourceConflictError`。
- 修改：统一采用含 project、schema/kind、document id 的稳定编码；同时处理 `_ID_RE` 的 128 字符上限。只读路由与将来 writer 必须复用同一 key helper，禁止各自拼接。补同项目跨 kind 同名、跨项目同名和长 id 用例。

### R5 / P2：静态资产验收不能切到移动中的源码根

- 计划位置：m3 第 297 行要求检查改指“真实根”。
- 干净冻结 worktree 中未包含 manifest，正是该候选的资产交付缺失。即使 C 后来在另一目录发布文件，也不能据此证明旧候选/旧 wheel 已包含并正确挂载该资产。
- 修改：把 C 的 manifest 与资产合入新的冻结候选，重建 Web/RPH 制品，再对安装后的包和实际 URL 校验 digest。外部 producer 交接的“已到达”和安装候选的“已收录/已挂载”分开记录。保留旧 absent 回执。

### R6 / P2：不建议删除已声明的 ControllerClient.recover

- 计划位置：m1 第 287 行。
- 仓内没有调用者不足以证明 SDK/外部客户端无人使用；该方法已进入公开契约和 method pin。删除会引入兼容性变化，不能仅通过同步删除 pin 来验收。
- 修改：保留签名并返回明确的 unsupported/not-delivered 错误且不发往不存在路由，或复用正式恢复用例接线；若决定移除，必须有显式契约版本/弃用安排和消费方审计。

## 验收报告需要纠正的口径

1. **“PASS（带 6 项失败）”不适合作为回归结论。** 回归应为 FAIL，另列“已验证的本地运行时场景 PASS”。尚未到达幂等逻辑的失败测试，只能说明该跨进程门仍未证明，不能证明幂等正确或错误。
2. **M2 定性应与 R6 同步。** `initial_inputs[].role`、`task_refs[].job_id` 是 experience 文档的合法字段；目前是 fixture 分类/扫描边界问题。原报告 §5 把它列为产品契约违规过重；移动到正确 fixture 域并保留 science 负向门是合理方案。
3. **区分本轮未验证、历史通过、环境不存在。** `/ACPServer/acp2-tools/deepseek-harness/` 和固定 Node 已存在；B/B26/H7-receipt.md 记录旧候选 `6ec01ec` 的真实 LSF job 1765 PASS。它们不证明当前候选 DSH/LSF 通过，但也不能被写成“本机无 DSH”“没有真实 LSF 证据”。本地 PATH 无 bsub 不证明远端 SSH/LSF 路径不存在。不要为核验这一点打印或读取凭据值。
4. **交接存在性与签收状态分开。** 当前树中 science/handoff-H7.json（A-29）已存在，且自身 release_readiness=false；B 的旧索引仍写 A-29 absent。应写“交接已到达，当前候选集成签收未完成/索引未刷新”。同理对 A-20/H1 样例逐件核对，不能机械搬运旧 absent。
5. **RPH 80 项测试不能替代完整客户端发行验收。** 当前 CLI 仍为 0.0.0、help/profile 阶段，无 bin 入口；这属于 C/T-43 的未完成实现，不全是缺 live 环境。B2 选范围发布可以保留该延期，但应明确延期的是实现、安装验证还是实际运行。

## 顺序和执行建议

先修正文档事实与 R1–R6，完成 B2 的范围选择，再冻结实施范围。范围决策应前置，最终签署仍在回归之后；否则先实现 7 类路由，最后再决定整个面板延期，容易做无效工作。

建议顺序：验收事实表 → 选定 release scope → 统一预检请求/key/provenance/claim 来源 → 小型共享 helper → 路由 → 面板与错误态接线 → 无拦截正向闭环及负向测试 → 定向回归 → 全量回归 → 新候选制品与干净安装 → 对应范围签署。

并行分工需要按文件安排：B1-2/B1-3 会共同修改 science_projection.py；B1-3 与 M1 涉及 v1_routes.py；B1-8 与 M4 同改 scientific-evidence.js。计划中“互不冲突”应改为明确公共文件 owner 和合并顺序。

另外，B1-0 若测试断言当前 404，它本身会通过，不能称作红测；应该把 404 留作基线探针，功能测试断言目标行为，实现在前应失败。M5b 优先使用新子进程隔离，避免大范围 purge sys.modules 掩盖真正残留的替身。

**建议复审结论：总判定认可；验收事实表需校正；整改计划 REVISE_BEFORE_EXECUTION。** 原文已有的摘要格式、读根、授权、不可覆盖证据等约束应保留，无需推翻重写。
