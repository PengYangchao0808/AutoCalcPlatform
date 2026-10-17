# ACP Execution-Integrity Re-baseline Notes

**Created:** 2026-10-06（`.omo/plans/acp-execution-integrity-remediation.md` todo 19）
**Audience:** 兄弟计划 `acp-cccp-architecture-remediation` 的 **todo 40（A8 平台契约完整回归）与 todo 41（A9 真实 QC + 远程生命周期三态）再基线**——本文件只登记再基线口径与文档/实现契约冲突修正，**不修改该计划文件**。
**Implementation stations:** `src/acp/scheduler/store.py`（CAS）、`src/acp/scheduler/manager.py`、`src/acp/scheduler/remote/{paths,release,submission,runner}.py`、`src/acp/calculations/{executor,step_requirements,step_result,checkpoint}.py`、`src/acp/calculations/batch/engine.py`。

---

## 1. 再基线口径（todo 40/41 验收时按以下契约理解）

| 项 | 契约（现行实现） |
|---|---|
| 新指纹 schema（两层身份） | executor 侧 `identity_schema=2`：`plan_identity`（计划级）+ `step_identity`（步骤级，实际解析的 charge/multiplicity 等任务专属参数纳入身份，`executor.py:938`）；batch 引擎保持 `identity_schema=1`、以逐 item `step_result.json` 存在性/版本判定兼容性（`checkpoint.py:25-26`）。身份不匹配 → 保守重算。 |
| `step_result.json` | batch 每 item 的自证回执（`calculations/step_result.py::write/read/verify_step_result`）——文件自证属于当前计算（item 身份 + 工件摘要）；缺证/不匹配不复用。 |
| CANCELLING 确认语义 | CANCELLING 只有**确认远端已停止**（bkill 后 bjobs 核对消失）后才置 CANCELLED；取消与 bsub 协调至恰好一种结局、无重复 bsub。 |
| submit/cancel 状态 | `result["remote"]["submit_state"]`（`intent`（先于 bsub 持久化，含 `submission_id`/owner token/lease）→ `submitted`）；`cancel_state` 同层。`not_accepted` 只由 5 条证据成立**且提交权租约已失效**；租约 TTL 由真实超时导出、wall-clock UTC 跨进程可解释。 |
| attempt / 原地重跑契约 | `attempt` 1-based，唯一来源 scheduler（`store.py` 列 + CAS 守卫）。rerun/edit-recalculate = **原地重跑**：同任务目录、同远端目录、`requeue_with_spec()` 单事务 `attempt+1`；旧 attempt 回执归档 `WORK/00_RUNTIME/attempts/<N>/`（`_archive_attempt_receipts` + `_archive_previous_resume_source`）；`_RERUN_STABLE_FILES`（input.xyz/input_source.json/task.json/job.json）跨 attempt 保留。**不生成 `{name}__rerun` 新任务/新目录。** |

## 2. 文档 ↔ 实现契约冲突修正（本 todo 一并落档）

1. **`{name}__rerun` 旧描述已移除**：README 队列操作节与 root `AGENTS.md` Job 生命周期行原描述"另起新任务（`{name}__rerun`）"已改为原地重跑口径（上文表末行）。
2. **`auto_sync` 语义**（`remote/config.py:267-274`、`runner.py:1943-1968`）：生产 `auto_sync=True` 每次发布**已验证**内容寻址 release（verify-then-publish）并绑定后才 bsub；`auto_sync=False` **绝不**回退共享可变目录——提交被拒；唯一逃生门是显式 dev 环境变量 `ACP_REMOTE_ALLOW_UNVERSIONED=1`（provenance 标注 `unversioned-shared` + 告警，不承担不可变保证）。
3. **共享契约表 A/B/C/D 字段投影**：job detail 与 v1 recovery 矩阵包含 `submit_state`/`cancel_state`（`api/v1_schemas.py::JobRecovery`）与 `attempt`（detail 响应字段）——todo 40 的 A8 回归矩阵按此断言字段存在与语义。
4. **`resume_source.json` + 两层身份**：batch 与 executor **同源** `resume_source.json`（`batch/engine.py:622 resolve_resume_source`）；恢复前检查旧 release/旧 attempt 的协议兼容性（executor 用 `identity_schema`、batch 用逐 item `step_result.json` 存在性/版本）；不兼容 → `compatible:false`、全量重算。任务专属参数（实际解析的 charge/multiplicity）入身份，全部 `item_cache_key` 调用点同步。
5. **必需步骤 blocked 时整体不判 completed**；诊断性继续必须显式 `diagnostic_only=True` 且恢复时重判。

## 3. D03 release 排除与既存顶层依赖风险（处理结论）

- **排除清单**：release/sync 文件集 = `build_sync_file_list` —— `src/acp` **排除 `api/` 与 `scheduler/`**、`src/cccp` 全部、`requirements-node.txt`、`config/defaults.yaml`（`release.py::build_release_manifest` docstring；`release_id` 只覆盖 files map）。
- **三处顶层依赖（节点侧不可用，因 scheduler/api 不随 sync）**：
  1. `src/acp/calculations/irc/source.py:16-17` — imports `acp.scheduler.files.resolve_safe` / `acp.scheduler.jobs`；
  2. `src/acp/results/irc_remote_live.py:12-13` — imports `acp.scheduler.remote.fetcher.RemoteResultFetcher` / `acp.scheduler.files`；
  3. `src/acp/results/structure_migration.py:12` — imports `acp.scheduler.jobs`。
- **结论**：三者经静态可达性核实**非 CLI 可达**（节点侧 `acp run` 执行路径不触达），node 侧隔离断言保持绿；Oracle 残余 5。**不做 sync 排除扩张**（不把 scheduler/api 加进同步集）；若未来节点侧需要这些模块，须先重构依赖方向而非扩同步。

## 4. 迁移台账注记

本计划（execution-integrity）全部机制归 **ACP 侧**（`acp.scheduler`/`acp.calculations`/`acp.api`），**不新增任何 cccp 驻点**——见 `tests/baseline/refactor-evidence/migration_ledger.md` 末尾 addendum。
