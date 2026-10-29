# P1 验收记录（提交契约 / 资源别名 / 容量声明）— 第二轮整改

> 执行：2026-10-29T16:45–17:15 +08:00
> 分支：`acp2/mcp-ux-remediation`；基线 fd2087c7
> 状态：**待外部复验**

## 整改记录

### 第一轮验收后

| 问题 | 整改 |
|---|---|
| `batch_roles` + flat scalar 被错误拒绝 | 删除冲突规则；Workbench 合法发送两者，引擎读 `role_opts`（`batch/engine.py:1335`） |
| Confsearch input 漏 `source_type/charge/multiplicity/source_ref/asset_id` | 允许表补齐（`input_chemistry_flags` 是消费者） |
| Batch input 无必填、`selected_ids` 位置错 | 补必填 + 把 `selected_ids` 移到 input |
| `levels` 未校验 | 从 catalog 派生 level_id→字段表 |
| preflight 不校验资源 | 新增 `validate_resource_request`，两入口共用 |
| Workbench 仍有 `max_conformers` | 从最小 Workbench 移除字段与提交逻辑 |
| 证据无真实命令 | 重采：命令、cwd、模块路径、退出码 |

### 第二轮验收后

| 问题 | 整改 |
|---|---|
| levels 允许表漏真实消费者：`screening_sp`、`dft_opt.recalc_hess`、`refinement_sp.aux_c_basis`、顶层 `refinement_threshold` | 从 `cccp/analysis/ensemble/levels.py` 的消费者集合补：新增 `screening_sp`（SP 全字段）、`dft_opt + recalc_hess`、`refinement_sp` 用 SP 全字段；`levels` 允许标量 `refinement_threshold` |
| Batch `items=[]` 无 source_type 通过；`batch_roles={"int":[]}` 通过 | 空列表/空字典视为缺失；`batch_roles.<role>` 必须是对象 |
| `batch_roles` 与 `opt_recalc_hess`/`scf_orbital_inherit` 同传被吞 | 当 flat 值与 `levels.batch` 同名键不一致时报 `conflicting_fields`；一致视为兼容镜像 |
| 资源前置校验与执行解析不一致：0/-1/`"4.0"`/`"1e999"` | `_core_int` 与执行解析同构：bool、非整数、非有限、负数、字符串小数全部拒绝 |
| 缺完整 argv 验收 | 新增 3 工作流本地 + 远端 argv 经 `build_parser` 解析（Confsearch / BatchOptimize / NMR） |
| 合法用例只调纯函数 | 新增 HTTP 入口回归：Workbench 形态 Confsearch（含 levels）与 BatchOptimize（batch_structures）preflight 200 |

### 第三轮验收后

| 问题 | 整改 |
|---|---|
| `dft_opt` 漏 `aux_j_basis`/`aux_c_basis`/`ri_approximation`（`_base_route_extras` 消费者） | 补进 `dft_opt` 允许表 |
| Batch 只在有 `levels.batch` 时检查镜像，`batch_roles.int.opt_recalc_hess=5` + 顶层 `10` 可绕过 | 镜像值同时从 `batch_roles` 各角色提取，不一致即 `conflicting_fields` |
| 本地 argv 仍是手拼 base+flags | 新增测试改用生产本地 builder `JobRunner._build_cmd`（NMR 绑定真实 `_build_nmr_cmd`/`_materialize_experiment`），与远端 `build_remote_cli_command` 一样经 `build_parser` 解析 |

### 第四轮验收后

| 问题 | 整改 |
|---|---|
| Batch 角色字段与顶层字段的映射不完整（`int.method→optimization_method`、`ts.method→transition_state_method` 等） | 增加 `_ROLE_FLAT_ALIASES`；镜像值同时从各角色与 `levels.batch` 提取，不一致 `conflicting_fields` |
| 无镜像值的顶层 flat 键被吞（如 `minimum_opt_recalc_hess`） | 不在 Workbench 默认镜像集内的、无对应角色值的 flat 键 → `shadowed_field` 拒绝 |
| P1-02 副作用证据只核任务数 | 测试改为拒绝前后 `store.counts()`、`admission.snapshot()` 全等，且 `_submission_jobs` 为空 |

## 本轮变更

| 文件 | 变更 |
|---|---|
| `src/acp/api/submission_contract.py` | 语义契约：顶层 method、枚举、容器、levels（catalog ∪ 消费者）、input 来源、batch 镜像冲突 |
| `src/acp/api/v1_routes.py` | 两入口共用契约 + 资源纯校验，422 `input_invalid` + `params.field/rule` |
| `src/acp/scheduler/jobs.py` | 删除 Confsearch `max_conformers` 映射（NMR 保留） |
| `src/acp/scheduler/resources.py` | `validate_resource_request`、`cores` 别名、与执行同构的解析 |
| `src/acp/api/routes.py` | capabilities limits 取已装配 admission |
| `frontend/ACP_Workbench_minimal.html` | 移除 `max_conformers` 入口 |
| `tests/test_acp_mcp_ux_p1_contract.py` | 36 条验收断言 |

## 逐条验收

### P1-01 纯提交契约

- 允许键 = 消费者表（scalar flags）∪ 显式登记非 flag 字段；levels 字段来自 catalog schema ∪ `cccp/analysis/ensemble/levels.py` 消费者。
- 未知键、非法枚举、错误容器、未知 level/字段、空输入、非镜像冲突 → 结构化 `field/rule`。
- 入参不被修改；HTTP 入口接受真实 Workbench 形态（含 `screening_sp`/`recalc_hess`/`aux_c_basis`）。

### P1-02 两入口共用

- preflight 与 submit 对同一错误返回相同的 `code/params`，位置在分类/审批/快照/派发之前；任务计数不变。
- 资源纯校验同样两入口共用。

### P1-03 `max_conformers`

- Confsearch 提交前 422；CLI 表面 NMR 有、Confsearch 无。
- 行为变化：HTTP 重提 422；`manager.rerun` 复用已保存 spec 绕过校验，旧键被忽略（不再生成不存在的 flag）。
- 三工作流本地/远端 argv 均通过 `build_parser` 解析。

### P1-04 资源别名

- `cores` 等价 `nproc`/`ncores`；优先级 `nproc > ncores > cores`；低优先级数值 0 视为未设置。
- 0、-1、`"4.0"`、`"1e999"`、bool、非整数 float 在执行前即拒绝；非零冲突拒绝；原对象不回写。
- `tests/test_memory_resource_chain.py` 60 passed。

### P1-05 容量声明

- capabilities.limits 取 `app.state.admission.limits`（同一实例交给 manager 与 app.state）。
- 环境覆盖后 discovery 与 snapshot 一致；无 admission 时 `enforcement="not-verified"`；新增键为附加项。

## 回归（真实命令、cwd、退出码，见 `evidence/p1/`）

| 批次 | 结果 |
|---|---|
| 契约测试 `tests/test_acp_mcp_ux_p1_contract.py` | 37 passed，exit 0 |
| 资源链 `tests/test_memory_resource_chain.py` | 60 passed，exit 0 |
| 批次 A（preflight/digest/discovery/progress/services-mcp/mcp-process） | 101 passed，exit 0（首跑遇 `test_signal_shutdown_is_clean[2]` 负载下抖动，单跑与复跑均通过，见证据 note） |
| 批次 B（retirement/v1-science/frame/structure-viewer/api） | 177 passed，exit 0 |

## 边界（不冒充 PASS）

- levels 字段表 = catalog `METHOD_SCHEMAS` ∪ `cccp/analysis/ensemble/levels.py` 消费者（含 `screening_sp`、`dft_opt` 的 `recalc_hess`/`aux_j_basis`/`aux_c_basis`/`ri_approximation`、顶层 `refinement_threshold`）。若仍有未登记消费者，集成回归继续发现。
- BatchOptimize 非 `batch_structures` 形态只要求存在来源之一，不做键级校验。
- C1 真实客户端转换链仍未验证（P0 结论，不阻塞 P1）。
- 未跑全量 `pytest tests/`（慢测/浏览器套件不在本阶段范围）。
