# P1 验收记录（提交契约 / 资源别名 / 容量声明）— 复验整改版

> 执行：2026-10-29T16:45–17:05 +08:00（含第一轮验收后的整改）
> 分支：`acp2/mcp-ux-remediation`；基线 fd2087c7
> 状态：**待外部复验**

## 第一轮验收后的整改（针对 FAIL）

| 问题 | 整改 | 证据 |
|---|---|---|
| `batch_roles` + flat scalar 被错误拒绝 | 删除该冲突规则。Workbench 合法发送两者；引擎在 `batch_roles` 路径读 `role_opts`（`batch/engine.py:1335`），flat 镜像不是执行源 | `test_batch_roles_with_flat_scalars_is_accepted` |
| Confsearch input 漏 `source_type/charge/multiplicity/source_ref/asset_id` | 允许表补齐；`input_chemistry_flags` 是真实消费者 | `test_workbench_shaped_confsearch_payload_is_accepted` |
| BatchOptimize input 漏 charge/multiplicity、无必填约束 | 补齐；`required_input_any` + `batch_structures_only` 生效（空 items 拒绝） | 契约单测 |
| `method.selected_ids` 被允许但无人消费 | 移到 input 允许表（`batchoptimize_method_flags` 读 `inp.selected_ids`） | 契约单测 |
| `levels` 嵌套未校验 | 从 `catalog.METHOD_SCHEMAS` 派生 level_id → 字段表；未知 level / 未知字段（如 `functonal`）422 | `test_nested_level_typo_is_refused`、`test_unknown_level_id_is_refused` |
| preflight 不校验资源 | 新增 `validate_resource_request`（纯函数），preflight 与 submit 共用；bool、非整数、非零冲突都拒绝；`"0"` 低优先级视为未设置 | `test_resource_conflict_is_refused`、`test_resource_bool_and_zero_alias` |
| Workbench 仍有 `max_conformers` 入口 | 从 `frontend/ACP_Workbench_minimal.html` 移除字段与提交逻辑 | `grep conf-max-conformers` = 0 |
| 证据日志命令写成 `<batch>` | 重采：真实命令、cwd、模块路径、退出码 | `evidence/p1/p1_regression_{a,b}.txt` |

## 本轮变更

| 文件 | 变更 |
|---|---|
| `src/acp/api/submission_contract.py` | 新增：语义契约（顶层 method、枚举、容器、levels 嵌套、input 来源） |
| `src/acp/api/v1_routes.py` | 两入口共用契约 + 资源纯校验，422 `input_invalid` + `params.field/rule` |
| `src/acp/scheduler/jobs.py` | 删除 Confsearch `max_conformers` 映射（NMR 保留） |
| `src/acp/scheduler/resources.py` | `validate_resource_request` + `cores` 别名 + 冲突规则 |
| `src/acp/api/routes.py` | capabilities limits 取已装配 admission |
| `frontend/ACP_Workbench_minimal.html` | 移除 `max_conformers` 入口 |
| `tests/test_acp_mcp_ux_p1_contract.py` | 26 条验收断言 |

## 逐条验收

### P1-01 纯提交契约（Confsearch + BatchOptimize）

- 允许键 = 消费者表（scalar flags）∪ 显式登记的非 flag 字段；`levels` 由 catalog schema 派生。
- 未知键 / 非法枚举 / 错误容器 / 缺失输入 / 未知 level 或字段 → 结构化 `field/rule`。
- 入参不被修改（`test_contract_does_not_mutate_inputs`）。

### P1-02 两入口共用

- preflight 与 submit 对同一错误返回完全相同的 `code/params`。
- 校验位于分类、审批、快照、派发之前；任务计数不变。
- 资源校验同样两入口共用（此前只有 submit 在执行层解析）。

### P1-03 `max_conformers`

- Confsearch 提交前 422；CLI 表面 `run nmr --help` 有、`run Confsearch --help` 无。
- **行为变化**：历史 Confsearch payload 带该键时，HTTP 重提 422；`manager.rerun` 复用已保存 spec，绕过校验，旧键被忽略（不再生成不存在的 flag）。
- Workbench 最小界面入口已移除。

### P1-04 资源别名

- `cores` 与 `nproc`/`ncores` 等价；优先级 `nproc > ncores > cores`；低优先级数值 0 视为未设置。
- 任意位置 bool、非整数、非零冲突 → 拒绝；`resources` 原始对象不被回写。
- `tests/test_memory_resource_chain.py` 全绿。

### P1-05 容量声明

- `/api/capabilities.limits` 取 `app.state.admission.limits`（同一实例交给 manager 与 app.state）。
- 环境覆盖 `ACP_ADMISSION_MAX_CORES=8` 后 discovery 与 snapshot 一致；无 admission 时 `enforcement="not-verified"`。
- 新增 `default_walltime_s`/`enforcement` 为附加键，既有客户端按开放字典接收。

## 回归

| 批次 | 命令/结果 | 证据 |
|---|---|---|
| P1 契约测试 | `pytest tests/test_acp_mcp_ux_p1_contract.py -q` → 26 passed，exit 0 | `evidence/p1/p1_contract_run.txt`（需按新命令重采，见下） |
| 批次 A | preflight fingerprint / request-field-equivalence / discovery / confsearch progress / services mcp / mcp process → 101 passed，exit 0 | `evidence/p1/p1_regression_a.txt` |
| 批次 B | workflow retirement / v1 science status / frame candidates / structure viewer / api → 177 passed，exit 0 | `evidence/p1/p1_regression_b.txt` |

## 边界（不冒充 PASS）

- `levels` 字段表来自 catalog（UI 与引擎共同读取）；若存在引擎-only 字段未进 catalog，会被拒绝——目前未发现，但需在集成回归中留意。
- BatchOptimize 非 `batch_structures` 形态（from_artifact/items_file/single）只要求存在其一，不做键级校验。
- C1 真实客户端转换链仍未验证（P0 结论，不阻塞 P1）。
- 未跑全量 `pytest tests/`（慢测与浏览器套件不在本阶段范围）。
