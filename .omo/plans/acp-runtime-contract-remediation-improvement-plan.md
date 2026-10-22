# ACP runtime-contract 整改计划评审与改进方案

评审日期：2026-10-08。代码基线：`ec3bbb149389ed59834d04f8ffe8e2591b35c0ed`，另含现有用户工作区改动。

原计划：`.omo/plans/acp-runtime-contract-remediation.md`，SHA-256：`b3df23912bd024120caac44f0f16b14956a9d40cd56978904eb0e19ad16273f5`。

**结论：九项整改方向合理，但建议修改后执行。** 原计划对问题驻点、架构边界、用户可见结果的描述大体准确；CAS 后执行副作用、复用 manager 的缓存、集合不截断成单帧、lifespan 管理调度器，以及执行真实 JS 构造器的验证方向均应保留。主要缺陷在实现闭环：副作用缺少跨 attempt 隔离，报告契约前后冲突，恢复凭据不够确定，最佳努力同步与立即一致的承诺不符，远程下载只覆盖已缓存文件，scan 集成测试没有覆盖实际提交入口。

建议保持九项用户结果及现有四层架构，不增加工作流、CLI 参数、数据库表或列。调度与科学恢复部分按高风险安排，不能仅以整份计划的“Medium”概括。

**1. 本次核验的范围与证据**

已阅读原计划及 scheduler、API、ACP、cccp、tests 的相关约束，对照任务所引用的代码。以下小型复现全部使用 `/tmp` 临时目录、合成数据和 fake/mocked QC；未启动生产服务，未执行真实 QC，也未运行完整 pytest 套件。

| 核验 | 观察结果 | 对计划的影响 |
| --- | --- | --- |
| TS Mode 首跑、再次运行、篡改 checkpoint 后再运行 | 首跑 9 个模式均有向量；再次运行仍有 9 个模式但 0 个有向量；篡改优化坐标后 optimize/frequency 调用次数仍各为 1 | GAP-3 确实存在；验收必须同时检查复用决定和消费者数据 |
| TS Mode 请求与 legacy adapter | 来源设为 PBE0/def2-TZVP/D4，两个阶段的 legacy resources 都缺 basis/dispersion；独立 adapter 复现只将 method/basis 放入 MethodSpec，其余 level 字段留在 binding | GAP-2 成立；还需保护 adapter 的双向无损转换 |
| 延迟任务投影 | 先同步新 PAUSED，再同步旧 RUNNING，得到 `jobs=paused, tasks=running` | T4 不能只补调用点；投影本身也必须拒绝过期快照 |
| 远程冷缓存目录索引 | `fetch_catalog` 成功、manifest 已缓存，但远端存在的结构和 `.out` 均未缓存 | T5/T10 的全量预热 fixture 会掩盖下载缺口 |
| scan 参数生成 | 空坐标生成了只有 `--scan-points 1` 的参数；`99,99,nan,inf` 和负索引也原样生成参数 | `scan_method_flags` 是参数生成器，不能作为充分的提交校验 |

主要代码证据：

- [终态提交与重试](<repo>/src/acp/scheduler/manager.py:4756)、[原地重跑](<repo>/src/acp/scheduler/manager.py:913)、[工件捕获](<repo>/src/acp/scheduler/runner.py:1980)。
- [TS Mode 恢复判定](<repo>/src/acp/calculations/tsmode/engine.py:174)、[报告结构](<repo>/src/acp/calculations/tsmode/contracts.py:379)、[IRC 消费者](<repo>/src/acp/calculations/irc/source.py:278)。
- [任务投影更新](<repo>/src/acp/scheduler/tasks.py:393)、[远程目录索引抓取](<repo>/src/acp/results/remote_structure_cache.py:427)、[缓存辅助产物筛选](<repo>/src/acp/results/remote_structure_cache.py:463)。
- [实际 wizard 提交入口](<repo>/frontend/ACP_Workbench_v2.html:30997)、[v2 batch 提交](<repo>/src/acp/api/v2_routes.py:590)、[scan 参数生成](<repo>/src/acp/scheduler/jobs.py:266)。

**2. 执行前应修订的任务要求**

**P0 — T1/T9：CAS 后置还不等于副作用隔离。**

原计划 T1 移动 capture/provenance 是必要修复，但 `_side_effect_lock` 只保护终态处理与重试，rerun 的目录清理、stage/artifact reset 不使用该锁。存在这样的交错：attempt N 完成 CAS → 用户原地重跑到 N+1 → N 的副作用继续捕获或写 job.json、同步 tasks、清理取消事件。marker 的 revision CAS 只能阻止最后的数据库更新，不能撤销已经发生的文件和工件写入。

应将 T1 的要求补为：

1. 为同一 job 的终态副作用与 rerun/edit/continue 清理建立互斥边界，明确锁顺序；执行副作用前在边界内重读当前 attempt/status。不能只增加一次无锁检查。
2. marker、工件和溯源都属于具体 attempt；旧 attempt 的重试直接退出，不得修改新 attempt 的 job.json、tasks、stage provenance 或 cancel event。
3. “恰好一次”改为“副作用可至少执行一次，落库和发布结果幂等”。已有串行机制必须覆盖查询已注册路径到注册完成的全过程；不能单凭 `list_by_job` 加 `snapshot_before` 推导并发唯一性。
4. 重试后从完整 registry 重建 `result.artifacts`，不能只保存本次新增工件。否则“已插入部分工件后崩溃”会让 marker 最终为 true，但 job result 缺少先前已注册工件。
5. 增加插入第一个工件后、写 provenance 后、marker 前三类失败注入；同时断言 registry、job result、stage provenance 一致。

另需明确终态副作用的范围：`runner.poll()` 当前在 manager CAS 前发出 `job.completed/job.failed/job.cancelled` 等终态事件（runner.py:704）。根执行契约要求终态副作用后置；T1 不应仅将该路径标为“context only”。应把终态完成事件纳入后置和幂等处理，保留正常运行期进度观测的独立语义。

**P0 — T2：先冻结报告 JSON 和科学级别的证据来源。**

T2 正文要求保留平面的 `resolved_level` 并增加 sibling `source_level`，但验收要求 `resolved_level.effective_level`。两者不一致。IRC 直接读取 `resolved_level.method/basis` 并与源 bundle 比较；嵌套结构会破坏现有读取。

建议固定为以下兼容形状，删除嵌套 `resolved_level.effective_level` 的验收要求：

```json
{
  "schema_version": "tsmode_report_v1",
  "resolved_level": {"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"},
  "source_level": {"method": "PBE0", "basis": "def2-TZVP", "dispersion": "D4"}
}
```

具体修订：

- 区分来源级别、请求级别、后端输入实际使用的级别；不能将“从 built request 复制”直接命名为已执行级别。将各阶段使用的输入工件及摘要作为证据，未知值显式标为未确认。
- 沿现有转换链检查 `grid → integration_grid → grid`、`scf → scf_convergence`、basis/dispersion/solvent 等全部字段，并覆盖空值、显式 `none`、默认配置冲突和复合方法。
- 如果 adapter 将此前位于 residue 的字段消费进 MethodSpec，`to_legacy_request` 必须同步恢复它们。当前反向转换只显式回写 basis；测试必须证明 round trip 不丢键、不加默认值、保留别名。
- 验收至少到 mock subprocess 下生成的 ORCA 输入，而非只检查请求对象。分别验证 optimize、frequency 和最终成功 rescue attempt 的输入证据。
- 增加 IRC 来源回归：既有受支持级别继续可读；D4/溶剂/网格等当前不支持的 IRC 级别继续明确拒绝。不要为使测试通过而放开 IRC 科学能力门控。

“不改 cccp”可保留为本轮边界，但不能因此把未验证的有效级别写成已执行事实。若发现既有下层接口无法提供必要信息，应单列有证据的后续修复，而不是在 ACP 新建第二套默认值解析器。

**P0 — T3/T11：用确定的恢复凭据，替换目录 glob 和可选的身份方案。**

T3 的“schema marker 和/或 fingerprint helper”“glob 或 step_result”“重算或失败”留给执行者的关键决定过多。目录中存在一个可解析的旧日志，并不能证明其属于当前优化结构和科学级别；只加 schema 字段也不等于采用统一身份规则。

建议冻结 TS Mode checkpoint v2 的阶段凭据，保持公开产物路径和 product id 不变：

- 优化凭据绑定源内容、目标模式、有效级别、优化参数、优化结构内容摘要及必要完成工件。校验坐标为有限的 N×3 数组，原子数与元素顺序符合来源。
- 频率凭据绑定当前采用的优化结构摘要、有效级别，以及确切的频率/模式工件相对路径与内容摘要。验证当前采用的优化结果，不能仅校验 checkpoint 中另一份坐标。
- 新执行时就保存确切 artifact 路径和摘要；旧 checkpoint 缺失凭据时保守重算，不通过 glob 为旧结果补造有效身份。保留旧文件的审计信息，避免未经验证原地“升级”。
- 优化凭据失效 → 重算优化并使下游失效；只有频率凭据失效 → 保留已验证优化、重算频率；仅发布失败 → 从已验证科学结果重试发布。将该决策表直接写进 T3。
- 优先读取既有 frequency wrapper 发布的 `normal_modes` 工件（数据来自 `FrequencyPayload.analysis`）；恢复路径重放已验证的规范数据。若保留日志兼容读取，必须使用准确路径和同一共享解析入口，不能扩展第二套科学解析。
- 频率表非空不足以验证模式完整：检查模式编号、每个必要模式的 N×3 有限位移、向量与几何绑定。新跑与恢复采用相同完成条件。

除现有正常恢复/坐标篡改场景，再补：同路径日志内容替换、可解析但属于旧结构的日志、残缺向量、优化坐标形状错误、有效默认配置变化、旧 schema、仅发布失败。不得以 `manual_review` 或 warning 把必需模式工件缺失判为普通 completed。

**P1 — T4：明确采用最终一致，并防止旧投影覆盖新状态。**

“每次转换后两表一致”与“同步失败非致命、较慢 reconcile 兜底”不能同时作为严格保证。本轮建议继续以 jobs 为权威，tasks 为可修复投影，并将 IS-4 改为：正常路径即时投影；投影失败允许有界滞后；过期快照不得覆盖较新的状态；漂移在约定扫描期限内收敛。

具体改进：

- `sync_job_transition` 应在同数据库的短事务中从当前 jobs 行投影，或采用等价的版本条件更新；不能继续无条件使用传入旧 JobRecord 的状态。
- 明确状态筛选、归档门控和统计在投影滞后期的读取策略。需要“始终显示同一真相”的字段可从 jobs 联查；不能只把列表显示改新而让筛选与计数继续读旧值。
- reconcile 使用分页的漂移查询并覆盖缺失 tasks 行，避免每个 job 一次查询，避免固定 limit 反复扫描同一批导致老任务饥饿。
- 将批大小、扫描频率和全量修复上界写成明确规则，例如每轮最多 B 个漂移、健康数据库中 N 个漂移在不超过 `ceil(N/B)` 次扫描后修复；不能同时只测“一轮修复一个”而声称规模有界。
- 保护 custom_name/name_revision、tags、archived 等组织字段；reconcile 只更新拥有的投影字段。

验收覆盖全部生命周期状态，以及 `新 PAUSED 同步 → 延迟旧 RUNNING 同步`、terminal→rerun 的旧完成投影、同步失败后恢复和超过一批漂移。若坚持立即强一致，则应改选 CAS 与窄字段投影同事务，并明确改变“同步失败非致命”的要求；无须为此增加数据库列。

**P1 — T5/T10：补齐冷缓存文件下载与目录树语义。**

`fetch_catalog` 明确保持几何懒加载，辅助产物只抓取 `frequency_modes`。读取 cache root 并不能让缺失的 structure、普通 file 或 WORK 文件自动出现；将这些文件预先放入测试缓存会产生假闭环。

应拆成三个明确能力：

1. manifest/小目录索引通过既有 singleton cache 读取。
2. 结构、频率和通用文件：安全规范化相对路径 → 读取缓存 → 缺失时 `cache.fetch(record, rel_path)` → 从受控缓存返回文件。SFTP 前后均保持路径边界，不写 task work_dir。
3. 目录树：使用既有远程 listing 能力，或明确只展示 manifest 注册的产品。完整 RESULT/WORK 文件树不能用部分缓存目录冒充；应将实际支持的范围写入端点契约。

T10 必须包含“远端有文件、缓存只有 manifest”的 fake fetcher，以及首次下载、再次命中、并发请求、远端确实缺失、传输失败、恶意路径、WORK listing。区分无文件和远程连接失败。会发起 SFTP 的前端调用也须检查远程超时设置，符合根 ANTI #28。

**P1 — T6：所有权需要覆盖初始化失败和停止全过程。**

移动 manager 构造到 lifespan 是正确方向，但当前 `shutdown()` 在停止 poll/reconcile/prefetch 等线程之前就删除 `.manager.lock`（manager.py:2774）。仅增加同进程 registry 并在 shutdown 清除，会保留新旧 manager 重叠运行的窗口。

T6 应补齐：

- `create_app()` 可创建多个无调度器的 app 对象；第二个同时进入相同 run_root 的 lifespan 才拒绝。顺序启动、关闭、再启动应成功。修正当前验收中 factory 与 lifespan 混用的描述。
- 使用解析后的 run_root 作为所有权键，在初始化开始前原子预留；构造失败需释放已获得的锁、registry 项和已启动资源。
- shutdown 先停止新调度并停止/等待各后台工作，再释放 registry 与文件锁；释放时校验仍属于自身。若工作线程未停止，不能静默将同一目录交给新 manager。
- 测试并发 lifespan 入口、相对路径/符号链接别名、不同 run_root 独立启动、初始化中途失败、重复 shutdown、慢线程停止，以及 stale same-PID 文件恢复。

建议将生命周期和 fixture 迁移放在行为改动之前，避免后续各组测试建立在即将失效的 app 构造方式上。现有 T6→T4 的依赖仅来自同文件编辑安排，并非业务前置条件。

**P1 — T8/T12：测试真实 wizard 入口，并增加充分的提交边界校验。**

Workbench v2 的 `submitJobBatch()` 实际逐个 POST `/api/v1/jobs`，不是 POST `/api/v2/tasks/batch`。T12 走后者或只测 runner spec，无法证明真实 wizard 到提交边界可用。v2 batch 当前也没有调用 scan 校验；v1 用户新增的 `_validate_scan_submission` 只调用参数生成器，不能保证坐标合法。

建议：

- 首轮明确只交付距离扫描；angle/dihedral 后续需证明完整 CLI、local/remote runner、任务解析和渲染链后再开放，不能仅凭 legacy parser 支持来判断。
- 执行真实 wizard 构造器，捕获最终 POST `/api/v1/jobs` body，再进入真实 v1 API 校验和 fake 调度器，断言命令参数。v2 batch 作为附加兼容场景；非法元素进入该接口既有 `failed[]`，不能混写为整请求必须 422。
- 前后端校验覆盖：坐标非空，整数且互异的原子索引，已知原子数范围，有限的起止值，距离取值有效，点数为整数且至少 2。复用现有合法性规则，参数生成器不兼任验证器。
- 显示编号采用 1-based，提交统一转换为 0-based 字符串；明确转换仅发生一次。扫描索引绑定最终提交结构的原子顺序；更换结构或批量选择不同结构必须重新验证，不能用一个预览的 atom_count 代表全部输入。
- 将统一校验放到可共享的 ACP 提交边界，供 v1 和 v2 使用；这属于校验补全，不改变 CLI/计算契约。对 v1 的用户已有 hunk 做保留式增量调整，不能覆盖。
- 覆盖草稿恢复、编辑重算回填、切换到其他 workflow 后不携带 scan 参数；无可确认结构时给出明确校验结果，不能提交无法核对的原子选择。

**P2 — T7：集合识别应是统一策略，审计信息不能在过滤中丢失。**

既有 `auto_reusable=false` 足以表达集合不是可直接复用的单结构。应明确同时覆盖 `all_conformers` 与 `confsearch_final_conformers` 的历史无 metadata 文件，新 writer 写明集合语义；不要把所有 `single_geometry()` 失败都解释为集合并跳过。

同一策略供历史结果保留、结构来源索引和远程预取消费。Confsearch 的逐构象扩展保留；集合仍可查看和下载。明确 `resolve_previous_outputs` 如何传回 skipped collection 的 id/path/reason，同时保持现有返回列表的调用者兼容，最终落入 `attempt_history`。集合混合单帧、只有集合、损坏单帧、本地/远程、两种集合 id 都应有验收。

T7 要写 attempt_history 就会修改 manager.py，因此原 Wave 1 的“disjoint files”并不成立；该任务与 T1/T4/T6 应串行合入。

**P2 — T13：缓存新鲜度与渲染失效都要覆盖。**

按 status 比较缓存和操作后删除条目可修复当前截图问题，但尚未覆盖：删除缓存后旧 detail 响应晚到、同 status 下 attempt 已改变、recovery 更新但列表 renderKey 未改变。

应补上 detail 请求代次或等价的过期响应拒绝机制；可获得 attempt/revision 时纳入新鲜度判断，并覆盖所有行按钮与 batch 操作的缓存失效。队列行的 recovery 变化也应触发更新。终态不显示 active-only 动作，同时保留新鲜服务端 recovery 对能力的决定权。

T13 的真实行为验证依赖 T4 返回新状态、T6 的浏览器服务 fixture 迁移，应写入依赖矩阵。新增测试包含未选中行 pause→resume、terminal rerun 菜单、延迟旧响应，以及不为每行每轮 poll 请求 detail 的调用次数断言。

**3. 建议替换原执行编排**

不要用“每波至少 3 项/目标 5–8 项”决定拆分。按用户结果和文件冲突安排，测试随功能完成；T9–T12 可保留为跨模块验收编号，但不必强制形成四个单独的 test-only 提交。

| 顺序/交付单元 | 原任务映射 | 前置条件 | 完成标志 |
| --- | --- | --- | --- |
| R0 基线与契约冻结 | 新增 T0 | 无 | 记录 HEAD/原计划摘要/用户 diff；冻结报告 JSON、恢复决策、状态一致性、remote cold-read 与 scan 边界；登记确定性复现 |
| R1 生命周期与测试设施 | T6 | R0 | import 无调度线程；并发同根拒绝；初始化失败与关闭可恢复；相关 fixture 已迁移 |
| R2 终态副作用 | T1 + T9 相关验收 | R1 | CAS 拒绝零终态副作用；部分失败幂等；旧 attempt 不污染重跑 |
| R3 jobs/tasks 投影 | T4 + T9 相关验收 | R2 | 旧快照不回写；正常即时、失败有界收敛；读取/筛选/统计策略明确 |
| R4 TS Mode 级别 | T2 + IRC 兼容验收 | R1 | 双向转换无损；两阶段输入与报告证据对应；平面 resolved_level 兼容 |
| R5 TS Mode 恢复 | T3 + T11 | R4 | v2 阶段凭据及重算决策实现；恢复与首跑模式等价；发布可独立重试 |
| R6 远程结果读取 | T5 + T10 | R1 | 只有 manifest 的缓存也能首次下载；目录树范围、错误与安全路径经过验证 |
| R7 集合结果重算 | T7 | R3 | 两种历史集合均可跳过并记录；单帧保留；损坏单帧仍阻断 |
| R8 scan 创建闭环 | T8 + T12 | R1；与 R6 的 v2 文件编辑串行 | 浏览器构造实际 v1 body；校验与 local/remote 命令一致；附加 v2 per-item 验证 |
| R9 队列动作刷新 | T13 | R3、R8 | 未选中行及延迟响应场景正确；新鲜 recovery 生效；无每行轮询 detail |
| R10 最终回归 | F1–F4 | R2–R9 全部完成 | 每个 IS 有行为证据；架构/快速套件及浏览器专项检查通过 |

R4→R5、R6 与 scheduler 交付链可以独立安排，但共享文件的改动应串行集成。若保留原测试文件，T10/T12 的同文件顺序也必须保留；可以用职责明确的新测试文件消除人为依赖。不要把文件编辑顺序描述成业务依赖。

**4. 验收矩阵应增加的关键反例**

| IS | 必须证明的反例/恢复情况 |
| --- | --- |
| IS-1 | CAS 拒绝；部分 capture 崩溃；marker 写失败；terminal CAS 后立即 rerun；旧完成事件重放 |
| IS-2 | 来源与 config 冲突；adapter round trip；请求与渲染输入不同；平面报告被 IRC 读取；不支持级别继续被门控 |
| IS-3 | 坐标/日志/模式工件篡改；日志可解析但身份错误；旧 schema；向量不完整；仅发布失败不重跑科学计算 |
| IS-4 | 新投影后延迟旧投影；同步异常；任务行缺失；多批漂移；组织字段不被同步覆盖 |
| IS-5 | 缓存只有 manifest；远端有未缓存文件；并发冷下载；WORK 树；远端缺失与断连；路径越界 |
| IS-6 | 并发 lifespan；factory 重复创建；同根别名；初始化失败；shutdown 未完成时的二次启动 |
| IS-7 | 两种历史集合；只有集合；集合加单帧；坏单帧；remote 预取与保留策略一致 |
| IS-8 | 实际 v1 POST；空/重复/负数/越界索引；非有限数值；点数不足；编号只转换一次；批量与切换结构 |
| IS-9 | 未选中行 pause→resume；terminal rerun 菜单；旧 detail 晚到；recovery 变化触发渲染；请求次数有界 |

最终命令门至少包含：

```bash
<venv>/bin/python -m pytest -m "not slow" -q
<venv>/bin/python -m pytest tests/test_architecture_invariants.py tests/test_f4_scope_audit.py tests/test_grep_gates_script.py -q
<venv>/bin/python -m pytest --run-slow tests/test_frontend_interaction.py -q
```

每个交付先跑与其修改匹配的 targeted tests；全快速套件只在设施迁移后和最终集成等有必要的时点执行。前端纯逻辑进入可执行 JS 测试，浏览器专项证明控件绑定、真实提交和行按钮；静态 source pins 仅作补充。浏览器必需场景跳过应记录 NOT_VERIFIED，不算验收通过。真实 QC 未运行也不能被描述为实际科学计算已验证。

**5. 原计划的流程文字需同步修正**

- 删除“零人工干预”与“完成前再等用户 explicit okay”的冲突。可逆修改和测试按既有授权推进；提交、部署等另按用户授权处理，不把额外确认当作质量验收。
- 明确 `.omo/` 仅保存本地计划与证据，保持不被 git 跟踪。当前 `test_f4_scope_audit.py::test_omo_no_new_files` 明确禁止 index 中出现 `.omo/`；原计划的“若约定允许可提交”应改为确定规则。
- 将 doc 更新分配到具体交付：scheduler 契约归 R1–R3/R7；API read-root/提交边界归 R6/R8；TS Mode 报告与 checkpoint 归 R4/R5。既有根执行不变量已经正确，应补充实现说明，不能通过改写不变量降低要求。
- 保留用户已有 `v1_routes.py`、`test_acp_api.py`、`test_scan_orca_manual_acceptance.py` 的改动与 `grad.engrad`；实现阶段新增补丁需基于现有 diff，不能重置或覆盖。原计划与已有审核账本也应保留；本文件是新的评审输入，不改写历史批准记录。
- 用交付结果决定提交粒度，取消固定 13 个提交的约束。新发现的缺陷应回到对应交付闭环修复，不需要等最终审查才形成下一轮计划。

本次仅新增此评审与改进方案，原整改计划及业务代码未修改。
