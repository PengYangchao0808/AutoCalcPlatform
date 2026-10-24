# remote-pending-fetch-fix - Work Plan

## TL;DR (For humans)

**目标**: 修复「远程节点计算完成后，结构查看器一直显示『等待远程结果 — 远程文件尚未同步，请稍后重试』」。根因是远程作业目录缺少调度器语境标记（`job.json`+`task.json`），节点侧工作流因此把结果嵌套写到 `<远程任务目录>/<分子名>/RESULT/…`，而 `RemoteStructureCache` 按扁平 `<远程任务目录>/RESULT/…` 拉取，永远失败且被静默吞掉 → `availability=pending_fetch` 常驻。

**交付行为**:
1. **新远程任务**：提交时在远端建立调度器语境（上传 `job.json`+`task.json`），产物平铺（回归 layout spec §2a 契约；同时修复远端 checkpoint 续算与进度读取的同类暗病）。
2. **已完成的旧嵌套任务**：读取端做嵌套兜底（仿 `find_remote_state_json` 先例），无需重跑即可查看。
3. **前端**：终态自动重试由 `refreshIfChanged` **单一拥有**（基于**新鲜响应**校验终态，30s backoff + captured-token 守卫 + dirty 守卫）；SSE 终态分支以 `refreshPending(jid, { force: true })` 即时触发（跳过可能过期的 `state.payload` 状态守卫）。**重试状态（`pendingFetchNextRetryAt`/`pendingFetchInFlight`）只在 `jobId` 变化时重置**；`refreshIfChanged` 委派给内部 `_retryPendingFetch`（无 availability 门槛）。补 `job.cancelled`；覆盖「抽屉打开 + SSE 丢失」。
4. `_CATALOG_FETCH_PATHS` 键名修复（`"xtb-optimize"`→`"xtb_optimize"`，含第 70 行循环；补 `nmr` 显式条目），并归一化结构查看器的 `xtb_optimize` 分发键。
5. 文档/AGENTS 契约补注 → 全量回归 → GitHub 推送（origin/main，XieningKe1；`GIT_ASKPASS` + **显式禁用 credential.helper**，PAT 不入 argv/仓库/凭据存储）→ 服务重启。

**它不会做什么**: 远程结果整体回传（违反“结果留在节点”设计红线）；SSH/paramiko 层重写；历史任务磁盘布局迁移/改写；前端其他查看器功能重构。

**工作量**: 5 波 10 个实施 todo + 4 个终验任务（F1–F4）。核心风险：(a) 前端重试状态所有权（Oracle 三轮聚焦，本版以「同 jobId 不清状态 + 新鲜响应校验 + 内部无门槛重试 + SSE force 通道」定稿）；(b) 嵌套兜底不得破坏扁平优先/既有 FakeFetcher；(c) 推送凭据卫生（含全局 credential.helper/store）。

## Scope

**In scope**:
1. `build_task_record` 抽取（`jobs.py`），本地 runner 复用（零行为变化；不设置 `result_manifest_path`/`layout_version`；保持 `project_id=record.project_id or ""`）。
2. RemoteJobRunner 在 bsub 前生成/确保并上传 `job.json`+`task.json` 到 `<remote_job_dir>`（`_prepare_and_submit` 统一入口，`submit_remote` 与 legacy `run()` 两路覆盖）。
3. `RemoteStructureCache.fetch()` 扁平优先 + 单层嵌套兜底（专用锁 memoize 前缀；仅 `FileNotFoundError` 触发；`fetcher.list_files` 缺失即跳过；缓存布局保持扁平），几何/频率产品路径一并受益。
4. 前端：公共 `refreshPending(jobId, opts)`（SSE 即时入口，支持 `force`）+ 内部 `_retryPendingFetch(jobId)`（无门槛重试原子操作，`finally` 按 jobId 守卫）+ `refreshIfChanged` 终态感知（唯一重试 owner，30s backoff + token 守卫 + dirty 守卫）；重试状态仅在 `jobId` 变化时重置；`loadStructureViewer` 自动 `?fetch=1` 条件覆盖 `pending_fetch` 与 `unavailable`；SSE types 与终态分支补 `job.cancelled`。
5. `_CATALOG_FETCH_PATHS` 键修复 + 第 70 行循环同步 + `nmr` 显式条目。
6. `structure_viewer.py` workflow-key 归一化：`xtb_optimize`（下划线）与 `xtb-optimize`（连字符）同解析。
7. 文档与 AGENTS 契约补注（远端调度器语境要求、嵌套兜底、终态重试 owner 与状态所有权、推送密钥规则）。
8. 全量回归 → GitHub 推送（origin/main，用户 XieningKe1）→ 服务重启提示。

**Out / Must NOT Have**:
- 任何形式的远程结果整体下载/回传（`remote/runner.py:24` “**No files are downloaded**” 设计红线）。
- SSH/SFTP 连接池、paramiko、RemoteResultFetcher 底层重写（仅调用其现有 API）。
- 历史任务磁盘布局迁移、`{job_id}` 目录回填、批量改写旧任务产物。
- 与本次修复无关的 workflow/前端重构。
- 任何形式的密钥入库/入 argv/入凭据存储：PAT 不得出现在代码、计划、提交、日志、命令行回显、`.git/config`、`~/.git-credentials`。

**兼容红线**:
- 扁平路径命中时不得触发任何额外 SFTP 调用（嵌套探测仅在 `FileNotFoundError` 后启用）。
- 上传标记失败 → 远程提交失败并清理远端目录（fail fast，不得静默产出一个嵌套任务）。
- 既有锁定测试语义不得放宽：`tests/test_frontend_sync.py` 的 remote auto-fetch/节点逻辑测试、`tests/test_acp_api_structure_viewer.py` 的 Four-endpoint 覆盖、`tests/test_remote_phase2.py` 现有用例。
- 未知 workflow 仍走 `_resolve_legacy`；归一化只新增别名，不改变既有键语义。

## Key Decisions（用户已确认）

- **D1 = A**：根因修复 + 旧任务只读兼容（新任务平铺；旧嵌套任务读取端兜底，无需重跑）。
- **D2 = 全量覆盖**：按“全部受嵌套影响的 workflow”修复（Confsearch、simple(singlepoint/optimize/frequency/xtb_optimize)、scan；BatchOptimize 已由 `--layout-mode single_flat` 平铺；PES/irc/nmr 落 output 根）。
- **D3 = 修前端次级问题**：任务转终态时若查看器正打开该任务且 `availability ∈ {pending_fetch, unavailable}`，自动重载目录；SSE 通道用 `force` 绕过可能过期的 payload 状态。
- **D4（三轮审查修正）**：前端重试由 `refreshIfChanged` 单一拥有；它基于**新鲜响应的 `job_status`/`availability`** 判定；进入重试前先过 dirty 守卫（`wasDirty` → `newerAvailable=true` 并返回，不委派）；backoff 命中时不委派；委派走内部 `_retryPendingFetch`（只做 in-flight 守卫 + `loadStructureViewer`，无 availability/terminal 门槛）。`pendingFetchInFlight`/`pendingFetchNextRetryAt` **仅在 `loadStructureViewer` 检测到 `jobId` 变化时重置**；`_retryPendingFetch` 的 `finally` **按 jobId 守卫**（换 job 后不得清新 job 的标志）；`_applyCatalogResponse` 在 `availability=ready` 时清 backoff；`loadStructureViewer` 的自动 `?fetch=1` 条件扩为 `(pending_fetch || unavailable)`。公共 `refreshPending(jobId, opts)` 默认带 availability+terminal 守卫，`opts.force=true`（SSE 终态事件使用）跳过守卫直连 `_retryPendingFetch`。**不在 `refreshSelectedJobSummary` 加钩子**。
- **审查**：`review_required: true`（momus + Oracle 双审，交付前必须通过）。
- **交付**：修复完成后推送到 GitHub（`origin` = `https://github.com/PengYangchao0808/AutoCalcPlatform.git`，branch `main`，账号 `XieningKe1`），PAT 由用户运行期提供（env `GITHUB_PAT`）；`GIT_ASKPASS`（按 prompt 返回用户名或 PAT，脚本不含字面量）+ `GIT_TERMINAL_PROMPT=0` + 清除 `GIT_CONFIG_PARAMETERS` + **显式清空 credential.helper**，不落盘/不入 argv。

## Verification strategy

- **每 todo 自带 agent 可执行验收**：pytest/node 命令 + 断言内容 + 证据落点。
- **根因锁（关键）**：
  - `tests/test_remote_phase2.py`：断言 bsub 前 `<remote_job_dir>/job.json` 与 `task.json` 已存在于 FakeSFTP `files`；本地 `task.json` 生成；标记上传失败 → 提交报错 + 远端目录清理。
  - 语义锁：带标记目录 `resolve_task_output_root(dir, "mol") == dir`（复用 `tests/test_acp_workflows_simple.py:148-165` 断言风格）；覆盖 Confsearch 双站点（`confsearch/engine.py:264-279` 与 `confsearch/protocols/xtb_md.py:51`）；fetch 映射为扁平相对路径。
  - `tests/test_acp_api_structure_viewer.py`：嵌套 FakeFetcher（仅 `read_file`，无 `list_files`）→ 兜底跳过、行为不变；带 `list_files` 的嵌套 FakeFetcher → `?fetch=1` 后 `availability=ready`；几何 200；任务目录零写入；二次 catalog 命中缓存。
  - **前端状态所有权锁（本版重点）**：Node 逻辑测试覆盖——(i) `refreshPending` 守卫与并发拦截（调用后**同步**断言 `state.pendingFetchInFlight === true`，证明同 job 重载未清状态）；(ii) `refreshIfChanged` 首轮 pending+terminal 委派产生 `fetch=1`，30s 内二次轮询**不再**新增 `fetch=1`（backoff 跨重试重载存活）；(iii) `ready` 后 `pendingFetchNextRetryAt` 归零；(iv) 陈旧 token 响应被丢弃；(v) 同一 tick race 回归 → 最终 `ready`；(vi) **硬失败路径**：自动 fetch 被 reject 导致 `availability=""` 后，下一轮 `refreshIfChanged` 仍能触发 `fetch=1`（无 availability 死锁）；(vii) **SSE force 通道**：`state.payload.job_status` 为过期 `"running"` 时，`refreshPending(jobId, { force: true })` 仍触发重载；不带 force 时沿用守卫（不触发）；(viii) **dirty 守卫**：`wasDirty` 状态下 `refreshIfChanged` 不委派、置 `newerAvailable`；(ix) **换 job 守卫**：重试进行中切到新 job，旧 `finally` 不得清除新 job 的 `pendingFetchInFlight`；(x) `unavailable` + terminal 的目录能触发第二次 `fetch=1`（注意：catalog 端点当前只发 `ready`/`pending_fetch`，`unavailable` 为 schema-only，测试需 stub 响应）。
  - `tests/test_frontend_sync.py` 源锁：`refreshPending` 与 `_retryPendingFetch` 存在；SSE 终态分支调用 `refreshPending(jid, { force: true })`；types 含 `job.cancelled`；`refreshIfChanged` 调用点位于 drawer-skip 块之外（结构/顺序断言）。
- **归一化锁**：`build_structure_viewer_payload(workflow="xtb_optimize")` 与 `"xtb-optimize"` 产出相同 entries，且不出现 legacy resolver 警告。
- **全量门禁**：`pytest -m "not slow" -x -q` 零失败 + `python -m compileall -q src`。
- **端到端模拟（F3）**：TestClient 组合 FakeSFTP（标记上传）+ FakeFetcher（扁平与嵌套两套）跑通「提交→完成→catalog ready→geometry 200」链路；实机集群验证在用户提供节点凭据时作为附加证据。

## Execution strategy

5 波顺序执行，todos 1–10 编号即推荐顺序；同波内标注依赖：

- **W1 根因（后端）**: T1 → T2（T2 依赖 T1 的 helper）。
- **W2 兼容（后端）**: T3 独立（仅依赖现有 fetcher API）。
- **W3 前端**: T4 → T5（T5 依赖 T4 的新方法/状态语义）。
- **W4 顺带+文档**: T6 独立；T7 依赖 T6 的键对齐；T8 依赖 T1–T7 实现定稿（写实文档）。
- **W5 收尾**: T9 依赖全部；T10（推送）仅可在 Final verification wave 全部 APPROVE 后执行。

执行纪律：测试与实现同 todo 同 commit；每 todo 完成即跑验收命令并记录证据；不引入新第三方依赖；遵循 AGENTS.md 反模式清单。

## Todos

### W1 — 根因：远程调度器语境（新任务平铺）

- [x] 1. 抽取 `build_task_record` 并让本地 runner 复用（零行为变化）
  - **References**: `src/acp/scheduler/runner.py:462-480`（内联 `TaskRecord` 构造；`:465` 为 `project_id=record.project_id or ""`）；`src/acp/scheduler/jobs.py`（`class JobSpec` :514、`class JobRecord` :593，文件已托管 E7 跨模块 helper）；`src/acp/storage/record.py:14-38`（TaskRecord 定义，仅 stdlib 依赖）；`src/acp/storage/layout.py:162`（TaskStorage）、`:262-267`（`write_task_json`）。
  - **任务内容**: 在 `jobs.py` 增加模块级 `build_task_record(record: JobRecord) -> TaskRecord`，字段映射与 `runner.py:462-480` **逐字段一致**：`task_id=record.id`、`project_id=record.project_id or ""`、`molecule_name=record.spec.molecule_name`、`task_name=record.spec.task_name`、`remark=record.spec.remark`、`display_name=record.spec.name`、`workflow=record.spec.workflow`、`task_dir_name=Path(record.work_dir).name`（等价 `runner.py:471` 的 `work_dir.name`）、`status=record.status.value`、`node_id=record.node_id`、`node_path=record.work_dir`、`input_hash=record.input_hash`、`current_stage=record.current_stage`、`created_at=record.created_at`、`updated_at=record.updated_at`；**不得**设置 `result_manifest_path` 与 `layout_version`（保持 dataclass 默认）；`runner.py` 改为调用该 helper。导入 `acp.storage.record.TaskRecord` 前确认无循环（storage 系仅 stdlib）。
  - **Acceptance**:
    - `runner.py` 生成的 `task.json` 字节语义与改动前一致（字段集合与取值完全相同；`project_id` 保留 `or ""`；`layout_version` 保持默认 2、`result_manifest_path` 仍为 None）。
    - 新 helper 单元测试：逐字段断言（含 `project_id` 空值回退、`task_dir_name` 取目录叶子名）。
  - **QA happy**: `pytest tests/test_acp_job_operations.py tests/test_acp_workflows_simple.py -m "not slow" -q` 全绿。
  - **QA failure**: helper 字段缺失/错位（或漏 `or ""`）→ 新单元测试红；旧 `task.json` 相关测试红。
  - **Commit**: `refactor(scheduler): extract build_task_record helper for task.json`

- [x] 2. RemoteJobRunner 在 bsub 前上传 `job.json`+`task.json` 调度器标记
  - **References**: `src/acp/scheduler/remote/runner.py`（`submit_remote` 186-266、`_run_remote` 668-749、`_prepare_and_submit` 755-853、`_cleanup_remote_dir` 855-865）；`src/acp/scheduler/manager.py:1571-1576`（`_write_job_json` 内容 = `record.to_dict()`；`submit` 546 与 STARTING 转换 1836 已写本地 job.json）；`src/acp/storage/layout.py:232-267`（TaskStorage）；`src/acp/scheduler/remote/sftp.py`（`FileStager.upload_file`）；`src/acp/workflows/_helpers.py:22-31`（节点侧判定契约）。
  - **任务内容**: 在 `_prepare_and_submit` 输入物化之后、`_submit_lsf` 之前调用新私有 `_upload_scheduler_markers(record, node, remote_job_dir, work_dir, event_log)`：
    1. 本地 `task.json` 缺失则用 `TaskStorage(work_dir).write_task_json(build_task_record(record))` 生成（存在则不动）。
    2. 本地 `job.json` 缺失则写 `json.dumps(record.to_dict(), indent=2, default=str)`（manager 正常已写；legacy `run()` 路径防御）。
    3. `FileStager.upload_file` 将两文件上传到 `<remote_job_dir>`；`event_log.append("remote.markers_uploaded", ...)`（记录文件名，不记录内容）。
    4. 上传异常直接抛出（与 input 上传失败同语义）→ 既有 `submit_remote`/`_run_remote` 的 except 分支负责远端目录清理，任务失败原因可见。
    - 上传的远端 `job.json` 为**提交时快照，仅作调度器语境标记**：节点侧只按存在性判定（`workflows/_helpers.py:24`），无任何节点侧代码读取其内容（`job.json`/`task.json` 内容读取仅出现在 `scheduler/manager.py:1402,1572`，均为服务端）；在 docstring 记录该语义，不做提交后刷新。
    - 目的：`is_scheduler_task_dir` 命中 → `resolve_task_output_root` 返回任务根 → 产物平铺（`RESULT/confsearch/…`、`RESULT/result_manifest.json`、`WORK/00_RUNTIME/checkpoint.json`、`state.json`）。
  - **Acceptance**:
    - FakeSFTP 断言：`sftp.files` 在 bsub 调用前包含 `<remote_job_dir>/job.json` 与 `<remote_job_dir>/task.json`；本地 `work_dir/task.json` 已生成且可 `TaskRecord.from_dict` 解析。
    - 语义断言：对上传后的远端根 `resolve_task_output_root(root, "mol") == root`（平铺）；Confsearch 两站点（`confsearch/engine.py:276`、`confsearch/protocols/xtb_md.py:51`）均返回扁平根；fetch 映射路径为扁平相对路径。
    - 失败断言：人为让 `upload_file` 对 `task.json` 抛错 → `submit_remote` 抛错且 `_cleanup_remote_dir` 被调用（远端目录移除）。
    - 既有 `tests/test_remote_phase2.py` 全部用例保持绿（未改 LSF 脚本与 CLI 命令生成）。
  - **QA happy**: `pytest tests/test_remote_phase2.py -m "not slow" -q` 全绿。
  - **QA failure**: 移除上传调用 → 标记断言红；把标记上传放到 bsub 之后 → 时序断言红。
  - **Commit**: `fix(scheduler): upload scheduler markers to remote job dir for flat v2 layout`

### W2 — 旧任务只读兼容

- [x] 3. `RemoteStructureCache` 嵌套布局只读兜底（扁平优先 + 前缀 memo）
  - **References**: `src/acp/results/remote_structure_cache.py`（`cache_path` 98-120、`get_cached` 138-141、`fetch` 147-206、`fetch_catalog` 208-221、`_fetch_catalog_products` 223-244）；`src/acp/scheduler/remote/fetcher.py`（`list_files` 143-162、`read_file` 230-250）；先例 `src/acp/scheduler/remote/monitor.py:225-248`（`find_remote_state_json` 嵌套兜底）。
  - **任务内容**: 为 `RemoteStructureCache` 增加 per-job 候选目录 memo 与专用发现锁（**不复用 `_master_lock` 包裹 SFTP**，避免全局串行）：
    1. `fetch(record, rel_path)` **每次仍扁平优先**；扁平 `read_file` 抛 `FileNotFoundError` 时才进入嵌套探测；**其他异常（含 `RemoteFileError`/网络错误）维持现状直接返回 None，不触发探测**。
    2. 嵌套探测：`list_fn = getattr(fetcher, "list_files", None)`；若 `list_fn is None`（既有 FakeFetcher 只实现 `read_file`）→ 直接返回 None（**兼容红线**）。否则取顶层目录（`entry.is_dir`，按名称排序），依次尝试 `<dir>/<rel_path>`；候选读取抛 `FileNotFoundError` → 试下一候选；抛**其他异常 → 记录 debug 并跳到下一候选**；首个命中即 memoize 前缀。memo **仅用于避免重复 `list_files`**，后续 fetch 仍扁平优先、未命中再试 memo 前缀（保证混合布局 `RESULT/...` 嵌套 + `input.xyz` 扁平两路径都可取）。同一 job 并发探测以发现锁串行（重复 `list_files` 可接受）。
    3. 缓存写入路径与目录结构保持扁平不变（`<run_root>/.remote_cache/<job_id>/<rel_path>`），catalog 相对引用（`geometry_ref`）无需变化。
    4. 无 fetcher / 列表失败 / 无子目录 → 维持现状返回 None（debug 日志，不得抛错）。
    5. 扁平命中时**不**调用 `list_files`（零额外 SFTP 调用）。
  - **Acceptance**（`tests/test_acp_api_structure_viewer.py`）:
    - **无 `list_files` 的 FakeFetcher**（现有 `:1369-1375, :1424-1426` 风格）→ 扁平命中用例保持绿；扁平未命中 → 返回 None 且不抛 `AttributeError`。
    - 带 `list_files` 的嵌套 FakeFetcher（仅提供 `<safe>/RESULT/confsearch/confsearch_manifest.json`）→ `GET …?fetch=1` 返回 `availability="ready"`、`default_entry_id` 正确；几何 `?fetch=1` 200；任务目录 `rglob` 前后快照零变化；第二次 catalog 无 fetch 参数仍 ready（缓存复用）。
    - 扁平 FakeFetcher 用例（现有 `test_catalog_fetch_projects_pure_remote_results_from_cache`）保持绿。
    - 单测：混合布局（`RESULT/...` 嵌套 + `input.xyz` 扁平）两路径都能取到；候选读取非 `FileNotFoundError` 异常被跳过而不中断；`list_files` 抛错时静默降级；非 `FileNotFoundError` 的扁平异常不触发嵌套探测。
  - **QA happy**: `pytest tests/test_acp_api_structure_viewer.py -m "not slow" -q` 全绿。
  - **QA failure**: 关闭嵌套兜底 → 嵌套用例红；缓存结构被改写为嵌套 → 缓存路径断言红；对无 `list_files` fetcher 调用探测 → AttributeError 用例红。
  - **Commit**: `fix(results): nested-layout fallback for remote structure cache`

### W3 — 前端终态自动刷新（单一重试 owner + 状态所有权）

- [x] 4. `structure_viewer.js`：`refreshPending` / `_retryPendingFetch` / `refreshIfChanged` 终态感知（状态所有权修正）+ Node 逻辑测试
  - **References**: `frontend/js/structure_viewer.js`（`_applyCatalogResponse` 417-432、state 重置块 476-487、`loadStructureViewer` 462-534、终态自动 `?fetch=1` 508-521、错误 catch 527-533、`refreshIfChanged` 584-637（`:615-618` dirty 守卫、`:620` 应用点）、公共 namespace 3705-3766）；测试 harness 先例 `tests/test_frontend_sync.py:1551-1614`（node -e + `ns._fetchImpl` 桩）。
  - **任务内容**:
    1. state 增加 `pendingFetchInFlight`（bool，默认 false）与 `pendingFetchNextRetryAt`（epoch ms，默认 0）。
    2. `loadStructureViewer(jobId, opts)`：进入重置块前 capture `var previousJobId = structureViewerState.jobId;`；在重置块内**仅当 `String(previousJobId || "") !== String(jobId || "")`（新 job）**才重置 `structureViewerState.pendingFetchInFlight = false; structureViewerState.pendingFetchNextRetryAt = 0;`（同 job 的重试重载保留状态）。同时把现有终态自动 `?fetch=1` 条件（`data.availability === "pending_fetch" && terminal`）扩展为 `(data.availability === "pending_fetch" || data.availability === "unavailable") && terminal`。
    3. 新增内部 `_retryPendingFetch(jobId)`（无 availability/terminal 门槛，只做 in-flight 原子守卫；`finally` 按 jobId 守卫）：
       ```js
       function _retryPendingFetch(jobId) {
         var state = structureViewerState;
         if (!state || String(state.jobId || "") !== String(jobId || "")) return Promise.resolve();
         if (state.pendingFetchInFlight) return Promise.resolve();
         state.pendingFetchInFlight = true;
         return loadStructureViewer(jobId).finally(function () {
           if (String(structureViewerState.jobId || "") === String(jobId || "")) {
             structureViewerState.pendingFetchInFlight = false;
           }
         });
       }
       ```
    4. 新增公共 `refreshPending(jobId, opts)`（SSE 即时入口；默认带守卫，`force` 跳过）：
       ```js
       function refreshPending(jobId, opts) {
         opts = opts || {};
         var state = structureViewerState;
         if (!state || String(state.jobId || "") !== String(jobId || "")) return Promise.resolve();
         if (!opts.force) {
           if (state.availability !== "pending_fetch" && state.availability !== "unavailable") return Promise.resolve();
           var status = String((state.payload && state.payload.job_status) || "").toLowerCase();
           if (status !== "completed" && status !== "failed" && status !== "cancelled") return Promise.resolve();
         }
         return _retryPendingFetch(state.jobId);
       }
       ```
       导出 `refreshPending`（并导出 `_retryPendingFetch` 供 Node 测试）。
    5. `refreshIfChanged()` 改为**基于新鲜响应的单一重试 owner**：fetch 前 capture `token = state.requestToken`；响应时先 `if (token !== state.requestToken) return;`；然后：
       ```js
       var st = String(data.job_status || "").toLowerCase();
       var term = st === "completed" || st === "failed" || st === "cancelled";
       if ((data.availability === "pending_fetch" || data.availability === "unavailable") && term) {
         if (wasDirty) { state.newerAvailable = true; renderInspector(); return; }   // dirty 守卫
         if (Date.now() < (state.pendingFetchNextRetryAt || 0)) return;               // 30s backoff
         state.pendingFetchNextRetryAt = Date.now() + 30000;
         return _retryPendingFetch(state.jobId);   // 无 availability 门槛 → 硬失败后仍能重试
       }
       ```
       否则沿用现有 revision 比较 + 应用逻辑（保留 token 守卫）。**判定只用本次响应的字段**，不读可能过期的 `state.payload`/`state.availability`。
    6. `_applyCatalogResponse`：新增一行——`if (data && data.availability === "ready") state.pendingFetchNextRetryAt = 0;`（成功后解除 backoff）。**该行必须放在函数开头的 token 早退（`capturedToken !== state.requestToken`）之后**，以免被丢弃的陈旧响应清掉 backoff。
  - **Acceptance**（Node 逻辑测试，node 缺失时 skip）:
    - `refreshPending`：pending+terminal+同 job（不带 force）→ 2 次 catalog 调用且第二次 URL 含 `fetch=1`，结束后 `availability="ready"`；`ready` / 不同 jobId / 非终态 → 0 次调用。
    - **状态所有权**：调用后**同步**断言 `ns.state.pendingFetchInFlight === true`（证明同 job 重载未清状态）；在此窗口内再次调用 → 调用计数不增。
    - `refreshIfChanged`：首轮 pending+terminal 响应 → 委派并产生 `fetch=1`；30s 窗口内再次轮询 → 不新增 `fetch=1`（backoff **跨重试重载**存活）；`ready` 后 `pendingFetchNextRetryAt` 归零。
    - **硬失败路径**：桩自动 fetch reject → `availability=""`；随后 `refreshIfChanged` 收到 pending+terminal 新鲜响应 → 仍触发 `fetch=1`（无死锁）。**测试驱动**：初始失败直接经 `loadStructureViewer`，或显式清 `pendingFetchNextRetryAt`/控制 `Date.now`，避免受 30s backoff 影响而 flaky。
    - **SSE force 通道**：`state.payload.job_status="running"`（过期）时 `refreshPending(jobId, {force:true})` 仍触发重载；不带 force → 不触发。
    - **dirty 守卫**：`wasDirty` 时 `refreshIfChanged` 不委派、置 `newerAvailable=true`（fetch=1 计数不增）。
    - **换 job 守卫**：重试进行中切到新 job，旧 `finally` 不清除新 job 的 `pendingFetchInFlight`。
    - **`unavailable`**：`unavailable`+terminal 的目录 → 第二次 URL 含 `fetch=1`。
    - 陈旧 token 丢弃；**race 回归**：同一 tick 先 `refreshIfChanged` 后终态事件 → 最终 `availability="ready"`。
    - 既有 Node 测试（`test_structure_viewer_remote_catalog_auto_fetch_retry` 等）保持绿。
  - **QA happy**: `pytest tests/test_frontend_sync.py -m "not slow" -q -k "structure_viewer"` 全绿。
  - **QA failure**: 去掉守卫 → 对应用例红；在 `loadStructureViewer` 无条件重置状态 → 状态所有权/backoff 用例红；去掉 dirty 守卫 → dirty 用例红；`finally` 不按 jobId 守卫 → 换 job 用例红。
  - **Commit**: `feat(frontend): terminal-aware catalog refresh with guarded retry`

- [x] 5. v2 HTML 接线：SSE 终态即时触发（force）+ `job.cancelled` + 源锁（不做 summary 钩子）
  - **References**: `frontend/ACP_Workbench_v2.html`（`openSSE` types 18087-18093、终态分支 18114-18125；`summaryPoll` 23977-24007：drawer 跳过 23986、`refreshIfChanged` 23996-23997）；`tests/test_frontend_sync.py` 源锁风格（如 `:1374`）。
  - **任务内容**:
    1. `openSSE` types 列表加入 `"job.cancelled"`；终态条件扩为 `(type === "job.completed" || type === "job.failed" || type === "job.cancelled" || type === "done")`。
    2. 该终态分支（选中任务内）追加 `if (window.ACPStructureViewer && window.ACPStructureViewer.refreshPending) window.ACPStructureViewer.refreshPending(jid, { force: true });`——`force` 使 SSE 事件（权威终态信号）不被可能过期的 `state.payload.job_status` 拦截。
    3. **不在 `refreshSelectedJobSummary` 增加钩子**（避免与同 tick 的 `refreshIfChanged` 竞态）。在 23996-23997 附近加注释说明：终态重试由 `refreshIfChanged` 单一拥有，它每 5s 运行且 drawer 打开/SSE 丢失时仍生效。
    4. 不改动 drawer-open 分支逻辑（`refreshIfChanged` 不受 drawer 影响，覆盖该路径）。
  - **Acceptance**:
    - 源锁：HTML 源码含 `"job.cancelled"`（types 与终态条件）、终态分支含 `refreshPending(jid, { force: true })`、`refreshSelectedJobSummary` 内不含 `refreshPending`（负向锁）。
    - 源结构锁：`refreshIfChanged` 调用点位于 `if (!drawerOpen) await refreshSelectedJobSummary();` **之后**且处于 `if (localGen % 2 === 0)` 分支内（证明 drawer-open 覆盖）。
    - Node 测试（T4）覆盖「抽屉打开/无 SSE，仅周期 `refreshIfChanged`」→ 终态后 ready；以及 SSE force 通道。
  - **QA happy**: `pytest tests/test_frontend_sync.py -m "not slow" -q` 全绿 + 手动/浏览器验证（任务运行中打开查看器，完成后横幅消失；SSE 断开时仍生效）。
  - **QA failure**: 删除 SSE 调用点/types 项或 force 参数 → 源锁红；在 summary 函数加回钩子 → 负向锁红；把 `refreshIfChanged` 移入 drawer 条件 → 源结构锁红。
  - **Commit**: `feat(frontend): wire terminal refresh and cancelled events in workbench`

### W4 — 顺带缺陷与文档

- [x] 6. catalog fetch 键修复（`xtb_optimize` + 第 70 行同步 + `nmr` 条目）
  - **References**: `src/acp/results/remote_structure_cache.py:34-71`（`_CATALOG_FETCH_PATHS` 第 43 行 `"xtb-optimize"`；派生 `_CATALOG_READY_PATHS` 59-61；**第 70 行显式循环仍硬编码 `"xtb-optimize"`**，70-71 行为 dict 赋值）；`src/acp/catalog.py:121`（workflow id = `xtb_optimize`）；`src/acp/scheduler/remote/script_gen.py:124-139`（远程允许列表同用 `xtb_optimize`）；`src/acp/workflows/nmr.py:1147-1163`（`ResultManifest(...).write(storage.result_dir())` → `RESULT/result_manifest.json`）。
  - **任务内容**: 将 `_CATALOG_FETCH_PATHS` 的 `"xtb-optimize"` 键改为 `"xtb_optimize"`（值与 simple 族一致：`RESULT/result_manifest.json`、`input.xyz`）；**同步修改第 70 行 `for _simple_workflow in (...)` 的字面量**；新增显式 `"nmr": ("RESULT/result_manifest.json",)` 条目；不加连字符别名。
  - **Acceptance**:
    - 单元断言：`"xtb_optimize" in _CATALOG_FETCH_PATHS` 且其路径等于 `optimize`/`frequency` 条目；`"nmr" in _CATALOG_FETCH_PATHS`；`"xtb-optimize" not in _CATALOG_FETCH_PATHS` **且** `"xtb-optimize" not in _CATALOG_READY_PATHS`。
    - fetch 集成测试（FakeFetcher 仅提供 `RESULT/result_manifest.json`，workflow=`xtb_optimize`）→ ready。
  - **QA happy**: `pytest tests/test_acp_api_structure_viewer.py -m "not slow" -q -k "remote or fetch"` 全绿。
  - **QA failure**: 只改第 43 行漏第 70 行**不会** KeyError（:70 是 dict 赋值，会静默创建陈旧 `"xtb-optimize"` 键）——由 `"xtb-optimize" not in _CATALOG_READY_PATHS` 断言捕获（红）；保留旧键 → 新断言红。
  - **Commit**: `fix(results): align xtb_optimize remote catalog fetch key`

- [x] 7. `structure_viewer.py` workflow-key 归一化（`xtb_optimize` 走 simple resolver）
  - **References**: `src/acp/results/structure_viewer.py`（`_OPTIMIZE_WORKFLOW_KEYS` 84-86；`_resolve_simple` 的 `_WORKFLOW_STEP_KIND` 1091-1096 与 `_WORKFLOW_LABELS` 1100-1105；`_DISPATCH_TABLE` 1513-1523；legacy 回退 1587）；`src/acp/api/v1_routes.py:2241,2345`（`workflow = str(record.spec.workflow or "")` 直接透传）；`src/acp/catalog.py:121`；`src/acp/cli.py:208`、`src/acp/scheduler/jobs.py:98`。
  - **任务内容**: 让 `xtb_optimize`（下划线）与历史 `xtb-optimize`（连字符）解析一致：`_DISPATCH_TABLE` 增加 `"xtb_optimize": _resolve_simple`；`_resolve_simple` 的两个映射各增加 `"xtb_optimize"` 别名（step kind `"optimize"`、label `"xTB 优化"`）；`_OPTIMIZE_WORKFLOW_KEYS` 加入 `"xtb_optimize"`。不改变未知 workflow → `_resolve_legacy` 的兜底语义。
  - **Acceptance**:
    - 同一份 `RESULT/result_manifest.json` fixture，`build_structure_viewer_payload(workflow="xtb_optimize")` 与 `workflow="xtb-optimize"` 产出相同 entries/default_id；前者不出现 legacy/兼容模式警告。
    - 未知 workflow（如 `"no-such-wf"`）仍走 `_resolve_legacy`。
    - 既有 `tests/test_acp_structure_viewer.py` / `tests/test_acp_api_structure_viewer.py` 全绿。
  - **QA happy**: `pytest tests/test_acp_structure_viewer.py tests/test_acp_api_structure_viewer.py -m "not slow" -q` 全绿。
  - **QA failure**: 只加 dispatch 漏 `_resolve_simple` 内映射 → entries 为空/标签错误（红）；删 legacy 兜底 → 未知 workflow 用例红。
  - **Commit**: `fix(results): accept xtb_optimize workflow key in structure viewer`

- [x] 8. 文档与 AGENTS 契约补注
  - **References**: `docs/ACP_Job_File_Layout_Spec.md`（§2/§2a/§3 布局契约、产物清单）；`docs/ACP_Structure_Viewer_DevDoc.md`（§8 远程缓存 206-214、端点表 155-163）；`AGENTS.md`（反模式 #22 调度器命名区、远程缓存条目）。
  - **任务内容**（实现定稿后写实，禁止空写）:
    1. Layout Spec：**远程作业目录必须携带 Zone A 标记（`job.json`+`task.json`）**，使节点侧调度器语境成立、产物平铺；标记为提交时快照、仅存在性判定；历史嵌套目录由读取端兜底，不迁移。
    2. Structure Viewer DevDoc §8：“嵌套兜底（扁平优先，仅 FileNotFoundError 触发、`list_files` 缺失降级、候选异常跳过）”；“终态重试单一 owner：`refreshIfChanged` 终态感知 + 30s backoff + token/dirty 守卫 + 状态所有权（仅换 job 重置、`finally` 按 jobId 守卫）；SSE 终态分支 `refreshPending(jid, {force:true})`；`_retryPendingFetch` 无门槛”；“`xtb_optimize` 键归一化”。
    3. `AGENTS.md`：反模式区补一条（远程提交省略调度器标记 → 节点产物嵌套 → 远程目录拉取/checkpoint 读取系统性失败）；结构查看器特性行补 `refreshPending`/`refreshIfChanged` 契约。
  - **Acceptance**: 文档含上述小节且与实现一致；`grep -n "markers\|嵌套兜底\|refreshPending\|xtb_optimize" docs/ACP_Structure_Viewer_DevDoc.md docs/ACP_Job_File_Layout_Spec.md AGENTS.md` 有实义命中。
  - **QA happy**: 人工核对 + grep 命中。
  - **QA failure**: 文档与实现不一致 → 检查清单红。
  - **Commit**: `docs: remote scheduler markers and structure-viewer sync contract`

### W5 — 收尾

- [x] 9. 全量回归
  - **References**: CI 段（py3.10-3.12、`pytest -m "not slow"`）；AGENTS.md COMMANDS。
  - **任务内容**: `pytest -m "not slow" -x -q` 全量绿；`python -m compileall -q src`；如环境有 node，确认 Node 逻辑测试真实执行（非 skip）；快速冒烟 `acp run serve`（临时 run_root）加载 Workbench 页面无 JS 错误。
  - **Acceptance**: 零失败退出码 0；无因本次改动变红的既有测试（除非属于本计划明确更新项且同 commit）。
  - **QA happy**: pytest 输出归档 + compileall 通过。
  - **QA failure**: 任一红 → 修复到绿，不得放宽断言。
  - **Commit**: `test: full regression green for remote pending-fetch fix`

- [x] 10. 交付：推送 GitHub + 服务重启（仅在 Final verification wave 全部 APPROVE 后执行）
  - **References**: `.git/config`（origin = `https://github.com/PengYangchao0808/AutoCalcPlatform.git`、branch main、user XieningKe1）；`scripts/install_systemd.sh`；AGENTS.md SYSTEMD 段。
  - **任务内容**:
    1. 推送前核对：`git status` 干净；`git log --oneline origin/main..HEAD` 仅本计划提交（若存在无关领先提交/merge → 停止并向用户确认，禁止夹带）。
    2. PAT 从运行期环境变量 `GITHUB_PAT` 读取（用户会话提供）；**经 `GIT_ASKPASS` 注入**（临时 helper 置于仓库外、chmod 700、脚本按 prompt 分支：含 `Username` → 输出 `${GITHUB_USER:-XieningKe1}`，否则输出 `printf '%s\n' "$GITHUB_PAT"`；脚本内无 PAT 字面量；推送后删除）；`GIT_TERMINAL_PROMPT=0`；**清除继承的 `GIT_CONFIG_PARAMETERS`**（`env -u GIT_CONFIG_PARAMETERS`）；**显式清空 credential helper**：`git -c credential.helper=` 叠加 `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=`（防止全局/系统/URL-specific `credential.helper=store|cache` 运行并把 PAT 写入 `~/.git-credentials`——已实测该组合可禁用 store；见 Review receipts）；**禁止** `https://user:PAT@...` 形式（argv/`ps`/错误输出泄露）。
    3. `git push origin main`；`git ls-remote origin refs/heads/main` 与 `git rev-parse HEAD` 一致。
    4. 密钥卫生：推送前后对 `~/.git-credentials` 与 `~/.config/git/credentials` 做存在性/校验和比对，确认未新增或变更；`git status`/`.git/config` 无凭据；`git grep -I -n -E 'ghp_|github_pat_' -- $(git rev-list --all)` 与工作区扫描零命中；提示用户**轮换该 PAT**（已在会话明文出现）。已知残余（接受）：`GITHUB_PAT` 在推送子进程 `/proc/<pid>/environ` 对同用户/root 可见（env 注入固有）。
    5. 部署：`sudo systemctl restart acp`（若具备权限）；否则输出重启指令与验证方法（重新打开旧远程任务 → 不再出现 pending 横幅）。
  - **Acceptance**: 远端 `main` 含本计划全部提交；仓库/历史/`.git/config`/凭据存储无 PAT；服务重启完成或已输出明确手动步骤。
  - **QA happy**: `git ls-remote` 输出与本地 HEAD 一致；密钥扫描与凭据存储比对均零变化。
  - **QA failure**: 推送被拒/落后远端 → 先 `git pull --rebase origin main` 再推；发现密钥、凭据文件变化或无关提交 → 立即停止并报告（不得强推掩盖）。
  - **Commit**: 无（推送任务，不产生新提交）

## Final verification wave

（并行执行，全部 APPROVE 才交付；若任一 REJECT → 修复后重跑该任务）

- [x] F1. Plan compliance audit
  - 逐 todo 核对实现与计划一致性（文件、函数名、字段、行为、测试名、提交信息）；无遗漏 todo；无 Scope OUT 项被实现（特别是：远程整树下载、SSH 层重写、布局迁移、无关重构）。
- [x] F2. Code quality review
  - `ruff check src tests`（至少新增/修改文件无 E/F/I 错误）；类型注解/日志/路径规范符合 AGENTS.md；新增行内无 bare `except Exception`；`build_task_record` 单一来源且不设 `result_manifest_path`/`layout_version`、保留 `project_id or ""`；`_CATALOG_FETCH_PATHS`/`_CATALOG_READY_PATHS` 无连字符残留；嵌套探测仅在 `FileNotFoundError` 且 `list_files` 存在时触发；前端重试状态仅在换 job 时重置且 `finally` 按 jobId 守卫。
- [x] F3. Real QA（端到端模拟 + 实机可选）
  - 端到端模拟：TestClient 组合 FakeSFTP（走 `submit_remote` 完整准备流程）+ FakeFetcher，跑通「提交 → 远端标记落盘 → 工作流语义平铺（含 `xtb_md.py` 站点）→ catalog `?fetch=1` ready → geometry 200」；嵌套 FakeFetcher（含/不含 `list_files`）跑通旧任务只读路径与降级路径；Node 逻辑测试覆盖 `refreshPending` 守卫/force/状态所有权、`refreshIfChanged` 终态感知/backoff/token/dirty、硬失败重试、换 job 守卫、`unavailable`、race 回归；`pytest tests/test_frontend_sync.py tests/test_acp_api_structure_viewer.py tests/test_remote_phase2.py tests/test_acp_structure_viewer.py -m "not slow"` 全绿并存档输出。
  - 实机（可选，用户提供节点凭据时）：提交一个 Confsearch 远程任务，完成后打开结构查看器确认无 pending 横幅、几何可加载。
- [x] F4. Scope fidelity & secret hygiene
  - 与用户决策 D1–D4 逐条对照终态行为；无用户未要求的附加行为改变；`git grep -I -E 'ghp_|github_pat_'` 对全历史零命中；`~/.git-credentials` 与 `~/.config/git/credentials` 无变更；计划/文档/测试无 PAT 字面量；推送仅含本计划提交。

## Commit strategy

- 每个 todo 一个 commit（消息见各 todo Commit 行），顺序 W1→W5；测试与实现同 commit。
- T1 为纯重构，必须单列 commit（便于回滚）；T6 与 T7 不得夹带其他改动。
- 推送仅由 T10 执行，且必须在 F1–F4 全部 APPROVE 后；推送前不产生新提交（若有修复提交，先在本地追加再推）。

## Success criteria

（特性级终态验收，全部为真即完成）

1. 新建远程任务（Confsearch/simple/scan/xtb_optimize）在远端产物平铺：`<remote_dir>/RESULT/…` 与 `state.json`、`WORK/00_RUNTIME/checkpoint.json` 均位于任务根；结构查看器 `availability=ready`，无「远程文件尚未同步」横幅。
2. 已完成的旧嵌套远程任务无需重跑即可查看（catalog `?fetch=1`、几何、频率产品均可拉取；缓存保持扁平；无 `list_files` 的 fetcher 安全降级）。
3. 前端在任务转终态时自动重载目录：SSE 在线即时触发（`force` 通道，不受过期 payload 状态影响）；drawer 打开/SSE 丢失时由 `refreshIfChanged` 兜底（新鲜响应判定、dirty 守卫、30s backoff 跨重试重载存活、token 守卫、硬失败后仍可重试）；`job.cancelled` 同样触发；同一 tick 竞态不再残留 pending 横幅。
4. `xtb_optimize` workflow：catalog fetch 键正确（cache 与 ready 两映射无连字符残留）**且**结构查看器按 simple resolver 解析（非 legacy）；`nmr` 有显式 fetch 条目。
5. `pytest -m "not slow" -x -q` 全绿 + `python -m compileall -q src` 通过。
6. 全部提交已推送到 `origin/main`（账号 XieningKe1），仓库/历史/`.git/config`/`~/.git-credentials` 无 PAT；服务已重启（或已输出手动步骤）。

## Review receipts (review_required: true)

### Round 1（2026-09-27）
- **Momus: APPROVE**。逐条核验全部 file:line 声明与代码一致；4 条非阻塞建议（T6 第 70 行字面量、`--output .` 引用微偏、推送优先 GIT_ASKPASS、T5 实机 QA 可选）。
- **Oracle: REJECT**（5 MUST-FIX：前端竞态、嵌套兜底健壮性、`xtb_optimize` 分发、PAT argv、未覆盖路径）。已在 v2 落实。

### Round 2（2026-09-27）
- **Momus: APPROVE**。确认 6 处修订与代码逐行吻合；3 条非阻塞。
- **Oracle: REJECT**（item 1 T4 状态所有权 NOT-CLOSED；item 4 T10 credential.helper 残留泄漏 NOT-CLOSED）。已在 v3 落实：状态仅换 job 重置、`_retryPendingFetch` 无门槛、`GIT_CONFIG_*` 清空 helper。

### Round 3（2026-09-27）
- **Momus: APPROVE**。抽查确认 T5 源结构锁可验证、T4/T6/T1/T3 修订自洽，无新矛盾。
- **Oracle: APPROVE**（"NEW must-fix: None blocking"）。T4 与 T10 均 CONFIRMED-CLOSED；`git -c credential.helper=` + `GIT_CONFIG_*` 经实测可禁用 store/cache/URL-specific helper；三条非阻塞建议：SSE 即时路径因过期 `job_status` 可能 no-op（措辞过强）、`unavailable` 分支内部 `fetch=1` 门槛不匹配、dirty 守卫顺序 / `finally` 换 job 清除。
- **v4 delta（本轮批准后落实的非阻塞加固，已在本计划正文）**：SSE 通道改 `refreshPending(jid, {force:true})`；`loadStructureViewer` 自动 fetch 条件扩至 `unavailable`；`refreshIfChanged` 委派前加 dirty 守卫；`_retryPendingFetch.finally` 按 jobId 守卫；T10 增 `env -u GIT_CONFIG_PARAMETERS` 与固定用户名 ASKPASS。delta 仅为收紧/补齐既有意图，不改变范围与依赖图。

### Round 4（v4 delta 定向复核）
- **Momus: APPROVE**。6 项 delta 与正文逐项一致、全链路自洽，每个 delta 均有可执行正/负向验收；无新矛盾。
- **Oracle: APPROVE**（"NEW must-fix: None blocking"）。5/5 delta CONFIRMED-CLOSED；实测 `env -u GIT_CONFIG_PARAMETERS` + `GIT_CONFIG_*` + `git -c credential.helper=` 可同时禁用 generic 与 URL-specific credential helper；T4 所有权模型与 T10 凭据加固未被 delta 破坏。
- **批准后编辑性澄清（不改变设计范围；源自 Oracle 非阻塞指引）**：(a) `_applyCatalogResponse` 的 backoff 重置须位于 token 早退之后；(b) 验收 (vi) 的驱动方式（避免 30s backoff 导致 flaky）；(c) 验收 (x) 需 stub `unavailable`（真实 catalog 端点只发 ready/pending_fetch）。
- **已接受的非阻塞残余**：一次额外 stale-response 窗口（受 in-flight 守卫限界，最坏多一次 load/render）；A→B→A 换 job 极端边缘（旧 `finally` 可能清新 A 的 in-flight 标志）；SSE force 期间最多 3 次 HTTP 请求（非风暴）。

### 审查门禁结论
- 四轮双审全部通过（R3 起双方 APPROVE；R4 对 v4 delta 再次双方 APPROVE）。无未决 must-fix；计划可交付执行。
