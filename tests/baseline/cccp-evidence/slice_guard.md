# C10-s1 — per-slice isolation / dependency / uniqueness guard (slice_guard.md)

- **tracked_path:** `tests/baseline/cccp-evidence/slice_guard.md`（受版本控制；C 路 `C直写`）
- **created:** 2026-10-18
- **plan_source:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 48（C10-s1）
- **harness:** `scripts/verify_cccp_slice_guard.py`
- **regression guard:** `tests/test_cccp_slice_guard.py`
- **registry:** `tests/baseline/cccp-evidence/workflow_task_map.md`
- **consumers:** T8（能力门）、T16–T30（随片扩展）、T49/T50（收口复跑）

## 1. 用途

首个迁移片（T8）前建立、随每片扩展的**可复用检查能力**：静态反向依赖/动态入口扫描 + 公开 API 无 acp 导入 + task core 可调用隔离 + workflow→task/helper 映射完整性 + 成功/错误科学自检 + 注入式负例。所有检查**命令行化**，不使用"文件存在/计数相等"作为唯一性证据。

## 2. 运行命令

```bash
# 全仓检查（5 项；exit 0 = 全绿）
python3.11 scripts/verify_cccp_slice_guard.py

# 注入式负例自检（临时副本，自带清理）
python3.11 scripts/verify_cccp_slice_guard.py --injection-selftest

# 指向临时副本/隔离 worktree 只跑根域检查（跳 callable/map/science 活检查）
python3.11 scripts/verify_cccp_slice_guard.py --cccp-root /tmp/wt/src/cccp

# 旧/新对象身份 hook（迁移片的 `A is B` 等价断言）
python3.11 scripts/verify_cccp_slice_guard.py --compare-identity acp.core.models:Structure cccp.analysis.structure.models:Structure

# 回归守护（快，纯函数）
python3.11 -m pytest tests/test_cccp_slice_guard.py -q
```

## 3. 检查项（`::guard-check::` JSON 逐项输出）

| # | 检查 | 判据 | 扫描语义 |
|---|---|---|---|
| 1 | `static-reverse-dependency` | `src/cccp/**/*.py` 零命中 | 只认真实 `import acp`/`from acp`、importlib/动态字符串目标、registry 字符串目标、CLI 绕路（subprocess/os.system/which 传 `acp`）；**docstring/注释/provenance 输出中的 `acp.*` 不算** |
| 2 | `public-api-import` | 子进程 `find_spec("acp") is None` 后导入公开面全 ok | 临时 cccp-only 树 + 剪除含 acp 的 sys.path + `BlockACP` 兜底 + `sys.modules` 泄漏断言 |
| 3 | `callable-task-core` | 每个 `tasks/run_*` 均被 `verify_cccp_isolation.py` 覆盖且脚本 PASS | 无 acp 子进程 structured `TaskResult`（stub backend 只抛 RuntimeError，不启动二进制） |
| 4 | `workflow-task-map` | 15 active 工作流各一行；task core 引用可解析；声明 probe 状态与真实覆盖一致 | 解析 tracked 映射表 + 交叉核对 `EXPECTED_TASKS` |
| 5 | `science-selftest` | ORCA optimize 输入块成功（`%geom` + `Recalc_Hess 10`）+ 非法请求抛 typed `TaskInputError` | acp-free 子进程；不启动 QC 二进制 |

## 4. 扩展点（新 `cccp.analysis.*` 自动纳入）

- 当 `src/cccp/analysis/**` 出现时，`discover_top_level_modules()` 自动把 `cccp.analysis` 纳入检查 2 的 acp-free 导入面，静态扫描（检查 1）本就覆盖全 `src/cccp/**`，任何新增 `tasks/run_*` 自动纳入检查 3 的覆盖要求。**无需修改 harness 即生效**（当前已生效：`cccp.analysis` 13 模块被探针导入）。
- workflow→task/helper 映射的新行/扩展只在 `workflow_task_map.md` 增补；新 active 工作流须同步 `ACTIVE_WORKFLOWS`（与 `registry.py` 的 edit coverage 同步）。
- 新 `cccp.analysis` 模块若出现反向依赖，检查 1 立即 FAIL——这是 feature，不是 flake。

## 5. 每片运行记录模板（per-slice run record）

> 迁移片（T8–T30）每片复制下表填一行，附命令原文与原始输出；写入该片 evidence。

```text
slice_id:            T<NN>
base_commit:         <git rev-parse HEAD>
guard_command:       python3.11 scripts/verify_cccp_slice_guard.py
guard_exit:          <0|1>
guard_summary:       checks: 5  passed: <n>  failed: <m>  |  RESULT: <PASS|FAIL>
isolation_command:   python3.11 scripts/verify_cccp_isolation.py
isolation_verdict:   <RESULT line, e.g. PASS — all 15 ...>
workflow_map_rows:   <该片新增/变更的 workflow 行 + probe 状态>
injection_command:   python3.11 scripts/verify_cccp_slice_guard.py --injection-selftest
injection_verdict:   <detected static=True dynamic=True cleaned=True>
identity_hook:       python3.11 scripts/verify_cccp_slice_guard.py --compare-identity <OLD> <NEW>
identity_verdict:    <PASS|FAIL>
success_fixture:     <input fixture path / command / expected artifact>
error_path:          <command / typed exception asserted>
output_comparison:   <old vs new output command + tolerance + verdict>
result:              <PASS|FAIL|NOT_VERIFIED>  (三态；真实软件未跑一律 NOT_VERIFIED)
scope_status:        <IN_SCOPE|DEFERRED|NOT_APPLICABLE>
raw_output_path:     .omo/evidence/acp-parallel-plan-3-cccp/<file>
```

- `success_fixture` / `error_path` / `output_comparison` 是**每片必须填的钩子**（本片只建立能力；具体值随片给出）。
- `result` 三态不得互相替代；`scope_status` 延期单独记录，延期不是通过。

## 6. 负向注入程序（临时副本，禁 live tree）

```bash
# 方式 A（自带，推荐）：临时副本 + 注入 + 检测 + 清理
python3.11 scripts/verify_cccp_slice_guard.py --injection-selftest

# 方式 B（隔离 worktree，证明整仓 harness 会 FAIL）
WT=/tmp/slice-guard-inject-$$
git worktree add --detach "$WT" HEAD
printf '\nimport acp\n' >> "$WT/src/cccp/utils/constants.py"
python3.11 scripts/verify_cccp_slice_guard.py --cccp-root "$WT/src/cccp" --skip-callable; echo "exit=$?"
git worktree remove --force "$WT"
test ! -e "$WT" && echo "worktree cleaned"
```

## 7. 已登记 finding（非本路 / 预存）

| finding | 归属 | 处置 |
|---|---|---|
| `test_f4_scope_audit::test_migration_scope_audit_reaches_migration_files` 失败（`src/acp/services_bootstrap.py`） | Plan-2 Services 车道（T3 已登记 pre-existing/other-lane） | 不在本片处理；不弱化 F4 门 |
| 扫描期间并发车道 C03/T16 落地的 `src/cccp/analysis/structure/parsers.py` 曾短暂含 `from acp.intake.models`（迁移中态） | Plan-3 C03/T16 车道（同路并发） | 检查 1 如实 FAIL → 该车道完成迁移后复跑 PASS；非本片可改文件 |
| `src/acp/calculation/identity.py` 变更 | Plan-3 T53 车道（同路并发） | 本片不 stage 其文件 |

## 8. 未验证项（NOT_VERIFIED）

| 项 | 状态 | 原因 |
|---|---|---|
| 任一真实 QC 软件运行（orca/xtb/crest/censo/isostat/shermo） | `NOT_VERIFIED` | 本片为隔离/静态能力；不启动真实软件 |
| `cccp.analysis` 迁移完成度（同路并发在途） | `NOT_VERIFIED` | 由 C03/T16/T52/T53 交付；本片仅确认其当前可 acp-free 导入 |
