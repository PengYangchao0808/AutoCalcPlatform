# ACP Services 发布准备记录（兼容窗口 + schema 扩展/收缩/回退 + 重启流程 + 发布清单）

日期：2026-10-19。计划：`.omo/plans/acp-parallel-plan-2-services.md` todo 71（B13-6，Wave 14）。
体例参照 `docs/reference/current/ACP_Services_Closure_20261019.md`；消费
`docs/reference/current/ACP_Services_Four_Combination_Acceptance.md`（四组合）与
`tests/baseline/services-evidence/{migration_ledger,backup_restore_drill,fault_matrix_report,packaging_rollout_receipts}.md`。

> **硬性边界：本 todo 只做发布准备。本文件撰写过程中未发布任何产物、未重启生产服务、
> 未修改 systemd unit、未触碰生产 run_root/DB/静态资源。实际发布与冒烟由 todo 76 在
> F1—F4 全部 APPROVE 后按 §6 清单执行（评审 X6：发布拆分）。无兼容矩阵不得宣称可发布。**

---

## 1. 范围与版本基线

| 项 | 值 |
|---|---|
| 代码 release 版本 | `acp 0.2.0` / `cccp 0.2.0`（4 处 lockstep：`src/acp/__init__.py`、`src/cccp/__init__.py`、`src/cccp/version.py`、`pyproject.toml`） |
| 科学静态资产 | `cccp.analysis.nmr.model_assets/*` 8/8 随单 wheel 发行（`packaging_rollout_receipts.md` §1.2） |
| Web 静态资源 | `frontend/**`（`ACP_Workbench_v2.html` + `js/` 模块 + `css/`）——**不入 wheel**（已声明缺口，`packaging_rollout_receipts.md` §2；由仓库检出 + 服务静态托管提供，代码 release 与静态资源同检出原子切换） |
| 数据库 schema | `acp_jobs.db`（SQLite）；forward-only 迁移台账 `acp_services/store/migrations.py`（21 条；`JobStore` 初始化后 schema version = 21） |
| 远端 release | 内容寻址版本化 release（`acp/scheduler/remote/release.py`）；生产 `auto_sync`，dev 逃生门 `ACP_REMOTE_ALLOW_UNVERSIONED=1`（显式 `unversioned-shared`） |
| 四组合记录 | `ACP_Services_Four_Combination_Acceptance.md`：F0+S0 / F1+S0 / F0+S1 / F1+S1 全部 PASS_IN_REPO（execution commit `d048950`；浏览器/科学等价 NOT_VERIFIED） |
| 收口记录 | `ACP_Services_Closure_20261019.md`（L1 1348 passed / F-3 FAIL 如实登记；L2 后端入口 10 PASS；生产门 NOT_VERIFIED） |

---

## 2. 兼容矩阵：代码 release × 静态资源 × 在途任务

窗口定义：一次发布 = 仓库检出前进到新提交 + `systemctl restart acp`（unit 经 `scripts/install_systemd.sh` 生成，见 §4）。窗口 = **发布重启时刻起，到所有旧客户端/旧 worker/旧 tab 自然消亡或被显式刷新为止**。窗口内四类对象必须可读可用：

| # | 对象 | 窗口内承诺 | 依据（已在候选上验证） | 失效条件（窗口何时结束） |
|---|---|---|---|---|
| M1 | **旧页面**（浏览器中已打开的 F0/F1 旧 tab，持有旧 JS bundle 或缓存静态资源） | 旧 GET 端点逐字保留（`/api/v1/jobs`、`jobs/{id}`、structure-viewer/energy-graph 等）；旧字段/错误/409 `pending_fetch` 语义不变；旧 tab 的轮询与 SSE 继续工作 | F0+S0 / F0+S1 四组合（旧 GET 219 passed，`d048950`）；`migration_ledger.md` 5 行 cache-only 逐资源 `[OPEN]` 兼容面 + 旧读取根 `[RETAINED]` 适配器；closure L2-03 | 新读取面（`/availability`、`/prepared/{resource}`）成为唯一读取路径且旧 GET 零消费者证据齐备后（ledger 退出条件；**不早于本发布**） |
| M2 | **旧 worker**（远端节点上按旧 release 提交的在途计算进程） | 一次 attempt 冻结其 envelope/plan/release：在途计算不被新代码重启或改写；PAUSED 远端 `bstop/bresume` 语义不变；attempt 回执归档 `WORK/00_RUNTIME/attempts/<N>/` 不变 | execution_chain 按 attempt 冻结选择（closure L2-06）；workflow enablement 8/15 envelope + 7 legacy 旧路径（L2-07）；执行完整性不变量 1/2 | 该 attempt 进入终态并被拉回；此后新任务走新 release |
| M3 | **旧 release**（远端内容寻址 release 目录） | 旧 release 目录不被删除/覆盖（内容寻址 = 按摘要存新不改旧）；`auto_sync` 只为新提交分配新 release_id；dev `unversioned-shared` 显式标注 | `release.py` 内容寻址设计 + `test_remote_code_release*`；执行不变量 1（一次 attempt ↔ 一个已验证执行版本） | 磁盘保留期清理（远端清理策略）触达旧 release 且无在途 attempt 引用 |
| M4 | **在途任务**（本地+远端 RUNNING/PAUSED/QUEUED/STARTING 任务） | 重启后 JobManager 启动恢复：`_requeue_active_on_startup` 先对账再派发（0 重复 bsub）；控制意图不被旧观测覆盖（CAS）；v1 checkpoint 保守重算；rerun 原地 attempt+1 | `backup_restore_drill.md` D1/D2/D3（对账先于派发、不重复派发、收养不二提交）；`test_checkpoint_resume_semantics` / `test_recovery_cross_version` | 任务终态；QUEUED 任务在新代码下派发（其 attempt 身份仍冻结） |
| M5 | **科学静态资产**（model_assets 8 文件） | 随代码 release 原子前进；旧任务读取资产走同一安装目录版本 | `packaging_rollout_receipts.md` §1.2（8/8 digest PASS） | 随代码 release 替换（无跨版本窗口需求：资产为只读模型） |
| M6 | **Web 静态资源**（`frontend/**`） | 与代码 release 同检出原子切换（服务重启后新页面 + 新 API 同时生效）；M1 覆盖旧 tab | FastAPI static 托管自仓库检出；`test_frontend_sync.py` / 前端选择 525 passed | 浏览器刷新后旧 tab 自然消亡 |

**矩阵可发布判据**（todo 76 执行前必须全部成立）：M1—M4 各行的「依据」列测试在**发布候选提交**上绿（见 §6 清单 G3）；任一行依据为 NOT_VERIFIED 时发布记录必须逐行注明，不得静默跳过。

---

## 3. Schema 扩展/切换/收缩与回退条件

当前态：`acp_jobs.db` 为 **SQLite forward-only 迁移台账**（`acp_services/store/migrations.py`，21 条；fault-matrix **F-2：迁移台账 forward-only，收缩不自动修复**）。所有已交付迁移均为**加性**（`ALTER TABLE … ADD COLUMN` / 建表 / 建索引；新列带 DEFAULT 或可空）。

### 3.1 旧 reader 能读哪些新记录

- **加性列扩展（本发布涉及的全部 schema 变化类型）**：旧 reader 用显式旧列投影（`SELECT id, workflow, name, status, …`）读新写入的行 → **可读**（新列对旧投影不可见）；旧 reader 用 `SELECT *` → SQLite 按名返回全部列，旧代码按名取旧列、忽略新列 → **不崩溃、可读**。已在回退演练实测（§5 步骤 4/5）。
- **旧代码写入新库**：新库列均为可空/带默认，旧代码 INSERT 不含新列 → 合法（新列落默认值）。
- **新 reader 读旧记录**：新列默认值语义（`revision=0`、`attempt=1`）即历史行语义（迁移 020 回填保证 attempt 从 result_json 推导）。
- **不可读的例外（明确排除在窗口承诺外）**：若未来迁移引入**语义改写**（同名列改变含义）或**非加性变更**（重命名/删列/改类型），旧 reader 兼容即失效——此类变更必须先在 `migration_ledger.md` 登记并单独评审，**本发布不包含任何此类变更**。

### 3.2 何时不可回退

以下任一条件成立时，**schema/数据回退不可行**，只能前滚修复：

1. 新代码已写入**旧代码无法解释其语义**的记录（例：新 status 枚举值被旧前端/旧 reader 当作未知态处理错误；CAS `revision` 前进后旧代码无守卫写入会覆盖控制意图——执行不变量 2 依赖 revision 语义，**回退代码而不回退数据 = 旧代码失去守卫**）。
2. 数据库已执行**非加性迁移**（当前无；一旦执行，回退 = 数据恢复而非降级）。
3. 快照不存在或快照晚于变更时刻（无回退点）。

**因此发布纪律：任何回退决策必须在「新代码开始写生产 DB」之前做出；一旦新代码写过生产 DB，代码回退必须伴随数据快照核对（§3.5）。**

### 3.3 代码回退（code rollback）

- **操作**：`git checkout <前一发布提交>`（或重新检出上一 release 产物）→ 重启服务（§4）。
- **适用窗口**：§3.2 三条件均不成立（新代码尚未写 DB，或写入均为加性且旧 reader 兼容已由 §5 演练证明）。
- **在途任务**：attempt 冻结身份使旧代码重启后可继续对账收养（backup_restore_drill D1—D3 同机制）；envelope 新任务停止产生，7 legacy 工作流路径不变。
- **验证**：回退后跑旧 GET 冒烟（`pytest tests/test_acp_api_v1.py -q`）+ `acp doctor`。

### 3.4 Schema 回退（schema rollback）

- **不存在降级 API**：`acp_services.store.migrations` 无 downgrade；`_MIGRATIONS` 只增不改（append-only；§5 步骤 6 实测）。**禁止**手写 `DROP COLUMN`/`UPDATE` 迁移「降级」。
- **合法的 schema 回退唯一路径 = 数据恢复**（§3.5）：恢复到扩展前快照，schema version 随库文件一并回到扩展前（§5 步骤 7 实测 version 守恒）。
- **收缩（contract）**：未来若删除过渡列/过渡表，前置条件 = ledger `[OPEN]` 兼容面全部关闭 + 旧 reader 零消费者证据；收缩迁移单独评审，且执行前必须有收缩前快照。**本发布不执行任何收缩。**

### 3.5 数据恢复（data restore）

- **机制**：一致备份恢复（SQLite online backup API + 任务树 + 密钥副本），已由 `tests/test_services_backup_restore.py`（7 passed）与 `backup_restore_drill.md` 演练：恢复后**先对账再派发**，0 重复 bsub；跳过对账的对照用例预期 FAIL（风险暴露）。
- **发布用法**：
  1. **扩展前**：`sqlite3 /var/lib/acp/runs/acp_jobs.db ".backup /var/lib/acp/runs/pre_release_<commit>.db"` + 打包任务树关键目录（`WORK/00_RUNTIME`、`RESULT`、`job.json`/`task.json`）；
  2. **恢复时**：停服务 → 恢复快照 → 启动服务 → 启动对账（JobManager `_requeue_active_on_startup` 自动执行）→ 核对 0 重复派发。
- **三者关系（分别写清）**：代码回退（§3.3）= 只换代码；schema 回退（§3.4）= 不存在独立操作，等于数据恢复；数据恢复（§3.5）= 换库文件（schema 随之回到旧版）。组合选择：仅代码问题 → §3.3；代码+数据语义污染 → §3.5（代码可同时按 §3.3 回退）；schema 非加性变更后 → §3.5 唯一路径。

---

## 4. 服务重启流程与 unit 生成来源核对

### 4.1 规范流程（项目铁律）

1. **编辑 `scripts/install_systemd.sh`，勿手改 `/etc/systemd/system/acp.service`**（根 AGENTS SYSTEMD 节；unit 为 generated 文件）。
2. 重新生成 + 重启：`sudo bash scripts/install_systemd.sh --user <user>`（脚本内含 `tee` 写 unit → `daemon-reload` → `restart`）。
3. 观察：`sudo journalctl -u acp -f`；`systemctl is-active acp`。

### 4.2 unit 生成来源核对（本 todo 只读核对，未重启、未改 unit）

生成模板 = `scripts/install_systemd.sh:32-51` 的 heredoc（关键行：`Description=ACP — Auto-Calc Platform Web Dashboard`、`After=network.target`、`WorkingDirectory=$PROJECT_ROOT`、`Environment=ACP_RUN_ROOT=/var/lib/acp/runs`、`StateDirectory=acp`、`ExecStart=$PROJECT_ROOT/scripts/start_acp.sh`、`Restart=on-failure`、`WantedBy=multi-user.target`）。启动器 `scripts/start_acp.sh:39` exec `uvicorn acp.api.server:app`。

**核对结果：DRIFT（生产 unit 与当前生成模板不一致）**。当前 `/etc/systemd/system/acp.service` 关键行对比：

| 键 | 生成模板（install_systemd.sh） | 生产 unit（2026-10-19 只读读取） | 判定 |
|---|---|---|---|
| Description | `ACP — Auto-Calc Platform Web Dashboard` | `ACP Auto-Calc Platform (FastAPI Web Service + Scheduler)` | DRIFT |
| After/Wants | `After=network.target` | `After=network-online.target` + `Wants=network-online.target` | DRIFT（多行） |
| ExecStart | `$PROJECT_ROOT/scripts/start_acp.sh` | `/opt/acp/venv/bin/acp run serve --host 0.0.0.0 --port 8765 --no-browser` | **DRIFT（实质：绕过 start_acp.sh，venv 直启）** |
| Environment | `ACP_RUN_ROOT=/var/lib/acp/runs` | 同 + `PYTHONDONTWRITEBYTECODE=1` | DRIFT（多一行） |
| 加固/日志行 | 无 | `TimeoutStopSec=30` `UMask=0027` `LimitNOFILE=65536` `StandardOutput/Error=journal` `SyslogIdentifier=acp` `Documentation=` | DRIFT（多行） |
| User/Group/WorkingDirectory/StateDirectory/LogsDirectory/Restart/WantedBy | 与模板一致 | 一致 | OK |

**结论**：现网 unit 曾被手改（或由其他版本脚本生成）——**恰好是本 todo 要防的反模式**。todo 76 重启前必须二选一收口（不得第三选手改）：

- **路径 A（推荐）**：把生产行并入 `scripts/install_systemd.sh` 模板（venv 直启 vs `start_acp.sh`、network-online、加固行、环境行——逐行评审后写入 heredoc），然后 `sudo bash scripts/install_systemd.sh --user xieningke` 重新生成；此后模板即真相。
- **路径 B**：接受模板现状，重新生成会**丢失**上述生产定制（venv 直启改为 start_acp.sh 需要 `.venv` 存在或 conda PATH 满足 `start_acp.sh:14-23` 的回退条件——当前生产用 `/opt/acp/venv`，须先核对 start_acp.sh 能否解析到它）。

**todo 76 执行时**：记录选择的路径 + 重新生成后的 `systemctl cat acp` 输出快照入发布记录；此后任何 unit 变更只经 `install_systemd.sh`。

---

## 5. 回退演练记录（一次，/tmp 隔离，未触生产）

- **演练脚本**：`/tmp/opencode/t71_rollback_drill.py`（临时，不入库）；对象 = `/tmp/opencode/t71-drill/acp_jobs.db`（一次性库，真实 `JobStore` + 真实 `migrations` 初始化）。
- **复现**：`python3.11 /tmp/opencode/t71_rollback_drill.py` → `RESULT=PASS`（7/7 步骤）。
- **路径**：schema 扩展（JobStore init，version=21）→ 写新式记录（revision/attempt/project_id）→ 快照 → **旧 reader 显式旧列投影读新记录** → 旧代码 `SELECT *` 不崩溃 → 确认无 downgrade API（schema 回退 = 数据恢复）→ 恢复快照 → row + schema version 守恒。

| # | 步骤 | 结果 | 实测细节 |
|---|---|---|---|
| 1 | expand：JobStore 初始化执行 forward 迁移 | PASS | schema version = 21（`_schema_migrations` 行数；无基表的条件迁移跳过不记录） |
| 2 | 写新式记录（新列 revision=3/attempt=2/project_id） | PASS | 行落库，新列可读 |
| 3 | 数据恢复快照 | PASS | `acp_jobs.db.bak` 生成 |
| 4 | **旧 reader 读新记录**（显式旧列投影） | PASS | 读到 `id/workflow/name/status/created_at/updated_at`，新列不可见、不报错 |
| 5 | 代码回退模拟（旧代码 `SELECT *`） | PASS | 18 个额外列按名返回被忽略，无异常 |
| 6 | schema 回退不可行确认 | PASS | `acp_services.store.migrations` 无 downgrade API（F-2 forward-only） |
| 7 | 数据恢复 | PASS | 恢复后 row 完整 + schema version 守恒（21=21） |

**演练结论**：加性扩展下「旧 reader × 新记录」与「旧代码 × 扩展后库」均成立；schema 回退唯一路径 = 数据恢复；与 §3 声明一致。演练未读写生产 run_root/DB、未重启服务、未改 unit。

---

## 6. 发布清单（todo 76 执行；F1—F4 全 APPROVE 后）

权威可勾选清单 = `tests/baseline/services-evidence/release_checklist.md`（本节为其摘要与门禁说明）。执行序：

1. **门禁**：F1—F4 全部 APPROVE（缺一不可；A05/C10 注册项按 closure §7.3 处理）；确认发布产物与 C-T54 所验候选可核对（评审 R4：产物后续修改使原验收失效）。
2. **版本 lockstep**：4 文件核对 `0.2.0`（`grep __version__` + `pyproject.toml`）。
3. **候选提交上守卫套件全绿**（§G3，见 checklist 文件精确命令）。
4. **扩展前快照**：§3.5 步骤 1。
5. **unit 收口**：§4.2 路径 A 或 B 执行 + `systemctl cat acp` 快照。
6. **重启与冒烟**：`install_systemd.sh` 重新生成重启 → `acp doctor` → 默认页 HTTP 200 → 一个本地闭环（FakeBackend 或 offline loop 级）→ 在途任务兼容核对（M2/M4 行逐项）。
7. **发布记录**：提交/构建/重启/冒烟证据入 `.omo/evidence/…/task-76-…`；结果核对归 F5（不回写 F1—F4）。

---

## 7. 声明

- 本 todo 交付 = 兼容矩阵（§2）+ schema 扩展/收缩/回退条件（§3，代码/schema/数据三者分写）+ 重启流程与 unit 来源核对（§4，只读）+ 一次隔离回退演练（§5）+ 发布清单（§6 + baseline 文件）。
- **未发布、未重启生产、未改 unit、未触生产数据**；todo 76 之前任何「已发布」声称均违反评审 X6。
- 守护：`tests/test_services_release_prep.py`（文档一致性 + 清单引用存在性 + unit 来源 + 矩阵行完整性 + 回退分写 + 演练引用 + 未发布负例）。
