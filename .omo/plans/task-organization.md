# task-organization - Work Plan

## TL;DR (For humans)

**What you'll get:** 项目内的任务列表升级为"分组浏览"：默认按分子折叠分组、组内按创建时间倒序；顶部一个工具栏提供搜索、分组方式切换、排序选择和多维筛选（不同维度取交集、同维度多选取并集）；完成/排队任务卡片精简（无进度条、无占位符）。第二阶段加入手动标签（可重命名/合并/批量添加）、分子别名与合并提示、归档；第三阶段加入保存视图、自动打标规则和任务来源链路浏览。

**Why this approach:** 分组、筛选、排序是三种独立操作，全部在服务端基于整个项目计算（计数不受分页影响）；"分子/备注/类型/批次"是自动属性，直接从已有字段派生（分子名、备注、工作流、`resources.batch_id`），不另存一份会失同步的文本 TAG；手动标签复用任务索引表新增的 tags 列，与结构 TS/INT TAG、节点 node_tags、实时状态完全隔离。数据基座复用已有的 `tasks` 索引表（它已带分子/备注/工作流专用列），提交事实 `spec_json` 只读不动。

**What it will NOT do:** 不移动/复制任务、不改计算目录、不改任务状态语义；不自动合并大小写或近似分子名（只提示）；不按完成时间自动归档；不从长任务标题截取分子名；不做多标签交叉组合分组；不给 v1 `/api/v1/jobs` 增加新参数。

**Effort:** XL（13 个实现任务，三个交付阶段）
**Risk:** Medium — 主要风险集中在数据层：tasks 表同步语义改造（防覆盖用户编辑）、一次性历史回填、以及 25848 行单文件前端的大范围改造。
**Decisions to sanity-check:** ① 批次分组复用前端已生成的 `resources.batch_id`（不新建列）；② tasks 表 `upsert` 改为 ON CONFLICT 白名单更新（"首写胜出"保护用户编辑）；③ 名称/拼音排序放在浏览器端 `Intl.Collator`，服务端只做时间排序；④ 视图偏好存浏览器 localStorage（按项目隔离），第三阶段保存视图才上服务端。

Your next move: 阅读下方计划后启动执行（`$start-work task-organization`），或先要求一轮高精度复审。

---

> TL;DR (machine): XL / Medium risk / 13 todos in 6 waves + 4 final verifiers — tasks-index-based whole-project group/filter/sort engine + v2 task-view API + Workbench grouped browser, 3 delivery phases.

## Scope
### Must have
- **P1（第一阶段）**：按 分子/备注/任务类型/提交批次/不分组 分组（默认分子、组内创建时间倒序）；排序：创建时间（新→旧/旧→新）、完成时间（新→旧）、名称 A→Z/Z→A（自然排序 TS2<TS10，中文拼音）、最近活动（仅实质状态/阶段变化，非轮询时间）；搜索（分子/任务名/备注/显示名）；状态多选筛选；完成任务卡片精简；每项目记忆分组/筛选/排序（localStorage）。
- **P1（数据与同步）**：tasks 索引表扩列（molecule_key/tags/archived/batch_id/last_activity_at/started_at/completed_at/group_id/progress）+ 迁移 014 + 历史回填（含 spec_json 解析）；upsert 改 ON CONFLICT 白名单；purge 级联删 tasks；move_job 同步 tasks.project_id；状态迁移写入 started_at/completed_at/last_activity_at（compare-before-write）。
- **P2（第二阶段）**：手动标签（提交时从 spec.tags 播种，此后独立演化）+ 标签清单/重命名/合并/删除；批量操作（加/删标签、归档/取消归档、设置分子归属）；分子别名与合并提示（仅提示不自动合并）；归档（仅终态任务、只影响默认可见性、"包含归档/仅归档"开关）；导出当前筛选结果。
- **P3（第三阶段）**：保存视图（服务端 projects.settings，活查询非快照）；用户自定义自动打标规则（仅用户显式建立，含手动回填）；来源链路只读浏览（上游 from-job 递归 + 下游反向发现）。
- **通用**：分组计数/筛选/排序/总建立在**整个项目**（含跨项目"全部"范围）计算，不受分页/截断影响；多标签去重——按分子/备注/类型/批次分组时每任务恰属一组，标签筛选天然去重，按标签分组允许一任务多组但 total 与批量选择按任务 ID 去重；i18n 双语（zh-CN/en-US）+ `test_frontend_sync.py` 契约锁定。
- 批次分组键 = `spec.resources["batch_id"]`（前端批量提交已生成共享 id，`ACP_Workbench_v2.html:23207/23593`；manager `_batch_slot_available` 已消费，`manager.py:1747-1793`）；v2 `/tasks/batch` 每请求生成共享 id 注入 resources。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不新增 `jobs.batch_id` 列（避免与 `resources.batch_id` 双重事实、避免名称冲突）；不修改 `_batch_slot_available` 现有行为。
- 不移动/复制任务、不修改计算目录结构、不修改 `jobs.status` 语义与状态机。
- 不把 完成/失败/运行中 等实时状态变成永久标签；状态仅作实时筛选 facet。
- 不把 node_tags 或结构 INT/TS TAG 混入整理标签；整理标签与结果面板 TAG 徽章视觉/数据分离。
- 不自动合并大小写不同/拼写近似的分子名（仅 suggestions 提示）；不按时间窗口伪造历史批次（无 batch_id 的历史任务归"单独提交"组）。
- 不根据完成时间自动归档；归档不改变计算状态/文件/来源关联。
- 不从长任务标题截取分子名（分组键只来自 molecule_name 字段经规范化）；长备注不按词拆标签。
- 第一版不做多标签交叉组合分组。
- 不回写 `spec_json`（整理元数据只落 tasks 显示层）；v1 `/api/v1/jobs` 既有参数与行为不变。
- 不引入新的 Python/JS 依赖（拼音/自然排序用浏览器内建 `Intl.Collator`；JSON 处理用 SQLite 内建 json_each + 探测 + Python fallback）。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（每个 todo 内实现+测试一体交付；前端用源码契约测试 `tests/test_frontend_sync.py` 模式 + node 冒烟 `skipif` 无 node）+ framework: pytest（`pytest -m "not slow"`，CI 同款）。
- Lint: `ruff check src tests` + `ruff format --check src tests`（只要求新增/改动文件零新违规）。
- Evidence: `.omo/evidence/task-organization/task-<N>.md`（每 todo 的 QA 记录：命令、输出摘要、结论）。
- 关键回归面：迁移幂等（重复 migrate 不变）、同步防覆盖（编辑后触发状态迁移/远程 resync 不丢）、purge 幻影行（删除后 task-view 不再出现）、整项目计数（截断后 counts/total 不变）、v1 `/jobs` 行为不变（既有测试全绿）。

## Execution strategy
### Parallel execution waves
- **Wave 1**: T1（数据基座：迁移 014 + 回填 + TaskIndex 改造 + purge/move 接线）
- **Wave 2**: T2（task_views.py 查询引擎）
- **Wave 3**: T3（v2 task-view/PATCH/batch 端点）
- **Wave 4**: T4（前端数据层） ∥ T12（来源链路 API，只读、独立）
- **Wave 5**: T5（前端工具栏与筛选） ∥ T6（前端分组列表与卡片精简） ∥ T7（标签与批量操作 API） ∥ T8（分子别名 migration 015 + API）
- **Wave 6**: T9（前端 P2 面板） ∥ T10（保存视图） ∥ T11（自动打标规则） ∥ T13（文档与 AGENTS 同步）
- **Final**: F1–F4 并行验证

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | — | T2,T3,T7,T8,T11 | — |
| T2 | T1 | T3 | — |
| T3 | T2 | T4,T5,T6,T7,T8,T9,T10,T11,T12 | — |
| T4 | T3 | T5,T6 | T12 |
| T5 | T4 | T9 | T6,T7,T8 |
| T6 | T4 | T9 | T5,T7,T8 |
| T7 | T1,T3 | T9 | T5,T6,T8 |
| T8 | T1,T3 | T9 | T5,T6,T7 |
| T9 | T5,T6,T7,T8 | — | T10,T11,T13 |
| T10 | T3,T5 | — | T9,T11,T13 |
| T11 | T1,T3 | — | T9,T10,T13 |
| T12 | T3 | — | T4 |
| T13 | T1,T2,T3 | — | T9,T10,T11 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. 数据基座：迁移 014 + 历史回填 + TaskIndex 防覆盖改造 + purge/move 接线
  What to do:
  a) `src/acp/scheduler/naming.py` 新增 `molecule_group_key(value: str) -> str`：`" ".join(value.split()).casefold()`（去首尾空白+折叠连续空白+casefold；空串原样返回）。保留现有 `canonical_molecule_name` 不动。
  b) `src/acp/scheduler/migrations.py` 追加迁移 `014`（追加在列表末尾、id "014"、注册进 `_apply_migration` 分派）：① `ALTER TABLE tasks` 逐列添加（先 `PRAGMA table_info(tasks)` 探测，模式照抄 `_apply_jobs_group_id_column`，`migrations.py:258-272`）：`molecule_key TEXT NOT NULL DEFAULT ''`、`tags TEXT NOT NULL DEFAULT '[]'`、`archived INTEGER NOT NULL DEFAULT 0`、`batch_id TEXT`、`last_activity_at TEXT`、`started_at TEXT`、`completed_at TEXT`、`group_id TEXT`、`progress REAL`；② 索引 `idx_tasks_project_archived(project_id, archived)`、`idx_tasks_molecule_key(molecule_key)`、`idx_tasks_batch_id(batch_id)`；③ Python 回填 `_backfill_tasks_from_jobs(conn)`：guard `_table_exists('tasks')`（迁移 010 保证其存在，`migrations.py:139-166`；`JobStore` 先于 `TaskIndex` 触发 migrate——`store.py:73` vs `manager.py:201`）；遍历 `jobs` 行，对 `tasks` 缺失的 task_id 用 Python 解析 `spec_json`（`json.loads`；jobs 表无这些列，`store.py:26-51`；解析参照 `_row_to_record`，`store.py:481-509`）派生：`molecule_name/task_name/remark/workflow`（spec 字段）、`tags`（`spec.tags` JSON 序列化，缺省 `'[]'`）、`molecule_key`（`molecule_group_key(molecule_name)`）、`batch_id`（`spec.resources["batch_id"]`，无则 NULL——**不伪造**）、`group_id`（拷贝 `jobs.group_id`，**不是** `jobs.id`）、`display_name/task_dir_name`（`Path(work_dir).name`，参照 `tasks.py:225-227`）、`status/started_at/completed_at`（jobs 列直拷）、`last_activity_at`（`COALESCE(completed_at, started_at, created_at)`）、`progress`（jobs 列）、`storage_mode`（remote_job_id 非空→`'sftp'`）、`node_path`（work_dir）；仅 INSERT 缺失行（不动已有行），幂等（重复执行结果不变）。
  c) `src/acp/scheduler/tasks.py`：⓪ 先扩展 `_TASK_COLUMNS`（`tasks.py:52-72`）与 `_COLUMN_DEFAULTS`（`:75-79`）纳入全部新列（INSERT 列集 + 默认值 `''`/`'[]'`/`0`）；① `upsert()` 从 `INSERT OR REPLACE` 改为 `INSERT INTO tasks (...) VALUES (...) ON CONFLICT(task_id) DO UPDATE SET <白名单>`（现语句在 `tasks.py:157-162`；REPLACE 会把未列出列重置为默认值——CRITICAL）。白名单（DO UPDATE 集）＝ sync 拥有的派生/生命周期列：`display_name, task_dir_name, workflow, status, current_stage, node_id, node_path, storage_mode, layout_version, input_hash, result_manifest_path, updated_at`；**首写胜出列**（不进 DO UPDATE）：`project_id, molecule_name, task_name, remark, molecule_key, tags, archived, batch_id, created_at`。② 新方法 `sync_job_transition(self, record: JobRecord) -> None`：读现行行（`SELECT status, current_stage FROM tasks WHERE task_id=?`）；无行则 fallback `sync_from_job(record)`；有行时 compare-before-write——status 或 current_stage 变化 → `UPDATE tasks SET status=?, current_stage=?, started_at=COALESCE(started_at,?), completed_at=?, last_activity_at=?, updated_at=? WHERE task_id=?`（completed_at 仅终态写入，last_activity_at=now）；仅 progress 变化 → 只更新 progress/updated_at（**不动 last_activity_at**）；均无变化 → 不写（消除每轮询写放大）。③ 新方法 `delete(self, task_id: str)`、`update_project(self, task_id: str, project_id: str)`。
  d) `src/acp/scheduler/manager.py` 接线：`_sync_task_status`（`manager.py:1600-1607`）改调 `sync_job_transition`；`manager.py:542`（提交）保持 `sync_from_job`（INSERT 路径）；`manager.py:1913`（远程 dispatch resync）保持 `sync_from_job`——现因白名单安全，不再覆盖用户编辑；`move_job`（`manager.py:560-602`）在 jobs 更新后追加 `self.tasks.update_project(job_id, new_project_id)`（tasks 可用性判空照抄 542 处模式）。
  e) `src/acp/scheduler/store.py`：`purge_cascade`（`store.py:315-334`）在删 jobs 前追加 `DELETE FROM tasks WHERE job_id=?`（purge 幻影行修复）。
  Must NOT do: 不加 jobs.batch_id 列；不动 `_batch_slot_available`；不动 `sync_from_job` 的 INSERT 列集之外的任何行为；不修改 v1/v2 任何端点。
  Parallelization: Wave 1 | Blocked by: — | Blocks: T2,T3,T7,T8,T11
  References (executor has NO interview context - be exhaustive): `src/acp/scheduler/tasks.py:27-49`(schema), `:52-79`(列/默认), `:142-162`(upsert INSERT OR REPLACE), `:164-193`(get/list/update_status), `:199-239`(sync_from_job 列来源)；`src/acp/scheduler/migrations.py:24-45`(_MIGRATIONS 结构+分派), `:139-166`(010 tasks), `:258-272`(008 Python-ALTER 模式), `:187-191`(末号 013)；`src/acp/scheduler/store.py:26-51`(jobs schema), `:73`(migrate 触点), `:315-334`(purge_cascade), `:481-509`(_row_to_record spec 解析)；`src/acp/scheduler/manager.py:199-201`(TaskIndex 构造), `:537`(group_id=group_id or job_id), `:542/1913`(sync 接线), `:560-602`(move_job), `:881-883`(_purge_job_records), `:1600-1607`(_sync_task_status), `:2189-2193`(每轮询调用点)；`src/acp/scheduler/naming.py:16-30`(canonical_molecule_name)；`src/acp/scheduler/jobs.py:567-644`(JobSpec tags/molecule_name/task_name/remark/resources), `:646-697`(JobRecord)
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_scheduler_task_index_org.py` 并全绿：① 幂等——构造含 5 条 jobs（2 条带 `resources.batch_id`、3 条不带；1 条 `group_id≠id`）的临时 DB，`migrate()` 跑两遍，断言 tasks 行数/内容逐字段一致、`batch_id` 仅 2 条非 NULL、`group_id` 等于 jobs.group_id；② 防覆盖——插入 tasks 行后手工改 `remark/tags/archived`，调用 `sync_from_job`（模拟 1913 resync）与 `sync_job_transition`（status 变化），断言 `remark/tags/archived/molecule_name` 未被重置、`display_name/task_dir_name` 随 work_dir 更新；③ compare-before-write——同 status/current_stage 连调 `sync_job_transition` 两次，断言 `last_activity_at/updated_at` 不变；status 变化时 `last_activity_at` 更新且 `started_at` 首次写入；④ purge——`purge_cascade` 后 tasks 无残留；⑤ move——`update_project` 后 project_id 一致。命令：`pytest tests/test_acp_scheduler_task_index_org.py -v`（全库回归：`pytest -m "not slow" -q`）。
  QA scenarios (name the exact tool + invocation): happy——临时 DB 迁移+回填+同步全链路断言（命令同上，全绿）；failure——损坏 spec_json（非法 JSON）的 jobs 行回填不崩溃（json.loads 失败 → tags='[]'/molecule_key=''，logger.warning，任务仍入行）；DELETE 未知 task_id 的 `delete()` 不抛错。Evidence `.omo/evidence/task-organization/task-1.md`
  Commit: Y | feat(scheduler): tasks org columns + migration 014 backfill + overwrite-safe TaskIndex

- [x] 2. 查询引擎：scheduler/task_views.py（整项目 分组/筛选/排序/计数/facets）
  What to do: 新建 `src/acp/scheduler/task_views.py`（纯查询模块，无写路径）：
  a) 契约类型（全部 `@dataclass(frozen=True)` + `from __future__ import annotations`，匹配包风格）：`GroupBy(str, Enum)`＝`molecule|remark|workflow|batch|tag|none`；`TaskSort(str, Enum)`＝`created_desc|created_asc|completed_desc|activity_desc|name_asc|name_desc`；`ArchivedFilter(str, Enum)`＝`exclude|include|only`；`TaskViewQuery`（project_id: `str | None`——None=跨项目"全部"范围、group_by、sort、statuses/workflows/molecule_keys/tags/batch_ids/remarks: `tuple[str, ...]`、search: `str`、archived、running_first: `bool`、group_limit: `int = 200`、max_total: `int = 5000`）。
  b) `query_project_tasks(index: TaskIndex, q: TaskViewQuery) -> dict[str, Any]`：单入口。SQL FROM `tasks`（JOIN `jobs j ON j.id=tasks.task_id` 取 `error` 不取——行保持轻量，仅取 tasks 列 + `j.error` 供详情页外提示？**不取 error**，行轻量）。WHERE 构造：`project_id=?`（q 非 None 时）；archived＝exclude→`archived=0`、only→`archived=1`；`status IN (...)`；`workflow IN (...)`；`molecule_key IN (...)`；`batch_id IN (...)`；`remark IN (...)`；标签并集＝多值 OR 的 `EXISTS (SELECT 1 FROM json_each(tasks.tags) WHERE json_each.value IN (...))`——模块导入时一次性探测 `SELECT count(*) FROM json_each('["a"]')`，失败置 `_JSON1_OK=False` 并 fallback Python 端过滤（加载候选行后按 tags 集合过滤，行为一致）；search＝`LOWER(molecule_name) LIKE ? ESCAPE '\' OR ...`（molecule_name/task_name/remark/display_name 四列，参数 `%`/`_`/`\` 转义，`LOWER` 统一两侧）。**同一维度多值=并集（OR/IN），跨维度=交集（AND）**。
  c) 分组：`GROUP BY` 键映射——molecule→`molecule_key`（`''`→哨兵组 `key="__unassigned__"`，`unassigned: true`）；remark→`remark`（`''`→同哨兵）；workflow→`workflow`（组带 `retired: bool`，从 `acp.catalog.WORKFLOW_CATALOG[workflow]["status"]=="retired"` 派生，未知 workflow→`retired: false`）；batch→`batch_id`（NULL→哨兵组 `__singles__`「单独提交」；组带 `min_created_at` 供前端显示批次时间）；tag→`json_each(tasks.tags)` 展开（多组；无标签→哨兵 `__untagged__`；**此视图下同一任务可出现多组**）+ 仅在 `_JSON1_OK` 时支持，否则抛 `ValueError("tag grouping requires SQLite JSON1")`；none→单组 `__all__`。
  d) 计数语义（契约，写入模块 docstring）：`total`＝筛选后去重任务数（`COUNT(DISTINCT task_id)`，**不受 group_limit/max_total 截断影响**）；`counts`＝筛选后整范围状态计数 dict（含 running/queued/completed/failed/paused 全 `JobStatus` 枚举键，兼容前端 `getQueueCounts`）；每 group `count`＝该组任务数——tag 分组时 `sum(group.count) ≥ total`（文档化，UI 以 total 为准，批量选择按任务 ID 去重）；`truncated`＝任一组被 group_limit 截断或总行数达 max_total 时为 true，且每组带 `truncated: bool`。
  e) facets（各维度计数，**排除本维度自身筛选、保留其他维度筛选**——选中某分子后类型 facet 仍显示全类型计数）：`statuses/workflows/molecules[{key,name,count}]/tags[{tag,count}]/batches[{batch_id,min_created_at,count}]`。
  f) 排序：SQL `ORDER BY`——created_desc/asc→`created_at`；completed_desc→`COALESCE(completed_at, created_at) DESC`（未完成任务按创建时间殿后）；activity_desc→`COALESCE(last_activity_at, created_at) DESC`；name_asc/desc→SQL 返回 `created_desc` 序（拼音/自然排序归前端，engine 不做）。组排序＝按组代表值（时间排序→组内最大对应时间；名称排序→组显示名，次级稳定键 `group.key ASC` 防刷新跳动）。`running_first=True` 时对行做**稳定分区**（active 状态在前，区内保持原排序）——作为独立后处理，不混入 ORDER BY。
  g) 行形状 `TaskRow`：字段名**对齐前端现有消费面**（`V1JobRecordModel` 风格）——`id, status, group_id, project_id, project_name, created_at, updated_at, started_at, completed_at, last_activity_at, current_stage, progress, molecule_name, task_name, remark, display_name, task_dir_name, workflow, tags, archived, batch_id` + 兼容嵌套 `spec: {workflow, molecule_name, task_name, remark, tags}`（最小化 `buildQueueRow` 改动）。`project_name` 经 `jobs.project_id` 对照 `projects` 表填充（跨项目视图分组层亦带）。
  Must NOT do: 不做拼音/名称排序（前端职责）；不写任何表；不引入分页 OFFSET（截断保护代替）；tag 分组在无 JSON1 时不静默降级（显式报错）。
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: T3
  References: `src/acp/scheduler/tasks.py`(表列=T1 后)；`src/acp/scheduler/jobs.py:32-61`(JobStatus/is_active/is_terminal)；`src/acp/catalog.py` WORKFLOW_CATALOG(retired 状态)；`src/acp/scheduler/store.py:251-300`(list_enriched 的 JOIN/ORDER 模式参照)；`src/acp/scheduler/projects.py`(projects 表对照)；`frontend/ACP_Workbench_v2.html:13556-13559`(getQueueCounts 形状), `:13678-13745`(buildQueueRow 消费字段)
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_scheduler_task_views.py` 并全绿，覆盖：① 分子分组——同名不同大小写/空白折叠同组、空分子入 `__unassigned__`；② 筛选语义——statuses 多选取并集、molecule+workflow+status 跨维交集；③ 标签并集去重——任务带 ["a","b"] 两标签均选中时只出现一次、total=1；④ 整项目计数——group_limit=2 截断后 total/counts/groups[].count 仍为全量值、truncated=true；⑤ facets 本维排除——选中 molecule 后 workflow facet 计数不变；⑥ 排序——created_asc/completed_desc(NULL 殿后)/activity_desc；running_first 稳定分区；⑦ batch 分组——共享 batch_id 同组、NULL 入 `__singles__`；⑧ search 大小写不敏感 + `%` 字面量转义；⑨ archived exclude/include/only；⑩ tag 分组多组成员 + `sum≥total`；⑪ JSON1 fallback——monkeypatch `_JSON1_OK=False` 后标签过滤结果与开启时一致、tag 分组抛 ValueError；⑫ project_id=None 跨项目。命令：`pytest tests/test_acp_scheduler_task_views.py -v`。
  QA scenarios: happy——10+ 任务构造库覆盖上述全部断言；failure——空项目返回空 groups/total=0；未知 group_by 值由 API 层 422（engine 收到即断言错误）。Evidence `.omo/evidence/task-organization/task-2.md`
  Commit: Y | feat(scheduler): whole-project task view engine (group/filter/sort/facets)

- [x] 3. v2 API：task-view 端点 + PATCH 整理元数据 + batch 共享 batch_id
  What to do:
  a) `src/acp/api/v2_schemas.py` 新增：`V2TaskRowModel`（T2g 行形状）、`V2TaskViewGroupModel`（key/display_name/unassigned/retired/count/truncated/min_created_at/jobs）、`V2TaskViewFacetsModel`、`V2TaskViewResponse`（groups/facets/total/truncated/counts/query 回显）、`V2TaskPatchRequest`（molecule_name/task_name/remark/tags 全 Optional）。
  b) `src/acp/api/v2_routes.py` 新增 `GET /projects/{project_id}/task-view`（project_id 路径参数，另支持 `scope=all` 走跨项目？——否：跨项目用独立 `GET /tasks/view?...`？**决策：单一端点 `GET /api/v2/task-view`，project_id 作为可选 query 参数**，None=全部，避免两条路径）：query 参数 `project_id?/group_by(regex ^(molecule|remark|workflow|batch|tag|none)$)/sort(^created_desc|created_asc|completed_desc|activity_desc|name_asc|name_desc$)/status(逗号分隔)/workflow/molecule/tag/batch/remark(逗号分隔)/q/archived(^exclude|include|only$)/running_first(bool)/group_limit(int ge=1 le=1000 默认 200)`；非法枚举→422（FastAPI pattern）。调用 `query_project_tasks`；对 **active 状态行**逐个 `store.get(id)` + 复用 `_enrich_job_snapshot`（从 `v1_routes` import；LRU 上限 256 只服务 active 行，量级≤并发上限，可承受——若 active>200 仅 enrich 前 200 并记 warning）；404：project_id 给定但项目不存在。
  c) `PATCH /api/v2/tasks/{task_id}`（v2 路由）：body `V2TaskPatchRequest`；仅更新 tasks 显示列——`molecule_name/task_name/remark`（变更时写入）、`tags`（整组替换）；`molecule_name` 变更 → 重算 `molecule_key=molecule_group_key(新值)` 并写入；校验：三文本字段 `len ≤ 200`、tags 每项 `strip()` 后非空且 `len ≤ 32`、总数 `≤ 20`（违规 422）；404 未知 task_id；**不触碰 jobs 表/spec_json/work_dir**。需要 TaskIndex 写方法：在 `tasks.py` 增加 `update_display_fields(task_id, *, molecule_name=None, task_name=None, remark=None, tags=None, molecule_key=None)`（显式列 UPDATE）。
  d) v2 `POST /tasks/batch`（`v2_routes.py:256-278`）：请求级生成 `batch_id = "batch_" + uuid4().hex[:12]`，`_submit_batch_item` 构造 spec 时若 `item.resources` 无 `batch_id` 则注入（`resources = {**item.resources, "batch_id": req_batch_id}`）；失败项不回收 id。
  e) 既有 `GET /projects/{project_id}/tasks`（`v2_routes.py:137-148`）行为不变（向后兼容）。
  Must NOT do: 不改 v1 `/api/v1/jobs`；PATCH 不允许编辑 `display_name/task_dir_name/status/archived`（archived 走 T7 batch-ops 专用语义校验）；不改 `GET /tasks/{task_id}`。
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: T4,T5,T6,T7,T8,T10,T11,T12
  References: `src/acp/api/v2_routes.py:1-30`(router 挂载 /api/v2), `:115-148`(projects/tasks 列表), `:151-154`(get_task), `:256-342`(batch)；`src/acp/api/v2_schemas.py`(现有模型风格)；`src/acp/api/v1_routes.py:231`(_ENRICHMENT_CACHE_LIMIT), `_enrich_job_snapshot` 定义段；`src/acp/scheduler/naming.py`(T1 新增 molecule_group_key)；FastAPI Query pattern 用法参照 `v2_routes.py:157-162`
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_api_v2_task_view.py` 并全绿（TestClient + 临时 run_root fixture，参照现有 API 测试模式）：① task-view happy——默认参数返回按分子分组、组内 created 倒序、counts 含全部状态键；② 非法 group_by/sort/archived → 422；③ project_id 不存在 → 404；④ PATCH happy——改 remark 后 spec_json 原文不变（读 jobs 行断言）、molecule_name 改名后 molecule_key 重算、tags 替换生效；⑤ PATCH failure——未知 id 404、201 字符 remark 422、空串标签项 422、>20 标签 422；⑥ batch——POST /tasks/batch 两项成功后两行 tasks.batch_id 相同且非各自 job_id；⑦ 向后兼容——旧 `GET /projects/{id}/tasks` 响应模型不变（既有测试快照）；⑧ active 行 enrichment——构造 running 任务断言行含 stage_index/stage_total 字段。命令：`pytest tests/test_acp_api_v2_task_view.py -v`。
  QA scenarios: happy——完整 fixture 项目（多分子/多状态/带标签/含归档）全参数组合；failure——引擎抛 ValueError（tag 分组无 JSON1 模拟）→ 500 带清晰 detail；PATCH 并发（两次连续 PATCH 不同字段）最终态一致。Evidence `.omo/evidence/task-organization/task-3.md`
  Commit: Y | feat(api): v2 task-view + task metadata PATCH + batch batch_id

- [x] 4. 前端数据层：apiV2 客户端 + task-view 拉取 + 视图偏好持久化
  What to do（`frontend/ACP_Workbench_v2.html` 单文件）：
  a) 新增 `apiV2(path, opts)` helper（`fetch("/api/v2" + path, ...)`，错误处理/JSON 解析复刻 `api()`（定义于 `:7606` 附近），**不改 `API_BASE`**——它被 ~40 个 v1 调用点依赖）。
  b) 视图状态常量与持久化：`const DEFAULT_TASK_VIEW = { groupBy: "molecule", sort: "created_desc", filters: { status: [], workflow: [], molecule: [], tag: [], batch: [], remark: [] }, q: "", archived: "exclude", runningFirst: false }`；`taskViewPrefs` 变量；`loadTaskViewPrefs(projectId)`/`saveTaskViewPrefs()`——localStorage key `acp.taskview.<project_id>`（"全部"范围用 `acp.taskview.__all__`），JSON 容错（损坏即回默认）；项目切换时加载。
  c) `refreshJobs()` 重写（函数名与调用点签名不变——它被 ~30 处调用：13102/13224/13313/13423/20774/23611 等）：组装 query（group_by/sort/各筛选/q/archived/running_first/group_limit=200；`selectedProjectId` 存在则 project_id，否则省略=跨项目），调 `apiV2("/task-view?" + qs)`；结果存 `taskViewCache = body`；派生 `jobsCache`——按 groups 展开为平铺行数组（保持组序+组内序），**字段形状=T3 行模型**（含兼容 spec 嵌套），使 `buildQueueRow/canonicalTaskName/pruneQueueSelection` 等现有消费者继续工作；`renderQueue(body.counts || {})` 调用不变（counts 形状已兼容 `getQueueCounts`）。
  d) 轮询适配：`startPolling()` Lane 1（`:24391`）保持节拍（active 存在 4s/空闲 15s）；在活跃任务存在时 task-view 全量刷新，纯空闲且无筛选时也保持（计数实时性）。
  Must NOT do: 不改 `API_BASE`/`api()`；不删除 `jobsCache` 及其消费面（渐进替换）；不引入新 JS 依赖/模块系统。
  Parallelization: Wave 4 | Blocked by: T3 | Blocks: T5,T6 | 可与 T12 并行
  References: `frontend/ACP_Workbench_v2.html:4874`(API_BASE), `:7614`(api()), `:13545-13554`(refreshJobs 现状), `:13556-13570`(getQueueCounts/renderQueue), `:4877-4887`(selectedProjectId/jobsCache), `:24391`(startPolling lanes), `:13154`(canonicalTaskName)；`tests/test_frontend_sync.py`(契约测试模式)
  Acceptance criteria (agent-executable): `tests/test_frontend_sync.py` 新增 `test_task_view_data_layer`：断言 HTML 源含 `apiV2` 定义与 `"/api/v2"` 字面量、`DEFAULT_TASK_VIEW` 的 `groupBy: "molecule"` 与 `sort: "created_desc"`、`acp.taskview.` 前缀 localStorage、`refreshJobs` 内不再含 `query.set("limit", "50")` 旧路径、`API_BASE` 仍为 `/api/v1`。命令：`pytest tests/test_frontend_sync.py -v -k task_view_data_layer`。
  QA scenarios: happy——契约断言通过；failure——`loadTaskViewPrefs` 输入损坏 JSON 回默认（契约断言 try/catch 分支存在）。Evidence `.omo/evidence/task-organization/task-4.md`
  Commit: Y | feat(workbench): v2 task-view data layer + per-project view prefs

- [x] 5. 前端工具栏：搜索/分组/排序/筛选面板/筛选 chips
  What to do:
  a) DOM：在 `#queue-summary` 与 `.queue-expanded` 之间（~`:3914`）插入工具栏：搜索框 `#task-view-search`（占位符 i18n「搜索任务、分子、备注…」，300ms debounce）、分组 select `#task-view-group`（分子/备注/任务类型/提交批次/自定义标签/不分组——标签选项 P2 前禁用置灰）、排序 select `#task-view-sort`（创建 新→旧/旧→新、完成 新→旧、名称 A→Z/Z→A、最近活动）、「筛选」按钮 `#task-view-filter-btn`（弹层含 状态/类型/分子/标签/批次 分组 checkbox，由 `taskViewCache.facets` 渲染，多选）、「筛选 N」角标、`#task-view-chips` 行（每个生效条件一个 chip：`label ×` 移除单个 + 「清除」全部）。
  b) 交互：任何变更 → `saveTaskViewPrefs()` + `refreshJobs()`；chips 从当前 prefs 渲染（状态/类型/标签用 i18n 显示名，分子/批次用原始名）。
  c) i18n：`I18N` zh-CN 与 en-US 两块各新增 `queue.view.*` 键约 30 个（分组/排序/筛选/占位/未标注分子/未填写备注/单独提交/清除/包含归档等，双语 parity）。
  Must NOT do: 不给卡片堆标签（chips 仅在筛选生效时出现）；不加「暂无」类占位。
  Parallelization: Wave 5 | Blocked by: T4 | Blocks: T9 | 可与 T6/T7/T8 并行
  References: `frontend/ACP_Workbench_v2.html:3900-3920`(queue-summary→expanded 区), `:5245-7330`(I18N zh 5523/en 6529 双块), `:7646`(t()), `:7729`(applyI18n data-i18n 机制), `:4026`(results-search 防抖模式参照)；`tests/test_frontend_sync.py:448-466,574-613`(i18n parity 测试模式)
  Acceptance criteria (agent-executable): `test_frontend_sync.py` 新增：① `test_task_view_toolbar_present`（断言五个控件 id 存在 + data-i18n 键引用）；② `test_queue_view_i18n_parity`（提取 `queue.view.*` 键集合，断言 zh-CN 与 en-US 键集相等且非空——沿用现有 parity 测试提取模式）；③ `test_task_view_chips_logic`（断言渲染函数与移除/清除 handler 名存在）。命令：`pytest tests/test_frontend_sync.py -v -k "task_view_toolbar or queue_view_i18n or task_view_chips"`。
  QA scenarios: happy——契约+parity 全绿；failure——缺失任一 locale 键 → parity 测试红（构造性验证：临时删键跑测试见红后恢复）。Evidence `.omo/evidence/task-organization/task-5.md`
  Commit: Y | feat(workbench): task view toolbar, filter panel and chips

- [x] 6. 前端分组列表与卡片精简
  What to do:
  a) 渲染重构：`renderQueueList(container)` 改基于 `taskViewCache.groups`——`group_by=none` 时平铺；否则每组折叠节（复用 `queueCollapsedGroups` 模式 `:4905` + `folderToggleSvg()`，**键命名空间化** `view:<groupBy>:<groupKey>` 防跨维度串折）；组头＝显示名 + `N 个任务` + 状态摘要行（`运行 x · 排队 x · 完成 x · 失败 x`，由 group.counts 渲染；`__unassigned__`→「未标注分子/未填写备注」i18n、`__singles__`→「单独提交」、`__untagged__`→「无标签」）；跨项目范围时最外层保留 `buildQueueProjectHeader` 分节（`:13609`）。tag 分组视图组头加提示「同一任务可能在多个标签组出现」。
  b) 排序：`taskViewCache` 行在前端重排函数 `sortTaskRows(rows)`——`name_asc/name_desc` 用 `new Intl.Collator("zh", { numeric: true, sensitivity: "variant" })`（自然排序 TS2<TS10 + 中文拼音）；时间排序信任服务端序；`runningFirst` 开关（工具栏 checkbox，默认关）做稳定分区；组排序同 T2f 契约；次级稳定键 group.key。
  c) 卡片精简 `buildQueueRow(job, opts)`：进度条**仅 active 状态渲染**（`isActiveJobStatus()`，`:7819`），完成/排队/终态以状态文字徽章表示（复用 `createStatusBadge`）——删除完成 100% 绿条路径；任务名单行 CSS ellipsis + `title` 全名；`spec.tags` 自定义标签最多显示 2 个 + `+N`（悬停列全）；`group_by=molecule` 时组内行**不重复**显示分子名（显示 task_name+remark 优先）；移除所有「暂无」占位渲染分支；TAG（TS/INT）徽章继续只在结果面板渲染（任务卡片不出现——数据面隔离）。
  d) 批量选择：`queueSelectedJobs`（`:5222`）Set 语义天然去重，tag 分组多现不改正确性；「全选本组」checkbox 在组头（进 Set 按任务 ID）。
  Must NOT do: 不改变批量清除现有流程；不显示"暂无"；不给每行堆全部标签。
  Parallelization: Wave 5 | Blocked by: T4 | Blocks: T9 | 可与 T5/T7/T8 并行
  References: `frontend/ACP_Workbench_v2.html:13575-13607`(groupJobsForQueue/aggregateGroupStatus), `:13609-13700`(buildQueueProjectHeader/buildQueueGroup/buildQueueRow), `:13745-13784`(进度条渲染), `:7800-7825`(normalizedProgress/isProgressIndeterminate/isActiveJobStatus), `:7793`(createStatusBadge), `:4905`(queueCollapsedGroups), `:2570`(.collapsed CSS), `:13332-13372`(updateQueueBatchBar)；`tests/test_frontend_sync.py`(源码契约模式)
  Acceptance criteria (agent-executable): `test_frontend_sync.py` 新增：① `test_task_view_group_render`（断言 `view:` 折叠键前缀、`__unassigned__/__singles__` 哨兵处理、组头计数渲染函数存在）；② `test_slim_card_rules`（断言进度条渲染分支被 `isActiveJobStatus` 守卫——源码中进度条构造存在于 active 分支内、完成分支无 100% 宽度路径；断言 ellipsis class + title 属性；断言标签 `+N` 截断逻辑）；③ `test_task_sort_collator`（断言 `new Intl.Collator("zh"` 与 `numeric: !0 或 true` 存在）；④ node 冒烟（`skipif shutil.which("node") is None`）：用 node 执行内联脚本对 `["TS10","TS2","BCB_ALLENE","乙醇"]` 按 `Intl.Collator("zh",{numeric:true})` 排序，断言 `TS2` 索引 < `TS10` 索引。命令：`pytest tests/test_frontend_sync.py -v -k "task_view_group or slim_card or task_sort"`。
  QA scenarios: happy——契约全绿 + node 冒烟通过；failure——node 缺失时 skip 而非 fail（断言 skipif 逻辑）。Evidence `.omo/evidence/task-organization/task-6.md`
  Commit: Y | feat(workbench): collapsible grouped task list + slim cards + collator sort

- [x] 7. P2 API：标签清单/重命名/合并/删除 + 批量操作
  What to do:
  a) `src/acp/api/v2_routes.py`：`GET /projects/{project_id}/tags` → 聚合标签清单 `[{tag, count}]`（`SELECT je.value AS tag, COUNT(*) FROM tasks, json_each(tasks.tags) je WHERE project_id=? GROUP BY je.value ORDER BY count DESC`；JSON1 fallback Python 聚合）；`POST /projects/{project_id}/tags/rename` body `{from, to}`（`from`/`to` 为 Python 关键字安全字段名 `source`/`target`）——遍历项目行改写 tasks.tags（去重、保持顺序）；`POST /projects/{project_id}/tags/merge` body `{sources: [str], target: str}`（多并一）；`POST /projects/{project_id}/tags/delete` body `{tag: str}`（仅解除标记，**不删任务**）。三者均返回 `{updated: n}`，逐行事务（TaskIndex 新方法 `rewrite_tags(project_id, transform: Callable[[list[str]], list[str]]) -> int`，持锁单连接遍历）。
  b) `POST /api/v2/tasks/batch-ops` body `{task_ids: [str], op: str, payload: dict}`：op∈`add_tags`(payload.tags 并集去重)/`remove_tags`/`set_molecule_name`(payload.molecule_name → 同时重算 molecule_key)/`archive`/`unarchive`；**archive 校验 tasks.status 属终态**（`JobStatus.is_terminal`；含活动任务则整体 400 并列出不合规 id——不做部分执行）；逐任务执行、返回 `{results: [{task_id, ok, error?}], updated: n}`；task_ids 上限 500。
  c) i18n 不涉及（纯 API）。
  Must NOT do: 标签操作不触碰 spec.tags（提交事实）；archive 不改 jobs.status；不做跨项目操作。
  Parallelization: Wave 5 | Blocked by: T1,T3 | Blocks: T9 | 可与 T5/T6/T8 并行
  References: `src/acp/scheduler/tasks.py`(T1 后列集)；`src/acp/scheduler/jobs.py:47`(is_terminal)；`src/acp/api/v2_routes.py`(路由风格)；`src/acp/scheduler/naming.py`(molecule_group_key)
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_api_v2_tag_registry.py` 并全绿：① tags 聚合计数正确（多任务共享标签）；② rename——旧名消失、任务数不变、`updated` 正确；③ merge 三并一；④ delete 只解除标记、任务行完好；⑤ batch-ops add/remove_tags 幂等（重复添加不重复）；⑥ archive 活动任务 400 + 不合规 id 列表、终态成功；⑦ set_molecule_name 后 molecule_key 同步；⑧ 未知 task_id 收敛进 results[].error 不 500。命令：`pytest tests/test_acp_api_v2_tag_registry.py -v`。
  QA scenarios: happy——含标签/归档/分子混合 fixture 全链路；failure——500 task_ids 超限 422；并发 rename+add（同锁串行不丢更新）。Evidence `.omo/evidence/task-organization/task-7.md`
  Commit: Y | feat(api): project tag registry + task batch operations

- [x] 8. P2：分子别名与合并（迁移 015）+ suggestions
  What to do:
  a) `migrations.py` 追加 `015`：`CREATE TABLE IF NOT EXISTS molecule_groups (project_id TEXT NOT NULL, group_key TEXT NOT NULL, display_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (project_id, group_key))`；`CREATE TABLE IF NOT EXISTS molecule_aliases (project_id TEXT NOT NULL, alias_key TEXT NOT NULL, group_key TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, alias_key))`（别名**项目级**）。
  b) `src/acp/scheduler/tasks.py` 或新 `src/acp/scheduler/molecule_groups.py`：`resolve_molecule_key(conn_or_index, project_id, raw_name) -> str`——alias 命中→group_key，否则 `molecule_group_key(raw_name)`；`apply_group_merge(project_id, alias_keys, target_key)`——`UPDATE tasks SET molecule_key=? WHERE project_id=? AND molecule_key IN (...)`。
  c) API：`GET /api/v2/projects/{id}/molecule-groups`（组+别名清单）；`POST .../molecule-groups/merge` body `{alias_keys: [str], target_key: str}`（合并重算受影响 tasks.molecule_key，返回 `{updated}`）；`GET .../molecule-groups/suggestions`——仅提示不改数据：扫描项目 distinct molecule_key，产出 `[{a, b, reason}]`，reason∈`casefold-equal`（大小写差异）/`separator-normalized-equal`（`-_` 与空白归一后相等），**不自动合并**；`DELETE .../molecule-groups/alias/{alias_key}`（解除别名：受影响任务 molecule_key 回落原名键）。
  d) T2 的 molecule 分组查询不变（molecule_key 已被 merge 物化，无运行时解析开销）。
  Must NOT do: 不自动应用任何 suggestion；合并不修改 molecule_name 原文（只改键）。
  Parallelization: Wave 5 | Blocked by: T1,T3 | Blocks: T9 | 可与 T5/T6/T7 并行
  References: `src/acp/scheduler/migrations.py`(014 后追加模式)；`src/acp/scheduler/naming.py`(molecule_group_key)；T2 引擎（molecule_key 消费面）
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_scheduler_molecule_groups.py` 并全绿：① alias 解析命中/未命中；② merge 后 tasks.molecule_key 批量改写、task-view 分组即时合并（调 query_project_tasks 验证两组变一组）；③ suggestions 产出 `BCB_ALLENE`×`BCB-Allene`（separator-normalized）与 `abc`×`ABC`（casefold），且任务分组未变；④ 解除别名回退原键；⑤ 015 幂等。命令：`pytest tests/test_acp_scheduler_molecule_groups.py -v`。
  QA scenarios: happy——别名/合并/回退全链路；failure——merge 目标键不存在 400；alias 重复创建冲突处理（UPSERT 语义）。Evidence `.omo/evidence/task-organization/task-8.md`
  Commit: Y | feat(scheduler): molecule groups/aliases with merge + suggestions

- [x] 9. P2 前端：批量工具栏 + 归档开关 + 导出 + 分子管理 + 标签管理
  What to do（`frontend/ACP_Workbench_v2.html`）：
  a) 批量工具栏：`updateQueueBatchBar()`（`:13332`）扩展——选中 N>0 时除「批量清除」外加：加标签/移除标签（小输入弹层→batch-ops）、归档/取消归档、设置分子归属（输入名→batch-ops set_molecule_name）；调 T7 端点后 `refreshJobs()`。
  b) 归档开关：工具栏 `#task-view-archived` select（默认排除/包含/仅归档）→ prefs.archived；归档行卡片淡化样式（`.archived` opacity）。
  c) 导出：工具栏「导出」按钮——当前筛选结果（taskViewCache 平铺行）客户端生成 CSV 与 JSON 下载（Blob + a[download]），列＝id/分子/任务名/备注/类型/状态/标签/创建/完成时间。
  d) 分子管理：「未标注分子」组头提供「批量补充归属」（选中行→输入分子名→batch-ops set_molecule_name）；分子分组视图组头「合并到…」动作（选另一组→调 merge API）；组头合并建议条（suggestions 命中当前组时显示「BCB-Allene 疑似同分子 → 合并」按钮，用户点击才执行）。
  e) 标签管理：筛选面板标签区「管理标签」入口→对话框（列标签清单+计数：重命名/合并/删除，调 T7 API）。
  f) i18n：`queue.view.*` 补 P2 键（归档/导出/合并/别名/批量操作提示等，双语 parity）。
  Must NOT do: viewer 内不提供任何提交任务入口（守 anti-pattern #29）；导出不落服务端文件。
  Parallelization: Wave 6 | Blocked by: T5,T6,T7,T8 | Blocks: —
  References: `frontend/ACP_Workbench_v2.html:13332-13423`(batch bar/purge 流程), `:4520-4541`(modal 模式), `:13545`(refreshJobs), T5/T6 产物；`tests/test_frontend_sync.py`
  Acceptance criteria (agent-executable): `test_frontend_sync.py` 新增 `test_p2_batch_and_archive_ui`：断言批量工具栏按钮 id、归档 select id、导出 handler、`batch-ops`/`tags/rename`/`molecule-groups/merge` 端点字面量引用、合并建议条渲染分支、i18n parity 扩展（`queue.view.*` 新键双语）。命令：`pytest tests/test_frontend_sync.py -v -k p2_batch_and_archive`。
  QA scenarios: happy——契约全绿；failure——无选中时批量按钮隐藏逻辑存在（断言 size>0 守卫）。Evidence `.omo/evidence/task-organization/task-9.md`
  Commit: Y | feat(workbench): P2 batch ops, archive toggle, export, molecule/tag management

- [x] 10. P3：保存视图（服务端 projects.settings + 前端菜单）
  What to do:
  a) 存储：`projects.settings` JSON 增键 `saved_views: [{id, name, query: {group_by, sort, statuses, workflows, molecule_keys, tags, batch_ids, search, archived, running_first}, created_at}]`——**复用现有** `PATCH /api/v1/projects/{project_id}`（`v1_schemas.ProjectUpdateRequest.settings`）读-合并-写回（PATCH 处理器中 settings 深合并而非整替换——检查现行为，若整替换则改为深合并仅对 settings 键）；`GET /api/v1/projects/{id}` 自然带出。前端亦可只读消费。无新表新端点。
  b) 前端：工具栏「视图 ▾」菜单——保存当前（命名对话框→PATCH）、列出已存视图（点击=套用 query 到 prefs+refreshJobs，**活查询非快照**）、删除；入口区支持「待检查结果/失败任务」类用户自建视图。
  Must NOT do: 不存任务 ID 列表（只存筛选条件）；不做服务端定时刷新视图。
  Parallelization: Wave 6 | Blocked by: T3,T5 | Blocks: —
  References: `src/acp/api/v1_routes.py`(projects PATCH 端点段)；`src/acp/scheduler/projects.py:259-269`(settings JSON)；`src/acp/api/v1_schemas.py:55-63`(ProjectModel/ProjectUpdateRequest)；T5 工具栏
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_api_v2_saved_views.py`（或并入现有 projects API 测试文件）：① 保存后 GET 返回 saved_views；② settings 深合并不覆盖其他 settings 键；③ 前端契约 `test_saved_views_ui`（菜单/保存/套用 handler + `saved_views` 字面量）。命令：`pytest tests/test_acp_api_v2_saved_views.py tests/test_frontend_sync.py -v -k "saved_view"`。
  QA scenarios: happy——保存→套用→删除循环；failure——重名保存 409 或后写覆盖（按实现断言其一并文档化）。Evidence `.omo/evidence/task-organization/task-10.md`
  Commit: Y | feat(api,workbench): saved task views in project settings

- [x] 11. P3：自动打标规则（用户显式建立）
  What to do:
  a) 模型与存储：`projects.settings.auto_tag_rules: [{id, field: remark|molecule_name|workflow, op: contains|equals, value: str, tag: str, enabled: bool}]`——仅用户显式创建，无默认规则。
  b) 应用点：`src/acp/api/v1_routes.py` 与 `v2_routes._submit_batch_item` 提交成功后（或 `manager.submit` 后统一）调 `apply_auto_tag_rules(project_settings, task_row) -> list[str]` 命中即并入 tasks.tags（经 T3 的 update_display_fields；放 API 层而非 manager，避免 scheduler 依赖 projects 读取——**决策：API 提交路径应用**）。
  c) 手动回填：`POST /api/v2/projects/{id}/auto-tag-rules/apply` → 对项目全部任务重放规则，返回 `{updated}`（规则新建后补历史）。
  d) 前端：项目设置区「自动打标规则」管理（增删启停编辑）+「立即应用」。
  Must NOT do: 规则不自动创建；不在轮询路径执行；规则不修改 molecule/remark 等其他字段。
  Parallelization: Wave 6 | Blocked by: T1,T3 | Blocks: —
  References: `src/acp/api/v1_routes.py`(v1 提交端点段), `v2_routes.py:299-342`(batch item)；`src/acp/scheduler/projects.py`(settings 读取)；T3 update_display_fields
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_api_auto_tag_rules.py`：① 提交 remark="Stepwise scan" 命中 contains 规则→tasks.tags 含规则标签；② enabled=false 不应用；③ apply 回填历史任务；④ 前端契约（规则管理 UI 键存在）。命令：`pytest tests/test_acp_api_auto_tag_rules.py -v`。
  QA scenarios: happy——规则建立→新提交自动带标→回填历史；failure——value 为空 422；规则 tag 与手动标签冲突去重。Evidence `.omo/evidence/task-organization/task-11.md`
  Commit: Y | feat(api,workbench): user-defined auto-tag rules with backfill

- [x] 12. P3：来源链路只读浏览
  What to do:
  a) `GET /api/v2/tasks/{task_id}/lineage` → `{upstream: [V2TaskSummary...], downstream: [...]}`：上游＝递归解析 `spec.input` 的来源引用（`input.source` 的 from-job / artifact_path 指向的任务 id；复用 `src/acp/scheduler/structure_sources.py` 的发现逻辑思路但按 job 查询），深度 ≤10 + 环检测（visited set）；下游＝反向扫描（jobs 中 spec_json LIKE 预筛 + Python 精确判定引用本 id）。全部只读。
  b) 前端：任务详情面板新增「来源链路」折叠区——上游链（可点击跳转选中任务）+ 下游列表；无链路显示空区（不报错）。
  Must NOT do: 不写任何数据；不做图布局（线性链+列表即可）；不改 structure_sources.py 现有行为。
  Parallelization: Wave 4 | Blocked by: T3 | Blocks: — | 可与 T4 并行
  References: `src/acp/scheduler/structure_sources.py`(来源发现模式)；`src/acp/api/v2_routes.py:151-154`(get_task 风格)；`src/acp/scheduler/store.py`(get/list)；前端任务详情面板结构（`ACP_Workbench_v2.html` 详情渲染段）
  Acceptance criteria (agent-executable): 新建 `tests/test_acp_api_v2_lineage.py`：① 构造 A→B→C 引用链（B.input.source 引 A id）断言 B 的 upstream 含 A、A 的 downstream 含 B；② 自引用环不死循环（深度截断）；③ 无来源任务返回空数组 200；④ 前端契约（lineage 面板渲染分支 + 端点字面量）。命令：`pytest tests/test_acp_api_v2_lineage.py -v`。
  QA scenarios: happy——三级链全量断言；failure——损坏 spec_json 行跳过并 warning（不 500）。Evidence `.omo/evidence/task-organization/task-12.md`
  Commit: Y | feat(api,workbench): read-only task lineage browsing

- [x] 13. 文档与知识库同步
  What to do: ① `docs/ACP_Project_Task_Storage_Design_v2.md` §9.1 增补新列/索引/同步规则（首写胜出白名单、transition 写入集、purge 级联含 tasks）；② 修正 `src/acp/api/v2_routes.py:6-7` 模块 docstring（"the jobs table is the task index" → tasks 表为服务端任务索引，jobs 为执行事实）；③ 根 `AGENTS.md`：anti-pattern #20 更新（`purge_cascade` 现含 tasks 删除）、WHERE TO LOOK 增 `scheduler/task_views.py` 行；④ 新建 `docs/ACP_Task_Organization_DevDoc.md`：分组/筛选/排序契约、计数语义（tag 分组 sum≥total）、batch_id 沿革（resources.batch_id）、API 面、i18n 命名空间、测试图谱；⑤ README「任务队列操作」段补分组浏览一句。
  Must NOT do: 不改任何运行代码（docstring 修正除外，属注释）。
  Parallelization: Wave 6 | Blocked by: T1,T2,T3 | Blocks: —
  References: `docs/ACP_Project_Task_Storage_Design_v2.md:484-506`(§9.1)；`AGENTS.md`(anti-patterns/where-to-look)；`README.md`
  Acceptance criteria (agent-executable): grep 校验——`AGENTS.md` 含 `task_views`；devdoc 存在且含「sum(group.count) ≥ total」契约句；`v2_routes.py` docstring 不再含 "the jobs table is the task index"。命令：`ls docs/ACP_Task_Organization_DevDoc.md && grep -c "task_views" AGENTS.md`。
  QA scenarios: happy——三处 grep 命中；failure——文档与实现漂移由 F4 范围审计兜底。Evidence `.omo/evidence/task-organization/task-13.md`
  Commit: Y | docs: task organization design sync + dev doc

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  对照本计划逐 todo 核验：每个验收命令真实执行且通过、Evidence 文件存在且含输出摘要；数据层四个关键回归（迁移幂等/同步防覆盖/purge 幻影/整项目计数）有专项测试证据。
- [x] F2. Code quality review
  `ruff check src tests` 与 `ruff format --check src tests` 对新增/改动文件零新违规；`pytest -m "not slow" -q` 全绿（CI 同款，`pip install -e '.[dev,api,remote]'` 环境）；无 `as any` 式类型抑制新增。
- [x] F3. Real manual QA
  启动 `acp run serve`（或 TestClient 脚本）+ 打开 `frontend/ACP_Workbench_v2.html`：进入项目→默认按分子折叠分组/组内时间倒序；切分组/排序/筛选/搜索/chips；编辑备注后触发状态迁移不回滚（防覆盖实证）；批量打标签/归档/导出；「全部」范围跨项目分节。证据：截图或 DOM 断言脚本输出入 Evidence。
- [x] F4. Scope fidelity
  Must-NOT 清单逐条审计：jobs.batch_id 列不存在（`PRAGMA table_info(jobs)`）；spec_json 无回写路径（PATCH 测试证据）；v1 /jobs 参数行为不变（既有测试）；无自动合并/自动归档/多标签交叉分组代码路径；无新 Python/JS 依赖（pyproject/frontend 无新增）。

## Commit strategy
- 每 todo 恰一个 commit（消息风格先 `git log --oneline -10` 对齐仓库现状；类型前缀 `feat(scope)/fix/test/docs`，scope 用 `scheduler/api/workbench`）。
- 提交前 `git status`/`git diff` 只暂存本 todo 文件；测试与实现同 commit；Evidence 文件不入库（.omo/ 已忽略或保持未跟踪）。
- 禁止 amend/force-push；失败的 commit 修复后新提交。

## Success criteria
1. 进入任一项目：任务默认按分子折叠分组、组内创建时间倒序、计数为整项目口径——有 F3 实证。
2. 分组/筛选/排序三者独立可组合，同维多选并集、跨维交集——T2/T3 测试全绿。
3. 用户编辑（备注/分子名/标签/归档）在任何同步路径（状态迁移、远程 resync、move、purge 后重建）下不丢失、不产生幻影行——T1 测试全绿。
4. 手动标签与 自动属性/结构 TAG/node_tags/状态 完全隔离；标签可重命名/合并/删除且删除不删任务——T7 测试全绿。
5. v1 API 与既有测试零回归（`pytest -m "not slow"` 全绿）；无新增依赖。
6. 前端 i18n 双语 parity + 契约测试锁定（工具栏/精简卡片/排序/批量/归档）——T4-T6/T9 契约全绿。
7. 三阶段功能全部交付且 Must-NOT 清单经 F4 审计通过。
