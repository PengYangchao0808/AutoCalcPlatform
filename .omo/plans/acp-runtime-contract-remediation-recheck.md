**ACP runtime contract remediation — 修订版复核与增量改进计划**

复核日期：2026-10-08。对象为当前 304 行、14 项任务的主计划；本次结论替代上一轮对旧主计划的执行就绪判断，不重复提出已经吸收的意见。

- 主计划：`.omo/plans/acp-runtime-contract-remediation.md`。
- 当前主计划 SHA-256：`34b057a78535c5189797ff38e6d3a21e6fd09cd412965673508bb7a0ab706aff`。
- 上一轮审阅的旧主计划 SHA-256：`b3df23912bd024120caac44f0f16b14956a9d40cd56978904eb0e19ad16273f5`。
- 代码 HEAD：`ec3bbb149389ed59834d04f8ffe8e2591b35c0ed`。
- 既有用户改动仍为 `src/acp/api/v1_routes.py`、`tests/test_acp_api.py`、`tests/test_scan_orca_manual_acceptance.py`，另有未跟踪的 `grad.engrad`；本次未修改这些文件，也未修改主计划或上一轮评审文档。

**判断：方向和任务范围合理，当前版本还需一次局部修订再执行。**

跨 attempt 副作用隔离、平面报告兼容、后端输入证据、分阶段恢复凭据、任务投影防倒退、按需远程下载、lifespan 所有权、真实 v1 scan 提交路径等，已经纳入主计划。无需重拆 14 项任务，也无需新增通用调度框架、数据库表或另一套科学解析器。

剩余问题分为两处执行安排冲突、三处实现与验收边界未明确，以及一处文案冲突。

| 优先级 | 问题 | 主计划位置 | 最小修订 |
| --- | --- | --- | --- |
| P1，执行冲突 | 依赖矩阵存在环，且与交付单元先后顺序不一致 | 113–130、任务 5/7 | 以 R-unit 为业务依赖权威，统一任务依赖；删除反向的 `5 → 7` |
| P1，执行冲突 | R0 提交失败测试，R1 却必须全套快测通过 | 87–89、137–143、203、289 | R0 保持绿色；回归测试的 red→green 在所属交付单元完成 |
| P1，恢复契约 | `normal_modes` 是可重建发布产物，不能独自证明科学完成 | 167–171、任务 12 | 明确科学凭据与发布状态；冻结期望模式集合与两层发布失败测试 |
| P1，数据接口 | attempt/revision 的实际 API 来源尚未落实 | 267、任务 5/14、IS-9 | 冻结列表与详情可比较的执行版本标识，使用真实响应验收 |
| P1，接口实现 | 现有 `apiRemote()` 固定走 v1，不能直接替换 v2 调用 | 187–191 | 保留 v2 URL，同时采用 60s 远程预算和统一错误转换 |
| P2，文案 | 自动验收完成与额外用户确认要求互相矛盾 | 84、278 | 删除将用户额外确认作为质量验收完成条件的句子 |

**1. 先消除依赖表冲突。**

主计划的 R-unit 表要求 R1（任务 7）先于 R2（任务 2），R2 再先于 R3（任务 5）。但任务矩阵和任务 5 正文又写任务 5 阻塞任务 7。合并 `Depends on` 与 `Blocks` 两列后，脚本直接检测到 `2 → 5 → 7 → 2` 环。

另有两种不一致：任务 2/3/6 的 `Depends on` 仅列任务 1，而 R-unit 表要求先完成任务 7；任务 3/4/6 又列为可以与任务 7 一同集成。这里混用了业务前置、文件编辑顺序和验收归属。

建议作如下局部修订：

- 保留现有 R-unit 表作为业务依赖权威；任务 2/3/6 的前置明确包括任务 7。
- 删除任务 5 的 `Blocks: 7`，同步清理矩阵和任务正文。
- 文件编辑顺序独立表达为 `manager.py: 7 → 2 → 5 → 8`、`tsmode/engine.py: 3 → 4`、HTML `9 → 14`、共享测试文件 `11 → 13`；v2 源码的 6/9 也确定一个编辑顺序。
- `Blocks` 由前置依赖反推，避免双向手工维护；不要混写任务编号与 `R` 单元编号。
- 在 R0 用拓扑检查确认无环，并检查任务正文与唯一依赖表一致。任务 10–13 的验收归属保留，允许随对应交付单元一起完成。

**2. 修正 R0 的测试落地方式。**

任务 1 明确要求只提交测试/常量、不改变生产行为，验收还要求出现预期失败测试，并标为可提交。若这些测试进入默认收集，尚未修复任务 2–14 时，任务 7 要求的“全套快测绿色”就无法成立。这与正常 TDD 过程中的暂时红灯不同。

建议 R0 只交付版本基线、契约定义/样例、确定性复现 fixture 与证据，保持默认测试集绿色。对生产行为的失败回归在各所属单元内添加，再通过实现转绿后一起落地；例如 TS Mode 恢复反例归 R5，远程下载反例归 R6。

如确需提前收集未来行为测试，必须明确临时 `xfail(strict=True)` 的逐项移除规则；不要通过修改 R1 的全套快测命令来静默排除这些测试。优先采用上一段的方式。

R0 基线还应记录“实际执行版本”的主计划摘要，旧摘要仅作为历史来源保留。

**3. 明确 TS Mode 的科学完成与发布边界。**

这一点也修正上一轮建议的表达：优先复用 canonical `normal_modes` 的方向合理，但它不能成为唯一科学完成依据。

现有代码证据：

- [frequency 发布函数](<repo>/src/acp/calculations/primitives/frequency.py:107) 明确采用 best effort：写 `normal_modes.json` 遇到 `OSError` 时返回空工件列表，科学任务仍可以是 `completed`。
- [legacy 结果适配](<repo>/src/acp/calculations/legacy_adapters.py:946) 不把 `FrequencyPayload.analysis` 直接带回 TS Mode 的 legacy 结果。
- [模式产品构建器](<repo>/src/acp/results/frequencies.py:45) 遍历已有 `mode_vectors`；某个频率编号完全没有向量时，不一定产生警告。隔离探针输入频率编号 `{6,7}`、只提供编号 `6` 的向量，得到一个模式且 `warnings=[]`。

因此，仅检查 JSON 存在、模式非空、已有向量形状正确，仍可能接纳不完整数据；仅把 JSON 缺失判为频率科学失败，又可能把发布失败误判为需要重新运行 QC。

应补入任务 4/12：

1. 在可验证的科学日志/分析和阶段凭据之间建立持久绑定，再记录可重试的发布状态。可以复用现有精确日志路径、摘要与共享 parser，不强制新增独立存储框架。
2. canonical 发布物缺失/损坏，但绑定的原始科学数据完整有效时，重新构建并发布，禁止重新执行 optimize/frequency。科学模式数据本身不完整时，才进入频率阶段失效分支。
3. 冻结期望的原生模式编号集合及允许省略的规则，校验集合完整性，而不是只遍历已输出模式。不要直接假设 `3N-6`，也不要改动既有原生编号语义。
4. 分别注入 frequency wrapper 写 `normal_modes.json` 失败和 TS Mode 最终 `_publish` 失败。在科学凭据有效时，两者恢复都断言 QC 调用数为零；另测“JSON 非空但缺少一个必需模式”。

**4. 给前端缓存明确的执行版本标识。**

任务 14 要求识别“同一状态下 attempt 已变化”，但又只要求 attempt/revision 在可用时参与比较。目前 [V1JobRecordModel](<repo>/src/acp/api/v1_schemas.py:288) 和 [V2TaskRowModel](<repo>/src/acp/api/v2_schemas.py:155) 都没有 `attempt`/执行 `revision`；[JobRecord](<repo>/src/acp/scheduler/jobs.py:782) 已有这些字段。API 中的 `name_revision` 是组织命名版本，不能代替执行版本。

本页的 request epoch 可以拒绝本页已知的旧请求；单靠它不能识别另一浏览器发起、且本页两次轮询都看到 `RUNNING` 的重跑。

建议由任务 5 的权威读取和任务 14 共同冻结：列表与 detail 读取同一 jobs 权威的 `(job_id, attempt, revision)`，或明确证明等价的现有版本标识足以完成比较。若采用新增只读 API 字段，只扩展响应模型，不新增 DB 列，不修改 recovery 的科学/业务判断。

验收必须经过真实 API 序列化：在两次 `RUNNING` 之间完成一次重跑，随后释放旧 attempt 的延迟详情响应，确认旧数据被丢弃且行重新渲染。不能仅在 JS fake payload 中加入真实接口并不存在的 attempt/revision。

**5. 远程超时封装必须保留 API 版本。**

主计划引用 ANTI #28 的 60s 远程预算是正确的，但需要写清 v2 的等价实现。

[apiRemote](<repo>/frontend/ACP_Workbench_v2.html:10107) 调用 `api()`；[api](<repo>/frontend/ACP_Workbench_v2.html:10040) 固定拼接 `API_BASE=/api/v1`。[apiV2](<repo>/frontend/ACP_Workbench_v2.html:10115) 才拼接 `/api/v2`。使用提取自源码的真实函数、只 stub 底层请求，探针得到：

```text
apiRemote('/tasks/task_demo/structures/optimized')
  → /api/v1/tasks/task_demo/structures/optimized，60000ms
apiV2('/tasks/task_demo/structures/optimized', {timeoutMs: API_REMOTE_TIMEOUT_MS})
  → /api/v2/tasks/task_demo/structures/optimized，60000ms
```

任务 6 应明确采用保留 v2 命名空间的远程封装：例如让远程 helper 支持 API 版本，或为 v2 明确传入远程预算并共享 `apiRequest` 的错误处理；同步记录其符合 ANTI #28 的 60s/AbortError 约束。不要将现有 v2 调用机械替换为当前 `apiRemote()`。

新增一条实际执行的 JS 测试，同时断言完整请求 URL、60000ms 预算和超时错误转换。Python API 测试负责服务端错误分类，不能单独证明浏览器请求封装正确。

**6. 清理收尾条件。**

第 84 行明确没有额外人工质量确认，第 278 行却要求等待用户明确同意才能宣布完成。建议将后者改为“F1–F4 和必需检查通过后汇报完成及证据；未验证的必需浏览器场景保持 NOT_VERIFIED，不能据此宣布验收完成”。提交、部署、服务重启是否授权独立判断，不用额外确认代替质量证据。

**建议的修订顺序与完成标准。**

1. 先统一依赖表和 R0 绿色门槛；不增加任务数量。
2. 将科学/发布边界、模式集合、前端执行版本和 v2 远程封装补入 R0 契约定义，并分别补入任务 4/12、5/14、6/11 的验收。
3. 删除冲突的额外确认句，校验每个新增条件都能落到已有交付单元的行为测试。
4. 无环检查通过、R0/R1 门槛可同时满足、三个边界有确定的数据来源和反例测试后，即可按现有 R-unit 结构实施。

本次验证运行了计划依赖检查、真实 Pydantic 模型字段检查、频率发布/模式完整性探针和源码 JS 函数执行；均为隔离探针，没有调用真实 QC、启动 API 服务或执行整改。未运行业务全套 pytest，因此不声明现有系统已修复。

可复现脚本与输出保存在 [.omo/evidence/acp-runtime-contract-remediation/recheck/probe.py](<repo>/.omo/evidence/acp-runtime-contract-remediation/recheck/probe.py) 和 [probe-output.json](<repo>/.omo/evidence/acp-runtime-contract-remediation/recheck/probe-output.json)。运行：`PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python .omo/evidence/acp-runtime-contract-remediation/recheck/probe.py`。这些文件保持 `.omo/` 本地未跟踪状态。
