# Round-7：外部 Agent 使用反馈修复（P0/P1 机制 + 次级坑）

## 背景

外部 MCP Agent 完整跑了一条研究链后提交了 8 类反馈：singlepoint 因 release 冻结永久 stale
整条不可用；batch_roles 静默吞掉单点级别；自定义基组被默认辅助基组静默覆盖；热化学（ZPE/H/G/S）
不能经 MCP 读取；推荐的 PES→publish→Batch 链路对纯 MCP 会话是死路；preflight 只做 schema
校验；以及 xTB 约束语法被忽略、幂等键拒绝括号、`/usr/bin/orca` 是 GNOME 屏幕阅读器、DLPNO
能量标签、cancel 回包过大、目录布局不一致等次级坑。

本 PR 按计划 `reports/acp2_mcp_guidance_20261030/round7_plan.md` 分 5 批实施，全部为行为修复，
不放宽科学/安全边界；每项可独立回滚。

## 修复清单

### P0

1. **singlepoint 冻结 release 永久 stale（#1）**：`attempt_freeze` 新增
   `refresh_local_release()` / `StaleReleaseRefreshError`（双 digest + 管理员命令）；
   `scheduler/runner` 丢弃缓存的 release，重建一次、重试一次；digest 校验永不跳过，
   注入的 freeze pin 不被替换；BatchOptimize 不走该路径因此不受影响。
2. **batch_roles 静默吞掉单点级别（#2）**：per-role `sp_method`/`sp_basis`（int/ts 各自生效，
   ts 不再继承 int 的 SP）；顶层 SP 镜像缺 int role/字段 → 422 `missing_role_mirror`
   （不再静默丢弃）；无 `batch_roles` 的平铺 payload 行为不变；`effective_config` 逐 role
   输出 SP + 来源。
3. **辅助基组覆盖轨道基组（#3）**：`route_render` 新增「恰好一个轨道基组」不变量
   （aux 必须以 /J、/C 后缀或 `%basis` 块出现），冲突抛出 typed `RouteBasisConflictError`
   （`rule=basis_conflict`）；预检、提交（新接线）与渲染器同规则拒绝；复合方法路由不变。

### P1

4. **热化学可经 MCP 读（#4）**：频率与 Batch 的 ZPE/H/G/S 发布为 `THERMO_REPORT` 产品
   （`RESULT/thermochemistry.json` / `RESULT/thermochemistry/<item>.json`），artifact 描述符
   映射为 `kind: "thermochemistry"`；缺块不造零；payload 不含宿主绝对路径。
5. **纯 MCP 的 TS 链路显式化（#5）**：`task.find_transition_state` 明确「无预铸授权时
   publish 必被拒」，把 inline `role:"ts"` 写成一等路径（不是兜底），禁止 probe/自造授权。
6. **preflight 语义（#6）**：新增 BatchOptimize / singlepoint 只读 analyzer（per-role SP、
   逐 item 角色投影、route/aux 冲突）；submit 运行同一 analyzer，预检/提交对静态错误
   同 code/field/rule；预检仍零副作用。
7. **xTB 约束（#7a）**：`$constrain` 语法在写盘前校验（typed `XtbConstraintSyntaxError`）；
   运行后检测 xTB 丢弃约束 → FAILED 而非 COMPLETED；XtbPathSearch 的原始 `path_inp_text`
   同样在 spawn 前校验。
8. **DLPNO 能量标签（#7d）**：结果取最后一个 `FINAL SINGLE POINT ENERGY`；`E(0)` 标为 SCF
   参考、`E(TOT)`/`E(T)` 保留 metadata；E(0)==final 时回退 E(TOT)+E(T) 或拒绝，绝不把
   E(0) 当 CCSD(T)。

### P2

9. **幂等键（#7b）**：唯一权威 `IDEMPOTENCY_KEY_PATTERN` 接受 `()`/`[]`（化学名常见），
   HTTP body/header、MCP、prepare/analyze 共用；长度 8–128 与危险字符拒绝不变。
10. **ORCA 可执行（#7c）**：解析边界探测 `orca --version`，识别 GNOME 屏幕阅读器并返回
    typed `backend_unavailable`，不把系统 orca 当计算程序。
11. **cancel 回包（#7e）**：MCP `acp_cancel` 只返回有界状态信封（job/attempt/reference/
    state/status/updated_at/cancelled/already_terminal），不再回传整个 spec；HTTP 路由不变。
12. **目录布局（#7f）**：`topic.artifact_locating` 记录 singlepoint `<job_dir>/sp.out` 与
    BatchOptimize `<job_dir>/WORK/05_SP/sp.out` 两处布局，manifest 仍是读数权威。
13. **证据/文档**：A+C、B+D、E 三份 evidence note；Batch 指南的 mirror 规则、
    per-role SP 语义与「预检不提示」过期句已更新；`PACK_VERSION` 1.4.0。

## 关键设计决定

- singlepoint stale：只重建、不跳 digest；失败消息携带管理员命令。
- 自定义基组：构造期保证恰好一个轨道基组；冲突拒绝而非静默覆盖。
- batch_roles：role 内 SP 权威；顶层仅 int 镜像；缺失明确 422。
- 热化学：复用既有 `THERMO_REPORT` 枚举（无 manifest schema 迁移），描述符映射新 kind。
- 纯 MCP 会话：inline role:"ts" 是一等路径；PES 发布需管理员预铸授权。
- DLPNO：默认最终单点能；分解能量仅 metadata。

## 验证（本地）

- 定向：round-7 六件套 + cccp 四件套 + 相关回归全绿（release 10、batch_roles 12、preflight_batch 11、thermochemistry 11、idempotency 27、cancel 2、route_basis 21、xtb_constraint 21、dlpno 9、orca_probe 14、xtb_path_guard 2、round5_parity 9、services_mcp 59、guidance 18+6、architecture/isolation 47；见 evidence notes）。
- **本地全仓 `pytest -m "not slow and not contract"`：12635 passed / 0 failed / 23 skipped
  / 116 deselected（40:44）**。包括 F4 Amendment U（round-7 范围登记）、capabilities summary
  预算、tsmode submit 兼容（submit analyzer 仅新增覆盖 BatchOptimize/singlepoint）。
- `validate_acp2_contracts.py` VALID、`validate_acp2_gap_coverage.py` VALID、
  `check_docs_links.py` PASS、`check_grep_gates.py --suite architecture-remediation` 通过、
  `verify_cccp_isolation.py` PASS。
- 护栏：digest 未跳、native_scan/pointwise 未放宽、预检零副作用、无第二 store、
  `cccp`/`acp_services` 隔离未破；keep-baseline 指南（TS 初猜来源、方法分档 wB97M-V、
  PES/scan 多键/键角/二面角）保持。

## 未验证（NOT_VERIFIED）

- 远端 freeze 重试（`remote/runner`）无独立测试；真实远端 release 匹配未运行。
- 真实 frequency 作业的 thermo 落盘与远端 artifact fetch。
- `ma-def2-SVP` 的实际注入点（需外部 Agent 的 job.json/.inp）。
- xTB 约束原始 payload 走 XtbConstrainedOpt 还是 XtbPathSearch::path_inp_text（两条路径均已加
  防护，但原始样本未取得）。
- cancel 原始 ~50KB 回包未逐字节复验（用同形状 fake + 真实 Controller HTTP/MCP 对照）。
- `-m contract` SDK lane、真实 QC/LSF、生产服务重启与部署冒烟。
- 既有（非本轮）测试隔离问题已在本 PR 顺手修复（machine_output autouse restore）。

## 回滚

每批独立：release 重建（A）、batch per-role SP（B）、QC 接口（C）、热化学/预检（D）、
MCP/指南（E）。breaking-ish：`batch_roles` 存在时顶层 SP 镜像缺 role 由静默忽略变为 422
`missing_role_mirror`；`sp_method/sp_basis` 仅作为 batch_roles 镜像接受。
