# Round-8：外部 Agent 全研究链反馈修复（MCP 自足性 + 方法选择 USER CORRECTION）

## 背景

外部 MCP Agent 完整跑完「TS 搜索 → BatchOptimize → IRC → DLPNO」后提交了 15 条反馈。核心诉求是
**MCP 必须自己覆盖 agent 的全部需求**：不应为了读产物、拿能量/吉布斯、定位任务而绕过 MCP 去读
run root 或 grep ORCA 输出。本轮按
`reports/acp2_mcp_guidance_20261030/round8_plan.md` 分 5 批实施，全部为收紧密封或纯投影，不放宽
任何科学/安全护栏。

## 修复清单

### P0（预检/文档说可行 → 运行必挂）

1. **Batch `items` 缺 `source_type`**：非空 `input.items` 必须配 `input.source_type="batch_structures"`，
   提交与预检同返回 `422 input_invalid` / `field=input.source_type` / `rule=missing_source_type`；
   运行期本地/远端派发守卫给出同名错误（不再落到「requires a batch artifact…」的谜语）。
   `from_artifact`/`items_file` 不受影响，**不做**「有 items 就推断 batch」。
2. **ArtifactRef 构造不出（文档错误）**：`acp_status.artifacts` 明确是**描述符**（含真实
   `size_bytes` 与现成的 `prepare` 调用模板），不是五元组；读取的正路改为
   `acp_prepare_artifact` → 回包 `artifact_ref`/`read` → `acp_read_artifact`。指南
   `topic.artifact_locating` 按此重写（旧文「描述符带 generation/digest」是错的那条路）。
3. **IRC `ts_source` 黑盒**：契约示例删除手写 `ts_source`；它是平台从
   `source_job_id + source_product_id` 派生的 provenance。半成品（缺任一锁定字段，含值为 `null`）
   → 提交与预检同返回 `422` / `rule=irc_ts_source_partial`；完整但不等值仍由 manager 报
   「changed」（证据比较未放宽）。预检投影 `effective.source_reference`（`runtime_derived`）。
4. **USER CORRECTION（方法选择）**：几何优化默认 **r2SCAN-3c**——对绝大多数体系它是最优选择；
   **不推荐用 wB97M-V 做几何优化**（仅用户明确要求或复现历史时，并记录理由）。单点默认仍是
   wB97M-V + def2-TZVP(D)。契约 Confsearch 示例同步改为 `dft_opt=r2SCAN-3c` /
   `refinement_sp=wB97M-V`；catalog 生产预设本就全为 r2SCAN-3c，未改指纹。

### P1（契约/闭环）

5. **状态词表与计数**：发布 10 个真实状态（含 `starting/pending/cancelling`），新增
   `status_group=active|terminal|running_now`（与 `status` 互斥，非法值 422 + `allowed`）；
   `counts` 改为**过滤前**且恒含 10 键（空 jobs 不再伴随空 counts）。
6. **Batch 顶层 mirror 静默失效**：缺 `int` role/字段时，`optimization_*`/SP/温度压力缩放等
   顶层 mirror 统一 422 `missing_role_mirror`（值不一致仍是既有的 `shadowed_field`）；指南给出
   「只交 TS」的正确写法（不写任何顶层 mirror）。
7. **`levels` 语义**：`opt_recalc_hess` 的 `"auto"` 在预检 `effective.roles.<role>.hessian_resolution`
   投影成具体间隔（`reason=auto|explicit_interval|explicit_off|symbols_required`）；`opt_initial_hessian`
   只报 requested。
8. **yellow flags**：`/usr/bin/orca` 是 GNOME 屏幕阅读器 → typed `backend_unavailable`（round-7
   已修，本轮回归守护）。
9. **TS 失败只回 `[unknown]`**：分类补齐 `scf_not_converged`/`opt_maxiter`/
   `hessian_not_positive_definite`/`error_termination`/`geometry_not_converged`（legacy
   `scf_failure` 保留），消息 `failed [<class>]: <summary>`（≤200 字符、无宿主绝对路径），
   详情进 `metadata.failure_details`；Batch 逐 item 记录 `rescue_failure_details`。
10. **结果要 grep `freq.out`**：`opt_freq_sp` 只要频率步成功就登记
    `RESULT/thermochemistry/<item>.json`（`thermo_report` → 描述符 `kind=thermochemistry`，
    可 prepare→read），ZPE/焓/吉布斯/熵 + 温度压力相校正；Shermo 产品优先且绝不覆盖；缺值不造假。
    structure 产品补 `energy_hartree`/`thermochemistry_product_id`/`hessian_*`。
11. **目录名复用**：收养失败**不删除**含他人文件的目录（保留 + 事件
    `task.dir_adoption_failed_leftover`）；分配时若 `<dir>/task.json` 属于别的作业则跳过并继续
    `__NN`；状态详情 `directory_owner_task_id` 可交叉验证。
12. **两个真相源 / 未知引用**：`acp_status` 未知 id → `not_found`（不可重试），文案提示
    `acp_list_jobs`、`task.json.task_id` 且 `job_id ≠ operation_id`；404 详情只读探测
    `run_root_task_found`（不收养、不写盘）。
13. **Hessian 算两遍**：本轮只做**可见性**（`hessian_recomputed`/`hessian_source`），复用策略
    待科学裁决（K1），不改任何默认。
14. **上下文与脱敏**：workflow-scoped 契约默认改为 CORE 视图（Batch 契约块 10.6KB → 3.9KB，
    整包 11.6KB → 5.0KB），`contract_sections=input|method|method.levels|resources|examples|
    constraints|gaps|full` 按需取（**MCP `acp_capabilities` 同样支持**）；预检回显把整份 xyz
    换成 `omitted+sha256+atoms+bytes`（25KB → <1KB，`plan_digest` 仍取全量）；
    local-single 的 MCP 响应给出 `work_dir/name/idempotency_key` 原文（API-key/多租户仍打码，
    默认 `redact()` 逐字节不变）。
15. **用户自认的锅（仅指南）**：跨方法只比相对值/能差（绝对总能偏移不是解析错误）；命名类判断
    计算前用 InChIKey/CAS/文献钉死。

## 关键设计决定

- MCP-first：产物读取、热化学、失败原因、任务定位、信封分区全部经 MCP 可达，不再需要读 run root。
- fail-fast：预检与提交对同一 payload 返回相同 `code/field/rule`（items/IRC/mirror/basis）。
- 不放宽：digest 校验、native_scan/pointwise 拒绝、TS 证据比较、preflight 零副作用、无第二状态库。
- 治理：F4 范围审核登记 **Amendment V**（round-8 ORCA 失败面）；契约 digest 链已重锚
  （`coverage_sha256 3e12a6ca…`，`contract_digest` 重算，validator VALID）。

## 验证（本地）

- 全仓 `pytest -m "not slow and not contract"`：**12858 passed / 0 failed / 23 skipped /
  116 deselected（40:47）**。
- 独立只读验收：13 条审计项中 12 条 PASS；发现的 P1（`contract_sections` 未贯通 MCP）已修并补
  MCP 层测试；P2 中「`ts_source` 值为 null 视为 partial」已修。
- `validate_acp2_contracts.py` VALID；`validate_acp2_gap_coverage.py` VALID；
  `check_docs_links.py` PASS（377 refs）；`check_grep_gates.py --suite architecture-remediation`
  10/0；`verify_cccp_isolation.py` PASS（15/15）。

## 未验证（NOT_VERIFIED，不得改写为 PASS）

- 真实 Controller 的 prepare→read 往返（用 Controller 形状 fake + 真实 validator 验证）。
- 真实 ORCA 频率日志的热化学解析；测试日志/失败尾巴为合成（仓库无真实失败 OptTS fixture）。
- 008/022 目录复用根因（只钉住不变量，需用户提供 task.json/job.json/state.json）。
- 真实 MCP SDK / 真 Controller 上的 `contract_sections`/`status_group`；MCP stdio 层为单元+进程测试。
- `counts` 在 >5000 作业项目上的扫描上限（有界读取，无截断标记）。
- 新 wire 字段（`contract_sections`/`status_group`）尚未写入 `application-api.v1.json` 契约语料
  （需版本化评审）。

## 回滚

各批独立：A 契约/预检、B 状态/产物/信封、C 指南、D 调度运行时、E 结果/失败可读性。
breaking-ish（PR 内已标注）：未知 id `resource_not_ready → not_found`；`counts` 改为过滤前；
workflow-scoped 默认 CORE 视图（`contract_sections=full` 可还原）；非空 items 缺 `source_type`
由「运行期失败」变 422；半成品 `ts_source` 由「changed」变 422；local-single 可见性放宽（限本模式）。
