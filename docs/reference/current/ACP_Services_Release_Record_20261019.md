# ACP Services 发布记录（todo 76 / B13-8 — 终验后发布执行与冒烟）

日期：2026-10-19。计划：`.omo/plans/acp-parallel-plan-2-services.md` todo 76（B13-8，Wave 14b）。
前置：F1—F4 全部 **APPROVE**（§1）；发布清单 = `tests/baseline/services-evidence/release_checklist.md`（todo 71 交付）；
背景文档 = `docs/reference/current/ACP_Services_Release_Prep_20261019.md`（兼容矩阵 §2 / schema 回退 §3 / unit §4）。
执行证据 = `.omo/evidence/acp-parallel-plan-2-services/task-76-acp-parallel-plan-2-services.md`（+ `task-76-unit-snapshot.txt`、`task-76-raw/`）。

> **三态口径（PASS / FAIL / NOT_VERIFIED）**：本记录每一腿给出其一，不以“部分执行”充当 PASS。
> 结果核对归 F5（发布后），本记录不回写 F1—F4 结论。

---

## 0. 结论摘要

| 腿 | 判定 | 一句话依据 |
|---|---|---|
| G0 门禁（F1—F4 全 APPROVE） | **PASS** | 4 份 APPROVE 证据在案（§1） |
| G1 版本 lockstep（4 处 `0.2.0`） | **PASS** | `grep __version__` + `pyproject.toml` 四处一致（§2.1） |
| 构建产物 + provenance | **PASS** | wheel sha256/大小/namelist 记录（§2.2—2.3） |
| 与 C-T54 候选产物可核对（评审 R4） | **PASS（配置/资产/条目集）· NOT_VERIFIED（字节级）** | 配置 sha 一致、8/8 资产摘要一致、候选树重建与发布 wheel 条目集零差异；但冻结候选 `355d96f` 之后有 **7 个触及 `src/` 的提交**，按 R4/C-T54 失效规则其字节级验收不覆盖本次产物（§2.4） |
| G2 扩展前快照 | **PASS（位置改记 /tmp，见偏离说明）** | 生产 DB 经 online-backup API 复制，生产目录零写入（§3.1） |
| G3 候选提交守卫套件 | **PASS（本执行实跑部分）· 覆盖引用（未复跑部分）** | 实跑 83 项 + grep gates + 旧 GET 101 项全绿；未复跑部分逐条引用 F1/F4 在 HEAD 的回执（§3.4、§8） |
| schema 扩展/收缩/回退演练（副本上） | **PASS（8/8 步骤）** | 生产 v20 → 副本 v21（迁移 021）→ 旧 reader 可读 → 数据恢复版本守恒（§3.2） |
| 发布候选冒烟（隔离端口 8897） | **PASS** | `/`、`/api/status`、`/api/workflows`、`/api/v1/jobs` 全 200；`acp doctor` 本地档 exit 0；一条离线闭环全绿（§4） |
| **G4/G5 服务重启与 unit 收口** | **NOT_VERIFIED** | `sudo -n true` → exit 1（`sudo: 需要密码`）——**无非交互式重启手段**；未重启、未重新生成 unit、未手改 unit（§5） |
| G6 在途任务兼容 | **PASS（生产侧：0 在途；候选读旧记录）· NOT_VERIFIED（重启后收养类项，因未重启）** | 生产 15 completed / 0 active；候选读迁移后副本 15 条旧记录全 200（§6） |
| 兼容窗口 M1（旧页面/旧 GET） | **PASS（候选提交上实测）** | `pytest tests/test_acp_api_v1.py` → 101 passed（§6.2） |
| 兼容窗口 M2/M3/M4（旧 worker/旧 release/重启恢复） | **NOT_VERIFIED（本次未重启、无远端在途 attempt）** | 依据改引 F1—F4 既有回执，逐行注明（§6.2） |
| **生产未被修改** | **PASS** | 生产 DB/unit/run_root/真实 `~/.cccp.yaml` 全程只读；未重启（§7） |

---

## 1. 门禁与发布提交（G0）

| 门 | APPROVE 证据 | 审计 HEAD |
|---|---|---|
| F1 计划/证据合规 | `.omo/evidence/acp-parallel-plan-2-services/f1-plan-compliance.md`（`VERDICT: APPROVE`） | `5f4b833` |
| F2 代码质量 | `.omo/evidence/acp-parallel-plan-2-services/f2-code-quality.md`（`VERDICT: APPROVE`） | 整改提交 `6f83a37`…`5f4b833` |
| F3 人工 QA | `.omo/evidence/acp-parallel-plan-2-services/f3-manual-qa.md`（`Verdict: APPROVE`） | 基线 `5f4b833`（整改 `d9dcb25`） |
| F4 理想态保真 | `.omo/evidence/acp-parallel-plan-2-services/f4-ideal-state-fidelity.md`（`VERDICT: APPROVE`） | `c7c26c6` |

- **发布提交（本记录唯一对象）**：`c7c26c6ed535fabf9ff7c0ed9c5e4d181f71fa09`
  （`fix(services): route remaining production writes through Data port; refresh closure F-3 status`，2026-10-19 21:57 +0800）。
- 构建输入洁净度：`git status --porcelain -- src/ pyproject.toml README.md LICENSE` → **clean**（工作区打包面与提交树一致，构建取提交树 `git archive c7c26c6`）。
- 发布形态说明：本次为**候选冒烟级发布执行**——产物构建、隔离环境冒烟、schema 演练在副本执行完毕；**生产服务切换（restart）因 sudo 无口令未执行**（§5，NOT_VERIFIED）。

---

## 2. 构建产物与 provenance（commit / build artifact）

### 2.1 版本 lockstep（G1）

```
$ grep -n '__version__' src/acp/__init__.py src/cccp/__init__.py src/cccp/version.py
src/acp/__init__.py:14:    __version__ = "0.2.0"
src/cccp/__init__.py:14:__version__ = "0.2.0"
src/cccp/version.py:6:__version__ = "0.2.0"
$ grep -n '^version' pyproject.toml
7:version = "0.2.0"
```

四处一致 → **PASS**（与 C-T54 所验 `0.2.0` 同版本）。

### 2.2 构建命令与产物

```bash
git archive c7c26c6 | tar -x -C /tmp/rel76/tree        # 提交树出树（非工作区）
python3.11 -m build --wheel --outdir /tmp/rel76 /tmp/rel76/tree
# → Successfully built acp-0.2.0-py3-none-any.whl   (exit 0, build 1.6.1, py3.11.13)
sha256sum /tmp/rel76/acp-0.2.0-py3-none-any.whl
```

| 项 | 值 |
|---|---|
| 产物 | `acp-0.2.0-py3-none-any.whl`（M1 live 单 wheel 形态） |
| sha256 | `1a0fd3ba3a636f458dc4e7659a09c896b9d91689697e34ed584089241ec5f1cd` |
| 大小 | 46,139,616 B |
| 构建配置 sha256（`pyproject.toml`） | `4d40cf842a31b9d04d18136a24086418422a17019b1bb90daa1bfc774423bbfb` |
| 工具链 | `/usr/bin/python3.11`（3.11.13）· `build 1.6.1`（隔离构建环境，`METADATA-Version: 2.4`） |
| 源 | `git archive c7c26c6`（提交树） |

### 2.3 namelist 核对

| 检查 | 结果 |
|---|---|
| 总条目 | **545**（`acp/` 280 · `acp_services/` 70 · `cccp/` 189 · dist-info 6 · 其余 12） |
| `acp_services*` | **70 条在包内**（`acp_services/__init__.py` 等，Services 层随包发行） |
| `cccp` 科学静态资产 | **8/8 `model_assets/*` 在包内，逐文件 sha256 == `tests/baseline/cccp-evidence/release_manifest.md` §1.3 master**（`LICENSE-DP5`/`NOTICE.md`/`atomic_reps.gz`/`frag_reps.gz`/`c_w_kde_mean_s_0.025.p`/`i_w_kde_mean_s_0.025.p`/`folded_scaled_errors.p`/`tms_references.txt`） |
| `py.typed` | `acp/py.typed` + `cccp/py.typed` |
| dist-info | `acp-0.2.0.dist-info/{METADATA,WHEEL,RECORD,entry_points.txt,top_level.txt,licenses/LICENSE}`；`Name: acp / Version: 0.2.0`；console script `acp = acp.cli:main` |
| legacy `acp/nmr/models/` | 0 条（与 C-T54 审计一致） |

原始输出：`task-76-raw/namelist-audit.txt`、`task-76-raw/wheel-sha256.txt`。

### 2.4 与 C-T54 候选产物的可核对性（评审 R4 — 三态如实）

C-T54（`.omo/evidence/acp-parallel-plan-3-cccp/task-54-acp-parallel-plan-3-cccp.md`）冻结候选 =
`355d96f66fcdaca2fc53d17c5e3eeca0319358ca`，其产物记录：

| C-T54 产物 | sha256 | 大小 | 条目 |
|---|---|---|---|
| M1 live 单 wheel `acp-0.2.0` | `bd2acb503925f0bbc113c8bece7ad8bf88d3eb49ec96c0aaa84ecdb71a48ac61` | 46,131,138 B | 543 |
| M2 `cccp-0.2.0` | `c66cc7c79be34e1605fddfcb350c7632976262dd580456f251833dbf4eadd314` | 44,541,501 B | 194 |
| M2 `acp-0.2.0` | `b1bc512f11fa3c45b1ea5e8c30a06e38680fa3033d8cfbe28e83762f019e1aca` | 1,592,480 B | 356 |
| 构建配置 sha256 | `4d40cf84…23bbfb`（candidate `pyproject.toml` blob） | — | — |

本次核对（逐项）：

| # | 可核对项 | 判定 | 依据 |
|---|---|---|---|
| R4-a | 构建配置同一性 | **PASS** | 本仓 `pyproject.toml` sha256 == C-T54 候选 blob `4d40cf84…`（逐字节同） |
| R4-b | 版本同一性 | **PASS** | 四处 lockstep `0.2.0` == C-T54 产物版本 |
| R4-c | 资产摘要同一性 | **PASS** | 8/8 `model_assets` digest == `release_manifest.md` master == C-T54 审计 8/8 True |
| R4-d | **条目集（namelist）同一性** | **PASS** | 本执行在同一工具链下额外重建候选树 wheel：`git archive 355d96f` → `acp-0.2.0-py3-none-any.whl`（sha `b2128f8854dd80d50a849a2a52690cdbd73c4ba89f0ff6768bb57634dbb420b5`，46,131,436 B，545 条）；**与发布 wheel 条目集 diff = ∅（0 差异）** —— 冻结候选后条目集未变（唯一新增文件 `src/acp_services/AGENTS.md` 不入包） |
| R4-e | 字节级 sha256 同一性 | **NOT_VERIFIED（按设计不成立 + R4 失效规则触发）** | ① wheel 非字节复现（C-T54 自述 sha 仅绑定其构建株；同为候选树，其记录 `bd2acb…` ≠ 本机重建 `b2128f…`，实证跨构建株不可比）；② `355d96f..c7c26c6` 有 **18 个提交、其中 7 个触及 `src/`**（`71c36da` `48b1660` `233297c` `6f83a37` `d9dcb25` `d51b024` `c7c26c6`）——按 C-T54 失效规则（“冻结后触及 `src/cccp|src/acp|src/acp_services` 的 commit 使本复验失效须重跑”）与 R4（“影响产物的后续修改使原验收失效”），**C-T54 的产物级验收不覆盖本次发布产物**，字节级“由 C-T54 所验”不成立 |
| R4-f | C-T54 记录条目数（543）与本机重建（545）差异 | **NOT_VERIFIED（差异未归因，移交 F5）** | 两次本机构建（候选树/发布树）条目集完全一致且均为 545，故 543↔545 差 2 条出在 C-T54 侧计数/构建环境，未归因；不作为阻塞项，但**禁止**把本 wheel 的 sha 与 C-T54 三个 sha 互相拼接引用 |

> **R4 结论（如实）**：发布产物在**配置、版本、资产、条目集**四个维度与 C-T54 候选可核对（PASS）；
> **字节级/“C-T54 已验”声明 NOT_VERIFIED**——冻结后 7 个 `src/` 提交使 C-T54 原验收对该产物失效，
> 补验（在发布提交上重跑 C-T54 复验）为 F5/后续跨路事项，本记录不代为宣称。

---

## 3. Schema 扩展 / 收缩 / 回退演练（仅副本；生产 DB 零写入）

### 3.1 扩展前快照（G2；位置偏离说明）

```bash
# 生产 DB 以 sqlite3 online-backup API 复制（连接 URI mode=ro，未写生产）
python3.11 -c "…conn=sqlite3.connect('file:/var/lib/acp/runs/acp_jobs.db?mode=ro',uri=True)…; conn.backup(dst)"
# → /tmp/rel76/db/pre_expand_c7c26c6.db   847,872 B   (v20, quick_check=ok, jobs=15, stage_tasks=129)
# → /tmp/rel76/runroot/acp_jobs.db         847,872 B   (候选冒烟副本，后经迁移 → v21)
```

- **偏离登记**：checklist G2 写“快照落 `RUN_ROOT`”，本任务硬约束为“**不得修改 `/var/lib/acp/runs`**”，故快照落 `/tmp/rel76/db/`；
  两者同为“扩展前回退点”，生产目录内**未创建** `pre_release_*` 文件（读取亦为只读）。
- **任务树快照（`WORK/00_RUNTIME`、`RESULT` 打包）未执行**：生产 run_root 13 GB 且 0 在途任务、树未变更；
  打包需写生产目录 → 与只读约束冲突 → 该子项 **NOT_VERIFIED（原因如上）**。

### 3.2 扩展/收缩/回退演练（脚本 `/tmp/rel76/t76_schema_drill.py`，原始结果 `task-76-raw/schema_drill_result.json`）

命令：`python3.11 /tmp/rel76/t76_schema_drill.py` → **`DRILL_RESULT=PASS`（8/8），exit 0**

| # | 步骤（实际执行） | 判定 | 实测 |
|---|---|---|---|
| 0 | 快照只读可读（旧 reader 基线） | PASS | v**20**、847,872 B、jobs=15、stage_tasks=129、`jobs` 24 列 |
| 1 | **扩展**：`acp_services.store.migrations.migrate(COPY)` | PASS | `applied=1`，version **20 → 21**；新应用迁移 = **`021`（create `job_outbox` + 2 索引，加性）**；`job_outbox` 已建 |
| 2 | 行守恒 | PASS | jobs 15→15、stage_tasks 129→129、`jobs` 列数 24→24（本迁移不触 `jobs` 列） |
| 3 | **旧 reader：显式旧列投影读扩展后库** | PASS | `SELECT id,workflow,name,status,work_dir,…(20 个迁移前列) FROM jobs` → 15 行，新表/新列不可见、无异常 |
| 4 | 旧代码 `SELECT *` 按名取旧列 | PASS | 15 行；`extra_columns=[]`（本次扩展未加 `jobs` 列，列级兼容**平凡成立**；**列级非平凡场景**=“新列 × 旧投影”，已由 todo 71 演练第 4/5 步覆盖：`revision/attempt/project_id` 18 个额外列按名返回被忽略） |
| 5 | **schema 回退不可行性确认** | PASS | `acp_services.store.migrations` 无 `downgrade`/`rollback` API；`_MIGRATIONS`（21 条）全文无 `DROP COLUMN`/`DROP TABLE`/`RENAME` → **schema 回退唯一路径 = 数据恢复**（与 Release_Prep §3.4 一致） |
| 6 | **数据恢复**：快照覆盖副本 | PASS | 恢复后 version **20 == 20**（版本守恒），jobs/stage_tasks 守恒 |
| 7 | 代码回退读面（旧 reader × 扩展后库） | PASS（读面）| 即步骤 3/4；**操作面（`git checkout <prev>` + 重启）未演练**——重启被 sudo 阻塞（§5），登记为该腿的运维步骤 **NOT_VERIFIED** |

**三类回退分别写清（执行到哪一步如实标注）**：

- **代码回退（code rollback）**：步骤 = checkout 前一发布提交 → `systemctl restart acp`（§4.1）→ 旧 GET 冒烟 + `acp doctor`。
  **读面已演练**（旧 reader 投影 PASS + `pytest tests/test_acp_api_v1.py` → 101 passed，见 §3.4/§6.2）；**操作面 NOT_VERIFIED（无 sudo）**。
- **schema 回退（schema rollback）**：**无降级 API（实测确认）**；唯一合法路径 = 数据恢复（步骤 6 实测版本守恒）。
- **数据恢复（data restore）**：快照 → 覆盖 → 对账再派发（机制见 `tests/test_services_backup_restore.py` / `backup_restore_drill.md`）；本次在副本实测“恢复 + 版本/行守恒”**PASS**，未触生产。

**收缩（contract）**：本发布**不执行任何收缩**（Release_Prep §3.4；`_MIGRATIONS` append-only，无 DROP/RENAME 迁移 — 步骤 5 实测）。

### 3.4 本执行实跑的守卫套件（候选提交 `c7c26c6`）

| 命令 | 结果 | exit |
|---|---|---|
| `python3.11 -m pytest tests/test_acp_api_v1.py -q`（旧 GET 兼容面 / 代码回退读面） | **101 passed**（189.93s） | 0 |
| `python3.11 -m pytest tests/test_services_release_prep.py tests/test_services_doc_sync.py tests/test_acp_services_isolation.py tests/test_architecture_invariants.py -q` | **63 passed** | 0 |
| `python3.11 -m pytest tests/test_packaging_assets.py tests/test_services_packaging_roles.py tests/test_scheduler_markers_audit.py -q` | **19 passed** | 0 |
| `python3.11 scripts/check_grep_gates.py --suite architecture-remediation` | `blocking findings: 0` | 0 |

**未在本执行复跑的 G3 项（逐条引用既有 HEAD 回执，不冒充本次执行）**：`tests/test_services_*.py` 全量、
`test_f4_scope_audit.py`、执行完整性/恢复四门（`test_execution_integrity_e2e` / `checkpoint_resume_semantics` /
`recovery_fixtures_smoke` / `recovery_cross_version`）——引用 F4 在 `c7c26c6` 的回执
（Services 聚合 **1455 passed**、isolation+invariants **43 passed**、IS-11 组合 **78 passed**）
与 F1 在 `5f4b833` 的 17 文件子集 **510 passed**。这些项在本记录中计为 **“覆盖引用（非本次执行）”**，不计入本执行 PASS 数。

---

## 4. 发布候选冒烟（隔离端口 8897；**未使用 8765**）

### 4.1 启动与端点

```bash
HOME=/tmp/rel76/home PYTHONUSERBASE=/home/redacted-user/.local \
  python3.11 -m acp run serve --host 127.0.0.1 --port 8897 \
    --run-root /tmp/rel76/runroot --no-browser --execution-mode local --log-level INFO
# → INFO: Uvicorn running on http://127.0.0.1:8897 ; Run root: /tmp/rel76/runroot
```

| 请求 | 结果 | 摘录 |
|---|---|---|
| `GET /` | **200**，82,478 B | `<title>ACP Molecular Wo…`（Workbench 页面，来自候选检出的静态资源） |
| `GET /api/status` | **200** | `{"service":"ACP Workbench","version":"0.2.0","status":"ok","host":"127.0.0.1","port":8897,"python":"3.11.13","run_root":"/tmp/rel76/runroot","queue":{"queued":0,"pending":0,"running":0,"paused":0,"completed":15,"failed":0,"cancelled":0}}` |
| `GET /api/workflows` | **200** | 15 项：`Confsearch, PESsearch, BatchOptimize, XtbPathSearch, XtbConstrainedOpt, OrcaGradient, nmr, singlepoint, optimize, frequency, scan, irc, tsmode, casscf, xtb_optimize`（registry 驱动清单，字段 `name/label/description/requires_binaries`） |
| `GET /api/v1/jobs?limit=50`（旧 GET 面） | **200** | **15 条旧记录全部可读**（`20261015_220157_008_PESsearch` 等，status 全 `completed`；payload 同时含旧字段与 `attempt/revision/name_revision` 新字段） |

### 4.2 `acp doctor`（本地档，隔离 HOME：`HOME=/tmp/rel76/home`，真实 `~/.cccp.yaml` 未读未写）

| 运行 | 配置 | exit | 摘录 |
|---|---|---|---|
| a | 隔离 HOME 无配置文件 | 1 | `No remote nodes configured (cluster.nodes is empty).` —— 未声明本地档时**保留既有远程门**（非缺陷，符合“保留既有远程 doctor 行为”） |
| b | 本地档 + `executables: {}` | 1 | `Local capability report (usage=local, tier=local-single)`：PASS(3)（run_root/store/config）、**FAIL(7)**——全部 QC 二进制不可解析；`software:orca — 已配置路径不可用（拒绝 PATH 回落：/usr/bin/orca）`（**正确拒绝 GNOME Orca**，S4c 反假 PASS 生效），`xtb/crest/censo/shermo/isostat/molclus` 拒绝 `None` 回落 |
| **c（正式冒烟）** | 本地档 + 真机可执行路径只读副本 | **0** | `Local doctor finished: all checks passed`；PASS(10)：7×`software:*（source=config）` + `run_root /tmp/rel76/loop-root 就绪` + `store` + `config`；FAIL(0)、NOT_VERIFIED(0) |

> doctor 结论：本地档行为 = **PASS（run c，exit 0）**；run a/b 一并记录，证明“零远程节点不误杀本地档（S4）”与“显式无效路径不假 PASS（S4c）”在真实命令面上同时成立。

### 4.3 一条轻量闭环（离线控制面；`HOME` 同上，`ACP_RUN_ROOT=/tmp/rel76/loop-root`）

| 步骤 | 命令 | exit | 摘录 |
|---|---|---|---|
| ingest | `python3.11 -m acp task ingest --xyz-text '…water…' --task-dir …/water_001_demo --name water` | **0** | `{"atom_count":3,"format":"xyz","formula":"H2O","status":"ok"}` |
| plan | `… acp task plan --task-dir …/water_001_demo --workflow singlepoint --method r2SCAN-3c` | **0** | `{"status":"ok","steps":["singlepoint"],"workflow":"singlepoint"}` |
| execute | `… acp task execute --workflow fake --input CCO --name rel76loop --follow` | **0** | `JobManager booted … Poll loop started … {"job_id":"20261019_223126_001_rel76loop","status":"completed","work_dir":"…/rel76loop_fake"}` |
| query | `… acp task query --job 20261019_223126_001_rel76loop` | **0** | `status=completed, attempt=1, revision=2, exit_code=0, progress=1.0`（CAS 字段在候选下读写正常） |
| export | `… acp task export --job 20261019_223126_001_rel76loop` | 1 | `{"status":"no_result",…}` —— 合成 `fake` 工作流不产 `result_manifest`，**按设计**；闭环以 query 终态读为准，此处如实记 exit 1 |

> 闭环判定：**PASS**（提交 → 执行 → 终态 → 持久记录读取，全程隔离 run_root，零生产接触；export 的 `no_result` 已注明按设计）。

### 4.4 停服与端口释放

```
pkill -TERM -f '<serve cmd>'  →  serve.log: "Application shutdown complete." / "Finished server process [1573379]"
curl http://127.0.0.1:8897/api/status → exit 7（connection refused）；ss：8897 不在监听；无残留 python3.11 -m acp 进程
curl http://127.0.0.1:8765/api/status → 200（生产服务全程未受影响）
```

**8897 已释放，服务已 kill（graceful）→ PASS。**

---

## 5. 服务重启（G4/G5）— **NOT_VERIFIED**

**判定：NOT_VERIFIED。阻塞原因（原样记录）：**

```
$ sudo -n true
sudo: 需要密码
$ echo $?
1
```

> **“sudo requires a password; no non-interactive restart”** —— 本机 sudo 无 NOPASSWD，**不存在非交互式重启手段**；
> 按任务约束不重试、不挂起、不索取密码。因此以下项一律 **NOT_VERIFIED**，且**未以任何替代手段伪造**：

| 项 | 状态 | 说明 |
|---|---|---|
| `sudo bash scripts/install_systemd.sh --user redacted-user`（G4 路径 A/B 重新生成） | **NOT_VERIFIED** | 脚本内 `sudo tee` 同样需要密码；**unit 未重新生成** |
| `sudo systemctl restart acp`（G5 重启） | **NOT_VERIFIED** | `sudo -n true` 未过 → 依规**不发起** restart |
| 重启后 `journalctl` / 重启后冒烟 | **NOT_VERIFIED** | 依赖重启 |
| 生产切换到 0.2.0 | **NOT_VERIFIED** | 生产仍为旧进程（见下） |

**unit 生成来源核对（只读完成，unit 未手改）**：快照 `.omo/evidence/acp-parallel-plan-2-services/task-76-unit-snapshot.txt`
（`systemctl cat acp` 原文 + `is-active`/`is-enabled`）。与模板 `scripts/install_systemd.sh:32-51` 对照：

| 键 | 生成模板 | 生产 unit（2026-10-19 只读） | 判定 |
|---|---|---|---|
| Description | `ACP — Auto-Calc Platform Web Dashboard` | `ACP Auto-Calc Platform (FastAPI Web Service + Scheduler)` | DRIFT |
| After/Wants | `After=network.target` | `network-online.target` + `Wants=` | DRIFT |
| ExecStart | `$PROJECT_ROOT/scripts/start_acp.sh` | `/opt/acp2/venv/bin/acp run serve --host 0.0.0.0 --port 8765 --no-browser` | **DRIFT（绕过 start_acp.sh）** |
| StateDirectory / LogsDirectory | 有 | **无** | DRIFT（反向缺失） |
| Environment | `ACP_RUN_ROOT` | 同 + `PYTHONDONTWRITEBYTECODE=1` | DRIFT |
| 加固/日志行 | 无 | `TimeoutStopSec/UMask/LimitNOFILE/StandardOutput*/SyslogIdentifier/Documentation` | DRIFT |
| User/Group/WorkingDirectory/Restart/WantedBy | 有 | 一致 | OK |

- drift 与 todo 71（§4.2）记录**完全一致，无新增手改**；`is-active=active`、`is-enabled=enabled`；`ExecStart` 仍指向
  `/opt/acp2/venv/bin/acp`（**生产 = 旧进程 `0.1.3`，pid 4536，2026-10-18 启动**）。
- **路径 A/B 收口决策：未执行（需 sudo 重新生成）→ NOT_VERIFIED**；本执行**未手改** `/etc/systemd/system/acp.service`（项目铁律遵守）。
- 后续（有 sudo 的执行者）：按 Release_Prep §4.2 二选一 → `install_systemd.sh` 重新生成 → restart → journal/冒烟 → 回填本记录 §5。

---

## 6. 在途任务与兼容窗口（G6 / 矩阵 M1—M6）

### 6.1 在途清单（重启前）

- 生产只读探针（`mode=ro` + backup API）：`jobs by status = [('completed', 15)]`，**0 active / 0 queued / 0 paused**。
- 生产 `/api/status`（只读 curl 8765）：`version 0.1.3`、`queue {queued:0, pending:0, running:0, paused:0, completed:15, failed:0, cancelled:0}`。
- → **重启前在途任务清单 = 空**（15 全 completed）。破坏在途任务风险 = 无；本执行未 pause/cancel/purge 任何任务。

### 6.2 兼容窗口逐行（Release_Prep §2）

| # | 对象 | 本次判定 | 依据（本次执行 or 引用） |
|---|---|---|---|
| M1 旧页面/旧 GET | **PASS（候选上实测）** | `pytest tests/test_acp_api_v1.py` → **101 passed exit 0**；候选 `GET /api/v1/jobs` → 200 且 15 条旧记录可读（§4.1）；新字段与旧字段同 payload 共存 |
| M2 旧 worker（远端在途 attempt） | **NOT_VERIFIED** | 无远端在途 attempt（0 active、无 LSF 流量）；且未重启 → 收养/`bstop` 语义本次无真实对象可验；引用 execution_chain attempt 冻结证据（closure L2-06，F1/F4 覆盖） |
| M3 旧 release（内容寻址） | **NOT_VERIFIED（本次无远端动作）** | 本执行未提交任何远端作业、未触 `release.py` 生产状态；引用 `test_remote_code_release*`（F1 覆盖） |
| M4 在途任务（重启恢复） | **NOT_VERIFIED（未重启）** | “重启后收养/0 重复 bsub”必须以真实重启验证——被 sudo 阻塞；引用 `backup_restore_drill.md` D1—D3（既有演练） |
| M5 科学静态资产 | **PASS** | wheel 内 8/8 digest == master（§2.3）；`test_packaging_assets` 等 19 passed（§3.4） |
| M6 Web 静态资源 | **PASS（候选检出）** | `/` 200（82,478 B，来自仓库检出静态托管）；与代码同检出原子切换（重启后生效于生产 — 生产侧切换归 §5 NOT_VERIFIED） |

**旧页面窗口**：**PASS**（旧 GET 实测）。**旧 worker / 旧 release 窗口**：**NOT_VERIFIED + 原因**（无在途远端 attempt 且未重启；
不以文档引用冒充实测）。**旧 release 窗口的“不删除旧目录”承诺**：本执行零远端写入 → 承诺未被破坏（只读推断，标注非实测）。

### 6.3 候选对旧记录的读取（副本）

候选服务以**扩展后（v21）副本**为 run_root，读出全部 **15 条 v20 时代旧记录**（`GET /api/v1/jobs` 200 + `acp task query` 可读行）——
**“旧记录 × 新代码（含新表 job_outbox）”可读性 PASS**；生产库本身未迁移（仍 v20），由运行中的 0.1.3 旧进程继续只读其事（无在途写）。

---

## 7. 生产未修改声明

**本执行对生产（`/var/lib/acp/runs`、`acp.service`、运行中 0.1.3 进程、真实 `~/.cccp.yaml`）的全部接触均为只读**：

| 接触 | 方式 | 是否写入 |
|---|---|---|
| `/var/lib/acp/runs/acp_jobs.db` | `sqlite3.connect('file:…?mode=ro', uri=True)` + `Connection.backup()` | **否**（快照与副本全部落 `/tmp/rel76`） |
| 生产 run_root 目录 | `ls` / `du` | **否**（未创建 `pre_release_*`，未打包任务树，见 §3.1 偏离登记） |
| 生产服务 | `curl 127.0.0.1:8765/api/status`（只读 GET） | **否** |
| systemd unit | `systemctl cat acp` / `is-active` / `is-enabled`（只读） | **否（未手改、未重新生成）** |
| 真实 `~/.cccp.yaml` | `sed` 只读查看（取可执行路径做隔离 doctor 配置） | **否**（写入的是 `/tmp/rel76/home/.cccp.yaml`） |
| 服务进程 | 无 `systemctl restart/stop`；候选服务只用 8897 | **否**（生产进程 pid 4536 自 10-18 持续运行） |

**明确声明：生产未被发布、未被重启、未被改配置、其数据库与任务树未被写入。**
本次“发布”执行的完整面 = 构建 + 副本 schema 演练 + 隔离冒烟；**生产切换是唯一未完成腿（§5 NOT_VERIFIED）**。

---

## 8. 发布清单（G0—G7）对照

> `tests/baseline/services-evidence/release_checklist.md` 本身**未勾选**（本任务文件所有权限定为发布记录 + evidence；
> 本节即其逐项回执，供 F5/清单 owner 回填）。

| 门 | 项 | 本记录判定 |
|---|---|---|
| G0 | F1/F2/F3/F4 APPROVE | **PASS**（§1，4 份证据路径） |
| G0 | 产物与 C-T54 可核对 | **PASS（配置/资产/条目集）+ NOT_VERIFIED（字节级）**（§2.4） |
| G0 | 矩阵 M1—M4 依据在候选上绿/注明 | **M1 PASS 实测；M2/M3/M4 NOT_VERIFIED 逐行注明**（§6.2） |
| G1 | 4 处版本一致 | **PASS**（§2.1） |
| G2 | DB `.backup` 快照 | **PASS**（位置改记 `/tmp/rel76/db`，§3.1 偏离登记） |
| G2 | 任务树快照 | **NOT_VERIFIED**（13 GB + 只读约束 + 0 在途，§3.1） |
| G3 | 守卫套件 | **本执行 183 项 + grep gates 全绿（§3.4）；全量项引用 F1/F4 HEAD 回执（非本次执行，如实标注）** |
| G4 | unit 收口（路径 A/B + 快照 + is-active） | 快照 **PASS**（§5，`task-76-unit-snapshot.txt`）；路径决策/重新生成 **NOT_VERIFIED（sudo）**；`is-active=active` **PASS** |
| G5 | restart/重启后冒烟/doctor/页面/离线闭环 | 重启 **NOT_VERIFIED（sudo）**；隔离候选冒烟全绿（§4，doctor **PASS exit 0**、页面/状态 **PASS**、闭环 **PASS**） |
| G6 | 在途清单 / 旧 GET / 旧 worker | 清单空 **PASS**、旧 GET **PASS**、旧 worker **NOT_VERIFIED**（§6） |
| G7 | 发布记录 + 证据 | **PASS**（本文件 + `.omo/evidence/…/task-76-*`） |
| G7 | 清单勾选回执 | **以 §8 表替代勾选**（文件所有权边界，说明见表头） |
| G7 | F5 已知会、未回写 F1—F4 | **PASS**（本记录不改任何 F 结论；R4 失效项移交 F5，§2.4/§10） |

**禁止项核对**：未在 F1—F4 前执行（G0 先行）✓；未手改 unit ✓；未无矩阵发布（矩阵 §6.2 逐行）✓；未破坏在途任务（0 在途 + 零控制操作）✓；
产物可核对性按 §2.4 如实三态声明（不拼接 C-T54 sha）✓。

---

## 9. 清理回执

- 候选服务：`Application shutdown complete` / `Finished server process [1573379]`；**8897 不再监听**；无残留 `python3.11 -m acp` 进程。
- 生产服务：`8765` 仍 `http=200`（pid 4536 未被触碰）。
- 临时目录：`/tmp/rel76`（构建树/候选树 wheel/DB 副本/日志）在证据回执归档后**整体删除**（`rm -rf /tmp/rel76`，回执见 evidence `task-76` §cleanup）。
- 隔离 HOME：`/tmp/rel76/home` 随上一并删除；真实 `~/.cccp.yaml` 未动。
- 仓库工作区：本执行未改任何产品代码/配置/测试基线；提交仅含本记录（§10）。

---

## 10. 证据索引与移交

| 证据 | 内容 |
|---|---|
| `.omo/evidence/acp-parallel-plan-2-services/task-76-acp-parallel-plan-2-services.md` | 主回执：命令 + exit code + 摘录（本文执行镜像） |
| `.omo/evidence/acp-parallel-plan-2-services/task-76-unit-snapshot.txt` | `systemctl cat acp` 只读快照（G4 指定文件名） |
| `.omo/evidence/acp-parallel-plan-2-services/task-76-raw/` | `wheel-sha256.txt`、`namelist-audit.txt`、`schema_drill_result.json`、`smoke-curls.txt`、`doctor*.log`、`serve.log`、`g3-*.log`、loop 日志、`src-diff-since-candidate.txt` |

**移交 F5 / 后续执行者（未完成即保持未完成，不回写 F1—F4）：**

1. **生产重启 + unit 收口（路径 A/B）** —— 需有 sudo 的会话执行；完成后回填 §5（现为 NOT_VERIFIED）。
2. **C-T54 补验** —— 冻结后 7 个 `src/` 提交使原字节级验收失效（R4）；在发布提交上重跑 lane-C 复验，或由 F5 明确接受“配置/资产/条目集”四维可核对口径。
3. **C-T54 条目数 543 ↔ 本机 545 差异归因**（§2.4 R4-f）。
4. **G2 任务树快照**（若 F5 认为必要：需写生产目录的授权）。
5. `tests/baseline/services-evidence/release_checklist.md` 的 `[ ]` 回填（文件 owner 执行；本记录 §8 为逐项回执）。

**提交**：`chore(services): post-verification release record + smoke (restart gate reported honestly)`
（**仅** `docs/reference/current/ACP_Services_Release_Record_20261019.md`；evidence 与 raw 不入库）。
