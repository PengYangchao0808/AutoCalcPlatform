# P1 验收记录（提交契约 / 资源别名 / 容量声明）

> 执行：2026-10-29T16:45–16:55 +08:00
> 分支：`acp2/mcp-ux-remediation`；基线 fd2087c7
> 状态：**待外部验收**

## 本轮变更

| 文件 | 变更 |
|---|---|
| `src/acp/api/submission_contract.py` | 新增：Confsearch/BatchOptimize 的语义契约（顶层 method 键、枚举、容器类型、输入来源、`batch_roles` 吞键冲突） |
| `src/acp/api/v1_routes.py` | `create_job` 与 `_preflight_projection` 共用同一校验，422 `input_invalid` + `params.field/rule` |
| `src/acp/scheduler/jobs.py` | 删除 Confsearch `max_conformers → --max-conformers` 映射（NMR 保留） |
| `src/acp/scheduler/resources.py` | `cores` 作为正式别名；`nproc > ncores > cores` 优先级；非零冲突报错 |
| `src/acp/api/routes.py` | capabilities `limits` 改从已装配 admission 实例派生（max_cores/max_walltime_s/default_walltime_s + enforcement） |
| `tests/test_acp_mcp_ux_p1_contract.py` | 新增 15 条验收断言 |

## 逐条验收

### P1-01 纯提交契约

- 允许键 = `jobs.py` 消费者表（scalar flags）∪ 显式登记的非 flag 字段（`solvent`/`ewin`/`levels`/`schema_id` 等）。
- 未知键 → `unknown_field`；非法枚举 → `invalid_enum`；容器类型错误 → `invalid_type`；缺输入 → `missing_input`；`batch_roles` + flat scalar → `conflicting_fields`。
- 不修改入参对象（校验只读）。
- 覆盖：**Confsearch + BatchOptimize 的顶层 method 键与已知枚举**；`levels` 嵌套仅类型检查（文档标注，后续 phase 扩展）。

### P1-02 两入口共用

- `POST /api/v1/preflight` 与 `POST /api/v1/jobs` 对同一错误返回**完全相同**的 `code/params`。
- 错误发生在分类/审批/快照/派发之前：测试断言任务计数不变。

### P1-03 `max_conformers`

- Confsearch 提交前 422（`method.max_conformers` unknown_field）。
- CLI 表面：`run nmr --help` 含 `--max-conformers`；`run Confsearch --help` 不含。
- 行为变化：历史上传 `max_conformers` 的 Confsearch 请求现在会被拒绝（预期，不再静默忽略）。

### P1-04 资源别名

- `cores=4` 与 `nproc=4`/`ncores=4` 等价；`nproc=4, cores=8` 报 `disagree`。
- 历史优先级保留：`nproc > ncores > cores`，低优先级 0 视为未设置。
- `tests/test_memory_resource_chain.py` 全绿（含摘要/优先级用例）。

### P1-05 容量声明

- `/api/capabilities.limits` 取 `app.state.admission.limits`：`max_cores`/`max_walltime_s`/`default_walltime_s`/`enforcement`。
- 环境覆盖 `ACP_ADMISSION_MAX_CORES=8` 后，discovery 与 admission snapshot 数值一致。
- 无 admission 时 `enforcement="not-verified"`（不伪造强制）。

## 回归

| 批次 | 结果 |
|---|---|
| P1 契约测试 | 15 passed，exit 0 |
| preflight fingerprint / request-field-equivalence / discovery / confsearch progress / services mcp / mcp process | 101 passed |
| workflow retirement / v1 science status / frame candidates / structure viewer / api | 177 passed |

证据：`/ACPServer/acp_mcp_ux_20261029/evidence/p1/`（含 cwd、命令、模块路径、文件哈希、退出码）。

## 边界（不冒充 PASS）

- `levels` 嵌套字段尚未逐个校验；只保证是对象。声明为 partial coverage，P2 的 capabilities 必须如实标注。
- BatchOptimize 仅对 `source_type=batch_structures` 校验 `items` 非空；其它输入形态不做键级校验。
- capabilities 新增 `default_walltime_s` / `enforcement` 为**附加键**，未删除既有键。
- C1 真实客户端转换链仍未验证（P0 结论，不阻塞 P1）。
- 未跑全量 `pytest tests/`（慢测与浏览器套件不在本阶段范围）。
